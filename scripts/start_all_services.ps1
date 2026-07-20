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

# Service configuration
$services = @(
    @{
        Name = "Streamlit MVP"
        Port = 8501
        HealthUrl = "http://localhost:8501/_stcore/health"
        StartCommand = "python"
        StartArgs = @("-m", "streamlit", "run", "app.py", "--server.port", "8501", "--server.headless", "true")
        WorkingDirectory = $projectRoot
        WindowStyle = "Hidden"
    },
    @{
        Name = "FastAPI Backend"
        Port = 2001
        HealthUrl = "http://localhost:2001/health"
        StartCommand = "python"
        StartArgs = @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "2001")
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

function Stop-ServiceByPort {
    param([int]$Port)
    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
        if ($connections) {
            $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
            foreach ($pid in $pids) {
                if ($pid -and $pid -ne 0) {
                    $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
                    if ($process) {
                        Write-Log "Stopping process: $($process.ProcessName) (PID: $pid, Port: $Port)" "WARN"
                        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                        Start-Sleep -Milliseconds 500
                    }
                }
            }
        }
    } catch {
        # Ignore errors
    }
}

function Stop-ExistingServices {
    Write-Log "Checking and stopping existing services..." "INFO"
    
    foreach ($service in $services) {
        Stop-ServiceByPort -Port $service.Port
    }
}

function Install-Dependencies {
    Write-Log "Checking and installing dependencies..." "INFO"
    
    # Check Python
    Write-Log "Checking Python environment..." "INFO"
    try {
        $pythonVersion = python --version 2>&1
        Write-Log "Python environment OK: $pythonVersion" "SUCCESS"
    } catch {
        Write-Log "Python not installed or not in PATH" "ERROR"
        return $false
    }
    
    # Check Node.js
    Write-Log "Checking Node.js environment..." "INFO"
    try {
        $nodeVersion = node --version 2>&1
        Write-Log "Node.js environment OK: $nodeVersion" "SUCCESS"
    } catch {
        Write-Log "Node.js not installed or not in PATH" "ERROR"
        return $false
    }
    
    # Check FFmpeg (optional)
    Write-Log "Checking FFmpeg environment..." "INFO"
    try {
        $ffmpegVersion = ffmpeg -version 2>&1 | Select-Object -First 1
        Write-Log "FFmpeg environment OK: $ffmpegVersion" "SUCCESS"
    } catch {
        Write-Log "FFmpeg not installed, video processing may be limited" "WARN"
    }
    
    # Install Python dependencies (incremental check)
    Write-Log "Checking Python dependencies..." "INFO"
    $pipCheck = python -c "import streamlit, pandas, pydantic" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Log "Python dependencies already installed" "SUCCESS"
    } else {
        Write-Log "Installing Python dependencies..." "INFO"
        Push-Location $projectRoot
        python -m pip install -r requirements.txt -q
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Python dependencies installed" "SUCCESS"
        } else {
            Write-Log "Python dependencies installation failed" "ERROR"
            Pop-Location
            return $false
        }
        Pop-Location
    }
    
    # Install backend dependencies (incremental check)
    Write-Log "Checking backend dependencies..." "INFO"
    $backendCheck = python -c "import fastapi, uvicorn" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Log "Backend dependencies already installed" "SUCCESS"
    } else {
        Write-Log "Installing backend dependencies..." "INFO"
        Push-Location (Join-Path $projectRoot "project\backend")
        python -m pip install -r requirements.txt -q
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Backend dependencies installed" "SUCCESS"
        } else {
            Write-Log "Backend dependencies installation failed" "ERROR"
            Pop-Location
            return $false
        }
        Pop-Location
    }
    
    # Install frontend dependencies (incremental check)
    $nodeModulesPath = Join-Path $projectRoot "project\frontend\node_modules"
    if (Test-Path $nodeModulesPath) {
        Write-Log "Frontend dependencies already installed" "SUCCESS"
    } else {
        Write-Log "Installing frontend dependencies..." "INFO"
        Push-Location (Join-Path $projectRoot "project\frontend")
        npm install --silent
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Frontend dependencies installed" "SUCCESS"
        } else {
            Write-Log "Frontend dependencies installation failed" "ERROR"
            Pop-Location
            return $false
        }
        Pop-Location
    }
    
    return $true
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
        }
        
        # Start service
        Start-Process -FilePath $service.StartCommand `
                     -ArgumentList $service.StartArgs `
                     -WorkingDirectory $service.WorkingDirectory `
                     -WindowStyle $service.WindowStyle
        
        # Wait for service to start
        Start-Sleep -Seconds 2
        
        # Check if service started successfully
        if (-not $SkipHealthCheck) {
            $maxAttempts = 15
            $attempt = 0
            $serviceStarted = $false
            
            while ($attempt -lt $maxAttempts -and -not $serviceStarted) {
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
                Write-Log "$name startup timeout, check logs" "ERROR"
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
    Write-Host "  Streamlit MVP: http://localhost:8501" -ForegroundColor Green
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