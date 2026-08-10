param(
    [ValidatePattern('^$|^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$ExpectedVersion = "",
    [string]$ReportPath = "",
    [switch]$RequireNoDeveloperTools
)

$ErrorActionPreference = "Stop"
$results = New-Object System.Collections.Generic.List[object]

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
        $target.Port -ne 1001 -or
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
        $requestStream = $request.GetRequestStream()
        try {
            $requestStream.Write($payload, 0, $payload.Length)
        }
        finally {
            $requestStream.Dispose()
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
    $response = Invoke-LocalHttp -Uri "http://127.0.0.1:1001/health"
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
$installRoot = Join-Path $localAppData "Programs\VideoInsight"
$installedExe = Join-Path $installRoot "VideoInsight.exe"
$dataRoot = Join-Path $localAppData "VideoInsight"
$database = Join-Path $dataRoot "data\video_intelligence.db"
$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "VideoInsight.lnk"
$startShortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "VideoInsight\VideoInsight.lnk"
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VideoInsight"

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
$installedExeSafe = Test-SafeLeaf -LiteralPath $installedExe
$desktopShortcutSafe = Test-SafeLeaf -LiteralPath $desktopShortcut
$startShortcutSafe = Test-SafeLeaf -LiteralPath $startShortcut
$databaseSafe = Test-SafeLeaf -LiteralPath $database
Add-Check -Name "Installed executable" -Passed $installedExeSafe -Evidence $installedExe
Add-Check -Name "Install directory has no links" -Passed $installTreeSafe -Evidence $installRoot
Add-Check -Name "Desktop shortcut" -Passed $desktopShortcutSafe -Evidence $desktopShortcut
Add-Check -Name "Start menu shortcut" -Passed $startShortcutSafe -Evidence $startShortcut
Add-Check -Name "Local data database" -Passed $databaseSafe -Evidence $database

$registeredVersion = ""
$registeredLocation = ""
if (Test-Path -LiteralPath $uninstallKey) {
    $uninstall = Get-ItemProperty -LiteralPath $uninstallKey
    $registeredVersion = [string]$uninstall.DisplayVersion
    $registeredLocation = [string]$uninstall.InstallLocation
}
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
    -Passed ((Test-Path -LiteralPath $uninstallKey) -and $ExpectedVersion -and $registeredVersion -eq $ExpectedVersion -and $registeredLocationMatches) `
    -Evidence ("version={0}; location={1}" -f $registeredVersion, $registeredLocation)

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

$health = Get-LocalHealth
$healthValid = $health -and `
    $health.status -eq "ok" -and `
    $health.service -eq "videoinsight-desktop-api" -and `
    $health.desktop_protocol -eq "2"
if (-not $healthValid) {
    # 新电脑首次启动可能较慢，只自动重试一次。
    Start-Sleep -Seconds 5
    $health = Get-LocalHealth
    $healthValid = $health -and `
        $health.status -eq "ok" -and `
        $health.service -eq "videoinsight-desktop-api" -and `
        $health.desktop_protocol -eq "2"
}
Add-Check `
    -Name "Local service health" `
    -Passed ([bool]$healthValid) `
    -Evidence ("service={0}; protocol={1}" -f $health.service, $health.desktop_protocol)

$loginPageStatus = Get-HttpStatus -Uri "http://127.0.0.1:1001/login"
Add-Check `
    -Name "Login page available" `
    -Passed ($loginPageStatus -eq 200) `
    -Evidence ("HTTP {0}" -f $loginPageStatus)

$invalidLoginStatus = Get-HttpStatus `
    -Uri "http://127.0.0.1:1001/api/v1/auth/customer-login" `
    -Method "Post" `
    -Body '{"code":"VI-INSTALL-CHECK-INVALID-DO-NOT-CREATE"}'
Add-Check `
    -Name "Company activation service reachable" `
    -Passed ($invalidLoginStatus -eq 401) `
    -Evidence ("expected invalid-code HTTP 401; actual HTTP {0}" -f $invalidLoginStatus)

$tasksStatus = Get-HttpStatus -Uri "http://127.0.0.1:1001/api/v1/tasks"
$crawlerStatus = Get-HttpStatus -Uri "http://127.0.0.1:1001/api/v1/crawler/capabilities"
$adminStatus = Get-HttpStatus -Uri "http://127.0.0.1:1001/api/v1/admin/status"
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
    $ReportPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "VideoInsight-clean-pc-acceptance-$timestamp.txt"
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
