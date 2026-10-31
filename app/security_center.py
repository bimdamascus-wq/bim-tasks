"""Phase 2 security: Telegram login confirmation, tamper-evident audit chain,
sensitive-operation log, password self-service, off-site backup scheduling and
the entry-point review shown on /security."""
import hashlib, hmac, json, os, secrets, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from .db import connect
from . import access, backup_offsite

log = logging.getLogger('bim.security')
MFA_COOKIE = 'bim_mfa'
MFA_TTL_MINUTES = 5
MFA_MAX_ATTEMPTS = 5
MFA_MAX_PER_WINDOW = 5
GENESIS = 'GENESIS'

# Audit actions shown on the sensitive-operations log, with an Arabic label.
SENSITIVE = {
    'user_create': 'إنشاء حساب', 'user_update': 'تعديل حساب', 'user_role_change': 'تغيير دور حساب',
    'user_archive': 'حذف حساب', 'user_permissions': 'تعديل صلاحيات فردية', 'supervisor_team': 'تعديل فريق مسؤول',
    'password_change_self': 'تغيير كلمة المرور', 'mfa_enable': 'تفعيل تأكيد الدخول', 'mfa_disable': 'إيقاف تأكيد الدخول',
    'mfa_reset': 'تصفير تأكيد الدخول لحساب', 'backup_run': 'نسخة احتياطية محلية', 'offsite_backup': 'نسخة خارج السيرفر',
    'offsite_backup_failed': 'فشل نسخة خارج السيرفر', 'backup_settings': 'تعديل إعدادات النسخ', 'backup_restore': 'استرجاع نسخة احتياطية',
    'sync_settings': 'تعديل رابط مزامنة Google Sheet', 'telegram_webhook_setup': 'إعداد Webhook تلغرام',
    'telegram_link_generate': 'رابط ربط Telegram', 'telegram_unlink': 'فك ربط Telegram',
    'iphone_key_issue': 'إصدار مفتاح iPhone', 'iphone_key_revoke': 'إلغاء مفتاح iPhone', 'iphone_owner_set': 'تغيير مالك اختصار الإدارة',
    'page_visibility': 'تعديل الصفحات الظاهرة', 'company_settings': 'تعديل إعداد الشركة', 'handover': 'تسليم أعمال موظف',
    'staff_channel': 'تغيير قناة موظف', 'task_archive': 'أرشفة مهمة',
}
SENSITIVE_PREFIXES = ('record_',)


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS user_mfa(
      user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      enabled INTEGER NOT NULL DEFAULT 0, enabled_at TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS mfa_challenges(
      id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      purpose TEXT NOT NULL CHECK(purpose IN ('login','enable','disable')), code_hash TEXT NOT NULL,
      gateway TEXT, ip_address TEXT, attempts INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, expires_at TEXT NOT NULL, used_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_mfa_challenges_user ON mfa_challenges(user_id,created_at);
    ''')
    cols = {r[1] for r in c.execute('PRAGMA table_info(audit_logs)')}
    if 'prev_hash' not in cols: c.execute('ALTER TABLE audit_logs ADD COLUMN prev_hash TEXT')
    if 'row_hash' not in cols: c.execute('ALTER TABLE audit_logs ADD COLUMN row_hash TEXT')


# ---------- tamper-evident audit log ----------
def _chain(prev, payload):
    return hashlib.sha256(((prev or GENESIS) + '|' + payload).encode('utf-8')).hexdigest()


def _payload(row):
    return json.dumps([row['user_id'], row['username_snapshot'], row['action'], row['entity_type'], row['entity_id'],
                       row['before_json'], row['after_json'], row['reason'], row['gateway'], row['ip_address'],
                       row['created_at']], ensure_ascii=False)


def append_audit(c, row: dict):
    """One INSERT statement reads the previous hash and writes the new one under SQLite's
    write lock, so two gateway processes can never fork the chain."""
    c.create_function('bim_chain', 2, _chain, deterministic=True)
    row = dict(row, created_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
    cols = ['user_id', 'username_snapshot', 'action', 'entity_type', 'entity_id', 'before_json', 'after_json',
            'reason', 'gateway', 'ip_address', 'created_at']
    prev = "(SELECT row_hash FROM audit_logs WHERE row_hash IS NOT NULL ORDER BY id DESC LIMIT 1)"
    c.execute(f"INSERT INTO audit_logs({','.join(cols)},prev_hash,row_hash) VALUES({','.join('?' for _ in cols)},"
              f"COALESCE({prev},'{GENESIS}'),bim_chain({prev},?))", [row[k] for k in cols] + [_payload(row)])


def verify_chain(c, limit=None):
    """Returns (ok, checked, first_bad_id). Rows from before this upgrade have no hash and are skipped."""
    prev, checked = GENESIS, 0
    for r in c.execute('SELECT * FROM audit_logs WHERE row_hash IS NOT NULL ORDER BY id'):
        if r['prev_hash'] != prev or r['row_hash'] != _chain(prev, _payload(r)):
            return False, checked, r['id']
        prev, checked = r['row_hash'], checked + 1
    return True, checked, None


def is_sensitive(action):
    return action in SENSITIVE or action.startswith(SENSITIVE_PREFIXES)


def sensitive_rows(c, limit=200):
    marks = ','.join('?' for _ in SENSITIVE)
    rows = []
    for r in c.execute(f"""SELECT id,username_snapshot,action,entity_type,entity_id,reason,gateway,ip_address,created_at,after_json
        FROM audit_logs WHERE action IN ({marks}) OR action LIKE 'record\\_%' ESCAPE '\\' ORDER BY id DESC LIMIT ?""",
                       list(SENSITIVE) + [int(limit)]):
        d = dict(r)
        d['label'] = SENSITIVE.get(r['action']) or {'record_delete': 'حذف سجل', 'record_disable': 'تعطيل سجل',
                                                     'record_enable': 'تفعيل سجل', 'record_restore': 'استعادة سجل'}.get(r['action'], r['action'])
        try:
            after = json.loads(r['after_json'] or '{}')
            d['detail'] = ', '.join(f'{k}: {v}' for k, v in after.items() if k in ('role_code', 'employee_ids', 'file', 'error', 'kind', 'username', 'full_name', 'is_active', 'password_changed'))[:300]
        except Exception:
            d['detail'] = ''
        d.pop('after_json', None)
        rows.append(d)
    return rows


# ---------- Telegram login confirmation ----------
def _now():
    return datetime.now(timezone.utc)


def _ts(d):
    return d.strftime('%Y-%m-%d %H:%M:%S')


def _code_hash(cid, code):
    return hashlib.sha256(f'{cid}:{code}'.encode()).hexdigest()


def mfa_enabled(c, uid):
    r = c.execute('SELECT enabled FROM user_mfa WHERE user_id=?', (uid,)).fetchone()
    return bool(r and r['enabled'])


def telegram_chat(c, uid):
    r = c.execute('''SELECT e.telegram_chat_id FROM users u JOIN employees e ON e.id=u.employee_id
                     WHERE u.id=? AND e.is_active=1''', (uid,)).fetchone()
    chat = (r['telegram_chat_id'] if r else '') or ''
    return chat.strip()


def send_code(c, uid, purpose, gateway, ip):
    """Create a 6-digit challenge and send it directly (never via the outbox, so the code is not stored)."""
    if c.execute("SELECT COUNT(*) FROM mfa_challenges WHERE user_id=? AND created_at>?",
                 (uid, _ts(_now() - timedelta(minutes=15)))).fetchone()[0] >= MFA_MAX_PER_WINDOW:
        raise HTTPException(429, 'طلبات رموز كثيرة؛ حاول بعد 15 دقيقة')
    chat = telegram_chat(c, uid)
    if not chat:
        raise HTTPException(409, 'هذا الحساب غير مربوط بتلغرام؛ اربط الموظف المرتبط بالحساب أولاً')
    cid, code = secrets.token_urlsafe(24), f'{secrets.randbelow(10**6):06d}'
    c.execute('UPDATE mfa_challenges SET used_at=? WHERE user_id=? AND purpose=? AND used_at IS NULL', (_ts(_now()), uid, purpose))
    c.execute('INSERT INTO mfa_challenges(id,user_id,purpose,code_hash,gateway,ip_address,expires_at) VALUES(?,?,?,?,?,?,?)',
              (cid, uid, purpose, _code_hash(cid, code), gateway, ip, _ts(_now() + timedelta(minutes=MFA_TTL_MINUTES))))
    why = {'login': 'تأكيد دخول من خارج الشركة', 'enable': 'تفعيل تأكيد الدخول', 'disable': 'إيقاف تأكيد الدخول'}[purpose]
    from .telegram_service import send
    r = send(chat, f'🔐 BIM Tasks — {why}\n\nالرمز: {code}\nصالح {MFA_TTL_MINUTES} دقائق. العنوان: {ip}\n\n'
                   'إذا لم تطلب هذا الرمز فلا تشاركه مع أحد وغيّر كلمة المرور فوراً.')
    if not r.get('ok'):
        c.execute('UPDATE mfa_challenges SET used_at=? WHERE id=?', (_ts(_now()), cid))
        raise HTTPException(502, 'تعذر إرسال الرمز على Telegram؛ لم يتم الدخول. استخدم شبكة الشركة أو أعد المحاولة')
    return cid


def check_code(c, cid, code, purpose, uid=None):
    """Returns the challenge row on success; burns it after too many wrong codes."""
    r = c.execute('SELECT * FROM mfa_challenges WHERE id=? AND purpose=?', (cid or '', purpose)).fetchone()
    if not r or r['used_at'] or (uid is not None and r['user_id'] != uid):
        raise HTTPException(400, 'الرمز منتهي؛ اطلب رمزاً جديداً')
    if datetime.strptime(r['expires_at'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc) < _now():
        raise HTTPException(400, 'انتهت صلاحية الرمز؛ اطلب رمزاً جديداً')
    code = ''.join(ch for ch in str(code or '') if ch.isdigit())
    if not hmac.compare_digest(_code_hash(cid, code), r['code_hash']):
        attempts = r['attempts'] + 1
        c.execute('UPDATE mfa_challenges SET attempts=?,used_at=CASE WHEN ?>=? THEN ? ELSE used_at END WHERE id=?',
                  (attempts, attempts, MFA_MAX_ATTEMPTS, _ts(_now()), cid))
        left = MFA_MAX_ATTEMPTS - attempts
        raise HTTPException(401, f'الرمز غير صحيح. المحاولات المتبقية: {left}' if left > 0 else 'تم إيقاف هذا الرمز بعد محاولات خاطئة؛ سجّل الدخول من جديد')
    c.execute('UPDATE mfa_challenges SET used_at=? WHERE id=?', (_ts(_now()), cid))
    return r


# ---------- off-site backup scheduling ----------
def backup_config(c):
    def s(k, d=''):
        r = c.execute('SELECT value FROM settings WHERE key=?', (k,)).fetchone()
        return (r['value'] if r else None) or d
    return {'folder': s('backup.offsite_dir', os.environ.get('BIM_OFFSITE_DIR', '')).strip(),
            'hour': int(s('backup.offsite_hour', '2')), 'keep': int(s('backup.offsite_keep', '30')),
            'passphrase_set': len(os.environ.get('BIM_BACKUP_PASSPHRASE', '')) >= 16,
            'last': json.loads(s('backup.offsite_last', '{}'))}


def _store_last(c, result):
    c.execute("INSERT INTO settings(key,value) VALUES('backup.offsite_last',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (json.dumps(result, ensure_ascii=False),))


def run_offsite_backup(actor=None, gateway='lan', ip='server'):
    c = connect()
    try:
        cfg = backup_config(c)
        at = datetime.now().isoformat(timespec='seconds')
        identity = actor or {'user_id': None, 'username': 'النسخ التلقائي اليومي'}
        try:
            result = backup_offsite.run(cfg['folder'], os.environ.get('BIM_BACKUP_PASSPHRASE', ''), cfg['keep'])
            result['at'] = at
            prev_ok = cfg['last'].get('last_ok_at')
            _store_last(c, {**result, 'last_ok_at': at, 'last_attempt_at': at})
            append_audit(c, _audit_row(identity, 'offsite_backup', {'file': result['file'], 'size': result['size']}, gateway, ip))
            return result
        except Exception as e:
            msg = str(e) if isinstance(e, backup_offsite.BackupError) else f'{type(e).__name__}: {e}'
            _store_last(c, {'ok': False, 'error': msg, 'at': at, 'last_attempt_at': at, 'last_ok_at': cfg['last'].get('last_ok_at')})
            append_audit(c, _audit_row(identity, 'offsite_backup_failed', {'error': msg[:300]}, gateway, ip))
            _alert_admins(c, '⚠️ BIM Tasks: فشلت النسخة الاحتياطية خارج السيرفر\n\n' + msg[:500])
            raise
    finally:
        c.close()


def _audit_row(u, action, after, gateway, ip):
    u = dict(u)
    return {'user_id': u.get('user_id'), 'username_snapshot': u.get('username'), 'action': action, 'entity_type': 'system',
            'entity_id': None, 'before_json': None, 'after_json': json.dumps(after, ensure_ascii=False),
            'reason': None, 'gateway': gateway, 'ip_address': ip}


def _alert_admins(c, text):
    try:
        from .telegram_queue import send_resilient
        for r in c.execute('''SELECT DISTINCT e.telegram_chat_id FROM users u JOIN roles r ON r.id=u.role_id JOIN employees e ON e.id=u.employee_id
            WHERE r.code='admin' AND u.is_active=1 AND u.deleted_at IS NULL AND e.telegram_chat_id IS NOT NULL AND trim(e.telegram_chat_id)<>'' '''):
            send_resilient(r[0], text, source_type='security_alert')
    except Exception:
        log.exception('Security alert could not be sent')


def process_offsite_backup():
    """Called by the LAN worker every 30 s: one verified off-site copy per day, retry hourly after a failure."""
    c = connect()
    try:
        cfg = backup_config(c)
        if not cfg['folder']:
            return
        now = datetime.now()
        last = cfg['last']
        if (last.get('last_ok_at') or '')[:10] == now.strftime('%Y-%m-%d') or now.hour < cfg['hour']:
            return
        if last.get('last_attempt_at'):
            try:
                if now - datetime.fromisoformat(last['last_attempt_at']) < timedelta(hours=1):
                    return
            except ValueError:
                pass
    finally:
        c.close()
    try:
        run_offsite_backup()
    except Exception as e:
        print('Off-site backup failed:', e)


# ---------- entry-point review ----------
def review(m, c):
    """Live checks shown on /security. Each item: (key, ok, title, detail)."""
    env = os.environ
    iph = m.IPHONE_SECRET or ''
    cfg = backup_config(c)
    last_ok = cfg['last'].get('last_ok_at')
    fresh = False
    if last_ok:
        try:
            fresh = datetime.now() - datetime.fromisoformat(last_ok) < timedelta(hours=26)
        except ValueError:
            pass
    managers = c.execute("""SELECT u.id,u.username,r.code role_code,COALESCE(f.enabled,0) mfa
        FROM users u JOIN roles r ON r.id=u.role_id LEFT JOIN user_mfa f ON f.user_id=u.id
        WHERE r.code IN ('admin','deputy') AND u.is_active=1 AND u.deleted_at IS NULL""").fetchall()
    weak_admin = c.execute("SELECT COUNT(*) FROM users WHERE lower(username)='admin' AND is_active=1 AND deleted_at IS NULL").fetchone()[0]
    items = [
        ('web', True, 'الموقع: كل صفحة وكل API تتحقق من الجلسة والصلاحية على السيرفر',
         'رفض افتراضي لأي مسار غير مسجّل كمسار عام، وفحص صلاحية الصفحة حسب جدول الصلاحيات، وحماية من الطلبات القادمة من مواقع أخرى.'),
        ('cookie', m.COOKIE_SECURE or m.GATEWAY == 'lan', 'ملف الجلسة آمن على البوابة الخارجية',
         'BIM_COOKIE_SECURE=1 على البوابة الخارجية' if m.GATEWAY == 'external' else 'البوابة الخارجية تفرض Secure تلقائياً لكل دخول خارجي.'),
        ('telegram', bool(m.TELEGRAM_WEBHOOK_SECRET) and len(m.TELEGRAM_WEBHOOK_SECRET) >= 24, 'Telegram: كل رسالة واردة تحمل السر الصحيح',
         'BIM_TELEGRAM_WEBHOOK_SECRET مضبوط' if m.TELEGRAM_WEBHOOK_SECRET else 'BIM_TELEGRAM_WEBHOOK_SECRET غير مضبوط: الـ webhook مرفوض بالكامل حتى تضبطه.'),
        ('iphone', not iph or (len(iph) >= 32 and iph != 'CHANGE_ME_LONG_RANDOM_SECRET'), 'الآيفون: المفتاح المشترك قوي',
         'المفتاح المشترك القديم ضعيف أو افتراضي؛ استبدله أو اعتمد مفاتيح الحسابات الفردية.' if iph and (len(iph) < 32 or iph == 'CHANGE_ME_LONG_RANDOM_SECRET') else 'مفاتيح فردية لكل حساب + حد محاولات خاطئة لكل عنوان.'),
        ('sheet', len(env.get('BIM_SYNC_SECRET', '')) >= 24, 'Google Sheet: سر المزامنة قوي',
         'سر المزامنة أقل من 24 حرفاً أو غير مضبوط.' if len(env.get('BIM_SYNC_SECRET', '')) < 24 else 'المزامنة لا تستطيع تغيير ربط Telegram لموظف موجود أو تعطيل موظف مرتبط بحساب إدارة.'),
        ('mfa', all(r['mfa'] for r in managers) and bool(managers), 'تأكيد الدخول من الخارج مفعّل لكل حسابات الإدارة',
         'غير مفعّل لـ: ' + '، '.join(r['username'] for r in managers if not r['mfa']) if any(not r['mfa'] for r in managers) else 'مفعّل.'),
        ('admin_name', not weak_admin, 'لا يوجد حساب باسم admin الافتراضي', 'غيّر اسم الدخول admin لاسم غير متوقع.' if weak_admin else 'جيد.'),
        ('backup_folder', bool(cfg['folder']), 'مجلد نسخ خارج السيرفر محدد', cfg['folder'] or 'غير محدد'),
        ('backup_key', cfg['passphrase_set'], 'كلمة سر تشفير النسخ مضبوطة (BIM_BACKUP_PASSPHRASE)', 'مضبوطة — احتفظ بنسخة ورقية منها خارج الشركة.' if cfg['passphrase_set'] else 'غير مضبوطة أو أقل من 16 حرفاً؛ لن تُنشأ نسخ خارجية بدونها.'),
        ('backup_fresh', fresh, 'آخر نسخة خارج السيرفر خلال 26 ساعة', last_ok or 'لا توجد نسخة ناجحة بعد'),
    ]
    ok, checked, bad = verify_chain(c)
    items.append(('audit_chain', ok, 'سجل العمليات الحساسة سليم وغير معدّل',
                  f'تم التحقق من {checked} سجل.' if ok else f'انقطاع في السلسلة عند السجل رقم {bad}: تم تعديل أو حذف سجلات.'))
    return [{'key': k, 'ok': bool(o), 'title': t, 'detail': d} for k, o, t, d in items]


def register(app, m):
    def login_challenge(c, req, u, gw):
        """Called from /login after the password is verified on the external gateway."""
        try:
            cid = send_code(c, u['id'], 'login', gw, m.ip(req))
        except HTTPException as e:
            from .activity_log import failed_login
            failed_login(c, req, u, u['username'], gw, m.ip(req), status=e.status_code)
            return m.templates.TemplateResponse(request=req, name='login.html', context={**m.ctx(req, 'تسجيل الدخول'), 'error': e.detail}, status_code=e.status_code)
        resp = RedirectResponse('/login/verify', 303)
        resp.set_cookie(MFA_COOKIE, cid, httponly=True, secure=True if gw == 'external' else m.COOKIE_SECURE, samesite='strict', max_age=MFA_TTL_MINUTES * 60, path='/login')
        return resp
    m.mfa_login_challenge = login_challenge
    m.mfa_required = lambda c, uid: mfa_enabled(c, uid)

    @app.get('/login/verify', response_class=HTMLResponse)
    def verify_page(req: Request, error: str | None = None):
        if not req.cookies.get(MFA_COOKIE):
            return RedirectResponse('/login', 303)
        return m.templates.TemplateResponse(request=req, name='login_verify.html', context={**m.ctx(req, 'تأكيد الدخول'), 'error': error})

    @app.post('/login/verify')
    def verify_submit(req: Request, code: str = Form(...)):
        cid = req.cookies.get(MFA_COOKIE)
        gw = m.gateway_of(req)
        c = connect()
        try:
            ch = c.execute('SELECT user_id,gateway FROM mfa_challenges WHERE id=?', (cid or '',)).fetchone()
            try:
                if not ch or ch['gateway'] != gw:
                    raise HTTPException(400, 'انتهت محاولة الدخول؛ سجّل الدخول من جديد')
                check_code(c, cid, code, 'login')
            except HTTPException as e:
                u = c.execute('SELECT id,username FROM users WHERE id=?', (ch['user_id'],)).fetchone() if ch else None
                from .activity_log import record
                record(c, {'user_id': u['id'] if u else None, 'username': u['username'] if u else ''}, 'login',
                       'رمز تأكيد دخول خاطئ أو منتهي', gw, m.ip(req), result='denied', status=e.status_code)
                if e.status_code == 401 and 'المتبقية' in str(e.detail):
                    return m.templates.TemplateResponse(request=req, name='login_verify.html', context={**m.ctx(req, 'تأكيد الدخول'), 'error': e.detail}, status_code=401)
                resp = m.templates.TemplateResponse(request=req, name='login.html', context={**m.ctx(req, 'تسجيل الدخول'), 'error': e.detail}, status_code=e.status_code)
                resp.delete_cookie(MFA_COOKIE, path='/login'); return resp
            u = c.execute('''SELECT u.*,r.code role_code,r.external_access_allowed FROM users u JOIN roles r ON r.id=u.role_id
                WHERE u.id=? AND u.is_active=1 AND u.deleted_at IS NULL''', (ch['user_id'],)).fetchone()
            if not u or (gw == 'external' and not u['external_access_allowed']):
                return RedirectResponse('/login', 303)
            resp = m._open_session(c, req, u, gw)
            resp.delete_cookie(MFA_COOKIE, path='/login')
            return resp
        finally:
            c.close()

    # ----- my account: password and login confirmation -----
    @app.get('/account', response_class=HTMLResponse)
    def account_page(req: Request):
        u = m.session(req)
        if not u: return RedirectResponse('/login', 303)
        return m.templates.TemplateResponse(request=req, name='account.html', context=m.ctx(req, 'حسابي والأمان'))

    @app.get('/api/me/security')
    def my_security(req: Request):
        u = m.session(req)
        if not u: raise HTTPException(401)
        c = connect()
        try:
            return {'role_code': u['role_code'], 'mfa_available': u['role_code'] in access.MANAGERS,
                    'mfa_enabled': mfa_enabled(c, u['user_id']), 'telegram_linked': bool(telegram_chat(c, u['user_id'])),
                    'gateway': m.gateway_of(req)}
        finally:
            c.close()

    @app.post('/api/me/password')
    async def change_my_password(req: Request):
        u = m.session(req)
        if not u: raise HTTPException(401)
        d = await req.json(); current = str(d.get('current') or ''); new = str(d.get('new') or '')
        c = connect()
        try:
            row = c.execute('SELECT password_hash FROM users WHERE id=?', (u['user_id'],)).fetchone()
            if not row or not m.verify_password(row['password_hash'], current):
                raise HTTPException(400, 'كلمة المرور الحالية غير صحيحة')
            if new == current: raise HTTPException(400, 'اختر كلمة مرور مختلفة عن الحالية')
            m._check_password(new, u['role_code'])
            c.execute('BEGIN IMMEDIATE')
            c.execute('UPDATE users SET password_hash=? WHERE id=?', (m.hash_password(new), u['user_id']))
            # End every other session; keep the one that just changed the password.
            c.execute('UPDATE login_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND revoked_at IS NULL AND id<>?', (u['user_id'], req.cookies.get(m.COOKIE)))
            m.audit(c, req, u, 'password_change_self', 'user', u['user_id'], after={'password_changed': True})
            c.commit(); return {'ok': True}
        except Exception:
            if c.in_transaction: c.rollback()
            raise
        finally:
            c.close()

    @app.post('/api/me/mfa/start')
    async def mfa_start(req: Request):
        u = m.session(req)
        if not u: raise HTTPException(401)
        if u['role_code'] not in access.MANAGERS: raise HTTPException(403, 'تأكيد الدخول متاح لحسابات الإدارة')
        d = await req.json(); action = d.get('action')
        if action not in ('enable', 'disable'): raise HTTPException(400)
        c = connect()
        try:
            if action == 'disable' and m.gateway_of(req) == 'lan':
                # Lost phone recovery: inside the company the account password is enough.
                row = c.execute('SELECT password_hash FROM users WHERE id=?', (u['user_id'],)).fetchone()
                if not m.verify_password(row['password_hash'], str(d.get('password') or '')):
                    raise HTTPException(400, 'كلمة المرور غير صحيحة')
                c.execute('INSERT INTO user_mfa(user_id,enabled,updated_at) VALUES(?,0,CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO UPDATE SET enabled=0,updated_at=CURRENT_TIMESTAMP', (u['user_id'],))
                m.audit(c, req, u, 'mfa_disable', 'user', u['user_id'], after={'via': 'lan_password'})
                return {'ok': True, 'done': True, 'enabled': False}
            cid = send_code(c, u['user_id'], action, m.gateway_of(req), m.ip(req))
            return {'ok': True, 'challenge': cid}
        finally:
            c.close()

    @app.post('/api/me/mfa/confirm')
    async def mfa_confirm(req: Request):
        u = m.session(req)
        if not u: raise HTTPException(401)
        d = await req.json(); cid = str(d.get('challenge') or '')
        c = connect()
        try:
            r = c.execute('SELECT purpose FROM mfa_challenges WHERE id=? AND user_id=?', (cid, u['user_id'])).fetchone()
            if not r or r['purpose'] not in ('enable', 'disable'): raise HTTPException(400, 'اطلب رمزاً جديداً')
            check_code(c, cid, d.get('code'), r['purpose'], u['user_id'])
            on = r['purpose'] == 'enable'
            c.execute('''INSERT INTO user_mfa(user_id,enabled,enabled_at,updated_at) VALUES(?,?,CASE WHEN ? THEN CURRENT_TIMESTAMP END,CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled,enabled_at=COALESCE(excluded.enabled_at,user_mfa.enabled_at),updated_at=CURRENT_TIMESTAMP''',
                      (u['user_id'], int(on), int(on)))
            m.audit(c, req, u, 'mfa_enable' if on else 'mfa_disable', 'user', u['user_id'], after={'via': 'telegram_code'})
            return {'ok': True, 'enabled': on}
        finally:
            c.close()

    # ----- administrator security center -----
    @app.get('/security', response_class=HTMLResponse)
    def security_page(req: Request):
        m.need(req, 'users.manage')
        return m.templates.TemplateResponse(request=req, name='security.html', context=m.ctx(req, 'الأمان والحماية'))

    @app.get('/api/security/overview')
    def overview(req: Request):
        u = m.need(req, 'users.manage')
        c = connect()
        try:
            cfg = backup_config(c)
            denied = [dict(r) for r in c.execute("""SELECT username,label,gateway,ip_address,http_status,created_at FROM user_activity
                WHERE kind='login' AND result='denied' ORDER BY id DESC LIMIT 30""")]
            mfa = [dict(r) for r in c.execute("""SELECT u.id,u.username,u.full_name,r.code role_code,COALESCE(f.enabled,0) enabled,f.enabled_at,
                CASE WHEN e.telegram_chat_id IS NOT NULL AND trim(e.telegram_chat_id)<>'' THEN 1 ELSE 0 END telegram_linked
                FROM users u JOIN roles r ON r.id=u.role_id LEFT JOIN user_mfa f ON f.user_id=u.id LEFT JOIN employees e ON e.id=u.employee_id
                WHERE r.code IN ('admin','deputy') AND u.is_active=1 AND u.deleted_at IS NULL ORDER BY r.id,u.username""")]
            return {'checks': review(m, c), 'mfa': mfa, 'denied_logins': denied, 'sensitive': sensitive_rows(c, 100),
                    'backup': {k: v for k, v in cfg.items()}, 'offsite_files': backup_offsite.list_backups(Path(cfg['folder'])) if cfg['folder'] else [],
                    'can_change': 'security.manage' in m.perms(u['user_id'])}
        finally:
            c.close()

    @app.put('/api/security/backup-settings')
    async def backup_settings(req: Request):
        u = m.need(req, 'security.manage'); d = await req.json()
        folder = str(d.get('folder') or '').strip()
        try:
            hour = int(d.get('hour', 2)); keep = int(d.get('keep', 30))
        except (TypeError, ValueError):
            raise HTTPException(400, 'الساعة أو عدد النسخ غير صحيح')
        if not 0 <= hour <= 23 or not 3 <= keep <= 365: raise HTTPException(400, 'الساعة 0-23 وعدد النسخ 3-365')
        if folder:
            p = Path(folder)
            if not p.is_absolute(): raise HTTPException(400, 'اكتب المسار الكامل للمجلد')
            if p.resolve() == m.ROOT.resolve() or m.ROOT.resolve() in p.resolve().parents:
                raise HTTPException(400, 'المجلد داخل مجلد النظام نفسه؛ اختر مكاناً خارج السيرفر (Google Drive/OneDrive/جهاز شبكة)')
            if not p.is_dir(): raise HTTPException(400, 'المجلد غير موجود أو غير متصل الآن')
        c = connect()
        try:
            for k, v in (('backup.offsite_dir', folder), ('backup.offsite_hour', str(hour)), ('backup.offsite_keep', str(keep))):
                c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (k, v))
            m.audit(c, req, u, 'backup_settings', 'system', None, after={'folder': folder, 'hour': hour, 'keep': keep})
            return {'ok': True}
        finally:
            c.close()

    @app.post('/api/security/backup-now')
    def backup_now(req: Request):
        u = m.need(req, 'users.manage')
        try:
            return run_offsite_backup(u, m.GATEWAY, m.ip(req))
        except backup_offsite.BackupError as e:
            raise HTTPException(400, str(e))

    @app.post('/api/security/mfa/{uid}/reset')
    def mfa_reset(uid: int, req: Request):
        u = m.need(req, 'security.manage')
        if uid == u['user_id']: raise HTTPException(400, 'استخدم صفحة «حسابي» لحسابك')
        c = connect()
        try:
            c.execute('UPDATE user_mfa SET enabled=0,updated_at=CURRENT_TIMESTAMP WHERE user_id=?', (uid,))
            m.audit(c, req, u, 'mfa_reset', 'user', uid, after={'enabled': False})
            return {'ok': True}
        finally:
            c.close()

    @app.get('/permissions', response_class=HTMLResponse)
    def permissions_page(req: Request):
        m.need(req, 'users.manage')
        pages = {}
        for path, code in access.PAGE_PERMS.items():
            pages.setdefault(code, []).append(path)
        return m.templates.TemplateResponse(request=req, name='permissions.html', context={**m.ctx(req, 'جدول الصلاحيات'), 'A': access, 'pages_by_perm': pages})
