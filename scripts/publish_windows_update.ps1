param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [string]$Notes = "稳定性与功能更新"
)

$ErrorActionPreference = "Stop"

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $stream = [System.IO.File]::OpenRead($LiteralPath)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Get-Sha256HexForBytes {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-ExistingPathAttributesForPublish {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $entryExists = [System.IO.File]::Exists($LiteralPath) -or [System.IO.Directory]::Exists($LiteralPath)
    if (-not $entryExists) {
        $parentPath = [System.IO.Path]::GetDirectoryName($LiteralPath)
        if (-not [string]::IsNullOrWhiteSpace($parentPath) -and [System.IO.Directory]::Exists($parentPath)) {
            $leafName = [System.IO.Path]::GetFileName($LiteralPath)
            foreach ($entryPath in [System.IO.Directory]::EnumerateFileSystemEntries($parentPath)) {
                if ([string]::Equals(
                    [System.IO.Path]::GetFileName($entryPath),
                    $leafName,
                    [System.StringComparison]::OrdinalIgnoreCase
                )) {
                    $entryExists = $true
                    break
                }
            }
        }
    }
    if (-not $entryExists) {
        return $null
    }
    try {
        return [System.IO.File]::GetAttributes($LiteralPath)
    }
    catch {
        throw "无法安全检查发布路径（可能是失效链接）：$LiteralPath"
    }
}

function Assert-NoReparsePointsForPublishPath {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$CandidatePath,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $resolvedRepositoryRoot = [System.IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\', '/')
    $repositoryPrefix = $resolvedRepositoryRoot + [System.IO.Path]::DirectorySeparatorChar
    $resolvedCandidatePath = [System.IO.Path]::GetFullPath($CandidatePath)
    if (-not $resolvedCandidatePath.StartsWith($repositoryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label 越出仓库，禁止发布：$resolvedCandidatePath"
    }

    $relativeCandidatePath = $resolvedCandidatePath.Substring($resolvedRepositoryRoot.Length)
    $pathParts = @($relativeCandidatePath -split '[\\/]' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $currentPath = $resolvedRepositoryRoot
    $pathsToCheck = @($currentPath)
    foreach ($pathPart in $pathParts) {
        $currentPath = Join-Path $currentPath $pathPart
        $pathsToCheck += $currentPath
    }
    for ($pathIndex = 0; $pathIndex -lt $pathsToCheck.Count; $pathIndex += 1) {
        $pathToCheck = $pathsToCheck[$pathIndex]
        $attributes = Get-ExistingPathAttributesForPublish -LiteralPath $pathToCheck
        if ($null -eq $attributes) {
            break
        }
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label 或其仓库内祖先不能是链接或联接点：$pathToCheck"
        }
        if (
            $pathIndex -lt ($pathsToCheck.Count - 1) -and
            ($attributes -band [System.IO.FileAttributes]::Directory) -eq 0
        ) {
            throw "$Label 的祖先不是目录：$pathToCheck"
        }
    }
}

function Get-EmbeddedStableVersion {
    param(
        [AllowEmptyString()][string]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $match = [System.Text.RegularExpressions.Regex]::Match(
        $Value,
        '^\s*([0-9]+)\.([0-9]+)\.([0-9]+)(?:\.0)?\s*$'
    )
    if (-not $match.Success) {
        throw "安装包缺少有效的 $Label，禁止发布。"
    }
    return "{0}.{1}.{2}" -f $match.Groups[1].Value, $match.Groups[2].Value, $match.Groups[3].Value
}

function Write-ReleaseRegistryAtomically {
    param(
        [Parameter(Mandatory = $true)][string]$RegistryPath,
        [Parameter(Mandatory = $true)][object]$Registry
    )
    Assert-NoReparsePointsForPublishPath `
        -RepositoryRoot $repositoryRoot `
        -CandidatePath $RegistryPath `
        -Label "正式版本登记表"
    $parentPath = Split-Path -Parent $RegistryPath
    $fileName = Split-Path -Leaf $RegistryPath
    $nonce = [guid]::NewGuid().ToString("N")
    $temporaryRegistryPath = Join-Path $parentPath ".$fileName.$nonce.tmp"
    $backupRegistryPath = Join-Path $parentPath ".$fileName.$nonce.bak"
    $registryStream = $null
    $registryCommitted = $false
    try {
        $registryJson = ($Registry | ConvertTo-Json -Depth 20) + [Environment]::NewLine
        $registryBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($registryJson)
        $registryStream = New-Object System.IO.FileStream(
            $temporaryRegistryPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $registryStream.Write($registryBytes, 0, $registryBytes.Length)
        $registryStream.Flush($true)
        $registryStream.Dispose()
        $registryStream = $null
        [System.IO.File]::Replace(
            $temporaryRegistryPath,
            $RegistryPath,
            $backupRegistryPath,
            $true
        )
        $registryCommitted = $true
    }
    finally {
        if ($null -ne $registryStream) { $registryStream.Dispose() }
        if (Test-Path -LiteralPath $temporaryRegistryPath) {
            try { Remove-Item -LiteralPath $temporaryRegistryPath -Force }
            catch { Write-Warning "正式版本登记临时文件未能清理：$temporaryRegistryPath" }
        }
        if (Test-Path -LiteralPath $backupRegistryPath) {
            try { Remove-Item -LiteralPath $backupRegistryPath -Force }
            catch {
                if ($registryCommitted) {
                    Write-Warning "正式版本登记已原子提交，但旧备份未能清理：$backupRegistryPath"
                }
                else { Write-Warning "正式版本登记旧备份未能清理：$backupRegistryPath" }
            }
        }
    }
}

function Get-NextStablePatch {
    param([Parameter(Mandatory = $true)][string]$CompletedVersion)
    $parts = $CompletedVersion.Split('.')
    if ($parts.Count -ne 3 -or [int64]$parts[2] -ge [int]::MaxValue) {
        throw "无法安全计算 $CompletedVersion 的下一候选版本。"
    }
    return "{0}.{1}.{2}" -f $parts[0], $parts[1], ([int64]$parts[2] + 1)
}

function Get-LockedManifestSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [switch]$KeepOpen
    )

    $stream = New-Object System.IO.FileStream(
        $ManifestPath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    try {
        if ($stream.Length -le 0 -or $stream.Length -gt 1MB) {
            throw "现有更新清单大小无效。"
        }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -le 0) { throw "无法完整读取现有更新清单。" }
            $offset += $read
        }
        try {
            $manifest = (New-Object System.Text.UTF8Encoding($false)).GetString($bytes) | ConvertFrom-Json
        }
        catch {
            throw "现有更新清单无法读取，禁止发布；请先人工核对。"
        }
        $result = [pscustomobject]@{
            Bytes = $bytes
            Sha256 = Get-Sha256HexForBytes -Bytes $bytes
            Manifest = $manifest
            Stream = $null
        }
        if ($KeepOpen) {
            $stream.Position = 0
            $result.Stream = $stream
            $stream = $null
        }
        return $result
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Assert-CurrentManifestMatchesRelease {
    param(
        [Parameter(Mandatory = $true)][object]$Manifest,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion,
        [Parameter(Mandatory = $true)][string]$ExpectedInstaller,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256,
        [Parameter(Mandatory = $true)][int64]$ExpectedSize
    )
    if (
        [string]$Manifest.version -ne $ExpectedVersion -or
        [string]$Manifest.installer -ne $ExpectedInstaller -or
        [string]$Manifest.sha256 -ne $ExpectedSha256 -or
        [int64]$Manifest.size_bytes -ne $ExpectedSize
    ) {
        throw "现有 latest.json 与已烧录的 Windows 安装包不一致，禁止自动修复或覆盖。"
    }
}

function Commit-LatestManifestAtomically {
    param(
        [Parameter(Mandatory = $true)][string]$TemporaryPath,
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [AllowNull()][string]$ExpectedExistingSha256
    )

    if ([string]::IsNullOrWhiteSpace($ExpectedExistingSha256)) {
        [System.IO.File]::Move($TemporaryPath, $ManifestPath)
        return $null
    }

    $backupPath = "$ManifestPath.$([guid]::NewGuid().ToString('N')).previous"
    $rollbackPath = "$ManifestPath.$([guid]::NewGuid().ToString('N')).rollback"
    Assert-NoReparsePointsForPublishPath -RepositoryRoot $repositoryRoot -CandidatePath $backupPath -Label "更新清单原子备份"
    Assert-NoReparsePointsForPublishPath -RepositoryRoot $repositoryRoot -CandidatePath $rollbackPath -Label "更新清单竞态恢复文件"
    [System.IO.File]::Replace($TemporaryPath, $ManifestPath, $backupPath, $true)
    $actualPreviousHash = Get-Sha256Hex -LiteralPath $backupPath
    if ($actualPreviousHash -ne $ExpectedExistingSha256) {
        try {
            [System.IO.File]::Replace($backupPath, $ManifestPath, $rollbackPath, $true)
        }
        catch {
            throw "检测到 latest.json 非协作并发改写，且无法自动恢复；并发文件保留在 $backupPath，请人工核对。"
        }
        throw "检测到 latest.json 非协作并发改写；已恢复并发写入内容，本次发布失败且版本仍被烧录。"
    }
    return $backupPath
}

function Copy-InstallerToPartialCreateNew {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$PartialPath
    )
    $inputStream = New-Object System.IO.FileStream(
        $SourcePath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $outputStream = $null
    try {
        $outputStream = New-Object System.IO.FileStream(
            $PartialPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $inputStream.CopyTo($outputStream)
        $outputStream.Flush($true)
    }
    finally {
        if ($null -ne $outputStream) { $outputStream.Dispose() }
        $inputStream.Dispose()
    }
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$updatesRoot = Join-Path $repositoryRoot "deploy\control-plane\updates"
$releaseRegistryRelativePath = "deploy/control-plane/release_versions.json"
$releaseRegistryPath = Join-Path $repositoryRoot "deploy\control-plane\release_versions.json"
$registryLockPath = "$releaseRegistryPath.lock"
$expectedName = "VideoInsight-$Version-Setup.exe"
$publishedInstaller = Join-Path $updatesRoot $expectedName
$manifestPath = Join-Path $updatesRoot "latest.json"
$temporaryInstaller = Join-Path `
    $updatesRoot `
    (".{0}.{1}.partial" -f $expectedName, [guid]::NewGuid().ToString("N"))
$temporaryManifest = Join-Path `
    $updatesRoot `
    (".latest.{0}.tmp" -f [guid]::NewGuid().ToString("N"))
$registryLock = $null
$ownsRegistryLock = $false
$manifestStream = $null
$publishedStream = $null
$sourceStream = $null
$manifestBackupPath = $null
$publishStateStarted = $false
$releaseCompleted = $false
$temporaryInstallerCreated = $false
$temporaryManifestCreated = $false

if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "正式更新版本号必须使用三段纯数字，例如 0.2.1。"
}
foreach ($publishPathCheck in @(
    @{ Path = $releaseRegistryPath; Label = "正式版本登记表" },
    @{ Path = $registryLockPath; Label = "正式发布锁" },
    @{ Path = $updatesRoot; Label = "更新目录" },
    @{ Path = $publishedInstaller; Label = "更新安装包" },
    @{ Path = $temporaryInstaller; Label = "更新安装包临时文件" },
    @{ Path = $manifestPath; Label = "更新清单" },
    @{ Path = $temporaryManifest; Label = "更新清单临时文件" }
)) {
    Assert-NoReparsePointsForPublishPath `
        -RepositoryRoot $repositoryRoot `
        -CandidatePath $publishPathCheck.Path `
        -Label $publishPathCheck.Label
}
& git -C $repositoryRoot ls-files --error-unmatch -- $releaseRegistryRelativePath *> $null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $releaseRegistryPath -PathType Leaf)) {
    throw "正式版本登记表必须存在并已纳入 Git 跟踪：$releaseRegistryPath"
}
$resolvedInstaller = (Resolve-Path -LiteralPath $InstallerPath).Path
if ([System.IO.Path]::GetFileName($resolvedInstaller) -ne $expectedName) {
    throw "安装包文件名必须是 $expectedName。"
}
$installer = Get-Item -LiteralPath $resolvedInstaller
if ($installer.Length -le 0 -or $installer.Length -gt 1GB) {
    throw "安装包大小无效。"
}
$versionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($resolvedInstaller)
$embeddedFileVersion = Get-EmbeddedStableVersion -Value $versionInfo.FileVersion -Label "FileVersion"
$embeddedProductVersion = Get-EmbeddedStableVersion -Value $versionInfo.ProductVersion -Label "ProductVersion"
if ($embeddedFileVersion -ne $Version -or $embeddedProductVersion -ne $Version) {
    throw "安装包内嵌版本与发布版本不一致：FileVersion=$embeddedFileVersion，ProductVersion=$embeddedProductVersion。"
}
$sourceHash = Get-Sha256Hex -LiteralPath $resolvedInstaller
$nextCandidate = Get-NextStablePatch -CompletedVersion $Version

try {
    try {
        $registryLock = New-Object System.IO.FileStream(
            $registryLockPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $ownsRegistryLock = $true
    }
    catch {
        throw "另一个正式构建或发布进程正在运行，或上次发布异常中断；禁止并行发布。"
    }

    try { $releaseRegistry = Get-Content -LiteralPath $releaseRegistryPath -Raw | ConvertFrom-Json }
    catch { throw "正式版本登记表无法读取：$releaseRegistryPath" }
    $topLevelProperties = (@($releaseRegistry.PSObject.Properties.Name) | Sort-Object) -join ','
    if (
        $topLevelProperties -notin @(
            "completed_releases,current_candidate,release_in_progress,schema_version,superseded_releases,used_versions",
            "completed_releases,current_candidate,release_in_progress,schema_version,used_versions"
        ) -or
        [int]$releaseRegistry.schema_version -ne 2
    ) {
        throw "正式版本登记表 schema_version 必须为 2 且字段必须完整。"
    }
    if (
        -not ($releaseRegistry.used_versions -is [System.Array]) -or
        $releaseRegistry.used_versions.Count -eq 0 -or
        -not ($releaseRegistry.completed_releases -is [System.Array]) -or
        ($null -ne $releaseRegistry.PSObject.Properties['superseded_releases'] -and -not ($releaseRegistry.superseded_releases -is [System.Array]))
    ) {
        throw "正式版本登记表 used_versions/completed_releases 类型无效。"
    }
    $usedVersions = @($releaseRegistry.used_versions | ForEach-Object { [string]$_ })
    $uniqueUsedVersions = @($usedVersions | Sort-Object -Unique)
    if (
        $uniqueUsedVersions.Count -ne $usedVersions.Count -or
        @($usedVersions | Where-Object { $_ -eq $Version }).Count -ne 1 -or
        $null -ne $releaseRegistry.current_candidate -or
        $null -eq $releaseRegistry.release_in_progress
    ) {
        throw "版本 $Version 未处于已烧录、待发布的 Windows 状态。"
    }
    foreach ($usedVersion in $usedVersions) {
        if ($usedVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
            throw "正式版本登记表包含无效的已使用版本：$usedVersion"
        }
    }
    $highestUsedVersion = $usedVersions | ForEach-Object { [version]$_ } | Sort-Object -Descending | Select-Object -First 1
    if ([version]$Version -ne $highestUsedVersion) {
        throw "发布版本必须是 used_versions 中唯一的最高版本。"
    }
    $inProgress = $releaseRegistry.release_in_progress
    $inProgressProperties = (@($inProgress.PSObject.Properties.Name) | Sort-Object) -join ','
    $startingWindowsState = [string]$inProgress.windows_state
    if (
        $inProgressProperties -ne "control_plane_sha256,installer_sha256,server_state,source_commit,version,windows_state" -or
        [string]$inProgress.version -ne $Version -or
        [string]$inProgress.source_commit -notmatch '^[0-9a-f]{40}$' -or
        [string]$inProgress.server_state -ne "ready" -or
        [string]$inProgress.control_plane_sha256 -notmatch '^[0-9a-f]{64}$' -or
        $startingWindowsState -notin @("built", "publishing") -or
        [string]$inProgress.installer_sha256 -ne $sourceHash
    ) {
        throw "只允许发布 release_in_progress 已登记的 exact Windows 安装包哈希。"
    }
    $completedReleases = @($releaseRegistry.completed_releases)
    $completedVersions = @()
    foreach ($completedRelease in $completedReleases) {
        $completedProperties = (@($completedRelease.PSObject.Properties.Name) | Sort-Object) -join ','
        if (
            $completedProperties -ne "control_plane_sha256,installer_sha256,server_state,source_commit,version,windows_state" -or
            [string]$completedRelease.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
            [string]$completedRelease.source_commit -notmatch '^[0-9a-f]{40}$' -or
            [string]$completedRelease.server_state -ne "ready" -or
            [string]$completedRelease.control_plane_sha256 -notmatch '^[0-9a-f]{64}$' -or
            [string]$completedRelease.windows_state -ne "complete" -or
            [string]$completedRelease.installer_sha256 -notmatch '^[0-9a-f]{64}$' -or
            $usedVersions -notcontains [string]$completedRelease.version
        ) {
            throw "正式版本登记表包含无效的 completed_releases 记录。"
        }
        $completedVersions += [string]$completedRelease.version
    }
    if (
        @($completedVersions | Sort-Object -Unique).Count -ne $completedVersions.Count -or
        $completedVersions -contains $Version
    ) {
        throw "版本 $Version 已存在完成记录，禁止重复发布。"
    }

    $sourceAttributes = [System.IO.File]::GetAttributes($resolvedInstaller)
    if (($sourceAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "客户安装包不能是链接或联接点。"
    }
    $sourceStream = New-Object System.IO.FileStream(
        $resolvedInstaller,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    if ($sourceStream.Length -ne $installer.Length -or (Get-Sha256Hex -LiteralPath $resolvedInstaller) -ne $sourceHash) {
        throw "客户安装包在发布门禁后发生变化，禁止发布。"
    }

    if (Test-Path -LiteralPath $updatesRoot) {
        $updatesRootItem = Get-Item -LiteralPath $updatesRoot -Force
        if (-not $updatesRootItem.PSIsContainer) {
            throw "更新目录路径已被非目录占用，禁止发布：$updatesRoot"
        }
    }
    $publishedAttributes = Get-ExistingPathAttributesForPublish -LiteralPath $publishedInstaller
    if ($startingWindowsState -eq "built" -and $null -ne $publishedAttributes) {
        throw "同版本更新文件已经存在或被链接占用，禁止覆盖：$publishedInstaller"
    }
    if ($null -ne $publishedAttributes -and ($publishedAttributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
        throw "更新安装包路径已被目录占用，禁止发布：$publishedInstaller"
    }

    $manifestAttributes = Get-ExistingPathAttributesForPublish -LiteralPath $manifestPath
    $existingManifestSnapshot = $null
    $manifestAlreadyCommitted = $false
    if ($null -ne $manifestAttributes) {
        if (($manifestAttributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
            throw "更新清单路径已被目录占用，禁止发布：$manifestPath"
        }
        $existingManifestSnapshot = Get-LockedManifestSnapshot -ManifestPath $manifestPath
        $currentVersion = [string]$existingManifestSnapshot.Manifest.version
        if ($currentVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
            throw "现有更新清单版本无效，禁止覆盖。"
        }
        if ([version]$currentVersion -gt [version]$Version) {
            throw "新更新版本必须高于当前 $currentVersion，禁止回退发布。"
        }
        if ([version]$currentVersion -eq [version]$Version) {
            if ($startingWindowsState -ne "publishing") {
                throw "latest.json 已包含同版本；非恢复态禁止重复发布。"
            }
            $manifestAlreadyCommitted = $true
        }
    }

    if ($startingWindowsState -eq "built") {
        $inProgress.windows_state = "publishing"
        Write-ReleaseRegistryAtomically -RegistryPath $releaseRegistryPath -Registry $releaseRegistry
    }
    $publishStateStarted = $true

    [System.IO.Directory]::CreateDirectory($updatesRoot) | Out-Null
    foreach ($publishPathCheck in @(
        @{ Path = $updatesRoot; Label = "更新目录" },
        @{ Path = $publishedInstaller; Label = "更新安装包" },
        @{ Path = $temporaryInstaller; Label = "更新安装包临时文件" },
        @{ Path = $manifestPath; Label = "更新清单" },
        @{ Path = $temporaryManifest; Label = "更新清单临时文件" }
    )) {
        Assert-NoReparsePointsForPublishPath `
            -RepositoryRoot $repositoryRoot `
            -CandidatePath $publishPathCheck.Path `
            -Label $publishPathCheck.Label
    }

    if ($null -eq (Get-ExistingPathAttributesForPublish -LiteralPath $publishedInstaller)) {
        Copy-InstallerToPartialCreateNew -SourcePath $resolvedInstaller -PartialPath $temporaryInstaller
        $temporaryInstallerCreated = $true
        $partialInstallerItem = Get-Item -LiteralPath $temporaryInstaller
        $partialInstallerHash = Get-Sha256Hex -LiteralPath $temporaryInstaller
        if ($partialInstallerItem.Length -ne $installer.Length -or $partialInstallerHash -ne $sourceHash) {
            throw "更新安装包临时副本与客户安装包不一致。"
        }
        [System.IO.File]::Move($temporaryInstaller, $publishedInstaller)
        $temporaryInstallerCreated = $false
    }
    elseif ($startingWindowsState -ne "publishing") {
        throw "更新安装包在检查后被其他进程占用；禁止覆盖。"
    }
    $publishedStream = New-Object System.IO.FileStream(
        $publishedInstaller,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $publishedItem = Get-Item -LiteralPath $publishedInstaller
    $hash = Get-Sha256Hex -LiteralPath $publishedInstaller
    if ($publishedItem.Length -ne $installer.Length -or $hash -ne $sourceHash) {
        throw "更新服务器副本与客户安装包的大小或 SHA256 不一致。"
    }

    if ($manifestAlreadyCommitted) {
        Assert-CurrentManifestMatchesRelease `
            -Manifest $existingManifestSnapshot.Manifest `
            -ExpectedVersion $Version `
            -ExpectedInstaller $expectedName `
            -ExpectedSha256 $hash `
            -ExpectedSize $publishedItem.Length
    }
    else {
        $manifestJson = [ordered]@{
            version = $Version
            installer = $expectedName
            sha256 = $hash
            size_bytes = $publishedItem.Length
            notes = $Notes.Substring(0, [Math]::Min($Notes.Length, 500))
            published_at = (Get-Date).ToUniversalTime().ToString("o")
        } | ConvertTo-Json
        $manifestBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($manifestJson)
        $temporaryStream = $null
        try {
            $temporaryStream = New-Object System.IO.FileStream(
                $temporaryManifest,
                [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None
            )
            $temporaryManifestCreated = $true
            $temporaryStream.Write($manifestBytes, 0, $manifestBytes.Length)
            $temporaryStream.Flush($true)
        }
        finally {
            if ($null -ne $temporaryStream) { $temporaryStream.Dispose() }
        }
        $expectedExistingHash = $null
        if ($null -ne $existingManifestSnapshot) {
            $expectedExistingHash = [string]$existingManifestSnapshot.Sha256
        }
        $manifestBackupPath = Commit-LatestManifestAtomically `
            -TemporaryPath $temporaryManifest `
            -ManifestPath $manifestPath `
            -ExpectedExistingSha256 $expectedExistingHash
        $temporaryManifestCreated = $false
    }

    $lockedManifest = Get-LockedManifestSnapshot -ManifestPath $manifestPath -KeepOpen
    $manifestStream = $lockedManifest.Stream
    Assert-CurrentManifestMatchesRelease `
        -Manifest $lockedManifest.Manifest `
        -ExpectedVersion $Version `
        -ExpectedInstaller $expectedName `
        -ExpectedSha256 $hash `
        -ExpectedSize $publishedItem.Length

    $completedEntry = [ordered]@{
        version = $Version
        source_commit = [string]$inProgress.source_commit
        server_state = "ready"
        control_plane_sha256 = [string]$inProgress.control_plane_sha256
        windows_state = "complete"
        installer_sha256 = $hash
    }
    $releaseRegistry.completed_releases = @($completedReleases + $completedEntry)
    $releaseRegistry.release_in_progress = $null
    $releaseRegistry.current_candidate = $nextCandidate
    Write-ReleaseRegistryAtomically -RegistryPath $releaseRegistryPath -Registry $releaseRegistry
    $releaseCompleted = $true

    if (-not [string]::IsNullOrWhiteSpace($manifestBackupPath) -and (Test-Path -LiteralPath $manifestBackupPath)) {
        try { Remove-Item -LiteralPath $manifestBackupPath -Force }
        catch { Write-Warning "旧 latest.json 原子备份未能清理，请人工核对：$manifestBackupPath" }
    }
}
catch {
    $originalFailure = $_
    if ($ownsRegistryLock -and $publishStateStarted -and -not $releaseCompleted) {
        Write-Warning "版本 $Version 保持 publishing 烧录态；只允许用登记的 exact SHA256 安装包恢复，禁止重建或复用版本。"
    }
    throw $originalFailure
}
finally {
    if ($temporaryInstallerCreated -and (Test-Path -LiteralPath $temporaryInstaller)) {
        try { Remove-Item -LiteralPath $temporaryInstaller -Force }
        catch { Write-Warning "发布临时安装包未能清理，请人工核对：$temporaryInstaller" }
    }
    if ($temporaryManifestCreated -and (Test-Path -LiteralPath $temporaryManifest)) {
        try { Remove-Item -LiteralPath $temporaryManifest -Force }
        catch { Write-Warning "发布临时清单未能清理，请人工核对：$temporaryManifest" }
    }
    if ($null -ne $manifestStream) { $manifestStream.Dispose() }
    if ($null -ne $publishedStream) { $publishedStream.Dispose() }
    if ($null -ne $sourceStream) { $sourceStream.Dispose() }
    if ($null -ne $registryLock) { $registryLock.Dispose() }
    if ($ownsRegistryLock -and (Test-Path -LiteralPath $registryLockPath)) {
        try { Remove-Item -LiteralPath $registryLockPath -Force }
        catch { Write-Warning "正式发布锁未能清理，请人工核对：$registryLockPath" }
    }
}

Write-Output "更新文件已准备：$publishedInstaller"
Write-Output "更新清单已准备：$manifestPath"
Write-Output "把 deploy/control-plane/updates 同步到公司服务器后，新版才会对客户可见。"
