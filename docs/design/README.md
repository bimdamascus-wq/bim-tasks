# Design options shown to the owner (2026-10-31)
Preview with real screenshots: https://claude.ai/artifact/6BjN9L4ZrpbfoZ9snVDPDo
- Model 1 = current `app/static/bim-ds.css` (light, white side menu, coloured borders, Fluent 3D icons).
- Model 2 = `model2_night.css` loaded AFTER bim-ds.css (dark navy, top icon bar, glass cards).
- Model 3 = `model3_tiles.css` loaded AFTER bim-ds.css (warm light, slim icon rail, solid colour blocks; uses Cairo from Google Fonts).
When the owner picks 2 or 3: append that file to the end of app/static/bim-ds.css (or link it after it in base.html,
command_center.html, login pages), re-run the browser check, rebuild the package.

- 2026-11-01 (after tiles install): owner wants the side menu wider and /my-team cards clearly separated.
  Preview https://claude.ai/artifact/PwDBvEfghPnKMY2nZc13LM : sidebar_A.css (150px, icon above label) or sidebar_B.css
  (220px, icon beside label) + my_team_cards.css. Append the chosen ones to app/static/bim-ds.css.
  Owner screen: ~1714px wide. Asset cache: bim-ds.css?v=2 never changed across packages and the service worker is
  cache-first for /static -> stale design after installs. Fix in the next package (version from release commit).
