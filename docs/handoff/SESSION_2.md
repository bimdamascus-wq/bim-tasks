# BIM Tasks — تسليم للمحادثة رقم 2

**للمالك:** افتح محادثة جديدة، اختار المستودع `bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب بس:
> اتبع الملف المرفق.

ما عليك تشرح شي. كل يلي بتحتاجه المحادثة الجديدة مكتوب هون وبـ CLAUDE.md.

---

## Instructions for Claude (session 2)

You continue work on BIM Tasks. The owner is not technical, speaks Syrian Arabic, and wants to be told exactly what
to click. Do not ask them to look things up in other conversations. Decide what you can from this file and the code.

### Start
1. Branch: work on **`claude/wonderful-edison-kzadbl`** (or the branch the session gives you, created from it).
   It contains two fixes not yet on the server or in `main`. `main` = what the server runs (commit 22e9f34).
2. Read `CLAUDE.md` fully. It has the server layout, the update/installer rules, the staging copy and the decisions.
3. `pip install -r requirements.txt pytest httpx`, then `python -m pytest tests -q` (21 pass at handoff).

### Scope of this session: cleanup and safety, NO visual changes
Deliver **two update packages**, one after the other. Give the owner package 2 only after they confirm package 1.
Each package: built with `deploy/build_update.py`, owner runs `INSTALL_TO_TEST.cmd` first, then `INSTALL_UPDATE.cmd`,
then `CHECK_STATUS.cmd` (zip `deploy/check_status/` for them) and pastes the report. Merge into `main` only after that.

**Package 1 — `fixes_tests` (small, behaviour fixes):**
- Already done on the branch: `/api/shift-test` 500 fix; `full_sync/RUN_FULL_SYNC.cmd` trailing `pause` removed.
- Shift-start Telegram message (`workers._send_open_tasks`): include group tasks the employee takes part in
  (`task_participants`, status not done), same query as `_tg_send_my_tasks`. **Decided** (owner delegated the choice):
  include them, so the test button, the "my tasks" command and the real message agree. Check the end-of-shift count too.
- `deploy/build_update.py`: run `python -m pytest tests -q` first and refuse to build if it fails.
- `deploy/check_status/check_status.py`: for `telegram_outbox` rows with status `failed`, print created time, source_type
  and `last_error` (no message text, mask chat ids except last 3 digits). Then read the owner's report and fix the cause
  if it is in our code (2 failed rows were seen on 2026-10-31).
- Google Sheet secret (open item 3): the server already accepts the `X-BIM-Sync-Secret` header. Find in the repo (or
  ask the owner to paste) how the Apps Script calls the server. If a change is needed in Apps Script, give the owner
  the exact lines and where to paste them, step by step. Keep `?secret=` working until they confirm; remove it in package 2.
- After install, the status report must show `BIM_Tasks_Full_Google_Sheets_Sync` as Ready with Last Result 0 between runs.

**Package 2 — `code_split` (phase 4, no behaviour change):**
- First add tests that pin current behaviour of the main pages/APIs that phase 3 will replace (each page route returns
  200 for an allowed role, 403/redirect for others; main JSON APIs keep their keys).
- Split `app/main.py` (~2700 lines) into modules by area (auth/session, tasks, employees, telegram, sync, backup, pages).
  Keep `app.main:app` as the entry point and keep names other modules import from `main` (`m.need`, `m.GATEWAY`, ...
  — grep `m\.` across `app/`). No route path, response or permission may change. Tests must pass unchanged.
- Delete dead code only with proof: templates no route renders, static files no template/JS references, routes nothing
  calls (check templates, static JS, `full_sync/`, tools). List what you deleted in the commit message.
- Remove `?secret=` support from `_sync_auth` if the owner confirmed the Apps Script change.

### Ask the owner (short questions, at the start)
- Did the automatic 02:00 off-site backup arrive on Telegram? (Only a manual one was confirmed.)
- Anything that broke or annoys them since the last update.

### Do not
- Change the look of any page (that is session 3).
- Add a gateway check to `iphone_accounts.eligible` (owner decided shortcuts work from outside).
- Push to `main` before the owner confirms the install with a status report.

### End of the session (mandatory)
Write `docs/handoff/SESSION_3.md` in the same format as this file: short Arabic part for the owner, then instructions.
Base it on the plan below, updated with what actually happened. Update `CLAUDE.md` (deployment log, open items).
Commit, push, and send the file to the owner with the line: "ارفق هالملف بالمحادثة الجاية واكتب: اتبع الملف المرفق".

---

## Plan for session 3 (draft — session 2 updates it)
Phase 3, part 1. The new design from the owner's earlier conversation is NOT available and the owner will not fetch it.
So session 3 makes the design itself:
1. Publish one clickable mock-up (Artifact) of the 8 pages: manager dashboard, tasks (list / board), task detail, team,
   recurring, reports, settings, supervisor page (mobile-first). Arabic RTL, keep BIM brand (red, logo in `app/static`).
   Base page contents on what the current 42 templates actually do, so nothing the team uses gets lost.
   Ask the owner to approve or comment before writing real pages.
2. Build the **supervisor mobile page** and the **manager dashboard** first, as new pages next to the old ones.
   One package, staging first.

## Plan for session 4 (draft)
Remaining new pages (tasks list/board, task detail, team, recurring, reports, settings), one package per 2–3 pages.
Old pages stay reachable until the owner accepts the new ones; then remove them and their CSS layers in a last package.
Feature ideas still open (owner has not chosen): daily Telegram summary for supervisors, per-team performance report —
the reports page and supervisor page may cover them; ask before building separately.
