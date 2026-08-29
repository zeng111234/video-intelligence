[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$Version,

    [Parameter()]
    [string]$OutputDirectory = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path

function Assert-NoReparsePoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $LiteralPath)) {
        return
    }
    $item = Get-Item -LiteralPath $LiteralPath -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label 不能是符号链接、目录联接或其他重解析点：$LiteralPath"
    }
}

function Assert-NoReparsePointInExistingPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $fullPath = [System.IO.Path]::GetFullPath($LiteralPath)
    $root = [System.IO.Path]::GetPathRoot($fullPath)
    if ([string]::IsNullOrWhiteSpace($root)) {
        throw "$Label 无法解析绝对路径：$LiteralPath"
    }
    $relative = $fullPath.Substring($root.Length).TrimStart('\', '/')
    $segments = @($relative -split '[\\/]' | Where-Object { $_ })
    $cursor = $root
    for ($index = 0; $index -lt $segments.Count; $index++) {
        if (-not (Test-Path -LiteralPath $cursor -PathType Container)) {
            break
        }
        $matches = @(
            Get-ChildItem -LiteralPath $cursor -Force |
                Where-Object { $_.Name -ceq $segments[$index] }
        )
        if ($matches.Count -eq 0) {
            break
        }
        if ($matches.Count -ne 1) {
            throw "$Label 包含无法唯一识别的同名路径条目：$LiteralPath"
        }
        $item = $matches[0]
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label 不能经过符号链接、目录联接或其他重解析点：$($item.FullName)"
        }
        if ($index -lt ($segments.Count - 1) -and -not $item.PSIsContainer) {
            throw "$Label 的中间路径不是目录：$($item.FullName)"
        }
        $cursor = $item.FullName
    }
}

function Get-ExistingLeafEntry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath
    )

    $parent = Split-Path -Parent $LiteralPath
    $name = Split-Path -Leaf $LiteralPath
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        return $null
    }
    $matches = @(
        Get-ChildItem -LiteralPath $parent -Force |
            Where-Object { $_.Name -ceq $name }
    )
    if ($matches.Count -gt 1) {
        throw "目标目录包含无法唯一识别的同名条目：$LiteralPath"
    }
    if ($matches.Count -eq 1) {
        return $matches[0]
    }
    return $null
}

function Assert-LeafEntryAbsent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $entry = Get-ExistingLeafEntry -LiteralPath $LiteralPath
    if ($null -ne $entry) {
        $kind = if (
            ($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) { '重解析点' } else { '现有文件或目录' }
        throw "$Label 已被$kind占用，禁止覆盖：$LiteralPath"
    }
}

function Read-ReleaseRegistry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath
    )

    try {
        return Get-Content -LiteralPath $LiteralPath -Raw | ConvertFrom-Json
    }
    catch {
        throw '正式版本登记表无法读取。'
    }
}

function Write-ReleaseRegistryAtomically {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath,

        [Parameter(Mandatory = $true)]
        [object]$Registry
    )

    Assert-NoReparsePointInExistingPath -LiteralPath $LiteralPath -Label '正式版本登记表路径'
    Assert-NoReparsePoint -LiteralPath $LiteralPath -Label '正式版本登记表'
    $parent = Split-Path -Parent $LiteralPath
    $name = Split-Path -Leaf $LiteralPath
    $nonce = [Guid]::NewGuid().ToString('N')
    $temporaryPath = Join-Path $parent ".$name.$nonce.tmp"
    $backupPath = Join-Path $parent ".$name.$nonce.bak"
    $stream = $null
    $committed = $false
    $operationFailure = $null
    try {
        $json = ($Registry | ConvertTo-Json -Depth 8) + [Environment]::NewLine
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json)
        $stream = [System.IO.FileStream]::new(
            $temporaryPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        $stream.Dispose()
        $stream = $null
        [System.IO.File]::Replace($temporaryPath, $LiteralPath, $backupPath, $true)
        $committed = $true
    }
    catch {
        $operationFailure = $_
    }
    finally {
        if ($null -ne $stream) {
            try {
                $stream.Dispose()
            }
            catch {
                Write-Warning '正式版本登记表临时句柄清理失败；原始写入结果保持不变。'
            }
        }
        foreach ($cleanupPath in @($temporaryPath, $backupPath)) {
            try {
                if (Test-Path -LiteralPath $cleanupPath) {
                    Remove-Item -LiteralPath $cleanupPath -Force
                }
            }
            catch {
                if ($committed) {
                    Write-Warning '正式版本登记表已经原子提交，但临时文件清理失败；状态不会回滚或伪装成失败。'
                }
                else {
                    Write-Warning '正式版本登记表写入失败且临时文件未能清理；后续发布仍须 fail-closed。'
                }
            }
        }
    }
    if ($null -ne $operationFailure) {
        throw $operationFailure
    }
}

function Assert-FreshReleaseCandidate {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Registry,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedVersion
    )

    $expectedTopLevelProperties = @(
        'schema_version',
        'used_versions',
        'current_candidate',
        'release_in_progress',
        'completed_releases',
        'superseded_releases'
    )
    $actualTopLevelProperties = @($Registry.PSObject.Properties.Name)
    if (Compare-Object -CaseSensitive $expectedTopLevelProperties $actualTopLevelProperties) {
        throw '正式版本登记表包含缺失或未授权的顶层字段。'
    }
    $schemaProperty = $Registry.PSObject.Properties['schema_version']
    $usedProperty = $Registry.PSObject.Properties['used_versions']
    $candidateProperty = $Registry.PSObject.Properties['current_candidate']
    $inProgressProperty = $Registry.PSObject.Properties['release_in_progress']
    $completedProperty = $Registry.PSObject.Properties['completed_releases']
    $supersededProperty = $Registry.PSObject.Properties['superseded_releases']
    if (
        $null -eq $schemaProperty -or [int]$schemaProperty.Value -ne 2 -or
        $null -eq $usedProperty -or $usedProperty.Value -isnot [System.Array] -or
        $null -eq $candidateProperty -or $candidateProperty.Value -isnot [string] -or
        $null -eq $inProgressProperty -or $null -ne $inProgressProperty.Value -or
        $null -eq $completedProperty -or $completedProperty.Value -isnot [System.Array] -or
        $null -eq $supersededProperty -or $supersededProperty.Value -isnot [System.Array]
    ) {
        throw '正式版本登记表 schema 无效或缺少 fail-closed 生命周期字段。'
    }
    $used = @($usedProperty.Value | ForEach-Object { [string]$_ })
    if (
        $used.Count -eq 0 -or
        @($used | Where-Object { $_ -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' }).Count -ne 0 -or
        @($used | Sort-Object -Unique).Count -ne $used.Count
    ) {
        throw '正式版本登记表包含无效或重复的已使用版本。'
    }
    if ($used -contains $ExpectedVersion) {
        throw "版本 $ExpectedVersion 已登记为使用过，禁止重新打包。"
    }
    if ([string]$candidateProperty.Value -ne $ExpectedVersion) {
        throw "本次只允许构建已经审查并登记的候选版本：$($candidateProperty.Value)"
    }
    $completedVersions = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($completed in @($completedProperty.Value)) {
        $expectedCompletedProperties = @(
            'version',
            'source_commit',
            'server_state',
            'control_plane_sha256',
            'windows_state',
            'installer_sha256'
        )
        if (
            $null -eq $completed -or
            (Compare-Object `
                -CaseSensitive `
                -ReferenceObject $expectedCompletedProperties `
                -DifferenceObject @($completed.PSObject.Properties.Name))
        ) {
            throw '正式版本登记表包含字段集合无效的完成记录。'
        }
        if (
            [string]$completed.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
            [string]$completed.source_commit -notmatch '^[0-9a-f]{40}$' -or
            [string]$completed.server_state -ne 'ready' -or
            [string]$completed.control_plane_sha256 -notmatch '^[0-9a-f]{64}$' -or
            [string]$completed.windows_state -ne 'complete' -or
            [string]$completed.installer_sha256 -notmatch '^[0-9a-f]{64}$' -or
            -not $completedVersions.Add([string]$completed.version) -or
            $used -notcontains [string]$completed.version
        ) {
            throw '正式版本登记表包含无效、重复或未烧录的完成记录。'
        }
    }
    foreach ($superseded in @($supersededProperty.Value)) {
        if (
            $null -eq $superseded -or
            ((@($superseded.PSObject.Properties.Name) | Sort-Object) -join ',') -ne
                'control_plane_sha256,installer_sha256,reason,server_state,source_commit,superseded_by,superseded_at,version,windows_state' -or
            [string]$superseded.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
            [string]$superseded.superseded_by -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
            [string]$superseded.source_commit -notmatch '^[0-9a-f]{40}$' -or
            [string]$superseded.server_state -ne 'ready' -or
            [string]$superseded.control_plane_sha256 -notmatch '^[0-9a-f]{64}$' -or
            [string]$superseded.windows_state -notin @('pending','failed','attempted','built') -or
            ([string]$superseded.installer_sha256 -ne '' -and [string]$superseded.installer_sha256 -notmatch '^[0-9a-f]{64}$') -or
            [string]::IsNullOrWhiteSpace([string]$superseded.reason) -or
            [string]::IsNullOrWhiteSpace([string]$superseded.superseded_at)
        ) { throw '正式版本登记表包含无效的 superseded_releases 记录。' }
    }
    $candidateBaseVersion = [version]($ExpectedVersion.Split('-', 2)[0])
    $usedBaseVersions = @(
        $used | ForEach-Object { [version]$_ }
    )
    if (
        $usedBaseVersions.Count -gt 0 -and
        $candidateBaseVersion -le ($usedBaseVersions | Sort-Object -Descending | Select-Object -First 1)
    ) {
        throw "候选版本 $ExpectedVersion 必须高于所有已使用版本。"
    }
}

function Get-GitBlobSha1 {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [byte[]]$Content
    )

    $header = [System.Text.Encoding]::ASCII.GetBytes("blob $($Content.Length)`0")
    $payload = [byte[]]::new($header.Length + $Content.Length)
    [System.Array]::Copy($header, 0, $payload, 0, $header.Length)
    [System.Array]::Copy($Content, 0, $payload, $header.Length, $Content.Length)
    $sha1 = [System.Security.Cryptography.SHA1]::Create()
    try {
        return ([System.BitConverter]::ToString($sha1.ComputeHash($payload))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha1.Dispose()
    }
}

function Get-CommittedGitBlobBytes {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,

        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[0-9a-f]{40}$')]
        [string]$BlobSha
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = 'git.exe'
    $startInfo.Arguments = "cat-file blob $BlobSha"
    $startInfo.WorkingDirectory = $RepositoryRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $memory = [System.IO.MemoryStream]::new()
    try {
        if (-not $process.Start()) {
            throw '无法启动 Git 读取正式版本源文件。'
        }
        $process.StandardOutput.BaseStream.CopyTo($memory)
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) {
            throw "无法读取正式版本源文件：$stderr"
        }
        return ,$memory.ToArray()
    }
    finally {
        $memory.Dispose()
        $process.Dispose()
    }
}

function Assert-HeadMatchesSourceCommit {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,

        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[0-9a-f]{40}$')]
        [string]$SourceCommit
    )

    $currentHead = [string](& git -C $RepositoryRoot rev-parse HEAD)
    if (
        $LASTEXITCODE -ne 0 -or
        [string]::IsNullOrWhiteSpace($currentHead) -or
        $currentHead.Trim() -ne $SourceCommit
    ) {
        throw '部署包生成期间正式版本源提交发生变化；该版本已烧录并作废。'
    }
}

function Assert-ReleaseArchiveStream {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.FileStream]$Stream,

        [Parameter(Mandatory = $true)]
        [object[]]$ExpectedEntries,

        [Parameter(Mandatory = $true)]
        [hashtable]$ExpectedBlobs,

        [Parameter(Mandatory = $true)]
        [hashtable]$ExpectedSizes,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedVersion
    )

    $releaseVersionEntry = 'release_version.txt'
    $expectedNames = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($entry in $ExpectedEntries) {
        if (-not $expectedNames.Add([string]$entry.Entry)) {
            throw "受审候选清单包含重复条目：$($entry.Entry)"
        }
    }
    if (-not $expectedNames.Add($releaseVersionEntry)) {
        throw '受审候选清单与版本条目冲突。'
    }

    $Stream.Position = 0
    $archiveToVerify = [System.IO.Compression.ZipArchive]::new(
        $Stream,
        [System.IO.Compression.ZipArchiveMode]::Read,
        $true
    )
    try {
        if ($archiveToVerify.Entries.Count -ne $expectedNames.Count) {
            throw '部署包条目数量与受审候选清单不一致。'
        }
        $seen = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::Ordinal
        )
        foreach ($zipEntry in $archiveToVerify.Entries) {
            if (-not $seen.Add($zipEntry.FullName)) {
                throw "部署包包含重复条目：$($zipEntry.FullName)"
            }
            if (-not $expectedNames.Contains($zipEntry.FullName)) {
                throw "部署包包含受审候选清单之外的条目：$($zipEntry.FullName)"
            }
            if ($zipEntry.FullName -eq $releaseVersionEntry) {
                $expectedVersionBytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
                    $ExpectedVersion
                )
                if ($zipEntry.Length -ne $expectedVersionBytes.Length) {
                    throw '部署包版本条目内容无效。'
                }
                $versionBytes = [byte[]]::new($expectedVersionBytes.Length)
                $versionStream = $zipEntry.Open()
                try {
                    $offset = 0
                    while ($offset -lt $versionBytes.Length) {
                        $read = $versionStream.Read(
                            $versionBytes,
                            $offset,
                            $versionBytes.Length - $offset
                        )
                        if ($read -le 0) {
                            throw '部署包版本条目提前结束。'
                        }
                        $offset += $read
                    }
                    if ($versionStream.ReadByte() -ne -1) {
                        throw '部署包版本条目包含额外内容。'
                    }
                }
                finally {
                    $versionStream.Dispose()
                }
                $versionMatches = $true
                for ($index = 0; $index -lt $versionBytes.Length; $index++) {
                    if ($versionBytes[$index] -ne $expectedVersionBytes[$index]) {
                        $versionMatches = $false
                        break
                    }
                }
                if (-not $versionMatches) {
                    throw '部署包版本条目与正式版本不一致。'
                }
                continue
            }
            if (
                -not $ExpectedBlobs.ContainsKey($zipEntry.FullName) -or
                -not $ExpectedSizes.ContainsKey($zipEntry.FullName) -or
                [long]$zipEntry.Length -ne [long]$ExpectedSizes[$zipEntry.FullName]
            ) {
                throw "部署包条目不属于精确受审候选清单：$($zipEntry.FullName)"
            }
            $entryStream = $zipEntry.Open()
            $sha1 = [System.Security.Cryptography.SHA1]::Create()
            try {
                $header = [System.Text.Encoding]::ASCII.GetBytes(
                    "blob $($zipEntry.Length)`0"
                )
                $sha1.TransformBlock($header, 0, $header.Length, $null, 0) | Out-Null
                $buffer = [byte[]]::new(1024 * 1024)
                $total = 0L
                while (($read = $entryStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $sha1.TransformBlock($buffer, 0, $read, $null, 0) | Out-Null
                    $total += $read
                    if ($total -gt [long]$ExpectedSizes[$zipEntry.FullName]) {
                        throw "部署包条目超过受审大小：$($zipEntry.FullName)"
                    }
                }
                $sha1.TransformFinalBlock([byte[]]::new(0), 0, 0) | Out-Null
                $actualBlob = ([System.BitConverter]::ToString($sha1.Hash)).Replace(
                    '-', ''
                ).ToLowerInvariant()
            }
            finally {
                $sha1.Dispose()
                $entryStream.Dispose()
            }
            if (
                $total -ne [long]$ExpectedSizes[$zipEntry.FullName] -or
                $actualBlob -ne $ExpectedBlobs[$zipEntry.FullName]
            ) {
                throw "部署包条目与正式版本源提交不一致：$($zipEntry.FullName)"
            }
        }
        if (-not $seen.SetEquals($expectedNames)) {
            throw '部署包条目集合与精确受审候选清单不一致。'
        }
    }
    finally {
        $archiveToVerify.Dispose()
    }

    $Stream.Position = 0
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha256.ComputeHash($Stream))).Replace(
            '-', ''
        ).ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
        $Stream.Position = 0
    }
}

$releaseRegistryPath = Join-Path $repositoryRoot 'deploy\control-plane\release_versions.json'
if (-not (Test-Path -LiteralPath $releaseRegistryPath -PathType Leaf)) {
    throw '缺少受版本控制的正式版本登记表。'
}
Assert-NoReparsePointInExistingPath -LiteralPath $releaseRegistryPath -Label '正式版本登记表路径'
Assert-NoReparsePoint -LiteralPath $releaseRegistryPath -Label '正式版本登记表'
$releaseRegistry = Read-ReleaseRegistry -LiteralPath $releaseRegistryPath
Assert-FreshReleaseCandidate -Registry $releaseRegistry -ExpectedVersion $Version

$resolvedOutputDirectory = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot "build\control-plane-ready-$Version"))
} elseif ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $OutputDirectory))
}

$archivePath = Join-Path $resolvedOutputDirectory "VideoInsight-control-plane-$Version.zip"
Assert-NoReparsePointInExistingPath -LiteralPath $resolvedOutputDirectory -Label '部署包输出路径'
Assert-LeafEntryAbsent -LiteralPath $archivePath -Label '正式部署包目标'

Write-Output '运行干净工作区完整发布门禁；通过前不会创建部署包。'
$sourceCommit = [string](& git -C $repositoryRoot rev-parse HEAD)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sourceCommit)) {
    throw '无法读取正式版本源提交。'
}
$sourceCommit = $sourceCommit.Trim()
& (Join-Path $PSScriptRoot 'check_release.ps1')
if ($LASTEXITCODE -ne 0) {
    throw '干净工作区发布门禁失败；控制层部署包未生成。'
}
Assert-HeadMatchesSourceCommit -RepositoryRoot $repositoryRoot -SourceCommit $sourceCommit

$registryLockPath = "$releaseRegistryPath.lock"
$registryLock = $null
$ownsRegistryLock = $false
$releaseReserved = $false
$serverBundleReady = $false
$releaseState = $null
$partialArchivePath = $null
$finalArchiveCreated = $false
$archiveFileStream = $null
$archive = $null
$partialVerificationStream = $null
$finalArchiveLockStream = $null
try {
    try {
        $registryLock = [System.IO.FileStream]::new(
            $registryLockPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $ownsRegistryLock = $true
    }
    catch {
        throw '另一个正式版本构建正在运行，或上次构建异常中断；禁止并行生成部署包。'
    }

    $dirtyState = @(& git -C $repositoryRoot status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0 -or $dirtyState.Count -ne 1 -or $dirtyState[0] -ne '?? deploy/control-plane/release_versions.json.lock') {
        throw '正式版本锁定前工作区发生变化，已停止生成部署包。'
    }
    Assert-HeadMatchesSourceCommit -RepositoryRoot $repositoryRoot -SourceCommit $sourceCommit
    $lockedRegistry = Read-ReleaseRegistry -LiteralPath $releaseRegistryPath
    Assert-FreshReleaseCandidate -Registry $lockedRegistry -ExpectedVersion $Version
    $usedVersions = @(
        @($lockedRegistry.used_versions | ForEach-Object { [string]$_ }) + $Version |
            Sort-Object -Unique
    )
    $completedReleasesProperty = $lockedRegistry.PSObject.Properties['completed_releases']
    [object[]]$completedReleases = @($completedReleasesProperty.Value)
    $releaseState = [ordered]@{
        schema_version = 2
        used_versions = $usedVersions
        current_candidate = $null
        release_in_progress = [ordered]@{
            version = $Version
            source_commit = $sourceCommit
            server_state = 'building'
            control_plane_sha256 = $null
            windows_state = 'pending'
            installer_sha256 = $null
        }
        completed_releases = [object[]]$completedReleases
        superseded_releases = [object[]]@($lockedRegistry.superseded_releases)
    }
    Write-ReleaseRegistryAtomically -LiteralPath $releaseRegistryPath -Registry $releaseState
    $releaseReserved = $true

    [System.IO.Directory]::CreateDirectory($resolvedOutputDirectory) | Out-Null
    Assert-NoReparsePointInExistingPath -LiteralPath $resolvedOutputDirectory -Label '部署包输出路径'
    Assert-LeafEntryAbsent -LiteralPath $archivePath -Label '正式部署包目标'
    $partialArchivePath = Join-Path $resolvedOutputDirectory (
        ".VideoInsight-control-plane-$Version.$([Guid]::NewGuid().ToString('N')).partial"
    )
    Assert-LeafEntryAbsent -LiteralPath $partialArchivePath -Label '部署包临时目标'

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
    '.mp3', '.wav', '.mp4', '.mov', '.mkv', '.webm',
    '.key', '.pem', '.p12', '.pfx', '.jks', '.keystore', '.der', '.p8', '.ppk'
)

$committedBlobs = @{}
$committedRecords = [System.Collections.Generic.List[object]]::new()
$treeLines = @(& git -C $repositoryRoot -c core.quotepath=false ls-tree -r $sourceCommit)
if ($LASTEXITCODE -ne 0) {
    throw '无法读取正式版本源提交的文件清单。'
}
foreach ($line in $treeLines) {
    if ($line -notmatch '^([0-9]{6}) ([a-z]+) ([0-9a-f]{40})\t(.+)$') {
        throw '正式版本源提交包含无法解析的树记录。'
    }
    $relativePath = $matches[4].Replace('\', '/')
    if ($relativePath -match '[\x00-\x1f]') {
        throw '正式版本源提交包含不允许的控制字符路径。'
    }
    $record = [pscustomobject]@{
        Mode = $matches[1]
        Type = $matches[2]
        Blob = $matches[3]
        Entry = $relativePath
    }
    $committedRecords.Add($record)
    if ($record.Type -eq 'blob' -and $record.Mode -in @('100644', '100755')) {
        $committedBlobs[$relativePath] = $record.Blob
    }
}

$candidatePaths = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::Ordinal
)
foreach ($relativePath in $explicitFiles) {
    $record = $committedRecords | Where-Object { $_.Entry -ceq $relativePath }
    if (
        @($record).Count -ne 1 -or
        $record.Type -ne 'blob' -or
        $record.Mode -notin @('100644', '100755')
    ) {
        throw "正式版本源提交缺少普通必要文件：$relativePath"
    }
    $candidatePaths.Add($relativePath) | Out-Null
}
foreach ($relativeTree in $sourceTrees) {
    $sourcePrefix = $relativeTree.TrimEnd('/') + '/'
    $treeRecords = @(
        $committedRecords | Where-Object { $_.Entry.StartsWith($sourcePrefix) }
    )
    if ($treeRecords.Count -eq 0) {
        throw "正式版本源提交缺少必要目录内容：$relativeTree"
    }
    foreach ($record in $treeRecords) {
        if ($record.Type -ne 'blob' -or $record.Mode -notin @('100644', '100755')) {
            throw "正式版本源提交的部署目录包含非普通文件：$($record.Entry)"
        }
        $candidatePaths.Add([string]$record.Entry) | Out-Null
    }
}

$entries = [System.Collections.Generic.List[object]]::new()
foreach ($relativePath in $candidatePaths) {
    if ($relativePath -in @(
        'deploy/control-plane/release_versions.json',
        'deploy/control-plane/release_versions.json.lock'
    )) {
        continue
    }
    $segments = $relativePath.Split('/')
    if ($segments | Where-Object { $forbiddenDirectoryNames -contains $_ }) {
        continue
    }
    $extension = [System.IO.Path]::GetExtension($relativePath).ToLowerInvariant()
    if ($forbiddenExtensions -contains $extension) {
        continue
    }
    $fileName = [System.IO.Path]::GetFileName($relativePath)
    if (
        $fileName.Equals('.env', [System.StringComparison]::OrdinalIgnoreCase) -or
        (
            $fileName.StartsWith('.env.', [System.StringComparison]::OrdinalIgnoreCase) -and
            -not $fileName.Equals('.env.example', [System.StringComparison]::OrdinalIgnoreCase)
        )
    ) {
        continue
    }
    $entries.Add([pscustomobject]@{
        Entry = $relativePath
    })
}

$entries = @($entries | Sort-Object Entry -Unique)
if ($entries.Count -lt 20) {
    throw "部署包文件数量异常（$($entries.Count)），已停止。"
}

foreach ($entry in $entries) {
    if (-not $committedBlobs.ContainsKey($entry.Entry)) {
        throw "部署包候选文件不属于正式版本源提交：$($entry.Entry)"
    }
}
$committedBlobBytes = @{}
$committedBlobSizes = @{}
foreach ($entry in $entries) {
    $committedBlobBytes[$entry.Entry] = [byte[]](
        Get-CommittedGitBlobBytes `
            -RepositoryRoot $repositoryRoot `
            -BlobSha $committedBlobs[$entry.Entry]
    )
    $committedBlobSizes[$entry.Entry] = (
        [byte[]]$committedBlobBytes[$entry.Entry]
    ).Length
}

$sensitivePath = $entries | Where-Object {
    $_.Entry -match '(^|/)(\.env|data|logs|backups|caddy-data|caddy-config|updates)(/|$)' -or
    $_.Entry -match '\.(db|sqlite3?|log|mp3|wav|mp4|mov|mkv|webm)$'
}
if ($sensitivePath) {
    throw "部署包包含不允许的运行文件：$($sensitivePath.Entry -join ', ')"
}
$privateKeyMarkers = @(
    '-----BEGIN PRIVATE KEY-----',
    '-----BEGIN ENCRYPTED PRIVATE KEY-----',
    '-----BEGIN RSA PRIVATE KEY-----',
    '-----BEGIN DSA PRIVATE KEY-----',
    '-----BEGIN EC PRIVATE KEY-----',
    '-----BEGIN OPENSSH PRIVATE KEY-----',
    'PuTTY-User-Key-File:'
)
foreach ($entry in $entries) {
    $content = [System.Text.Encoding]::UTF8.GetString(
        [byte[]]$committedBlobBytes[$entry.Entry]
    )
    foreach ($marker in $privateKeyMarkers) {
        if ($content.Contains($marker)) {
            throw "部署包候选文件包含私钥标记：$($entry.Entry)"
        }
    }
}

$avatarManifestRelativePath = 'deploy/control-plane/bootstrap/avatar_assets/shuying_cloud.json'
try {
    if (-not $committedBlobBytes.ContainsKey($avatarManifestRelativePath)) {
        throw 'manifest missing from committed release source'
    }
    $avatarManifestJson = [System.Text.Encoding]::UTF8.GetString(
        [byte[]]$committedBlobBytes[$avatarManifestRelativePath]
    )
    $avatarManifest = $avatarManifestJson | ConvertFrom-Json
} catch {
    throw '已训练数字人资产清单无法读取，已停止生成服务器包。'
}
$sharedAvatarAssets = @($avatarManifest.assets)
$requiredAvatar = @(
    $sharedAvatarAssets | Where-Object {
        $_.asset_id -eq 'shuying-avatar-21920' -and
        $_.kind -eq 'avatar' -and
        $_.provider_asset_id -eq '21920' -and
        $_.authorized -eq $true -and
        $_.shared -eq $true -and
        $_.status -eq 'ready'
    }
)
$requiredVoice = @(
    $sharedAvatarAssets | Where-Object {
        $_.asset_id -eq 'shuying-voice-7869' -and
        $_.kind -eq 'voice' -and
        $_.provider_asset_id -eq '7869' -and
        $_.authorized -eq $true -and
        $_.shared -eq $true -and
        $_.status -eq 'ready'
    }
)
if ($sharedAvatarAssets.Count -ne 2 -or $requiredAvatar.Count -ne 1 -or $requiredVoice.Count -ne 1) {
    throw '未找到一组已授权且训练完成的形象和声音；为避免重新付费训练，已停止生成服务器包。'
}
$sharedAssetEntry = 'deploy/control-plane/bootstrap/avatar_assets/shuying_cloud.json'
if ($entries.Entry -notcontains $sharedAssetEntry) {
    throw '受版本控制的数字人资产清单没有进入部署包候选文件。'
}
$releaseVersionEntry = 'release_version.txt'

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archiveFileStream = [System.IO.FileStream]::new(
    $partialArchivePath,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::None
)
$archive = [System.IO.Compression.ZipArchive]::new(
    $archiveFileStream,
    [System.IO.Compression.ZipArchiveMode]::Create,
    $true
)
try {
    foreach ($entry in $entries) {
        $blobBytes = [byte[]]$committedBlobBytes[$entry.Entry]
        $committedEntry = $archive.CreateEntry(
            $entry.Entry,
            [System.IO.Compression.CompressionLevel]::Optimal
        )
        $committedEntryStream = $committedEntry.Open()
        try {
            $committedEntryStream.Write($blobBytes, 0, $blobBytes.Length)
        }
        finally {
            $committedEntryStream.Dispose()
        }
    }
    $versionEntry = $archive.CreateEntry(
        $releaseVersionEntry,
        [System.IO.Compression.CompressionLevel]::Optimal
    )
    $versionWriter = [System.IO.StreamWriter]::new(
        $versionEntry.Open(),
        [System.Text.UTF8Encoding]::new($false)
    )
    try {
        $versionWriter.Write($Version)
    } finally {
        $versionWriter.Dispose()
    }
} finally {
    try {
        $archive.Dispose()
        $archive = $null
    }
    finally {
        try {
            $archiveFileStream.Flush($true)
        }
        finally {
            $archiveFileStream.Dispose()
            $archiveFileStream = $null
        }
    }
}

$partialVerificationStream = [System.IO.FileStream]::new(
    $partialArchivePath,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::None
)
try {
    $digest = Assert-ReleaseArchiveStream `
        -Stream $partialVerificationStream `
        -ExpectedEntries $entries `
        -ExpectedBlobs $committedBlobs `
        -ExpectedSizes $committedBlobSizes `
        -ExpectedVersion $Version
}
finally {
    $partialVerificationStream.Dispose()
    $partialVerificationStream = $null
}

$finalDirtyState = @(& git -C $repositoryRoot status --porcelain --untracked-files=all)
$expectedDirtyState = @(
    ' M deploy/control-plane/release_versions.json',
    '?? deploy/control-plane/release_versions.json.lock'
)
if (
    $LASTEXITCODE -ne 0 -or
    (Compare-Object -ReferenceObject $expectedDirtyState -DifferenceObject $finalDirtyState)
) {
    throw '部署包生成期间工作区发生变化；该版本已烧录，部署包已作废。'
}

Assert-HeadMatchesSourceCommit -RepositoryRoot $repositoryRoot -SourceCommit $sourceCommit
Assert-LeafEntryAbsent -LiteralPath $archivePath -Label '正式部署包目标'
[System.IO.File]::Move($partialArchivePath, $archivePath)
$partialArchivePath = $null
$finalArchiveCreated = $true
$archiveInfo = Get-Item -LiteralPath $archivePath -Force
if (
    -not ($archiveInfo -is [System.IO.FileInfo]) -or
    ($archiveInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
) {
    throw '正式部署包目标被重解析点替换，制品已作废。'
}
$finalArchiveLockStream = [System.IO.FileStream]::new(
    $archivePath,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::None
)
try {
    $finalDigest = Assert-ReleaseArchiveStream `
        -Stream $finalArchiveLockStream `
        -ExpectedEntries $entries `
        -ExpectedBlobs $committedBlobs `
        -ExpectedSizes $committedBlobSizes `
        -ExpectedVersion $Version
    if ($finalDigest -ne $digest) {
        throw '正式部署包原子落盘后的 SHA256 发生变化，制品已作废。'
    }
    $archiveInfo = Get-Item -LiteralPath $archivePath -Force
    if (
        -not ($archiveInfo -is [System.IO.FileInfo]) -or
        ($archiveInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw '正式部署包目标在登记前被替换，制品已作废。'
    }
    Assert-HeadMatchesSourceCommit -RepositoryRoot $repositoryRoot -SourceCommit $sourceCommit
    $releaseState.release_in_progress.server_state = 'ready'
    $releaseState.release_in_progress.control_plane_sha256 = $digest
    Write-ReleaseRegistryAtomically -LiteralPath $releaseRegistryPath -Registry $releaseState
    $serverBundleReady = $true
}
finally {
    $finalArchiveLockStream.Dispose()
    $finalArchiveLockStream = $null
}

Write-Output "公司服务器部署包已生成：$archivePath"
Write-Output "源提交：$sourceCommit"
Write-Output "文件数：$($entries.Count + 1)"
Write-Output "大小：$($archiveInfo.Length) bytes"
Write-Output "SHA256：$digest"
Write-Output '版本号已烧录并绑定到上述源提交与部署包哈希；禁止用同一版本重新打包。'
Write-Output '请只提交版本登记表的状态变化，再使用这个精确 SHA256 部署。'
Write-Output '已带入训练完成的共享形象与声音编号；不会重新提交训练。'
Write-Output '包内不含 .env、供应商密钥、数据库、日志、训练样本、媒体、备份或桌面安装包。'
}
catch {
    $originalFailure = $_
    foreach ($disposable in @(
        $archive,
        $archiveFileStream,
        $partialVerificationStream,
        $finalArchiveLockStream
    )) {
        if ($null -ne $disposable) {
            try {
                $disposable.Dispose()
            }
            catch {
                Write-Warning '部署包文件句柄清理失败；临时制品将保留并禁止使用。'
            }
        }
    }
    $archive = $null
    $archiveFileStream = $null
    $partialVerificationStream = $null
    $finalArchiveLockStream = $null
    if ($releaseReserved -and -not $serverBundleReady -and $null -ne $releaseState) {
        try {
            $releaseState.release_in_progress.server_state = 'failed'
            $releaseState.release_in_progress.control_plane_sha256 = $null
            Write-ReleaseRegistryAtomically -LiteralPath $releaseRegistryPath -Registry $releaseState
        }
        catch {
            Write-Warning '无法把版本登记表更新为失败状态；该版本仍视为已经烧录，禁止复用。'
        }
    }
    if (-not $serverBundleReady -and $finalArchiveCreated) {
        Write-Warning '正式部署包已经原子落盘，但版本状态未完成；制品保留作审计且禁止部署或同版重打。'
    }
    if (
        -not $serverBundleReady -and
        -not [string]::IsNullOrWhiteSpace($partialArchivePath) -and
        (Test-Path -LiteralPath $partialArchivePath -PathType Leaf)
    ) {
        try {
            Remove-Item -LiteralPath $partialArchivePath -Force
        }
        catch {
            Write-Warning '部署包临时制品清理失败；该文件保留作审计且禁止使用。'
        }
    }
    throw $originalFailure
}
finally {
    if ($ownsRegistryLock) {
        try {
            if ($null -ne $registryLock) {
                $registryLock.Dispose()
            }
            if (Test-Path -LiteralPath $registryLockPath -PathType Leaf) {
                Remove-Item -LiteralPath $registryLockPath -Force
            }
        }
        catch {
            Write-Warning '正式版本锁清理失败；制品状态不回滚，后续构建会保持 fail-closed。'
        }
    }
}
