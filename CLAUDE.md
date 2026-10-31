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

## Open items (agreed with the owner, not started)
1. `RUN_EXTERNAL.cmd` never sets `BIM_GATEWAY=external` / `BIM_COOKIE_SECURE=1`, so the external process also starts
   the workers (reminders, Telegram queue, backups run twice → possible duplicate messages). External detection itself works
   through Cloudflare headers. Fix: set both variables in `RUN_EXTERNAL.cmd` (and `BIM_GATEWAY=lan` in `RUN_LAN.cmd`).
2. Staging copy on the server (e.g. `C:\BIM_Tasks_Test`, port 8799, copy of data) created by a double-click file, to try updates first.
3. Google Sheet sync sends its secret in the URL query (`?secret=`); move to the `X-BIM-Sync-Secret` header (needs the Apps Script changed too).
4. Feature ideas offered, owner has not chosen yet: supervisor dashboard for their team, daily Telegram summary for supervisors,
   per-team performance report. Ask the owner what slows down their daily work before building anything.

