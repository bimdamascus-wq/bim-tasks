"""Operational reports.
Split out of app/main.py (package code_split) with no change in behaviour; main.py imports this module."""
from __future__ import annotations
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from typing import Optional
from .db import connect
from .main import (
    app, ctx, need, templates
)

# ---------- Operational reports ----------
@app.get('/operations-reports', response_class=HTMLResponse)
def operations_reports_page(req:Request):
    need(req,'dashboard.view')
    return templates.TemplateResponse(request=req,name='operations_reports.html',context=ctx(req,'التقارير التشغيلية'))

@app.get('/api/operations-report')
def operations_report(req:Request, days:int=30, employee_id:Optional[int]=None, department:Optional[str]=None):
    need(req,'dashboard.view'); days=min(max(int(days or 30),1),365); c=connect()
    where=["date(t.created_at,'localtime')>=date('now','localtime',?)"]; args=[f'-{days-1} days']
    if employee_id: where.append("t.employee_id=?"); args.append(employee_id)
    if department: where.append("COALESCE(e.department,'')=?"); args.append(department)
    w=' AND '.join(where)
    summary=dict(c.execute(f"""SELECT COUNT(*) total,
      SUM(CASE WHEN t.status='تم' THEN 1 ELSE 0 END) completed,
      SUM(CASE WHEN t.status NOT IN ('تم','ملغاة') AND t.due_at IS NOT NULL AND datetime(t.due_at)<datetime('now','localtime') THEN 1 ELSE 0 END) overdue,
      ROUND(AVG(CASE WHEN t.started_at IS NOT NULL THEN (julianday(t.started_at)-julianday(t.created_at))*24*60 END),1) avg_start_minutes,
      ROUND(AVG(CASE WHEN t.completed_at IS NOT NULL THEN (julianday(t.completed_at)-julianday(t.created_at))*24*60 END),1) avg_complete_minutes
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE {w}""",args).fetchone())
    problem_where=["date(te.created_at,'localtime')>=date('now','localtime',?)","te.event_type='note'","te.source='telegram'"]
    pargs=[f'-{days-1} days']
    if employee_id: problem_where.append("te.employee_id=?"); pargs.append(employee_id)
    if department:problem_where.append("te.employee_id IN (SELECT id FROM employees WHERE department=?)");pargs.append(department)
    problems=c.execute("SELECT COUNT(*) n FROM task_events te WHERE "+' AND '.join(problem_where),pargs).fetchone()['n']
    summary['problems']=problems
    rows=[dict(x) for x in c.execute(f"""SELECT t.employee_id employee_id,COALESCE(e.name,CASE WHEN t.is_group=1 THEN 'مهمة جماعية' ELSE 'بدون موظف' END) employee_name,
      COALESCE(e.department,'—') department,COUNT(*) total,
      SUM(CASE WHEN t.status='تم' THEN 1 ELSE 0 END) completed,
      SUM(CASE WHEN t.status NOT IN ('تم','ملغاة') AND t.due_at IS NOT NULL AND datetime(t.due_at)<datetime('now','localtime') THEN 1 ELSE 0 END) overdue,
      ROUND(AVG(CASE WHEN t.started_at IS NOT NULL THEN (julianday(t.started_at)-julianday(t.created_at))*24*60 END),1) avg_start_minutes,
      ROUND(AVG(CASE WHEN t.completed_at IS NOT NULL THEN (julianday(t.completed_at)-julianday(t.created_at))*24*60 END),1) avg_complete_minutes
      FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id
      WHERE {w}
      GROUP BY t.employee_id,e.name,e.department ORDER BY total DESC""",args)]
    employees=[dict(x) for x in c.execute("SELECT id,name,department FROM employees WHERE is_active=1 ORDER BY name")]
    departments=[x['department'] for x in c.execute("SELECT DISTINCT department FROM employees WHERE department IS NOT NULL AND trim(department)<>'' ORDER BY department")]
    c.close(); return {'days':days,'summary':summary,'rows':rows,'employees':employees,'departments':departments}


# Details for interactive operational report cards; shares scope/status rules with summary.
@app.get('/api/operations-report/tasks')
def operations_report_tasks(req:Request, days:int=30, employee_id:Optional[int]=None, department:Optional[str]=None, category:str='total', q:str='', unassigned:bool=False, offset:int=0):
    need(req,'dashboard.view')
    if category not in {'total','completed','overdue','active'}:
        raise HTTPException(400,'تصنيف غير صالح')
    days=min(max(int(days or 30),1),365)
    c=connect()
    try:
        clauses=["date(t.created_at,'localtime')>=date('now','localtime',?)"]
        params=[f'-{days-1} days']
        if unassigned:clauses.append('t.employee_id IS NULL')
        if employee_id:
            clauses.append('t.employee_id=?');params.append(employee_id)
        if department:
            clauses.append("COALESCE(e.department,'')=?");params.append(department)
        if category=='completed':clauses.append("t.status='تم'")
        if category=='overdue':clauses.append("t.status NOT IN ('تم','ملغاة') AND t.due_at IS NOT NULL AND datetime(t.due_at)<datetime('now','localtime')")
        if category=='active':clauses.append("t.status NOT IN ('تم','ملغاة')")
        q=q.strip()[:120]
        if q:
            clauses.append("(t.title LIKE ? OR COALESCE(e.name,'') LIKE ? OR COALESCE(e.department,'') LIKE ? OR CAST(t.id AS TEXT) LIKE ?)")
            params.extend(['%'+q+'%']*4)
        where=' AND '.join(clauses)
        total=c.execute('SELECT COUNT(*) n FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id WHERE '+where,params).fetchone()['n']
        items=[dict(x) for x in c.execute(f"""SELECT t.id,t.title,t.status,t.due_at,t.created_at,
            COALESCE(e.name,'مهمة جماعية / غير معينة') employee_name,COALESCE(e.department,'—') department
            FROM tasks t LEFT JOIN employees e ON e.id=t.employee_id
            WHERE {where} ORDER BY t.id DESC LIMIT 500 OFFSET ?""",[*params,max(0,offset)])]
        return {'total':total,'items':items,'truncated':total>max(0,offset)+len(items),'next_offset':max(0,offset)+len(items)}
    finally:
        c.close()
