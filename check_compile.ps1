# === UTF-8 编码保障 ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$env:HTTP_PROXY = "http://127.0.0.1:10808"
$env:HTTPS_PROXY = "http://127.0.0.1:10808"

Set-Location 'C:\Users\zeng\Desktop\video\project\frontend'

# Use npx to run tsc check on the file
npx tsc --noEmit --skipLibCheck 2>&1 | Select-Object -First 50
