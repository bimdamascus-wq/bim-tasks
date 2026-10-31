# BIM Tasks — تسليم للمحادثة رقم 5

**للمالك:** افتح محادثة جديدة، اختار المستودع `bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب بس:
> اتبع الملف المرفق.

بأول رسالة قول:
- نزّلت تحديث `supervisor_tools` ولا لأ؟ إذا نزّلته: شغّل "تقرير حالة BIM" (كبسة يمين ← Run as administrator) والصق التقرير.
- وصل المسؤولين الملخص اليومي الساعة 6 المسا؟ في حدا اشتكى؟
- وصلتك نسخة الساعة 2 الاحتياطية على Telegram؟

---

## Instructions for Claude (session 5)

The owner is not technical, speaks Syrian Arabic, wants exact clicks (which file, which folder, right click → Run as
administrator). Read `CLAUDE.md` first (deployment log, code layout, open items).

### State at handoff
- Server runs **6db03f1** (`code_split`, confirmed 15:05 on 2026-10-31). `main` = fcba200 (6db03f1 + docs).
- Package **`supervisor_tools`** (commit c94b3e6, base 6db03f1, branch `claude/new-session-j9yp8h`) given, not confirmed:
  archive no-op fix (cause of the 98 duplicate "cancelled" messages), daily supervisor Telegram summary (ON by default,
  18:00, settings at the end of /shift-center), /team-performance + CSV (permission `reports.team`), /my-team page.
- Tests: 34 pass. Route snapshot regenerated on purpose for each new route (see commits).

### First thing to do
1. With the post-install report: ports 8797 `lan` / 8798 `external` on c94b3e6 → fast-forward `main` to the branch.
2. Full sync: runs after 15:07 on 2026-10-31 should be `mode=incremental`, short; one `mode=full` after 03:00 daily.
   If incremental runs still take minutes, look at which table is large in the SUMMARY counts.
3. `?secret=` still "not seen yet" → remove it (see SESSION_4 for the exact places) in the next package.
4. `[telegram queue]` / notifications: check the supervisor summary rows (kind "ملخص الفريق اليومي") were sent, once
   per supervisor per day. Supervisors not linked to Telegram are listed on /shift-center.
5. Ask about the 02:00 off-site backup (first automatic run 2026-11-01).

### Next scope (owner decision: redesign last, now it is next)
Visual redesign: ~42 templates → ~8 pages (manager dashboard, tasks list/board, task detail, team, recurring, reports,
settings, supervisor page mobile-first — /my-team is the functional version to restyle). Remove the stacked CSS layers
(bim-system-v510, bim-theme-v63, bim-v81-center-ar, bim-premium-ui) only when no page loads them any more.
Start with a clickable mock-up (Artifact) for the owner to approve; build new pages next to the old ones; remove old
ones only after acceptance; one or two pages per package, each tried with INSTALL_TO_TEST.cmd first.

### Do not
- Add a gateway check to `iphone_accounts.eligible` (owner decided shortcuts work from outside).
- Push to `main` before the owner confirms an install with a status report.
- Treat `shift_dispatch_log` / `group_dispatch_log` as append-only (workers update and delete their rows).

### End of the session (mandatory)
Write `docs/handoff/SESSION_6.md` in the same format, update `CLAUDE.md`, commit, push, and send the file to the owner
with the line: "ارفق هالملف بالمحادثة الجاية واكتب: اتبع الملف المرفق".
