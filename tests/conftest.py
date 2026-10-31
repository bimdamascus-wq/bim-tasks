"""Shared fixture: a temporary copy of the app with a fresh database and a few users of every role.
Never touches the real data/ folder."""
import os, shutil, sys, importlib
from pathlib import Path
import pytest

SRC = Path(__file__).resolve().parents[1]
ADMIN_PW = 'Boss-Strong-Pass-2026'
PW = 'Team-Pass-2026x'


@pytest.fixture(scope='session')
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp('bim')
    shutil.copytree(SRC / 'app', root / 'app', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(SRC / 'schema.sql', root / 'schema.sql')
    (root / 'data').mkdir()
    offsite = tmp_path_factory.mktemp('offsite')
    os.environ.update({'BIM_ADMIN_USERNAME': 'boss', 'BIM_ADMIN_PASSWORD': ADMIN_PW, 'BIM_GATEWAY': 'lan',
                       'BIM_TELEGRAM_WEBHOOK_SECRET': 'x' * 32, 'BIM_SYNC_SECRET': 's' * 32,
                       'BIM_IPHONE_SECRET': 'i' * 40, 'BIM_BACKUP_PASSPHRASE': 'correct horse battery staple',
                       'BIM_BACKUP_DIR': str(tmp_path_factory.mktemp('localbk')), 'TELEGRAM_BOT_TOKEN': ''})
    for k in [k for k in sys.modules if k == 'app' or k.startswith('app.')]:
        del sys.modules[k]
    sys.path.insert(0, str(root))
    importlib.import_module('app.db').init_db()  # delegation.py reads settings at import time
    m = importlib.import_module('app.main')
    from fastapi.testclient import TestClient
    sent = []
    import app.telegram_service as ts
    ts.send = lambda chat, text, **kw: sent.append((chat, text)) or {'ok': True, 'result': {'message_id': 1}}
    import app.telegram_queue as tq
    tq.low_level_send = lambda chat, text, **kw: {'ok': True, 'result': {'message_id': 1}}
    with TestClient(m.app, base_url='https://testserver'):
        pass  # runs startup: seed
    c = m.connect()
    ids = {}
    for name, chat in [('الإدارة', None), ('بوس', '111'), ('نائب', '222'), ('سامر', '333'), ('عضو فريق', '444'), ('موظف آخر', '555')]:
        c.execute('INSERT INTO employees(name,telegram_chat_id) VALUES(?,?)', (name, chat))
        ids[name] = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    role = lambda code: c.execute('SELECT id FROM roles WHERE code=?', (code,)).fetchone()[0]
    c.execute("UPDATE users SET employee_id=? WHERE username='boss'", (ids['بوس'],))
    for user, r, emp in [('dep', 'deputy', 'نائب'), ('samer', 'supervisor', 'سامر'), ('member', 'employee', 'عضو فريق'), ('other', 'employee', 'موظف آخر')]:
        c.execute('INSERT INTO users(username,full_name,password_hash,role_id,employee_id) VALUES(?,?,?,?,?)',
                  (user, user, m.hash_password(PW), role(r), ids[emp]))
    samer = c.execute("SELECT id FROM users WHERE username='samer'").fetchone()[0]
    c.execute('INSERT INTO supervisor_team(user_id,employee_id) VALUES(?,?)', (samer, ids['عضو فريق']))
    c.close()
    return {'m': m, 'TestClient': TestClient, 'ids': ids, 'sent': sent, 'offsite': offsite, 'root': root}


def client(env, user=None, pw=PW, external=False):
    h = {'origin': 'https://testserver'}
    if external:
        h['cf-ray'] = 'test'
    cl = env['TestClient'](env['m'].app, base_url='https://testserver', headers=h)
    if user:
        r = cl.post('/login', data={'username': user, 'password': ADMIN_PW if user == 'boss' else pw}, follow_redirects=False)
        assert r.status_code == 303, r.text[:300]
    return cl


def task(cl, emp_id, **kw):
    r = cl.post('/api/tasks', json={'title': kw.pop('title', 'مهمة'), 'employee_id': emp_id, **kw})
    assert r.status_code == 200, r.text
    return r.json()['id']
