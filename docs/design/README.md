# Design options shown to the owner (2026-10-31)
Preview with real screenshots: https://claude.ai/artifact/6BjN9L4ZrpbfoZ9snVDPDo
- Model 1 = current `app/static/bim-ds.css` (light, white side menu, coloured borders, Fluent 3D icons).
- Model 2 = `model2_night.css` loaded AFTER bim-ds.css (dark navy, top icon bar, glass cards).
- Model 3 = `model3_tiles.css` loaded AFTER bim-ds.css (warm light, slim icon rail, solid colour blocks; uses Cairo from Google Fonts).
When the owner picks 2 or 3: append that file to the end of app/static/bim-ds.css (or link it after it in base.html,
command_center.html, login pages), re-run the browser check, rebuild the package.
