"""Navigation of the new page shell (design 2026-10-31, base.html).

Built on the server from access.py, so a link shows exactly when the server would open the page for this account.
Every page that the old menus offered (the JS-built sidebar of bim-ui-v510.js, the old top links in base.html and
the admin home menu) is listed here; pages hidden by the admin on /page-visibility stay hidden, as before.
"""
from . import access
from .db import connect

# (group, [(path, label, icon)])
GROUPS = [
    ('الرئيسية', [('/', 'لوحة المدير', 'home'), ('/my-work', 'مهامي', 'check'), ('/my-team', 'متابعة الفريق', 'team')]),
    ('المهام', [('/tasks', 'كل المهام', 'list'), ('/tasks-table', 'المهام كجدول', 'table'), ('/manage', 'إدارة المهام', 'edit'),
                ('/recurring', 'المهام المتكررة', 'repeat'), ('/archive', 'الأرشيف', 'archive')]),
    ('الفريق', [('/employees', 'الموظفون', 'team'), ('/users', 'المستخدمون', 'user'), ('/access-control', 'الصلاحيات والقنوات', 'key'),
                ('/job-profiles', 'المسميات الوظيفية', 'badge'), ('/workload', 'عبء العمل', 'gauge'), ('/handover', 'تسليم الأعمال', 'swap'),
                ('/shift-center', 'الشيفتات', 'clock')]),
    ('التقارير والسجلات', [('/team-performance', 'أداء الفريق', 'chart'), ('/operations-reports', 'التقارير التشغيلية', 'chart'),
                           ('/activity-log', 'السجل الشامل', 'history'), ('/audit', 'سجل التغييرات', 'history'),
                           ('/record-management', 'إدارة السجلات والحذف', 'archive')]),
    ('الأتمتة والربط', [('/template-library', 'قوالب العمل', 'layers'), ('/automation-center', 'مركز الأتمتة', 'bolt'),
                        ('/notification-center', 'مركز الإشعارات', 'bell'), ('/telegram-center', 'Telegram', 'send'),
                        ('/telegram-groups', 'مجموعات Telegram', 'send'), ('/telegram-health', 'صحة Telegram', 'pulse'),
                        ('/telegram-dashboard', 'تقارير Telegram', 'chart'), ('/iphone-setup', 'إعداد الهاتف', 'phone'),
                        ('/integrations', 'الربط والتكامل', 'link')]),
    ('النظام', [('/security', 'الأمان والحماية', 'shield'), ('/permissions', 'جدول الصلاحيات', 'key'), ('/company', 'إعداد الشركة', 'building'),
                ('/page-visibility', 'الصفحات الظاهرة', 'eye'), ('/system-health', 'صحة النظام', 'pulse'), ('/sync', 'مزامنة Google Sheet', 'sync'),
                ('/backup', 'النسخ الاحتياطي', 'shield')]),
]

ICONS = {
    'home': 'M3 11l9-8 9 8v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z',
    'check': 'M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9',
    'team': 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
    'list': 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
    'table': 'M3 3h18v18H3zM3 9h18M3 15h18M9 3v18',
    'edit': 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z',
    'repeat': 'M17 1l4 4-4 4M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4M21 13v2a4 4 0 0 1-4 4H3',
    'archive': 'M21 8v13H3V8M1 3h22v5H1zM10 12h4',
    'user': 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8',
    'key': 'M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8zM15.5 7.5l3 3L22 7l-3-3',
    'badge': 'M4 4h16v16H4zM8 9h8M8 13h5',
    'gauge': 'M12 14l4-4M3.3 19a10 10 0 1 1 17.4 0',
    'swap': 'M16 3l4 4-4 4M20 7H4M8 21l-4-4 4-4M4 17h16',
    'clock': 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2',
    'chart': 'M3 3v18h18M7 15l4-4 3 3 5-6',
    'history': 'M3 3v5h5M3.05 13A9 9 0 1 0 6 5.3L3 8M12 7v5l4 2',
    'layers': 'M12 2l10 5-10 5L2 7zM2 17l10 5 10-5M2 12l10 5 10-5',
    'bolt': 'M13 2L3 14h9l-1 8 10-12h-9z',
    'bell': 'M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0',
    'send': 'M22 2L11 13M22 2l-7 20-4-9-9-4z',
    'pulse': 'M22 12h-4l-3 9L9 3l-3 9H2',
    'phone': 'M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM12 18h.01',
    'link': 'M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7',
    'shield': 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
    'building': 'M3 21h18M5 21V7l7-4 7 4v14M9 9h1M9 13h1M9 17h1M14 9h1M14 13h1M14 17h1',
    'eye': 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6',
    'sync': 'M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15',
    'account': 'M12 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM3 22a9 9 0 0 1 18 0',
    'logout': 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
}

# Colour of each page's 3D icon (bim-ds.css .c-<name>): related pages share a colour family.
COLORS = {
    '/': 'indigo', '/my-work': 'green', '/my-team': 'teal',
    '/tasks': 'blue', '/tasks-table': 'blue', '/manage': 'orange', '/recurring': 'purple', '/archive': 'slate',
    '/employees': 'teal', '/users': 'indigo', '/access-control': 'amber', '/job-profiles': 'pink', '/workload': 'orange',
    '/handover': 'purple', '/shift-center': 'amber',
    '/team-performance': 'green', '/operations-reports': 'blue', '/activity-log': 'slate', '/audit': 'slate',
    '/record-management': 'red',
    '/template-library': 'pink', '/automation-center': 'amber', '/notification-center': 'red', '/telegram-center': 'sky',
    '/telegram-groups': 'sky', '/telegram-health': 'green', '/telegram-dashboard': 'sky', '/iphone-setup': 'slate',
    '/integrations': 'purple',
    '/security': 'red', '/permissions': 'amber', '/company': 'indigo', '/page-visibility': 'teal', '/system-health': 'green',
    '/sync': 'blue', '/backup': 'purple', '/account': 'indigo',
}

# 3D picture of each page (app/static/icons3d/<name>.png, Microsoft Fluent Emoji 3D, MIT licence) — same family as
# the icons of the owner's financial report.
ICON3D = {
    '/': 'home', '/my-work': 'check', '/my-team': 'team',
    '/tasks': 'clipboard', '/tasks-table': 'table', '/manage': 'memo', '/recurring': 'repeat', '/archive': 'archive',
    '/employees': 'briefcase', '/users': 'user', '/access-control': 'key', '/job-profiles': 'bookmark', '/workload': 'hourglass',
    '/handover': 'handover', '/shift-center': 'alarm',
    '/team-performance': 'chartup', '/operations-reports': 'barchart', '/activity-log': 'scroll', '/audit': 'ledger',
    '/record-management': 'trash',
    '/template-library': 'folder', '/automation-center': 'bolt', '/notification-center': 'bell', '/telegram-center': 'envelope',
    '/telegram-groups': 'speech', '/telegram-health': 'stethoscope', '/telegram-dashboard': 'antenna', '/iphone-setup': 'phone',
    '/integrations': 'link',
    '/security': 'shield', '/permissions': 'lock', '/company': 'building', '/page-visibility': 'eye', '/system-health': 'heart',
    '/sync': 'sync', '/backup': 'floppy', '/account': 'gear',
}

TITLES = {p: l for _, items in GROUPS for p, l, _ in items}
PAGE_ICON = {p: ic for _, items in GROUPS for p, _, ic in items}
PAGE_ICON['/account'] = 'account'
TITLES.update({'/account': 'حسابي'})


def _hidden():
    try:
        from .command_center import hidden_pages
        c = connect()
        try:
            return set(hidden_pages(c))
        finally:
            c.close()
    except Exception:
        return set()


def nav(user, permissions, path='/'):
    """[(group, [{'path','label','icon','active'}])] for this account; empty groups are dropped.
    Pages the admin hid on /page-visibility are not dropped (the old top links still showed them): they move to a
    last group "صفحات أخرى", so every page stays one click away."""
    if not user:
        return []
    perms = set(permissions or ())
    role = user['role_code']
    hidden = _hidden()
    out, more = [], []
    for group, items in GROUPS:
        links = []
        for p, label, icon in items:
            if p == '/' and role not in access.MANAGERS:
                continue
            if p == '/my-team' and role == 'employee':
                continue
            if p == '/my-work' and role != 'employee':  # the page sends everyone else to /tasks
                continue
            need = access.page_permission(p)
            if need and need not in perms:
                continue
            active = path == p or (p != '/' and path.startswith(p + '/'))
            item = {'path': p, 'label': label, 'icon': ICONS[icon], 'active': active, 'color': COLORS.get(p, 'blue'), 'img': ICON3D.get(p, 'clipboard')}
            (more if p in hidden and p not in ('/', '/my-work', '/my-team') else links).append(item)
        if links:
            out.append((group, links))
    if more:
        out.append(('صفحات أخرى', more))
    return out


def icon(name):
    return ICONS.get(name, '')


def page(path):
    """3D icon of the page shown in the top bar: {'icon', 'color'}."""
    key = path if path in PAGE_ICON else ('/tasks' if path.startswith('/task/') else '')
    return {'icon': ICONS.get(PAGE_ICON.get(key, 'list'), ''), 'color': COLORS.get(key, 'blue'), 'img': ICON3D.get(key, 'clipboard')}
