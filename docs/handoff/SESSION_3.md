# BIM Tasks — تسليم للمحادثة رقم 3

**للمالك:** افتح محادثة جديدة، اختار المستودع `bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب بس:
> اتبع الملف المرفق.

ما عليك تشرح شي. كل يلي بتحتاجه المحادثة الجديدة مكتوب هون وبـ CLAUDE.md.
وإذا وصلتك نسخة الساعة 2 الاحتياطية على Telegram، قول هالشي بأول رسالة.

---

## Instructions for Claude (session 3)

You continue work on BIM Tasks. The owner is not technical, speaks Syrian Arabic, and wants to be told exactly what
to click (which file, which folder, right click → Run as administrator). Always say WHERE a file is.
Do not ask them to look things up in other conversations. Decide what you can from this file and the code.

### State at handoff (2026-10-31, end of session 2)
- Server runs commit **5123848** (package `fixes_tests`, installed 13:23, confirmed by status report).
  `main` = server code + tooling/docs (a67da42 or later). Branch for new work: create it from `main`
  (or use the branch the session gives you, rebased on `main`).
- Tests: `pip install -r requirements.txt pytest httpx`, `python -m pytest tests -q` → 23 pass.
- `build_update.py` now refuses to build if the tests fail.
- Status tool: `deploy/check_status/` (zip with folder `BIM_Check_Status/`). Owner: extract, right click
  `CHECK_STATUS.cmd` → Run as administrator, paste the Notepad report. It now also shows: failed Telegram rows,
  how the Sheet sends its secret, full-sync processes and last runs, duplicate notifications with details,
  last-24h activity per table. Output is forced to UTF-8 (Arabic names crashed it on cp1252).
- One-off tool `deploy/fix_sheet_sync/` (kills full-sync processes older than 20 min) was used once; worked.

### What session 2 found (do not redo)
- Failed Telegram rows #6/#7 (2026-10-29): server had no internet/DNS. Not a code fault.
- The Apps Script already sends `X-BIM-Sync-Secret`; `?secret=` was never seen (watched since 13:23). No Apps Script change.
- Full sync (`full_sync/sync_all.py`, every 5 min) re-uploads EVERY table in batches of 20. Runs take 4–13 min,
  one failed with "read operation timed out" (45 s). Synced rows grew 1738 → 2977 in a day (log tables).
  0x800710E0 in the task's Last Result = "a run was refused because one is still running" — expected with overlap.
- 98 duplicate-notification pairs on 2026-10-31 13:50–14:33 while the team was working. Package 1 touched no
  notification code. Breakdown not read yet → read it in the first status report of this session.

### Scope of this session: package `code_split` (agreed with the owner: "تمام")
Same flow: build with `deploy/build_update.py`, owner runs `INSTALL_TO_TEST.cmd`, then `INSTALL_UPDATE.cmd`, then the
status report. Merge into `main` only after that. Tell the owner to open a few pages and press a few buttons on the
test copy (127.0.0.1:8799) before the real install: this package touches everything inside.

1. **Full sync faster (only owner-visible change; the Sheet must end up with the same data).**
   The Apps Script upserts by (table, source key), so sending fewer rows is safe. Append-only log tables
   (`audit_logs`, `task_events`, `notifications`, `sync_events`, `shift_dispatch_log`, `group_dispatch_log`):
   send only rows with id > last id sent OK (state file under `sync_full_logs/`, written only after a table succeeds).
   Other tables: send in full as today (they are small). Once a day (first run after 03:00) send everything in full.
   Keep the probe and protocol unchanged. Add a lock file so overlapping runs exit at once with code 0
   (stale lock > 30 min is ignored). Tests with a fake receiver.
2. **Status tool in a fixed place:** ship `deploy/check_status/*` inside the package to `C:\BIM_Tasks_Server\tools\check_status\`
   and have the installer create a desktop shortcut "تقرير حالة BIM" (runs elevated, or tell the owner right click →
   Run as administrator). Keep it read-only.
3. **Tests first, then split.** Add tests that pin current behaviour: every page route returns 200 for an allowed role
   and 403/redirect for others; main JSON APIs keep their keys. Move the `env` fixture to `tests/conftest.py`.
4. **Split `app/main.py`** by area (auth/session, tasks, employees, telegram, sync, backup, pages). Keep `app.main:app`
   as entry point and every name other modules import from `main` (`grep -rn "m\.\|from .main\|main\." app/`).
   No route path, response or permission may change. Tests pass unchanged.
5. **Dead code** only with proof (no route renders the template / no template or JS references the static file /
   nothing calls the route — check templates, static JS, `full_sync/`, `tools/`). List deletions in the commit message.
   Candidates: stacked CSS layers bim-system-v510 / bim-theme-v63 / bim-v81 / premium-ui.
6. **Remove `?secret=`** from `_sync_auth` (and `_note_sync_auth`'s query branch) if the first status report still
   shows "?secret= ... not seen yet".

### Do not
- Change the look of any page (redesign is the last phase).
- Add a gateway check to `iphone_accounts.eligible` (owner decided shortcuts work from outside).
- Push to `main` before the owner confirms the install with a status report.

### End of the session (mandatory)
Write `docs/handoff/SESSION_4.md` in the same format: short Arabic part for the owner, then instructions, based on the
plan below updated with what happened. Update `CLAUDE.md` (deployment log, open items). Commit, push, and send the file
to the owner with the line: "ارفق هالملف بالمحادثة الجاية واكتب: اتبع الملف المرفق".

---

## Plan for the next session (draft — this session updates it)
Owner decision (2026-10-31): finish all functional work first; the visual redesign comes last.
New screens use the current look (`base.html`, existing CSS). Confirm in one line at the start:
1. **Daily Telegram summary for each supervisor** ("المسؤول"): at a set hour (setting, default end of day), per team
   member: done today, overdue, open, not yet viewed. LAN worker only, `supervisor_team` scope from `access.py`,
   idempotent per day (like `shift_dispatch_log`), on/off per supervisor. Admin/deputy: same for everyone, optional.
2. **Team performance report**: per team and per employee for a chosen period — created, completed, completed late,
   overdue now, average time to complete. JSON API + one simple page + Excel/CSV export. Supervisors see only their team.
3. **Supervisor page (functional, mobile friendly, current style)**: their team's open/overdue tasks with quick actions
   (reassign, change status, remind on Telegram). Reuse existing APIs and permission checks.

## After that (draft)
Visual redesign of the whole system: ~42 templates → ~8 pages (manager dashboard, tasks list/board, task detail, team,
recurring, reports, settings, supervisor page mobile-first), remove the stacked CSS layers. Start with a clickable
mock-up (Artifact) for the owner to approve; build new pages next to the old ones; remove old ones only after acceptance.
