# BIM Tasks — تسليم للمحادثة رقم 8

**للمالك:** افتح محادثة جديدة، اختار المستودع `bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب بس:
> اتبع الملف المرفق.

بأول رسالة قول:
- نزّلت التحديث الشامل (`BIM_Update_big_update.zip`) ولا لأ؟ وبعده التحديث الصغير (`BIM_Update_delete_archive.zip`)؟
- شغّل "تقرير حالة BIM" (من سطح المكتب، أو `C:\BIM_Tasks_Server\tools\check_status\CHECK_STATUS.cmd`، كبسة يمين ← Run as administrator) والصق التقرير.
- شو هنن البرنامجين يلي شغّالين عالبورتين 8080 و8070؟
- وصل المسؤولين الملخص اليومي الساعة 6 المسا؟
- في شي بالصفحات الجديدة مو عاجبك (الجرس، ملف الموظف، لوحة المهام، التبويبات)؟

---

## Instructions for Claude (session 8)

The owner is not technical, speaks Syrian Arabic, is tired of long sessions: short answers, exact clicks, no jargon.
Read `CLAUDE.md` first (deployment log, code layout, design notes). Browser check scripts: `docs/dev/`.

### State at handoff (2026-11-02, evening)
- Server: 9c2cf82 (`tiles_menu`) confirmed by report 18:48. `main` = 9c2cf82.
- Package `big_update` built and given: commit 4cde1b7 (+ docs after it), branch `claude/modest-faraday-syi9e5`,
  BASE 9c2cf82. NOT confirmed installed, NOT merged into `main`. No `--check` route: every new API answers 401
  without a session, same as a missing route on the old code, so `--check` proves nothing; the installer's
  release.json commit check on each port is the proof.
- Owner approved the preview (https://claude.ai/artifact/SRC7ewHzffx1xuE6H4apDg) with "تمام".
- Test copy (8799) was not running and dates from 2026-10-31: told the owner to run TEST_COPY_CREATE.cmd before
  INSTALL_TO_TEST.cmd.

### Second package `delete_archive` (2026-11-03, owner asked)
- Commit 6a5955a, BASE 4cde1b7 (must go on AFTER big_update; install.ps1 now refuses a wrong base using
  app\release.json). Delete-employee button (employees rows + profile, `bimDeleteEmployee` in bim-shell.js: open-task
  count, optional handover, then DELETE /api/employees/{id}); archive button inside /task/{id} (tasks.delete).
- After both are installed and the report shows 6a5955a on 8797/8798: fast-forward `main` to the branch.

### What `big_update` contains (see the commit message of 50acce9)
1. Thin column-filter icon (orange only when a filter is active).
2. `/employee/{id}` profile (`app/employee_profile.py`, template `employee_profile.html`); names link to it everywhere.
3. Merged pages: `/tasks?view=cards|table|quick|board` (`routes_pages.TASK_VIEWS`); `/tasks-table` and `/manage`
   redirect (303, query kept). Telegram (4 tabs), logs (2), reports (2) keep their own addresses and share the tab bar
   (`shell_nav.TABS`, `MERGED`, rendered in `base.html`). Menu 36 → 29. `command_center.PAGES` follows.
4. Bell: `app/inbox.py`, table `inbox_items`. `scan()` reads new task_events (status → تم), task_comments,
   task_attachments, tasks (source telegram*/iphone*) and late tasks (due within the last 48 h, once per task+due_at).
   Runs from the LAN worker and before `/api/inbox` is read (throttled to 15 s). First run starts from "now".
   Supervisor = own team (can_view_task at write and read time). The actor of a page change comes from audit_logs
   (±5 s); the actor is never notified.
5. Kanban view `tasks_board.html` (drag → `PATCH /api/tasks/{id}/status`, select on phones/touch).
6. Standing rule: table columns fit content (`bim-ds.css` big_update block + `fitTables` in `bim-shell.js`; long-text
   columns get `.ds-col-wrap` by header word or > 48 chars).
7. `?secret=` refused; the status report shows `sync.auth_last_query_refused` if a Sheet function still tries it.
   `app/templates/dashboard.html` deleted (installer deletes it).
- All css/js URLs now carry `?v={{ asset_v }}` (hash of every static css/js).

### After the post-install report
- Check 8797/8798 on the new commit (4cde1b7 after big_update, 6a5955a after delete_archive), workers on 8797, `?secret= REFUSED ... none - good`.
- If OK: fast-forward `main` to the branch, record it in CLAUDE.md.
- If the owner reports a problem: fix on the branch, build a small package with BASE = the installed commit.

### Known, not done (tell the owner only if he asks)
- On the admin home and elsewhere the avatar letter "و" renders like a Latin "g" in the BIM Arabic font
  (pre-existing, also on 9c2cf82). Likely the woff2 subset; check before changing fonts.
- Bell items are not in the Google Sheet sync (not in `full_sync/sync_all.py` ALLOW) — on purpose.

### Do not
- Add a gateway check to `iphone_accounts.eligible`.
- Push to `main` before the owner confirms an install with a status report.
- Treat `shift_dispatch_log` / `group_dispatch_log` as append-only.
- Downscale preview screenshots or show options without the login page if the design changes it.
- Drop any button, field or option; table columns must keep fitting their content (standing rule).

### End of the session (mandatory)
Write `docs/handoff/SESSION_9.md` in the same format, update `CLAUDE.md`, commit, push, and send the file to the owner
with the line: "ارفق هالملف بالمحادثة الجاية واكتب: اتبع الملف المرفق".
