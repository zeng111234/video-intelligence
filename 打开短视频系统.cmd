@echo off
chcp 65001 >nul

:: Check PowerShell (prefer PowerShell 7 if available)
set "PS_CMD=powershell.exe"
where pwsh.exe >nul 2>&1
if %errorlevel% equ 0 (
    set "PS_CMD=pwsh.exe"
)

%PS_CMD% -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_app.ps1"
set "LAUNCH_EXIT=%ERRORLEVEL%"
if not "%LAUNCH_EXIT%"=="0" pause
exit /b %LAUNCH_EXIT%
