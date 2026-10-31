# BIM Tasks — تسليم للمحادثة رقم 4

**للمالك:** افتح محادثة جديدة، اختار المستودع `bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب بس:
> اتبع الملف المرفق.

إذا ما كنت نزّلت تحديث `code_split` لسا، قول هالشي بأول رسالة.
وإذا نزّلته: كبسة يمين على أيقونة **تقرير حالة BIM** على سطح المكتب ← Run as administrator، وانسخ التقرير بأول رسالة.
وقول كمان إذا وصلتك نسخة الساعة 2 الاحتياطية على Telegram.

---

## Instructions for Claude (session 4)

You continue work on BIM Tasks. The owner is not technical, speaks Syrian Arabic, and wants to be told exactly what
to click (which file, which folder, right click → Run as administrator). Always say WHERE a file is.
Read `CLAUDE.md` first (deployment log, code layout, open items).

### State at handoff (end of session 3, 2026-10-31)
- Server runs **5123848** (`fixes_tests`) unless the owner says `code_split` is installed.
- Package **`code_split`** (commit 6db03f1, branch `claude/new-session-j9yp8h`) was given to the owner:
  incremental full sync + lock, status tool in `C:\BIM_Tasks_Server\tools\check_status\` with desktop shortcut
  "تقرير حالة BIM", `main.py` split into `app/routes_*.py`, 3 dead functions removed, more tests (30 pass).
- `main` = 6f8f458 (server code 5123848 + docs). Do NOT merge the branch before the owner confirms the install.

### First thing to do
1. If `code_split` is not installed yet: walk the owner through it (see "How to install" below), then the report.
2. With the post-install report:
   - ports 8797 `lan` and 8798 `external` on commit 6db03f1 → fast-forward `main` to the branch, note it in CLAUDE.md.
   - `[google sheet full sync]`: after the first (full) run, runs should say `mode=incremental`, upload few rows and
     finish in well under 5 minutes; one `mode=full` per day after 03:00. Lock file should not stay for > 30 min.
   - `?secret=` line still "not seen yet" after a full working day → remove the `?secret=` branch from `_sync_auth`
     and the `query` branch of `_note_sync_auth` (app/main.py), adjust `test_sync_auth_records_header_or_query`
     (query must now get 401). Ship it in the next package.
   - Read the duplicate-notification breakdown (98 pairs on 2026-10-31 13:50–14:33 were never explained). Pairs of
     different `kind` on the same task within 2 min after assign+edit can be normal; same kind twice is a bug.
3. Ask whether the automatic 02:00 off-site backup arrived on Telegram (first run 2026-11-01).

### How to install (for the owner)
- Download `BIM_Update_code_split.zip`, copy it to the server, right click → Extract All.
- In the extracted folder: right click `INSTALL_TO_TEST.cmd` → Run as administrator (test copy, 127.0.0.1:8799).
  Then open http://127.0.0.1:8799 on the server, log in, open a few pages (tasks, employees, Telegram center,
  reports, settings) and press a few buttons. This package touched every route file.
- If OK: right click `INSTALL_UPDATE.cmd` → Run as administrator. Wait for "تم التحديث بنجاح".
- Then the desktop icon "تقرير حالة BIM" → right click → Run as administrator → paste the Notepad text.

### Scope of session 4: features (owner decision: functional work first, redesign last)
New screens use the current look (`base.html`, existing CSS). Confirm the order in one line at the start, then:
1. **Daily Telegram summary for each supervisor** ("المسؤول"): at a set hour (setting, default end of day), per team
   member: done today, overdue, open, not yet viewed. LAN worker only (`app/workers.py`), `supervisor_team` scope from
   `access.py`, idempotent per day (a dispatch-log row per supervisor+date, like `shift_dispatch_log`), on/off per
   supervisor. Admin/deputy: same for everyone, optional. Tests with the fake Telegram sender in `tests/conftest.py`.
2. **Team performance report**: per team and per employee for a chosen period — created, completed, completed late,
   overdue now, average time to complete. JSON API + one simple page + CSV export. Supervisors see only their team.
   Add the page to `access.PAGE_PERMS`, the route in a `routes_*.py` module, and regenerate the route snapshot
   on purpose (`BIM_UPDATE_SNAPSHOT=1`), saying so in the commit.
3. **Supervisor page (functional, mobile friendly, current style)**: their team's open/overdue tasks with quick actions
   (reassign, change status, remind on Telegram). Reuse existing APIs and permission checks.
Ship as one package (`supervisor_tools`) or two; same flow: build with `deploy/build_update.py`, test copy first.

### Do not
- Change the look of existing pages (redesign is the last phase).
- Add a gateway check to `iphone_accounts.eligible` (owner decided shortcuts work from outside).
- Push to `main` before the owner confirms an install with a status report.
- Treat `shift_dispatch_log` / `group_dispatch_log` as append-only (workers update and delete their rows).

### End of the session (mandatory)
Write `docs/handoff/SESSION_5.md` in the same format: short Arabic part for the owner, then instructions, based on the
plan below updated with what happened. Update `CLAUDE.md` (deployment log, open items). Commit, push, and send the file
to the owner with the line: "ارفق هالملف بالمحادثة الجاية واكتب: اتبع الملف المرفق".

---

## After that (draft)
Visual redesign of the whole system: ~42 templates → ~8 pages (manager dashboard, tasks list/board, task detail, team,
recurring, reports, settings, supervisor page mobile-first), remove the stacked CSS layers (bim-system-v510,
bim-theme-v63, bim-v81-center-ar, bim-premium-ui — all still loaded by base.html/login pages today, so they are not
dead code yet). Start with a clickable mock-up (Artifact) for the owner to approve; build new pages next to the old
ones; remove old ones only after acceptance.
