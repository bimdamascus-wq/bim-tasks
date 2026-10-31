"""V6.3 manager command center (new home dashboard) and page-visibility settings."""
import json
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .db import connect
from .delegation import decorate

# Every page that can be shown or hidden from the menu. The home page and this settings page are always visible.
PAGES=[
 ('العمل اليومي',[('/tasks','كل المهام'),('/tasks-table','المهام كجدول'),('/manage','إدارة المهام'),('/my-work','مهامي'),('/archive','الأرشيف')]),
 ('الفريق',[('/employees','الموظفون'),('/users','المستخدمون'),('/access-control','الصلاحيات والقنوات'),('/job-profiles','المسميات الوظيفية'),('/workload','عبء العمل'),('/handover','تسليم الأعمال'),('/shift-center','الشيفتات')]),
 ('الأتمتة',[('/recurring','المهام المتكررة'),('/template-library','قوالب العمل'),('/automation-center','مركز الأتمتة'),('/notification-center','مركز الإشعارات')]),
 ('Telegram والهاتف',[('/telegram-center','مركز Telegram'),('/telegram-groups','مجموعات Telegram'),('/telegram-health','صحة Telegram'),('/telegram-dashboard','تقارير Telegram'),('/iphone-setup','إعداد الهاتف'),('/integrations','الربط والتكامل')]),
 ('التقارير والسجلات',[('/team-performance','أداء الفريق'),('/operations-reports','التقارير التشغيلية'),('/activity-log','سجل النشاط'),('/audit','سجل التغييرات'),('/record-management','إدارة السجلات')]),
 ('النظام',[('/security','الأمان والحماية'),('/permissions','جدول الصلاحيات'),('/company','إعداد الشركة'),('/system-health','صحة النظام'),('/sync','مزامنة Google Sheet'),('/backup','النسخ الاحتياطي')]),
]
ALL_PATHS={p for _,items in PAGES for p,_ in items}
DEFAULT_HIDDEN=['/job-profiles','/template-library','/workload','/handover','/automation-center','/operations-reports',
                '/telegram-dashboard','/audit','/integrations','/record-management','/company']
SETTING='ui.hidden_pages'

def hidden_pages(c):
    r=c.execute('SELECT value FROM settings WHERE key=?',(SETTING,)).fetchone()
    if not r:return list(DEFAULT_HIDDEN)
    try:return [p for p in json.loads(r[0]) if p in ALL_PATHS]
    except Exception:return list(DEFAULT_HIDDEN)

OPEN="t.archived_at IS NULL AND t.status NOT IN ('تم','ملغاة')"
LATE=OPEN+" AND t.due_at IS NOT NULL AND datetime(t.due_at)<datetime('now','localtime')"

def _rows(c, where, params, order, limit):
    rows=[dict(x) for x in c.execute(f'''SELECT t.id,t.title,t.status,t.priority,t.due_at,t.created_at,t.completed_at,t.employee_id,t.created_by,t.source,
      COALESCE(e.name,'بدون موظف') employee_name FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id
      WHERE {where} ORDER BY {order} LIMIT {int(limit)}''',params)]
    return decorate(c,rows)

def dashboard_data(c, scope='today', employee_id=None):
    emp_sql,emp_p=('',[]) if not employee_id else (' AND t.employee_id=?',[int(employee_id)])
    q=lambda s,p=():c.execute(s,p).fetchone()[0]
    week_done=c.execute(f"""SELECT COUNT(*),SUM(CASE WHEN t.due_at IS NULL OR datetime(t.completed_at,'localtime')<=datetime(t.due_at) THEN 1 ELSE 0 END)
      FROM tasks t WHERE t.status='تم' AND t.completed_at IS NOT NULL AND datetime(t.completed_at,'localtime')>=datetime('now','localtime','-7 days'){emp_sql}""",emp_p).fetchone()
    stats={'open':q(f'SELECT COUNT(*) FROM tasks t WHERE {OPEN}{emp_sql}',emp_p),
           'late':q(f'SELECT COUNT(*) FROM tasks t WHERE {LATE}{emp_sql}',emp_p),
           'done_today':q(f"SELECT COUNT(*) FROM tasks t WHERE t.status='تم' AND date(t.completed_at,'localtime')=date('now','localtime'){emp_sql}",emp_p),
           'on_time':round(100*(week_done[1] or 0)/week_done[0]) if week_done[0] else None,
           'delegated_today':q("SELECT COUNT(*) FROM task_delegations WHERE date(created_at,'localtime')=date('now','localtime')")}
    if scope=='week':
        due_where=f"{OPEN} AND NOT ({LATE}) AND t.due_at IS NOT NULL AND date(t.due_at)<=date('now','localtime','+7 days')"
        done_where="t.status='تم' AND datetime(t.completed_at,'localtime')>=datetime('now','localtime','-7 days')"
    elif scope=='all':
        due_where=f"{OPEN} AND NOT ({LATE})"
        done_where="t.status='تم' AND datetime(t.completed_at,'localtime')>=datetime('now','localtime','-30 days')"
    else:
        scope='today'
        due_where=f"{OPEN} AND NOT ({LATE}) AND (date(t.due_at)=date('now','localtime') OR (t.due_at IS NULL AND date(t.created_at,'localtime')=date('now','localtime')))"
        done_where="t.status='تم' AND date(t.completed_at,'localtime')=date('now','localtime')"
    groups={'attention':_rows(c,LATE+emp_sql,emp_p,"datetime(t.due_at)",60),
            'due':_rows(c,due_where+emp_sql,emp_p,"CASE WHEN t.due_at IS NULL THEN 1 ELSE 0 END,datetime(t.due_at),t.id DESC",80),
            'done':_rows(c,done_where+emp_sql,emp_p,"t.completed_at DESC",30)}
    # The team: people with a working login who are not managers or view-only accounts.
    team=[dict(x) for x in c.execute(f'''SELECT e.id,e.name,
        (SELECT COUNT(*) FROM tasks t WHERE t.employee_id=e.id AND {OPEN}) open,
        (SELECT COUNT(*) FROM tasks t WHERE t.employee_id=e.id AND {LATE}) late
      FROM employees e WHERE e.is_active=1 AND EXISTS(SELECT 1 FROM users u JOIN roles r ON r.id=u.role_id
        WHERE u.employee_id=e.id AND u.is_active=1 AND u.deleted_at IS NULL AND r.code NOT IN ('admin','viewer'))
      ORDER BY e.sort_order,e.name''')]
    if not team:
        team=[dict(x) for x in c.execute(f'''SELECT e.id,e.name,COUNT(t.id) open,SUM(CASE WHEN datetime(t.due_at)<datetime('now','localtime') THEN 1 ELSE 0 END) late
          FROM tasks t JOIN employees e ON e.id=t.employee_id WHERE {OPEN} GROUP BY e.id ORDER BY open DESC LIMIT 6''')]
    top=max([m['open'] or 0 for m in team]+[1])
    for m in team:
        m['late']=m['late'] or 0; m['load']=round(100*(m['open'] or 0)/top)
    feed=[]
    for r in c.execute('''SELECT ev.event_type,ev.new_status,ev.note,ev.created_at,t.id task_id,t.title,COALESCE(e.name,'') name
        FROM task_events ev JOIN tasks t ON t.id=ev.task_id LEFT JOIN employees e ON e.id=ev.employee_id
        WHERE ev.event_type IN ('status','delegated','note') ORDER BY ev.id DESC LIMIT 12'''):
        if r['event_type']=='status':
            verb={'تم':'أنجز','بدأت':'بدأ','قيد التنفيذ':'يعمل على','بانتظار':'أوقف مؤقتاً','ملغاة':'ألغى'}.get(r['new_status'] or '','حدّث')
            text=f"{r['name'] or 'الإدارة'} {verb} «{r['title']}»"
        elif r['event_type']=='delegated':
            text=f"وزّع «{r['title']}»: {r['note'] or ''}"
        else:
            text=f"{r['name'] or 'موظف'} أضاف ملاحظة على «{r['title']}»"
        feed.append({'text':text,'at':r['created_at'],'task_id':r['task_id'],'name':r['name']})
    return {'scope':scope,'stats':stats,'groups':groups,'team':team,'feed':feed}

def register(app, m):
    def _manager(req):
        u=m.session(req)
        if not u:return None
        if u['role_code'] not in ('admin','deputy'):raise HTTPException(403,'لوحة الإدارة للمدير ونائب المدير فقط')
        return u

    @app.get('/api/v2/dashboard')
    def v2_dashboard(req:Request, scope:str='today', employee_id:int|None=None):
        u=_manager(req)
        if not u:raise HTTPException(401)
        c=connect()
        try:return dashboard_data(c,scope,employee_id)
        finally:c.close()

    @app.get('/api/v2/parse-task')
    def v2_parse(req:Request, text:str=''):
        m.need(req,'tasks.create')
        c=connect()
        try:
            emp,title,priority=m._parse_iphone_text(c,text[:4000])
            return {'employee_id':emp['id'] if emp else None,'employee':emp['name'] if emp else None,'title':title,'priority':priority}
        finally:c.close()

    @app.get('/api/page-visibility')
    def get_visibility(req:Request):
        m.need(req,'users.manage'); c=connect()
        try:
            h=set(hidden_pages(c))
            return {'groups':[{'label':g,'pages':[{'path':p,'label':l,'hidden':p in h} for p,l in items]} for g,items in PAGES],
                    'defaults':DEFAULT_HIDDEN}
        finally:c.close()

    @app.put('/api/page-visibility')
    async def put_visibility(req:Request):
        u=m.need(req,'users.manage'); d=await req.json()
        hidden=[p for p in (d.get('hidden') or []) if p in ALL_PATHS]
        c=connect()
        try:
            c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(SETTING,json.dumps(hidden)))
            m.audit(c,req,u,'page_visibility','system',None,after={'hidden':hidden})
            return {'ok':True,'hidden':hidden}
        finally:c.close()

    @app.get('/page-visibility', response_class=HTMLResponse)
    def visibility_page(req:Request):
        if not m.session(req):return RedirectResponse('/login',status_code=303)
        m.need(req,'users.manage')
        return m.templates.TemplateResponse(request=req,name='page_visibility.html',context=m.ctx(req,'الصفحات الظاهرة'))
