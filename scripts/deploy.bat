@echo off
REM 短视频智能生产系统 - Windows 部署脚本

echo ==========================================
echo   短视频智能生产系统 - 部署脚本
echo ==========================================
echo.

REM 检查 Python
echo 步骤 1: 检查依赖...
echo ----------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到 Python
    pause
    exit /b 1
)
echo ✓ Python 已安装

node --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到 Node.js
    pause
    exit /b 1
)
echo ✓ Node.js 已安装

ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo 警告: 未找到 FFmpeg（语音转写功能需要）
)
echo ✓ FFmpeg 已安装

echo.
echo 步骤 2: 安装 Python 依赖...
echo ----------------------------------------

REM 创建虚拟环境
if not exist "venv" (
    echo 创建虚拟环境...
    python -m venv venv
)

REM 激活虚拟环境
call venv\Scripts\activate.bat

REM 安装依赖
pip install -r requirements.txt
pip install -r project\backend\requirements.txt

echo ✓ Python 依赖安装完成

echo.
echo 步骤 3: 安装前端依赖...
echo ----------------------------------------

cd project\frontend
call npm install
cd ..\..

echo ✓ 前端依赖安装完成

echo.
echo 步骤 4: 构建前端...
echo ----------------------------------------

cd project\frontend
call npm run build
cd ..\..

echo ✓ 前端构建完成

echo.
echo 步骤 5: 初始化数据库...
echo ----------------------------------------

if not exist "data" mkdir data
python -m database.migrations.runner

echo ✓ 数据库初始化完成

echo.
echo 步骤 6: 创建启动脚本...
echo ----------------------------------------

REM 创建启动脚本
(
echo @echo off
echo REM 启动所有服务
echo.
echo echo 启动后端服务...
echo start "Backend" python -m uvicorn project.backend.app.main:app --host 127.0.0.1 --port 2001 --no-proxy-headers
echo.
echo echo 启动前端开发服务器...
echo cd project\frontend
echo start "Frontend" npm run dev
echo cd ..\..
echo.
echo echo.
echo echo ==========================================
echo echo   服务已启动
echo echo ==========================================
echo echo.
echo echo   前端: http://localhost:1001
echo echo   后端: http://localhost:2001
echo echo   API文档: http://localhost:2001/docs
echo echo.
echo echo 按任意键停止所有服务...
echo pause ^>nul
echo.
echo echo 停止服务...
echo taskkill /FI "WindowTitle eq Backend*" /F ^>nul 2^>^&1
echo taskkill /FI "WindowTitle eq Frontend*" /F ^>nul 2^>^&1
echo echo 服务已停止
) > start.bat

echo ✓ 启动脚本已创建

echo.
echo ==========================================
echo   部署完成！
echo ==========================================
echo.
echo 启动方式：
echo   start.bat
echo.
echo 或手动启动：
echo   1. 后端: python -m uvicorn project.backend.app.main:app --host 127.0.0.1 --port 2001 --no-proxy-headers
echo   2. 前端: cd project\frontend ^&^& npm run dev
echo.
echo 访问地址：
echo   - 前端: http://localhost:1001
echo   - 后端: http://localhost:2001
echo   - API文档: http://localhost:2001/docs
echo.
pause
