param(
    [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$unpacked = Join-Path $repoRoot "project\frontend\release\win-unpacked"
$releaseRoot = Join-Path $repoRoot "project\frontend\release"
$buildRoot = Join-Path $repoRoot "build"
$workspace = Join-Path $buildRoot "offline-installer"
$output = Join-Path $releaseRoot "VideoInsight-$Version-Setup.exe"
$appIcon = Join-Path $repoRoot "project\frontend\desktop\icon.ico"

if (-not (Test-Path -LiteralPath (Join-Path $unpacked "VideoInsight.exe"))) {
    throw "请先生成 win-unpacked 目录版。"
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "正式版本号必须使用三段纯数字，例如 0.2.1。"
}
if (-not (Test-Path -LiteralPath $appIcon)) {
    throw "缺少应用图标，请先生成 Windows 目录版。"
}

$resolvedBuildRoot = [System.IO.Path]::GetFullPath($buildRoot).TrimEnd('\') + '\'
$resolvedWorkspace = [System.IO.Path]::GetFullPath($workspace)
if (-not $resolvedWorkspace.StartsWith($resolvedBuildRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "打包临时目录不安全：$resolvedWorkspace"
}
if (Test-Path -LiteralPath $workspace) {
    Remove-Item -LiteralPath $workspace -Recurse -Force
}
New-Item -ItemType Directory -Path $workspace -Force | Out-Null
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null

Copy-Item -LiteralPath (Join-Path $PSScriptRoot "install_windows_desktop.ps1") -Destination $workspace
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "uninstall_windows_desktop.ps1") -Destination $workspace
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "verify_windows_install.ps1") -Destination $workspace
$utf8WithBom = New-Object System.Text.UTF8Encoding($true)
foreach ($scriptName in @("install_windows_desktop.ps1", "uninstall_windows_desktop.ps1", "verify_windows_install.ps1")) {
    $scriptPath = Join-Path $workspace $scriptName
    $scriptContent = [System.IO.File]::ReadAllText($scriptPath, [System.Text.Encoding]::UTF8)
    [System.IO.File]::WriteAllText($scriptPath, $scriptContent, $utf8WithBom)
}

$payload = Join-Path $workspace "payload.zip"
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $unpacked,
    $payload,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $false
)

if (Test-Path -LiteralPath $output) {
    throw "同版本安装包已经存在，禁止覆盖：$output"
}
$versionFile = Join-Path $workspace "version.txt"
[System.IO.File]::WriteAllText($versionFile, $Version, [System.Text.Encoding]::ASCII)
$csc = "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $csc)) {
    throw "缺少 Windows .NET 编译器：$csc"
}
$installScript = Join-Path $workspace "install_windows_desktop.ps1"
$uninstallScript = Join-Path $workspace "uninstall_windows_desktop.ps1"
$verifierScript = Join-Path $workspace "verify_windows_install.ps1"
$bootstrapSource = Join-Path $PSScriptRoot "offline_installer_bootstrap.cs"
& $csc `
    /nologo `
    /target:winexe `
    /platform:anycpu `
    /optimize+ `
    "/win32icon:$appIcon" `
    "/out:$output" `
    "/resource:$payload,VideoInsight.Payload" `
    "/resource:$installScript,VideoInsight.InstallScript" `
    "/resource:$uninstallScript,VideoInsight.UninstallScript" `
    "/resource:$verifierScript,VideoInsight.VerifierScript" `
    "/resource:$versionFile,VideoInsight.Version" `
    /reference:System.Windows.Forms.dll `
    $bootstrapSource
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $output)) {
    throw ".NET 离线安装器生成失败。"
}

$item = Get-Item -LiteralPath $output
Copy-Item `
    -LiteralPath $verifierScript `
    -Destination (Join-Path $releaseRoot "verify_windows_install.ps1") `
    -Force
Copy-Item `
    -LiteralPath (Join-Path $repoRoot "CLEAN_PC_ACCEPTANCE.md") `
    -Destination (Join-Path $releaseRoot "CLEAN_PC_ACCEPTANCE.md") `
    -Force
Copy-Item `
    -LiteralPath (Join-Path $repoRoot "PRODUCTION_RELEASE_CHECKLIST.md") `
    -Destination (Join-Path $releaseRoot "PRODUCTION_RELEASE_CHECKLIST.md") `
    -Force
$checksumTargets = @(
    $output,
    (Join-Path $releaseRoot "verify_windows_install.ps1")
)
$checksumLines = foreach ($target in $checksumTargets) {
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $target
    "{0}  {1}" -f $hash.Hash, (Split-Path -Leaf $target)
}
[System.IO.File]::WriteAllLines(
    (Join-Path $releaseRoot "SHA256SUMS.txt"),
    $checksumLines,
    [System.Text.Encoding]::ASCII
)
Write-Output "离线安装器已生成：$($item.FullName)"
Write-Output "大小：$([math]::Round($item.Length / 1MB, 2)) MiB"
