@echo off
call "C:\BIM_Tasks_Server\STOP_LAN.cmd"
timeout /t 2 /nobreak >nul
start "" /min "C:\BIM_Tasks_Server\RUN_LAN.cmd"
