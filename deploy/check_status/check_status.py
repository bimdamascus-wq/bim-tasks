"""Read-only status report from the BIM Tasks database. Prints no secrets.
    python check_status.py C:\\BIM_Tasks_Server
"""
import json, sqlite3, sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else r'C:\BIM_Tasks_Server')
db = root / 'data' / 'bim_tasks.db'


def env_flags():
    keys = {}
    p = root / '.env'
    if p.exists():
        for line in p.read_text(encoding='utf-8-sig', errors='replace').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                keys[k.strip()] = bool(v.strip())
    print('[.env] (set = has a value; values are not shown)')
    for k in ('TELEGRAM_BOT_TOKEN', 'BIM_BACKUP_PASSPHRASE', 'BIM_SYNC_SECRET', 'BIM_TELEGRAM_WEBHOOK_SECRET', 'BIM_GATEWAY'):
        print(f'  {k}: {"set" if keys.get(k) else ("empty" if k in keys else "missing")}')


def q(c, sql, args=()):
    try:
        return c.execute(sql, args).fetchall()
    except Exception as e:
        print(f'  (query failed: {type(e).__name__}: {e})')
        return []


def main():
    env_flags()
    if not db.exists():
        print('[database] NOT FOUND:', db); return
    c = sqlite3.connect(str(db), timeout=30)
    c.execute('PRAGMA busy_timeout=30000')
    s = dict(q(c, "SELECT key,value FROM settings WHERE key LIKE 'backup.%'"))
    print('\n[off-site backup]')
    print('  send to Telegram:', 'ON' if s.get('backup.telegram') == '1' else 'OFF')
    print('  folder set:', 'yes' if (s.get('backup.offsite_dir') or '').strip() else 'no')
    print('  hour:', s.get('backup.offsite_hour', '2 (default)'))
    try:
        last = json.loads(s.get('backup.offsite_last') or '{}')
    except Exception:
        last = {}
    for k in ('ok', 'at', 'last_ok_at', 'last_attempt_at', 'file', 'size', 'where', 'error'):
        if k in last:
            print(f'  last.{k}: {last[k]}')
    if isinstance(last.get('telegram'), dict):
        t = last['telegram']
        print('  last.telegram:', {k: t[k] for k in t if k in ('ok', 'parts', 'sent', 'chats', 'error')})
    for r in q(c, "SELECT created_at,action FROM audit_logs WHERE action LIKE 'offsite_backup%' ORDER BY id DESC LIMIT 7"):
        print('  history:', r[0], r[1])

    print('\n[telegram queue]')
    for r in q(c, "SELECT status,COUNT(*) FROM telegram_outbox GROUP BY status"):
        print(f'  {r[0]}: {r[1]}')

    print('\n[possible duplicate messages, last 3 days] (same task + same kind + same employee more than once in 2 minutes)')
    rows = q(c, """SELECT date(a.sent_at),COUNT(*) FROM notifications a JOIN notifications b
        ON a.task_id=b.task_id AND a.kind=b.kind AND a.employee_id IS b.employee_id AND a.id<b.id
        AND abs(strftime('%s',b.sent_at)-strftime('%s',a.sent_at))<=120
        WHERE a.sent_at>datetime('now','-3 days') AND a.task_id IS NOT NULL GROUP BY date(a.sent_at)""")
    if not rows:
        print('  none found')
    for r in rows:
        print(f'  {r[0]}: {r[1]} pairs')
    c.close()


main()
