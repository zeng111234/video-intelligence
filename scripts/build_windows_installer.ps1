param(
    [switch]$SkipChecks,
    [switch]$DirectoryOnly,
    [string]$ControlPlaneUrl = "",
    [string]$Version = "0.2.0"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$frontendRoot = Join-Path $repositoryRoot "project\frontend"
$packagingPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$testPython = Join-Path $repositoryRoot "venv\Scripts\python.exe"
$pyInstallerWork = Join-Path $repositoryRoot "build\pyinstaller"
$desktopBackend = Join-Path $frontendRoot "desktop\backend"
$desktopConfig = Join-Path $pyInstallerWork "desktop-control-plane.json"
$releaseConfig = Join-Path $frontendRoot "desktop\release-config.json"
$appIcon = Join-Path $frontendRoot "desktop\icon.ico"

if ($Version -notmatch '^[0-9]+(?:\.[0-9]+){1,3}(?:-[0-9A-Za-z.-]+)?$') {
    throw "版本号格式无效。"
}

if ($ControlPlaneUrl) {
    $parsedControlPlaneUrl = $null
    if (-not [System.Uri]::TryCreate($ControlPlaneUrl, [System.UriKind]::Absolute, [ref]$parsedControlPlaneUrl)) {
        throw "公司服务地址不是有效的绝对 URL。"
    }
    $loopbackHost = $parsedControlPlaneUrl.Host -in @("localhost", "127.0.0.1", "::1")
    if ($parsedControlPlaneUrl.Scheme -ne "https" -and -not ($parsedControlPlaneUrl.Scheme -eq "http" -and $loopbackHost)) {
        throw "正式公司服务地址必须使用 HTTPS；只有本机测试可使用 HTTP。"
    }
    if ($parsedControlPlaneUrl.UserInfo -or $parsedControlPlaneUrl.Query -or $parsedControlPlaneUrl.Fragment -or $parsedControlPlaneUrl.AbsolutePath -ne "/") {
        throw "公司服务地址只能填写 HTTPS 域名，不能包含账号、查询参数、片段或子路径。"
    }
}

if (-not (Test-Path -LiteralPath $packagingPython)) {
    throw "缺少 Windows 打包环境：$packagingPython"
}
if (-not $SkipChecks -and -not (Test-Path -LiteralPath $testPython)) {
    throw "缺少项目测试环境：$testPython"
}

& $packagingPython (Join-Path $PSScriptRoot "generate_app_icon.py") `
    --output $appIcon `
    --png-output (Join-Path $frontendRoot "desktop\icon.png")
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $appIcon)) {
    throw "应用图标生成失败"
}

if (-not $SkipChecks) {
    Push-Location $frontendRoot
    try {
        & npx tsc --noEmit
        if ($LASTEXITCODE -ne 0) { throw "前端类型检查失败" }
        & npm test
        if ($LASTEXITCODE -ne 0) { throw "前端测试失败" }
        & npm run test:update
        if ($LASTEXITCODE -ne 0) { throw "桌面更新安全测试失败" }
    }
    finally {
        Pop-Location
    }

    Push-Location $repositoryRoot
    try {
        & (Join-Path $PSScriptRoot "run_release_python_tests.ps1") -PythonPath $testPython
        if ($LASTEXITCODE -ne 0) { throw "Python 发布测试失败" }
    }
    finally {
        Pop-Location
    }
}

Push-Location $frontendRoot
try {
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "前端构建失败" }
}
finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $pyInstallerWork, $desktopBackend | Out-Null
$desktopConfigPayload = @{
    enabled = [bool]$ControlPlaneUrl
    control_plane_url = $ControlPlaneUrl.TrimEnd('/')
} | ConvertTo-Json
[System.IO.File]::WriteAllText(
    $desktopConfig,
    $desktopConfigPayload,
    (New-Object System.Text.UTF8Encoding($false))
)
$releaseConfigPayload = @{
    current_version = $Version
    control_plane_url = $ControlPlaneUrl.TrimEnd('/')
} | ConvertTo-Json
[System.IO.File]::WriteAllText(
    $releaseConfig,
    $releaseConfigPayload,
    (New-Object System.Text.UTF8Encoding($false))
)

Push-Location $repositoryRoot
try {
    & $packagingPython -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --onedir `
        --name VideoInsightBackend `
        --distpath $desktopBackend `
        --workpath (Join-Path $pyInstallerWork "work") `
        --specpath (Join-Path $pyInstallerWork "spec") `
        --paths $repositoryRoot `
        --add-data "$(Join-Path $repositoryRoot 'project/frontend/dist');project/frontend/dist" `
        --add-data "$(Join-Path $repositoryRoot 'data/templates');data/templates" `
        --add-data "$(Join-Path $repositoryRoot 'scripts/doubao_browser_worker.mjs');scripts" `
        --add-data "$(Join-Path $repositoryRoot 'scripts/doubao_mobile_worker.mjs');scripts" `
        --add-data "$(Join-Path $repositoryRoot 'assets');assets" `
        --add-data "$desktopConfig;config" `
        --hidden-import project.backend.app.services.control_plane_client `
        --hidden-import project.backend.app.services.remote_asr `
        --hidden-import project.backend.app.services.remote_avatar `
        --hidden-import project.backend.app.services.remote_copywriting `
        --hidden-import project.backend.app.services.remote_video_editor `
        --collect-all jieba `
        scripts/desktop_launcher.py
    if ($LASTEXITCODE -ne 0) { throw "本地服务打包失败" }
}
finally {
    Pop-Location
}

Push-Location $frontendRoot
try {
    if ($DirectoryOnly) {
        & npx electron-builder --dir "--config.extraMetadata.version=$Version"
        if ($LASTEXITCODE -ne 0) { throw "Windows 目录版生成失败" }
    }
    else {
        & npx electron-builder --win nsis "--config.extraMetadata.version=$Version"
        if ($LASTEXITCODE -ne 0) { throw "Windows 安装包生成失败" }
    }
}
finally {
    Pop-Location
}

Write-Host "Windows 构建已生成：$frontendRoot\release"
