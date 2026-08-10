# Video Intelligence System - Service Startup Script
# Version: 2.0.5
# Date: 2026-07-20

param(
    [switch]$SkipBrowser,
    [switch]$SkipHealthCheck
)

# === UTF-8 encoding ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$serviceLogDirectory = Join-Path $projectRoot "data\logs\services"
$pythonCommand = Join-Path $projectRoot ".venv\Scripts\python.exe"

# Service configuration
$services = @(
    @{
        Name = "FastAPI Backend"
        Port = 2001
        HealthUrl = "http://localhost:2001/health"
        StartCommand = $pythonCommand
        StartArgs = @("-X", "utf8", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "2001", "--no-proxy-headers")
        WorkingDirectory = Join-Path $projectRoot "project\backend"
        WindowStyle = "Hidden"
        EnvVars = @{ PYTHONPATH = $projectRoot }
    },
    @{
        Name = "React Frontend"
        Port = 1001
        HealthUrl = "http://localhost:1001"
        StartCommand = "npm.cmd"
        StartArgs = @("run", "dev")
        WorkingDirectory = Join-Path $projectRoot "project\frontend"
        WindowStyle = "Hidden"
    }
)

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "HH:mm:ss"
    switch ($Level) {
        "ERROR" { Write-Host "[$timestamp] [ERROR] $Message" -ForegroundColor Red }
        "WARN"  { Write-Host "[$timestamp] [WARN] $Message" -ForegroundColor Yellow }
        "SUCCESS" { Write-Host "[$timestamp] [SUCCESS] $Message" -ForegroundColor Green }
        "INFO"  { Write-Host "[$timestamp] [INFO] $Message" -ForegroundColor Cyan }
        default { Write-Host "[$timestamp] $Message" }
    }
}

function Test-PortAvailable {
    param([int]$Port)
    try {
        $connection = New-Object System.Net.Sockets.TcpClient
        $connection.Connect("localhost", $Port)
        $connection.Close()
        return $false
    } catch {
        return $true
    }
}

function Test-ServiceHealth {
    param([string]$Url, [int]$TimeoutSec = 5)
    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -ErrorAction Stop
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function New-ServiceLogPaths {
    param([string]$Name)

    if (-not (Test-Path -LiteralPath $serviceLogDirectory)) {
        New-Item -ItemType Directory -Path $serviceLogDirectory -Force | Out-Null
    }

    $safeName = (($Name.ToLowerInvariant() -replace "[^a-z0-9]+", "-").Trim("-"))
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    return @{
        Output = Join-Path $serviceLogDirectory "$safeName-$timestamp.stdout.log"
        Error = Join-Path $serviceLogDirectory "$safeName-$timestamp.stderr.log"
    }
}

function Remove-OldServiceLogs {
    param([string]$Name)

    $safeName = (($Name.ToLowerInvariant() -replace "[^a-z0-9]+", "-").Trim("-"))
    $oldLogs = @(Get-ChildItem -LiteralPath $serviceLogDirectory -File -Filter "$safeName-*.log" |
        Sort-Object LastWriteTime -Descending)
    $oldLogs | Select-Object -Skip 20 | Remove-Item -Force
}

function Get-RedactedLogTail {
    param([string]$Path, [int]$Lines = 12)

    if (-not (Test-Path -LiteralPath $Path)) {
        return @()
    }

    return @(Get-Content -LiteralPath $Path -Tail $Lines | ForEach-Object {
        $_ -replace "(?i)(DASHSCOPE_API_KEY|ALIBABA_CLOUD_ACCESS_KEY_SECRET)\\s*=\\s*\\S+", '$1=[已隐藏]' `
           -replace "\\bsk-[A-Za-z0-9._-]+\\b", "[已隐藏]"
    })
}

function Show-ServiceStartupFailure {
    param(
        [string]$Name,
        [System.Diagnostics.Process]$Process,
        [string]$ErrorLogPath
    )

    $details = Get-RedactedLogTail -Path $ErrorLogPath
    if (($details -join "`n") -match "VIDEO_EDITOR_PROVIDER_MODE") {
        Write-Log "剪辑服务配置无效：VIDEO_EDITOR_PROVIDER_MODE 只能填 sandbox 或 aliyun。" "ERROR"
    }

    Write-Log "$Name 已退出（退出码 $($Process.ExitCode)）。错误日志：$ErrorLogPath" "ERROR"
    if ($details.Count -gt 0) {
        Write-Host "[DETAIL] 最近错误（已隐藏密钥）：" -ForegroundColor DarkYellow
        $details | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkYellow }
    }
}

function Stop-ServiceByPort {
    param([int]$Port)
    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
        if ($connections) {
            $processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
            foreach ($processId in $processIds) {
                if ($processId -and $processId -ne 0) {
                    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
                    if ($process) {
                        Write-Log "Stopping process: $($process.ProcessName) (PID: $processId, Port: $Port)" "WARN"
                        Stop-Process -Id $processId -Force -ErrorAction Stop
                        Start-Sleep -Milliseconds 500
                    }
                }
            }
        }
    } catch {
        Write-Log "无法停止端口 $Port 上的旧服务：$($_.Exception.Message)" "ERROR"
    }
}

function Stop-ExistingServices {
    Write-Log "Checking and stopping existing services..." "INFO"
    
    foreach ($service in $services) {
        Stop-ServiceByPort -Port $service.Port
    }
}

function Install-Dependencies {
    Write-Log "正在检查项目运行环境..." "INFO"
    $setupScript = Join-Path $projectRoot "scripts\setup_windows.ps1"
    & $setupScript
    if ($LASTEXITCODE -ne 0) {
        Write-Log "运行环境尚未准备好。请按上面的提示处理后重新运行 start.bat。" "ERROR"
        return $false
    }
    return Test-Path -LiteralPath $pythonCommand
}

function Start-Services {
    Write-Log "Starting services..." "INFO"
    
    # Set PYTHONPATH for backend
    $env:PYTHONPATH = $projectRoot
    
    foreach ($service in $services) {
        $name = $service.Name
        $port = $service.Port
        
        Write-Log "Starting $name (Port: $port)..." "INFO"
        
        # Check if port is available
        if (-not (Test-PortAvailable -Port $port)) {
            Write-Log "Port $port is occupied, stopping existing processes..." "WARN"
            Stop-ExistingServices
            Start-Sleep -Seconds 1
            if (-not (Test-PortAvailable -Port $port)) {
                Write-Log "端口 $port 仍被占用，已停止启动以避免端口冲突。" "ERROR"
                return $false
            }
        }
        
        # Start service and preserve its output for diagnosis if it exits early.
        $logPaths = New-ServiceLogPaths -Name $name
        try {
            $serviceProcess = Start-Process -FilePath $service.StartCommand `
                                             -ArgumentList $service.StartArgs `
                                             -WorkingDirectory $service.WorkingDirectory `
                                             -WindowStyle $service.WindowStyle `
                                             -RedirectStandardOutput $logPaths.Output `
                                             -RedirectStandardError $logPaths.Error `
                                             -PassThru
        } catch {
            Write-Log "$name 无法启动：$($_.Exception.Message)" "ERROR"
            return $false
        }
        
        # Wait for service to start
        Start-Sleep -Seconds 2
        Remove-OldServiceLogs -Name $name
        
        # Check if service started successfully
        if (-not $SkipHealthCheck) {
            $maxAttempts = 15
            $attempt = 0
            $serviceStarted = $false
            
            while ($attempt -lt $maxAttempts -and -not $serviceStarted) {
                if ($serviceProcess.HasExited) {
                    Show-ServiceStartupFailure -Name $name -Process $serviceProcess -ErrorLogPath $logPaths.Error
                    return $false
                }

                $attempt++
                Write-Log "Waiting for $name to start... ($attempt/$maxAttempts)" "INFO"
                
                if (Test-ServiceHealth -Url $service.HealthUrl) {
                    $serviceStarted = $true
                    Write-Log "$name started successfully" "SUCCESS"
                } else {
                    Start-Sleep -Seconds 1
                }
            }
            
            if (-not $serviceStarted) {
                Write-Log "$name startup timeout. 输出日志：$($logPaths.Output)，错误日志：$($logPaths.Error)" "ERROR"
                return $false
            }
        } else {
            Write-Log "$name started (skipped health check)" "SUCCESS"
        }
    }
    
    return $true
}

function Show-ServiceInfo {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  All services started successfully!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Service URLs:" -ForegroundColor White
    Write-Host "  FastAPI Backend: http://localhost:2001" -ForegroundColor Green
    Write-Host "  React Frontend: http://localhost:1001" -ForegroundColor Green
    Write-Host ""
    Write-Host "Database:" -ForegroundColor White
    Write-Host "  SQLite: data/video_intelligence.db" -ForegroundColor Green
    Write-Host ""
    Write-Host "Press Ctrl+C to stop all services" -ForegroundColor Gray
    Write-Host ""
}

# Main execution
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Video Intelligence System - Service Startup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Stop existing services
Stop-ExistingServices

# Install dependencies
if (-not (Install-Dependencies)) {
    Write-Log "Dependency installation failed, startup terminated" "ERROR"
    exit 1
}

# Start services
if (-not (Start-Services)) {
    Write-Log "Service startup failed" "ERROR"
    exit 1
}

# Show service info
Show-ServiceInfo

# Open browser
if (-not $SkipBrowser) {
    Write-Log "Opening browser..." "INFO"
    Start-Process "http://localhost:1001"
}

# Keep script running
Write-Log "Services running, press Ctrl+C to stop all services" "INFO"

# Wait for user interrupt
try {
    while ($true) {
        Start-Sleep -Seconds 1
    }
} finally {
    # Cleanup: stop all services
    Write-Log "Stopping services..." "INFO"
    Stop-ExistingServices
    Write-Log "All services stopped" "SUCCESS"
}
