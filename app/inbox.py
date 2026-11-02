"""Notification bell in the top bar (big_update, owner 2026-11-02).

For managers (whole company) and supervisors (their team only): a task became late, a new comment or attachment,
a task was completed, a new task arrived from Telegram or the iPhone shortcut. Telegram notifications are untouched.

Items are not written at each place where these things happen (there are many: dashboard, Telegram buttons, iPhone,
group tasks...). Instead `scan()` reads what was added since the last scan (task_events, task_comments,
task_attachments, tasks) and writes one row per person. It runs from the LAN worker every 30 s and before the bell is
read, in either process. It is idempotent: UNIQUE(user_id, dedup) + INSERT OR IGNORE, so two processes scanning at
the same moment cannot create duplicates.
"""
import json
import threading
import time
from fastapi import HTTPException, Request
from . import access
from .db import connect

ROLES = ('admin', 'deputy', 'supervisor')
CURSOR = 'inbox.cursor'
LATE_WINDOW_HOURS = 48  # a task that became late within this window gets an item (older ones were late before)
_last_scan = 0.0
_lock = threading.Lock()


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS inbox_items(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
      kind TEXT NOT NULL,
      text TEXT NOT NULL,
      dedup TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      read_at TEXT,
      UNIQUE(user_id, dedup))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_inbox_user ON inbox_items(user_id, read_at, id)')


def _recipients(c):
    return [dict(r) for r in c.execute('''SELECT u.id user_id, u.employee_id, r.code role_code FROM users u JOIN roles r ON r.id=u.role_id
        WHERE u.is_active=1 AND u.deleted_at IS NULL AND r.code IN ('admin','deputy','supervisor')''')]


def _sees(c, person, task_id):
    if person['role_code'] in access.MANAGERS:
        return True
    return access.can_view_task(c, person, task_id)


def _audit_actor(c, task_id, at):
    """User who changed a task from a page at time `at` (UTC, same clock as task_events.created_at)."""
    r = c.execute('''SELECT user_id FROM audit_logs WHERE entity_type='task' AND entity_id=? AND
        action IN ('task_status','task_update','participant_status') AND
        ABS(strftime('%s',created_at)-strftime('%s',?))<=5 ORDER BY id DESC LIMIT 1''', (str(task_id), at)).fetchone()
    return r[0] if r else None


def _users_of_employee(c, employee_id):
    if not employee_id:
        return set()
    return {r[0] for r in c.execute('SELECT id FROM users WHERE employee_id=?', (employee_id,))}


def _title(c, task_id):
    r = c.execute("SELECT t.title, COALESCE(e.name,'') name FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE t.id=?", (task_id,)).fetchone()
    return (r[0], r[1]) if r else ('', '')


def _add(c, people, task_id, kind, text, dedup, skip=()):
    for p in people:
        if p['user_id'] in skip or not _sees(c, p, task_id):
            continue
        c.execute('INSERT OR IGNORE INTO inbox_items(user_id,task_id,kind,text,dedup) VALUES(?,?,?,?,?)',
                  (p['user_id'], task_id, kind, text, dedup))


def _max(c, table):
    return c.execute(f'SELECT COALESCE(MAX(id),0) FROM {table}').fetchone()[0]


def scan(c=None, force=False):
    """Write bell items for everything new since the last scan. Never raises (the bell must not break a page)."""
    global _last_scan
    if not force and time.monotonic() - _last_scan < 15:
        return
    if not _lock.acquire(blocking=False):
        return
    own = c is None
    try:
        _last_scan = time.monotonic()
        if own:
            c = connect()
        r = c.execute('SELECT value FROM settings WHERE key=?', (CURSOR,)).fetchone()
        if not r:  # first run: start from now, do not fill the bell with history
            cur = {'ev': _max(c, 'task_events'), 'cm': _max(c, 'task_comments'), 'at': _max(c, 'task_attachments'), 'tk': _max(c, 'tasks')}
            c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (CURSOR, json.dumps(cur)))
        else:
            cur = json.loads(r[0])
        people = _recipients(c)
        top = {'ev': _max(c, 'task_events'), 'cm': _max(c, 'task_comments'), 'at': _max(c, 'task_attachments'), 'tk': _max(c, 'tasks')}
        lo = {k: (top[k] if top[k] - cur.get(k, 0) > 5000 else cur.get(k, 0)) for k in top}  # far behind: skip, no flood
        c.execute('BEGIN IMMEDIATE')
        try:
            for ev in c.execute("""SELECT id,task_id,employee_id,source,created_at FROM task_events WHERE id>? AND id<=? AND event_type='status'
                                   AND new_status='تم' ORDER BY id""", (lo['ev'], top['ev'])).fetchall():
                title, name = _title(c, ev['task_id'])
                actor = _audit_actor(c, ev['task_id'], ev['created_at']) if ev['source'] != 'telegram' else None
                skip = {actor} | (_users_of_employee(c, ev['employee_id']) if ev['source'] == 'telegram' else set())
                who = f'{name} ' if name else ''
                _add(c, people, ev['task_id'], 'done', f'{who}أنجز «{title}»', f"done:{ev['id']}", skip)
            for cm in c.execute('SELECT id,task_id,user_id,username_snapshot FROM task_comments WHERE id>? AND id<=? ORDER BY id', (lo['cm'], top['cm'])).fetchall():
                title, _ = _title(c, cm['task_id'])
                _add(c, people, cm['task_id'], 'comment', f"تعليق جديد من {cm['username_snapshot'] or 'موظف'} على «{title}»", f"comment:{cm['id']}", {cm['user_id']})
            for at in c.execute('SELECT id,task_id,user_id,original_name FROM task_attachments WHERE id>? AND id<=? ORDER BY id', (lo['at'], top['at'])).fetchall():
                title, _ = _title(c, at['task_id'])
                _add(c, people, at['task_id'], 'attachment', f"مرفق جديد على «{title}»: {at['original_name'] or ''}".strip(), f"attachment:{at['id']}", {at['user_id']})
            for tk in c.execute('SELECT id,title,source,created_by FROM tasks WHERE id>? AND id<=? ORDER BY id', (lo['tk'], top['tk'])).fetchall():
                src = (tk['source'] or '').lower()
                if src.startswith('telegram') or src.startswith('iphone'):
                    via = 'Telegram' if src.startswith('telegram') else 'الهاتف'
                    _add(c, people, tk['id'], 'new', f"مهمة جديدة من {via}: «{tk['title']}»", f"new:{tk['id']}", {tk['created_by']})
            for t in c.execute(f"""SELECT t.id,t.title,t.due_at,COALESCE(e.name,'') name FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id
                    WHERE t.archived_at IS NULL AND t.status NOT IN ('تم','ملغاة') AND t.due_at IS NOT NULL AND t.due_at<>''
                    AND datetime(replace(t.due_at,'T',' '))<datetime('now','localtime')
                    AND datetime(replace(t.due_at,'T',' '))>=datetime('now','localtime','-{LATE_WINDOW_HOURS} hours')""").fetchall():
                who = f" ({t['name']})" if t['name'] else ''
                _add(c, people, t['id'], 'late', f"تأخرت «{t['title']}»{who}", f"late:{t['id']}:{t['due_at']}")
            c.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(top), CURSOR))
            c.execute('COMMIT')
        except Exception:
            c.execute('ROLLBACK')
            raise
    except Exception:
        pass
    finally:
        if own and c is not None:
            c.close()
        _lock.release()


def _person(req, m):
    u = m.session(req)
    if not u:
        raise HTTPException(401, 'يجب تسجيل الدخول')
    if u['role_code'] not in ROLES:
        raise HTTPException(403, 'التنبيهات للمدير والنائب والمسؤول')
    return u


def items(c, u, limit=30):
    rows = [dict(r) for r in c.execute('''SELECT id,task_id,kind,text,created_at,read_at FROM inbox_items WHERE user_id=?
        ORDER BY id DESC LIMIT ?''', (u['user_id'], int(limit) * 3))]
    person = {'user_id': u['user_id'], 'employee_id': u['employee_id'], 'role_code': u['role_code']}
    rows = [r for r in rows if not r['task_id'] or _sees(c, person, r['task_id'])][:limit]
    unread = c.execute('SELECT COUNT(*) FROM inbox_items WHERE user_id=? AND read_at IS NULL', (u['user_id'],)).fetchone()[0]
    return {'unread': unread, 'items': rows}


def register(app, m):
    @app.get('/api/inbox')
    def get_inbox(req: Request):
        u = _person(req, m)
        scan()
        c = connect()
        try:
            return items(c, u)
        finally:
            c.close()

    @app.post('/api/inbox/{iid}/read')
    def read_one(iid: int, req: Request):
        u = _person(req, m)
        c = connect()
        try:
            c.execute('UPDATE inbox_items SET read_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND read_at IS NULL', (iid, u['user_id']))
            return {'ok': True}
        finally:
            c.close()

    @app.post('/api/inbox/read-all')
    def read_all(req: Request):
        u = _person(req, m)
        c = connect()
        try:
            c.execute('UPDATE inbox_items SET read_at=CURRENT_TIMESTAMP WHERE user_id=? AND read_at IS NULL', (u['user_id'],))
            return {'ok': True}
        finally:
            c.close()
