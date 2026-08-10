$ErrorActionPreference = "Stop"

$localAppData = [Environment]::GetFolderPath("LocalApplicationData")
$programsRoot = [System.IO.Path]::GetFullPath((Join-Path $localAppData "Programs")).TrimEnd('\') + '\'
$installRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
if (-not $installRoot.StartsWith($programsRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "拒绝卸载不安全的目录：$installRoot"
}

Get-Process -Name VideoInsight, VideoInsightBackend -ErrorAction SilentlyContinue |
    Stop-Process -Force

$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "VideoInsight.lnk"
$startMenuDir = Join-Path ([Environment]::GetFolderPath("Programs")) "VideoInsight"
Remove-Item -LiteralPath $desktopShortcut -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $startMenuDir) {
    Remove-Item -LiteralPath $startMenuDir -Recurse -Force
}
Remove-Item -LiteralPath "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VideoInsight" -Recurse -Force -ErrorAction SilentlyContinue

$escapedInstallRoot = $installRoot.Replace("'", "''")
$cleanup = @"
Start-Sleep -Seconds 2
`$target = '$escapedInstallRoot'
`$allowed = '$($programsRoot.Replace("'", "''"))'
if (`$target.StartsWith(`$allowed, [System.StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath `$target)) {
    Remove-Item -LiteralPath `$target -Recurse -Force
}
"@
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cleanup))
Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", $encoded -WindowStyle Hidden

Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show(
    "VideoInsight 已卸载。客户数据保留在本机，重新安装后可以继续使用。",
    "VideoInsight 已卸载"
) | Out-Null
