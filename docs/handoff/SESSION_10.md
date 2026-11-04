# BIM Tasks — تسليم للمحادثة رقم 10

**للمالك:** افتح محادثة جديدة، اختار المستودع `bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب بس:
> اتبع الملف المرفق.

بأول رسالة قول:
- نصّبت التحديثات التلاتة بالترتيب (`followup_lively` ← `dashboard_pro` ← `reminders3`)؟ إذا إي، ابعت «تقرير حالة BIM».
- كيف طلعت لوحة المدير الجديدة عندك؟ بدك تغيّر شي؟
- جرّبت التذكير الجديد (مثلاً «يتكرر كل ساعة» على مهمة إلك)؟ وصلك؟
- بمجموعة المتابعة: زر كل مهمة والتعليق بالرد (Reply) شغّالين؟
- وصل للمسؤولين الملخص اليومي الساعة ٦ المسا؟

---

## Instructions for Claude (session 10)

The owner is not technical, speaks Syrian Arabic, is tired of long sessions: short answers, exact clicks, no jargon.
Read `CLAUDE.md` first (deployment log "Session 9", code layout, design notes, other systems on the server).
Branch with all work: `claude/zealous-lamport-x69beu` (contains everything of `claude/modest-faraday-syi9e5`).
Demo server + screenshots: `docs/dev/README.md`; Telegram capture method: `docs/handoff/SESSION_9.md` ("capture").

### State at handoff (2026-11-04)
- Server: e98e2a8 (`followup_tasks`, report 2026-11-04 06:35, all ports). `main` = e661696 (= e98e2a8 + docs).
- Given to the owner, NOT confirmed installed, must go in this order (each installer refuses a wrong base):
  1. `followup_lively` — d4d84ab, BASE e98e2a8: follow-up group messages in Telegram HTML (bold, emojis, separators).
  2. `dashboard_pro` — e4f1485, BASE d4d84ab: manager dashboard redesign (`/`, command_center.html, `#dx` block in
     bim-ds.css, /api/v2/dashboard extra fields: due_today, unassigned, unseen, done_week, mix, trend, team done7).
  3. `reminders3` — 7da6b1d, BASE e4f1485: three reminder options (app/reminders.py, bim-reminder.js). First start
     rebuilds `task_reminders` (old CHECK removed, every_n/every_unit/before_minutes, old rows converted).
- After the status report shows all ports on 7da6b1d: fast-forward `main` to the branch and push.

### What to check in the first report after install
- Ports 8797 lan+workers, 8798 external, 8799 test, all 7da6b1d.
- No errors in server-lan.err.log at startup (the reminder migration runs there; it is in one transaction).
- Sync: one run will upload all task_reminders rows again (their content changed) — expected, then back to seconds.

### Open items
- Owner never answered: 18:00 supervisor summary arrived?
- Test copy data is still from 2026-10-31: tell him to run `TEST_COPY_CREATE.cmd` before the next INSTALL_TO_TEST.
- `?secret=` never seen in any report: can be removed in a later package (harmless).
- Ideas NOT requested (do not do without asking): rest of the pages in the dashboard's calm style.

### Do not
- Stop python processes by name or by `app.main:app` alone (other systems on this server use it too).
- Add a gateway check to `iphone_accounts.eligible`.
- Push to `main` before the owner confirms an install with a status report.
- Treat `shift_dispatch_log` / `group_dispatch_log` as append-only.
- Drop any button, field or option; keep table columns fitted to content (standing rule).

### End of the session (mandatory)
Write `docs/handoff/SESSION_11.md` in the same format, update `CLAUDE.md`, commit, push, and send the file to the owner
with the line: "ارفق هالملف بالمحادثة الجاية واكتب: اتبع الملف المرفق".
