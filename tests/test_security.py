"""Security regression tests for roles, scope, entry points, login confirmation, audit chain and backups.

Run on any machine (does not touch the real data/ folder):
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
import os, re, shutil, sqlite3, sys, importlib
from pathlib import Path
import pytest

from conftest import SRC, ADMIN_PW, PW, client, task


# ---------- Phase 1: default deny & page table ----------
def test_every_private_route_rejects_anonymous(env):
    m = env['m']
    from app import access
    anon = env['TestClient'](m.app, base_url='https://testserver', headers={'origin': 'https://testserver'})
    checked = 0
    for r in m.app.routes:
        path = getattr(r, 'path', '')
        if not path or path in access.PUBLIC_PATHS or path.startswith(('/static', '/openapi', '/docs', '/redoc')):
            continue
        url = re.sub(r'\{[^}]+\}', '1', path)
        for method in sorted(getattr(r, 'methods', []) - {'HEAD'}):
            res = anon.request(method, url, follow_redirects=False)
            assert res.status_code in (401, 303), f'{method} {path} -> {res.status_code}'
            if res.status_code == 303:
                assert res.headers['location'] == '/login'
            checked += 1
    assert checked > 150


def test_page_table_matches_what_server_enforces(env):
    from app import access
    for user, role in [('boss', 'admin'), ('dep', 'deputy'), ('samer', 'supervisor'), ('member', 'employee')]:
        cl = client(env, user)
        allowed = access.MATRIX[role]
        for path, code in access.PAGE_PERMS.items():
            res = cl.get(path, follow_redirects=False)
            if code in allowed:
                assert res.status_code in (200, 303), f'{user} {path} -> {res.status_code}'
            else:
                assert res.status_code in (303, 403), f'{user} should not open {path} ({res.status_code})'


# ---------- Phase 1: supervisor scope ----------
def test_supervisor_sees_and_changes_only_team(env):
    ids = env['ids']
    boss, samer = client(env, 'boss'), client(env, 'samer')
    mine, theirs = task(boss, ids['عضو فريق'], title='فريق سامر'), task(boss, ids['موظف آخر'], title='فريق آخر')
    listed = {t['id'] for t in samer.get('/api/tasks').json()}
    assert mine in listed and theirs not in listed
    assert samer.get(f'/api/tasks/{theirs}').status_code == 404
    assert samer.patch(f'/api/tasks/{theirs}', json={'title': 'x'}).status_code == 403
    assert samer.patch(f'/api/tasks/{theirs}/status', json={'status': 'تم'}).status_code == 404
    assert samer.post(f'/api/tasks/{theirs}/restore').status_code == 403
    assert samer.post(f'/api/tasks/{theirs}/telegram').status_code == 403
    assert samer.patch(f'/api/tasks/{mine}', json={'title': 'معدلة'}).status_code == 200
    assert samer.patch(f'/api/tasks/{mine}', json={'employee_id': ids['موظف آخر']}).status_code == 403
    assert samer.post('/api/tasks', json={'title': 'لبرّا', 'employee_id': ids['موظف آخر']}).status_code == 403
    assert samer.post('/api/tasks', json={'title': 'جماعية', 'participants': [ids['عضو فريق'], ids['موظف آخر']]}).status_code == 403
    task(samer, ids['عضو فريق'], title='من سامر')
    emps = {e['id'] for e in samer.get('/api/employees').json()}
    assert emps == {ids['عضو فريق'], ids['سامر']}
    opts = {e['id'] for e in samer.get(f'/api/tasks/{mine}/delegate-options').json()['employees']}
    assert ids['موظف آخر'] not in opts
    assert samer.post(f'/api/tasks/{mine}/delegate', json={'employee_id': ids['موظف آخر'], 'channel': 'whatsapp'}).status_code == 403
    assert samer.post('/api/recurring', json={'title': 'ر', 'employee_id': ids['موظف آخر'], 'recurrence_type': 'يومي', 'run_time': '09:00'}).status_code == 403


def test_mixed_group_task_is_manager_only(env):
    ids = env['ids']
    boss, samer = client(env, 'boss'), client(env, 'samer')
    r = boss.post('/api/tasks', json={'title': 'مختلطة', 'participants': [ids['عضو فريق'], ids['موظف آخر']]})
    tid = r.json()['id']
    assert samer.patch(f'/api/tasks/{tid}', json={'title': 'x'}).status_code in (403, 404)


def test_employee_sees_only_own(env):
    ids = env['ids']
    boss, other = client(env, 'boss'), client(env, 'other')
    t = task(boss, ids['عضو فريق'])
    assert other.get(f'/api/tasks/{t}').status_code == 404
    m = env['m']; c = m.connect()
    for x in other.get('/api/tasks').json():  # own tasks, or group tasks they take part in
        assert x['employee_id'] == ids['موظف آخر'] or c.execute('SELECT 1 FROM task_participants WHERE task_id=? AND employee_id=?', (x['id'], ids['موظف آخر'])).fetchone()
    assert other.post('/api/tasks', json={'title': 'x', 'employee_id': ids['موظف آخر']}).status_code == 403


# ---------- Phase 1: deputy vs administrator ----------
def test_deputy_cannot_touch_sensitive(env):
    dep = client(env, 'dep')
    boss_id = env['m'].connect().execute("SELECT id FROM users WHERE username='boss'").fetchone()[0]
    member_id = env['m'].connect().execute("SELECT id FROM users WHERE username='member'").fetchone()[0]
    assert dep.post('/api/users', json={'username': 'x1', 'password': 'Long-Password-123', 'role_code': 'admin'}).status_code == 403
    assert dep.post('/api/users', json={'username': 'x2', 'password': 'Long-Password-123', 'role_code': 'deputy'}).status_code == 403
    assert dep.patch(f'/api/users/{boss_id}', json={'password': 'Hijack-Password-123'}).status_code == 403
    assert dep.delete(f'/api/users/{boss_id}').status_code == 403
    assert dep.post(f'/api/records/users/{boss_id}/disable', json={}).status_code == 403
    assert dep.patch(f'/api/users/{member_id}', json={'role_code': 'deputy'}).status_code == 403
    assert dep.put(f'/api/access-control/{member_id}', json={'tasks.view': True}).status_code == 403
    assert dep.put('/api/security/backup-settings', json={'folder': '', 'hour': 2, 'keep': 30}).status_code == 403
    assert dep.post('/api/sync/settings', json={'webapp_url': ''}).status_code == 403
    assert dep.post(f'/api/employees/{env["ids"]["بوس"]}/telegram-unlink').status_code == 403
    r = dep.post('/api/users', json={'username': 'newsup', 'password': 'Team-Pass-2026x', 'role_code': 'supervisor'})
    assert r.status_code == 200, r.text
    assert dep.post('/api/users', json={'username': 'weak', 'password': '123456', 'role_code': 'employee'}).status_code == 400


def test_overrides_cannot_exceed_role(env):
    boss = client(env, 'boss')
    samer_id = env['m'].connect().execute("SELECT id FROM users WHERE username='samer'").fetchone()[0]
    assert boss.put(f'/api/access-control/{samer_id}', json={'users.manage': True}).status_code == 400
    assert boss.put(f'/api/access-control/{samer_id}', json={'tasks.delete': True}).status_code == 200
    assert 'tasks.delete' in env['m'].perms(samer_id)
    env['m'].connect().execute('DELETE FROM universal_user_permissions WHERE user_id=?', (samer_id,))


# ---------- Phase 2: entry points ----------
def test_cross_site_post_without_origin_is_refused(env):
    cl = client(env, 'boss')
    res = cl.post('/api/tasks', json={'title': 'x'}, headers={'origin': ''})
    assert res.status_code == 403
    res = cl.post('/api/tasks', json={'title': 'x'}, headers={'origin': 'https://evil.example'})
    assert res.status_code == 403


def test_telegram_webhook_requires_secret(env):
    m = env['m']
    anon = env['TestClient'](m.app, base_url='https://testserver')
    assert anon.post('/api/telegram/webhook', json={'update_id': 1}, headers={'x-telegram-bot-api-secret-token': 'wrong'}).status_code == 401
    saved, m.TELEGRAM_WEBHOOK_SECRET = m.TELEGRAM_WEBHOOK_SECRET, ''
    try:
        assert anon.post('/api/telegram/webhook', json={'update_id': 2}).status_code == 503
    finally:
        m.TELEGRAM_WEBHOOK_SECRET = saved


def test_iphone_wrong_key_is_rate_limited(env):
    anon = env['TestClient'](env['m'].app, base_url='https://testserver', headers={'x-forwarded-for': '9.9.9.9'})
    codes = [anon.post('/api/iphone/task', data={'text': 'x'}, headers={'x-bim-secret': f'bad{i}'}).status_code for i in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_sheet_sync_cannot_repoint_manager_telegram(env):
    m = env['m']
    anon = env['TestClient'](m.app, base_url='https://testserver')
    r = anon.post('/api/sync/push-employee', json={'name': 'بوس', 'telegram_chat_id': '999', 'is_active': 0}, headers={'x-bim-sync-secret': 's' * 32})
    assert r.status_code == 200
    row = m.connect().execute("SELECT telegram_chat_id,is_active FROM employees WHERE name='بوس'").fetchone()
    assert row['telegram_chat_id'] == '111' and row['is_active'] == 1


# ---------- Phase 2: login confirmation ----------
def _last_code(env):
    return re.search(r'الرمز: (\d{6})', env['sent'][-1][1]).group(1)


def test_external_admin_login_needs_telegram_code(env):
    lan = client(env, 'boss')
    phone = client(env, 'boss', external=True)  # a session opened before the confirmation was switched on
    assert phone.get('/api/me').status_code == 200
    ch = lan.post('/api/me/mfa/start', json={'action': 'enable'}).json()['challenge']
    assert env['sent'][-1][0] == '111'
    assert lan.post('/api/me/mfa/confirm', json={'challenge': ch, 'code': _last_code(env)}).json()['enabled'] is True
    assert phone.get('/api/me').status_code == 401  # old session closed
    assert lan.get('/api/me').status_code == 200    # the session that enabled it stays

    ext = env['TestClient'](env['m'].app, base_url='https://testserver', headers={'origin': 'https://testserver', 'cf-ray': 't'})
    r = ext.post('/login', data={'username': 'boss', 'password': ADMIN_PW}, follow_redirects=False)
    assert r.headers['location'] == '/login/verify' and 'bim_tasks_session' not in r.cookies
    assert ext.get('/api/me').status_code == 401
    assert ext.post('/login/verify', data={'code': '000000'}, follow_redirects=False).status_code == 401
    r = ext.post('/login/verify', data={'code': _last_code(env)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/'
    assert ext.get('/api/me').json()['username'] == 'boss'
    # Inside the company nothing changes.
    assert client(env, 'boss').get('/api/me').status_code == 200
    # The code is single-use.
    ext2 = env['TestClient'](env['m'].app, base_url='https://testserver', headers={'origin': 'https://testserver', 'cf-ray': 't'})
    ext2.cookies.set('bim_mfa', 'stale', path='/login')
    assert ext2.post('/login/verify', data={'code': _last_code(env)}, follow_redirects=False).status_code == 400


def test_wrong_codes_burn_the_challenge(env):
    ext = env['TestClient'](env['m'].app, base_url='https://testserver', headers={'origin': 'https://testserver', 'cf-ray': 't'})
    ext.post('/login', data={'username': 'boss', 'password': ADMIN_PW}, follow_redirects=False)
    good = _last_code(env)
    for _ in range(5):
        ext.post('/login/verify', data={'code': '000001' if good != '000001' else '000002'}, follow_redirects=False)
    assert ext.post('/login/verify', data={'code': good}, follow_redirects=False).status_code == 400


# ---------- Phase 2: audit chain ----------
def test_audit_chain_detects_tampering(env):
    from app.security_center import verify_chain
    m = env['m']
    client(env, 'boss').post('/api/tasks', json={'title': 'سجل'})
    c = m.connect()
    ok, checked, _ = verify_chain(c)
    assert ok and checked > 5
    row = c.execute('SELECT id,after_json FROM audit_logs WHERE row_hash IS NOT NULL ORDER BY id LIMIT 1 OFFSET 2').fetchone()
    c.execute("UPDATE audit_logs SET after_json='{\"edited\":1}' WHERE id=?", (row['id'],))
    ok, _, bad = verify_chain(c)
    assert not ok and bad == row['id']
    c.execute('UPDATE audit_logs SET after_json=? WHERE id=?', (row['after_json'], row['id']))
    assert verify_chain(c)[0]


# ---------- Phase 2: off-site backup and restore ----------
def test_offsite_backup_restore_roundtrip(env, tmp_path):
    from app import backup_offsite
    boss = client(env, 'boss')
    assert boss.put('/api/security/backup-settings', json={'folder': str(env['offsite']), 'hour': 2, 'keep': 5}).status_code == 200
    r = boss.post('/api/security/backup-now')
    assert r.status_code == 200, r.text
    f = env['offsite'] / r.json()['file']
    assert f.exists() and f.read_bytes()[:8] == b'BIMBAK01'
    assert b'SQLite format' not in f.read_bytes()  # really encrypted
    with pytest.raises(backup_offsite.BackupError):
        backup_offsite.verify(f, 'wrong passphrase value!!')
    cut = tmp_path / 'cut.bimbak'; cut.write_bytes(f.read_bytes()[:-100])
    with pytest.raises(backup_offsite.BackupError):
        backup_offsite.verify(cut, os.environ['BIM_BACKUP_PASSPHRASE'])
    target = tmp_path / 'restore_root'; (target / 'data').mkdir(parents=True)
    (target / 'data' / 'bim_tasks.db').write_bytes(b'old')
    res = backup_offsite.restore_into(f, os.environ['BIM_BACKUP_PASSPHRASE'], target)
    c = sqlite3.connect(str(target / 'data' / 'bim_tasks.db'))
    assert c.execute("SELECT COUNT(*) FROM users WHERE username='boss'").fetchone()[0] == 1
    assert (Path(res['previous_data']) / 'bim_tasks.db').read_bytes() == b'old'
    sens = boss.get('/api/security/overview').json()
    assert any(x['action'] == 'offsite_backup' for x in sens['sensitive'])
    assert any(c['key'] == 'backup_fresh' and c['ok'] for c in sens['checks'])


def test_backup_folder_inside_server_is_refused(env):
    boss = client(env, 'boss')
    assert boss.put('/api/security/backup-settings', json={'folder': str(env['root'] / 'data'), 'hour': 2, 'keep': 5}).status_code == 400


def test_backup_to_telegram_in_parts(env, tmp_path):
    from app import backup_offsite, security_center, telegram_media
    boss = client(env, 'boss')
    assert boss.put('/api/security/backup-settings', json={'folder': '', 'hour': 2, 'keep': 5}).status_code == 200
    assert boss.put('/api/security/backup-telegram', json={'enabled': True}).status_code == 200
    dep = client(env, 'dep')
    assert dep.put('/api/security/backup-telegram', json={'enabled': False}).status_code == 403
    uploads = []
    saved_upload, saved_part = telegram_media._curl_upload, security_center.TELEGRAM_PART
    def fake_upload(method, chat, path, field, caption=''):
        copy = tmp_path / path.name; copy.write_bytes(path.read_bytes())
        uploads.append((chat, copy, caption)); return {'ok': True}
    telegram_media._curl_upload, security_center.TELEGRAM_PART = fake_upload, 4000
    try:
        r = boss.post('/api/security/backup-now')
    finally:
        telegram_media._curl_upload, security_center.TELEGRAM_PART = saved_upload, saved_part
    assert r.status_code == 200, r.text
    assert r.json()['telegram']['parts'] > 1 and {u[0] for u in uploads} == {'111'}
    first = uploads[0][1]
    assert first.name.endswith('of' + str(len(uploads)))
    joined = backup_offsite.join_parts(first, tmp_path / 'joined.bimbak')
    assert backup_offsite.verify(joined, os.environ['BIM_BACKUP_PASSPHRASE'])['counts']['users'] >= 5
    uploads[1][1].unlink()
    with pytest.raises(backup_offsite.BackupError):
        backup_offsite.join_parts(first, tmp_path / 'again.bimbak')
    checks = {c['key']: c for c in boss.get('/api/security/overview').json()['checks']}
    assert checks['backup_folder']['ok'] and checks['backup_fresh']['ok']


# ---------- Gateways, release stamp, staging copy ----------
def test_release_health_reports_commit_and_gateway(env):
    m = env['m']
    (env['root'] / 'app' / 'release.json').write_text('{"commit": "abc1234", "name": "t"}', encoding='utf-8')
    saved, saved_gw = m.RELEASE, m.GATEWAY
    m.RELEASE, m.GATEWAY = m._release(), 'external'  # no worker thread in tests; 'lan' would answer 503
    try:
        local = env['TestClient'](m.app, base_url='http://127.0.0.1')
        r = local.get('/api/release-health')
        assert r.status_code == 200, r.text
        assert r.json()['commit'] == 'abc1234' and r.json()['gateway'] == 'external'
    finally:
        m.RELEASE, m.GATEWAY = saved, saved_gw
        (env['root'] / 'app' / 'release.json').unlink()


def test_iphone_key_unchanged_by_external_gateway(env):
    # The external process now really runs as 'external'. Shortcut keys must keep working the way they did
    # (that process used to report 'lan'), and an admin outside can still issue a key for an employee.
    m = env['m']
    c = m.connect()
    member = c.execute("SELECT id FROM users WHERE username='member'").fetchone()[0]
    c.execute("UPDATE user_mfa SET enabled=0 WHERE user_id=(SELECT id FROM users WHERE username='boss')")
    c.execute("DELETE FROM login_attempts WHERE username='[iphone]'"); c.close()  # earlier test locked this client out
    saved, m.GATEWAY = m.GATEWAY, 'external'
    try:
        boss = client(env, 'boss', external=True)
        r = boss.post(f'/api/iphone/accounts/{member}/key')
        assert r.status_code == 200, r.text
        phone = env['TestClient'](m.app, base_url='https://testserver', headers={'cf-ray': 'x'})
        r = phone.post('/api/iphone/task', json={'text': 'مهمة من برا'}, headers={'x-bim-secret': r.json()['secret']})
        assert r.status_code == 200, r.text
        assert r.json()['employee'] == 'عضو فريق'
    finally:
        m.GATEWAY = saved


def test_test_copy_cuts_every_outside_link(tmp_path):
    sys.path.insert(0, str(SRC / 'tools'))
    try:
        import make_test_copy as mtc
    finally:
        sys.path.remove(str(SRC / 'tools'))
    src, dst = tmp_path / 'real', tmp_path / 'test'
    (src / 'data' / 'attachments').mkdir(parents=True)
    (src / 'data' / 'attachments' / 'a.txt').write_text('x')
    (src / '.env').write_text('TELEGRAM_BOT_TOKEN=123:abc\nBIM_SYNC_SECRET=s\nBIM_ADMIN_PASSWORD=keep\n# BIM_IPHONE_SECRET=c\n'
                              'BIM_BACKUP_PASSPHRASE=p\nBIM_BACKUP_DIR=C:\\BIM_Tasks_Backups\n', encoding='utf-8')
    c = sqlite3.connect(src / 'data' / 'bim_tasks.db')
    c.executescript("PRAGMA journal_mode=WAL; CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);"
                    "CREATE TABLE user_mfa(user_id INTEGER PRIMARY KEY, enabled INTEGER);"
                    "INSERT INTO settings VALUES('sync.webapp_url','https://script.google.com/x'),('backup.offsite_dir','D:/off'),"
                    "('backup.telegram','1'),('company.name','BIM'); INSERT INTO user_mfa VALUES(1,1);")
    c.commit()  # left open on purpose: the real system is running while the copy is made
    with pytest.raises(SystemExit):
        mtc.main(['--src', str(src), '--dst', str(src / 'sub')])
    mtc.main(['--src', str(src), '--dst', str(dst)])
    c.close()
    env_text = (dst / '.env').read_text(encoding='utf-8')
    for key in ('TELEGRAM_BOT_TOKEN', 'BIM_SYNC_SECRET', 'BIM_BACKUP_PASSPHRASE', 'BIM_IPHONE_SECRET', 'BIM_OFFSITE_DIR'):
        assert re.search(rf'^{key}=$', env_text, re.M), key
    assert '123:abc' not in env_text and 'BIM_ADMIN_PASSWORD=keep' in env_text
    assert f'BIM_BACKUP_DIR={dst / "backups"}' in env_text
    d = sqlite3.connect(dst / 'data' / 'bim_tasks.db')
    assert dict(d.execute('SELECT key,value FROM settings').fetchall()) == {'company.name': 'BIM'}
    assert d.execute('SELECT enabled FROM user_mfa').fetchone()[0] == 0
    d.close()
    assert (dst / 'data' / 'attachments' / 'a.txt').exists() and (dst / 'TEST_COPY.txt').exists()
    assert sqlite3.connect(src / 'data' / 'bim_tasks.db').execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 4


def test_shift_test_button_lists_tasks(env):
    # Was a 500 on the server: the query has two placeholders and got one value.
    m = env['m']
    c = m.connect()
    c.execute("UPDATE employees SET telegram_enabled=1 WHERE id=?", (env['ids']['بوس'],))
    c.execute("UPDATE user_mfa SET enabled=0 WHERE user_id=(SELECT id FROM users WHERE username='boss')"); c.close()
    boss = client(env, 'boss')
    task(boss, env['ids']['بوس'], title='مهمة الشيفت')
    for kind in ('start', 'end'):
        r = boss.post('/api/shift-test', json={'employee_id': env['ids']['بوس'], 'kind': kind})
        assert r.status_code == 200, r.text


def test_shift_messages_include_group_tasks(env, monkeypatch):
    # Shift-start list and end-of-shift count must match the "my tasks" command: own tasks + open group participation.
    m = env['m']
    import app.workers as w
    eid = env['ids']['موظف آخر']
    c = m.connect()
    c.execute("INSERT INTO tasks(task_code,title,employee_id,status,priority) VALUES('SG-1','مهمة فردية',?,'جديدة','عادي')", (eid,))
    c.execute("INSERT INTO tasks(task_code,title,status,priority,is_group) VALUES('SG-2','مهمة جماعية','جديدة','عادي',1)")
    gid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('INSERT INTO task_participants(task_id,employee_id) VALUES(?,?)', (gid, eid))
    c.execute("INSERT INTO tasks(task_code,title,status,priority,is_group) VALUES('SG-3','جماعية أنهيت جزئي','جديدة','عادي',1)")
    did = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute("INSERT INTO task_participants(task_id,employee_id,status) VALUES(?,?,'تم')", (did, eid))
    out = []
    monkeypatch.setattr(w, 'send', lambda chat, text, **kw: out.append(text) or {'ok': True})
    e = c.execute('SELECT * FROM employees WHERE id=?', (eid,)).fetchone()
    titles = [r['title'] for r in w._open_task_rows(c, eid)]
    w._send_open_tasks(c, e)
    c.close()
    assert 'مهمة فردية' in titles and 'مهمة جماعية' in titles and 'جماعية أنهيت جزئي' not in titles
    assert 'مهمة جماعية' in out[-1] and 'جماعية أنهيت جزئي' not in out[-1]


def test_sync_auth_records_header_or_query(env):
    m = env['m']
    m._SYNC_AUTH_SEEN.clear()
    cl = client(env)
    assert cl.get('/api/sync/pull', headers={'X-BIM-Sync-Secret': 's' * 32}).status_code == 200
    assert cl.get('/api/sync/pull?secret=' + 's' * 32).status_code == 200   # still accepted until the Sheet is changed
    assert cl.get('/api/sync/pull', headers={'X-BIM-Sync-Secret': 'wrong'}).status_code == 401
    c = m.connect()
    got = dict(c.execute("SELECT key,value FROM settings WHERE key LIKE 'sync.auth_last_%'").fetchall())
    c.close()
    assert '/api/sync/pull' in got['sync.auth_last_header'] and '/api/sync/pull' in got['sync.auth_last_query']
    assert 's' * 32 not in ''.join(got.values())


def test_archiving_twice_notifies_once(env):
    ids, sent = env['ids'], env['sent']
    boss = client(env, 'boss')
    tid = task(boss, ids['موظف آخر'], title='أرشفة مرتين')
    c = env['m'].connect()
    count = lambda: c.execute("SELECT COUNT(*) FROM task_events WHERE task_id=? AND event_type='cancelled'", (tid,)).fetchone()[0]
    notes = lambda: c.execute("SELECT COUNT(*) FROM notifications WHERE task_id=? AND kind='إلغاء المهمة'", (tid,)).fetchone()[0]
    assert boss.delete(f'/api/tasks/{tid}').json() == {'ok': True, 'archived': True}
    first = (count(), notes(), len(sent))
    r = boss.delete(f'/api/tasks/{tid}')
    assert r.status_code == 200 and r.json().get('already') is True
    assert (count(), notes(), len(sent)) == first and first[0] == 1
    c.close()
