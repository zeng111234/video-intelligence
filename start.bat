@echo off
setlocal
echo ========================================
echo  Video Intelligence System - Quick Start
echo ========================================
echo.

:: Check PowerShell (prefer PowerShell 7 if available)
set "PS_CMD=powershell.exe"
where pwsh.exe >nul 2>&1
if %errorlevel% equ 0 (
    set "PS_CMD=pwsh.exe"
    echo [INFO] Using PowerShell 7 (pwsh.exe^)
) else (
    echo [INFO] Using Windows PowerShell 5.1 (powershell.exe^)
)

:: Default behaviour connects to the company server: company login, credits and
:: cloud transcription / cloud video editing.  For an offline local demo that
:: deliberately disables all of that, run:  start.bat -LocalDemo
echo [START] Checking the environment and launching all services, please wait...
echo [MODE]  Company server mode (use -LocalDemo for the offline demo instead)
echo.
%PS_CMD% -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all_services.ps1" %*

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Startup failed. Check the error messages above.
    pause
    exit /b 1
)

echo.
echo [DONE] All services started successfully.
echo [URL] Frontend:        http://localhost:1001
echo [URL] Backend API:     http://localhost:2001
echo.
pause
