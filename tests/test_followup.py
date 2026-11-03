"""Follow-up group on Telegram (app/followup.py): everything about a task also appears in the group."""
import time
from datetime import datetime
from conftest import client, task

GROUP = '-100777'
HOOK = {'x-telegram-bot-api-secret-token': 'x' * 32}


def _setup(env):
    import app.telegram_queue as tq, app.telegram_service as ts
    log = {'send': [], 'edit': [], 'answer': []}
    seq = iter(range(5000, 9000))

    def fake_send(chat, text, reply_markup=None, **kw):
        mid = next(seq); log['send'].append((str(chat), text, reply_markup, mid))
        return {'ok': True, 'message_id': mid, 'result': {'message_id': mid}}
    tq.low_level_send = fake_send
    tq.edit_message = lambda chat, mid, text, reply_markup=None: log['edit'].append((str(chat), mid, text, reply_markup)) or {'ok': True}
    ts.answer_callback = lambda cbid, text='', show_alert=False: log['answer'].append(text) or {'ok': True}
    m = env['m']; c = m.connect()
    c.execute("INSERT OR IGNORE INTO telegram_groups(name,chat_id) VALUES('المتابعة',?)", (GROUP,))
    gid = c.execute('SELECT id FROM telegram_groups WHERE chat_id=?', (GROUP,)).fetchone()[0]
    c.execute('UPDATE employees SET telegram_enabled=1')
    c.close()
    return log, gid


def _run(env):
    from app import followup, telegram_queue
    followup.process(); telegram_queue.process_queue(200)


def _group(log):
    return [s for s in log['send'] if s[0] == GROUP]


def _buttons(markup):
    return [b.get('callback_data') for row in (markup or {}).get('inline_keyboard', []) for b in row]


def test_followup_group_mirrors_task_and_edits_card(env):
    m, ids = env['m'], env['ids']
    log, gid = _setup(env)
    boss = client(env, 'boss')
    member = ids['موظف آخر']
    assert boss.put('/api/followup', json={'enabled': True, 'group_id': 999999}).status_code == 400
    r = boss.put('/api/followup', json={'enabled': True, 'group_id': gid, 'employee_ids': [], 'digest': False})
    assert r.status_code == 200, r.text
    assert client(env, 'member').get('/api/followup').status_code == 403
    _run(env)  # nothing old is posted when switched on
    assert _group(log) == []

    tid = task(boss, member, title='تقرير الموقع', priority='عاجلة')
    _run(env)
    cards = [s for s in _group(log) if 'تقرير الموقع' in s[1]]
    assert len(cards) == 1, _group(log)
    card = cards[0]
    assert '🆕 مهمة جديدة' in card[1] and 'موظف آخر' in card[1] and '✍️ من: مدير النظام' in card[1]
    assert f'fu:remind:{tid}' in _buttons(card[2])
    c = m.connect()
    assert c.execute('SELECT message_id FROM followup_messages WHERE task_id=?', (tid,)).fetchone()[0] == card[3]
    c.close()

    # Employee presses «✅ تم» on Telegram -> group gets "أنجز" and the card is edited without the remind button.
    log['send'].clear()
    r = env['TestClient'](m.app, base_url='https://testserver').post('/api/telegram/webhook', headers=HOOK, json={
        'update_id': int(time.time() * 1000) % 10**9, 'callback_query': {'id': 'cb1', 'data': f'task:{tid}:done',
        'from': {'id': 555}, 'message': {'chat': {'id': 555}, 'message_id': 1}}})
    assert r.status_code == 200, r.text
    c = m.connect(); assert c.execute('SELECT status FROM tasks WHERE id=?', (tid,)).fetchone()[0] == 'تم'; c.close()
    _run(env)
    assert any('أنجز' in s[1] and 'تقرير الموقع' in s[1] for s in _group(log)), _group(log)
    edits = [e for e in log['edit'] if e[0] == GROUP and e[1] == card[3]]
    assert edits and 'الحالة: تم' in edits[-1][2] and f'fu:remind:{tid}' not in _buttons(edits[-1][3])

    # Same events again: nothing posted twice.
    n = len(_group(log)); _run(env); assert len(_group(log)) == n

    # Comment from the page, edit, reassignment and archive are all mirrored.
    t2 = task(boss, member, title='جرد المستودع')
    _run(env); log['send'].clear()
    assert boss.post(f'/api/tasks/{t2}/comments', json={'body': 'لا تنسى الصور'}).status_code == 200
    assert boss.patch(f'/api/tasks/{t2}', json={'priority': 'عالية'}).status_code == 200
    assert boss.patch(f'/api/tasks/{t2}', json={'employee_id': ids['عضو فريق']}).status_code == 200
    assert boss.delete(f'/api/tasks/{t2}').status_code == 200
    _run(env)
    texts = '\n---\n'.join(s[1] for s in _group(log))
    assert 'لا تنسى الصور' in texts
    assert 'الأولوية: عادي ← عالية' in texts
    assert 'نُقلت' in texts and 'عضو فريق' in texts and 'موظف آخر' in texts
    assert 'أُرشفت' in texts
    assert boss.post(f'/api/tasks/{t2}/restore').status_code == 200
    _run(env)
    assert any('رجعت' in s[1] and 'جرد المستودع' in s[1] for s in _group(log))


def test_followup_remind_button_rules(env):
    m, ids = env['m'], env['ids']
    log, gid = _setup(env)
    boss = client(env, 'boss')
    assert boss.put('/api/followup', json={'enabled': True, 'group_id': gid, 'employee_ids': [], 'digest': False}).status_code == 200
    tid = task(boss, ids['موظف آخر'], title='اتصال بالمورد')
    _run(env)
    hook = env['TestClient'](m.app, base_url='https://testserver')

    def press(chat, n):
        return hook.post('/api/telegram/webhook', headers=HOOK, json={'update_id': 700000 + n, 'callback_query': {
            'id': f'r{n}', 'data': f'fu:remind:{tid}', 'from': {'id': 9999, 'first_name': 'ريم'},
            'message': {'chat': {'id': chat}, 'message_id': 1}}})

    log['send'].clear()
    assert press(333, 1).status_code == 200                      # from another chat: refused
    assert 'من مجموعة المتابعة فقط' in log['answer'][-1]
    assert not [s for s in log['send'] if s[0] == '555']
    assert press(int(GROUP), 2).status_code == 200               # from the group: employee reminded
    assert log['answer'][-1] == 'انبعت التذكير'
    assert [s for s in log['send'] if s[0] == '555' and 'اتصال بالمورد' in s[1]]
    _run(env)
    assert any('ريم ذكّر موظف آخر' in s[1] for s in _group(log))
    assert press(int(GROUP), 3).status_code == 200               # again within 15 min: refused
    assert 'ربع ساعة' in log['answer'][-1]


def test_followup_scope_list(env):
    m, ids = env['m'], env['ids']
    log, gid = _setup(env)
    boss = client(env, 'boss')
    assert boss.put('/api/followup', json={'enabled': True, 'group_id': gid, 'employee_ids': [ids['موظف آخر']], 'digest': False}).status_code == 200
    _run(env); log['send'].clear()
    task(boss, ids['عضو فريق'], title='خارج القائمة')
    task(boss, ids['موظف آخر'], title='داخل القائمة')
    _run(env)
    texts = ' '.join(s[1] for s in _group(log))
    assert 'داخل القائمة' in texts and 'خارج القائمة' not in texts
    # Morning list: once a day, only the chosen employees.
    from app import followup
    c = m.connect()
    cfg = followup.settings(c)
    text = followup.digest(c, cfg, datetime.now())
    c.close()
    assert 'موظف آخر' in text and 'عضو فريق' not in text
    boss.put('/api/followup', json={'enabled': False})


def test_followup_problem_note_and_system_reminder(env):
    m, ids = env['m'], env['ids']
    log, gid = _setup(env)
    boss = client(env, 'boss')
    assert boss.put('/api/followup', json={'enabled': True, 'group_id': gid, 'employee_ids': [], 'digest': False}).status_code == 200
    tid = task(boss, ids['موظف آخر'], title='صيانة المكيف')
    _run(env); log['send'].clear()
    hook = env['TestClient'](m.app, base_url='https://testserver')
    assert hook.post('/api/telegram/webhook', headers=HOOK, json={'update_id': 810001, 'callback_query': {
        'id': 'n1', 'data': f'task:{tid}:note', 'from': {'id': 555}, 'message': {'chat': {'id': 555}, 'message_id': 1}}}).status_code == 200
    assert hook.post('/api/telegram/webhook', headers=HOOK, json={'update_id': 810002, 'message': {
        'chat': {'id': 555, 'type': 'private'}, 'from': {'id': 555}, 'text': 'القطعة مو موجودة'}}).status_code == 200
    c = m.connect(); m._notify_employee(c, tid, 'تذكير تلقائي', notice_category='reminders'); c.close()
    _run(env)
    texts = '\n---\n'.join(s[1] for s in _group(log))
    assert 'كتب مشكلة' in texts and 'القطعة مو موجودة' in texts
    assert texts.count('القطعة مو موجودة') == 1          # the Telegram comment row is not posted a second time
    assert 'النظام ذكّر موظف آخر' in texts and 'تذكير تلقائي' in texts
    boss.put('/api/followup', json={'enabled': False})
