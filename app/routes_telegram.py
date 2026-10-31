"""Telegram: webhook, bot actions, link codes, dashboard, reports, health and outgoing queue.
Split out of app/main.py (package code_split) with no change in behaviour; main.py imports this module."""
from __future__ import annotations
import hmac
import json
import re
import secrets
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from .db import connect
from .telegram_queue import send_resilient as send
from .telegram_service import answer_callback, get_me, get_webhook_info, set_webhook
from .universal import company, task_form
from .work_settings import permits, profile
from . import main as _main  # settings that tests or plugins may change at run time are read from main
from .main import (
    _is_audio, _refresh_telegram_task_messages, _remember_telegram_task_message, _tg_employee,
    _tg_send_instructions, _tg_send_my_tasks, _tg_set_status, _tg_task_for_employee, app, audit, ctx, need,
    perms, templates
)

# ---------- Telegram Phase 1 ----------
@app.get('/telegram-center', response_class=HTMLResponse)
def telegram_center_page(req:Request):
    need(req,'employees.edit')
    return templates.TemplateResponse(request=req,name='telegram_center.html',context=ctx(req,'مركز Telegram'))

@app.get('/api/telegram/status')
def telegram_status_api(req:Request):
    need(req,'employees.edit')
    me=get_me(); wh=get_webhook_info(); c=connect()
    linked=c.execute("SELECT COUNT(*) n FROM employees WHERE is_active=1 AND telegram_chat_id IS NOT NULL AND trim(telegram_chat_id)<>''").fetchone()['n']
    total=c.execute("SELECT COUNT(*) n FROM employees WHERE is_active=1").fetchone()['n']
    c.close()
    return {'ok':bool(me.get('ok')),'bot':me.get('result'),'bot_error':me.get('error'),'webhook':wh.get('result') if wh.get('ok') else None,'webhook_error':wh.get('error'),'linked':linked,'total':total}

@app.post('/api/telegram/setup-webhook')
def telegram_setup_webhook(req:Request):
    u=need(req,'security.manage')
    if not _main.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(500,'Webhook secret غير مضبوط')
    c=connect()
    try:base_url=company(c)['public_url'] or _main.PUBLIC_URL
    finally:c.close()
    url=base_url.rstrip('/')+'/api/telegram/webhook'
    r=set_webhook(url,_main.TELEGRAM_WEBHOOK_SECRET)
    c=connect(); audit(c,req,u,'telegram_webhook_setup','system',None,after={'url':url,'ok':r.get('ok')}); c.close()
    if not r.get('ok'):
        raise HTTPException(502,r.get('error') or 'تعذر تفعيل Webhook')
    return {'ok':True,'url':url}

def _guard_manager_telegram(c,u,eid):
    # The Telegram chat of a manager receives login codes, so only the administrator (or that deputy) may change it.
    owners=c.execute("SELECT u.id,r.code FROM users u JOIN roles r ON r.id=u.role_id WHERE u.employee_id=? AND u.is_active=1 AND u.deleted_at IS NULL AND r.code IN ('admin','deputy')",(eid,)).fetchall()
    if owners and 'security.manage' not in perms(u['user_id']) and not all(o['code']=='deputy' and o['id']==u['user_id'] for o in owners):
        raise HTTPException(403,'ربط Telegram لحساب إدارة يتم من المدير فقط')

@app.post('/api/employees/{eid}/telegram-link')
def employee_telegram_link(eid:int,req:Request):
    u=need(req,'employees.edit'); c=connect()
    e=c.execute('SELECT * FROM employees WHERE id=?',(eid,)).fetchone()
    if not e:
        c.close(); raise HTTPException(404,'الموظف غير موجود')
    try: _guard_manager_telegram(c,u,eid)
    except HTTPException: c.close(); raise
    code=secrets.token_urlsafe(18)
    c.execute("UPDATE employees SET telegram_link_code=?,telegram_link_expires_at=datetime('now','+24 hours'),updated_at=CURRENT_TIMESTAMP WHERE id=?",(code,eid))
    me=get_me()
    if not me.get('ok'):
        c.close(); raise HTTPException(502,me.get('error') or 'تعذر قراءة بيانات البوت')
    username=(me.get('result') or {}).get('username')
    if not username:
        c.close(); raise HTTPException(500,'اسم مستخدم البوت غير متوفر')
    link=f'https://t.me/{username}?start={code}'
    audit(c,req,u,'telegram_link_generate','employee',eid,after={'expires_hours':24}); c.close()
    return {'ok':True,'link':link,'expires_hours':24}

@app.post('/api/employees/{eid}/telegram-unlink')
def employee_telegram_unlink(eid:int, req:Request):
    u=need(req,'employees.edit'); c=connect()
    e=c.execute('SELECT * FROM employees WHERE id=?',(eid,)).fetchone()
    if not e:
        c.close(); raise HTTPException(404,'الموظف غير موجود')
    try: _guard_manager_telegram(c,u,eid)
    except HTTPException: c.close(); raise
    before=dict(e)
    c.execute("""UPDATE employees SET telegram_chat_id=NULL,telegram_link_code=NULL,
      telegram_link_expires_at=NULL,telegram_linked_at=NULL,updated_at=CURRENT_TIMESTAMP
      WHERE id=?""",(eid,))
    audit(c,req,u,'telegram_unlink','employee',eid,before=before,
          after={'telegram_chat_id':None})
    c.close()
    return {'ok':True}

@app.post('/api/telegram/webhook')
async def telegram_webhook(req:Request):
    # Without the secret anyone could post fake updates and act as any linked employee.
    if not _main.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(503,'Telegram webhook secret غير مضبوط')
    if not hmac.compare_digest(req.headers.get('x-telegram-bot-api-secret-token',''),_main.TELEGRAM_WEBHOOK_SECRET):
        raise HTTPException(401,'Telegram secret غير صحيح')
    update=await req.json(); update_id=update.get('update_id'); c=connect()
    if update_id is not None:
        if c.execute('SELECT 1 FROM telegram_processed_updates WHERE update_id=?',(int(update_id),)).fetchone():
            c.close(); return {'ok':True,'duplicate':True}
        claim=c.execute("INSERT INTO bim_webhook_claims(update_id,claimed_at) VALUES(?,CURRENT_TIMESTAMP) ON CONFLICT(update_id) DO UPDATE SET claimed_at=CURRENT_TIMESTAMP WHERE claimed_at<datetime('now','-5 minutes')",(int(update_id),))
        if not claim.rowcount:c.close();raise HTTPException(503,'تحديث قيد المعالجة')
    try:
        msg=update.get('message') or {}; cb=update.get('callback_query') or {}
        if msg:
            chat_obj=msg.get('chat') or {}
            chat=chat_obj.get('id'); chat_type=str(chat_obj.get('type') or '')
            text=str(msg.get('text') or '').strip()
            if not chat: return {'ok':True}
            if chat_type in ('group','supergroup') and text.startswith('/linkgroup'):
                parts=text.split(maxsplit=1); code=parts[1].strip() if len(parts)>1 else ''
                if not code:
                    send(str(chat),'أرسل أمر الربط بالشكل: /linkgroup CODE')
                    return {'ok':True}
                g=c.execute("""SELECT * FROM telegram_groups WHERE link_code=? AND is_active=1
                  AND link_expires_at IS NOT NULL AND link_expires_at>=CURRENT_TIMESTAMP""",(code,)).fetchone()
                if not g:
                    send(str(chat),'رمز ربط المجموعة غير صالح أو انتهت مدته.')
                    return {'ok':True}
                other=c.execute('SELECT id,name FROM telegram_groups WHERE chat_id=? AND id<>?',(str(chat),g['id'])).fetchone()
                if other:
                    send(str(chat),'هذه المجموعة مرتبطة مسبقاً بسجل آخر في BIM Tasks.')
                    return {'ok':True}
                title=str(chat_obj.get('title') or g['name'] or '').strip()
                c.execute("""UPDATE telegram_groups SET chat_id=?,telegram_title=?,linked_at=CURRENT_TIMESTAMP,
                  link_code=NULL,link_expires_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""",(str(chat),title,g['id']))
                send(str(chat),f"✅ تم ربط المجموعة بنظام BIM Tasks\nالاسم: {g['name']}")
                return {'ok':True}
            if chat_type in ('group','supergroup'):
                return {'ok':True}
            if text.startswith('/start'):
                parts=text.split(maxsplit=1); code=parts[1].strip() if len(parts)>1 else ''; existing=_tg_employee(c,chat)
                if existing:
                    send(str(chat),f"أهلاً {existing['name']} 👋\nتم ربط حسابك مسبقاً.",reply_markup={'inline_keyboard':[[{'text':'📋 مهامي اليوم','callback_data':'my_tasks'}]]})
                    return {'ok':True}
                if not code:
                    send(str(chat),'مرحباً بك في BIM Tasks.\nلربط حسابك استخدم رابط الربط الذي ترسله لك الإدارة.')
                    return {'ok':True}
                e=c.execute("SELECT * FROM employees WHERE telegram_link_code=? AND is_active=1 AND telegram_link_expires_at IS NOT NULL AND telegram_link_expires_at>=CURRENT_TIMESTAMP",(code,)).fetchone()
                if not e:
                    send(str(chat),'رابط الربط غير صالح أو انتهت مدته. اطلب رابطاً جديداً من الإدارة.')
                    return {'ok':True}
                other=c.execute('SELECT id FROM employees WHERE telegram_chat_id=? AND id<>?',(str(chat),e['id'])).fetchone()
                if other:
                    send(str(chat),'هذا حساب Telegram مرتبط بموظف آخر. راجع الإدارة.')
                    return {'ok':True}
                c.execute("UPDATE employees SET telegram_chat_id=?,telegram_linked_at=CURRENT_TIMESTAMP,telegram_link_code=NULL,telegram_link_expires_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",(str(chat),e['id']))
                send(str(chat),f"✅ تم ربط Telegram بنجاح\nالموظف: {e['name']}",reply_markup={'inline_keyboard':[[{'text':'📋 مهامي اليوم','callback_data':'my_tasks'}]]})
                return {'ok':True}
            e=_tg_employee(c,chat)
            if not e:
                send(str(chat),'حسابك غير مربوط بموظف في BIM Tasks. استخدم رابط الربط من الإدارة.')
                return {'ok':True}
            pending=c.execute('SELECT * FROM telegram_pending_actions WHERE chat_id=?',(str(chat),)).fetchone()
            doc=msg.get('document') or {}
            if any(msg.get(k) for k in ('voice','audio','video_note')) or (doc and _is_audio(doc.get('file_name') or '',doc.get('mime_type') or '')):
                send(str(chat),'🚫 الرسائل الصوتية غير مقبولة. اكتب ملاحظتك نصاً، أو أرسل صورة أو ملفاً.')
                return {'ok':True}
            if pending and pending['action']=='note' and any(msg.get(k) for k in ('photo','document','video')):
                from .telegram_incoming import save_note_media
                save_note_media(c,e,pending,msg);return {'ok':True}
            if pending and text:
                if text.lower() in ('/cancel','الغاء','إلغاء'):
                    c.execute('DELETE FROM telegram_pending_actions WHERE chat_id=?',(str(chat),)); send(str(chat),'تم إلغاء إضافة الملاحظة.')
                    return {'ok':True}
                task=_tg_task_for_employee(c,int(pending['task_id']),e['id'])
                if not task:
                    c.execute('DELETE FROM telegram_pending_actions WHERE chat_id=?',(str(chat),)); send(str(chat),'تعذر العثور على المهمة.')
                    return {'ok':True}
                if pending['action']!='completion' and not permits(c,e['id'],'comment'):return {'ok':True}
                if pending['action']=='completion':
                    entry=c.execute('SELECT * FROM bim_completion_entries WHERE task_id=? AND employee_id=? ORDER BY id DESC LIMIT 1',(task['id'],e['id'])).fetchone()
                    if entry:
                        fields=json.loads(entry['form_json']);lines=text.splitlines();values={key:lines[i] if i<len(lines) else '' for i,key in enumerate(fields)}
                        c.execute('UPDATE bim_completion_entries SET values_json=? WHERE id=?',(json.dumps(values,ensure_ascii=False),entry['id']))
                    c.execute('DELETE FROM telegram_pending_actions WHERE chat_id=?',(str(chat),));send(str(chat),'تم حفظ بيانات الإنجاز. المهمة مكتملة مسبقاً.');return {'ok':True}
                c.execute('INSERT INTO task_comments(task_id,user_id,username_snapshot,body) VALUES(?,NULL,?,?)',(task['id'],e['name'],text))
                c.execute("INSERT INTO task_events(task_id,employee_id,event_type,note,source) VALUES(?,?,'note',?,'telegram')",(task['id'],e['id'],text))
                c.execute('DELETE FROM telegram_pending_actions WHERE chat_id=?',(str(chat),)); send(str(chat),'✅ تم حفظ المشكلة / الملاحظة على المهمة.')
                return {'ok':True}
            if text in ('تعليمات عملي','/instructions'):
                _tg_send_instructions(c,e);return {'ok':True}
            if text in ('مهامي اليوم','مهامي','/tasks','/mytasks'):
                _tg_send_my_tasks(c,e); return {'ok':True}
            send(str(chat),'اختر من أزرار رسالة المهمة أو استخدم «مهامي اليوم».',reply_markup={'inline_keyboard':[[{'text':'📋 مهامي اليوم','callback_data':'my_tasks'}]]})
            return {'ok':True}
        if cb:
            cbid=cb.get('id'); data=str(cb.get('data') or ''); chat_id=str((cb.get('from') or {}).get('id') or ''); e=_tg_employee(c,chat_id)
            if not e:
                answer_callback(cbid,'الحساب غير مربوط',True); return {'ok':True}
            if data in ('work_instructions','instructions_read'):
                p=profile(c,e['id'])
                if data=='instructions_read':
                    c.execute("INSERT INTO bim_instruction_reads(employee_id,signature) VALUES(?,?) ON CONFLICT(employee_id) DO UPDATE SET signature=excluded.signature,read_at=CURRENT_TIMESTAMP",(e['id'],p['instructions_signature']))
                    answer_callback(cbid,'تم تسجيل قراءة التعليمات');return {'ok':True}
                answer_callback(cbid,'تعليمات عملي');_tg_send_instructions(c,e);return {'ok':True}
            if data=='my_tasks':
                answer_callback(cbid,'جاري عرض مهامك'); _tg_send_my_tasks(c,e); return {'ok':True}
            m=re.match(r'^task:(\d+):(started|progress|done|note)$',data)
            if not m:
                answer_callback(cbid,'أمر غير معروف'); return {'ok':True}
            tid=int(m.group(1)); action=m.group(2); task=_tg_task_for_employee(c,tid,e['id'])
            if not task:
                answer_callback(cbid,'هذه المهمة ليست مخصصة لك',True); return {'ok':True}
            if not permits(c,e['id'],{'started':'start','progress':'progress','done':'complete','note':'comment'}[action]):
                answer_callback(cbid,'هذه العملية غير مفعلة لك',True);return {'ok':True}
            if action=='note':
                c.execute("INSERT INTO telegram_pending_actions(chat_id,employee_id,task_id,action) VALUES(?,?,?,'note') ON CONFLICT(chat_id) DO UPDATE SET employee_id=excluded.employee_id,task_id=excluded.task_id,action='note',created_at=CURRENT_TIMESTAMP",(chat_id,e['id'],tid))
                answer_callback(cbid,'اكتب المشكلة أو الملاحظة الآن')
                send(chat_id,f"⚠️ اكتب الآن المشكلة أو الملاحظة الخاصة بالمهمة:\n{task['title']}\n\nللإلغاء أرسل /cancel")
                return {'ok':True}
            new_status={'started':'بدأت','progress':'قيد التنفيذ','done':'تم'}[action]
            changed=_tg_set_status(c,task,e,new_status)
            if changed and new_status=='تم':
                fields=task_form(c,tid,e['id'])
                if fields is None:fields=profile(c,e['id'])['config']['completion_fields']
                if fields:
                    c.execute("INSERT INTO telegram_pending_actions(chat_id,employee_id,task_id,action) VALUES(?,?,?,'completion') ON CONFLICT(chat_id) DO UPDATE SET employee_id=excluded.employee_id,task_id=excluded.task_id,action='completion',created_at=CURRENT_TIMESTAMP",(chat_id,e['id'],tid))
                    send(chat_id,'✅ تم الإنجاز فوراً. يمكنك إرسال البيانات الاختيارية التالية، قيمة في كل سطر بالترتيب، أو /cancel للتخطي:\n'+'\n'.join(fields))
            answer_callback(cbid,'تم تحديث الحالة' if changed else 'الحالة مسجلة مسبقاً')
            fresh=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone(); message=cb.get('message') or {}
            if message.get('message_id'):
                _remember_telegram_task_message(c,tid,chat_id,{'ok':True,'message_id':message['message_id']})
                _refresh_telegram_task_messages(c,tid)
            return {'ok':True}
    except Exception:
        if update_id is not None:c.execute('DELETE FROM bim_webhook_claims WHERE update_id=?',(int(update_id),))
        raise
    else:
        pass
    finally:
        # Returning from try still executes finally; only mark success if no exception.
        import sys
        if update_id is not None and sys.exc_info()[0] is None:
            c.execute('INSERT OR IGNORE INTO telegram_processed_updates(update_id) VALUES(?)',(int(update_id),))
            c.execute('DELETE FROM bim_webhook_claims WHERE update_id=?',(int(update_id),))
        c.close()
    return {'ok':True}

# ---------- Telegram Dashboard / Reports ----------
@app.get('/telegram-dashboard', response_class=HTMLResponse)
def telegram_dashboard_page(req:Request):
    need(req,'employees.edit')
    return templates.TemplateResponse(request=req,name='telegram_dashboard.html',context=ctx(req,'Telegram Dashboard'))

@app.get('/api/telegram-dashboard')
def api_telegram_dashboard(req:Request, days:int=7):
    need(req,'employees.edit')
    days=min(max(int(days or 7),1),90)
    c=connect()
    try:
        linked_employees=c.execute("""SELECT COUNT(*) n FROM employees
          WHERE is_active=1 AND telegram_enabled=1
            AND telegram_chat_id IS NOT NULL AND trim(telegram_chat_id)<>''""").fetchone()['n']
        unlinked_employees=c.execute("""SELECT COUNT(*) n FROM employees
          WHERE is_active=1 AND telegram_enabled=1
            AND (telegram_chat_id IS NULL OR trim(telegram_chat_id)='')""").fetchone()['n']
        linked_groups=c.execute("""SELECT COUNT(*) n FROM telegram_groups
          WHERE is_active=1 AND chat_id IS NOT NULL AND trim(chat_id)<>''""").fetchone()['n']
        active_group_reminders=c.execute("""SELECT COUNT(*) n FROM group_reminders
          WHERE is_active=1""").fetchone()['n']

        q={x['status']:x['n'] for x in c.execute(
            "SELECT status,COUNT(*) n FROM telegram_outbox GROUP BY status"
        ).fetchall()}

        notif_rows=c.execute("""SELECT status,COUNT(*) n FROM notifications
          WHERE channel='telegram'
            AND date(COALESCE(sent_at,created_at))>=date('now','localtime',?)
          GROUP BY status""",(f'-{days-1} days',)).fetchall()
        notif={x['status']:x['n'] for x in notif_rows}

        group_rows=c.execute("""SELECT status,COUNT(*) n FROM group_dispatch_log
          WHERE date(COALESCE(sent_at,created_at))>=date('now','localtime',?)
          GROUP BY status""",(f'-{days-1} days',)).fetchall()
        group_counts={x['status']:x['n'] for x in group_rows}

        daily=[dict(x) for x in c.execute("""WITH RECURSIVE d(day) AS (
            SELECT date('now','localtime',?)
            UNION ALL
            SELECT date(day,'+1 day') FROM d WHERE day < date('now','localtime')
          )
          SELECT d.day,
            COALESCE((SELECT COUNT(*) FROM notifications n
              WHERE n.channel='telegram' AND n.status='sent'
                AND date(COALESCE(n.sent_at,n.created_at))=d.day),0) notification_sent,
            COALESCE((SELECT COUNT(*) FROM notifications n
              WHERE n.channel='telegram' AND n.status='failed'
                AND date(COALESCE(n.sent_at,n.created_at))=d.day),0) notification_failed,
            COALESCE((SELECT COUNT(*) FROM telegram_outbox o
              WHERE o.status='sent' AND date(o.sent_at)=d.day),0) queue_recovered,
            COALESCE((SELECT COUNT(*) FROM group_dispatch_log g
              WHERE g.status='sent' AND date(COALESCE(g.sent_at,g.created_at))=d.day),0) group_sent
          FROM d ORDER BY d.day""",(f'-{days-1} days',)).fetchall()]

        employees=[dict(x) for x in c.execute("""SELECT id,name,department,shift_type,
          telegram_chat_id,telegram_enabled,is_active,telegram_linked_at
          FROM employees
          WHERE is_active=1 AND telegram_enabled=1
          ORDER BY CASE WHEN telegram_chat_id IS NULL OR trim(telegram_chat_id)='' THEN 0 ELSE 1 END,
                   department,name""").fetchall()]

        groups=[dict(x) for x in c.execute("""SELECT g.id,g.name,g.department,g.chat_id,g.telegram_title,g.is_active,g.linked_at,
          (SELECT COUNT(*) FROM group_reminders r WHERE r.group_id=g.id AND r.is_active=1) active_reminders,
          (SELECT MAX(sent_at) FROM group_dispatch_log l WHERE l.group_id=g.id AND l.status='sent') last_sent_at,
          (SELECT COUNT(*) FROM group_dispatch_log l WHERE l.group_id=g.id AND l.status='failed'
             AND date(l.created_at)>=date('now','localtime',?)) recent_failures
          FROM telegram_groups g
          ORDER BY g.is_active DESC,g.name""",(f'-{days-1} days',)).fetchall()]

        failures=[dict(x) for x in c.execute("""SELECT 'queue' source,
            id ref_id,chat_id target,source_type kind,last_error error,created_at
          FROM telegram_outbox
          WHERE status='failed'
          UNION ALL
          SELECT 'group' source,l.id ref_id,COALESCE(g.name,'—') target,
            COALESCE(r.title,'تذكير جماعي') kind,l.error,l.created_at
          FROM group_dispatch_log l
          LEFT JOIN telegram_groups g ON g.id=l.group_id
          LEFT JOIN group_reminders r ON r.id=l.reminder_id
          WHERE l.status='failed'
          ORDER BY created_at DESC LIMIT 100""").fetchall()]
        today_status={
          'started':c.execute("SELECT COUNT(*) n FROM task_events WHERE event_type='status' AND new_status='بدأت' AND date(created_at,'localtime')=date('now','localtime')").fetchone()['n'],
          'in_progress':c.execute("SELECT COUNT(*) n FROM task_events WHERE event_type='status' AND new_status='قيد التنفيذ' AND date(created_at,'localtime')=date('now','localtime')").fetchone()['n'],
          'completed':c.execute("SELECT COUNT(*) n FROM task_events WHERE event_type='status' AND new_status='تم' AND date(created_at,'localtime')=date('now','localtime')").fetchone()['n'],
          'overdue':c.execute("SELECT COUNT(*) n FROM tasks WHERE archived_at IS NULL AND status NOT IN ('تم','ملغاة') AND due_at IS NOT NULL AND datetime(due_at)<datetime('now','localtime')").fetchone()['n'],
          'problems':c.execute("SELECT COUNT(*) n FROM task_events WHERE event_type='note' AND source='telegram' AND date(created_at,'localtime')=date('now','localtime')").fetchone()['n'],
          'groups_today':c.execute("SELECT COUNT(DISTINCT reminder_id) n FROM group_dispatch_log WHERE date(created_at,'localtime')=date('now','localtime')").fetchone()['n']
        }
    finally:
        c.close()

    bot={'ok':False}; webhook={'ok':False}
    try: bot=get_me()
    except Exception as e: bot={'ok':False,'error':str(e)}
    try: webhook=get_webhook_info()
    except Exception as e: webhook={'ok':False,'error':str(e)}

    return {
      'days':days,
      'bot':bot,'webhook':webhook,
      'linked_employees':linked_employees,
      'unlinked_employees':unlinked_employees,
      'linked_groups':linked_groups,
      'active_group_reminders':active_group_reminders,
      'queue':{
        'pending':q.get('pending',0),
        'retrying':q.get('retrying',0),
        'failed':q.get('failed',0),
        'sent':q.get('sent',0)
      },
      'notifications':notif,
      'group_dispatch':group_counts,
      'daily':daily,
      'employees':employees,
      'groups':groups,
      'failures':failures,
      'today_status':today_status,
      'bot_identity':(bot.get('result') or {}) if bot.get('ok') else {}
    }

@app.get('/api/telegram-report.csv')
def telegram_report_csv(req:Request, days:int=30):
    need(req,'employees.edit')
    days=min(max(int(days or 30),1),365)
    c=connect()
    try:
        rows=[dict(x) for x in c.execute("""SELECT
          COALESCE(sent_at,created_at) event_time,
          'individual' scope,
          CAST(employee_id AS TEXT) target,
          kind,
          status,
          COALESCE(error,'') error
          FROM notifications
          WHERE channel='telegram'
            AND date(COALESCE(sent_at,created_at))>=date('now','localtime',?)
          UNION ALL
          SELECT COALESCE(l.sent_at,l.created_at) event_time,
          'group' scope,
          COALESCE(g.name,CAST(l.group_id AS TEXT)) target,
          COALESCE(r.title,'group reminder') kind,
          l.status,
          COALESCE(l.error,'') error
          FROM group_dispatch_log l
          LEFT JOIN telegram_groups g ON g.id=l.group_id
          LEFT JOIN group_reminders r ON r.id=l.reminder_id
          WHERE date(COALESCE(l.sent_at,l.created_at))>=date('now','localtime',?)
          UNION ALL
          SELECT COALESCE(sent_at,created_at) event_time,
          'queue' scope,
          chat_id target,
          source_type kind,
          status,
          COALESCE(last_error,'') error
          FROM telegram_outbox
          WHERE date(COALESCE(sent_at,created_at))>=date('now','localtime',?)
          ORDER BY event_time DESC""",
          (f'-{days-1} days',f'-{days-1} days',f'-{days-1} days')).fetchall()]
    finally:
        c.close()

    import csv, io
    out=io.StringIO()
    out.write('\ufeff')
    w=csv.writer(out)
    w.writerow(['الوقت','النطاق','الهدف','النوع','الحالة','الخطأ'])
    for x in rows:
        w.writerow([x.get('event_time',''),x.get('scope',''),x.get('target',''),
                    x.get('kind',''),x.get('status',''),x.get('error','')])
    return Response(content=out.getvalue(),media_type='text/csv; charset=utf-8',
      headers={'Content-Disposition':f'attachment; filename="telegram_report_{days}d.csv"'})


# ---------- Telegram Health / Queue / Reports ----------
@app.get('/telegram-health', response_class=HTMLResponse)
def telegram_health_page(req:Request):
    need(req,'employees.edit')
    return templates.TemplateResponse(request=req,name='telegram_health.html',context=ctx(req,'Telegram Health'))

@app.get('/api/telegram-queue')
def telegram_queue_list(req:Request, limit:int=200):
    need(req,'employees.edit'); c=connect()
    rows=[dict(x) for x in c.execute("""SELECT * FROM telegram_outbox
      ORDER BY CASE status WHEN 'failed' THEN 1 WHEN 'retrying' THEN 2 WHEN 'pending' THEN 3 ELSE 4 END,
      id DESC LIMIT ?""",(min(max(int(limit),1),500),))]
    c.close(); return rows

@app.post('/api/telegram-queue/{qid}/retry')
def telegram_queue_retry(qid:int, req:Request):
    u=need(req,'employees.edit'); c=connect()
    row=c.execute('SELECT * FROM telegram_outbox WHERE id=?',(qid,)).fetchone()
    if not row: c.close(); raise HTTPException(404,'عنصر الطابور غير موجود')
    c.execute("""UPDATE telegram_outbox SET status='pending',attempts=0,next_attempt_at=CURRENT_TIMESTAMP,
      last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""",(qid,))
    audit(c,req,u,'telegram_queue_retry','telegram_outbox',qid,before=dict(row),after={'status':'pending'})
    c.close(); return {'ok':True}

@app.post('/api/telegram-queue/retry-failed')
def telegram_queue_retry_failed(req:Request):
    u=need(req,'employees.edit'); c=connect()
    count=c.execute("SELECT COUNT(*) n FROM telegram_outbox WHERE status='failed'").fetchone()['n']
    c.execute("""UPDATE telegram_outbox SET status='pending',attempts=0,next_attempt_at=CURRENT_TIMESTAMP,
      last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE status='failed'""")
    audit(c,req,u,'telegram_queue_retry_failed','telegram_outbox',None,after={'count':count})
    c.close(); return {'ok':True,'count':count}

@app.get('/api/telegram-health')
def api_telegram_health(req:Request):
    need(req,'employees.edit'); c=connect()
    linked_employees=c.execute("""SELECT COUNT(*) n FROM employees
      WHERE is_active=1 AND telegram_enabled=1 AND telegram_chat_id IS NOT NULL AND trim(telegram_chat_id)<>''""").fetchone()['n']
    unlinked_employees=c.execute("""SELECT COUNT(*) n FROM employees
      WHERE is_active=1 AND telegram_enabled=1 AND (telegram_chat_id IS NULL OR trim(telegram_chat_id)='')""").fetchone()['n']
    linked_groups=c.execute("""SELECT COUNT(*) n FROM telegram_groups
      WHERE is_active=1 AND chat_id IS NOT NULL AND trim(chat_id)<>''""").fetchone()['n']
    q={x['status']:x['n'] for x in c.execute("SELECT status,COUNT(*) n FROM telegram_outbox GROUP BY status").fetchall()}
    sent_today=c.execute("""SELECT COUNT(*) n FROM telegram_outbox
      WHERE status='sent' AND date(sent_at)=date('now','localtime')""").fetchone()['n']
    notif_today=c.execute("""SELECT status,COUNT(*) n FROM notifications
      WHERE channel='telegram' AND date(sent_at)=date('now','localtime') GROUP BY status""").fetchall()
    notification_counts={x['status']:x['n'] for x in notif_today}
    recent=[dict(x) for x in c.execute("""SELECT * FROM telegram_outbox
      ORDER BY id DESC LIMIT 30""").fetchall()]
    linked_employee_rows=[dict(x) for x in c.execute("""SELECT id,name,department,telegram_chat_id,telegram_linked_at
      FROM employees WHERE is_active=1 AND telegram_enabled=1
        AND telegram_chat_id IS NOT NULL AND trim(telegram_chat_id)<>''
      ORDER BY department,name""").fetchall()]
    last_success=c.execute("""SELECT COALESCE(sent_at,created_at) at,kind,employee_id
      FROM notifications WHERE channel='telegram' AND status='sent'
      ORDER BY COALESCE(sent_at,created_at) DESC LIMIT 1""").fetchone()
    last_error=c.execute("""SELECT COALESCE(sent_at,created_at) at,kind,error
      FROM notifications WHERE channel='telegram' AND status='failed'
      ORDER BY COALESCE(sent_at,created_at) DESC LIMIT 1""").fetchone()
    c.close()
    bot={'ok':False}; webhook={'ok':False}
    try: bot=get_me()
    except Exception as e: bot={'ok':False,'error':str(e)}
    try: webhook=get_webhook_info()
    except Exception as e: webhook={'ok':False,'error':str(e)}
    return {
      'bot':bot,'webhook':webhook,
      'linked_employees':linked_employees,'unlinked_employees':unlinked_employees,
      'linked_groups':linked_groups,
      'queue':{'pending':q.get('pending',0),'retrying':q.get('retrying',0),
               'failed':q.get('failed',0),'sent':q.get('sent',0),'sent_today':sent_today},
      'notifications_today':notification_counts,
      'recent':recent,
      'linked_employee_rows':linked_employee_rows,
      'last_success':dict(last_success) if last_success else None,
      'last_error':dict(last_error) if last_error else None,
      'bot_identity':(bot.get('result') or {}) if bot.get('ok') else {}
    }
