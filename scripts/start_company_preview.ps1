# Start the current source checkout against the company authentication service
# without reusing the normal source workspace's local customer data.  This is
# for development preview only; it never builds or updates the Windows EXE.

[CmdletBinding()]
param(
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot "start_all_services.ps1"

if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "找不到源码启动脚本：$launcher"
}

Write-Host "启动源码公司服务预览：本地素材与安装包数据不会被覆盖。" -ForegroundColor Cyan
$arguments = @("-UseCompanyServer")
if ($SkipBrowser) {
    $arguments += "-SkipBrowser"
}
& $launcher @arguments
exit $LASTEXITCODE
