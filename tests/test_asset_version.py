"""A CSS-only change must change the asset version, or browsers keep the old design (owner 2026-11-23)."""


def test_css_change_changes_asset_version(env):
    m = env['m']
    css = m.ROOT / 'app' / 'static' / 'bim-ds.css'
    before, old = m._asset_version(), css.read_bytes()
    try:
        css.write_bytes(old + b'\n/* test */')
        assert m._asset_version() != before
    finally:
        css.write_bytes(old)
