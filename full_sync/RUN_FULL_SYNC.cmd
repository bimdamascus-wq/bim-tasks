@echo off
set BIM_SERVER_ROOT=C:\BIM_Tasks_Server
"C:\BIM_Tasks_Server\.venv\Scripts\python.exe" "C:\BIM_Tasks_Server\full_sync\sync_all.py" >> "C:\BIM_Tasks_Server\sync_full_logs\scheduled.log" 2>&1
