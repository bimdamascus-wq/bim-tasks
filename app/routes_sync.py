"""Two-way Google Sheet synchronisation endpoints.
Split out of app/main.py (package code_split) with no change in behaviour; main.py imports this module."""
from __future__ import annotations
import json
import time
import urllib.error
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from .db import connect
from .main import (
    _record_sync_event, _save_reminder, _set_setting, _setting, _sync_auth, _sync_secret, _task_sync_payload,
    _try_instant_sheet_push, app, audit, ctx, need, templates
)

@app.get('/sync', response_class=HTMLResponse)
def sync_page(req:Request):
    need(req,'dashboard.view')
    return templates.TemplateResponse(request=req,name='sync.html',context=ctx(req,'مركز المزامنة'))

@app.get('/api/sync/status')
def sync_status(req:Request):
    need(req,'dashboard.view'); c=connect()
    last_event=c.execute('SELECT COALESCE(MAX(id),0) n FROM sync_events').fetchone()['n']
    open_conflicts=c.execute("SELECT COUNT(*) n FROM sync_conflicts WHERE status='open'").fetchone()['n']
    dirty=c.execute('SELECT COUNT(*) n FROM tasks WHERE COALESCE(sync_dirty,1)=1').fetchone()['n']
    data={'ok':True,'last_event_id':last_event,'open_conflicts':open_conflicts,'dirty_tasks':dirty,
          'webapp_url':_setting(c,'sync.webapp_url',''),'spreadsheet_id':'1LUhf7uRnyn7qRm5Jjd0ATGbXAB45QvjH4iOaUvpvDDY',
          'sync_secret_configured':bool(_sync_secret()),
          'instant_push_ready':bool(_setting(c,'sync.webapp_url','').strip() and _sync_secret())}
    c.close(); return data

@app.post('/api/sync/settings')
async def sync_settings(req:Request):
    u=need(req,'security.manage'); d=await req.json(); c=connect()
    if 'webapp_url' in d:
        url=str(d.get('webapp_url') or '').strip()
        if url and not (url.startswith('https://script.google.com/') or url.startswith('https://script.googleusercontent.com/')):
            c.close(); raise HTTPException(400,'رابط Apps Script غير صحيح')
        old=_setting(c,'sync.webapp_url','')
        _set_setting(c,'sync.webapp_url',url)
        audit(c,req,u,'sync_settings','settings','sync.webapp_url',before={'webapp_url':old},after={'webapp_url':url})
    c.close(); return {'ok':True}

@app.get('/api/sync/test-webapp')
def sync_test_webapp(req:Request):
    need(req,'dashboard.view'); c=connect()
    url=_setting(c,'sync.webapp_url','').strip()
    secret=_sync_secret()
    c.close()
    if not url:
        return {'ok':False,'compatible':False,'stage':'config','message':'رابط Apps Script غير محفوظ'}
    if not secret:
        return {'ok':False,'compatible':False,'stage':'config','message':'BIM_SYNC_SECRET غير مضبوط على السيرفر'}
    body=json.dumps({'action':'sync_probe','secret':secret,'source':'bim_tasks','version':'4.0.2'},ensure_ascii=False).encode('utf-8')
    try:
        r=urllib.request.Request(url,data=body,headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(r,timeout=8.0) as resp:
            status=getattr(resp,'status',200)
            raw=resp.read().decode('utf-8','replace')
        parsed=None
        try: parsed=json.loads(raw or '{}')
        except Exception: parsed=None
        compatible=bool(parsed and parsed.get('ok') and str(parsed.get('action') or '') in ('sync_probe','probe'))
        if compatible:
            return {'ok':True,'compatible':True,'http_status':status,
                    'message':'Apps Script متصل ويدعم بروتوكول مزامنة BIM Tasks',
                    'response':parsed}
        # Common old Apps Script behavior: returns HTTP 200 but treats the probe as old iPhone input.
        return {'ok':False,'compatible':False,'http_status':status,
                'message':'الرابط يرد، لكنه لا يدعم استقبال server_event من BIM Tasks. هذا هو سبب بقاء المهام بانتظار المزامنة.',
                'response':parsed if parsed is not None else raw[:1000]}
    except Exception as e:
        return {'ok':False,'compatible':False,'stage':'network',
                'message':'تعذر الاتصال بـ Apps Script: '+str(e)}


@app.post('/api/sync/retry-dirty')
def sync_retry_dirty(req:Request, after_id:int=0, limit:int=5):
    """Retry a small page of dirty tasks so Cloudflare/browser timeouts cannot stop the whole backlog."""
    u=need(req,'users.manage'); c=connect()
    limit=min(max(int(limit or 5),1),10)
    rows=c.execute(
        """SELECT id,task_code,title,status,sync_version
           FROM tasks
           WHERE COALESCE(sync_dirty,1)=1 AND id>?
           ORDER BY id LIMIT ?""",
        (max(int(after_id or 0),0),limit)
    ).fetchall()

    attempted=0; synced=0; failed=0; failures=[]; last_id=after_id
    for r in rows:
        tid=int(r['id']); last_id=tid; attempted+=1
        eid=_record_sync_event(c,tid,'upsert','server')
        result=_try_instant_sheet_push(c,eid)
        if result.get('ok'):
            synced+=1
        else:
            failed+=1
            failures.append({
                'id':tid,
                'task_code':r['task_code'],
                'title':r['title'],
                'status':r['status'],
                'sync_version':r['sync_version'],
                'stage':result.get('stage'),
                'message':result.get('message'),
                'http_status':result.get('http_status'),
                'response':result.get('response')
            })

    remaining=c.execute(
        "SELECT COUNT(*) n FROM tasks WHERE COALESCE(sync_dirty,1)=1"
    ).fetchone()['n']

    has_more=c.execute(
        "SELECT COUNT(*) n FROM tasks WHERE COALESCE(sync_dirty,1)=1 AND id>?",
        (last_id,)
    ).fetchone()['n'] > 0 if attempted else False

    audit(c,req,u,'sync_retry_dirty','sync',None,after={
        'attempted':attempted,'synced':synced,'failed':failed,
        'after_id':after_id,'last_id':last_id,'remaining':remaining
    })
    c.close()
    return {
        'ok':True,
        'attempted':attempted,
        'synced':synced,
        'failed':failed,
        'failures':failures,
        'next_after_id':last_id,
        'has_more':has_more,
        'remaining':remaining
    }

@app.get('/api/sync/pull')
def sync_pull(req:Request, after_id:int=0, limit:int=300):
    _sync_auth(req); c=connect()
    rows=[dict(x) for x in c.execute('SELECT * FROM sync_events WHERE id>? ORDER BY id LIMIT ?',(max(after_id,0),min(max(limit,1),1000)))]
    cursor=rows[-1]['id'] if rows else after_id
    c.close(); return {'ok':True,'events':rows,'cursor':cursor}

@app.get('/api/sync/snapshot')
def sync_snapshot(req:Request):
    _sync_auth(req); c=connect()
    tasks=[_task_sync_payload(c,r['id']) for r in c.execute('SELECT id FROM tasks ORDER BY id')]
    employees=[dict(x) for x in c.execute('SELECT id,name,department,whatsapp,telegram_chat_id,telegram_enabled,whatsapp_enabled,is_active,updated_at FROM employees ORDER BY id')]
    cursor=c.execute('SELECT COALESCE(MAX(id),0) n FROM sync_events').fetchone()['n']
    c.close(); return {'ok':True,'tasks':tasks,'employees':employees,'cursor':cursor}

@app.get('/api/sync/tasks-page')
def sync_tasks_page(req:Request, after_task_id:int=0, limit:int=50):
    _sync_auth(req); c=connect()
    lim=min(max(int(limit or 50),1),200)
    rows=c.execute('SELECT id FROM tasks WHERE id>? ORDER BY id LIMIT ?',(max(int(after_task_id or 0),0),lim)).fetchall()
    tasks=[_task_sync_payload(c,r['id']) for r in rows]
    next_after=tasks[-1]['id'] if tasks else max(int(after_task_id or 0),0)
    more=bool(c.execute('SELECT 1 FROM tasks WHERE id>? LIMIT 1',(next_after,)).fetchone())
    event_cursor=c.execute('SELECT COALESCE(MAX(id),0) n FROM sync_events').fetchone()['n']
    c.close()
    return {'ok':True,'tasks':tasks,'next_after_task_id':next_after,'has_more':more,'event_cursor':event_cursor}

@app.post('/api/sync/push-task')
async def sync_push_task(req:Request):
    _sync_auth(req); d=await req.json(); c=connect()
    task_id=d.get('id')
    task_code=str(d.get('task_code') or '').strip()
    incoming_ver=int(d.get('sync_version') or 0)
    employee_name=str(d.get('employee_name') or '').strip(); eid=None
    if employee_name:
        er=c.execute('SELECT id FROM employees WHERE lower(name)=lower(?)',(employee_name,)).fetchone()
        if er: eid=er['id']

    old=None
    if task_id:
        try: old=c.execute('SELECT * FROM tasks WHERE id=?',(int(task_id),)).fetchone()
        except Exception: old=None
    if not old and task_code:
        old=c.execute('SELECT * FROM tasks WHERE task_code=?',(task_code,)).fetchone()

    if old:
        server_ver=int(old['sync_version'] or 1)
        # A row that has no version yet is a legacy row being linked for the first time.
        # Do not treat that first link as a conflict.
        if incoming_ver and incoming_ver < server_ver and str(old['sync_source'] or 'server')!='sheet':
            c.execute("""INSERT INTO sync_conflicts(entity_type,entity_key,server_version,sheet_version,server_json,sheet_json)
                         VALUES('task',?,?,?,?,?)""",
                      (str(old['id']),server_ver,incoming_ver,
                       json.dumps(dict(old),ensure_ascii=False,default=str),
                       json.dumps(d,ensure_ascii=False,default=str)))
            server=_task_sync_payload(c,old['id']); c.close()
            raise HTTPException(409,detail={'message':'تعارض مزامنة','server':server})

        newver=max(server_ver,incoming_ver)+1
        c.execute("""UPDATE tasks SET employee_id=?,title=?,notes=?,priority=?,status=?,due_at=?,archived_at=?,
                     updated_at=CURRENT_TIMESTAMP,sync_version=?,sync_source='sheet',sync_dirty=0,sync_last_at=CURRENT_TIMESTAMP
                     WHERE id=?""",
                  (eid,str(d.get('title') or old['title']),d.get('notes'),
                   d.get('priority') or 'عادي',d.get('status') or 'جديدة',
                   d.get('due_at') or None,d.get('archived_at') or None,newver,old['id']))
        tid=old['id']; action='sheet_update'
    else:
        title=str(d.get('title') or '').strip()
        if not title:
            c.close(); raise HTTPException(400,'المهمة مطلوبة')
        code=task_code or f"TASK-{int(time.time()*1000)}"
        try:
            c.execute("""INSERT INTO tasks(task_code,employee_id,title,notes,priority,status,due_at,source,archived_at,
                         sync_version,sync_source,sync_dirty,sync_last_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,0,CURRENT_TIMESTAMP)""",
                      (code,eid,title,d.get('notes'),d.get('priority') or 'عادي',
                       d.get('status') or 'جديدة',d.get('due_at') or None,'sheet',
                       d.get('archived_at') or None,max(incoming_ver,0)+1,'sheet'))
        except Exception as ex:
            c.close()
            raise HTTPException(409,f'تعذر إنشاء المهمة من الشيت: {ex}')
        tid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        _save_reminder(c,tid,{'type':'بدون'}); action='sheet_create'

    _record_sync_event(c,tid,action,'sheet')
    out=_task_sync_payload(c,tid)
    c.close()
    return {'ok':True,'task':out}

@app.post('/api/sync/push-employee')
async def sync_push_employee(req:Request):
    _sync_auth(req); d=await req.json(); name=str(d.get('name') or '').strip()
    if not name: raise HTTPException(400,'اسم الموظف مطلوب')
    c=connect(); old=c.execute('SELECT * FROM employees WHERE lower(name)=lower(?)',(name,)).fetchone()
    vals=(d.get('department'),d.get('whatsapp'),d.get('telegram_chat_id'),int(bool(d.get('telegram_enabled',1))),int(bool(d.get('whatsapp_enabled',1))),int(bool(d.get('is_active',1))))
    if old:
        # The sheet never re-points an existing Telegram link (login codes travel there) and never
        # deactivates an employee tied to a manager account (that would lock management out).
        manager=c.execute("SELECT 1 FROM users u JOIN roles r ON r.id=u.role_id WHERE u.employee_id=? AND r.code IN ('admin','deputy') AND u.deleted_at IS NULL",(old['id'],)).fetchone()
        vals=vals[:2]+(old['telegram_chat_id'],)+vals[3:5]+((old['is_active'],) if manager else vals[5:])
        c.execute('UPDATE employees SET department=?,whatsapp=?,telegram_chat_id=?,telegram_enabled=?,whatsapp_enabled=?,is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',vals+(old['id'],)); eid=old['id']
    else:
        c.execute('INSERT INTO employees(name,department,whatsapp,telegram_chat_id,telegram_enabled,whatsapp_enabled,is_active) VALUES(?,?,?,?,?,?,?)',(name,)+vals); eid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.close(); return {'ok':True,'id':eid}

@app.get('/api/sync/conflicts')
def sync_conflicts(req:Request):
    need(req,'users.manage'); c=connect(); rows=[dict(x) for x in c.execute("SELECT * FROM sync_conflicts WHERE status='open' ORDER BY id DESC LIMIT 200")]; c.close(); return rows
