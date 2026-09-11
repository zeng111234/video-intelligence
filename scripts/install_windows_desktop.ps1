param(
    [string]$Version = "0.2.0",
    [string]$VerifierPath = "",
    [string]$InstallRoot = "",
    [string]$RuntimeRoot = "",
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
$existingInstallRoot = $null
$previousRuntimeRoot = $null
$runtimeMigrationCreated = $false

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
        [AllowEmptyString()][string]$RegisteredVersion = "",
        [Parameter(Mandatory = $true)][bool]$HasExistingInstall
    )
    $stableVersionPattern = '^[0-9]+\.[0-9]+\.[0-9]+$'
    if ($NewVersion -notmatch $stableVersionPattern) {
        throw "安装包版本号无效：$NewVersion"
    }
    $hasValidInstalledVersion = `
        -not [string]::IsNullOrWhiteSpace($InstalledVersion) -and `
        $InstalledVersion -match $stableVersionPattern
    $hasRegisteredVersion = -not [string]::IsNullOrWhiteSpace($RegisteredVersion)
    $hasValidRegisteredVersion = $hasRegisteredVersion -and $RegisteredVersion -match $stableVersionPattern
    if ($hasRegisteredVersion -and -not $hasValidRegisteredVersion) {
        throw "卸载登记中的版本号无效；为防止误覆盖，已停止安装。"
    }
    if ($HasExistingInstall) {
        if (-not $hasValidInstalledVersion -or -not $hasValidRegisteredVersion) {
            throw "检测到已有安装目录，但应用实际版本或卸载登记版本缺失；为防止误覆盖，已停止安装。"
        }
        if (
            [version]$NewVersion -le [version]$InstalledVersion -or
            [version]$NewVersion -le [version]$RegisteredVersion
        ) {
            throw "应用实际版本为 $InstalledVersion，卸载登记版本为 $RegisteredVersion；只允许安装同时高于两者的新版本。"
        }
        if ($InstalledVersion -ne $RegisteredVersion) {
            Write-Warning "检测到旧安装的应用版本与卸载登记不一致；新版本更高，将安全修复登记。"
        }
        return
    }
    if ($hasValidRegisteredVersion -and [version]$NewVersion -le [version]$RegisteredVersion) {
        throw "卸载登记版本为 $RegisteredVersion；只允许安装更高版本，不能重复安装或降级。"
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
        [Parameter(Mandatory = $true)][string]$InstallRoot
    )
    $installRootAttributes = Get-ExistingPathAttributesForInstall -LiteralPath $InstallRoot
    if ($null -eq $installRootAttributes) {
        return ""
    }
    if (($installRootAttributes -band [System.IO.FileAttributes]::Directory) -eq 0) {
        throw "现有安装路径不是目录；已停止覆盖。"
    }
    Assert-NoReparsePointsInTree -RootPath $InstallRoot -Label "现有安装目录"
    $installedExecutable = Join-Path $InstallRoot "VideoInsight.exe"
    $fileVersion = Get-ValidatedExecutableStableVersion `
        -ExecutablePath $installedExecutable `
        -Label "现有 VideoInsight.exe"
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

function Get-RecordedDesktopPort {
    param([Parameter(Mandatory = $true)][string]$RuntimeRootPath)

    # 旧版把当前端口写在 desktop-runtime.json 里。旧版没有这个文件时返回 0，
    # 由调用方回退到扫描已知端口。
    $statePath = Join-Path $RuntimeRootPath "data\desktop-runtime.json"
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        return 0
    }
    try {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $port = [int]$state.port
        if ($port -ge 1024 -and $port -le 65535) {
            return $port
        }
    }
    catch {
    }
    return 0
}

function Get-OwnedListener {
    param(
        [Parameter(Mandatory = $true)][int[]]$Ports,
        [Parameter(Mandatory = $true)][string]$ExpectedInstallRoot
    )

    # 只返回"监听进程的可执行文件位于本安装目录内"的监听项。
    # 端口被别人（源码开发服务、其他程序）占用时不算数，也不该阻断升级。
    $owned = @()
    foreach ($port in $Ports) {
        $connections = @(
            Get-NetTCPConnection `
                -LocalPort $port `
                -State Listen `
                -ErrorAction SilentlyContinue
        )
        foreach ($connection in $connections) {
            $ownerId = [int]$connection.OwningProcess
            if ($ownerId -le 0) {
                continue
            }
            $ownerProcess = Get-Process -Id $ownerId -ErrorAction SilentlyContinue
            if (-not $ownerProcess) {
                continue
            }
            $ownerPath = ""
            try { $ownerPath = [string]$ownerProcess.Path } catch { $ownerPath = "" }
            if (
                -not [string]::IsNullOrWhiteSpace($ownerPath) -and
                (Test-ProcessPathWithinInstallRoot `
                    -ProcessPath $ownerPath `
                    -ExpectedInstallRoot $ExpectedInstallRoot)
            ) {
                $owned += $connection
            }
        }
    }
    return $owned
}

function Wait-LocalPortReleased {
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeRootPath,
        [Parameter(Mandatory = $true)][string]$ExpectedInstallRoot,
        [int[]]$CandidatePorts = @(),
        [int]$TimeoutSeconds = 30
    )

    # 只等进程退出是不够的：Windows 上端口可能还被占用一小段时间，此时新版本
    # 会挑到另一个端口，验收脚本却按旧端口去探活，于是误报"登录页 404"——
    # 0.2.51 的三次升级失败正是这种形态。这里必须确认端口真的释放了。
    #
    # 但不能无条件等 1001/2001：源码开发服务（scripts/start_all_services.ps1）
    # 用的就是这两个端口，盲等会把"开发服务器还在跑"误判成"旧 EXE 没退"，
    # 等满 30 秒后直接让升级失败。因此只等**确实属于本安装目录**的监听进程。
    $ports = @()
    $recorded = Get-RecordedDesktopPort -RuntimeRootPath $RuntimeRootPath
    if ($recorded -gt 0) {
        $ports += $recorded
    }
    $ports += $CandidatePorts
    $ports = @(
        $ports | Where-Object { $_ -ge 1024 -and $_ -le 65535 } | Select-Object -Unique
    )
    if ($ports.Count -eq 0) {
        return
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $owned = @(
            Get-OwnedListener -Ports $ports -ExpectedInstallRoot $ExpectedInstallRoot
        )
        if ($owned.Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 400
    }

    $stillListening = @(
        Get-OwnedListener -Ports $ports -ExpectedInstallRoot $ExpectedInstallRoot
    )
    $ownerIds = @($stillListening | Select-Object -ExpandProperty OwningProcess -Unique)
    throw (
        "等待旧版本释放本机端口超时（{0} 秒）：端口 {1} 仍被本安装目录的进程占用（PID: {2}）。" -f `
            $TimeoutSeconds,
            (@($ports) -join "、"),
            (($ownerIds | ForEach-Object { [string]$_ }) -join "、")
    )
}

function Remove-StaleDesktopRuntimeState {
    param([Parameter(Mandatory = $true)][string]$RuntimeRootPath)

    # 旧版本被强杀时会留下 desktop-runtime.json。新版本启动前必须清掉它，
    # 否则验收脚本会读到一条指向已死进程的旧记录，进而用错误的端口探活。
    $statePath = Join-Path $RuntimeRootPath "data\desktop-runtime.json"
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        return
    }
    try {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        # 注意：不要用 $pid 作变量名，它是 PowerShell 自动变量（当前进程 ID）。
        $recordedProcessId = [int]$state.pid
        if ($recordedProcessId -gt 0) {
            $alive = Get-Process -Id $recordedProcessId -ErrorAction SilentlyContinue
            if ($alive) {
                # 进程还在：不清记录，交给进程关闭流程处理。
                return
            }
        }
        Remove-Item -LiteralPath $statePath -Force
    }
    catch {
        # 内容已损坏，同样按失效记录删除，避免下次启动误读。
        Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
    }
}

function Get-ActiveTaskSummary {
    param([Parameter(Mandatory = $true)][string]$RuntimeRootPath)

    # 覆盖安装会打断正在跑的抓取/转写/数字人/剪辑。旧版本还活着时只能用免登录
    # 接口，因此读 /health 里的 active_task_count —— 它只含数量，不含任务内容。
    $candidates = @(
        (Get-RecordedDesktopPort -RuntimeRootPath $RuntimeRootPath),
        1001,
        2001
    ) | Where-Object { $_ -ge 1024 -and $_ -le 65535 } | Select-Object -Unique

    foreach ($port in $candidates) {
        try {
            $response = Invoke-WebRequest `
                -UseBasicParsing `
                -Uri ("http://127.0.0.1:{0}/health" -f $port) `
                -TimeoutSec 3 `
                -ErrorAction Stop
            $payload = $response.Content | ConvertFrom-Json
            if ($payload.service -eq "videoinsight-desktop-api") {
                return [pscustomobject]@{
                    Count  = [int]$payload.active_task_count
                    ByKind = $payload.active_tasks_by_kind
                    Port   = $port
                }
            }
        }
        catch {
        }
    }
    return [pscustomobject]@{ Count = 0; ByKind = $null; Port = 0 }
}

function Format-ActiveTaskSummary {
    param([Parameter(Mandatory = $true)][object]$Summary)

    $labels = @{
        crawler       = "找素材"
        video_editing = "剪辑"
        transcription = "转写"
        avatar        = "数字人"
        copywriting   = "文案"
        publishing    = "发布"
    }
    $parts = @()
    if ($Summary.ByKind) {
        foreach ($property in $Summary.ByKind.PSObject.Properties) {
            $label = $labels[$property.Name]
            if (-not $label) { $label = $property.Name }
            $parts += ("{0} {1} 个" -f $label, $property.Value)
        }
    }
    if ($parts.Count -eq 0) {
        return ("共 {0} 个任务正在进行。" -f $Summary.Count)
    }
    return ("正在进行的任务：" + ($parts -join "、") + "。")
}

function Resolve-ActiveTasksBeforeUpdate {
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeRootPath,
        [switch]$Quiet,
        [int]$WaitTimeoutSeconds = 900
    )

    $summary = Get-ActiveTaskSummary -RuntimeRootPath $RuntimeRootPath
    if ($summary.Count -le 0) {
        return
    }
    if ($Quiet) {
        # 静默模式没有交互界面，只能记录后继续；新版启动时会自动恢复被中断的
        # 抓取队列，因此不会留下永久卡住的任务。
        Write-Warning (
            "检测到 {0} 个进行中的任务，静默安装将继续进行。" -f $summary.Count
        )
        return
    }

    $detail = Format-ActiveTaskSummary -Summary $summary
    Write-Host $detail

    while ($true) {
        Add-Type -AssemblyName PresentationFramework
        $answer = [System.Windows.MessageBox]::Show(
            (
                "$detail`n`n现在更新会打断这些任务。`n`n" +
                "选择""是""：等它们跑完再更新（软件保持打开即可）。`n" +
                "选择""否""：立即更新；新版启动后会自动恢复被中断的抓取任务。`n" +
                "选择""取消""：先不更新。"
            ),
            "VideoInsight 有新版本，但还有任务在跑",
            [System.Windows.MessageBoxButton]::YesNoCancel,
            [System.Windows.MessageBoxImage]::Warning
        )

        if ($answer -eq [System.Windows.MessageBoxResult]::Cancel) {
            throw "用户取消了更新：仍有任务正在进行。"
        }
        if ($answer -eq [System.Windows.MessageBoxResult]::No) {
            return
        }

        # 等待任务跑完。期间不打断用户；超时后重新询问，不无限静默等待。
        $phase = "等待进行中的任务完成"
        $deadline = (Get-Date).AddSeconds($WaitTimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 3
            $current = Get-ActiveTaskSummary -RuntimeRootPath $RuntimeRootPath
            if ($current.Count -le 0) {
                return
            }
        }
        $summary = Get-ActiveTaskSummary -RuntimeRootPath $RuntimeRootPath
        if ($summary.Count -le 0) {
            return
        }
        $detail = (
            "等待超时，仍有任务在进行。" + (Format-ActiveTaskSummary -Summary $summary)
        )
    }
}

function Get-UserDataDirFromCommandLine {
    param([AllowEmptyString()][string]$CommandLine)

    # 提取 --user-data-dir 的值。Windows 上有三种常见写法，必须都支持：
    #
    #   1) 整段参数被引号包住："--user-data-dir=C:\path with space"
    #   2) 只给值加引号：   --user-data-dir="C:\path with space"
    #   3) 不带引号：       --user-data-dir=C:\profile
    #
    # 顺序不能颠倒。若先匹配第 3 种，第 1 种会在第一个空格处被截断（得到
    # C:\path），而不含空格时又会把结尾的引号带进值里（得到 C:\profile"）。
    # 这两种都会让遗留浏览器匹配不上、profile 锁回收不掉。
    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return ""
    }
    $patterns = @(
        '"--user-data-dir=([^"]+)"',
        '--user-data-dir="([^"]+)"',
        '--user-data-dir=([^\s"]+)'
    )
    foreach ($pattern in $patterns) {
        $match = [regex]::Match(
            $CommandLine,
            $pattern,
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
        if ($match.Success) {
            return ([string]$match.Groups[1].Value).Replace("/", "\").TrimEnd("\")
        }
    }
    return ""
}

function Stop-OwnedBrowsers {
    param([Parameter(Mandatory = $true)][string]$RuntimeRootPath)

    # 后端退出时它自己会关闭由它启动的浏览器；但旧版本被强杀时来不及做，
    # 残留的 Chrome 会占住 profile 锁，让新版抓取打不开页面。
    #
    # 只结束 PID 与 --user-data-dir 都匹配的进程。绝不按进程名批量结束 Chrome，
    # 否则会连带关掉用户自己开着的浏览器。
    $profileRoot = Join-Path $RuntimeRootPath "data\browser_profiles"
    if (-not (Test-Path -LiteralPath $profileRoot -PathType Container)) {
        return
    }

    $records = @(
        Get-ChildItem `
            -LiteralPath $profileRoot `
            -Recurse `
            -Filter "browser-process.json" `
            -File `
            -ErrorAction SilentlyContinue
    )
    foreach ($recordPath in $records) {
        try {
            $record = Get-Content -LiteralPath $recordPath.FullName -Raw | ConvertFrom-Json
            $recordedProcessId = [int]$record.pid
            $userDataDir = [string]$record.user_data_dir
            if ($recordedProcessId -le 0 -or [string]::IsNullOrWhiteSpace($userDataDir)) {
                Remove-Item -LiteralPath $recordPath.FullName -Force -ErrorAction SilentlyContinue
                continue
            }
            $browserProcess = Get-Process -Id $recordedProcessId -ErrorAction SilentlyContinue
            if (-not $browserProcess) {
                Remove-Item -LiteralPath $recordPath.FullName -Force -ErrorAction SilentlyContinue
                continue
            }
            # 必须核对命令行里的 --user-data-dir：PID 会被系统复用，可能已经
            # 指向用户后来打开的别的程序。
            $commandLine = ""
            try { $commandLine = [string]$browserProcess.CommandLine } catch { $commandLine = "" }
            if ([string]::IsNullOrWhiteSpace($commandLine)) {
                $commandLine = [string](
                    Get-CimInstance `
                        Win32_Process `
                        -Filter "ProcessId=$recordedProcessId" `
                        -ErrorAction SilentlyContinue
                ).CommandLine
            }
            $expected = Get-UserDataDirFromCommandLine -CommandLine $commandLine
            if ([string]::IsNullOrWhiteSpace($expected)) {
                # 命令行里没有 --user-data-dir，不是我们启动的浏览器。
                Remove-Item -LiteralPath $recordPath.FullName -Force -ErrorAction SilentlyContinue
                continue
            }
            $normalizedRecorded = $userDataDir.Replace("/", "\").TrimEnd("\")
            if (
                -not [string]::Equals(
                    $expected,
                    $normalizedRecorded,
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            ) {
                # 不是我们启动的浏览器（或 PID 已被复用），不碰它。
                Remove-Item -LiteralPath $recordPath.FullName -Force -ErrorAction SilentlyContinue
                continue
            }
            Write-Host ("关闭上次遗留的浏览器进程（PID {0}）。" -f $recordedProcessId)
            Stop-Process -Id $recordedProcessId -Force -ErrorAction SilentlyContinue
            try {
                Wait-Process -Id $recordedProcessId -Timeout 20 -ErrorAction Stop
            }
            catch {
                Write-Warning (
                    "浏览器进程 {0} 未在 20 秒内退出，请手动关闭后重试。" -f $recordedProcessId
                )
            }
            Remove-Item -LiteralPath $recordPath.FullName -Force -ErrorAction SilentlyContinue
        }
        catch {
            # 单条记录损坏不应中断安装。
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
    foreach ($name in @("DisplayName", "DisplayVersion", "Publisher", "DisplayIcon", "InstallLocation", "RuntimeLocation", "UninstallString", "QuietUninstallString")) {
        $property = $PreviousValues.PSObject.Properties[$name]
        if ($null -eq $property -or $null -eq $property.Value) { continue }
        New-ItemProperty `
            -Path $RegistryPath `
            -Name $name `
            -Value ([string]$property.Value) `
            -PropertyType String `
            -Force | Out-Null
    }
    foreach ($name in @("NoModify", "NoRepair", "EstimatedSize")) {
        $property = $PreviousValues.PSObject.Properties[$name]
        if ($null -eq $property -or $null -eq $property.Value) { continue }
        New-ItemProperty `
            -Path $RegistryPath `
            -Name $name `
            -Value ([int]$property.Value) `
            -PropertyType DWord `
            -Force | Out-Null
    }
}

function Restore-ShortcutBackupSafely {
    param(
        [Parameter(Mandatory = $true)][string]$BackupPath,
        [Parameter(Mandatory = $true)][string]$ShortcutPath
    )
    if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
        throw "快捷方式备份不存在，无法自动恢复。"
    }
    Assert-NoReparsePoint -Path $BackupPath -Label "快捷方式备份"
    $shortcutParent = [System.IO.Path]::GetDirectoryName($ShortcutPath)
    Assert-NoReparsePointsInAncestors -Path $shortcutParent -Label "快捷方式恢复目录"
    if (-not (Test-Path -LiteralPath $shortcutParent -PathType Container)) {
        New-Item -ItemType Directory -Path $shortcutParent | Out-Null
    }
    Assert-NoReparsePoint -Path $shortcutParent -Label "快捷方式恢复目录"
    [System.IO.File]::Copy($BackupPath, $ShortcutPath, $false)
}

function Assert-FixedLocalDrivePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not [System.IO.Path]::IsPathRooted($Path) -or $Path.StartsWith('\\')) {
        throw "$Label 必须位于电脑内置的本地磁盘。"
    }
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $driveRoot = [System.IO.Path]::GetPathRoot($fullPath)
    $drive = [System.IO.DriveInfo]::new($driveRoot)
    if (-not $drive.IsReady -or $drive.DriveType -ne [System.IO.DriveType]::Fixed) {
        throw "$Label 不能位于U盘或网络盘。"
    }
    if ([string]::Equals(
        $fullPath.TrimEnd('\', '/'),
        $driveRoot.TrimEnd('\', '/'),
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Label 不能直接使用磁盘根目录。"
    }
}

function Test-EmptyDirectoryForInstall {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    Assert-NoReparsePoint -Path $Path -Label "预建安装目录"
    return @(Get-ChildItem -LiteralPath $Path -Force).Count -eq 0
}

function Test-ReusableVideoInsightRuntimeRoot {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    Assert-NoReparsePointsInTree -RootPath $Path -Label "既有客户数据目录"
    $marker = Join-Path $Path ".videoinsight-runtime.json"
    if (Test-Path -LiteralPath $marker -PathType Leaf) {
        try {
            $payload = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
            return $payload.product -eq "VideoInsight" -and [int]$payload.schema_version -eq 1
        }
        catch { return $false }
    }
    # 兼容 0.2.39：旧版尚未写归属标记，只接管包含真实 SQLite 库的数据目录。
    $database = Join-Path $Path "data\video_intelligence.db"
    if (-not (Test-Path -LiteralPath $database -PathType Leaf)) { return $false }
    $stream = [System.IO.File]::OpenRead($database)
    try {
        if ($stream.Length -lt 16) { return $false }
        $header = New-Object byte[] 16
        [void]$stream.Read($header, 0, 16)
        return [System.Text.Encoding]::ASCII.GetString($header) -eq "SQLite format 3`0"
    }
    finally { $stream.Dispose() }
}

function Get-FileSha256ForMigration {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha256.ComputeHash($stream)
        return ([System.BitConverter]::ToString($digest)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

function Copy-DirectoryTreeForMigration {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )
    Assert-NoReparsePointsInTree -RootPath $SourceRoot -Label "原数据目录"
    if (Test-Path -LiteralPath $DestinationRoot) {
        throw "新的数据目录已经存在，为避免覆盖未知文件，已停止迁移：$DestinationRoot"
    }
    New-Item -ItemType Directory -Path $DestinationRoot | Out-Null
    $sourcePath = [System.IO.Path]::GetFullPath($SourceRoot).TrimEnd('\', '/')
    $destinationPath = [System.IO.Path]::GetFullPath($DestinationRoot).TrimEnd('\', '/')
    foreach ($directory in Get-ChildItem -LiteralPath $sourcePath -Directory -Recurse -Force) {
        $relative = $directory.FullName.Substring($sourcePath.Length).TrimStart('\', '/')
        New-Item -ItemType Directory -Path (Join-Path $destinationPath $relative) -Force | Out-Null
    }
    foreach ($file in Get-ChildItem -LiteralPath $sourcePath -File -Recurse -Force) {
        $relative = $file.FullName.Substring($sourcePath.Length).TrimStart('\', '/')
        $target = Join-Path $destinationPath $relative
        $targetParent = [System.IO.Path]::GetDirectoryName($target)
        if (-not (Test-Path -LiteralPath $targetParent)) {
            New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
        }
        [System.IO.File]::Copy($file.FullName, $target, $false)
        if ((Get-Item -LiteralPath $target).Length -ne $file.Length) {
            throw "数据文件复制后大小不一致：$relative"
        }
        $sourceHash = Get-FileSha256ForMigration -Path $file.FullName
        $targetHash = Get-FileSha256ForMigration -Path $target
        if ($sourceHash -ne $targetHash) {
            throw "数据文件复制后内容不一致：$relative"
        }
    }
    Assert-NoReparsePointsInTree -RootPath $DestinationRoot -Label "新数据目录"
}

try {
    # 全局互斥：用户重复双击安装包时只能有一个安装流程在跑。
    #
    # 否则两个安装器会同时移动同一个安装目录、同时写卸载登记、同时启动新版本，
    # 结果就是"半更新"状态和多个后端进程。
    $installerMutex = New-Object System.Threading.Mutex(
        $false,
        "Global\VideoInsightInstaller"
    )
    $mutexAcquired = $false
    try {
        $mutexAcquired = $installerMutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        # 上一个安装器异常退出留下了这个互斥体，视为已获取。
        $mutexAcquired = $true
    }
    if (-not $mutexAcquired) {
        $busyMessage = "已经有一个 VideoInsight 安装程序正在运行，请等它结束后再试。"
        if ($Quiet) {
            Write-Error $busyMessage
        }
        else {
            Add-Type -AssemblyName PresentationFramework
            [System.Windows.MessageBox]::Show($busyMessage, "VideoInsight 安装") | Out-Null
        }
        exit 1
    }

    $payload = Join-Path $PSScriptRoot "payload.zip"
    if (-not (Test-Path -LiteralPath $payload)) {
        throw "安装包内容不完整：缺少 payload.zip"
    }

    $localAppData = [Environment]::GetFolderPath("LocalApplicationData")
    $defaultInstallRoot = Join-Path $localAppData "Programs\VideoInsight"
    $defaultRuntimeRoot = Join-Path $localAppData "VideoInsight"
    $uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VideoInsight"
    if (Test-Path -LiteralPath $uninstallKey) {
        $previousUninstall = Get-ItemProperty -LiteralPath $uninstallKey
    }
    if ([string]::IsNullOrWhiteSpace($InstallRoot)) { $InstallRoot = $defaultInstallRoot }
    if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) { $RuntimeRoot = $defaultRuntimeRoot }
    $installRoot = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\', '/')
    $RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\', '/')
    $programsRoot = [System.IO.Path]::GetDirectoryName($installRoot)
    $existingInstallRoot = if (-not [string]::IsNullOrWhiteSpace([string]$previousUninstall.InstallLocation)) {
        [System.IO.Path]::GetFullPath([string]$previousUninstall.InstallLocation).TrimEnd('\', '/')
    }
    else { $installRoot }
    $previousRuntimeRoot = if (-not [string]::IsNullOrWhiteSpace([string]$previousUninstall.RuntimeLocation)) {
        [System.IO.Path]::GetFullPath([string]$previousUninstall.RuntimeLocation).TrimEnd('\', '/')
    }
    else { $defaultRuntimeRoot }
    Assert-FixedLocalDrivePath -Path $installRoot -Label "程序安装位置"
    Assert-FixedLocalDrivePath -Path $RuntimeRoot -Label "数据保存位置"
    if ([string]::Equals($installRoot, $RuntimeRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "程序目录和数据目录必须分开，避免升级时覆盖客户数据。"
    }
    $installPrefix = $installRoot + [System.IO.Path]::DirectorySeparatorChar
    $runtimePrefix = $RuntimeRoot + [System.IO.Path]::DirectorySeparatorChar
    if (
        $RuntimeRoot.StartsWith($installPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        $installRoot.StartsWith($runtimePrefix, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "程序目录和数据目录不能互相包含，避免升级或卸载时误删客户数据。"
    }
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
    foreach ($protectedRoot in @($programsRoot, $installRoot, $RuntimeRoot, $existingInstallRoot, $previousRuntimeRoot, $startMenuProgramsRoot, $startMenuDir, $desktopShortcut, $startMenuShortcut)) {
        Assert-NoReparsePointsInAncestors -Path $protectedRoot -Label "安装目标"
    }
    Assert-NoReparsePoint -Path $programsRoot -Label "程序目录"
    Assert-NoReparsePoint -Path $installRoot -Label "现有安装目录"
    Assert-NoReparsePoint -Path $startMenuProgramsRoot -Label "开始菜单目录"
    Assert-NoReparsePoint -Path $startMenuDir -Label "VideoInsight 开始菜单目录"
    Assert-NoReparsePoint -Path $desktopShortcut -Label "桌面快捷方式"
    Assert-NoReparsePoint -Path $startMenuShortcut -Label "开始菜单快捷方式"
    $precreatedEmptyInstallRoot = Test-EmptyDirectoryForInstall -Path $installRoot
    if (
        (Test-Path -LiteralPath $installRoot) -and
        -not $precreatedEmptyInstallRoot -and
        -not [string]::Equals($installRoot, $existingInstallRoot, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "选择的新安装目录已存在，为避免覆盖未知文件，已停止安装。"
    }
    $hasExistingInstall = Test-Path -LiteralPath $existingInstallRoot
    if (
        $precreatedEmptyInstallRoot -and
        [string]::Equals($existingInstallRoot, $installRoot, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        $hasExistingInstall = $false
    }
    $validatedInstalledVersion = Get-ValidatedInstalledApplicationVersion `
        -InstallRoot $existingInstallRoot
    Assert-NewerInstallerVersion `
        -NewVersion $Version `
        -InstalledVersion $validatedInstalledVersion `
        -RegisteredVersion ([string]$previousUninstall.DisplayVersion) `
        -HasExistingInstall $hasExistingInstall

    New-Item -ItemType Directory -Path $programsRoot -Force | Out-Null
    $stage = Join-Path $programsRoot (".VideoInsight-install-" + [guid]::NewGuid().ToString("N"))
    $backupParent = [System.IO.Path]::GetDirectoryName($existingInstallRoot)
    $backupRoot = Join-Path $backupParent (".VideoInsight-backup-" + [guid]::NewGuid().ToString("N"))
    Assert-ChildPath -Parent $programsRoot -Child $stage
    Assert-ChildPath -Parent $backupParent -Child $backupRoot
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

    # 覆盖安装前先确认没有正在跑的抓取/转写/数字人/剪辑任务，避免打断用户的活。
    # 必须在关闭旧进程之前问，否则 /health 已经没人应答，永远查不到。
    $phase = "检查进行中的任务"
    Resolve-ActiveTasksBeforeUpdate -RuntimeRootPath $RuntimeRoot -Quiet:$Quiet

    $phase = "关闭旧版后台进程"
    Stop-VideoInsightProcesses -ExpectedInstallRoot $existingInstallRoot
    # 旧版被强杀时可能留下占用 profile 锁的浏览器；只回收确实由它启动的那些。
    $phase = "关闭上次遗留的浏览器"
    Stop-OwnedBrowsers -RuntimeRootPath $RuntimeRoot
    # 只等进程退出不够：端口可能还被占用，新版本会挑到别的端口，而验收脚本
    # 仍按旧端口探活，于是误报"登录页 404"。必须确认端口真的释放。
    Wait-LocalPortReleased `
        -RuntimeRootPath $RuntimeRoot `
        -ExpectedInstallRoot $existingInstallRoot `
        -CandidatePorts @(
            (Get-RecordedDesktopPort -RuntimeRootPath $RuntimeRoot),
            1001,
            2001
        )
    # 清掉旧进程留下的失效运行时记录，避免新版本启动后验收读到已死进程的端口。
    Remove-StaleDesktopRuntimeState -RuntimeRootPath $RuntimeRoot

    if ($hasExistingInstall) {
        $phase = "保留旧版本"
        Assert-NoReparsePointsInTree -RootPath $existingInstallRoot -Label "现有安装目录"
        Invoke-WithSingleRetry `
            -Description "移动旧版本" `
            -Action { Move-Item -LiteralPath $existingInstallRoot -Destination $backupRoot }
    }

    $phase = "启用新版本"
    try {
        if ($precreatedEmptyInstallRoot) {
            Assert-NoReparsePoint -Path $installRoot -Label "预建安装目录"
            [System.IO.Directory]::Delete($installRoot, $false)
        }
        Invoke-WithSingleRetry `
            -Description "启用新版本" `
            -Action { Move-Item -LiteralPath $stage -Destination $installRoot }
        $stage = $null
        $newVersionActivated = $true
    }
    catch {
        if ((Test-Path -LiteralPath $backupRoot) -and -not (Test-Path -LiteralPath $existingInstallRoot)) {
            Move-Item -LiteralPath $backupRoot -Destination $existingInstallRoot
            $backupRoot = $null
        }
        throw
    }

    $phase = "准备客户数据目录"
    if (-not [string]::Equals($previousRuntimeRoot, $RuntimeRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $RuntimeRoot -PathType Container) {
            $runtimeRootReusable = Test-ReusableVideoInsightRuntimeRoot -Path $RuntimeRoot
            $runtimeRootEmpty = Test-EmptyDirectoryForInstall -Path $RuntimeRoot
            if (-not $runtimeRootReusable -and -not $runtimeRootEmpty) {
                throw "选择的数据目录不是可识别的 VideoInsight 数据目录，已停止安装：$RuntimeRoot"
            }
            if ($runtimeRootEmpty -and (Test-Path -LiteralPath $previousRuntimeRoot -PathType Container)) {
                [System.IO.Directory]::Delete($RuntimeRoot, $false)
                $runtimeMigrationCreated = $true
                Copy-DirectoryTreeForMigration -SourceRoot $previousRuntimeRoot -DestinationRoot $RuntimeRoot
            }
        }
        elseif (Test-Path -LiteralPath $previousRuntimeRoot -PathType Container) {
            $runtimeMigrationCreated = $true
            Copy-DirectoryTreeForMigration -SourceRoot $previousRuntimeRoot -DestinationRoot $RuntimeRoot
        }
        else {
            New-Item -ItemType Directory -Path $RuntimeRoot | Out-Null
            $runtimeMigrationCreated = $true
        }
    }
    elseif (-not (Test-Path -LiteralPath $RuntimeRoot)) {
        New-Item -ItemType Directory -Path $RuntimeRoot | Out-Null
    }
    elseif (-not (Test-ReusableVideoInsightRuntimeRoot -Path $RuntimeRoot) -and -not (Test-EmptyDirectoryForInstall -Path $RuntimeRoot)) {
        throw "选择的数据目录不是可识别的 VideoInsight 数据目录，已停止安装：$RuntimeRoot"
    }
    Assert-NoReparsePointsInTree -RootPath $RuntimeRoot -Label "客户数据目录"
    $runtimeMarker = @{ product = "VideoInsight"; schema_version = 1 } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText(
        (Join-Path $RuntimeRoot ".videoinsight-runtime.json"),
        $runtimeMarker,
        [System.Text.UTF8Encoding]::new($false)
    )

    $phase = "写入卸载与验收工具"
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "uninstall_windows_desktop.ps1") -Destination (Join-Path $installRoot "Uninstall-VideoInsight.ps1") -Force
    if ($VerifierPath -and (Test-Path -LiteralPath $VerifierPath)) {
        Copy-Item -LiteralPath $VerifierPath -Destination (Join-Path $installRoot "Verify-VideoInsight.ps1") -Force
    }
    $runtimeConfiguration = @{ runtimeRoot = $RuntimeRoot } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText(
        (Join-Path $installRoot "runtime-location.json"),
        $runtimeConfiguration,
        [System.Text.UTF8Encoding]::new($false)
    )

    $phase = "创建快捷方式"
    $shell = New-Object -ComObject WScript.Shell
    $startMenuDirCreated = -not (Test-Path -LiteralPath $startMenuDir)
    if ($startMenuDirCreated) {
        New-Item -ItemType Directory -Path $startMenuDir | Out-Null
    }
    $shortcutBackupRoot = Join-Path $programsRoot (".VideoInsight-shortcuts-" + [guid]::NewGuid().ToString("N"))
    Assert-ChildPath -Parent $programsRoot -Child $shortcutBackupRoot
    New-Item -ItemType Directory -Path $shortcutBackupRoot | Out-Null
    $shortcutDefinitions = @(
        @{
            Path = $desktopShortcut
            Target = (Join-Path $installRoot "VideoInsight.exe")
            Arguments = ""
            Description = "VideoInsight 视频创作工作台"
        },
        @{
            Path = $startMenuShortcut
            Target = (Join-Path $installRoot "VideoInsight.exe")
            Arguments = ""
            Description = "VideoInsight 视频创作工作台"
        }
    )
    for ($shortcutIndex = 0; $shortcutIndex -lt $shortcutDefinitions.Count; $shortcutIndex++) {
        $shortcutDefinition = $shortcutDefinitions[$shortcutIndex]
        $shortcutPath = [string]$shortcutDefinition.Path
        if (Test-Path -LiteralPath $shortcutPath) {
            Assert-NoReparsePoint -Path $shortcutPath -Label "既有快捷方式"
            $shortcutBackupPath = Join-Path $shortcutBackupRoot ("shortcut-$shortcutIndex.lnk")
            Copy-Item -LiteralPath $shortcutPath -Destination $shortcutBackupPath
            $shortcutBackups[$shortcutPath] = $shortcutBackupPath
        }
        $touchedShortcutPaths += $shortcutPath
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = [string]$shortcutDefinition.Target
        $shortcut.Arguments = [string]$shortcutDefinition.Arguments
        $shortcut.WorkingDirectory = $installRoot
        $shortcut.Description = [string]$shortcutDefinition.Description
        $shortcut.Save()
    }

    $phase = "注册卸载信息"
    New-Item -Path $uninstallKey -Force | Out-Null
    $uninstallCommand = '"' + $trustedPowerShell + '" -NoProfile -ExecutionPolicy Bypass -File "' + $uninstallScript + '"'
    $estimatedSizeKb = [Math]::Max(1, [int64]([Math]::Ceiling((Get-ChildItem -LiteralPath $installRoot -Recurse -Force -File | Measure-Object -Property Length -Sum).Sum / 1KB)))
    New-ItemProperty -Path $uninstallKey -Name DisplayName -Value "VideoInsight" -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name DisplayVersion -Value $Version -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name Publisher -Value "VideoInsight" -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name DisplayIcon -Value (Join-Path $installRoot "VideoInsight.exe") -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name InstallLocation -Value $installRoot -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name RuntimeLocation -Value $RuntimeRoot -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name UninstallString -Value $uninstallCommand -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name QuietUninstallString -Value $uninstallCommand -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $uninstallKey -Name EstimatedSize -Value $estimatedSizeKb -PropertyType DWord -Force | Out-Null

    $phase = "启动 VideoInsight"
    $desktopStartedAtUtc = [DateTime]::UtcNow
    Start-Process -FilePath (Join-Path $installRoot "VideoInsight.exe")

    $phase = "自动验收新版本"
    $installedVerifier = Join-Path $installRoot "Verify-VideoInsight.ps1"
    if (-not (Test-Path -LiteralPath $installedVerifier -PathType Leaf)) {
        throw "安装包缺少自动验收工具，不能确认新版本可用。"
    }
    $verificationPowerShell = $trustedPowerShell
    $verificationReportDirectory = Join-Path $RuntimeRoot "data\logs"
    New-Item -ItemType Directory -Path $verificationReportDirectory -Force | Out-Null
    $verificationReport = Join-Path $verificationReportDirectory ("install-acceptance-{0}.txt" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    $runtimeState = Join-Path $RuntimeRoot "data\desktop-runtime.json"
    # 等新版本写出运行时记录。后端要先导入整个应用和 uvicorn 才写这份记录，
    # 干净电脑上 15 秒不一定够；给 60 秒，并且确认记录确实是本次启动之后写的、
    # 内容可解析、端口合法，避免拿到一份旧记录就往下走。
    # 即便这里超时，验收脚本的轮询里也会继续重新发现端口。
    $recordDeadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $recordDeadline) {
        if (Test-Path -LiteralPath $runtimeState -PathType Leaf) {
            $stateFile = Get-Item -LiteralPath $runtimeState
            if ($stateFile.LastWriteTimeUtc -ge $desktopStartedAtUtc) {
                try {
                    $probe = Get-Content -LiteralPath $runtimeState -Raw | ConvertFrom-Json
                    $probePort = [int]$probe.port
                    if ($probePort -ge 1024 -and $probePort -le 65535) {
                        break
                    }
                }
                catch {
                    # 记录可能正在写入，下一轮再读。
                }
            }
        }
        Start-Sleep -Milliseconds 250
    }
    & $verificationPowerShell `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $installedVerifier `
        -ExpectedVersion $Version `
        -ReportPath $verificationReport `
        -InstalledAfterUtc $desktopStartedAtUtc.ToString("o")
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
    if (
        $runtimeMigrationCreated -and
        -not [string]::Equals($previousRuntimeRoot, $RuntimeRoot, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        [void](Remove-PostCommitBackupSafely -Path $previousRuntimeRoot -Label "旧数据目录")
    }

    if (-not $Quiet) {
        try {
            Add-Type -AssemblyName PresentationFramework
            [System.Windows.MessageBox]::Show(
                "VideoInsight 已安装并启动。桌面快捷方式已经创建。`n需要卸载时，请在 Windows 的「已安装的应用」中选择 VideoInsight；客户数据默认保留。`n后续覆盖安装会保留客户数据。",
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
            Move-Item -LiteralPath $backupRoot -Destination $existingInstallRoot
            $backupRoot = $null
            Restore-UninstallRegistration `
                -RegistryPath $uninstallKey `
                -PreviousValues $previousUninstall
            $previousExecutable = Join-Path $existingInstallRoot "VideoInsight.exe"
            if (Test-Path -LiteralPath $previousExecutable -PathType Leaf) {
                Start-Process -FilePath $previousExecutable
            }
        }
        elseif ($newVersionActivated -and $installRoot -and (Test-Path -LiteralPath $installRoot)) {
            Assert-NoReparsePointsInTree -RootPath $installRoot -Label "失败的新版本目录"
            Remove-DirectoryTreeWithoutFollowingReparse -RootPath $installRoot -Label "失败的新版本目录"
            Restore-UninstallRegistration -RegistryPath $uninstallKey -PreviousValues $previousUninstall
        }
        if ($runtimeMigrationCreated -and (Test-Path -LiteralPath $RuntimeRoot)) {
            Assert-NoReparsePointsInTree -RootPath $RuntimeRoot -Label "失败安装创建的数据目录"
            Remove-DirectoryTreeWithoutFollowingReparse -RootPath $RuntimeRoot -Label "失败安装创建的数据目录"
            $runtimeMigrationCreated = $false
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
                Restore-ShortcutBackupSafely `
                    -BackupPath $shortcutBackups[$shortcutPath] `
                    -ShortcutPath $shortcutPath
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
finally {
    # 无论成功失败都要释放安装互斥体，否则用户再也装不上。
    if ($mutexAcquired -and $installerMutex) {
        try {
            $installerMutex.ReleaseMutex()
        }
        catch {
        }
        $installerMutex.Dispose()
    }
}
