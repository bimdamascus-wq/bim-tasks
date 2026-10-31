@echo off
cd /d C:\BIM_Tasks_Server
if not exist logs mkdir logs
"C:\BIM_Tasks_Server\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8797 >>"C:\BIM_Tasks_Server\logs\server-lan.out.log" 2>>"C:\BIM_Tasks_Server\logs\server-lan.err.log"
