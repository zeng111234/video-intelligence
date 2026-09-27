# VideoInsight · 短视频热点洞察与智能生产系统

> 面向内容团队的短视频热点发现 → AI 文案 → 数字人/精剪 → 多平台发布全链路系统。
> A full-pipeline short-video intelligence & production system: trend discovery, AI copywriting, avatar video, auto-editing, multi-platform publishing.

本仓库为商业化项目中由本人负责核心研发的版本整理，公开版本已移除交付文档与内部配置，代码与功能保持完整。

## 功能总览

| 模块 | 说明 |
|------|------|
| 热点发现 | 抖音 / 小红书 / 快手 / B站 候选采集、商业 API 统一网关、热度计算、关键词趋势、候选管理 |
| 视频转文案 | MP4/MOV 上传与分享链接解析；本地 faster-whisper + 云端 ASR 双通道；在线校对；TXT/JSON/SRT 导出；CER 质量评估 |
| AI 文案改写 | DeepSeek 等多模型接入；改写 / 扩写 / 爆款风格；草稿与历史管理 |
| 数字人生成 | 文案驱动视频合成；多形象多音色；异步任务与结果校验 |
| 智能剪辑 | 本地 FFmpeg 流水线（字幕烧录、关键词强调、B-roll/PiP、转场）+ 云端剪辑双通道；报价确认后才开始计费生产 |
| 批量生产 | 端到端工作流编排（热点 → 文案 → 成片 → 发布）、任务队列、断点恢复 |
| 多平台发布 | 抖音 / 快手 / 视频号 / 小红书适配器；沙箱与生产模式；状态追踪与回滚；防封限流 |
| 积分账户 | 按量计费、余额流水、余额不足拦截、防并发超扣 |
| 系统韧性 | 统一重试策略、上下文资源监控、任务状态机、迁移框架 |

## 技术栈

- **后端**：Python 3.12+ · FastAPI · SQLAlchemy · SQLite（开发）/ PostgreSQL（生产）· FFmpeg · faster-whisper · Playwright
- **前端**：React 18 · TypeScript · Vite 5 · Ant Design 5 · React Router 6 · Recharts
- **工程化**：pytest · ruff · 数据库迁移框架 · GitHub Actions · 一键启动脚本（Windows 自动装环境）

## 快速开始

```bash
# Windows：双击 start.bat（自动检测并安装 Python/Node 依赖）
# 或 PowerShell：
.\scripts\start_all_services.ps1
```

启动后访问：

- 工作台：http://localhost:1001/pipeline
- API 健康：http://localhost:2001/health
- Swagger 文档：http://localhost:2001/docs

`.env` 不随仓库分发：首次启动从 `.env.example` 生成本地无密钥配置，需要云服务时再填自己的账号。

## 项目结构

```
video-intelligence/
├── src/                # 核心业务层：30+ 服务模块（models/services/adapters/repositories）
│   ├── services/       # 转写、文案、数字人、剪辑、流水线、发布、积分等
│   ├── adapters/       # ASR / 数字人 / 平台采集适配器（本地+云端双通道桥接）
│   └── repositories/   # SQLite 仓储与 Mock 测试仓储
├── project/backend/    # FastAPI 服务（18 个 API 路由模块 + 迁移框架）
├── project/frontend/   # React 18 前端（18 个页面）
├── scripts/            # 启动 / 验收 / 基准测试脚本
├── database/           # SQL 初始化与迁移脚本
└── tests/              # 服务层与 API 层 pytest
```

## 开发指南

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest -q
```

提交规范：`feat / fix / docs / style / refactor / test / chore`。

## 设计要点

- **双通道桥接**：ASR 与视频剪辑均为"本地 + 云端"双实现，经桥接层统一接口，可按成本与网络环境切换
- **计费安全**：所有可能产生费用的动作先报价、用户确认后才执行；费用未知时阻断而非猜测
- **状态可信**：严格区分"已创建 / 处理中 / 等待确认 / 已生成 / 已发布"，不把中间状态包装成最终成功
- **可恢复性**：任务进度持久化，崩溃 / 重启后可从断点恢复，用户输入不丢失

## License

TBD（整理中）
