"""Fill a staging copy (normally C:\\BIM_Tasks_Test) with a safe copy of the real data.

    python tools\\make_test_copy.py --src C:\\BIM_Tasks_Server --dst C:\\BIM_Tasks_Test

The code and .venv are copied by make_test_copy.ps1 before this runs. This script:
  * copies the live database with SQLite's backup API (safe while the real system is running, WAL included),
    plus attachments and the company logo;
  * writes a .env with everything that reaches the outside removed, so the copy can never message employees,
    change the Telegram webhook, push to the Google Sheet, or write/prune the real backups;
  * clears the same links inside the copied database and switches off login confirmation (it needs Telegram);
  * writes TEST_COPY.txt, without which RUN_TEST.cmd refuses to start.
The real system is only read.
"""
from __future__ import annotations
import argparse, shutil, sqlite3, sys
from datetime import datetime
from pathlib import Path

MARKER = 'TEST_COPY.txt'
# Emptied in the copy's .env: Telegram, Google Sheet, iPhone, encrypted off-site backups.
BLANK = ('TELEGRAM_BOT_TOKEN', 'BIM_TELEGRAM_WEBHOOK_SECRET', 'BIM_SYNC_SECRET', 'BIM_IPHONE_SECRET',
         'BIM_BACKUP_PASSPHRASE', 'BIM_OFFSITE_DIR')
# Settings in the copied database that point at real outside places.
CLEAR_SETTINGS = ('sync.webapp_url', 'backup.offsite_dir', 'backup.telegram')


def copy_db(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ('', '-wal', '-shm'):
        p = Path(str(dst) + suffix)
        if p.exists():
            p.unlink()
    s = sqlite3.connect(str(src), timeout=30)  # same way as tools/backup_bim_tasks.py; only read
    d = sqlite3.connect(str(dst))
    try:
        s.execute('PRAGMA busy_timeout=30000')
        s.backup(d)
    finally:
        d.close(); s.close()


def sanitize_db(db: Path):
    c = sqlite3.connect(str(db))
    try:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'settings' in tables:
            c.executemany('DELETE FROM settings WHERE key=?', [(k,) for k in CLEAR_SETTINGS])
        if 'user_mfa' in tables:
            c.execute('UPDATE user_mfa SET enabled=0')
        c.commit()
    finally:
        c.close()


def write_env(src_env: Path, dst_env: Path, dst_root: Path):
    lines = src_env.read_text(encoding='utf-8-sig').splitlines() if src_env.exists() else []
    forced = {k: '' for k in BLANK}
    forced['BIM_BACKUP_DIR'] = str(dst_root / 'backups')
    out, seen = [], set()
    for line in lines:
        key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
        if key in forced:
            out.append(f'{key}={forced[key]}'); seen.add(key)
        else:
            out.append(line)
    out += [f'{k}={v}' for k, v in forced.items() if k not in seen]
    dst_env.write_text('# TEST COPY - outside connections removed by tools/make_test_copy.py\n' + '\n'.join(out) + '\n',
                       encoding='utf-8')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--dst', required=True)
    a = ap.parse_args(argv)
    src, dst = Path(a.src).resolve(), Path(a.dst).resolve()
    if src == dst or src in dst.parents or dst in src.parents:
        sys.exit('Refusing: the test copy must be a separate folder from the real system.')
    if not (src / 'data' / 'bim_tasks.db').exists():
        sys.exit(f'Database not found: {src / "data" / "bim_tasks.db"}')
    copy_db(src / 'data' / 'bim_tasks.db', dst / 'data' / 'bim_tasks.db')
    sanitize_db(dst / 'data' / 'bim_tasks.db')
    for name in ('attachments', 'company_logo'):
        s, d = src / 'data' / name, dst / 'data' / name
        if d.exists():
            shutil.rmtree(d) if d.is_dir() else d.unlink()
        if s.is_dir():
            shutil.copytree(s, d)
        elif s.exists():
            shutil.copy2(s, d)
    write_env(src / '.env', dst / '.env', dst)
    (dst / 'backups').mkdir(exist_ok=True)
    (dst / MARKER).write_text(f'BIM Tasks test copy made {datetime.now():%Y-%m-%d %H:%M} from {src}\n'
                              'Port 8799, local only. Telegram, Google Sheet and off-site backups are disabled.\n',
                              encoding='utf-8')
    print('TEST_COPY_OK')


if __name__ == '__main__':
    main()
