# 短视频热点洞察与智能生产系统 - 项目交接

## 1. 交接目的

本文件是新 GPT/Codex 接手项目时的首要上下文。开始工作前依次阅读：

1. 本文件。
2. `docs/archive/video-intelligence-production-dev-doc-full-v1.6.md` 中与当前任务相关的章节。
3. 工作区或用户消息中的 `AGENTS.md` 约束。

本文记录当前仓库的已验证状态，不替代产品与开发文档。实时工作区与 Git 状态和本文冲突时，以实时状态为准。

## 2. 目标、范围与非目标

第一阶段目标是一个面向品牌营销团队的 Streamlit Web MVP：

```text
候选导入/检索
→ 互动指标快照
→ 热度排序
→ 用户选择候选
→ 上传有权处理的媒体
→ FFmpeg 提取音频
→ faster-whisper 转写
→ 在线校对并导出 TXT / JSON / SRT
```

当前里程碑只完成可点击的 Mock 纵向框架。真实 CSV/Excel 入库、SQLite 持久化、热度公式、FFmpeg、faster-whisper 和平台 API 尚未实现。

数字人、文案改写、自动剪辑、React、FastAPI、Docker、Redis、PostgreSQL、付费 API、多用户鉴权和大规模采集均为当前非目标。

## 3. 当前已实现状态

仓库已经包含可运行的 Streamlit 1.58 三页应用：

- **爆火视频检索**：24 条 Mock 候选，支持关键词、平台、赛道、时间和最低互动量筛选；展示指标卡、热度榜、置信度、缺失字段、入榜原因和候选选择。
- **音视频转文案**：候选元数据关联、授权确认、媒体上传入口、Mock 处理阶段、低置信度片段校对以及 TXT、JSON、SRT 下载。
- **任务记录**：包含检索与转写任务，覆盖处理中、成功和失败状态；显示进度、耗时、输出、明确错误和最多一次重试。
- **统一领域层**：`VideoCandidate`、`VideoMetricSnapshot`、`HeatResult`、`TaskRecord`、`TranscriptionTask` 等 Pydantic 模型。
- **接口隔离**：`CrawlerAdapter`、候选仓储、任务仓储、候选服务、热度服务和转写服务；页面不直接依赖平台返回字段。
- **Mock 仓储**：保存在每个 Streamlit 会话的 `st.session_state` 中，不跨用户共享可变状态。
- **资源预留**：SQLite 连接和 ASR 模型通过 `st.cache_resource` 延迟加载；当前启动不会导入或调用 faster-whisper。
- **失败策略**：外部连接类操作最多执行两次，即首次失败后自动重试一次，再失败则返回用户可见错误。
- **UI**：顶部导航、原生 Streamlit 组件、Material Symbols 和中性浅色 B 端主题；未使用自定义 CSS 或第三方 UI 组件。

当前 Git 状态（2026-07-17 验证）：

```text
branch: main
HEAD: 5b09c7d Remove obsolete video project files
tracking: origin/main
worktree: clean
```

## 4. 当前未实现状态

- CSV/Excel 与手工链接的真实解析、校验和导入。
- SQLite 表结构、迁移、候选持久化、指标快照与去重。
- 文档定义的 EI、ER、APR、GV、分桶分位数及 S/A/B 阈值计算；当前 `HeatService` 仅用于 Mock UI。
- FFmpeg 媒体探测、16kHz 单声道 WAV 提取和临时文件清理。
- faster-whisper tiny/base int8、VAD、时间戳和真实低置信度片段生成。
- `speech.wav`、`review_items.json` 与真实处理结果包。
- 抖音或其他平台的官方授权适配器。
- Streamlit Community Cloud 部署与云端降级验证。

## 5. 架构与数据流

```text
app.py / app_pages
        ↓
CandidateService / HeatService / TranscriptionService
        ↓
CandidateRepository / TaskRepository 协议
        ↓
MockRepository（当前） → SQLiteRepository（下一阶段）

未来媒体路径：
授权上传 → FFmpeg → faster-whisper → 校对 → TXT / JSON / SRT
```

重要文件：

- `app.py`：页面配置、顶部导航、会话初始化和运行能力提示。
- `app_pages/`：候选检索、音视频转文案、任务记录三个 UI 页面。
- `src/models.py`：统一领域模型与枚举。
- `src/contracts.py`：采集器和仓储协议。
- `src/repositories/mock.py`：当前会话级 Mock 仓储。
- `src/services/`：候选、热度和转写服务。
- `src/resources.py`：FFmpeg 能力检测及 SQLite/ASR 资源预留。
- `tests/`：领域、仓储、服务、重试与 Streamlit 页面测试。
- `docs/archive/video-intelligence-production-dev-doc-full-v1.6.md`：完整产品、技术、商业和合规设计。

## 6. 关键决策与理由

- 首期固定使用 Streamlit，优先快速获得可见、可验证结果。
- 页面目录使用 `app_pages/` 和 `st.Page + st.navigation`，避免旧式 `pages/` 自动发现冲突。
- 首版先用 Mock 纵向切片验证页面和接口边界，不提前安装重型媒体/模型依赖。
- 缺失指标必须保持 `None/null`，不得伪装为 0。
- 候选发现与媒体处理权分离；公开链接不代表取得媒体下载或处理授权。
- 正式转写只处理用户上传、客户自有账号或明确授权的媒体。
- 非官方采集器只能用于本地研究 POC，生产默认关闭；不实现 Cookie 池、代理池、验证码绕过、签名破解、去水印或全站下载。
- 自动平台尚未确认时默认预留抖音，但无官方权限前使用 Mock、手工链接或 CSV 演示。
- 网络、API 或工具因连接问题失败时最多自动重试一次，不循环重连。

## 7. 环境、安装与运行

2026-07-17 已验证环境：

```text
Windows 10 / PowerShell
Python 3.12.3
Streamlit 1.58.0
FFmpeg 8.1.2 Essentials（用户级安装）
```

安装运行依赖：

```powershell
python -m pip install -r requirements.txt
```

启动本地应用：

```powershell
python -m streamlit run app.py
```

Windows 也可以直接双击仓库根目录的 `打开短视频系统.cmd`。该启动文件会检查依赖、后台启动 Streamlit、等待健康检查通过并打开默认浏览器。

默认地址：`http://127.0.0.1:8501/`。当前 Mock 框架不需要模型文件或任何密钥。

安装开发依赖并验证：

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m compileall -q app.py app_pages src tests
```

最近一次验证结果：Ruff 通过、22 个 Python 文件格式通过、16 个测试通过。

## 8. 数据、密钥与外部依赖

- 不提交 `.env`、Streamlit Secrets、Cookie、数据库、媒体、模型权重或生成物。
- `packages.txt` 已声明云端系统依赖 `ffmpeg`；本机安装在 `%LOCALAPPDATA%\Programs\ffmpeg`，并已加入用户级 `PATH`。
- 未来若使用厂商或平台接口，密钥仅通过环境变量或 `.streamlit/secrets.toml` 提供；具体变量名应在选择供应商后记录，当前没有必需密钥。
- `references/github/` 仅作本地阅读参考，已在 `.gitignore` 中排除。

## 9. 阻塞项与未决问题

当前框架运行没有阻塞项。进入真实媒体切片前存在以下待办：

- FFmpeg 8.1.2 已安装并通过 `ffmpeg -version` 与 `ffprobe -version` 验证。
- 第一个自动采集平台尚未最终确认；默认建议抖音。
- 尚未确认是否具备抖音企业主体、开发者账号和官方数据权限。
- 在官方权限获批前，正式演示只能使用 Mock、客户自有数据、手工链接或 CSV。

## 10. 下一步与验收标准

**下一项建议工作：实现 SQLite + CSV/手工导入纵向切片，不要同时接入真实平台或 ASR。**

实施内容：

1. 建立 SQLite schema 和 `SQLiteRepository`，保持当前仓储协议不变。
2. 实现 `ManualImportAdapter`，支持 CSV/Excel 和手工链接/指标输入。
3. 标准化字段并验证可空指标、来源、采样时间、置信度和授权范围。
4. 保存同一作品的多次指标快照并按平台作品 ID 去重。
5. 将候选页从 MockRepository 切换为可配置仓储，同时保留 Mock 演示模式。

该切片验收标准：

- 可导入至少 20 条候选并在页面排序、筛选和导出。
- 缺失播放、分享或收藏时数据库和页面均保持 `null`。
- 重复导入同一作品不会产生重复候选，但可新增指标快照。
- 导入错误能定位到具体行和字段，不静默丢弃。
- 新增单元、仓储集成和 Streamlit 页面测试，现有 16 个测试继续通过。
- 数据库、导入文件和运行产物不进入 Git。

完成该切片后，再单独实施 FFmpeg + faster-whisper 真实转写切片。
