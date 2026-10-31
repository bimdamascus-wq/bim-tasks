"""Public routes (health, manifest, service worker) and login / logout / sessions.
Split out of app/main.py (package code_split) with no change in behaviour; main.py imports this module."""
from __future__ import annotations
from fastapi import Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from typing import Optional
from . import access
from .db import connect
from .security import iso, new_csrf_token, new_session_id, now, session_expiry, verify_password
from .universal import channel
from . import main as _main  # settings that tests or plugins may change at run time are read from main
from .main import (
    APP_VERSION, COOKIE, DOMAIN, LOGIN_MAX_FAILS_IP, LOGIN_MAX_FAILS_USER, LOGIN_WINDOW_MINUTES, ROOT, app,
    ctx, gateway_of, ip, session, templates
)

# ---------- public/static ----------
@app.get('/health')
def health():
    c = connect();
    try:
        db_ok = c.execute('SELECT 1').fetchone()[0] == 1
        tasks = c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
    finally: c.close()
    return {'status': 'ok' if db_ok else 'error', 'system': 'BIM Tasks', 'version': APP_VERSION,
            'gateway': _main.GATEWAY, 'domain': DOMAIN, 'public_url': _main.PUBLIC_URL, 'tasks': tasks}

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
    if gw == 'external' and _main.mfa_required(c, u['id']):
        try: return _main.mfa_login_challenge(c, req, u, gw)
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
    resp.set_cookie(COOKIE, sid, httponly=True, secure=_main.COOKIE_SECURE or gw == 'external', samesite='lax', max_age=43200)
    return resp

@app.get('/logout')
def logout(req: Request):
    sid = req.cookies.get(COOKIE); actor=session(req); c = connect()
    if actor:
        from .activity_log import auth_event
        auth_event(c,req,actor,sid,'logout',_main.GATEWAY,ip(req))
    c.execute('UPDATE login_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE id=?', (sid,)); c.close()
    resp = RedirectResponse('/login', 303); resp.delete_cookie(COOKIE); return resp
