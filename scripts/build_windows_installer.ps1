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
$mediaToolManifest = Join-Path $repositoryRoot "config\windows-media-tools.sha256"

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

if (-not (Test-Path -LiteralPath $mediaToolManifest -PathType Leaf)) {
    throw "缺少 Windows 媒体工具校验清单：$mediaToolManifest"
}
$mediaCommands = @{}
foreach ($toolName in @("ffmpeg.exe", "ffprobe.exe")) {
    $command = Get-Command $toolName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        throw "缺少 Windows 媒体工具：$toolName"
    }
    $mediaCommands[$toolName] = $command.Source
}
$mediaBinDirectory = Split-Path -Parent $mediaCommands["ffmpeg.exe"]
if ((Split-Path -Parent $mediaCommands["ffprobe.exe"]) -ne $mediaBinDirectory) {
    throw "ffmpeg.exe 与 ffprobe.exe 必须来自同一受审目录。"
}
$mediaDistributionRoot = Split-Path -Parent $mediaBinDirectory
$mediaSources = @{
    "LICENSE" = Join-Path $mediaDistributionRoot "LICENSE"
    "README.txt" = Join-Path $mediaDistributionRoot "README.txt"
    "ffmpeg.exe" = $mediaCommands["ffmpeg.exe"]
    "ffprobe.exe" = $mediaCommands["ffprobe.exe"]
}
$expectedMediaHashes = @{}
foreach ($line in Get-Content -LiteralPath $mediaToolManifest -Encoding UTF8) {
    if ($line -match '^([0-9a-f]{64})  (LICENSE|README\.txt|ffmpeg\.exe|ffprobe\.exe)$') {
        $expectedMediaHashes[$Matches[2]] = $Matches[1]
    }
}
if ($expectedMediaHashes.Count -ne 4) {
    throw "Windows 媒体工具校验清单无效。"
}
foreach ($entry in $mediaSources.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $entry.Value -PathType Leaf)) {
        throw "Windows 媒体工具发行文件缺失：$($entry.Key)"
    }
    $actualHash = (Get-FileHash -LiteralPath $entry.Value -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedMediaHashes[$entry.Key]) {
        throw "Windows 媒体工具校验失败：$($entry.Key)"
    }
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
        --add-data "$mediaToolManifest;media" `
        --add-data "$($mediaSources['LICENSE']);media" `
        --add-data "$($mediaSources['README.txt']);media" `
        --add-binary "$($mediaSources['ffmpeg.exe']);media" `
        --add-binary "$($mediaSources['ffprobe.exe']);media" `
        --hidden-import project.backend.app.services.control_plane_client `
        --hidden-import project.backend.app.services.remote_asr `
        --hidden-import project.backend.app.services.remote_avatar `
        --hidden-import project.backend.app.services.remote_copywriting `
        --hidden-import project.backend.app.services.remote_video_editor `
        --exclude-module pyarrow `
        --collect-all jieba `
        scripts/desktop_launcher.py
    if ($LASTEXITCODE -ne 0) { throw "本地服务打包失败" }
}
finally {
    Pop-Location
}

# PyInstaller's pyarrow hook intentionally collects package data, including
# pyarrow's test fixtures.  They are not runtime dependencies and the release
# payload validator correctly rejects them as bundled data.  Remove only this
# generated test-fixture directory before Electron copies the backend.
$pyarrowTestData = Join-Path $desktopBackend "VideoInsightBackend\_internal\pyarrow\tests"
if (Test-Path -LiteralPath $pyarrowTestData) {
    Remove-Item -LiteralPath $pyarrowTestData -Recurse -Force
}

Push-Location $frontendRoot
try {
    # Workaround: write electron-builder output to a fresh directory so that
    # the existing (potentially AV-locked) win-unpacked resources/*.asar are
    # never touched. The downstream scripts (build_final_windows_release.ps1
    # and build_offline_windows_installer.ps1) read the matching `$unpacked`
    # value, so this stays consistent across the release flow.
    $freshOutput = "release\win-unpacked-fresh"
    if ($DirectoryOnly) {
        & npx electron-builder --dir "--config.extraMetadata.version=$Version" "--config.directories.output=$freshOutput"
        if ($LASTEXITCODE -ne 0) { throw "Windows 目录版生成失败" }
    }
    else {
        & npx electron-builder --win nsis "--config.extraMetadata.version=$Version" "--config.directories.output=$freshOutput"
        if ($LASTEXITCODE -ne 0) { throw "Windows 安装包生成失败" }
    }
}
finally {
    Pop-Location
}

Write-Host "Windows 构建已生成：$frontendRoot\release\win-unpacked-fresh"
