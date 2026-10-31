@echo off
cd /d C:\BIM_Tasks_Server
if not exist logs mkdir logs
rem LAN gateway: the only process that runs the workers (reminders, Telegram queue, backups).
set BIM_GATEWAY=lan
set BIM_PORT=8797
set BIM_COOKIE_SECURE=0
"C:\BIM_Tasks_Server\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8797 >>"C:\BIM_Tasks_Server\logs\server-lan.out.log" 2>>"C:\BIM_Tasks_Server\logs\server-lan.err.log"
