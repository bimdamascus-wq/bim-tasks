# BIM Tasks — تسليم للمحادثة رقم 9

**للمالك:** افتح محادثة جديدة، اختار المستودع `bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب بس:
> اتبع الملف المرفق.

بأول رسالة قول:
- عجبك الشكل الجديد لرسائل Telegram يلي بالصورة (`telegram_new_look.png`)؟ «تمام» ولا بدك تغيير؟
- رجّعت تشغّل «نواقص الموردين» (8080) ولا ما عاد بتستعملوه؟
- وصل المسؤولين الملخص اليومي الساعة ٦ المسا؟
- مين بدك يكونوا بمجموعة المتابعة؟ (بس الأسماء، كرمال نعرف شو لازم يشوفوا)

---

## Instructions for Claude (session 9)

The owner is not technical, speaks Syrian Arabic, is tired of long sessions: short answers, exact clicks, no jargon.
Read `CLAUDE.md` first (deployment log, code layout, design notes, the CORRECTION about other systems on the server).
Branch with all work: `claude/modest-faraday-syi9e5`. Browser and Telegram check scripts: `docs/dev/` and the
"capture" method below.

### State at handoff (2026-11-03, morning)
- Server: 6a5955a (`big_update` + `delete_archive`) on 8797/8798/8799, confirmed by report 07:51. `main` = 23ef6df
  (6a5955a + docs). Nothing waiting to be installed.
- On the branch, NOT shipped (all go into the next package, BASE 6a5955a):
  1. 6d1a6a0 — installer `Stop-Bim` and status report only touch OUR ports (8797/8798, test 8799). Before this, every
     install also stopped «نواقص الصالة» (8070, restarts itself) and «نواقص الموردين» (8080, stayed down). IMPORTANT.
  2. 28a345f — Telegram texts: Arabic dates (`main.ar_when`), one header, no "sent at" line, 🔴/🟠 priority, keyboard
     shows only next steps, task lists ("مهامي اليوم" + shift lists) have one button per task (`open:ID` callback →
     `_tg_send_task_card`), typo fix. Preview sent: `dist/telegram_new_look.png` (regenerate: see "capture"). Owner saw
     it but has NOT said "تمام" yet — ask.
  3. `app/followup.py` — WORK IN PROGRESS, not imported anywhere yet (harmless if shipped, but finish it first).

### Next feature: follow-up group on Telegram (owner, 2026-11-03)
Owner's answers: deliver through a **Telegram group** (members = the follow-up staff; existing linking flow on
/telegram-groups: create group → link code → add bot → `/linkgroup CODE`); group members **follow and remind only**
(button «🔔 ذكّر الموظف», no status change from the group); scope: all employees (an optional employee list is in the
settings shape).
**Owner's latest rule:** EVERYTHING that the system shows the manager or sends to the assigned employee must also
appear in the group, so nobody reminds an employee about a task that is already done. At least:
- new task (with the employee's name and who sent it), every status change (بدأت / قيد التنفيذ / تم) from any path
  (Telegram buttons, web, group tasks' participants), problem/note and comments both ways, attachments,
  edits (title/priority/due), reassignment (from → to), cancel/archive, restore, late, every reminder the system sent
  (automatic, shift, manual Telegram resend, from the group), and the morning list of open tasks per employee.
- When a task is done/cancelled, also EDIT the original group card (remember group message ids, like
  `telegram_task_messages`) to show the final status and drop the «ذكّر» button.
Design already in `app/followup.py`: settings key `followup.settings`, cursor `followup.cursor` (first run starts from
"now"), messages through `telegram_queue.enqueue` with dedupe keys, `handle_callback` for `fu:remind:ID` (allowed
only from the follow-up group chat, one reminder per task per 15 min, posts "X ذكّر Y" in the group, audit),
`/api/followup` GET/PUT (employees.edit). Still to do:
- Extend the scan to ALL the events above (task_events types: created, status, participant_status, note, comment,
  attachment, edited, reassigned, cancelled, lifecycle, telegram_sent; plus tasks/comments/attachments tables).
  Prefer one scan of task_events + task_comments + task_attachments since a cursor (same idea as `app/inbox.py`).
  Check which actions do NOT write a task_event today (e.g. archive writes 'cancelled'? verify) and add events there.
- Wire it: `workers.loop` → `followup.process()`; webhook → call `followup.handle_callback(c, cb)` BEFORE the
  employee lookup (the presser may not be a linked employee); `main.py` routes block → `followup.register`.
- Settings UI at the top of /telegram-groups: choose the group, on/off, digest time, which kinds (all on by default).
- Tests (fake send as in tests/conftest.py): new task → group card; employee presses تم → group gets "أنجز" AND the
  card is edited; remind from another chat refused; second remind within 15 min refused; scope list respected.
- Preview for the owner first: real captured messages as a phone/group picture (method below), wait for "تمام".
- Then one package with items 1+2+3: `python deploy/build_update.py followup_group 6a5955a "مجموعة المتابعة على
  Telegram وتحسين رسائل المهام"`, INSTALL_TO_TEST first (tell him to run TEST_COPY_CREATE.cmd before), then INSTALL_UPDATE,
  then status report → merge into `main`. Tell him the installer no longer stops his other systems.

### Capture method (how the Telegram previews were made)
Copy app/ + schema.sql to a temp dir, init_db, import app.main, patch `telegram_queue.low_level_send`,
`telegram_queue.edit_message`, `telegram_service.send` and every module's `answer_callback` to record
(chat, text, buttons); drive it with TestClient (login boss, POST /api/tasks, POST /api/telegram/webhook with header
`x-telegram-bot-api-secret-token` and callback_query data like `task:ID:done`, `my_tasks`, `open:ID`); dump JSON;
render bubbles into an HTML phone frame; screenshot with Playwright (`NODE_PATH=$(npm root -g) node shot.js`).
Group messages: also patch `telegram_queue.enqueue` or run `process_queue()` with the patched sender.

### Do not
- Stop python processes by name or by `app.main:app` alone (other systems on this server use it too).
- Add a gateway check to `iphone_accounts.eligible`.
- Push to `main` before the owner confirms an install with a status report.
- Treat `shift_dispatch_log` / `group_dispatch_log` as append-only.
- Drop any button, field or option; keep table columns fitted to content (standing rule).

### End of the session (mandatory)
Write `docs/handoff/SESSION_10.md` in the same format, update `CLAUDE.md`, commit, push, and send the file to the owner
with the line: "ارفق هالملف بالمحادثة الجاية واكتب: اتبع الملف المرفق".
