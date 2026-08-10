param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [string]$Notes = "稳定性与功能更新"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$updatesRoot = Join-Path $repositoryRoot "deploy\control-plane\updates"

if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "正式更新版本号必须使用三段纯数字，例如 0.2.1。"
}
$resolvedInstaller = (Resolve-Path -LiteralPath $InstallerPath).Path
$expectedName = "VideoInsight-$Version-Setup.exe"
if ([System.IO.Path]::GetFileName($resolvedInstaller) -ne $expectedName) {
    throw "安装包文件名必须是 $expectedName。"
}
$installer = Get-Item -LiteralPath $resolvedInstaller
if ($installer.Length -le 0 -or $installer.Length -gt 1GB) {
    throw "安装包大小无效。"
}

New-Item -ItemType Directory -Path $updatesRoot -Force | Out-Null
$publishedInstaller = Join-Path $updatesRoot $expectedName
$manifestPath = Join-Path $updatesRoot "latest.json"
if (Test-Path -LiteralPath $publishedInstaller) {
    throw "同版本更新文件已经存在，禁止覆盖：$publishedInstaller"
}
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    try {
        $currentManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $currentVersion = [string]$currentManifest.version
    }
    catch {
        throw "现有更新清单无法读取，禁止覆盖；请先人工核对：$manifestPath"
    }
    if ($currentVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
        throw "现有更新清单版本无效，禁止覆盖：$currentVersion"
    }
    if ([version]$Version -le [version]$currentVersion) {
        throw "新更新版本必须高于当前 $currentVersion，禁止回退或重复发布。"
    }
}
Copy-Item -LiteralPath $resolvedInstaller -Destination $publishedInstaller
$hash = (Get-FileHash -LiteralPath $publishedInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest = [ordered]@{
    version = $Version
    installer = $expectedName
    sha256 = $hash
    size_bytes = (Get-Item -LiteralPath $publishedInstaller).Length
    notes = $Notes.Substring(0, [Math]::Min($Notes.Length, 500))
    published_at = (Get-Date).ToUniversalTime().ToString("o")
} | ConvertTo-Json
$temporaryManifest = Join-Path $updatesRoot ".latest.json.tmp"
[System.IO.File]::WriteAllText(
    $temporaryManifest,
    $manifest,
    (New-Object System.Text.UTF8Encoding($false))
)
Move-Item -LiteralPath $temporaryManifest -Destination $manifestPath -Force

Write-Output "更新文件已准备：$publishedInstaller"
Write-Output "更新清单已准备：$manifestPath"
Write-Output "把 deploy/control-plane/updates 同步到公司服务器后，新版才会对客户可见。"
