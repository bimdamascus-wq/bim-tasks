"""Team performance report: numbers, period, supervisor scope, CSV export."""
from datetime import date, datetime, timedelta
from conftest import client, task


def test_team_performance(env):
    m, ids = env['m'], env['ids']
    boss, samer, member = client(env, 'boss'), client(env, 'samer'), client(env, 'member')
    emp = ids['عضو فريق']
    before = boss.get('/api/team-performance').json()
    row0 = {r['employee_id']: r for r in before['employees']}[emp]
    late_due = (datetime.now() - timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M')
    t1 = task(boss, emp, title='أداء ١', due_at=late_due)          # will be completed late
    task(boss, emp, title='أداء ٢', due_at=late_due)               # stays overdue
    t3 = task(boss, emp, title='أداء ٣')                           # completed, no due date
    for t in (t1, t3):
        assert boss.patch(f'/api/tasks/{t}/status', json={'status': 'تم'}).status_code == 200
    d = boss.get('/api/team-performance').json()
    r = {x['employee_id']: x for x in d['employees']}[emp]
    assert r['created'] - row0['created'] == 3
    assert r['completed'] - row0['completed'] == 2
    assert r['completed_late'] - row0['completed_late'] == 1
    assert r['overdue_now'] - row0['overdue_now'] == 1
    assert r['avg_hours'] is not None
    team = [t for t in d['teams'] if t['supervisor'] == 'samer'][0]
    assert team['members'] == 1 and team['completed'] == r['completed']

    # Supervisor: own team (and own record) only; employee: no access.
    s = samer.get('/api/team-performance').json()
    assert {x['employee_id'] for x in s['employees']} <= {emp, ids['سامر']} and ids['موظف آخر'] not in {x['employee_id'] for x in s['employees']}
    assert member.get('/api/team-performance').status_code == 403
    assert member.get('/team-performance', follow_redirects=False).status_code in (303, 403)
    assert samer.get('/team-performance').status_code == 200

    # Period outside today: nothing created/completed; bad input refused.
    old = (date.today() - timedelta(days=400)).isoformat()
    p = boss.get(f'/api/team-performance?date_from={old}&date_to={old}').json()
    assert sum(x['created'] + x['completed'] for x in p['employees']) == 0
    assert boss.get('/api/team-performance?date_from=2026-02-01&date_to=2026-01-01').status_code == 400
    assert boss.get('/api/team-performance?date_from=x').status_code == 400

    csv = boss.get('/api/team-performance.csv')
    assert csv.status_code == 200 and csv.text.startswith('﻿') and 'عضو فريق' in csv.text
    assert 'attachment' in csv.headers['content-disposition']
