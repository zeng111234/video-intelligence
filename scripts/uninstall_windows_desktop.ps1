$ErrorActionPreference = "Stop"

function Get-ExistingPathAttributesForUninstall {
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
    catch { throw "无法安全检查卸载路径（可能是失效链接）：$LiteralPath" }
}

function Assert-NoReparsePoint {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $attributes = Get-ExistingPathAttributesForUninstall -LiteralPath $Path
    if ($null -eq $attributes) { return }
    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label 不能是链接或联接点：$Path"
    }
}

function Assert-NoReparsePointsInAncestors {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($resolvedPath).TrimEnd('\', '/')
    $relative = $resolvedPath.Substring($root.Length)
    $current = $root + [System.IO.Path]::DirectorySeparatorChar
    foreach ($part in @($relative -split '[\\/]' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
        $current = Join-Path $current $part
        $attributes = Get-ExistingPathAttributesForUninstall -LiteralPath $current
        if ($null -eq $attributes) { break }
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label 或其祖先不能是链接或联接点：$current"
        }
    }
}

function Assert-NoReparsePointsInTree {
    param(
        [Parameter(Mandatory = $true)][string]$RootPath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $RootPath)) { return }
    Assert-NoReparsePoint -Path $RootPath -Label $Label
    if (-not (Test-Path -LiteralPath $RootPath -PathType Container)) { return }
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push([System.IO.Path]::GetFullPath($RootPath))
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($current)) {
            $attributes = [System.IO.File]::GetAttributes($entry)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label 包含链接或联接点：$entry"
            }
            if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                $pending.Push($entry)
            }
        }
    }
}

function Test-ProcessPathWithinInstallRoot {
    param(
        [AllowEmptyString()][string]$ProcessPath,
        [Parameter(Mandatory = $true)][string]$ExpectedInstallRoot
    )
    if ([string]::IsNullOrWhiteSpace($ProcessPath)) {
        throw "无法读取同名进程路径；请人工关闭后重试卸载。"
    }
    $root = [System.IO.Path]::GetFullPath($ExpectedInstallRoot).TrimEnd('\', '/')
    $prefix = $root + [System.IO.Path]::DirectorySeparatorChar
    $resolvedProcessPath = [System.IO.Path]::GetFullPath($ProcessPath)
    return $resolvedProcessPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Stop-VideoInsightProcesses {
    param([Parameter(Mandatory = $true)][string]$ExpectedInstallRoot)
    $ownedProcesses = @()
    foreach ($process in @(Get-Process -Name VideoInsight, VideoInsightBackend -ErrorAction SilentlyContinue)) {
        try { $processPath = [string]$process.Path }
        catch { throw "无法读取同名进程路径；请人工关闭所有 VideoInsight 窗口后重试卸载。" }
        if (Test-ProcessPathWithinInstallRoot -ProcessPath $processPath -ExpectedInstallRoot $ExpectedInstallRoot) {
            $ownedProcesses += $process
        }
    }
    foreach ($process in $ownedProcesses) {
        Stop-Process -Id $process.Id -Force
    }
}

function Remove-OwnedShortcutSafely {
    param(
        [Parameter(Mandatory = $true)][string]$ShortcutPath,
        [Parameter(Mandatory = $true)][string]$ExpectedTarget
    )
    if (-not (Test-Path -LiteralPath $ShortcutPath)) { return }
    Assert-NoReparsePoint -Path $ShortcutPath -Label "VideoInsight 快捷方式"
    $item = Get-Item -LiteralPath $ShortcutPath -Force
    if ($item.PSIsContainer) {
        throw "VideoInsight 快捷方式路径被目录占用，拒绝删除：$ShortcutPath"
    }
    try {
        $shell = New-Object -ComObject WScript.Shell
        $actualTarget = [System.IO.Path]::GetFullPath([string]$shell.CreateShortcut($ShortcutPath).TargetPath)
    }
    catch {
        throw "无法验证 VideoInsight 快捷方式目标，拒绝删除：$ShortcutPath"
    }
    $resolvedExpectedTarget = [System.IO.Path]::GetFullPath($ExpectedTarget)
    if (-not [string]::Equals($actualTarget, $resolvedExpectedTarget, [System.StringComparison]::OrdinalIgnoreCase)) {
        Write-Warning "同名快捷方式不属于当前安装，已保留：$ShortcutPath"
        return
    }
    Remove-Item -LiteralPath $ShortcutPath -Force
}

function Remove-EmptyOwnedDirectorySafely {
    param([Parameter(Mandatory = $true)][string]$DirectoryPath)
    if (-not (Test-Path -LiteralPath $DirectoryPath -PathType Container)) { return }
    Assert-NoReparsePoint -Path $DirectoryPath -Label "VideoInsight 开始菜单目录"
    if (@(Get-ChildItem -LiteralPath $DirectoryPath -Force).Count -eq 0) {
        Remove-Item -LiteralPath $DirectoryPath -Force
    }
}

function Assert-FixedLocalDrivePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    if ($resolvedPath.StartsWith('\\')) {
        throw "$Label 不能位于网络共享目录：$resolvedPath"
    }
    $root = [System.IO.Path]::GetPathRoot($resolvedPath)
    if ([string]::IsNullOrWhiteSpace($root) -or [string]::Equals($resolvedPath.TrimEnd('\', '/'), $root.TrimEnd('\', '/'), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label 不能直接使用磁盘根目录：$resolvedPath"
    }
    $driveName = $root.TrimEnd('\', '/').TrimEnd(':')
    $drive = Get-PSDrive -Name $driveName -PSProvider FileSystem -ErrorAction Stop
    if ($drive.DisplayRoot -or $drive.Root -notmatch '^[A-Za-z]:\\$') {
        throw "$Label 必须位于本机固定磁盘：$resolvedPath"
    }
}

$localAppData = [Environment]::GetFolderPath("LocalApplicationData")
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VideoInsight"
if (-not (Test-Path -LiteralPath $uninstallKey)) {
    throw "找不到 VideoInsight 安装登记，拒绝猜测卸载目录。"
}
$registration = Get-ItemProperty -LiteralPath $uninstallKey
$registeredInstallLocation = [string]$registration.InstallLocation
if ([string]::IsNullOrWhiteSpace($registeredInstallLocation)) {
    throw "VideoInsight 安装登记缺少安装位置，拒绝猜测卸载目录。"
}
$expectedInstallRoot = [System.IO.Path]::GetFullPath($registeredInstallLocation).TrimEnd('\', '/')
$installRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
if (-not [string]::Equals($installRoot, $expectedInstallRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "卸载脚本位置与安装登记不一致，拒绝删除：$installRoot"
}
$programsRoot = [System.IO.Path]::GetDirectoryName($expectedInstallRoot).TrimEnd('\', '/')
Assert-FixedLocalDrivePath -Path $programsRoot -Label "程序目录"
Assert-FixedLocalDrivePath -Path $installRoot -Label "VideoInsight 安装目录"
Assert-NoReparsePointsInAncestors -Path $programsRoot -Label "程序目录"
Assert-NoReparsePointsInAncestors -Path $installRoot -Label "VideoInsight 安装目录"
Assert-NoReparsePoint -Path $programsRoot -Label "程序目录"
Assert-NoReparsePointsInTree -RootPath $installRoot -Label "VideoInsight 安装目录"

Stop-VideoInsightProcesses -ExpectedInstallRoot $installRoot

$desktopRoot = [Environment]::GetFolderPath("Desktop")
$startMenuProgramsRoot = [Environment]::GetFolderPath("Programs")
$desktopShortcut = Join-Path $desktopRoot "VideoInsight.lnk"
$startMenuDir = Join-Path $startMenuProgramsRoot "VideoInsight"
$startMenuShortcut = Join-Path $startMenuDir "VideoInsight.lnk"
foreach ($safeRoot in @($desktopRoot, $startMenuProgramsRoot, $startMenuDir)) {
    Assert-NoReparsePointsInAncestors -Path $safeRoot -Label "快捷方式目录"
    Assert-NoReparsePoint -Path $safeRoot -Label "快捷方式目录"
}
$installedExecutable = Join-Path $installRoot "VideoInsight.exe"
Remove-OwnedShortcutSafely -ShortcutPath $desktopShortcut -ExpectedTarget $installedExecutable
Remove-OwnedShortcutSafely -ShortcutPath $startMenuShortcut -ExpectedTarget $installedExecutable
Remove-EmptyOwnedDirectorySafely -DirectoryPath $startMenuDir

$escapedInstallRoot = $installRoot.Replace("'", "''")
$escapedExpectedInstallRoot = $expectedInstallRoot.Replace("'", "''")
$escapedProgramsRoot = $programsRoot.Replace("'", "''")
$escapedUninstallKey = $uninstallKey.Replace("'", "''")
$cleanupFailureLog = Join-Path $localAppData ("VideoInsight-uninstall-failed-{0}.log" -f [guid]::NewGuid().ToString("N"))
$cleanupPowerShell = Join-Path $PSHOME "powershell.exe"
Assert-NoReparsePointsInAncestors -Path $localAppData -Label "本机应用数据目录"
Assert-NoReparsePoint -Path $cleanupFailureLog -Label "卸载失败日志"
if ($null -ne (Get-ExistingPathAttributesForUninstall -LiteralPath $cleanupFailureLog)) {
    throw "卸载失败日志随机路径已被占用：$cleanupFailureLog"
}
$escapedCleanupFailureLog = $cleanupFailureLog.Replace("'", "''")
$cleanup = @"
`$ErrorActionPreference = 'Stop'
try {
    Start-Sleep -Seconds 2
    `$target = [System.IO.Path]::GetFullPath('$escapedInstallRoot')
    `$expected = [System.IO.Path]::GetFullPath('$escapedExpectedInstallRoot')
    `$programs = [System.IO.Path]::GetFullPath('$escapedProgramsRoot')
    `$uninstaller = [System.IO.Path]::GetFullPath((Join-Path `$target 'Uninstall-VideoInsight.ps1'))
    if (-not [string]::Equals(`$target, `$expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw '卸载目标与安装登记不再一致。'
    }
    foreach (`$checkedRoot in @(`$programs, `$target)) {
        `$pathRoot = [System.IO.Path]::GetPathRoot(`$checkedRoot).TrimEnd('\', '/')
        `$relativePath = `$checkedRoot.Substring(`$pathRoot.Length)
        `$currentPath = `$pathRoot + [System.IO.Path]::DirectorySeparatorChar
        foreach (`$part in @(`$relativePath -split '[\\/]' | Where-Object { -not [string]::IsNullOrWhiteSpace(`$_) })) {
            `$currentPath = Join-Path `$currentPath `$part
            `$entryExists = [System.IO.File]::Exists(`$currentPath) -or [System.IO.Directory]::Exists(`$currentPath)
            if (-not `$entryExists) {
                `$parentPath = [System.IO.Path]::GetDirectoryName(`$currentPath)
                if ([System.IO.Directory]::Exists(`$parentPath)) {
                    foreach (`$entryPath in [System.IO.Directory]::EnumerateFileSystemEntries(`$parentPath)) {
                        if ([string]::Equals([System.IO.Path]::GetFileName(`$entryPath), [System.IO.Path]::GetFileName(`$currentPath), [System.StringComparison]::OrdinalIgnoreCase)) {
                            `$entryExists = `$true
                            break
                        }
                    }
                }
            }
            if (`$entryExists) {
                `$attributes = [System.IO.File]::GetAttributes(`$currentPath)
                if ((`$attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw '卸载目标或其祖先已变成链接。'
                }
            }
            else { break }
        }
    }
    if ([System.IO.Directory]::Exists(`$target)) {
        `$pending = New-Object 'System.Collections.Generic.Stack[string]'
        `$directories = New-Object 'System.Collections.Generic.List[string]'
        `$pending.Push(`$target)
        while (`$pending.Count -gt 0) {
            `$current = `$pending.Pop()
            `$currentAttributes = [System.IO.File]::GetAttributes(`$current)
            if ((`$currentAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw '安装目录在清理期间变成了链接。'
            }
            `$directories.Add(`$current)
            foreach (`$entry in [System.IO.Directory]::EnumerateFileSystemEntries(`$current)) {
                `$attributes = [System.IO.File]::GetAttributes(`$entry)
                if ((`$attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw '安装目录包含链接，拒绝删除。'
                }
                if ((`$attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                    `$pending.Push(`$entry)
                }
                elseif (-not [string]::Equals([System.IO.Path]::GetFullPath(`$entry), `$uninstaller, [System.StringComparison]::OrdinalIgnoreCase)) {
                    [System.IO.File]::SetAttributes(`$entry, [System.IO.FileAttributes]::Normal)
                    [System.IO.File]::Delete(`$entry)
                }
            }
        }
        `$orderedDirectories = @(`$directories | Sort-Object { `$_.Length } -Descending)
        foreach (`$directory in `$orderedDirectories) {
            if ([string]::Equals(`$directory, `$target, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
            `$attributes = [System.IO.File]::GetAttributes(`$directory)
            if ((`$attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw '安装子目录在清理期间变成了链接。'
            }
            [System.IO.Directory]::Delete(`$directory, `$false)
        }
        if ([System.IO.File]::Exists(`$uninstaller)) {
            `$uninstallerAttributes = [System.IO.File]::GetAttributes(`$uninstaller)
            if ((`$uninstallerAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw '卸载脚本在清理期间变成了链接。'
            }
            [System.IO.File]::SetAttributes(`$uninstaller, [System.IO.FileAttributes]::Normal)
            [System.IO.File]::Delete(`$uninstaller)
        }
        `$targetAttributes = [System.IO.File]::GetAttributes(`$target)
        if ((`$targetAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw '安装根目录在清理期间变成了链接。'
        }
        [System.IO.Directory]::Delete(`$target, `$false)
    }
    try {
        Remove-Item -LiteralPath '$escapedUninstallKey' -Recurse -Force -ErrorAction Stop
    }
    catch {
        Start-Sleep -Milliseconds 1500
        Remove-Item -LiteralPath '$escapedUninstallKey' -Recurse -Force -ErrorAction Stop
    }
}
catch {
    try {
        `$message = "`$([DateTimeOffset]::Now.ToString('o')) ``n`$(`$_.Exception.Message)``n"
        `$bytes = (New-Object System.Text.UTF8Encoding(`$false)).GetBytes(`$message)
        `$logStream = New-Object System.IO.FileStream(
            '$escapedCleanupFailureLog',
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::Read
        )
        try { `$logStream.Write(`$bytes, 0, `$bytes.Length); `$logStream.Flush(`$true) }
        finally { `$logStream.Dispose() }
    }
    catch { }
    exit 1
}
"@
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cleanup))
if (-not [System.IO.File]::Exists($cleanupPowerShell)) {
    throw "找不到受信任的 Windows PowerShell 卸载清理程序：$cleanupPowerShell"
}
Assert-NoReparsePoint -Path $cleanupPowerShell -Label "Windows PowerShell"
Start-Process `
    -FilePath $cleanupPowerShell `
    -ArgumentList "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", $encoded `
    -WindowStyle Hidden

try {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "VideoInsight 卸载清理已安排。清理成功后才会删除卸载登记；客户数据会保留。若登记仍在，请查看失败日志并联系维护人员处理。",
        "VideoInsight 卸载已安排"
    ) | Out-Null
}
catch {
    Write-Warning "卸载清理已安排，但提示窗口未能显示。失败时卸载登记会保留，日志路径：$cleanupFailureLog"
}
