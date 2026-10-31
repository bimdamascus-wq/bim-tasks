from __future__ import annotations
import sqlite3
import os, json, time, calendar, re, urllib.request, urllib.error, subprocess, secrets, hmac
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, Form, HTTPException, Header, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from .db import connect
from .universal import company, channel, task_form
from .work_settings import validate_recurring, profile, save_config, validate_config, apply_job_tasks, permits, record_completion
from .seed import seed
from .security import verify_password, hash_password, new_session_id, new_csrf_token, session_expiry, iso, now
from .telegram_service import answer_callback, edit_message, get_me, set_webhook, get_webhook_info
from .telegram_queue import send_resilient as send
from .telegram_media import send_attachment
from .workers import start as start_workers, stop as stop_workers
from . import access

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
templates = Jinja2Templates(directory=str(ROOT / 'app' / 'templates'))
# Set by the run script of each process: 'lan' (8797, runs the workers), 'external' (8798, behind the tunnel),
# 'test' (staging copy on 8799, no workers). Only 'lan' starts reminders, the Telegram queue and backups.
GATEWAY = os.environ.get('BIM_GATEWAY', 'lan').strip().lower()
COOKIE_SECURE = os.environ.get('BIM_COOKIE_SECURE', '0') == '1'
COOKIE = 'bim_tasks_session'
IPHONE_SECRET = os.environ.get('BIM_IPHONE_SECRET', '').strip()
PUBLIC_URL = os.environ.get('BIM_PUBLIC_URL', 'https://task.bimdamascus.com').strip()
DOMAIN = 'task.bimdamascus.com'
TELEGRAM_WEBHOOK_SECRET = os.environ.get('BIM_TELEGRAM_WEBHOOK_SECRET','').strip()

APP_VERSION = '6.3.1-unified'

def _release():
    # Written into the update package by deploy/build_update.py; lets the installer prove the new code is running.
    try: return json.loads((ROOT / 'app' / 'release.json').read_text(encoding='utf-8'))
    except Exception: return {}
RELEASE = _release()
LOGIN_WINDOW_MINUTES = 15
LOGIN_MAX_FAILS_USER = 8
LOGIN_MAX_FAILS_IP = 25
CSRF_EXEMPT_PREFIXES = ('/api/telegram/webhook', '/api/iphone/', '/api/sync/')

app = FastAPI(title='BIM Damascus Tasks', version=APP_VERSION)
app.mount('/static', StaticFiles(directory=str(ROOT / 'app' / 'static')), name='static')

@app.on_event('startup')
def startup():
    seed()
    if GATEWAY == 'lan': start_workers()

@app.on_event('shutdown')
def shutdown():
    if GATEWAY == 'lan': stop_workers()

def _from_proxy(req: Request):
    host = req.client.host if req.client else ''
    return host in ('127.0.0.1', '::1', 'localhost')

def ip(req: Request):
    host = req.client.host if req.client else 'unknown'
    # Forwarding headers are trusted only from the local tunnel/proxy, never from LAN clients.
    if _from_proxy(req):
        cf = req.headers.get('cf-connecting-ip', '').strip()
        xf = req.headers.get('x-forwarded-for', '').split(',')[0].strip()
        return cf or xf or host
    return host

def gateway_of(req: Request):
    # A request that reached the LAN port through Cloudflare is still an internet request.
    if GATEWAY == 'external' or req.headers.get('cf-connecting-ip') or req.headers.get('cf-ray'):
        return 'external'
    return 'lan'

def _allowed_hosts(req: Request):
    hosts = {DOMAIN.lower()}
    for v in (req.headers.get('host', ''), req.headers.get('x-forwarded-host', ''), PUBLIC_URL):
        v = v.split('://')[-1].split('/')[0].strip().lower()
        if v: hosts.add(v)
    return hosts

@app.middleware('http')
async def security_guard(req: Request, call_next):
    # Cross-site request protection: a logged-in browser may only change data from this system's own pages.
    if req.method not in ('GET', 'HEAD', 'OPTIONS') and req.cookies.get(COOKIE) and not req.url.path.startswith(CSRF_EXEMPT_PREFIXES):
        origin = req.headers.get('origin') or req.headers.get('referer') or ''
        host = origin.split('://')[-1].split('/')[0].strip().lower()
        if not origin or origin.strip().lower() == 'null' or host not in _allowed_hosts(req):
            return Response('{"detail":"طلب مرفوض لأسباب أمنية"}', status_code=403, media_type='application/json')
    resp = await call_next(req)
    h = resp.headers
    h.setdefault('X-Content-Type-Options', 'nosniff')
    h.setdefault('X-Frame-Options', 'SAMEORIGIN')
    h.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    h.setdefault('Permissions-Policy', 'microphone=(), geolocation=(), payment=(), usb=()')
    if gateway_of(req) == 'external':
        h.setdefault('Strict-Transport-Security', 'max-age=31536000')
    return resp

@app.middleware('http')
async def auth_guard(req: Request, call_next):
    # Default deny: a route is reachable without a session only if listed in access.PUBLIC_PATHS.
    # Pages are also checked against access.PAGE_PERMS so the permissions table is enforced, not just documented.
    path = req.url.path
    if path in access.PUBLIC_PATHS or path.startswith(access.PUBLIC_PREFIXES) or req.method == 'OPTIONS':
        return await call_next(req)
    from starlette.concurrency import run_in_threadpool
    u = await run_in_threadpool(session, req)
    wants_html = not path.startswith('/api/') and req.method == 'GET'
    if not u:
        if wants_html: return RedirectResponse('/login', status_code=303)
        return Response('{"detail":"يجب تسجيل الدخول"}', status_code=401, media_type='application/json')
    code = access.page_permission(path)
    if code and req.method == 'GET' and code not in await run_in_threadpool(perms, u['user_id']):
        if path == '/': return RedirectResponse('/my-work' if u['role_code'] == 'employee' else '/tasks', status_code=303)
        return Response('{"detail":"لا تملك صلاحية هذه الصفحة"}', status_code=403, media_type='application/json')
    return await call_next(req)

def session(req: Request):
    sid = req.cookies.get(COOKIE)
    if not sid: return None
    c = connect()
    r = c.execute('''SELECT s.*,u.username,u.full_name,u.employee_id,u.is_active,u.deleted_at,
      r.code role_code,r.name_ar role_name,r.external_access_allowed
      FROM login_sessions s JOIN users u ON u.id=s.user_id JOIN roles r ON r.id=u.role_id
      WHERE s.id=? AND s.revoked_at IS NULL''', (sid,)).fetchone()
    c.close()
    if not r or not r['is_active']: return None
    if 'deleted_at' in r.keys() and r['deleted_at']: return None
    try:
        if datetime.fromisoformat(r['expires_at']) <= datetime.now(timezone.utc): return None
    except Exception:
        return None
    if r['employee_id']:
        c=connect()
        try: blocked=channel(c,r['employee_id'])=='telegram' or not c.execute('SELECT 1 FROM employees WHERE id=? AND is_active=1',(r['employee_id'],)).fetchone()
        finally:c.close()
        if blocked:return None
    gw = gateway_of(req)
    if r['gateway'] != gw: return None
    if gw == 'external' and not r['external_access_allowed']: return None
    return r

def perms(uid: int):
    # Effective permissions come from app/access.py; per-user switches can never exceed the role ceiling.
    c = connect()
    try:
        role = c.execute('SELECT r.code FROM users u JOIN roles r ON r.id=u.role_id WHERE u.id=?', (uid,)).fetchone()
        if not role: return set()
        overrides = [(r['permission'], r['allowed']) for r in c.execute('SELECT permission,allowed FROM universal_user_permissions WHERE user_id=?', (uid,))]
        return access.effective(role['code'], overrides)
    finally: c.close()

def need(req: Request, code: str):
    u = session(req)
    if not u: raise HTTPException(401, 'يجب تسجيل الدخول')
    if code not in perms(u['user_id']): raise HTTPException(403, 'لا تملك الصلاحية')
    return u

def need_work_write(req, action='complete'):
    u=need(req,'tasks.view')
    if u['role_code']=='employee':
        c=connect()
        try:
            if not permits(c,u['employee_id'],action): raise HTTPException(403,'هذه العملية غير مفعلة لهذا الموظف')
        finally: c.close()
    elif 'tasks.edit' not in perms(u['user_id']): raise HTTPException(403,'لا تملك صلاحية تعديل المهام')
    return u

def ctx(req: Request, title: str):
    u = session(req)
    c=connect()
    try:cfg=company(c)
    finally:c.close()
    return {'company':cfg,'request': req, 'user': u, 'permissions': perms(u['user_id']) if u else set(),
            'title': title, 'gateway': GATEWAY, 'public_url': cfg['public_url'] or PUBLIC_URL}

def audit(c, req, u, action, etype=None, eid=None, before=None, after=None, reason=None):
    from .activity_log import sanitize
    from .security_center import append_audit
    before=sanitize(before); after=sanitize(after)
    append_audit(c,{'user_id':u['user_id'] if u else None,'username_snapshot':u['username'] if u else None,'action':action,
      'entity_type':etype,'entity_id':str(eid) if eid is not None else None,
      'before_json':json.dumps(before, ensure_ascii=False, default=str) if before else None,
      'after_json':json.dumps(after, ensure_ascii=False, default=str) if after else None,
      'reason':reason,'gateway':gateway_of(req),'ip_address':ip(req)})

def _employee_row(c, employee_id):
    if not employee_id: return None
    return c.execute('SELECT * FROM employees WHERE id=?', (employee_id,)).fetchone()

def _task_accessible(c, u, tid:int):
    row=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
    if not row: return None
    if not access.is_manager(u) and not access.can_view_task(c,u,tid): return None
    return row

def _safe_filename(name:str):
    name=(name or 'file').strip().replace('\\','_').replace('/','_')
    name=re.sub(r'[^\w.\-\u0600-\u06FF ]+','_',name,flags=re.UNICODE)
    return name[:120] or 'file'

AUDIO_EXTS={'.mp3','.m4a','.ogg','.oga','.opus','.wav','.aac','.amr','.flac','.wma','.weba','.caf','.aiff','.aif','.3gp'}
SQL_NOT_AUDIO="COALESCE(kind,'')<>'voice' AND lower(COALESCE(mime_type,'')) NOT LIKE 'audio/%'"

def _is_audio(name:str, mime:str='', kind:str=''):
    # Voice recordings are not accepted, stored, shown or sent anywhere in the system.
    return kind=='voice' or str(mime or '').lower().startswith('audio/') or Path(str(name or '')).suffix.lower() in AUDIO_EXTS

def _attachment_dir(tid:int):
    p=ROOT/'data'/'attachments'/f'task_{tid}'
    p.mkdir(parents=True,exist_ok=True)
    return p

def _telegram_task_keyboard(task_id:int, status:str='جديدة'):
    rows=[]
    if status not in ('تم','ملغاة'):
        rows.append([
            {'text':'▶️ بدأت','callback_data':f'task:{task_id}:started'},
            {'text':'⏳ قيد التنفيذ','callback_data':f'task:{task_id}:progress'},
            {'text':'✅ تم','callback_data':f'task:{task_id}:done'}
        ])
        rows.append([{'text':'⚠️ مشكلة / ملاحظة','callback_data':f'task:{task_id}:note'}])
    else:
        rows.append([{'text':'⚠️ إضافة ملاحظة','callback_data':f'task:{task_id}:note'}])
    rows.append([{'text':'📋 مهامي اليوم','callback_data':'my_tasks'},{'text':'📖 تعليمات عملي','callback_data':'work_instructions'}])
    return {'inline_keyboard':rows}

def _telegram_task_text(row, kind='مهمة جديدة'):
    due=f"\n⏰ الموعد: {row['due_at']}" if row['due_at'] else ''
    notes=f"\n📝 ملاحظات: {row['notes']}" if 'notes' in row.keys() and row['notes'] else ''
    return f"📌 {kind}\n\nالمهمة: {row['title']}\nالأولوية: {row['priority']}\nالحالة: {row['status']}{due}{notes}\nوقت الإرسال/التحديث: {datetime.now().strftime('%Y-%m-%d %H:%M')}"

def _remember_telegram_task_message(c, task_id, chat_id, result):
    # Never record queued deliveries before Telegram returns a real message_id.
    message_id = result.get('message_id') if result.get('ok') and not result.get('queued') else None
    if message_id is not None:
        c.execute("INSERT OR IGNORE INTO telegram_task_messages(task_id,chat_id,message_id) VALUES(?,?,?)",
                  (int(task_id),str(chat_id),int(message_id)))


def _refresh_telegram_task_messages(c, task_id):
    task=c.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
    if not task:return False
    messages=c.execute('SELECT chat_id,message_id FROM telegram_task_messages WHERE task_id=? ORDER BY message_id DESC LIMIT 100',(task_id,)).fetchall()
    updated=False
    for msg in messages:
        e=_tg_employee(c,msg['chat_id'])
        if not e or not e['telegram_enabled'] or not _tg_task_for_employee(c,task_id,e['id']):continue
        from .telegram_queue import enqueue_edit
        enqueue_edit(task_id,msg['chat_id'],int(msg['message_id']));updated=True
    return updated


def _notify_employee(c, task_id: int, kind='مهمة جديدة', notice_category='tasks'):
    row=c.execute("""SELECT t.*,e.id employee_id,e.name,e.telegram_chat_id,e.telegram_enabled
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE t.id=?""",(task_id,)).fetchone()
    if not row or not row['telegram_enabled'] or not row['telegram_chat_id']:
        return {'ok':False,'skipped':True}
    from .work_settings import notices
    if not notices(c,row['employee_id'],notice_category):return {'ok':True,'skipped':True}
    text=_telegram_task_text(row,kind)
    from .delegation import telegram_prefix
    prefix=telegram_prefix(c,row)
    if prefix:text=prefix+'\n'+text
    prefs=profile(c,row['employee_id']);mode=c.execute('SELECT display_mode FROM bim_employee_prefs WHERE employee_id=?',(row['employee_id'],)).fetchone()
    if not mode or not mode[0]:mode=c.execute('SELECT p.display_mode FROM bim_job_profiles p JOIN bim_employee_jobs j ON j.job_id=p.id WHERE j.employee_id=? AND p.is_active=1 ORDER BY p.name LIMIT 1',(row['employee_id'],)).fetchone()
    display=mode[0] if mode else 'on_demand'
    if prefs['instructions'] and (display=='auto' or (display=='once' and not prefs['instructions_read'])):text+='\n\n📖 تعليمات عملي:\n'+prefs['instructions']
    r=send(row['telegram_chat_id'],text,source_type='task_card',source_id=task_id,reply_markup=_telegram_task_keyboard(task_id,row['status']))
    _remember_telegram_task_message(c,task_id,row['telegram_chat_id'],r)
    c.execute("""INSERT INTO notifications(task_id,employee_id,channel,kind,status,error,sent_at)
      VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",(task_id,row['employee_id'],'telegram',kind,'queued' if r.get('queued') else ('sent' if r.get('ok') else 'failed'),r.get('error')))
    if r.get('ok'):
        c.execute("INSERT INTO task_events(task_id,employee_id,event_type,note,source) VALUES(?,?,'telegram_sent',?,'telegram')",(task_id,row['employee_id'],kind))
    return r

def _notify_employee_id(c, employee_id:int, text:str, task_id:int|None=None, kind:str='تنبيه مهمة', reply_markup=None):
    if channel(c,employee_id)=='web':return {'ok':False,'skipped':True}
    e=c.execute("SELECT id,name,telegram_chat_id,telegram_enabled FROM employees WHERE id=?",(employee_id,)).fetchone()
    if not e or not e['telegram_enabled'] or not e['telegram_chat_id']:
        return {'ok':False,'skipped':True}
    r=send(e['telegram_chat_id'],text,source_type='task_card' if reply_markup and task_id else 'task_notice',source_id=task_id,reply_markup=reply_markup)
    if reply_markup and task_id:_remember_telegram_task_message(c,task_id,e['telegram_chat_id'],r)
    try:
        c.execute("""INSERT INTO notifications(task_id,employee_id,channel,kind,status,error,sent_at)
          VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
          (task_id,employee_id,'telegram',kind,'queued' if r.get('queued') else ('sent' if r.get('ok') else 'failed'),r.get('error')))
        if task_id:
            c.execute("""INSERT INTO task_events(task_id,employee_id,event_type,note,source)
              VALUES(?,?,'telegram_notice',?,'dashboard')""",(task_id,employee_id,kind))
    except Exception:
        pass
    return r

def _notify_admin_comment(c, task_id:int, author_name:str, body:str):
    row=c.execute("""SELECT t.id,t.title,t.employee_id,e.name employee_name,e.telegram_chat_id,e.telegram_enabled
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE t.id=?""",(task_id,)).fetchone()
    if not row or not row['telegram_enabled'] or not row['telegram_chat_id']:
        return {'ok':False,'skipped':True}
    text=(f"📝 ملاحظة جديدة من الإدارة\n\n"
          f"المهمة: {row['title']}\n"
          f"من: {author_name}\n\n"
          f"{body}")
    r=send(row['telegram_chat_id'],text,reply_markup=_telegram_task_keyboard(task_id))
    try:
        c.execute("""INSERT INTO notifications(task_id,employee_id,channel,kind,status,error,sent_at)
          VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",(task_id,row['employee_id'],'telegram','ملاحظة من الإدارة',
          'sent' if r.get('ok') else 'failed',r.get('error')))
        c.execute("""INSERT INTO task_events(task_id,employee_id,event_type,note,source)
          VALUES(?,?,'admin_note',?,'dashboard')""",(task_id,row['employee_id'],
          f"{author_name}: {body}"))
    except Exception:
        pass
    return r

def _notify_task_notes_update(c, task_id:int, author_name:str, notes:str):
    if not str(notes or '').strip():
        return {'ok':False,'skipped':True}
    return _notify_admin_comment(c,task_id,author_name,f"تحديث ملاحظات المهمة:\n{str(notes).strip()}")

def _notify_task_attachment(c, task_id:int, author_name:str, attachment_row):
    row=c.execute("""SELECT t.id,t.title,t.employee_id,e.telegram_chat_id,e.telegram_enabled
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE t.id=?""",(task_id,)).fetchone()
    if not row or not row['telegram_enabled'] or not row['telegram_chat_id']:
        return {'ok':False,'skipped':True}
    if _is_audio(attachment_row['original_name'],attachment_row['mime_type'],attachment_row['kind']):
        return {'ok':False,'skipped':True}
    path=ROOT/attachment_row['relative_path']
    caption=(f"📎 مرفق جديد على المهمة\n\n"
             f"المهمة: {row['title']}\n"
             f"من: {author_name}\n"
             f"الملف: {attachment_row['original_name']}")
    r=send_attachment(row['telegram_chat_id'],path,attachment_row['mime_type'] or '',caption)
    try:
        c.execute("""INSERT INTO notifications(task_id,employee_id,channel,kind,status,error,sent_at)
          VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
          (task_id,row['employee_id'],'telegram','مرفق جديد',
           'sent' if r.get('ok') else 'failed',r.get('error')))
        c.execute("""INSERT INTO task_events(task_id,employee_id,event_type,note,source)
          VALUES(?,?,'attachment',?,'dashboard')""",
          (task_id,row['employee_id'],f"{author_name}: {attachment_row['original_name']}"))
    except Exception:
        pass
    return r

def _tg_employee(c,chat_id):
    e=c.execute("SELECT * FROM employees WHERE telegram_chat_id=? AND is_active=1",(str(chat_id),)).fetchone()
    return e if e and channel(c,e['id'])!='web' else None

def _tg_task_for_employee(c,tid,eid):
    return c.execute("SELECT * FROM tasks WHERE id=? AND archived_at IS NULL AND (employee_id=? OR EXISTS(SELECT 1 FROM task_participants p WHERE p.task_id=tasks.id AND p.employee_id=?))",(tid,eid,eid)).fetchone()

def _tg_send_instructions(c,e):
    p=profile(c,e['id'])
    return send(e['telegram_chat_id'],p['instructions'] or 'لا توجد تعليمات مسجلة',reply_markup={'inline_keyboard':[[{'text':'قرأت التعليمات','callback_data':'instructions_read'}]]})

def _tg_send_my_tasks(c,e):
    rows=c.execute("""SELECT id,title,status,priority,due_at FROM tasks
      WHERE (employee_id=? OR EXISTS(SELECT 1 FROM task_participants p WHERE p.task_id=tasks.id AND p.employee_id=? AND p.status<>'تم')) AND archived_at IS NULL AND status NOT IN ('تم','ملغاة')
      ORDER BY CASE priority WHEN 'عاجلة' THEN 1 WHEN 'عالية' THEN 2 WHEN 'عادي' THEN 3 ELSE 4 END,
      CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,due_at,id DESC""",(e['id'],e['id'])).fetchall()
    if not rows:
        return send(e['telegram_chat_id'],'✅ لا توجد لديك مهام مفتوحة حالياً.')
    lines=[f"📋 مهامك المفتوحة — {e['name']}"]
    for i,t in enumerate(rows,1):
        due=f" | ⏰ {t['due_at']}" if t['due_at'] else ''
        lines.append(f"{i}. {t['title']} — {t['status']} — {t['priority']}{due}")
    return send(e['telegram_chat_id'],'\n'.join(lines))

def _tg_set_status(c,task,e,new_status):
    action={'بدأت':'start','قيد التنفيذ':'progress','تم':'complete'}.get(new_status)
    if not action or not permits(c,e['id'],action): return False
    if task['is_group']:
        from .universal import telegram_team_status
        return telegram_team_status(c,task,e,new_status)
    c.execute('BEGIN IMMEDIATE')
    try:
        live=c.execute('SELECT * FROM tasks WHERE id=? AND employee_id=?',(task['id'],e['id'])).fetchone()
        if not live or live['status']==new_status or live['status'] in ('تم','ملغاة') or (live['status']=='قيد التنفيذ' and new_status=='بدأت'):
            c.rollback();return False
        old=live['status']
        c.execute("UPDATE tasks SET status=?,started_at=COALESCE(started_at,CURRENT_TIMESTAMP),in_progress_at=CASE WHEN ?='قيد التنفيذ' THEN COALESCE(in_progress_at,CURRENT_TIMESTAMP) ELSE in_progress_at END,completed_at=CASE WHEN ?='تم' THEN COALESCE(completed_at,CURRENT_TIMESTAMP) ELSE completed_at END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(new_status,new_status,new_status,task['id']))
        c.execute("INSERT INTO task_events(task_id,employee_id,event_type,old_status,new_status,source) VALUES(?,?,'status',?,?,'telegram')",(task['id'],e['id'],old,new_status))
        if new_status=='تم': record_completion(c,task['id'],e['id'],{},'telegram')
        sync_id=_touch_task_sync(c,task['id'],'server','status')
        c.commit();_try_instant_sheet_push(c,sync_id);return True
    except Exception:
        c.rollback();raise


def _task_with_reminder(c, tid):
    return c.execute('''SELECT t.*,COALESCE(e.name,CASE WHEN t.is_group=1 THEN 'مهمة جماعية' ELSE 'بدون موظف' END) employee_name,
      r.type reminder_type,r.start_at reminder_start_at,r.specific_at reminder_specific_at,
      r.weekly_day reminder_weekly_day,r.weekly_time reminder_weekly_time,
      r.monthly_day reminder_monthly_day,r.monthly_time reminder_monthly_time,
      r.is_active reminder_active,r.last_sent_at reminder_last_sent_at
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id
      LEFT JOIN task_reminders r ON r.task_id=t.id WHERE t.id=?''', (tid,)).fetchone()

def _save_reminder(c, tid, rem):
    rem = rem or {}; typ = rem.get('type', 'بدون') or 'بدون'
    active = 0 if typ == 'بدون' else 1
    c.execute('''INSERT INTO task_reminders(task_id,type,start_at,specific_at,weekly_day,weekly_time,monthly_day,monthly_time,is_active,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
      ON CONFLICT(task_id) DO UPDATE SET type=excluded.type,start_at=excluded.start_at,
      specific_at=excluded.specific_at,weekly_day=excluded.weekly_day,weekly_time=excluded.weekly_time,
      monthly_day=excluded.monthly_day,monthly_time=excluded.monthly_time,is_active=excluded.is_active,
      updated_at=CURRENT_TIMESTAMP''',
      (tid, typ, rem.get('start_at'), rem.get('specific_at'), rem.get('weekly_day'), rem.get('weekly_time'),
       rem.get('monthly_day'), rem.get('monthly_time'), active))


def _setting(c, key:str, default:str=''):
    r=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return (r['value'] if r else default) or default

def _set_setting(c,key:str,value:str):
    c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))

def _sync_secret():
    return os.environ.get('BIM_SYNC_SECRET','').strip()

def _sync_auth(req:Request):
    secret=req.headers.get('x-bim-sync-secret','') or req.query_params.get('secret','')
    if not _sync_secret() or not hmac.compare_digest(secret,_sync_secret()):
        raise HTTPException(401,'Sync secret غير صحيح')

def _task_sync_payload(c, tid:int):
    r=c.execute("""SELECT t.id,t.task_code,t.employee_id,COALESCE(e.name,'') employee_name,t.title,t.notes,
      t.priority,t.status,t.due_at,t.created_at,t.updated_at,t.archived_at,
      COALESCE(t.sync_version,1) sync_version,COALESCE(t.sync_source,'server') sync_source
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE t.id=?""",(tid,)).fetchone()
    return dict(r) if r else None

def _record_sync_event(c, tid:int, action:str='upsert', source:str='server'):
    payload=_task_sync_payload(c,tid)
    if not payload: return None
    c.execute('INSERT INTO sync_events(entity_type,entity_key,action,source,payload_json) VALUES(?,?,?,?,?)',
              ('task',str(tid),action,source,json.dumps(payload,ensure_ascii=False,default=str)))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]

def _try_instant_sheet_push(c, event_id):
    """Push one sync event and return a diagnostic result instead of hiding failures."""
    if not event_id:
        return {'ok':False,'stage':'event','message':'لا يوجد Sync Event'}
    url=_setting(c,'sync.webapp_url','').strip(); secret=_sync_secret()
    if not url:
        return {'ok':False,'stage':'config','message':'رابط Apps Script غير محفوظ'}
    if not secret:
        return {'ok':False,'stage':'config','message':'BIM_SYNC_SECRET غير مضبوط على السيرفر'}

    ev=c.execute('SELECT * FROM sync_events WHERE id=?',(event_id,)).fetchone()
    if not ev:
        return {'ok':False,'stage':'event','message':'لم يتم العثور على Sync Event'}

    body=json.dumps({'action':'server_event','secret':secret,'event':dict(ev)},
                    ensure_ascii=False).encode('utf-8')
    try:
        r=urllib.request.Request(
            url,data=body,headers={'Content-Type':'application/json'},method='POST'
        )
        # Apps Script can be slow on large legacy sheets. Five seconds caused
        # old tasks to remain dirty even when the Web App was healthy.
        with urllib.request.urlopen(r,timeout=15.0) as resp:
            status=getattr(resp,'status',200)
            raw=resp.read().decode('utf-8','replace')

        parsed=None
        try:
            parsed=json.loads(raw or '{}')
        except Exception:
            parsed=None

        ok=bool(
            200 <= status < 300 and
            parsed and parsed.get('ok') and
            str(parsed.get('action') or '') in ('server_event','sync_event')
        )

        if ok:
            c.execute(
                'UPDATE tasks SET sync_dirty=0,sync_last_at=CURRENT_TIMESTAMP WHERE id=?',
                (int(ev['entity_key']),)
            )
            return {
                'ok':True,'stage':'done','http_status':status,
                'message':str(parsed.get('message') or 'تمت المزامنة'),
                'response':parsed
            }

        if parsed:
            msg=str(parsed.get('message') or 'Apps Script رفض عملية المزامنة')
        elif 200 <= status < 300:
            msg='Apps Script أعاد استجابة غير مفهومة'
        else:
            msg=f'HTTP {status}'
        return {
            'ok':False,'stage':'apps_script','http_status':status,
            'message':msg,'response':parsed if parsed is not None else raw[:1200]
        }

    except urllib.error.HTTPError as e:
        try:
            raw=e.read().decode('utf-8','replace')
        except Exception:
            raw=''
        return {
            'ok':False,'stage':'http','http_status':getattr(e,'code',None),
            'message':f'HTTP {getattr(e,"code","")} — {getattr(e,"reason","")}'.strip(),
            'response':raw[:1200]
        }
    except Exception as e:
        return {
            'ok':False,'stage':'network',
            'message':f'{type(e).__name__}: {e}'
        }

def _touch_task_sync(c, tid:int, source:str='server', action:str='upsert'):
    c.execute("""UPDATE tasks SET sync_version=COALESCE(sync_version,0)+1,sync_source=?,sync_dirty=1,
                 sync_last_at=CURRENT_TIMESTAMP WHERE id=?""",(source,tid))
    eid=_record_sync_event(c,tid,action,source)
    # Any change created inside BIM Tasks (web / iPhone / Telegram paths)
    # should be pushed immediately to Google Sheet.
    # Sheet-originated changes are excluded to prevent an echo loop.
    if source!='sheet' and not c.in_transaction:
        _try_instant_sheet_push(c,eid)
    return eid

def _norm_ar(s):
    s=str(s or '').strip().lower()
    for a,b in [('أ','ا'),('إ','ا'),('آ','ا'),('ى','ي'),('ة','ه')]: s=s.replace(a,b)
    return re.sub(r'\s+',' ',s)

def _parse_iphone_text(c,text:str):
    raw=re.sub(r'\s+',' ',str(text or '').strip())
    employees=c.execute('SELECT * FROM employees WHERE is_active=1 ORDER BY length(name) DESC').fetchall()
    emp=None; nr=_norm_ar(raw)
    for e in employees:
        nn=_norm_ar(e['name'])
        if nr==nn or nr.startswith(nn+' ') or nr.startswith(nn+',') or nr.startswith(nn+'،'):
            emp=e; raw=raw[len(e['name']):].lstrip(' ,،:-'); break
    priority='عادي'; n=_norm_ar(raw)
    if any(x in n for x in ('عاجل','عاجله','ضروري جدا','فورا')): priority='عاجلة'
    elif any(x in n for x in ('اهميه عاليه','اولويه عاليه','عاليه')): priority='عالية'
    elif any(x in n for x in ('منخفضه','اولويه منخفضه')): priority='منخفضة'
    return emp,(raw or str(text).strip()),priority

# ---------- public/static ----------
@app.get('/health')
def health():
    c = connect();
    try:
        db_ok = c.execute('SELECT 1').fetchone()[0] == 1
        tasks = c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
    finally: c.close()
    return {'status': 'ok' if db_ok else 'error', 'system': 'BIM Tasks', 'version': APP_VERSION,
            'gateway': GATEWAY, 'domain': DOMAIN, 'public_url': PUBLIC_URL, 'tasks': tasks}

@app.get('/manifest.webmanifest')
def manifest(): return FileResponse(ROOT/'app'/'static'/'manifest.webmanifest', media_type='application/manifest+json')

@app.get('/service-worker.js')
def sw(): return FileResponse(ROOT/'app'/'static'/'service-worker.js', media_type='application/javascript')

# ---------- authentication ----------
@app.get('/login', response_class=HTMLResponse)
def login_page(req: Request, error: Optional[str]=None):
    return templates.TemplateResponse(request=req, name='login.html', context={**ctx(req,'تسجيل الدخول'), 'error':error})

@app.post('/login')
def login(req: Request, username: str=Form(...), password: str=Form(...)):
    c = connect(); uname = username.strip()[:150]; gw = gateway_of(req)
    window = f'-{LOGIN_WINDOW_MINUTES} minutes'
    fails_user = c.execute('''SELECT COUNT(*) FROM login_attempts WHERE lower(username)=lower(?) AND success=0
      AND attempted_at>datetime('now',?) AND id>COALESCE((SELECT MAX(id) FROM login_attempts WHERE lower(username)=lower(?) AND success=1),0)''',
      (uname, window, uname)).fetchone()[0]
    fails_ip = c.execute("SELECT COUNT(*) FROM login_attempts WHERE ip_address=? AND success=0 AND attempted_at>datetime('now',?)",
      (ip(req), window)).fetchone()[0]
    if fails_user >= LOGIN_MAX_FAILS_USER or fails_ip >= LOGIN_MAX_FAILS_IP:
        from .activity_log import failed_login
        failed_login(c,req,None,uname,gw,ip(req),status=429)
        c.close(); return templates.TemplateResponse(request=req, name='login.html', context={**ctx(req,'تسجيل الدخول'), 'error':f'تم إيقاف الدخول مؤقتاً بسبب محاولات خاطئة متكررة. حاول بعد {LOGIN_WINDOW_MINUTES} دقيقة.'}, status_code=429)
    u = c.execute('''SELECT u.*,r.code role_code,r.external_access_allowed FROM users u JOIN roles r ON r.id=u.role_id
      WHERE lower(u.username)=lower(?)''', (uname,)).fetchone()
    ok = bool(u and u['is_active'] and not u['deleted_at'] and verify_password(u['password_hash'], password))
    c.execute('INSERT INTO login_attempts(username,ip_address,success) VALUES(?,?,?)', (uname, ip(req), 1 if ok else 0))
    if not ok:
        from .activity_log import failed_login
        failed_login(c,req,u,uname,gw,ip(req))
        c.close(); return templates.TemplateResponse(request=req, name='login.html', context={**ctx(req,'تسجيل الدخول'), 'error':'اسم المستخدم أو كلمة المرور غير صحيحة'}, status_code=401)
    if gw == 'external' and not u['external_access_allowed']:
        from .activity_log import failed_login
        failed_login(c,req,u,uname,gw,ip(req),status=403)
        c.close(); return templates.TemplateResponse(request=req, name='login.html', context={**ctx(req,'تسجيل الدخول'), 'error':'هذا الحساب غير مسموح له بالدخول من الخارج'}, status_code=403)
    if u['employee_id'] and (channel(c,u['employee_id'])=='telegram' or not c.execute('SELECT 1 FROM employees WHERE id=? AND is_active=1',(u['employee_id'],)).fetchone()):
        from .activity_log import failed_login
        failed_login(c,req,u,uname,gw,ip(req),status=403)
        c.close();return templates.TemplateResponse(request=req,name='login.html',context={**ctx(req,'تسجيل الدخول'),'error':'هذا الحساب مضبوط لاستخدام Telegram فقط؛ راجع الإدارة لتفعيل الويب'},status_code=403)
    if gw == 'external' and mfa_required(c, u['id']):
        try: return mfa_login_challenge(c, req, u, gw)
        finally: c.close()
    try: return _open_session(c, req, u, gw)
    finally: c.close()

def _open_session(c, req, u, gw):
    sid = new_session_id(); csrf = new_csrf_token()
    c.execute('INSERT INTO login_sessions(id,user_id,csrf_token,gateway,expires_at,ip_address,user_agent) VALUES(?,?,?,?,?,?,?)',
              (sid, u['id'], csrf, gw, session_expiry(), ip(req), req.headers.get('user-agent','')))
    c.execute('UPDATE users SET last_login_at=?,last_login_ip=? WHERE id=?', (iso(now()), ip(req), u['id']))
    from .activity_log import auth_event
    auth_event(c,req,{'user_id':u['id'],'username':u['username']},sid,'login',gw,ip(req))
    resp = RedirectResponse('/' if u['role_code'] in access.MANAGERS else ('/my-work' if u['role_code']=='employee' else '/tasks'), 303)
    resp.set_cookie(COOKIE, sid, httponly=True, secure=COOKIE_SECURE or gw == 'external', samesite='lax', max_age=43200)
    return resp

@app.get('/logout')
def logout(req: Request):
    sid = req.cookies.get(COOKIE); actor=session(req); c = connect()
    if actor:
        from .activity_log import auth_event
        auth_event(c,req,actor,sid,'logout',GATEWAY,ip(req))
    c.execute('UPDATE login_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE id=?', (sid,)); c.close()
    resp = RedirectResponse('/login', 303); resp.delete_cookie(COOKIE); return resp

# ---------- pages ----------
@app.get('/', response_class=HTMLResponse)
def dashboard(req: Request):
    u = session(req)
    if not u:
        return RedirectResponse('/login', status_code=303)
    admin=need(req,'dashboard.view')
    if admin['role_code'] not in ('admin','deputy'): raise HTTPException(403,'لوحة الإدارة للمدير ونائب المدير فقط')
    from .command_center import PAGES, hidden_pages
    c=connect()
    try:hidden=set(hidden_pages(c))
    finally:c.close()
    nav=[(g,[(p,l) for p,l in items if p not in hidden and p!='/my-work']) for g,items in PAGES]
    nav=[(g,items) for g,items in nav if items]
    if 'users.manage' in perms(admin['user_id']):nav.append(('الإعدادات',[('/page-visibility','الصفحات الظاهرة')]))
    return templates.TemplateResponse(request=req,name='command_center.html',context={**ctx(req,'الرئيسية'),'cc_nav':nav})
@app.get('/tasks', response_class=HTMLResponse)
def tasks_page(req: Request): need(req,'tasks.view'); return templates.TemplateResponse(request=req,name='tasks.html',context=ctx(req,'كل المهام'))
@app.get('/my-work', response_class=HTMLResponse)
def my_work(req: Request):
    u = need(req, 'tasks.view')
    if u['role_code'] != 'employee':
        return RedirectResponse('/tasks', status_code=303)
    if not u['employee_id']:
        raise HTTPException(403, 'الحساب غير مربوط بموظف')
    return templates.TemplateResponse(request=req, name='my_work.html', context=ctx(req, 'مهامي'))

@app.get('/tasks-table', response_class=HTMLResponse)
def tasks_table_page(req: Request): need(req,'tasks.view'); return templates.TemplateResponse(request=req,name='tasks_table.html',context=ctx(req,'المهام كجدول'))
@app.get('/manage', response_class=HTMLResponse)
def manage_page(req: Request): need(req,'tasks.edit'); return templates.TemplateResponse(request=req,name='manage.html',context=ctx(req,'إدارة المهام'))
@app.get('/employees', response_class=HTMLResponse)
def employees_page(req: Request): need(req,'employees.view'); return templates.TemplateResponse(request=req,name='employees.html',context=ctx(req,'الموظفون'))
@app.get('/recurring', response_class=HTMLResponse)
def recurring_page(req: Request): need(req,'recurring.view'); return templates.TemplateResponse(request=req,name='recurring.html',context=ctx(req,'المهام المتكررة'))
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
    if not TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(500,'Webhook secret غير مضبوط')
    c=connect()
    try:base_url=company(c)['public_url'] or PUBLIC_URL
    finally:c.close()
    url=base_url.rstrip('/')+'/api/telegram/webhook'
    r=set_webhook(url,TELEGRAM_WEBHOOK_SECRET)
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
    if not TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(503,'Telegram webhook secret غير مضبوط')
    if not hmac.compare_digest(req.headers.get('x-telegram-bot-api-secret-token',''),TELEGRAM_WEBHOOK_SECRET):
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

# ---------- Operational reports ----------
@app.get('/operations-reports', response_class=HTMLResponse)
def operations_reports_page(req:Request):
    need(req,'dashboard.view')
    return templates.TemplateResponse(request=req,name='operations_reports.html',context=ctx(req,'التقارير التشغيلية'))

@app.get('/api/operations-report')
def operations_report(req:Request, days:int=30, employee_id:Optional[int]=None, department:Optional[str]=None):
    need(req,'dashboard.view'); days=min(max(int(days or 30),1),365); c=connect()
    where=["date(t.created_at,'localtime')>=date('now','localtime',?)"]; args=[f'-{days-1} days']
    if employee_id: where.append("t.employee_id=?"); args.append(employee_id)
    if department: where.append("COALESCE(e.department,'')=?"); args.append(department)
    w=' AND '.join(where)
    summary=dict(c.execute(f"""SELECT COUNT(*) total,
      SUM(CASE WHEN t.status='تم' THEN 1 ELSE 0 END) completed,
      SUM(CASE WHEN t.status NOT IN ('تم','ملغاة') AND t.due_at IS NOT NULL AND datetime(t.due_at)<datetime('now','localtime') THEN 1 ELSE 0 END) overdue,
      ROUND(AVG(CASE WHEN t.started_at IS NOT NULL THEN (julianday(t.started_at)-julianday(t.created_at))*24*60 END),1) avg_start_minutes,
      ROUND(AVG(CASE WHEN t.completed_at IS NOT NULL THEN (julianday(t.completed_at)-julianday(t.created_at))*24*60 END),1) avg_complete_minutes
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE {w}""",args).fetchone())
    problem_where=["date(te.created_at,'localtime')>=date('now','localtime',?)","te.event_type='note'","te.source='telegram'"]
    pargs=[f'-{days-1} days']
    if employee_id: problem_where.append("te.employee_id=?"); pargs.append(employee_id)
    if department:problem_where.append("te.employee_id IN (SELECT id FROM employees WHERE department=?)");pargs.append(department)
    problems=c.execute("SELECT COUNT(*) n FROM task_events te WHERE "+' AND '.join(problem_where),pargs).fetchone()['n']
    summary['problems']=problems
    rows=[dict(x) for x in c.execute(f"""SELECT t.employee_id employee_id,COALESCE(e.name,CASE WHEN t.is_group=1 THEN 'مهمة جماعية' ELSE 'بدون موظف' END) employee_name,
      COALESCE(e.department,'—') department,COUNT(*) total,
      SUM(CASE WHEN t.status='تم' THEN 1 ELSE 0 END) completed,
      SUM(CASE WHEN t.status NOT IN ('تم','ملغاة') AND t.due_at IS NOT NULL AND datetime(t.due_at)<datetime('now','localtime') THEN 1 ELSE 0 END) overdue,
      ROUND(AVG(CASE WHEN t.started_at IS NOT NULL THEN (julianday(t.started_at)-julianday(t.created_at))*24*60 END),1) avg_start_minutes,
      ROUND(AVG(CASE WHEN t.completed_at IS NOT NULL THEN (julianday(t.completed_at)-julianday(t.created_at))*24*60 END),1) avg_complete_minutes
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id
      WHERE {w}
      GROUP BY t.employee_id,e.name,e.department ORDER BY total DESC""",args)]
    employees=[dict(x) for x in c.execute("SELECT id,name,department FROM employees WHERE is_active=1 ORDER BY name")]
    departments=[x['department'] for x in c.execute("SELECT DISTINCT department FROM employees WHERE department IS NOT NULL AND trim(department)<>'' ORDER BY department")]
    c.close(); return {'days':days,'summary':summary,'rows':rows,'employees':employees,'departments':departments}


# Details for interactive operational report cards; shares scope/status rules with summary.
@app.get('/api/operations-report/tasks')
def operations_report_tasks(req:Request, days:int=30, employee_id:Optional[int]=None, department:Optional[str]=None, category:str='total', q:str='', unassigned:bool=False, offset:int=0):
    need(req,'dashboard.view')
    if category not in {'total','completed','overdue','active'}:
        raise HTTPException(400,'تصنيف غير صالح')
    days=min(max(int(days or 30),1),365)
    c=connect()
    try:
        clauses=["date(t.created_at,'localtime')>=date('now','localtime',?)"]
        params=[f'-{days-1} days']
        if unassigned:clauses.append('t.employee_id IS NULL')
        if employee_id:
            clauses.append('t.employee_id=?');params.append(employee_id)
        if department:
            clauses.append("COALESCE(e.department,'')=?");params.append(department)
        if category=='completed':clauses.append("t.status='تم'")
        if category=='overdue':clauses.append("t.status NOT IN ('تم','ملغاة') AND t.due_at IS NOT NULL AND datetime(t.due_at)<datetime('now','localtime')")
        if category=='active':clauses.append("t.status NOT IN ('تم','ملغاة')")
        q=q.strip()[:120]
        if q:
            clauses.append("(t.title LIKE ? OR COALESCE(e.name,'') LIKE ? OR COALESCE(e.department,'') LIKE ? OR CAST(t.id AS TEXT) LIKE ?)")
            params.extend(['%'+q+'%']*4)
        where=' AND '.join(clauses)
        total=c.execute('SELECT COUNT(*) n FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE '+where,params).fetchone()['n']
        items=[dict(x) for x in c.execute(f"""SELECT t.id,t.title,t.status,t.due_at,t.created_at,
            COALESCE(e.name,'مهمة جماعية / غير معينة') employee_name,COALESCE(e.department,'—') department
            FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id
            WHERE {where} ORDER BY t.id DESC LIMIT 500 OFFSET ?""",[*params,max(0,offset)])]
        return {'total':total,'items':items,'truncated':total>max(0,offset)+len(items),'next_offset':max(0,offset)+len(items)}
    finally:
        c.close()

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
      CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,due_at,id DESC LIMIT 50""",(eid,)).fetchall()
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

@app.get('/integrations', response_class=HTMLResponse)
def integrations_page(req: Request): need(req,'dashboard.view'); return templates.TemplateResponse(request=req,name='integrations.html',context=ctx(req,'الربط والتكامل'))
@app.get('/audit', response_class=HTMLResponse)
def audit_page(req: Request): need(req,'audit.view'); return templates.TemplateResponse(request=req,name='audit.html',context=ctx(req,'سجل النشاط'))
@app.get('/users', response_class=HTMLResponse)
def users_page(req: Request): need(req,'users.manage'); return templates.TemplateResponse(request=req,name='users.html',context=ctx(req,'المستخدمون'))
@app.get('/system-health', response_class=HTMLResponse)
def health_page(req: Request): need(req,'dashboard.view'); return templates.TemplateResponse(request=req,name='health.html',context=ctx(req,'صحة النظام'))

@app.get('/archive', response_class=HTMLResponse)
def archive_page(req: Request): need(req,'tasks.view'); return templates.TemplateResponse(request=req,name='archive.html',context=ctx(req,'الأرشيف'))

@app.get('/task/{tid}', response_class=HTMLResponse)
def task_detail_page(tid:int, req:Request):
    u=need(req,'tasks.view'); c=connect(); row=_task_accessible(c,u,tid); c.close()
    if not row: raise HTTPException(404,'المهمة غير موجودة')
    return templates.TemplateResponse(request=req,name='task_detail.html',context={**ctx(req,'تفاصيل المهمة'),'task_id':tid})

# ---------- basic APIs ----------
@app.get('/api/me')
def api_me(req: Request):
    u = session(req)
    if not u: raise HTTPException(401)
    return {'id': u['user_id'], 'username': u['username'], 'full_name': u['full_name'], 'role': u['role_name'],
            'role_code': u['role_code'], 'employee_id': u['employee_id'], 'permissions': sorted(perms(u['user_id'])),
            'gateway': GATEWAY, 'public_url': PUBLIC_URL, 'hidden_pages': _hidden_pages()}

def _hidden_pages():
    from .command_center import hidden_pages
    c=connect()
    try:return hidden_pages(c)
    finally:c.close()

@app.get('/api/release-health')
def release_health(req: Request):
    # Minimal readiness evidence, no employee data, tokens, paths or counts.
    if not req.client or req.client.host not in ('127.0.0.1', '::1', 'testclient'):
        raise HTTPException(403, 'Local health check only')
    from . import workers as worker_state
    c = connect()
    try:
        for table in ('tasks', 'bim_job_profiles', 'bim_employee_jobs',
                      'bim_employee_prefs', 'telegram_task_messages', 'bim_work_settings', 'bim_instruction_reads', 'bim_completion_entries', 'bim_recurring_rules', 'bim_job_recurring', 'bim_webhook_claims', 'universal_templates', 'universal_template_versions', 'universal_links', 'universal_overrides', 'universal_task_meta', 'universal_staff_settings', 'universal_user_permissions'):
            c.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchall()
    finally:
        c.close()
    worker_ok = bool(worker_state.THREAD and worker_state.THREAD.is_alive())
    if GATEWAY == 'lan' and not worker_ok:
        raise HTTPException(503, 'Background worker unavailable')
    return {'service': 'bim-tasks', 'release': '2026-10-22-bundles-r6',
            'database': 'ready', 'gateway': GATEWAY, 'worker_alive': worker_ok,
            'commit': RELEASE.get('commit', '')}


@app.get('/api/client-status')
def client_status(req: Request):
    need(req,'dashboard.view'); c=connect()
    failed=c.execute("SELECT COUNT(*) FROM notifications WHERE status='failed'").fetchone()[0]
    c.close(); return {'ok':True,'gateway':GATEWAY,'sync_pending':0,'sync_failed':failed,'domain':DOMAIN}

@app.get('/api/dashboard')
def api_dashboard(req: Request):
    need(req,'dashboard.view')
    if session(req)['role_code'] not in ('admin','deputy'): raise HTTPException(403,'غير مصرح')
    c=connect(); q=lambda s:c.execute(s).fetchone()[0]
    data={'total':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND status NOT IN('تم','ملغاة')"),
          'today':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND date(created_at,'localtime')=date('now','localtime')"),
          'late':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND due_at IS NOT NULL AND datetime(due_at)<datetime('now','localtime') AND status NOT IN('تم','ملغاة')"),
          'done':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND status='تم'"),
          'working':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND status IN('بدأت','قيد التنفيذ')"),
          'waiting':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND status='بانتظار'"),
          'unseen':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND viewed_at IS NULL AND status NOT IN('تم','ملغاة')"),
          'employees':q("SELECT COUNT(*) FROM employees WHERE is_active=1"),
          'unassigned':q("SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND employee_id IS NULL AND status NOT IN('تم','ملغاة')"),
          'sync_dirty':q("SELECT COUNT(*) FROM tasks WHERE COALESCE(sync_dirty,1)=1"),
          'sync_conflicts':q("SELECT COUNT(*) FROM sync_conflicts WHERE status='open'")}
    data['by_employee']=[dict(x) for x in c.execute('''SELECT COALESCE(e.name,'بدون موظف') name,COUNT(*) count
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE t.archived_at IS NULL AND t.status NOT IN('تم','ملغاة')
      GROUP BY t.employee_id ORDER BY count DESC,e.name LIMIT 12''')]
    data['latest']=[dict(x) for x in c.execute('''SELECT t.id,t.task_code,t.title,t.status,t.priority,t.due_at,
      COALESCE(e.name,'بدون موظف') employee FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE t.archived_at IS NULL ORDER BY t.id DESC LIMIT 12''')]
    c.close(); return data

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
      (SELECT COUNT(*) FROM task_participants p3 WHERE p3.task_id=t.id) participant_count
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id LEFT JOIN task_reminders r ON r.task_id=t.id {w}
      ORDER BY CASE t.status WHEN 'جديدة' THEN 1 WHEN 'بدأت' THEN 2 WHEN 'قيد التنفيذ' THEN 3 WHEN 'بانتظار' THEN 4 ELSE 9 END,t.id DESC''',p)]
    from .delegation import decorate
    decorate(c,rows)
    c.close(); return rows

@app.get('/api/tasks/{tid}')
def api_task(tid:int, req:Request):
    u=need(req,'tasks.view'); c=connect(); access=_task_accessible(c,u,tid)
    if not access: c.close(); raise HTTPException(404)
    if u['role_code']=='employee' and not access['viewed_at']:
        c.execute('UPDATE tasks SET viewed_at=CURRENT_TIMESTAMP WHERE id=?',(tid,))
    row=_task_with_reminder(c,tid)
    d=dict(row); d['participants']=[dict(x) for x in c.execute('''SELECT p.*,e.name FROM task_participants p JOIN employees e ON e.id=p.employee_id WHERE p.task_id=?''',(tid,))]
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
        c.execute("INSERT INTO task_events(task_id,employee_id,event_type,note,source) VALUES(?,?,'edited',?,'dashboard')",(tid,old['employee_id'],json.dumps({k:d[k] for k in d if k in allowed},ensure_ascii=False)))
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
              f"🔁 تم نقل المهمة منك\n\nالمهمة: {old['title']}\nلن تظهر لك كت مهمة مسندة بعد الآن.",
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
            changed.append(f"{labels[k]}: {old[k] or '—'} ← {d.get(k) or '—'}")
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


# ---------- recurring ----------
@app.get('/api/recurring')
def api_recurring(req:Request):
    u=need(req,'recurring.view'); c=connect()
    try:
        scope=access.employee_scope(c,u)
        return [dict(x) for x in c.execute('''SELECT r.*,COALESCE((SELECT workdays_only FROM bim_recurring_rules br WHERE br.recurring_id=r.id),0) workdays_only,COALESCE(e.name,'بدون موظف') employee_name
          FROM recurring_tasks r LEFT JOIN employees e ON e.id=r.employee_id WHERE r.id NOT IN(SELECT record_id FROM record_lifecycle WHERE entity='recurring' AND deleted_at IS NOT NULL) ORDER BY r.id DESC''') if scope is None or x['employee_id'] in scope]
    finally: c.close()

@app.post('/api/recurring')
async def add_recurring(req:Request):
    u=need(req,'recurring.edit'); d=await req.json(); title=str(d.get('title','')).strip()
    d.update(validate_recurring(d))
    c=connect()
    try: access.require_assignable(c,u,[d.get('employee_id')])
    except HTTPException: c.close(); raise
    if not c.execute('SELECT 1 FROM employees WHERE id=? AND is_active=1',(d.get('employee_id'),)).fetchone():c.close();raise HTTPException(400,'اختر موظفاً فعالاً')
    c.execute('''INSERT INTO recurring_tasks(employee_id,title,notes,priority,recurrence_type,week_day,month_day,run_time,created_by)
      VALUES(?,?,?,?,?,?,?,?,?)''',(d.get('employee_id') or None,title,d.get('notes'),d.get('priority','عادي'),d['recurrence_type'],d.get('week_day'),d.get('month_day'),d.get('run_time','09:00'),u['user_id']))
    rid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('INSERT INTO bim_recurring_rules VALUES(?,?)',(rid,int(d.get('workdays_only',True))))
    audit(c,req,u,'recurring_create','recurring',rid,after=d); c.close(); return {'ok':True,'id':rid}

@app.patch('/api/recurring/{rid}')
async def edit_recurring(rid:int, req:Request):
    u=need(req,'recurring.edit'); d=await req.json(); c=connect(); old=c.execute('SELECT * FROM recurring_tasks WHERE id=?',(rid,)).fetchone()
    if not old: c.close(); raise HTTPException(404)
    # An unassigned rule (employee_id NULL) belongs to management, never to a supervisor's team.
    current=old['employee_id'] if old['employee_id'] is not None else -1
    try: access.require_assignable(c,u,[current]+([d['employee_id']] if 'employee_id' in d else []))
    except HTTPException: c.close(); raise
    merged=dict(old);merged.update(d)
    try:validate_recurring(merged)
    except Exception:c.close();raise
    if 'employee_id' in d and not c.execute('SELECT 1 FROM employees WHERE id=?',(d['employee_id'],)).fetchone():c.close();raise HTTPException(400,'الموظف غير موجود')
    if 'workdays_only' in d:c.execute('INSERT INTO bim_recurring_rules VALUES(?,?) ON CONFLICT(recurring_id) DO UPDATE SET workdays_only=excluded.workdays_only',(rid,int(bool(d['workdays_only']))))
    allowed=['employee_id','title','notes','priority','recurrence_type','week_day','month_day','run_time','is_active']; sets=[]; vals=[]
    for k in allowed:
        if k in d: sets.append(k+'=?'); vals.append(None if k in ('employee_id','week_day','month_day') and d[k] in ('',None) else d[k])
    if sets: sets.append('updated_at=CURRENT_TIMESTAMP'); vals.append(rid); c.execute('UPDATE recurring_tasks SET '+','.join(sets)+' WHERE id=?',vals)
    audit(c,req,u,'recurring_update','recurring',rid,before=dict(old),after=d); c.close(); return {'ok':True}

@app.delete('/api/recurring/{rid}')
def delete_recurring(rid:int, req:Request):
    from .lifecycle import change
    return change(req,'recurring',rid,'delete')


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


# ---------- users / audit / settings ----------
@app.get('/api/audit')
def api_audit(req:Request, limit:int=300):
    need(req,'audit.view'); c=connect(); rows=[dict(x) for x in c.execute('SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?', (min(max(limit,1),1000),))]; c.close(); return rows

@app.get('/api/users')
def api_users(req:Request):
    need(req,'users.manage'); c=connect(); rows=[dict(x) for x in c.execute('''SELECT u.id,u.username,u.full_name,u.employee_id,u.is_active,u.deleted_at,u.last_login_at,u.last_login_ip,(SELECT MAX(a.logout_at) FROM user_session_activity a WHERE a.user_id=u.id) last_logout_at,(SELECT MAX(a.last_seen_at) FROM user_session_activity a WHERE a.user_id=u.id) last_activity_at,r.code role_code,r.name_ar role_name,r.external_access_allowed,(SELECT COUNT(*) FROM supervisor_team st WHERE st.user_id=u.id) team_size FROM users u JOIN roles r ON r.id=u.role_id WHERE u.deleted_at IS NULL ORDER BY u.id''')]; c.close(); return rows

@app.post('/api/users')
async def add_user(req:Request):
    u=need(req,'users.manage'); d=await req.json(); username=str(d.get('username','')).strip(); password=str(d.get('password',''))
    role_code=d.get('role_code','employee')
    if role_code not in access.VISIBLE_ROLES: raise HTTPException(400,'الدور غير صحيح')
    access.require_role_change(perms(u['user_id']),role_code)
    if not username: raise HTTPException(400,'اسم المستخدم مطلوب')
    _check_password(password,role_code)
    c=connect(); role=c.execute('SELECT id FROM roles WHERE code=?',(role_code,)).fetchone()
    if not role: c.close(); raise HTTPException(400,'الدور غير صحيح')
    try:
        c.execute('INSERT INTO users(username,full_name,password_hash,role_id,employee_id,is_active) VALUES(?,?,?,?,?,1)',(username,d.get('full_name') or username,hash_password(password),role['id'],d.get('employee_id') or None)); uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        if d.get('is_active')==0: c.execute('UPDATE users SET is_active=0 WHERE id=?',(uid,))
        audit(c,req,u,'user_create','user',uid,after={k:v for k,v in d.items() if k!='password'})
    except Exception as e: c.close(); raise HTTPException(409,str(e))
    c.close(); return {'ok':True,'id':uid}

@app.patch('/api/users/{uid}')
async def edit_user(uid:int, req:Request):
    u=need(req,'users.manage'); d=await req.json(); c=connect(); old=c.execute('SELECT * FROM users WHERE id=? AND deleted_at IS NULL',(uid,)).fetchone()
    if not old: c.close(); raise HTTPException(404)
    if 'username' in d:
        name=str(d['username']).strip()
        if not name: c.close(); raise HTTPException(400,'اسم المستخدم مطلوب')
    else: name=None
    oldrole=c.execute('SELECT code FROM roles WHERE id=?',(old['role_id'],)).fetchone()['code']
    targetrole=d.get('role_code',oldrole)
    try:
        if targetrole!=oldrole and targetrole not in access.VISIBLE_ROLES: raise HTTPException(400,'الدور غير صحيح')
        access.require_role_change(perms(u['user_id']),oldrole,targetrole)
        if d.get('password'): _check_password(str(d['password']),targetrole)
    except HTTPException: c.close(); raise
    if oldrole=='admin' and (targetrole!='admin' or ('is_active' in d and not bool(d['is_active']))):
        remaining=c.execute("SELECT COUNT(*) FROM users u JOIN roles r ON r.id=u.role_id WHERE r.code='admin' AND u.is_active=1 AND u.deleted_at IS NULL AND u.id<>?",(uid,)).fetchone()[0]
        if not remaining: c.close(); raise HTTPException(409,'لا يمكن تعطيل أو تغيير صلاحية آخر مدير')
    if uid==u['user_id'] and ('is_active' in d and not bool(d['is_active'])):
        c.close(); raise HTTPException(409,'لا يمكنك تعطيل حسابك الحالي')
    sets=[]; vals=[]
    if name is not None: sets.append('username=?');vals.append(name)
    for k in ['full_name','employee_id','is_active']:
        if k in d: sets.append(k+'=?'); vals.append(d[k] or None if k=='employee_id' else d[k])
    if 'role_code' in d:
        rr=c.execute('SELECT id FROM roles WHERE code=?',(d['role_code'],)).fetchone()
        if not rr: c.close(); raise HTTPException(400,'الدور غير صحيح')
        sets.append('role_id=?'); vals.append(rr['id'])
    if d.get('password'): sets.append('password_hash=?'); vals.append(hash_password(str(d['password'])))
    if sets: vals.append(uid); c.execute('UPDATE users SET '+','.join(sets)+' WHERE id=?',vals)
    if d.get('is_active','KEEP') is False or d.get('is_active','KEEP')==0 or d.get('password') or targetrole!=oldrole:
        c.execute('UPDATE login_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND revoked_at IS NULL',(uid,))
    if targetrole!='supervisor': c.execute('DELETE FROM supervisor_team WHERE user_id=?',(uid,))
    after={k:v for k,v in d.items() if k!='password'}
    if d.get('password'): after['password_changed']=True
    audit(c,req,u,'user_role_change' if targetrole!=oldrole else 'user_update','user',uid,before={k:v for k,v in dict(old).items() if k!='password_hash'},after=after); c.close(); return {'ok':True}

@app.delete('/api/users/{uid}')
def archive_user(uid:int, req:Request):
    actor=need(req,'users.manage')
    if uid==actor['user_id']: raise HTTPException(409,'لا يمكن حذف الحساب الجاري استخدامه')
    c=connect()
    old=c.execute('SELECT u.*,r.code role_code FROM users u JOIN roles r ON r.id=u.role_id WHERE u.id=? AND u.deleted_at IS NULL',(uid,)).fetchone()
    if not old: c.close();raise HTTPException(404,'المستخدم غير موجود')
    try: access.require_role_change(perms(actor['user_id']),old['role_code'])
    except HTTPException: c.close(); raise
    if old['role_code']=='admin':
        count=c.execute("SELECT COUNT(*) FROM users u JOIN roles r ON r.id=u.role_id WHERE r.code='admin' AND u.is_active=1 AND u.deleted_at IS NULL AND u.id<>?",(uid,)).fetchone()[0]
        if not count: c.close();raise HTTPException(409,'لا يمكن حذف آخر مدير')
    c.execute('BEGIN IMMEDIATE')
    try:
        c.execute('UPDATE users SET is_active=0,deleted_at=CURRENT_TIMESTAMP WHERE id=?',(uid,))
        c.execute('UPDATE login_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND revoked_at IS NULL',(uid,))
        audit(c,req,actor,'user_archive','user',uid,before={k:v for k,v in dict(old).items() if k!='password_hash'},reason='حذف آمن مع الاحتفاظ بالسجل')
        c.execute('COMMIT')
    except: c.execute('ROLLBACK');c.close();raise
    c.close();return {'ok':True,'method':'archived'}

def _check_password(password:str, role_code:str):
    minimum=12 if role_code in access.MANAGERS else 8
    if len(password)<minimum: raise HTTPException(400,f'كلمة المرور يجب ألا تقل عن {minimum} أحرف لهذا الدور')
    if password.isdigit() or len(set(password))<4: raise HTTPException(400,'كلمة المرور سهلة التخمين؛ استخدم أحرفاً وأرقاماً مختلفة')

@app.get('/api/users/{uid}/team')
def get_supervisor_team(uid:int, req:Request):
    need(req,'users.manage'); c=connect()
    try:
        u=c.execute('SELECT u.id,r.code role_code FROM users u JOIN roles r ON r.id=u.role_id WHERE u.id=? AND u.deleted_at IS NULL',(uid,)).fetchone()
        if not u: raise HTTPException(404)
        return {'user_id':uid,'role_code':u['role_code'],'employee_ids':[r[0] for r in c.execute('SELECT employee_id FROM supervisor_team WHERE user_id=? ORDER BY employee_id',(uid,))]}
    finally: c.close()

@app.put('/api/users/{uid}/team')
async def set_supervisor_team(uid:int, req:Request):
    actor=need(req,'users.manage'); d=await req.json(); raw=d.get('employee_ids')
    if not isinstance(raw,list) or len(raw)>500: raise HTTPException(400,'قائمة الفريق غير صحيحة')
    try: ids=sorted({int(x) for x in raw})
    except (TypeError,ValueError): raise HTTPException(400,'قائمة الفريق غير صحيحة')
    c=connect()
    try:
        c.execute('BEGIN IMMEDIATE')
        u=c.execute('SELECT u.id,r.code role_code FROM users u JOIN roles r ON r.id=u.role_id WHERE u.id=? AND u.deleted_at IS NULL',(uid,)).fetchone()
        if not u: raise HTTPException(404)
        if u['role_code']!='supervisor': raise HTTPException(400,'الفريق يُحدد لحساب «المسؤول» فقط')
        if ids and c.execute(f"SELECT COUNT(*) FROM employees WHERE id IN ({','.join('?' for _ in ids)})",ids).fetchone()[0]!=len(ids): raise HTTPException(400,'أحد الموظفين غير موجود')
        before=[r[0] for r in c.execute('SELECT employee_id FROM supervisor_team WHERE user_id=? ORDER BY employee_id',(uid,))]
        c.execute('DELETE FROM supervisor_team WHERE user_id=?',(uid,))
        c.executemany('INSERT INTO supervisor_team(user_id,employee_id) VALUES(?,?)',[(uid,e) for e in ids])
        audit(c,req,actor,'supervisor_team','user',uid,before={'employee_ids':before},after={'employee_ids':ids})
        c.commit(); return {'ok':True,'employee_ids':ids}
    except Exception: c.rollback(); raise
    finally: c.close()

@app.get('/api/settings')
def api_settings(req:Request):
    need(req,'dashboard.view'); c=connect(); data={r['key']:r['value'] for r in c.execute('SELECT * FROM settings')}; c.close();
    data.update({'gateway':GATEWAY,'public_url':PUBLIC_URL,'domain':DOMAIN,'telegram_configured':bool(os.environ.get('TELEGRAM_BOT_TOKEN','').strip()),'iphone_secret_configured':bool(IPHONE_SECRET)})
    return data


@app.get('/iphone-setup', response_class=HTMLResponse)
def iphone_setup_page(req:Request):
    need(req,'users.manage')
    return templates.TemplateResponse(request=req,name='iphone_setup.html',context=ctx(req,'إعداد اختصارات iPhone'))

@app.get('/api/iphone/setup')
def iphone_setup_api(req:Request):
    need(req,'users.manage')
    if not IPHONE_SECRET:
        raise HTTPException(500,'سر iPhone غير مضبوط في الخادم')
    base=(PUBLIC_URL or 'https://task.bimdamascus.com').rstrip('/')
    return {
        'ok':True,
        'voice_url':base+'/api/iphone/voice',
        'text_url':base+'/api/iphone/text',
        'secret':IPHONE_SECRET,
        'header':'X-BIM-Secret',
        'field':'text'
    }

# ---------- iPhone shortcuts: separate voice and written ----------
async def _iphone_value_to_text(v):
    if v is None:
        return ''
    # Starlette's multipart parser can return its own UploadFile class,
    # which is not always an isinstance(..., fastapi.UploadFile).
    # Detect uploaded text by behavior instead of class identity.
    if hasattr(v,'read') and callable(getattr(v,'read',None)):
        raw=await v.read()
        if isinstance(raw,str):
            return raw.lstrip('\ufeff\u200e\u200f').strip()
        for enc in ('utf-8-sig','utf-8','utf-16','cp1256','latin-1'):
            try:
                return bytes(raw).decode(enc).lstrip('\ufeff\u200e\u200f').strip()
            except Exception:
                pass
        return bytes(raw).decode('utf-8',errors='replace').lstrip('\ufeff\u200e\u200f').strip()
    if isinstance(v,(bytes,bytearray)):
        return bytes(v).decode('utf-8',errors='replace').lstrip('\ufeff\u200e\u200f').strip()
    return str(v).lstrip('\ufeff\u200e\u200f').strip()

async def _iphone_create(req:Request, mode:str='text', x_bim_secret:Optional[str]=None):
    from .iphone_accounts import create
    return await create(req,mode,x_bim_secret,_sys.modules[__name__])

@app.post('/api/iphone/task')
async def iphone_task(req:Request, x_bim_secret:Optional[str]=Header(None)):
    return await _iphone_create(req,'text',x_bim_secret)

@app.post('/api/iphone/voice')
async def iphone_voice(req:Request, x_bim_secret:Optional[str]=Header(None)):
    return await _iphone_create(req,'voice',x_bim_secret)

@app.post('/api/iphone/text')
async def iphone_text(req:Request, x_bim_secret:Optional[str]=Header(None)):
    return await _iphone_create(req,'text',x_bim_secret)

# ---------- two-way Google Sheet synchronization ----------

# ---------- emergency backup ----------
BACKUP_ROOT = Path(os.environ.get('BIM_BACKUP_DIR', r'C:\BIM_Tasks_Backups'))

def _backup_files():
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    files=[]
    for p in sorted(BACKUP_ROOT.glob('*'), key=lambda x:x.stat().st_mtime if x.exists() else 0, reverse=True)[:100]:
        if p.is_file():
            try:
                files.append({'name':p.name,'size':p.stat().st_size,'modified':datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec='seconds')})
            except Exception:
                pass
    return files

@app.get('/backup', response_class=HTMLResponse)
def backup_page(req:Request):
    need(req,'dashboard.view')
    return templates.TemplateResponse(request=req,name='backup.html',context=ctx(req,'النسخ الاحتياطي والطوارئ'))

@app.get('/api/backup/status')
def backup_status(req:Request):
    need(req,'dashboard.view')
    files=_backup_files()
    latest_db=next((x for x in files if x['name'].lower().endswith('.db')),None)
    latest_full=next((x for x in files if x['name'].lower().endswith('.zip')),None)
    return {'ok':True,'backup_dir':str(BACKUP_ROOT),'latest_db':latest_db,'latest_full':latest_full,'files':files[:20]}

@app.post('/api/backup/run')
def backup_run(req:Request, kind:str='db'):
    u=need(req,'users.manage')
    script=ROOT/'tools'/'backup_bim_tasks.py'
    if not script.exists(): raise HTTPException(500,'ملف النسخ الاحتياطي غير موجود')
    mode='full' if str(kind).lower()=='full' else 'db'
    try:
        r=subprocess.run([str(ROOT/'.venv'/'Scripts'/'python.exe'),str(script),mode],
                         cwd=str(ROOT),capture_output=True,text=True,timeout=900)
        if r.returncode!=0:
            raise HTTPException(500,(r.stderr or r.stdout or 'فشل النسخ الاحتياطي')[-1000:])
    except subprocess.TimeoutExpired:
        raise HTTPException(504,'استغرق النسخ الاحتياطي وقتاً أطول من المتوقع')
    c=connect()
    audit(c,req,u,'backup_run','system',None,after={'kind':mode})
    c.close()
    return {'ok':True,'kind':mode,'message':'تم إنشاء النسخة الاحتياطية'}

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

@app.post('/api/bim-employee-profile/{eid}/acknowledge')
def acknowledge_instructions(eid:int,req:Request):
    p=bim_employee_profile(req,eid)
    c=connect()
    try:
        c.execute("INSERT INTO bim_instruction_reads(employee_id,signature) VALUES(?,?) ON CONFLICT(employee_id) DO UPDATE SET signature=excluded.signature,read_at=CURRENT_TIMESTAMP",(eid,p['instructions_signature']))
        return {'ok':True}
    finally:c.close()

@app.get('/notification-center',response_class=HTMLResponse)
def notification_center(req:Request):
    need(req,'users.manage')
    return templates.TemplateResponse(request=req,name='notification_center.html',context=ctx(req,'مركز الإشعارات'))

@app.get('/api/admin-notification-settings')
def admin_notification_settings(req:Request):
    need(req,'users.manage');c=connect()
    try:
        from .admin_notifications import settings
        return settings(c)
    finally:c.close()

@app.put('/api/admin-notification-settings')
async def update_admin_notification_settings(req:Request):
    u=need(req,'users.manage');d=await req.json()
    try:ids=sorted(set(int(x) for x in d.get('employee_ids',[])))
    except (TypeError,ValueError):raise HTTPException(400,'المستلمون غير صحيحين')
    tm=d.get('daily_time','18:00')
    if len(ids)>30 or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',str(tm)):raise HTTPException(400,'راجع المستلمين والوقت')
    c=connect()
    try:
        for eid in ids:
            if not c.execute('SELECT 1 FROM employees WHERE id=? AND is_active=1',(eid,)).fetchone():raise HTTPException(400,'مستلم غير موجود')
        cfg={'enabled':bool(d.get('enabled')),'employee_ids':ids,'daily_enabled':bool(d.get('daily_enabled')),'daily_time':tm}
        c.execute("INSERT INTO settings(key,value) VALUES('bim_admin_notifications',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(cfg),))
        audit(c,req,u,'notification_settings','settings',after=cfg)
        return {'ok':True}
    finally:c.close()

from . import universal
import sys as _sys
universal.register(app, _sys.modules[__name__])

from . import lifecycle
lifecycle.register(app,_sys.modules[__name__])

from . import activity_log
activity_log.register(app,_sys.modules[__name__])

from . import iphone_accounts
iphone_accounts.register(app,_sys.modules[__name__])

from . import delegation
delegation.register(app,_sys.modules[__name__])

from . import command_center
command_center.register(app,_sys.modules[__name__])

from . import security_center
security_center.register(app,_sys.modules[__name__])
