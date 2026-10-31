@echo off
rem Staging copy only (C:\BIM_Tasks_Test). Refuses to run anywhere without the TEST_COPY.txt marker.
cd /d "%~dp0"
if not exist "%~dp0TEST_COPY.txt" (
  echo This is not the BIM Tasks test copy. Use TEST_COPY_CREATE.cmd on the server folder.
  pause
  exit /b 1
)
if not exist "%~dp0logs" mkdir "%~dp0logs"
set BIM_GATEWAY=test
set BIM_PORT=8799
set BIM_COOKIE_SECURE=0
"%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8799 >>"%~dp0logs\server-test.log" 2>&1
