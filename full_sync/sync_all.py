#!/usr/bin/env python3
"""BIM Tasks full mirror: read-only SQLite -> separately deployed Google Apps Script.
Never sends secrets, passwords, sessions, IPs or message credentials.

Faster runs (package code_split):
- Append-only log tables (INCREMENTAL) send only rows with id > the last id the Sheet accepted. The last id is kept
  in sync_full_logs/incremental_state.json and written only after the whole table went through.
- Every other table sends only rows whose content changed since the Sheet last accepted the table (a short hash per
  row, keyed by the primary key, kept in the same state file). Rows deleted in the app stay in the Sheet, as before.
- Once a day (first run after 03:00, or `--full`) every table is sent in full, as before.
- A lock file stops overlapping runs: a second run exits at once with code 0. A lock older than 30 min is ignored.
The receiver upserts by (table, source key), so sending fewer rows leaves the Sheet with the same data.
"""
import csv, hashlib, json, os, sqlite3, sys, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(os.environ.get('BIM_SERVER_ROOT',r'C:\BIM_Tasks_Server'))
DB=ROOT/'data'/'bim_tasks.db'
LOGROOT=ROOT/'sync_full_logs'
# Only rows are ever added to these (ids from AUTOINCREMENT, never updated or deleted by the app).
# shift_dispatch_log / group_dispatch_log are NOT here: the workers update their status and delete failed rows.
INCREMENTAL=('audit_logs','task_events','sync_events','notifications')
FULL_HOUR=3
LOCK_STALE_SECONDS=30*60
ALLOW={
 'employees':None,'settings':{'key','value','updated_at'},'users':{'id','username','full_name','role_id','employee_id','is_active','created_at','last_login_at','deleted_at'},
 'roles':None,'permissions':None,'role_permissions':None,
 'tasks':None,'task_participants':None,'task_subtasks':None,'task_comments':None,
 'task_events':None,'task_reminders':None,'task_templates':None,'recurring_tasks':None,
 'task_attachments':{'id','task_id','user_id','original_name','mime_type','size_bytes','kind','created_at'},
 'group_reminders':None,'group_dispatch_log':None,'telegram_groups':{'id','name','telegram_title','department','is_active','created_at','updated_at'},
 'shift_exceptions':None,'shift_messages':None,'shift_dispatch_log':None,
 'notifications':{'id','task_id','employee_id','channel','kind','status','error','sent_at','created_at'},
 'saved_views':None,'audit_logs':{'id','user_id','username_snapshot','action','entity_type','entity_id','before_json','after_json','reason','gateway','created_at'},
 'sync_conflicts':{'id','entity_type','entity_key','server_version','sheet_version','status','created_at','resolved_at'},
 'sync_events':{'id','entity_type','entity_key','action','source','created_at'},
}
DENY_SUBSTR=('password','secret','token','salt','credential','cookie','ip_address','link_code','link_expires','chat_id','phone','whatsapp','telegram_outbox','api_key','stored_name','relative_path','reply_markup')

def scrub(item):
 out={}
 for k,v in item.items():
  if any(word in k.lower() for word in DENY_SUBSTR):continue
  if k in ('before_json','after_json'):
   # Audit payloads may contain credentials from legacy writes, so do not mirror them.
   continue
  if isinstance(v,bytes): continue
  out[k]=v
 return out

def get_rows(conn, name, after_id=None):
 cols={r['name'] for r in conn.execute('PRAGMA table_info("'+name+'")')}
 if not cols:return []
 selected=ALLOW[name]
 records=[]
 sql='SELECT * FROM "'+name+'"'
 args=()
 if after_id is not None:sql+=' WHERE id>? ORDER BY id';args=(int(after_id),)
 for rr in conn.execute(sql,args):
  item=scrub(dict(rr))
  if selected is not None:item={k:v for k,v in item.items() if k in selected}
  if name=='settings': item={k:v for k,v in item.items() if k!='value' or not any(w in str(item.get('key','')).lower() for w in DENY_SUBSTR)}
  records.append(item)
 return records

def call(url, secret, body):
 payload=json.dumps(dict(body,secret=secret),ensure_ascii=False,separators=(',',':')).encode('utf-8')
 req=urllib.request.Request(url,data=payload,headers={'Content-Type':'application/json'},method='POST')
 with urllib.request.urlopen(req,timeout=45) as response:
  raw=response.read(200000).decode('utf-8','replace')
 return json.loads(raw)

def pk_cols(conn, name):
 return [r['name'] for r in sorted(conn.execute('PRAGMA table_info("'+name+'")'),key=lambda r:r['pk']) if r['pk']]

def row_key(record, pk):
 if not pk or any(c not in record for c in pk):return None
 return '|'.join(str(record[c]) for c in pk)

def row_hash(record):
 return hashlib.sha1(json.dumps(record,ensure_ascii=False,sort_keys=True,default=str).encode('utf-8')).hexdigest()[:16]

def read_state():
 try:
  st=json.loads((LOGROOT/'incremental_state.json').read_text(encoding='utf-8'))
  if isinstance(st,dict) and isinstance(st.get('last_id'),dict):
   if not isinstance(st.get('hashes'),dict):st['hashes']={}
   return st
 except Exception:pass
 return {'last_id':{},'last_full_date':'','hashes':{}}

def write_state(st):
 tmp=LOGROOT/'incremental_state.json.tmp'
 tmp.write_text(json.dumps(st,ensure_ascii=False,indent=1),encoding='utf-8')
 os.replace(tmp,LOGROOT/'incremental_state.json')

def full_due(st,now):
 # No state yet -> full. Otherwise once per day, on the first run at or after FULL_HOUR.
 if not st.get('last_full_date'):return True
 return now.hour>=FULL_HOUR and st['last_full_date']!=now.strftime('%Y-%m-%d')

def take_lock():
 lock=LOGROOT/'sync.lock'
 for _ in range(2):
  try:
   fd=os.open(str(lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY)
   os.write(fd,(str(os.getpid())+' '+datetime.now().isoformat()).encode());os.close(fd)
   return lock
  except FileExistsError:
   try:age=time.time()-lock.stat().st_mtime
   except FileNotFoundError:continue
   if age<LOCK_STALE_SECONDS:return None
   # Left behind by a run that was killed: ignore it.
   try:lock.unlink()
   except FileNotFoundError:pass
 return None

def main(argv=None):
 argv=sys.argv[1:] if argv is None else argv
 LOGROOT.mkdir(parents=True,exist_ok=True)
 lock=take_lock()
 if lock is None:
  print('BIM full sync: another run is still working; this one exits (not an error).')
  return 0
 try:return run('--full' in argv)
 finally:
  try:lock.unlink()
  except FileNotFoundError:pass

def run(force_full=False):
 if not DB.is_file():raise RuntimeError('Database not found: '+str(DB))
 now=datetime.now()
 ts=now.strftime('%Y%m%d_%H%M%S')
 state=read_state()
 full=force_full or full_due(state,now)
 report={'started_at':datetime.now(timezone.utc).isoformat(),'source':str(DB),'mode':'full' if full else 'incremental',
         'counts':{},'unchanged':{},'sent':{},'errors':[], 'status':'not_started'}
 env={}
 envfile=ROOT/'.env'
 if envfile.exists():
  for line in envfile.read_text(encoding='utf-8-sig').splitlines():
   if '=' in line and not line.lstrip().startswith('#'):
    k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
 url=(os.environ.get('BIM_FULL_SYNC_WEBAPP_URL') or env.get('BIM_FULL_SYNC_WEBAPP_URL') or '').strip()
 secret=(os.environ.get('BIM_FULL_SYNC_SECRET') or env.get('BIM_FULL_SYNC_SECRET') or env.get('BIM_SYNC_SECRET') or '').strip()
 conn=sqlite3.connect('file:'+DB.as_posix()+'?mode=ro',uri=True)
 conn.row_factory=sqlite3.Row
 mirror={};new_hashes={}
 try:
  for table in ALLOW:
   after=None if full or table not in INCREMENTAL else int(state['last_id'].get(table,0))
   data=get_rows(conn,table,after)
   if table not in INCREMENTAL:
    pk=pk_cols(conn,table);keyed={}
    for r in data:
     k=row_key(r,pk)
     if k is None:keyed=None;break
     keyed[k]=row_hash(r)
    if keyed is not None:
     new_hashes[table]=keyed
     if not full:
      old=state['hashes'].get(table,{})
      before=len(data)
      data=[r for r in data if old.get(row_key(r,pk))!=keyed[row_key(r,pk)]]
      report['unchanged'][table]=before-len(data)
   mirror[table]=data;report['counts'][table]=len(data)
 finally:conn.close()
 # Make immutable local report for verifying data even when remote receiver is not ready.
 with (LOGROOT/('SUMMARY_'+ts+'.json')).open('w',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
 if not url or not secret:
  report['status']='NEEDS_SHEETS_RECEIVER'
  report['errors'].append('Configure BIM_FULL_SYNC_WEBAPP_URL and BIM_FULL_SYNC_SECRET in server .env; no remote changes made')
 elif not url.startswith('https://script.google.com/'):
  report['status']='INVALID_RECEIVER_URL';report['errors'].append('Only official Apps Script URLs allowed')
 else:
  try:
   probe=call(url,secret,{'action':'bim_fullsync_probe','version':'1'})
   if not (probe.get('ok') is True and probe.get('protocol')=='bim_fullsync_v1'):
    raise RuntimeError('Receiver does not support bim_fullsync_v1; upload blocked')
   for table,records in mirror.items():
    report['sent'][table]=0
    # Rows are safely upserted by (table, source key). Individual batches small for Apps Script quotas.
    for i in range(0,len(records),20):
     subset=records[i:i+20]
     response=call(url,secret,{'action':'bim_fullsync_batch','table':table,'rows':subset,'version':'1'})
     if not (response.get('ok') is True and response.get('protocol')=='bim_fullsync_v1' and int(response.get('accepted',-1))==len(subset)):
      raise RuntimeError(table+' batch '+str(i)+' rejected: '+str(response)[:500])
     report['sent'][table]+=len(subset)
    if table in new_hashes:
     # The whole table went through: remember what the Sheet now holds (drops ids deleted in the app).
     state['hashes'][table]=new_hashes[table];write_state(state)
    if table in INCREMENTAL and records:
     # The whole table went through: next run starts after its highest id.
     state['last_id'][table]=max(int(state['last_id'].get(table,0)),max(int(r['id']) for r in records))
     write_state(state)
   if full:
    state['last_full_date']=now.strftime('%Y-%m-%d');write_state(state)
   report['status']='SENT_TO_SHEETS'
  except Exception as e:
   report['status']='PARTIAL_OR_FAILED'
   report['errors'].append(str(e))
 report['finished_at']=datetime.now(timezone.utc).isoformat()
 with (LOGROOT/('SUMMARY_'+ts+'.json')).open('w',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
 print('BIM full sync:',report['status'],'('+report['mode']+')')
 print('Records:',report['counts'])
 print('Uploaded:',report['sent'])
 print('Report:',LOGROOT/('SUMMARY_'+ts+'.json'))
 for e in report['errors']:print('WARN:',e)
 return 0 if report['status']=='SENT_TO_SHEETS' else 2

if __name__=='__main__':
 try:sys.exit(main())
 except Exception as exc:print('ERROR:',exc);sys.exit(3)
