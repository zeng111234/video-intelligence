param(
    [string]$SourceImage = $env:LOCAL_AVATAR_AVATAR_PATH,
    [string]$DrivenAudio = $env:LOCAL_AVATAR_AUDIO_PATH,
    [string]$OutputPath = $env:LOCAL_AVATAR_OUTPUT_PATH,
    [string]$RepoDir = $env:SADTALKER_REPO_DIR,
    [string]$Python = $env:SADTALKER_PYTHON,
    [string]$CheckpointDir = $env:SADTALKER_CHECKPOINT_DIR
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoDir)) {
    $RepoDir = Join-Path $PSScriptRoot "..\SadTalker"
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = Join-Path $PSScriptRoot "..\local_models\sadtalker\.venv\Scripts\python.exe"
}
if ([string]::IsNullOrWhiteSpace($CheckpointDir)) {
    $CheckpointDir = Join-Path $RepoDir "checkpoints"
}

$repo = (Resolve-Path -LiteralPath $RepoDir).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$checkpointPath = (Resolve-Path -LiteralPath $CheckpointDir).Path
$source = (Resolve-Path -LiteralPath $SourceImage).Path
$audio = (Resolve-Path -LiteralPath $DrivenAudio).Path
$output = [System.IO.Path]::GetFullPath($OutputPath)
$outputParent = [System.IO.Path]::GetDirectoryName($output)
if (-not [System.IO.Directory]::Exists($outputParent)) {
    [System.IO.Directory]::CreateDirectory($outputParent) | Out-Null
}

$required = @(
    (Join-Path $checkpointPath "SadTalker_V0.0.2_256.safetensors"),
    (Join-Path $checkpointPath "mapping_00229-model.pth.tar")
)
foreach ($file in $required) {
    if (-not [System.IO.File]::Exists($file)) {
        throw "Missing SadTalker model file: $file"
    }
}

$resultRoot = Join-Path $outputParent "sadtalker_results"
if (-not [System.IO.Directory]::Exists($resultRoot)) {
    [System.IO.Directory]::CreateDirectory($resultRoot) | Out-Null
}

Push-Location $repo
try {
    & $pythonPath "inference.py" `
        --driven_audio $audio `
        --source_image $source `
        --checkpoint_dir $checkpointPath `
        --result_dir $resultRoot `
        --size 256 `
        --preprocess crop `
        --still
    if ($LASTEXITCODE -ne 0) {
        throw "SadTalker inference failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

$latest = Get-ChildItem -LiteralPath $resultRoot -Filter "*.mp4" -File |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($null -eq $latest) {
    throw "SadTalker did not create an mp4 file."
}

Copy-Item -LiteralPath $latest.FullName -Destination $output -Force
if (-not [System.IO.File]::Exists($output) -or (Get-Item -LiteralPath $output).Length -lt 12) {
    throw "SadTalker output mp4 is invalid."
}
