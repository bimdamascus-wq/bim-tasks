# BIM Tasks — تسليم للمحادثة الجاية (إذا احتجت وحدة)

**للمالك:** الشغل الأساسي خلص. إذا صار شي أو بدك تعديل: افتح محادثة جديدة، اختار المستودع
`bimdamascus-wq/bim-tasks`، ارفق هالملف، واكتب: اتبع الملف المرفق — وقول شو المشكلة.

---

## Instructions for Claude
Read `CLAUDE.md` first. Owner is not technical, Syrian Arabic, tired of long sessions: keep answers short, exact clicks.

State: server runs 7195f1f (`new_look`) unless the owner installed `night_design` (the chosen dark design; see the deployment log). Ask for the status report (`C:\BIM_Tasks_Server\tools\check_status\CHECK_STATUS.cmd` or the desktop icon
"تقرير حالة BIM"), confirm the commit on 8797/8798, then fast-forward `main` to `claude/new-session-j9yp8h`.

Check in that report: sync runs short with `unchanged=` large; no new duplicate notifications; supervisor summary rows
(kind "ملخص الفريق اليومي") once per supervisor per day; ask whether the 02:00 off-site backup arrived on Telegram.

Optional only (do not push on the owner): drop `?secret=` from `_sync_auth`/`_note_sync_auth` if still "not seen";
remove old stacked CSS layers; page rebuilds only if the owner asks.

Never: gateway check in `iphone_accounts.eligible`; push to `main` before a confirmed install; treat
`shift_dispatch_log`/`group_dispatch_log` as append-only.
