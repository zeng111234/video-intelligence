# Video Intelligence System - Service Startup Script
# Version: 2.0.5
# Date: 2026-07-20

param(
    [switch]$SkipBrowser,
    [switch]$SkipHealthCheck,
    # Uses the real company authentication and credit service while keeping
    # source-preview files in a separate, ignored runtime directory.  This is
    # intentionally opt-in: the normal source launcher remains fully local.
    [switch]$UseCompanyServer,
    # 只启动并管理本地 FastAPI，避免修复/验收后端时触碰已有前端进程。
    [switch]$BackendOnly,
    [string]$CompanyServerUrl = "https://xmt.syszr.cn",
    [string]$CompanyRuntimeRoot = ""
)

# === UTF-8 encoding ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$serviceLogDirectory = Join-Path $projectRoot "data\logs\services"
$pythonCommand = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runtimeRoot = $projectRoot
$companyServerOrigin = ""
$projectEnvPath = Join-Path $projectRoot ".env"
$backendEnvPath = Join-Path $projectRoot "project\backend\.env"

function Import-ProjectEnvironment {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = ([string]$rawLine).Trim()
        if (-not $line -or $line.StartsWith("#") -or $line.IndexOf("=") -lt 1) {
            continue
        }
        $parts = $line -split "=", 2
        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($key -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
            continue
        }
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        # A company-managed machine may already expose its approved supplier
        # credentials as system/user environment variables.  Keep a non-empty
        # inherited value authoritative; use the local .env only as the
        # portable fallback for values that are not configured on this PC.
        $currentValue = [Environment]::GetEnvironmentVariable($key, "Process")
        if ([string]::IsNullOrWhiteSpace($currentValue)) {
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
}

function Write-ConfiguredModeSummary {
    $modeNames = @(
        @{ Key = "ASR_MODE"; Label = "转写" },
        @{ Key = "VIDEO_EDITOR_PROVIDER_MODE"; Label = "剪辑" },
        @{ Key = "CRAWLER_PROVIDER_MODE"; Label = "素材发现" },
        @{ Key = "COPYWRITING_MODE"; Label = "文案" },
        @{ Key = "AVATAR_PROVIDER_MODE"; Label = "数字人" }
    )
    $summary = @($modeNames | ForEach-Object {
        $value = [Environment]::GetEnvironmentVariable($_.Key, "Process")
        if ([string]::IsNullOrWhiteSpace($value)) { $value = "未配置" }
        "$($_.Label)=$value"
    }) -join "，"
    Write-Log "源码实际运行模式：$summary" "INFO"
}

if ($UseCompanyServer) {
    try {
        $companyServerUri = [Uri]$CompanyServerUrl
    } catch {
        throw "公司服务地址无效。请提供纯 HTTPS 域名，例如 https://xmt.syszr.cn。"
    }
    if (
        $companyServerUri.Scheme -ne "https" -or
        -not $companyServerUri.Host -or
        $companyServerUri.UserInfo -or
        $companyServerUri.Query -or
        $companyServerUri.Fragment -or
        $companyServerUri.AbsolutePath -notin @("", "/")
    ) {
        throw "公司服务地址必须是无路径、无账号参数的 HTTPS 域名。"
    }
    $companyServerOrigin = $companyServerUri.GetLeftPart([System.UriPartial]::Authority)
    $runtimeRoot = if ([string]::IsNullOrWhiteSpace($CompanyRuntimeRoot)) {
        Join-Path $projectRoot "build\company-source-preview"
    } else {
        [IO.Path]::GetFullPath($CompanyRuntimeRoot)
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $runtimeRoot "data") | Out-Null
}
$ffmpegCommand = Get-Command "ffmpeg.exe" -ErrorAction SilentlyContinue
$backendPath = if ($ffmpegCommand) {
    "$(Split-Path -Parent $ffmpegCommand.Source);$env:Path"
} else {
    $env:Path
}

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
        # The source checkout defaults to an isolated local demo workspace.
        # -UseCompanyServer is an explicit preview path: it uses a separate
        # ignored runtime directory so a real customer session can never
        # inherit this checkout's existing demo data or desktop-owner binding.
        EnvVars = @{
            PYTHONPATH = $projectRoot
            VIDEOINSIGHT_DESKTOP_CLIENT = "true"
            VIDEOINSIGHT_DESKTOP_DEMO = if ($UseCompanyServer) { "false" } else { "true" }
            VIDEOINSIGHT_DEMO_OWNER = if ($UseCompanyServer) { "" } else { "DEMO-0815" }
            VIDEOINSIGHT_CONTROL_PLANE_ENABLED = if ($UseCompanyServer) { "true" } else { "false" }
            VIDEOINSIGHT_CONTROL_PLANE_URL = $companyServerOrigin
            VIDEOINSIGHT_RUNTIME_ROOT = $runtimeRoot
            AUTH_SESSION_STORE = "sqlite"
            # The source preview must see the same local media tools as the
            # packaged backend, otherwise the UI can be current while formal
            # rendering silently falls back to the old compatibility path.
            Path = $backendPath
        }
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

if ($BackendOnly) {
    $services = @($services | Where-Object { $_.Name -eq "FastAPI Backend" })
}

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
    param(
        [string]$Url,
        [int]$TimeoutSec = 5,
        [switch]$RequireDesktopMode,
        [bool]$ExpectedControlPlane = $false
    )
    try {
        # Backend /health returns JSON and must still pass the desktop-mode
        # boundary checks below. The frontend health URL is an HTML document,
        # so treating every service as a JSON status endpoint falsely reports
        # a healthy Vite process as a startup timeout.
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec -ErrorAction Stop
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
            return $false
        }
        if (-not $RequireDesktopMode) {
            return $true
        }
        $payload = $response.Content | ConvertFrom-Json
        if ($payload.status -ne "ok") {
            return $false
        }
        return (
            [bool]$payload.desktop_client -and
            ([bool]$payload.desktop_demo -eq (-not $ExpectedControlPlane)) -and
            ([bool]$payload.control_plane_enabled -eq $ExpectedControlPlane)
        )
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
    $envPath = if (Test-Path -LiteralPath $projectEnvPath -PathType Leaf) {
        $projectEnvPath
    } elseif (Test-Path -LiteralPath $backendEnvPath -PathType Leaf) {
        $backendEnvPath
    } else {
        $projectEnvPath
    }
    Import-ProjectEnvironment -Path $envPath
    Write-ConfiguredModeSummary
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
        $previousEnvironment = @{}
        try {
            # Start-Process inherits the current environment on Windows
            # PowerShell 5.1.  Apply per-service values only for the spawn,
            # then restore the parent process so the frontend and launcher do
            # not accidentally retain backend-only settings.
            if ($service.EnvVars) {
                foreach ($entry in ($service.EnvVars.GetEnumerator())) {
                    $key = [string]$entry.Key
                    $previousEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
                    [Environment]::SetEnvironmentVariable($key, [string]$entry.Value, "Process")
                }
            }
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
        } finally {
            foreach ($entry in $previousEnvironment.GetEnumerator()) {
                [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
            }
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
                
                $healthReady = if ($name -eq "FastAPI Backend") {
                    Test-ServiceHealth `
                        -Url $service.HealthUrl `
                        -RequireDesktopMode `
                        -ExpectedControlPlane ([bool]$UseCompanyServer)
                } else {
                    Test-ServiceHealth -Url $service.HealthUrl
                }
                if ($healthReady) {
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
    if (-not $BackendOnly) {
        Write-Host "  React Frontend: http://localhost:1001" -ForegroundColor Green
    }
    Write-Host ""
    Write-Host "Runtime workspace:" -ForegroundColor White
    Write-Host "  $runtimeRoot" -ForegroundColor Green
    if ($UseCompanyServer) {
        Write-Host "  Company authentication: $companyServerOrigin" -ForegroundColor Green
        Write-Host "  This preview does not reuse the normal source workspace's local media or login binding." -ForegroundColor Yellow
    } else {
        Write-Host "  Company authentication: disabled (local desktop workspace)" -ForegroundColor Yellow
        Write-Host "  Provider modes come from .env; see the mode summary above." -ForegroundColor Yellow
    }
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
if (-not $SkipBrowser -and -not $BackendOnly) {
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
