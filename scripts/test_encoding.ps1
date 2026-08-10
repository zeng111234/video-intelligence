# === UTF-8 编码验证脚本 ===
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=== PowerShell 编码验证 ===" -ForegroundColor Cyan
Write-Host ""

# 检查当前代码页
$currentCodePage = [Console]::OutputEncoding.CodePage
Write-Host "当前代码页: $currentCodePage" -ForegroundColor Green

# 检查 OutputEncoding
$encodingName = [Console]::OutputEncoding.EncodingName
Write-Host "OutputEncoding: $encodingName" -ForegroundColor Green

# 测试中文输出
Write-Host ""
Write-Host "=== 中文输出测试 ===" -ForegroundColor Cyan
Write-Host "测试中文: 你好世界！" -ForegroundColor Green
Write-Host "特殊字符: ① ② ③ ④ ⑤" -ForegroundColor Green
Write-Host "中文标点：，。！？；：""''【】（）" -ForegroundColor Green

# 测试文件写入（可选）
$testFile = Join-Path $env:TEMP "crow5_encoding_test.txt"
"测试中文内容：你好世界！" | Out-File -FilePath $testFile -Encoding UTF8
Write-Host ""
Write-Host "=== 文件写入测试 ===" -ForegroundColor Cyan
Write-Host "测试文件已写入: $testFile" -ForegroundColor Green
Write-Host "请用记事本打开此文件，确认中文显示正常。" -ForegroundColor Yellow

# 检查是否安装了 PowerShell 7
$ps7Path = Get-Command pwsh.exe -ErrorAction SilentlyContinue
if ($ps7Path) {
    Write-Host ""
    Write-Host "=== PowerShell 7 检测 ===" -ForegroundColor Cyan
    Write-Host "检测到 PowerShell 7: $($ps7Path.Source)" -ForegroundColor Green
    Write-Host "建议使用 pwsh.exe 运行脚本，默认 UTF-8 编码。" -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "=== PowerShell 7 检测 ===" -ForegroundColor Cyan
    Write-Host "未检测到 PowerShell 7。" -ForegroundColor Yellow
    Write-Host "建议安装 PowerShell 7 以获得更好的 UTF-8 支持。" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== 验证完成 ===" -ForegroundColor Cyan
Write-Host "如果所有中文显示正常，说明编码设置成功。" -ForegroundColor Green
Write-Host "如果仍有乱码，请检查系统区域设置或终端字体。" -ForegroundColor Yellow
