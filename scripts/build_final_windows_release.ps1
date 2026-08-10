param(
    [Parameter(Mandatory = $true)][string]$ControlPlaneUrl,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Notes = "稳定性与功能更新"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$releasePython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$unpacked = Join-Path $repositoryRoot "project\frontend\release\win-unpacked"
$installer = Join-Path $repositoryRoot "project\frontend\release\VideoInsight-$Version-Setup.exe"
$publishedInstaller = Join-Path $repositoryRoot "deploy\control-plane\updates\VideoInsight-$Version-Setup.exe"

if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "正式版本号必须使用三段纯数字，例如 0.2.1。"
}
if ([version]$Version -le [version]"0.2.0") {
    throw "0.2.0 及更早版本已用于历史或示例构建，请选择从未使用过的新版本号。"
}
if (-not (Test-Path -LiteralPath $releasePython)) {
    throw "缺少 Windows 发布环境：$releasePython"
}
if ((Test-Path -LiteralPath $installer) -or (Test-Path -LiteralPath $publishedInstaller)) {
    throw "同版本安装包已经存在。为防止重复发送或覆盖，请选择从未使用过的新版本号：$Version"
}

Write-Output "[1/6] 验证正式公司服务（只读及无效登录检查，不会调用收费供应商）"
$verificationVariables = @(
    "VIDEOINSIGHT_VERIFY_ACTIVATION_CODE",
    "VIDEOINSIGHT_VERIFY_ADMIN_USERNAME",
    "VIDEOINSIGHT_VERIFY_ADMIN_PASSWORD"
)
$savedVerificationEnvironment = @{}
foreach ($name in $verificationVariables) {
    $savedVerificationEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}
try {
    & $releasePython `
        (Join-Path $repositoryRoot "deploy\control-plane\verify_control_plane.py") `
        --base-url $ControlPlaneUrl
    if ($LASTEXITCODE -ne 0) { throw "正式公司服务尚未通过无付费在线检查。" }
}
finally {
    foreach ($name in $verificationVariables) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $savedVerificationEnvironment[$name],
            "Process"
        )
    }
}

Write-Output "[2/6] 运行干净工作区发布门禁"
& (Join-Path $PSScriptRoot "check_release.ps1")
if ($LASTEXITCODE -ne 0) { throw "发布门禁失败；最终安装包未生成。" }

Write-Output "[3/6] 生成 Windows 应用目录"
& (Join-Path $PSScriptRoot "build_windows_installer.ps1") `
    -SkipChecks `
    -DirectoryOnly `
    -ControlPlaneUrl $ControlPlaneUrl `
    -Version $Version
if ($LASTEXITCODE -ne 0) { throw "Windows 应用目录构建失败。" }

Write-Output "[4/6] 检查客户包配置、运行数据和构建机密钥"
$secretEnvArguments = @()
foreach ($secretEnvPath in @(
    (Join-Path $repositoryRoot ".env"),
    (Join-Path $repositoryRoot ".env.production"),
    (Join-Path $repositoryRoot "project\backend\.env")
)) {
    if (Test-Path -LiteralPath $secretEnvPath -PathType Leaf) {
        $secretEnvArguments += "--secret-env-file"
        $secretEnvArguments += $secretEnvPath
    }
}
& $releasePython `
    (Join-Path $PSScriptRoot "verify_windows_release_payload.py") `
    --package-root $unpacked `
    --control-plane-url $ControlPlaneUrl `
    --version $Version `
    @secretEnvArguments
if ($LASTEXITCODE -ne 0) { throw "Windows 客户包安全检查失败。" }

Write-Output "[5/6] 生成唯一的离线安装包"
& (Join-Path $PSScriptRoot "build_offline_windows_installer.ps1") -Version $Version
if ($LASTEXITCODE -ne 0) { throw "单文件安装包构建失败。" }

Write-Output "[6/6] 生成服务器更新清单"
& (Join-Path $PSScriptRoot "publish_windows_update.ps1") `
    -Version $Version `
    -InstallerPath $installer `
    -Notes $Notes
if ($LASTEXITCODE -ne 0) { throw "更新清单生成失败。" }

Write-Output "最终安装包和更新文件已完成；同一版本只需向客户发送一次安装包。"
