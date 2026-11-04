# BIM Tasks — notes for whoever changes this code

Task management system for BIM Damascus. FastAPI + SQLite (WAL), Arabic RTL UI.
The owner talks in Syrian Arabic; answer in the same dialect.

## Production server (Windows, not reachable from here)
- Root: `C:\BIM_Tasks_Server`, Python venv at `.venv\Scripts\python.exe`
- Two processes of the same app sharing one SQLite file:
  - LAN gateway `0.0.0.0:8797`, scheduled task **`\BIM Tasks\LAN`** (inside the "BIM Tasks" folder — plain `LAN` does not exist) → `RUN_LAN.cmd`
  - External gateway `127.0.0.1:8798`, scheduled task **`BIM Tasks External 8798`** → `RUN_EXTERNAL.cmd`; reached through a tunnel at https://task.bimdamascus.com
- Other tasks: `\BIM Tasks\Backup Hourly`, `\BIM Tasks\Backup Daily Full`, `BIM_Tasks_Full_Google_Sheets_Sync`
- Workers (Telegram queue, reminders, backups) run only in the LAN process.
- Secrets live only in the server's `.env` (see `.env.example`). Never commit `.env` or `data/`.

## How updates reach the server
The owner does not run git on the server. Every change ships as a self-installing zip:
`deploy/<update>/` (installer) + `payload/` (changed files, same paths as the server root) + `wheels/` for new dependencies.
The owner extracts it on the server and runs the `.cmd` as administrator.

Build every update with the generic installer (`deploy/installer/`), never a hand-written one:
```
python deploy/build_update.py <name> main "<Arabic title>" --check <route that only the new code has>
```
It ships the git diff between `main` (= what the server runs) and HEAD, refuses to run twice, and rolls back on failure.
`--check` is optional: every package carries `app/release.json` (commit), and the installer requires each port's
`/api/release-health` to report that commit and its own gateway (8797 `lan`, 8798 `external`, 8799 `test`).

Staging copy: `TEST_COPY_CREATE.cmd` (server root, double-click as admin) builds/refreshes `C:\BIM_Tasks_Test`
on `127.0.0.1:8799` from the live data (`tools/make_test_copy.ps1` + `.py`). The copy has no Telegram token, no Sheet
secret/URL, no off-site backup settings, its own backup dir, login confirmation off, and `BIM_GATEWAY=test` (no workers).
`RUN_TEST.cmd` refuses to start without `TEST_COPY.txt`. Every package also has `INSTALL_TO_TEST.cmd`
(= `install.ps1 -Test`): try it there first. The real installer and the test one never stop each other's processes.
Work on a branch; merge into `main` only after the owner confirms the install succeeded.

Lessons from the first install (2026-10-31), keep them in every installer:
- Use the real task names above; fall back to `RUN_LAN.cmd` / `RUN_EXTERNAL.cmd` if a task is missing.
- Stop by matching `uvicorn` + `app.main:app` in the process command line, not the root path (the venv launcher spawns a child python without it). Wait until ports 8797/8798 are free.
- After starting, verify the NEW code answers (hit a route that only exists in the new version), not just `/health`.
- Refuse to run twice over a half-finished install: a second run backs up already-updated files and ruins the rollback copy.
- Windows PowerShell 5.1: save `.ps1` as UTF-8 with BOM (Arabic text), use `$ErrorActionPreference='Continue'` (pip writes to stderr), and tell the user not to click inside the console (QuickEdit freezes it).
- Prefer giving the owner a double-click file over commands to paste.

## Access model
`app/access.py` is the single source of truth: roles (admin, deputy, supervisor = "المسؤول", employee; viewer is hidden legacy),
the permission matrix, per-user override ceilings, supervisor team scope, page→permission table and public paths.
`main.auth_guard` enforces default-deny plus the page table on every request; routes also call `need()`.
Do not grant permissions by editing `role_permissions` in the DB — `seed()` rewrites it from `access.py` on every start.

## Tests
`python -m pytest tests -q` — runs against a temporary copy, never touches `data/`.
Known pre-existing issue: on a completely empty database `delegation.py` reads `settings` at import time and fails; tests call `init_db()` first.

## Code layout (since package `code_split`)
- big_update: merged pages/tab bar in `shell_nav.TABS`/`MERGED` (+ `base.html`), task views in `routes_pages.TASK_VIEWS`,
  bell in `app/inbox.py` (table `inbox_items`), profile in `app/employee_profile.py`. Table columns fit content: keep it.
- `app/main.py`: settings, app object, middleware, shared helpers (auth/session, Telegram task texts, Sheet push).
  Route modules `app/routes_{auth,pages,telegram,shifts,reports,employees,tasks,recurring,users,backup,sync}.py`
  import helpers `from .main import ...` and read run-time-changeable settings as `_main.X` (GATEWAY, RELEASE,
  PUBLIC_URL, COOKIE_SECURE, IPHONE_SECRET, TELEGRAM_WEBHOOK_SECRET, mfa_*). main.py imports every route module
  back, so `m.x` in the plugin modules (`register(app, m)`) and `from .main import x` keep working.
- New routes go in the matching `routes_*.py`. If you add a module, import it in main.py's routes block.
- `tests/test_routes_snapshot.py` pins every GET answer per role. A deliberate change: regenerate with
  `BIM_UPDATE_SNAPSHOT=1 python -m pytest tests/test_routes_snapshot.py -q` and say why in the commit.


## Standing rules (owner decisions — keep)
- COST (owner 2026-11-04): always do whatever saves the owner tokens/money without asking first (keep this file short,
  no needless agents/fetches, short answers). Still confirm anything that reaches the server, GitHub `main` or outsiders.
- iPhone shortcut keys work from outside for every role: `iphone_accounts.eligible` has no gateway check — do not add one.
- Every table column is as wide as its longest cell, never wider (no width:100%); only the long text column may wrap.
- Never drop a button, field or option when changing a page. Small updates ship separately from design work.
- Design: all look changes in `app/static/bim-ds.css` (tiles model, Cairo from fonts/BIMArabic.woff2, logo without frame);
  the menu is `app/shell_nav.py`. Asset URLs carry `?v={{ asset_v }}`; stale browser cache looks like "nothing changed".
- Other systems share the server (leave alone): 8070 `C:\BIM_Hall_Shortages`, 8080 `C:\نواقص الموردين`, Athar_Yawmi,
  BIM Damascus Shops, BIM Rent 8093, BIM ShamCash; python also on 8787/8788/8843/8092. They are uvicorn `app.main:app`
  too: never stop python by name or by `app.main` alone — only by our ports 8797/8798 (8799 test).
- Full sync (`full_sync/`): log tables incremental by id, other tables by row hash, full once a day after 03:00.
  `shift_dispatch_log`/`group_dispatch_log` are NOT append-only (workers update/delete). An occasional 0x800710E0 is normal.
- Status report: `tools/check_status/` (desktop shortcut "تقرير حالة BIM"). Read-only, no secrets.

## Current state (2026-11-04)
- Server = e98e2a8 (`followup_tasks`) on 8797 lan+workers, 8798 external, 8799 test. `main` = server code. Nothing pending.
- Follow-up Telegram group (`app/followup.py`, settings at the top of /telegram-groups): per-task cards, morning list with
  one button per task, Reply = comment, `remind_ids` limits who may press «ذكّر».
- Ask the owner: do the per-task buttons / Reply-comments work in the real group? Did supervisors get the 18:00 summary?
- Optional: drop `?secret=` from the Sheet sync (never seen since 2026-10-31; Apps Script uses the header).
- Test copy data is from 2026-10-31: run TEST_COPY_CREATE.cmd before the next INSTALL_TO_TEST.
- History (every package, report and diagnosis): `docs/handoff/HISTORY.md`; per-session plans `docs/handoff/SESSION_<n>.md`.
