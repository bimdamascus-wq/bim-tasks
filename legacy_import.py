"""Import the current BIM Tasks Google Sheet export (.xlsx) into SQLite.
Usage: python legacy_import.py "مهام الموظفين - بيم دمشق.xlsx"
Safe to run more than once: employees are upserted; tasks are deduplicated by a stable legacy code.
"""
from __future__ import annotations
import sys, re, hashlib
from pathlib import Path
from datetime import datetime
from openpyxl import load_workbook
from app.db import init_db, connect
from app.seed import seed

def norm(s):
    s=str(s or '').strip().lower().replace('أ','ا').replace('إ','ا').replace('آ','ا').replace('ى','ي').replace('ة','ه')
    return re.sub(r'\s+',' ',s)

def find_sheet(wb, names):
    ns={norm(x):x for x in wb.sheetnames}
    for n in names:
        if norm(n) in ns:return wb[ns[norm(n)]]
    return None

def header_map(ws, max_rows=5):
    best={}; bestrow=1
    for r in range(1,min(ws.max_row,max_rows)+1):
        m={norm(ws.cell(r,c).value):c for c in range(1,ws.max_column+1) if ws.cell(r,c).value not in (None,'')}
        if len(m)>len(best):best=m;bestrow=r
    return best,bestrow

def col(h,*keys):
    for k in keys:
        nk=norm(k)
        if nk in h:return h[nk]
    for hk,c in h.items():
        for k in keys:
            if norm(k) in hk:return c
    return None

def v(ws,r,c): return ws.cell(r,c).value if c else None

def iso_date(x):
    if not x:return None
    if isinstance(x,datetime):return x.isoformat(timespec='minutes')
    s=str(x).strip()
    for fmt in ('%d/%m/%Y %H:%M','%d/%m/%Y','%Y-%m-%d %H:%M:%S','%Y-%m-%dT%H:%M'):
        try:return datetime.strptime(s,fmt).isoformat(timespec='minutes')
        except:pass
    return s

def main(path):
    path=Path(path); wb=load_workbook(path,data_only=True,read_only=True); seed(); c=connect()
    stats={'employees':0,'tasks':0,'recurring':0,'participants':0}
    emp_ids={}
    es=find_sheet(wb,['الموظفون','الموظفين'])
    if es:
        h,hr=header_map(es); cn=col(h,'اسم الموظف','الموظف','الاسم'); cd=col(h,'القسم'); cw=col(h,'رقم WhatsApp','واتساب','whatsapp'); ct=col(h,'Telegram Chat ID','chat id'); cte=col(h,'Telegram')
        for r in range(hr+1,es.max_row+1):
            name=str(v(es,r,cn) or '').strip()
            if not name:continue
            row=c.execute('SELECT id FROM employees WHERE lower(name)=lower(?)',(name,)).fetchone()
            if row:eid=row['id'];c.execute('UPDATE employees SET department=?,whatsapp=?,telegram_chat_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(v(es,r,cd),v(es,r,cw),v(es,r,ct),eid))
            else:
                c.execute('INSERT INTO employees(name,department,whatsapp,telegram_chat_id,telegram_enabled) VALUES(?,?,?,?,?)',(name,v(es,r,cd),v(es,r,cw),v(es,r,ct),0 if norm(v(es,r,cte)) in ('لا','no','0') else 1));eid=c.execute('SELECT last_insert_rowid()').fetchone()[0];stats['employees']+=1
            emp_ids[norm(name)]=eid
    ts=find_sheet(wb,['المهام'])
    if ts:
        h,hr=header_map(ts); ce=col(h,'الموظف','اسم الموظف'); ctask=col(h,'المهمة','اسم المهمة'); cp=col(h,'الأولوية'); cs=col(h,'الحالة'); cdue=col(h,'الموعد','التاريخ'); cn=col(h,'الملاحظات')
        if not ctask: ctask=3
        for r in range(hr+1,ts.max_row+1):
            title=str(v(ts,r,ctask) or '').strip()
            if not title:continue
            en=str(v(ts,r,ce) or '').strip(); eid=emp_ids.get(norm(en))
            key=f'{r}|{en}|{title}|{v(ts,r,cdue)}'; code='LEGACY-'+hashlib.sha1(key.encode('utf-8')).hexdigest()[:14]
            if c.execute('SELECT 1 FROM tasks WHERE task_code=?',(code,)).fetchone():continue
            pr=str(v(ts,r,cp) or 'عادي').strip(); pr=pr if pr in ('منخفضة','عادي','عالية','عاجلة') else 'عادي'
            st=str(v(ts,r,cs) or 'جديدة').strip(); st=st if st in ('جديدة','بدأت','قيد التنفيذ','بانتظار','تم','ملغاة') else 'جديدة'
            c.execute('INSERT INTO tasks(task_code,employee_id,title,notes,priority,status,due_at,source) VALUES(?,?,?,?,?,?,?,?)',(code,eid,title,v(ts,r,cn),pr,st,iso_date(v(ts,r,cdue)),'legacy_sheet'));tid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute("INSERT INTO task_reminders(task_id,type,is_active) VALUES(?,'بدون',0)",(tid,));stats['tasks']+=1
    rs=find_sheet(wb,['المهام المتكررة'])
    if rs:
        h,hr=header_map(rs); ce=col(h,'الموظف','اسم الموظف'); ct=col(h,'المهمة'); ctype=col(h,'التكرار','النوع'); ctime=col(h,'الوقت','الساعة'); cp=col(h,'الأولوية')
        for r in range(hr+1,rs.max_row+1):
            title=str(v(rs,r,ct) or '').strip()
            if not title:continue
            en=str(v(rs,r,ce) or '').strip(); typ=str(v(rs,r,ctype) or 'يومي').strip(); typ=typ if typ in ('يومي','أسبوعي','شهري') else 'يومي'
            c.execute('INSERT INTO recurring_tasks(employee_id,title,priority,recurrence_type,run_time) VALUES(?,?,?,?,?)',(emp_ids.get(norm(en)),title,v(rs,r,cp) or 'عادي',typ,str(v(rs,r,ctime) or '09:00')));stats['recurring']+=1
    c.close();print('تم الاستيراد:',stats)
if __name__=='__main__':
    if len(sys.argv)<2:raise SystemExit('استخدم: python legacy_import.py file.xlsx')
    main(sys.argv[1])
