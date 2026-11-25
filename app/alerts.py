"""«تنبيه» from any Telegram group (owner 2026-11-23; replaces the separate Sheets/Apps Script alert bot, starting fresh).
An allowed person replies «تنبيه» to a message in any group our bot is in -> one task assigned to that person, holding
the original text, its author, the group and a link. The group gets a short fixed answer with the alert number; the
details and the status buttons go to the person in private (the normal task card). Only the alerted message is stored.
Allowed: ANY active employee linked to Telegram (employees.telegram_chat_id = Telegram user id); owner 2026-11-25:
ticking names in a list was too much. The ~200 group members not in the system get a short «not linked» answer.
The alert's status buttons: its own person, or a manager/deputy/supervisor/follow-up-list person.
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
    have = {r[1] for r in c.execute('PRAGMA table_info(telegram_alerts)')}
    for col in ('message_text', 'link', 'reason'):  # added with the alerts page (2026-11-23)
        if col not in have:
            c.execute(f'ALTER TABLE telegram_alerts ADD COLUMN {col} TEXT')


REFUSED_UNLINKED = '⚠️ ما قدرت سجّل التنبيه: حسابك على تلغرام مو مربوط بموظف بنظام BIM Tasks.\nاربط حسابك من الخاص مع البوت (زر Start) وجرّب مرة تانية.'


def is_trigger(text):
    # owner's team types «تنبيه», «تنبية», «#تنبيه», «تنبيه!»: all count
    return str(text or '').strip().strip('#!.؟?،, ').replace('ة', 'ه') == TRIGGER


def reporter(c, telegram_user_id):
    """(employee, user_id or None) when this Telegram user may log alerts (any linked active employee), else None."""
    e = c.execute('SELECT * FROM employees WHERE telegram_chat_id=? AND is_active=1', (str(telegram_user_id or ''),)).fetchone()
    if not e:
        return None
    u = c.execute('SELECT id FROM users WHERE employee_id=? AND deleted_at IS NULL ORDER BY id LIMIT 1', (e['id'],)).fetchone()
    return e, (u['id'] if u else None)


def is_manager(c, e):
    """May act on anyone's alert: manager/deputy/supervisor account, or a person in the follow-up list."""
    from .followup import settings
    allowed = {int(x) for x in settings(c).get('remind_ids') or []}
    u = c.execute('''SELECT u.id, r.code FROM users u JOIN roles r ON r.id=u.role_id
                     WHERE u.employee_id=? AND u.deleted_at IS NULL ORDER BY u.id LIMIT 1''', (e['id'],)).fetchone()
    return e['id'] in allowed or bool(u and u['code'] in ('admin', 'deputy', 'supervisor'))


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
    if not is_trigger(msg.get('text')) or not src or (src.get('from') or {}).get('is_bot'):
        return False  # «تنبيه» on a bot message stays a follow-up comment
    chat, frm = msg.get('chat') or {}, msg.get('from') or {}
    who = reporter(c, frm.get('id'))
    if not who:  # say why (silence left the owner guessing)
        m.send(str(chat.get('id')), REFUSED_UNLINKED)
        return True
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
    cur = c.execute('''INSERT INTO telegram_alerts(chat_id,message_id,reporter_employee_id,author_name,author_telegram_id,group_title,message_text,link)
                       VALUES(?,?,?,?,?,?,?,?)''', (chat_id, int(src['message_id']), e['id'], author, str((src.get('from') or {}).get('id') or ''), group, text[:4000], link))
    n = cur.lastrowid
    title = f'تنبيه ت-{n}: {group or "مجموعة"}'
    notes = '\n'.join(x for x in (f'الرسالة: {text}', f'صاحب الرسالة: {author}', f'المجموعة: {group}',
                                  f'الرابط: {link}' if link else '', 'سبب المشكلة: اكتبه بزر «📝 سبب المشكلة».') if x)
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


# ---------- the old alert options: قيد المعالجة / بانتظار رد / تم الحل / إعادة فتح / سبب المشكلة ----------
# Alert words -> task status. «مفتوحة» = جديدة; «مغلقة» = an archived alert.
ACTIONS = {'work': 'قيد التنفيذ', 'wait': 'بانتظار', 'done': 'تم', 'reopen': 'جديدة'}
LABEL = {'جديدة': 'مفتوحة', 'بدأت': 'قيد المعالجة', 'قيد التنفيذ': 'قيد المعالجة', 'بانتظار': 'بانتظار رد', 'تم': 'تم الحل',
         'ملغاة': 'مغلقة'}


def is_alert(task_id):
    from .db import connect
    c = connect()
    try:
        return bool(c.execute("SELECT 1 FROM tasks WHERE id=? AND source='telegram_alert'", (task_id,)).fetchone())
    finally:
        c.close()


def keyboard(task_id, status):
    """Private card buttons of an alert (the old bot's options)."""
    if status == 'تم':
        rows = [[{'text': '🔁 إعادة فتح', 'callback_data': f'alert:{task_id}:reopen'},
                 {'text': '📝 سبب المشكلة', 'callback_data': f'task:{task_id}:note'}]]
    else:
        rows = [[{'text': '🔧 قيد المعالجة', 'callback_data': f'alert:{task_id}:work'},
                 {'text': '⏳ بانتظار رد', 'callback_data': f'alert:{task_id}:wait'}],
                [{'text': '✅ تم الحل', 'callback_data': f'alert:{task_id}:done'},
                 {'text': '📝 سبب المشكلة', 'callback_data': f'task:{task_id}:note'}]]
    return {'inline_keyboard': rows}


def set_status(c, task_id, action, employee_id=None, source='dashboard'):
    """Any move between the alert states, including reopening a solved one. Returns the new status or None."""
    new = ACTIONS.get(action)
    t = c.execute("SELECT * FROM tasks WHERE id=? AND source='telegram_alert'", (task_id,)).fetchone()
    if not new or not t or t['status'] == new:
        return None
    c.execute("""UPDATE tasks SET status=?, archived_at=CASE WHEN ?='جديدة' THEN NULL ELSE archived_at END,
                 in_progress_at=CASE WHEN ?='قيد التنفيذ' THEN COALESCE(in_progress_at,CURRENT_TIMESTAMP) ELSE in_progress_at END,
                 completed_at=CASE WHEN ?='تم' THEN CURRENT_TIMESTAMP ELSE NULL END, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
              (new, new, new, new, task_id))
    c.execute("INSERT INTO task_events(task_id,employee_id,event_type,old_status,new_status,note,source) VALUES(?,?,'status',?,?,?,?)",
              (task_id, employee_id, t['status'], new, 'إعادة فتح التنبيه' if action == 'reopen' else None, source))
    return new


def save_reason(c, task_id, reason):
    c.execute('UPDATE telegram_alerts SET reason=? WHERE task_id=?', (str(reason or '').strip()[:2000], task_id))


def callback(c, cb, e, m):
    """'alert:ID:work|wait|done|reopen' pressed in the private card: the alert's own person or any allowed reporter."""
    import re
    from .telegram_service import answer_callback
    mo = re.match(r'^alert:(\d+):(work|wait|done|reopen)$', str(cb.get('data') or ''))
    if not mo:
        return False
    tid, action = int(mo.group(1)), mo.group(2)
    t = c.execute("SELECT * FROM tasks WHERE id=? AND source='telegram_alert'", (tid,)).fetchone()
    if not t or (t['employee_id'] != e['id'] and not is_manager(c, e)):
        answer_callback(cb.get('id'), 'هذا التنبيه مو إلك', True)
        return True
    new = set_status(c, tid, action, e['id'], 'telegram')
    answer_callback(cb.get('id'), f'صار: {LABEL[new]}' if new else 'الحالة مسجّلة من قبل')
    if new:
        msg = cb.get('message') or {}
        if msg.get('message_id'):
            m._remember_telegram_task_message(c, tid, str(e['telegram_chat_id']), {'ok': True, 'message_id': msg['message_id']})
        m._refresh_telegram_task_messages(c, tid)
        m._touch_task_sync(c, tid, 'server', 'status')
    return True


def page_data(c, status='', group='', q=''):
    """Everything the /alerts page shows: counts, the list, groups, authors, resolution time, reopenings."""
    migrate(c)
    rows = [dict(r) for r in c.execute("""SELECT a.id n, a.task_id, a.group_title, a.author_name, a.message_text, a.link, a.reason,
          a.created_at, t.status, t.completed_at, t.archived_at, e.name reporter,
          (julianday(t.completed_at)-julianday(a.created_at))*24 hours,
          (SELECT COUNT(*) FROM task_events v WHERE v.task_id=t.id AND v.event_type='status' AND v.old_status='تم'
             AND v.new_status<>'تم') reopened
        FROM telegram_alerts a JOIN tasks t ON t.id=a.task_id LEFT JOIN employees e ON e.id=a.reporter_employee_id
        ORDER BY a.id DESC""")]
    for r in rows:
        r['state'] = 'مغلقة' if r['archived_at'] else LABEL.get(r['status'], r['status'])
    stats = {k: sum(1 for r in rows if r['state'] == k) for k in ('مفتوحة', 'قيد المعالجة', 'بانتظار رد', 'تم الحل', 'مغلقة')}
    hours = [r['hours'] for r in rows if r['state'] == 'تم الحل' and r['hours'] is not None and r['hours'] >= 0]
    stats.update(total=len(rows), groups=len({r['group_title'] for r in rows}), reopened=sum(1 for r in rows if r['reopened']),
                 avg_hours=round(sum(hours) / len(hours), 1) if hours else None)

    def top(key):
        counts = {}
        for r in rows:
            counts[r[key] or '—'] = counts.get(r[key] or '—', 0) + 1
        return sorted(counts.items(), key=lambda x: -x[1])[:8]
    ql = str(q or '').strip()
    shown = [r for r in rows if (not status or r['state'] == status) and (not group or r['group_title'] == group)
             and (not ql or any(ql in str(r[k] or '') for k in ('message_text', 'author_name', 'group_title', 'reason', 'reporter')))]
    from .followup import settings
    ids = [int(x) for x in settings(c).get('remind_ids') or []]
    who = [r['name'] for r in c.execute(f"SELECT name FROM employees WHERE id IN ({','.join('?' * len(ids))}) ORDER BY name", ids)] if ids else []
    return {'stats': stats, 'rows': shown, 'groups': top('group_title'), 'authors': top('author_name'),
            'all_groups': sorted({r['group_title'] or '' for r in rows}), 'reporters': who}
