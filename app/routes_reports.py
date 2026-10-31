"""Operational reports.
Split out of app/main.py (package code_split) with no change in behaviour; main.py imports this module."""
from __future__ import annotations
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from typing import Optional
from .db import connect
import csv
import io
from datetime import date, datetime, timedelta
from fastapi.responses import Response
from . import access
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


# ---------- Team performance (per team and per employee, chosen period) ----------
# Counts tasks assigned directly to the employee (group-task participants are not counted).
# Archived tasks count only as "completed" (finished work stays real); dates are server local time.
def _period(date_from, date_to):
    try:
        end=date.fromisoformat(date_to) if date_to else date.today()
        start=date.fromisoformat(date_from) if date_from else end-timedelta(days=29)
    except ValueError: raise HTTPException(400,'التاريخ بصيغة YYYY-MM-DD')
    if start>end: raise HTTPException(400,'تاريخ البداية بعد تاريخ النهاية')
    if (end-start).days>366: raise HTTPException(400,'الفترة سنة كحد أقصى')
    return start.isoformat(),end.isoformat()

def _perf_rows(c, ids, start, end):
    if not ids: return []
    marks=','.join('?'*len(ids))
    q=f"""SELECT e.id employee_id,e.name,COALESCE(e.department,'') department,
      (SELECT COUNT(*) FROM tasks t WHERE t.employee_id=e.id AND t.archived_at IS NULL
         AND date(t.created_at,'localtime') BETWEEN ? AND ?) created,
      (SELECT COUNT(*) FROM tasks t WHERE t.employee_id=e.id AND t.status='تم' AND t.completed_at IS NOT NULL
         AND date(t.completed_at,'localtime') BETWEEN ? AND ?) completed,
      (SELECT COUNT(*) FROM tasks t WHERE t.employee_id=e.id AND t.status='تم' AND t.completed_at IS NOT NULL
         AND date(t.completed_at,'localtime') BETWEEN ? AND ? AND t.due_at IS NOT NULL AND t.due_at<>''
         AND datetime(t.completed_at,'localtime')>datetime(replace(t.due_at,'T',' '))) completed_late,
      (SELECT COUNT(*) FROM tasks t WHERE t.employee_id=e.id AND t.archived_at IS NULL AND t.status NOT IN ('تم','ملغاة')
         AND t.due_at IS NOT NULL AND t.due_at<>'' AND datetime(replace(t.due_at,'T',' '))<datetime('now','localtime')) overdue_now,
      (SELECT ROUND(AVG((julianday(t.completed_at)-julianday(t.created_at))*24),1) FROM tasks t
         WHERE t.employee_id=e.id AND t.status='تم' AND t.completed_at IS NOT NULL
         AND date(t.completed_at,'localtime') BETWEEN ? AND ?) avg_hours
      FROM employees e WHERE e.id IN ({marks}) ORDER BY e.name"""
    return [dict(r) for r in c.execute(q,[start,end]*4+list(ids))]

def _sum(rows):
    out={k:sum(r[k] for r in rows) for k in ('created','completed','completed_late','overdue_now')}
    w=[(r['avg_hours'],r['completed']) for r in rows if r['avg_hours'] is not None and r['completed']]
    out['avg_hours']=round(sum(a*n for a,n in w)/sum(n for _,n in w),1) if w else None
    return out

def team_performance_data(c, u, start, end):
    scope=access.employee_scope(c,u)
    if scope is None:
        ids=[r[0] for r in c.execute('SELECT id FROM employees WHERE is_active=1')]
        sups=c.execute("""SELECT u.id,COALESCE(NULLIF(u.full_name,''),u.username) name FROM users u JOIN roles r ON r.id=u.role_id
            WHERE r.code='supervisor' AND u.is_active=1 AND u.deleted_at IS NULL ORDER BY name""").fetchall()
    else:
        ids=sorted(scope); sups=[{'id':u['user_id'],'name':u['full_name'] or u['username'] or 'فريقي'}]
    rows=_perf_rows(c,ids,start,end); by_id={r['employee_id']:r for r in rows}
    teams=[]
    for s in sups:
        members=[r[0] for r in c.execute('SELECT employee_id FROM supervisor_team WHERE user_id=?',(s['id'],))]
        mrows=[by_id[i] for i in members if i in by_id]
        teams.append({'supervisor':s['name'],'members':len(mrows),**_sum(mrows)})
    return {'from':start,'to':end,'employees':rows,'teams':teams,'total':_sum(rows)}

@app.get('/team-performance',response_class=HTMLResponse)
def team_performance_page(req:Request):
    need(req,'reports.team')
    return templates.TemplateResponse(request=req,name='team_performance.html',context=ctx(req,'أداء الفريق'))

@app.get('/api/team-performance')
def team_performance(req:Request, date_from:Optional[str]=None, date_to:Optional[str]=None):
    u=need(req,'reports.team'); start,end=_period(date_from,date_to); c=connect()
    try: return team_performance_data(c,u,start,end)
    finally: c.close()

@app.get('/api/team-performance.csv')
def team_performance_csv(req:Request, date_from:Optional[str]=None, date_to:Optional[str]=None):
    u=need(req,'reports.team'); start,end=_period(date_from,date_to); c=connect()
    try: d=team_performance_data(c,u,start,end)
    finally: c.close()
    buf=io.StringIO(); w=csv.writer(buf)
    w.writerow([f'أداء الفريق من {start} إلى {end}'])
    w.writerow(['الموظف','القسم','مهام جديدة','مكتملة','مكتملة متأخرة','متأخرة الآن','متوسط ساعات الإنجاز'])
    for r in d['employees']:
        w.writerow([r['name'],r['department'],r['created'],r['completed'],r['completed_late'],r['overdue_now'],r['avg_hours'] if r['avg_hours'] is not None else ''])
    if d['teams']:
        w.writerow([]); w.writerow(['الفريق (المسؤول)','عدد الموظفين','مهام جديدة','مكتملة','مكتملة متأخرة','متأخرة الآن','متوسط ساعات الإنجاز'])
        for t in d['teams']:
            w.writerow([t['supervisor'],t['members'],t['created'],t['completed'],t['completed_late'],t['overdue_now'],t['avg_hours'] if t['avg_hours'] is not None else ''])
    name=f'team_performance_{start}_{end}.csv'
    # BOM so Excel opens the Arabic text correctly.
    return Response('\ufeff'+buf.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{name}"'})
