"""HTML pages of the main menu and the basic APIs behind them (/api/me, release health, dashboard).
Split out of app/main.py (package code_split) with no change in behaviour; main.py imports this module."""
from __future__ import annotations
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .db import connect
from . import main as _main  # settings that tests or plugins may change at run time are read from main
from .main import (
    DOMAIN, _task_accessible, app, ctx, need, perms, session, templates
)

# ---------- pages ----------
@app.get('/', response_class=HTMLResponse)
def dashboard(req: Request):
    u = session(req)
    if not u:
        return RedirectResponse('/login', status_code=303)
    admin=need(req,'dashboard.view')
    if admin['role_code'] not in ('admin','deputy'): raise HTTPException(403,'لوحة الإدارة للمدير ونائب المدير فقط')
    from .command_center import PAGES, hidden_pages
    c=connect()
    try:hidden=set(hidden_pages(c))
    finally:c.close()
    nav=[(g,[(p,l) for p,l in items if p not in hidden and p!='/my-work']) for g,items in PAGES]
    nav=[(g,items) for g,items in nav if items]
    if 'users.manage' in perms(admin['user_id']):nav.append(('الإعدادات',[('/page-visibility','الصفحات الظاهرة')]))
    return templates.TemplateResponse(request=req,name='command_center.html',context={**ctx(req,'الرئيسية'),'cc_nav':nav})
@app.get('/settings', response_class=HTMLResponse)
def settings_hub(req: Request):
    """«الإعدادات»: every page that left the short side menu (owner 2026-11-10), as tiles. Login only; each tile is
    shown only if this account may open that page (same rules as the menu)."""
    u = session(req)
    if not u:
        return RedirectResponse('/login', status_code=303)
    from . import shell_nav
    return templates.TemplateResponse(request=req, name='settings_hub.html',
                                      context={**ctx(req, 'الإعدادات وباقي الصفحات'), 'hub': shell_nav.hub(u, perms(u['user_id']))})


# "المهام": one page, four views (big_update). Each view keeps the permission of the page it replaces.
TASK_VIEWS = {'cards': ('tasks.html', 'tasks.view', 'كل المهام'), 'table': ('tasks_table.html', 'tasks.view', 'المهام كجدول'),
              'quick': ('manage.html', 'tasks.edit', 'إدارة المهام'), 'board': ('tasks_board.html', 'tasks.view', 'لوحة المهام')}

@app.get('/tasks', response_class=HTMLResponse)
def tasks_page(req: Request, view: str = 'cards'):
    tpl, code, title = TASK_VIEWS.get(view) or TASK_VIEWS['cards']
    need(req, code)
    return templates.TemplateResponse(request=req, name=tpl, context={**ctx(req, title), 'task_view': view if view in TASK_VIEWS else 'cards'})

def _to_view(req: Request, view: str):
    # Old addresses keep working: same query string, the browser keeps any #fragment (e.g. /manage#12).
    q = req.url.query
    return RedirectResponse('/tasks?view=' + view + ('&' + q if q else ''), status_code=303)
@app.get('/my-work', response_class=HTMLResponse)
def my_work(req: Request):
    u = need(req, 'tasks.view')
    if u['role_code'] != 'employee':
        return RedirectResponse('/tasks', status_code=303)
    if not u['employee_id']:
        raise HTTPException(403, 'الحساب غير مربوط بموظف')
    return templates.TemplateResponse(request=req, name='my_work.html', context=ctx(req, 'مهامي'))

@app.get('/tasks-table')
def tasks_table_page(req: Request): need(req,'tasks.view'); return _to_view(req, 'table')
@app.get('/manage')
def manage_page(req: Request): need(req,'tasks.edit'); return _to_view(req, 'quick')
@app.get('/employees', response_class=HTMLResponse)
def employees_page(req: Request): need(req,'employees.view'); return templates.TemplateResponse(request=req,name='employees.html',context=ctx(req,'الموظفون'))
@app.get('/recurring', response_class=HTMLResponse)
def recurring_page(req: Request): need(req,'recurring.view'); return templates.TemplateResponse(request=req,name='recurring.html',context=ctx(req,'المهام المتكررة'))

@app.get('/integrations', response_class=HTMLResponse)
def integrations_page(req: Request): need(req,'dashboard.view'); return templates.TemplateResponse(request=req,name='integrations.html',context=ctx(req,'الربط والتكامل'))
@app.get('/audit', response_class=HTMLResponse)
def audit_page(req: Request): need(req,'audit.view'); return templates.TemplateResponse(request=req,name='audit.html',context=ctx(req,'سجل النشاط'))
@app.get('/users', response_class=HTMLResponse)
def users_page(req: Request): need(req,'users.manage'); return templates.TemplateResponse(request=req,name='users.html',context=ctx(req,'المستخدمون'))
@app.get('/system-health', response_class=HTMLResponse)
def health_page(req: Request): need(req,'dashboard.view'); return templates.TemplateResponse(request=req,name='health.html',context=ctx(req,'صحة النظام'))

@app.get('/archive', response_class=HTMLResponse)
def archive_page(req: Request): need(req,'tasks.view'); return templates.TemplateResponse(request=req,name='archive.html',context=ctx(req,'الأرشيف'))

@app.get('/task/{tid}', response_class=HTMLResponse)
def task_detail_page(tid:int, req:Request):
    u=need(req,'tasks.view'); c=connect(); row=_task_accessible(c,u,tid); c.close()
    if not row: raise HTTPException(404,'المهمة غير موجودة')
    return templates.TemplateResponse(request=req,name='task_detail.html',context={**ctx(req,'تفاصيل المهمة'),'task_id':tid})

# ---------- basic APIs ----------
@app.get('/api/me')
def api_me(req: Request):
    u = session(req)
    if not u: raise HTTPException(401)
    return {'id': u['user_id'], 'username': u['username'], 'full_name': u['full_name'], 'role': u['role_name'],
            'role_code': u['role_code'], 'employee_id': u['employee_id'], 'permissions': sorted(perms(u['user_id'])),
            'gateway': _main.GATEWAY, 'public_url': _main.PUBLIC_URL, 'hidden_pages': _hidden_pages()}

def _hidden_pages():
    from .command_center import hidden_pages
    c=connect()
    try:return hidden_pages(c)
    finally:c.close()

@app.get('/api/release-health')
def release_health(req: Request):
    # Minimal readiness evidence, no employee data, tokens, paths or counts.
    if not req.client or req.client.host not in ('127.0.0.1', '::1', 'testclient'):
        raise HTTPException(403, 'Local health check only')
    from . import workers as worker_state
    c = connect()
    try:
        for table in ('tasks', 'bim_job_profiles', 'bim_employee_jobs',
                      'bim_employee_prefs', 'telegram_task_messages', 'bim_work_settings', 'bim_instruction_reads', 'bim_completion_entries', 'bim_recurring_rules', 'bim_job_recurring', 'bim_webhook_claims', 'universal_templates', 'universal_template_versions', 'universal_links', 'universal_overrides', 'universal_task_meta', 'universal_staff_settings', 'universal_user_permissions'):
            c.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchall()
    finally:
        c.close()
    worker_ok = bool(worker_state.THREAD and worker_state.THREAD.is_alive())
    if _main.GATEWAY == 'lan' and not worker_ok:
        raise HTTPException(503, 'Background worker unavailable')
    return {'service': 'bim-tasks', 'release': '2026-10-22-bundles-r6',
            'database': 'ready', 'gateway': _main.GATEWAY, 'worker_alive': worker_ok,
            'commit': _main.RELEASE.get('commit', '')}


@app.get('/api/client-status')
def client_status(req: Request):
    need(req,'dashboard.view'); c=connect()
    failed=c.execute("SELECT COUNT(*) FROM notifications WHERE status='failed'").fetchone()[0]
    c.close(); return {'ok':True,'gateway':_main.GATEWAY,'sync_pending':0,'sync_failed':failed,'domain':DOMAIN}

@app.get('/api/dashboard')
def api_dashboard(req: Request):
    need(req,'dashboard.view')
    if session(req)['role_code'] not in ('admin','deputy'): raise HTTPException(403,'غير مصرح')
    c=connect(); q=lambda s:c.execute(s).fetchone()[0]
    data={'total':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND status NOT IN('تم','ملغاة')"),
          'today':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND date(created_at,'localtime')=date('now','localtime')"),
          'late':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND due_at IS NOT NULL AND datetime(due_at)<datetime('now','localtime') AND status NOT IN('تم','ملغاة')"),
          'done':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND status='تم'"),
          'working':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND status IN('بدأت','قيد التنفيذ')"),
          'waiting':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND status='بانتظار'"),
          'unseen':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND viewed_at IS NULL AND status NOT IN('تم','ملغاة')"),
          'employees':q("SELECT COUNT(*) FROM employees WHERE is_active=1"),
          'unassigned':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND employee_id IS NULL AND status NOT IN('تم','ملغاة')"),
          'sync_dirty':q("SELECT COUNT(*) FROM tasks WHERE COALESCE(sync_dirty,1)=1"),
          'sync_conflicts':q("SELECT COUNT(*) FROM sync_conflicts WHERE status='open'")}
    data['by_employee']=[dict(x) for x in c.execute('''SELECT COALESCE(e.name,'بدون موظف') name,COUNT(*) count
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE t.archived_at IS NULL AND t.status NOT IN('تم','ملغاة')
      GROUP BY t.employee_id ORDER BY count DESC,e.name LIMIT 12''')]
    data['latest']=[dict(x) for x in c.execute('''SELECT t.id,t.task_code,t.title,t.status,t.priority,t.due_at,
      COALESCE(e.name,'بدون موظف') employee FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE t.archived_at IS NULL ORDER BY t.id DESC LIMIT 12''')]
    c.close(); return data


@app.get('/my-team', response_class=HTMLResponse)
def my_team_page(req: Request):
    # Supervisor's mobile page: the team's open/overdue tasks with quick actions. Uses the existing task APIs,
    # so the same scope and permission checks apply (a supervisor only sees and changes their team).
    need(req,'tasks.edit'); return templates.TemplateResponse(request=req,name='my_team.html',context=ctx(req,'متابعة الفريق'))
