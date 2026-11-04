# BIM Tasks — تسليم للمحادثة رقم 10

**للمالك:** افتح محادثة جديدة، اختار المستودع `bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب بس:
> اتبع الملف المرفق.

بأول رسالة قول:
- نزّلت التحديث `followup_group`؟ إذا إي، ابعت «تقرير حالة BIM».
- وصل للمسؤولين الملخص اليومي الساعة ٦ المسا؟ (اسألهم)
- اشتغلت مجموعة المتابعة؟ في شي ناقص؟

---

## Instructions for Claude (session 10)
Owner: not technical, Syrian Arabic, short answers, exact clicks. Read `CLAUDE.md` first ("Session 9").
Branch with all work: `claude/zealous-lamport-x69beu` (contains everything of `claude/modest-faraday-syi9e5`).

### State at handoff (2026-11-03)
- Server: e98e2a8 (`followup_tasks`, report 2026-11-04 06:35, all ports). Package `followup_lively` (d4d84ab, BASE e98e2a8,
  bold/emoji group messages) given, NOT confirmed: ask for the status report, then fast-forward `main`.
  Ask whether the per-task buttons and Reply-comments work in the real group. `main` = 23ef6df.
- On the branch, NOT shipped, all for ONE package (BASE 6a5955a):
  1. 6d1a6a0 installer/status report touch only our ports (stops no longer kill 8070/8080). IMPORTANT.
  2. 28a345f new Telegram texts for employees (Arabic dates, one button per task in lists).
  3. Follow-up group (see CLAUDE.md "Session 9"). 51 tests pass.

### Next steps
1. DONE: package `followup_group` built (commit bcc6ed2, BASE 6a5955a) and given to the owner.
2. Told him: TEST_COPY_CREATE.cmd first, then INSTALL_TO_TEST.cmd, then INSTALL_UPDATE; the installer no longer stops his other systems.
3. After install, set it up with him on /telegram-groups: create group → «🔗 ربط المجموعة» → add the bot to the Telegram
   group → send `/linkgroup CODE` → in the box «👀 مجموعة المتابعة» choose it, «مفعّلة», save.
4. Status report → merge into `main`.

### Do not
Same list as SESSION_9 (no stopping python by name, no gateway check in iphone_accounts.eligible, no push to `main`
before a confirmed install, dispatch logs are not append-only, never drop a button/field, columns fit content).

### End of the session (mandatory)
Write `docs/handoff/SESSION_11.md` in the same format, update `CLAUDE.md`, commit, push, and send the file to the owner
with the line: "ارفق هالملف بالمحادثة الجاية واكتب: اتبع الملف المرفق".
