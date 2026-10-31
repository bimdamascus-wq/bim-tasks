"""Daily Telegram summary for each supervisor ("المسؤول") about their team.

Once a day, at or after the configured time (server local time), each supervisor whose employee record is linked to
Telegram receives, per team member: tasks done today, overdue, open, and open but not yet viewed.
Runs only in the LAN worker (app/workers.py). One shift_dispatch_log row per supervisor per day
(event_key supervisor_summary:<user_id>:<date>) makes it send at most once, even across restarts.
Admins/deputies can also receive it for every active employee (setting, off by default).
"""
import json
from datetime import datetime

KIND = 'ملخص الفريق اليومي'
DEFAULTS = {'supervisor_summary_enabled': '1', 'supervisor_summary_time': '18:00',
            'supervisor_summary_managers': '0', 'supervisor_summary_off': '[]'}

# Same "open" rule as the shift messages and the Telegram "my tasks" command (workers.OPEN_TASKS_WHERE).
_OPEN = ("(t.employee_id=e.id OR EXISTS(SELECT 1 FROM task_participants p WHERE p.task_id=t.id AND p.employee_id=e.id AND p.status<>'تم'))"
         " AND t.archived_at IS NULL AND t.status NOT IN ('تم','ملغاة')")


def settings(c):
    out = dict(DEFAULTS)
    for r in c.execute('SELECT key,value FROM settings WHERE key IN (%s)' % ','.join('?' * len(DEFAULTS)), list(DEFAULTS)):
        out[r['key']] = r['value']
    return out


def off_users(cfg):
    try:
        return {int(x) for x in json.loads(cfg.get('supervisor_summary_off') or '[]')}
    except Exception:
        return set()


def _on(v):
    return str(v).strip().lower() not in ('', '0', 'false', 'no', 'off')


def _minutes(hhmm):
    try:
        h, m = map(int, str(hhmm).split(':')[:2])
        return h * 60 + m
    except Exception:
        return 18 * 60


def recipients(c, include_managers=False):
    """Active accounts that get the summary, with their Telegram chat."""
    roles = ('supervisor', 'admin', 'deputy') if include_managers else ('supervisor',)
    return c.execute(f"""SELECT u.id user_id,u.full_name,r.code role_code,e.id employee_id,e.name,e.telegram_chat_id
        FROM users u JOIN roles r ON r.id=u.role_id JOIN employees e ON e.id=u.employee_id
        WHERE u.is_active=1 AND u.deleted_at IS NULL AND r.code IN ({','.join('?' * len(roles))})
          AND e.is_active=1 AND e.telegram_enabled=1 AND e.telegram_chat_id IS NOT NULL AND trim(e.telegram_chat_id)<>''
        ORDER BY u.id""", roles).fetchall()


def members(c, rec):
    """Employee ids covered: the supervisor's team (not themselves); everyone active for admin/deputy."""
    if rec['role_code'] in ('admin', 'deputy'):
        return [r[0] for r in c.execute('SELECT id FROM employees WHERE is_active=1 ORDER BY name')]
    return [r[0] for r in c.execute("""SELECT s.employee_id FROM supervisor_team s JOIN employees e ON e.id=s.employee_id
        WHERE s.user_id=? AND e.is_active=1 AND s.employee_id<>COALESCE(?,-1) ORDER BY e.name""",
                                    (rec['user_id'], rec['employee_id']))]


def team_rows(c, employee_ids):
    """Per employee: done today, overdue, open, not viewed. Dates in server local time (completed_at is UTC)."""
    if not employee_ids:
        return []
    marks = ','.join('?' * len(employee_ids))
    return c.execute(f"""SELECT e.id,e.name,
        (SELECT COUNT(*) FROM tasks t WHERE (t.employee_id=e.id OR EXISTS(SELECT 1 FROM task_participants p
              WHERE p.task_id=t.id AND p.employee_id=e.id)) AND t.archived_at IS NULL AND t.status='تم'
              AND date(t.completed_at,'localtime')=date('now','localtime')) done_today,
        (SELECT COUNT(*) FROM tasks t WHERE {_OPEN} AND t.due_at IS NOT NULL AND t.due_at<>''
              AND datetime(replace(t.due_at,'T',' '))<datetime('now','localtime')) overdue,
        (SELECT COUNT(*) FROM tasks t WHERE {_OPEN}) open,
        (SELECT COUNT(*) FROM tasks t WHERE {_OPEN} AND t.viewed_at IS NULL) not_viewed
        FROM employees e WHERE e.id IN ({marks}) ORDER BY e.name""", employee_ids).fetchall()


def build_text(c, rec, now=None):
    now = now or datetime.now()
    rows = team_rows(c, members(c, rec))
    if not rows:
        return None
    lines = [f"📊 ملخص الفريق — {now:%Y-%m-%d}", '']
    tot = {'done_today': 0, 'overdue': 0, 'open': 0, 'not_viewed': 0}
    for r in rows:
        for k in tot:
            tot[k] += r[k]
        flag = ' ⚠️' if r['overdue'] else ''
        lines.append(f"👤 {r['name']}{flag}\n   ✅ أنجز اليوم: {r['done_today']} | ⏰ متأخرة: {r['overdue']}"
                     f" | 📂 مفتوحة: {r['open']} | 👁️ لم تُفتح: {r['not_viewed']}")
    lines += ['', f"المجموع: ✅ {tot['done_today']} | ⏰ {tot['overdue']} | 📂 {tot['open']} | 👁️ {tot['not_viewed']}"]
    return '\n'.join(lines)


def process(now=None, send=None):
    """Called every 30 s by the LAN worker. Sends today's summary once per recipient after the set time."""
    from .db import connect
    from . import workers as w
    send = send or w.send
    now = now or datetime.now()
    c = connect()
    try:
        cfg = settings(c)
        if not _on(cfg['supervisor_summary_enabled']):
            return 0
        if now.hour * 60 + now.minute < _minutes(cfg['supervisor_summary_time']):
            return 0
        day, skip, sent = now.date().isoformat(), off_users(cfg), 0
        for rec in recipients(c, _on(cfg['supervisor_summary_managers'])):
            if rec['user_id'] in skip:
                continue
            key = f"supervisor_summary:{rec['user_id']}:{day}"
            if not w._claim(c, key, rec['employee_id'], 'supervisor_summary'):
                continue
            text = build_text(c, rec, now)
            if not text:  # nobody in the team: nothing to send today
                w._finish(c, key, True)
                continue
            r = send(rec['telegram_chat_id'], text)
            w._notify_log(c, rec['employee_id'], KIND, r)
            w._finish(c, key, bool(r.get('ok')), r.get('error'))
            sent += 1
        return sent
    finally:
        c.close()
