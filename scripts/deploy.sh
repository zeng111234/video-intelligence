#!/bin/bash
# 短视频智能生产系统 - 一键部署脚本

set -e

echo "=========================================="
echo "  短视频智能生产系统 - 部署脚本"
echo "=========================================="

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查依赖
check_dependency() {
    if ! command -v $1 &> /dev/null; then
        echo -e "${RED}错误: 未找到 $1${NC}"
        return 1
    fi
    echo -e "${GREEN}✓ $1 已安装${NC}"
    return 0
}

echo ""
echo "步骤 1: 检查依赖..."
echo "----------------------------------------"

check_dependency "python3" || check_dependency "python"
check_dependency "node"
check_dependency "npm"
check_dependency "ffmpeg"

# 检查 Python 版本
PYTHON_VERSION=$(python3 --version 2>&1 || python --version 2>&1)
echo "Python 版本: $PYTHON_VERSION"

# 检查 Node 版本
NODE_VERSION=$(node --version)
echo "Node 版本: $NODE_VERSION"

echo ""
echo "步骤 2: 安装 Python 依赖..."
echo "----------------------------------------"

# 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv || python -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate 2>/dev/null || source venv/Scripts/activate 2>/dev/null

# 安装依赖
pip install -r requirements.txt
pip install -r project/backend/requirements.txt

echo -e "${GREEN}✓ Python 依赖安装完成${NC}"

echo ""
echo "步骤 3: 安装前端依赖..."
echo "----------------------------------------"

cd project/frontend
npm install
cd ../..

echo -e "${GREEN}✓ 前端依赖安装完成${NC}"

echo ""
echo "步骤 4: 构建前端..."
echo "----------------------------------------"

cd project/frontend
npm run build
cd ../..

echo -e "${GREEN}✓ 前端构建完成${NC}"

echo ""
echo "步骤 5: 初始化数据库..."
echo "----------------------------------------"

# 创建数据目录
mkdir -p data

# 运行数据库迁移
python3 -m database.migrations.runner || python -m database.migrations.runner

echo -e "${GREEN}✓ 数据库初始化完成${NC}"

echo ""
echo "步骤 6: 配置环境变量..."
echo "----------------------------------------"

# 创建 .env 文件（如果不存在）
if [ ! -f ".env" ]; then
    cat > .env << EOF
# 应用配置
APP_ENV=production
APP_DEBUG=false

# ASR 配置
ASR_MODE=local

# 数字人配置
AVATAR_MODE=mock
# DUIX_TTS_URL=http://127.0.0.1:18180
# DUIX_VIDEO_URL=http://127.0.0.1:8383

# 数据库
DATABASE_PATH=data/video_intelligence.db
EOF
    echo -e "${GREEN}✓ 环境配置文件已创建${NC}"
else
    echo -e "${YELLOW}⚠ .env 文件已存在，跳过${NC}"
fi

echo ""
echo "步骤 7: 创建启动脚本..."
echo "----------------------------------------"

# 创建启动脚本
cat > start.sh << 'EOF'
#!/bin/bash
# 启动所有服务

echo "启动后端服务..."
python -m uvicorn project.backend.app.main:app --host 0.0.0.0 --port 2001 &
BACKEND_PID=$!

echo "启动前端开发服务器..."
cd project/frontend
npm run dev &
FRONTEND_PID=$!
cd ../..

echo ""
echo "=========================================="
echo "  服务已启动"
echo "=========================================="
echo ""
echo "  前端: http://localhost:1001"
echo "  后端: http://localhost:2001"
echo "  API文档: http://localhost:2001/docs"
echo ""
echo "按 Ctrl+C 停止所有服务"
echo "=========================================="

# 等待中断信号
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
EOF

chmod +x start.sh

echo -e "${GREEN}✓ 启动脚本已创建${NC}"

echo ""
echo "=========================================="
echo -e "${GREEN}  部署完成！${NC}"
echo "=========================================="
echo ""
echo "启动方式："
echo "  ./start.sh"
echo ""
echo "或手动启动："
echo "  1. 后端: python -m uvicorn project.backend.app.main:app --host 0.0.0.0 --port 2001"
echo "  2. 前端: cd project/frontend && npm run dev"
echo ""
echo "访问地址："
echo "  - 前端: http://localhost:1001"
echo "  - 后端: http://localhost:2001"
echo "  - API文档: http://localhost:2001/docs"
echo ""
