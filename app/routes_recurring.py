"""Recurring tasks.
Split out of app/main.py (package code_split) with no change in behaviour; main.py imports this module."""
from __future__ import annotations
from fastapi import HTTPException, Request
from . import access
from .db import connect
from .work_settings import validate_recurring
from .main import (
    app, audit, need
)

# ---------- recurring ----------
@app.get('/api/recurring')
def api_recurring(req:Request):
    u=need(req,'recurring.view'); c=connect()
    try:
        scope=access.employee_scope(c,u)
        return [dict(x) for x in c.execute('''SELECT r.*,COALESCE((SELECT workdays_only FROM bim_recurring_rules br WHERE br.recurring_id=r.id),0) workdays_only,COALESCE(e.name,'بدون موظف') employee_name
          FROM recurring_tasks r LEFT JOIN employees e ON e.id=r.employee_id WHERE r.id NOT IN(SELECT record_id FROM record_lifecycle WHERE entity='recurring' AND deleted_at IS NOT NULL) ORDER BY r.id DESC''') if scope is None or x['employee_id'] in scope]
    finally: c.close()

@app.post('/api/recurring')
async def add_recurring(req:Request):
    u=need(req,'recurring.edit'); d=await req.json(); title=str(d.get('title','')).strip()
    d.update(validate_recurring(d))
    c=connect()
    try: access.require_assignable(c,u,[d.get('employee_id')])
    except HTTPException: c.close(); raise
    if not c.execute('SELECT 1 FROM employees WHERE id=? AND is_active=1',(d.get('employee_id'),)).fetchone():c.close();raise HTTPException(400,'اختر موظفاً فعالاً')
    c.execute('''INSERT INTO recurring_tasks(employee_id,title,notes,priority,recurrence_type,week_day,month_day,run_time,created_by)
      VALUES(?,?,?,?,?,?,?,?,?)''',(d.get('employee_id') or None,title,d.get('notes'),d.get('priority','عادي'),d['recurrence_type'],d.get('week_day'),d.get('month_day'),d.get('run_time','09:00'),u['user_id']))
    rid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('INSERT INTO bim_recurring_rules VALUES(?,?)',(rid,int(d.get('workdays_only',True))))
    audit(c,req,u,'recurring_create','recurring',rid,after=d); c.close(); return {'ok':True,'id':rid}

@app.patch('/api/recurring/{rid}')
async def edit_recurring(rid:int, req:Request):
    u=need(req,'recurring.edit'); d=await req.json(); c=connect(); old=c.execute('SELECT * FROM recurring_tasks WHERE id=?',(rid,)).fetchone()
    if not old: c.close(); raise HTTPException(404)
    # An unassigned rule (employee_id NULL) belongs to management, never to a supervisor's team.
    current=old['employee_id'] if old['employee_id'] is not None else -1
    try: access.require_assignable(c,u,[current]+([d['employee_id']] if 'employee_id' in d else []))
    except HTTPException: c.close(); raise
    merged=dict(old);merged.update(d)
    try:validate_recurring(merged)
    except Exception:c.close();raise
    if 'employee_id' in d and not c.execute('SELECT 1 FROM employees WHERE id=?',(d['employee_id'],)).fetchone():c.close();raise HTTPException(400,'الموظف غير موجود')
    if 'workdays_only' in d:c.execute('INSERT INTO bim_recurring_rules VALUES(?,?) ON CONFLICT(recurring_id) DO UPDATE SET workdays_only=excluded.workdays_only',(rid,int(bool(d['workdays_only']))))
    allowed=['employee_id','title','notes','priority','recurrence_type','week_day','month_day','run_time','is_active']; sets=[]; vals=[]
    for k in allowed:
        if k in d: sets.append(k+'=?'); vals.append(None if k in ('employee_id','week_day','month_day') and d[k] in ('',None) else d[k])
    if sets: sets.append('updated_at=CURRENT_TIMESTAMP'); vals.append(rid); c.execute('UPDATE recurring_tasks SET '+','.join(sets)+' WHERE id=?',vals)
    audit(c,req,u,'recurring_update','recurring',rid,before=dict(old),after=d); c.close(); return {'ok':True}

@app.delete('/api/recurring/{rid}')
def delete_recurring(rid:int, req:Request):
    from .lifecycle import change
    return change(req,'recurring',rid,'delete')
