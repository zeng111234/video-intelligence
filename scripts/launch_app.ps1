# === UTF-8 encoding ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$appUrl = "http://127.0.0.1:1001/"
$healthUrl = "http://127.0.0.1:1001/"
$crawlerCapabilityUrl = "http://127.0.0.1:2001/api/v1/crawler/browser-discovery/capabilities"
$requiredHotspotAdapterVersion = "hotspot_fiber_v2"

function Test-AppHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Test-HotspotBackendReady {
    try {
        $response = Invoke-RestMethod -Uri $crawlerCapabilityUrl -TimeoutSec 3
        return (
            $response.provider_name -eq "douyin_local_browser" -and
            $response.adapter_version -eq $requiredHotspotAdapterVersion
        )
    }
    catch {
        return $false
    }
}

function Wait-HotspotBackendReady {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (Test-HotspotBackendReady) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

if (-not (Test-HotspotBackendReady)) {
    Write-Host "[UPDATE] Restarting backend to load the Hotspot crawler adapter..." -ForegroundColor Yellow
    $backendRestart = Join-Path $projectRoot "scripts\restart_backend.ps1"
    & $backendRestart
    if (-not (Wait-HotspotBackendReady)) {
        Write-Host "[ERROR] Backend started but the Hotspot crawler adapter was not confirmed." -ForegroundColor Red
        exit 1
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
