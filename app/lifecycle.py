"""Reversible administrative deletion. Operational history is never removed."""
import re
from fastapi import Request,HTTPException
from fastapi.responses import HTMLResponse,JSONResponse
from .db import connect
from .template_items import open_tasks,cancel_open,sync_changes
# Table identifiers are an internal allowlist, never user SQL.
CATALOG={
 'templates':('universal_templates','name','قوالب العمل','users.manage','/api/templates'),
 'jobs':('bim_job_profiles','name','المسميات الوظيفية','users.manage','/api/bim-job-profiles'),
 'employees':('employees','name','الموظفون','employees.edit','/api/employees'),
 'users':('users','username','المستخدمون','users.manage','/api/users'),
 'recurring':('recurring_tasks','title','المهام الثابتة السابقة','recurring.edit','/api/recurring'),
 'shift_messages':('shift_messages','message_text','رسائل الدوام','employees.edit','/api/shift-messages'),
 'shift_exceptions':('shift_exceptions','note','الإجازات والدوام الاستثنائي','employees.edit','/api/shift-exceptions'),
 'telegram_groups':('telegram_groups','name','مجموعات Telegram','employees.edit','/api/telegram-groups'),
 'group_reminders':('group_reminders','title','تذكيرات المجموعات','employees.edit','/api/group-reminders'),
 'tasks':('tasks','title','المهام المسندة','tasks.delete','/api/tasks'),
}

def deleted(c,entity,rid):
    return bool(c.execute('SELECT 1 FROM record_lifecycle WHERE entity=? AND record_id=? AND deleted_at IS NOT NULL',(entity,rid)).fetchone())

def change(req,entity,rid,action,data=None):
    from . import main as m
    if entity not in CATALOG or action not in ('disable','enable','delete','restore'):raise HTTPException(400,'إجراء غير صحيح')
    table,label,_,permission,_=CATALOG[entity];actor=m.need(req,permission);data=data or {};c=connect();cancelled=[]
    try:
        c.execute('BEGIN IMMEDIATE');r=c.execute(f'SELECT * FROM {table} WHERE id=?',(rid,)).fetchone()
        if not r:raise HTTPException(404,'السجل غير موجود')
        is_deleted=deleted(c,entity,rid) or (entity=='users' and bool(r['deleted_at']))
        if is_deleted and action not in ('restore','delete'):raise HTTPException(409,'استعد السجل المحذوف أولاً')
        off=action in ('disable','delete','restore')
        if entity=='users' and action in ('disable','delete'):
            if rid==actor['user_id']:raise HTTPException(409,'لا يمكنك تعطيل أو حذف حسابك الحالي')
            role=c.execute('SELECT code FROM roles WHERE id=?',(r['role_id'],)).fetchone()[0]
            if role=='admin' and not c.execute("SELECT 1 FROM users u JOIN roles r ON r.id=u.role_id WHERE r.code='admin' AND u.is_active=1 AND u.deleted_at IS NULL AND u.id<>?",(rid,)).fetchone():raise HTTPException(409,'لا يمكن تعطيل أو حذف آخر مدير')
        if entity=='employees' and action in ('disable','delete'):
            # Prevent losing administration through an employee/account association.
            if c.execute("SELECT 1 FROM users u JOIN roles r ON r.id=u.role_id WHERE u.employee_id=? AND u.is_active=1 AND u.deleted_at IS NULL AND (u.id=? OR r.code='admin')",(rid,actor['user_id'])).fetchone():raise HTTPException(409,'انقل ارتباط حساب المدير بهذا الموظف قبل تعطيله')
        if entity=='templates':
            if data.get('expected_version')!=r['version']:raise HTTPException(409,'تغير القالب؛ أعد تحميله')
            if action in ('disable','delete'):
                affected=open_tasks(c,rid)
                if affected and data.get('open_policy') not in ('keep','cancel'):raise HTTPException(400,'اختر إبقاء المهام المفتوحة أو إلغاءها')
                if data.get('open_policy')=='cancel':cancelled=cancel_open(c,rid)
            # A lifecycle transition is also a versioned template change.
            version=r['version']+1
            c.execute('INSERT INTO universal_template_versions(template_id,version,body_json) SELECT template_id,?,body_json FROM universal_template_versions WHERE template_id=? AND version=?',(version,rid,r['version']))
            c.execute('UPDATE universal_templates SET version=? WHERE id=?',(version,rid))
        if entity=='tasks':
            c.execute('UPDATE tasks SET archived_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END,updated_at=CURRENT_TIMESTAMP WHERE id=?',(int(off),rid))
            if off:c.execute('UPDATE task_reminders SET is_active=0 WHERE task_id=?',(rid,))
            c.execute("INSERT INTO task_events(task_id,event_type,note,source) VALUES(?,'lifecycle',?,'dashboard')",(rid,action))
        else:c.execute(f'UPDATE {table} SET is_active=? WHERE id=?',(0 if off else 1,rid))
        if entity=='users':
            if action=='delete':c.execute('UPDATE users SET deleted_at=CURRENT_TIMESTAMP WHERE id=?',(rid,))
            elif action=='restore':c.execute('UPDATE users SET deleted_at=NULL WHERE id=?',(rid,))
            if off:c.execute('UPDATE login_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND revoked_at IS NULL',(rid,))
        if entity=='employees' and off:
            c.execute('UPDATE login_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id IN(SELECT id FROM users WHERE employee_id=?) AND revoked_at IS NULL',(rid,))
        if action=='delete':c.execute('INSERT INTO record_lifecycle(entity,record_id,deleted_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(entity,record_id) DO UPDATE SET deleted_at=excluded.deleted_at',(entity,rid))
        elif action=='restore':c.execute('DELETE FROM record_lifecycle WHERE entity=? AND record_id=?',(entity,rid))
        m.audit(c,req,actor,'record_'+action,entity,rid,after={'open_policy':data.get('open_policy'),'cancelled_task_ids':cancelled});c.commit()
        sync_changes(c,cancelled+([rid] if entity=='tasks' else []))
        return {'ok':True,'action':action,'cancelled_task_ids':cancelled,'retained_history':True}
    except Exception:c.rollback();raise
    finally:c.close()

def register(app,m):
    @app.get('/record-management',response_class=HTMLResponse)
    def page(req:Request):
        m.need(req,'users.manage');return m.templates.TemplateResponse(request=req,name='record_management.html',context=m.ctx(req,'إدارة السجلات والحذف'))

    @app.get('/api/records')
    def records(req:Request,entity:str='jobs',show_deleted:bool=False,q:str='',offset:int=0):
        m.need(req,'users.manage')
        if entity not in CATALOG:raise HTTPException(400)
        table,label,title,_,_=CATALOG[entity];c=connect()
        try:
            active='CASE WHEN t.archived_at IS NULL THEN 1 ELSE 0 END' if entity=='tasks' else 't.is_active'
            extra=' OR t.deleted_at IS NOT NULL' if entity=='users' else ''
            where='' if show_deleted else f' AND NOT(l.deleted_at IS NOT NULL{extra})'
            rows=[dict(r) for r in c.execute(f"SELECT t.id,COALESCE(t.{label},'') name,{active} is_active,l.deleted_at FROM {table} t LEFT JOIN record_lifecycle l ON l.entity=? AND l.record_id=t.id WHERE COALESCE(t.{label},'') LIKE ? {where} ORDER BY t.id DESC LIMIT 201 OFFSET ?",(entity,'%'+q[:100]+'%',max(0,offset)))]
            # Backfill the UI indicator for user records archived by earlier releases.
            if entity=='users':
                for r in rows:
                    if not r['deleted_at']:r['deleted_at']=c.execute('SELECT deleted_at FROM users WHERE id=?',(r['id'],)).fetchone()[0]
            return {'catalog':[{'key':k,'name':v[2]} for k,v in CATALOG.items()],'rows':rows[:200],'has_more':len(rows)>200,'entity':entity}
        finally:c.close()

    @app.post('/api/records/{entity}/{rid}/{action}')
    async def action(entity:str,rid:int,action:str,req:Request):
        m.need(req,'users.manage');d=await req.json();return change(req,entity,rid,action,d)

    @app.delete('/api/templates/{tid}')
    async def delete_template(tid:int,req:Request):return change(req,'templates',tid,'delete',await req.json())

    @app.delete('/api/bim-job-profiles/{jid}')
    def delete_job(jid:int,req:Request):return change(req,'jobs',jid,'delete')

    @app.delete('/api/employees/{eid}')
    def delete_employee(eid:int,req:Request):return change(req,'employees',eid,'delete')

    @app.middleware('http')
    async def protect_deleted(req,call_next):
        if req.method in ('PATCH','PUT','POST'):
            for entity,(_,_,_,_,prefix) in CATALOG.items():
                match=re.fullmatch(re.escape(prefix)+r'/(\d+)(?:/.*)?',req.url.path)
                if match:
                    c=connect()
                    try:blocked=deleted(c,entity,int(match[1]))
                    finally:c.close()
                    if blocked:return JSONResponse({'detail':'السجل محذوف؛ استعده من إدارة السجلات قبل تعديله'},status_code=409)
                    break
        return await call_next(req)
