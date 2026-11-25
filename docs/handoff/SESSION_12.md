# BIM Tasks — تسليم للمحادثة رقم 12

**للمالك:** افتح محادثة جديدة، اختار المستودع `bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب بس:
> اتبع الملف المرفق.

بأول رسالة قول:
- نصّبت **`BIM_Update_full_25nov.zip`**؟ إذا إي، ابعت «تقرير حالة BIM» (من سطح المكتب).
- «تنبيه» بالمجموعة: ردّ البوت «تم تسجيل تنبيه إداري» ووصلتك البطاقة عالخاص؟
- الشكل الجديد (جداول بخطوط) وسحب عرض الأعمدة: في صفحة لسا مو عاجبتك؟ ابعت صورتها.
- غيّرت البوت بأداة `BIM_Change_Bot.zip`؟ (بس إذا لسا بدك بوت تاني)

---

## Instructions for Claude (session 12)

Owner: not technical, Syrian Arabic, tired after many rounds. Short answers, exact clicks, decide yourself, real
screenshots (demo server + Playwright, see docs/dev/README.md). Read `CLAUDE.md` "Session 11" first.
Branch with all work: `claude/lucid-rubin-mwn175` (draft PR bimdamascus-wq/bim-tasks#1). `main` = 124a711 (old).

### State at handoff (2026-11-25)
- No status report this session. Owner says he installed `alert_reason` (36b8fa0, cumulative over install_app),
  then `alert_reporters` (4536fd0, the bot answered with its 13280aa text), then "the one before" `employee_flow`
  (a312b20). Last given: **`full_25nov`** (commit d225f22, BASE 4536fd0, also_base 18a3337 21d243c c97b0e3 169fc25
  a312b20 9fc6694) = everything below. When a report shows d225f22 on 8797: fast-forward `main` to the branch, push.
- Bot: owner made the bot admin in groups; «تنبيه» reaches the server (refusals were shown). Rule now: ANY active
  employee linked to Telegram may log an alert (alerts.reporter); acting on others' alerts = manager/deputy/
  supervisor or follow-up list (alerts.is_manager). Unlinked group members get «حسابك مو مربوط».
  Old alert system (Apps Script, REPORTER_IDS) was read: docs not in repo (owner's zip). Not rebuilt: members/
  memberships pages (owner did not ask again).
- `tools/change_bot/` (zip `BIM_Change_Bot.zip`): token pasted on the server only, getMe check, setWebhook on the new
  bot, .env backup `.env.before_change_bot_*`, restarts ONLY ports 8797/8798. First version closed at once (Arabic
  in .cmd); fixed (ASCII .cmd, all text in Python, waits for Enter). Owner never confirmed a successful run.

### Done this session (all in full_25nov)
- SHEET v6 (owner chose option 2 of 3 real-page mock-ups): last block of bim-ds.css — tables with grid lines, teal
  header, zebra; «كل المهام» columns = small sheets, one row per task, all visible; profile and «متابعة الفريق» task
  lists as rows; square frames/number boxes; buttons hover/press feedback.
- `app/static/bim-colresize.js` (base.html): drag a table header edge to resize, double-click resets, saved in
  localStorage per page+table. Profile/my-team lists are CSS grids, NOT resizable (told the owner).
- Employee page: «🗄 أرشفة» per task row (tasks.delete, confirm), «＋ مهمة جديدة» opens /tasks?new=1&employee=ID.
- Task page: «✏️ تعديل المهمة» dialog (title/notes/priority/due, PATCH only changed fields), «رجوع لصفحة الموظف»,
  readable history (no raw JSON for edits).
- New-task window (tasks.html): stays open after save, clears fields, keeps + remembers last employee
  (localStorage `bim-last-task-employee`), «＋ إضافة مهمة أخرى لنفس الموظف» = several titles, one task each.

### Next steps
1. Read the report; fast-forward `main` if d225f22.
2. Ask what still bothers him on the new look; fix from a screenshot.
3. Possible asks: same new-task behaviour in the home quick-add and /follow; resizable profile/my-team columns;
   forum topics: alert answer in the same topic (send with message_thread_id).
4. Still open from before: 18:00 supervisor summary (never answered), test copy data from 2026-10-31,
   `?secret=` removal, 8798 probably unused, v2 interface (/v2) not restyled with SHEET v6.

### Do not
- Stop python processes by name or by `app.main:app` alone (other systems on this server).
- Put Arabic text in a .cmd file (cmd.exe closes the window).
- Ask for or accept the bot token in chat.
- Push to `main` before a status report confirms the install.
- Use `//` comments inside one-line template JS (it silently ate the rest of saveNew once).

### End of the session (mandatory)
Write `docs/handoff/SESSION_13.md` in the same format, update `CLAUDE.md`, commit, push, and send the file to the owner
with the line: "ارفق هالملف بالمحادثة الجاية واكتب: اتبع الملف المرفق".
