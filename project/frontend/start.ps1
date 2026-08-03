# === UTF-8 编码保障 ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$setupScript = Join-Path $root "scripts\setup_windows.ps1"
& $setupScript
if ($LASTEXITCODE -ne 0) {
    exit 1
}
npm run dev
