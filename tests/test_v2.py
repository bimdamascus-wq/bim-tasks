"""Interface v2 (owner 2026-11-23): new pages answer for managers, stay closed to employees, login lands on /v2."""
from conftest import client


def test_v2_pages(env):
    boss, samer, member = client(env, 'boss'), client(env, 'samer'), client(env, 'member')
    for p in ('/v2', '/v2/tasks', '/v2/alerts', '/v2/team', '/v2/recurring', '/v2/reports', '/v2/settings'):
        r = boss.get(p)
        assert r.status_code == 200 and 'v2.css' in r.text and 'bim-ds.css' not in r.text, p
    assert samer.get('/v2/tasks').status_code == 200
    assert member.get('/v2/tasks', follow_redirects=False).status_code in (303, 403)
    r = client(env).post('/login', data={'username': 'samer', 'password': 'Team-Pass-2026x'}, follow_redirects=False)
    assert r.headers['location'] == '/v2'
    r = client(env).post('/login', data={'username': 'member', 'password': 'Team-Pass-2026x'}, follow_redirects=False)
    assert r.headers['location'] == '/my-work'
