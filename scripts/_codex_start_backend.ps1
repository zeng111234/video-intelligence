$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot "project\backend"
$env:PYTHONPATH = $projectRoot
Start-Process -FilePath "python" `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "2001") `
    -WorkingDirectory $backendDir `
    -WindowStyle Hidden
