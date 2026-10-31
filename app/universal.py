"""Reusable company configuration, versioned templates, workload and handover."""
import json,hashlib,re,secrets
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from fastapi import HTTPException,Request,UploadFile,File
from fastapi.responses import HTMLResponse,FileResponse
from .db import connect
from .template_items import task_items, instruction_items, validate_items, item_body, effective_item, removed_keys, open_tasks, cancel_open, sync_changes

ROOT=Path(__file__).resolve().parents[1]
BASE_COMPANY={'name':'بيم دمشق','timezone':'Asia/Damascus','departments':[],'work_days':[0,1,2,3,4,5,6],'public_url':'','configured':False,'logo':False}

def company(c):
    r=c.execute("SELECT value FROM settings WHERE key='universal_company'").fetchone()
    return {**BASE_COMPANY,**(json.loads(r[0]) if r else {})}

def zone(name):
    if not re.fullmatch(r'[A-Za-z0-9_+/-]+',name) or '..' in name or name.startswith('/'):raise ValueError('Invalid timezone')
    try:return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        import zipfile,io
        try:
            with zipfile.ZipFile(ROOT/'app/timezones.zip') as z:return ZoneInfo.from_file(io.BytesIO(z.read(name)),key=name)
        except (KeyError,FileNotFoundError):raise ZoneInfoNotFoundError(name)

def local_now(c):
    return datetime.now(zone(company(c)['timezone'])).replace(tzinfo=None)

def channel(c,eid):
    r=c.execute('SELECT channel FROM universal_staff_settings WHERE employee_id=?',(eid,)).fetchone()
    return r[0] if r else 'both'

def body_for(c,tid,version=None):
    if version is None:
        r=c.execute('SELECT version FROM universal_templates WHERE id=?',(tid,)).fetchone()
        if not r:raise HTTPException(404,'القالب غير موجود')
        version=r[0]
    r=c.execute('SELECT body_json FROM universal_template_versions WHERE template_id=? AND version=?',(tid,version)).fetchone()
    if not r:raise HTTPException(404,'نسخة القالب غير موجودة')
    return json.loads(r[0])

def targets(c,tid):
    return [r[0] for r in c.execute("""SELECT DISTINCT e.id FROM employees e WHERE e.is_active=1 AND (
      EXISTS(SELECT 1 FROM universal_links l WHERE l.template_id=? AND l.target_kind='employee' AND l.target_id=e.id)
      OR EXISTS(SELECT 1 FROM universal_links l JOIN bim_employee_jobs j ON j.job_id=l.target_id
        JOIN bim_job_profiles p ON p.id=j.job_id WHERE l.template_id=? AND l.target_kind='job' AND p.is_active=1 AND j.employee_id=e.id)) ORDER BY e.id""",(tid,tid))]

def effective(c,tid,eid):
    b=body_for(c,tid);r=c.execute('SELECT body_json FROM universal_overrides WHERE template_id=? AND employee_id=?',(tid,eid)).fetchone()
    if r:
        d=json.loads(r[0]);b.update({k:v for k,v in d.items() if k!='items'})
    if 'tasks' in b:
        b['tasks']=[{**item,**(json.loads(r[0]).get('items',{}).get(item['key'],{}) if r else {})} for item in b['tasks']]
    return b

def instructions(c,eid):
    parts=[]
    for t in c.execute("SELECT id,name FROM universal_templates WHERE kind='instructions' AND is_active=1 ORDER BY id"):
        if eid in targets(c,t['id']):parts.append(t['name']+'\n'+effective(c,t['id'],eid)['instructions'])
    return '\n\n'.join(parts)

def validate_body(d,kind):
    return validate_items(d,kind,validate_single)

def validate_single(d,kind):
    if not isinstance(d,dict):raise HTTPException(400,'بيانات القالب غير صحيحة')
    text=str(d.get('instructions') or '')
    if len(text)>24000:raise HTTPException(400,'التعليمات طويلة')
    b={'instructions':text}
    if kind=='instructions':return b
    title=str(d.get('title') or '').strip();mode=d.get('mode','individual');schedule=d.get('schedule','manual')
    tm=d.get('run_time','09:00');priority=d.get('priority','عادي')
    if not title or len(title)>500 or mode not in ('individual','team') or schedule not in ('manual','daily','weekly','monthly') or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',str(tm)):raise HTTPException(400,'راجع عنوان المهمة ونوعها وتكرارها ووقتها')
    try:wd=int(d.get('week_day',0));md=int(d.get('month_day',1));minutes=int(d.get('estimated_minutes',0));due=int(d.get('due_minutes',480))
    except (ValueError,TypeError):raise HTTPException(400,'الأيام والمدد يجب أن تكون أرقاماً')
    if not 0<=wd<=6 or not 1<=md<=31 or not 0<=minutes<=100000 or not 1<=due<=525600:raise HTTPException(400,'المدة أو اليوم خارج المجال')
    fields=d.get('completion_fields',[])
    if not isinstance(fields,list) or len(fields)>12 or any(not isinstance(x,str) or not x.strip() or len(x)>100 for x in fields):raise HTTPException(400,'حقول الإنجاز غير صحيحة')
    if priority not in ('عادي','عالية','عاجلة','منخفضة'):raise HTTPException(400,'الأولوية غير صحيحة')
    b.update(title=title,mode=mode,schedule=schedule,run_time=tm,week_day=wd,month_day=md,estimated_minutes=minutes,due_minutes=due,priority=priority,completion_fields=list(dict.fromkeys(fields)),workdays_only=bool(d.get('workdays_only',True)))
    return b

def working(c,eid,day):
    e=c.execute('SELECT work_days,is_active FROM employees WHERE id=?',(eid,)).fetchone()
    if not e or not e['is_active']:return False
    ex=c.execute("SELECT exception_type FROM shift_exceptions WHERE employee_id=? AND is_active=1 AND date(start_date)<=date(?) AND date(end_date)>=date(?) ORDER BY id DESC LIMIT 1",(eid,day.isoformat(),day.isoformat())).fetchone()
    if ex:return ex[0]=='دوام استثنائي'
    return str((day.weekday()+1)%7) in str(e['work_days'] or '').split(',')

def due_key(b,now):
    import calendar
    if b['schedule']=='manual' or now.strftime('%H:%M')<b['run_time']:return None
    if b['schedule']=='weekly' and (now.weekday()+1)%7!=b['week_day']:return None
    if b['schedule']=='monthly' and now.day!=min(b['month_day'],calendar.monthrange(now.year,now.month)[1]):return None
    return now.date().isoformat()

def generate(c,tid,uid=None,manual_key=None):
    # Caller holds transaction; task and claim snapshot are committed together.
    t=c.execute("SELECT * FROM universal_templates WHERE id=? AND is_active=1 AND kind='tasks'",(tid,)).fetchone()
    if not t:return []
    base=body_for(c,tid);now=local_now(c);created=[]
    for item in task_items(base):
        if not item.get('is_active',True):continue
        key=('manual:'+manual_key) if manual_key else due_key(item,now)
        if key is None:continue
        ids=[eid for eid in targets(c,tid) if manual_key or not item['workdays_only'] or working(c,eid,now.date())]
        groups=[ids] if item['mode']=='team' else [[eid] for eid in ids]
        for people in groups:
            if not people:continue
            b=item_body(base,item) if item['mode']=='team' else effective_item(c,tid,people[0],base,item)
            # Legacy item retains R5 generation keys, even after conversion to a bundle.
            prefix=str(tid) if item['key']=='legacy' else f"{tid}:item:{item['key']}"
            gkey=prefix+':'+key+':'+('team' if item['mode']=='team' else str(people[0]))
            if c.execute('SELECT 1 FROM universal_task_meta WHERE generation_key=?',(gkey,)).fetchone():continue
            cur=c.execute("INSERT INTO tasks(task_code,employee_id,title,notes,priority,status,due_at,created_by,source,is_group) VALUES(?,?,?,?,?,'جديدة',?,?,?,?)",('TPL-'+secrets.token_hex(8),people[0] if item['mode']=='individual' else None,b['title'],b['instructions'],b['priority'],(now+timedelta(minutes=b['due_minutes'])).isoformat(timespec='minutes'),uid,'template',int(item['mode']=='team')))
            taskid=cur.lastrowid
            if item['mode']=='team':c.executemany('INSERT INTO task_participants(task_id,employee_id) VALUES(?,?)',[(taskid,eid) for eid in people])
            c.execute('INSERT INTO universal_task_meta(task_id,template_id,version,body_json,generation_key) VALUES(?,?,?,?,?)',(taskid,tid,t['version'],json.dumps(b,ensure_ascii=False),gkey))
            c.execute("INSERT INTO task_events(task_id,employee_id,event_type,note,source) VALUES(?,?,'created',?,'template')",(taskid,people[0] if item['mode']=='individual' else None,t['name']))
            c.execute("INSERT INTO task_reminders(task_id,type,is_active) VALUES(?,'بدون',0)",(taskid,))
            created.append(taskid)
    return created


def dispatch_created(ids):
    from . import main
    c=connect()
    try:
        for tid in ids:
            t=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
            main._touch_task_sync(c,tid,'server','create')
            if t['is_group']:
                for r in c.execute('SELECT employee_id FROM task_participants WHERE task_id=?',(tid,)).fetchall():
                    main._notify_employee_id(c,r[0],main._telegram_task_text(t,'مهمة مشتركة جديدة'),tid,reply_markup=main._telegram_task_keyboard(tid))
            else:main._notify_employee(c,tid)
    finally:c.close()

def process_templates():
    c=connect();made=[]
    try:
        c.execute('BEGIN IMMEDIATE')
        for r in c.execute("SELECT id FROM universal_templates WHERE is_active=1 AND kind='tasks'").fetchall():made+=generate(c,r[0])
        c.commit()
    except Exception:c.rollback();raise
    finally:c.close()
    dispatch_created(made)

def task_form(c,tid,eid):
    r=c.execute('SELECT body_json FROM universal_task_meta WHERE task_id=?',(tid,)).fetchone()
    return json.loads(r[0]).get('completion_fields',[]) if r else None

def register(app,m):
    def admin(req):return m.need(req,'users.manage')
    def change_log(c,req,u,action,etype,eid,data):m.audit(c,req,u,action,etype,eid,after=data)

    @app.get('/company',response_class=HTMLResponse)
    def company_page(req:Request):
        admin(req);return m.templates.TemplateResponse(request=req,name='company.html',context=m.ctx(req,'إعداد الشركة'))

    @app.get('/api/company')
    def get_company(req:Request):
        if not m.session(req):raise HTTPException(401)
        c=connect()
        try:return company(c)
        finally:c.close()

    @app.put('/api/company')
    async def put_company(req:Request):
        u=admin(req);d=await req.json();name=str(d.get('name') or '').strip();tz=str(d.get('timezone','Asia/Damascus'));days=d.get('work_days',[]);deps=d.get('departments',[]);url=str(d.get('public_url') or '').rstrip('/')
        try:zone(tz)
        except (ZoneInfoNotFoundError,ValueError):raise HTTPException(400,'المنطقة الزمنية غير متاحة')
        if not name or len(name)>100 or not isinstance(days,list) or not days or any(type(x) is not int or x not in range(7) for x in days) or not isinstance(deps,list) or len(deps)>100 or any(not isinstance(x,str) or not x.strip() or len(x)>100 for x in deps):raise HTTPException(400,'راجع الاسم والأقسام وأيام العمل')
        if url and not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::\d+)?',url):raise HTTPException(400,'الرابط العام يجب أن يكون عنوان HTTPS دون مسار')
        c=connect()
        try:
            value={**company(c),'name':name,'timezone':tz,'departments':list(dict.fromkeys(deps)),'work_days':sorted(set(days)),'public_url':url,'configured':True}
            c.execute("INSERT INTO settings(key,value) VALUES('universal_company',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(value,ensure_ascii=False),))
            change_log(c,req,u,'company_settings','settings',None,value);return {'ok':True}
        finally:c.close()

    @app.post('/api/company/logo')
    async def logo_upload(req:Request,file:UploadFile=File(...)):
        admin(req);data=await file.read(2*1024*1024+1)
        if len(data)>2*1024*1024 or not (data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff')):raise HTTPException(400,'ارفع PNG أو JPEG حتى 2 ميغابايت')
        dest=ROOT/'data/company_logo';dest.parent.mkdir(exist_ok=True);tmp=dest.with_suffix('.tmp');tmp.write_bytes(data);tmp.replace(dest)
        c=connect()
        try:
            cfg=company(c);cfg['logo']=True;c.execute("INSERT INTO settings(key,value) VALUES('universal_company',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(cfg,ensure_ascii=False),))
        finally:c.close()
        return {'ok':True}

    @app.get('/company-logo')
    def logo():
        p=ROOT/'data/company_logo'
        if not p.is_file():raise HTTPException(404)
        return FileResponse(p,media_type='image/png' if p.read_bytes()[:8]==b'\x89PNG\r\n\x1a\n' else 'image/jpeg',headers={'X-Content-Type-Options':'nosniff'})

    @app.get('/template-library',response_class=HTMLResponse)
    def library_page(req:Request):
        admin(req);return m.templates.TemplateResponse(request=req,name='template_library.html',context=m.ctx(req,'قوالب العمل'))

    @app.get('/api/templates')
    def list_templates(req:Request):
        admin(req);c=connect()
        try:
            rows=[]
            for r in c.execute("SELECT * FROM universal_templates WHERE NOT EXISTS(SELECT 1 FROM record_lifecycle l WHERE l.entity='templates' AND l.record_id=universal_templates.id AND l.deleted_at IS NOT NULL) ORDER BY id DESC").fetchall():
                t=dict(r);t.update(body=body_for(c,t['id']),employees=targets(c,t['id']),links=[dict(x) for x in c.execute('SELECT target_kind,target_id FROM universal_links WHERE template_id=?',(t['id'],))]);rows.append(t)
            return rows
        finally:c.close()

    @app.post('/api/templates')
    async def create_template(req:Request):
        u=admin(req);d=await req.json();name=str(d.get('name') or '').strip();kind=d.get('kind','tasks');body=validate_body(d.get('body',{}),kind)
        if d.get('is_active',1) not in (0,1):raise HTTPException(400,'حالة القالب غير صحيحة')
        if not name or len(name)>100 or kind not in ('tasks','instructions'):raise HTTPException(400,'اسم أو نوع القالب غير صحيح')
        c=connect()
        try:
            c.execute('BEGIN IMMEDIATE');tid=c.execute('INSERT INTO universal_templates(name,kind,is_active) VALUES(?,?,?)',(name,kind,d.get('is_active',1))).lastrowid
            c.execute('INSERT INTO universal_template_versions(template_id,version,body_json) VALUES(?,1,?)',(tid,json.dumps(body,ensure_ascii=False)));change_log(c,req,u,'template_create','template',tid,d);c.commit();return {'id':tid,'version':1}
        except Exception:c.rollback();raise
        finally:c.close()

    @app.patch('/api/templates/{tid}')
    async def edit_template(tid:int,req:Request):
        u=admin(req);d=await req.json();c=connect()
        try:
            c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM universal_templates WHERE id=?',(tid,)).fetchone()
            if not old:raise HTTPException(404)
            if d.get('expected_version')!=old['version']:raise HTTPException(409,'تغير القالب؛ أعد تحميله قبل الحفظ')
            name=str(d.get('name',old['name'])).strip()
            if not name or len(name)>100:raise HTTPException(400,'الاسم غير صحيح')
            if d.get('is_active',old['is_active']) not in (0,1):raise HTTPException(400,'حالة القالب غير صحيحة')
            old_body=body_for(c,tid);body=validate_body(d.get('body',old_body),old['kind']);version=old['version']+1
            keys=removed_keys(old_body,body,old['kind']);deactivating=old['is_active'] and not d.get('is_active',old['is_active'])
            affected=open_tasks(c,tid,None if deactivating else keys) if old['kind']=='tasks' and (keys or deactivating) else []
            if affected and d.get('open_policy') not in ('keep','cancel'):raise HTTPException(400,'اختر إبقاء المهام المفتوحة أو إلغاءها')
            cancelled=cancel_open(c,tid,None if deactivating else keys) if affected and d.get('open_policy')=='cancel' else []
            c.execute('INSERT INTO universal_template_versions(template_id,version,body_json) VALUES(?,?,?)',(tid,version,json.dumps(body,ensure_ascii=False)))
            c.execute('UPDATE universal_templates SET name=?,version=?,is_active=? WHERE id=?',(name,version,int(d.get('is_active',old['is_active'])),tid));change_log(c,req,u,'template_version','template',tid,d);c.commit();sync_changes(c,cancelled);return {'ok':True,'version':version,'cancelled_task_ids':cancelled}
        except Exception:c.rollback();raise
        finally:c.close()

    @app.put('/api/templates/{tid}/links')
    async def link_template(tid:int,req:Request):
        u=admin(req);d=await req.json();c=connect()
        try:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM universal_templates WHERE id=?',(tid,)).fetchone():raise HTTPException(404)
            links=[]
            for kind,key,table in [('employee','employee_ids','employees'),('job','job_ids','bim_job_profiles')]:
                raw=d.get(key,[])
                if not isinstance(raw,list) or len(raw)>1000:raise HTTPException(400,'قائمة الربط غير صحيحة')
                for eid in set(raw):
                    if type(eid) is not int or not c.execute(f'SELECT 1 FROM {table} WHERE id=?',(eid,)).fetchone():raise HTTPException(400,'أحد الأسماء غير موجود')
                    links.append((tid,kind,eid))
            c.execute('DELETE FROM universal_links WHERE template_id=?',(tid,));c.executemany('INSERT INTO universal_links VALUES(?,?,?)',links)
            change_log(c,req,u,'template_links','template',tid,d);c.commit();return {'ok':True,'employees':targets(c,tid)}
        except Exception:c.rollback();raise
        finally:c.close()

    @app.put('/api/templates/{tid}/employee/{eid}')
    async def override_template(tid:int,eid:int,req:Request):
        u=admin(req);d=await req.json();allowed={'instructions','title','priority','estimated_minutes','due_minutes','completion_fields'}
        if not isinstance(d,dict) or set(d)-allowed:raise HTTPException(400,'تخصيص غير صحيح')
        c=connect()
        try:
            t=c.execute('SELECT * FROM universal_templates WHERE id=?',(tid,)).fetchone()
            if not t or eid not in targets(c,tid):raise HTTPException(404)
            if t['kind']=='tasks' and ('tasks' in body_for(c,tid) or body_for(c,tid)['mode']=='team'):raise HTTPException(400,'المهمة مشتركة؛ عدّل قالبها للجميع')
            normalized=validate_single({**body_for(c,tid),**d},t['kind'])
            if set(d)-set(normalized):raise HTTPException(400,'حقول التخصيص لا تناسب نوع القالب')
            d={k:normalized[k] for k in d}
            if not d:
                c.execute('DELETE FROM universal_overrides WHERE template_id=? AND employee_id=?',(tid,eid));change_log(c,req,u,'template_override_clear','employee',eid,{});return {'ok':True}
            c.execute('INSERT INTO universal_overrides VALUES(?,?,?) ON CONFLICT(template_id,employee_id) DO UPDATE SET body_json=excluded.body_json',(tid,eid,json.dumps(d,ensure_ascii=False)));change_log(c,req,u,'template_override','employee',eid,d);return {'ok':True}
        finally:c.close()

    @app.put('/api/templates/{tid}/employee/{eid}/items/{key}')
    async def override_item(tid:int,eid:int,key:str,req:Request):
        u=admin(req);d=await req.json();allowed={'instructions','title','priority','estimated_minutes','due_minutes','completion_fields'}
        if not isinstance(d,dict) or set(d)-allowed:raise HTTPException(400,'حقول التخصيص غير صحيحة')
        c=connect()
        try:
            c.execute('BEGIN IMMEDIATE')
            t=c.execute('SELECT * FROM universal_templates WHERE id=?',(tid,)).fetchone()
            if not t or t['kind']!='tasks' or eid not in targets(c,tid):raise HTTPException(404)
            item=next((x for x in task_items(body_for(c,tid)) if x['key']==key),None)
            if not item:raise HTTPException(404)
            if item['mode']=='team':raise HTTPException(400,'المهمة مشتركة؛ عدّل البند للجميع')
            normalized=validate_single({**item,**d},'tasks');r=c.execute('SELECT body_json FROM universal_overrides WHERE template_id=? AND employee_id=?',(tid,eid)).fetchone()
            settings=json.loads(r[0]) if r else {};settings.setdefault('items',{})
            if d:settings['items'][key]={k:normalized[k] for k in d}
            else:settings['items'].pop(key,None)
            if not settings['items']:settings.pop('items')
            if settings:c.execute('INSERT INTO universal_overrides VALUES(?,?,?) ON CONFLICT(template_id,employee_id) DO UPDATE SET body_json=excluded.body_json',(tid,eid,json.dumps(settings,ensure_ascii=False)))
            else:c.execute('DELETE FROM universal_overrides WHERE template_id=? AND employee_id=?',(tid,eid))
            change_log(c,req,u,'template_item_override','employee',eid,{'template':tid,'key':key,'values':d});c.commit();return {'ok':True}
        except Exception:c.rollback();raise
        finally:c.close()

    @app.get('/api/templates/{tid}/preview')
    def preview(tid:int,req:Request):
        admin(req);c=connect()
        try:
            t=c.execute('SELECT * FROM universal_templates WHERE id=?',(tid,)).fetchone()
            if not t:raise HTTPException(404)
            return {'template':dict(t),'body':body_for(c,tid),'employees':[{'id':eid,'name':c.execute('SELECT name FROM employees WHERE id=?',(eid,)).fetchone()[0],'body':effective(c,tid,eid)} for eid in targets(c,tid)],'open_tasks':[dict(r) for r in c.execute("SELECT t.id,t.title,t.status,mt.version,mt.customized FROM tasks t JOIN universal_task_meta mt ON mt.task_id=t.id WHERE mt.template_id=? AND t.status NOT IN ('تم','ملغاة') AND t.archived_at IS NULL",(tid,))]}
        finally:c.close()

    @app.post('/api/templates/{tid}/generate')
    async def run_template(tid:int,req:Request):
        u=admin(req);d=await req.json();key=d.get('request_key')
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',key):raise HTTPException(400,'معرف العملية غير صحيح')
        c=connect()
        try:
            c.execute('BEGIN IMMEDIATE');ids=generate(c,tid,u['user_id'],key);change_log(c,req,u,'template_generate','template',tid,{'task_ids':ids});c.commit()
        except Exception:c.rollback();raise
        finally:c.close()
        dispatch_created(ids);return {'ok':True,'task_ids':ids}

    @app.post('/api/templates/{tid}/apply-open')
    async def apply_open(tid:int,req:Request):
        u=admin(req);d=await req.json();selected=d.get('task_ids',[]);c=connect();changed=[];skipped=[]
        if not isinstance(selected,list) or len(selected)>1000:raise HTTPException(400)
        try:
            c.execute('BEGIN IMMEDIATE');t=c.execute('SELECT * FROM universal_templates WHERE id=?',(tid,)).fetchone()
            if not t or d.get('expected_version')!=t['version']:raise HTTPException(409,'تغير إصدار القالب')
            for taskid in selected:
                r=c.execute('SELECT t.*,mt.customized,mt.body_json snapshot FROM tasks t JOIN universal_task_meta mt ON mt.task_id=t.id WHERE t.id=? AND mt.template_id=?',(taskid,tid)).fetchone()
                if not r or r['customized'] or r['archived_at'] or r['status'] in ('تم','ملغاة'):skipped.append(taskid);continue
                base=body_for(c,tid);identity=json.loads(r['snapshot']).get('item_key','legacy')
                override=c.execute('SELECT body_json FROM universal_overrides WHERE template_id=? AND employee_id=?',(tid,r['employee_id'])).fetchone() if r['employee_id'] else None
                if override:
                    config=json.loads(override[0])
                    if set(config)-{'items'} or identity in config.get('items',{}):skipped.append(taskid);continue
                item=next((x for x in task_items(base) if x['key']==identity and x.get('is_active',True)),None)
                if not item:skipped.append(taskid);continue
                b=item_body(base,item)
                if (b['mode']=='team')!=bool(r['is_group']):skipped.append(taskid);continue
                c.execute('UPDATE tasks SET title=?,notes=?,priority=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(b['title'],b['instructions'],b['priority'],taskid));c.execute('UPDATE universal_task_meta SET version=?,body_json=? WHERE task_id=?',(t['version'],json.dumps(b,ensure_ascii=False),taskid));c.execute("INSERT INTO task_events(task_id,event_type,note,source) VALUES(?,'template_update',?,'dashboard')",(taskid,str(t['version'])));changed.append(taskid)
            change_log(c,req,u,'template_apply_open','template',tid,{'changed':changed,'skipped':skipped});c.commit()
            for taskid in changed:m._touch_task_sync(c,taskid);m._refresh_telegram_task_messages(c,taskid)
            return {'ok':True,'changed':changed,'skipped':skipped}
        except Exception:c.rollback();raise
        finally:c.close()

    @app.get('/workload',response_class=HTMLResponse)
    def workload_page(req:Request):
        m.need(req,'dashboard.view');return m.templates.TemplateResponse(request=req,name='workload.html',context=m.ctx(req,'عبء العمل'))

    @app.get('/api/workload')
    def workload(req:Request):
        m.need(req,'dashboard.view');c=connect()
        try:
            now=local_now(c).isoformat(timespec='seconds');rows=[]
            for e in c.execute('SELECT * FROM employees WHERE is_active=1 ORDER BY name').fetchall():
                tasks=[dict(t) for t in c.execute("SELECT t.*,mt.body_json,(SELECT COUNT(*) FROM task_participants p WHERE p.task_id=t.id) members FROM tasks t LEFT JOIN universal_task_meta mt ON mt.task_id=t.id WHERE t.archived_at IS NULL AND t.status NOT IN ('تم','ملغاة') AND (t.employee_id=? OR EXISTS(SELECT 1 FROM task_participants p WHERE p.task_id=t.id AND p.employee_id=? AND p.status<>'تم'))",(e['id'],e['id']))]
                minutes=sum(json.loads(t['body_json'] or '{}').get('estimated_minutes',0)/(t['members'] or 1) for t in tasks)
                cfg=c.execute('SELECT capacity_minutes FROM universal_staff_settings WHERE employee_id=?',(e['id'],)).fetchone()
                rows.append({'id':e['id'],'name':e['name'],'department':e['department'],'active':len(tasks),'progress':sum(t['status'] in ('بدأت','قيد التنفيذ') for t in tasks),'overdue':sum(bool(t['due_at'] and t['due_at'].replace(' ','T')<now) for t in tasks),'estimated_minutes':round(minutes,1),'unestimated':sum(not json.loads(t['body_json'] or '{}').get('estimated_minutes') for t in tasks),'capacity_minutes':cfg[0] if cfg else 480,'tasks':[{'id':t['id'],'title':t['title'],'status':t['status']} for t in tasks]})
            return {'employees':rows,'as_of':now,'note':'المدة تقديرية للأعمال المفتوحة، وليست تقييماً لأداء الموظف. مدة المهمة المشتركة موزعة بالتساوي بين المشاركين.'}
        finally:c.close()

    @app.get('/api/staff/{eid}/settings')
    def staff_settings(eid:int,req:Request):
        admin(req);c=connect()
        try:
            row=c.execute('SELECT * FROM universal_staff_settings WHERE employee_id=?',(eid,)).fetchone()
            return dict(row) if row else {'employee_id':eid,'channel':'both','capacity_minutes':480}
        finally:c.close()

    @app.put('/api/staff/{eid}/settings')
    async def set_staff(eid:int,req:Request):
        u=admin(req);d=await req.json();ch=d.get('channel','both');cap=d.get('capacity_minutes',480)
        if ch not in ('web','telegram','both') or type(cap) is not int or not 1<=cap<=1440:raise HTTPException(400,'الوسيلة أو السعة اليومية غير صحيحة')
        c=connect()
        try:
            if not c.execute('SELECT 1 FROM employees WHERE id=?',(eid,)).fetchone():raise HTTPException(404)
            if ch=='telegram' and c.execute("SELECT 1 FROM users u JOIN roles r ON r.id=u.role_id WHERE u.employee_id=? AND r.code IN ('admin','deputy') AND u.is_active=1",(eid,)).fetchone():raise HTTPException(400,'حساب الإدارة يحتاج دخول الويب')
            c.execute('INSERT INTO universal_staff_settings VALUES(?,?,?) ON CONFLICT(employee_id) DO UPDATE SET channel=excluded.channel,capacity_minutes=excluded.capacity_minutes',(eid,ch,cap));change_log(c,req,u,'staff_channel','employee',eid,d);return {'ok':True}
        finally:c.close()

    @app.get('/access-control',response_class=HTMLResponse)
    def access_page(req:Request):
        admin(req);return m.templates.TemplateResponse(request=req,name='access_control.html',context=m.ctx(req,'صلاحيات المستخدمين'))

    @app.get('/api/access-control')
    def access_list(req:Request):
        admin(req);c=connect()
        from . import access
        try:return {'ceilings':{k:sorted(access.ceiling(k)) for k in access.MATRIX},'defaults':{k:sorted(v) for k,v in access.MATRIX.items()},'permissions':[dict(r) for r in c.execute('SELECT code,name_ar FROM permissions ORDER BY id')],'users':[dict(r) for r in c.execute("SELECT u.id,u.username,u.employee_id,r.code role_code FROM users u JOIN roles r ON r.id=u.role_id WHERE u.deleted_at IS NULL")],'overrides':[dict(r) for r in c.execute('SELECT * FROM universal_user_permissions')]}
        finally:c.close()

    @app.put('/api/access-control/{uid}')
    async def access_update(uid:int,req:Request):
        actor=m.need(req,'security.manage');d=await req.json();c=connect()
        from . import access
        try:
            c.execute('BEGIN IMMEDIATE');u=c.execute('SELECT u.*,r.code role_code FROM users u JOIN roles r ON r.id=u.role_id WHERE u.id=?',(uid,)).fetchone()
            known={r[0] for r in c.execute('SELECT code FROM permissions')}
            if not u:raise HTTPException(404)
            if not isinstance(d,dict) or set(d)-known or any(type(v) is not bool for v in d.values()):raise HTTPException(400)
            # Individual switches stay inside the role's ceiling shown on /permissions.
            beyond=sorted(k for k,v in d.items() if v and k not in access.ceiling(u['role_code']))
            if beyond:raise HTTPException(400,'هذه الصلاحيات خارج حدود الدور: '+'، '.join(beyond)+' — غيّر الدور بدلاً من ذلك')
            if uid==actor['user_id'] and d.get('users.manage') is False:raise HTTPException(400,'لا يمكنك إلغاء وصولك إلى إدارة الصلاحيات')
            c.execute('DELETE FROM universal_user_permissions WHERE user_id=?',(uid,));c.executemany('INSERT INTO universal_user_permissions VALUES(?,?,?)',[(uid,k,int(v)) for k,v in d.items()]);change_log(c,req,actor,'user_permissions','user',uid,d);c.commit();return {'ok':True}
        except Exception:c.rollback();raise
        finally:c.close()

    @app.get('/handover',response_class=HTMLResponse)
    def handover_page(req:Request):
        admin(req);return m.templates.TemplateResponse(request=req,name='handover.html',context=m.ctx(req,'تسليم الأعمال'))

    @app.post('/api/handover/preview')
    async def handover_preview(req:Request):
        admin(req);d=await req.json();eid=d.get('from_employee');c=connect()
        try:return {'tasks':[dict(r) for r in c.execute("SELECT DISTINCT t.id,t.title,t.status,t.is_group FROM tasks t LEFT JOIN task_participants p ON p.task_id=t.id WHERE t.archived_at IS NULL AND t.status NOT IN ('تم','ملغاة') AND (t.employee_id=? OR (p.employee_id=? AND p.status<>'تم'))",(eid,eid))]}
        finally:c.close()

    @app.post('/api/handover')
    async def handover(req:Request):
        actor=admin(req);d=await req.json();old=d.get('from_employee');new=d.get('to_employee');ids=d.get('task_ids',[]);c=connect()
        if old==new or not isinstance(ids,list) or len(ids)>1000:raise HTTPException(400,'راجع الموظفين والمهام')
        try:
            c.execute('BEGIN IMMEDIATE')
            for eid in (old,new):
                if not c.execute('SELECT 1 FROM employees WHERE id=? AND is_active=1',(eid,)).fetchone():raise HTTPException(400,'الموظف غير موجود أو موقوف')
            changed=[]
            for tid in set(ids):
                t=c.execute("SELECT * FROM tasks WHERE id=? AND archived_at IS NULL AND status NOT IN ('تم','ملغاة')",(tid,)).fetchone()
                if not t:raise HTTPException(409,'إحدى المهام انتهت أو لم تعد متاحة؛ أعد المعاينة')
                if t['is_group']:
                    if not c.execute("SELECT 1 FROM task_participants WHERE task_id=? AND employee_id=? AND status<>'تم'",(tid,old)).fetchone():raise HTTPException(409,'مشاركة الموظف منتهية أو غير متاحة')
                    if c.execute('SELECT 1 FROM task_participants WHERE task_id=? AND employee_id=?',(tid,new)).fetchone():raise HTTPException(409,'الموظف البديل مشارك بالفعل في المهمة؛ اختر موظفاً آخر')
                    c.execute('DELETE FROM task_participants WHERE task_id=? AND employee_id=?',(tid,old));c.execute('INSERT OR IGNORE INTO task_participants(task_id,employee_id) VALUES(?,?)',(tid,new))
                else:
                    if t['employee_id']!=old:raise HTTPException(409,'تغير مسؤول المهمة')
                    c.execute('UPDATE tasks SET employee_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(new,tid))
                c.execute('UPDATE universal_task_meta SET customized=1 WHERE task_id=?',(tid,));c.execute("INSERT INTO task_events(task_id,employee_id,event_type,note,source) VALUES(?,?,'handover',?,'dashboard')",(tid,new,f'{old} → {new}'));changed.append(tid)
            if d.get('deactivate'):
                if c.execute('SELECT 1 FROM users WHERE id=? AND employee_id=?',(actor['user_id'],old)).fetchone():raise HTTPException(400,'لا توقف حسابك الحالي')
                c.execute('UPDATE employees SET is_active=0 WHERE id=?',(old,));c.execute('UPDATE users SET is_active=0 WHERE employee_id=?',(old,));c.execute('UPDATE recurring_tasks SET is_active=0 WHERE employee_id=?',(old,))
            change_log(c,req,actor,'handover','employee',old,d);c.commit()
            for tid in changed:
                m._touch_task_sync(c,tid);m._notify_employee_id(c,old,'نُقلت المهمة رقم '+str(tid)+' إلى موظف آخر',tid);m._notify_employee_id(c,new,'أُسندت إليك المهمة رقم '+str(tid),tid,reply_markup=m._telegram_task_keyboard(tid))
            return {'ok':True,'task_ids':changed}
        except Exception:c.rollback();raise
        finally:c.close()

    @app.get('/api/tasks/{tid}/work-profile')
    def task_work_profile(tid:int,req:Request):
        u=m.need(req,'tasks.view');c=connect()
        try:
            task=m._task_accessible(c,u,tid)
            if not task:raise HTTPException(404)
            meta=c.execute('SELECT body_json FROM universal_task_meta WHERE task_id=?',(tid,)).fetchone()
            b=json.loads(meta[0]) if meta else {};p=m.profile(c,u['employee_id'] or task['employee_id']);text='\n\n'.join(x for x in [p['instructions'],b.get('instructions','')] if x);fields=b.get('completion_fields',p['config']['completion_fields'])
            return {'instructions':text,'completion_fields':fields,'estimated_minutes':b.get('estimated_minutes',0)}
        finally:c.close()

def telegram_team_status(c,task,e,value):
    from . import main as m
    c.execute('BEGIN IMMEDIATE')
    try:
        live=m._tg_task_for_employee(c,task['id'],e['id'])
        p=c.execute('SELECT status FROM task_participants WHERE task_id=? AND employee_id=?',(task['id'],e['id'])).fetchone()
        if not live or not p or live['status'] in ('تم','ملغاة') or p[0] in ('تم',value) or (p[0]=='قيد التنفيذ' and value=='بدأت'):c.rollback();return False
        c.execute('UPDATE task_participants SET status=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND employee_id=?',(value,task['id'],e['id']))
        c.execute("INSERT INTO task_events(task_id,employee_id,event_type,old_status,new_status,source) VALUES(?,?,'participant_status',?,?,'telegram')",(task['id'],e['id'],p[0],value))
        if value=='تم':m.record_completion(c,task['id'],e['id'],{},'telegram')
        incomplete=c.execute("SELECT COUNT(*) FROM task_participants WHERE task_id=? AND status<>'تم'",(task['id'],)).fetchone()[0]
        status='تم' if incomplete==0 else 'قيد التنفيذ'
        c.execute("UPDATE tasks SET status=?,started_at=COALESCE(started_at,CURRENT_TIMESTAMP),completed_at=CASE WHEN ?='تم' THEN COALESCE(completed_at,CURRENT_TIMESTAMP) ELSE completed_at END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,status,task['id']))
        sync=m._touch_task_sync(c,task['id']);c.commit();m._try_instant_sheet_push(c,sync);return True
    except Exception:c.rollback();raise
