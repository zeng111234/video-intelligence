# === UTF-8 encoding ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "SilentlyContinue"

# Kill ALL python processes on port 2001
Write-Host "[INFO] Killing all processes on port 2001..."
$maxAttempts = 5
for ($i = 0; $i -lt $maxAttempts; $i++) {
    $connections = Get-NetTCPConnection -LocalPort 2001 -State Listen -ErrorAction SilentlyContinue
    if (-not $connections) {
        Write-Host "[INFO] Port 2001 is free"
        break
    }
    $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        if ($procId -and $procId -ne 0) {
            Write-Host "[INFO] Killing PID $procId (attempt $($i+1))"
            Stop-Process -Id $procId -Force
        }
    }
    Start-Sleep -Seconds 2
}

# Final check
$finalCheck = Get-NetTCPConnection -LocalPort 2001 -State Listen -ErrorAction SilentlyContinue
if ($finalCheck) {
    Write-Host "[ERROR] Port 2001 still occupied after $maxAttempts attempts"
    exit 1
}

Write-Host "[SUCCESS] Port 2001 is now free"

# Start backend from this script's repository, regardless of user name or drive.
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = $projectRoot
$env:VIDEOINSIGHT_DESKTOP_CLIENT = "true"
$env:VIDEOINSIGHT_DESKTOP_DEMO = "true"
$env:VIDEOINSIGHT_DEMO_OWNER = "DEMO-0815"
$env:VIDEOINSIGHT_CONTROL_PLANE_ENABLED = "false"
$env:VIDEOINSIGHT_CONTROL_PLANE_URL = ""
$env:VIDEOINSIGHT_RUNTIME_ROOT = $projectRoot
$backendDir = Join-Path $projectRoot "project\backend"
$pythonCommand = Join-Path $projectRoot ".venv\Scripts\python.exe"
$serviceLogDirectory = Join-Path $projectRoot "data\logs\services"
New-Item -ItemType Directory -Force -Path $serviceLogDirectory | Out-Null
$logTimestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdoutLogPath = Join-Path $serviceLogDirectory "fastapi-backend-restart-$logTimestamp.stdout.log"
$stderrLogPath = Join-Path $serviceLogDirectory "fastapi-backend-restart-$logTimestamp.stderr.log"

if (-not (Test-Path -LiteralPath $pythonCommand)) {
    $setupScript = Join-Path $projectRoot "scripts\setup_windows.ps1"
    & $setupScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] 运行环境安装失败，后端未启动。"
        exit 1
    }
}

Write-Host "[INFO] Starting FastAPI backend..."
Start-Process -FilePath $pythonCommand `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "2001", "--no-proxy-headers") `
    -WorkingDirectory $backendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLogPath `
    -RedirectStandardError $stderrLogPath

Write-Host "[INFO] Backend logs: $stdoutLogPath / $stderrLogPath"

Start-Sleep -Seconds 4

# Health check
try {
    $root = Invoke-WebRequest -Uri "http://localhost:2001/" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "[SUCCESS] Root '/' returned status: $($root.StatusCode), content length: $($root.Content.Length)"
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    Write-Host "[INFO] Root '/' returned status: $statusCode"
}

try {
    $health = Invoke-WebRequest -Uri "http://localhost:2001/health" -TimeoutSec 5 -ErrorAction Stop
    $healthPayload = $health.Content | ConvertFrom-Json
    if (
        $healthPayload.status -ne "ok" -or
        -not [bool]$healthPayload.desktop_client -or
        -not [bool]$healthPayload.desktop_demo -or
        [bool]$healthPayload.control_plane_enabled
    ) {
        Write-Host "[ERROR] 后端已启动，但桌面运行模式校验失败：$($health.Content)"
        exit 1
    }
    Write-Host "[SUCCESS] Health check: $($health.StatusCode) - 桌面模式已确认"
} catch {
    Write-Host "[ERROR] Health check failed: $($_.Exception.Message)"
    exit 1
}

Write-Host "[DONE]"
