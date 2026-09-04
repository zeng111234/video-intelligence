# Windows first-run setup for the React/FastAPI application.

param(
    [switch]$CheckOnly
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRequirements = Join-Path $projectRoot "requirements.txt"
$backendRequirements = Join-Path $projectRoot "project\backend\requirements.txt"
$frontendDirectory = Join-Path $projectRoot "project\frontend"
$frontendLockFile = Join-Path $frontendDirectory "package-lock.json"
$venvDirectory = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvDirectory "Scripts\python.exe"
$setupStateDirectory = Join-Path $projectRoot ".setup"
$pythonStamp = Join-Path $setupStateDirectory "python-runtime-requirements.sha256"
$frontendStamp = Join-Path $setupStateDirectory "frontend-lock.sha256"

function Write-Step {
    param([string]$Message, [string]$Level = "INFO")

    $color = switch ($Level) {
        "OK" { "Green" }
        "WARN" { "Yellow" }
        "ERROR" { "Red" }
        default { "Cyan" }
    }
    Write-Host "[$Level] $Message" -ForegroundColor $color
}

function Get-FileHashValue {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Get-SavedHash {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return ""
    }
    return (Get-Content -LiteralPath $Path -Raw).Trim()
}

function Test-BackendImports {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        return $false
    }
    Push-Location $projectRoot
    try {
        & $venvPython -c "import fastapi, uvicorn, pydantic, httpx, multipart, PIL, jieba, pandas, openpyxl, faster_whisper, edge_tts; import playwright.sync_api; import src.resources" 2>$null
        return $LASTEXITCODE -eq 0
    }
    finally {
        Pop-Location
    }
}

function Test-FrontendInstall {
    $viteCommand = Join-Path $frontendDirectory "node_modules\.bin\vite.cmd"
    return Test-Path -LiteralPath $viteCommand
}

function Test-PortInUse {
    param([int]$Port)

    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", $Port)
        $client.Close()
        return $true
    }
    catch {
        return $false
    }
}

function Update-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $pathParts = @($machinePath, $userPath) | Where-Object { $_ }
    $env:Path = $pathParts -join ";"
}

function Get-CompatiblePython {
    $candidates = @(
        @{ Command = Get-Command "python.exe" -ErrorAction SilentlyContinue; Arguments = @() },
        @{ Command = Get-Command "py.exe" -ErrorAction SilentlyContinue; Arguments = @("-3") }
    )
    foreach ($candidate in $candidates) {
        if (-not $candidate.Command) {
            continue
        }
        $arguments = @($candidate.Arguments)
        $version = & $candidate.Command.Source @arguments -c "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{
                Command = $candidate.Command
                Arguments = $candidate.Arguments
                Version = $version
            }
        }
    }
    return $null
}

function Get-CompatibleNode {
    $node = Get-Command "node.exe" -ErrorAction SilentlyContinue
    $npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if (-not $node -or -not $npm) {
        return $null
    }
    $version = (& $node.Source --version).TrimStart("v")
    $major = 0
    if (-not [int]::TryParse($version.Split(".")[0], [ref]$major) -or $major -lt 18) {
        return $null
    }
    return [pscustomobject]@{
        Node = $node
        Npm = $npm
        Version = $version
    }
}

function Get-WinGetCommand {
    $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if ($winget) {
        return $winget
    }

    # WinGet is delivered by Windows App Installer. If it already exists but
    # has not been registered for this account, ask Windows to register it once.
    try {
        if (Get-AppxPackage "Microsoft.DesktopAppInstaller" -ErrorAction SilentlyContinue) {
            Add-AppxPackage -RegisterByFamilyName -MainPackage "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe" -ErrorAction Stop
            Update-ProcessPath
            $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
        }
    }
    catch {
        $winget = $null
    }
    return $winget
}

function Install-SystemPackage {
    param(
        [string]$PackageId,
        [string]$DisplayName
    )

    $winget = Get-WinGetCommand
    if (-not $winget) {
        Write-Step "电脑缺少 Windows 应用安装程序，无法自动安装 $DisplayName。" "ERROR"
        Write-Host "请先安装微软 App Installer：https://aka.ms/getwinget"
        return $false
    }

    Write-Step "正在自动安装 $DisplayName，Windows 可能会显示一次权限确认..."
    & $winget.Source install --id $PackageId --exact --source winget --silent --force --disable-interactivity --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Step "$DisplayName 自动安装失败，已停止，不会循环重试。" "ERROR"
        return $false
    }
    Update-ProcessPath
    return $true
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  短视频系统 - 运行环境准备" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "项目位置：$projectRoot"
Write-Host ""

$pythonRuntime = Get-CompatiblePython
if (-not $pythonRuntime) {
    if ($CheckOnly -or -not (Install-SystemPackage -PackageId "Python.Python.3.12" -DisplayName "Python 3.12")) {
        exit 1
    }
    $pythonRuntime = Get-CompatiblePython
    if (-not $pythonRuntime) {
        Write-Step "Python 已安装，但当前窗口尚未识别。请关闭窗口后重新双击 start.bat。" "ERROR"
        exit 1
    }
}
$basePythonCommand = $pythonRuntime.Command
$basePythonArguments = $pythonRuntime.Arguments
Write-Step "Python $($pythonRuntime.Version) 可用。" "OK"

$nodeRuntime = Get-CompatibleNode
if (-not $nodeRuntime) {
    if ($CheckOnly -or -not (Install-SystemPackage -PackageId "OpenJS.NodeJS.LTS" -DisplayName "Node.js LTS")) {
        exit 1
    }
    $nodeRuntime = Get-CompatibleNode
    if (-not $nodeRuntime) {
        Write-Step "Node.js 已安装，但当前窗口尚未识别。请关闭窗口后重新双击 start.bat。" "ERROR"
        exit 1
    }
}
$nodeCommand = $nodeRuntime.Node
$npmCommand = $nodeRuntime.Npm
Write-Step "Node.js $($nodeRuntime.Version) 可用。" "OK"

$requirementsHash = @(
    (Get-FileHashValue -Path $runtimeRequirements)
    (Get-FileHashValue -Path $backendRequirements)
) -join ":"
$frontendHash = Get-FileHashValue -Path $frontendLockFile
$backendReady = (Test-BackendImports) -and ((Get-SavedHash -Path $pythonStamp) -eq $requirementsHash)
$frontendReady = (Test-FrontendInstall) -and ((Get-SavedHash -Path $frontendStamp) -eq $frontendHash)

if ($CheckOnly) {
    if (-not $backendReady) {
        Write-Step "后端依赖尚未准备好。请运行 start.bat。" "WARN"
    }
    if (-not $frontendReady) {
        Write-Step "前端依赖尚未准备好。请运行 start.bat。" "WARN"
    }
    if ($backendReady -and $frontendReady) {
        Write-Step "项目依赖已经准备好。" "OK"
        exit 0
    }
    exit 1
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Step "正在创建项目专用 Python 环境（不会修改系统 Python 包）..."
    & $basePythonCommand.Source @basePythonArguments -m venv $venvDirectory
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        Write-Step "项目专用 Python 环境创建失败。" "ERROR"
        exit 1
    }
}

if (-not $backendReady) {
    Write-Step "正在安装 Python 运行依赖，首次安装需要几分钟..."
    foreach ($requirementsPath in @($runtimeRequirements, $backendRequirements)) {
        Write-Step "正在安装 $(Split-Path -Leaf $requirementsPath)..."
        & $venvPython -m pip install --disable-pip-version-check --no-input -r $requirementsPath
        if ($LASTEXITCODE -ne 0) {
            Write-Step "当前 pip 下载源不可用，正在改用官方 PyPI 重试一次..." "WARN"
            & $venvPython -m pip install --disable-pip-version-check --no-input --index-url "https://pypi.org/simple" -r $requirementsPath
            if ($LASTEXITCODE -ne 0) {
                Write-Step "$(Split-Path -Leaf $requirementsPath) 安装失败。请检查网络后再运行一次 start.bat。" "ERROR"
                exit 1
            }
        }
    }
    if (-not (Test-BackendImports)) {
        Write-Step "后端依赖安装完成，但导入检查没有通过。" "ERROR"
        exit 1
    }
}

if (-not (Test-Path -LiteralPath $setupStateDirectory)) {
    New-Item -ItemType Directory -Path $setupStateDirectory -Force | Out-Null
}
Set-Content -LiteralPath $pythonStamp -Value $requirementsHash -Encoding ASCII
Write-Step "后端依赖已准备好。" "OK"

if (-not $frontendReady) {
    if (Test-PortInUse -Port 1001) {
        Write-Step "前端仍在运行，Windows 正在占用安装文件。请先关闭短视频系统，再运行一次 start.bat。" "ERROR"
        exit 1
    }
    Write-Step "正在按锁文件安装前端依赖，首次安装需要几分钟..."
    Push-Location $frontendDirectory
    try {
        & $npmCommand.Source ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            Write-Step "前端依赖安装失败。请检查网络后再运行一次 start.bat。" "ERROR"
            exit 1
        }
    }
    finally {
        Pop-Location
    }
    if (-not (Test-FrontendInstall)) {
        Write-Step "前端依赖安装完成，但 Vite 启动工具不存在。" "ERROR"
        exit 1
    }
}
Set-Content -LiteralPath $frontendStamp -Value $frontendHash -Encoding ASCII
Write-Step "前端依赖已准备好。" "OK"

$localEnvPath = Join-Path $projectRoot ".env"
$exampleEnvPath = Join-Path $projectRoot ".env.example"
if (-not (Test-Path -LiteralPath $localEnvPath)) {
    Copy-Item -LiteralPath $exampleEnvPath -Destination $localEnvPath
    Write-Step "已创建本机配置 .env。需要云服务时，再在配置页或 .env 中填写自己的密钥。" "OK"
}

if (Get-Command "ffmpeg.exe" -ErrorAction SilentlyContinue) {
    Write-Step "FFmpeg 可用。" "OK"
}
else {
    Write-Step "未找到 FFmpeg；系统可以启动，但本地视频处理功能暂不可用。" "WARN"
    Write-Host "下载：https://ffmpeg.org/download.html"
}

$browserPaths = @()
$browserRoots = @(${env:ProgramFiles}, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
foreach ($browserRoot in $browserRoots) {
    $browserPaths += Join-Path $browserRoot "Google\Chrome\Application\chrome.exe"
    $browserPaths += Join-Path $browserRoot "Microsoft\Edge\Application\msedge.exe"
}
$browserPaths = @($browserPaths | Where-Object { Test-Path -LiteralPath $_ })
if ($env:LOCALAPPDATA) {
    $localChrome = Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe"
    if (Test-Path -LiteralPath $localChrome) {
        $browserPaths += $localChrome
    }
}
if ($browserPaths.Count -gt 0) {
    Write-Step "Chrome/Edge 可用于热点发现。" "OK"
}
else {
    Write-Step "未找到 Chrome 或 Edge；系统可以启动，但热点发现浏览器功能暂不可用。" "WARN"
}

Write-Host ""
Write-Step "环境准备完成。以后只需双击 start.bat。" "OK"
exit 0
