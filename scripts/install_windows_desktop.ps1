param(
    [string]$Version = "0.2.0",
    [string]$VerifierPath = "",
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$phase = "准备安装"
$stage = $null
$backupRoot = $null
$installRoot = $null
$programsRoot = $null
$uninstallKey = $null
$previousUninstall = $null
$newVersionActivated = $false
$installationCommitted = $false
$desktopShortcut = $null
$startMenuDir = $null
$startMenuProgramsRoot = $null
$startMenuShortcut = $null
$startMenuDirCreated = $false
$shortcutBackupRoot = $null
$touchedShortcutPaths = @()
$shortcutBackups = @{}

function Test-InstallationRollbackRequired {
    param([Parameter(Mandatory = $true)][bool]$InstallationCommitted)
    return -not $InstallationCommitted
}

function Remove-PostCommitBackupSafely {
    param(
        [AllowEmptyString()][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return $true
    }
    try {
        Assert-NoReparsePointsInTree -RootPath $Path -Label $Label
        Invoke-WithSingleRetry `
            -Description "清理 $Label" `
            -Action { Remove-DirectoryTreeWithoutFollowingReparse -RootPath $Path -Label $Label }
        return $true
    }
    catch {
        Write-Warning "$Label 暂未清理；已验收的新版本和卸载登记会继续保留。"
        return $false
    }
}

function Assert-NewerInstallerVersion {
    param(
        [Parameter(Mandatory = $true)][string]$NewVersion,
        [AllowEmptyString()][string]$InstalledVersion = "",
        [Parameter(Mandatory = $true)][bool]$HasExistingInstall
    )
    $stableVersionPattern = '^[0-9]+\.[0-9]+\.[0-9]+$'
    if ($NewVersion -notmatch $stableVersionPattern) {
        throw "安装包版本号无效：$NewVersion"
    }
    $hasValidInstalledVersion = -not [string]::IsNullOrWhiteSpace($InstalledVersion) -and $InstalledVersion -match $stableVersionPattern
    if ($hasValidInstalledVersion) {
        if ([version]$NewVersion -le [version]$InstalledVersion) {
            throw "已安装版本为 $InstalledVersion；只允许安装更高版本，不能重复安装或降级。"
        }
        return
    }
    if ($HasExistingInstall) {
        throw "检测到已有安装目录，但已安装版本登记缺失或无效；为防止误覆盖，已停止安装。"
    }
}

function Get-ValidatedExecutableStableVersion {
    param(
        [Parameter(Mandatory = $true)][string]$ExecutablePath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
        throw "$Label 缺少可核对版本的 EXE。"
    }
    Assert-NoReparsePoint -Path $ExecutablePath -Label $Label
    $versionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($ExecutablePath)
    $stableVersionPattern = '^\s*([0-9]+)\.([0-9]+)\.([0-9]+)(?:\.0)?\s*$'
    $fileMatch = [regex]::Match([string]$versionInfo.FileVersion, $stableVersionPattern)
    $productMatch = [regex]::Match([string]$versionInfo.ProductVersion, $stableVersionPattern)
    if (-not $fileMatch.Success -or -not $productMatch.Success) {
        throw "$Label 的 FileVersion 或 ProductVersion 无效；已停止覆盖。"
    }
    $fileVersion = "{0}.{1}.{2}" -f `
        $fileMatch.Groups[1].Value, $fileMatch.Groups[2].Value, $fileMatch.Groups[3].Value
    $productVersion = "{0}.{1}.{2}" -f `
        $productMatch.Groups[1].Value, $productMatch.Groups[2].Value, $productMatch.Groups[3].Value
    if ($fileVersion -ne $productVersion) {
        throw "$Label 的 FileVersion 与 ProductVersion 不一致；已停止覆盖。"
    }
    return $fileVersion
}

function Get-ValidatedInstalledApplicationVersion {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [AllowEmptyString()][string]$RegisteredVersion = ""
    )
    $installRootAttributes = Get-ExistingPathAttributesForInstall -LiteralPath $InstallRoot
    if ($null -eq $installRootAttributes) {
        return $RegisteredVersion
    }
    if (($installRootAttributes -band [System.IO.FileAttributes]::Directory) -eq 0) {
        throw "现有安装路径不是目录；已停止覆盖。"
    }
    Assert-NoReparsePointsInTree -RootPath $InstallRoot -Label "现有安装目录"
    $installedExecutable = Join-Path $InstallRoot "VideoInsight.exe"
    $fileVersion = Get-ValidatedExecutableStableVersion `
        -ExecutablePath $installedExecutable `
        -Label "现有 VideoInsight.exe"
    if (
        $RegisteredVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        [string]$RegisteredVersion -ne $fileVersion
    ) {
        throw "现有应用实际版本 $fileVersion 与卸载登记版本不一致；已停止覆盖。"
    }
    return $fileVersion
}

function Get-ExistingPathAttributesForInstall {
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
    catch { throw "无法安全检查安装路径（可能是失效链接）：$LiteralPath" }
}

function Assert-NoReparsePoint {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $attributes = Get-ExistingPathAttributesForInstall -LiteralPath $Path
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
    $relativePath = $resolvedPath.Substring($root.Length)
    $currentPath = $root + [System.IO.Path]::DirectorySeparatorChar
    foreach ($part in @($relativePath -split '[\\/]' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
        $currentPath = Join-Path $currentPath $part
        $attributes = Get-ExistingPathAttributesForInstall -LiteralPath $currentPath
        if ($null -eq $attributes) { break }
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label 或其祖先不能是链接或联接点：$currentPath"
        }
    }
}

function Assert-NoReparsePointsInTree {
    param(
        [Parameter(Mandatory = $true)][string]$RootPath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $RootPath)) {
        return
    }
    Assert-NoReparsePoint -Path $RootPath -Label $Label
    $rootItem = Get-Item -LiteralPath $RootPath -Force
    if (-not $rootItem.PSIsContainer) {
        return
    }
    $pendingDirectories = New-Object 'System.Collections.Generic.Stack[string]'
    $pendingDirectories.Push($rootItem.FullName)
    while ($pendingDirectories.Count -gt 0) {
        $currentDirectory = $pendingDirectories.Pop()
        foreach ($entryPath in [System.IO.Directory]::EnumerateFileSystemEntries($currentDirectory)) {
            $attributes = [System.IO.File]::GetAttributes($entryPath)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label 包含链接或联接点：$entryPath"
            }
            if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                $pendingDirectories.Push($entryPath)
            }
        }
    }
}

function Remove-DirectoryTreeWithoutFollowingReparse {
    param(
        [Parameter(Mandatory = $true)][string]$RootPath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $RootPath)) { return }
    Assert-NoReparsePointsInTree -RootPath $RootPath -Label $Label
    if (-not (Test-Path -LiteralPath $RootPath -PathType Container)) {
        throw "$Label 不是目录，拒绝递归清理：$RootPath"
    }
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $directories = New-Object 'System.Collections.Generic.List[string]'
    $pending.Push([System.IO.Path]::GetFullPath($RootPath))
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        $currentAttributes = [System.IO.File]::GetAttributes($current)
        if (($currentAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label 在清理期间变成链接：$current"
        }
        $directories.Add($current)
        foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($current)) {
            $attributes = [System.IO.File]::GetAttributes($entry)
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label 在清理期间包含链接：$entry"
            }
            if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                $pending.Push($entry)
            }
            else {
                [System.IO.File]::SetAttributes($entry, [System.IO.FileAttributes]::Normal)
                [System.IO.File]::Delete($entry)
            }
        }
    }
    foreach ($directory in @($directories | Sort-Object { $_.Length } -Descending)) {
        $attributes = [System.IO.File]::GetAttributes($directory)
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Label 在清理期间变成链接：$directory"
        }
        [System.IO.Directory]::Delete($directory, $false)
    }
}

function Assert-ChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Child
    )
    $parentPath = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    $childPath = [System.IO.Path]::GetFullPath($Child)
    if (-not $childPath.StartsWith($parentPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "安装目录不安全：$childPath"
    }
}

function Invoke-WithSingleRetry {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [Parameter(Mandatory = $true)][string]$Description
    )
    try {
        & $Action
    }
    catch {
        $firstError = $_.Exception.Message
        Start-Sleep -Milliseconds 1500
        try {
            & $Action
        }
        catch {
            throw "$Description 失败：$($_.Exception.Message)（首次失败：$firstError）"
        }
    }
}

function Test-ProcessPathWithinInstallRoot {
    param(
        [AllowEmptyString()][string]$ProcessPath,
        [Parameter(Mandatory = $true)][string]$ExpectedInstallRoot
    )
    if ([string]::IsNullOrWhiteSpace($ProcessPath)) {
        throw "无法读取 VideoInsight 进程路径，请人工关闭软件后重试。"
    }
    $installPrefix = [System.IO.Path]::GetFullPath($ExpectedInstallRoot).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $resolvedProcessPath = [System.IO.Path]::GetFullPath($ProcessPath)
    return $resolvedProcessPath.StartsWith($installPrefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Stop-VideoInsightProcesses {
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedInstallRoot
    )
    $processes = @(Get-Process -Name VideoInsight, VideoInsightBackend -ErrorAction SilentlyContinue)
    if ($processes.Count -eq 0) {
        return
    }

    $ownedProcesses = @()
    foreach ($process in $processes) {
        try {
            $processPath = [string]$process.Path
        }
        catch {
            throw "无法读取 VideoInsight 进程路径，请人工关闭软件后重试。"
        }
        if (Test-ProcessPathWithinInstallRoot -ProcessPath $processPath -ExpectedInstallRoot $ExpectedInstallRoot) {
            $ownedProcesses += $process
        }
    }
    if ($ownedProcesses.Count -eq 0) {
        return
    }

    $ownedProcesses | Stop-Process -Force
    try {
        $ownedProcesses | Wait-Process -Timeout 15 -ErrorAction Stop
    }
    catch {
        $remaining = @(
            $ownedProcesses |
                Where-Object { Get-Process -Id $_.Id -ErrorAction SilentlyContinue }
        )
        if ($remaining.Count -gt 0) {
            throw "VideoInsight 仍在后台运行，请关闭软件后重新安装。"
        }
    }
}

function Restore-UninstallRegistration {
    param(
        [string]$RegistryPath,
        [object]$PreviousValues
    )
    if (-not $RegistryPath) {
        return
    }
    if (-not $PreviousValues) {
        if (Test-Path -LiteralPath $RegistryPath) {
            Remove-Item -LiteralPath $RegistryPath -Recurse -Force -ErrorAction Stop
        }
        return
    }
    New-Item -Path $RegistryPath -Force | Out-Null
    foreach ($name in @("DisplayName", "DisplayVersion", "Publisher", "InstallLocation", "UninstallString")) {
        New-ItemProperty `
            -Path $RegistryPath `
            -Name $name `
            -Value ([string]$PreviousValues.$name) `
            -PropertyType String `
            -Force | Out-Null
    }
    foreach ($name in @("NoModify", "NoRepair")) {
        New-ItemProperty `
            -Path $RegistryPath `
            -Name $name `
            -Value ([int]$PreviousValues.$name) `
            -PropertyType DWord `
            -Force | Out-Null
    }
}

try {
    $payload = Join-Path $PSScriptRoot "payload.zip"
    if (-not (Test-Path -LiteralPath $payload)) {
        throw "安装包内容不完整：缺少 payload.zip"
    }

    $localAppData = [Environment]::GetFolderPath("LocalApplicationData")
    $programsRoot = Join-Path $localAppData "Programs"
    $installRoot = Join-Path $programsRoot "VideoInsight"
    $startMenuProgramsRoot = [Environment]::GetFolderPath("Programs")
    $startMenuDir = Join-Path $startMenuProgramsRoot "VideoInsight"
    $desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "VideoInsight.lnk"
    $startMenuShortcut = Join-Path $startMenuDir "VideoInsight.lnk"
    $trustedPowerShell = Join-Path $PSHOME "powershell.exe"
    if (-not (Test-Path -LiteralPath $trustedPowerShell -PathType Leaf)) {
        throw "找不到受信任的 Windows PowerShell。"
    }
    Assert-NoReparsePoint -Path $trustedPowerShell -Label "Windows PowerShell"
    Assert-ChildPath -Parent $programsRoot -Child $installRoot
    Assert-ChildPath -Parent $startMenuProgramsRoot -Child $startMenuDir
    foreach ($protectedRoot in @($programsRoot, $installRoot, $startMenuProgramsRoot, $startMenuDir, $desktopShortcut, $startMenuShortcut)) {
        Assert-NoReparsePointsInAncestors -Path $protectedRoot -Label "安装目标"
    }
    Assert-NoReparsePoint -Path $programsRoot -Label "程序目录"
    Assert-NoReparsePoint -Path $installRoot -Label "现有安装目录"
    Assert-NoReparsePoint -Path $startMenuProgramsRoot -Label "开始菜单目录"
    Assert-NoReparsePoint -Path $startMenuDir -Label "VideoInsight 开始菜单目录"
    Assert-NoReparsePoint -Path $desktopShortcut -Label "桌面快捷方式"
    Assert-NoReparsePoint -Path $startMenuShortcut -Label "开始菜单快捷方式"
    $uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VideoInsight"
    if (Test-Path -LiteralPath $uninstallKey) {
        $previousUninstall = Get-ItemProperty -LiteralPath $uninstallKey
    }
    $hasExistingInstall = Test-Path -LiteralPath $installRoot
    $validatedInstalledVersion = Get-ValidatedInstalledApplicationVersion `
        -InstallRoot $installRoot `
        -RegisteredVersion ([string]$previousUninstall.DisplayVersion)
    Assert-NewerInstallerVersion `
        -NewVersion $Version `
        -InstalledVersion $validatedInstalledVersion `
        -HasExistingInstall $hasExistingInstall

    New-Item -ItemType Directory -Path $programsRoot -Force | Out-Null
    $stage = Join-Path $programsRoot (".VideoInsight-install-" + [guid]::NewGuid().ToString("N"))
    $backupRoot = Join-Path $programsRoot (".VideoInsight-backup-" + [guid]::NewGuid().ToString("N"))
    Assert-ChildPath -Parent $programsRoot -Child $stage
    Assert-ChildPath -Parent $programsRoot -Child $backupRoot
    Assert-NoReparsePointsInAncestors -Path $stage -Label "新版本暂存目录"
    Assert-NoReparsePointsInAncestors -Path $backupRoot -Label "旧版本备份目录"

    $phase = "解压安装文件"
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    Expand-Archive -LiteralPath $payload -DestinationPath $stage -Force
    Assert-NoReparsePointsInTree -RootPath $stage -Label "新版本暂存目录"
    $stagedExecutable = Join-Path $stage "VideoInsight.exe"
    $stagedVersion = Get-ValidatedExecutableStableVersion `
        -ExecutablePath $stagedExecutable `
        -Label "新 payload VideoInsight.exe"
    if ($stagedVersion -ne $Version) {
        throw "新 payload 的实际 EXE 版本 $stagedVersion 与安装包版本 $Version 不一致。"
    }

    $phase = "关闭旧版后台进程"
    Stop-VideoInsightProcesses -ExpectedInstallRoot $installRoot

    if (Test-Path -LiteralPath $installRoot) {
        $phase = "保留旧版本"
        Assert-NoReparsePointsInTree -RootPath $installRoot -Label "现有安装目录"
        Invoke-WithSingleRetry `
            -Description "移动旧版本" `
            -Action { Move-Item -LiteralPath $installRoot -Destination $backupRoot }
    }

    $phase = "启用新版本"
    try {
        Invoke-WithSingleRetry `
            -Description "启用新版本" `
            -Action { Move-Item -LiteralPath $stage -Destination $installRoot }
        $stage = $null
        $newVersionActivated = $true
    }
    catch {
        if ((Test-Path -LiteralPath $backupRoot) -and -not (Test-Path -LiteralPath $installRoot)) {
            Move-Item -LiteralPath $backupRoot -Destination $installRoot
            $backupRoot = $null
        }
        throw
    }

    $phase = "写入卸载与验收工具"
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "uninstall_windows_desktop.ps1") -Destination (Join-Path $installRoot "Uninstall-VideoInsight.ps1") -Force
    if ($VerifierPath -and (Test-Path -LiteralPath $VerifierPath)) {
        Copy-Item -LiteralPath $VerifierPath -Destination (Join-Path $installRoot "Verify-VideoInsight.ps1") -Force
    }

    $phase = "创建快捷方式"
    $shell = New-Object -ComObject WScript.Shell
    $startMenuDirCreated = -not (Test-Path -LiteralPath $startMenuDir)
    if ($startMenuDirCreated) {
        New-Item -ItemType Directory -Path $startMenuDir | Out-Null
    }
    $shortcutBackupRoot = Join-Path $programsRoot (".VideoInsight-shortcuts-" + [guid]::NewGuid().ToString("N"))
    Assert-ChildPath -Parent $programsRoot -Child $shortcutBackupRoot
    New-Item -ItemType Directory -Path $shortcutBackupRoot | Out-Null
    $shortcutPaths = @($desktopShortcut, $startMenuShortcut)
    for ($shortcutIndex = 0; $shortcutIndex -lt $shortcutPaths.Count; $shortcutIndex++) {
        $shortcutPath = $shortcutPaths[$shortcutIndex]
        if (Test-Path -LiteralPath $shortcutPath) {
            Assert-NoReparsePoint -Path $shortcutPath -Label "既有快捷方式"
            $shortcutBackupPath = Join-Path $shortcutBackupRoot ("shortcut-$shortcutIndex.lnk")
            Copy-Item -LiteralPath $shortcutPath -Destination $shortcutBackupPath
            $shortcutBackups[$shortcutPath] = $shortcutBackupPath
        }
        $touchedShortcutPaths += $shortcutPath
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = Join-Path $installRoot "VideoInsight.exe"
        $shortcut.WorkingDirectory = $installRoot
        $shortcut.Description = "VideoInsight 短视频工作台"
        $shortcut.Save()
    }

    $phase = "注册卸载信息"
    New-Item -Path $uninstallKey -Force | Out-Null
    $uninstallScript = Join-Path $installRoot "Uninstall-VideoInsight.ps1"
    $uninstallCommand = '"' + $trustedPowerShell + '" -NoProfile -ExecutionPolicy Bypass -File "' + $uninstallScript + '"'
    New-ItemProperty -Path $uninstallKey -Name DisplayName -Value "VideoInsight" -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name DisplayVersion -Value $Version -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name Publisher -Value "VideoInsight" -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name InstallLocation -Value $installRoot -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name UninstallString -Value $uninstallCommand -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null

    $phase = "启动 VideoInsight"
    Start-Process -FilePath (Join-Path $installRoot "VideoInsight.exe")

    $phase = "自动验收新版本"
    $installedVerifier = Join-Path $installRoot "Verify-VideoInsight.ps1"
    if (-not (Test-Path -LiteralPath $installedVerifier -PathType Leaf)) {
        throw "安装包缺少自动验收工具，不能确认新版本可用。"
    }
    $verificationPowerShell = $trustedPowerShell
    & $verificationPowerShell `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $installedVerifier `
        -ExpectedVersion $Version
    if ($LASTEXITCODE -ne 0) {
        throw "自动验收未全部通过，已停止启用新版本。"
    }
    $installationCommitted = $true

    $oldBackupRemoved = Remove-PostCommitBackupSafely `
        -Path $backupRoot `
        -Label "旧版本备份目录"
    if ($oldBackupRemoved) {
        $backupRoot = $null
    }
    $shortcutBackupRemoved = Remove-PostCommitBackupSafely `
        -Path $shortcutBackupRoot `
        -Label "快捷方式备份目录"
    if ($shortcutBackupRemoved) {
        $shortcutBackupRoot = $null
    }

    if (-not $Quiet) {
        try {
            Add-Type -AssemblyName PresentationFramework
            [System.Windows.MessageBox]::Show(
                "VideoInsight 已安装并启动。桌面快捷方式已经创建。`n后续覆盖安装会保留客户数据。",
                "VideoInsight 安装完成"
            ) | Out-Null
        }
        catch {
            Write-Warning "完成提示未能显示；已验收的新版本和卸载登记会继续保留。"
        }
    }
}
catch {
    $originalError = $_.Exception.Message
    if (-not (Test-InstallationRollbackRequired -InstallationCommitted $installationCommitted)) {
        Write-Warning "$phase 的后置处理未完成：$originalError；已验收的新版本和卸载登记会继续保留。"
        return
    }
    $rollbackError = ""
    try {
        if ($newVersionActivated -or ($backupRoot -and (Test-Path -LiteralPath $backupRoot))) {
            Stop-VideoInsightProcesses -ExpectedInstallRoot $installRoot
        }
        if ($backupRoot -and (Test-Path -LiteralPath $backupRoot)) {
            if ($installRoot -and (Test-Path -LiteralPath $installRoot)) {
                Assert-NoReparsePointsInTree -RootPath $installRoot -Label "失败的新版本目录"
                Remove-DirectoryTreeWithoutFollowingReparse -RootPath $installRoot -Label "失败的新版本目录"
            }
            Move-Item -LiteralPath $backupRoot -Destination $installRoot
            $backupRoot = $null
            Restore-UninstallRegistration `
                -RegistryPath $uninstallKey `
                -PreviousValues $previousUninstall
            $previousExecutable = Join-Path $installRoot "VideoInsight.exe"
            if (Test-Path -LiteralPath $previousExecutable -PathType Leaf) {
                Start-Process -FilePath $previousExecutable
            }
        }
        elseif ($newVersionActivated -and $installRoot -and (Test-Path -LiteralPath $installRoot)) {
            Assert-NoReparsePointsInTree -RootPath $installRoot -Label "失败的新版本目录"
            Remove-DirectoryTreeWithoutFollowingReparse -RootPath $installRoot -Label "失败的新版本目录"
            Restore-UninstallRegistration -RegistryPath $uninstallKey -PreviousValues $previousUninstall
        }
    }
    catch {
        $rollbackError += "；自动恢复应用或卸载登记失败：$($_.Exception.Message)"
    }
    try {
        foreach ($shortcutPath in $touchedShortcutPaths) {
            if (Test-Path -LiteralPath $shortcutPath) {
                Assert-NoReparsePoint -Path $shortcutPath -Label "失败安装创建的快捷方式"
                Remove-Item -LiteralPath $shortcutPath -Force
            }
            if ($shortcutBackups.ContainsKey($shortcutPath)) {
                [System.IO.File]::Copy($shortcutBackups[$shortcutPath], $shortcutPath, $false)
            }
        }
        if ($startMenuDirCreated -and (Test-Path -LiteralPath $startMenuDir -PathType Container)) {
            Assert-NoReparsePoint -Path $startMenuDir -Label "本次创建的开始菜单目录"
            if (@(Get-ChildItem -LiteralPath $startMenuDir -Force).Count -eq 0) {
                Remove-Item -LiteralPath $startMenuDir -Force
            }
        }
        if ($shortcutBackupRoot -and (Test-Path -LiteralPath $shortcutBackupRoot)) {
            Assert-NoReparsePointsInTree -RootPath $shortcutBackupRoot -Label "快捷方式备份目录"
            Remove-DirectoryTreeWithoutFollowingReparse -RootPath $shortcutBackupRoot -Label "快捷方式备份目录"
            $shortcutBackupRoot = $null
        }
    }
    catch {
        $rollbackError += "；自动恢复快捷方式失败：$($_.Exception.Message)"
    }
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        try {
            Assert-NoReparsePointsInTree -RootPath $stage -Label "新版本暂存目录"
            Remove-DirectoryTreeWithoutFollowingReparse -RootPath $stage -Label "新版本暂存目录"
        }
        catch {
        }
    }
    $message = "$phase 失败：$originalError$rollbackError"
    if ($Quiet) {
        Write-Error $message
    }
    else {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            $message,
            "VideoInsight 安装失败"
        ) | Out-Null
    }
    exit 1
}
