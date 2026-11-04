"""Tasks: list, detail, create, edit, status, participants, comments, attachments, quick voice.
Split out of app/main.py (package code_split) with no change in behaviour; main.py imports this module."""
from __future__ import annotations
import json
import time
from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Optional
from . import access
from .db import connect
from .work_settings import record_completion
from .main import (
    ROOT, SQL_NOT_AUDIO, _attachment_dir, _is_audio, _notify_admin_comment, _notify_employee,
    _notify_employee_id, _notify_task_attachment, _notify_task_notes_update, _refresh_telegram_task_messages,
    _safe_filename, _save_reminder, _task_accessible, _task_with_reminder, _telegram_task_keyboard,
    _touch_task_sync, _try_instant_sheet_push, app, ar_when, audit, need, need_work_write
)

# ---------- tasks ----------
@app.get('/api/tasks')
def api_tasks(req: Request, status:Optional[str]=None, employee_id:Optional[int]=None, q:Optional[str]=None, archived:int=0, department:Optional[str]=None, priority:Optional[str]=None, date_from:Optional[str]=None, date_to:Optional[str]=None, overdue:bool=False, problems:bool=False):
    u=need(req,'tasks.view'); c=connect(); where=[]; p=[]
    where.append('t.archived_at IS NOT NULL' if archived else 't.archived_at IS NULL')
    if u['role_code']=='employee' and not u['employee_id']:
        c.close()
        raise HTTPException(403, 'الحساب غير مربوط بموظف، راجع الإدارة')
    scope_sql,scope_args=access.task_filter_sql(c,u); where.append(scope_sql); p += scope_args
    if status: where.append('t.status=?'); p.append(status)
    if employee_id is not None: where.append('t.employee_id=?'); p.append(employee_id)
    if department:where.append('e.department=?');p.append(department)
    if priority:where.append('t.priority=?');p.append(priority)
    if date_from:where.append("date(t.created_at,'localtime')>=date(?)");p.append(date_from)
    if date_to:where.append("date(t.created_at,'localtime')<=date(?)");p.append(date_to)
    if overdue:where.append("t.status NOT IN ('تم','ملغاة') AND datetime(t.due_at)<datetime('now','localtime')")
    if problems:where.append("EXISTS(SELECT 1 FROM task_events ev WHERE ev.task_id=t.id AND ev.event_type='note')")
    if q: where.append('(t.title LIKE ? OR t.notes LIKE ? OR e.name LIKE ?)'); p += [f'%{q}%',f'%{q}%',f'%{q}%']
    w='WHERE '+' AND '.join(where) if where else ''
    rows=[dict(x) for x in c.execute(f'''SELECT t.*,COALESCE(e.name,CASE WHEN t.is_group=1 THEN 'مهمة جماعية' ELSE 'بدون موظف' END) employee_name,
      r.type reminder_type,r.start_at reminder_start_at,r.specific_at reminder_specific_at,
      r.weekly_day reminder_weekly_day,r.weekly_time reminder_weekly_time,r.monthly_day reminder_monthly_day,
      r.monthly_time reminder_monthly_time,r.is_active reminder_active,r.last_sent_at reminder_last_sent_at,
      r.every_n reminder_every_n,r.every_unit reminder_every_unit,r.before_minutes reminder_before_minutes,
      (SELECT COUNT(*) FROM task_participants p3 WHERE p3.task_id=t.id) participant_count
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id LEFT JOIN task_reminders r ON r.task_id=t.id {w}
      ORDER BY CASE t.status WHEN 'جديدة' THEN 1 WHEN 'بدأت' THEN 2 WHEN 'قيد التنفيذ' THEN 3 WHEN 'بانتظار' THEN 4 ELSE 9 END,t.id DESC''',p)]
    from .delegation import decorate
    decorate(c,rows)
    from .reminders import label as reminder_label
    for r in rows: r['reminder_label']=reminder_label({'type':r['reminder_type'],'start_at':r['reminder_start_at'],'every_n':r['reminder_every_n'],
        'every_unit':r['reminder_every_unit'],'before_minutes':r['reminder_before_minutes'],'is_active':r['reminder_active']})
    c.close(); return rows

@app.get('/api/tasks/{tid}')
def api_task(tid:int, req:Request):
    u=need(req,'tasks.view'); c=connect(); access=_task_accessible(c,u,tid)
    if not access: c.close(); raise HTTPException(404)
    if u['role_code']=='employee' and not access['viewed_at']:
        c.execute('UPDATE tasks SET viewed_at=CURRENT_TIMESTAMP WHERE id=?',(tid,))
    row=_task_with_reminder(c,tid)
    d=dict(row)
    from .reminders import label as reminder_label
    d['reminder_label']=reminder_label({'type':d['reminder_type'],'start_at':d['reminder_start_at'],'every_n':d['reminder_every_n'],
        'every_unit':d['reminder_every_unit'],'before_minutes':d['reminder_before_minutes'],'is_active':d['reminder_active']})
    d['participants']=[dict(x) for x in c.execute('''SELECT p.*,e.name FROM task_participants p JOIN employees e ON e.id=p.employee_id WHERE p.task_id=?''',(tid,))]
    d['completion_entries']=[dict(x) for x in c.execute('SELECT * FROM bim_completion_entries WHERE task_id=? ORDER BY id',(tid,))]
    d['subtasks']=[dict(x) for x in c.execute('SELECT * FROM task_subtasks WHERE task_id=? ORDER BY id',(tid,))]
    d['comments']=[dict(x) for x in c.execute('SELECT * FROM task_comments WHERE task_id=? ORDER BY id',(tid,))]
    d['attachments']=[dict(x) for x in c.execute(f'SELECT * FROM task_attachments WHERE task_id=? AND {SQL_NOT_AUDIO} ORDER BY id',(tid,))]
    d['events']=[dict(x) for x in c.execute('SELECT * FROM task_events WHERE task_id=? ORDER BY id',(tid,))]
    from .delegation import decorate
    decorate(c,[d])
    d['delegations']=[dict(x) for x in c.execute('SELECT * FROM task_delegations WHERE task_id=? ORDER BY id DESC',(tid,))]
    c.close(); return d

@app.post('/api/tasks')
async def create_task(req: Request):
    u=need(req,'tasks.create'); d=await req.json(); title=str(d.get('title','')).strip()
    if not title: raise HTTPException(400,'المهمة مطلوبة')
    from .reminders import normalize as check_reminder
    try: check_reminder(d.get('reminder'))  # refuse before the task row exists
    except ValueError as e: raise HTTPException(400,str(e))
    if (d.get('reminder') or {}).get('type')=='قبل الموعد' and not d.get('due_at'): raise HTTPException(400,'«قبل الموعد» يحتاج موعداً للمهمة')
    c=connect(); code=f"TASK-{int(time.time()*1000)}"; emp=d.get('employee_id') or None
    participants=d.get('participants') or []
    if not isinstance(participants,list): c.close();raise HTTPException(400,'قائمة المشاركين غير صحيحة')
    try: participants=list(dict.fromkeys(int(e) for e in participants))
    except (ValueError,TypeError): c.close();raise HTTPException(400,'معرف أحد المشاركين غير صحيح')
    if len(participants)>200: c.close();raise HTTPException(400,'الحد الأقصى 200 مشارك')
    if participants:
        marks=','.join('?' for _ in participants)
        count=c.execute('SELECT COUNT(*) FROM employees WHERE is_active=1 AND id IN ('+marks+')',participants).fetchone()[0]
        if count!=len(participants): c.close();raise HTTPException(400,'أحد المشاركين غير موجود أو غير فعال')
        d['participants']=participants
    try:
        access.require_assignable(c,u,[emp]+participants)
        if emp and not c.execute('SELECT 1 FROM employees WHERE id=? AND is_active=1',(emp,)).fetchone(): raise HTTPException(400,'الموظف غير موجود أو غير فعال')
    except HTTPException: c.close(); raise
    c.execute('''INSERT INTO tasks(task_code,employee_id,title,notes,priority,status,due_at,created_by,source,is_group)
      VALUES(?,?,?,?,?,?,?,?,?,?)''',(code,emp,title,d.get('notes'),d.get('priority','عادي'),'جديدة',d.get('due_at') or None,u['user_id'],d.get('source','dashboard'),1 if d.get('participants') else 0))
    tid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    for eid in d.get('participants') or []: c.execute('INSERT OR IGNORE INTO task_participants(task_id,employee_id) VALUES(?,?)',(tid,int(eid)))
    for st in d.get('subtasks') or []:
        if str(st).strip(): c.execute('INSERT INTO task_subtasks(task_id,title) VALUES(?,?)',(tid,str(st).strip()))
    _save_reminder(c,tid,d.get('reminder') or {})
    audit(c,req,u,'task_create','task',tid,after=d)
    c.execute("INSERT INTO task_events(task_id,employee_id,event_type,new_status,source) VALUES(?,?,'created','جديدة','dashboard')",(tid,emp))
    _touch_task_sync(c,tid,'server','create')
    if d.get('participants'):
        for eid in sorted(set(int(x) for x in d['participants'])):
            _notify_employee_id(c,eid,f"📌 مهمة جماعية جديدة\n\nالمهمة: {title}\nالأولوية: {d.get('priority','عادي')}\nيمكنك متابعة حالة مشاركتك في BIM Tasks.",tid,'مهمة جماعية')
    else:
        _notify_employee(c,tid,'مهمة جديدة')
    c.close(); return {'ok':True,'id':tid,'task_code':code}

@app.patch('/api/tasks/{tid}')
async def edit_task(tid:int, req:Request):
    u=need(req,'tasks.edit'); d=await req.json(); c=connect()
    c.execute('BEGIN IMMEDIATE')
    try:
        old=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
        if not old: raise HTTPException(404,'المهمة غير موجودة')
        access.require_manage_task(c,u,old)
        if 'employee_id' in d or 'participants' in d:
            access.require_assignable(c,u,[d.get('employee_id')]+list(d.get('participants') or []))
        if 'status' in d and d['status'] not in {'جديدة','بدأت','قيد التنفيذ','بانتظار','تم','ملغاة'}:raise HTTPException(400,'حالة غير صحيحة')
        allowed=['employee_id','title','notes','priority','status','due_at']; fields=[]; vals=[]
        for k in allowed:
            if k in d:
                fields.append(f'{k}=?'); v=d[k]
                if k in ('employee_id','due_at') and not v: v=None
                vals.append(v)
        if 'status' in d:
            if d['status']=='تم': fields.append('completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP)')
            elif old['status']=='تم': fields.append('completed_at=NULL')
        if fields:
            c.execute('UPDATE universal_task_meta SET customized=1 WHERE task_id=?',(tid,))
            fields.append('updated_at=CURRENT_TIMESTAMP'); vals.append(tid); c.execute('UPDATE tasks SET '+','.join(fields)+' WHERE id=?',vals)
        if 'participants' in d:
            selected=[int(e) for e in (d.get('participants') or [])]
            for eid in selected: c.execute('INSERT OR IGNORE INTO task_participants(task_id,employee_id) VALUES(?,?)',(tid,eid))
            if selected:
                c.execute('DELETE FROM task_participants WHERE task_id=? AND employee_id NOT IN ('+','.join('?' for _ in selected)+')',[tid]+selected)
            else: c.execute('DELETE FROM task_participants WHERE task_id=?',(tid,))
            c.execute('UPDATE tasks SET is_group=? WHERE id=?',(1 if d.get('participants') else 0,tid))
        if 'reminder' in d: _save_reminder(c,tid,d.get('reminder') or {})
        c.execute("INSERT INTO task_events(task_id,employee_id,event_type,note,source) VALUES(?,?,'edited',?,'dashboard')",(tid,old['employee_id'],json.dumps({**{k:d[k] for k in d if k in allowed},'_old':{k:old[k] for k in ('title','priority','due_at','notes','employee_id')}},ensure_ascii=False)))
        if 'status' in d and d['status']!=old['status']:
            c.execute("UPDATE tasks SET started_at=CASE WHEN ? IN ('بدأت','قيد التنفيذ','تم') THEN COALESCE(started_at,CURRENT_TIMESTAMP) ELSE started_at END WHERE id=?",(d['status'],tid))
            c.execute("INSERT INTO task_events(task_id,employee_id,event_type,old_status,new_status,source) VALUES(?,?,'status',?,?,'dashboard')",(tid,old['employee_id'],old['status'],d['status']))
        if d.get('status')=='تم' and old['status']!='تم':record_completion(c,tid,old['employee_id'],d.get('completion',{}),'dashboard')
        audit(c,req,u,'task_update','task',tid,before=dict(old),after=d,reason=d.get('reason'))
    
        c.commit()
    except Exception:
        c.rollback();c.close();raise

    actor=u['full_name'] or u['username']
    old_emp=old['employee_id'] or None
    new_emp=(d.get('employee_id') if 'employee_id' in d else old_emp) or None

    if 'notes' in d and str(d.get('notes') or '').strip() != str(old['notes'] or '').strip() and u['role_code']!='employee':
        _notify_task_notes_update(c,tid,actor,d.get('notes'))

    # BOT-I-017: notify old and new employee when reassigned.
    if 'employee_id' in d and old_emp != new_emp:
        if old_emp:
            _notify_employee_id(c,old_emp,
              f"🔁 تم نقل المهمة منك\n\nالمهمة: {old['title']}\nلن تظهر لك كمهمة مسندة بعد الآن.",
              tid,'نقل المهمة من الموظف السابق')
        if new_emp:
            _notify_employee(c,tid,'تم إسناد المهمة إليك')
        c.execute("""INSERT INTO task_events(task_id,employee_id,event_type,note,source)
          VALUES(?,?,'reassigned',?,'dashboard')""",
          (tid,new_emp,f"من الموظف {old_emp or 'بدون'} إلى {new_emp or 'بدون'}"))

    # BOT-I-015: cancellation stops reminders and informs assigned employee.
    if 'status' in d and str(d.get('status') or '')=='ملغاة' and str(old['status'] or '')!='ملغاة':
        c.execute("UPDATE task_reminders SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE task_id=?",(tid,))
        target=new_emp or old_emp
        if target:
            _notify_employee_id(c,target,f"❌ تم إلغاء المهمة\n\nالمهمة: {old['title']}",tid,'إلغاء المهمة')

    # BOT-I-016: send a clear edit notice for important changes.
    changed=[]
    labels={'title':'اسم المهمة','priority':'الأولوية','due_at':'الموعد'}
    for k in ('title','priority','due_at'):
        if k in d and (d.get(k) or None)!=(old[k] or None):
            fmt=(lambda v: ar_when(v) or '—') if k=='due_at' else (lambda v: v or '—')
            changed.append(f"{labels[k]}: {fmt(old[k])} ← {fmt(d.get(k))}")
    if changed and new_emp:
        fresh=c.execute("SELECT * FROM tasks WHERE id=?",(tid,)).fetchone()
        text="✏️ تم تعديل المهمة\n\n"+f"المهمة: {fresh['title']}\n"+"\n".join(changed)
        _notify_employee_id(c,new_emp,text,tid,'تعديل المهمة',_telegram_task_keyboard(tid,fresh['status']))

    _touch_task_sync(c,tid,'server','update'); c.close(); return {'ok':True}

@app.delete('/api/tasks/{tid}')
def delete_task(tid:int, req:Request):
    u=need(req,'tasks.delete'); c=connect(); old=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
    if not old: c.close(); raise HTTPException(404,'المهمة غير موجودة')
    if not access.can_manage_task(c,u,old): c.close(); raise HTTPException(403,'هذه المهمة خارج نطاق فريقك')
    if old['archived_at']:
        # Already archived (a second click while the first request was still sending to Telegram):
        # do not notify, log or sync it again. 2026-10-31: up to 6 "cancelled" messages per task this way.
        c.close(); return {'ok':True,'archived':True,'already':True}
    if old['employee_id']:
        _notify_employee_id(c,int(old['employee_id']),f"❌ تم إلغاء / أرشفة المهمة\n\nالمهمة: {old['title']}",tid,'إلغاء المهمة')
    c.execute("UPDATE task_reminders SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE task_id=?",(tid,))
    c.execute('UPDATE tasks SET archived_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?',(tid,))
    c.execute("""INSERT INTO task_events(task_id,employee_id,event_type,note,source)
      VALUES(?,?,'cancelled','تم إلغاء / أرشفة المهمة','dashboard')""",(tid,old['employee_id']))
    audit(c,req,u,'task_archive','task',tid,before=dict(old),after={'archived':True})
    _touch_task_sync(c,tid,'server','archive'); c.close(); return {'ok':True,'archived':True}

@app.post('/api/tasks/{tid}/restore')
def restore_task(tid:int, req:Request):
    u=need(req,'tasks.edit'); c=connect(); old=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
    if not old: c.close(); raise HTTPException(404,'المهمة غير موجودة')
    if not access.can_manage_task(c,u,old): c.close(); raise HTTPException(403,'هذه المهمة خارج نطاق فريقك')
    c.execute('UPDATE tasks SET archived_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?',(tid,))
    if old['archived_at']:c.execute("INSERT INTO task_events(task_id,employee_id,event_type,note,source) VALUES(?,?,'restored','استرجاع من الأرشيف','dashboard')",(tid,old['employee_id']))
    audit(c,req,u,'task_restore','task',tid,before=dict(old),after={'archived':False}); _touch_task_sync(c,tid,'server','restore'); c.close(); return {'ok':True}

@app.patch('/api/tasks/{tid}/participants/{eid}/status')
async def participant_status(tid:int,eid:int,req:Request):
    d=await req.json();value=d.get('status');u=need_work_write(req,{'بدأت':'start','قيد التنفيذ':'progress','تم':'complete'}.get(value,'complete'))
    if value not in {'جديدة','بدأت','قيد التنفيذ','بانتظار','تم'}: raise HTTPException(400,'حالة غير صحيحة')
    if u['role_code']=='employee' and int(u['employee_id'] or -1)!=eid: raise HTTPException(403,'يمكنك تحديث حالتك فقط')
    if u['role_code']=='viewer': raise HTTPException(403,'غير مسموح بتعديل الحالة')
    c=connect()
    try:
        c.execute('BEGIN IMMEDIATE')
        scope=access.employee_scope(c,u)
        if scope is not None and eid not in scope: raise HTTPException(403,'هذا المشارك خارج نطاق فريقك')
        task=_task_accessible(c,u,tid)
        original=c.execute('SELECT status FROM task_participants WHERE task_id=? AND employee_id=?',(tid,eid)).fetchone()
        if not task or not original:raise HTTPException(404,'المشارك غير موجود')
        if task['archived_at'] or task['status']=='ملغاة':raise HTTPException(409,'المهمة ملغاة أو مؤرشفة')
        if original['status']==value:c.rollback();return {'ok':True,'already_recorded':True}
        if u['role_code']=='employee' and (value not in ('بدأت','قيد التنفيذ','تم') or original['status']=='تم' or (original['status']=='قيد التنفيذ' and value=='بدأت')):raise HTTPException(409,'لا يمكن الرجوع إلى حالة سابقة')
        c.execute('UPDATE task_participants SET status=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND employee_id=?',(value,tid,eid))
        c.execute("INSERT INTO task_events(task_id,employee_id,event_type,old_status,new_status,source) VALUES(?,?,'participant_status',?,?,'dashboard')",(tid,eid,original['status'],value))
        if value=='تم':record_completion(c,tid,eid,d.get('completion',{}),'dashboard')
        incomplete=c.execute("SELECT COUNT(*) FROM task_participants WHERE task_id=? AND status<>'تم'",(tid,)).fetchone()[0]
        new_status='تم' if incomplete==0 else 'قيد التنفيذ'
        c.execute("UPDATE tasks SET status=?,started_at=COALESCE(started_at,CURRENT_TIMESTAMP),completed_at=CASE WHEN ?='تم' THEN COALESCE(completed_at,CURRENT_TIMESTAMP) ELSE NULL END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(new_status,new_status,tid))
        if new_status!=task['status']:c.execute("INSERT INTO task_events(task_id,event_type,old_status,new_status,source) VALUES(?,'status',?,?,'dashboard')",(tid,task['status'],new_status))
        audit(c,req,u,'participant_status','task',tid,before={'employee_id':eid,'status':original['status']},after={'employee_id':eid,'status':value})
        sync_id=_touch_task_sync(c,tid,'server','participant_status')
        c.commit();_try_instant_sheet_push(c,sync_id)
        return {'ok':True,'all_completed':incomplete==0}
    except Exception:c.rollback();raise
    finally:c.close()

REMIND_GAP = 60  # seconds: a second click within a minute does not send a second reminder

def _remind_text(t, sender, note=''):
    lines=['🔔 تذكير بمهمة', f"📝 {t['title']}"]
    if t['due_at']:lines.append(f"⏰ الموعد: {ar_when(t['due_at'])}")
    lines.append(f"📌 الحالة: {t['status']}")
    if note:lines.append(f"💬 {note}")
    lines.append(f"👤 من: {sender}")
    from . import main as _main
    lines.append(f"🔗 {str(_main.PUBLIC_URL).rstrip('/')}/task/{t['id']}")
    return '\n'.join(lines)

@app.post('/api/tasks/{tid}/remind')
async def remind_task(tid:int, req:Request):
    """«🔔 ذكّر» on the task page (owner 2026-11-04): channel 'telegram' sends the task card again with a reminder
    title; 'whatsapp' returns a wa.me link with a ready reminder text (the manager presses send in WhatsApp).
    Both are logged as a 'reminded' task_event (not 'delegated'), which the follow-up group also shows."""
    u=need(req,'tasks.edit')
    try:d=await req.json()
    except Exception:d={}
    ch=str(d.get('channel') or '')
    if ch not in ('telegram','whatsapp'):raise HTTPException(400,'اختر Telegram أو واتساب')
    note=str(d.get('note') or '').strip()[:500]
    c=connect()
    try:
        t=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
        if not t:raise HTTPException(404,'المهمة غير موجودة')
        if not access.can_manage_task(c,u,t):raise HTTPException(403,'هذه المهمة خارج نطاق فريقك')
        if t['archived_at'] or t['status'] in ('تم','ملغاة'):raise HTTPException(400,'المهمة منتهية، ما في داعي للتذكير')
        e=c.execute('SELECT * FROM employees WHERE id=?',(t['employee_id'],)).fetchone() if t['employee_id'] else None
        if not e:raise HTTPException(400,'المهمة غير مسندة لموظف')
        sender=u['full_name'] or u['username']
        how='Telegram' if ch=='telegram' else 'واتساب'
        out={'ok':True,'channel':ch,'employee':e['name']}
        if ch=='telegram':
            if not (e['telegram_enabled'] and e['telegram_chat_id']):
                raise HTTPException(400,'هذا الموظف غير مربوط على Telegram؛ استخدم واتساب')
            last=c.execute("""SELECT 1 FROM task_events WHERE task_id=? AND event_type='reminded' AND source='telegram'
              AND created_at>=datetime('now',?)""",(tid,f'-{REMIND_GAP} seconds')).fetchone()
            if last:return {**out,'already':True}
            from .main import send, _remember_telegram_task_message
            r=send(e['telegram_chat_id'],_remind_text(t,sender,note),source_type='task_card',source_id=tid,
                   reply_markup=_telegram_task_keyboard(tid,t['status']))
            if not r.get('ok') and not r.get('queued'):raise HTTPException(502,r.get('error') or 'تعذر الإرسال على Telegram')
            _remember_telegram_task_message(c,tid,e['telegram_chat_id'],r)
            c.execute("""INSERT INTO notifications(task_id,employee_id,channel,kind,status,error,sent_at)
              VALUES(?,?,'telegram','تذكير من الإدارة',?,?,CURRENT_TIMESTAMP)""",
              (tid,e['id'],'queued' if r.get('queued') else 'sent',r.get('error')))
            out['queued']=bool(r.get('queued'))
        else:
            from .delegation import whatsapp_number
            from urllib.parse import quote
            num=whatsapp_number(e['whatsapp'])
            out['url']=('https://wa.me/'+num if num else 'https://wa.me/')+'?text='+quote(_remind_text(t,sender,note))
            out['has_number']=bool(num)
        c.execute("INSERT INTO task_events(task_id,employee_id,event_type,note,source) VALUES(?,?,'reminded',?,?)",
                  (tid,e['id'],f"{sender} ذكّر {e['name']} ({how})"+(f" — {note}" if note else ''),ch))
        audit(c,req,u,'task_remind','task',tid,after={'employee':e['name'],'channel':ch,'note':note})
        return out
    finally:c.close()

@app.post('/api/tasks/{tid}/telegram')
def resend_telegram(tid:int, req:Request):
    u=need(req,'tasks.edit'); c=connect(); old=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
    if not old: c.close(); raise HTTPException(404,'المهمة غير موجودة')
    if not access.can_manage_task(c,u,old): c.close(); raise HTTPException(403,'هذه المهمة خارج نطاق فريقك')
    r=_notify_employee(c,tid,'إعادة إرسال المهمة'); audit(c,req,u,'telegram_resend','task',tid,after=r); c.close(); return r

@app.patch('/api/tasks/{tid}/status')
async def change_status(tid:int, req:Request):
    d=await req.json(); status=d.get('status')
    u=need_work_write(req,{'بدأت':'start','قيد التنفيذ':'progress','تم':'complete'}.get(status,'complete'))
    if status not in {'جديدة','بدأت','قيد التنفيذ','بانتظار','تم','ملغاة'}: raise HTTPException(400,'حالة غير صحيحة')
    c=connect()
    try:
        c.execute('BEGIN IMMEDIATE')
        task=_task_accessible(c,u,tid)
        if not task: raise HTTPException(404)
        if u['role_code']!='employee': access.require_manage_task(c,u,task)
        if task['archived_at']:raise HTTPException(409,'المهمة مؤرشفة')
        if u['role_code']=='employee':
            if status not in {'بدأت','قيد التنفيذ','تم'}: raise HTTPException(403,'يمكنك بدء المهمة أو إنهاؤها فقط')
            if task['is_group']: raise HTTPException(409,'استخدم حالة مشاركتك في المهمة الجماعية')
        if task['status']==status:
            c.rollback();return {'ok':True,'already_recorded':True}
        if u['role_code']=='employee' and (task['status'] in ('تم','ملغاة') or (task['status']=='قيد التنفيذ' and status=='بدأت')):
            raise HTTPException(409,'لا يمكن الرجوع إلى حالة سابقة')
        c.execute("UPDATE tasks SET status=?,started_at=CASE WHEN ? IN ('بدأت','قيد التنفيذ','تم') THEN COALESCE(started_at,CURRENT_TIMESTAMP) ELSE started_at END,in_progress_at=CASE WHEN ?='قيد التنفيذ' THEN COALESCE(in_progress_at,CURRENT_TIMESTAMP) ELSE in_progress_at END,completed_at=CASE WHEN ?='تم' THEN COALESCE(completed_at,CURRENT_TIMESTAMP) ELSE NULL END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,status,status,status,tid))
        if status=='تم': record_completion(c,tid,task['employee_id'],d.get('completion',{}),'dashboard')
        c.execute("INSERT INTO task_events(task_id,employee_id,event_type,old_status,new_status,source) VALUES(?,?,'status',?,?,'dashboard')",(tid,task['employee_id'],task['status'],status))
        audit(c,req,u,'task_status','task',tid,before={'status':task['status']},after={'status':status})
        sync_id=_touch_task_sync(c,tid,'server','status')
        c.commit();_try_instant_sheet_push(c,sync_id)
    except Exception:
        c.rollback();raise
    finally: c.close()
    nc=connect()
    try:
        _refresh_telegram_task_messages(nc,tid)
    finally: nc.close()
    return {'ok':True,'already_recorded':False}


# ---------- task discussion / attachments / quick capture ----------
@app.post('/api/tasks/{tid}/comments')
async def add_task_comment(tid:int, req:Request):
    u=need_work_write(req,'comment'); d=await req.json(); body=str(d.get('body','')).strip()
    if not body: raise HTTPException(400,'التعليق فارغ')
    c=connect(); task=_task_accessible(c,u,tid)
    if not task: c.close(); raise HTTPException(404)
    author=u['full_name'] or u['username']
    c.execute('INSERT INTO task_comments(task_id,user_id,username_snapshot,body) VALUES(?,?,?,?)',
              (tid,u['user_id'],author,body))
    cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute("INSERT INTO task_events(task_id,employee_id,event_type,note,source) VALUES(?,?,'comment',?,'dashboard')",
              (tid,task['employee_id'],f"{author}: {body}"))
    tg={'ok':False,'skipped':True}
    if u['role_code']!='employee':
        tg=_notify_admin_comment(c,tid,author,body)
    audit(c,req,u,'task_comment','task',tid,after={'comment_id':cid,'body':body,'telegram':tg})
    c.close()
    return {'ok':True,'id':cid,'telegram':tg}

@app.post('/api/tasks/{tid}/attachments')
async def add_task_attachment(tid:int, req:Request, file:UploadFile=File(...)):
    u=need_work_write(req,'attach'); c=connect(); task=_task_accessible(c,u,tid)
    if not task: c.close(); raise HTTPException(404)
    if _is_audio(file.filename or '',file.content_type or ''):
        c.close(); raise HTTPException(400,'التسجيلات الصوتية غير مقبولة في النظام')
    data=await file.read()
    if len(data)>25*1024*1024:
        c.close(); raise HTTPException(413,'الحد الأقصى للمرفق 25 MB')
    original=_safe_filename(file.filename or 'file')
    ext=Path(original).suffix[:12]
    stored=f"{int(time.time()*1000)}_{u['user_id']}{ext}"
    dest=_attachment_dir(tid)/stored
    dest.write_bytes(data)
    rel=str(dest.relative_to(ROOT)).replace('\\','/')
    mime=file.content_type or 'application/octet-stream'
    kind='image' if mime.startswith('image/') else 'file'
    c.execute("""INSERT INTO task_attachments(task_id,user_id,original_name,stored_name,relative_path,mime_type,size_bytes,kind)
                 VALUES(?,?,?,?,?,?,?,?)""",(tid,u['user_id'],original,stored,rel,mime,len(data),kind))
    aid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    a=c.execute('SELECT * FROM task_attachments WHERE id=?',(aid,)).fetchone()
    tg={'ok':False,'skipped':True}
    if u['role_code']!='employee':
        tg=_notify_task_attachment(c,tid,u['full_name'] or u['username'],a)
    audit(c,req,u,'task_attachment','task',tid,after={
        'attachment_id':aid,'name':original,'kind':kind,'telegram':tg
    })
    c.close()
    return {'ok':True,'id':aid,'kind':kind,'telegram':tg}

@app.get('/api/attachments/{aid}')
def get_attachment(aid:int, req:Request):
    u=need(req,'tasks.view'); c=connect(); a=c.execute('SELECT * FROM task_attachments WHERE id=?',(aid,)).fetchone()
    if not a or _is_audio(a['original_name'],a['mime_type'],a['kind']) or not _task_accessible(c,u,a['task_id']):
        c.close(); raise HTTPException(404)
    path=ROOT/a['relative_path']; c.close()
    if not path.exists(): raise HTTPException(404,'الملف غير موجود')
    return FileResponse(path,media_type=a['mime_type'] or 'application/octet-stream',filename=a['original_name'])

@app.post('/api/quick/voice')
def quick_voice_task(req:Request):
    need(req,'tasks.create')
    raise HTTPException(410,'التسجيلات الصوتية غير مقبولة في النظام؛ استخدم اختصار iPhone أو اكتب المهمة')
