"""Full Google Sheet sync (full_sync/sync_all.py) against a fake receiver: incremental log tables, daily full, lock."""
import importlib.util, json, os, sqlite3, sys, time
from datetime import datetime
from pathlib import Path
import pytest

SRC = Path(__file__).resolve().parents[1]


@pytest.fixture
def sync(tmp_path, monkeypatch):
    (tmp_path / 'data').mkdir()
    c = sqlite3.connect(tmp_path / 'data' / 'bim_tasks.db')
    c.executescript((SRC / 'schema.sql').read_text(encoding='utf-8'))
    c.execute("INSERT INTO employees(name) VALUES('أحمد')")
    c.execute("INSERT INTO tasks(task_code,title,employee_id) VALUES('T-1','مهمة',1)")
    for i in range(45):
        c.execute("INSERT INTO task_events(task_id,event_type) VALUES(1,'x')")
    c.commit(); c.close()
    (tmp_path / '.env').write_text('BIM_FULL_SYNC_WEBAPP_URL=https://script.google.com/macros/s/x/exec\nBIM_FULL_SYNC_SECRET=abc\n', encoding='utf-8')
    monkeypatch.setenv('BIM_SERVER_ROOT', str(tmp_path))
    for k in ('BIM_FULL_SYNC_WEBAPP_URL', 'BIM_FULL_SYNC_SECRET', 'BIM_SYNC_SECRET'):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location('sync_all_under_test', SRC / 'full_sync' / 'sync_all.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    sheet = {'calls': [], 'rows': {}, 'fail': None}

    def call(url, secret, body):
        assert secret == 'abc'
        sheet['calls'].append(body)
        if body['action'] == 'bim_fullsync_probe':
            return {'ok': True, 'protocol': 'bim_fullsync_v1'}
        if sheet['fail'] == body['table']:
            raise TimeoutError('The read operation timed out')
        for r in body['rows']:  # receiver upserts by (table, source key)
            key = r.get('id', r.get('key'))
            sheet['rows'][(body['table'], key)] = r
        return {'ok': True, 'protocol': 'bim_fullsync_v1', 'accepted': len(body['rows'])}
    mod.call = call
    return mod, sheet, tmp_path


def sent_rows(sheet, table):
    return sum(len(b['rows']) for b in sheet['calls'] if b.get('table') == table)


def add_events(root, n):
    c = sqlite3.connect(root / 'data' / 'bim_tasks.db')
    for _ in range(n):
        c.execute("INSERT INTO task_events(task_id,event_type) VALUES(1,'y')")
    c.execute("UPDATE tasks SET title='معدلة'")
    c.commit(); c.close()


def test_first_run_full_then_only_new_log_rows(sync):
    mod, sheet, root = sync
    assert mod.main([]) == 0
    assert sent_rows(sheet, 'task_events') == 45 and sent_rows(sheet, 'tasks') == 1
    state = json.loads((root / 'sync_full_logs' / 'incremental_state.json').read_text(encoding='utf-8'))
    assert state['last_id']['task_events'] == 45 and state['last_full_date']
    before = dict(sheet['rows'])
    sheet['calls'].clear()
    add_events(root, 3)
    assert mod.main([]) == 0
    assert sent_rows(sheet, 'task_events') == 3            # only the new rows
    assert sent_rows(sheet, 'tasks') == 1                  # other tables still in full
    assert sheet['rows'][('tasks', 1)]['title'] == 'معدلة'
    assert sheet['calls'][0]['action'] == 'bim_fullsync_probe'
    # Same Sheet content as a full upload would give.
    assert len([k for k in sheet['rows'] if k[0] == 'task_events']) == 48
    assert set(before) <= set(sheet['rows'])
    summary = json.loads(sorted((root / 'sync_full_logs').glob('SUMMARY_*.json'))[-1].read_text(encoding='utf-8'))
    assert summary['mode'] == 'incremental' and summary['status'] == 'SENT_TO_SHEETS'


def test_failed_table_is_resent_next_time(sync):
    mod, sheet, root = sync
    assert mod.main([]) == 0
    add_events(root, 25)
    sheet['fail'] = 'task_events'
    assert mod.main([]) == 2
    state = json.loads((root / 'sync_full_logs' / 'incremental_state.json').read_text(encoding='utf-8'))
    assert state['last_id']['task_events'] == 45            # not moved: the table did not finish
    sheet['fail'] = None; sheet['calls'].clear()
    assert mod.main([]) == 0
    assert sent_rows(sheet, 'task_events') == 25


def test_daily_full_after_3am_once(sync, monkeypatch):
    mod, sheet, root = sync
    assert mod.main([]) == 0
    state_file = root / 'sync_full_logs' / 'incremental_state.json'
    st = json.loads(state_file.read_text(encoding='utf-8')); st['last_full_date'] = '2026-10-31'
    state_file.write_text(json.dumps(st), encoding='utf-8')
    for hour, expect_full in [(2, False), (3, True), (4, False)]:
        sheet['calls'].clear()
        assert mod.full_due(json.loads(state_file.read_text(encoding='utf-8')), datetime(2026, 11, 1, hour, 5)) == expect_full
        monkeypatch.setattr(mod, 'datetime', type('D', (datetime,), {'now': classmethod(lambda cls, tz=None: datetime(2026, 11, 1, hour, 5))}))
        assert mod.main([]) == 0
        assert sent_rows(sheet, 'task_events') == (45 if expect_full else 0)
    sheet['calls'].clear()
    assert mod.main(['--full']) == 0
    assert sent_rows(sheet, 'task_events') == 45


def test_overlapping_run_exits_at_once(sync):
    mod, sheet, root = sync
    (root / 'sync_full_logs').mkdir()
    lock = root / 'sync_full_logs' / 'sync.lock'
    lock.write_text('1234', encoding='utf-8')
    assert mod.main([]) == 0
    assert sheet['calls'] == [] and lock.exists()
    old = time.time() - 31 * 60
    os.utime(lock, (old, old))                               # left by a killed run
    assert mod.main([]) == 0
    assert sheet['calls'] and not lock.exists()


def test_lock_released_after_error(sync):
    mod, sheet, root = sync
    (root / 'data' / 'bim_tasks.db').unlink()
    with pytest.raises(RuntimeError):
        mod.main([])
    assert not (root / 'sync_full_logs' / 'sync.lock').exists()


def test_only_changed_rows_of_other_tables(sync):
    mod, sheet, root = sync
    assert mod.main([]) == 0                                  # first run: full
    sheet['calls'].clear()
    assert mod.main([]) == 0                                  # nothing changed
    assert sent_rows(sheet, 'tasks') == 0 and sent_rows(sheet, 'employees') == 0
    assert [b for b in sheet['calls'] if b['action'] == 'bim_fullsync_batch'] == []
    c = sqlite3.connect(root / 'data' / 'bim_tasks.db')
    c.execute("INSERT INTO tasks(task_code,title,employee_id) VALUES('T-2','ثانية',1)")
    c.execute("UPDATE employees SET name='أحمد علي' WHERE id=1")
    c.commit(); c.close()
    sheet['calls'].clear()
    assert mod.main([]) == 0
    assert sent_rows(sheet, 'tasks') == 1 and sheet['rows'][('tasks', 2)]['title'] == 'ثانية'
    assert sent_rows(sheet, 'employees') == 1 and sheet['rows'][('employees', 1)]['name'] == 'أحمد علي'
    summary = json.loads(sorted((root / 'sync_full_logs').glob('SUMMARY_*.json'))[-1].read_text(encoding='utf-8'))
    assert summary['unchanged']['tasks'] == 1


def test_changed_rows_resent_after_failure(sync):
    mod, sheet, root = sync
    assert mod.main([]) == 0
    c = sqlite3.connect(root / 'data' / 'bim_tasks.db')
    c.execute("UPDATE tasks SET title='بعد الفشل'"); c.commit(); c.close()
    sheet['fail'] = 'tasks'
    assert mod.main([]) == 2
    sheet['fail'] = None; sheet['calls'].clear()
    assert mod.main([]) == 0
    assert sent_rows(sheet, 'tasks') == 1 and sheet['rows'][('tasks', 1)]['title'] == 'بعد الفشل'


def test_upgrade_from_state_without_hashes_sends_everything_once(sync):
    mod, sheet, root = sync
    assert mod.main([]) == 0
    st_file = root / 'sync_full_logs' / 'incremental_state.json'
    st = json.loads(st_file.read_text(encoding='utf-8')); st.pop('hashes')
    st_file.write_text(json.dumps(st), encoding='utf-8')     # state written by package code_split
    sheet['calls'].clear()
    assert mod.main([]) == 0
    assert sent_rows(sheet, 'tasks') == 1 and sent_rows(sheet, 'task_events') == 0
