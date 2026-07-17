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

当前里程碑已完成“SQLite + CSV/XLSX/手工导入 + 人工复核 + 多快照热度规则”纵向切片。FFmpeg、faster-whisper 和真实平台 API 尚未实现。

数字人、文案改写、自动剪辑、React、FastAPI、Docker、Redis、PostgreSQL、付费 API、多用户鉴权和大规模采集均为当前非目标。

## 3. 当前已实现状态

仓库已经包含可运行的 Streamlit 1.58 三页应用：

- **爆火视频检索**：24 条 Mock 候选，支持关键词、平台、赛道、时间和最低互动量筛选；展示指标卡、热度榜、置信度、缺失字段、入榜原因和候选选择。
- **音视频转文案**：候选元数据关联、授权确认、媒体上传入口、Mock 处理阶段、低置信度片段校对以及 TXT、JSON、SRT 下载。
- **任务记录**：包含检索与转写任务，覆盖处理中、成功和失败状态；显示进度、耗时、输出、明确错误和最多一次重试。
- **统一领域层**：`VideoCandidate`、`VideoMetricSnapshot`、`HeatResult`、`TaskRecord`、`TranscriptionTask` 等 Pydantic 模型。
- **接口隔离**：`CrawlerAdapter`、候选仓储、任务仓储、候选服务、热度服务和转写服务；页面不直接依赖平台返回字段。
- **Mock 仓储**：保存在每个 Streamlit 会话的 `st.session_state` 中，不跨用户共享可变状态。
- **资源管理**：每个 Streamlit 会话持有独立 SQLite 仓储连接，数据库文件跨会话持久化；ASR 模型通过 `st.cache_resource` 延迟加载，当前启动不会导入或调用 faster-whisper。
- **失败策略**：外部连接类操作最多执行两次，即首次失败后自动重试一次，再失败则返回用户可见错误。
- **SQLite 持久化**：候选、指标快照、热度结果、来源批次、人工复核和任务均持久化；候选按平台作品 ID 去重，指标按采样时间追加。
- **合规数据入口**：支持 CSV/XLSX、手工链接与可见指标、显式抖音公开 URL 元数据；官方热门榜和关键词入口在权限获批前保持关闭。
- **人工复核**：候选必须确认数字人实际出镜、属于 B2B/AI 企业服务、存在营销获客 CTA，未通过复核时不输出爆火结论。
- **热度规则 v1**：实现 EI、触达/互动质量/增长/账号穿透/时效分量、同桶分位、冷启动门禁、静态高热、缺失字段降级和 provisional 标记。
- **UI**：顶部导航、原生 Streamlit 组件、Material Symbols 和中性浅色 B 端主题；未使用自定义 CSS 或第三方 UI 组件。

实施前 Git 基线（2026-07-17 验证）：

```text
branch: main
HEAD: e8de387 Document current MVP handoff status and Windows startup instructions
tracking: origin/main
worktree: clean
```

## 4. 当前未实现状态

- SQLite 版本化迁移工具；当前使用幂等 `CREATE TABLE IF NOT EXISTS` 初始化 schema。
- 后台定时调度；当前按页面提示在 T+2/T+6/T+24 小时手工或重新导入快照。
- 已确认异常互动的自动检测；当前明确返回 `not_evaluated`，不伪造异常惩罚。
- FFmpeg 媒体探测、16kHz 单声道 WAV 提取和临时文件清理。
- faster-whisper tiny/base int8、VAD、时间戳和真实低置信度片段生成。
- `speech.wav`、`review_items.json` 与真实处理结果包。
- 抖音或其他平台的真实官方授权调用；当前只有默认关闭且会明确报权限不足的适配器入口。
- Streamlit Community Cloud 部署与云端降级验证。

## 5. 架构与数据流

```text
app.py / app_pages
        ↓
CandidateService / HeatService / TranscriptionService
        ↓
CandidateRepository / TaskRepository 协议
        ↓
SQLiteRepository（当前默认） / MockRepository（测试与演示降级）

未来媒体路径：
授权上传 → FFmpeg → faster-whisper → 校对 → TXT / JSON / SRT
```

重要文件：

- `app.py`：页面配置、顶部导航、会话初始化和运行能力提示。
- `app_pages/`：候选检索、音视频转文案、任务记录三个 UI 页面。
- `src/models.py`：统一领域模型与枚举。
- `src/contracts.py`：采集器和仓储协议。
- `src/repositories/sqlite.py`：当前默认仓储；`mock.py` 保留给测试和演示降级。
- `src/services/`：候选、热度和转写服务。
- `src/resources.py`：FFmpeg 能力检测及 ASR 资源预留。
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

默认地址：`http://127.0.0.1:8501/`。当前候选导入与热度链路不需要模型文件或密钥；正式平台接口和真实转写仍需后续配置。

安装开发依赖并验证：

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m compileall -q app.py app_pages src tests
```

最近一次验证结果：Ruff 通过、29 个 Python 文件格式通过、26 个测试通过。

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

**下一项建议工作：用真实、经授权或人工提供的 300 条 B2B/AI 企业服务数字人口播候选完成四快照试点；不要通过非官方逆向绕过平台权限。**

实施内容：

1. 用页面下载模板收集 300 条已人工确认的目标候选。
2. 在首次导入后 2、6、24 小时重复导入可见指标，形成每条四快照目标。
3. 抽检至少 50 条复核结果，确认数字人/赛道/CTA 判断一致率达到 90%。
4. 复查 provisional Top 20 及入榜解释，记录运营认可结果。
5. 同步申请抖音热门榜及与自身业务相关的关键词权限，获批后再实现真实官方客户端。

该切片验收标准：

- 可导入 300 条经复核候选并在页面排序、筛选和追溯来源。
- 缺失播放、分享或收藏时数据库和页面均保持 `null`。
- 重复导入同一作品不会产生重复候选，但可新增指标快照。
- 导入错误能定位到具体行和字段，不静默丢弃。
- 单元、仓储集成和 Streamlit 页面测试全部通过。
- 数据库、导入文件和运行产物不进入 Git。

完成该切片后，再单独实施 FFmpeg + faster-whisper 真实转写切片。
