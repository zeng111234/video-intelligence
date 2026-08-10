[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [Parameter()]
    [string]$OutputDirectory = '交付文件'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$resolvedOutputDirectory = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $OutputDirectory))
}
[System.IO.Directory]::CreateDirectory($resolvedOutputDirectory) | Out-Null

$archivePath = Join-Path $resolvedOutputDirectory "VideoInsight-control-plane-$Version.zip"
if (Test-Path -LiteralPath $archivePath) {
    throw "部署包已存在，为避免误发旧包请先核对并显式移走：$archivePath"
}

$explicitFiles = @(
    '.dockerignore',
    'PRODUCTION_RELEASE_CHECKLIST.md',
    'project/__init__.py',
    'project/backend/__init__.py'
)
$sourceTrees = @(
    'project/backend/app',
    'src',
    'database',
    'deploy/control-plane'
)
$forbiddenDirectoryNames = @(
    '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache',
    'data', 'logs', 'backups', 'caddy-data', 'caddy-config', 'updates'
)
$forbiddenExtensions = @(
    '.db', '.sqlite', '.sqlite3', '.log', '.pyc', '.pyo',
    '.mp3', '.wav', '.mp4', '.mov', '.mkv', '.webm'
)

$candidateFiles = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
foreach ($relativePath in $explicitFiles) {
    $fullPath = Join-Path $repositoryRoot $relativePath
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        throw "部署包缺少必要文件：$relativePath"
    }
    $candidateFiles.Add((Get-Item -LiteralPath $fullPath))
}
foreach ($relativeTree in $sourceTrees) {
    $fullPath = Join-Path $repositoryRoot $relativeTree
    if (-not (Test-Path -LiteralPath $fullPath -PathType Container)) {
        throw "部署包缺少必要目录：$relativeTree"
    }
    foreach ($file in Get-ChildItem -LiteralPath $fullPath -Recurse -File -Force) {
        $candidateFiles.Add($file)
    }
}

$rootPrefix = $repositoryRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
$entries = [System.Collections.Generic.List[object]]::new()
foreach ($file in $candidateFiles) {
    $fullPath = [System.IO.Path]::GetFullPath($file.FullName)
    if (-not $fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "发现仓库之外的文件，已停止：$fullPath"
    }
    $relativePath = $fullPath.Substring($rootPrefix.Length).Replace('\', '/')
    $segments = $relativePath.Split('/')
    if ($segments | Where-Object { $forbiddenDirectoryNames -contains $_ }) {
        continue
    }
    if ($forbiddenExtensions -contains $file.Extension.ToLowerInvariant()) {
        continue
    }
    if ($file.Name -eq '.env' -or ($file.Name.StartsWith('.env.') -and $file.Name -ne '.env.example')) {
        continue
    }
    $entries.Add([pscustomobject]@{
        Source = $fullPath
        Entry = $relativePath
    })
}

$entries = @($entries | Sort-Object Entry -Unique)
if ($entries.Count -lt 20) {
    throw "部署包文件数量异常（$($entries.Count)），已停止。"
}

$sensitivePath = $entries | Where-Object {
    $_.Entry -match '(^|/)(\.env|data|logs|backups|caddy-data|caddy-config|updates)(/|$)' -or
    $_.Entry -match '\.(db|sqlite3?|log|mp3|wav|mp4|mov|mkv|webm)$'
}
if ($sensitivePath) {
    throw "部署包包含不允许的运行文件：$($sensitivePath.Entry -join ', ')"
}

$avatarManifestPath = Join-Path $repositoryRoot 'data/avatar_assets/shuying_cloud.json'
if (-not (Test-Path -LiteralPath $avatarManifestPath -PathType Leaf)) {
    throw '缺少已训练数字人资产清单；为避免客户重新付费训练，已停止生成服务器包。'
}
try {
    $avatarManifest = Get-Content -LiteralPath $avatarManifestPath -Raw | ConvertFrom-Json
} catch {
    throw '已训练数字人资产清单无法读取，已停止生成服务器包。'
}
$sharedAvatarAssets = @(
    $avatarManifest.assets |
        Where-Object {
            $_.authorized -eq $true -and
            $_.status -eq 'ready' -and
            $_.kind -in @('avatar', 'voice') -and
            -not [string]::IsNullOrWhiteSpace([string]$_.provider_asset_id)
        } |
        ForEach-Object {
            $record = [ordered]@{
                asset_id = [string]$_.asset_id
                kind = [string]$_.kind
                name = [string]$_.name
                preview_url = if ($_.kind -eq 'avatar' -and ([string]$_.preview_url).StartsWith('https://')) { [string]$_.preview_url } else { $null }
                authorized = $true
                preview_type = if ($_.kind -eq 'avatar') { [string]$_.preview_type } else { 'audio' }
                status = 'ready'
                status_message = '已训练完成，可直接使用；无需重新训练。'
                source_type = if ($_.kind -eq 'voice') { 'custom_clone' } else { 'custom' }
                shared = $true
                provider_asset_id = [string]$_.provider_asset_id
            }
            [pscustomobject]$record
        }
)
$sharedKinds = @($sharedAvatarAssets | ForEach-Object { $_.kind } | Sort-Object -Unique)
if ($sharedAvatarAssets.Count -lt 2 -or $sharedKinds -notcontains 'avatar' -or $sharedKinds -notcontains 'voice') {
    throw '未找到一组已授权且训练完成的形象和声音；为避免重新付费训练，已停止生成服务器包。'
}
$sharedAssetPayload = @{ assets = $sharedAvatarAssets } |
    ConvertTo-Json -Depth 8
$sharedAssetEntry = 'deploy/control-plane/bootstrap/avatar_assets/shuying_cloud.json'

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::Open(
    $archivePath,
    [System.IO.Compression.ZipArchiveMode]::Create
)
try {
    foreach ($entry in $entries) {
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive,
            $entry.Source,
            $entry.Entry,
            [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
    $bootstrapEntry = $archive.CreateEntry(
        $sharedAssetEntry,
        [System.IO.Compression.CompressionLevel]::Optimal
    )
    $writer = [System.IO.StreamWriter]::new(
        $bootstrapEntry.Open(),
        [System.Text.UTF8Encoding]::new($false)
    )
    try {
        $writer.Write($sharedAssetPayload)
    } finally {
        $writer.Dispose()
    }
} finally {
    $archive.Dispose()
}

$archiveInfo = Get-Item -LiteralPath $archivePath
$digest = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Output "公司服务器部署包已生成：$archivePath"
Write-Output "文件数：$($entries.Count + 1)"
Write-Output "大小：$($archiveInfo.Length) bytes"
Write-Output "SHA256：$digest"
Write-Output '已带入训练完成的共享形象与声音编号；不会重新提交训练。'
Write-Output '包内不含 .env、供应商密钥、数据库、日志、训练样本、媒体、备份或桌面安装包。'
