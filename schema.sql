PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS roles(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 code TEXT NOT NULL UNIQUE,
 name_ar TEXT NOT NULL,
 external_access_allowed INTEGER NOT NULL DEFAULT 0 CHECK(external_access_allowed IN(0,1))
);
CREATE TABLE IF NOT EXISTS permissions(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 code TEXT NOT NULL UNIQUE,
 name_ar TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS role_permissions(
 role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
 permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
 PRIMARY KEY(role_id,permission_id)
);
CREATE TABLE IF NOT EXISTS users(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 full_name TEXT NOT NULL,
 password_hash TEXT NOT NULL,
 role_id INTEGER NOT NULL REFERENCES roles(id),
 employee_id INTEGER,
 is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN(0,1)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 last_login_at TEXT,
 last_login_ip TEXT
);
CREATE TABLE IF NOT EXISTS login_sessions(
 id TEXT PRIMARY KEY,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 csrf_token TEXT NOT NULL,
 gateway TEXT NOT NULL CHECK(gateway IN('lan','external')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 expires_at TEXT NOT NULL,
 ip_address TEXT,
 user_agent TEXT,
 revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS login_attempts(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 username TEXT,
 ip_address TEXT,
 success INTEGER NOT NULL,
 attempted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_logs(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER REFERENCES users(id),
 username_snapshot TEXT,
 action TEXT NOT NULL,
 entity_type TEXT,
 entity_id TEXT,
 before_json TEXT,
 after_json TEXT,
 reason TEXT,
 gateway TEXT,
 ip_address TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS employees(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL UNIQUE COLLATE NOCASE,
 department TEXT,
 whatsapp TEXT,
 telegram_chat_id TEXT,
 telegram_link_code TEXT,
 telegram_link_expires_at TEXT,
 telegram_linked_at TEXT,
 shift_type TEXT NOT NULL DEFAULT 'بدون',
 shift_start TEXT,
 shift_end TEXT,
 work_days TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
 telegram_enabled INTEGER NOT NULL DEFAULT 1,
 whatsapp_enabled INTEGER NOT NULL DEFAULT 1,
 is_active INTEGER NOT NULL DEFAULT 1,
 sort_order INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_employees_active_sort ON employees(is_active,sort_order,name);

CREATE TABLE IF NOT EXISTS tasks(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 task_code TEXT NOT NULL UNIQUE,
 employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
 title TEXT NOT NULL,
 notes TEXT,
 priority TEXT NOT NULL DEFAULT 'عادي' CHECK(priority IN('منخفضة','عادي','عالية','عاجلة')),
 status TEXT NOT NULL DEFAULT 'جديدة' CHECK(status IN('جديدة','بدأت','قيد التنفيذ','بانتظار','تم','ملغاة')),
 due_at TEXT,
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 source TEXT NOT NULL DEFAULT 'dashboard',
 is_group INTEGER NOT NULL DEFAULT 0,
 completed_at TEXT,
 started_at TEXT,
 in_progress_at TEXT,
 viewed_at TEXT,
 archived_at TEXT,
 sync_version INTEGER NOT NULL DEFAULT 1,
 sync_source TEXT NOT NULL DEFAULT 'server',
 sync_dirty INTEGER NOT NULL DEFAULT 1,
 sync_last_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tasks_employee ON tasks(employee_id,status);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_at,status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);

CREATE TABLE IF NOT EXISTS task_participants(
 task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
 status TEXT NOT NULL DEFAULT 'جديدة',
 started_at TEXT,
 completed_at TEXT,
 PRIMARY KEY(task_id,employee_id)
);
CREATE TABLE IF NOT EXISTS task_subtasks(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 title TEXT NOT NULL,
 is_done INTEGER NOT NULL DEFAULT 0,
 sort_order INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 completed_at TEXT
);
CREATE TABLE IF NOT EXISTS task_templates(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 title TEXT NOT NULL,
 notes TEXT,
 priority TEXT NOT NULL DEFAULT 'عادي',
 default_employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
 is_active INTEGER NOT NULL DEFAULT 1,
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS saved_views(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL,
 filters_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recurring_tasks(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
 title TEXT NOT NULL,
 notes TEXT,
 priority TEXT NOT NULL DEFAULT 'عادي',
 recurrence_type TEXT NOT NULL CHECK(recurrence_type IN('يومي','أسبوعي','شهري')),
 week_day INTEGER,
 month_day INTEGER,
 run_time TEXT NOT NULL DEFAULT '09:00',
 is_active INTEGER NOT NULL DEFAULT 1,
 last_run_key TEXT,
 created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_reminders(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id INTEGER NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
 type TEXT NOT NULL DEFAULT 'بدون',
 start_at TEXT,
 specific_at TEXT,
 weekly_day INTEGER,
 weekly_time TEXT,
 monthly_day INTEGER,
 monthly_time TEXT,
 last_sent_at TEXT,
 is_active INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 every_n INTEGER,
 every_unit TEXT,
 before_minutes INTEGER
);
CREATE TABLE IF NOT EXISTS notifications(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
 employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
 channel TEXT NOT NULL,
 kind TEXT NOT NULL,
 status TEXT NOT NULL,
 error TEXT,
 sent_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status,created_at);
CREATE TABLE IF NOT EXISTS settings(
 key TEXT PRIMARY KEY,
 value TEXT
);

CREATE TABLE IF NOT EXISTS task_comments(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 username_snapshot TEXT,
 body TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_task_comments_task ON task_comments(task_id,id);

CREATE TABLE IF NOT EXISTS task_attachments(
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
);
CREATE INDEX IF NOT EXISTS idx_task_attachments_task ON task_attachments(task_id,id);

CREATE TABLE IF NOT EXISTS sync_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 entity_type TEXT NOT NULL,
 entity_key TEXT NOT NULL,
 action TEXT NOT NULL,
 source TEXT NOT NULL DEFAULT 'server',
 payload_json TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sync_events_id ON sync_events(id);
CREATE TABLE IF NOT EXISTS sync_conflicts(
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
);
CREATE INDEX IF NOT EXISTS idx_sync_conflicts_status ON sync_conflicts(status,id);

CREATE TABLE IF NOT EXISTS task_events(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,event_type TEXT NOT NULL,old_status TEXT,new_status TEXT,note TEXT,source TEXT NOT NULL DEFAULT 'system',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id,id);
CREATE TABLE IF NOT EXISTS telegram_processed_updates(update_id INTEGER PRIMARY KEY,processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS telegram_pending_actions(chat_id TEXT PRIMARY KEY,employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,action TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);


CREATE TABLE IF NOT EXISTS shift_messages(
 id INTEGER PRIMARY KEY AUTOINCREMENT, message_type TEXT NOT NULL, audience_type TEXT NOT NULL DEFAULT 'all', target_shift TEXT, target_department TEXT, message_text TEXT NOT NULL, start_date TEXT, end_date TEXT, is_active INTEGER NOT NULL DEFAULT 1, created_by INTEGER REFERENCES users(id) ON DELETE SET NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_shift_messages_active ON shift_messages(is_active,message_type,start_date,end_date);
CREATE TABLE IF NOT EXISTS shift_dispatch_log(
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE, employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE, task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, sent_at TEXT);
CREATE INDEX IF NOT EXISTS idx_shift_dispatch_employee ON shift_dispatch_log(employee_id,created_at);


CREATE TABLE IF NOT EXISTS telegram_groups(
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
);
CREATE INDEX IF NOT EXISTS idx_telegram_groups_active ON telegram_groups(is_active,name);

CREATE TABLE IF NOT EXISTS group_reminders(
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
);
CREATE INDEX IF NOT EXISTS idx_group_reminders_active ON group_reminders(is_active,group_id);

CREATE TABLE IF NOT EXISTS group_dispatch_log(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 reminder_id INTEGER REFERENCES group_reminders(id) ON DELETE SET NULL,
 group_id INTEGER REFERENCES telegram_groups(id) ON DELETE SET NULL,
 event_key TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'pending',
 error TEXT,
 sent_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_group_dispatch_log_group ON group_dispatch_log(group_id,created_at);


CREATE TABLE IF NOT EXISTS telegram_outbox(
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
 reply_markup_json TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_telegram_outbox_status ON telegram_outbox(status,next_attempt_at,id);


CREATE TABLE IF NOT EXISTS shift_exceptions(
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
);
CREATE INDEX IF NOT EXISTS idx_shift_exceptions_employee
 ON shift_exceptions(employee_id,start_date,end_date,is_active);
