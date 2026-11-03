"""Follow-up group on Telegram (owner 2026-11-03).

The owner picks one linked Telegram group (/telegram-groups). The bot posts there, for every employee (or a chosen
list): each new task with the employee's name, when a task is done, when an employee writes a problem/note, when a
task becomes late, and one list of all open tasks per employee each morning. Under task messages there is a
"🔔 ذكّر الموظف" button (sends the employee the task again) and a link to the task in the system. Nobody in the group
can change a task's status from there (owner's choice: follow and remind only).

Runs in the LAN worker only (scan of new rows since a cursor, like the bell). Every message goes through the outbox
with a dedupe key, so a crash or a second run never posts the same thing twice.
"""
import json
import re
import time
from datetime import datetime
from fastapi import HTTPException, Request
from .db import connect

SETTING = 'followup.settings'
CURSOR = 'followup.cursor'
DEFAULTS = {'enabled': False, 'group_id': None, 'employee_ids': [], 'digest_time': '09:00',
            'new': True, 'done': True, 'notes': True, 'late': True, 'digest': True}
REMIND_GAP = 15 * 60  # seconds between two reminders for the same task from the group


def settings(c):
    r = c.execute('SELECT value FROM settings WHERE key=?', (SETTING,)).fetchone()
    out = dict(DEFAULTS)
    if r:
        try: out.update(json.loads(r[0]))
        except Exception: pass
    return out


def _group_chat(c, cfg):
    if not cfg.get('enabled') or not cfg.get('group_id'):
        return None
    g = c.execute("SELECT chat_id FROM telegram_groups WHERE id=? AND is_active=1 AND chat_id IS NOT NULL AND chat_id<>''", (cfg['group_id'],)).fetchone()
    return str(g[0]) if g else None


def _who(c, t):
    if t['employee_id']:
        r = c.execute('SELECT name FROM employees WHERE id=?', (t['employee_id'],)).fetchone()
        return r[0] if r else 'موظف'
    if t['is_group']:
        names = [r[0] for r in c.execute('SELECT e.name FROM task_participants p JOIN employees e ON e.id=p.employee_id WHERE p.task_id=? ORDER BY e.name', (t['id'],))]
        return '، '.join(names) or 'مهمة جماعية'
    return ''


def _watched(c, cfg, t):
    ids = {int(x) for x in cfg.get('employee_ids') or []}
    if not ids:
        return bool(t['employee_id'] or t['is_group'])
    if t['employee_id']:
        return int(t['employee_id']) in ids
    return any(r[0] in ids for r in c.execute('SELECT employee_id FROM task_participants WHERE task_id=?', (t['id'],)))


def _buttons(tid, remind=True):
    from . import main as m
    rows = []
    if remind:
        rows.append([{'text': '🔔 ذكّر الموظف', 'callback_data': f'fu:remind:{tid}'}])
    url = (m.PUBLIC_URL or '').rstrip('/')
    if url.startswith('https://'):
        rows.append([{'text': '🌐 فتح المهمة بالنظام', 'url': f'{url}/task/{tid}'}])
    return {'inline_keyboard': rows} if rows else None


def _card(c, t, head):
    from .main import ar_when, _is_late
    prio = {'عاجلة': '🔴 عاجلة', 'عالية': '🟠 عالية'}.get(t['priority'], t['priority'])
    lines = [head, '', f"المهمة: {t['title']}", f"الأولوية: {prio}", f"الحالة: {t['status']}"]
    if t['due_at']:
        lines.append(f"⏰ الموعد: {ar_when(t['due_at'])}" + (' ⚠️ متأخرة' if _is_late(t) else ''))
    if t['notes']:
        lines.append(f"📝 ملاحظات: {t['notes']}")
    return '\n'.join(lines)


def _max(c, table):
    return c.execute(f'SELECT COALESCE(MAX(id),0) FROM {table}').fetchone()[0]


def process(c=None, now=None):
    """Post what is new since the last run. Never raises (called from the worker loop)."""
    own = c is None
    try:
        if own: c = connect()
        cfg = settings(c); chat = _group_chat(c, cfg)
        top = {'tk': _max(c, 'tasks'), 'ev': _max(c, 'task_events')}
        r = c.execute('SELECT value FROM settings WHERE key=?', (CURSOR,)).fetchone()
        cur = json.loads(r[0]) if r else None
        if not chat or cur is None:  # off, or first run: start from now (no history flood)
            c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (CURSOR, json.dumps(top)))
            return 0
        from .telegram_queue import enqueue
        from .delegation import telegram_prefix
        from .main import ar_when
        out = []
        if cfg.get('new'):
            for t in c.execute("SELECT * FROM tasks WHERE id>? AND id<=? AND archived_at IS NULL ORDER BY id", (cur.get('tk', 0), top['tk'])).fetchall():
                if not _watched(c, cfg, t): continue
                origin = (telegram_prefix(c, t) or '').split(' ', 1)[-1].removeprefix('مهمة ')
                head = f"🆕 مهمة جديدة لـ {_who(c, t)}" + (f" — {origin}" if origin else '')
                out.append((f"fu:new:{t['id']}", _card(c, t, head), _buttons(t['id'])))
        for ev in c.execute("""SELECT ev.*, t.title FROM task_events ev JOIN tasks t ON t.id=ev.task_id WHERE ev.id>? AND ev.id<=?
                               AND ((ev.event_type='status' AND ev.new_status='تم') OR ev.event_type='note') ORDER BY ev.id""",
                            (cur.get('ev', 0), top['ev'])).fetchall():
            t = c.execute('SELECT * FROM tasks WHERE id=?', (ev['task_id'],)).fetchone()
            if not t or not _watched(c, cfg, t): continue
            name = _who(c, t)
            if ev['event_type'] == 'status' and cfg.get('done'):
                out.append((f"fu:done:{ev['id']}", f"✅ {name} أنجز «{ev['title']}»", _buttons(t['id'], remind=False)))
            elif ev['event_type'] == 'note' and cfg.get('notes'):
                out.append((f"fu:note:{ev['id']}", f"⚠️ {name} كتب مشكلة / ملاحظة على «{ev['title']}»:\n\n{ev['note'] or ''}", _buttons(t['id'])))
        if cfg.get('late'):
            for t in c.execute("""SELECT * FROM tasks WHERE archived_at IS NULL AND status NOT IN ('تم','ملغاة') AND due_at IS NOT NULL AND due_at<>''
                    AND datetime(replace(due_at,'T',' '))<datetime('now','localtime')
                    AND datetime(replace(due_at,'T',' '))>=datetime('now','localtime','-48 hours')""").fetchall():
                if not _watched(c, cfg, t): continue
                out.append((f"fu:late:{t['id']}:{t['due_at']}", f"⏰ تأخرت مهمة {_who(c, t)}: «{t['title']}»\nكان موعدها {ar_when(t['due_at'])}، وحالتها: {t['status']}", _buttons(t['id'])))
        now = now or datetime.now(); day = now.date().isoformat()
        if cfg.get('digest') and now.strftime('%H:%M') >= (cfg.get('digest_time') or '09:00'):
            text = digest(c, cfg)
            if text:
                for i in range(0, len(text), 3800):
                    out.append((f"fu:digest:{day}:{i}", text[i:i + 3800], None))
        for key, text, kb in out:
            enqueue(chat, text, 'followup', None, dedupe_key=key, reply_markup=kb)
        c.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(top), CURSOR))
        return len(out)
    except Exception as e:
        print('Follow-up group error:', e)
        return 0
    finally:
        if own and c is not None: c.close()


def digest(c, cfg):
    from .main import ar_when, _is_late
    rows = c.execute("""SELECT t.*, COALESCE(e.name,'') ename FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id
        WHERE t.archived_at IS NULL AND t.status NOT IN ('تم','ملغاة') ORDER BY e.sort_order, e.name,
        CASE t.priority WHEN 'عاجلة' THEN 1 WHEN 'عالية' THEN 2 WHEN 'عادي' THEN 3 ELSE 4 END,
        CASE WHEN t.due_at IS NULL THEN 1 ELSE 0 END, t.due_at""").fetchall()
    by = {}
    for t in rows:
        if not _watched(c, cfg, t): continue
        by.setdefault(t['ename'] or _who(c, t), []).append(t)
    if not by: return ''
    ar = lambda n: str(n).translate(str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩'))
    lines = [f"📋 المهام المفتوحة — {ar_when(datetime.now().date().isoformat())}", '']
    for name, ts in by.items():
        late = sum(1 for t in ts if t['due_at'] and _is_late(t))
        lines.append(f"👤 {name} — {ar(len(ts))} مفتوحة" + (f"، ⚠️ {ar(late)} متأخرة" if late else ''))
        for i, t in enumerate(ts, 1):
            due = f" · ⏰ {ar_when(t['due_at'])}" if t['due_at'] else ''
            lines.append(f"   {ar(i)}. {t['title']} · {t['status']}{due}" + (' ⚠️' if t['due_at'] and _is_late(t) else ''))
        lines.append('')
    return '\n'.join(lines).strip()


def handle_callback(c, cb):
    """'fu:remind:ID' pressed in the follow-up group. Being in that group is the permission (the owner controls who
    is in it). Returns True when handled."""
    from . import main as m
    from .telegram_service import answer_callback
    data = str(cb.get('data') or '')
    mo = re.match(r'^fu:remind:(\d+)$', data)
    if not mo: return False
    cbid = cb.get('id'); tid = int(mo.group(1))
    chat = str(((cb.get('message') or {}).get('chat') or {}).get('id') or '')
    if not chat or chat != _group_chat(c, settings(c)):
        answer_callback(cbid, 'هذا الزر يعمل من مجموعة المتابعة فقط', True); return True
    t = c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    if not t or t['archived_at'] or t['status'] in ('تم', 'ملغاة'):
        answer_callback(cbid, 'المهمة منتهية أو مؤرشفة', True); return True
    key = f'followup.remind.{tid}'
    last = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    if last and time.time() - float(last[0]) < REMIND_GAP:
        answer_callback(cbid, 'انبعت تذكير لهالمهمة من شوي، جرّب بعد ربع ساعة', True); return True
    c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(time.time())))
    if t['is_group']:
        for p in c.execute('SELECT employee_id FROM task_participants WHERE task_id=? AND status<>?', (tid, 'تم')).fetchall():
            m._notify_employee_id(c, p[0], m._telegram_task_text(t, '🔔 تذكير من المتابعة'), tid, 'تذكير من المتابعة', m._telegram_task_keyboard(tid, t['status']))
    else:
        m._notify_employee(c, tid, '🔔 تذكير من المتابعة', notice_category='reminders')
    who = (cb.get('from') or {}).get('first_name') or 'المتابعة'
    answer_callback(cbid, 'انبعت التذكير')
    from .telegram_queue import enqueue
    enqueue(chat, f"🔔 {who} ذكّر {_who(c, t)} بـ «{t['title']}»", 'followup', None, dedupe_key=f'fu:reminded:{tid}:{int(time.time())}')
    m.audit(c, None, None, 'followup_remind', 'task', tid, after={'by_telegram': who})
    return True


def register(app, m):
    @app.get('/api/followup')
    def get_followup(req: Request):
        m.need(req, 'employees.edit'); c = connect()
        try:
            cfg = settings(c)
            groups = [dict(r) for r in c.execute("SELECT id,name,chat_id IS NOT NULL AND chat_id<>'' linked FROM telegram_groups WHERE is_active=1 ORDER BY name")]
            return {'settings': cfg, 'groups': groups}
        finally: c.close()

    @app.put('/api/followup')
    async def put_followup(req: Request):
        u = m.need(req, 'employees.edit'); d = await req.json(); c = connect()
        try:
            cfg = settings(c)
            for k in ('enabled', 'new', 'done', 'notes', 'late', 'digest'):
                if k in d: cfg[k] = bool(d[k])
            if 'group_id' in d:
                gid = d['group_id']
                if gid not in (None, '') and not c.execute('SELECT 1 FROM telegram_groups WHERE id=? AND is_active=1', (int(gid),)).fetchone():
                    raise HTTPException(400, 'المجموعة غير موجودة')
                cfg['group_id'] = int(gid) if gid not in (None, '') else None
            if 'digest_time' in d:
                if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', str(d['digest_time'] or '')): raise HTTPException(400, 'الوقت بصيغة HH:MM')
                cfg['digest_time'] = d['digest_time']
            if 'employee_ids' in d:
                cfg['employee_ids'] = [int(x) for x in d['employee_ids'] or []]
            if cfg['enabled'] and not _group_chat(c, cfg):
                raise HTTPException(400, 'اختر مجموعة مربوطة بـ Telegram أولاً')
            c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (SETTING, json.dumps(cfg)))
            m.audit(c, req, u, 'followup_settings', 'settings', None, after=cfg)
            return {'ok': True, 'settings': cfg}
        finally: c.close()
