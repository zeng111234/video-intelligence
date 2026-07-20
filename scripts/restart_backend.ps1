# === UTF-8 encoding ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "SilentlyContinue"

# Kill ALL python processes on port 2001
Write-Host "[INFO] Killing all processes on port 2001..."
$maxAttempts = 5
for ($i = 0; $i -lt $maxAttempts; $i++) {
    $connections = Get-NetTCPConnection -LocalPort 2001 -ErrorAction SilentlyContinue
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
$finalCheck = Get-NetTCPConnection -LocalPort 2001 -ErrorAction SilentlyContinue
if ($finalCheck) {
    Write-Host "[ERROR] Port 2001 still occupied after $maxAttempts attempts"
    exit 1
}

Write-Host "[SUCCESS] Port 2001 is now free"

# Start backend
$projectRoot = "C:\Users\zeng\Desktop\video"
$env:PYTHONPATH = $projectRoot
$backendDir = Join-Path $projectRoot "project\backend"

Write-Host "[INFO] Starting FastAPI backend..."
Start-Process -FilePath "python" `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "2001") `
    -WorkingDirectory $backendDir `
    -WindowStyle Hidden

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
    Write-Host "[SUCCESS] Health check: $($health.StatusCode) - $($health.Content)"
} catch {
    Write-Host "[ERROR] Health check failed: $($_.Exception.Message)"
}

Write-Host "[DONE]"
