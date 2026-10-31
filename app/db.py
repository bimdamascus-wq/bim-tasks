from pathlib import Path
import sqlite3
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'bim_tasks.db'

def connect():
    DB.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB,timeout=30,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA busy_timeout=5000')
    return c

def _cols(c, table):
    try:
        return {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
    except Exception:
        return set()

def _migrate(c):
    emp_cols=_cols(c,'employees')
    if emp_cols:
        if 'telegram_link_code' not in emp_cols:c.execute("ALTER TABLE employees ADD COLUMN telegram_link_code TEXT")
        if 'telegram_link_expires_at' not in emp_cols:c.execute("ALTER TABLE employees ADD COLUMN telegram_link_expires_at TEXT")
        if 'telegram_linked_at' not in emp_cols:c.execute("ALTER TABLE employees ADD COLUMN telegram_linked_at TEXT")
        if 'shift_type' not in emp_cols:c.execute("ALTER TABLE employees ADD COLUMN shift_type TEXT NOT NULL DEFAULT 'بدون'")
        if 'shift_start' not in emp_cols:c.execute("ALTER TABLE employees ADD COLUMN shift_start TEXT")
        if 'shift_end' not in emp_cols:c.execute("ALTER TABLE employees ADD COLUMN shift_end TEXT")
        if 'work_days' not in emp_cols:c.execute("ALTER TABLE employees ADD COLUMN work_days TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6'")
    task_cols=_cols(c,'tasks')
    if task_cols:
        if 'viewed_at' not in task_cols:
            c.execute('ALTER TABLE tasks ADD COLUMN viewed_at TEXT')
        if 'archived_at' not in task_cols:
            c.execute('ALTER TABLE tasks ADD COLUMN archived_at TEXT')
        if 'sync_version' not in task_cols:
            c.execute("ALTER TABLE tasks ADD COLUMN sync_version INTEGER NOT NULL DEFAULT 1")
        if 'sync_source' not in task_cols:
            c.execute("ALTER TABLE tasks ADD COLUMN sync_source TEXT NOT NULL DEFAULT 'server'")
        if 'sync_dirty' not in task_cols:
            c.execute("ALTER TABLE tasks ADD COLUMN sync_dirty INTEGER NOT NULL DEFAULT 1")
        if 'sync_last_at' not in task_cols:
            c.execute("ALTER TABLE tasks ADD COLUMN sync_last_at TEXT")
        if 'started_at' not in task_cols:c.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")
        if 'in_progress_at' not in task_cols:c.execute("ALTER TABLE tasks ADD COLUMN in_progress_at TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS task_comments(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      username_snapshot TEXT,
      body TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_task_comments_task ON task_comments(task_id,id)')
    c.execute("""CREATE TABLE IF NOT EXISTS task_attachments(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      original_name TEXT NOT NULL,
      stored_name TEXT NOT NULL,
      relative_path TEXT NOT NULL,
      mime_type TEXT,
      size_bytes INTEGER NOT NULL DEFAULT 0,
      kind TEXT NOT NULL DEFAULT 'file',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_task_attachments_task ON task_attachments(task_id,id)')
    c.execute("""CREATE TABLE IF NOT EXISTS sync_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      entity_type TEXT NOT NULL,
      entity_key TEXT NOT NULL,
      action TEXT NOT NULL,
      source TEXT NOT NULL DEFAULT 'server',
      payload_json TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_sync_events_id ON sync_events(id)')
    c.execute("""CREATE TABLE IF NOT EXISTS sync_conflicts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      entity_type TEXT NOT NULL,
      entity_key TEXT NOT NULL,
      server_version INTEGER,
      sheet_version INTEGER,
      server_json TEXT,
      sheet_json TEXT,
      status TEXT NOT NULL DEFAULT 'open',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      resolved_at TEXT
    )""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_sync_conflicts_status ON sync_conflicts(status,id)')
    c.execute("""CREATE TABLE IF NOT EXISTS task_events(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,event_type TEXT NOT NULL,old_status TEXT,new_status TEXT,note TEXT,source TEXT NOT NULL DEFAULT 'system',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id,id)')
    c.execute("""CREATE TABLE IF NOT EXISTS telegram_processed_updates(update_id INTEGER PRIMARY KEY,processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS telegram_pending_actions(chat_id TEXT PRIMARY KEY,employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,action TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS shift_messages(
      id INTEGER PRIMARY KEY AUTOINCREMENT, message_type TEXT NOT NULL, audience_type TEXT NOT NULL DEFAULT 'all',
      target_shift TEXT, target_department TEXT, message_text TEXT NOT NULL, start_date TEXT, end_date TEXT,
      is_active INTEGER NOT NULL DEFAULT 1, created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_shift_messages_active ON shift_messages(is_active,message_type,start_date,end_date)')
    c.execute("""CREATE TABLE IF NOT EXISTS shift_dispatch_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE, employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
      task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', error TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, sent_at TEXT)""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_shift_dispatch_employee ON shift_dispatch_log(employee_id,created_at)')
    c.execute("""CREATE TABLE IF NOT EXISTS telegram_groups(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      chat_id TEXT UNIQUE,
      telegram_title TEXT,
      department TEXT,
      is_active INTEGER NOT NULL DEFAULT 1,
      link_code TEXT,
      link_expires_at TEXT,
      linked_at TEXT,
      created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_telegram_groups_active ON telegram_groups(is_active,name)')
    c.execute("""CREATE TABLE IF NOT EXISTS group_reminders(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      group_id INTEGER NOT NULL REFERENCES telegram_groups(id) ON DELETE CASCADE,
      title TEXT,
      message_text TEXT NOT NULL,
      recurrence_type TEXT NOT NULL DEFAULT 'مرة واحدة',
      every_n_days INTEGER,
      week_day INTEGER,
      month_day INTEGER,
      send_time TEXT,
      specific_at TEXT,
      start_date TEXT,
      end_date TEXT,
      is_active INTEGER NOT NULL DEFAULT 1,
      last_run_key TEXT,
      last_sent_at TEXT,
      created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_group_reminders_active ON group_reminders(is_active,group_id)')
    c.execute("""CREATE TABLE IF NOT EXISTS group_dispatch_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      reminder_id INTEGER REFERENCES group_reminders(id) ON DELETE SET NULL,
      group_id INTEGER REFERENCES telegram_groups(id) ON DELETE SET NULL,
      event_key TEXT NOT NULL UNIQUE,
      status TEXT NOT NULL DEFAULT 'pending',
      error TEXT,
      sent_at TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_group_dispatch_log_group ON group_dispatch_log(group_id,created_at)')
    c.execute("""CREATE TABLE IF NOT EXISTS telegram_task_messages(
      task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      chat_id TEXT NOT NULL,
      message_id INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(chat_id,message_id)
    )""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_telegram_task_messages_task ON telegram_task_messages(task_id,chat_id)')
    c.execute("""CREATE TABLE IF NOT EXISTS telegram_outbox(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      chat_id TEXT NOT NULL,
      message_text TEXT NOT NULL,
      source_type TEXT NOT NULL DEFAULT 'text',
      source_id TEXT,
      dedupe_key TEXT UNIQUE,
      status TEXT NOT NULL DEFAULT 'pending',
      attempts INTEGER NOT NULL DEFAULT 0,
      max_attempts INTEGER NOT NULL DEFAULT 5,
      next_attempt_at TEXT,
      last_error TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      sent_at TEXT
    )""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_telegram_outbox_status ON telegram_outbox(status,next_attempt_at,id)')
    out_cols=_cols(c,'telegram_outbox')
    if out_cols and 'reply_markup_json' not in out_cols:
        c.execute("ALTER TABLE telegram_outbox ADD COLUMN reply_markup_json TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS shift_exceptions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
      exception_type TEXT NOT NULL DEFAULT 'إجازة',
      start_date TEXT NOT NULL,
      end_date TEXT NOT NULL,
      note TEXT,
      is_active INTEGER NOT NULL DEFAULT 1,
      created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_shift_exceptions_employee ON shift_exceptions(employee_id,start_date,end_date,is_active)')

    # Phase 2: employee job profiles, work policies and presentation preferences.
    c.execute("""CREATE TABLE IF NOT EXISTS bim_job_profiles (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
      is_active INTEGER NOT NULL DEFAULT 1, instructions TEXT NOT NULL DEFAULT '',
      display_mode TEXT NOT NULL DEFAULT 'on_demand' CHECK(display_mode IN ('auto','on_demand','once')),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS bim_employee_jobs (
      employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
      job_id INTEGER NOT NULL REFERENCES bim_job_profiles(id) ON DELETE CASCADE,
      PRIMARY KEY(employee_id,job_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS bim_employee_prefs (
      employee_id INTEGER PRIMARY KEY REFERENCES employees(id) ON DELETE CASCADE,
      display_mode TEXT CHECK(display_mode IN ('auto','on_demand','once')),
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    c.execute("""CREATE TABLE IF NOT EXISTS bim_work_settings (
      scope TEXT NOT NULL CHECK(scope IN ('job','employee')), owner_id INTEGER NOT NULL,
      config_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(scope,owner_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS bim_instruction_reads (
      employee_id INTEGER PRIMARY KEY REFERENCES employees(id) ON DELETE CASCADE,
      signature TEXT NOT NULL,read_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS bim_job_recurring (
      employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
      job_id INTEGER NOT NULL REFERENCES bim_job_profiles(id) ON DELETE CASCADE,
      template_key TEXT NOT NULL, recurring_id INTEGER REFERENCES recurring_tasks(id) ON DELETE SET NULL,
      PRIMARY KEY(employee_id,job_id,template_key))""")
    c.execute("""CREATE TABLE IF NOT EXISTS bim_recurring_rules (
      recurring_id INTEGER PRIMARY KEY REFERENCES recurring_tasks(id) ON DELETE CASCADE,
      workdays_only INTEGER NOT NULL DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS bim_completion_entries (
      id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
      form_json TEXT NOT NULL,values_json TEXT NOT NULL,source TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

    c.execute("CREATE TABLE IF NOT EXISTS bim_webhook_claims(update_id INTEGER PRIMARY KEY,claimed_at TEXT NOT NULL)")

    c.execute("""CREATE TABLE IF NOT EXISTS universal_templates(
      id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,kind TEXT NOT NULL,
      version INTEGER NOT NULL DEFAULT 1,is_active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS universal_template_versions(
      template_id INTEGER NOT NULL REFERENCES universal_templates(id),version INTEGER NOT NULL,
      body_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(template_id,version))""")
    c.execute("""CREATE TABLE IF NOT EXISTS universal_links(
      template_id INTEGER NOT NULL REFERENCES universal_templates(id),target_kind TEXT NOT NULL,
      target_id INTEGER NOT NULL,PRIMARY KEY(template_id,target_kind,target_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS universal_overrides(
      template_id INTEGER NOT NULL REFERENCES universal_templates(id),employee_id INTEGER NOT NULL REFERENCES employees(id),
      body_json TEXT NOT NULL,PRIMARY KEY(template_id,employee_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS universal_task_meta(
      task_id INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,template_id INTEGER REFERENCES universal_templates(id),
      version INTEGER,body_json TEXT NOT NULL,generation_key TEXT UNIQUE,customized INTEGER NOT NULL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS universal_staff_settings(
      employee_id INTEGER PRIMARY KEY REFERENCES employees(id),channel TEXT NOT NULL DEFAULT 'both',
      capacity_minutes INTEGER NOT NULL DEFAULT 480)""")
    c.execute("""CREATE TABLE IF NOT EXISTS universal_user_permissions(
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,permission TEXT NOT NULL,
      allowed INTEGER NOT NULL,PRIMARY KEY(user_id,permission))""")
    c.execute("""CREATE TABLE IF NOT EXISTS universal_reads(
      employee_id INTEGER NOT NULL REFERENCES employees(id),task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      signature TEXT NOT NULL,PRIMARY KEY(employee_id,task_id))""")

    c.execute("""CREATE TABLE IF NOT EXISTS record_lifecycle(
      entity TEXT NOT NULL,record_id INTEGER NOT NULL,deleted_at TEXT,
      PRIMARY KEY(entity,record_id))""")

def init_db():
    c=connect()
    sql=(ROOT/'schema.sql').read_text(encoding='utf-8')
    c.executescript(sql)
    _migrate(c)
    from .activity_log import migrate as migrate_activity
    migrate_activity(c)
    from .iphone_accounts import migrate as migrate_iphone
    migrate_iphone(c)
    from .access import migrate as migrate_access
    migrate_access(c)
    from .security_center import migrate as migrate_security
    migrate_security(c)
    c.close()
