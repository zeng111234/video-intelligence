param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"

function Assert-NoReparsePointsInTree {
    param(
        [Parameter(Mandatory = $true)][string]$RootPath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $rootItem = Get-Item -LiteralPath $RootPath -Force
    if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label 不能是链接或联接点：$RootPath"
    }
    $pendingDirectories = New-Object 'System.Collections.Generic.Stack[string]'
    $pendingDirectories.Push($rootItem.FullName)
    while ($pendingDirectories.Count -gt 0) {
        $currentDirectory = $pendingDirectories.Pop()
        foreach ($entryPath in [System.IO.Directory]::EnumerateFileSystemEntries($currentDirectory)) {
            $attributes = [System.IO.File]::GetAttributes($entryPath)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label 包含链接或联接点，禁止继续：$entryPath"
            }
            if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                $pendingDirectories.Push($entryPath)
            }
        }
    }
}

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

function Get-EmbeddedStableVersionForOfflineBuild {
    param(
        [AllowEmptyString()][string]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $match = [regex]::Match($Value, '^\s*([0-9]+)\.([0-9]+)\.([0-9]+)(?:\.0)?\s*$')
    if (-not $match.Success) { throw "离线安装器缺少有效的 $Label。" }
    return "{0}.{1}.{2}" -f $match.Groups[1].Value, $match.Groups[2].Value, $match.Groups[3].Value
}

function Get-ExistingPathAttributesForOfflineBuild {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $exists = [System.IO.File]::Exists($LiteralPath) -or [System.IO.Directory]::Exists($LiteralPath)
    if (-not $exists) {
        $parentPath = [System.IO.Path]::GetDirectoryName($LiteralPath)
        if (-not [string]::IsNullOrWhiteSpace($parentPath) -and [System.IO.Directory]::Exists($parentPath)) {
            $leafName = [System.IO.Path]::GetFileName($LiteralPath)
            foreach ($entryPath in [System.IO.Directory]::EnumerateFileSystemEntries($parentPath)) {
                if ([string]::Equals([System.IO.Path]::GetFileName($entryPath), $leafName, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $exists = $true
                    break
                }
            }
        }
    }
    if (-not $exists) { return $null }
    try { return [System.IO.File]::GetAttributes($LiteralPath) }
    catch { throw "无法安全检查离线构建路径（可能是失效链接）：$LiteralPath" }
}

function Assert-NoReparsePointsForOfflinePath {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$CandidatePath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $resolvedRoot = [System.IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\', '/')
    $resolvedCandidate = [System.IO.Path]::GetFullPath($CandidatePath)
    $rootPrefix = $resolvedRoot + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedCandidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label 越出仓库：$resolvedCandidate"
    }
    $relativePath = $resolvedCandidate.Substring($resolvedRoot.Length)
    $currentPath = $resolvedRoot
    $paths = @($currentPath)
    foreach ($part in @($relativePath -split '[\\/]' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
        $currentPath = Join-Path $currentPath $part
        $paths += $currentPath
    }
    for ($index = 0; $index -lt $paths.Count; $index += 1) {
        $attributes = Get-ExistingPathAttributesForOfflineBuild -LiteralPath $paths[$index]
        if ($null -eq $attributes) { break }
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label 或其祖先不能是链接或联接点：$($paths[$index])"
        }
        if ($index -lt ($paths.Count - 1) -and ($attributes -band [System.IO.FileAttributes]::Directory) -eq 0) {
            throw "$Label 的祖先不是目录：$($paths[$index])"
        }
    }
}

function Assert-NoReparsePointsInAbsoluteOfflinePath {
    param(
        [Parameter(Mandatory = $true)][string]$CandidatePath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $resolvedCandidate = [System.IO.Path]::GetFullPath($CandidatePath)
    $pathRoot = [System.IO.Path]::GetPathRoot($resolvedCandidate).TrimEnd('\', '/')
    $relativePath = $resolvedCandidate.Substring($pathRoot.Length)
    $currentPath = $pathRoot + [System.IO.Path]::DirectorySeparatorChar
    foreach ($part in @($relativePath -split '[\\/]' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
        $currentPath = Join-Path $currentPath $part
        $attributes = Get-ExistingPathAttributesForOfflineBuild -LiteralPath $currentPath
        if ($null -eq $attributes) { break }
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label 或其祖先不能是链接或联接点：$currentPath"
        }
    }
}

function Get-TrustedCSharpCompilerPath {
    $runtimeDirectory = [System.Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()
    if ([string]::IsNullOrWhiteSpace($runtimeDirectory)) {
        throw "无法从 Windows .NET 运行时确定 C# 编译器目录。"
    }
    $compilerPath = [System.IO.Path]::GetFullPath((Join-Path $runtimeDirectory "csc.exe"))
    Assert-NoReparsePointsInAbsoluteOfflinePath -CandidatePath $compilerPath -Label "Windows .NET C# 编译器"
    if (-not (Test-Path -LiteralPath $compilerPath -PathType Leaf)) {
        throw "缺少 Windows .NET 编译器：$compilerPath"
    }
    $runtimeDirectoryInfo = New-Object System.IO.DirectoryInfo($runtimeDirectory.TrimEnd('\', '/'))
    $frameworkDirectoryInfo = $runtimeDirectoryInfo.Parent
    $microsoftNetDirectoryInfo = if ($null -ne $frameworkDirectoryInfo) { $frameworkDirectoryInfo.Parent } else { $null }
    $trustedWindowsDirectoryInfo = if ($null -ne $microsoftNetDirectoryInfo) { $microsoftNetDirectoryInfo.Parent } else { $null }
    if (
        $null -eq $trustedWindowsDirectoryInfo -or
        $null -eq $microsoftNetDirectoryInfo -or
        -not [string]::Equals($microsoftNetDirectoryInfo.Name, "Microsoft.NET", [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Windows .NET 运行时目录不属于系统 Microsoft.NET：$runtimeDirectory"
    }
    $securityModulePath = Join-Path $PSHOME "Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1"
    Assert-NoReparsePointsInAbsoluteOfflinePath -CandidatePath $securityModulePath -Label "Windows 签名验证模块"
    $originalSystemRoot = $env:SystemRoot
    $originalWindir = $env:windir
    try {
        $env:SystemRoot = $trustedWindowsDirectoryInfo.FullName
        $env:windir = $trustedWindowsDirectoryInfo.FullName
        Import-Module -Name $securityModulePath -ErrorAction Stop
        $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $compilerPath
    }
    finally {
        $env:SystemRoot = $originalSystemRoot
        $env:windir = $originalWindir
    }
    if (
        [string]$signature.Status -ne "Valid" -or
        $null -eq $signature.SignerCertificate -or
        [string]$signature.SignerCertificate.Subject -notmatch '(^|, )O=Microsoft Corporation(,|$)'
    ) {
        throw "Windows .NET C# 编译器缺少有效的 Microsoft 签名：$compilerPath"
    }
    return $compilerPath
}

function Assert-OfflineBuildLifecycle {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion,
        [Parameter(Mandatory = $true)][string]$RegistryPath,
        [Parameter(Mandatory = $true)][string]$AttemptMarkerPath,
        [Parameter(Mandatory = $true)][string]$OfflineStartedMarkerPath
    )
    $registryRelativePath = "deploy/control-plane/release_versions.json"
    & git -C $RepositoryRoot ls-files --error-unmatch -- $registryRelativePath *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "离线正式构建要求受 Git 跟踪的 release_versions.json。"
    }
    $dirtyEntries = @(& git -C $RepositoryRoot status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0 -or $dirtyEntries.Count -ne 0) {
        throw "离线正式构建前工作区必须完全干净。"
    }
    $lockPath = "$RegistryPath.lock"
    Assert-NoReparsePointsForOfflinePath -RepositoryRoot $RepositoryRoot -CandidatePath $RegistryPath -Label "正式版本登记表"
    Assert-NoReparsePointsForOfflinePath -RepositoryRoot $RepositoryRoot -CandidatePath $lockPath -Label "正式版本锁"
    Assert-NoReparsePointsForOfflinePath -RepositoryRoot $RepositoryRoot -CandidatePath $AttemptMarkerPath -Label "Windows 尝试标记"
    Assert-NoReparsePointsForOfflinePath -RepositoryRoot $RepositoryRoot -CandidatePath $OfflineStartedMarkerPath -Label "离线构建一次性标记"
    $lockStream = $null
    $ownsLock = $false
    try {
        try {
            $lockStream = New-Object System.IO.FileStream(
                $lockPath,
                [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None
            )
            $ownsLock = $true
        }
        catch { throw "另一个正式构建或发布进程正在运行，禁止离线打包。" }
        $expectedLockStatus = "?? deploy/control-plane/release_versions.json.lock"
        $lockedStatus = @(& git -C $RepositoryRoot status --porcelain --untracked-files=all)
        if (
            $LASTEXITCODE -ne 0 -or
            $lockedStatus.Count -ne 1 -or
            [string]$lockedStatus[0] -ne $expectedLockStatus
        ) {
            throw "离线正式构建锁定后工作区发生变化，禁止打包。"
        }
        try { $registry = Get-Content -LiteralPath $RegistryPath -Raw | ConvertFrom-Json }
        catch { throw "正式版本登记表无法读取。" }
        $topProperties = (@($registry.PSObject.Properties.Name) | Sort-Object) -join ','
        if (
            $topProperties -ne "completed_releases,current_candidate,release_in_progress,schema_version,used_versions" -or
            [int]$registry.schema_version -ne 2 -or
            $null -ne $registry.current_candidate -or
            $null -eq $registry.release_in_progress
        ) { throw "离线正式构建要求 schema 2 的已烧录 release_in_progress。" }
        $usedVersions = @($registry.used_versions | ForEach-Object { [string]$_ })
        if (
            -not ($registry.used_versions -is [System.Array]) -or
            $usedVersions.Count -eq 0 -or
            @($usedVersions | Sort-Object -Unique).Count -ne $usedVersions.Count -or
            @($usedVersions | Where-Object { $_ -eq $ExpectedVersion }).Count -ne 1
        ) { throw "离线构建版本必须在 used_versions 中恰好烧录一次。" }
        foreach ($usedVersion in $usedVersions) {
            if ($usedVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { throw "used_versions 包含无效版本。" }
        }
        $highestUsed = $usedVersions | ForEach-Object { [version]$_ } | Sort-Object -Descending | Select-Object -First 1
        $release = $registry.release_in_progress
        $releaseProperties = (@($release.PSObject.Properties.Name) | Sort-Object) -join ','
        if (
            [version]$ExpectedVersion -ne $highestUsed -or
            $releaseProperties -ne "control_plane_sha256,installer_sha256,server_state,source_commit,version,windows_state" -or
            [string]$release.version -ne $ExpectedVersion -or
            [string]$release.source_commit -notmatch '^[0-9a-f]{40}$' -or
            [string]$release.server_state -ne "ready" -or
            [string]$release.control_plane_sha256 -notmatch '^[0-9a-f]{64}$' -or
            [string]$release.windows_state -ne "attempted" -or
            -not [string]::IsNullOrWhiteSpace([string]$release.installer_sha256)
        ) { throw "离线安装器只允许在当前版本 attempted 状态构建一次。" }
        if (-not (Test-Path -LiteralPath $AttemptMarkerPath -PathType Leaf)) {
            throw "缺少当前版本 Windows attempted 标记，禁止绕过 final 脚本单独重包。"
        }
        $expectedMarker = "version=$ExpectedVersion`nsource_commit=$([string]$release.source_commit)`n"
        $actualMarker = [System.IO.File]::ReadAllText($AttemptMarkerPath, [System.Text.Encoding]::UTF8).Replace("`r`n", "`n")
        if ($actualMarker -ne $expectedMarker) {
            throw "Windows attempted 标记与版本账本不一致，禁止打包。"
        }
        $recoveryOnly = $false
        $offlineStartedMarkerAttributes = Get-ExistingPathAttributesForOfflineBuild -LiteralPath $OfflineStartedMarkerPath
        if ($null -ne $offlineStartedMarkerAttributes) {
            if (
                ($offlineStartedMarkerAttributes -band [System.IO.FileAttributes]::Directory) -ne 0 -or
                ($offlineStartedMarkerAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
            ) {
                throw "离线构建一次性标记不是安全普通文件，禁止继续。"
            }
            $actualOfflineStartedMarker = [System.IO.File]::ReadAllText(
                $OfflineStartedMarkerPath,
                [System.Text.Encoding]::UTF8
            ).Replace("`r`n", "`n")
            if ($actualOfflineStartedMarker -ne $expectedMarker) {
                throw "离线构建一次性标记与版本账本不一致，禁止继续。"
            }
            $recoveryOnly = $true
        }
        else {
            $offlineStartedStream = $null
            try {
                $offlineStartedStream = New-Object System.IO.FileStream(
                    $OfflineStartedMarkerPath,
                    [System.IO.FileMode]::CreateNew,
                    [System.IO.FileAccess]::Write,
                    [System.IO.FileShare]::None
                )
                $offlineStartedBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($expectedMarker)
                $offlineStartedStream.Write($offlineStartedBytes, 0, $offlineStartedBytes.Length)
                $offlineStartedStream.Flush($true)
            }
            finally {
                if ($null -ne $offlineStartedStream) { $offlineStartedStream.Dispose() }
            }
        }
        & git -C $RepositoryRoot merge-base --is-ancestor ([string]$release.source_commit) HEAD *> $null
        if ($LASTEXITCODE -ne 0) { throw "release source_commit 不是当前 HEAD 的祖先。" }
        $sourceDiff = @(& git -C $RepositoryRoot diff --name-only "$([string]$release.source_commit)..HEAD")
        if ($LASTEXITCODE -ne 0 -or $sourceDiff.Count -ne 1 -or [string]$sourceDiff[0] -ne $registryRelativePath) {
            throw "source_commit 之后只允许提交 release_versions.json。"
        }
        return [pscustomobject]@{
            Stream = $lockStream
            Path = $lockPath
            RecoveryOnly = $recoveryOnly
        }
    }
    catch {
        if ($null -ne $lockStream) { $lockStream.Dispose() }
        if ($ownsLock -and (Test-Path -LiteralPath $lockPath)) {
            try { Remove-Item -LiteralPath $lockPath -Force }
            catch { Write-Warning "失败后的正式版本锁未能清理：$lockPath" }
        }
        throw
    }
}

function Write-OfflineReleaseRegistryAtomically {
    param(
        [Parameter(Mandatory = $true)][string]$RegistryPath,
        [Parameter(Mandatory = $true)][object]$Registry
    )
    Assert-NoReparsePointsForOfflinePath -RepositoryRoot $repoRoot -CandidatePath $RegistryPath -Label "正式版本登记表"
    $parentPath = [System.IO.Path]::GetDirectoryName($RegistryPath)
    $fileName = [System.IO.Path]::GetFileName($RegistryPath)
    $nonce = [guid]::NewGuid().ToString("N")
    $temporaryPath = Join-Path $parentPath ".$fileName.$nonce.tmp"
    $backupPath = Join-Path $parentPath ".$fileName.$nonce.bak"
    $stream = $null
    $committed = $false
    try {
        $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes(
            (($Registry | ConvertTo-Json -Depth 20) + [Environment]::NewLine)
        )
        $stream = New-Object System.IO.FileStream(
            $temporaryPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        $stream.Dispose()
        $stream = $null
        [System.IO.File]::Replace($temporaryPath, $RegistryPath, $backupPath, $true)
        $committed = $true
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if (Test-Path -LiteralPath $temporaryPath) {
            try { Remove-Item -LiteralPath $temporaryPath -Force }
            catch { Write-Warning "离线构建登记临时文件未能清理：$temporaryPath" }
        }
        if (Test-Path -LiteralPath $backupPath) {
            try { Remove-Item -LiteralPath $backupPath -Force }
            catch {
                if ($committed) { Write-Warning "离线构建状态已提交，但旧登记备份未清理：$backupPath" }
                else { Write-Warning "离线构建旧登记备份未清理：$backupPath" }
            }
        }
    }
}

function Complete-OfflineArtifactRegistry {
    param(
        [Parameter(Mandatory = $true)][string]$RegistryPath,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion,
        [Parameter(Mandatory = $true)][string]$InstallerSha256
    )
    if ($InstallerSha256 -notmatch '^[0-9a-f]{64}$') { throw "安装包 SHA256 无效。" }
    try { $registry = Get-Content -LiteralPath $RegistryPath -Raw | ConvertFrom-Json }
    catch { throw "无法读取待完成的正式版本登记表。" }
    $release = $registry.release_in_progress
    if (
        $null -eq $release -or
        [string]$release.version -ne $ExpectedVersion -or
        [string]$release.server_state -ne "ready" -or
        [string]$release.windows_state -ne "attempted" -or
        -not [string]::IsNullOrWhiteSpace([string]$release.installer_sha256)
    ) {
        throw "离线制品提交时 release_in_progress 已变化，禁止登记。"
    }
    $release.windows_state = "built"
    $release.installer_sha256 = $InstallerSha256
    Write-OfflineReleaseRegistryAtomically -RegistryPath $RegistryPath -Registry $registry
}

function Set-OfflineArtifactFailed {
    param(
        [Parameter(Mandatory = $true)][string]$RegistryPath,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion
    )
    try { $registry = Get-Content -LiteralPath $RegistryPath -Raw | ConvertFrom-Json }
    catch { throw "无法读取待烧录失败状态的正式版本登记表。" }
    $release = $registry.release_in_progress
    if ($null -eq $release -or [string]$release.version -ne $ExpectedVersion) {
        throw "离线构建失败时 release_in_progress 已变化，无法安全烧录失败状态。"
    }
    $windowsState = [string]$release.windows_state
    if ($windowsState -eq "built") { return "built" }
    if ($windowsState -eq "failed") { return "failed" }
    if (
        $windowsState -ne "attempted" -or
        -not [string]::IsNullOrWhiteSpace([string]$release.installer_sha256)
    ) {
        throw "离线构建失败时 Windows 状态不允许自动烧录 failed：$windowsState"
    }
    $release.windows_state = "failed"
    Write-OfflineReleaseRegistryAtomically -RegistryPath $RegistryPath -Registry $registry
    return "failed"
}

function Try-RecoverCommittedOfflineArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$OutputDirectory,
        [Parameter(Mandatory = $true)][string[]]$DeliveryPaths,
        [Parameter(Mandatory = $true)][string]$InstallerPath,
        [Parameter(Mandatory = $true)][string]$ExpectedInstallerName,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion,
        [Parameter(Mandatory = $true)][string]$RegistryPath,
        [switch]$ValidateOnly
    )
    if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) { return $false }
    $items = @(Get-ChildItem -LiteralPath $OutputDirectory -Force)
    if ($items.Count -eq 0) { return $false }
    if ($items.Count -ne $DeliveryPaths.Count) {
        throw "发现不完整或含额外文件的旧交付目录，禁止重建或覆盖。"
    }
    foreach ($deliveryPath in $DeliveryPaths) {
        $attributes = Get-ExistingPathAttributesForOfflineBuild -LiteralPath $deliveryPath
        if (
            $null -eq $attributes -or
            ($attributes -band [System.IO.FileAttributes]::Directory) -ne 0 -or
            ($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) { throw "旧交付目录不满足 exact artifact 恢复条件：$deliveryPath" }
    }
    $versionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($InstallerPath)
    $fileVersion = Get-EmbeddedStableVersionForOfflineBuild -Value $versionInfo.FileVersion -Label "FileVersion"
    $productVersion = Get-EmbeddedStableVersionForOfflineBuild -Value $versionInfo.ProductVersion -Label "ProductVersion"
    if ($fileVersion -ne $ExpectedVersion -or $productVersion -ne $ExpectedVersion) {
        throw "旧交付安装包版本不一致，禁止恢复。"
    }
    $installerHash = Get-Sha256Hex -LiteralPath $InstallerPath
    $verifierPath = Join-Path $OutputDirectory "verify_windows_install.ps1"
    $cleanPcAcceptancePath = Join-Path $OutputDirectory "CLEAN_PC_ACCEPTANCE.md"
    $productionChecklistPath = Join-Path $OutputDirectory "PRODUCTION_RELEASE_CHECKLIST.md"
    $trustedDeliverySources = @(
        @{
            Delivery = $verifierPath
            Source = (Join-Path $repoRoot "scripts\verify_windows_install.ps1")
            Name = "verify_windows_install.ps1"
        },
        @{
            Delivery = $cleanPcAcceptancePath
            Source = (Join-Path $repoRoot "CLEAN_PC_ACCEPTANCE.md")
            Name = "CLEAN_PC_ACCEPTANCE.md"
        },
        @{
            Delivery = $productionChecklistPath
            Source = (Join-Path $repoRoot "PRODUCTION_RELEASE_CHECKLIST.md")
            Name = "PRODUCTION_RELEASE_CHECKLIST.md"
        }
    )
    foreach ($trustedDeliverySource in $trustedDeliverySources) {
        Assert-NoReparsePointsForOfflinePath `
            -RepositoryRoot $repoRoot `
            -CandidatePath $trustedDeliverySource.Source `
            -Label "受信任交付源文件"
        if (
            -not (Test-Path -LiteralPath $trustedDeliverySource.Source -PathType Leaf) -or
            (Get-Sha256Hex -LiteralPath $trustedDeliverySource.Delivery) -ne
                (Get-Sha256Hex -LiteralPath $trustedDeliverySource.Source)
        ) {
            throw "旧交付文件与当前受信任源码不一致，禁止恢复：$($trustedDeliverySource.Name)"
        }
    }
    $checksumPath = Join-Path $OutputDirectory "SHA256SUMS.txt"
    if ((Get-Item -LiteralPath $checksumPath).Length -gt 1MB) {
        throw "旧交付 SHA256SUMS 过大，禁止恢复。"
    }
    $expectedChecksumLines = @(
        ("{0}  {1}" -f $installerHash, $ExpectedInstallerName),
        ("{0}  {1}" -f (Get-Sha256Hex -LiteralPath $verifierPath), "verify_windows_install.ps1"),
        ("{0}  {1}" -f (Get-Sha256Hex -LiteralPath $cleanPcAcceptancePath), "CLEAN_PC_ACCEPTANCE.md"),
        ("{0}  {1}" -f (Get-Sha256Hex -LiteralPath $productionChecklistPath), "PRODUCTION_RELEASE_CHECKLIST.md")
    )
    $actualChecksumLines = @([System.IO.File]::ReadAllLines($checksumPath, [System.Text.Encoding]::ASCII))
    if (($actualChecksumLines -join "`n") -ne ($expectedChecksumLines -join "`n")) {
        throw "旧交付 SHA256SUMS 与 exact artifact 不一致，禁止恢复。"
    }
    if (-not $ValidateOnly) {
        Complete-OfflineArtifactRegistry `
            -RegistryPath $RegistryPath `
            -ExpectedVersion $ExpectedVersion `
            -InstallerSha256 $installerHash
    }
    return $true
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$unpacked = Join-Path $repoRoot "project\frontend\release\win-unpacked"
$buildRoot = Join-Path $repoRoot "build"
$workspace = Join-Path $buildRoot ("offline-installer-{0}-{1}" -f $Version, [guid]::NewGuid().ToString("N"))
$appIcon = Join-Path $repoRoot "project\frontend\desktop\icon.ico"
$releaseRegistryPath = Join-Path $repoRoot "deploy\control-plane\release_versions.json"
$attemptMarkerPath = Join-Path $buildRoot ".windows-release-$Version.attempted"
$offlineStartedMarkerPath = Join-Path $buildRoot ".windows-release-$Version.offline-started"

if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "正式版本号必须使用三段纯数字，例如 0.2.1。"
}

$resolvedBuildRoot = [System.IO.Path]::GetFullPath($buildRoot).TrimEnd('\', '/')
$buildRootPrefix = $resolvedBuildRoot + [System.IO.Path]::DirectorySeparatorChar
if (Test-Path -LiteralPath $buildRoot) {
    $buildRootItem = Get-Item -LiteralPath $buildRoot -Force
    if (-not $buildRootItem.PSIsContainer) {
        throw "build 路径不是目录，禁止清理或写入：$buildRoot"
    }
    if (($buildRootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "build 目录不能是链接或联接点，禁止清理或写入：$buildRoot"
    }
}
$resolvedOutputDirectory = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    [System.IO.Path]::GetFullPath((Join-Path $buildRoot "final-windows-release-$Version"))
}
elseif ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))
}
if (
    $resolvedOutputDirectory -eq $resolvedBuildRoot -or
    -not $resolvedOutputDirectory.StartsWith(
        $buildRootPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "离线安装器输出目录必须是 build 下的明确子目录：$resolvedOutputDirectory"
}
$outputAncestor = $resolvedOutputDirectory
while (-not [string]::Equals($outputAncestor, $resolvedBuildRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    if (Test-Path -LiteralPath $outputAncestor) {
        $outputAncestorItem = Get-Item -LiteralPath $outputAncestor -Force
        if (($outputAncestorItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "build 到交付目录之间不能包含链接或联接点：$outputAncestor"
        }
    }
    $outputAncestor = [System.IO.Path]::GetDirectoryName($outputAncestor)
    if ([string]::IsNullOrWhiteSpace($outputAncestor)) {
        throw "无法安全确认离线安装器输出目录：$resolvedOutputDirectory"
    }
}

$resolvedWorkspace = [System.IO.Path]::GetFullPath($workspace)
if (-not $resolvedWorkspace.StartsWith($buildRootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "打包临时目录不安全：$resolvedWorkspace"
}
$workspacePrefix = $resolvedWorkspace.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
$outputPrefix = $resolvedOutputDirectory.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
if (
    $resolvedWorkspace -eq $resolvedOutputDirectory -or
    $resolvedWorkspace.StartsWith($outputPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
    $resolvedOutputDirectory.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)
) {
    throw "交付目录不能与打包临时目录重叠。"
}
Assert-NoReparsePointsForOfflinePath `
    -RepositoryRoot $repoRoot `
    -CandidatePath $resolvedWorkspace `
    -Label "随机打包临时目录"
Assert-NoReparsePointsForOfflinePath `
    -RepositoryRoot $repoRoot `
    -CandidatePath $resolvedOutputDirectory `
    -Label "客户交付目录"

$offlineLifecycleLease = Assert-OfflineBuildLifecycle `
    -RepositoryRoot $repoRoot `
    -ExpectedVersion $Version `
    -RegistryPath $releaseRegistryPath `
    -AttemptMarkerPath $attemptMarkerPath `
    -OfflineStartedMarkerPath $offlineStartedMarkerPath

$expectedName = "VideoInsight-$Version-Setup.exe"
$output = Join-Path $resolvedOutputDirectory $expectedName
$deliveryPaths = @(
    $output,
    (Join-Path $resolvedOutputDirectory "verify_windows_install.ps1"),
    (Join-Path $resolvedOutputDirectory "CLEAN_PC_ACCEPTANCE.md"),
    (Join-Path $resolvedOutputDirectory "PRODUCTION_RELEASE_CHECKLIST.md"),
    (Join-Path $resolvedOutputDirectory "SHA256SUMS.txt")
)
$retainOfflineLifecycleLock = $false
try {
if (Try-RecoverCommittedOfflineArtifact `
    -OutputDirectory $resolvedOutputDirectory `
    -DeliveryPaths $deliveryPaths `
    -InstallerPath $output `
    -ExpectedInstallerName $expectedName `
    -ExpectedVersion $Version `
    -RegistryPath $releaseRegistryPath
) {
    Write-Output "已从完整且哈希一致的交付目录恢复 built 状态：$output"
    return
}
if ([bool]$offlineLifecycleLease.RecoveryOnly) {
    Set-OfflineArtifactFailed -RegistryPath $releaseRegistryPath -ExpectedVersion $Version | Out-Null
    throw "离线构建一次性权利已使用，但没有可恢复的完整 exact artifact；版本已烧录为 failed，禁止重新编译。"
}
if (-not (Test-Path -LiteralPath $unpacked -PathType Container)) {
    throw "请先生成 win-unpacked 目录版。"
}
Assert-NoReparsePointsInTree -RootPath $unpacked -Label "Windows 应用目录"
if (-not (Test-Path -LiteralPath (Join-Path $unpacked "VideoInsight.exe") -PathType Leaf)) {
    throw "请先生成 win-unpacked 目录版。"
}
if (-not (Test-Path -LiteralPath $appIcon)) {
    throw "缺少应用图标，请先生成 Windows 目录版。"
}

if (Test-Path -LiteralPath $resolvedOutputDirectory -PathType Leaf) {
    throw "离线安装器输出路径不是目录：$resolvedOutputDirectory"
}
if (Test-Path -LiteralPath $resolvedOutputDirectory -PathType Container) {
    $outputDirectoryItem = Get-Item -LiteralPath $resolvedOutputDirectory -Force
    if (($outputDirectoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "离线安装器输出目录不能是链接或联接点：$resolvedOutputDirectory"
    }
    $existingDeliveryItems = @(Get-ChildItem -LiteralPath $resolvedOutputDirectory -Force)
    if ($existingDeliveryItems.Count -gt 0) {
        throw "离线安装器输出目录必须为空；脚本不会清理或覆盖已有交付文件：$resolvedOutputDirectory"
    }
}
foreach ($deliveryPath in $deliveryPaths) {
    $deliveryAttributes = Get-ExistingPathAttributesForOfflineBuild -LiteralPath $deliveryPath
    if ($null -ne $deliveryAttributes) {
        throw "交付文件已经存在，禁止覆盖：$deliveryPath"
    }
}
if ($null -ne (Get-ExistingPathAttributesForOfflineBuild -LiteralPath $workspace)) {
    throw "随机打包临时路径已被占用，脚本不会清理或覆盖：$workspace"
}
[System.IO.Directory]::CreateDirectory($workspace) | Out-Null
[System.IO.Directory]::CreateDirectory($resolvedOutputDirectory) | Out-Null
Assert-NoReparsePointsForOfflinePath -RepositoryRoot $repoRoot -CandidatePath $workspace -Label "随机打包临时目录"
Assert-NoReparsePointsForOfflinePath -RepositoryRoot $repoRoot -CandidatePath $resolvedOutputDirectory -Label "客户交付目录"
Assert-NoReparsePointsInTree -RootPath $workspace -Label "随机打包临时目录"

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

$versionFile = Join-Path $workspace "version.txt"
[System.IO.File]::WriteAllText($versionFile, $Version, [System.Text.Encoding]::ASCII)
$versionMetadataSource = Join-Path $workspace "version_metadata.cs"
$assemblyVersion = "$Version.0"
$versionMetadata = @"
using System.Reflection;
[assembly: AssemblyVersion("$assemblyVersion")]
[assembly: AssemblyFileVersion("$assemblyVersion")]
[assembly: AssemblyInformationalVersion("$Version")]
"@
[System.IO.File]::WriteAllText(
    $versionMetadataSource,
    $versionMetadata,
    [System.Text.Encoding]::ASCII
)
$csc = Get-TrustedCSharpCompilerPath
$installScript = Join-Path $workspace "install_windows_desktop.ps1"
$uninstallScript = Join-Path $workspace "uninstall_windows_desktop.ps1"
$verifierScript = Join-Path $workspace "verify_windows_install.ps1"
$bootstrapSource = Join-Path $PSScriptRoot "offline_installer_bootstrap.cs"
$partialOutput = Join-Path $workspace "VideoInsight-$Version-Setup.partial.exe"
& $csc `
    /nologo `
    /target:winexe `
    /platform:anycpu `
    /optimize+ `
    "/win32icon:$appIcon" `
    "/out:$partialOutput" `
    "/resource:$payload,VideoInsight.Payload" `
    "/resource:$installScript,VideoInsight.InstallScript" `
    "/resource:$uninstallScript,VideoInsight.UninstallScript" `
    "/resource:$verifierScript,VideoInsight.VerifierScript" `
    "/resource:$versionFile,VideoInsight.Version" `
    /reference:System.Windows.Forms.dll `
    $bootstrapSource `
    $versionMetadataSource
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $partialOutput -PathType Leaf)) {
    throw ".NET 离线安装器生成失败。"
}
$partialVersionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($partialOutput)
$partialFileVersion = Get-EmbeddedStableVersionForOfflineBuild -Value $partialVersionInfo.FileVersion -Label "FileVersion"
$partialProductVersion = Get-EmbeddedStableVersionForOfflineBuild -Value $partialVersionInfo.ProductVersion -Label "ProductVersion"
if ($partialFileVersion -ne $Version -or $partialProductVersion -ne $Version) {
    throw "离线安装器内嵌 FileVersion/ProductVersion 与正式版本不一致。"
}

foreach ($deliveryPath in $deliveryPaths) {
    if ($null -ne (Get-ExistingPathAttributesForOfflineBuild -LiteralPath $deliveryPath)) {
        throw "交付文件在构建期间被其他进程占用，禁止覆盖：$deliveryPath"
    }
}
$partialHash = Get-Sha256Hex -LiteralPath $partialOutput
$verifierDeliveryPath = Join-Path $resolvedOutputDirectory "verify_windows_install.ps1"
[System.IO.File]::Copy(
    $verifierScript,
    $verifierDeliveryPath,
    $false
)
[System.IO.File]::Copy(
    (Join-Path $repoRoot "CLEAN_PC_ACCEPTANCE.md"),
    (Join-Path $resolvedOutputDirectory "CLEAN_PC_ACCEPTANCE.md"),
    $false
)
[System.IO.File]::Copy(
    (Join-Path $repoRoot "PRODUCTION_RELEASE_CHECKLIST.md"),
    (Join-Path $resolvedOutputDirectory "PRODUCTION_RELEASE_CHECKLIST.md"),
    $false
)
$checksumLines = @(
    ("{0}  {1}" -f $partialHash, $expectedName),
    ("{0}  {1}" -f (Get-Sha256Hex -LiteralPath $verifierDeliveryPath), "verify_windows_install.ps1"),
    ("{0}  {1}" -f (Get-Sha256Hex -LiteralPath (Join-Path $resolvedOutputDirectory "CLEAN_PC_ACCEPTANCE.md")), "CLEAN_PC_ACCEPTANCE.md"),
    ("{0}  {1}" -f (Get-Sha256Hex -LiteralPath (Join-Path $resolvedOutputDirectory "PRODUCTION_RELEASE_CHECKLIST.md")), "PRODUCTION_RELEASE_CHECKLIST.md")
)
$checksumPath = Join-Path $resolvedOutputDirectory "SHA256SUMS.txt"
$checksumBytes = [System.Text.Encoding]::ASCII.GetBytes(
    (($checksumLines -join [Environment]::NewLine) + [Environment]::NewLine)
)
$checksumStream = New-Object System.IO.FileStream(
    $checksumPath,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::None
)
try {
    $checksumStream.Write($checksumBytes, 0, $checksumBytes.Length)
    $checksumStream.Flush($true)
}
finally {
    $checksumStream.Dispose()
}
$preCommitExecutables = @(Get-ChildItem -LiteralPath $resolvedOutputDirectory -Filter "*.exe" -File)
if ($preCommitExecutables.Count -ne 0) {
    throw "客户交付目录在最终提交前出现了其他 EXE，禁止覆盖。"
}
if ($null -ne (Get-ExistingPathAttributesForOfflineBuild -LiteralPath $output)) {
    throw "正式安装包路径在检查后被其他进程占用，禁止覆盖：$output"
}
$expectedLockStatus = "?? deploy/control-plane/release_versions.json.lock"
$preCommitStatus = @(& git -C $repoRoot status --porcelain --untracked-files=all)
if (
    $LASTEXITCODE -ne 0 -or
    $preCommitStatus.Count -ne 1 -or
    [string]$preCommitStatus[0] -ne $expectedLockStatus
) {
    throw "离线安装包最终提交前工作区发生变化，禁止写入客户交付 EXE。"
}
[System.IO.File]::Move($partialOutput, $output)
$item = Get-Item -LiteralPath $output
if ($item.Length -le 0 -or (Get-Sha256Hex -LiteralPath $output) -ne $partialHash) {
    throw "正式安装包提交后大小或 SHA256 与已验证 partial 不一致。"
}
$committedVersionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($output)
$committedFileVersion = Get-EmbeddedStableVersionForOfflineBuild -Value $committedVersionInfo.FileVersion -Label "FileVersion"
$committedProductVersion = Get-EmbeddedStableVersionForOfflineBuild -Value $committedVersionInfo.ProductVersion -Label "ProductVersion"
if ($committedFileVersion -ne $Version -or $committedProductVersion -ne $Version) {
    throw "正式安装包提交后的 FileVersion/ProductVersion 与当前版本不一致。"
}
$deliveryExecutables = @(Get-ChildItem -LiteralPath $resolvedOutputDirectory -Filter "*.exe" -File)
if ($deliveryExecutables.Count -ne 1 -or $deliveryExecutables[0].FullName -ne $item.FullName) {
    throw "客户交付目录必须且只能包含当前版本的一个 EXE：$resolvedOutputDirectory"
}
Complete-OfflineArtifactRegistry `
    -RegistryPath $releaseRegistryPath `
    -ExpectedVersion $Version `
    -InstallerSha256 $partialHash
Write-Output "离线安装器已生成：$($item.FullName)"
Write-Output "大小：$([math]::Round($item.Length / 1MB, 2)) MiB"
}
catch {
    $originalFailure = $_
    $recoverableArtifact = $false
    if ($null -ne (Get-ExistingPathAttributesForOfflineBuild -LiteralPath $output)) {
        try {
            $recoverableArtifact = Try-RecoverCommittedOfflineArtifact `
                -OutputDirectory $resolvedOutputDirectory `
                -DeliveryPaths $deliveryPaths `
                -InstallerPath $output `
                -ExpectedInstallerName $expectedName `
                -ExpectedVersion $Version `
                -RegistryPath $releaseRegistryPath `
                -ValidateOnly
        }
        catch {
            Write-Warning "已落盘交付目录不满足 exact artifact 恢复条件；当前版本将烧录为 failed。"
        }
    }
    if ($recoverableArtifact) {
        Write-Warning "完整 exact artifact 已落盘但 built 登记未完成；保留 attempted，仅允许同目录恢复，禁止重新编译。"
    }
    else {
        try {
            Set-OfflineArtifactFailed `
                -RegistryPath $releaseRegistryPath `
                -ExpectedVersion $Version | Out-Null
        }
        catch {
            $retainOfflineLifecycleLock = $true
            Write-Warning "无法原子烧录离线构建 failed 状态；正式版本锁将保留，禁止再次编译，需人工核对。"
        }
    }
    throw $originalFailure
}
finally {
    if ($null -ne $offlineLifecycleLease -and $null -ne $offlineLifecycleLease.Stream) {
        $offlineLifecycleLease.Stream.Dispose()
    }
    if (
        -not $retainOfflineLifecycleLock -and
        $null -ne $offlineLifecycleLease -and
        -not [string]::IsNullOrWhiteSpace([string]$offlineLifecycleLease.Path) -and
        (Test-Path -LiteralPath $offlineLifecycleLease.Path)
    ) {
        try { Remove-Item -LiteralPath $offlineLifecycleLease.Path -Force }
        catch {
            Write-Warning "离线构建状态已提交，但正式版本锁未能清理；请人工核对：$($offlineLifecycleLease.Path)"
        }
    }
}
