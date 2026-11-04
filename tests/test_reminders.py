"""Task reminders, three options (app/reminders.py, owner 2026-11-04)."""
import sqlite3
from datetime import datetime, timedelta
from conftest import client, task


def test_repeat_every_10_days_and_months():
    from app.reminders import last_occurrence
    s = datetime(2026, 11, 1, 9, 0)
    assert last_occurrence(s, 10, 'يوم', datetime(2026, 10, 31)) is None
    assert last_occurrence(s, 10, 'يوم', datetime(2026, 11, 10, 8)) == s
    assert last_occurrence(s, 10, 'يوم', datetime(2026, 11, 11, 9, 1)) == datetime(2026, 11, 11, 9)
    assert last_occurrence(datetime(2026, 1, 31, 9), 1, 'شهر', datetime(2026, 2, 28, 10)) == datetime(2026, 2, 28, 9)
    assert last_occurrence(s, 2, 'ساعة', datetime(2026, 11, 1, 12, 30)) == datetime(2026, 11, 1, 11)


def test_due_rules(env):
    from app import reminders as R
    m = env['m']; c = m.connect()
    emp = env['ids']['موظف آخر']  # no shift -> working time 08:00-20:00
    t = {'employee_id': emp, 'due_at': '2026-11-05T14:00'}
    rep = lambda **k: {'type': 'يتكرر', 'start_at': '2026-11-01T09:00', 'every_n': 10, 'every_unit': 'يوم', 'before_minutes': None, 'last_sent_at': None, **k}
    assert R.due(c, rep(), t, datetime(2026, 11, 1, 9, 0))
    assert not R.due(c, rep(last_sent_at='2026-11-01T09:00:30'), t, datetime(2026, 11, 5))
    assert R.due(c, rep(last_sent_at='2026-11-01T09:00:30'), t, datetime(2026, 11, 11, 9, 5))
    hourly = rep(every_n=1, every_unit='ساعة')
    assert not R.due(c, hourly, t, datetime(2026, 11, 2, 3, 0))        # 3 a.m.: outside working time
    assert R.due(c, hourly, t, datetime(2026, 11, 2, 10, 0))
    before = {'type': 'قبل الموعد', 'start_at': None, 'every_n': None, 'every_unit': None, 'before_minutes': 1440, 'last_sent_at': None}
    assert not R.due(c, before, t, datetime(2026, 11, 4, 13, 0))
    assert R.due(c, before, t, datetime(2026, 11, 4, 14, 0))
    assert not R.due(c, dict(before, last_sent_at='2026-11-04T14:00:10'), t, datetime(2026, 11, 4, 18))
    assert not R.due(c, before, t, datetime(2026, 11, 5, 15))          # past the due time: the late reminders take over
    once = {'type': 'مرة واحدة', 'start_at': '2026-11-03T10:00', 'last_sent_at': None}
    assert R.due(c, once, t, datetime(2026, 11, 3, 10, 0)) and not R.due(c, once, t, datetime(2026, 11, 3, 9))
    c.close()
    assert R.label({'type': 'يتكرر', 'every_n': 10, 'every_unit': 'يوم', 'is_active': 1}) == 'كل ١٠ أيام'
    assert R.label({'type': 'قبل الموعد', 'before_minutes': 1440, 'is_active': 1}) == 'قبل الموعد بيوم'


def test_api_saves_and_validates(env):
    boss = client(env, 'boss'); emp = env['ids']['موظف آخر']
    tid = task(boss, emp, title='تذكير كل ١٠ أيام', reminder={'type': 'يتكرر', 'every_n': 10, 'every_unit': 'يوم', 'start_at': '2026-11-08T09:00'})
    t = boss.get(f'/api/tasks/{tid}').json()
    assert t['reminder_label'] == 'كل ١٠ أيام' and t['reminder_every_unit'] == 'يوم'
    assert boss.post('/api/tasks', json={'title': 'x', 'employee_id': emp, 'reminder': {'type': 'يتكرر', 'every_n': 0, 'every_unit': 'يوم'}}).status_code == 400
    assert boss.post('/api/tasks', json={'title': 'x', 'employee_id': emp, 'reminder': {'type': 'قبل الموعد', 'before_minutes': 60}}).status_code == 400
    # an old page sending an old type still works
    assert boss.patch(f'/api/tasks/{tid}', json={'reminder': {'type': 'كل ساعتين', 'start_at': '2026-11-08T09:00'}}).status_code == 200
    assert boss.get(f'/api/tasks/{tid}').json()['reminder_label'] == 'كل ساعتين'
    assert boss.patch(f'/api/tasks/{tid}', json={'reminder': {'type': 'بدون'}}).status_code == 200
    assert boss.get(f'/api/tasks/{tid}').json()['reminder_label'] == 'بدون'


def test_migration_converts_old_rows(tmp_path):
    from app import reminders as R
    db = sqlite3.connect(tmp_path / 'old.db', isolation_level=None); db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE tasks(id INTEGER PRIMARY KEY)')
    db.execute("""CREATE TABLE task_reminders(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
      type TEXT NOT NULL DEFAULT 'بدون' CHECK(type IN('بدون','مرة واحدة','كل ساعة','كل ساعتين','أسبوعياً','شهرياً','تاريخ محدد')),
      start_at TEXT,specific_at TEXT,weekly_day INTEGER,weekly_time TEXT,monthly_day INTEGER,monthly_time TEXT,last_sent_at TEXT,
      is_active INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    rows = [('تاريخ محدد', None, '2026-11-09T10:00', None, None, None, None, None), ('كل ساعتين', '2026-11-01T08:00', None, None, None, None, None, None),
            ('أسبوعياً', None, None, 0, '09:00', None, None, '2026-11-01 06:00:00'), ('شهرياً', None, None, None, None, 15, '10:30', None),
            ('بدون', None, None, None, None, None, None, None)]
    for i, r in enumerate(rows, 1):
        db.execute('INSERT INTO tasks(id) VALUES(?)', (i,))
        db.execute('INSERT INTO task_reminders(task_id,type,start_at,specific_at,weekly_day,weekly_time,monthly_day,monthly_time,last_sent_at,is_active) VALUES(?,?,?,?,?,?,?,?,?,1)', (i, *r))
    assert R.migrate(db) and not R.migrate(db)          # second run does nothing
    got = {r['task_id']: dict(r) for r in db.execute('SELECT * FROM task_reminders')}
    assert got[1]['type'] == 'مرة واحدة' and got[1]['start_at'] == '2026-11-09T10:00'
    assert (got[2]['type'], got[2]['every_n'], got[2]['every_unit']) == ('يتكرر', 2, 'ساعة')
    assert (got[3]['every_n'], got[3]['every_unit']) == (1, 'أسبوع') and datetime.fromisoformat(got[3]['start_at']).weekday() == 6  # Sunday
    assert got[4]['every_unit'] == 'شهر' and got[4]['start_at'].endswith('-15T10:30')
    assert got[5]['type'] == 'بدون'
    db.execute("INSERT INTO tasks(id) VALUES(9)")
    db.execute("INSERT INTO task_reminders(task_id,type,every_n,every_unit) VALUES(9,'يتكرر',10,'يوم')")  # CHECK is gone
