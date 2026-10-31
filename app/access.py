"""Single source of truth for roles, permissions and data scope.

Every page/API guard, the seed data and the /permissions page are derived from
this file. Change access here, never by editing role_permissions in the DB.
"""
from fastapi import HTTPException

# Roles offered in the UI, in display order. 'viewer' is a hidden legacy role
# kept only so old accounts keep working; it is never offered for new accounts.
ROLES=[
 ('admin','المدير',1),
 ('deputy','نائب المدير',1),
 ('supervisor','المسؤول',1),
 ('employee','الموظف',0),
]
LEGACY_ROLES=[('viewer','مشاهدة فقط (قديم)',0)]
ROLE_LABELS={code:name for code,name,_ in ROLES+LEGACY_ROLES}
MANAGERS=('admin','deputy')
VISIBLE_ROLES=tuple(code for code,_,_ in ROLES)

# (code, Arabic label, group shown on the permissions page)
PERMISSIONS=[
 ('dashboard.view','لوحة الإدارة والتقارير وصحة النظام','الإدارة'),
 ('tasks.view','عرض المهام','المهام'),
 ('tasks.create','إضافة مهمة وتوزيعها','المهام'),
 ('tasks.edit','تعديل المهام وتغيير حالتها','المهام'),
 ('tasks.delete','أرشفة / حذف المهام','المهام'),
 ('employees.view','عرض الموظفين','الفريق'),
 ('employees.edit','إدارة الموظفين وTelegram والشيفتات','الفريق'),
 ('recurring.view','عرض المهام المتكررة','المهام'),
 ('recurring.edit','إدارة المهام المتكررة','المهام'),
 ('users.manage','إدارة حسابات المسؤولين والموظفين والإعدادات','الإدارة'),
 ('audit.view','سجل التغييرات والنشاط','الإدارة'),
 ('external.login','الدخول من خارج الشركة','الدخول'),
 ('security.manage','العمليات الحساسة: حسابات المدير والنائب، الصلاحيات، الأمان، الاسترجاع','الأمان'),
]
PERMISSION_CODES={p[0] for p in PERMISSIONS}

_ALL=set(PERMISSION_CODES)
MATRIX={
 'admin':set(_ALL),
 'deputy':_ALL-{'security.manage'},
 'supervisor':{'tasks.view','tasks.create','tasks.edit','employees.view','recurring.view','recurring.edit','external.login'},
 'employee':{'tasks.view'},
 'viewer':{'tasks.view','employees.view'},
}
# Extra permissions an administrator may switch on for one person from
# /access-control. Anything not here can never be granted by an override.
OPTIONAL={
 'admin':set(),
 'deputy':set(),
 'supervisor':{'tasks.delete'},
 'employee':set(),
 'viewer':set(),
}
# What each role can see/change. Shown on the permissions page.
SCOPE={
 'admin':'كل الشركة',
 'deputy':'كل الشركة',
 'supervisor':'فريقه فقط (الموظفون الذين يحددهم المدير) + مهامه الشخصية',
 'employee':'مهامه الشخصية فقط',
 'viewer':'عرض فقط',
}
# Operations reserved to the administrator (security.manage), listed on the permissions page.
SENSITIVE_OPERATIONS=[
 'إنشاء أو تعديل أو تعطيل حساب مدير أو نائب مدير، أو منح هذين الدورين',
 'تعديل الصلاحيات الفردية لأي حساب',
 'إعدادات الأمان: تأكيد الدخول، النسخ خارج السيرفر، الاسترجاع',
 'ربط أو فك Telegram لموظف مرتبط بحساب مدير',
 'رابط مزامنة Google Sheet وإعداد Webhook تلغرام ومالك اختصار iPhone الإداري',
]

def ceiling(role_code):
    return MATRIX.get(role_code,set())|OPTIONAL.get(role_code,set())

def effective(role_code, overrides):
    """Role defaults, then per-user switches, never above the role's ceiling."""
    result=set(MATRIX.get(role_code,set()))
    for code,allowed in overrides:
        if allowed:result.add(code)
        else:result.discard(code)
    return result&ceiling(role_code)

def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS supervisor_team(
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(user_id,employee_id))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_supervisor_team_employee ON supervisor_team(employee_id)')

def sync_roles(c):
    """Make roles/permissions in the database match MATRIX exactly."""
    for code,name,ext in ROLES+LEGACY_ROLES:
        c.execute('INSERT INTO roles(code,name_ar,external_access_allowed) VALUES(?,?,?) ON CONFLICT(code) DO UPDATE SET name_ar=excluded.name_ar,external_access_allowed=excluded.external_access_allowed',(code,name,ext))
    for code,name,_ in PERMISSIONS:
        c.execute('INSERT INTO permissions(code,name_ar) VALUES(?,?) ON CONFLICT(code) DO UPDATE SET name_ar=excluded.name_ar',(code,name))
    for role,codes in MATRIX.items():
        rid=c.execute('SELECT id FROM roles WHERE code=?',(role,)).fetchone()[0]
        c.execute('DELETE FROM role_permissions WHERE role_id=?',(rid,))
        for code in codes:
            c.execute('INSERT INTO role_permissions(role_id,permission_id) SELECT ?,id FROM permissions WHERE code=?',(rid,code))

# ---------- data scope ----------
def is_manager(u):return u['role_code'] in MANAGERS

def team(c, u):
    """Employee ids a supervisor is responsible for, including their own record."""
    ids={r[0] for r in c.execute('SELECT employee_id FROM supervisor_team WHERE user_id=?',(u['user_id'],))}
    if u['employee_id']:ids.add(int(u['employee_id']))
    return ids

def employee_scope(c, u):
    """None = whole company; otherwise the set of employee ids this account may act on."""
    role=u['role_code']
    if role in MANAGERS or role=='viewer':return None
    if role=='supervisor':return team(c,u)
    return {int(u['employee_id'])} if u['employee_id'] else set()

def _marks(ids):return ','.join('?' for _ in ids) or 'NULL'

def task_filter_sql(c, u, alias='t'):
    """SQL condition limiting a task query to what this account may see."""
    role=u['role_code']
    if role in MANAGERS or role=='viewer':return '1=1',[]
    if role=='employee':
        eid=int(u['employee_id'] or -1)
        return f'({alias}.employee_id=? OR EXISTS(SELECT 1 FROM task_participants sp WHERE sp.task_id={alias}.id AND sp.employee_id=?))',[eid,eid]
    ids=sorted(team(c,u)) or [-1];own=int(u['employee_id'] or -1);m=_marks(ids)
    sql=(f'({alias}.employee_id IN ({m})'
         f' OR ({alias}.employee_id IS NULL AND {alias}.is_group=0 AND {alias}.created_by=?)'
         f' OR ({alias}.is_group=1 AND EXISTS(SELECT 1 FROM task_participants sp WHERE sp.task_id={alias}.id)'
         f'     AND NOT EXISTS(SELECT 1 FROM task_participants sp WHERE sp.task_id={alias}.id AND sp.employee_id NOT IN ({m})))'
         f' OR EXISTS(SELECT 1 FROM task_participants sp WHERE sp.task_id={alias}.id AND sp.employee_id=?))')
    return sql,ids+[u['user_id']]+ids+[own]

def can_view_task(c, u, tid):
    sql,args=task_filter_sql(c,u)
    return bool(c.execute(f'SELECT 1 FROM tasks t WHERE t.id=? AND {sql}',[tid]+args).fetchone())

def can_manage_task(c, u, row):
    """Changing a task as a whole (edit, archive, reassign, resend, delegate)."""
    role=u['role_code']
    if role in MANAGERS:return True
    if role!='supervisor':return False
    ids=team(c,u)
    if row['is_group']:
        parts={r[0] for r in c.execute('SELECT employee_id FROM task_participants WHERE task_id=?',(row['id'],))}
        return bool(parts) and parts<=ids
    if row['employee_id'] is None:return row['created_by']==u['user_id']
    return int(row['employee_id']) in ids

def require_manage_task(c, u, row):
    if not can_manage_task(c,u,row):raise HTTPException(403,'هذه المهمة خارج نطاق فريقك')

def require_assignable(c, u, employee_ids):
    """Every employee a task/recurring rule is assigned to must be inside the actor's scope."""
    ids={int(e) for e in employee_ids if e not in (None,'')}
    scope=employee_scope(c,u)
    if scope is not None and not ids<=scope:
        raise HTTPException(403,'لا يمكنك إسناد مهام لموظف خارج فريقك')
    return ids

def require_role_change(actor_perms, *role_codes):
    """Only the administrator may create, edit or grant the admin/deputy roles."""
    if any(r in MANAGERS for r in role_codes if r) and 'security.manage' not in actor_perms:
        raise HTTPException(403,'حسابات المدير ونائب المدير يديرها المدير فقط')

# Every page and the permission it needs. The server enforces this table for
# every request (main.auth_guard) in addition to each route's own check, and
# the /permissions page is generated from it, so the table cannot drift.
PAGE_PERMS={
 '/':'dashboard.view','/operations-reports':'dashboard.view','/workload':'dashboard.view','/integrations':'dashboard.view',
 '/system-health':'dashboard.view','/backup':'dashboard.view','/sync':'dashboard.view',
 '/tasks':'tasks.view','/tasks-table':'tasks.view','/archive':'tasks.view','/my-work':'tasks.view',
 '/manage':'tasks.edit',
 '/employees':'employees.view',
 '/recurring':'recurring.view',
 '/telegram-center':'employees.edit','/telegram-groups':'employees.edit','/telegram-health':'employees.edit',
 '/telegram-dashboard':'employees.edit','/shift-center':'employees.edit','/automation-center':'employees.edit',
 '/audit':'audit.view','/activity-log':'audit.view',
 '/users':'users.manage','/job-profiles':'users.manage','/iphone-setup':'users.manage','/notification-center':'users.manage',
 '/record-management':'users.manage','/company':'users.manage','/template-library':'users.manage','/access-control':'users.manage',
 '/handover':'users.manage','/page-visibility':'users.manage','/security':'users.manage','/permissions':'users.manage',
}
# Reachable without a login session. Each one authenticates in its own way
# (Telegram secret header, iPhone key, Google Sheet secret, or none for static/public assets).
PUBLIC_PATHS={
 '/login','/login/verify','/logout','/health','/manifest.webmanifest','/service-worker.js','/company-logo','/favicon.ico',
 '/api/telegram/webhook','/api/iphone/task','/api/iphone/voice','/api/iphone/text',
 '/api/sync/pull','/api/sync/snapshot','/api/sync/tasks-page','/api/sync/push-task','/api/sync/push-employee',
 '/api/release-health',
}
PUBLIC_PREFIXES=('/static/',)

def page_permission(path):
    if path in PAGE_PERMS:return PAGE_PERMS[path]
    if path.startswith('/task/'):return 'tasks.view'
    return None
