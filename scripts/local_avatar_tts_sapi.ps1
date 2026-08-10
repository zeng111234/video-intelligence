param(
    [string]$Text = $env:LOCAL_AVATAR_SCRIPT_TEXT,
    [string]$OutputPath = $env:LOCAL_AVATAR_AUDIO_OUTPUT_PATH,
    [string]$SpeechRate = $env:LOCAL_AVATAR_SPEECH_RATE
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Text)) {
    throw "LOCAL_AVATAR_SCRIPT_TEXT is empty."
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    throw "LOCAL_AVATAR_AUDIO_OUTPUT_PATH is empty."
}

$output = [System.IO.Path]::GetFullPath($OutputPath)
$parent = [System.IO.Path]::GetDirectoryName($output)
if (-not [System.IO.Directory]::Exists($parent)) {
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null
}

Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer

$rateValue = 1.0
if (-not [string]::IsNullOrWhiteSpace($SpeechRate)) {
    [double]::TryParse($SpeechRate, [ref]$rateValue) | Out-Null
}
$speaker.Rate = [Math]::Max(-4, [Math]::Min(4, [int](($rateValue - 1.0) * 10)))
$speaker.SetOutputToWaveFile($output)
$speaker.Speak($Text)
$speaker.Dispose()

if (-not [System.IO.File]::Exists($output) -or (Get-Item -LiteralPath $output).Length -eq 0) {
    throw "TTS did not create a valid wav file."
}
