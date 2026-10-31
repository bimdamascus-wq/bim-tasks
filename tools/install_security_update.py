"""Helper used by INSTALL_SECURITY_UPDATE.cmd. Run from the server root with the server's venv.

  env      add any missing secret to .env (never changes an existing value)
  webhook  re-register the Telegram webhook if 'env' created a new webhook secret
"""
import os, secrets, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / '.env'
FLAG = ROOT / 'logs' / '.security_update_new_webhook'
NOTE = ROOT / 'BACKUP_PASSWORD_KEEP_OUTSIDE.txt'


def read_env():
    text = ENV.read_text(encoding='utf-8-sig') if ENV.exists() else ''
    values = {}
    for line in text.splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k, v = line.split('=', 1)
            values[k.strip()] = v.strip()
    return text, values


def step_env():
    text, values = read_env()
    weak = ('', 'CHANGE_ME_RANDOM_32_CHARS_OR_MORE')
    added = []
    if values.get('BIM_TELEGRAM_WEBHOOK_SECRET', '') in weak:
        added.append(('BIM_TELEGRAM_WEBHOOK_SECRET', secrets.token_urlsafe(32)))
        FLAG.parent.mkdir(exist_ok=True); FLAG.write_text('1')
    if values.get('BIM_SYNC_SECRET', '') in weak:
        added.append(('BIM_SYNC_SECRET', secrets.token_urlsafe(32)))
    passphrase = values.get('BIM_BACKUP_PASSPHRASE', '')
    if len(passphrase) < 16:
        # Words are easier to copy onto paper correctly than random symbols.
        passphrase = '-'.join(secrets.token_hex(3) for _ in range(5))
        added.append(('BIM_BACKUP_PASSPHRASE', passphrase))
    if added:
        lines = [l for l in text.splitlines() if l.split('=', 1)[0].strip() not in dict(added)]
        lines += ['', '# Added by security update'] + [f'{k}={v}' for k, v in added]
        ENV.write_text('\n'.join(lines).strip('\n') + '\n', encoding='utf-8')
    NOTE.write_text(
        'BIM Tasks - كلمة سر النسخ الاحتياطية\n'
        '=====================================\n\n'
        f'BIM_BACKUP_PASSPHRASE = {passphrase}\n\n'
        'اطبع هالورقة أو انسخها واحفظها برّا الشركة، بعدين احذف هالملف.\n'
        'بدونها النسخ الاحتياطية ما بتنفتح إذا خرب السيرفر.\n', encoding='utf-8')
    for k, _ in added:
        print('  + .env:', k)
    print('ENV_OK')


def step_webhook():
    if not FLAG.exists():
        print('WEBHOOK_UNCHANGED'); return
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv
    load_dotenv(ENV, override=True)
    if not os.environ.get('TELEGRAM_BOT_TOKEN', '').strip():
        FLAG.unlink(); print('WEBHOOK_NO_TOKEN'); return
    from app.db import connect
    from app.telegram_service import set_webhook
    base = os.environ.get('BIM_PUBLIC_URL', 'https://task.bimdamascus.com')
    try:
        from app.universal import company
        c = connect()
        base = company(c).get('public_url') or base
        c.close()
    except Exception:
        pass
    r = set_webhook(base.rstrip('/') + '/api/telegram/webhook', os.environ['BIM_TELEGRAM_WEBHOOK_SECRET'])
    if r.get('ok'):
        FLAG.unlink(); print('WEBHOOK_OK')
    else:
        print('WEBHOOK_FAILED', r.get('error'))


if __name__ == '__main__':
    {'env': step_env, 'webhook': step_webhook}[sys.argv[1]]()
