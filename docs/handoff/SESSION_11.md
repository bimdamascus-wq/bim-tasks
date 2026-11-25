# BIM Tasks — تسليم للمحادثة رقم 11

**للمالك:** افتح محادثة جديدة، اختار المستودع `bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب بس:
> اتبع الملف المرفق.

بأول رسالة قول:
- نصّبت **`BIM_Update_install_app.zip`**؟ إذا إي، ابعت «تقرير حالة BIM» (من سطح المكتب).
- كيف لقيت **الواجهة الجديدة** (القائمة فوق: الرئيسية، المهام، التنبيهات…)؟ شو أكتر شي زعجك فيها؟
- ضفت البوت عالمجموعات **وعملته مشرف**؟ جرّبت كلمة «تنبيه» بمجموعة وحدة؟ رد البوت؟
- طلع زر «تثبيت» بكروم لما فتحت task.bimdamascus.com؟

---

## Instructions for Claude (session 11)

The owner is not technical, speaks Syrian Arabic, is tired and frustrated after many redesign rounds: short answers,
exact clicks, no jargon, decide yourself like a professional (he asked for that), show real screenshots, not mock-ups.
Read `CLAUDE.md` first (sections "Session 10 ..." at the end). Branch with all work: `claude/new-session-k7b05c`.
Demo server + screenshots: `docs/dev/README.md` (seed demo data, Playwright `NODE_PATH=$(npm root -g)`).

### State at handoff (2026-11-25)
- Last status REPORT (2026-11-23 13:38): server 124a711 (`short_menu`) on 8797; 8798 not running (site still works from
  outside, so the tunnel most likely points to 8797). `main` = 124a711.
- After that the owner saw `home_v4` working (f77dc69). Not confirmed by any report: `group_alerts` (818e6d6),
  `new_identity` (3e59d83), `follow_screen` (12d86ab), `new_interface` (d2e0ff3), `install_app` (7724daa).
- `install_app` is CUMULATIVE (base f77dc69, `also_base` 818e6d6/3e59d83/12d86ab/d2e0ff3): one package carries all of
  them. When a report shows 7724daa on 8797: fast-forward `main` to the branch and push.

### What exists now (all on the branch)
- **Interface v2** (`/v2`, routes_v2.py, templates/v2/*, static/v2.css + v2.js): new frame with top navigation; pages
  الرئيسية، المهام (list + detail, one-click actions), التنبيهات، الفريق، المتكررة (read only)، التقارير، الإعدادات (tiles to old
  admin pages). New login page. Managers + supervisors land on /v2 after login; employees /my-work.
- **Telegram «تنبيه»** (app/alerts.py): Reply «تنبيه» in any group the bot is in -> task (source telegram_alert,
  ALERT-n / «ت-n»), group answer, private card with قيد المعالجة / بانتظار رد / تم الحل / سبب المشكلة / إعادة فتح; page
  /alerts and /v2/alerts. Allowed: followup `remind_ids`, else admin/deputy/supervisor accounts; reporter must be a
  LINKED employee. Bot must see group messages: privacy off in BotFather OR bot is a group ADMIN (owner's bot was
  created on another phone number; advised: make the bot admin in each group — no new bot needed).
- **Installable app**: square icons, manifest start_url /v2, service worker on v2 pages (only over HTTPS domain).
- Old interface still reachable (`/`, old pages) from /v2/settings.

### Next steps (in order)
1. Read the report; fast-forward `main` if 7724daa.
2. Owner feedback on v2: fix what bothers him (ask for ONE concrete thing + a screenshot of the screen).
3. Rebuild in v2 the old pages he still opens (usage numbers in the report: [pages opened, last 30 days]):
   task edit/attachments/archive, recurring add/edit, employees add/edit, Telegram linking. Then retire the old shell.
4. If he still wants a NEW bot: never take the token in chat; give him a double-click tool that writes
   TELEGRAM_BOT_TOKEN into `.env`, restarts the tasks and calls set_webhook; every employee must press Start again.

### Open items
- Never answered: did supervisors get the 18:00 summary?
- Test copy data still from 2026-10-31 (TEST_COPY_CREATE.cmd before any INSTALL_TO_TEST).
- `?secret=` never seen in any report: can be removed (harmless).
- 8798 probably unused (confirm the tunnel target before removing it).
- Installer `also_base` logic was not run on Windows here (no PowerShell): if a red message appears, ask for a photo.

### Do not
- Restyle old pages again with CSS layers and call it "new" — the owner rejects that every time.
- Stop python processes by name or by `app.main:app` alone (other systems on this server use it).
- Add a gateway check to `iphone_accounts.eligible`.
- Push to `main` before a status report confirms the install.
- Ask for or accept the bot token in chat.

### End of the session (mandatory)
Write `docs/handoff/SESSION_12.md` in the same format, update `CLAUDE.md`, commit, push, and send the file to the owner
with the line: "ارفق هالملف بالمحادثة الجاية واكتب: اتبع الملف المرفق".
