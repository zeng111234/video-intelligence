# 短视频热点洞察与智能生产系统

面向品牌营销团队的短视频热点发现、候选管理、批量内容生产与多平台发布系统。

## 项目简介

本系统是一个完整的短视频内容生产平台，包含以下核心功能：
- **热点发现**：三平台（抖音、小红书、视频号）商业 API 统一网关、热度计算与候选管理
- **内容生产**：视频音轨转文案、文案改写引擎、数字人生成、AI 视频剪辑（FFmpeg 流水线）
- **一键发布**：多平台发布适配器（支持沙箱与生产模式）
- **系统韧性**：统一重试策略、上下文资源监控、任务状态管理

## 技术栈

### 后端技术
- **Python 3.12+**：主要编程语言
- **FastAPI**：REST API 服务框架
- **SQLite**：本地数据库（开发环境）
- **PostgreSQL**：生产数据库（可选）
- **FFmpeg**：视频处理工具
- **faster-whisper**：本地语音识别引擎

### 前端技术
- **React 18**：前端框架
- **TypeScript**：类型安全的 JavaScript
- **Vite 5**：构建工具
- **Ant Design 5**：UI 组件库
- **React Router 6**：路由管理

## 快速开始

### 环境要求

- **Python 3.12+**（缺少时由 `start.bat` 自动安装，并创建项目专用 `.venv`）
- **Node.js 18+**（缺少时由 `start.bat` 自动安装；Node.js 22.11 也可直接使用）
- **Chrome 或 Edge**（热点发现浏览器功能需要）
- **FFmpeg**（仅本地视频处理功能需要；不影响系统启动）

项目不依赖固定用户名、盘符或安装目录。首次安装会把 Python 包放进仓库内的 `.venv`，前端依赖严格按 `package-lock.json` 安装，不会污染朋友电脑上的其他 Python 项目。关键词爬虫的“热点宝”发现功能使用本机 Chrome/Edge，不会额外下载 Playwright 自带浏览器。

### 一键启动（推荐）

#### Windows 用户
```bash
# 直接双击 start.bat
# 首次运行会自动安装项目依赖，以后仍使用这一个入口。
```

`start.bat` 会先检测 Python、Node.js 和项目依赖，缺少时通过 Windows 应用安装程序自动安装，然后启动前后端并打开页面。如果电脑连 Windows 应用安装程序都没有，才会停止并给出唯一的安装地址。自动安装失败后不会循环重试。`.env` 不随项目分发：首次启动只会从 `.env.example` 创建一份无密钥的本机配置，需要云服务时再填写朋友自己的账号配置。

#### PowerShell 用户
```powershell
# 执行启动脚本
.\scripts\start_all_services.ps1

# 跳过浏览器自动打开
.\scripts\start_all_services.ps1 -SkipBrowser

# 跳过健康检查（快速启动）
.\scripts\start_all_services.ps1 -SkipHealthCheck
```

### 手动启动

#### 1. 启动 FastAPI 后端
```powershell
# 在仓库根目录设置 PYTHONPATH（后端需要访问 src/ 目录）
$env:PYTHONPATH = (Get-Location).Path

# 进入后端目录
cd project/backend

# 安装依赖
python -m pip install -r requirements.txt

# 启动服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 2001
```

#### 2. 启动 React 前端
```powershell
# 进入前端目录
cd project/frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

## 服务端口配置

| 服务 | 端口 | 说明 | 访问地址 |
|------|------|------|----------|
| **React 前端** | 1001 | 正式前端界面 | http://localhost:1001 |
| **FastAPI 后端** | 2001 | 正式 REST API 服务 | http://localhost:2001 |

### 端口冲突处理

如果端口被占用，启动脚本会自动尝试停止占用端口的进程。如需手动处理：

```powershell
# 查看端口占用情况
netstat -ano | findstr "2001 1001"

# 停止占用端口的进程（替换 <PID> 为实际进程ID）
taskkill /PID <PID> /F
```

## 数据库配置

### 默认配置（SQLite）
- 数据库文件：`data/video_intelligence.db`
- 无需额外配置，开箱即用

### 生产环境配置（PostgreSQL）

如需切换到 PostgreSQL，创建 `.env` 文件：

```env
# PostgreSQL 配置
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=video
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password_here

# 应用配置
APP_ENV=production
APP_DEBUG=false
```

### 数据库初始化

```powershell
# PostgreSQL 初始化脚本
psql -U postgres -d video -f database/scripts/init.postgres.sql
```

## 项目结构

```
video/
├── app.py                    # legacy Streamlit 源码，仅供追溯，不再作为正式入口
├── app_pages/                # legacy Streamlit 页面模块
├── src/                      # 核心业务逻辑
│   ├── models.py             # 数据模型定义（VideoCandidate、TaskRecord 等）
│   ├── contracts.py          # 接口协议定义（CrawlerAdapter、Repository 等）
│   ├── services/             # 业务服务层（14 个服务模块）
│   │   ├── candidate.py      # 候选管理
│   │   ├── commercial_search.py # 商业搜索
│   │   ├── heat.py           # 热度计算
│   │   ├── transcription.py  # 转写服务
│   │   ├── avatar.py         # 数字人服务
│   │   ├── pipeline.py       # 流水线服务
│   │   ├── publisher.py      # 发布服务
│   │   ├── copywriting.py    # 文案服务
│   │   ├── video_editor.py   # 视频编辑服务
│   │   ├── video_source.py   # 视频源管理
│   │   ├── discovery.py      # 关键词发现
│   │   ├── keyword_trend.py  # 关键词趋势
│   │   └── source.py         # 数据源管理
│   ├── repositories/         # 数据访问层
│   │   ├── sqlite.py         # SQLite 仓储实现
│   │   └── mock.py           # Mock 仓储（测试用）
│   ├── adapters/             # 外部服务适配器
│   │   ├── asr_bridge.py     # ASR 桥接层（统一本地/云端 ASR）
│   │   ├── aliyun_asr.py     # 阿里云 ASR 适配器
│   │   ├── avatar.py         # 数字人适配器
│   │   └── ...               # 平台适配器（抖音、小红书、视频号等）
│   ├── resources.py          # 资源管理（FFmpeg、ASR）
│   ├── asr_quality.py        # ASR 质量评估（CER、数字准确率）
│   ├── app_state.py          # 应用状态管理
│   └── retry_policy.py       # 重试策略
├── project/
│   ├── frontend/             # React 前端工程（端口 1001）
│   │   ├── src/
│   │   │   ├── App.tsx       # 根组件（Router + ErrorBoundary）
│   │   │   ├── pages/        # 页面组件
│   │   │   │   ├── CandidatesPage.tsx    # 候选检索
│   │   │   │   ├── TranscriptionPage.tsx # 转写页面
│   │   │   │   ├── PipelinePage.tsx      # 流水线
│   │   │   │   ├── TasksPage.tsx         # 任务记录
│   │   │   │   ├── AdminPage.tsx         # 系统管理
│   │   │   │   └── NotFoundPage.tsx      # 404 页面
│   │   │   ├── components/   # 通用组件
│   │   │   └── api/          # API 客户端与类型定义
│   │   ├── public/           # 静态资源
│   │   └── package.json      # 前端依赖配置
│   └── backend/              # FastAPI 后端工程（端口 2001）
│       ├── app/
│       │   ├── main.py       # FastAPI 应用入口
│       │   ├── api/v1/       # API 路由（9 个模块）
│       │   │   ├── candidates.py     # 候选搜索
│       │   │   ├── transcriptions.py # 转写任务
│       │   │   ├── pipelines.py      # 流水线
│       │   │   ├── tasks.py          # 任务查询
│       │   │   ├── admin.py          # 系统管理
│       │   │   ├── copywriting.py    # 文案改写
│       │   │   ├── video_editor.py   # 视频编辑
│       │   │   └── publish.py        # 多平台发布
│       │   ├── core/         # 核心配置（config.py、deps.py、security.py）
│       │   └── schemas/      # Pydantic 模式
│       ├── requirements.txt  # 后端依赖
│       └── tests/            # 后端 API 与服务测试
├── database/                 # 数据库脚本
│   ├── scripts/              # SQL 初始化脚本
│   └── migrations/           # 数据库迁移框架
│       ├── runner.py         # MigrationRunner 核心类
│       ├── 001_initial_schema.py # 初始 Schema（15 张表 + 6 索引）
│       ├── 002_add_column_migrations.py # 列扩展迁移
│       ├── 003_add_media_resolution_tables.py # 媒体解析迁移
│       ├── 004_quarantine_orphan_sampling_checkpoints.py # 历史检查点修复
│       └── __main__.py       # CLI 入口
├── data/                     # 数据文件目录
│   └── video_intelligence.db # SQLite 数据库
├── scripts/                  # 脚本工具
│   ├── start_all_services.ps1 # 全服务启动脚本
│   ├── launch_app.ps1        # 单应用启动脚本
│   └── benchmark_asr.py      # ASR 基准测试
├── tests/                    # 共享领域与历史 Streamlit 测试代码
├── doc/                      # 项目文档（详细设计、交付报告等）
├── docs/                     # 项目文档（归档、指南等）
├── prototype/                # 产品原型
│   ├── decision-guide.html   # 任务决策引导界面
│   ├── b2b-optimization/     # B2B 优化原型（高保真 Dashboard）
│   └── optimized-v1/         # 优化版原型
├── references/               # 参考资料
├── utils/                    # 工具函数
├── start.bat                 # Windows 一键启动脚本
├── requirements.txt          # Python 依赖
└── README.md                 # 项目说明文档
```

## 核心功能

### 1. 爆火视频检索
- 三平台（抖音、小红书、视频号）一键查询
- 商业 API 统一网关
- 热度计算与候选管理
- 支持 CSV/XLSX 导入

### 2. 视频音轨转文案
- 支持 MP4/MOV 视频上传
- FFmpeg 音轨提取
- faster-whisper 本地语音识别
- 在线校对与导出（TXT/JSON/SRT）

### 3. 数字人生成
- 文案驱动视频生成
- 多形象、多音色支持
- 异步任务管理
- 结果验证与存储

### 4. 批量生产流水线
- 端到端自动化编排
- 任务队列管理
- 进度追踪与状态管理

### 5. 多平台发布
- 抖音、快手、视频号适配
- 沙箱与生产模式
- 状态追踪与回滚

## 竞品分析

### 即创（JiChuang）平台对比

本系统与字节跳动即创平台的详细对比分析，请参考：[竞品分析报告](doc/competitive_analysis_jichuang.md)

**核心差异化优势**：
1. **热点洞察能力**：实时热点发现，即创不具备
2. **文案改写能力**：智能文案优化，即创不具备
3. **多平台发布**：支持抖音、快手、小红书等，即创主要限于抖音
4. **数据私有化**：品牌数据完全私有化，保障数据安全
5. **全链路解决方案**：热点发现→内容生产→多平台发布

**结论**：对于品牌营销团队（B端），"热点洞察+多平台发布"的价值大于"单条视频免费但仅限抖音"的价值。

## API 文档

### FastAPI 后端 API

启动后端服务后，访问以下地址查看 API 文档：

- **Swagger UI**: http://localhost:2001/docs
- **ReDoc**: http://localhost:2001/redoc

### 主要 API 端点

```
GET  /health                           # 健康检查
POST /api/v1/candidates/search         # 候选搜索
POST /api/v1/transcriptions            # 创建转写任务
GET  /api/v1/transcriptions/{id}       # 查询转写任务
POST /api/v1/pipelines                 # 创建流水线
GET  /api/v1/pipelines/{id}            # 查询流水线
GET  /api/v1/tasks                     # 任务列表
GET  /api/v1/admin/status              # 系统状态
POST /api/v1/copywriting/rewrite       # 文案改写
POST /api/v1/video-editor/edit         # 视频剪辑
GET  /api/v1/video-editor/capabilities # 剪辑器能力
POST /api/v1/publish                   # 发布视频
GET  /api/v1/publish/platforms         # 可用平台
```

## 开发指南

### 代码规范

```powershell
# 安装开发依赖
python -m pip install -r requirements-dev.txt

# 代码检查
python -m ruff check .

# 代码格式化
python -m ruff format .

# 运行测试
python -m pytest -q

# 编译检查
python -m compileall -q app.py app_pages src scripts tests
```

### 分支管理

1. 从 `main` 创建功能分支：`feature/your-feature`
2. 完成开发并本地验证
3. 提交 Pull Request 到 `main`
4. 代码评审后合并

### 提交规范

```
feat: 新功能
fix: 修复 bug
docs: 文档更新
style: 代码格式调整
refactor: 重构
test: 测试相关
chore: 构建/工具相关
```

## 部署说明

### 开发环境
- 使用 SQLite 数据库
- 启用热重载
- 调试模式开启

### 生产环境
1. 配置 PostgreSQL 数据库
2. 设置环境变量
3. 构建前端：`npm run build`
4. 启动后端：`uvicorn app.main:app --host 0.0.0.0 --port 2001`
5. 配置反向代理（Nginx）

### Docker 部署（可选）

```bash
# 构建镜像
docker build -t video-intelligence .

# 运行容器
docker run -p 1001:1001 -p 2001:2001 video-intelligence
```

## 常见问题

### Q: 端口被占用怎么办？
A: 启动脚本会自动尝试停止占用端口的进程。如需手动处理，使用 `netstat -ano | findstr "端口号"` 查看进程，然后 `taskkill /PID <PID> /F` 停止进程。

### Q: Python 依赖安装失败？
A: 检查网络连接，尝试使用国内镜像源：
```powershell
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: FFmpeg 未安装？
A: 访问 https://ffmpeg.org/download.html 下载安装，或使用包管理器：
```powershell
# Windows (winget)
winget install ffmpeg

# 或使用 chocolatey
choco install ffmpeg
```

### Q: 前端启动失败？
A: 先获取完整的最新项目文件，确保 `package.json` 和 `package-lock.json` 来自同一版本，然后重新双击根目录的 `start.bat`。启动程序会按锁文件自动准备前端依赖，无需手动运行 npm 命令。

### Q: 数据库连接失败？
A: 检查数据库配置，确保 PostgreSQL 服务已启动。开发环境默认使用 SQLite，无需额外配置。

## 许可证

本项目为私有项目，仅限内部使用。

## 联系方式

如有问题或建议，请联系开发团队。

---

**最后更新**: 2026-07-20  
**版本**: 2.0.0  
**维护者**: Crow5 开发团队
