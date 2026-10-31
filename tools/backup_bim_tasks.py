from __future__ import annotations
import os, sys, sqlite3, shutil, zipfile, time
from pathlib import Path
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
DB=DATA/'bim_tasks.db'
ATT=DATA/'attachments'
ENV=ROOT/'.env'
BACKUP=Path(os.environ.get('BIM_BACKUP_DIR',r'C:\BIM_Tasks_Backups'))
BACKUP.mkdir(parents=True,exist_ok=True)

def stamp():
    return datetime.now().strftime('%Y%m%d_%H%M%S')

def safe_db_copy(dest:Path):
    if not DB.exists():
        raise SystemExit('Database not found: '+str(DB))
    src=sqlite3.connect(str(DB),timeout=30)
    dst=sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close(); src.close()

def cleanup(pattern:str, keep:int):
    files=sorted(BACKUP.glob(pattern),key=lambda p:p.stat().st_mtime,reverse=True)
    for p in files[keep:]:
        try:p.unlink()
        except Exception:pass

def db_backup():
    out=BACKUP/f'BIM_Tasks_DB_{stamp()}.db'
    safe_db_copy(out)
    cleanup('BIM_Tasks_DB_*.db',168)  # 7 days hourly
    print(out)
    return out

def full_backup():
    s=stamp()
    temp=BACKUP/f'.BIM_Tasks_FULL_{s}.db'
    safe_db_copy(temp)
    out=BACKUP/f'BIM_Tasks_FULL_{s}.zip'
    try:
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as z:
            z.write(temp,'data/bim_tasks.db')
            if ENV.exists():
                z.write(ENV,'.env')
            if ATT.exists():
                for p in ATT.rglob('*'):
                    if p.is_file():
                        z.write(p,p.relative_to(ROOT))
        cleanup('BIM_Tasks_FULL_*.zip',30)
    finally:
        try: temp.unlink()
        except Exception: pass
    print(out)
    return out

if __name__=='__main__':
    mode=(sys.argv[1] if len(sys.argv)>1 else 'db').lower()
    if mode=='full': full_backup()
    else: db_backup()
