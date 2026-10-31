"""Job defaults and explicit employee overrides; no administrative role grants."""
import hashlib
import json
import re
from fastapi import HTTPException

ACTIONS=('start','progress','complete','comment','attach')
NOTICES=('tasks','reminders','shift_start','shift_end')
DEFAULT={'instructions':'','permissions':{k:True for k in ACTIONS},'notifications':{k:True for k in NOTICES},'completion_fields':[],'task_templates':[]}

def validate_config(raw):
    if not isinstance(raw,dict) or set(raw)-set(DEFAULT): raise HTTPException(400,'إعدادات العمل غير صحيحة')
    out={}
    if 'instructions' in raw:
        if not isinstance(raw['instructions'],str) or len(raw['instructions'])>24000: raise HTTPException(400,'التعليمات طويلة أو غير صحيحة')
        out['instructions']=raw['instructions']
    for key,keys in [('permissions',ACTIONS),('notifications',NOTICES)]:
        if key in raw:
            v=raw[key]
            if not isinstance(v,dict) or set(v)-set(keys) or any(type(x) is not bool for x in v.values()):raise HTTPException(400,'خيارات الصلاحيات أو التنبيهات غير صحيحة')
            out[key]=v
    if 'completion_fields' in raw:
        rows=raw['completion_fields']
        if not isinstance(rows,list) or len(rows)>12 or any(not isinstance(x,str) or not x.strip() or len(x)>100 for x in rows):raise HTTPException(400,'حقول الإنجاز: حتى 12 حقلاً، كل حقل حتى 100 حرف')
        out['completion_fields']=list(dict.fromkeys(x.strip() for x in rows))
    if 'task_templates' in raw:
        rows=raw['task_templates']
        if not isinstance(rows,list) or len(rows)>50: raise HTTPException(400,'قائمة المهام الثابتة غير صحيحة')
        out['task_templates']=[];seen=set()
        for idx,r in enumerate(rows):
            if not isinstance(r,dict):raise HTTPException(400,'مهمة ثابتة غير صحيحة')
            d=validate_recurring(r)
            d['key']=str(r.get('key') or 'task-'+str(idx+1))[:80]
            if d['key'] in seen:raise HTTPException(400,'معرف المهمة الثابتة مكرر')
            seen.add(d['key']);out['task_templates'].append(d)
    return out

def validate_recurring(r):
    title=str(r.get('title') or '').strip();typ=r.get('recurrence_type','يومي');tm=r.get('run_time','09:00')
    if not title or len(title)>500 or typ not in ('يومي','أسبوعي','شهري') or not isinstance(tm,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',tm):raise HTTPException(400,'راجع عنوان المهمة وتكرارها ووقت تنفيذها')
    try:wd=int(r.get('week_day') if r.get('week_day') not in ('',None) else 0);md=int(r.get('month_day') or 1)
    except (ValueError,TypeError):raise HTTPException(400,'اليوم غير صحيح')
    if not 0<=wd<=6 or not 1<=md<=31:raise HTTPException(400,'اليوم خارج المجال')
    priority=r.get('priority','عادي')
    if priority not in ('عادي','عالية','عاجلة','منخفضة'):raise HTTPException(400,'الأولوية غير صحيحة')
    return dict(title=title,notes=str(r.get('notes') or '')[:12000],recurrence_type=typ,run_time=tm,week_day=wd,month_day=md,priority=priority,workdays_only=bool(r.get('workdays_only',True)))

def save_config(c,scope,owner,raw):
    cfg=validate_config(raw)
    c.execute('INSERT INTO bim_work_settings(scope,owner_id,config_json) VALUES(?,?,?) ON CONFLICT(scope,owner_id) DO UPDATE SET config_json=excluded.config_json',(scope,owner,json.dumps(cfg,ensure_ascii=False)))

def read_config(c,scope,owner):
    r=c.execute('SELECT config_json FROM bim_work_settings WHERE scope=? AND owner_id=?',(scope,owner)).fetchone()
    return json.loads(r[0]) if r else {}

def profile(c,eid):
    cfg=json.loads(json.dumps(DEFAULT));instructions=[];fields=[];templates=[]
    jobs=c.execute('SELECT p.* FROM bim_job_profiles p JOIN bim_employee_jobs j ON j.job_id=p.id WHERE j.employee_id=? AND p.is_active=1 ORDER BY p.name',(eid,)).fetchall()
    for job in jobs:
        j=read_config(c,'job',job['id'])
        instructions.append(job['name']+'\n'+j.get('instructions',job['instructions']))
        for key in ('permissions','notifications'):
            for k,v in j.get(key,{}).items():cfg[key][k]=cfg[key][k] and v
        fields.extend(j.get('completion_fields',[]));templates.extend(j.get('task_templates',[]))
    cfg.update(instructions='\n\n'.join(instructions),completion_fields=list(dict.fromkeys(fields)),task_templates=templates)
    override=read_config(c,'employee',eid)
    for k,v in override.items():
        if k in ('permissions','notifications'):cfg[k].update(v)
        else:cfg[k]=v
    from .universal import instructions
    cfg['instructions']='\n\n'.join(x for x in [cfg['instructions'],instructions(c,eid)] if x)
    sig=hashlib.sha256(cfg['instructions'].encode()).hexdigest()
    seen=c.execute('SELECT signature FROM bim_instruction_reads WHERE employee_id=?',(eid,)).fetchone()
    return dict(config=cfg,config_override=override,instructions=cfg['instructions'],instructions_signature=sig,instructions_read=bool(seen and seen[0]==sig))

def permits(c,eid,action):
    return bool(eid and profile(c,eid)['config']['permissions'].get(action,False))

def notices(c,eid,kind):
    from .universal import channel
    if channel(c,eid)=='web':return False
    return bool(eid and profile(c,eid)['config']['notifications'].get(kind,True))

def apply_job_tasks(c,eid,uid):
    # Snapshot once: an employee's edits/deletions are never silently overwritten.
    jobs=c.execute('SELECT job_id FROM bim_employee_jobs WHERE employee_id=?',(eid,)).fetchall()
    for job in jobs:
        jid=job[0]
        for t in read_config(c,'job',jid).get('task_templates',[]):
            if c.execute('SELECT 1 FROM bim_job_recurring WHERE employee_id=? AND job_id=? AND template_key=?',(eid,jid,t['key'])).fetchone():continue
            cur=c.execute('INSERT INTO recurring_tasks(employee_id,title,notes,priority,recurrence_type,week_day,month_day,run_time,created_by) VALUES(?,?,?,?,?,?,?,?,?)',(eid,t['title'],t['notes'],t['priority'],t['recurrence_type'],t['week_day'],t['month_day'],t['run_time'],uid))
            c.execute('INSERT INTO bim_job_recurring VALUES(?,?,?,?)',(eid,jid,t['key'],cur.lastrowid))
            c.execute('INSERT INTO bim_recurring_rules VALUES(?,?)',(cur.lastrowid,int(t['workdays_only'])))

def record_completion(c,tid,eid,values,source):
    if not isinstance(values,dict) or len(json.dumps(values,ensure_ascii=False))>16000:raise HTTPException(400,'بيانات الإنجاز غير صحيحة')
    from .universal import task_form
    fields=task_form(c,tid,eid)
    if fields is None:fields=profile(c,eid)['config']['completion_fields'] if eid else []
    if set(values)-set(fields) or any(not isinstance(v,(str,int,float,bool,type(None))) for v in values.values()):raise HTTPException(400,'حقول الإنجاز غير معروفة')
    c.execute('INSERT INTO bim_completion_entries(task_id,employee_id,form_json,values_json,source) VALUES(?,?,?,?,?)',(tid,eid,json.dumps(fields,ensure_ascii=False),json.dumps(values,ensure_ascii=False),source))
