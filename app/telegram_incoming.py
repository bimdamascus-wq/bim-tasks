"""Bounded incoming Telegram attachments, scoped to the employee's pending note."""
import re,secrets
from pathlib import Path
import httpx
from fastapi import HTTPException
from .telegram_service import _api,_token,_verify
LIMIT=20*1024*1024

def download(file_id):
    r=_api('getFile',{'file_id':file_id})
    if not r.get('ok'):raise HTTPException(502,'تعذر جلب مرفق Telegram؛ حاول مجدداً')
    f=r.get('result') or {};path=f.get('file_path','')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+',path) or '..' in path or path.startswith('/') or int(f.get('file_size',0))>LIMIT:raise HTTPException(400,'المرفق غير مدعوم أو يتجاوز 20 ميغابايت')
    try:
        with httpx.Client(verify=_verify(),timeout=30,follow_redirects=False) as client:
            with client.stream('GET','https://api.telegram.org/file/bot'+_token()+'/'+path) as response:
                response.raise_for_status();parts=[];size=0
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>LIMIT:raise HTTPException(400,'المرفق يتجاوز 20 ميغابايت')
                    parts.append(chunk)
        return b''.join(parts)
    except HTTPException:raise
    except Exception:raise HTTPException(502,'تعذر تنزيل المرفق؛ حاول مجدداً')

def save_note_media(c,e,pending,msg):
    from . import main as m
    task=m._tg_task_for_employee(c,pending['task_id'],e['id'])
    if not task or not m.permits(c,e['id'],'attach') or not m.permits(c,e['id'],'comment'):raise HTTPException(403,'المرفقات غير متاحة لهذه المهمة')
    kind=next(k for k in ('document','photo','video') if msg.get(k));item=msg[kind][-1] if kind=='photo' else msg[kind]
    if int(item.get('file_size',0))>LIMIT:raise HTTPException(400,'الحد 20 ميغابايت')
    data=download(item['file_id']);name=Path(str(item.get('file_name') or ('telegram-'+kind+({'photo':'.jpg','video':'.mp4'}.get(kind,'.bin')))).replace('\\','/')).name[:200]
    stored=secrets.token_hex(20)+'.bin';folder=m._attachment_dir(task['id']);dest=folder/stored;dest.write_bytes(data)
    try:
        c.execute('BEGIN IMMEDIATE')
        # Recheck ownership after download, before attaching a file to the task.
        if not m._tg_task_for_employee(c,task['id'],e['id']):raise HTTPException(409,'تغير مسؤول المهمة')
        c.execute('INSERT INTO task_attachments(task_id,original_name,stored_name,relative_path,mime_type,size_bytes,kind) VALUES(?,?,?,?,?,?,?)',(task['id'],name,stored,dest.relative_to(m.ROOT).as_posix(),item.get('mime_type') or ('image/jpeg' if kind=='photo' else 'application/octet-stream'),len(data),'file'))
        caption=str(msg.get('caption') or 'مرفق من Telegram')[:12000]
        c.execute('INSERT INTO task_comments(task_id,username_snapshot,body) VALUES(?,?,?)',(task['id'],e['name'],caption))
        c.execute("INSERT INTO task_events(task_id,employee_id,event_type,note,source) VALUES(?,?,'attachment',?,'telegram')",(task['id'],e['id'],name))
        c.execute('DELETE FROM telegram_pending_actions WHERE chat_id=?',(str(msg['chat']['id']),));c.commit()
    except Exception:c.rollback();dest.unlink(missing_ok=True);raise
    m.send(str(msg['chat']['id']),'تم حفظ المرفق والملاحظة على المهمة.')
