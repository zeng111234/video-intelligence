$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot "project\backend"
$env:PYTHONPATH = $projectRoot
Start-Process -FilePath "python" `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "2001", "--no-proxy-headers") `
    -WorkingDirectory $backendDir `
    -WindowStyle Hidden
