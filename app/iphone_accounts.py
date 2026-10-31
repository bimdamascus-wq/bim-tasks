"""Per-account shortcut keys grant create-own-task only. Existing manager key remains compatible."""
import hashlib,hmac,json,secrets,time,logging
from fastapi import Request,HTTPException
from .db import connect
from .activity_log import record

def migrate(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS iphone_account_keys(
 user_id INTEGER PRIMARY KEY REFERENCES users(id),employee_id INTEGER NOT NULL REFERENCES employees(id),
 key_hash TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,last_used_at TEXT);
 CREATE TABLE IF NOT EXISTS iphone_preferences(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 ''')

def eligible(c,m,uid):
 u=c.execute('''SELECT u.id user_id,u.username,u.full_name,u.employee_id,u.is_active,u.deleted_at,
 r.code role_code,r.external_access_allowed FROM users u JOIN roles r ON r.id=u.role_id WHERE u.id=?''',(uid,)).fetchone()
 if not u or not u['is_active'] or u['deleted_at']:raise HTTPException(403,'الحساب غير فعال')
 e=c.execute('SELECT * FROM employees WHERE id=? AND is_active=1',(u['employee_id'],)).fetchone()
 if not e:raise HTTPException(403,'اربط الحساب بموظف فعال أولاً')
 if m.channel(c,e['id'])=='telegram':raise HTTPException(403,'قناة هذا الموظف مخصصة لتلغرام فقط')
 if m.GATEWAY=='external' and not u['external_access_allowed']:raise HTTPException(403,'الدخول الخارجي غير مسموح لهذا الحساب')
 if 'tasks.view' not in m.perms(uid):raise HTTPException(403,'الحساب لا يملك صلاحية الوصول إلى المهام')
 return dict(u),e

def administration(c,m):
 rows=[e for e in c.execute('SELECT * FROM employees WHERE is_active=1') if m._norm_ar(e['name'])==m._norm_ar('الادارة')]
 if len(rows)!=1:raise HTTPException(409,'يجب وجود موظف فعال واحد باسم الادارة؛ لم تحفظ المهمة لتجنب إسناد خاطئ')
 return rows[0]

async def create(req,mode,supplied,m):
 if len(await req.body())>16000:raise HTTPException(413,'النص طويل جداً')
 ctype=req.headers.get('content-type','').lower()
 try:d=await req.json() if 'application/json' in ctype else dict((await req.form()).multi_items())
 except Exception:raise HTTPException(400,'بيانات غير صالحة')
 if not isinstance(d,dict):raise HTTPException(400,'بيانات غير صالحة')
 key=(supplied or await m._iphone_value_to_text(d.get('secret'))).strip()
 c=connect();actor=None;legacy=False;tid=None
 try:
  # Wrong shortcut keys count like failed logins, so a key cannot be guessed by brute force.
  if c.execute("SELECT COUNT(*) FROM login_attempts WHERE username='[iphone]' AND ip_address=? AND success=0 AND attempted_at>datetime('now','-15 minutes')",(m.ip(req),)).fetchone()[0]>=10:
   raise HTTPException(429,'محاولات خاطئة كثيرة؛ أعد المحاولة بعد 15 دقيقة')
  match=c.execute('SELECT * FROM iphone_account_keys WHERE key_hash=?',(hashlib.sha256(key.encode()).hexdigest(),)).fetchone() if key else None
  if match:
   actor,emp=eligible(c,m,match['user_id'])
   if emp['id']!=match['employee_id']:raise HTTPException(403,'تغير الموظف المرتبط بالحساب؛ أعد إصدار مفتاح الاختصار')
  elif key and m.IPHONE_SECRET and hmac.compare_digest(key,m.IPHONE_SECRET):legacy=True;emp=None
  else:
   c.execute("INSERT INTO login_attempts(username,ip_address,success) VALUES('[iphone]',?,0)",(m.ip(req),))
   record(c,{'user_id':None,'username':'[iphone]'},'login','مفتاح اختصار iPhone خاطئ',m.GATEWAY,m.ip(req),result='denied',status=401)
   raise HTTPException(401,'مفتاح الاختصار غير صحيح أو ملغى')
  raw=await m._iphone_value_to_text(d.get('text') or d.get('task') or d.get('voiceText') or d.get('النص'))
  if not raw or len(raw)>4000:raise HTTPException(400,'أدخل نص المهمة بطول لا يتجاوز 4000 حرف')
  parsed,title,priority=m._parse_iphone_text(c,raw)
  if legacy:
   emp=parsed if parsed is not None else administration(c,m)
   pref=c.execute("SELECT value FROM iphone_preferences WHERE key='legacy_owner'").fetchone()
   if pref:
    actor,_=eligible(c,m,int(pref['value']))
    if actor['role_code']!='admin':raise HTTPException(403,'صاحب الاختصارات الإدارية لم يعد مديراً')
  else:
   # Employee names in spoken text never redirect a personal shortcut to another person.
   if parsed is None or parsed['id']!=emp['id']:title=raw
  if d.get('priority') in ('عادي','عاجلة','عالية','منخفضة'):priority=d['priority']
  identity=actor or {'user_id':None,'username':'اختصار الإدارة — مفتاح مشترك'}
  c.execute('BEGIN IMMEDIATE')
  if actor and c.execute("SELECT COUNT(*) FROM user_activity WHERE user_id=? AND target LIKE 'iphone:%' AND created_at>strftime('%Y-%m-%dT%H:%M:%fZ','now','-1 minute')",(actor['user_id'],)).fetchone()[0]>=30:
   raise HTTPException(429,'طلبات كثيرة؛ أعد المحاولة بعد دقيقة')
  code='TASK-'+str(int(time.time()*1000))+'-'+secrets.token_hex(3)
  c.execute('INSERT INTO tasks(task_code,employee_id,title,priority,status,source,created_by) VALUES(?,?,?,?,?,?,?)',(code,emp['id'],title,priority,'جديدة','iphone_'+mode,actor['user_id'] if actor else None))
  tid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  m._save_reminder(c,tid,{'type':'بدون'});m._touch_task_sync(c,tid,'iphone','create')
  m.audit(c,req,identity,'task_create','task',tid,after={'title':title,'employee_id':emp['id'],'priority':priority,'source':'iphone_'+mode})
  record(c,identity,'request','إنشاء مهمة '+('صوتية' if mode=='voice' else 'كتابية')+' من الاختصار',m.GATEWAY,m.ip(req),target='iphone:'+str(tid),result='success',status=200)
  if match:c.execute('UPDATE iphone_account_keys SET last_used_at=CURRENT_TIMESTAMP WHERE user_id=?',(actor['user_id'],))
  c.execute('COMMIT')
  try:m._notify_employee(c,tid,'مهمة من iPhone')
  except Exception:logging.getLogger('bim.iphone').exception('Task saved; notification failed')
  return {'ok':True,'id':tid,'employee':emp['name'],'task':title,'priority':priority,'mode':mode,'message':'تم حفظ المهمة لدى '+emp['name']}
 except Exception:
  if c.in_transaction:c.execute('ROLLBACK')
  raise
 finally:c.close()

def register(app,m):
 @app.get('/api/iphone/accounts')
 def accounts(req:Request):
  m.need(req,'users.manage');c=connect()
  try:
   rows=[dict(x) for x in c.execute('''SELECT u.id,u.username,u.full_name,u.employee_id,e.name employee_name,r.code role_code,
    k.created_at,k.last_used_at FROM users u JOIN roles r ON r.id=u.role_id LEFT JOIN employees e ON e.id=u.employee_id
    LEFT JOIN iphone_account_keys k ON k.user_id=u.id WHERE u.is_active=1 AND u.deleted_at IS NULL ORDER BY u.id''')]
   owner=c.execute("SELECT value FROM iphone_preferences WHERE key='legacy_owner'").fetchone()
   return {'items':rows,'legacy_owner':int(owner['value']) if owner else None,'default_employee':'الادارة'}
  finally:c.close()
 @app.post('/api/iphone/accounts/{uid}/key')
 def issue(uid:int,req:Request):
  admin=m.need(req,'users.manage');c=connect()
  try:
   u,e=eligible(c,m,uid);key='bim_'+secrets.token_urlsafe(32)
   c.execute('INSERT INTO iphone_account_keys(user_id,employee_id,key_hash) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET employee_id=excluded.employee_id,key_hash=excluded.key_hash,created_at=CURRENT_TIMESTAMP,last_used_at=NULL',(uid,e['id'],hashlib.sha256(key.encode()).hexdigest()))
   m.audit(c,req,admin,'iphone_key_issue','user',uid,after={'employee_id':e['id']})
   return {'ok':True,'secret':key,'employee':e['name'],'message':'انسخ المفتاح الآن؛ يظهر مرة واحدة. المفتاح السابق لهذا الحساب ألغي.'}
  finally:c.close()
 @app.delete('/api/iphone/accounts/{uid}/key')
 def revoke(uid:int,req:Request):
  admin=m.need(req,'users.manage');c=connect()
  try:
   c.execute('DELETE FROM iphone_account_keys WHERE user_id=?',(uid,));m.audit(c,req,admin,'iphone_key_revoke','user',uid);return {'ok':True}
  finally:c.close()
 @app.put('/api/iphone/legacy-owner')
 async def owner(req:Request):
  admin=m.need(req,'security.manage');d=await req.json();c=connect()
  try:
   try:uid=int(d.get('user_id',0))
   except (ValueError,TypeError):raise HTTPException(400,'اختر حساب المدير')
   u,e=eligible(c,m,uid)
   if u['role_code']!='admin' or e['id']!=administration(c,m)['id']:raise HTTPException(400,'اختر حساب مدير مرتبط بالموظف الادارة')
   c.execute("INSERT INTO iphone_preferences(key,value) VALUES('legacy_owner',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(uid),))
   m.audit(c,req,admin,'iphone_owner_set','user',uid,after={'employee_id':e['id']});return {'ok':True}
  finally:c.close()
