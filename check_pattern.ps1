# === UTF-8 编码保障 ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location 'C:\Users\zeng\Desktop\video\project\frontend'

# Check for any ))} patterns that might cause Babel parser issues
$content = Get-Content 'src\pages\PipelinePage.tsx' -Encoding UTF8
for($i = 0; $i -lt $content.Length; $i++) {
    $line = $content[$i]
    if ($line -match '\)\)\}' -and $line -notmatch '^\s*//') {
        Write-Host ("{0}: {1}" -f ($i+1), $line.TrimEnd())
    }
}
