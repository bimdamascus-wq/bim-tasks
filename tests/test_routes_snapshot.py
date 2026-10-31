"""Pins what every route answers today, so refactors (splitting app/main.py, removing dead code) change nothing.

* The route table (method + path + endpoint name) must stay the same.
* Every GET route is opened as admin, deputy, supervisor and employee; the status code, the kind of answer and its
  shape (JSON keys, two levels deep; HTML <title>, static files loaded, element ids) must match tests/snapshots/routes.json.
Regenerate only on purpose:  BIM_UPDATE_SNAPSHOT=1 python -m pytest tests/test_routes_snapshot.py -q
"""
import json, os, re
from pathlib import Path
from conftest import client, task

SNAP = Path(__file__).parent / 'snapshots' / 'routes.json'
SKIP = {'/logout'}                     # would end the session used for the next routes
ROLES = ['boss', 'dep', 'samer', 'member']
VOLATILE = re.compile(r'^(\d+|\d{4}-\d\d(-\d\d)?.*|T-\d+)$')   # ids and dates used as keys


def shape(v, depth=0):
    if depth >= 2:
        return type(v).__name__
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            out['<key>' if VOLATILE.match(str(k)) else k] = shape(x, depth + 1)
        return out
    if isinstance(v, list):
        return [shape(v[0], depth + 1)] if v else []
    return type(v).__name__


def answer(res):
    ctype = res.headers.get('content-type', '').split(';')[0]
    out = {'status': res.status_code, 'type': ctype}
    if res.status_code in (301, 302, 303, 307):
        out['location'] = res.headers.get('location')
    elif ctype == 'application/json':
        out['shape'] = shape(res.json())
    elif ctype == 'text/html':
        t = re.search(r'<title>(.*?)</title>', res.text, re.S)
        out['title'] = t.group(1).strip() if t else None
        # Which CSS/JS files the page loads, in order (guards dead-code removal and template moves).
        out['assets'] = re.findall(r'(?:src|href)="(/static/[^"?]+)', res.text)
        out['ids'] = sorted(set(re.findall(r'\bid="([A-Za-z][\w-]*)"', res.text)))
    return out


def route_table(m):
    rows = []
    for r in m.app.routes:
        for meth in sorted(getattr(r, 'methods', None) or ['WS']):
            rows.append(f"{meth} {getattr(r, 'path', '')} {getattr(r, 'name', '')}")
    return sorted(rows)


def test_routes_answer_as_before(env):
    m, ids = env['m'], env['ids']
    boss = client(env, 'boss')
    tid = task(boss, ids['عضو فريق'], title='لقطة')
    c = m.connect()
    samer = c.execute("SELECT id FROM users WHERE username='samer'").fetchone()[0]
    c.close()
    params = {'tid': tid, 'eid': ids['عضو فريق'], 'uid': samer, 'aid': 1}
    got = {'routes': route_table(m), 'answers': {}}
    gets = sorted({r.path for r in m.app.routes if 'GET' in (getattr(r, 'methods', None) or ())
                   and not r.path.startswith(('/openapi', '/docs', '/redoc', '/static'))} - SKIP)
    for user in ROLES:
        cl = boss if user == 'boss' else client(env, user)
        for path in gets:
            url = re.sub(r'\{(\w+)\}', lambda x: str(params[x.group(1)]), path)
            got['answers'][f'{user} {path}'] = answer(cl.get(url, follow_redirects=False))
    if os.environ.get('BIM_UPDATE_SNAPSHOT') == '1' or not SNAP.exists():
        SNAP.write_text(json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
    want = json.loads(SNAP.read_text(encoding='utf-8'))
    assert got['routes'] == want['routes']
    diff = {k: (want['answers'].get(k), v) for k, v in got['answers'].items() if want['answers'].get(k) != v}
    missing = set(want['answers']) - set(got['answers'])
    assert not diff and not missing, json.dumps({'changed': diff, 'missing': sorted(missing)}, ensure_ascii=False, indent=1)[:4000]
