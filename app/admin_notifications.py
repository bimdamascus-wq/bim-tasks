"""Manager alerts and daily summary. Explicit employee recipients only."""
import json
from datetime import datetime
from .db import connect
from .telegram_queue import send_resilient

def settings(c):
    row=c.execute("SELECT value FROM settings WHERE key='bim_admin_notifications'").fetchone()
    return json.loads(row[0]) if row else {'enabled':False,'employee_ids':[],'daily_enabled':False,'daily_time':'18:00'}

def process_admin_notifications():
    c=connect();now=datetime.now();day=now.date().isoformat()
    try:
        cfg=settings(c)
        if not cfg.get('enabled'):return
        targets=[]
        for eid in cfg.get('employee_ids',[]):
            e=c.execute('SELECT id,telegram_chat_id FROM employees WHERE id=? AND is_active=1 AND telegram_enabled=1 AND telegram_chat_id IS NOT NULL',(eid,)).fetchone()
            if e:targets.append(e)
        if not targets:return
        events=[]
        for r in c.execute("SELECT ev.id,t.title,e.name,ev.note FROM task_events ev JOIN tasks t ON t.id=ev.task_id LEFT JOIN employees e ON e.id=ev.employee_id WHERE ev.event_type='note' AND date(ev.created_at,'localtime')=date('now','localtime')"):
            events.append(('problem:'+str(r['id']),f"⚠️ مشكلة / ملاحظة\n{r['name'] or ''}\n{r['title']}\n{r['note'] or ''}"))
        for r in c.execute("SELECT t.id,t.title,e.name FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE t.priority='عاجلة' AND t.status NOT IN ('تم','ملغاة') AND t.archived_at IS NULL AND datetime(t.due_at)<datetime('now','localtime')"):
            events.append(('urgent:'+str(r['id'])+':'+day,f"🚨 مهمة عاجلة متأخرة\n{r['name'] or ''}\n{r['title']}"))
        for r in c.execute("SELECT id FROM telegram_outbox WHERE status='failed' AND source_type<>'admin_alert' AND date(updated_at,'localtime')=date('now','localtime')"):
            events.append(('failure:'+str(r['id']),f"⚠️ تعذر تسليم رسالة رقم {r['id']} بعد المحاولات. راجع طابور الإرسال."))
        if cfg.get('daily_enabled') and now.strftime('%H:%M')>=cfg.get('daily_time','18:00'):
            rows=c.execute("SELECT e.name,COUNT(t.id) total,SUM(CASE WHEN t.status='تم' AND date(t.completed_at,'localtime')=date('now','localtime') THEN 1 ELSE 0 END) done,SUM(CASE WHEN t.status NOT IN ('تم','ملغاة') AND datetime(t.due_at)<datetime('now','localtime') THEN 1 ELSE 0 END) overdue FROM employees e LEFT JOIN tasks t ON t.employee_id=e.id AND t.archived_at IS NULL WHERE e.is_active=1 GROUP BY e.id ORDER BY e.name").fetchall()
            lines=[f"📊 تقرير العمل اليومي — {day}"]+[f"{r['name']}: أنجز اليوم {r['done'] or 0} · متأخر {r['overdue'] or 0} · إجمالي {r['total']}" for r in rows]
            for i in range(0,len(lines),20):events.append(('daily:'+day+':'+str(i),'\n'.join(lines[i:i+20])))
        # Durable outbox dedupe is created before any network call by the dispatcher.
        from .telegram_queue import enqueue
        for key,text in events:
            for e in targets:enqueue(e['telegram_chat_id'],text,'admin_alert',dedupe_key='admin:'+str(e['id'])+':'+key)
    finally:c.close()
