"""Short side menu + «الإعدادات» page (owner 2026-11-10: too many pages). Nothing may become unreachable."""
import re
from conftest import client


def _hrefs(html, start, end):
    part = html[html.index(start):html.index(end)] if end else html[html.index(start):]
    return re.findall(r'href="([^"?#]+)', part)


def test_every_page_is_in_menu_or_settings(env):
    from app import shell_nav
    for user in ('boss', 'samer', 'member'):
        cl = client(env, user)
        page = cl.get('/settings')
        assert page.status_code == 200
        side = set(_hrefs(page.text, 'ds-nav', 'ds-user'))
        hub = set(_hrefs(page.text, '<main class="hub"', None))
        # what the account could open from the old long menu
        me = env['m']
        c = me.connect()
        u = c.execute("SELECT u.*,r.code role_code FROM users u JOIN roles r ON r.id=u.role_id WHERE username=?", (user,)).fetchone()
        c.close()
        everything = {it['path'] for _, _, it in shell_nav._items(dict(u), me.perms(u['id']), '/settings')}
        assert everything <= side | hub, (user, everything - side - hub)
        for h in hub - {'/settings'}:   # every tile opens for this account
            if h.startswith('/'):
                assert cl.get(h, follow_redirects=False).status_code in (200, 303), (user, h)
        if user == 'boss':
            menu = [h for h in _hrefs(page.text, 'ds-nav', 'ds-user') if h not in ('/account', '/logout')]
            assert len(menu) <= 8 and '/settings' in menu and '/users' not in menu
            assert 'is-active' in page.text[page.text.index('href="/settings"') - 120:page.text.index('href="/settings"') + 80]
