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

## Deployment log
- 2026-10-31: security update installed on the server with `deploy/security_update` (v2). Repository `main` matches the server code.
- 2026-10-31: update `telegram_backup` (branch `next-update`, package built with `deploy/build_update.py`) given to the owner.
  Owner confirmed it is installed; merged into `main`. Still ask whether the daily backup file arrives on Telegram.

- 2026-10-31: update `gateway_staging` installed 11:07 (status report: 8797 lan+workers, 8798 external no workers,
  8799 test copy, all on commit 22e9f34). `main` fast-forwarded to 22e9f34 = server code.
  Off-site backup to Telegram ran OK at 10:33 (manual, 1 part); first automatic run is 02:00 — ask whether it arrived.
- Status tool: `tools/check_status/` (shipped to the server from package `code_split` on; installer puts a desktop shortcut "تقرير حالة BIM" that runs as administrator; before that: zip it, owner runs `CHECK_STATUS.cmd`, pastes the report). Read-only, no secrets.
  On Windows each uvicorn shows twice (venv launcher + Python313 child) — normal. Task "Last Result 267009" = still running — normal.
- Pending on the branch, not shipped yet: fix for `/api/shift-test` (500, seen in server-lan.err.log).
- Pending on the branch: `full_sync/RUN_FULL_SYNC.cmd` ended with `pause`, so the scheduled run (SYSTEM, no one to press a key)
  never ended; the next 5-minute run was refused (Last Result 0x800710E0). Removed. The sheet still looked fine because the
  app pushes each change instantly (`_try_instant_sheet_push`); the full sync is the catch-up. After install, the status
  report should show that task "Ready" with Last Result 0 between runs.
- Owner confirmed: manual Telegram backup file arrived. Still ask about the automatic 02:00 one.
- 2026-10-31 (session 2): package `fixes_tests` (commit 5123848, branch `claude/confident-darwin-2zs0qr`, base 22e9f34)
  given to the owner with `BIM_Check_Status.zip` — NOT yet confirmed installed. Contains the shift-test fix, the
  RUN_FULL_SYNC pause fix, group tasks in shift-start/end messages (open item 5), the pytest gate in build_update.py,
  failed Telegram rows in the status report (open item 6), and a note of how the Sheet sends its secret.
  Apps Script code is not in the repo: the status report's "[google sheet -> server ...]" section shows whether
  `?secret=` is still used. Only if it is, ask the owner to paste the Apps Script (secret value removed) and give exact lines.
- 2026-10-31 13:23: owner installed `fixes_tests` (report 13:47: 8797 lan+workers, 8798 external, both commit 5123848).
  Not merged into `main` yet: the report crashed (cp1252 stdout + Arabic employee name, fixed) before the failed-Telegram
  and Sheet-secret sections. Full sync task showed Running + Last Result 0x800710E0 (a run was refused because one was
  still running): either an old instance started before the install still waits on the removed `pause`, or a run takes
  longer than 5 minutes. Report now lists sync processes with start times and the last SUMMARY_*.json runs to tell which.
  The 02:00 off-site backup has not had a chance yet: `telegram_backup` was installed 10:31, first automatic run 2026-11-01 02:00.
- Report 13:50 answered all three:
  * Full sync stuck: `cmd.exe` pid 2052 started 07:57:05 = the last pre-install run (finished 08:01, then sat on `pause`).
    Given the owner `deploy/fix_sheet_sync/` (kills sync processes older than 20 min). Runs take 4-11 min on a 5-minute
    schedule, so an occasional 0x800710E0 ("already running, refused") is expected even when healthy; judge by SUMMARY_*.json.
  * Failed Telegram rows #6/#7 (2026-10-29 13:00, "timed out" / "getaddrinfo failed", 5 tries over 26 min): the server had
    no internet/DNS then. Not a code fault; nothing changed.
  * The Apps Script ALREADY sends `X-BIM-Sync-Secret` (header seen 13:50 on /api/sync/tasks-page, `?secret=` not seen).
    No Apps Script change needed. Remove `?secret=` in package 2 once a later report still shows it "not seen yet".
- Report 14:33: stuck sync gone; runs 13:57 OK (11 min), 14:12 FAILED ("read operation timed out" = 45 s urlopen),
  14:27 running. NEW: 98 duplicate-notification pairs on 2026-10-31 (none at 13:50), and synced rows jumped
  1865 -> 2977 in 30 min (task_events 130->353, audit_logs 462->616, sync_events 257->411, notifications 143->212,
  task count unchanged 310). Cause unknown yet; report now breaks duplicates and activity down per task/kind/source.
  Full sync uploads every table in full every 5 min in batches of 20 — it cannot keep up as log tables grow.
  Proposal for package 2 (owner decision): hourly schedule and/or no log tables / incremental upload.
  Owner: the team was working in the system at that time. Package 1 diff touches no task-notification code
  (only `_sync_auth` and the shift lists in workers). Install confirmed -> `main` fast-forwarded (server = 5123848).
  Still read the duplicate breakdown in the next report; normal work (assign then edit) can produce pairs.

- 2026-10-31 (session 3): package `code_split` (commit 6db03f1, branch `claude/new-session-j9yp8h`, base 5123848)
  given to the owner — NOT yet confirmed installed, NOT merged into `main`. Contents:
  * Full sync: `audit_logs`, `task_events`, `sync_events`, `notifications` send only rows with id > last id accepted
    (`sync_full_logs/incremental_state.json`); full upload once a day (first run after 03:00, or `--full`); lock file
    `sync_full_logs/sync.lock` (overlap exits 0, stale > 30 min ignored). `shift_dispatch_log`/`group_dispatch_log`
    stay FULL on purpose: workers UPDATE and DELETE their rows (the session-3 plan wrongly called them append-only).
  * Status tool moved to `tools/check_status/` (ships to the server); installer makes a common-desktop shortcut
    "تقرير حالة BIM" with the Run-as-administrator flag. Report shows sync mode, incremental state and lock.
  * `app/main.py` split into `app/routes_*.py` (see "Code layout"). Dead code removed: 3 unused functions.
  * Tests: `tests/conftest.py` (shared `env`), `tests/test_routes_snapshot.py` + `tests/snapshots/routes.json`,
    `tests/test_full_sync.py`. 30 pass.
  * `?secret=` NOT removed: the report only stores the last time each way was seen, so a Sheet function that still
    uses `?secret=` but did not run between 13:23 and 14:33 would look "not seen". Remove it in the next package
    if the post-install report (a full working day later) still says "not seen yet".

- Report 15:09 (2026-10-31): `code_split` installed 15:05, 8797 lan+workers and 8798 external both on 6db03f1.
  `main` fast-forwarded to fcba200 (= 6db03f1 + docs). First new sync run 15:07 = mode full (no state yet), lock
  present while it runs; old full runs took 11-19 min with 2 timeouts. Next report must show mode=incremental runs.
  `?secret=` still not seen (header seen 15:09). 02:00 off-site backup: not yet due.
  Duplicates EXPLAINED: all 98 pairs = "إلغاء المهمة" to آية نسب, 2026-10-31 14:00-14:29 local; user "اية نسب" archived
  tasks from /manage (347 task_archive audits, 310 tasks exist). DELETE /api/tasks/{tid} was not idempotent and
  sends Telegram inside the request, so repeated clicks re-archived, re-logged and re-notified (3-6x per task).
  Fixed on the branch (already-archived -> no-op, test). Asked the owner whether that mass archive was intended.

- 2026-10-31 (session 3, cont.): owner confirmed the mass archive by آية نسب was on his request. Package
  `supervisor_tools` (commit c94b3e6, base 6db03f1, branch `claude/new-session-j9yp8h`) given — NOT yet confirmed:
  archive no-op fix; daily supervisor Telegram summary (`app/supervisor_summary.py`, LAN worker, default ON at 18:00,
  settings at the end of /shift-center); /team-performance (+ CSV, new permission `reports.team` for admin/deputy/
  supervisor); /my-team mobile page (tasks.edit). Merge into `main` only after the post-install report.

- Report 16:45 (2026-10-31): `supervisor_tools` installed 16:42; 8797 lan+workers, 8798 external, 8799 test all on
  c94b3e6. `main` fast-forwarded to the branch (2b50541 = c94b3e6 + docs + installer shortcut fix, not yet shipped).
  Desktop shortcut was NOT created by either install (owner: no icon) -> installer now saves it under an ASCII name
  and renames it, on public + current desktop; ships with the next package. Until then: tools\check_status\CHECK_STATUS.cmd.
  Sync incremental works: ~875 rows/run instead of ~3140, all SENT, no timeouts, task Ready/0. But a run still takes
  5-6 min (tasks 310 + task_reminders 310 + shift_dispatch_log 109 re-sent each time, ~7 s per 20-row batch), so with
  the lock it effectively runs every 10 min. Proposal (owner to decide): send only rows whose content hash changed.
  `?secret=` still not seen (header 16:44). First supervisor summary due 18:00 — ask tomorrow if it arrived.

- 2026-10-31 evening: owner chose mock-up option 1 (calm, side menu) but with BIGGER and BOLDER text (mock-up updated:
  sizes x1.2, base 17px/500). Package `sync_hash` (commit b14dee6, base c94b3e6) given — NOT yet confirmed: row-hash sync
  for non-log tables, status report shows `unchanged=`, installer shortcut fix. `?secret=` still NOT removed (waiting a
  full working day of reports). After install a run should take seconds and upload only changed rows.

- 2026-10-31 night: owner asked for this to be the LAST step. Package `new_look` (commit 7195f1f, base c94b3e6, so it also
  carries `sync_hash`'s files) given — NOT yet confirmed: `app/static/bim-final.css` loaded last on every page
  (option 1 look, bigger/bolder text, slate side menu). CSS only. The full rebuild of ~42 templates into 8 pages is
  NOT done and not planned: the owner is satisfied with the restyle; do it only if he asks again.

- Report 17:19 (2026-10-31): `sync_hash` installed 17:11 and `new_look` 17:18; 8797 lan+workers, 8798 external, 8799 test
  all on 7195f1f. `main` fast-forwarded. The 17:17 sync run is the first with row hashes (no hashes stored yet -> sends
  everything once, ~6 min, as designed); from the next run `unchanged=` should be ~880 and runs should take seconds.
  `?secret=` still not seen. Still open: 02:00 off-site backup, 18:00 supervisor summary, desktop icon appeared?

- 2026-10-31 night: owner rejected `new_look` ("only colours changed") and asked for a 100% new design keeping every
  button/option. Package `new_design` (commit e5d2668, base 7195f1f) given — NOT yet confirmed: server-rendered shell
  (base.html + app/shell_nav.py + bim-shell.js), one stylesheet `app/static/bim-ds.css`, new admin home/login markup,
  12 old CSS/JS files deleted. Old-vs-new browser comparison (admin/supervisor/employee, 38 pages): nothing missing.

- 2026-10-31 night: owner feedback on `new_design`: menu too wide/dark, wants colourful 3D icons, coloured boxes everywhere,
  tables with column lines and colours, /manage rows shorter with Arabic dates. Package `new_design2` (base 7195f1f, so it
  works whether or not `new_design` was installed) given — NOT yet confirmed. If he sends a picture of the 3D icons he made
  in another conversation, match `.ds-ico` in bim-ds.css to it.

- 2026-11-01: owner saw 3 options as real screenshots (https://claude.ai/artifact/6BjN9L4ZrpbfoZ9snVDPDo) and chose
  MODEL 2 "night". Package `night_design` (commit 1c96ea9, base 7195f1f; contains everything since: shell, bim-ds.css,
  Fluent 3D icons in app/static/icons3d, night block at the end of bim-ds.css) given — NOT yet confirmed.
  Supersedes `new_design`/`new_design2` (never confirmed). Contrast check script idea: walk each visible text node's
  ancestors to the first opaque background and flag ratio < 3 (watch `color(srgb …)` values, they are 0–1).

- 2026-11-01: CORRECTION — the owner's screenshot showed the TILES model (warm page, slim icon rail on the right, solid
  colour blocks); he called it "2" because the preview tabs read right-to-left. Night block removed; tiles block (from
  docs/design/model3_tiles.css + fixes) appended to bim-ds.css; Cairo served locally from fonts/BIMArabic.woff2; BIM logo
  shown WITHOUT any frame (owner). Package `tiles_design` (base 7195f1f) supersedes `night_design` (never confirmed).
  Contrast check on 37 pages: clean.

- 2026-11-01: owner confirmed the tiles design looks right after reopening the browser (stale cache was the cause).
  Chose side menu option A (150px rail, icon above label) + separated /my-team cards. Asset URLs now carry
  `?v={{ asset_v }}` (hash of bim-ds.css, bim-shell.js, bim-ui-v510.js; main.ASSET_V) and the service worker serves
  /static css/js network-first (cache bim-tasks-ds-3). Package `tiles_menu` (base 7195f1f) given — NOT yet confirmed.

- 2026-11-02: owner confirmed the automatic 02:00 off-site backup ARRIVED on Telegram (closed). Supervisor 18:00 summary
  not checked by him yet. `tiles_menu` install not confirmed yet. Owner asked for ONE big update in a NEW conversation:
  see docs/handoff/SESSION_7.md (filter icon, employee profile, merge task/Telegram/log/report pages into tabbed pages,
  notification bell, Kanban view, cleanup). Browser check scripts kept in docs/dev/.

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

## Open items
- DECIDED by the owner (2026-10-31): personal iPhone shortcut keys must work from outside the company, for every role,
  even roles without external login. That is why `iphone_accounts.eligible` has no gateway check — do not add one.
- Done in session 2 (package `fixes_tests`): workers no longer run in the external process, staging copy, group tasks
  in shift messages, shift-test 500, full-sync `pause`, failed-Telegram diagnosis (no internet on 2026-10-29).
- Sheet secret: Apps Script uses the `X-BIM-Sync-Secret` header (seen). Remove `?secret=` once a report after a full
  working day still shows "?secret= ... not seen yet" (was deferred from `code_split`).
- `code_split` installed and merged. Next report: check sync runs are mode=incremental and short; one full after 03:00.
- `supervisor_tools` installed and merged. Ask: did supervisors get the 18:00 summary? any complaints?
- Owner (2026-10-31): small updates ship SEPARATELY from the redesign. `sync_hash` waiting for install + report.
- Design: all look changes go in `app/static/bim-ds.css`; the menu is `app/shell_nav.py` (add new pages there).
- STANDING RULE (owner 2026-11-02): every table column is as wide as its longest cell, never wider (no width:100%
  spreading spare space); only the long text column may wrap. Applies to all current and future tables.
- `new_look` confirmed and merged. Remaining, all optional: drop `?secret=` (still unseen; harmless),
  remove the old stacked CSS layers once nothing needs them, rebuild pages only if the owner asks.
- Redesign mock-ups for approval: https://claude.ai/artifact/CnuJyxwEvGNBDeiTQdDCwY (3 options: 1 calm light + side menu,
  2 dark monitoring room, 3 big cards + top menu; each a manager dashboard + a phone screen; sample data). Waiting for
  the owner's choice. Next: drop `?secret=` (if still unseen) — see `docs/handoff/SESSION_5.md`.
- 02:00 off-site backup: arrived (owner, 2026-11-02).

## Roadmap agreed with the owner (2026-10-31) — session-by-session plan in `docs/handoff/SESSION_<n>.md` — phases 1–2 were done in another conversation, not in this repo
- Owner decision: all functional work first, redesign LAST (session 4). Session 3 = supervisor daily Telegram summary,
  team performance report, functional supervisor page — see `docs/handoff/SESSION_2.md`.
- Phase 3 (now last): rebuild pages in a new design. ~42 templates today → 8 pages: manager dashboard, tasks (list or board),
  task detail, team, recurring, reports, settings, supervisor page (built mobile-first).
  The earlier design is not available and the owner will not fetch it: make a mock-up for approval first.
- Phase 4: split `app/main.py` (~2700 lines) into clear modules, delete dead code (e.g. the stacked CSS layers
  bim-system-v510 / bim-theme-v63 / bim-v81 / premium-ui...), and run the tests automatically before every package
  (`build_update.py` should refuse to build if `pytest` fails).
- Recommended order (told the owner): one update per step, each tried with INSTALL_TO_TEST.cmd first:
  A) the pending small fixes + pytest gate in build_update; B) more tests on the pages/APIs that phase 3 will replace;
  C) split main.py with no behaviour change; D) new pages one or two at a time, old pages kept until the new ones are accepted;
  supervisor mobile page early because it is new, not a replacement.


## Session 7 (2026-11-02)
- Report 18:48: server = 9c2cf82 (`tiles_menu`) on 8797/8798; 8799 test copy not running (copy dated 2026-10-31 —
  refresh it with TEST_COPY_CREATE.cmd before INSTALL_TO_TEST). `main` fast-forwarded to 9c2cf82. `?secret=` never seen.
  Unknown python processes on ports 8080 and 8070 (started 11/01 17:27 and 11/02 09:39) — not ours, ask the owner.
- `big_update`: owner approved the preview (https://claude.ai/artifact/SRC7ewHzffx1xuE6H4apDg). cmp (boss/samer/emp1)
  and contrast clean. Package built: commit 4cde1b7, BASE 9c2cf82, branch `claude/modest-faraday-syi9e5`, no --check
  (new APIs answer 401 like missing routes; the commit check is the proof). NOT confirmed installed, NOT merged.
  Details and next steps: `docs/handoff/SESSION_8.md`.
  Deviations told to the owner: menu 36 -> 29 (only the requested merges); late bell once per task+due (not daily);
  Telegram/logs/reports tabs keep their old addresses (only /tasks-table and /manage redirect).
- 2026-11-03: owner asked why there is no "delete employee" (it existed only on /record-management) and whether a task
  can be archived from inside it (no). Package `delete_archive` (commit 6a5955a, BASE 4cde1b7 = big_update) given — NOT
  confirmed: delete button on /employees rows + profile (dialog: open-task count, optional handover, then reversible
  delete), archive button on /task/{id} (tasks.delete, warns about the Telegram cancel message). install.ps1 now stops
  if app\release.json on the system is not the package's base, so it cannot go on before big_update.
  tests/conftest.py runs the route snapshot first (shared DB). Install order for the owner: big_update, then delete_archive.
- Report 07:51 (2026-11-03): `big_update` installed 06:46 and `delete_archive` 07:51; 8797 lan+workers, 8798 external,
  8799 test all on 6a5955a. `main` fast-forwarded to the branch. `?secret=` refused: none (header seen 07:51). Sync OK
  (seconds per run). Off-site 02:00 backup OK. Owner tested: 4 employees created, 3 record deletes, 5 archives,
  12 reassignments. Port 8080 process gone; port 8070 python (cmd.exe parent) restarted 07:51:10 right after the install
  — not ours, still ask what it is. Test copy data still from 2026-10-31 (code updated there, data not refreshed).
- 2026-11-03: the port-8070 process is a copy of THIS app (check_status lists only `uvicorn` + `app.main:app`), run by the
  global Python313 under a cmd.exe, restarted 9 s after the install. Owner does not know it. Given `BIM_Check_Ports.zip`
  (tools/check_ports, read-only): parent chain, release-health of the extra port, scheduled tasks/startup/services,
  folders with app\main.py and which .env keys are set. Risk to check: with a bot token and no BIM_GATEWAY it runs the
  workers (duplicate Telegram reminders). Do NOT stop it before the report shows what it is.
- CORRECTION 2026-11-03 (check_ports report 07:56): 8070 is NOT ours — `C:\BIM_Hall_Shortages` ("hall-shortages" 1.2.0,
  task `\BIM Damascus\HallShortages`, loop in run_server.ps1, no .env/db). 8080 was `C:\نواقص الموردين` (task
  `\BIM Damascus\Server`, now Ready = stopped). Both are uvicorn `app.main:app`, so OUR installer's Stop-Bim stopped them on
  every install; 8070 restarted itself, 8080 stayed down after big_update. Fixed on the branch (match --port 8797|8798 /
  8799 only; check_status lists only our ports) — ships with the next package. Owner given RESTART_8080.cmd
  (schtasks /Run "\BIM Damascus\Server"), to use only if that system is still used.
  Other systems on this server (leave alone): Athar_Yawmi, BIM Damascus Shops, BIM Rent LAN HTTPS 8093, BIM ShamCash;
  python also listens on 8787/8788/8843/8092. Never stop python processes by name or by app.main alone.
- 2026-11-03: owner asked how task messages look on Telegram. Captured the real texts (run the app on demo data with
  low_level_send/edit_message/answer_callback patched; script idea in this session) and showed them:
  https://claude.ai/artifact/Ho4Xz3TBwSZ7u2Z1Y2XKCy. Issues found, NOT fixed yet (owner to decide): raw ISO dates
  ("2026-11-05T14:00") in `_telegram_task_text`, edit notice and my-tasks list; double header ("مهمة من المدير" prefix from
  delegation.telegram_prefix + "📌 مهمة جديدة"); technical "وقت الإرسال/التحديث" line; typo "كت مهمة" in routes_tasks.py
  reassign notice; "▶️ بدأت" button still shown after start; after "تم" the same message is edited twice.
- 2026-11-03: owner saw the new Telegram look (dist/telegram_new_look.png, commit 28a345f, not shipped, no "تمام" yet).
  New request: a FOLLOW-UP GROUP on Telegram that sees everything the manager/employee sees (esp. "تم", so nobody
  reminds a finished task), members can remind but not change status. Started in `app/followup.py` (WIP, not wired).
  Owner ended the session here — everything to do next is in `docs/handoff/SESSION_9.md`.

## Session 9 (2026-11-03)
- Branch `claude/zealous-lamport-x69beu` = `claude/modest-faraday-syi9e5` + the follow-up group. Server still 6a5955a.
- Follow-up group DONE on the branch (`app/followup.py`, tests/test_followup.py, settings box at the top of
  /telegram-groups, `/api/followup`). One scan per 30 s in the LAN worker over tasks, task_events, task_comments
  (page comments only; Telegram comments are the note/caption), task_attachments (file itself sent) and notifications
  (reminder kinds only), cursor `followup.cursor`. Group card per task: outbox types `followup_card`/`followup_edit`/
  `followup_file`, rendered at send time, message ids in table `followup_messages`, edited on every status/edit/
  reassign/archive/restore/late. «ذكّر الموظف» only on the card and the late message (event messages: link only).
  New task_events: `restored` (restore_task). `edited` notes now carry `_old` values.
- Preview for the owner: dist/followup_group_preview.png (capture script idea in SESSION_9 "capture"; dist/ is gitignored).
- Owner: «عجبتني الصورة» (= تمام). 8080 (نواقص الموردين) works again. 18:00 supervisor summary: he does not know yet.
  He asked to choose in the settings who may act: added `remind_ids` (only those employees, matched by Telegram user id
  = employees.telegram_chat_id, may press «ذكّر»; empty = everyone in the group). Group MEMBERSHIP itself is managed in
  Telegram (bots cannot list or add members).
- Package `followup_group` built: commit bcc6ed2, BASE 6a5955a, branch `claude/zealous-lamport-x69beu`, 10 files
  (6d1a6a0 port-only installer + 28a345f Telegram texts + follow-up group). NOT confirmed installed, NOT merged.
- Owner installed `followup_group` and linked a group (he saw the morning list there; no status report yet).
  Feedback: all tasks came in ONE message, nobody can act on one task. Package `followup_tasks` (commit e98e2a8, BASE
  bcc6ed2, 4 files) given — NOT confirmed: morning list = header + one message per employee with a button per task
  (`fu:open:ID` posts the card now); Reply to any group message about a task = comment on it (table `followup_refs`,
  saved as task_comments user_id NULL + event 'comment' source telegram_group, sent to the employee; only `remind_ids`
  people if set); button «📋 أرسل قائمة المهام الآن» (POST /api/followup/digest-now). After install: status report,
  then merge into `main` (server = e98e2a8).
- Report 06:35 (2026-11-04): `followup_tasks` installed 06:35; 8797 lan+workers, 8798 external, 8799 test all on
  e98e2a8. `main` fast-forwarded to the branch. Sync incremental OK (seconds, unchanged=946). Off-site 02:00 backup OK.
  `?secret=` refused: none. Duplicates: none. Test copy data still from 2026-10-31 (TEST_COPY_CREATE.cmd never re-run).
  Still ask: does the per-task morning list / Reply-comment work in the real group? 18:00 supervisor summary arrived?
- 2026-11-04: owner asked for livelier, clearer group messages. Group texts are now Telegram HTML (bold, emojis, ━ separators,
  status/priority icons; `followup.h()` escapes user text, `send_html` falls back to plain if Telegram refuses the HTML;
  file captions stay plain). Preview dist/followup_lively_preview.png. Package `followup_lively` (commit d4d84ab, BASE
  e98e2a8, 3 files) given — NOT confirmed. Font size cannot be changed on Telegram (only bold/italic).
- 2026-11-04: owner: dashboard looks "like a kids' app", wants a professional one that uses the space. Redesigned `/`
  (command_center.html + scoped `#dx` block at the end of bim-ds.css; /api/v2/dashboard gained due_today, unassigned,
  unseen, done_week, mix, trend, team done7). Every old action kept (quick add/Ctrl K, period, mark done, team filter,
  profile links, activity); the separate "quick filter by employee" card became an employee select + clickable team rows.
  Before/after: dist/dashboard_old.png / dashboard_new.png (demo data). Package `dashboard_pro` (commit e4f1485, BASE
  d4d84ab = followup_lively, so lively must be installed first) given — NOT confirmed.
- 2026-11-04: owner wanted fewer reminder options covering everything ("every 10 days"). Agreed 3 options: مرة واحدة /
  يتكرر (every N ساعة|يوم|أسبوع|شهر from a start time; hours only in working time) / قبل الموعد (30 min..2 days).
  `app/reminders.py` + `bim-reminder.js` (new-task window, 🔔 window in the quick list and on the task page). On first start
  `reminders.migrate()` rebuilds task_reminders (old CHECK removed, every_n/every_unit/before_minutes added, old rows
  converted, last_sent_at -> local time). Rolling back to old code keeps working but old code ignores the new types.
  Package `reminders3` (commit 7da6b1d, BASE e4f1485 = dashboard_pro) given — NOT confirmed. Install order:
  followup_lively -> dashboard_pro -> reminders3.
- Session 9 ended 2026-11-04 at the owner's request. Next: `docs/handoff/SESSION_10.md` (3 packages waiting for install,
  in order followup_lively -> dashboard_pro -> reminders3; then status report and fast-forward `main`).

## Session 10 (2026-11-04)
- Owner says `followup_lively`, `dashboard_pro`, `reminders3` are installed (NO status report yet -> `main` not moved).
- Owner asked how to remind an employee again by WhatsApp/Telegram. Before: Telegram «🔔 تذكير» only on /my-team and the
  employee profile, the follow-up group button, scheduled reminders; WhatsApp only through «توزيع المهمة» (logged as a
  delegation). Package `remind_now` (commit 5adabad, BASE 7da6b1d, 5 files) given — NOT confirmed: task page buttons
  «🔔 ذكّر الآن (تلغرام)» / «💬 ذكّر على الواتس», POST /api/tasks/{tid}/remind (tasks.edit + team scope, Telegram
  at most once a minute per task, WhatsApp = wa.me link, the manager presses send), task_event `reminded` + audit
  `task_remind`, follow-up group shows it (kind `reminders`). After the report shows 5adabad on all ports: fast-forward `main`.
- Report 20:29 (2026-11-10): all 19 packages done; 8797 lan+workers and 8798 external on 5adabad (`remind_now`); 8799 test
  copy not running (data still 2026-10-31). `main` fast-forwarded to df21ef6 (= 5adabad + docs). Sync incremental OK,
  off-site 02:00 OK (one failure 11-06), `?secret=` refused: none, duplicates: none.
  FOUND: the follow-up group was upgraded to a SUPERGROUP; its chat id changed and every group message since 2026-11-10
  06:00 failed (119 failed rows, 400 + migrate_to_chat_id). Those failed rows are NOT retried (stale).
- Owner: «ذكّر» said «غير مربوط» for an employee shown as «✅ مربوط» — cause confirmed by him: «إرسال Telegram» was off.
- Package `telegram_fixes` (commit 052c218, BASE 5adabad, 8 files) given — NOT confirmed: `main.telegram_block_reason()`
  names the real cause (not linked / sending off / channel web-only / notice kind off) on both reminder routes; list and
  profile show «مربوط · الإرسال موقوف»; finishing a Telegram link turns sending on; `telegram_service._api` follows
  `migrate_to_chat_id` (moves telegram_groups + pending outbox, audit `telegram_group_migrated`, resends once, not edits);
  webhook handles the migrate service message too.
- Owner unhappy with the dashboard again ("kids' game") and says there are too many pages (admin menu = 30 entries).
  Advice given: do NOT redesign blindly (8th look change); first a short menu (6 daily pages + «⚙️ الإعدادات» page
  holding the rest, nothing deleted; employee sees مهامي only), then the dashboard from a screenshot + 3 concrete
  complaints, approved as a picture before code. Waiting for his answer.
- Owner: «سوّيه انت» (no screenshot, no list). Dashboard v3 "sober" committed (38a607d, NOT shipped): see commit message.
  Finding: the "kids' game" feel comes mostly from the SHELL (3D Fluent icons in the rail and top bar, peach active item,
  beige top bar), which he himself asked for on 2026-10-31/11-01. Shown 3 pictures: current / A = v3 only /
  B = v3 + plain line icons (shell_nav.ICONS paths already exist) + white rail, dark active item (B was a scratch-copy
  preview of base.html only, not committed). Waiting for A or B. Package must use BASE 052c218 (telegram_fixes) once
  that one is confirmed installed.
- Owner chose A and says `telegram_fixes` is installed (no status report yet -> `main` stays df21ef6 = 5adabad).
  Package `dashboard_v3` (commit 0b7aca0, BASE 052c218, 2 files: bim-ds.css + command_center.html) given — NOT
  confirmed. After the report shows 0b7aca0 on 8797/8798: fast-forward `main` to the branch.
- Report 20:45 (2026-11-10): `telegram_fixes` 20:38 and `dashboard_v3` 20:44 installed; 8797 lan+workers and 8798
  external on 0b7aca0. `main` fast-forwarded to the branch. Follow-up group migration not yet proven (no group send
  since the install; 8 rows "retrying" may be old-chat rows that will migrate on their next try) — next report: no new
  "upgraded to a supergroup" failures. Sync: 20:32 Google-side error + 20:37 timeout, 20:42 OK (transient). Minor:
  scheduled.log shows "WARN: ERROR: 'charmap' codec can't encode" (Arabic error text printed on cp1252) — cosmetic, not fixed.
