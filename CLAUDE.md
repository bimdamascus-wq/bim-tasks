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
- Status tool: `deploy/check_status/` (zip it, owner double-clicks `CHECK_STATUS.cmd`, pastes the report). Read-only, no secrets.
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
  `main` NOT merged yet: wait until the duplicate burst is explained (could it be from package 1?).

## Open items (agreed with the owner, not started)
1. (in `gateway_staging`) external process ran the workers too. 2. (in `gateway_staging`) staging copy.
- DECIDED by the owner (2026-10-31): personal iPhone shortcut keys must work from outside the company, for every role,
  even roles without external login. That is why `iphone_accounts.eligible` has no gateway check — do not add one.
3. Google Sheet sync sends its secret in the URL query (`?secret=`); move to the `X-BIM-Sync-Secret` header (needs the Apps Script changed too).
4. Feature ideas offered, owner has not chosen yet: supervisor dashboard for their team, daily Telegram summary for supervisors,
   per-team performance report. Ask the owner what slows down their daily work before building anything.
5. Shift-start Telegram message (`workers._send_open_tasks`) lists only tasks where `employee_id` = the employee; group tasks
   they take part in are missing, while the shift-test button and the "my tasks" command include them. DECIDED: include them.
6. 2 failed rows in `telegram_outbox` (status report 2026-10-31) — check which chat/why.

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

