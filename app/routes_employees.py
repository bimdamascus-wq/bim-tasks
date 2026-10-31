"""Employees, job profiles and the per-employee work profile.
Split out of app/main.py (package code_split) with no change in behaviour; main.py imports this module."""
from __future__ import annotations
import json
import sqlite3
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from . import access
from .db import connect
from .work_settings import apply_job_tasks, profile, save_config, validate_config
from .main import (
    app, audit, ctx, need, perms, session, templates
)

# ---------- employees ----------
@app.get('/api/employees')
def api_employees(req: Request, all: int=0):
    u=need(req,'employees.view'); c=connect(); wh="WHERE e.id NOT IN(SELECT record_id FROM record_lifecycle WHERE entity='employees' AND deleted_at IS NOT NULL)"+('' if all else ' AND e.is_active=1')
    scope=access.employee_scope(c,u)
    rows=[dict(x) for x in c.execute(f'''SELECT e.*,(SELECT COUNT(*) FROM tasks t WHERE t.employee_id=e.id AND t.status NOT IN('تم','ملغاة')) open_tasks
      FROM employees e {wh} ORDER BY sort_order,name''')]; c.close()
    return [r for r in rows if scope is None or r['id'] in scope]

@app.get('/api/task-options/employees')
def api_task_employee_options(req:Request):
    u=need(req,'tasks.view'); c=connect()
    try:
        if u['role_code'] == 'employee' and not u['employee_id']: raise HTTPException(403, 'الحساب غير مربوط بموظف')
        scope=access.employee_scope(c,u)
        rows=[dict(x) for x in c.execute('SELECT id,name,department FROM employees WHERE is_active=1 ORDER BY sort_order,name')]
        return [r for r in rows if scope is None or r['id'] in scope]
    finally: c.close()

@app.post('/api/employees')
async def add_employee(req: Request):
    u=need(req,'employees.edit'); d=await req.json(); name=str(d.get('name','')).strip()
    if not name: raise HTTPException(400,'الاسم مطلوب')
    c=connect()
    try:
        c.execute('''INSERT INTO employees(name,department,whatsapp,telegram_chat_id,telegram_enabled,whatsapp_enabled,shift_type,shift_start,shift_end,work_days) VALUES(?,?,?,?,?,?,?,?,?,?)''',
          (name,d.get('department'),d.get('whatsapp'),d.get('telegram_chat_id'),1 if d.get('telegram_enabled',True) else 0,1 if d.get('whatsapp_enabled',True) else 0,d.get('shift_type') or 'بدون',d.get('shift_start') or None,d.get('shift_end') or None,d.get('work_days') or '0,1,2,3,4,5,6'))
        eid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; audit(c,req,u,'employee_create','employee',eid,after=d)
    except Exception as e:
        c.close(); raise HTTPException(409, f'تعذر إضافة الموظف: {e}')
    c.close(); return {'ok':True,'id':eid}

@app.patch('/api/employees/{eid}')
async def edit_employee(eid:int, req:Request):
    u=need(req,'employees.edit'); d=await req.json(); c=connect(); old=c.execute('SELECT * FROM employees WHERE id=?',(eid,)).fetchone()
    if not old: c.close(); raise HTTPException(404,'الموظف غير موجود')
    if 'name' in d:
        d['name']=str(d.get('name') or '').strip()
        if not d['name']:
            c.close(); raise HTTPException(400,'الاسم مطلوب')
    allowed=['name','department','whatsapp','telegram_enabled','whatsapp_enabled','is_active','sort_order','shift_type','shift_start','shift_end','work_days']; sets=[]; vals=[]
    for k in allowed:
        if k in d:
            sets.append(k+'=?')
            if k in ('telegram_enabled','whatsapp_enabled','is_active'):
                vals.append(1 if bool(d[k]) else 0)
            else:
                vals.append(d[k])
    if sets:
        sets.append('updated_at=CURRENT_TIMESTAMP'); vals.append(eid); c.execute('UPDATE employees SET '+','.join(sets)+' WHERE id=?',vals)
    audit(c,req,u,'employee_update','employee',eid,before=dict(old),after=d); c.close(); return {'ok':True}


@app.get('/job-profiles', response_class=HTMLResponse)
def bim_job_profiles_page(req: Request):
    need(req,'users.manage')
    return templates.TemplateResponse(request=req,name='job_profiles.html',context=ctx(req,'الوظائف والتعليمات'))

@app.get('/api/bim-job-profiles')
def bim_job_profiles_list(req:Request):
    u=need(req,'users.manage'); c=connect()
    try:
        jobs=[dict(x) for x in c.execute("SELECT * FROM bim_job_profiles WHERE id NOT IN(SELECT record_id FROM record_lifecycle WHERE entity='jobs' AND deleted_at IS NOT NULL) ORDER BY name")]
        for j in jobs:
            r=c.execute("SELECT config_json FROM bim_work_settings WHERE scope='job' AND owner_id=?",(j['id'],)).fetchone(); j['config']=json.loads(r[0]) if r else {}
        links=[dict(x) for x in c.execute('SELECT employee_id,job_id FROM bim_employee_jobs ORDER BY employee_id,job_id')]
        prefs=[dict(x) for x in c.execute('SELECT * FROM bim_employee_prefs')]
        return {'jobs':jobs,'links':links,'preferences':prefs}
    finally: c.close()

@app.post('/api/bim-job-profiles')
async def bim_job_profiles_create(req:Request):
    u=need(req,'users.manage'); d=await req.json()
    name=str(d.get('name') or '').strip(); instruction=str(d.get('instructions') or '').strip()
    mode=d.get('display_mode','on_demand')
    if not name or len(name)>100 or len(instruction)>12000 or mode not in ('auto','on_demand','once'):
        raise HTTPException(400,'بيانات الوظيفة غير صحيحة')
    validate_config(d.get('config',{}))
    c=connect()
    try:
        c.execute('INSERT INTO bim_job_profiles(name,instructions,display_mode) VALUES(?,?,?)',(name,instruction,mode))
        jid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        save_config(c,'job',jid,d.get('config',{}))
        audit(c,req,u,'job_create','job',jid,after={'name':name,'display_mode':mode})
        return {'ok':True,'id':jid}
    except sqlite3.IntegrityError: raise HTTPException(409,'المسمى الوظيفي موجود')
    finally: c.close()

@app.patch('/api/bim-job-profiles/{jid}')
async def bim_job_profiles_update(jid:int,req:Request):
    u=need(req,'users.manage'); d=await req.json(); c=connect()
    try:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('SELECT * FROM bim_job_profiles WHERE id=?',(jid,)).fetchone()
        if not old: raise HTTPException(404,'الوظيفة غير موجودة')
        if 'config' in d: save_config(c,'job',jid,d['config'])
        fields=[]; vals=[]
        for k in ('name','instructions','display_mode','is_active'):
            if k not in d: continue
            v=d[k]
            if k=='name': v=str(v or '').strip()
            if k=='instructions': v=str(v or '').strip()
            if k=='display_mode' and v not in ('auto','on_demand','once'): raise HTTPException(400,'نمط العرض غير صحيح')
            if k=='name' and (not v or len(v)>100): raise HTTPException(400,'الاسم غير صحيح')
            if k=='instructions' and len(v)>12000: raise HTTPException(400,'التعليمات طويلة جداً')
            if k=='is_active': v=1 if bool(v) else 0
            fields.append(k+'=?'); vals.append(v)
        if fields:
            c.execute('UPDATE bim_job_profiles SET '+','.join(fields)+' WHERE id=?',(*vals,jid))
            audit(c,req,u,'job_update','job',jid,before=dict(old),after={k:d[k] for k in d if k in ('name','instructions','display_mode','is_active')})
        c.commit();return {'ok':True}
    except sqlite3.IntegrityError:
        c.rollback();raise HTTPException(409,'المسمى الوظيفي موجود')
    except Exception:
        c.rollback();raise
    finally: c.close()

@app.get('/api/bim-employee-profile/{eid}')
def bim_employee_profile(req:Request,eid:int):
    u=session(req)
    if not u: raise HTTPException(401,'يجب تسجيل الدخول')
    if u['role_code']=='employee':
        if not u['employee_id'] or int(u['employee_id'])!=eid: raise HTTPException(403,'غير مسموح')
    elif 'users.manage' not in perms(u['user_id']): raise HTTPException(403,'غير مسموح')
    c=connect()
    try:
        if not c.execute('SELECT 1 FROM employees WHERE id=?',(eid,)).fetchone(): raise HTTPException(404,'الموظف غير موجود')
        jobs=[dict(x) for x in c.execute('SELECT p.* FROM bim_job_profiles p JOIN bim_employee_jobs j ON j.job_id=p.id WHERE j.employee_id=? AND p.is_active=1 ORDER BY p.name',(eid,))]
        pref=c.execute('SELECT display_mode FROM bim_employee_prefs WHERE employee_id=?',(eid,)).fetchone()
        result=profile(c,eid)
        result.update(jobs=jobs,display_mode_override=pref['display_mode'] if pref else None,display_mode=pref['display_mode'] if pref and pref['display_mode'] else (jobs[0]['display_mode'] if jobs else 'on_demand'))
        return result
    finally: c.close()

@app.put('/api/bim-employee-profile/{eid}')
async def bim_employee_profile_update(req:Request,eid:int):
    u=need(req,'users.manage');d=await req.json(); raw=d.get('job_ids',[])
    if not isinstance(raw,list) or len(raw)>30: raise HTTPException(400,'الوظائف غير صحيحة')
    try: ids=sorted(set(int(x) for x in raw))
    except (TypeError,ValueError): raise HTTPException(400,'الوظائف غير صحيحة')
    mode=d.get('display_mode_override')
    if mode not in (None,'auto','on_demand','once'): raise HTTPException(400,'طريقة العرض غير صحيحة')
    c=connect()
    try:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 FROM employees WHERE id=?',(eid,)).fetchone(): raise HTTPException(404,'الموظف غير موجود')
        if ids and c.execute('SELECT COUNT(*) FROM bim_job_profiles WHERE id IN ('+','.join('?'*len(ids))+')',ids).fetchone()[0]!=len(ids):
            raise HTTPException(400,'إحدى الوظائف غير متاحة')
        c.execute('DELETE FROM bim_employee_jobs WHERE employee_id=?',(eid,))
        c.executemany('INSERT INTO bim_employee_jobs(employee_id,job_id) VALUES(?,?)',[(eid,j) for j in ids])
        c.execute('INSERT INTO bim_employee_prefs(employee_id,display_mode) VALUES(?,?) ON CONFLICT(employee_id) DO UPDATE SET display_mode=excluded.display_mode,updated_at=CURRENT_TIMESTAMP',(eid,mode))
        config=d.get('config_override')
        if 'instructions_override' in d:
            config=dict(config or profile(c,eid)['config_override']); config['instructions']=d['instructions_override']
        if config is not None: save_config(c,'employee',eid,config)
        apply_job_tasks(c,eid,u['user_id'])
        audit(c,req,u,'employee_profile_update','employee',eid,after={'job_ids':ids,'display_mode_override':mode})
        c.execute('COMMIT');return {'ok':True}
    except Exception:
        if c.in_transaction: c.execute('ROLLBACK')
        raise
    finally:c.close()

@app.post('/api/bim-employee-profile/{eid}/acknowledge')
def acknowledge_instructions(eid:int,req:Request):
    p=bim_employee_profile(req,eid)
    c=connect()
    try:
        c.execute("INSERT INTO bim_instruction_reads(employee_id,signature) VALUES(?,?) ON CONFLICT(employee_id) DO UPDATE SET signature=excluded.signature,read_at=CURRENT_TIMESTAMP",(eid,p['instructions_signature']))
        return {'ok':True}
    finally:c.close()
