"""Users, supervisor teams, audit log, settings, iPhone setup and shortcuts, admin notification settings.
Split out of app/main.py (package code_split) with no change in behaviour; main.py imports this module."""
from __future__ import annotations
import json
import os
import re
from fastapi import HTTPException, Header, Request
from fastapi.responses import HTMLResponse
from typing import Optional
from . import access
from .db import connect
from .security import hash_password
from . import main as _main  # settings that tests or plugins may change at run time are read from main
from .main import (
    DOMAIN, app, audit, ctx, need, perms, templates
)

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
    data.update({'gateway':_main.GATEWAY,'public_url':_main.PUBLIC_URL,'domain':DOMAIN,'telegram_configured':bool(os.environ.get('TELEGRAM_BOT_TOKEN','').strip()),'iphone_secret_configured':bool(_main.IPHONE_SECRET)})
    return data


@app.get('/iphone-setup', response_class=HTMLResponse)
def iphone_setup_page(req:Request):
    need(req,'users.manage')
    return templates.TemplateResponse(request=req,name='iphone_setup.html',context=ctx(req,'إعداد اختصارات iPhone'))

@app.get('/api/iphone/setup')
def iphone_setup_api(req:Request):
    need(req,'users.manage')
    if not _main.IPHONE_SECRET:
        raise HTTPException(500,'سر iPhone غير مضبوط في الخادم')
    base=(_main.PUBLIC_URL or 'https://task.bimdamascus.com').rstrip('/')
    return {
        'ok':True,
        'voice_url':base+'/api/iphone/voice',
        'text_url':base+'/api/iphone/text',
        'secret':_main.IPHONE_SECRET,
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
    return await create(req,mode,x_bim_secret,_main)

@app.post('/api/iphone/task')
async def iphone_task(req:Request, x_bim_secret:Optional[str]=Header(None)):
    return await _iphone_create(req,'text',x_bim_secret)

@app.post('/api/iphone/voice')
async def iphone_voice(req:Request, x_bim_secret:Optional[str]=Header(None)):
    return await _iphone_create(req,'voice',x_bim_secret)

@app.post('/api/iphone/text')
async def iphone_text(req:Request, x_bim_secret:Optional[str]=Header(None)):
    return await _iphone_create(req,'text',x_bim_secret)

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
