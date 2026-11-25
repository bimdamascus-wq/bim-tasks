"""«تنبيه» reply in any group -> one task for the reporter (owner 2026-11-23)."""
from conftest import client

HOOK = {'x-telegram-bot-api-secret-token': 'x' * 32}
GROUP = {'id': -1001234567, 'type': 'supergroup', 'title': 'مجموعة الصالة'}


def _upd(uid, frm_id, src_id, text='تنبيه', src_from=None):
    return {'update_id': uid, 'message': {'message_id': src_id + 1000, 'chat': GROUP, 'from': {'id': frm_id, 'first_name': 'x'},
            'text': text, 'reply_to_message': {'message_id': src_id, 'chat': GROUP, 'text': 'الرف فاضي من الصبح',
                                               'from': src_from or {'id': 9001, 'first_name': 'خالد', 'username': 'khaled'}}}}


def test_alert_creates_one_task_and_answers(env):
    import app.telegram_queue as tq
    sent = []
    tq.low_level_send = lambda chat, text, **kw: sent.append((str(chat), text)) or {'ok': True, 'message_id': 1, 'result': {'message_id': 1}}
    m = env['m']; cl = client(env)
    c = m.connect(); c.execute("UPDATE employees SET telegram_enabled=1"); c.close()
    assert cl.post('/api/telegram/webhook', headers=HOOK, json=_upd(880001, 333, 50)).status_code == 200  # samer = supervisor
    c = m.connect()
    t = c.execute("SELECT * FROM tasks WHERE source='telegram_alert'").fetchone()
    assert t and t['employee_id'] == env['ids']['سامر'] and 'الرف فاضي' in t['notes'] and 'خالد (@khaled)' in t['notes']
    assert 'https://t.me/c/1234567/50' in t['notes'] and t['priority'] == 'عالية'
    c.close()
    assert any(ch == str(GROUP['id']) and 'تم تسجيل تنبيه إداري' in tx for ch, tx in sent)
    assert any(ch == '333' for ch, _ in sent)  # private card to the reporter
    # same message again: no second task
    cl.post('/api/telegram/webhook', headers=HOOK, json=_upd(880002, 333, 50))
    # an employee account (not allowed while remind_ids is empty): refused with the reason
    cl.post('/api/telegram/webhook', headers=HOOK, json=_upd(880003, 444, 51))
    assert any('ما عندك صلاحية' in tx for _, tx in sent)
    # a Telegram user not linked to any employee: refused with the reason
    cl.post('/api/telegram/webhook', headers=HOOK, json=_upd(880005, 777777, 53))
    assert any('مو مربوط' in tx for _, tx in sent)
    # «تنبية» (ة) counts as «تنبيه»
    cl.post('/api/telegram/webhook', headers=HOOK, json=_upd(880006, 333, 54, text='تنبية'))
    # «تنبيه» on a bot message is not an alert
    cl.post('/api/telegram/webhook', headers=HOOK, json=_upd(880004, 333, 52, src_from={'id': 1, 'is_bot': True}))
    c = m.connect()
    assert c.execute("SELECT COUNT(*) FROM tasks WHERE source='telegram_alert'").fetchone()[0] == 2
    c.close()
    assert any('مسجّل من قبل' in tx for _, tx in sent)


def test_alert_page_and_old_options(env):
    """/alerts page data + the old bot's options: قيد المعالجة، بانتظار رد، تم الحل، إعادة فتح، سبب المشكلة."""
    import app.telegram_queue as tq, app.telegram_service as ts
    tq.low_level_send = lambda chat, text, **kw: {'ok': True, 'message_id': 1, 'result': {'message_id': 1}}
    answers = []
    ts.answer_callback = lambda cbid, text='', show_alert=False: answers.append(text) or {'ok': True}
    m = env['m']; boss = client(env, 'boss'); hook = client(env)
    c = m.connect(); c.execute("UPDATE employees SET telegram_enabled=1"); c.close()
    hook.post('/api/telegram/webhook', headers=HOOK, json=_upd(880101, 333, 70))
    d = boss.get('/api/alerts').json()
    row = next(r for r in d['rows'] if 'الرف فاضي' in (r['message_text'] or ''))
    tid = row['task_id']
    assert row['state'] == 'مفتوحة' and d['stats']['total'] >= 1 and row['group_title'] == 'مجموعة الصالة'
    # private card buttons are the alert ones
    from app.main import _telegram_task_keyboard
    assert 'alert:%d:wait' % tid in str(_telegram_task_keyboard(tid, 'جديدة'))
    # Telegram: بانتظار رد then تم الحل by the reporter (samer, chat 333)
    for i, act in enumerate(('wait', 'done')):
        hook.post('/api/telegram/webhook', headers=HOOK, json={'update_id': 880110 + i, 'callback_query': {
            'id': f'cb{i}', 'from': {'id': 333}, 'data': f'alert:{tid}:{act}', 'message': {'message_id': 5}}})
    assert boss.get('/api/alerts?status=تم الحل').json()['rows'][0]['task_id'] == tid
    # web: reopen + reason
    assert boss.post(f'/api/alerts/{tid}/status', json={'action': 'reopen'}).json()['status'] == 'جديدة'
    assert boss.post(f'/api/alerts/{tid}/reason', json={'reason': 'المورد ما بعت'}).status_code == 200
    r = next(x for x in boss.get('/api/alerts').json()['rows'] if x['task_id'] == tid)
    assert r['state'] == 'مفتوحة' and r['reopened'] == 1 and r['reason'] == 'المورد ما بعت'
    # employees cannot open the page
    assert client(env, 'member').get('/api/alerts').status_code == 403
    assert boss.get('/alerts').status_code == 200
