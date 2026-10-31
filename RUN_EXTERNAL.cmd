@echo off
cd /d "C:\BIM_Tasks_Server"
if not exist "C:\BIM_Tasks_Server\logs" mkdir "C:\BIM_Tasks_Server\logs"
echo [%date% %time%] BIM Tasks External starting on 8798>>"C:\BIM_Tasks_Server\logs\server-external.log"
"C:\BIM_Tasks_Server\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8798 >>"C:\BIM_Tasks_Server\logs\server-external.log" 2>&1
