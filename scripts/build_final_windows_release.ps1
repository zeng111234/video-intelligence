param(
    [Parameter(Mandatory = $true)][string]$ControlPlaneUrl,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$PaidAcceptanceReport,
    [string]$ControlPlaneVersion = "",
    [string]$Notes = "稳定性与功能更新"
)

$verificationVariables = @(
    "VIDEOINSIGHT_VERIFY_ACTIVATION_CODE",
    "VIDEOINSIGHT_VERIFY_ADMIN_USERNAME",
    "VIDEOINSIGHT_VERIFY_ADMIN_PASSWORD"
)
$paidAcceptanceVariable = "VIDEOINSIGHT_ACCEPTANCE_ACTIVATION_CODE"
$releaseSecretVariables = @($verificationVariables) + @($paidAcceptanceVariable)
$paidAcceptanceValue = $null
$verificationActivationValue = $null
$verificationAdminUsernameValue = $null
$verificationAdminPasswordValue = $null
$registryLock = $null
$ownsRegistryLock = $false
$windowsAttemptStarted = $false
$attemptMarkerCreated = $false
$attemptMarkerPath = $null
$releaseRegistryPath = $null
try {
function Get-ExistingPathAttributesForRelease {
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
    if (-not $entryExists) { return $null }
    try { return [System.IO.File]::GetAttributes($LiteralPath) }
    catch { throw "无法安全检查发布路径（可能是失效链接）：$LiteralPath" }
}

function Assert-NoReparsePointsForReleasePath {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$CandidatePath
    )
    $resolvedRepositoryRoot = [System.IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\', '/')
    $repositoryPrefix = $resolvedRepositoryRoot + [System.IO.Path]::DirectorySeparatorChar
    $resolvedCandidatePath = [System.IO.Path]::GetFullPath($CandidatePath)
    if (-not $resolvedCandidatePath.StartsWith($repositoryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Windows 构建输出路径越出仓库：$resolvedCandidatePath"
    }

    $relativeCandidatePath = $resolvedCandidatePath.Substring($resolvedRepositoryRoot.Length)
    $pathParts = @($relativeCandidatePath -split '[\\/]' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $ancestorPath = $resolvedRepositoryRoot
    $pathsToCheck = @($ancestorPath)
    foreach ($pathPart in $pathParts) {
        $ancestorPath = Join-Path $ancestorPath $pathPart
        $pathsToCheck += $ancestorPath
    }
    for ($pathIndex = 0; $pathIndex -lt $pathsToCheck.Count; $pathIndex += 1) {
        $pathToCheck = $pathsToCheck[$pathIndex]
        $attributes = Get-ExistingPathAttributesForRelease -LiteralPath $pathToCheck
        if ($null -eq $attributes) { break }
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Windows 构建输出路径或其祖先不能是链接或联接点：$pathToCheck"
        }
        if (
            $pathIndex -lt ($pathsToCheck.Count - 1) -and
            ($attributes -band [System.IO.FileAttributes]::Directory) -eq 0
        ) {
            throw "Windows 构建输出路径的祖先不是目录：$pathToCheck"
        }
    }

    if (-not (Test-Path -LiteralPath $resolvedCandidatePath -PathType Container)) {
        return
    }
    $pendingDirectories = New-Object 'System.Collections.Generic.Stack[string]'
    $pendingDirectories.Push($resolvedCandidatePath)
    while ($pendingDirectories.Count -gt 0) {
        $currentDirectory = $pendingDirectories.Pop()
        foreach ($entryPath in [System.IO.Directory]::EnumerateFileSystemEntries($currentDirectory)) {
            $attributes = [System.IO.File]::GetAttributes($entryPath)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Windows 构建输出目录包含链接或联接点：$entryPath"
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

function Get-EmbeddedStableVersionForFinalRelease {
    param(
        [AllowEmptyString()][string]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $match = [regex]::Match($Value, '^\s*([0-9]+)\.([0-9]+)\.([0-9]+)(?:\.0)?\s*$')
    if (-not $match.Success) { throw "客户安装包缺少有效的 $Label，禁止登记 built。" }
    return "{0}.{1}.{2}" -f $match.Groups[1].Value, $match.Groups[2].Value, $match.Groups[3].Value
}

function Read-ReleaseRegistry {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    try {
        return Get-Content -LiteralPath $LiteralPath -Raw | ConvertFrom-Json
    }
    catch {
        throw "正式版本登记表无法读取：$LiteralPath"
    }
}

function Write-ReleaseRegistryAtomically {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][object]$Registry
    )
    Assert-NoReparsePointsForReleasePath `
        -RepositoryRoot $repositoryRoot `
        -CandidatePath $LiteralPath
    $parentPath = Split-Path -Parent $LiteralPath
    $fileName = Split-Path -Leaf $LiteralPath
    $nonce = [guid]::NewGuid().ToString("N")
    $temporaryPath = Join-Path $parentPath ".$fileName.$nonce.tmp"
    $backupPath = Join-Path $parentPath ".$fileName.$nonce.bak"
    $stream = $null
    $registryCommitted = $false
    try {
        $json = ($Registry | ConvertTo-Json -Depth 20) + [Environment]::NewLine
        $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($json)
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
        [System.IO.File]::Replace($temporaryPath, $LiteralPath, $backupPath, $true)
        $registryCommitted = $true
    }
    finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
        if (Test-Path -LiteralPath $temporaryPath) {
            try { Remove-Item -LiteralPath $temporaryPath -Force }
            catch { Write-Warning "正式版本登记临时文件未能清理：$temporaryPath" }
        }
        if (Test-Path -LiteralPath $backupPath) {
            try { Remove-Item -LiteralPath $backupPath -Force }
            catch {
                if ($registryCommitted) {
                    Write-Warning "正式版本登记已原子提交，但旧备份未能清理：$backupPath"
                }
                else { Write-Warning "正式版本登记旧备份未能清理：$backupPath" }
            }
        }
    }
}

function Close-ReleaseRegistryLock {
    if ($null -ne $script:registryLock) {
        $script:registryLock.Dispose()
        $script:registryLock = $null
    }
    if ($script:ownsRegistryLock -and (Test-Path -LiteralPath $script:registryLockPath)) {
        try { Remove-Item -LiteralPath $script:registryLockPath -Force }
        catch {
            Write-Warning "正式版本状态已提交或核验，但锁文件未能清理；请人工核对：$($script:registryLockPath)"
        }
    }
    $script:ownsRegistryLock = $false
}

function Open-ReleaseRegistryLock {
    Assert-NoReparsePointsForReleasePath `
        -RepositoryRoot $repositoryRoot `
        -CandidatePath $registryLockPath
    try {
        $script:registryLock = New-Object System.IO.FileStream(
            $registryLockPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $script:ownsRegistryLock = $true
    }
    catch {
        throw "另一个正式发布步骤正在运行，或上次状态写入异常中断；请先人工核对锁文件：$registryLockPath"
    }
}

function Assert-TrackedCleanReleaseInputs {
    param([Parameter(Mandatory = $true)][string[]]$RelativePaths)
    foreach ($relativePath in $RelativePaths) {
        & git -C $repositoryRoot ls-files --error-unmatch -- $relativePath *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "正式验收输入必须先纳入 Git 跟踪：$relativePath"
        }
    }
    $dirtyEntries = @(& git -C $repositoryRoot status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        throw "无法确认 Git 工作区状态；禁止让正式凭据进入验收子进程。"
    }
    if ($dirtyEntries.Count -gt 0) {
        throw "Git 工作区存在未提交或未跟踪内容；禁止让正式凭据进入验收子进程。"
    }
}

function Assert-ReleaseSourceCommit {
    param(
        [Parameter(Mandatory = $true)][string]$SourceCommit,
        [Parameter(Mandatory = $true)][string]$RegistryRelativePath
    )
    if ($SourceCommit -notmatch '^[0-9a-fA-F]{40,64}$') {
        throw "正式发布源提交无效。"
    }
    & git -C $repositoryRoot merge-base --is-ancestor $SourceCommit HEAD *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "正式发布源提交不是当前 HEAD 的祖先，禁止混用其他源码构建 Windows 包。"
    }
    $changedPaths = @(& git -C $repositoryRoot diff --name-only "$SourceCommit..HEAD" --)
    if ($LASTEXITCODE -ne 0) {
        throw "无法核对正式发布源提交与当前 HEAD。"
    }
    $normalizedChangedPaths = @(
        $changedPaths |
            ForEach-Object { ([string]$_).Trim().Replace('\', '/') } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if (
        $normalizedChangedPaths.Count -ne 1 -or
        $normalizedChangedPaths[0] -ne $RegistryRelativePath
    ) {
        throw "正式发布源提交之后只允许提交版本登记表；检测到其他源码变化。"
    }
}

function Assert-WindowsReleaseRegistryState {
    param(
        [Parameter(Mandatory = $true)][object]$Registry,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion
    )
    $expectedTopLevelProperties = "completed_releases,current_candidate,release_in_progress,schema_version,superseded_releases,used_versions"
    $topLevelProperties = (@($Registry.PSObject.Properties.Name) | Sort-Object) -join ','
    $legacyTopLevelProperties = "completed_releases,current_candidate,release_in_progress,schema_version,used_versions"
    if (
        $topLevelProperties -notin @($expectedTopLevelProperties, $legacyTopLevelProperties) -or
        [int]$Registry.schema_version -ne 2
    ) {
        throw "正式版本登记表 schema_version 必须为 2。"
    }
    if (-not ($Registry.used_versions -is [System.Array]) -or $Registry.used_versions.Count -eq 0) {
        throw "正式版本登记表 used_versions 必须是非空数组。"
    }
    $usedVersions = @($Registry.used_versions | ForEach-Object { [string]$_ })
    $invalidUsedVersions = @($usedVersions | Where-Object { $_ -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' })
    $uniqueUsedVersions = @($usedVersions | Sort-Object -Unique)
    if (
        $invalidUsedVersions.Count -gt 0 -or
        $uniqueUsedVersions.Count -ne $usedVersions.Count -or
        @($usedVersions | Where-Object { $_ -eq $ExpectedVersion }).Count -ne 1
    ) {
        throw "版本 $ExpectedVersion 尚未由公司服务器部署包阶段锁定并烧录。"
    }
    $highestUsedVersion = $usedVersions | ForEach-Object { [version]$_ } | Sort-Object -Descending | Select-Object -First 1
    if ([version]$ExpectedVersion -ne $highestUsedVersion) {
        throw "版本 $ExpectedVersion 必须是 used_versions 中唯一的最高版本。"
    }
    if ($null -ne $Registry.current_candidate) {
        throw "Windows 构建期间 current_candidate 必须为空。"
    }
    if (-not ($Registry.completed_releases -is [System.Array])) {
        throw "正式版本登记表 completed_releases 必须是数组。"
    }
    if ($null -ne $Registry.PSObject.Properties['superseded_releases'] -and -not ($Registry.superseded_releases -is [System.Array])) {
        throw "正式版本登记表 superseded_releases 必须是数组。"
    }
    $completedVersions = @()
    foreach ($completedRelease in @($Registry.completed_releases)) {
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
    if (@($completedVersions | Sort-Object -Unique).Count -ne $completedVersions.Count) {
        throw "正式版本登记表 completed_releases 版本重复。"
    }
    $inProgressProperty = $Registry.PSObject.Properties['release_in_progress']
    if ($null -eq $inProgressProperty -or $null -eq $inProgressProperty.Value) {
        throw "缺少公司服务器部署包阶段写入的 release_in_progress。"
    }
    $releaseInProgress = $inProgressProperty.Value
    $inProgressProperties = (@($releaseInProgress.PSObject.Properties.Name) | Sort-Object) -join ','
    if (
        $inProgressProperties -ne "control_plane_sha256,installer_sha256,server_state,source_commit,version,windows_state" -or
        [string]$releaseInProgress.version -ne $ExpectedVersion -or
        [string]$releaseInProgress.source_commit -notmatch '^[0-9a-f]{40}$' -or
        $completedVersions -contains $ExpectedVersion
    ) {
        throw "release_in_progress 版本与 Windows 构建版本不一致。"
    }
    if ([string]$releaseInProgress.server_state -ne "ready") {
        throw "公司服务器部署包尚未进入 ready 状态，禁止构建 Windows 包。"
    }
    if ([string]$releaseInProgress.control_plane_sha256 -notmatch '^[0-9a-fA-F]{64}$') {
        throw "公司服务器部署包缺少有效的 control_plane_sha256。"
    }
    $windowsState = [string]$releaseInProgress.windows_state
    if ($windowsState -notin @("pending", "building", "attempted", "built")) {
        throw "Windows 发布状态 $windowsState 不允许继续当前版本。"
    }
    if (
        $windowsState -in @("pending", "building", "attempted") -and
        -not [string]::IsNullOrWhiteSpace([string]$releaseInProgress.installer_sha256)
    ) {
        throw "Windows 发布状态 $windowsState 不应已有 installer_sha256。"
    }
    if (
        $windowsState -eq "built" -and
        [string]$releaseInProgress.installer_sha256 -notmatch '^[0-9a-fA-F]{64}$'
    ) {
        throw "built 状态缺少有效的 installer_sha256。"
    }
    return $releaseInProgress
}

function Get-ValidatedControlPlaneOrigin {
    param([Parameter(Mandatory = $true)][string]$Value)
    try {
        $uri = New-Object System.Uri($Value.Trim())
    }
    catch {
        throw "公司服务地址必须是纯 HTTPS 域名。"
    }
    if (
        -not $uri.IsAbsoluteUri -or
        $uri.Scheme -ne "https" -or
        -not [string]::IsNullOrWhiteSpace($uri.UserInfo) -or
        ($uri.AbsolutePath -ne "/" -and -not [string]::IsNullOrWhiteSpace($uri.AbsolutePath)) -or
        -not [string]::IsNullOrWhiteSpace($uri.Query) -or
        -not [string]::IsNullOrWhiteSpace($uri.Fragment)
    ) {
        throw "公司服务地址必须是纯 HTTPS 域名，不能包含账号、参数或子路径。"
    }
    $hostname = $uri.DnsSafeHost.ToLowerInvariant()
    if (
        $hostname -eq "localhost" -or
        $hostname -in @("example.com", "example.net", "example.org") -or
        $hostname.EndsWith(".localhost") -or
        $hostname.EndsWith(".test") -or
        $hostname.EndsWith(".invalid") -or
        $hostname.EndsWith(".example")
    ) {
        throw "公司服务地址不能使用本机或示例域名。"
    }
    $parsedAddress = $null
    if ([System.Net.IPAddress]::TryParse($hostname, [ref]$parsedAddress)) {
        if (
            [System.Net.IPAddress]::IsLoopback($parsedAddress) -or
            $parsedAddress.Equals([System.Net.IPAddress]::Any) -or
            $parsedAddress.Equals([System.Net.IPAddress]::IPv6Any)
        ) {
            throw "公司服务地址不能使用本机或未指定 IP。"
        }
    }
    return $uri.AbsoluteUri.TrimEnd('/')
}

function Invoke-ReleaseJsonRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [AllowNull()][object]$Body = $null,
        [hashtable]$Headers = @{}
    )
    Add-Type -AssemblyName System.Net.Http
    $bodyJson = if ($null -ne $Body) {
        $Body | ConvertTo-Json -Depth 20 -Compress
    } else {
        $null
    }
    for ($attempt = 0; $attempt -lt 2; $attempt += 1) {
        $handler = New-Object System.Net.Http.HttpClientHandler
        $handler.AllowAutoRedirect = $false
        $handler.UseCookies = $false
        $handler.UseProxy = $false
        $client = New-Object System.Net.Http.HttpClient($handler)
        $client.Timeout = [TimeSpan]::FromSeconds(45)
        $request = New-Object System.Net.Http.HttpRequestMessage(
            (New-Object System.Net.Http.HttpMethod($Method.ToUpperInvariant())),
            $Uri
        )
        $response = $null
        try {
            [void]$request.Headers.TryAddWithoutValidation("Accept", "application/json")
            foreach ($headerName in $Headers.Keys) {
                [void]$request.Headers.TryAddWithoutValidation($headerName, [string]$Headers[$headerName])
            }
            if ($null -ne $bodyJson) {
                $request.Content = New-Object System.Net.Http.StringContent(
                    $bodyJson,
                    (New-Object System.Text.UTF8Encoding($false)),
                    "application/json"
                )
            }
            try {
                $response = $client.SendAsync($request).GetAwaiter().GetResult()
            }
            catch {
                if ($attempt -eq 0) {
                    Start-Sleep -Milliseconds 750
                    continue
                }
                throw
            }
            $statusCode = [int]$response.StatusCode
            if ($statusCode -in @(301, 302, 303, 307, 308)) {
                throw "正式公司服务返回了重定向，已拒绝继续发送或使用凭据。"
            }
            $rawBody = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            $payload = $null
            if (-not [string]::IsNullOrWhiteSpace($rawBody)) {
                try { $payload = $rawBody | ConvertFrom-Json }
                catch { throw "正式公司服务返回了无效 JSON。" }
            }
            return [pscustomobject]@{ StatusCode = $statusCode; Payload = $payload }
        }
        finally {
            if ($null -ne $response) { $response.Dispose() }
            $request.Dispose()
            $client.Dispose()
            $handler.Dispose()
        }
    }
    throw "正式公司服务连接失败。"
}

function Invoke-ControlPlaneAuthoritativeGate {
    param(
        [Parameter(Mandatory = $true)][string]$Origin,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion,
        [Parameter(Mandatory = $true)][string]$ActivationCode,
        [Parameter(Mandatory = $true)][string]$AdminUsername,
        [Parameter(Mandatory = $true)][string]$AdminPassword
    )
    $health = Invoke-ReleaseJsonRequest -Method "GET" -Uri "$Origin/health"
    if (
        $health.StatusCode -ne 200 -or
        [string]$health.Payload.status -ne "ok" -or
        [string]$health.Payload.release_version -ne $ExpectedVersion
    ) {
        throw "正式公司服务 health 未绑定当前发布版本。"
    }
    $ready = Invoke-ReleaseJsonRequest -Method "GET" -Uri "$Origin/ready"
    if ($ready.StatusCode -ne 200 -or [string]$ready.Payload.status -ne "ready") {
        throw "正式公司服务数据库尚未 ready。"
    }
    $customerLogin = Invoke-ReleaseJsonRequest `
        -Method "POST" `
        -Uri "$Origin/api/v1/auth/customer-login" `
        -Body @{ code = $ActivationCode }
    $customerToken = [string]$customerLogin.Payload.token
    if ($customerLogin.StatusCode -ne 200 -or [string]::IsNullOrWhiteSpace($customerToken)) {
        throw "正式验收客户账号无法登录。"
    }
    $credits = Invoke-ReleaseJsonRequest `
        -Method "GET" `
        -Uri "$Origin/api/v1/credits" `
        -Headers @{ "X-Customer-Token" = $customerToken }
    if ($credits.StatusCode -ne 200) {
        throw "正式验收客户账号无法读取自己的积分。"
    }
    $adminLogin = Invoke-ReleaseJsonRequest `
        -Method "POST" `
        -Uri "$Origin/api/v1/auth/admin-login" `
        -Body @{ username = $AdminUsername; password = $AdminPassword }
    $adminToken = [string]$adminLogin.Payload.token
    if ($adminLogin.StatusCode -ne 200 -or [string]::IsNullOrWhiteSpace($adminToken)) {
        throw "正式验收管理员账号无法登录。"
    }
    $status = Invoke-ReleaseJsonRequest `
        -Method "GET" `
        -Uri "$Origin/api/v1/admin/server-status" `
        -Headers @{ "X-Admin-Token" = $adminToken }
    $payload = $status.Payload
    $avatarAsset = $payload.avatar.required_shared_assets.avatar
    $voiceAsset = $payload.avatar.required_shared_assets.voice
    if (
        $status.StatusCode -ne 200 -or
        [string]$payload.service -ne "ready" -or
        [string]$payload.crawler.location -ne "customer_desktop" -or
        [bool]$payload.crawler.billable -ne $false -or
        [bool]$payload.crawler.server_provider_disabled -ne $true -or
        [string]$payload.copywriting.mode -ne "production" -or
        [bool]$payload.copywriting.enabled -ne $true -or
        [string]$payload.transcription.provider_mode -ne "aliyun" -or
        [string]$payload.transcription.provider_name -ne "aliyun_fun_asr" -or
        [bool]$payload.transcription.live_ready -ne $true -or
        [bool]$payload.transcription.enabled -ne $true -or
        [bool]$payload.transcription.is_mock -ne $false -or
        [bool]$payload.transcription.billing_authorized -ne $true -or
        @($payload.transcription.missing_configuration).Count -ne 0 -or
        [string]$payload.video_editor.provider_mode -ne "aliyun" -or
        [string]$payload.video_editor.provider_name -ne "aliyun_cloud_editor" -or
        [bool]$payload.video_editor.live_ready -ne $true -or
        [bool]$payload.video_editor.enabled -ne $true -or
        [bool]$payload.video_editor.is_mock -ne $false -or
        @($payload.video_editor.missing_configuration).Count -ne 0 -or
        [string]$payload.avatar.mode -ne "production" -or
        [string]$payload.avatar.provider_name -ne "shuying_legacy_cloud" -or
        [bool]$payload.avatar.enabled -ne $true -or
        @($payload.avatar.missing_configuration).Count -ne 0 -or
        [string]$avatarAsset.asset_id -ne "shuying-avatar-21920" -or
        [string]$avatarAsset.provider_asset_id -ne "21920" -or
        [bool]$avatarAsset.ready -ne $true -or
        [string]$voiceAsset.asset_id -ne "shuying-voice-7869" -or
        [string]$voiceAsset.provider_asset_id -ne "7869" -or
        [bool]$voiceAsset.ready -ne $true
    ) {
        throw "正式供应商配置、计费授权或固定共享资产未全部 ready。"
    }
}

function ConvertTo-CanonicalReleaseProofObject {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [System.Collections.IDictionary]) {
        $orderedDictionary = [ordered]@{}
        foreach ($key in @($Value.Keys) | Sort-Object) {
            $orderedDictionary[[string]$key] = ConvertTo-CanonicalReleaseProofObject $Value[$key]
        }
        return [pscustomobject]$orderedDictionary
    }
    if ($Value -is [pscustomobject]) {
        $orderedObject = [ordered]@{}
        foreach ($property in @($Value.PSObject.Properties) | Sort-Object Name) {
            $orderedObject[$property.Name] = ConvertTo-CanonicalReleaseProofObject $property.Value
        }
        return [pscustomobject]$orderedObject
    }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        $orderedItems = @(
            foreach ($item in $Value) {
                ConvertTo-CanonicalReleaseProofObject $item
            }
        )
        Write-Output -NoEnumerate $orderedItems
        return
    }
    return $Value
}

function ConvertTo-CanonicalReleaseProofJson {
    param([Parameter(Mandatory = $true)][object]$Value)
    return ConvertTo-CanonicalReleaseProofObject $Value |
        ConvertTo-Json -Depth 30 -Compress
}

function Assert-PaidAcceptanceAuthoritativeProof {
    param(
        [Parameter(Mandatory = $true)][string]$Origin,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion,
        [Parameter(Mandatory = $true)][string]$ReportPath,
        [Parameter(Mandatory = $true)][string]$ActivationCode
    )
    try { $report = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json }
    catch { throw "无法读取真实付费验收报告。" }
    $runId = [string]$report.run_id
    $manifestHash = [string]$report.evidence_manifest_sha256
    if (
        [int]$report.schema_version -ne 1 -or
        [string]$report.status -ne "passed" -or
        [string]$report.acceptance_scope -ne "paid_execution" -or
        [bool]$report.paid_execution_performed -ne $true -or
        [string]$report.target_origin.TrimEnd('/') -ne $Origin -or
        [string]$report.release_version -ne $ExpectedVersion -or
        $runId -notmatch '^[0-9a-f]{32}$' -or
        $manifestHash -notmatch '^[0-9a-f]{64}$'
    ) {
        throw "真实付费验收报告未绑定当前正式地址、版本、运行或证据清单。"
    }
    $finishedAt = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse([string]$report.finished_at, [ref]$finishedAt)) {
        throw "真实付费验收报告完成时间无效。"
    }
    $now = [DateTimeOffset]::Now
    if ($finishedAt -gt $now.AddMinutes(5) -or $finishedAt -lt $now.AddHours(-24)) {
        throw "真实付费验收报告已过期或时间异常。"
    }
    try {
        $budget = [decimal][string]$report.budget_credits
        $spent = [decimal][string]$report.actual_spent_credits
        $initial = [decimal][string]$report.initial_balance_credits
        $final = [decimal][string]$report.final_balance_credits
    }
    catch { throw "真实付费验收报告费用字段无效。" }
    if (
        $budget -le 0 -or $budget -gt 5 -or
        $spent -lt 0 -or $spent -gt $budget -or
        $initial -lt 0 -or $initial -gt 5 -or
        ($initial - $final) -ne $spent
    ) {
        throw "真实付费验收报告费用超出硬门禁或账目不平。"
    }
    $customerLogin = Invoke-ReleaseJsonRequest `
        -Method "POST" `
        -Uri "$Origin/api/v1/auth/customer-login" `
        -Body @{ code = $ActivationCode }
    $customerToken = [string]$customerLogin.Payload.token
    if ($customerLogin.StatusCode -ne 200 -or [string]::IsNullOrWhiteSpace($customerToken)) {
        throw "在线报告核验无法登录验收服务器。"
    }
    $escapedManifestHash = [Uri]::EscapeDataString($manifestHash)
    $proof = Invoke-ReleaseJsonRequest `
        -Method "GET" `
        -Uri "$Origin/api/v1/provider/release-acceptance/proof/$runId`?evidence_manifest_sha256=$escapedManifestHash" `
        -Headers @{ "X-Customer-Token" = $customerToken }
    if ($proof.StatusCode -ne 200) {
        throw "正式服务器不存在与该报告匹配的在线证明。"
    }
    $online = $proof.Payload
    if (
        [int]$online.schema_version -ne 1 -or
        [string]$online.release_version -ne $ExpectedVersion -or
        [string]$online.run_id -ne $runId -or
        [string]$online.evidence_manifest_sha256 -ne $manifestHash -or
        [string]$online.provider -ne "shuying_legacy_cloud" -or
        [string]$online.avatar_id -ne "shuying-avatar-21920" -or
        [string]$online.voice_id -ne "shuying-voice-7869" -or
        [string]$online.ledger.digest_sha256 -notmatch '^[0-9a-f]{64}$'
    ) {
        throw "服务器在线证明与报告版本、运行、固定资产或账本摘要不一致。"
    }
    $validatedAt = [DateTimeOffset]::MinValue
    $expiresAt = [DateTimeOffset]::MinValue
    if (
        -not [DateTimeOffset]::TryParse([string]$online.validated_at, [ref]$validatedAt) -or
        -not [DateTimeOffset]::TryParse([string]$online.expires_at, [ref]$expiresAt) -or
        $expiresAt -le $validatedAt -or
        $expiresAt -gt $validatedAt.AddHours(24) -or
        $validatedAt -gt $now.AddMinutes(5) -or
        $expiresAt -lt $now
    ) {
        throw "服务器在线证明已过期或有效期无效。"
    }
    $expectedCapabilityNames = "asr,avatar,copywriting,video_editor"
    $onlineCapabilityNames = (@($online.capabilities.PSObject.Properties.Name) | Sort-Object) -join ','
    $reportCapabilityNames = (@($report.capabilities.PSObject.Properties.Name) | Sort-Object) -join ','
    if (
        $onlineCapabilityNames -ne $expectedCapabilityNames -or
        $reportCapabilityNames -ne $expectedCapabilityNames
    ) {
        throw "服务器在线证明必须且只能包含四项付费能力。"
    }
    foreach ($capabilityName in @("copywriting", "asr", "video_editor", "avatar")) {
        $onlineCapability = $online.capabilities.$capabilityName
        $reportCapability = $report.capabilities.$capabilityName
        if (
            [string]$onlineCapability.status -ne "passed" -or
            [string]$reportCapability.status -ne "passed" -or
            [string]$onlineCapability.result_sha256 -notmatch '^[0-9a-f]{64}$' -or
            [string]$onlineCapability.result_sha256 -ne [string]$reportCapability.result_sha256
        ) {
            throw "服务器在线证明的 $capabilityName 结果哈希与报告不一致。"
        }
    }
    if (
        [double]$online.capabilities.video_editor.duration_seconds -ne
            [double]$report.capabilities.video_editor.result_duration_seconds -or
        [double]$online.capabilities.avatar.duration_seconds -ne
            [double]$report.capabilities.avatar.duration_seconds -or
        [int]$online.capabilities.avatar.billed_seconds -ne
            [int]$report.capabilities.avatar.billed_seconds
    ) {
        throw "服务器在线证明的成片时长或数字人计费秒数与报告不一致。"
    }
    $expectedTransactionIds = @(
        @($report.new_transactions | ForEach-Object { [int64]$_.id }) | Sort-Object
    )
    $onlineTransactionIds = @(
        @($online.ledger.transaction_ids | ForEach-Object { [int64]$_ }) | Sort-Object
    )
    if (
        $expectedTransactionIds.Count -eq 0 -or
        ($expectedTransactionIds -join ',') -ne ($onlineTransactionIds -join ',')
    ) {
        throw "服务器在线证明的账本流水编号与报告不一致。"
    }
    if ($null -eq $report.server_proof) {
        throw "真实付费验收报告缺少保存的服务器证明。"
    }
    $storedProofJson = ConvertTo-CanonicalReleaseProofJson $report.server_proof
    $onlineProofJson = ConvertTo-CanonicalReleaseProofJson $online
    if ($storedProofJson -ne $onlineProofJson) {
        throw "在线证明与报告保存的服务器证明不一致。"
    }
}

function Test-ByteSequenceInArray {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Buffer,
        [Parameter(Mandatory = $true)][byte[]]$Pattern
    )
    if ($Pattern.Length -eq 0 -or $Buffer.Length -lt $Pattern.Length) { return $false }
    $searchIndex = 0
    while ($searchIndex -le ($Buffer.Length - $Pattern.Length)) {
        $candidate = [Array]::IndexOf($Buffer, $Pattern[0], $searchIndex)
        if ($candidate -lt 0 -or $candidate + $Pattern.Length -gt $Buffer.Length) {
            return $false
        }
        $matches = $true
        for ($offset = 1; $offset -lt $Pattern.Length; $offset += 1) {
            if ($Buffer[$candidate + $offset] -ne $Pattern[$offset]) {
                $matches = $false
                break
            }
        }
        if ($matches) { return $true }
        $searchIndex = $candidate + 1
    }
    return $false
}

function Test-FileContainsBytePattern {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][byte[]]$Pattern
    )
    $stream = [System.IO.File]::OpenRead($LiteralPath)
    try {
        $chunk = New-Object byte[] (1024 * 1024)
        $tail = New-Object byte[] 0
        while (($read = $stream.Read($chunk, 0, $chunk.Length)) -gt 0) {
            $window = New-Object byte[] ($tail.Length + $read)
            if ($tail.Length -gt 0) {
                [Array]::Copy($tail, 0, $window, 0, $tail.Length)
            }
            [Array]::Copy($chunk, 0, $window, $tail.Length, $read)
            if (Test-ByteSequenceInArray -Buffer $window -Pattern $Pattern) { return $true }
            $tailLength = [Math]::Min([Math]::Max(0, $Pattern.Length - 1), $window.Length)
            $tail = New-Object byte[] $tailLength
            if ($tailLength -gt 0) {
                [Array]::Copy($window, $window.Length - $tailLength, $tail, 0, $tailLength)
            }
        }
        return $false
    }
    finally {
        $stream.Dispose()
    }
}

function Assert-WindowsReleasePayloadAuthoritative {
    param(
        [Parameter(Mandatory = $true)][string]$PackageRoot,
        [Parameter(Mandatory = $true)][string]$Origin,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$SecretEnvironmentFiles
    )
    Assert-NoReparsePointsForReleasePath -RepositoryRoot $repositoryRoot -CandidatePath $PackageRoot
    $requiredFiles = @{
        desktop = Join-Path $PackageRoot "VideoInsight.exe"
        backend = Join-Path $PackageRoot "resources\backend\VideoInsightBackend.exe"
        desktop_config = Join-Path $PackageRoot "resources\config\release.json"
        backend_config = Join-Path $PackageRoot "resources\backend\_internal\config\desktop-control-plane.json"
        ffmpeg = Join-Path $PackageRoot "resources\backend\_internal\media\ffmpeg.exe"
        ffprobe = Join-Path $PackageRoot "resources\backend\_internal\media\ffprobe.exe"
        media_license = Join-Path $PackageRoot "resources\backend\_internal\media\LICENSE"
        media_readme = Join-Path $PackageRoot "resources\backend\_internal\media\README.txt"
        media_manifest = Join-Path $PackageRoot "resources\backend\_internal\media\windows-media-tools.sha256"
    }
    foreach ($requiredFile in $requiredFiles.GetEnumerator()) {
        if (-not (Test-Path -LiteralPath $requiredFile.Value -PathType Leaf)) {
            throw "Windows 客户包缺少必要文件：$($requiredFile.Key)"
        }
    }
    $expectedMediaHashes = @{}
    foreach ($line in Get-Content -LiteralPath $requiredFiles.media_manifest -Encoding UTF8) {
        if ($line -match '^([0-9a-f]{64})  (LICENSE|README\.txt|ffmpeg\.exe|ffprobe\.exe)$') {
            $expectedMediaHashes[$Matches[2]] = $Matches[1]
        }
    }
    if ($expectedMediaHashes.Count -ne 4) {
        throw "Windows 媒体组件校验清单无效。"
    }
    $mediaFiles = @{
        "LICENSE" = $requiredFiles.media_license
        "README.txt" = $requiredFiles.media_readme
        "ffmpeg.exe" = $requiredFiles.ffmpeg
        "ffprobe.exe" = $requiredFiles.ffprobe
    }
    foreach ($mediaFile in $mediaFiles.GetEnumerator()) {
        $actualMediaHash = Get-Sha256Hex -LiteralPath $mediaFile.Value
        if ($actualMediaHash -ne $expectedMediaHashes[$mediaFile.Key]) {
            throw "Windows 媒体组件校验失败：$($mediaFile.Key)"
        }
    }
    $trustedMediaBinaryPaths = @(
        [IO.Path]::GetFullPath($requiredFiles.ffmpeg),
        [IO.Path]::GetFullPath($requiredFiles.ffprobe)
    )
    try {
        $desktopConfig = Get-Content -LiteralPath $requiredFiles.desktop_config -Raw | ConvertFrom-Json
        $backendConfig = Get-Content -LiteralPath $requiredFiles.backend_config -Raw | ConvertFrom-Json
    }
    catch { throw "Windows 客户包发布配置不是有效 JSON。" }
    if (
        [string]$desktopConfig.current_version -ne $ExpectedVersion -or
        [string]$desktopConfig.control_plane_url.TrimEnd('/') -ne $Origin -or
        [bool]$backendConfig.enabled -ne $true -or
        [string]$backendConfig.control_plane_url.TrimEnd('/') -ne $Origin
    ) {
        throw "Windows 客户包配置未绑定当前正式公司地址和版本。"
    }
    $forbiddenDirectories = @(".git", "backups", "browser_profiles", "data", "logs", "outputs", "uploads")
    $forbiddenExtensions = @(
        ".db", ".key", ".log", ".m4a", ".mkv", ".mov", ".mp3", ".mp4",
        ".p12", ".pfx", ".pem", ".der", ".p8", ".ppk",
        ".sqlite", ".sqlite3", ".wav", ".webm"
    )
    $packageFiles = @(Get-ChildItem -LiteralPath $PackageRoot -File -Recurse -Force)
    foreach ($packageFile in $packageFiles) {
        $relativePath = $packageFile.FullName.Substring($PackageRoot.TrimEnd('\', '/').Length + 1)
        $segments = @($relativePath -split '[\\/]')
        $parentSegments = if ($segments.Count -gt 1) { @($segments[0..($segments.Count - 2)]) } else { @() }
        $isTrustedCertifiCaBundle = [string]::Equals(
            $relativePath,
            "resources\backend\_internal\certifi\cacert.pem",
            [StringComparison]::OrdinalIgnoreCase
        )
        $isTrustedBuiltinTemplate = [string]::Equals(
            $relativePath,
            "resources\backend\_internal\data\templates\builtin.json",
            [StringComparison]::OrdinalIgnoreCase
        )
        if (
            (
                @($parentSegments | Where-Object { $forbiddenDirectories -contains $_.ToLowerInvariant() }).Count -gt 0 -and
                -not $isTrustedBuiltinTemplate
            ) -or
            $packageFile.Name -eq ".env" -or
            $packageFile.Name.StartsWith(".env.", [StringComparison]::OrdinalIgnoreCase) -or
            (
                $forbiddenExtensions -contains $packageFile.Extension.ToLowerInvariant() -and
                -not $isTrustedCertifiCaBundle
            )
        ) {
            throw "Windows 客户包包含不允许的密钥、运行数据或媒体文件：$relativePath"
        }
    }
    $sensitiveKeys = @(
        "APP_SECRET_KEY", "API_KEY", "ADMIN_PASSWORD", "POSTGRES_PASSWORD",
        "VIDEOINSIGHT_WORKER_TOKEN",
        "DASHSCOPE_API_KEY", "ALIYUN_MODEL_STUDIO_WORKSPACE_ID",
        "ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
        "ALIYUN_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_SECRET",
        "ALIYUN_ASR_ACCESS_KEY_ID", "ALIYUN_ASR_ACCESS_KEY_SECRET", "ALIYUN_ASR_APP_KEY",
        "ONEAPI_API_KEY", "DOUYIN_CLIENT_KEY", "DOUYIN_CLIENT_SECRET",
        "COPYWRITING_API_KEY", "OPENAI_API_KEY", "AVATAR_API_KEY",
        "SHUYING_AVATAR_API_CODE", "AVATAR_SERVICE_TOKEN",
        "BAIDU_XILING_APP_ID", "BAIDU_XILING_APP_KEY",
        "PUBLISH_DOUYIN_CLIENT_KEY", "PUBLISH_DOUYIN_CLIENT_SECRET",
        "PUBLISH_DOUYIN_ACCESS_TOKEN", "PUBLISH_DOUYIN_REFRESH_TOKEN",
        "PUBLISH_KUAISHOU_CLIENT_KEY", "PUBLISH_KUAISHOU_CLIENT_SECRET",
        "PUBLISH_KUAISHOU_ACCESS_TOKEN", "PUBLISH_WECHAT_CHANNELS_CLIENT_KEY",
        "PUBLISH_WECHAT_CHANNELS_CLIENT_SECRET", "PUBLISH_WECHAT_CHANNELS_ACCESS_TOKEN",
        "PUBLISH_XIAOHONGSHU_CLIENT_KEY", "PUBLISH_XIAOHONGSHU_CLIENT_SECRET",
        "PUBLISH_XIAOHONGSHU_ACCESS_TOKEN"
    )
    $publicSecretPlaceholders = @(
        "change-me", "changeme", "placeholder", "replace-me",
        "your-secret-key-change-this", "change_me_at_least_16_characters",
        "postgres"
    )
    $secretValues = New-Object 'System.Collections.Generic.List[string]'
    foreach ($key in $sensitiveKeys) {
        $value = [Environment]::GetEnvironmentVariable($key, "Process")
        if (
            -not [string]::IsNullOrWhiteSpace($value) -and
            $value.Trim().Length -ge 8 -and
            $value.Trim().ToLowerInvariant() -notin $publicSecretPlaceholders
        ) {
            $secretValues.Add($value.Trim())
        }
    }
    foreach ($secretFile in $SecretEnvironmentFiles) {
        if (-not (Test-Path -LiteralPath $secretFile -PathType Leaf)) { continue }
        $secretFileInfo = Get-Item -LiteralPath $secretFile
        if ($secretFileInfo.Length -gt 1MB) { throw "构建机环境文件过大，拒绝读取：$secretFile" }
        foreach ($line in Get-Content -LiteralPath $secretFile -Encoding UTF8) {
            $trimmed = ([string]$line).Trim()
            if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
            $parts = $trimmed.Split('=', 2)
            $key = $parts[0].Trim().ToUpperInvariant()
            if ($sensitiveKeys -notcontains $key) { continue }
            $value = $parts[1].Trim().Trim('"', "'")
            if (
                $value.Length -ge 8 -and
                $value.ToLowerInvariant() -notin $publicSecretPlaceholders
            ) {
                $secretValues.Add($value)
            }
        }
    }
    $patterns = New-Object 'System.Collections.Generic.List[object]'
    foreach ($marker in @(
        "-----BEGIN PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----", "-----BEGIN DSA PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----", "-----BEGIN OPENSSH PRIVATE KEY-----",
        "PuTTY-User-Key-File:"
    )) {
        $patterns.Add([pscustomobject]@{ Name = "PRIVATE_KEY"; Bytes = [Text.Encoding]::ASCII.GetBytes($marker) })
    }
    foreach ($secretValue in @($secretValues | Sort-Object -Unique)) {
        foreach ($encoding in @(
            (New-Object System.Text.UTF8Encoding($false)),
            [Text.Encoding]::Unicode,
            [Text.Encoding]::BigEndianUnicode
        )) {
            $patterns.Add([pscustomobject]@{ Name = "CONFIGURED_SECRET"; Bytes = $encoding.GetBytes($secretValue) })
        }
    }
    foreach ($packageFile in $packageFiles) {
        if ($trustedMediaBinaryPaths -contains [IO.Path]::GetFullPath($packageFile.FullName)) {
            continue
        }
        foreach ($pattern in $patterns) {
            if (Test-FileContainsBytePattern -LiteralPath $packageFile.FullName -Pattern $pattern.Bytes) {
                throw "Windows 客户包包含构建机供应商密钥或私钥标记：$($packageFile.FullName)"
            }
        }
    }
}

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$releasePython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$unpacked = Join-Path $repositoryRoot "project\frontend\release\win-unpacked"
$releaseRegistryRelativePath = "deploy/control-plane/release_versions.json"
$releaseRegistryPath = Join-Path $repositoryRoot "deploy\control-plane\release_versions.json"
$registryLockPath = "$releaseRegistryPath.lock"
$updatesRoot = Join-Path $repositoryRoot "deploy\control-plane\updates"
$manifestPath = Join-Path $updatesRoot "latest.json"
$deliveryDirectory = Join-Path $repositoryRoot "build\final-windows-release-$Version"
$installer = Join-Path $deliveryDirectory "VideoInsight-$Version-Setup.exe"
$publishedInstaller = Join-Path $updatesRoot "VideoInsight-$Version-Setup.exe"
$attemptMarkerPath = Join-Path $repositoryRoot "build\.windows-release-$Version.attempted"
$windowsBuildOutputPaths = @(
    (Join-Path $repositoryRoot "build\pyinstaller"),
    (Join-Path $repositoryRoot "project\frontend\dist"),
    (Join-Path $repositoryRoot "project\frontend\desktop\backend"),
    (Join-Path $repositoryRoot "project\frontend\desktop\release-config.json"),
    (Join-Path $repositoryRoot "project\frontend\desktop\icon.ico"),
    (Join-Path $repositoryRoot "project\frontend\desktop\icon.png"),
    (Join-Path $repositoryRoot "project\frontend\release"),
    $deliveryDirectory,
    $manifestPath,
    $publishedInstaller,
    $releaseRegistryPath,
    $registryLockPath,
    $attemptMarkerPath
)
foreach ($windowsBuildOutputPath in $windowsBuildOutputPaths) {
    Assert-NoReparsePointsForReleasePath `
        -RepositoryRoot $repositoryRoot `
        -CandidatePath $windowsBuildOutputPath
}

if ([string]::IsNullOrWhiteSpace($ControlPlaneVersion)) {
    $ControlPlaneVersion = $Version
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "正式版本号必须使用三段纯数字，例如 0.2.1。"
}
if ($ControlPlaneVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "正式控制层版本号必须使用三段纯数字，例如 0.2.1。"
}
if (-not (Test-Path -LiteralPath $releaseRegistryPath -PathType Leaf)) {
    throw "缺少受版本控制的正式版本登记表：$releaseRegistryPath"
}

if (Test-Path -LiteralPath $manifestPath) {
    $manifestItem = Get-Item -LiteralPath $manifestPath -Force
    if ($manifestItem.PSIsContainer) {
        throw "现有更新清单路径被目录占用，禁止开始 Windows 构建：$manifestPath"
    }
    try {
        $existingManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $latestVersion = [string]$existingManifest.version
    }
    catch {
        throw "现有更新清单无法读取，禁止开始 Windows 构建：$manifestPath"
    }
    if ($latestVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
        throw "现有更新清单版本无效，禁止开始 Windows 构建：$latestVersion"
    }
    if ([version]$Version -le [version]$latestVersion) {
        throw "最终构建版本必须高于现有更新版本 $latestVersion，禁止重复或回退。"
    }
}
if (Test-Path -LiteralPath $deliveryDirectory -PathType Leaf) {
    throw "客户交付路径不是目录：$deliveryDirectory"
}
if (Test-Path -LiteralPath $deliveryDirectory -PathType Container) {
    $existingDeliveryItems = @(Get-ChildItem -LiteralPath $deliveryDirectory -Force)
    if ($existingDeliveryItems.Count -gt 0) {
        throw "客户交付目录必须为空；脚本不会清理或覆盖已有发布文件：$deliveryDirectory"
    }
}
if (-not (Test-Path -LiteralPath $releasePython -PathType Leaf)) {
    throw "缺少 Windows 发布环境：$releasePython"
}
if (Test-Path -LiteralPath $publishedInstaller) {
    throw "同版本安装包已经存在。为防止重复发送或覆盖，请改用下一版本。"
}
if (Test-Path -LiteralPath $attemptMarkerPath) {
    throw "版本 $Version 已经开始过 Windows 制品尝试，禁止同版重包：$attemptMarkerPath"
}

$credentialBearingInputs = @(
    $releaseRegistryRelativePath,
    "deploy/control-plane/verify_control_plane.py",
    "scripts/run_paid_release_acceptance.py",
    "scripts/build_final_windows_release.ps1"
)
Assert-TrackedCleanReleaseInputs -RelativePaths $credentialBearingInputs
$preflightRegistry = Read-ReleaseRegistry -LiteralPath $releaseRegistryPath
$preflightRelease = Assert-WindowsReleaseRegistryState `
    -Registry $preflightRegistry `
    -ExpectedVersion $Version
Assert-ReleaseSourceCommit `
    -SourceCommit ([string]$preflightRelease.source_commit) `
    -RegistryRelativePath $releaseRegistryRelativePath
foreach ($name in $verificationVariables) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name, "Process"))) {
        throw "最终构建前必须在当前进程配置 $name；脚本不会读取、打印或写入该值。"
    }
}
if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($paidAcceptanceVariable, "Process"))) {
    throw "最终构建前必须在当前进程配置 $paidAcceptanceVariable，供付费验收报告在线核验；脚本不会读取、打印或写入该值。"
}
$verificationActivationValue = [Environment]::GetEnvironmentVariable(
    "VIDEOINSIGHT_VERIFY_ACTIVATION_CODE", "Process"
)
$verificationAdminUsernameValue = [Environment]::GetEnvironmentVariable(
    "VIDEOINSIGHT_VERIFY_ADMIN_USERNAME", "Process"
)
$verificationAdminPasswordValue = [Environment]::GetEnvironmentVariable(
    "VIDEOINSIGHT_VERIFY_ADMIN_PASSWORD", "Process"
)
$paidAcceptanceValue = [Environment]::GetEnvironmentVariable($paidAcceptanceVariable, "Process")
foreach ($name in $releaseSecretVariables) {
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
$resolvedPaidAcceptanceReport = (Resolve-Path -LiteralPath $PaidAcceptanceReport -ErrorAction Stop).Path
$validatedControlPlaneOrigin = Get-ValidatedControlPlaneOrigin -Value $ControlPlaneUrl

$stateTransitionMessage = $null
Open-ReleaseRegistryLock
try {
    $expectedLockStatus = "?? deploy/control-plane/release_versions.json.lock"
    $lockedStatus = @(& git -C $repositoryRoot status --porcelain --untracked-files=all)
    if (
        $LASTEXITCODE -ne 0 -or
        $lockedStatus.Count -ne 1 -or
        [string]$lockedStatus[0] -ne $expectedLockStatus
    ) {
        throw "正式版本状态锁定期间工作区发生变化，已停止。"
    }
    $releaseRegistry = Read-ReleaseRegistry -LiteralPath $releaseRegistryPath
    $releaseInProgress = Assert-WindowsReleaseRegistryState `
        -Registry $releaseRegistry `
        -ExpectedVersion $Version
    Assert-ReleaseSourceCommit `
        -SourceCommit ([string]$releaseInProgress.source_commit) `
        -RegistryRelativePath $releaseRegistryRelativePath
    $windowsState = [string]$releaseInProgress.windows_state
    if ($windowsState -eq "pending") {
        $releaseInProgress.windows_state = "building"
        Write-ReleaseRegistryAtomically -LiteralPath $releaseRegistryPath -Registry $releaseRegistry
        $stateTransitionMessage = "Windows 状态已原子置为 building；请只提交 release_versions.json 后重新运行。"
    }
    elseif ($windowsState -eq "building") {
        $releaseInProgress.windows_state = "attempted"
        Write-ReleaseRegistryAtomically -LiteralPath $releaseRegistryPath -Registry $releaseRegistry
        $stateTransitionMessage = "Windows 状态已原子置为 attempted；请只提交 release_versions.json 后最后运行一次。"
    }
    elseif ($windowsState -eq "built") {
        throw "版本 $Version 已经生成过 Windows 安装包，禁止同版重包；只能继续发布已登记哈希的制品。"
    }
}
finally {
    Close-ReleaseRegistryLock
}
if (-not [string]::IsNullOrWhiteSpace($stateTransitionMessage)) {
    throw $stateTransitionMessage
}

# State transitions are committed in separate registry-only commits.  Recheck
# immediately before any credential-bearing child process.
Assert-TrackedCleanReleaseInputs -RelativePaths $credentialBearingInputs
$windowsAttemptStarted = $true
$buildRoot = Join-Path $repositoryRoot "build"
[System.IO.Directory]::CreateDirectory($buildRoot) | Out-Null
Assert-NoReparsePointsForReleasePath `
    -RepositoryRoot $repositoryRoot `
    -CandidatePath $attemptMarkerPath
$attemptStream = $null
try {
    $attemptStream = New-Object System.IO.FileStream(
        $attemptMarkerPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    $attemptBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes(
        "version=$Version`nsource_commit=$([string]$releaseInProgress.source_commit)`n"
    )
    $attemptStream.Write($attemptBytes, 0, $attemptBytes.Length)
    $attemptStream.Flush($true)
}
finally {
    if ($null -ne $attemptStream) {
        $attemptStream.Dispose()
    }
}
$attemptMarkerCreated = $true

Write-Output "[1/7] 验证正式公司服务（登录专用测试账号，不会调用收费供应商）"
try {
    Invoke-ControlPlaneAuthoritativeGate `
        -Origin $validatedControlPlaneOrigin `
        -ExpectedVersion $ControlPlaneVersion `
        -ActivationCode $verificationActivationValue `
        -AdminUsername $verificationAdminUsernameValue `
        -AdminPassword $verificationAdminPasswordValue
}
finally {
    $verificationActivationValue = $null
    $verificationAdminUsernameValue = $null
    $verificationAdminPasswordValue = $null
}

Write-Output "[2/7] 验证最近 24 小时内的最低成本真实付费闭环"
try {
    Assert-PaidAcceptanceAuthoritativeProof `
        -Origin $validatedControlPlaneOrigin `
        -ExpectedVersion $ControlPlaneVersion `
        -ReportPath $resolvedPaidAcceptanceReport `
        -ActivationCode $paidAcceptanceValue
}
finally {
    $paidAcceptanceValue = $null
}

Write-Output "[3/7] 运行干净工作区发布门禁"
& (Join-Path $PSScriptRoot "check_release.ps1")
if ($LASTEXITCODE -ne 0) { throw "发布门禁失败；最终安装包未生成。" }

Write-Output "[4/7] 生成 Windows 应用目录"
& (Join-Path $PSScriptRoot "build_windows_installer.ps1") `
    -SkipChecks `
    -DirectoryOnly `
    -ControlPlaneUrl $ControlPlaneUrl `
    -Version $Version
if ($LASTEXITCODE -ne 0) { throw "Windows 应用目录构建失败。" }

Write-Output "[5/7] 检查客户包配置、运行数据和构建机密钥"
$secretEnvironmentFiles = @()
foreach ($secretEnvPath in @(
    (Join-Path $repositoryRoot ".env"),
    (Join-Path $repositoryRoot ".env.production"),
    (Join-Path $repositoryRoot "project\backend\.env"),
    (Join-Path $repositoryRoot "deploy\control-plane\.env")
    )) {
    $secretEnvAttributes = Get-ExistingPathAttributesForRelease -LiteralPath $secretEnvPath
    if ($null -eq $secretEnvAttributes) { continue }
    if (
        ($secretEnvAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        ($secretEnvAttributes -band [System.IO.FileAttributes]::Directory) -ne 0
    ) {
        throw "构建机环境输入必须是无链接的普通文件：$secretEnvPath"
    }
    if ($null -ne $secretEnvAttributes) {
        $secretEnvironmentFiles += $secretEnvPath
    }
}
Assert-WindowsReleasePayloadAuthoritative `
    -PackageRoot $unpacked `
    -Origin $validatedControlPlaneOrigin `
    -ExpectedVersion $Version `
    -SecretEnvironmentFiles $secretEnvironmentFiles

Write-Output "[6/7] 生成唯一的离线安装包"
& (Join-Path $PSScriptRoot "build_offline_windows_installer.ps1") `
    -Version $Version `
    -OutputDirectory $deliveryDirectory
if ($LASTEXITCODE -ne 0) { throw "单文件安装包构建失败。" }

$deliveryExecutables = @(Get-ChildItem -LiteralPath $deliveryDirectory -Filter "*.exe" -File)
if (
    $deliveryExecutables.Count -ne 1 -or
    -not [string]::Equals(
        $deliveryExecutables[0].FullName,
        $installer,
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw "客户交付目录必须且只能包含当前版本的一个 EXE：$deliveryDirectory"
}
$customerInstaller = Get-Item -LiteralPath $installer
$customerHash = Get-Sha256Hex -LiteralPath $installer
$customerVersionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($installer)
$customerFileVersion = Get-EmbeddedStableVersionForFinalRelease `
    -Value $customerVersionInfo.FileVersion `
    -Label "FileVersion"
$customerProductVersion = Get-EmbeddedStableVersionForFinalRelease `
    -Value $customerVersionInfo.ProductVersion `
    -Label "ProductVersion"
if ($customerFileVersion -ne $Version -or $customerProductVersion -ne $Version) {
    throw "客户安装包内嵌 FileVersion/ProductVersion 与当前版本不一致，禁止登记 built。"
}

Open-ReleaseRegistryLock
try {
    $releaseRegistry = Read-ReleaseRegistry -LiteralPath $releaseRegistryPath
    $releaseInProgress = Assert-WindowsReleaseRegistryState `
        -Registry $releaseRegistry `
        -ExpectedVersion $Version
    if (
        [string]$releaseInProgress.windows_state -ne "built" -or
        [string]$releaseInProgress.installer_sha256 -ne $customerHash
    ) {
        throw "离线构建未在同一发布锁内登记 exact built SHA256，禁止继续发布。"
    }
}
finally {
    Close-ReleaseRegistryLock
}

Write-Output "[7/7] 生成服务器更新清单"
& (Join-Path $PSScriptRoot "publish_windows_update.ps1") `
    -Version $Version `
    -InstallerPath $installer `
    -Notes $Notes
if ($LASTEXITCODE -ne 0) { throw "更新清单生成失败。" }

if (-not (Test-Path -LiteralPath $publishedInstaller -PathType Leaf)) {
    throw "更新服务器副本缺失：$publishedInstaller"
}
$serviceInstaller = Get-Item -LiteralPath $publishedInstaller
$serviceHash = Get-Sha256Hex -LiteralPath $publishedInstaller
if (
    $customerInstaller.Length -ne $serviceInstaller.Length -or
    $customerHash -ne $serviceHash
) {
    throw "客户安装包与更新服务器副本的大小或 SHA256 不一致。"
}

$completedRegistry = Read-ReleaseRegistry -LiteralPath $releaseRegistryPath
$completedVersionParts = $Version.Split('.')
$expectedNextCandidate = "{0}.{1}.{2}" -f `
    $completedVersionParts[0], `
    $completedVersionParts[1], `
    ([int64]$completedVersionParts[2] + 1)
$matchingCompletedReleases = @(
    @($completedRegistry.completed_releases) |
        Where-Object {
            [string]$_.version -eq $Version -and
            [string]$_.windows_state -eq "complete" -and
            [string]$_.installer_sha256 -eq $customerHash
        }
)
if (
    $null -ne $completedRegistry.release_in_progress -or
    [string]$completedRegistry.current_candidate -ne $expectedNextCandidate -or
    $matchingCompletedReleases.Count -ne 1
) {
    throw "更新发布未正确完成版本状态；$Version 已烧录，禁止重试。"
}
$windowsAttemptStarted = $false
try {
    if ($attemptMarkerCreated -and (Test-Path -LiteralPath $attemptMarkerPath -PathType Leaf)) {
        Remove-Item -LiteralPath $attemptMarkerPath -Force
        $attemptMarkerCreated = $false
    }
}
catch {
    Write-Warning "Windows 尝试标记未能清理；发布已完成，请人工核对：$attemptMarkerPath"
}
Write-Output "最终安装包和更新文件已完成；同一版本只需向客户发送一次安装包。"
Write-Output "客户唯一发送路径：$($customerInstaller.FullName)"
}
catch {
    $originalFailure = $_
    if ($windowsAttemptStarted -and -not [string]::IsNullOrWhiteSpace($releaseRegistryPath)) {
        try {
            Open-ReleaseRegistryLock
            $failedRegistry = Read-ReleaseRegistry -LiteralPath $releaseRegistryPath
            $failedInProgressProperty = $failedRegistry.PSObject.Properties['release_in_progress']
            if ($null -ne $failedInProgressProperty -and $null -ne $failedInProgressProperty.Value) {
                $failedInProgress = $failedInProgressProperty.Value
                if (
                    [string]$failedInProgress.version -eq $Version -and
                    [string]$failedInProgress.windows_state -eq "attempted"
                ) {
                    if (
                        -not [string]::IsNullOrWhiteSpace($installer) -and
                        (Test-Path -LiteralPath $installer -PathType Leaf)
                    ) {
                        Write-Warning "发现 attempted 状态下已提交的客户安装包；保留 attempted。请以相同 Version/OutputDirectory 单独重跑 build_offline_windows_installer.ps1，仅作 exact-artifact 恢复，禁止重建或覆盖。"
                    }
                    else {
                        $failedInProgress.windows_state = "failed"
                        Write-ReleaseRegistryAtomically `
                            -LiteralPath $releaseRegistryPath `
                            -Registry $failedRegistry
                    }
                }
                elseif (
                    [string]$failedInProgress.version -eq $Version -and
                    [string]$failedInProgress.windows_state -in @("built", "publishing")
                ) {
                    Write-Warning "已登记 exact installer SHA256 的 $($failedInProgress.windows_state) 状态保持不变；请只恢复发布，禁止同版重建。"
                }
            }
        }
        catch {
            Write-Warning "无法把 Windows 发布状态更新为 failed；版本 $Version 仍视为已烧录，禁止复用。"
        }
        finally {
            try { Close-ReleaseRegistryLock } catch { }
        }
    }
    throw $originalFailure
}
finally {
    try { Close-ReleaseRegistryLock } catch { }
    foreach ($name in $releaseSecretVariables) {
        [Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
    $paidAcceptanceValue = $null
    $verificationActivationValue = $null
    $verificationAdminUsernameValue = $null
    $verificationAdminPasswordValue = $null
}
