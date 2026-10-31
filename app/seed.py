import os
from .db import connect,init_db
from .security import hash_password
ROLES=[('admin','مدير',1),('deputy','نائب المدير',1),('supervisor','مشرف',1),('employee','موظف',0),('viewer','مشاهدة فقط',0)]
PERMS=[
('dashboard.view','عرض الرئيسية'),('tasks.view','عرض المهام'),('tasks.create','إضافة مهمة'),('tasks.edit','تعديل المهام'),('tasks.delete','حذف المهام'),
('employees.view','عرض الموظفين'),('employees.edit','إدارة الموظفين'),('recurring.view','عرض المتكررة'),('recurring.edit','إدارة المتكررة'),
('users.manage','إدارة المستخدمين'),('audit.view','سجل التدقيق'),('external.login','دخول خارجي')]
def seed():
 init_db();c=connect();c.execute('BEGIN IMMEDIATE')
 try:
  for code,name,ext in ROLES:c.execute('INSERT OR IGNORE INTO roles(code,name_ar,external_access_allowed) VALUES(?,?,?)',(code,name,ext))
  for code,name in PERMS:c.execute('INSERT OR IGNORE INTO permissions(code,name_ar) VALUES(?,?)',(code,name))
  admin=c.execute("SELECT id FROM roles WHERE code='admin'").fetchone()['id']; sup=c.execute("SELECT id FROM roles WHERE code='supervisor'").fetchone()['id']; emp=c.execute("SELECT id FROM roles WHERE code='employee'").fetchone()['id']; view=c.execute("SELECT id FROM roles WHERE code='viewer'").fetchone()['id']
  # Migration: revoke administration from staff even if granted in earlier versions.
  c.execute("DELETE FROM role_permissions WHERE permission_id=(SELECT id FROM permissions WHERE code='dashboard.view') AND role_id IN (SELECT id FROM roles WHERE code IN ('supervisor','employee','viewer'))")
  partcols={r['name'] for r in c.execute('PRAGMA table_info(task_participants)')}
  if 'status' not in partcols: c.execute("ALTER TABLE task_participants ADD COLUMN status TEXT NOT NULL DEFAULT 'جديدة'")
  if 'updated_at' not in partcols: c.execute('ALTER TABLE task_participants ADD COLUMN updated_at TEXT')
  existing_cols={r['name'] for r in c.execute('PRAGMA table_info(users)')}
  if 'deleted_at' not in existing_cols: c.execute('ALTER TABLE users ADD COLUMN deleted_at TEXT')
  allp=[r['id'] for r in c.execute('SELECT id FROM permissions')]
  for p in allp:c.execute('INSERT OR IGNORE INTO role_permissions(role_id,permission_id) VALUES(?,?)',(admin,p))
  deputy=c.execute("SELECT id FROM roles WHERE code='deputy'").fetchone()['id']
  for p in allp:c.execute('INSERT OR IGNORE INTO role_permissions(role_id,permission_id) VALUES(?,?)',(deputy,p))
  supcodes=['tasks.view','tasks.create','tasks.edit','employees.view','recurring.view','recurring.edit']
  for code in supcodes:
   p=c.execute('SELECT id FROM permissions WHERE code=?',(code,)).fetchone()['id'];c.execute('INSERT OR IGNORE INTO role_permissions VALUES(?,?)',(sup,p))
  for role,codes in [(emp,['tasks.view']), (view,['tasks.view','employees.view'])]:
   for code in codes:
    p=c.execute('SELECT id FROM permissions WHERE code=?',(code,)).fetchone()['id'];c.execute('INSERT OR IGNORE INTO role_permissions VALUES(?,?)',(role,p))
  if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
   u=os.environ.get('BIM_ADMIN_USERNAME','admin'); pw=os.environ.get('BIM_ADMIN_PASSWORD','ChangeMeNow123!')
   c.execute('INSERT INTO users(username,full_name,password_hash,role_id) VALUES(?,?,?,?)',(u,'مدير النظام',hash_password(pw),admin))
  c.execute('COMMIT')
 except:
  c.execute('ROLLBACK');raise
 finally:c.close()
