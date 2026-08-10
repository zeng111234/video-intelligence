param(
    [string]$PythonPath = "python"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Invoke-Pytest {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & $PythonPath -m pytest @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python test failed: $($Arguments -join ' ')"
    }
}

Push-Location $repositoryRoot
try {
    Write-Output "[1/3] FastAPI and control-plane tests"
    Invoke-Pytest -Arguments @("project/backend/tests", "-q")

    Write-Output "[2/3] Service and adapter tests (isolated per file)"
    $rootTests = Get-ChildItem -LiteralPath (Join-Path $repositoryRoot "tests") `
        -Filter "test_*.py" -File | Sort-Object Name
    foreach ($testFile in $rootTests) {
        if ($testFile.Name -eq "test_streamlit_pages.py") {
            continue
        }
        Invoke-Pytest -Arguments @($testFile.FullName, "-q")
    }

    Write-Output "[3/3] Legacy Streamlit page tests (isolated groups)"
    $streamlitFile = Join-Path $repositoryRoot "tests\test_streamlit_pages.py"
    $firstGroup = @(
        "business_ui",
        "top_navigation",
        "pages_render",
        "candidate_page_shows",
        "three_platform_search",
        "sandbox_search"
    ) -join " or "
    Invoke-Pytest -Arguments @($streamlitFile, "-q", "-k", $firstGroup)
    Invoke-Pytest -Arguments @($streamlitFile, "-q", "-k", "not ($firstGroup)")
}
finally {
    Pop-Location
}

Write-Output "All Python release tests passed."
