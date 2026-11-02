import threading, time, calendar
from datetime import datetime, timedelta
from .db import connect
from .work_settings import notices
from .telegram_queue import send_resilient as send, process_queue

STOP=threading.Event(); THREAD=None
DEFAULT_SETTINGS={'shift_due_soon_enabled':'1','shift_due_soon_minutes':'30','shift_overdue_enabled':'1','shift_start_tasks_enabled':'1','shift_end_summary_enabled':'1','shift_end_summary_minutes':'60'}

def _setting(c,key):
    r=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return r['value'] if r else DEFAULT_SETTINGS.get(key,'')

def _setting_bool(c,key): return str(_setting(c,key)).strip().lower() not in ('','0','false','no','off')
def _setting_int(c,key,default):
    try:return max(0,int(_setting(c,key)))
    except:return default

def _send_task(c,task_id,kind):
    from .main import _notify_employee
    row=c.execute('SELECT employee_id,status FROM tasks WHERE id=?',(task_id,)).fetchone()
    if not row or row['status'] in ('تم','ملغاة'):return False
    key='tasks' if 'جديدة' in kind else 'reminders'
    if not notices(c,row['employee_id'],key):return True
    return bool(_notify_employee(c,task_id,kind,notice_category=key).get('ok'))

def _parse(x):
    if not x:return None
    try:return datetime.fromisoformat(str(x).replace('Z','+00:00')).replace(tzinfo=None)
    except:return None

def _mins(hhmm):
    try:
        h,m=map(int,str(hhmm).split(':')[:2]); return h*60+m
    except:return 0

def _shift_context(e,now):
    if not e['shift_start'] or not e['shift_end'] or str(e['shift_type'] or 'بدون')=='بدون': return None
    start=_mins(e['shift_start']); end=_mins(e['shift_end']); nowm=now.hour*60+now.minute
    duration=(end-start+1440)%1440 or 1440; elapsed=(nowm-start+1440)%1440
    shift_date=now.date()
    if end<start and nowm<=end: shift_date=(now-timedelta(days=1)).date()
    wd=(shift_date.weekday()+1)%7
    days={x.strip() for x in str(e['work_days'] or '0,1,2,3,4,5,6').split(',') if x.strip()}
    return {'duration':duration,'elapsed':elapsed,'in_shift':elapsed<=duration,'shift_date':shift_date,'is_work_day':str(wd) in days}

def _claim(c,key,eid,kind,task_id=None):
    cur=c.execute("INSERT OR IGNORE INTO shift_dispatch_log(event_key,employee_id,task_id,kind,status) VALUES(?,?,?,?,'pending')",(key,eid,task_id,kind))
    return cur.rowcount==1

def _finish(c,key,ok,error=None):
    if ok:c.execute("UPDATE shift_dispatch_log SET status='sent',error=NULL,sent_at=CURRENT_TIMESTAMP WHERE event_key=?",(key,))
    else:c.execute('DELETE FROM shift_dispatch_log WHERE event_key=?',(key,))

def _notify_log(c,eid,kind,result,task_id=None):
    c.execute("INSERT INTO notifications(task_id,employee_id,channel,kind,status,error,sent_at) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)",(task_id,eid,'telegram',kind,'queued' if result.get('queued') else ('sent' if result.get('ok') else 'failed'),result.get('error')))

# المهام المفتوحة للموظف: المسندة له مباشرة + المهام الجماعية التي يشارك فيها ولم ينهِ جزءه.
# نفس الشرط المستخدم في زر اختبار الشيفت وأمر "مهامي" على Telegram، حتى تتطابق الرسائل الثلاث.
OPEN_TASKS_WHERE=("(employee_id=? OR EXISTS(SELECT 1 FROM task_participants p WHERE p.task_id=tasks.id AND p.employee_id=? AND p.status<>'تم'))"
                  " AND archived_at IS NULL AND status NOT IN ('تم','ملغاة')")

def _open_task_rows(c,eid):
    sql=("SELECT id,title,status,priority,due_at FROM tasks WHERE "+OPEN_TASKS_WHERE+
         " ORDER BY CASE priority WHEN 'عاجلة' THEN 1 WHEN 'عالية' THEN 2 WHEN 'عادي' THEN 3 ELSE 4 END,CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,due_at,id DESC")
    return c.execute(sql,(eid,eid)).fetchall()

def _send_open_tasks(c,e,prefix='📋 مهامك اليوم',rows=None):
    if rows is None: rows=_open_task_rows(c,e['id'])
    if not rows:return send(e['telegram_chat_id'],f"{prefix}\n\n✅ لا توجد لديك مهام مفتوحة حالياً.")
    lines=[prefix]
    for i,t in enumerate(rows,1):
        due=f" | ⏰ {t['due_at']}" if t['due_at'] else ''
        lines.append(f"{i}. {t['title']} — {t['status']} — {t['priority']}{due}")
    return send(e['telegram_chat_id'],'\n'.join(lines))

def _matching_shift_messages(c,e,typ,day):
    sql="SELECT * FROM shift_messages WHERE is_active=1 AND message_type=? AND (start_date IS NULL OR start_date='' OR date(start_date)<=date(?)) AND (end_date IS NULL OR end_date='' OR date(end_date)>=date(?)) ORDER BY id"
    rows=c.execute(sql,(typ,str(day),str(day))).fetchall(); out=[]
    for r in rows:
        a=r['audience_type'] or 'all'
        if a=='all' or (a=='shift' and (r['target_shift'] or '')==(e['shift_type'] or '')) or (a=='department' and (r['target_department'] or '').strip().casefold()==(e['department'] or '').strip().casefold()): out.append(r)
    return out

def _send_shift_messages(c,e,typ,day):
    label='بداية الشيفت' if typ=='start' else 'نهاية الشيفت'
    for m in _matching_shift_messages(c,e,typ,day):
        key=f"shift_message:{m['id']}:{e['id']}:{day}"
        if not _claim(c,key,e['id'],f'shift_{typ}_message'): continue
        r=send(e['telegram_chat_id'],m['message_text'])
        _notify_log(c,e['id'],f'رسالة {label}',r)
        _finish(c,key,bool(r.get('ok')),r.get('error'))

def process_shift_automation():
    c=connect(); now=datetime.now()
    try:
        sql="SELECT * FROM employees WHERE is_active=1 AND telegram_enabled=1 AND telegram_chat_id IS NOT NULL AND trim(telegram_chat_id)<>'' AND shift_type<>'بدون' AND shift_start IS NOT NULL AND shift_end IS NOT NULL"
        employees=c.execute(sql).fetchall()
        for e in employees:
            ctx=_shift_context(e,now)
            if not ctx or not ctx['in_shift']: continue
            day=ctx['shift_date'].isoformat()
            ex=c.execute("""SELECT * FROM shift_exceptions
              WHERE employee_id=? AND is_active=1
                AND date(start_date)<=date(?) AND date(end_date)>=date(?)
              ORDER BY id DESC LIMIT 1""",(e['id'],day,day)).fetchone()
            if ex and str(ex['exception_type'] or '')=='إجازة':
                continue
            if not ctx['is_work_day'] and not (ex and str(ex['exception_type'] or '')=='دوام استثنائي'):
                continue
            # رسائل بداية الشيفت مستقلة عن خيار إرسال مهام اليوم.
            # في النسخ السابقة كانت الرسالة لا تُرسل إذا كان shift_start_tasks_enabled=0.
            if notices(c,e['id'],'shift_start'): _send_shift_messages(c,e,'start',day)

            if _setting_bool(c,'shift_start_tasks_enabled') and notices(c,e['id'],'shift_start'):
                key=f"shift_start_tasks:{e['id']}:{day}"
                if _claim(c,key,e['id'],'shift_start_tasks'):
                    r=_send_open_tasks(c,e)
                    _notify_log(c,e['id'],'مهام بداية الشيفت',r)
                    _finish(c,key,bool(r.get('ok')),r.get('error'))
            due_soon=_setting_int(c,'shift_due_soon_minutes',30)
            tasks=c.execute("SELECT * FROM tasks WHERE employee_id=? AND archived_at IS NULL AND status NOT IN ('تم','ملغاة') AND due_at IS NOT NULL",(e['id'],)).fetchall()
            for t in tasks if notices(c,e['id'],'reminders') else []:
                due=_parse(t['due_at'])
                if not due: continue
                delta=(due-now).total_seconds()/60
                if _setting_bool(c,'shift_due_soon_enabled') and 0<=delta<=due_soon:
                    key=f"due_soon:{t['id']}:{t['due_at']}"
                    if _claim(c,key,e['id'],'due_soon',t['id']):
                        ok=_send_task(c,t['id'],f'الموعد يقترب — بقي تقريباً {max(0,int(delta))} دقيقة')
                        _finish(c,key,ok,'تعذر الإرسال' if not ok else None)
                elif _setting_bool(c,'shift_overdue_enabled') and delta<0:
                    key=f"overdue:{t['id']}:{day}"
                    if _claim(c,key,e['id'],'overdue',t['id']):
                        ok=_send_task(c,t['id'],'المهمة متأخرة')
                        _finish(c,key,ok,'تعذر الإرسال' if not ok else None)
            lead=_setting_int(c,'shift_end_summary_minutes',60)
            if ctx['elapsed']>=max(0,ctx['duration']-lead):
                # رسالة نهاية الشيفت مستقلة أيضاً عن خيار ملخص المهام.
                if notices(c,e['id'],'shift_end'): _send_shift_messages(c,e,'end',day)

                if _setting_bool(c,'shift_end_summary_enabled') and notices(c,e['id'],'shift_end'):
                    key=f"shift_end_summary:{e['id']}:{day}"
                    if _claim(c,key,e['id'],'shift_end_summary'):
                        rows=_open_task_rows(c,e['id']); count=len(rows)
                        if count:
                            r=_send_open_tasks(c,e,f'⏳ قبل نهاية الشيفت — بقي لديك {count} مهام غير مكتملة',rows)
                            _notify_log(c,e['id'],'ملخص نهاية الشيفت',r)
                            _finish(c,key,bool(r.get('ok')),r.get('error'))
                        else:
                            _finish(c,key,True,None)
    finally:c.close()

def _group_event_due(r, now):
    typ=str(r['recurrence_type'] or 'مرة واحدة')
    if r['start_date']:
        try:
            if now.date() < datetime.fromisoformat(str(r['start_date'])[:10]).date(): return (False,None)
        except: pass
    if r['end_date']:
        try:
            if now.date() > datetime.fromisoformat(str(r['end_date'])[:10]).date(): return (False,None)
        except: pass

    def hm():
        try:
            h,m=map(int,str(r['send_time'] or '09:00').split(':')[:2]); return h,m
        except:return 9,0

    if typ in ('مرة واحدة','تاريخ محدد'):
        at=_parse(r['specific_at'])
        if at and now>=at and not r['last_sent_at']:
            return (True, f"once:{r['id']}:{at.isoformat(timespec='minutes')}")
        return (False,None)

    h,m=hm()
    if (now.hour,now.minute)<(h,m): return (False,None)
    if typ=='يومي':
        return (True, f"daily:{r['id']}:{now.date().isoformat()}")
    if typ=='أسبوعي':
        weekday=(now.weekday()+1)%7
        if weekday==int(r['week_day'] or 0):
            iso=now.isocalendar()
            return (True, f"weekly:{r['id']}:{iso.year}-W{iso.week}")
        return (False,None)
    if typ=='شهري':
        max_day=calendar.monthrange(now.year,now.month)[1]
        target=min(max(1,int(r['month_day'] or 1)),max_day)
        if now.day==target:
            return (True, f"monthly:{r['id']}:{now.year}-{now.month:02d}")
        return (False,None)
    if typ=='كل X أيام':
        n=max(1,int(r['every_n_days'] or 1))
        base=None
        if r['start_date']:
            try: base=datetime.fromisoformat(str(r['start_date'])[:10]).date()
            except: base=None
        if base is None:
            try: base=datetime.fromisoformat(str(r['created_at'])[:10]).date()
            except: base=now.date()
        days=(now.date()-base).days
        if days>=0 and days % n == 0:
            return (True, f"every{n}:{r['id']}:{now.date().isoformat()}")
    return (False,None)

def process_group_reminders():
    c=connect(); now=datetime.now()
    try:
        rows=c.execute("""SELECT r.*,g.name group_name,g.chat_id,g.is_active group_active
          FROM group_reminders r JOIN telegram_groups g ON g.id=r.group_id
          WHERE r.is_active=1 AND g.is_active=1 AND g.chat_id IS NOT NULL AND trim(g.chat_id)<>''""").fetchall()
        for r in rows:
            due,key=_group_event_due(r,now)
            if not due or not key: continue
            exists=c.execute("SELECT 1 FROM group_dispatch_log WHERE event_key=?",(key,)).fetchone()
            if exists: continue
            c.execute("""INSERT OR IGNORE INTO group_dispatch_log(reminder_id,group_id,event_key,status)
                         VALUES(?,?,?,'pending')""",(r['id'],r['group_id'],key))
            prefix=(str(r['title']).strip()+"\n\n") if r['title'] else ''
            result=send(r['chat_id'],prefix+str(r['message_text']),source_type='group_reminder',source_id=key,dedupe_key='group:'+key)
            if result.get('ok'):
                if result.get('queued'):
                    c.execute("""UPDATE group_dispatch_log SET status='queued',error=? WHERE event_key=?""",
                              (str(result.get('error') or 'بانتظار إعادة المحاولة')[:1000],key))
                else:
                    c.execute("""UPDATE group_dispatch_log SET status='sent',sent_at=CURRENT_TIMESTAMP,error=NULL
                                 WHERE event_key=?""",(key,))
                c.execute("""UPDATE group_reminders SET last_run_key=?,last_sent_at=CASE WHEN ?=0 THEN CURRENT_TIMESTAMP ELSE last_sent_at END,
                             updated_at=CURRENT_TIMESTAMP WHERE id=?""",(key,1 if result.get('queued') else 0,r['id']))
                if str(r['recurrence_type'] or '') in ('مرة واحدة','تاريخ محدد'):
                    c.execute("UPDATE group_reminders SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",(r['id'],))
            else:
                c.execute("""UPDATE group_dispatch_log SET status='failed',error=? WHERE event_key=?""",
                          (str(result.get('error') or 'تعذر الإرسال')[:1000],key))
    finally:
        c.close()


def process_reminders():
    c=connect(); now=datetime.now()
    try:
        rows=c.execute("SELECT r.*,t.status FROM task_reminders r JOIN tasks t ON t.id=r.task_id WHERE r.is_active=1 AND t.status NOT IN ('تم','ملغاة')").fetchall()
        for r in rows:
            due=False; last=_parse(r['last_sent_at']); typ=r['type']
            if typ in ('مرة واحدة','تاريخ محدد'):
                at=_parse(r['specific_at'] if typ=='تاريخ محدد' else r['start_at']); due=bool(at and now>=at and not last)
            elif typ in ('كل ساعة','كل ساعتين'):
                start=_parse(r['start_at']) or now; hours=1 if typ=='كل ساعة' else 2; due=now>=start and (not last or (now-last).total_seconds()>=hours*3600)
            elif typ=='أسبوعياً':
                h,m=map(int,(r['weekly_time'] or '09:00').split(':')); weekday=(now.weekday()+1)%7; due=(weekday==int(r['weekly_day'] or 0) and (now.hour,now.minute)>=(h,m) and (not last or (now-last).total_seconds()>=6*86400))
            elif typ=='شهرياً':
                h,m=map(int,(r['monthly_time'] or '09:00').split(':')); max_day=calendar.monthrange(now.year,now.month)[1]; day=min(max(1,int(r['monthly_day'] or 1)),max_day); due=(now.day>=day and (now.hour,now.minute)>=(h,m) and (not last or (last.year,last.month)!=(now.year,now.month)))
            if due and _send_task(c,r['task_id'],'تذكير تلقائي'):
                c.execute('UPDATE task_reminders SET last_sent_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?',(r['id'],))
                if typ in ('مرة واحدة','تاريخ محدد'): c.execute('UPDATE task_reminders SET is_active=0 WHERE id=?',(r['id'],))
    finally:c.close()

def process_recurring():
    c=connect(); now=datetime.now(); key_day=now.strftime('%Y-%m-%d')
    try:
        for r in c.execute('SELECT * FROM recurring_tasks WHERE is_active=1').fetchall():
            if c.execute('SELECT 1 FROM bim_job_recurring j JOIN bim_job_profiles p ON p.id=j.job_id WHERE j.recurring_id=? AND p.is_active=0',(r['id'],)).fetchone():continue
            e=c.execute('SELECT * FROM employees WHERE id=? AND is_active=1',(r['employee_id'],)).fetchone()
            if not e:continue
            rule=c.execute('SELECT workdays_only FROM bim_recurring_rules WHERE recurring_id=?',(r['id'],)).fetchone()
            if rule and rule[0]:
                day=now.date().isoformat(); wd=str((now.weekday()+1)%7)
                ex=c.execute('SELECT exception_type FROM shift_exceptions WHERE employee_id=? AND is_active=1 AND date(start_date)<=date(?) AND date(end_date)>=date(?) ORDER BY id DESC LIMIT 1',(e['id'],day,day)).fetchone()
                if ex and ex[0]=='إجازة':continue
                if wd not in str(e['work_days'] or '').split(',') and not (ex and ex[0]=='دوام استثنائي'):continue
            try:h,m=map(int,(r['run_time'] or '09:00').split(':'))
            except:h,m=9,0
            if (now.hour,now.minute)<(h,m):continue
            due=False; key=''; weekday=(now.weekday()+1)%7
            if r['recurrence_type']=='يومي': due=True; key=key_day
            elif r['recurrence_type']=='أسبوعي': due=weekday==int(r['week_day'] or 0); key=f"{now.isocalendar().year}-W{now.isocalendar().week}"
            elif r['recurrence_type']=='شهري':
                max_day=calendar.monthrange(now.year,now.month)[1]; target=min(max(1,int(r['month_day'] or 1)),max_day); due=now.day==target; key=now.strftime('%Y-%m')
            if due and r['last_run_key']!=key:
                c.execute('BEGIN IMMEDIATE')
                claim=c.execute('UPDATE recurring_tasks SET last_run_key=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND is_active=1 AND (last_run_key IS NULL OR last_run_key<>?)',(key,r['id'],key))
                if not claim.rowcount: c.rollback();continue
                code=f"TASK-{int(time.time()*1000)}-{r['id']}"
                c.execute("INSERT INTO tasks(task_code,employee_id,title,notes,priority,created_by,source) VALUES(?,?,?,?,?,?,?)",(code,r['employee_id'],r['title'],r['notes'],r['priority'],r['created_by'],'recurring'))
                tid=c.execute('SELECT last_insert_rowid() x').fetchone()['x']
                c.execute('UPDATE recurring_tasks SET last_run_key=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(key,r['id']))
                c.execute("INSERT INTO task_reminders(task_id,type,is_active) VALUES(?, 'بدون',0)",(tid,))
                c.commit()
                _send_task(c,tid,'مهمة متكررة جديدة')
    finally:c.close()

def process_daily_backup():
    # Safety net: keep at least one database copy per day even if the Windows backup task stops.
    import os, sqlite3
    from pathlib import Path
    from .db import DB
    root=Path(os.environ.get('BIM_BACKUP_DIR',r'C:\BIM_Tasks_Backups'))
    root.mkdir(parents=True,exist_ok=True)
    day=datetime.now().strftime('%Y%m%d')
    if any(root.glob(f'BIM_Tasks_DB_{day}_*.db')) or any(root.glob(f'BIM_Tasks_DAILY_{day}.db')):return
    out=root/f'BIM_Tasks_DAILY_{day}.db'; tmp=root/f'.BIM_Tasks_DAILY_{day}.tmp'
    src=sqlite3.connect(str(DB),timeout=30); dst=sqlite3.connect(str(tmp))
    try:src.backup(dst)
    finally:dst.close();src.close()
    tmp.replace(out)
    for old in sorted(root.glob('BIM_Tasks_DAILY_*.db'),reverse=True)[60:]:
        try:old.unlink()
        except Exception:pass

def loop():
    while not STOP.wait(30):
        try:
            from .universal import process_templates
            try:process_templates()
            except Exception as template_error:print('Template worker error:',template_error)
            from .admin_notifications import process_admin_notifications
            try:process_daily_backup()
            except Exception as backup_error:print('Daily backup error:',backup_error)
            from .security_center import process_offsite_backup
            try:process_offsite_backup()
            except Exception as offsite_error:print('Off-site backup error:',offsite_error)
            process_admin_notifications(); process_queue(); process_reminders(); process_recurring(); process_shift_automation(); process_group_reminders()
            from .inbox import scan as inbox_scan
            inbox_scan(force=True)  # notification bell (never raises)
            from .supervisor_summary import process as process_supervisor_summary
            try:process_supervisor_summary()
            except Exception as summary_error:print('Supervisor summary error:',summary_error)
        except Exception as e: print('BIM task worker error:',e)

def start():
    global THREAD
    if THREAD and THREAD.is_alive(): return
    STOP.clear(); THREAD=threading.Thread(target=loop,daemon=True,name='bim-task-worker'); THREAD.start()
def stop(): STOP.set()
