"""Switch BIM Tasks to another Telegram bot (owner 2026-11-25). Run by CHANGE_BOT.cmd on the server, as admin.
The token is pasted on the server only (never in chat). Steps: check it with getMe, back up .env, write
TELEGRAM_BOT_TOKEN, point the new bot's webhook at our server, restart ONLY our ports 8797/8798.
All the work and every Arabic text is here, not in the .cmd: cmd.exe misreads UTF-8 Arabic and closed the window (2026-11-25)."""
import json, os, re, shutil, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(os.environ.get('BIM_ROOT') or Path(__file__).resolve().parents[2])  # BIM_ROOT: the zip can be extracted anywhere
ENV = ROOT / '.env'
WINDOWS_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')  # read before any app import could load .env


def api(token, method, payload=None):
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}', data=json.dumps(payload or {}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b'{}')
    except Exception as e:
        return {'ok': False, 'description': str(e)}


def env_lines():
    return ENV.read_text(encoding='utf-8-sig').splitlines() if ENV.exists() else []


def env_get(lines, key):
    return next((l.split('=', 1)[1].strip().strip('"\'') for l in lines if l.split('=', 1)[0].strip() == key), '')


def with_token(lines, token):
    """The .env lines with TELEGRAM_BOT_TOKEN set (replaced in place, or appended)."""
    out, done = [], False
    for l in lines:
        if l.split('=', 1)[0].strip() == 'TELEGRAM_BOT_TOKEN':
            out.append(f'TELEGRAM_BOT_TOKEN={token}'); done = True
        else:
            out.append(l)
    return out if done else out + [f'TELEGRAM_BOT_TOKEN={token}']


def public_url(lines):
    try:  # the address saved in the system settings wins, like the «تفعيل Webhook» button
        sys.path.insert(0, str(ROOT))
        from app.db import connect
        from app.universal import company
        c = connect(); u = company(c).get('public_url'); c.close()
        if u: return u
    except Exception:
        pass
    return env_get(lines, 'BIM_PUBLIC_URL') or 'https://task.bimdamascus.com'


def main():
    lines = env_lines()
    if not lines:
        print('ما لقيت ملف .env بـ', ENV); return 1
    print('الصق رمز البوت الجديد (من BotFather) واكبس Enter:')
    token = input('> ').strip()
    if not re.fullmatch(r'\d{5,}:[A-Za-z0-9_-]{30,}', token):
        print('هاد مو شكل رمز بوت. لازم يكون متل: 123456789:ABC...'); return 1
    me = api(token, 'getMe')
    if not me.get('ok'):
        print('تلغرام رفض الرمز:', me.get('description')); return 1
    name = me['result'].get('username')
    if token == env_get(lines, 'TELEGRAM_BOT_TOKEN'):
        print(f'@{name} هو نفسه البوت الحالي. ما في شي لتغييره.'); return 1
    print(f'\nالبوت الجديد: @{name}')
    if input('للتأكيد اكتب 1 واكبس Enter: ').strip() != '1':
        print('ما تغيّر شي.'); return 1
    secret = env_get(lines, 'BIM_TELEGRAM_WEBHOOK_SECRET')
    url = public_url(lines).rstrip('/') + '/api/telegram/webhook'
    wh = api(token, 'setWebhook', {'url': url, 'secret_token': secret, 'allowed_updates': ['message', 'callback_query'],
                                   'drop_pending_updates': False})
    if not wh.get('ok'):
        print('ما قدرت وجّه البوت عالنظام:', wh.get('description'), '\nما تغيّر شي.'); return 1
    backup = ENV.with_name(f'.env.before_change_bot_{time.strftime("%Y%m%d_%H%M%S")}')
    shutil.copy2(ENV, backup)
    ENV.write_text('\n'.join(with_token(lines, token)) + '\n', encoding='utf-8')
    print(f'\nتم: الرمز الجديد محفوظ (@{name}). النسخة القديمة من الإعدادات: {backup.name}')
    if WINDOWS_TOKEN:  # a Windows variable beats .env (load_dotenv does not override it)
        print('⚠️ في متغير ويندوز اسمه TELEGRAM_BOT_TOKEN عالسيرفر، وهاد بيغلب الإعدادات. صوّر هالشاشة وابعتها.')
    restart()
    return 0


def restart():
    print('\nعم أطفي النظام (8797 و 8798) وأرجع شغّله...')
    for t in ('\\BIM Tasks\\LAN', 'BIM Tasks External 8798'):
        subprocess.run(['schtasks', '/End', '/TN', t], capture_output=True)
    # Same stop rule as the installer: only OUR ports, never the other app.main:app systems on this server.
    subprocess.run(['powershell', '-NoProfile', '-Command',
                    "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*.exe' -and $_.CommandLine -match 'uvicorn'"
                    " -and $_.CommandLine -match 'app\\.main:app' -and $_.CommandLine -match '--port\\s+(8797|8798)\\b' }"
                    " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], capture_output=True)
    time.sleep(6)
    for t, cmd in (('\\BIM Tasks\\LAN', 'RUN_LAN.cmd'), ('BIM Tasks External 8798', 'RUN_EXTERNAL.cmd')):
        if subprocess.run(['schtasks', '/Run', '/TN', t], capture_output=True).returncode:
            subprocess.Popen(['cmd', '/c', 'start', '', '/min', str(ROOT / cmd)], cwd=str(ROOT))
    for _ in range(30):
        try:
            urllib.request.urlopen('http://127.0.0.1:8797/health', timeout=3); print('✅ خلص. النظام رجع اشتغل عالبوت الجديد.'); return
        except Exception:
            time.sleep(2)
    print('⚠️ النظام لسا ما رجع. استنى دقيقتين وافتحه، وإذا ما فتح شغّل START_BIM_NOW.cmd كمسؤول.')


if __name__ == '__main__':
    try:
        code = main()
    except Exception as e:  # never let the window vanish without telling what happened
        print('صار خطأ:', repr(e)); code = 1
    input('\nاكبس Enter لتسكّر الشاشة...')
    sys.exit(code)
