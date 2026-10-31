"""Emergency backup page and API.
Split out of app/main.py (package code_split) with no change in behaviour; main.py imports this module."""
from __future__ import annotations
import os
import subprocess
from datetime import datetime
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from pathlib import Path
from .db import connect
from .main import (
    ROOT, app, audit, ctx, need, templates
)

# ---------- two-way Google Sheet synchronization ----------

# ---------- emergency backup ----------
BACKUP_ROOT = Path(os.environ.get('BIM_BACKUP_DIR', r'C:\BIM_Tasks_Backups'))

def _backup_files():
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    files=[]
    for p in sorted(BACKUP_ROOT.glob('*'), key=lambda x:x.stat().st_mtime if x.exists() else 0, reverse=True)[:100]:
        if p.is_file():
            try:
                files.append({'name':p.name,'size':p.stat().st_size,'modified':datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec='seconds')})
            except Exception:
                pass
    return files

@app.get('/backup', response_class=HTMLResponse)
def backup_page(req:Request):
    need(req,'dashboard.view')
    return templates.TemplateResponse(request=req,name='backup.html',context=ctx(req,'النسخ الاحتياطي والطوارئ'))

@app.get('/api/backup/status')
def backup_status(req:Request):
    need(req,'dashboard.view')
    files=_backup_files()
    latest_db=next((x for x in files if x['name'].lower().endswith('.db')),None)
    latest_full=next((x for x in files if x['name'].lower().endswith('.zip')),None)
    return {'ok':True,'backup_dir':str(BACKUP_ROOT),'latest_db':latest_db,'latest_full':latest_full,'files':files[:20]}

@app.post('/api/backup/run')
def backup_run(req:Request, kind:str='db'):
    u=need(req,'users.manage')
    script=ROOT/'tools'/'backup_bim_tasks.py'
    if not script.exists(): raise HTTPException(500,'ملف النسخ الاحتياطي غير موجود')
    mode='full' if str(kind).lower()=='full' else 'db'
    try:
        r=subprocess.run([str(ROOT/'.venv'/'Scripts'/'python.exe'),str(script),mode],
                         cwd=str(ROOT),capture_output=True,text=True,timeout=900)
        if r.returncode!=0:
            raise HTTPException(500,(r.stderr or r.stdout or 'فشل النسخ الاحتياطي')[-1000:])
    except subprocess.TimeoutExpired:
        raise HTTPException(504,'استغرق النسخ الاحتياطي وقتاً أطول من المتوقع')
    c=connect()
    audit(c,req,u,'backup_run','system',None,after={'kind':mode})
    c.close()
    return {'ok':True,'kind':mode,'message':'تم إنشاء النسخة الاحتياطية'}
