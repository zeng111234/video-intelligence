[CmdletBinding()]
param(
    [string]$SshHost = 'videoinsight-server'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($SshHost -notmatch '^[A-Za-z0-9._-]+$') {
    throw 'SSH 主机别名格式无效。'
}

$resolved = @(& ssh -G -- $SshHost 2>&1)
if ($LASTEXITCODE -ne 0) {
    throw '无法读取 SSH 配置。'
}
$settings = @{}
foreach ($line in $resolved) {
    if ($line -match '^([^ ]+)\s+(.+)$') {
        $settings[$matches[1].ToLowerInvariant()] = $matches[2].Trim()
    }
}
foreach ($required in @{
    user = 'devuser'
    hostname = '47.121.114.248'
    port = '22'
    batchmode = 'yes'
    passwordauthentication = 'no'
    kbdinteractiveauthentication = 'no'
    identitiesonly = 'yes'
}.GetEnumerator()) {
    if ($settings[$required.Key] -ne $required.Value) {
        throw "SSH 安全配置不符合要求：$($required.Key)。"
    }
}

$sshOptions = @(
    '-o', 'BatchMode=yes',
    '-o', 'PasswordAuthentication=no',
    '-o', 'KbdInteractiveAuthentication=no',
    '-o', 'NumberOfPasswordPrompts=0',
    '--', $SshHost
)
$identity = @(& ssh @sshOptions 'id -un' 2>&1)
if ($LASTEXITCODE -ne 0 -or ($identity -join "`n").Trim() -ne 'devuser') {
    throw '密钥登录失败；已停止，绝不会回退到密码认证。'
}

$probe = @(& ssh @sshOptions 'sudo -n /usr/local/sbin/videoinsight-release-gateway probe' 2>&1)
if ($LASTEXITCODE -ne 0 -or ($probe -join "`n") -notmatch 'VIDEOINSIGHT_RELEASE_GATEWAY_READY') {
    throw '服务器尚未安装受限发布入口，或仍未授权 devuser 使用；禁止上传部署。'
}

$sudoRules = @(& ssh @sshOptions 'sudo -n -l' 2>&1)
if ($LASTEXITCODE -ne 0) {
    throw '无法审计 devuser 的 sudo 权限；禁止上传部署。'
}
$sudoText = $sudoRules -join "`n"
foreach ($unsafePattern in @(
    '(?m)NOPASSWD:\s*/usr/bin/systemctl(?:\s|,|$)',
    '(?m)NOPASSWD:\s*/bin/cp(?:\s|,|$)',
    '(?m)(?:^|,)\s*/bin/mv(?:\s|,|$)',
    '(?m)/bin/rm\s+-rf\s+/opt/app/\*'
)) {
    if ($sudoText -match $unsafePattern) {
        throw '检测到旧的过宽 sudo 权限；必须由服务器管理员删除后才能部署。'
    }
}

Write-Output 'VIDEOINSIGHT_DEPLOY_ACCESS_OK'
