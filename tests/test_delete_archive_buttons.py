"""Delete-employee button (employees page + profile) and archive button inside a task (owner, 2026-11-03)."""
from conftest import client, task


def test_buttons_shown_by_permission(env):
    ids = env['ids']
    boss, samer, member = client(env, 'boss'), client(env, 'samer'), client(env, 'member')
    assert 'deleteEmployee(' in boss.get('/employees').text
    assert 'id="epDelete"' in boss.get(f"/employee/{ids['عضو فريق']}").text
    assert 'id="epDelete"' not in samer.get(f"/employee/{ids['عضو فريق']}").text  # no employees.edit
    tid = task(boss, ids['عضو فريق'], title='زر الأرشفة')
    try:
        assert 'id="archiveTask"' in boss.get(f'/task/{tid}').text
        assert 'id="archiveTask"' not in member.get(f'/task/{tid}').text      # no tasks.delete
    finally:
        boss.delete(f'/api/tasks/{tid}')


def test_delete_employee_after_moving_open_tasks(env):
    m = env['m']
    boss = client(env, 'boss')
    c = m.connect()
    c.execute("INSERT INTO employees(name) VALUES('موظف مؤقت للحذف')")
    eid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.close()
    tid = task(boss, eid, title='مهمة موظف سيُحذف')
    to = env['ids']['موظف آخر']
    prev = boss.post('/api/handover/preview', json={'from_employee': eid}).json()['tasks']
    assert [t['id'] for t in prev] == [tid]
    assert boss.post('/api/handover', json={'from_employee': eid, 'to_employee': to, 'task_ids': [tid]}).status_code == 200
    assert boss.delete(f'/api/employees/{eid}').status_code == 200
    assert boss.get(f'/api/tasks/{tid}').json()['employee_id'] == to
    assert eid not in [e['id'] for e in boss.get('/api/employees?all=1').json()]
    boss.delete(f'/api/tasks/{tid}')


def test_task_page_archive_keeps_its_rules(env):
    ids = env['ids']
    boss, member = client(env, 'boss'), client(env, 'member')
    tid = task(boss, ids['عضو فريق'], title='أرشفة من جوّا المهمة')
    assert member.delete(f'/api/tasks/{tid}').status_code == 403
    assert boss.delete(f'/api/tasks/{tid}').json()['archived'] is True
    assert boss.get(f'/api/tasks/{tid}').json()['archived_at']
