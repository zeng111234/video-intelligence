# === UTF-8 编码保障 ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$lines = Get-Content 'C:\Users\zeng\Desktop\video\project\frontend\src\pages\PipelinePage.tsx' -Encoding UTF8
for($i=2240; $i -le 2255; $i++) {
    Write-Host ('{0}: {1}' -f $i, $lines[$i-1])
}
