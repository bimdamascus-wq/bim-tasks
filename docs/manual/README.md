# User manual (PDF) — how to rebuild
1. Demo server: copy app/ + schema.sql to a temp dir, `python seed_demo.py <dir>`, start uvicorn on 8766 (see docs/dev/README.md),
   `python data_demo.py` (tasks, statuses, recurring). Alerts were added by calling `alerts.handle` with a fake group message.
2. `NODE_PATH=$(npm root -g) node shots.js <shots dir>` (boss / samer / ahmad pages).
3. `python build.py <shots dir> app/static/fonts app/static/bim-damascus-logo.png` -> manual.html, then
   `node pdf.js <abs manual.html> <out.pdf>`. Text lives in build.py (Syrian Arabic, one chapter per ch()).
