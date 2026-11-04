from datetime import datetime, timedelta
import json
from .db import connect
from .telegram_service import send as low_level_send, edit_message

RETRY_DELAYS_MINUTES = [1, 2, 5, 15, 30]

def _next_delay(attempts:int)->int:
    idx=max(0,min(attempts-1,len(RETRY_DELAYS_MINUTES)-1))
    return RETRY_DELAYS_MINUTES[idx]

def enqueue(chat_id, text, source_type='text', source_id=None, dedupe_key=None, max_attempts=5, last_error=None, reply_markup=None):
    c=connect()
    try:
        if dedupe_key:
            old=c.execute("SELECT id,status FROM telegram_outbox WHERE dedupe_key=?",(dedupe_key,)).fetchone()
            if old:
                return int(old['id'])
        c.execute("""INSERT OR IGNORE INTO telegram_outbox
          (chat_id,message_text,source_type,source_id,dedupe_key,status,attempts,max_attempts,next_attempt_at,last_error,reply_markup_json)
          VALUES(?,?,?,?,?,'pending',0,?,CURRENT_TIMESTAMP,?,?)""",
          (str(chat_id),str(text),source_type,str(source_id) if source_id is not None else None,
           dedupe_key,int(max_attempts),str(last_error or '')[:1000] or None,
           json.dumps(reply_markup,ensure_ascii=False) if reply_markup else None))
        if dedupe_key:return int(c.execute('SELECT id FROM telegram_outbox WHERE dedupe_key=?',(dedupe_key,)).fetchone()[0])
        return int(c.execute("SELECT last_insert_rowid() x").fetchone()['x'])
    finally:
        c.close()

def send_resilient(chat_id, text, source_type='text', source_id=None, dedupe_key=None, reply_markup=None):
    result=low_level_send(str(chat_id),str(text),reply_markup=reply_markup)
    if result.get('ok'):
        return result
    qid=enqueue(chat_id,text,source_type,source_id,dedupe_key,last_error=result.get('error'),reply_markup=reply_markup)
    return {'ok':True,'queued':True,'queue_id':qid,'error':result.get('error')}

def process_queue(limit=25):
    c=connect()
    try:
        c.execute("UPDATE telegram_outbox SET status='failed',last_error=COALESCE(last_error,'انتهت مهلة محاولة الإرسال'),updated_at=CURRENT_TIMESTAMP WHERE status='retrying' AND attempts>=max_attempts AND datetime(next_attempt_at)<=CURRENT_TIMESTAMP")
        rows=c.execute("""SELECT * FROM telegram_outbox
          WHERE status IN ('pending','retrying')
            AND attempts < max_attempts
            AND (next_attempt_at IS NULL OR datetime(next_attempt_at)<=CURRENT_TIMESTAMP)
          ORDER BY id LIMIT ?""",(int(limit),)).fetchall()
        for row in rows:
            attempts=int(row['attempts'] or 0)+1
            claimed=c.execute("""UPDATE telegram_outbox SET status='retrying',attempts=?,next_attempt_at=datetime('now','+5 minutes'),
                         updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('pending','retrying') AND attempts=? AND (next_attempt_at IS NULL OR datetime(next_attempt_at)<=CURRENT_TIMESTAMP)""",(attempts,row['id'],row['attempts']))
            if not claimed.rowcount:continue
            markup=None
            if 'reply_markup_json' in row.keys() and row['reply_markup_json']:
                try: markup=json.loads(row['reply_markup_json'])
                except Exception: markup=None
            message_text=row['message_text']
            if row['source_type'] in ('task_card','task_edit') and row['source_id']:
                # A queued task may have changed while Telegram was unreachable.
                from .main import _tg_employee, _tg_task_for_employee
                e=_tg_employee(c,row['chat_id'])
                t=_tg_task_for_employee(c,int(row['source_id']),e['id']) if e else None
                if not t or not e or not e['telegram_enabled']:
                    c.execute("UPDATE telegram_outbox SET status='failed',last_error='لم تعد المهمة مخصصة للمستلم',updated_at=CURRENT_TIMESTAMP WHERE id=?",(row['id'],))
                    continue
                from .main import _telegram_task_text, _telegram_task_keyboard
                message_text=_telegram_task_text(t,'تحديث مهمة')
                markup=_telegram_task_keyboard(int(row['source_id']),t['status'])
            if row['source_type'] in ('followup_card','followup_show','followup_edit','followup_file'):
                from .followup import outbox_send
                result=outbox_send(c,row)
                if not result.get('ok') and result.get('final'):
                    c.execute("UPDATE telegram_outbox SET status='failed',last_error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(str(result.get('error'))[:1000],row['id']))
                    continue
            elif row['source_type']=='task_edit':
                meta=json.loads(row['reply_markup_json']); result=edit_message(row['chat_id'],meta['message_id'],message_text,reply_markup=markup)
                if 'message is not modified' in str(result.get('error','')).lower():result={'ok':True}
            elif row['source_type']=='followup':
                from .followup import send_html  # follow-up group messages are HTML (bold, owner 2026-11-04)
                result=send_html(row['chat_id'],message_text,markup)
            else:
                result=low_level_send(row['chat_id'],message_text,reply_markup=markup)
            if result.get('ok'):
                if row['source_type']=='task_card' and row['source_id'] and result.get('message_id'):
                    try:
                        c.execute("INSERT OR IGNORE INTO telegram_task_messages(task_id,chat_id,message_id) VALUES(?,?,?)",
                                  (int(row['source_id']),str(row['chat_id']),int(result['message_id'])))
                    except (ValueError,TypeError):
                        pass
                if row['source_type']=='followup' and row['source_id'] and result.get('message_id'):
                    from .followup import remember_ref  # a reply to this message in the group = comment on that task
                    remember_ref(c,row['chat_id'],result['message_id'],row['source_id'])
                c.execute("""UPDATE telegram_outbox SET status='sent',last_error=NULL,
                             sent_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?""",(row['id'],))
                # If this queue item came from a group reminder, close that dispatch record too.
                if row['source_type']=='group_reminder' and row['source_id']:
                    c.execute("""UPDATE group_dispatch_log SET status='sent',error=NULL,sent_at=CURRENT_TIMESTAMP
                                 WHERE event_key=?""",(row['source_id'],))
            else:
                err=str(result.get('error') or 'تعذر الإرسال')[:1000]
                if attempts >= int(row['max_attempts'] or 5):
                    c.execute("""UPDATE telegram_outbox SET status='failed',last_error=?,
                                 updated_at=CURRENT_TIMESTAMP WHERE id=?""",(err,row['id']))
                else:
                    delay=_next_delay(attempts)
                    c.execute("""UPDATE telegram_outbox SET status='retrying',last_error=?,
                                 next_attempt_at=datetime('now',?),updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                              (err,f'+{delay} minutes',row['id']))
    finally:
        c.close()


def enqueue_edit(task_id,chat_id,message_id):
    from .main import _telegram_task_text, _telegram_task_keyboard
    c=connect()
    try:
        t=c.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
        if not t:return False
        result=edit_message(chat_id,message_id,_telegram_task_text(t,'تحديث مهمة'),reply_markup=_telegram_task_keyboard(task_id,t['status']))
        if result.get('ok') or 'message is not modified' in str(result.get('error','')).lower():return True
    finally:c.close()
    enqueue(chat_id,'تحديث بطاقة المهمة',source_type='task_edit',source_id=task_id,reply_markup={'message_id':message_id},last_error=result.get('error'))
    return True
