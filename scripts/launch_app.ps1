# === UTF-8 encoding ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$appUrl = "http://127.0.0.1:1001/"
$healthUrl = "http://127.0.0.1:1001/"

function Test-AppHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

if (-not (Test-AppHealth)) {
    Write-Host "[START] Starting official React/FastAPI services..." -ForegroundColor Cyan
    $launcher = Join-Path $projectRoot "scripts\start_all_services.ps1"
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $launcher, "-SkipBrowser") `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden | Out-Null

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if (Test-AppHealth) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        Write-Host "[ERROR] Startup timed out. Check whether port 1001 or 2001 is already in use." -ForegroundColor Red
        exit 1
    }
}

if ($env:VIDEO_APP_NO_BROWSER -ne "1") {
    Write-Host "[READY] Opening $appUrl" -ForegroundColor Green
    Start-Process $appUrl
}

exit 0
