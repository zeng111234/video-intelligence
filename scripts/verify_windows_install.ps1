param(
    [ValidatePattern('^$|^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$ExpectedVersion = "",
    [string]$ReportPath = "",
    [switch]$RequireNoDeveloperTools,
    # 本次安装开始的时间（ISO8601）。用于确认 desktop-runtime.json 是这次安装
    # 产生的，而不是旧版本留下的——旧记录会让验收脚本用错误的端口探活。
    [string]$InstalledAfterUtc = ""
)

$ErrorActionPreference = "Stop"
$results = New-Object System.Collections.Generic.List[object]
$script:ServicePort = 1001

function Add-Check {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Evidence
    )
    $results.Add([pscustomobject]@{
        Check = $Name
        Result = if ($Passed) { "PASS" } else { "FAIL" }
        Evidence = $Evidence
    })
}

function ConvertTo-ThreePartVersion {
    param([AllowEmptyString()][string]$Value)

    $trimmed = ([string]$Value).Trim()
    if ($trimmed -notmatch '^([0-9]+)\.([0-9]+)\.([0-9]+)(?:\.0)?$') {
        return ""
    }
    return ("{0}.{1}.{2}" -f $matches[1], $matches[2], $matches[3])
}

function Invoke-LocalHttp {
    param(
        [string]$Uri,
        [hashtable]$Headers = @{},
        [string]$Method = "Get",
        [string]$Body = "",
        [string]$ContentType = "application/json"
    )
    $target = [Uri]$Uri
    if (
        $target.Scheme -ne "http" -or
        $target.Host -ne "127.0.0.1" -or
        $target.Port -ne $script:ServicePort -or
        -not [string]::IsNullOrEmpty($target.UserInfo)
    ) {
        throw "自动验收只允许连接本机 VideoInsight 服务。"
    }
    $request = [System.Net.HttpWebRequest]::Create($target)
    $request.Method = $Method.ToUpperInvariant()
    $request.Proxy = $null
    $request.AllowAutoRedirect = $false
    $request.Timeout = 10000
    $request.ReadWriteTimeout = 10000
    $request.Accept = "application/json"
    foreach ($name in $Headers.Keys) {
        $request.Headers[[string]$name] = [string]$Headers[$name]
    }
    if ($Body) {
        $payload = [System.Text.UTF8Encoding]::new($false).GetBytes($Body)
        $request.ContentType = $ContentType
        $request.ContentLength = $payload.Length
        $requestStream = $null
        try {
            $requestStream = $request.GetRequestStream()
            $requestStream.Write($payload, 0, $payload.Length)
        }
        catch [System.Net.WebException] {
            if ($null -eq $_.Exception.Response) {
                return [pscustomobject]@{ StatusCode = 0; Body = "" }
            }
            throw
        }
        finally {
            if ($null -ne $requestStream) {
                $requestStream.Dispose()
            }
        }
    }
    $response = $null
    try {
        $response = [System.Net.HttpWebResponse]$request.GetResponse()
    }
    catch [System.Net.WebException] {
        if ($null -ne $_.Exception.Response) {
            $response = [System.Net.HttpWebResponse]$_.Exception.Response
        }
        else {
            return [pscustomobject]@{ StatusCode = 0; Body = "" }
        }
    }
    try {
        $reader = [System.IO.StreamReader]::new(
            $response.GetResponseStream(),
            [System.Text.Encoding]::UTF8
        )
        try {
            $responseBody = $reader.ReadToEnd()
        }
        finally {
            $reader.Dispose()
        }
        return [pscustomobject]@{
            StatusCode = [int]$response.StatusCode
            Body = $responseBody
        }
    }
    finally {
        if ($null -ne $response) {
            $response.Dispose()
        }
    }
}

function Get-HttpStatus {
    param(
        [string]$Uri,
        [hashtable]$Headers = @{},
        [string]$Method = "Get",
        [string]$Body = "",
        [string]$ContentType = "application/json"
    )
    return [int](
        Invoke-LocalHttp `
            -Uri $Uri `
            -Headers $Headers `
            -Method $Method `
            -Body $Body `
            -ContentType $ContentType
    ).StatusCode
}

function Get-LocalHealth {
    $response = Invoke-LocalHttp -Uri ("{0}/health" -f $script:ServiceOrigin)
    if ($response.StatusCode -ne 200) {
        return $null
    }
    try {
        return $response.Body | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Test-SafeDirectoryTree {
    param([string]$LiteralPath)
    if (-not (Test-Path -LiteralPath $LiteralPath -PathType Container)) {
        return $false
    }
    $pending = New-Object 'System.Collections.Generic.Stack[System.IO.DirectoryInfo]'
    $root = Get-Item -LiteralPath $LiteralPath -Force
    if (($root.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        return $false
    }
    $pending.Push($root)
    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        foreach ($item in Get-ChildItem -LiteralPath $directory.FullName -Force) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                return $false
            }
            if ($item.PSIsContainer) {
                $pending.Push($item)
            }
        }
    }
    return $true
}

function Test-PathInsideRoot {
    param([string]$Candidate, [string]$Root)
    if ([string]::IsNullOrWhiteSpace($Candidate)) {
        return $false
    }
    try {
        $candidatePath = [System.IO.Path]::GetFullPath($Candidate)
        $rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    }
    catch {
        return $false
    }
    return $candidatePath.StartsWith(
        $rootPath + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Test-SafeLeaf {
    param([string]$LiteralPath)
    if (-not (Test-Path -LiteralPath $LiteralPath -PathType Leaf)) {
        return $false
    }
    $item = Get-Item -LiteralPath $LiteralPath -Force
    return ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0
}

$localAppData = [Environment]::GetFolderPath("LocalApplicationData")
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VideoInsight"
$uninstall = $null
$registeredVersion = ""
$registeredLocation = ""
$registeredRuntimeLocation = ""
if (Test-Path -LiteralPath $uninstallKey) {
    $uninstall = Get-ItemProperty -LiteralPath $uninstallKey
    $registeredVersion = [string]$uninstall.DisplayVersion
    $registeredLocation = [string]$uninstall.InstallLocation
    $registeredRuntimeLocation = [string]$uninstall.RuntimeLocation
}
$installRoot = if ([string]::IsNullOrWhiteSpace($registeredLocation)) {
    Join-Path $localAppData "Programs\VideoInsight"
}
else { [System.IO.Path]::GetFullPath($registeredLocation) }
$installedExe = Join-Path $installRoot "VideoInsight.exe"
$dataRoot = if ([string]::IsNullOrWhiteSpace($registeredRuntimeLocation)) {
    Join-Path $localAppData "VideoInsight"
}
else { [System.IO.Path]::GetFullPath($registeredRuntimeLocation) }
$database = Join-Path $dataRoot "data\video_intelligence.db"
$runtimeState = Join-Path $dataRoot "data\desktop-runtime.json"
$runtimePointer = Join-Path $installRoot "runtime-location.json"
$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "VideoInsight.lnk"
$startShortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "VideoInsight\VideoInsight.lnk"

# 新版桌面程序每次选择空闲本机端口；旧版没有状态文件时仍兼容 1001。
if (Test-SafeLeaf -LiteralPath $runtimeState) {
    try {
        $state = Get-Content -LiteralPath $runtimeState -Raw | ConvertFrom-Json
        $candidatePort = [int]$state.port
        $candidateProcess = Get-Process -Id ([int]$state.pid) -ErrorAction SilentlyContinue
        if (
            $state.schema_version -eq 1 -and
            $candidatePort -ge 1024 -and
            $candidatePort -le 65535 -and
            $candidateProcess
        ) {
            $script:ServicePort = $candidatePort
        }
    }
    catch { }
}
$script:ServiceOrigin = "http://127.0.0.1:$script:ServicePort"

# 运行时记录必须确实是"这次安装"产生的（0.2.52）。
#
# 0.2.51 的升级失败里，安装器会读到旧版本留下的 desktop-runtime.json，于是按
# 一个已经死掉的进程和错误的端口去探活，表现为"登录页 404"、"后端未就绪"。
# 因此除了端口和 PID 可用，还要确认：
#   * started_at 晚于本次安装开始时间；
#   * PID 指向的进程确实存在；
#   * 该进程的可执行文件位于本次安装目录内。
$runtimeRecordFresh = $false
$runtimeRecordEvidence = "no record"
if (Test-SafeLeaf -LiteralPath $runtimeState) {
    try {
        $record = Get-Content -LiteralPath $runtimeState -Raw | ConvertFrom-Json
        $recordPid = [int]$record.pid
        $recordProcess = Get-Process -Id $recordPid -ErrorAction SilentlyContinue
        $recordProcessPath = ""
        if ($recordProcess) {
            try { $recordProcessPath = [string]$recordProcess.Path } catch { $recordProcessPath = "" }
        }
        $processInsideInstallRoot = Test-PathInsideRoot `
            -Candidate $recordProcessPath `
            -Root $installRoot
        $startedAfter = $true
        $startedAtValue = ""
        if (-not [string]::IsNullOrWhiteSpace($InstalledAfterUtc)) {
            $startedAtValue = [string]$record.started_at
            if ([string]::IsNullOrWhiteSpace($startedAtValue)) {
                # 旧记录没有 started_at：无法证明是本次产生，按不合格处理。
                $startedAfter = $false
            }
            else {
                try {
                    $startedAfter = (
                        [DateTime]::Parse($startedAtValue).ToUniversalTime() -ge
                        [DateTime]::Parse($InstalledAfterUtc).ToUniversalTime()
                    )
                }
                catch {
                    $startedAfter = $false
                }
            }
        }
        $runtimeRecordFresh = [bool](
            $recordProcess -and $processInsideInstallRoot -and $startedAfter
        )
        $runtimeRecordEvidence = (
            "pid={0}; process={1}; inside_install_root={2}; started_at={3}; after_install={4}" -f `
                $recordPid, $recordProcessPath, $processInsideInstallRoot,
                $startedAtValue, $startedAfter
        )
    }
    catch {
        $runtimeRecordFresh = $false
        $runtimeRecordEvidence = "record unreadable: $($_.Exception.Message)"
    }
}
Add-Check `
    -Name "Runtime record belongs to this install" `
    -Passed $runtimeRecordFresh `
    -Evidence $runtimeRecordEvidence

$python = Get-Command python.exe -ErrorAction SilentlyContinue
$node = Get-Command node.exe -ErrorAction SilentlyContinue
$developerToolsAbsent = (-not $python) -and (-not $node)
$developerToolsCheckName = if ($RequireNoDeveloperTools) {
    "Python and Node unavailable to verification runtime"
}
else {
    "Developer tools inventory (not enforced)"
}
Add-Check `
    -Name $developerToolsCheckName `
    -Passed ((-not $RequireNoDeveloperTools) -or $developerToolsAbsent) `
    -Evidence ("python={0}; node={1}" -f $(if ($python) { $python.Source } else { "not on PATH" }), $(if ($node) { $node.Source } else { "not on PATH" }))

$installTreeSafe = Test-SafeDirectoryTree -LiteralPath $installRoot
$dataTreeSafe = Test-SafeDirectoryTree -LiteralPath $dataRoot
$runtimePointerMatches = $false
if (Test-SafeLeaf -LiteralPath $runtimePointer) {
    try {
        $pointerPayload = Get-Content -LiteralPath $runtimePointer -Raw | ConvertFrom-Json
        $runtimePointerMatches = [string]::Equals(
            [System.IO.Path]::GetFullPath([string]$pointerPayload.runtimeRoot),
            [System.IO.Path]::GetFullPath($dataRoot),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    }
    catch {
        $runtimePointerMatches = $false
    }
}
$installedExeSafe = Test-SafeLeaf -LiteralPath $installedExe
$desktopShortcutSafe = Test-SafeLeaf -LiteralPath $desktopShortcut
$startShortcutSafe = Test-SafeLeaf -LiteralPath $startShortcut
Add-Check -Name "Installed executable" -Passed $installedExeSafe -Evidence $installedExe
Add-Check -Name "Install directory has no links" -Passed $installTreeSafe -Evidence $installRoot
Add-Check -Name "Data directory has no links" -Passed $dataTreeSafe -Evidence $dataRoot
Add-Check -Name "Selected data location" -Passed $runtimePointerMatches -Evidence $dataRoot
Add-Check -Name "Desktop shortcut" -Passed $desktopShortcutSafe -Evidence $desktopShortcut
Add-Check -Name "Start menu shortcut" -Passed $startShortcutSafe -Evidence $startShortcut

if ([string]::IsNullOrWhiteSpace($ExpectedVersion)) {
    if ($registeredVersion -match '^[0-9]+\.[0-9]+\.[0-9]+$') {
        $ExpectedVersion = $registeredVersion
    }
}
$registeredLocationMatches = $false
try {
    $registeredLocationMatches = (
        [System.IO.Path]::GetFullPath($registeredLocation) -eq
        [System.IO.Path]::GetFullPath($installRoot)
    )
}
catch {
    $registeredLocationMatches = $false
}
Add-Check `
    -Name "Apps and Features registration" `
    -Passed ((Test-Path -LiteralPath $uninstallKey) -and $ExpectedVersion -and $registeredVersion -eq $ExpectedVersion -and $registeredLocationMatches -and -not [string]::IsNullOrWhiteSpace($registeredRuntimeLocation)) `
    -Evidence ("version={0}; location={1}; data={2}" -f $registeredVersion, $registeredLocation, $registeredRuntimeLocation)

$installedFileVersion = ""
$installedProductVersion = ""
if ($installedExeSafe) {
    $installedVersionInfo = (Get-Item -LiteralPath $installedExe).VersionInfo
    $installedFileVersion = [string]$installedVersionInfo.FileVersion
    $installedProductVersion = [string]$installedVersionInfo.ProductVersion
}
$expectedCanonicalVersion = ConvertTo-ThreePartVersion -Value $ExpectedVersion
$fileCanonicalVersion = ConvertTo-ThreePartVersion -Value $installedFileVersion
$productCanonicalVersion = ConvertTo-ThreePartVersion -Value $installedProductVersion
Add-Check `
    -Name "Installed executable version" `
    -Passed (
        $expectedCanonicalVersion -and
        $fileCanonicalVersion -eq $expectedCanonicalVersion -and
        $productCanonicalVersion -eq $expectedCanonicalVersion
    ) `
    -Evidence (
        "expected={0}; file={1}; product={2}" -f `
            $expectedCanonicalVersion, $installedFileVersion, $installedProductVersion
    )

$shortcutTarget = ""
if ($desktopShortcutSafe) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcutTarget = $shell.CreateShortcut($desktopShortcut).TargetPath
}
Add-Check -Name "Shortcut target" -Passed ($shortcutTarget -eq $installedExe) -Evidence $shortcutTarget

# 等待后端就绪（0.2.52）。
#
# 历史缺陷：这里只探一次，失败后固定睡 5 秒再探一次就结论"不健康"。干净电脑
# 首次启动要解压、建库、拉起 Playwright，几十秒是正常的；桌面端自己都愿意等
# 90 秒（main.cjs 的 waitForBackend）。验收窗口比客户端还短，就会在机器慢的
# 时候误报"后端未就绪"，紧接着 /login 探活也会 404——正是之前那两次升级失败
# 的形态。
#
# 现在改成有界轮询：最多等 90 秒，一旦就绪立刻返回。
#
# 但也不能无条件等满：如果根本没有任何 VideoInsight 进程在跑，说明程序压根没
# 起来（或已经被关掉），再等 90 秒只是让用户干瞪眼——独立运行验收脚本时尤其
# 明显。给 15 秒宽限期让安装器刚启动的进程出现，之后仍无进程就直接判失败。
$health = $null
$healthValid = $false
$healthDeadline = (Get-Date).AddSeconds(90)
$healthStartedAt = Get-Date
while ((Get-Date) -lt $healthDeadline) {
    $health = Get-LocalHealth
    if (
        $health -and
        $health.status -eq "ok" -and
        $health.service -eq "videoinsight-desktop-api" -and
        $health.desktop_protocol -eq "2"
    ) {
        $healthValid = $true
        break
    }
    if ((Get-Date) - $healthStartedAt -gt [TimeSpan]::FromSeconds(15)) {
        $anyProcess = @(
            Get-Process `
                -Name VideoInsight, VideoInsightBackend `
                -ErrorAction SilentlyContinue
        )
        if ($anyProcess.Count -eq 0) {
            break
        }
    }
    Start-Sleep -Milliseconds 750
}
Add-Check `
    -Name "Local service health" `
    -Passed ([bool]$healthValid) `
    -Evidence ("service={0}; protocol={1}" -f $health.service, $health.desktop_protocol)

# 服务健康后数据库才应当存在；首次启动时给 SQLite 初始化一个短暂、有限的窗口。
for ($attempt = 0; $attempt -lt 30 -and -not (Test-SafeLeaf -LiteralPath $database); $attempt++) {
    Start-Sleep -Milliseconds 250
}
$databaseSafe = Test-SafeLeaf -LiteralPath $database
Add-Check -Name "Local data database" -Passed $databaseSafe -Evidence $database

$loginPageStatus = Get-HttpStatus -Uri ("{0}/login" -f $script:ServiceOrigin)
Add-Check `
    -Name "Login page available" `
    -Passed ($loginPageStatus -eq 200) `
    -Evidence ("HTTP {0}" -f $loginPageStatus)

$invalidLoginStatus = Get-HttpStatus `
    -Uri ("{0}/api/v1/auth/customer-login" -f $script:ServiceOrigin) `
    -Method "Post" `
    -Body '{"code":"VI-INSTALL-CHECK-INVALID-DO-NOT-CREATE"}'
Add-Check `
    -Name "Company activation service reachable" `
    -Passed ($invalidLoginStatus -eq 401) `
    -Evidence ("expected invalid-code HTTP 401; actual HTTP {0}" -f $invalidLoginStatus)

$tasksStatus = Get-HttpStatus -Uri ("{0}/api/v1/tasks" -f $script:ServiceOrigin)
$crawlerStatus = Get-HttpStatus -Uri ("{0}/api/v1/crawler/capabilities" -f $script:ServiceOrigin)
$adminStatus = Get-HttpStatus -Uri ("{0}/api/v1/admin/status" -f $script:ServiceOrigin)
Add-Check -Name "Tasks require login" -Passed ($tasksStatus -eq 401) -Evidence ("HTTP {0}" -f $tasksStatus)
Add-Check -Name "Crawler requires login" -Passed ($crawlerStatus -eq 401) -Evidence ("HTTP {0}" -f $crawlerStatus)
Add-Check -Name "Admin requires login" -Passed ($adminStatus -eq 401) -Evidence ("HTTP {0}" -f $adminStatus)

$processes = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @("VideoInsight.exe", "VideoInsightBackend.exe")
})
$unexpectedProcess = $processes | Where-Object {
    -not (Test-PathInsideRoot -Candidate ([string]$_.ExecutablePath) -Root $installRoot)
}
$processEvidence = ($processes | ForEach-Object { $_.ExecutablePath } | Sort-Object -Unique) -join "; "
Add-Check `
    -Name "Processes run from install directory" `
    -Passed ($processes.Count -gt 0 -and -not $unexpectedProcess) `
    -Evidence $processEvidence

if (-not $ReportPath) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $ReportPath = Join-Path $dataRoot "data\logs\install-acceptance-$timestamp.txt"
}
$reportDirectory = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($ReportPath))
if (-not (Test-Path -LiteralPath $reportDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
}

$failed = @($results | Where-Object { $_.Result -eq "FAIL" })
$summary = if ($failed.Count -eq 0) { "PASS" } else { "FAIL" }
$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("VideoInsight clean-PC acceptance: $summary")
$lines.Add("Generated: $((Get-Date).ToString('s'))")
$lines.Add("Windows: $([Environment]::OSVersion.VersionString)")
$lines.Add("Paid/vendor actions executed: NO")
$lines.Add("")
foreach ($result in $results) {
    $lines.Add("[$($result.Result)] $($result.Check) - $($result.Evidence)")
}
[System.IO.File]::WriteAllLines($ReportPath, $lines, (New-Object System.Text.UTF8Encoding($true)))

$results | Format-Table -AutoSize
Write-Output "Report: $ReportPath"
exit $failed.Count
