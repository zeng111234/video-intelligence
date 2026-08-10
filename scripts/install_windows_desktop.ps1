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
$desktopShortcut = $null
$startMenuDir = $null

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

function Stop-VideoInsightProcesses {
    $processes = @(Get-Process -Name VideoInsight, VideoInsightBackend -ErrorAction SilentlyContinue)
    if ($processes.Count -eq 0) {
        return
    }

    $processes | Stop-Process -Force
    try {
        $processes | Wait-Process -Timeout 15 -ErrorAction Stop
    }
    catch {
        $remaining = @(Get-Process -Name VideoInsight, VideoInsightBackend -ErrorAction SilentlyContinue)
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
        Remove-Item -LiteralPath $RegistryPath -Recurse -Force -ErrorAction SilentlyContinue
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
    Assert-ChildPath -Parent $programsRoot -Child $installRoot
    $uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VideoInsight"
    if (Test-Path -LiteralPath $uninstallKey) {
        $previousUninstall = Get-ItemProperty -LiteralPath $uninstallKey
    }

    New-Item -ItemType Directory -Path $programsRoot -Force | Out-Null
    $stage = Join-Path $programsRoot (".VideoInsight-install-" + [guid]::NewGuid().ToString("N"))
    $backupRoot = Join-Path $programsRoot (".VideoInsight-backup-" + [guid]::NewGuid().ToString("N"))
    Assert-ChildPath -Parent $programsRoot -Child $stage
    Assert-ChildPath -Parent $programsRoot -Child $backupRoot

    $phase = "解压安装文件"
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    Expand-Archive -LiteralPath $payload -DestinationPath $stage -Force
    if (-not (Test-Path -LiteralPath (Join-Path $stage "VideoInsight.exe"))) {
        throw "安装包内容不完整：缺少 VideoInsight.exe"
    }

    $phase = "关闭旧版后台进程"
    Stop-VideoInsightProcesses

    if (Test-Path -LiteralPath $installRoot) {
        $phase = "保留旧版本"
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
    $desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "VideoInsight.lnk"
    $startMenuDir = Join-Path ([Environment]::GetFolderPath("Programs")) "VideoInsight"
    New-Item -ItemType Directory -Path $startMenuDir -Force | Out-Null
    $startMenuShortcut = Join-Path $startMenuDir "VideoInsight.lnk"
    foreach ($shortcutPath in @($desktopShortcut, $startMenuShortcut)) {
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = Join-Path $installRoot "VideoInsight.exe"
        $shortcut.WorkingDirectory = $installRoot
        $shortcut.Description = "VideoInsight 短视频工作台"
        $shortcut.Save()
    }

    $phase = "注册卸载信息"
    New-Item -Path $uninstallKey -Force | Out-Null
    $uninstallScript = Join-Path $installRoot "Uninstall-VideoInsight.ps1"
    $uninstallCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $uninstallScript + '"'
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
    $verificationPowerShell = Join-Path $PSHOME "powershell.exe"
    if (-not (Test-Path -LiteralPath $verificationPowerShell -PathType Leaf)) {
        $verificationPowerShell = "powershell.exe"
    }
    & $verificationPowerShell `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $installedVerifier `
        -ExpectedVersion $Version `
        -RequireNoDeveloperTools
    if ($LASTEXITCODE -ne 0) {
        throw "自动验收未全部通过，已停止启用新版本。"
    }

    if (Test-Path -LiteralPath $backupRoot) {
        try {
            Invoke-WithSingleRetry `
                -Description "清理旧版本" `
                -Action { Remove-Item -LiteralPath $backupRoot -Recurse -Force }
            $backupRoot = $null
        }
        catch {
            # 新版本已成功启动；旧版本备份的延迟清理不应让安装显示失败。
        }
    }

    if (-not $Quiet) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            "VideoInsight 已安装并启动。桌面快捷方式已经创建。`n后续覆盖安装会保留客户数据。",
            "VideoInsight 安装完成"
        ) | Out-Null
    }
}
catch {
    $originalError = $_.Exception.Message
    $rollbackError = ""
    try {
        if ($newVersionActivated -or ($backupRoot -and (Test-Path -LiteralPath $backupRoot))) {
            Stop-VideoInsightProcesses
        }
        if ($backupRoot -and (Test-Path -LiteralPath $backupRoot)) {
            if ($installRoot -and (Test-Path -LiteralPath $installRoot)) {
                Remove-Item -LiteralPath $installRoot -Recurse -Force
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
            Remove-Item -LiteralPath $installRoot -Recurse -Force
            if ($desktopShortcut) {
                Remove-Item -LiteralPath $desktopShortcut -Force -ErrorAction SilentlyContinue
            }
            if ($startMenuDir -and (Test-Path -LiteralPath $startMenuDir)) {
                Remove-Item -LiteralPath $startMenuDir -Recurse -Force
            }
            Restore-UninstallRegistration -RegistryPath $uninstallKey -PreviousValues $null
        }
    }
    catch {
        $rollbackError = "；自动恢复旧版本也失败：$($_.Exception.Message)"
    }
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        try {
            Remove-Item -LiteralPath $stage -Recurse -Force
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
