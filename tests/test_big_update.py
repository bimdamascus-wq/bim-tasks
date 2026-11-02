"""big_update: merged pages (tabs + old addresses), employee profile scope, notification bell, Kanban view."""
from conftest import client, task


def test_old_task_pages_redirect_to_views(env):
    boss = client(env, 'boss')
    r = boss.get('/tasks-table?employee=x', follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/tasks?view=table&employee=x'
    r = boss.get('/manage', follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/tasks?view=quick'
    for view in ('cards', 'table', 'quick', 'board'):
        page = boss.get('/tasks?view=' + view)
        assert page.status_code == 200, view
        assert 'class="ds-tabs"' in page.text and '/tasks?view=board' in page.text
    assert 'id="board"' in boss.get('/tasks?view=board').text
    assert 'manage-table' in boss.get('/tasks?view=quick').text


def test_quick_edit_view_keeps_its_permission(env):
    member = client(env, 'member')
    assert member.get('/tasks?view=quick').status_code == 403
    assert member.get('/manage', follow_redirects=False).status_code == 403
    page = member.get('/tasks?view=board')
    assert page.status_code == 200
    assert '/tasks?view=quick' not in page.text  # no tab the account cannot open


def test_menu_is_merged(env):
    html = client(env, 'boss').get('/tasks').text
    side = html[html.index('ds-nav'):html.index('ds-user')]
    for gone in ('href="/tasks-table"', 'href="/manage"', 'href="/telegram-groups"', 'href="/audit"', 'href="/operations-reports"'):
        assert gone not in side, gone
    for kept in ('href="/tasks"', 'href="/telegram-center"', 'href="/activity-log"', 'href="/team-performance"'):
        assert kept in side, kept
    tg = client(env, 'boss').get('/telegram-health').text
    assert 'href="/telegram-groups"' in tg[tg.index('ds-tabs'):]


def test_supervisor_reports_tab_hidden(env):
    samer = client(env, 'samer')
    page = samer.get('/team-performance').text
    assert '/operations-reports' not in page
    assert samer.get('/operations-reports').status_code == 403


def test_employee_profile_scope(env):
    ids = env['ids']
    boss, samer, member = client(env, 'boss'), client(env, 'samer'), client(env, 'member')
    assert boss.get(f"/employee/{ids['موظف آخر']}").status_code == 200
    d = boss.get(f"/api/employee-profile/{ids['سامر']}").json()
    assert d['employee']['name'] == 'سامر' and d['employee']['telegram_linked'] is True
    assert set(d['counts']) >= {'open', 'late', 'done_week', 'unseen'}
    assert samer.get(f"/employee/{ids['عضو فريق']}").status_code == 200
    assert samer.get(f"/employee/{ids['موظف آخر']}").status_code == 403
    assert samer.get(f"/api/employee-profile/{ids['موظف آخر']}").status_code == 403
    r = member.get(f"/employee/{ids['عضو فريق']}", follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/my-work'
    assert member.get(f"/api/employee-profile/{ids['عضو فريق']}").status_code == 403
    assert boss.get('/employee/99999').status_code == 404


def _drop(env, *tids):
    # The test database is shared by every test file: remove what this file created so counts elsewhere stay right.
    c = env['m'].connect()
    try:
        for t in tids:
            for table in ('inbox_items', 'task_comments', 'task_attachments', 'task_events', 'task_reminders', 'task_participants', 'notifications', 'bim_completion_entries'):
                try: c.execute(f'DELETE FROM {table} WHERE task_id=?', (t,))
                except Exception: pass
            c.execute('DELETE FROM tasks WHERE id=?', (t,))
    finally:
        c.close()


def _inbox(env, cl):
    env['m'].inbox.scan(force=True)
    return cl.get('/api/inbox').json()


def test_bell_items_and_scope(env):
    ids, m = env['ids'], env['m']
    boss, samer, member = client(env, 'boss'), client(env, 'samer'), client(env, 'member')
    m.inbox.scan(force=True)  # sets the starting point
    mine = task(boss, ids['عضو فريق'], title='مهمة الجرس')
    theirs = task(boss, ids['موظف آخر'], title='مهمة خارج الفريق')
    try:
        _bell_checks(env, boss, samer, member, mine, theirs)
    finally:
        _drop(env, mine, theirs)


def _bell_checks(env, boss, samer, member, mine, theirs):
    assert member.post(f'/api/tasks/{mine}/comments', json={'body': 'وصلت'}).status_code == 200
    assert boss.post(f'/api/tasks/{theirs}/comments', json={'body': 'ملاحظة المدير'}).status_code == 200
    assert member.patch(f'/api/tasks/{mine}/status', json={'status': 'تم'}).status_code == 200
    b = _inbox(env, boss)
    texts = [i['text'] for i in b['items']]
    assert any('مهمة الجرس' in t and i['kind'] == 'comment' for t, i in zip(texts, b['items']))
    assert any(i['kind'] == 'done' and i['task_id'] == mine for i in b['items'])
    assert not any(i['task_id'] == theirs and i['kind'] == 'comment' for i in b['items'])  # own comment: no bell
    s = _inbox(env, samer)
    assert any(i['task_id'] == mine for i in s['items'])
    assert not any(i['task_id'] == theirs for i in s['items'])  # outside the supervisor's team
    assert member.get('/api/inbox').status_code == 403
    # idempotent: scanning again adds nothing
    before = b['unread']
    assert _inbox(env, boss)['unread'] == before
    first = b['items'][0]['id']
    assert boss.post(f'/api/inbox/{first}/read', json={}).status_code == 200
    assert _inbox(env, boss)['unread'] == before - 1
    assert boss.post('/api/inbox/read-all', json={}).status_code == 200
    assert _inbox(env, boss)['unread'] == 0


def test_bell_late_once_per_due(env):
    ids, m = env['ids'], env['m']
    boss = client(env, 'boss')
    m.inbox.scan(force=True)
    tid = task(boss, ids['عضو فريق'], title='ستتأخر')
    try:
        c = m.connect()
        c.execute("UPDATE tasks SET due_at=strftime('%Y-%m-%dT%H:%M',datetime('now','localtime','-1 hours')) WHERE id=?", (tid,))
        c.close()
        late = lambda: [i for i in _inbox(env, boss)['items'] if i['task_id'] == tid and i['kind'] == 'late']
        assert len(late()) == 1
        assert len(late()) == 1
    finally:
        _drop(env, tid)
