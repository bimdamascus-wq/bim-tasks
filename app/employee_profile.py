"""Employee profile /employee/{id} (big_update, owner 2026-11-02): opened by clicking an employee name anywhere.

Scope: managers see every employee, a supervisor only the employees of their team (access.employee_scope),
an employee is sent to /my-work. The tasks themselves come from /api/tasks?employee_id=..., so the usual task scope
and the quick actions' own checks (status, reassign, Telegram reminder) apply unchanged.
"""
from datetime import date, timedelta
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from . import access
from .db import connect

OPEN = "t.archived_at IS NULL AND t.status NOT IN ('تم','ملغاة')"
LATE = OPEN + " AND t.due_at IS NOT NULL AND t.due_at<>'' AND datetime(replace(t.due_at,'T',' '))<datetime('now','localtime')"


def _allowed(c, u, eid):
    scope = access.employee_scope(c, u)
    return scope is None or eid in scope


def profile_data(c, eid):
    e = c.execute('''SELECT id,name,COALESCE(department,'') department,telegram_chat_id,telegram_linked_at,telegram_enabled,
        is_active,shift_type,shift_start,shift_end FROM employees WHERE id=?''', (eid,)).fetchone()
    if not e:
        return None
    jobs = [r[0] for r in c.execute('''SELECT j.name FROM bim_employee_jobs ej JOIN bim_job_profiles j ON j.id=ej.job_id
        WHERE ej.employee_id=? ORDER BY j.name''', (eid,))]
    q = lambda where, p=(): c.execute(f'SELECT COUNT(*) FROM tasks t WHERE t.employee_id=? AND {where}', (eid, *p)).fetchone()[0]
    counts = {
        'open': q(OPEN), 'late': q(LATE),
        'done_week': q("t.status='تم' AND t.completed_at IS NOT NULL AND datetime(t.completed_at,'localtime')>=datetime('now','localtime','-7 days')"),
        'unseen': q(OPEN + ' AND t.viewed_at IS NULL'),
        'archived': q('t.archived_at IS NOT NULL'),
    }
    recurring = [dict(r) for r in c.execute('''SELECT r.id,r.title,r.recurrence_type,r.week_day,r.month_day,r.run_time,r.is_active,r.priority
        FROM recurring_tasks r WHERE r.employee_id=? AND r.id NOT IN(SELECT record_id FROM record_lifecycle WHERE entity='recurring' AND deleted_at IS NOT NULL)
        ORDER BY r.is_active DESC,r.id DESC''', (eid,))]
    from .routes_reports import _perf_rows
    end = date.today(); start = end - timedelta(days=29)
    perf = (_perf_rows(c, [eid], start.isoformat(), end.isoformat()) or [{}])[0]
    done = perf.get('completed') or 0
    perf = {'from': start.isoformat(), 'to': end.isoformat(), 'created': perf.get('created') or 0, 'completed': done,
            'completed_late': perf.get('completed_late') or 0, 'avg_hours': perf.get('avg_hours'),
            'on_time': round(100 * (done - (perf.get('completed_late') or 0)) / done) if done else None}
    user = c.execute('''SELECT u.username,r.code role_code FROM users u JOIN roles r ON r.id=u.role_id
        WHERE u.employee_id=? AND u.deleted_at IS NULL ORDER BY u.is_active DESC LIMIT 1''', (eid,)).fetchone()
    return {'employee': {'id': e['id'], 'name': e['name'], 'department': e['department'], 'jobs': jobs,
                         'telegram_linked': bool(e['telegram_chat_id']), 'telegram_enabled': bool(e['telegram_enabled']),
                         'telegram_linked_at': e['telegram_linked_at'], 'is_active': bool(e['is_active']),
                         'shift': e['shift_type'] if e['shift_type'] and e['shift_type'] != 'بدون' else '',
                         'shift_start': e['shift_start'], 'shift_end': e['shift_end'],
                         'role': access.ROLE_LABELS.get(user['role_code'], '') if user else ''},
            'counts': counts, 'recurring': recurring, 'performance': perf}


def register(app, m):
    @app.get('/employee/{eid}', response_class=HTMLResponse)
    def employee_page(eid: int, req: Request):
        u = m.session(req)
        if not u:
            return RedirectResponse('/login', status_code=303)
        m.need(req, 'tasks.view')
        if u['role_code'] == 'employee':
            return RedirectResponse('/my-work', status_code=303)
        c = connect()
        try:
            if not _allowed(c, u, eid):
                raise HTTPException(403, 'هذا الموظف خارج نطاق فريقك')
            row = c.execute('SELECT name FROM employees WHERE id=?', (eid,)).fetchone()
        finally:
            c.close()
        if not row:
            raise HTTPException(404, 'الموظف غير موجود')
        return m.templates.TemplateResponse(request=req, name='employee_profile.html',
                                            context={**m.ctx(req, 'ملف الموظف'), 'employee_id': eid, 'employee_name': row['name']})

    @app.get('/api/employee-profile/{eid}')
    def employee_profile(eid: int, req: Request):
        u = m.need(req, 'tasks.view')
        if u['role_code'] == 'employee':
            raise HTTPException(403, 'غير مصرح')
        c = connect()
        try:
            if not _allowed(c, u, eid):
                raise HTTPException(403, 'هذا الموظف خارج نطاق فريقك')
            d = profile_data(c, eid)
            if not d:
                raise HTTPException(404, 'الموظف غير موجود')
            return d
        finally:
            c.close()
