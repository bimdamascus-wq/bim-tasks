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
from . import shell_nav as _shell_nav  # navigation of the page shell (base.html)
templates.env.globals.update(ds_nav=_shell_nav.nav, ds_icon=_shell_nav.icon, ds_titles=_shell_nav.TITLES)
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

_SYNC_AUTH_SEEN={}
def _note_sync_auth(via:str, path:str):
    """Remember (at most once a minute) whether the Sheet authenticates with the header or with ?secret=.
    CHECK_STATUS shows it, so we know when the old ?secret= way is no longer used and can be removed."""
    t=time.time()
    if t-_SYNC_AUTH_SEEN.get(via,0)<60: return
    _SYNC_AUTH_SEEN[via]=t
    try:
        c=connect()
        try: _set_setting(c,f'sync.auth_last_{via}',json.dumps({'at':datetime.now().isoformat(timespec='seconds'),'path':path}))
        finally: c.close()
    except Exception: pass

def _sync_auth(req:Request):
    header=req.headers.get('x-bim-sync-secret','')
    secret=header or req.query_params.get('secret','')
    if not _sync_secret() or not hmac.compare_digest(secret,_sync_secret()):
        raise HTTPException(401,'Sync secret غير صحيح')
    _note_sync_auth('header' if header else 'query',req.url.path)

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


# ---------- routes, by area: app/routes_*.py (split out of this file in package code_split) ----------
# This file keeps settings, middleware and shared helpers. The route modules import what they need from here;
# settings that can change at run time (GATEWAY, RELEASE, secrets, mfa_* set by security_center) they read as
# main.X. Every name is imported back so `from .main import x` and `m.x` in the other modules keep working.
from .routes_auth import (  # noqa: E402,F401
    _open_session, health, login, login_page, logout, manifest, sw
)
from .routes_pages import (  # noqa: E402,F401
    _hidden_pages, api_dashboard, api_me, archive_page, audit_page, client_status, dashboard, employees_page,
    health_page, integrations_page, manage_page, my_work, recurring_page, release_health, task_detail_page,
    tasks_page, tasks_table_page, users_page
)
from .routes_telegram import (  # noqa: E402,F401
    _guard_manager_telegram, api_telegram_dashboard, api_telegram_health, employee_telegram_link,
    employee_telegram_unlink, telegram_center_page, telegram_dashboard_page, telegram_health_page,
    telegram_queue_list, telegram_queue_retry, telegram_queue_retry_failed, telegram_report_csv,
    telegram_setup_webhook, telegram_status_api, telegram_webhook
)
from .routes_shifts import (  # noqa: E402,F401
    _SHIFT_SETTING_KEYS, _next_group_send_at, _next_group_send_at_unbounded, _shift_test_matches,
    add_group_reminder, add_telegram_group, api_group_reminders, api_telegram_groups, automation_center_page,
    automation_summary, create_shift_message, delete_group_reminder, delete_shift_message,
    delete_telegram_group, edit_group_reminder, edit_telegram_group, get_shift_messages, get_shift_settings,
    group_dispatch_log, run_shift_test, save_shift_settings, shift_center_page, shift_dispatch_log,
    shift_exception_add, shift_exception_delete, shift_exceptions_list, shift_test_employees,
    telegram_group_link_code, telegram_group_unlink, telegram_groups_page, update_shift_message
)
from .routes_reports import (  # noqa: E402,F401
    operations_report, operations_report_tasks, operations_reports_page
)
from .routes_employees import (  # noqa: E402,F401
    acknowledge_instructions, add_employee, api_employees, api_task_employee_options, bim_employee_profile,
    bim_employee_profile_update, bim_job_profiles_create, bim_job_profiles_list, bim_job_profiles_page,
    bim_job_profiles_update, edit_employee
)
from .routes_tasks import (  # noqa: E402,F401
    add_task_attachment, add_task_comment, api_task, api_tasks, change_status, create_task, delete_task,
    edit_task, get_attachment, participant_status, quick_voice_task, resend_telegram, restore_task
)
from .routes_recurring import (  # noqa: E402,F401
    add_recurring, api_recurring, delete_recurring, edit_recurring
)
from .routes_users import (  # noqa: E402,F401
    _check_password, _iphone_create, _iphone_value_to_text, add_user, admin_notification_settings, api_audit,
    api_settings, api_users, archive_user, edit_user, get_supervisor_team, iphone_setup_api,
    iphone_setup_page, iphone_task, iphone_text, iphone_voice, notification_center, set_supervisor_team,
    update_admin_notification_settings
)
from .routes_backup import (  # noqa: E402,F401
    BACKUP_ROOT, _backup_files, backup_page, backup_run, backup_status
)
from .routes_sync import (  # noqa: E402,F401
    sync_conflicts, sync_page, sync_pull, sync_push_employee, sync_push_task, sync_retry_dirty, sync_settings,
    sync_snapshot, sync_status, sync_tasks_page, sync_test_webapp
)


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
