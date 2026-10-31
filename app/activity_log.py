"""Authenticated activity history. No request bodies, input values or session tokens in API output."""
import json,re,logging
from datetime import date,timedelta
from urllib.parse import urlsplit
from fastapi import Request,HTTPException
from starlette.concurrency import run_in_threadpool
from .db import connect
log=logging.getLogger('bim.activity')
PAGES={'/':'الرئيسية','/tasks':'المهام','/tasks-table':'جدول المهام','/manage':'إدارة المهام','/employees':'الموظفون','/users':'المستخدمون','/my-work':'مهامي','/audit':'سجل التغييرات','/activity-log':'السجل الشامل','/operations-reports':'التقارير التشغيلية','/team-performance':'أداء الفريق','/recurring':'المهام المتكررة','/company':'إعداد الشركة','/template-library':'قوالب العمل','/access-control':'الصلاحيات والقنوات','/handover':'تسليم الأعمال','/job-profiles':'المسميات الوظيفية','/notification-center':'مركز الإشعارات','/workload':'عبء العمل','/record-management':'إدارة السجلات','/telegram-center':'مركز تلغرام','/telegram-groups':'مجموعات تلغرام','/telegram-health':'صحة تلغرام','/telegram-dashboard':'تقارير تلغرام','/shift-center':'الشيفتات','/automation-center':'الأتمتة','/integrations':'التكاملات','/system-health':'صحة النظام','/archive':'الأرشيف','/sync':'المزامنة','/backup':'النسخ الاحتياطي','/iphone-setup':'إعداد الهاتف','/security':'الأمان والحماية','/permissions':'جدول الصلاحيات','/account':'حسابي','/logout':'تسجيل الخروج'}
RESOURCES={'tasks':'المهام','users':'المستخدمون','employees':'الموظفون','templates':'قوالب العمل','recurring':'المهام المتكررة','company':'الشركة','handover':'تسليم الأعمال','record-management':'السجلات','lifecycle':'السجلات','telegram':'تلغرام','telegram-groups':'مجموعات تلغرام','shift-settings':'إعدادات الدوام','shift-messages':'رسائل الدوام','shift-exceptions':'استثناءات الدوام','group-reminders':'تذكيرات المجموعات','bim-job-profiles':'المسميات الوظيفية','bim-employee-profile':'ملف الموظف','backup':'النسخ الاحتياطي','sync':'المزامنة','attachments':'المرفقات','notification-settings':'الإشعارات','security':'الأمان','me':'حسابي'}
def migrate(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS user_activity(
 id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,username TEXT,kind TEXT NOT NULL,
 label TEXT NOT NULL,page TEXT NOT NULL DEFAULT '',target TEXT NOT NULL DEFAULT '',
 result TEXT NOT NULL DEFAULT 'observed',http_status INTEGER,gateway TEXT,ip_address TEXT,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
 CREATE INDEX IF NOT EXISTS ix_activity_user_time ON user_activity(user_id,created_at,id);
 CREATE INDEX IF NOT EXISTS ix_activity_time ON user_activity(created_at,id);
 CREATE TABLE IF NOT EXISTS user_session_activity(session_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,
 last_seen_at TEXT,logout_at TEXT);
 CREATE INDEX IF NOT EXISTS ix_session_activity_user ON user_session_activity(user_id,last_seen_at);
 ''')
def page_path(value):
 p=urlsplit(str(value or '')).path
 return p if p in PAGES or re.fullmatch(r'/task/\d+',p) else ''
def record(c,u,kind,label,gateway,ip='',page='',target='',result='observed',status=None):
 c.execute('INSERT INTO user_activity(user_id,username,kind,label,page,target,result,http_status,gateway,ip_address) VALUES(?,?,?,?,?,?,?,?,?,?)',
 (u['user_id'] if u else None,u['username'] if u else '',kind,label[:160],page[:160],target[:180],result,status,gateway,ip[:100]))
def touch(c,sid,uid):
 if sid:c.execute("INSERT INTO user_session_activity(session_id,user_id,last_seen_at) VALUES(?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now')) ON CONFLICT(session_id) DO UPDATE SET last_seen_at=excluded.last_seen_at",(sid,uid))
def failed_login(c,req,u,uname,gateway,ip,status=401):
 # Username is the attempted account, not a verified actor; never store submitted passwords.
 identity={'user_id':u['id'] if u else None,'username':str(uname)[:100]}
 record(c,identity,'login','محاولة دخول مرفوضة — هوية غير مؤكدة',gateway,ip,result='denied',status=status)

def auth_event(c,req,u,sid,kind,gateway,ip):
 record(c,u,kind,'تسجيل دخول' if kind=='login' else 'تسجيل خروج صريح',gateway,ip,result='success',status=200)
 touch(c,sid,u['user_id'])
 if kind=='logout':c.execute("UPDATE user_session_activity SET logout_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE session_id=?",(sid,))
def sanitize(value):
 if isinstance(value,dict):return {k:sanitize(v) for k,v in value.items() if not re.search(r'password|token|secret|csrf|authorization|cookie|session|private.?key|credential',k,re.I)}
 if isinstance(value,list):return [sanitize(v) for v in value]
 return value

def redact(value):
 allowed={'title','name','status','priority','department','employee_id','is_active','role_code','role_id','archived','archived_at','due_at','completed_at','estimated_minutes','capacity_minutes','channel','job_ids','display_mode','display_mode_override','version','task_ids','changed','skipped','count','open_policy','cancelled_task_ids','shift_type','shift_start','shift_end','work_days','telegram_enabled','whatsapp_enabled','permissions','start','progress','done','comment','attachment','run_time','schedule','mode','kind'}
 if isinstance(value,dict):return {k:redact(v) for k,v in value.items() if k in allowed}
 if isinstance(value,list):return [redact(v) for v in value[:100]]
 if isinstance(value,str):return value[:500]
 return value

def register(app,m):
 @app.middleware('http')
 async def capture(req,call_next):
  path=req.url.path
  tracked=not path.startswith(('/static/','/api/activity')) and path not in ('/login','/logout','/health','/api/release-health')
  u=None
  if tracked:
   try:u=await run_in_threadpool(m.session,req)
   except Exception:log.exception('Could not resolve activity identity')
  try:response=await call_next(req)
  except Exception:
   if u:await run_in_threadpool(store,req,u,path,500,'')
   raise
  if u:await run_in_threadpool(store,req,u,path,response.status_code,response.headers.get('content-type',''),response.headers.get('content-disposition',''))
  return response
 def store(req,u,path,status,ctype,disposition=''):
  page=page_path(path);kind=None
  if req.method=='GET' and page:kind='page';label='فتح صفحة: '+PAGES.get(page,'تفاصيل المهمة')
  elif req.method not in ('GET','HEAD','OPTIONS') and path.startswith('/api'):
   kind='request';resource=path.split('/')[2];label={'POST':'تنفيذ / إضافة','PUT':'حفظ','PATCH':'تعديل','DELETE':'حذف'}.get(req.method,'تنفيذ')+' — '+RESOURCES.get(resource,'إجراء بالنظام')
  elif req.method=='GET' and ('attachment' in disposition.lower() or path.endswith(('.csv','.xlsx','.pdf')) or path.startswith('/api/attachments/')):kind='download';label='طلب تنزيل / تصدير'
  if not kind:return
  route=req.scope.get('route');target=getattr(route,'path',path)
  # Only registered route templates and numeric entity ids; no query strings or secret path values.
  if not route:target='مسار غير معروف'
  elif re.search(r'\{',target):
   for k,v in req.path_params.items():
    if str(v).isdigit():target=target.replace('{'+k+'}',str(v))
  referer=urlsplit(req.headers.get('referer',''))
  if not page and referer.netloc==req.url.netloc:page=page_path(referer.path)
  c=connect()
  try:
   record(c,u,kind,label,m.GATEWAY,m.ip(req),page,target,'success' if status<400 else 'denied' if status in (401,403) else 'failed',status)
   touch(c,req.cookies.get(m.COOKIE),u['user_id'])
  except Exception:log.exception('Activity write failed')
  finally:c.close()
 @app.get('/activity-log')
 def activity_page(req:Request):
  m.need(req,'audit.view')
  return m.templates.TemplateResponse(request=req,name='activity_log.html',context=m.ctx(req,'السجل الشامل للمستخدمين'))
 @app.post('/api/activity/events')
 async def client_event(req:Request):
  u=m.session(req)
  if not u:raise HTTPException(401,'انتهت الجلسة')
  if req.headers.get('x-bim-activity')!='1':raise HTTPException(403,'طلب غير صالح')
  origin=req.headers.get('origin')
  if origin and urlsplit(origin).netloc!=req.url.netloc:raise HTTPException(403,'مصدر غير صالح')
  raw=await req.body()
  if len(raw)>1024:raise HTTPException(413,'طلب كبير')
  try:d=json.loads(raw)
  except (ValueError,TypeError):raise HTTPException(400,'طلب غير صالح')
  if not isinstance(d,dict) or d.get('kind') not in ('click','heartbeat'):raise HTTPException(400,'نوع غير صالح')
  p=page_path(d.get('page'))
  if not p:raise HTTPException(400,'صفحة غير صالحة')
  c=connect()
  try:
   sid=req.cookies.get(m.COOKIE)
   # Limit client telemetry per session/user; it is observed UI activity, never proof of a save.
   if d['kind']=='click':
    count=c.execute("SELECT COUNT(*) FROM user_activity WHERE user_id=? AND kind='click' AND julianday(created_at)>julianday('now','-1 minute')",(u['user_id'],)).fetchone()[0]
    if count>=120:raise HTTPException(429,'طلبات متكررة')
    label=re.sub(r'[\x00-\x1f]',' ',str(d.get('label','زر')))[:100]
    record(c,u,'click','ضغط: '+label,m.GATEWAY,m.ip(req),p,result='observed')
   touch(c,sid,u['user_id']);return {'ok':True}
  finally:c.close()
 def filters(user_id,start,end,gateway):
  where=['1=1'];args=[]
  if user_id is not None:where.append('user_id=?');args.append(user_id)
  for name,value in [('start',start),('end',end)]:
   if value:
    try:day=date.fromisoformat(value)
    except ValueError:raise HTTPException(400,'تاريخ غير صالح')
    # User-selected dates are Damascus local dates (UTC+3); stored timestamps are UTC.
    where.append("julianday(created_at) "+('>=' if name=='start' else '<')+" julianday(?,'-3 hours')")
    args.append(str(day if name=='start' else day+timedelta(days=1)))
  if gateway:
   if gateway not in ('lan','external'):raise HTTPException(400,'بوابة غير صالحة')
   where.append('gateway=?');args.append(gateway)
  return where,args
 @app.get('/api/activity/events')
 def events(req:Request,user_id:int=None,start:str='',end:str='',gateway:str='',kind:str='',result:str='',page:str='',offset:int=0,limit:int=100):
  m.need(req,'audit.view');where,args=filters(user_id,start,end,gateway)
  for column,value,choices in [('kind',kind,('page','request','download','click','login','logout')),('result',result,('success','failed','denied','observed'))]:
   if value:
    if value not in choices:raise HTTPException(400,'مرشح غير صالح')
    where.append(column+'=?');args.append(value)
  if page:
   if page=='/task/':where.append("page LIKE '/task/%'")
   elif page_path(page):where.append('page=?');args.append(page)
   else:raise HTTPException(400,'صفحة غير صالحة')
  clause=' AND '.join(where);c=connect()
  try:
   total=c.execute('SELECT COUNT(*) FROM user_activity WHERE '+clause,args).fetchone()[0]
   rows=[dict(x) for x in c.execute('SELECT * FROM user_activity WHERE '+clause+' ORDER BY id DESC LIMIT ? OFFSET ?',args+[min(max(limit,1),100),max(offset,0)])]
   users=[dict(x) for x in c.execute('SELECT id,username,full_name FROM users ORDER BY full_name')]
   return {'items':rows,'total':total,'users':users,'pages':PAGES}
  finally:c.close()
 @app.get('/api/activity/sessions')
 def sessions(req:Request,user_id:int=None,start:str='',end:str='',gateway:str='',offset:int=0):
  m.need(req,'audit.view');c=connect();clauses,args=filters(user_id,start,end,gateway);where='WHERE '+' AND '.join(clauses)
  for key in ('user_id','created_at','gateway'):where=re.sub(r'\b'+key+r'\b','s.'+key,where)
  try:
   rows=c.execute('''SELECT u.username,u.full_name,s.created_at,s.expires_at,s.gateway,s.ip_address,a.last_seen_at,a.logout_at,
    CASE WHEN a.logout_at IS NOT NULL THEN 'logout' WHEN s.revoked_at IS NOT NULL THEN 'revoked'
    WHEN julianday(s.expires_at)<=julianday('now') THEN 'expired' ELSE 'valid' END state,
    CASE WHEN a.logout_at IS NOT NULL THEN a.logout_at WHEN s.revoked_at IS NOT NULL THEN s.revoked_at
    WHEN julianday(s.expires_at)<=julianday('now') THEN s.expires_at ELSE NULL END ended_at
    FROM login_sessions s JOIN users u ON u.id=s.user_id LEFT JOIN user_session_activity a ON a.session_id=s.id '''+where+' ORDER BY s.created_at DESC LIMIT 100 OFFSET ?',args+[max(offset,0)]).fetchall()
   total=c.execute('SELECT COUNT(*) FROM login_sessions s '+where,args).fetchone()[0]
   return {'items':[dict(x) for x in rows],'total':total}
  finally:c.close()
 @app.get('/api/activity/changes')
 def change_list(req:Request,user_id:int=None,start:str='',end:str='',gateway:str='',offset:int=0):
  m.need(req,'audit.view');where,args=filters(user_id,start,end,gateway);clause=' AND '.join(where);c=connect()
  try:
   total=c.execute('SELECT COUNT(*) FROM audit_logs WHERE '+clause,args).fetchone()[0]
   rows=[dict(x) for x in c.execute('SELECT id,user_id,username_snapshot username,action,entity_type,entity_id,gateway,created_at FROM audit_logs WHERE '+clause+' ORDER BY id DESC LIMIT 100 OFFSET ?',args+[max(offset,0)])]
   return {'items':rows,'total':total}
  finally:c.close()
 @app.get('/api/activity/changes/{aid}')
 def changes(aid:int,req:Request):
  m.need(req,'audit.view');c=connect()
  try:r=c.execute('SELECT id,action,before_json,after_json FROM audit_logs WHERE id=?',(aid,)).fetchone()
  finally:c.close()
  if not r:raise HTTPException(404,'السجل غير موجود')
  def safe(s):
   try:return redact(json.loads(s or '{}'))
   except ValueError:return {}
  return {'id':r['id'],'before':safe(r['before_json']),'after':safe(r['after_json'])}
