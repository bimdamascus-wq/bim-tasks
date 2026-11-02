# Browser checks used for the redesign (not shipped to the server)
Run a local copy: copy `app/` and `schema.sql` to a temp dir, `init_db()` + `seed()`, add demo employees/users
(boss / samer supervisor with a team / emp1 employee), then
`BIM_ADMIN_USERNAME=boss BIM_ADMIN_PASSWORD=Boss-Strong-Pass-2026 BIM_GATEWAY=test TELEGRAM_BOT_TOKEN= python -m uvicorn app.main:app --port 8766`.
Playwright is pre-installed: `NODE_PATH=$(npm root -g) node <script>`.
- `cmp.js USER PW` — old server on 8765 vs new on 8766: per page, every button text, field name, link and element id
  that exists in old but not in new (nothing may be missing), plus menu destinations. Run for boss, samer, emp1.
- `contrast.js [extra.css]` — flags visible text with contrast < 3:1 on 37 pages (admin). Handles `color(srgb …)`.
- `shots_multi.js OUT USER PW WIDTH HEIGHT TAG css1,css2 /page …` — screenshots with optional injected CSS
  (used to show design options before building). Owner's screen is ~1714px wide; never downscale previews.
