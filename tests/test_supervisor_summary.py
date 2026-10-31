"""Daily Telegram summary for supervisors (app/supervisor_summary.py)."""
from datetime import datetime, timedelta
from conftest import client, task


def test_supervisor_gets_team_summary_once_a_day(env):
    from app import supervisor_summary as ss
    m, ids = env['m'], env['ids']
    boss = client(env, 'boss')
    member = ids['عضو فريق']
    done = task(boss, member, title='منجزة')
    assert boss.patch(f'/api/tasks/{done}/status', json={'status': 'تم'}).status_code == 200
    late = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
    task(boss, member, title='متأخرة', due_at=late)
    task(boss, ids['موظف آخر'], title='خارج الفريق')
    c = m.connect()
    c.execute('UPDATE employees SET telegram_enabled=1')
    c.close()
    out = []
    fake = lambda chat, text, **kw: out.append((chat, text)) or {'ok': True}
    today = datetime.now().replace(second=0, microsecond=0)
    assert ss.process(now=today.replace(hour=17, minute=59), send=fake) == 0 and out == []   # before 18:00
    assert ss.process(now=today.replace(hour=18, minute=0), send=fake) == 1
    chat, text = out[0]
    assert chat == '333'                                  # سامر, the supervisor
    assert 'عضو فريق' in text and 'موظف آخر' not in text and 'سامر' not in text
    row = [l for l in text.splitlines() if 'أنجز اليوم' in l][0]
    assert 'أنجز اليوم: 1' in row and 'متأخرة: 1' in row
    assert ss.process(now=today.replace(hour=20), send=fake) == 0 and len(out) == 1     # once a day

    # Settings: per-supervisor off switch and managers option; only managers may change them.
    samer = client(env, 'samer')
    assert samer.get('/api/supervisor-summary/settings').status_code == 403
    s = boss.get('/api/supervisor-summary/settings').json()
    sup = [p for p in s['people'] if p['role'] == 'supervisor'][0]
    assert sup['team_size'] == 1 and sup['enabled']
    assert boss.put('/api/supervisor-summary/settings', json={'enabled': True, 'time': '25:00'}).status_code == 400
    assert boss.put('/api/supervisor-summary/settings', json={'enabled': True, 'time': '18:00', 'managers': True,
                                                             'off': [sup['user_id']]}).status_code == 200
    c = m.connect()
    c.execute("DELETE FROM shift_dispatch_log WHERE kind='supervisor_summary'")
    c.close()
    out.clear()
    ss.process(now=today.replace(hour=18, minute=5), send=fake)
    chats = {x[0] for x in out}
    assert '333' not in chats and '111' in chats            # supervisor switched off; admin gets everyone
    assert 'خارج الفريق' not in out[0][1] and 'موظف آخر' in [x for x in out if x[0] == '111'][0][1]
    prev = boss.get(f"/api/supervisor-summary/preview?user_id={sup['user_id']}").json()['text']
    assert 'عضو فريق' in prev
    boss.put('/api/supervisor-summary/settings', json={'enabled': True, 'time': '18:00'})
