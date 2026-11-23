"""BIM Tasks v2: the new interface built from scratch (owner 2026-11-23: "build it new, 100% different, professional").
Seven pages under /v2 with their own frame (templates/v2/base.html) and stylesheet (static/v2.css). They use the
existing APIs only, so data, Telegram and permissions are unchanged. Rare admin pages stay in the old interface and are
reached from «الإعدادات»."""
from __future__ import annotations
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .main import app, ctx, need, perms, session, templates

# (key, address, label, permission needed to see it)
NAV = [('home', '/v2', 'الرئيسية', 'tasks.edit'), ('tasks', '/v2/tasks', 'المهام', 'tasks.edit'),
       ('alerts', '/v2/alerts', 'التنبيهات', 'tasks.edit'), ('team', '/v2/team', 'الفريق', 'employees.view'),
       ('recurring', '/v2/recurring', 'المتكررة', 'recurring.view'), ('reports', '/v2/reports', 'التقارير', 'reports.team'),
       ('settings', '/v2/settings', 'الإعدادات', None)]
PAGES = {key: (label, code) for key, _, label, code in NAV}


def _page(req: Request, key: str, extra=None):
    label, code = PAGES[key]
    if not session(req):
        return RedirectResponse('/login', status_code=303)
    if code:
        need(req, code)
    return templates.TemplateResponse(request=req, name=f'v2/{key}.html',
                                      context={**ctx(req, label), 'v2_nav': NAV, 'v2_page': key, **(extra or {})})


@app.get('/v2', response_class=HTMLResponse)
def v2_home(req: Request):
    u = session(req)
    if u and u['role_code'] == 'employee':  # the installed app starts at /v2 for everyone
        return RedirectResponse('/my-work', status_code=303)
    return _page(req, 'home')


@app.get('/v2/tasks', response_class=HTMLResponse)
def v2_tasks(req: Request): return _page(req, 'tasks')


@app.get('/v2/alerts', response_class=HTMLResponse)
def v2_alerts(req: Request): return _page(req, 'alerts')


@app.get('/v2/team', response_class=HTMLResponse)
def v2_team(req: Request): return _page(req, 'team')


@app.get('/v2/recurring', response_class=HTMLResponse)
def v2_recurring(req: Request): return _page(req, 'recurring')


@app.get('/v2/reports', response_class=HTMLResponse)
def v2_reports(req: Request): return _page(req, 'reports')


@app.get('/v2/settings', response_class=HTMLResponse)
def v2_settings(req: Request):
    u = session(req)
    if not u:
        return RedirectResponse('/login', status_code=303)
    from . import shell_nav
    return _page(req, 'settings', {'hub': shell_nav.hub(u, perms(u['user_id']))})
