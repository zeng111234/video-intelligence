@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_app.ps1"
set "LAUNCH_EXIT=%ERRORLEVEL%"
if not "%LAUNCH_EXIT%"=="0" pause
exit /b %LAUNCH_EXIT%
