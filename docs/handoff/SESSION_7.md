# BIM Tasks — تسليم للمحادثة رقم 7

**للمالك:** افتح محادثة جديدة، اختار المستودع `bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب بس:
> اتبع الملف المرفق.

بأول رسالة قول:
- نزّلت آخر تحديث للتصميم (`tiles_menu`، القائمة الأعرض وبطاقات متابعة الفريق) ولا لأ؟
- إذا فيك: شغّل "تقرير حالة BIM" (من سطح المكتب، أو `C:\BIM_Tasks_Server\tools\check_status\CHECK_STATUS.cmd`، كبسة يمين ← Run as administrator) والصق التقرير.
- وصل المسؤولين الملخص اليومي الساعة 6 المسا؟

---

## Instructions for Claude (session 7)

The owner is not technical, speaks Syrian Arabic, is tired of long sessions: short answers, exact clicks, no jargon.
Read `CLAUDE.md` first (deployment log, code layout, design notes). Browser check scripts: `docs/dev/`.

### State at handoff (2026-11-02)
- Server: 7195f1f (`new_look`) confirmed by report; after that the owner installed at least `tiles_design` (he saw it),
  maybe `tiles_menu` (9c2cf82). Ask for the status report and read the commit on 8797/8798 before building anything.
  Then fast-forward `main` to the installed commit. Build the next package with BASE = that commit.
- Design in use: `app/static/bim-ds.css` = base + v3/v4 + TILES block + sidebar A + /my-team cards (see comments in the
  file). Menu: `app/shell_nav.py`. 3D icons: `app/static/icons3d/` (Fluent Emoji 3D, MIT). Asset URLs carry
  `?v={{ asset_v }}` (content hash) — keep that for every new css/js file.
- Off-site 02:00 backup to Telegram: works (owner confirmed). `?secret=`: never seen in any report.

### The owner asked for ONE update containing all of this (package name: `big_update`)
Agreed with him on 2026-11-02. **Before coding the pages, show a preview** (real screenshots at 1714px, not
downscaled, as an Artifact page, like https://claude.ai/artifact/PwDBvEfghPnKMY2nZc13LM) and wait for "تمام".
Do not delete any button, field or option: every old page becomes a tab or view of a new page; old URLs keep working
(redirect to the new page + tab, e.g. `/tasks-table` → `/tasks#table`). Run `docs/dev/cmp.js` old vs new for boss,
samer and emp1 (nothing missing) and `docs/dev/contrast.js` (clean) before building.

1. **Filter icon** (Excel-style column filter, `bim-excel-filter-v2.js` / `button.bim-filter-toggle`): the funnel is
   too thick. Make it thin, small and light; coloured only when a filter is active. Filtering itself unchanged.
2. **Employee profile** `/employee/{id}`: opened by clicking an employee name anywhere (tasks cards, table, manage,
   employees page, admin home team list, /my-team, team performance). Header (name, job, department, Telegram
   linked or not), coloured boxes (open, late, done this week, not opened), ALL tasks in tabs (open / late / done /
   archived) with the quick actions of /my-team (status, reassign, Telegram reminder), recurring tasks, short
   performance. Scope: managers all, supervisor only own team (`access.employee_scope`), employee → `/my-work`.
   New page → add to `access.PAGE_PERMS` (pattern like `/task/`), `activity_log.PAGES`, tests, snapshot on purpose.
3. **Merge pages** (menu 36 → ~25):
   - "المهام" `/tasks` with view buttons **بطاقات / جدول / تعديل سريع / لوحة** (cards = old /tasks, table = old
     /tasks-table, quick edit = old /manage, board = new Kanban, item 5). Keep each view's permissions
     (quick edit needs tasks.edit like /manage).
   - "Telegram": one page with tabs = telegram-center, telegram-groups, telegram-health, telegram-dashboard.
   - "السجلات": activity-log + audit. "التقارير": team-performance + operations-reports (keep each tab's permission:
     reports.team vs dashboard.view; a supervisor must not see the operations tab).
   Update `shell_nav.GROUPS`, `command_center.PAGES` (page visibility), and redirects for the old paths.
4. **Notification bell** in the top bar (managers and supervisors; supervisor = own team only): task became late,
   new comment/attachment on a task, task completed, new task from Telegram/iPhone. Unread count, mark as read,
   click opens the task. New table (e.g. `inbox_items` with user_id, task_id, kind, created_at, read_at), filled where
   these events already happen (status change, comments, overdue worker in LAN process — idempotent per task/day).
   Keep Telegram notifications as they are.
5. **Kanban board** view inside "المهام": columns جديدة / بدأت / قيد التنفيذ / بانتظار / تم; drag a card to change
   status using the existing `PATCH /api/tasks/{id}/status` (same rules and notifications); on phones a status menu
   instead of dragging.
6. **Table columns fit their content — a STANDING RULE for every table in the system (owner, 2026-11-02).** Each
   column is as wide as its longest cell, no wider (example he gave: the "الموعد" column on /tasks-table is far too
   wide). Today `table{width:100%}` spreads the spare width over all columns. Do: tables `width:auto` (or `max-content`)
   inside their `.scroll-box`, short columns (dates, status, priority, numbers, buttons) `white-space:nowrap`, only the
   long text column (task title, notes, message) may wrap with a sensible max-width. Check every page with a table
   (tasks views, manage, archive, recurring, employees, users, telegram pages, logs, reports, shift center, security,
   sync, backup, my-team, team performance) at 1714px and on a phone.
7. **Cleanup:** drop `?secret=` from `_sync_auth` and the query branch of `_note_sync_auth` (app/main.py) if the
   report still says "not seen yet"; update `test_sync_auth_records_header_or_query` (query → 401). Delete the unused
   `app/templates/dashboard.html` (no route renders it — verify again).

Also tell him (no code): the admin/deputy can already receive the daily summary for ALL employees — /shift-center,
end of page, "المدير والنائب" → on.

### Process
Preview → owner OK → build → tests + cmp + contrast → `python deploy/build_update.py big_update <BASE> "تحديث شامل:
ملف الموظف وتنظيم الصفحات والجرس" --check /api/<a new route>` → owner: INSTALL_TO_TEST first, then INSTALL_UPDATE,
then status report → merge into `main`.

### Do not
- Add a gateway check to `iphone_accounts.eligible`.
- Push to `main` before the owner confirms an install with a status report.
- Treat `shift_dispatch_log` / `group_dispatch_log` as append-only.
- Downscale preview screenshots or show options without the login page if the design changes it.

### End of the session (mandatory)
Write `docs/handoff/SESSION_8.md` in the same format, update `CLAUDE.md`, commit, push, and send the file to the owner
with the line: "ارفق هالملف بالمحادثة الجاية واكتب: اتبع الملف المرفق".
