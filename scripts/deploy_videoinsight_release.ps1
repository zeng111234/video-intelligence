[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$Version,

    [ValidateSet('Auto', 'ControlPlane', 'Desktop')]
    [string]$Component = 'Auto',

    [string]$SshHost = 'videoinsight-server'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$registryPath = Join-Path $repositoryRoot 'deploy\control-plane\release_versions.json'
$controlArchive = Join-Path $repositoryRoot "build\control-plane-ready-$Version\VideoInsight-control-plane-$Version.zip"
$updatesRoot = Join-Path $repositoryRoot 'deploy\control-plane\updates'
$installerName = "VideoInsight-$Version-Setup.exe"
$installerPath = Join-Path $updatesRoot $installerName
$manifestPath = Join-Path $updatesRoot 'latest.json'
$gateway = '/usr/local/sbin/videoinsight-release-gateway'

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    return (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Invoke-Ssh {
    param([Parameter(Mandatory = $true)][string]$RemoteCommand)
    $arguments = @(
        '-o', 'BatchMode=yes',
        '-o', 'PasswordAuthentication=no',
        '-o', 'KbdInteractiveAuthentication=no',
        '-o', 'NumberOfPasswordPrompts=0',
        '--', $SshHost, $RemoteCommand
    )
    $output = @(& ssh @arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "服务器命令失败；已停止且不会回退到密码认证。`n$($output -join "`n")"
    }
    return $output
}

function Send-StagedFile {
    param(
        [Parameter(Mandatory = $true)][string]$LocalPath,
        [Parameter(Mandatory = $true)][string]$RemotePath
    )
    $arguments = @(
        '-q',
        '-o', 'BatchMode=yes',
        '-o', 'PasswordAuthentication=no',
        '-o', 'KbdInteractiveAuthentication=no',
        '-o', 'NumberOfPasswordPrompts=0',
        '--', $LocalPath, "${SshHost}:$RemotePath"
    )
    $output = @(& scp @arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "制品上传失败；已停止且不会回退到密码认证。`n$($output -join "`n")"
    }
}

function Get-JsonWithSingleRetry {
    param([Parameter(Mandatory = $true)][string]$Uri)
    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        try {
            return Invoke-RestMethod -Uri $Uri -TimeoutSec 15
        }
        catch {
            if ($attempt -eq 1) { throw }
            Start-Sleep -Seconds 1
        }
    }
}

if ($SshHost -notmatch '^[A-Za-z0-9._-]+$') {
    throw 'SSH 主机别名格式无效。'
}
& (Join-Path $PSScriptRoot 'test_videoinsight_deploy_access.ps1') -SshHost $SshHost
if ($LASTEXITCODE -ne 0) {
    throw '服务器发布权限预检失败。'
}

$registry = Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json
$inProgress = $registry.release_in_progress
$completed = @($registry.completed_releases | Where-Object { [string]$_.version -eq $Version })
$release = if ($null -ne $inProgress -and [string]$inProgress.version -eq $Version) {
    $inProgress
} elseif ($completed.Count -eq 1) {
    $completed[0]
} else {
    throw "版本 $Version 没有唯一、有效的正式发布登记。"
}
if ([string]$release.server_state -ne 'ready' -or [string]$release.control_plane_sha256 -notmatch '^[0-9a-f]{64}$') {
    throw "版本 $Version 的控制层制品尚未进入 ready 状态。"
}

$health = Get-JsonWithSingleRetry -Uri 'https://xmt.syszr.cn/health'
$onlineControlVersion = [string]$health.release_version
$latest = Get-JsonWithSingleRetry -Uri 'https://xmt.syszr.cn/desktop-updates/latest.json'
$onlineDesktopVersion = [string]$latest.version
$deployedAnything = $false

$shouldDeployControl = $Component -in @('Auto', 'ControlPlane') -and $onlineControlVersion -ne $Version
if ($shouldDeployControl) {
    if (-not (Test-Path -LiteralPath $controlArchive -PathType Leaf)) {
        if ($Component -eq 'ControlPlane') { throw "缺少控制层正式制品：$controlArchive" }
    }
    else {
        $controlSha = Get-Sha256Hex -LiteralPath $controlArchive
        if ($controlSha -ne [string]$release.control_plane_sha256) {
            throw '控制层制品 SHA256 与正式版本登记不一致。'
        }
        $nonce = [Guid]::NewGuid().ToString('N')
        $remoteDirectory = "/home/devuser/.videoinsight-release-staging/$nonce"
        $remoteArchive = "$remoteDirectory/$(Split-Path -Leaf $controlArchive)"
        Invoke-Ssh -RemoteCommand "mkdir -m 700 -- $remoteDirectory" | Out-Null
        Send-StagedFile -LocalPath $controlArchive -RemotePath $remoteArchive
        Invoke-Ssh -RemoteCommand "sudo -n $gateway deploy-control-plane $Version $controlSha $remoteArchive"
        $verifiedHealth = Get-JsonWithSingleRetry -Uri 'https://xmt.syszr.cn/health'
        if ([string]$verifiedHealth.release_version -ne $Version) {
            throw '控制层发布命令已返回，但公网健康检查版本不一致。'
        }
        $deployedAnything = $true
    }
}

$shouldDeployDesktop = $Component -in @('Auto', 'Desktop') -and $onlineDesktopVersion -ne $Version
if ($shouldDeployDesktop) {
    $desktopFilesExist = (
        (Test-Path -LiteralPath $installerPath -PathType Leaf) -and
        (Test-Path -LiteralPath $manifestPath -PathType Leaf)
    )
    if (-not $desktopFilesExist) {
        if ($Component -eq 'Desktop') { throw '缺少桌面端正式安装包或 latest.json。' }
    }
    else {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $installerSha = Get-Sha256Hex -LiteralPath $installerPath
        $manifestSha = Get-Sha256Hex -LiteralPath $manifestPath
        $installerSize = (Get-Item -LiteralPath $installerPath).Length
        if (
            [string]$manifest.version -ne $Version -or
            [string]$manifest.installer -ne $installerName -or
            [string]$manifest.sha256 -ne $installerSha -or
            [int64]$manifest.size_bytes -ne $installerSize
        ) {
            throw '桌面端安装包与 latest.json 不一致。'
        }
        if ([string]$release.windows_state -ne 'complete' -or [string]$release.installer_sha256 -ne $installerSha) {
            throw '桌面端制品尚未在正式版本登记中标记为 complete，或哈希不一致。'
        }
        $nonce = [Guid]::NewGuid().ToString('N')
        $remoteDirectory = "/home/devuser/.videoinsight-release-staging/$nonce"
        $remoteInstaller = "$remoteDirectory/$installerName"
        $remoteManifest = "$remoteDirectory/latest.json"
        Invoke-Ssh -RemoteCommand "mkdir -m 700 -- $remoteDirectory" | Out-Null
        Send-StagedFile -LocalPath $installerPath -RemotePath $remoteInstaller
        Send-StagedFile -LocalPath $manifestPath -RemotePath $remoteManifest
        Invoke-Ssh -RemoteCommand "sudo -n $gateway publish-desktop $Version $installerSha $manifestSha $remoteInstaller $remoteManifest"
        $verifiedLatest = Get-JsonWithSingleRetry -Uri 'https://xmt.syszr.cn/desktop-updates/latest.json'
        if (
            [string]$verifiedLatest.version -ne $Version -or
            [string]$verifiedLatest.sha256 -ne $installerSha -or
            [int64]$verifiedLatest.size_bytes -ne $installerSize
        ) {
            throw '桌面端发布命令已返回，但公网 latest.json 证据不一致。'
        }
        $deployedAnything = $true
    }
}

if (-not $deployedAnything) {
    if ($onlineControlVersion -eq $Version -and $onlineDesktopVersion -eq $Version) {
        Write-Output "版本 $Version 的控制层和桌面更新均已在线，无需重复部署。"
    }
    else {
        throw '没有找到当前阶段可以安全部署的正式制品。'
    }
}
else {
    Write-Output "VIDEOINSIGHT_RELEASE_DEPLOYED version=$Version component=$Component"
}
