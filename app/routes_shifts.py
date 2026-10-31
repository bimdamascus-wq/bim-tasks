"""Shift messages, Telegram groups and group reminders, leave exceptions and the manual shift test.
Split out of app/main.py (package code_split) with no change in behaviour; main.py imports this module."""
from __future__ import annotations
import calendar
import json
import re
import secrets
import time
from datetime import datetime, timedelta
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from .db import connect
from .security import now
from .telegram_queue import send_resilient as send
from .main import (
    app, audit, ctx, need, templates
)

# ---------- Phase 2: shifts & automated Telegram ----------
@app.get('/shift-center', response_class=HTMLResponse)
def shift_center_page(req:Request):
    need(req,'employees.edit'); return templates.TemplateResponse(request=req,name='shift_center.html',context=ctx(req,'الشيفتات والتذكيرات'))
_SHIFT_SETTING_KEYS={'shift_due_soon_enabled':'1','shift_due_soon_minutes':'30','shift_overdue_enabled':'1','shift_start_tasks_enabled':'1','shift_end_summary_enabled':'1','shift_end_summary_minutes':'60'}
@app.get('/api/shift-settings')
def get_shift_settings(req:Request):
    need(req,'employees.edit'); c=connect(); out={}
    for k,d in _SHIFT_SETTING_KEYS.items():
        r=c.execute('SELECT value FROM settings WHERE key=?',(k,)).fetchone(); out[k]=r['value'] if r else d
    c.close(); return out
@app.patch('/api/shift-settings')
async def save_shift_settings(req:Request):
    u=need(req,'employees.edit'); d=await req.json(); c=connect(); changed={}
    for k in _SHIFT_SETTING_KEYS:
        if k in d:
            v=str(d[k]).strip(); c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,v)); changed[k]=v
    audit(c,req,u,'shift_settings_update','system',None,after=changed); c.close(); return {'ok':True}
@app.get('/api/shift-messages')
def get_shift_messages(req:Request):
    need(req,'employees.edit'); c=connect(); rows=[dict(x) for x in c.execute("SELECT * FROM shift_messages WHERE id NOT IN(SELECT record_id FROM record_lifecycle WHERE entity='shift_messages' AND deleted_at IS NOT NULL) ORDER BY is_active DESC,id DESC")]; c.close(); return rows
@app.post('/api/shift-messages')
async def create_shift_message(req:Request):
    u=need(req,'employees.edit'); d=await req.json(); text=str(d.get('message_text') or '').strip()
    if not text: raise HTTPException(400,'نص الرسالة مطلوب')
    typ=d.get('message_type') if d.get('message_type') in ('start','end') else 'start'; aud=d.get('audience_type') if d.get('audience_type') in ('all','shift','department') else 'all'
    c=connect(); c.execute("INSERT INTO shift_messages(message_type,audience_type,target_shift,target_department,message_text,start_date,end_date,is_active,created_by) VALUES(?,?,?,?,?,?,?,?,?)",(typ,aud,d.get('target_shift') or None,d.get('target_department') or None,text,d.get('start_date') or None,d.get('end_date') or None,1 if d.get('is_active',True) else 0,u['user_id'])); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; audit(c,req,u,'shift_message_create','shift_message',mid,after=d); c.close(); return {'ok':True,'id':mid}
@app.patch('/api/shift-messages/{mid}')
async def update_shift_message(mid:int, req:Request):
    u=need(req,'employees.edit'); d=await req.json(); c=connect(); old=c.execute('SELECT * FROM shift_messages WHERE id=?',(mid,)).fetchone()
    if not old: c.close(); raise HTTPException(404,'الرسالة غير موجودة')
    allowed=['message_type','audience_type','target_shift','target_department','message_text','start_date','end_date','is_active']; fields=[]; vals=[]
    for k in allowed:
        if k in d: fields.append(k+'=?'); vals.append((1 if bool(d[k]) else 0) if k=='is_active' else (d[k] or None))
    if fields: fields.append('updated_at=CURRENT_TIMESTAMP'); vals.append(mid); c.execute('UPDATE shift_messages SET '+','.join(fields)+' WHERE id=?',vals)
    audit(c,req,u,'shift_message_update','shift_message',mid,before=dict(old),after=d); c.close(); return {'ok':True}
@app.delete('/api/shift-messages/{mid}')
def delete_shift_message(mid:int, req:Request):
    from .lifecycle import change
    return change(req,'shift_messages',mid,'delete')


@app.get('/api/shift-dispatch-log')
def shift_dispatch_log(req:Request, limit:int=100):
    need(req,'employees.edit'); c=connect(); rows=[dict(x) for x in c.execute("SELECT l.*,e.name employee_name,t.title task_title FROM shift_dispatch_log l LEFT JOIN employees e ON e.id=l.employee_id LEFT JOIN tasks t ON t.id=l.task_id ORDER BY l.id DESC LIMIT ?",(min(max(limit,1),500),))]; c.close(); return rows



def _next_group_send_at_unbounded(r, now=None):
    now=now or datetime.now()
    typ=str(r['recurrence_type'] or 'مرة واحدة')
    try:
        if r['end_date'] and now.date()>datetime.fromisoformat(str(r['end_date'])[:10]).date():
            return None
    except: pass
    try:
        start_date=datetime.fromisoformat(str(r['start_date'])[:10]).date() if r['start_date'] else now.date()
    except:
        start_date=now.date()
    def hm():
        try:
            h,m=map(int,str(r['send_time'] or '09:00').split(':')[:2]); return h,m
        except:return 9,0
    if typ in ('مرة واحدة','تاريخ محدد'):
        at=None
        try: at=datetime.fromisoformat(str(r['specific_at']).replace('Z','+00:00')).replace(tzinfo=None) if r['specific_at'] else None
        except: pass
        return at.isoformat(timespec='minutes') if at and at>=now else None
    h,m=hm()
    d=max(now.date(),start_date)
    if typ=='يومي':
        candidate=datetime.combine(d,datetime.min.time()).replace(hour=h,minute=m)
        if candidate<now: candidate+=timedelta(days=1)
        return candidate.isoformat(timespec='minutes')
    if typ=='أسبوعي':
        target=int(r['week_day'] or 0)
        for i in range(8):
            dd=d+timedelta(days=i); wd=(dd.weekday()+1)%7
            candidate=datetime.combine(dd,datetime.min.time()).replace(hour=h,minute=m)
            if wd==target and candidate>=now:return candidate.isoformat(timespec='minutes')
    if typ=='شهري':
        day=max(1,min(31,int(r['month_day'] or 1)))
        y,mn=d.year,d.month
        for _ in range(14):
            maxd=calendar.monthrange(y,mn)[1]
            dd=datetime(y,mn,min(day,maxd),h,m)
            if dd>=now and dd.date()>=start_date:return dd.isoformat(timespec='minutes')
            mn+=1
            if mn>12:y+=1;mn=1
    if typ=='كل X أيام':
        n=max(1,int(r['every_n_days'] or 1))
        dd=start_date
        if dd<now.date():
            diff=(now.date()-dd).days
            dd += timedelta(days=((diff+n-1)//n)*n)
        candidate=datetime.combine(dd,datetime.min.time()).replace(hour=h,minute=m)
        if candidate<now:candidate+=timedelta(days=n)
        return candidate.isoformat(timespec='minutes')
    return None


def _next_group_send_at(r, now=None):
    result=_next_group_send_at_unbounded(r,now)
    if result and r['end_date'] and result[:10]>str(r['end_date'])[:10]:return None
    return result

# ---------- Telegram Groups / Phase 3 ----------
@app.get('/telegram-groups', response_class=HTMLResponse)
def telegram_groups_page(req:Request):
    need(req,'employees.edit')
    return templates.TemplateResponse(request=req,name='telegram_groups.html',context=ctx(req,'مجموعات Telegram'))

@app.get('/api/telegram-groups')
def api_telegram_groups(req:Request):
    need(req,'employees.edit'); c=connect()
    rows=[dict(x) for x in c.execute("""SELECT g.*,
      (SELECT COUNT(*) FROM group_reminders r WHERE r.group_id=g.id AND r.is_active=1) active_reminders
      FROM telegram_groups g WHERE g.id NOT IN(SELECT record_id FROM record_lifecycle WHERE entity='telegram_groups' AND deleted_at IS NOT NULL) ORDER BY g.is_active DESC,g.name""")]
    c.close(); return rows

@app.post('/api/telegram-groups')
async def add_telegram_group(req:Request):
    u=need(req,'employees.edit'); d=await req.json()
    name=str(d.get('name') or '').strip()
    if not name: raise HTTPException(400,'اسم المجموعة مطلوب')
    c=connect()
    c.execute("""INSERT INTO telegram_groups(name,department,is_active,created_by)
                 VALUES(?,?,?,?)""",(name,str(d.get('department') or '').strip() or None,
                 1 if d.get('is_active',True) else 0,u['user_id']))
    gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    audit(c,req,u,'telegram_group_create','telegram_group',gid,after=d)
    c.close(); return {'ok':True,'id':gid}

@app.patch('/api/telegram-groups/{gid}')
async def edit_telegram_group(gid:int, req:Request):
    u=need(req,'employees.edit'); d=await req.json(); c=connect()
    old=c.execute('SELECT * FROM telegram_groups WHERE id=?',(gid,)).fetchone()
    if not old: c.close(); raise HTTPException(404,'المجموعة غير موجودة')
    sets=[]; vals=[]
    for k in ('name','department','is_active'):
        if k in d:
            sets.append(k+'=?')
            vals.append((1 if bool(d[k]) else 0) if k=='is_active' else (str(d[k]).strip() or None))
    if sets:
        sets.append('updated_at=CURRENT_TIMESTAMP'); vals.append(gid)
        c.execute('UPDATE telegram_groups SET '+','.join(sets)+' WHERE id=?',vals)
    audit(c,req,u,'telegram_group_update','telegram_group',gid,before=dict(old),after=d)
    c.close(); return {'ok':True}

@app.delete('/api/telegram-groups/{gid}')
def delete_telegram_group(gid:int, req:Request):
    from .lifecycle import change
    return change(req,'telegram_groups',gid,'delete')


@app.post('/api/telegram-groups/{gid}/link-code')
def telegram_group_link_code(gid:int, req:Request):
    u=need(req,'employees.edit'); c=connect()
    g=c.execute('SELECT * FROM telegram_groups WHERE id=?',(gid,)).fetchone()
    if not g: c.close(); raise HTTPException(404,'المجموعة غير موجودة')
    code=secrets.token_urlsafe(9).replace('-','').replace('_','')[:10]
    c.execute("""UPDATE telegram_groups SET link_code=?,link_expires_at=datetime('now','+24 hours'),
                 updated_at=CURRENT_TIMESTAMP WHERE id=?""",(code,gid))
    audit(c,req,u,'telegram_group_link_code','telegram_group',gid,after={'expires_hours':24})
    c.close()
    return {'ok':True,'code':code,'command':f'/linkgroup {code}','expires_hours':24}

@app.post('/api/telegram-groups/{gid}/unlink')
def telegram_group_unlink(gid:int, req:Request):
    u=need(req,'employees.edit'); c=connect()
    g=c.execute('SELECT * FROM telegram_groups WHERE id=?',(gid,)).fetchone()
    if not g: c.close(); raise HTTPException(404,'المجموعة غير موجودة')
    c.execute("""UPDATE telegram_groups SET chat_id=NULL,telegram_title=NULL,linked_at=NULL,
                 link_code=NULL,link_expires_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""",(gid,))
    audit(c,req,u,'telegram_group_unlink','telegram_group',gid,before=dict(g),after={'chat_id':None})
    c.close(); return {'ok':True}

@app.get('/api/group-reminders')
def api_group_reminders(req:Request):
    need(req,'employees.edit'); c=connect()
    rows=[]
    for x in c.execute("""SELECT r.*,g.name group_name,g.chat_id
      FROM group_reminders r JOIN telegram_groups g ON g.id=r.group_id
      WHERE r.id NOT IN(SELECT record_id FROM record_lifecycle WHERE entity='group_reminders' AND deleted_at IS NOT NULL) AND g.id NOT IN(SELECT record_id FROM record_lifecycle WHERE entity='telegram_groups' AND deleted_at IS NOT NULL) ORDER BY r.is_active DESC,r.id DESC"""):
        d=dict(x); d['next_send_at']=_next_group_send_at(x) if x['is_active'] else None; rows.append(d)
    c.close(); return rows

@app.post('/api/group-reminders')
async def add_group_reminder(req:Request):
    u=need(req,'employees.edit'); d=await req.json()
    raw_ids=d.get('group_ids') if 'group_ids' in d else [d.get('group_id')]
    if not isinstance(raw_ids,list) or not raw_ids or len(raw_ids)>100:
        raise HTTPException(400,'اختر مجموعة واحدة على الأقل وبحد أقصى 100 مجموعة')
    try:
        if any(isinstance(x,bool) or not str(x).isdigit() for x in raw_ids):raise ValueError()
        gids=list(dict.fromkeys(int(x) for x in raw_ids))
        if any(x<=0 for x in gids):raise ValueError()
    except (ValueError,TypeError):raise HTTPException(400,'اختيار مجموعات غير صالح')
    text=str(d.get('message_text') or '').strip()
    if not text:raise HTTPException(400,'نص الرسالة مطلوب')
    typ=str(d.get('recurrence_type') or 'مرة واحدة')
    if typ not in {'مرة واحدة','تاريخ محدد','يومي','أسبوعي','شهري','كل X أيام'}:
        raise HTTPException(400,'نوع التكرار غير صحيح')
    c=connect();ids=[]
    try:
        c.execute('BEGIN IMMEDIATE')
        for gid in gids:
            if not c.execute("SELECT 1 FROM telegram_groups WHERE id=? AND id NOT IN (SELECT record_id FROM record_lifecycle WHERE entity='telegram_groups' AND deleted_at IS NOT NULL)",(gid,)).fetchone():
                raise HTTPException(404,'إحدى المجموعات غير موجودة أو محذوفة؛ لم يحفظ أي تذكير')
        for gid in gids:
            rid=c.execute("""INSERT INTO group_reminders(group_id,title,message_text,recurrence_type,every_n_days,
                week_day,month_day,send_time,specific_at,start_date,end_date,is_active,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (gid,str(d.get('title') or '').strip() or None,text,typ,d.get('every_n_days') or None,
                 d.get('week_day') if d.get('week_day') is not None else None,d.get('month_day') or None,
                 str(d.get('send_time') or '').strip() or None,str(d.get('specific_at') or '').strip() or None,
                 str(d.get('start_date') or '').strip() or None,str(d.get('end_date') or '').strip() or None,
                 1 if d.get('is_active',True) else 0,u['user_id'])).lastrowid
            ids.append(rid)
            audit(c,req,u,'group_reminder_create','group_reminder',rid,after={**d,'group_id':gid})
        c.execute('COMMIT');return {'ok':True,'id':ids[0],'ids':ids,'count':len(ids)}
    except Exception:
        if c.in_transaction:c.execute('ROLLBACK')
        raise
    finally:c.close()

@app.patch('/api/group-reminders/{rid}')
async def edit_group_reminder(rid:int, req:Request):
    u=need(req,'employees.edit'); d=await req.json(); c=connect()
    old=c.execute('SELECT * FROM group_reminders WHERE id=?',(rid,)).fetchone()
    if not old: c.close(); raise HTTPException(404,'التذكير غير موجود')
    allowed=['group_id','title','message_text','recurrence_type','every_n_days','week_day','month_day',
             'send_time','specific_at','start_date','end_date','is_active']
    sets=[]; vals=[]
    for k in allowed:
        if k in d:
            sets.append(k+'=?')
            if k=='is_active': vals.append(1 if bool(d[k]) else 0)
            else: vals.append(d[k] if d[k] not in ('',None) else None)
    if sets:
        sets += ['last_run_key=NULL','updated_at=CURRENT_TIMESTAMP']; vals.append(rid)
        c.execute('UPDATE group_reminders SET '+','.join(sets)+' WHERE id=?',vals)
    audit(c,req,u,'group_reminder_update','group_reminder',rid,before=dict(old),after=d)
    c.close(); return {'ok':True}

@app.delete('/api/group-reminders/{rid}')
def delete_group_reminder(rid:int, req:Request):
    from .lifecycle import change
    return change(req,'group_reminders',rid,'delete')


@app.get('/api/group-dispatch-log')
def group_dispatch_log(req:Request, limit:int=200):
    need(req,'employees.edit'); c=connect()
    rows=[dict(x) for x in c.execute("""SELECT l.*,g.name group_name,r.title reminder_title
      FROM group_dispatch_log l LEFT JOIN telegram_groups g ON g.id=l.group_id
      LEFT JOIN group_reminders r ON r.id=l.reminder_id
      ORDER BY l.id DESC LIMIT ?""",(min(max(limit,1),500),))]
    c.close(); return rows




# ---------- Shift exceptions / leave ----------
@app.get('/api/shift-exceptions')
def shift_exceptions_list(req:Request):
    need(req,'employees.edit'); c=connect()
    rows=[dict(x) for x in c.execute("""SELECT x.*,e.name employee_name,e.department
      FROM shift_exceptions x JOIN employees e ON e.id=x.employee_id
      WHERE x.id NOT IN(SELECT record_id FROM record_lifecycle WHERE entity='shift_exceptions' AND deleted_at IS NOT NULL) ORDER BY x.is_active DESC,date(x.start_date) DESC,x.id DESC""")]
    c.close(); return rows

@app.post('/api/shift-exceptions')
async def shift_exception_add(req:Request):
    u=need(req,'employees.edit'); d=await req.json()
    eid=int(d.get('employee_id') or 0); typ=str(d.get('exception_type') or 'إجازة')
    if typ not in ('إجازة','دوام استثنائي'): raise HTTPException(400,'نوع الاستثناء غير صحيح')
    sd=str(d.get('start_date') or '').strip(); ed=str(d.get('end_date') or sd).strip()
    if not eid or not sd: raise HTTPException(400,'الموظف وتاريخ البداية مطلوبان')
    c=connect()
    c.execute("""INSERT INTO shift_exceptions(employee_id,exception_type,start_date,end_date,note,is_active,created_by)
      VALUES(?,?,?,?,?,?,?)""",(eid,typ,sd,ed,str(d.get('note') or '').strip() or None,
      1 if d.get('is_active',True) else 0,u['user_id']))
    xid=c.execute("SELECT last_insert_rowid() x").fetchone()['x']
    audit(c,req,u,'shift_exception_create','shift_exception',xid,after=d); c.close()
    return {'ok':True,'id':xid}

@app.delete('/api/shift-exceptions/{xid}')
def shift_exception_delete(xid:int, req:Request):
    from .lifecycle import change
    return change(req,'shift_exceptions',xid,'delete')


@app.get('/automation-center', response_class=HTMLResponse)
def automation_center_page(req:Request):
    need(req,'employees.edit')
    return templates.TemplateResponse(request=req,name='automation_center.html',context=ctx(req,'مركز الأتمتة'))

@app.get('/api/automation-summary')
def automation_summary(req:Request):
    need(req,'employees.edit'); c=connect()
    out={
      'shift_messages':c.execute("SELECT COUNT(*) n FROM shift_messages WHERE is_active=1").fetchone()['n'],
      'shift_exceptions':c.execute("SELECT COUNT(*) n FROM shift_exceptions WHERE is_active=1").fetchone()['n'],
      'task_reminders':c.execute("SELECT COUNT(*) n FROM task_reminders WHERE is_active=1").fetchone()['n'],
      'group_reminders':c.execute("SELECT COUNT(*) n FROM group_reminders WHERE is_active=1").fetchone()['n'],
      'recurring_tasks':c.execute("SELECT COUNT(*) n FROM recurring_tasks WHERE is_active=1").fetchone()['n'],
      'queue_pending':c.execute("SELECT COUNT(*) n FROM telegram_outbox WHERE status IN ('pending','retrying')").fetchone()['n']
    }
    c.close(); return out

# ---------- Shift Test (manual, does not wait for real shift time) ----------
@app.get('/api/shift-test/employees')
def shift_test_employees(req:Request):
    need(req,'employees.edit'); c=connect()
    rows=[dict(x) for x in c.execute("""SELECT id,name,department,shift_type,shift_start,shift_end,
      telegram_chat_id,telegram_enabled FROM employees
      WHERE is_active=1 ORDER BY name""").fetchall()]
    c.close(); return rows

def _shift_test_matches(m,e):
    a=m['audience_type'] or 'all'
    if a=='all': return True
    if a=='shift': return (m['target_shift'] or '')==(e['shift_type'] or '')
    if a=='department':
        return (m['target_department'] or '').strip().casefold()==(e['department'] or '').strip().casefold()
    return False

@app.post('/api/shift-test')
async def run_shift_test(req:Request):
    u=need(req,'employees.edit'); d=await req.json()
    eid=int(d.get('employee_id') or 0); kind=str(d.get('kind') or '')
    if kind not in ('start','end'): raise HTTPException(400,'نوع الاختبار غير صحيح')
    c=connect()
    e=c.execute('SELECT * FROM employees WHERE id=?',(eid,)).fetchone()
    if not e: c.close(); raise HTTPException(404,'الموظف غير موجود')
    if not e['telegram_enabled'] or not e['telegram_chat_id']:
        c.close(); raise HTTPException(400,'الموظف غير مربوط بـ Telegram')
    today=datetime.now().date().isoformat()
    msgs=c.execute("""SELECT * FROM shift_messages WHERE is_active=1 AND message_type=?
      AND (start_date IS NULL OR start_date='' OR date(start_date)<=date(?))
      AND (end_date IS NULL OR end_date='' OR date(end_date)>=date(?)) ORDER BY id""",
      (kind,today,today)).fetchall()
    sent_count=0; queued_count=0
    marker='🧪 اختبار بداية الشيفت' if kind=='start' else '🧪 اختبار نهاية الشيفت'
    r=send(e['telegram_chat_id'],marker)
    sent_count += 1 if r.get('ok') and not r.get('queued') else 0
    queued_count += 1 if r.get('queued') else 0
    for m in msgs:
        if _shift_test_matches(m,e):
            r=send(e['telegram_chat_id'],m['message_text'],source_type='shift_test',source_id=f"{eid}:{kind}")
            sent_count += 1 if r.get('ok') and not r.get('queued') else 0
            queued_count += 1 if r.get('queued') else 0
    tasks=c.execute("""SELECT id,title,status,priority,due_at FROM tasks
      WHERE (employee_id=? OR EXISTS(SELECT 1 FROM task_participants p WHERE p.task_id=tasks.id AND p.employee_id=? AND p.status<>'تم')) AND archived_at IS NULL AND status NOT IN ('تم','ملغاة')
      ORDER BY CASE priority WHEN 'عاجلة' THEN 1 WHEN 'عالية' THEN 2 WHEN 'عادي' THEN 3 ELSE 4 END,
      CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,due_at,id DESC LIMIT 50""",(eid,eid)).fetchall()
    if kind=='start':
        lines=['📋 مهامك اليوم — اختبار']
        if tasks:
            for i,t in enumerate(tasks,1):
                due=f" | ⏰ {t['due_at']}" if t['due_at'] else ''
                lines.append(f"{i}. {t['title']} — {t['status']} — {t['priority']}{due}")
        else: lines.append('✅ لا توجد لديك مهام مفتوحة حالياً.')
    else:
        lines=['📋 ملخص نهاية الشيفت — اختبار']
        if tasks:
            lines.append(f"بقي لديك {len(tasks)} مهمة غير مكتملة:")
            for i,t in enumerate(tasks,1):
                lines.append(f"{i}. {t['title']} — {t['status']}")
        else: lines.append('✅ جميع المهام مكتملة.')
    r=send(e['telegram_chat_id'],'\n'.join(lines),source_type='shift_test',source_id=f"{eid}:{kind}:tasks")
    sent_count += 1 if r.get('ok') and not r.get('queued') else 0
    queued_count += 1 if r.get('queued') else 0
    event_key=f"shift_test:{eid}:{kind}:{int(time.time())}"
    c.execute("""INSERT OR IGNORE INTO shift_dispatch_log(event_key,employee_id,kind,status,error,sent_at)
      VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)""",(event_key,eid,'اختبار بداية الشيفت' if kind=='start' else 'اختبار نهاية الشيفت',
      'queued' if queued_count else 'sent',None))
    audit(c,req,u,'shift_test','employee',eid,after={'kind':kind,'sent':sent_count,'queued':queued_count})
    c.close()
    return {'ok':True,'sent':sent_count,'queued':queued_count}


# ---------- daily Telegram summary for supervisors (app/supervisor_summary.py) ----------
@app.get('/api/supervisor-summary/settings')
def supervisor_summary_settings(req:Request):
    need(req,'employees.edit'); c=connect()
    try:
        from . import supervisor_summary as ss
        cfg=ss.settings(c); off=ss.off_users(cfg)
        people=[{'user_id':r['user_id'],'name':r['full_name'] or r['name'],'role':r['role_code'],'team_size':len(ss.members(c,r)),
                 'enabled':r['user_id'] not in off} for r in ss.recipients(c,True)]
        unlinked=[r['full_name'] or r['username'] for r in c.execute("""SELECT u.full_name,u.username FROM users u JOIN roles r ON r.id=u.role_id
            LEFT JOIN employees e ON e.id=u.employee_id WHERE r.code='supervisor' AND u.is_active=1 AND u.deleted_at IS NULL
            AND (e.id IS NULL OR e.telegram_enabled<>1 OR e.telegram_chat_id IS NULL OR trim(e.telegram_chat_id)='')""")]
        return {'enabled':ss._on(cfg['supervisor_summary_enabled']),'time':cfg['supervisor_summary_time'],
                'managers':ss._on(cfg['supervisor_summary_managers']),'people':people,'not_linked':unlinked}
    finally: c.close()

@app.put('/api/supervisor-summary/settings')
async def save_supervisor_summary_settings(req:Request):
    u=need(req,'employees.edit'); d=await req.json(); c=connect()
    try:
        t=str(d.get('time','18:00')).strip()
        if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',t): raise HTTPException(400,'الوقت بصيغة 18:00')
        vals={'supervisor_summary_enabled':'1' if d.get('enabled') else '0','supervisor_summary_time':t,
              'supervisor_summary_managers':'1' if d.get('managers') else '0',
              'supervisor_summary_off':json.dumps(sorted({int(x) for x in d.get('off',[])}))}
        for k,v in vals.items():
            c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,v))
        audit(c,req,u,'supervisor_summary_settings','system',None,after=vals); return {'ok':True}
    finally: c.close()

@app.get('/api/supervisor-summary/preview')
def supervisor_summary_preview(req:Request, user_id:int=0):
    need(req,'employees.edit'); c=connect()
    try:
        from . import supervisor_summary as ss
        rec=next((r for r in ss.recipients(c,True) if r['user_id']==user_id),None)
        if not rec: raise HTTPException(404,'الحساب غير موجود أو غير مربوط بـ Telegram')
        return {'text':ss.build_text(c,rec) or 'لا يوجد موظفون في فريق هذا المسؤول — لن تُرسل رسالة.'}
    finally: c.close()
