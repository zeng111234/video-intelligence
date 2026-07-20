@echo off
echo ========================================
echo  Video Intelligence System - Quick Start
echo ========================================
echo.

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.12+ and add to PATH.
    echo [DOWNLOAD] https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Check Node.js
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not found. Please install Node.js 18+ and add to PATH.
    echo [DOWNLOAD] https://nodejs.org/
    pause
    exit /b 1
)

:: Check PowerShell (prefer PowerShell 7 if available)
set "PS_CMD=powershell.exe"
where pwsh.exe >nul 2>&1
if %errorlevel% equ 0 (
    set "PS_CMD=pwsh.exe"
    echo [INFO] Using PowerShell 7 (pwsh.exe^)
) else (
    echo [INFO] Using Windows PowerShell 5.1 (powershell.exe^)
)

:: Run PowerShell startup script
echo [START] Launching all services, please wait...
echo.
%PS_CMD% -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all_services.ps1"

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
echo [URL] Streamlit MVP:   http://localhost:8501
echo.
pause
