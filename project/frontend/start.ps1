# === UTF-8 编码保障 ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
npm install
npm run dev
