param(
    [string]$ExpectedVersion = "0.2.0",
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

function Get-HttpStatus {
    param(
        [string]$Uri,
        [hashtable]$Headers = @{},
        [string]$Method = "Get",
        [string]$Body = "",
        [string]$ContentType = "application/json"
    )
    try {
        $parameters = @{
            Uri = $Uri
            Headers = $Headers
            Method = $Method
            UseBasicParsing = $true
            TimeoutSec = 10
        }
        if ($Body) {
            $parameters["Body"] = $Body
            $parameters["ContentType"] = $ContentType
        }
        $response = Invoke-WebRequest @parameters
        return [int]$response.StatusCode
    }
    catch {
        if ($_.Exception.Response) {
            return [int]$_.Exception.Response.StatusCode
        }
        return 0
    }
}

function Get-LocalHealth {
    try {
        return Invoke-RestMethod `
            -Uri "http://127.0.0.1:1001/health" `
            -Method Get `
            -TimeoutSec 10 `
            -UseBasicParsing
    }
    catch {
        return $null
    }
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

Add-Check -Name "Installed executable" -Passed (Test-Path -LiteralPath $installedExe) -Evidence $installedExe
Add-Check -Name "Desktop shortcut" -Passed (Test-Path -LiteralPath $desktopShortcut) -Evidence $desktopShortcut
Add-Check -Name "Start menu shortcut" -Passed (Test-Path -LiteralPath $startShortcut) -Evidence $startShortcut
Add-Check -Name "Local data database" -Passed (Test-Path -LiteralPath $database) -Evidence $database

$registeredVersion = ""
$registeredLocation = ""
if (Test-Path -LiteralPath $uninstallKey) {
    $uninstall = Get-ItemProperty -LiteralPath $uninstallKey
    $registeredVersion = [string]$uninstall.DisplayVersion
    $registeredLocation = [string]$uninstall.InstallLocation
}
Add-Check `
    -Name "Apps and Features registration" `
    -Passed ((Test-Path -LiteralPath $uninstallKey) -and $registeredVersion -eq $ExpectedVersion -and $registeredLocation -eq $installRoot) `
    -Evidence ("version={0}; location={1}" -f $registeredVersion, $registeredLocation)

$shortcutTarget = ""
if (Test-Path -LiteralPath $desktopShortcut) {
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

$processes = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @("VideoInsight.exe", "VideoInsightBackend.exe")
}
$unexpectedProcess = $processes | Where-Object {
    -not $_.ExecutablePath.StartsWith($installRoot, [System.StringComparison]::OrdinalIgnoreCase)
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
