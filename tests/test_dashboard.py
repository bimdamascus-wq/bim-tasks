"""Manager dashboard API (redesign 2026-11-04): the figures behind the KPI strip and the two charts."""
from conftest import client, task


def test_dashboard_has_kpis_trend_and_mix(env):
    boss = client(env, 'boss')
    t = task(boss, env['ids']['موظف آخر'], title='لوحة')
    assert boss.patch(f'/api/tasks/{t}/status', json={'status': 'تم'}).status_code == 200
    task(boss, env['ids']['موظف آخر'], title='مفتوحة للوحة')
    d = boss.get('/api/v2/dashboard').json()
    for k in ('open', 'late', 'done_today', 'on_time', 'due_today', 'unassigned', 'unseen', 'done_week'):
        assert k in d['stats'], k
    assert len(d['trend']) == 14 and d['trend'][-1]['created'] >= 2 and d['trend'][-1]['done'] >= 1
    assert sum(d['mix'].values()) == d['stats']['open']
    assert all('done7' in m for m in d['team'])
    assert client(env, 'member').get('/api/v2/dashboard').status_code == 403
