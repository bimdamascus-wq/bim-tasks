"""Read-only status report from the BIM Tasks database. Prints no secrets.
    python check_status.py C:\\BIM_Tasks_Server
"""
import json, re, sqlite3, sys
from pathlib import Path

# Windows prints through cp1252 when the output is piped: Arabic names crashed the report (2026-10-31).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

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


def mask_chat(chat):
    chat = str(chat or '')
    return ('*' * max(0, len(chat) - 3)) + chat[-3:] if chat else '-'


def clean_error(text):
    text = str(text or '-')
    text = re.sub(r'\d{5,}:[A-Za-z0-9_-]{20,}', '<token>', text)          # a bot token, never print it
    text = re.sub(r'-?\d{5,}', lambda m: mask_chat(m.group(0)), text)     # chat ids inside the error
    return text.replace('\n', ' ')[:300]


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
    failed = q(c, """SELECT o.id,o.created_at,o.updated_at,o.source_type,o.attempts,o.chat_id,o.last_error,
        (SELECT name FROM employees e WHERE trim(e.telegram_chat_id)=trim(o.chat_id) LIMIT 1)
        FROM telegram_outbox o WHERE o.status='failed' ORDER BY o.id DESC LIMIT 20""")
    if failed:
        print('  failed messages (newest first, message text not shown):')
    for r in failed:
        print(f'  #{r[0]} created {r[1]} | last try {r[2]} | type {r[3]} | tries {r[4]} | chat {mask_chat(r[5])}'
              f' ({r[7] or "no employee with this chat"})')
        print(f'      error: {clean_error(r[6])}')

    print('\n[google sheet -> server: how the sheet sends its secret] (only time and path are stored)')
    seen = dict(q(c, "SELECT key,value FROM settings WHERE key IN ('sync.auth_last_header','sync.auth_last_query')"))
    print('  header (new, safe):', seen.get('sync.auth_last_header') or 'not seen yet')
    print('  ?secret= in the link (old):', seen.get('sync.auth_last_query') or 'not seen yet')

    print('\n[possible duplicate messages, last 3 days] (same task + same kind + same employee more than once in 2 minutes)')
    rows = q(c, """SELECT date(a.sent_at),COUNT(*) FROM notifications a JOIN notifications b
        ON a.task_id=b.task_id AND a.kind=b.kind AND a.employee_id IS b.employee_id AND a.id<b.id
        AND abs(strftime('%s',b.sent_at)-strftime('%s',a.sent_at))<=120
        WHERE a.sent_at>datetime('now','-3 days') AND a.task_id IS NOT NULL GROUP BY date(a.sent_at)""")
    if not rows:
        print('  none found')
    for r in rows:
        print(f'  {r[0]}: {r[1]} pairs')
    if rows:
        print('  details, last 24 hours (task id | kind | employee | how many | first .. last, UTC):')
        for r in q(c, """SELECT n.task_id,n.kind,COALESCE(e.name,'-'),COUNT(*),MIN(n.sent_at),MAX(n.sent_at),
              GROUP_CONCAT(DISTINCT n.status)
            FROM notifications n LEFT JOIN employees e ON e.id=n.employee_id
            WHERE n.sent_at>datetime('now','-1 day') AND n.task_id IS NOT NULL
            GROUP BY n.task_id,n.kind,n.employee_id HAVING COUNT(*)>1 ORDER BY COUNT(*) DESC LIMIT 15"""):
            print(f'    task {r[0]} | {r[1]} | {r[2]} | {r[3]} times | {r[4]} .. {r[5]} | {r[6]}')

    print('\n[activity, last 24 hours] (what fills the database)')
    for r in q(c, """SELECT event_type,source,COUNT(*),MIN(created_at),MAX(created_at) FROM task_events
        WHERE created_at>datetime('now','-1 day') GROUP BY event_type,source ORDER BY COUNT(*) DESC LIMIT 12"""):
        print(f'  task_events  {r[0]} / {r[1]}: {r[2]}  ({r[3]} .. {r[4]} UTC)')
    for r in q(c, """SELECT action,COALESCE(username_snapshot,'-'),COUNT(*),MIN(created_at),MAX(created_at) FROM audit_logs
        WHERE created_at>datetime('now','-1 day') GROUP BY action,username_snapshot ORDER BY COUNT(*) DESC LIMIT 12"""):
        print(f'  audit_logs   {r[0]} by {r[1]}: {r[2]}  ({r[3]} .. {r[4]} UTC)')
    for r in q(c, """SELECT action,source,COUNT(*) FROM sync_events
        WHERE created_at>datetime('now','-1 day') GROUP BY action,source ORDER BY COUNT(*) DESC LIMIT 8"""):
        print(f'  sync_events  {r[0]} / {r[1]}: {r[2]}')
    c.close()
    full_sync_report()


def full_sync_report():
    print('\n[google sheet full sync: last 3 runs] (from sync_full_logs\\SUMMARY_*.json)')
    logs = sorted((root / 'sync_full_logs').glob('SUMMARY_*.json'))[-3:]
    if not logs:
        print('  no run found')
    for p in logs:
        try:
            r = json.loads(p.read_text(encoding='utf-8'))
        except Exception as e:
            print(f'  {p.name}: unreadable ({type(e).__name__})'); continue
        rows = sum((r.get('counts') or {}).values())
        sent = sum((r.get('sent') or {}).values())
        print(f"  {p.name}: status={r.get('status')} started={r.get('started_at')} finished={r.get('finished_at', 'NOT FINISHED')}"
              f" rows={rows} uploaded={sent}")
        for e in (r.get('errors') or [])[:3]:
            print('      error:', clean_error(e))


main()
