# BIM Tasks — تسليم للمحادثة رقم 10

**للمالك:** افتح محادثة جديدة، اختار المستودع `bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب بس:
> اتبع الملف المرفق.

بأول رسالة قول:
- عجبتك صورة مجموعة المتابعة (`followup_group_preview.png`) وصورة رسائل Telegram الجديدة؟ «تمام» ولا بدك تغيير؟
- أي أسئلة من المحادثة 9 ما جاوبت عليها (الموردين 8080، ملخص الساعة ٦، أسماء مجموعة المتابعة).

---

## Instructions for Claude (session 10)
Owner: not technical, Syrian Arabic, short answers, exact clicks. Read `CLAUDE.md` first ("Session 9").
Branch with all work: `claude/zealous-lamport-x69beu` (contains everything of `claude/modest-faraday-syi9e5`).

### State at handoff (2026-11-03)
- Server: 6a5955a. Nothing waiting to be installed. `main` = 23ef6df.
- On the branch, NOT shipped, all for ONE package (BASE 6a5955a):
  1. 6d1a6a0 installer/status report touch only our ports (stops no longer kill 8070/8080). IMPORTANT.
  2. 28a345f new Telegram texts for employees (Arabic dates, one button per task in lists).
  3. Follow-up group (see CLAUDE.md "Session 9"). 51 tests pass.

### Next steps
1. If the owner says «تمام» (or after his changes): `python deploy/build_update.py followup_group 6a5955a "مجموعة المتابعة على Telegram وتحسين رسائل المهام"`.
2. Tell him: TEST_COPY_CREATE.cmd first, then INSTALL_TO_TEST.cmd, then INSTALL_UPDATE; the installer no longer stops his other systems.
3. After install, set it up with him on /telegram-groups: create group → «🔗 ربط المجموعة» → add the bot to the Telegram
   group → send `/linkgroup CODE` → in the box «👀 مجموعة المتابعة» choose it, «مفعّلة», save.
4. Status report → merge into `main`.

### Do not
Same list as SESSION_9 (no stopping python by name, no gateway check in iphone_accounts.eligible, no push to `main`
before a confirmed install, dispatch logs are not append-only, never drop a button/field, columns fit content).

### End of the session (mandatory)
Write `docs/handoff/SESSION_11.md` in the same format, update `CLAUDE.md`, commit, push, and send the file to the owner
with the line: "ارفق هالملف بالمحادثة الجاية واكتب: اتبع الملف المرفق".
