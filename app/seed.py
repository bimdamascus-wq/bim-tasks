import os
from .db import connect,init_db
from .security import hash_password
from . import access
def seed():
 init_db();c=connect();c.execute('BEGIN IMMEDIATE')
 try:
  partcols={r['name'] for r in c.execute('PRAGMA table_info(task_participants)')}
  if 'status' not in partcols: c.execute("ALTER TABLE task_participants ADD COLUMN status TEXT NOT NULL DEFAULT 'جديدة'")
  if 'updated_at' not in partcols: c.execute('ALTER TABLE task_participants ADD COLUMN updated_at TEXT')
  existing_cols={r['name'] for r in c.execute('PRAGMA table_info(users)')}
  if 'deleted_at' not in existing_cols: c.execute('ALTER TABLE users ADD COLUMN deleted_at TEXT')
  # Roles and permissions always match app/access.py exactly (also removes stale grants).
  access.sync_roles(c)
  if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
   admin=c.execute("SELECT id FROM roles WHERE code='admin'").fetchone()['id']
   u=os.environ.get('BIM_ADMIN_USERNAME','admin'); pw=os.environ.get('BIM_ADMIN_PASSWORD','ChangeMeNow123!')
   c.execute('INSERT INTO users(username,full_name,password_hash,role_id) VALUES(?,?,?,?)',(u,'مدير النظام',hash_password(pw),admin))
  c.execute('COMMIT')
 except:
  c.execute('ROLLBACK');raise
 finally:c.close()
