"""Stable identities for multi-task templates; legacy single-task identity is retained."""
import json,re
from fastapi import HTTPException

def task_items(body):
    return body['tasks'] if 'tasks' in body else [{**body,'key':'legacy','is_active':True}]

def instruction_items(body):
    return body.get('instruction_items',[{'key':'legacy','text':body.get('instructions',''),'is_active':True}])

def validate_items(d,kind,validate_single):
    if not isinstance(d,dict):raise HTTPException(400,'بيانات القالب غير صحيحة')
    key='tasks' if kind=='tasks' else 'instruction_items'
    if key not in d:return validate_single(d,kind)
    rows=d[key]
    if not isinstance(rows,list) or len(rows)>100:raise HTTPException(400,'الحد الأقصى 100 بند في القالب')
    seen=set();result=[]
    for r in rows:
        if not isinstance(r,dict):raise HTTPException(400,'بند غير صحيح')
        identity=r.get('key')
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',identity) or identity in seen:raise HTTPException(400,'معرّف البند مفقود أو مكرر')
        seen.add(identity)
        if type(r.get('is_active',True)) is not bool:raise HTTPException(400,'حالة البند غير صحيحة')
        if kind=='tasks':item=validate_single(r,'tasks')
        else:
            text=r.get('text','')
            if not isinstance(text,str) or not text.strip() or len(text)>12000:raise HTTPException(400,'اكتب نص التعليمات حتى 12000 حرف لكل بند')
            item={'text':text.strip()}
        item.update(key=identity,is_active=r.get('is_active',True));result.append(item)
    if kind=='instructions':
        text='\n\n'.join(r['text'] for r in result if r['is_active'])
        if len(text)>24000:raise HTTPException(400,'مجموع التعليمات يتجاوز 24000 حرف')
        return {'instruction_items':result,'instructions':text}
    text=str(d.get('instructions') or '')
    if len(text)>24000:raise HTTPException(400,'التعليمات العامة طويلة')
    return {'tasks':result,'instructions':text}

def item_body(body,item):
    b={**item};b['item_key']=item['key']
    if 'tasks' in body:b['instructions']='\n\n'.join(x for x in [body.get('instructions',''),item.get('instructions','')] if x)
    return b

def effective_item(c,tid,eid,body,item):
    b=item_body(body,item)
    row=c.execute('SELECT body_json FROM universal_overrides WHERE template_id=? AND employee_id=?',(tid,eid)).fetchone()
    if row:
        d=json.loads(row[0]);b.update({k:v for k,v in d.items() if k!='items'})
        b.update(d.get('items',{}).get(item['key'],{}))
    return b

def removed_keys(old,new,kind):
    getter=task_items if kind=='tasks' else instruction_items
    active={r['key'] for r in getter(new) if r.get('is_active',True)}
    return {r['key'] for r in getter(old) if r.get('is_active',True)}-active

def open_tasks(c,tid,keys=None):
    rows=c.execute("SELECT t.id,t.title,t.status,mt.body_json,mt.version,mt.customized FROM tasks t JOIN universal_task_meta mt ON mt.task_id=t.id WHERE mt.template_id=? AND t.status NOT IN ('تم','ملغاة') AND t.archived_at IS NULL",(tid,)).fetchall()
    return [dict(r) for r in rows if keys is None or json.loads(r['body_json']).get('item_key','legacy') in keys]

def cancel_open(c,tid,keys=None):
    rows=open_tasks(c,tid,keys)
    for r in rows:
        c.execute("UPDATE tasks SET status='ملغاة',updated_at=CURRENT_TIMESTAMP WHERE id=?",(r['id'],))
        c.execute('UPDATE task_reminders SET is_active=0 WHERE task_id=?',(r['id'],))
        c.execute("INSERT INTO task_events(task_id,event_type,old_status,new_status,note,source) VALUES(?,'template_cancel',?,'ملغاة','إلغاء صريح من إدارة القالب','dashboard')",(r['id'],r['status']))
    return [r['id'] for r in rows]

def sync_changes(c,ids):
    from . import main
    for tid in ids:
        main._touch_task_sync(c,tid);main._refresh_telegram_task_messages(c,tid)
