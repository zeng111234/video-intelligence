# === UTF-8 编码保障 ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = "$root;$env:PYTHONPATH"
Write-Host "Project root: $root" -ForegroundColor Cyan
Write-Host "Starting FastAPI on port 2001..." -ForegroundColor Green
python -m pip install -r requirements.txt -q
python -m uvicorn app.main:app --host 0.0.0.0 --port 2001 --reload
