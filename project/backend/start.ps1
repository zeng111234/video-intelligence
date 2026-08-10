# === UTF-8 编码保障 ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = "$root;$env:PYTHONPATH"
$setupScript = Join-Path $root "scripts\setup_windows.ps1"
& $setupScript
if ($LASTEXITCODE -ne 0) {
    exit 1
}
$pythonCommand = Join-Path $root ".venv\Scripts\python.exe"
Write-Host "Project root: $root" -ForegroundColor Cyan
Write-Host "Starting FastAPI on port 2001..." -ForegroundColor Green
& $pythonCommand -m uvicorn app.main:app --host 127.0.0.1 --port 2001 --reload --no-proxy-headers
