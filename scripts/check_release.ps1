[CmdletBinding()]
param(
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repositoryRoot

function Invoke-ReleaseStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Action
    )

    Write-Host ""
    Write-Host "[CHECK] $Name"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE."
    }
}

function Test-PythonModuleAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonPath,
        [Parameter(Mandatory = $true)]
        [string]$ModuleName
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $PythonPath -m $ModuleName --version *> $null
    $moduleExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    return $moduleExitCode -eq 0
}

function Test-PythonReleaseEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonPath
    )

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $false
    }
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $PythonPath -c "import fastapi, pandas, pytest, pytest_benchmark, streamlit" *> $null
    $importExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    return $importExitCode -eq 0
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required for the release check."
}

$dirtyEntries = @(& git status --porcelain)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read Git working tree status."
}
if ($dirtyEntries.Count -gt 0 -and -not $AllowDirty) {
    Write-Host "[BLOCKED] The working tree has $($dirtyEntries.Count) uncommitted entries."
    Write-Host "Review and commit the intended release, then run this check again."
    Write-Host "Use -AllowDirty only while developing; it is not valid release evidence."
    exit 1
}
if ($dirtyEntries.Count -gt 0) {
    Write-Warning "Running against a dirty working tree. This result is for development only."
}

$trackedFiles = @(& git -c core.quotepath=false ls-files)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to list tracked files."
}
$forbiddenTrackedFiles = @(
    $trackedFiles | Where-Object {
        $trackedFileName = [System.IO.Path]::GetFileName([string]$_)
        $trackedFileName.Equals(".env", [System.StringComparison]::OrdinalIgnoreCase) -or
        (
            $trackedFileName.StartsWith(".env.", [System.StringComparison]::OrdinalIgnoreCase) -and
            -not $trackedFileName.Equals(".env.example", [System.StringComparison]::OrdinalIgnoreCase)
        ) -or
        (
            $_ -ne "data/templates/builtin.json" -and
            $_ -match "(^|/)(data|outputs|uploads|models)/"
        ) -or
        $_ -match "\.(db|sqlite|sqlite3|mp4|mov|mp3|wav)$" -or
        $_ -match "^forge_rss_analysis/"
    }
)
if ($forbiddenTrackedFiles.Count -gt 0) {
    Write-Host "[BLOCKED] Local secrets, runtime data, media, or third-party analysis files are tracked:"
    $forbiddenTrackedFiles | ForEach-Object { Write-Host "  $_" }
    exit 1
}

$systemPython = Get-Command python -ErrorAction SilentlyContinue
$pythonCandidates = @(
    (Join-Path $repositoryRoot "venv\Scripts\python.exe"),
    (Join-Path $repositoryRoot ".venv\Scripts\python.exe")
)
if ($systemPython) {
    $pythonCandidates += $systemPython.Source
}
$pythonCandidates = @($pythonCandidates | Select-Object -Unique)

$testPythonPath = $pythonCandidates |
    Where-Object { Test-PythonReleaseEnvironment -PythonPath $_ } |
    Select-Object -First 1
if (-not $testPythonPath) {
    throw "No complete release-test Python environment was found. Install requirements-dev.txt into a dedicated development virtual environment."
}

$lintPythonPath = $pythonCandidates |
    Where-Object { Test-PythonModuleAvailable -PythonPath $_ -ModuleName "ruff" } |
    Select-Object -First 1
if (-not $lintPythonPath) {
    throw "Ruff was not found. Install requirements-dev.txt before the release check."
}

$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
$npxCommand = Get-Command npx.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand -or -not $npxCommand) {
    throw "Node.js tools were not found. Run start.bat once to install project dependencies."
}

$frontendRoot = Join-Path $repositoryRoot "project\frontend"
if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot "node_modules") -PathType Container)) {
    throw "Frontend dependencies are missing. Run start.bat once before the release check."
}

Invoke-ReleaseStep "Git whitespace check" { & git diff --check }
Invoke-ReleaseStep "Configured supplier secret scan" {
    & $testPythonPath `
        (Join-Path $repositoryRoot "scripts\scan_release_secrets.py") `
        --repository-root $repositoryRoot
}
Invoke-ReleaseStep "Python lint" { & $lintPythonPath -m ruff check . }
Invoke-ReleaseStep "Python release test suite" {
    & (Join-Path $repositoryRoot "scripts\run_release_python_tests.ps1") `
        -PythonPath $testPythonPath
}

Push-Location -LiteralPath $frontendRoot
try {
    Invoke-ReleaseStep "Frontend test suite" { & npx vitest run --retry=2 }
    Invoke-ReleaseStep "Desktop update test suite" { & $npmCommand.Source run test:update }
    Invoke-ReleaseStep "TypeScript check" { & $npxCommand.Source tsc --noEmit }
    Invoke-ReleaseStep "Production frontend build" { & $npmCommand.Source run build }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "[PASS] Automated release checks passed."
if ($AllowDirty) {
    Write-Warning "Commit the reviewed release scope and rerun without -AllowDirty before delivery."
}
