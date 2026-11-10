"""«🔔 ذكّر الآن (تلغرام)» and «💬 ذكّر على الواتس» on the task page (owner 2026-11-04)."""
from urllib.parse import unquote
from conftest import client, task


def _sent(env, chat):
    import app.telegram_queue as tq
    log = []
    tq.low_level_send = lambda c, text, **kw: log.append((str(c), text, kw.get('reply_markup'))) or {'ok': True, 'result': {'message_id': 77}}
    return log


def test_remind_telegram_and_whatsapp(env):
    m, ids = env['m'], env['ids']
    boss = client(env, 'boss')
    emp = ids['موظف آخر']
    c = m.connect(); c.execute("UPDATE employees SET telegram_enabled=1, whatsapp='0944123456' WHERE id=?", (emp,)); c.close()
    tid = task(boss, emp, title='تذكير تجريبي', due_at='2026-11-05T14:00')
    log = _sent(env, '555')

    r = boss.post(f'/api/tasks/{tid}/remind', json={'channel': 'telegram'})
    assert r.status_code == 200, r.text
    assert r.json()['employee'] == 'موظف آخر' and not r.json().get('already')
    mine = [x for x in log if x[0] == '555']
    assert mine and '🔔 تذكير بمهمة' in mine[-1][1] and 'تذكير تجريبي' in mine[-1][1]
    assert '2026-11-05T14:00' not in mine[-1][1]  # Arabic date, not ISO
    assert mine[-1][2]  # status buttons stay on the reminder
    n = len(mine)
    # a second click within a minute does not send again
    assert boss.post(f'/api/tasks/{tid}/remind', json={'channel': 'telegram'}).json()['already'] is True
    assert len([x for x in log if x[0] == '555']) == n

    r = boss.post(f'/api/tasks/{tid}/remind', json={'channel': 'whatsapp'})
    assert r.status_code == 200, r.text
    url = r.json()['url']
    assert url.startswith('https://wa.me/963944123456?text=') and 'تذكير تجريبي' in unquote(url)

    c = m.connect()
    evs = [dict(x) for x in c.execute("SELECT * FROM task_events WHERE task_id=? AND event_type='reminded' ORDER BY id", (tid,))]
    acts = c.execute("SELECT COUNT(*) FROM audit_logs WHERE action='task_remind' AND entity_id=?", (tid,)).fetchone()[0]
    c.close()
    assert [e['source'] for e in evs] == ['telegram', 'whatsapp'] and 'واتساب' in evs[1]['note']
    assert acts == 2
    # shows in the task history as «تذكير», not as a delegation
    d = boss.get(f'/api/tasks/{tid}').json()
    assert 'reminded' in [e['event_type'] for e in d['events']] and not d.get('delegations')


def test_remind_refusals(env):
    m, ids = env['m'], env['ids']
    boss = client(env, 'boss')
    tid = task(boss, ids['موظف آخر'], title='خارج الفريق')
    assert boss.post(f'/api/tasks/{tid}/remind', json={'channel': 'sms'}).status_code == 400
    # supervisor: only his team
    assert client(env, 'samer').post(f'/api/tasks/{tid}/remind', json={'channel': 'whatsapp'}).status_code == 403
    # employee has no tasks.edit
    assert client(env, 'other').post(f'/api/tasks/{tid}/remind', json={'channel': 'whatsapp'}).status_code == 403
    # not linked on Telegram -> clear message
    c = m.connect(); c.execute('UPDATE employees SET telegram_enabled=0 WHERE id=?', (ids['عضو فريق'],)); c.close()
    t2 = task(boss, ids['عضو فريق'], title='غير مربوط')
    r = boss.post(f'/api/tasks/{t2}/remind', json={'channel': 'telegram'})
    assert r.status_code == 400 and 'موقوف' in r.json()['detail'] and 'الواتس' in r.json()['detail']
    assert client(env, 'samer').post(f'/api/tasks/{t2}/remind', json={'channel': 'whatsapp'}).status_code == 200
    c = m.connect(); c.execute('UPDATE employees SET telegram_enabled=1 WHERE id=?', (ids['عضو فريق'],)); c.close()
    # finished task
    t3 = task(boss, ids['موظف آخر'], title='منتهية')  # not in the supervisor's team (keeps his daily summary counts)
    assert boss.patch(f'/api/tasks/{t3}/status', json={'status': 'تم'}).status_code == 200
    assert boss.post(f'/api/tasks/{t3}/remind', json={'channel': 'telegram'}).status_code == 400


def test_followup_group_shows_reminder(env):
    from app import followup
    c = env['m'].connect()
    ev = {'event_type': 'reminded', 'note': 'boss ذكّر موظف آخر (واتساب)', 'employee_id': env['ids']['موظف آخر'], 'id': 1}
    t = c.execute('SELECT * FROM tasks ORDER BY id DESC LIMIT 1').fetchone()
    kind, text = followup._event_message(c, ev, t)
    c.close()
    assert kind == 'reminders' and 'تذكير' in text and 'واتساب' in text


def test_block_reason_explains_linked_but_stopped(env):
    """Owner 2026-11-10: the list said «مربوط» but «ذكّر» said «غير مربوط». Now the reason is named."""
    m, ids = env['m'], env['ids']
    boss = client(env, 'boss')
    emp = ids['موظف آخر']
    tid = task(boss, emp, title='سبب واضح')
    c = m.connect(); c.execute('UPDATE employees SET telegram_enabled=0 WHERE id=?', (emp,)); c.close()
    r = boss.post(f'/api/tasks/{tid}/remind', json={'channel': 'telegram'})
    assert r.status_code == 400 and 'إرسال Telegram' in r.json()['detail'] and 'موقوف' in r.json()['detail']
    r = boss.post(f'/api/tasks/{tid}/telegram').json()   # «🔔 تذكير» on /my-team and the profile
    assert r['ok'] is False and 'موقوف' in r['error']
    c = m.connect(); c.execute('UPDATE employees SET telegram_enabled=1 WHERE id=?', (emp,))
    c.execute("INSERT INTO universal_staff_settings(employee_id,channel,capacity_minutes) VALUES(?,'web',480) "
              "ON CONFLICT(employee_id) DO UPDATE SET channel='web'", (emp,)); c.close()
    r = boss.post(f'/api/tasks/{tid}/remind', json={'channel': 'telegram'})
    assert r.status_code == 400 and 'الويب فقط' in r.json()['detail']
    c = m.connect(); c.execute('DELETE FROM universal_staff_settings WHERE employee_id=?', (emp,))
    c.execute("UPDATE employees SET telegram_chat_id=NULL WHERE id=?", (emp,)); c.close()
    r = boss.post(f'/api/tasks/{tid}/remind', json={'channel': 'telegram'})
    assert r.status_code == 400 and 'غير مربوط' in r.json()['detail']
    c = m.connect(); c.execute("UPDATE employees SET telegram_chat_id='555' WHERE id=?", (emp,)); c.close()
    assert boss.post(f'/api/tasks/{tid}/remind', json={'channel': 'telegram'}).status_code == 200
