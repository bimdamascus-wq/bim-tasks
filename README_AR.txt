BIM DAMASCUS TASKS SERVER V1.1
===============================
الدومين المعتمد: https://task.bimdamascus.com

الفكرة:
- FastAPI + SQLite WAL: قاعدة محلية سريعة مثل نظام الموردين.
- بوابة LAN على 8797.
- بوابة External منفصلة على 8798، والدومين يوجّه لها فقط.
- Telegram والتذكيرات تعمل داخل Worker محلي كل 30 ثانية.
- اختصار iPhone يرسل POST إلى /api/iphone/task.
- PWA + Offline Queue للعمليات المسموحة.

التثبيت على Windows:
1) فك الضغط في مجلد ثابت مثل C:\BIM_TASKS
2) شغّل PowerShell كمسؤول داخل المجلد: .\INSTALL.ps1
3) افتح .env وعدل:
   BIM_IPHONE_SECRET
   TELEGRAM_BOT_TOKEN
   BIM_ADMIN_PASSWORD
4) شغّل .\run_lan.ps1
5) افتح http://SERVER-IP:8797

الدخول الخارجي:
- شغل .\run_external.ps1
- استخدم Caddyfile.example أو Cloudflare Tunnel لربط task.bimdamascus.com إلى 127.0.0.1:8798
- لا تفتح منفذ SQLite على الإنترنت.
- أدوار admin و supervisor مسموح لها خارجيًا افتراضيًا؛ employee و viewer غير مسموح حتى تعدل السياسة.

اختصار iPhone:
URL: https://task.bimdamascus.com/api/iphone/task
Method: POST
Header: X-BIM-Secret = القيمة الموجودة في .env
Body Form: text = النص الناتج من الإملاء أو طلب النص

ترحيل النظام القديم:
- من Google Sheet اختر تنزيل > Microsoft Excel (.xlsx)
- انسخ الملف بجانب legacy_import.py
- شغل: .\.venv\Scripts\python.exe legacy_import.py "اسم الملف.xlsx"
- الموظف غير المطابق يبقى بدون موظف ولا يتم تعيين اسم عشوائي.

مهم:
هذه الحزمة لا تحتوي أي Telegram Token أو Google OAuth Token حقيقي.
