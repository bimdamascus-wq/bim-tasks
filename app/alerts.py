"""«تنبيه» from any Telegram group (owner 2026-11-23; replaces the separate Sheets/Apps Script alert bot, starting fresh).
An allowed person replies «تنبيه» to a message in any group our bot is in -> one task assigned to that person, holding
the original text, its author, the group and a link. The group gets a short fixed answer with the alert number; the
details and the status buttons go to the person in private (the normal task card). Only the alerted message is stored.
Allowed: a linked employee (employees.telegram_chat_id = Telegram user id) who is in the follow-up «remind_ids» list,
or, when that list is empty, whose account is a manager, deputy or supervisor. Anyone else is ignored silently.
The bot sees replies to other people's messages only with privacy mode OFF in BotFather (or as a group admin)."""
TRIGGER = 'تنبيه'
GROUP_ANSWER = '❌ تم تسجيل تنبيه إداري، والموضوع قيد المتابعة.\nرقم التنبيه: {n}'


def migrate(c):
    # One row per alerted message: the second «تنبيه» on the same message does not create a second task.
    c.execute('''CREATE TABLE IF NOT EXISTS telegram_alerts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      chat_id TEXT NOT NULL, message_id INTEGER NOT NULL,
      task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
      reporter_employee_id INTEGER, author_name TEXT, author_telegram_id TEXT, group_title TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(chat_id, message_id))''')


def reporter(c, telegram_user_id):
    """(employee, user_id or None) when this Telegram user may log alerts, else None."""
    e = c.execute('SELECT * FROM employees WHERE telegram_chat_id=? AND is_active=1', (str(telegram_user_id or ''),)).fetchone()
    if not e:
        return None
    from .followup import settings
    allowed = {int(x) for x in settings(c).get('remind_ids') or []}
    u = c.execute('''SELECT u.id, r.code FROM users u JOIN roles r ON r.id=u.role_id
                     WHERE u.employee_id=? AND u.deleted_at IS NULL ORDER BY u.id LIMIT 1''', (e['id'],)).fetchone()
    ok = e['id'] in allowed if allowed else bool(u and u['code'] in ('admin', 'deputy', 'supervisor'))
    return (e, u['id'] if u else None) if ok else None


def message_link(chat, message_id):
    if chat.get('username'):
        return f"https://t.me/{chat['username']}/{message_id}"
    cid = str(chat.get('id') or '')
    return f"https://t.me/c/{cid[4:]}/{message_id}" if cid.startswith('-100') else ''


def _author(frm):
    name = ' '.join(x for x in (frm.get('first_name'), frm.get('last_name')) if x) or 'بدون اسم'
    return name + (f" (@{frm['username']})" if frm.get('username') else '')


def _original_text(src):
    text = str(src.get('text') or src.get('caption') or '').strip()
    label = next((w for k, w in (('photo', 'صورة'), ('voice', 'رسالة صوتية'), ('video', 'فيديو'), ('document', 'ملف'),
                                 ('sticker', 'ملصق')) if src.get(k)), 'رسالة بلا نص')
    return text or f'[{label}]'


def handle(c, msg, m):
    """Called by the webhook for every group message. Returns True when it was an alert (handled), else False."""
    src = msg.get('reply_to_message') or {}
    if str(msg.get('text') or '').strip() != TRIGGER or not src or (src.get('from') or {}).get('is_bot'):
        return False  # «تنبيه» on a bot message stays a follow-up comment
    chat, frm = msg.get('chat') or {}, msg.get('from') or {}
    who = reporter(c, frm.get('id'))
    if not who:
        return True  # not allowed: say nothing in the group
    e, user_id = who
    migrate(c)
    chat_id = str(chat.get('id'))
    old = c.execute('SELECT id FROM telegram_alerts WHERE chat_id=? AND message_id=?', (chat_id, int(src['message_id']))).fetchone()
    if old:
        m.send(chat_id, f'هالرسالة عليها تنبيه مسجّل من قبل.\nرقم التنبيه: ت-{old["id"]}')
        return True
    group = str(chat.get('title') or '').strip()
    author = _author(src.get('from') or {})
    text = _original_text(src)
    link = message_link(chat, src['message_id'])
    cur = c.execute('''INSERT INTO telegram_alerts(chat_id,message_id,reporter_employee_id,author_name,author_telegram_id,group_title)
                       VALUES(?,?,?,?,?,?)''', (chat_id, int(src['message_id']), e['id'], author, str((src.get('from') or {}).get('id') or ''), group))
    n = cur.lastrowid
    title = f'تنبيه ت-{n}: {group or "مجموعة"}'
    notes = '\n'.join(x for x in (f'الرسالة: {text}', f'صاحب الرسالة: {author}', f'المجموعة: {group}',
                                  f'الرابط: {link}' if link else '', 'سبب المشكلة: اكتبه بزر «⚠️ مشكلة / ملاحظة».') if x)
    c.execute('''INSERT INTO tasks(task_code,employee_id,title,notes,priority,status,created_by,source)
                 VALUES(?,?,?,?,'عالية','جديدة',?,'telegram_alert')''', (f'ALERT-{n}', e['id'], title[:200], notes, user_id))
    tid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('UPDATE telegram_alerts SET task_id=? WHERE id=?', (tid, n))
    m._save_reminder(c, tid, {'type': 'بدون'})
    c.execute("INSERT INTO task_events(task_id,employee_id,event_type,new_status,note,source) VALUES(?,?,'created','جديدة',?,'telegram_alert')",
              (tid, e['id'], f'تنبيه من مجموعة {group}'))
    m._touch_task_sync(c, tid, 'server', 'create')
    m.send(chat_id, GROUP_ANSWER.format(n=f'ت-{n}'))
    m._notify_employee(c, tid, 'تنبيه جديد')  # private card with the status buttons
    return True
