"""Group upgraded to a supergroup (report 2026-11-10: 119 follow-up messages failed with «group chat was upgraded to a
supergroup chat»). The service must follow migrate_to_chat_id, store it and resend."""
import json
from conftest import client

OLD, NEW = '-4000000518', '-1003000000048'


def test_send_follows_supergroup_migration(env, monkeypatch):
    m = env['m']
    import app.telegram_service as ts
    c = m.connect()
    c.execute("INSERT INTO telegram_groups(name,chat_id) VALUES('ترقية',?)", (OLD,))
    c.execute("INSERT INTO telegram_outbox(chat_id,message_text,source_type,status,attempts,max_attempts) VALUES(?,?,?,?,0,5)",
              (OLD, 'بالطابور', 'followup', 'pending'))
    c.close()
    calls = []

    def fake_once(method, payload=None, timeout=20):
        calls.append((method, str(payload.get('chat_id'))))
        if str(payload.get('chat_id')) == OLD:
            raw = {'ok': False, 'error_code': 400, 'description': 'Bad Request: group chat was upgraded to a supergroup chat',
                   'parameters': {'migrate_to_chat_id': int(NEW)}}
            return {'ok': False, 'error': str(raw), 'raw': raw}
        return {'ok': True, 'result': {'message_id': 9}, 'raw': {}}
    monkeypatch.setattr(ts, '_api_once', fake_once)
    monkeypatch.setattr(ts, '_token', lambda: 'x')
    r = ts._api('sendMessage', {'chat_id': OLD, 'text': 'مرحبا'})
    assert r['ok'] and calls == [('sendMessage', OLD), ('sendMessage', NEW)]
    c = m.connect()
    assert c.execute("SELECT chat_id FROM telegram_groups WHERE name='ترقية'").fetchone()[0] == NEW
    assert c.execute("SELECT COUNT(*) FROM telegram_outbox WHERE chat_id=? AND status='pending'", (OLD,)).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM audit_logs WHERE action='telegram_group_migrated'").fetchone()[0] == 1
    c.close()
    # edits are not retried (old message ids do not exist in the supergroup)
    calls.clear()
    c = m.connect(); c.execute("UPDATE telegram_groups SET chat_id=? WHERE name='ترقية'", (OLD,)); c.close()
    assert not ts.edit_message(OLD, 5, 'x')['ok'] and calls == [('editMessageText', OLD)]


def test_webhook_migration_message(env):
    m = env['m']
    c = m.connect(); c.execute("INSERT INTO telegram_groups(name,chat_id) VALUES('ويبهوك','-4111')"); c.close()
    cl = client(env)
    r = cl.post('/api/telegram/webhook', headers={'x-telegram-bot-api-secret-token': 'x' * 32},
                json={'update_id': 990001, 'message': {'chat': {'id': -4111, 'type': 'group'}, 'migrate_to_chat_id': -1004111}})
    assert r.status_code == 200, r.text
    c = m.connect()
    assert c.execute("SELECT chat_id FROM telegram_groups WHERE name='ويبهوك'").fetchone()[0] == '-1004111'
    c.close()
