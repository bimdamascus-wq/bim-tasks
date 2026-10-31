@echo off
cscript //nologo "C:\BIM_Tasks_Server\STOP_BIM_TASKS.vbs"
timeout /t 2 /nobreak >nul
cscript //nologo "C:\BIM_Tasks_Server\START_BIM_TASKS.vbs"
exit /b 0
