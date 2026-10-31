"""Task origin (from the manager / written by the person himself) and distribution to staff by Telegram or WhatsApp."""
import re
from datetime import datetime
from urllib.parse import quote
from fastapi import HTTPException, Request
from .db import connect

MANAGER_ROLES=('admin','deputy')
LABELS={'manager':'📌 من المدير','self':'✍️ كتبها بنفسه','assigned':'👤 من {name}','system':'⚙️ تلقائية'}

def migrate(c):
    c.execute("""CREATE TABLE IF NOT EXISTS task_delegations(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      from_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      from_name TEXT,
      employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
      employee_name TEXT,
      channel TEXT NOT NULL,
      note TEXT,
      status TEXT NOT NULL DEFAULT 'sent',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    c.execute('CREATE INDEX IF NOT EXISTS idx_task_delegations_task ON task_delegations(task_id,id)')
    # One-time backfill: tasks sent from a personal iPhone shortcut belong to that shortcut's user.
    if not c.execute("SELECT 1 FROM settings WHERE key='delegation.iphone_backfill'").fetchone():
        try:
            c.execute("""UPDATE tasks SET created_by=(SELECT a.user_id FROM user_activity a
                WHERE a.target='iphone:'||tasks.id AND a.user_id IS NOT NULL ORDER BY a.id LIMIT 1)
                WHERE created_by IS NULL AND source LIKE 'iphone_%'
                AND EXISTS(SELECT 1 FROM user_activity a WHERE a.target='iphone:'||tasks.id AND a.user_id IS NOT NULL)""")
        except Exception:
            pass
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('delegation.iphone_backfill','1')")

def _users(c):
    return {r['id']:r for r in c.execute('''SELECT u.id,u.employee_id,COALESCE(NULLIF(u.full_name,''),u.username) name,r.code role_code
      FROM users u JOIN roles r ON r.id=u.role_id''')}

def origin(row, users):
    creator=users.get(row['created_by']) if row['created_by'] else None
    if creator:
        if creator['employee_id'] and row['employee_id'] and creator['employee_id']==row['employee_id']:return 'self',creator['name']
        if creator['role_code'] in MANAGER_ROLES:return 'manager',creator['name']
        return 'assigned',creator['name']
    if str(row['source'] or '').startswith('iphone_'):return 'manager','المدير'  # shared management shortcut
    return 'system',''

def decorate(c, rows):
    users=_users(c)
    last={}
    ids=[r['id'] for r in rows]
    for i in range(0,len(ids),500):
        part=ids[i:i+500]
        for d in c.execute(f"SELECT task_id,employee_name,channel,created_at FROM task_delegations WHERE id IN (SELECT MAX(id) FROM task_delegations WHERE task_id IN ({','.join('?'*len(part))}) GROUP BY task_id)",part):
            last[d['task_id']]=dict(d)
    for r in rows:
        kind,name=origin(r,users)
        r['origin']=kind; r['origin_by']=name
        r['origin_label']=LABELS[kind].format(name=name or '')
        r['last_delegation']=last.get(r['id'])
    return rows

def telegram_prefix(c, task_row):
    kind,name=origin(task_row,_users(c))
    return {'manager':'📌 مهمة من المدير','self':'✍️ مهمة كتبتها أنت','assigned':f'👤 مهمة من {name}'}.get(kind,'')

def whatsapp_number(raw):
    d=re.sub(r'\D','',str(raw or ''))
    if d.startswith('00'):d=d[2:]
    elif d.startswith('0'):d='963'+d[1:]
    elif len(d)==9 and d.startswith('9'):d='963'+d
    return d if 9<=len(d)<=15 else ''

def task_message(t, note='', sender=''):
    lines=[f"📋 مهمة: {t['title']}"]
    if t['priority'] and t['priority']!='عادي':lines.append(f"الأولوية: {t['priority']}")
    if t['due_at']:lines.append(f"⏰ الموعد: {str(t['due_at']).replace('T',' ')[:16]}")
    if t['notes']:lines.append(f"📝 {t['notes']}")
    if note:lines.append(f"ملاحظة: {note}")
    if sender:lines.append(f"— {sender}")
    return '\n'.join(lines)

def register(app, m):
    c=connect()
    try:migrate(c)
    finally:c.close()

    def _can_delegate(req, tid):
        u=m.need(req,'tasks.view')
        if u['role_code']=='viewer':raise HTTPException(403,'لا تملك صلاحية توزيع المهام')
        c=connect()
        t=m._task_accessible(c,u,tid)
        if not t:c.close();raise HTTPException(404)
        return u,c,t

    @app.get('/api/tasks/{tid}/delegate-options')
    def delegate_options(tid:int, req:Request):
        u,c,t=_can_delegate(req,tid)
        try:
            emps=[{'id':e['id'],'name':e['name'],'department':e['department'] or '',
                   'telegram':bool(e['telegram_enabled'] and e['telegram_chat_id']),
                   'whatsapp':whatsapp_number(e['whatsapp'])}
                  for e in c.execute('SELECT * FROM employees WHERE is_active=1 ORDER BY sort_order,name')
                  if e['id']!=t['employee_id'] and m._norm_ar(e['name'])!=m._norm_ar('الادارة')]
            sender=u['full_name'] or u['username']
            return {'employees':emps,'message':task_message(t,'',sender)}
        finally:c.close()

    @app.post('/api/tasks/{tid}/delegate')
    async def delegate(tid:int, req:Request):
        d=await req.json()
        u,c,t=_can_delegate(req,tid)
        try:
            ch=str(d.get('channel') or '')
            if ch not in ('telegram','whatsapp'):raise HTTPException(400,'اختر Telegram أو واتساب')
            note=str(d.get('note') or '').strip()[:1000]
            e=c.execute('SELECT * FROM employees WHERE id=? AND is_active=1',(int(d.get('employee_id') or 0),)).fetchone()
            if not e:raise HTTPException(400,'اختر الموظف')
            sender=u['full_name'] or u['username']
            text=task_message(t,note,sender)
            result={'ok':True}
            if ch=='telegram':
                if not (e['telegram_enabled'] and e['telegram_chat_id']):raise HTTPException(400,'هذا الموظف غير مربوط على Telegram؛ استخدم واتساب')
                result=m.send(e['telegram_chat_id'],text,source_type='task_delegation',source_id=tid)
                if not result.get('ok') and not result.get('queued'):raise HTTPException(502,result.get('error') or 'تعذر الإرسال على Telegram')
            c.execute('INSERT INTO task_delegations(task_id,from_user_id,from_name,employee_id,employee_name,channel,note) VALUES(?,?,?,?,?,?,?)',
                      (tid,u['user_id'],sender,e['id'],e['name'],ch,note))
            how='Telegram' if ch=='telegram' else 'واتساب'
            c.execute("INSERT INTO task_events(task_id,employee_id,event_type,note,source) VALUES(?,?,'delegated',?,?)",
                      (tid,e['id'],f"{sender} ← {e['name']} ({how})",ch))
            m.audit(c,req,u,'task_delegate','task',tid,after={'employee_id':e['id'],'employee':e['name'],'channel':ch,'note':note})
            out={'ok':True,'channel':ch,'employee':e['name']}
            if ch=='whatsapp':
                num=whatsapp_number(e['whatsapp'])
                out['url']=('https://wa.me/'+num if num else 'https://wa.me/')+'?text='+quote(text)
            return out
        finally:c.close()
