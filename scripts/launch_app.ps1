# === UTF-8 encoding ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$appUrl = "http://127.0.0.1:8501/"
$healthUrl = "${appUrl}_stcore/health"
$ffmpegBin = Join-Path $env:LOCALAPPDATA "Programs\ffmpeg\bin"

function Test-AppHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Python was not found. Install Python 3.12 first." -ForegroundColor Red
    exit 1
}

Set-Location $projectRoot

& python -c "import streamlit, pandas, pydantic" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[SETUP] Installing project dependencies..." -ForegroundColor Cyan
    & python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Dependency installation failed. Check the network and retry." -ForegroundColor Red
        exit 1
    }
}

if (-not (Test-AppHealth)) {
    Write-Host "[START] Starting the video intelligence app..." -ForegroundColor Cyan
    if (Test-Path -LiteralPath $ffmpegBin) {
        $env:Path = "$ffmpegBin;$env:Path"
    }
    Start-Process -FilePath "python" -ArgumentList "-m streamlit run app.py --server.headless=true --server.port=8501" -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (Test-AppHealth) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        Write-Host "[ERROR] Startup timed out. Check whether port 8501 is already in use." -ForegroundColor Red
        exit 1
    }
}

if ($env:VIDEO_APP_NO_BROWSER -ne "1") {
    Write-Host "[READY] Opening $appUrl" -ForegroundColor Green
    Start-Process $appUrl
}

exit 0
