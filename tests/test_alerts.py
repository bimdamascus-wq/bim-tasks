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
    # an employee account (not allowed while remind_ids is empty): ignored
    cl.post('/api/telegram/webhook', headers=HOOK, json=_upd(880003, 444, 51))
    # «تنبيه» on a bot message is not an alert
    cl.post('/api/telegram/webhook', headers=HOOK, json=_upd(880004, 333, 52, src_from={'id': 1, 'is_bot': True}))
    c = m.connect()
    assert c.execute("SELECT COUNT(*) FROM tasks WHERE source='telegram_alert'").fetchone()[0] == 1
    c.close()
    assert any('مسجّل من قبل' in tx for _, tx in sent)
