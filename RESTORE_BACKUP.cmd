@echo off
chcp 65001 >nul
cd /d %~dp0
echo === BIM Tasks - استرجاع نسخة احتياطية ===
echo سيتم ايقاف النظام اولاً.
call cscript //nologo "%~dp0STOP_BIM_TASKS.vbs"
timeout /t 3 >nul
".venv\Scripts\python.exe" "tools\restore_bim_tasks.py" %*
pause
