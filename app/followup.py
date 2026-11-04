"""Follow-up group on Telegram (owner 2026-11-03).

The owner picks one linked Telegram group (/telegram-groups). Owner's rule: EVERYTHING the system shows the manager
or sends to the assigned employee also appears in that group, so nobody reminds an employee about a task that is
already done. For every watched employee (all, or a chosen list) the bot posts:
  - a card for each new task (employee, who sent it, title, priority, status, due, notes); the card is EDITED on
    every later change so it always shows the current status, and loses the «ذكّر» button once the task is done,
    cancelled or archived;
  - every status change from any path (Telegram buttons, web, group-task participants), problems/notes, comments,
    attachments (the file itself), edits (title/priority/due/notes), reassignment, cancel/archive, restore, late,
    every reminder the system sent to the employee, and one morning list of open tasks per employee.
Group members follow and remind only: the one button is «🔔 ذكّر الموظف», on the card and on the late message only
(event messages would keep a stale button after the task is done; the card is edited, they are not).

How: the LAN worker calls `process()` every 30 s. It reads what was added since a cursor (task_events, tasks,
task_comments, task_attachments, notifications), like the bell (`app/inbox.py`). Every message goes through the
Telegram outbox with a dedupe key, so a crash or a second run never posts the same thing twice. Cards and edits are
rendered when the outbox sends them (`render_card`), so a card queued while Telegram was down still shows the
latest state.
"""
import html
import json
import re
import time
from datetime import datetime
from fastapi import HTTPException, Request
from .db import connect

SETTING = 'followup.settings'
CURSOR = 'followup.cursor'
KINDS = ('new', 'status', 'notes', 'files', 'edits', 'reminders', 'late', 'digest')
DEFAULTS = {'enabled': False, 'group_id': None, 'employee_ids': [], 'remind_ids': [], 'digest_time': '09:00', **{k: True for k in KINDS}}
REMIND_GAP = 15 * 60  # seconds between two reminders for the same task from the group
FAR_BEHIND = 5000     # cursor this far behind (e.g. turned on after months off): skip, do not flood the group
MAX_TEXT = 3500
CLOSED = ('تم', 'ملغاة')

# Group messages are Telegram HTML (owner 2026-11-04: livelier, emojis, clearer): bold for what matters, every value
# that comes from users goes through h().
STATUS_LINE = {'بدأت': '▶️ <b>{who} بدأ بالمهمة</b> 💪', 'قيد التنفيذ': '⏳ <b>{who}: المهمة قيد التنفيذ</b> 🔧',
               'تم': '✅ <b>{who} أنجز المهمة</b> 🎉', 'ملغاة': '❌ <b>أُلغيت المهمة</b>',
               'بانتظار': '⏸️ <b>المهمة صارت «بانتظار»</b>', 'جديدة': '↩️ <b>رجعت المهمة «جديدة»</b>'}
STATUS_ICON = {'جديدة': '🆕', 'بدأت': '▶️', 'قيد التنفيذ': '⏳', 'بانتظار': '⏸️', 'تم': '✅', 'ملغاة': '❌'}
PRIO_ICON = {'عاجلة': '🔴', 'عالية': '🟠', 'عادي': '🟢', 'منخفضة': '⚪'}
SEP = '━━━━━━━━━━━━━━'
h = lambda v: html.escape(str(v if v is not None else ''), quote=False)


def plain(text):
    """HTML message -> plain text (fallback when Telegram refuses the HTML, file captions)."""
    return html.unescape(re.sub(r'<[^>]+>', '', str(text or '')))


def send_html(chat, text, reply_markup=None):
    """Send one group message as HTML; if Telegram cannot parse it, send it as plain text instead of failing."""
    from . import telegram_queue as q
    r = q.low_level_send(chat, text, reply_markup=reply_markup, parse_mode='HTML')
    if not r.get('ok') and "parse entities" in str(r.get('error', '')).lower():
        r = q.low_level_send(chat, plain(text), reply_markup=reply_markup)
    return r


def _title_line(t):
    return f"📝 <b>{h(t['title'])}</b>"


def _by(actor, skip=''):
    return f"\n👤 بواسطة: {h(actor)}" if actor and actor != skip else ''
# notifications.kind values that are reminders (everything else the employee gets is already a group message:
# new task, edit, cancel, reassignment, comment, attachment). 'تذكير من المتابعة' is posted by handle_callback.
REMINDER_KINDS = ('تذكير تلقائي', 'المهمة متأخرة', 'إعادة إرسال المهمة', 'مهام بداية الشيفت', 'ملخص نهاية الشيفت')


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS followup_messages(
      task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      chat_id TEXT NOT NULL,
      message_id INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(task_id, chat_id, message_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS followup_refs(
      chat_id TEXT NOT NULL,
      message_id INTEGER NOT NULL,
      task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      PRIMARY KEY(chat_id, message_id))''')


def remember_ref(c, chat, message_id, task_id):
    if chat and message_id and task_id:
        c.execute('INSERT OR REPLACE INTO followup_refs(chat_id,message_id,task_id) VALUES(?,?,?)', (str(chat), int(message_id), int(task_id)))


def settings(c):
    r = c.execute('SELECT value FROM settings WHERE key=?', (SETTING,)).fetchone()
    out = dict(DEFAULTS)
    if r:
        try: out.update(json.loads(r[0]))
        except Exception: pass
    return out


def _put(c, key, value):
    c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))


def _group_chat(c, cfg):
    if not cfg.get('enabled') or not cfg.get('group_id'):
        return None
    g = c.execute("SELECT chat_id FROM telegram_groups WHERE id=? AND is_active=1 AND chat_id IS NOT NULL AND chat_id<>''", (cfg['group_id'],)).fetchone()
    return str(g[0]) if g else None


def _emp_name(c, eid):
    if not eid: return ''
    r = c.execute('SELECT name FROM employees WHERE id=?', (eid,)).fetchone()
    return r[0] if r else ''


def _who(c, t):
    if t['employee_id']:
        return _emp_name(c, t['employee_id']) or 'موظف'
    if t['is_group']:
        names = [r[0] for r in c.execute('SELECT e.name FROM task_participants p JOIN employees e ON e.id=p.employee_id WHERE p.task_id=? ORDER BY e.name', (t['id'],))]
        return '، '.join(names) or 'مهمة جماعية'
    return 'بدون موظف'


def _watched(c, cfg, t):
    ids = {int(x) for x in cfg.get('employee_ids') or []}
    if not ids:
        return bool(t['employee_id'] or t['is_group'])
    if t['employee_id']:
        return int(t['employee_id']) in ids
    return any(r[0] in ids for r in c.execute('SELECT employee_id FROM task_participants WHERE task_id=?', (t['id'],)))


def _user_name(c, uid):
    if not uid: return ''
    r = c.execute('SELECT COALESCE(NULLIF(full_name,\'\'),username) FROM users WHERE id=?', (uid,)).fetchone()
    return r[0] if r else ''


def _actor(c, ev):
    """Who made a change: the employee for Telegram events, the logged-in user for page events."""
    if ev['source'] == 'telegram':
        return _emp_name(c, ev['employee_id'])
    r = c.execute('''SELECT user_id FROM audit_logs WHERE entity_type='task' AND entity_id=? AND user_id IS NOT NULL AND
        ABS(strftime('%s',created_at)-strftime('%s',?))<=5 ORDER BY id DESC LIMIT 1''', (str(ev['task_id']), ev['created_at'])).fetchone()
    return _user_name(c, r[0]) if r else ''


def _origin(c, t):
    from .delegation import origin, _users
    kind, name = origin(t, _users(c))
    if kind == 'self': return 'كتبها الموظف لنفسه'
    if kind in ('manager', 'assigned'): return name or 'المدير'
    src = str(t['source'] or '')
    return {'template': 'قالب مهام', 'recurring': 'مهمة متكررة'}.get(src, 'النظام')


def _buttons(tid, remind=True):
    from . import main as m
    rows = []
    if remind:
        rows.append([{'text': '🔔 ذكّر الموظف', 'callback_data': f'fu:remind:{tid}'}])
    url = (m.PUBLIC_URL or '').rstrip('/')
    if url.startswith('https://'):
        rows.append([{'text': '🌐 فتح المهمة بالنظام', 'url': f'{url}/task/{tid}'}])
    return {'inline_keyboard': rows}


def _open(t):
    return not t['archived_at'] and t['status'] not in CLOSED


def render_card(c, tid, new=False):
    """(text, keyboard) of the group card for task `tid` in its CURRENT state, or None if the task is gone."""
    from .main import ar_when, _is_late
    t = c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    if not t: return None
    lines = ['🆕 <b>مهمة جديدة</b> 🚀' if new else '📌 <b>بطاقة مهمة</b>', SEP, _title_line(t), '',
             f"👤 الموظف: <b>{h(_who(c, t))}</b>", f"✍️ من: {h(_origin(c, t))}",
             f"{PRIO_ICON.get(t['priority'], '🔹')} الأولوية: <b>{h(t['priority'])}</b>",
             f"{STATUS_ICON.get(t['status'], '🔹')} الحالة: <b>{h(t['status'])}</b>"]
    if t['due_at']:
        lines.append(f"⏰ الموعد: <b>{h(ar_when(t['due_at']))}</b>" + (' ⚠️ <b>متأخرة!</b>' if _is_late(t) else ''))
    if t['notes']:
        lines.append(f"🗒️ ملاحظات: <i>{h(t['notes'][:1500])}</i>")
    if t['archived_at']:
        lines += [SEP, '🗄️ <b>المهمة مؤرشفة</b> — لا حاجة للتذكير']
    elif t['status'] == 'تم':
        lines += [SEP, '✅ <b>المهمة منجزة</b> — لا حاجة للتذكير 🎉']
    elif t['status'] == 'ملغاة':
        lines += [SEP, '❌ <b>المهمة ملغاة</b> — لا حاجة للتذكير']
    return '\n'.join(lines), _buttons(tid, remind=_open(t))


def _max(c, table):
    return c.execute(f'SELECT COALESCE(MAX(id),0) FROM {table}').fetchone()[0]


def _top(c):
    return {'tk': _max(c, 'tasks'), 'ev': _max(c, 'task_events'), 'cm': _max(c, 'task_comments'),
            'at': _max(c, 'task_attachments'), 'nt': _max(c, 'notifications')}


def _names_in(c, text):
    """'من الموظف 3 إلى 5' / '3 → 5' -> ('أحمد', 'سامر'); 'بدون' stays 'بدون'."""
    parts = re.findall(r'(\d+|بدون|None)', str(text or ''))
    out = [('بدون' if p in ('بدون', 'None') else (_emp_name(c, int(p)) or f'موظف {p}')) for p in parts[:2]]
    return (out + ['بدون', 'بدون'])[:2]


def _edit_lines(c, ev):
    from .main import ar_when
    try: d = json.loads(ev['note'] or '{}')
    except Exception: return []
    old = d.get('_old') or {}
    labels = {'title': 'اسم المهمة', 'priority': 'الأولوية', 'due_at': 'الموعد'}
    out = []
    for k, lab in labels.items():
        if k in d and (d.get(k) or None) != (old.get(k) or None):
            fmt = (lambda v: ar_when(v) or '—') if k == 'due_at' else (lambda v: v or '—')
            out.append(f"🔸 {lab}: {h(fmt(old.get(k)))} ← <b>{h(fmt(d.get(k)))}</b>")
    if 'notes' in d and str(d.get('notes') or '').strip() != str(old.get('notes') or '').strip():
        out.append(f"🗒️ الملاحظات صارت: <i>{h(str(d.get('notes') or '—').strip()[:1500])}</i>")
    return out


def _event_message(c, ev, t):
    """(kind, HTML text) for one task_event, or None when another source already covers it."""
    typ = ev['event_type']; who = _who(c, t); title = _title_line(t)
    if typ == 'status':
        line = STATUS_LINE.get(ev['new_status'], '🔄 <b>تغيّرت حالة المهمة</b>').format(who=h(who))
        return 'status', f"{line}\n{title}\n🔄 {h(ev['old_status'] or '—')} ← <b>{h(ev['new_status'])}</b>{_by(_actor(c, ev), who)}"
    if typ == 'participant_status':
        name = _emp_name(c, ev['employee_id']) or 'مشارك'
        icon = STATUS_ICON.get(ev['new_status'], '👥')
        return 'status', (f"👥 <b>{h(name)}</b> — حالته بالمهمة الجماعية {icon}\n{title}\n"
                          f"🔄 {h(ev['old_status'] or '—')} ← <b>{h(ev['new_status'])}</b>{_by(_actor(c, ev), name)}")
    if typ == 'note':
        return 'notes', (f"⚠️ <b>مشكلة / ملاحظة من {h(_emp_name(c, ev['employee_id']) or who)}</b>\n{title}\n{SEP}\n"
                         f"💬 {h((ev['note'] or '')[:MAX_TEXT])}")
    if typ == 'edited':
        lines = _edit_lines(c, ev)
        if not lines: return None
        return 'edits', f"✏️ <b>تعديل على مهمة {h(who)}</b>\n{title}\n" + '\n'.join(lines) + _by(_actor(c, ev))
    if typ in ('reassigned', 'handover'):
        a, b = _names_in(c, ev['note'])
        return 'edits', f"🔁 <b>نُقلت المهمة</b>\n{title}\n👤 من: {h(a)} ← إلى: <b>{h(b)}</b>{_by(_actor(c, ev))}"
    if typ == 'delegated':
        return 'edits', f"↪️ <b>تحويل المهمة</b>\n{title}\n{h(ev['note'] or '')}"
    if typ == 'cancelled' or (typ == 'lifecycle' and ev['note'] in ('delete', 'disable')):
        return 'status', f"🗄️ <b>أُرشفت / أُلغيت مهمة {h(who)}</b>\n{title}{_by(_actor(c, ev))}"
    if typ == 'restored' or (typ == 'lifecycle' and ev['note'] in ('restore', 'enable')):
        return 'status', (f"♻️ <b>رجعت مهمة {h(who)} من الأرشيف</b>\n{title}\n"
                          f"{STATUS_ICON.get(t['status'], '🔹')} الحالة: <b>{h(t['status'])}</b>{_by(_actor(c, ev))}")
    if typ == 'template_cancel':
        return 'status', f"❌ <b>أُلغيت مهمة {h(who)} من إدارة القالب</b>\n{title}"
    if typ == 'reminded':  # «🔔 ذكّر» on the task page, by Telegram or WhatsApp
        return 'reminders', f"🔔 <b>تذكير لـ {h(who)}</b>\n{title}\n{h(ev['note'] or '')}"
    if typ == 'template_update':
        return 'edits', f"✏️ <b>تحدّثت مهمة {h(who)} من القالب</b>\n{title}"
    return None  # created / comment / attachment / admin_note / telegram_sent / telegram_notice: covered elsewhere


CARD_EVENTS = ('status', 'participant_status', 'edited', 'reassigned', 'handover', 'cancelled', 'lifecycle', 'restored',
               'template_cancel', 'template_update')


def process(c=None, now=None):
    """Post what is new since the last run. Never raises (called from the worker loop)."""
    own = c is None
    try:
        if own: c = connect()
        migrate(c)
        cfg = settings(c); chat = _group_chat(c, cfg)
        top = _top(c)
        r = c.execute('SELECT value FROM settings WHERE key=?', (CURSOR,)).fetchone()
        cur = json.loads(r[0]) if r else None
        if not chat or cur is None:  # off, or first run: start from now (no history flood)
            _put(c, CURSOR, json.dumps(top))
            return 0
        lo = {k: (top[k] if top[k] - cur.get(k, 0) > FAR_BEHIND else cur.get(k, 0)) for k in top}
        from .telegram_queue import enqueue
        from .main import ar_when, _is_late, ROOT
        out = []      # (dedupe key, text, keyboard, source_type, source_id)
        edits = {}    # task id -> last event id that changed the card
        task = lambda tid: c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()

        if cfg.get('new'):
            for t in c.execute('SELECT * FROM tasks WHERE id>? AND id<=? AND archived_at IS NULL ORDER BY id', (lo['tk'], top['tk'])).fetchall():
                if _watched(c, cfg, t):
                    out.append((f"fu:new:{t['id']}", 'بطاقة مهمة', None, 'followup_card', t['id']))

        for ev in c.execute('SELECT * FROM task_events WHERE id>? AND id<=? ORDER BY id', (lo['ev'], top['ev'])).fetchall():
            t = task(ev['task_id'])
            if not t or not _watched(c, cfg, t): continue
            if ev['event_type'] in CARD_EVENTS: edits[t['id']] = ev['id']
            msg = _event_message(c, ev, t)
            if msg and cfg.get(msg[0]):
                out.append((f"fu:ev:{ev['id']}", msg[1], _buttons(t['id'], remind=False), 'followup', t['id']))

        if cfg.get('notes'):
            # Page comments (user_id set). Telegram comments (user_id NULL) are the problem note / attachment caption,
            # already posted from task_events / task_attachments.
            for cm in c.execute('SELECT * FROM task_comments WHERE id>? AND id<=? AND user_id IS NOT NULL ORDER BY id', (lo['cm'], top['cm'])).fetchall():
                t = task(cm['task_id'])
                if not t or not _watched(c, cfg, t): continue
                out.append((f"fu:cm:{cm['id']}", f"💬 <b>تعليق من {h(cm['username_snapshot'] or 'مستخدم')}</b> على مهمة {h(_who(c, t))}\n{_title_line(t)}\n{SEP}\n🗨️ {h((cm['body'] or '')[:MAX_TEXT])}",
                            _buttons(t['id'], remind=False), 'followup', t['id']))

        if cfg.get('files'):
            from .main import _is_audio
            for a in c.execute('SELECT * FROM task_attachments WHERE id>? AND id<=? ORDER BY id', (lo['at'], top['at'])).fetchall():
                t = task(a['task_id'])
                if not t or not _watched(c, cfg, t) or _is_audio(a['original_name'], a['mime_type'], a['kind']): continue
                if a['user_id']:
                    by = _user_name(c, a['user_id'])
                else:
                    ev = c.execute("SELECT employee_id FROM task_events WHERE task_id=? AND event_type='attachment' AND source='telegram' AND note=? ORDER BY id DESC LIMIT 1", (t['id'], a['original_name'])).fetchone()
                    by = _emp_name(c, ev[0]) if ev else _who(c, t)
                cap = c.execute("SELECT body FROM task_comments WHERE task_id=? AND user_id IS NULL AND id>? AND id<=? AND body<>'مرفق من Telegram' ORDER BY id DESC LIMIT 1",
                                (t['id'], lo['cm'], top['cm'])).fetchone() if not a['user_id'] else None
                text = f"📎 مرفق من {by or 'موظف'} على مهمة {_who(c, t)}\n📝 {t['title']}\n📄 الملف: {a['original_name']}" + (f"\n\n💬 {cap[0][:600]}" if cap else '')
                if (ROOT / a['relative_path']).exists():
                    out.append((f"fu:at:{a['id']}", text[:1000], None, 'followup_file', a['id']))
                else:
                    out.append((f"fu:at:{a['id']}", h(text) + '\n(الملف غير موجود على السيرفر)', _buttons(t['id'], remind=False), 'followup', t['id']))

        if cfg.get('reminders'):
            marks = ','.join('?' * len(REMINDER_KINDS))
            for n in c.execute(f"""SELECT * FROM notifications WHERE id>? AND id<=? AND status<>'failed'
                    AND (kind IN ({marks}) OR kind LIKE 'الموعد يقترب%' OR kind LIKE 'تذكير تلقائي%') ORDER BY id""", (lo['nt'], top['nt'], *REMINDER_KINDS)).fetchall():
                name = _emp_name(c, n['employee_id']) or 'موظف'
                if n['task_id']:
                    t = task(n['task_id'])
                    if not t or not _watched(c, cfg, t): continue
                    out.append((f"fu:nt:{n['id']}", f"🔔 <b>النظام ذكّر {h(name)}</b>\n{_title_line(t)}\n🕒 {h(n['kind'])}", _buttons(t['id'], remind=False), 'followup', t['id']))
                else:
                    ids = {int(x) for x in cfg.get('employee_ids') or []}
                    if ids and n['employee_id'] not in ids: continue
                    out.append((f"fu:nt:{n['id']}", f"📋 أُرسلت إلى <b>{h(name)}</b> قائمة مهامه: {h(n['kind'])}", None, 'followup', None))

        if cfg.get('late'):
            for t in c.execute("""SELECT * FROM tasks WHERE archived_at IS NULL AND status NOT IN ('تم','ملغاة') AND due_at IS NOT NULL AND due_at<>''
                    AND datetime(replace(due_at,'T',' '))<datetime('now','localtime')
                    AND datetime(replace(due_at,'T',' '))>=datetime('now','localtime','-48 hours')""").fetchall():
                if not _watched(c, cfg, t): continue
                key = f"fu:late:{t['id']}:{t['due_at']}"
                if c.execute('SELECT 1 FROM telegram_outbox WHERE dedupe_key=?', (key,)).fetchone(): continue
                edits.setdefault(t['id'], f"late{t['due_at']}")
                out.append((key, f"🚨 <b>تأخرت مهمة {h(_who(c, t))}</b>\n{_title_line(t)}\n⏰ كان موعدها: <b>{h(ar_when(t['due_at']))}</b>\n{STATUS_ICON.get(t['status'], '🔹')} حالتها: <b>{h(t['status'])}</b>", _buttons(t['id']), 'followup', t['id']))

        now = now or datetime.now(); day = now.date().isoformat()
        if cfg.get('digest') and now.strftime('%H:%M') >= (cfg.get('digest_time') or '09:00'):
            done = c.execute('SELECT value FROM settings WHERE key=?', ('followup.digest_day',)).fetchone()
            if not done or done[0] != day:
                _put(c, 'followup.digest_day', day)  # once a day, even when the list is empty
                for i, (text, kb) in enumerate(digest(c, cfg, now)):
                    out.append((f"fu:digest:{day}:{i}", text, kb, 'followup', None))

        for key, text, kb, stype, sid in out:
            enqueue(chat, text, stype, sid, dedupe_key=key, reply_markup=kb)
        for tid, mark in edits.items():
            for msg in c.execute('SELECT message_id FROM followup_messages WHERE task_id=? AND chat_id=?', (tid, chat)).fetchall():
                enqueue(chat, 'تحديث بطاقة المتابعة', 'followup_edit', tid, dedupe_key=f"fu:edit:{tid}:{msg[0]}:{mark}",
                        reply_markup={'message_id': int(msg[0])})
        _put(c, CURSOR, json.dumps(top))
        return len(out)
    except Exception as e:
        print('Follow-up group error:', e)
        return 0
    finally:
        if own and c is not None: c.close()


def digest(c, cfg, now=None):
    """Morning list: a header, then one message per employee with one button per task. Pressing a task posts its card
    (with «ذكّر» and the link) so the group can follow, remind or reply to it (owner 2026-11-03: one long message was
    not usable)."""
    from .main import ar_when, _is_late
    rows = c.execute("""SELECT t.*, COALESCE(e.name,'') ename FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id
        WHERE t.archived_at IS NULL AND t.status NOT IN ('تم','ملغاة') ORDER BY e.sort_order, e.name,
        CASE t.priority WHEN 'عاجلة' THEN 1 WHEN 'عالية' THEN 2 WHEN 'عادي' THEN 3 ELSE 4 END,
        CASE WHEN t.due_at IS NULL THEN 1 ELSE 0 END, t.due_at""").fetchall()
    by = {}
    for t in rows:
        if not _watched(c, cfg, t): continue
        by.setdefault(t['ename'] or _who(c, t), []).append(t)
    if not by: return []
    ar = lambda n: str(n).translate(str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩'))
    total = sum(len(ts) for ts in by.values())
    out = [(f"☀️ <b>صباح الخير! المهام المفتوحة — {h(ar_when((now or datetime.now()).date().isoformat()))}</b>\n{SEP}\n"
            f"📊 <b>{ar(total)}</b> مهمة عند <b>{ar(len(by))}</b> موظف\n\n"
            f"👇 تحت كل موظف لحاله: اضغط على أي مهمة لتظهر بطاقتها هنا\n"
            f"🔔 من البطاقة: «ذكّر الموظف»\n💬 للتعليق: اعمل «رد / Reply» على البطاقة واكتب تعليقك", None)]
    for name, ts in by.items():
        late = sum(1 for t in ts if t['due_at'] and _is_late(t))
        for part in range(0, len(ts), 40):
            chunk = ts[part:part + 40]
            lines = [f"👤 <b>{h(name)}</b> — {ar(len(ts))} مفتوحة" + (f" · 🚨 <b>{ar(late)} متأخرة</b>" if late else '') + (' (تابع)' if part else ''), SEP]
            kb = []
            for i, t in enumerate(chunk, part + 1):
                bad = t['due_at'] and _is_late(t)
                due = f" · ⏰ {ar_when(t['due_at'])}" if t['due_at'] else ''
                lines.append(f"{PRIO_ICON.get(t['priority'], '🔹')} {ar(i)}. <b>{h(t['title'][:120])}</b>\n      {STATUS_ICON.get(t['status'], '🔹')} {h(t['status'])}{h(due)}" + (' 🚨' if bad else ''))
                kb.append([{'text': f"{'🚨 ' if bad else ''}{ar(i)}. {t['title'][:45]}", 'callback_data': f"fu:open:{t['id']}"}])
            out.append(('\n'.join(lines)[:MAX_TEXT], {'inline_keyboard': kb}))
    return out


def outbox_send(c, row):
    """Called by telegram_queue.process_queue for source types followup_card / followup_edit / followup_file.
    Returns the Telegram result dict."""
    from . import telegram_queue as q
    st = row['source_type']; sid = int(row['source_id']) if row['source_id'] else None
    if st == 'followup_file':
        from .main import ROOT
        from .telegram_media import send_attachment
        a = c.execute('SELECT * FROM task_attachments WHERE id=?', (sid,)).fetchone()
        if not a: return {'ok': False, 'error': 'المرفق محذوف', 'final': True}
        return send_attachment(row['chat_id'], ROOT / a['relative_path'], a['mime_type'] or '', plain(row['message_text']))
    card = render_card(c, sid, new=(st == 'followup_card'))
    if not card: return {'ok': False, 'error': 'المهمة محذوفة', 'final': True}
    text, kb = card
    if st in ('followup_card', 'followup_show'):
        r = send_html(row['chat_id'], text, kb)
        if r.get('ok') and r.get('message_id'):
            c.execute('INSERT OR IGNORE INTO followup_messages(task_id,chat_id,message_id) VALUES(?,?,?)', (sid, str(row['chat_id']), int(r['message_id'])))
            remember_ref(c, row['chat_id'], r['message_id'], sid)
        return r
    meta = json.loads(row['reply_markup_json'] or '{}')
    r = q.edit_message(row['chat_id'], int(meta['message_id']), text, reply_markup=kb, parse_mode='HTML')
    if 'message is not modified' in str(r.get('error', '')).lower(): return {'ok': True}
    if 'message to edit not found' in str(r.get('error', '')).lower():
        c.execute('DELETE FROM followup_messages WHERE task_id=? AND chat_id=? AND message_id=?', (sid, str(row['chat_id']), int(meta['message_id'])))
        return {'ok': False, 'error': r.get('error'), 'final': True}
    return r


def _may_act(c, telegram_user_id):
    """Owner 2026-11-03: everyone in the group sees everything; only the people chosen in the settings (if any) may
    remind or comment. Matched by Telegram user id = the employee's private chat id."""
    allowed = {int(x) for x in settings(c).get('remind_ids') or []}
    if not allowed: return True
    return bool(c.execute(f"SELECT 1 FROM employees WHERE telegram_chat_id=? AND id IN ({','.join('?' * len(allowed))})",
                          (str(telegram_user_id or ''), *allowed)).fetchone())


def post_card(c, chat, tid):
    """Post the current card of a task in the group now (button in the morning list). Queued if Telegram fails."""
    from . import telegram_queue as q
    card = render_card(c, tid)
    if not card: return {'ok': False}
    r = send_html(chat, card[0], card[1])
    if r.get('ok') and r.get('message_id'):
        c.execute('INSERT OR IGNORE INTO followup_messages(task_id,chat_id,message_id) VALUES(?,?,?)', (tid, chat, int(r['message_id'])))
        remember_ref(c, chat, r['message_id'], tid)
    elif not r.get('ok'):
        q.enqueue(chat, 'بطاقة مهمة', 'followup_show', tid, dedupe_key=f'fu:show:{tid}:{int(time.time())}')
    return r


def handle_group_message(c, msg):
    """A reply in the follow-up group to a bot message about a task = a comment on that task: saved on the task and
    sent to the employee. Returns True when the message was for us."""
    chat = str((msg.get('chat') or {}).get('id') or '')
    if not chat or chat != _group_chat(c, settings(c)): return False
    ref = msg.get('reply_to_message') or {}
    text = str(msg.get('text') or msg.get('caption') or '').strip()
    if not ref or not text: return False
    row = c.execute('SELECT task_id FROM followup_refs WHERE chat_id=? AND message_id=?', (chat, int(ref.get('message_id') or 0))).fetchone()
    if not row: return False
    from . import main as m
    from .telegram_queue import enqueue
    f = msg.get('from') or {}
    who = ' '.join(x for x in (f.get('first_name'), f.get('last_name')) if x) or 'المتابعة'
    if not _may_act(c, f.get('id')):
        enqueue(chat, f"⛔ <b>{h(who)}</b>: التعليق مسموح فقط للأشخاص المحددين في إعدادات مجموعة المتابعة.", 'followup', None,
                dedupe_key=f"fu:deny:{chat}:{msg.get('message_id')}")
        return True
    t = c.execute('SELECT * FROM tasks WHERE id=?', (row[0],)).fetchone()
    if not t: return True
    body = text[:4000]
    c.execute('INSERT INTO task_comments(task_id,user_id,username_snapshot,body) VALUES(?,NULL,?,?)', (t['id'], f'{who} (مجموعة المتابعة)', body))
    c.execute("INSERT INTO task_events(task_id,employee_id,event_type,note,source) VALUES(?,?,'comment',?,'telegram_group')", (t['id'], t['employee_id'], f'{who}: {body}'))
    sent = False
    if t['is_group']:
        for p in c.execute('SELECT employee_id FROM task_participants WHERE task_id=?', (t['id'],)).fetchall():
            sent = bool(m._notify_employee_id(c, p[0], f"📝 تعليق من المتابعة\n\nالمهمة: {t['title']}\nمن: {who}\n\n{body}", t['id'], 'تعليق من المتابعة').get('ok')) or sent
    else:
        sent = bool(m._notify_admin_comment(c, t['id'], who, body).get('ok'))
    to = _who(c, t)
    enqueue(chat, f"✅ <b>تم حفظ تعليق {h(who)}</b>\n{_title_line(t)}\n" + (f"📨 وصل إلى {h(to)}" if sent else f"📭 لم يصل إلى {h(to)} على Telegram، سيراه في النظام"),
            'followup', t['id'], dedupe_key=f"fu:commented:{chat}:{msg.get('message_id')}")
    from .security_center import append_audit
    append_audit(c, {'user_id': None, 'username_snapshot': f'telegram:{who}', 'action': 'followup_comment', 'entity_type': 'task',
                     'entity_id': str(t['id']), 'before_json': None, 'after_json': json.dumps({'by_telegram': who, 'telegram_user_id': f.get('id'), 'body': body}, ensure_ascii=False),
                     'reason': None, 'gateway': 'telegram', 'ip_address': None})
    return True


def handle_callback(c, cb):
    """'fu:remind:ID' pressed in the follow-up group. Being in that group is the permission (the owner controls who
    is in it). Returns True when handled (the caller must not look the presser up as an employee)."""
    from . import main as m
    from .telegram_service import answer_callback
    data = str(cb.get('data') or '')
    mo = re.match(r'^fu:(remind|open):(\d+)$', data)
    if not mo: return False
    cbid = cb.get('id'); tid = int(mo.group(2))
    chat = str(((cb.get('message') or {}).get('chat') or {}).get('id') or '')
    if not chat or chat != _group_chat(c, settings(c)):
        answer_callback(cbid, 'هذا الزر يعمل من مجموعة المتابعة فقط', True); return True
    if mo.group(1) == 'open':  # morning list: show this task's card (anyone in the group may look)
        answer_callback(cbid, 'تفضل، البطاقة في الأسفل' if post_card(c, chat, tid).get('ok') else 'ستصل البطاقة بعد قليل')
        return True
    if not _may_act(c, (cb.get('from') or {}).get('id')):
        answer_callback(cbid, 'التذكير مسموح فقط للأشخاص المحددين في إعدادات مجموعة المتابعة', True); return True
    t = c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    if not t or not _open(t):
        answer_callback(cbid, 'المهمة منتهية أو مؤرشفة، لا حاجة للتذكير', True); return True
    key = f'followup.remind.{tid}'
    last = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    if last and time.time() - float(last[0]) < REMIND_GAP:
        answer_callback(cbid, 'أُرسل تذكير لهذه المهمة قبل قليل، حاول بعد ربع ساعة', True); return True
    _put(c, key, str(time.time()))
    if t['is_group']:
        for p in c.execute('SELECT employee_id FROM task_participants WHERE task_id=? AND status<>?', (tid, 'تم')).fetchall():
            m._notify_employee_id(c, p[0], m._telegram_task_text(t, 'تذكير من المتابعة'), tid, 'تذكير من المتابعة', m._telegram_task_keyboard(tid, t['status']))
    else:
        m._notify_employee(c, tid, 'تذكير من المتابعة', notice_category='reminders')
    f = cb.get('from') or {}
    who = ' '.join(x for x in (f.get('first_name'), f.get('last_name')) if x) or 'المتابعة'
    answer_callback(cbid, 'تم إرسال التذكير')
    from .telegram_queue import enqueue
    enqueue(chat, f"🔔 <b>{h(who)} ذكّر {h(_who(c, t))}</b> 👀\n{_title_line(t)}", 'followup', tid, dedupe_key=f'fu:reminded:{tid}:{int(time.time())}')
    from .security_center import append_audit  # main.audit needs a web request; this comes from Telegram
    append_audit(c, {'user_id': None, 'username_snapshot': f'telegram:{who}', 'action': 'followup_remind', 'entity_type': 'task',
                     'entity_id': str(tid), 'before_json': None, 'after_json': json.dumps({'by_telegram': who, 'telegram_user_id': f.get('id')}, ensure_ascii=False),
                     'reason': None, 'gateway': 'telegram', 'ip_address': None})
    return True


def register(app, m):
    c = connect()
    try: migrate(c)
    finally: c.close()

    @app.get('/api/followup')
    def get_followup(req: Request):
        m.need(req, 'employees.edit'); c = connect()
        try:
            cfg = settings(c)
            groups = [dict(r) for r in c.execute("SELECT id,name,(chat_id IS NOT NULL AND chat_id<>'') linked FROM telegram_groups WHERE is_active=1 ORDER BY name")]
            employees = [dict(r) for r in c.execute("SELECT id,name,(telegram_chat_id IS NOT NULL AND telegram_chat_id<>'') telegram FROM employees WHERE is_active=1 ORDER BY sort_order,name")]
            return {'settings': cfg, 'groups': groups, 'employees': employees, 'active': bool(_group_chat(c, cfg))}
        finally: c.close()

    @app.post('/api/followup/digest-now')
    def followup_digest_now(req: Request):
        u = m.need(req, 'employees.edit'); c = connect()
        try:
            chat = _group_chat(c, settings(c))
            if not chat: raise HTTPException(400, 'مجموعة المتابعة غير مفعّلة')
            from .telegram_queue import enqueue
            parts = digest(c, settings(c))
            stamp = int(time.time())
            for i, (text, kb) in enumerate(parts):
                enqueue(chat, text, 'followup', None, dedupe_key=f'fu:digestnow:{stamp}:{i}', reply_markup=kb)
            m.audit(c, req, u, 'followup_digest_now', 'settings', None, after={'messages': len(parts)})
            return {'ok': True, 'messages': len(parts)}
        finally: c.close()

    @app.put('/api/followup')
    async def put_followup(req: Request):
        u = m.need(req, 'employees.edit'); d = await req.json(); c = connect()
        try:
            cfg = settings(c)
            for k in ('enabled',) + KINDS:
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
            if 'remind_ids' in d:
                cfg['remind_ids'] = [int(x) for x in d['remind_ids'] or []]
            if cfg['enabled'] and not _group_chat(c, cfg):
                raise HTTPException(400, 'اختر مجموعة مربوطة بـ Telegram أولاً')
            was = _group_chat(c, settings(c))
            _put(c, SETTING, json.dumps(cfg))
            if not was and _group_chat(c, cfg):  # just switched on: start from now, not from when it was last on
                _put(c, CURSOR, json.dumps(_top(c)))
            m.audit(c, req, u, 'followup_settings', 'settings', None, after=cfg)
            return {'ok': True, 'settings': cfg}
        finally: c.close()
