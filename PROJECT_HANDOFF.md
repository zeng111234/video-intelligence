# 短视频热点洞察与智能生产系统 - 项目交接

## 1. 交接目的

本文件是新 GPT/Codex 接手项目时的首要上下文。开始工作前依次阅读：

1. 本文件。
2. `docs/archive/video-intelligence-production-dev-doc-full-v1.6.md` 中与当前任务相关的章节。
3. 工作区或用户消息中的 `AGENTS.md` 约束。

本文记录当前仓库的已验证状态，不替代产品与开发文档。实时工作区与 Git 状态和本文冲突时，以实时状态为准。

### 2026-07-18 三平台商业 API 网关增量

- 新增 `LicensedSearchProvider`、供应商能力/搜索页/用量模型，以及父级 `SearchBatch` 和三个 `PlatformSearchRun` 子任务。
- 默认 `VIDEO_LICENSED_PROVIDER_MODE=sandbox`，离线生成三平台固定演示候选，不访问平台、不计外部调用；生产模式在真实供应商适配器和商业授权验收前保持关闭。
- SQLite 新增批次、平台运行、供应商请求锁和用量账本；相同请求 60 秒防重，成功结果缓存 10 分钟，360 次预警、450 次硬停止，结果状态不明确时阻止重发。
- 趋势表已迁移为 `(keyword, platform, video_id, computed_at)` 主键；匹配记录新增供应商维度，平台、供应商和时间窗口不再共用趋势池。
- 首页改为关键词、近 7 天、每平台 10 条和中文确认弹窗；结果按抖音、小红书、微信视频号三个页签独立展示，历史库与备用导入折叠保留。
- 旧抖音适配器默认关闭，只有设置 `DOUYIN_OFFICIAL_VERIFIED=true` 且真实 OAuth/Scope 已验收后才会由配置工厂启用。
- Ruff、编译和 67 项自动化测试通过；当前 8501 旧进程需重启后才会加载本次新增模块。

### 2026-07-18 三平台合规基础增量

- `Platform` 新增 `WECHAT_CHANNELS`，当前业务平台为抖音、小红书、微信视频号；UI 使用中文标签并恢复平台列与三平台过滤。
- CSV/XLSX 和手工入口支持三平台。抖音/小红书校验常见公开域名；视频号可使用公开 URL，或 `feed_id + finder_user_name` 追溯，不伪造缺失 URL，不自动抓取或下载媒体。
- 平台能力卡明确：抖音为“待授权 / 受限自动获取”，配置凭证仍不代表授权通过；小红书/视频号为“仅手工 / CSV”，自动入口不可点击且不会触发网络请求。
- 候选仍以 `(platform, platform_item_id)` 去重；缺失互动指标、证据和来源 URL 保持空值语义。
- 关键词命中、趋势计算和趋势仓储查询新增平台维度，同关键词不得跨平台共享样本池、分位数或榜单。
- 转写页显示候选平台与视频号追溯字段；媒体仍必须由用户授权上传，系统不会根据候选链接自动下载。
- 固定响应自动化验证覆盖三平台状态、零网络调用、三平台导入、SQLite 追溯字段、平台隔离排名和转写候选平台；共 55 项测试通过。

### 2026-07-17 关键词发现增量

- 已实现候选页顶部低调用量关键词入口：输入如“二手车”，每次固定一次综合排序搜索，可选召回 1–10 条、默认 10 条，平台有下一页也不继续请求。
- 凭证默认留空；复制 `.streamlit/secrets.toml.example` 为 `.streamlit/secrets.toml` 后填写 `DOUYIN_CLIENT_KEY` 与 `DOUYIN_CLIENT_SECRET`，或使用同名环境变量。真实凭证不得提交。
- 未配置时按钮禁用且不发起网络请求。连接类失败只自动重试一次；权限类错误直接明确返回。
- 每次发现任务与关键词命中关系持久化到 `discovery_runs`、`candidate_matches`，包含平台综合位置、采样时间、发布时间范围和排序模式。
- 新增近 7 天关键词趋势榜：按点赞增长、年龄归一化点赞、综合名次、新鲜度和持续入榜次数重排，自有榜与平台召回位置明确分开。
- 首轮置信度最高 0.45；少于 30 条只显示“观察中”。疑似点赞结构或增长轨迹异常执行 0.75 惩罚并保留待核验，不自动剔除。
- 关键词归属以匹配记录为准，同一视频可属于多个关键词而不互相覆盖；获取后自动重算近 7 天榜单。
- 2026-07-17 已完成主流程收敛：关键词趋势榜默认展示，候选单选后上传授权媒体，真实执行 FFmpeg 与 faster-whisper，校对版本持久化，确认成稿后导出。
- 相同关键词、时间范围、数量在 60 秒内由 SQLite 指纹阻止重复付费请求；首次召回会建立 T+2/T+6/T+24 复采检查点。

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

当前里程碑已完成“SQLite + 三平台合规导入 + 商业 API 统一网关与沙箱 + 平台隔离趋势排名 + 真实本地转写 + 校对版本与导出”纵向切片。真实三平台自动搜索仍待供应商沙箱文档、凭证、报价和 B 端商业授权。

数字人、文案改写、自动剪辑、React、FastAPI、Docker、Redis、PostgreSQL、多用户鉴权和大规模采集均为当前非目标。

## 3. 当前已实现状态

仓库已经包含可运行的 Streamlit 1.58 三页应用：

- **爆火视频检索**：三平台一键批次、中文费用确认、沙箱/生产状态、缓存与配额；三个独立 Top 10，每个平台最多返回 10 条。
- **音视频转文案**：候选元数据关联、授权确认、媒体双重校验、FFmpeg 音频提取、faster-whisper 本地识别、低置信度校对和确认成稿后下载。
- **任务记录**：包含检索与转写任务，覆盖处理中、成功和失败状态；显示进度、耗时、输出、明确错误和最多一次重试。
- **统一领域层**：`VideoCandidate`、`VideoMetricSnapshot`、`HeatResult`、`TaskRecord`、`TranscriptionTask` 等 Pydantic 模型。
- **接口隔离**：`CrawlerAdapter`、`LicensedSearchProvider`、候选仓储、任务仓储、商业搜索服务、热度服务和转写服务；页面不直接依赖供应商返回字段。
- **Mock 仓储**：保存在每个 Streamlit 会话的 `st.session_state` 中，不跨用户共享可变状态。
- **资源管理**：每个 Streamlit 会话持有独立 SQLite 仓储连接，数据库文件跨会话持久化；ASR 模型通过 `st.cache_resource` 延迟加载，当前启动不会导入或调用 faster-whisper。
- **失败策略**：外部连接类操作最多执行两次，即首次失败后自动重试一次，再失败则返回用户可见错误。
- **SQLite 持久化**：候选、指标快照、热度结果、来源批次、人工复核和任务均持久化；候选按平台作品 ID 去重，指标按采样时间追加。
- **合规数据入口**：三平台支持 CSV/XLSX、手工链接与可见指标；视频号可用 `feed_id + finder_user_name` 追溯。显式公开 URL 元数据读取仍只限抖音；官方关键词客户端已实现但在凭证缺失时保持禁用，热门榜仍待单独权限。
- **通用关键词范围**：数字人、B2B/AI 和营销 CTA 字段仅为历史兼容，不再限制主流程候选。
- **热度规则**：关键词趋势榜与通用热度分开；样本或增长快照不足时仅显示观察排名，异常候选不得输出高等级。
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
- 通用热度模型的已确认异常检测；关键词趋势模型仅实现“疑似异常待核验”降权，不声称证明刷赞。
- 真实授权媒体的人工验收样本库；自动化测试使用固定媒体响应，不提交用户媒体。
- 新榜或数说故事真实适配器；当前没有供应商文档和凭证，不会伪造网络字段或提前启用生产开关。
- 抖音热门榜或正式 OAuth 关键词搜索；旧客户端保留但默认关闭，尚待真实应用凭证与 Scope 验证。
- Streamlit Community Cloud 部署与云端降级验证。

## 5. 架构与数据流

```text
app.py / app_pages
        ↓
CommercialSearchService / CandidateService / HeatService / TranscriptionService
        ↓
CandidateRepository / TaskRepository 协议
        ↓
SQLiteRepository（当前默认） / MockRepository（测试与演示降级）

当前媒体路径：
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
- 真实转写使用本地 `base/int8` 模型，首次识别时按需下载模型权重。
- 缺失指标必须保持 `None/null`，不得伪装为 0。
- 候选发现与媒体处理权分离；公开链接不代表取得媒体下载或处理授权。
- 正式转写只处理用户上传、客户自有账号或明确授权的媒体。
- 非官方采集器只能用于本地研究 POC，生产默认关闭；不实现 Cookie 池、代理池、验证码绕过、签名破解、去水印或全站下载。
- 三平台自动搜索统一走授权商业供应商；未验收前只使用离线沙箱、手工链接或 CSV。
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

默认地址：`http://127.0.0.1:8501/`。CSV、手工导入与本地转写不需要平台密钥；抖音关键词搜索需配置应用凭证。

安装开发依赖并验证：

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m compileall -q app.py app_pages src tests
```

最近一次验证结果：Ruff、编译与 67 个测试通过，SQLite 商业批次/趋势迁移与校对版本重启持久化通过；8501 健康检查返回 `ok`，但旧运行进程需人工重启后加载新模块。

## 8. 数据、密钥与外部依赖

- 不提交 `.env`、Streamlit Secrets、Cookie、数据库、媒体、模型权重或生成物。
- `packages.txt` 已声明云端系统依赖 `ffmpeg`；本机安装在 `%LOCALAPPDATA%\Programs\ffmpeg`，并已加入用户级 `PATH`。
- 数据模式通过 `VIDEO_LICENSED_PROVIDER_MODE` 配置，默认 `sandbox`；真实供应商密钥只能通过环境变量、Streamlit Secrets 或 Windows 凭证管理器提供，数据库只保存凭证别名。
- 抖音旧接口还需显式设置 `DOUYIN_OFFICIAL_VERIFIED=true` 才可能启用，仓库不保存真实凭证。
- `references/github/` 仅作本地阅读参考，已在 `.gitignore` 中排除。

## 9. 阻塞项与未决问题

离线沙箱和转写框架没有代码阻塞项。进入三平台真实商业数据验收前存在以下待办：

- FFmpeg 8.1.2 已安装并通过 `ffmpeg -version` 与 `ffprobe -version` 验证。
- 尚未取得新榜或数说故事的沙箱文档、固定响应、报价、API 凭证和 B 端商业授权条款。
- 未完成真实适配器与小流量验收前，候选检索只能使用演示数据、客户自有数据、手工链接或 CSV；本地转写可使用已授权媒体。

## 10. 下一步与验收标准

**下一项建议工作：向新榜提交统一字段与预算需求，取得沙箱文档和合同条款后实现首个真实供应商适配器；若不满足授权门槛则保持沙箱，不降级为非授权爬虫。**

实施内容：

1. 申请抖音关键词搜索权限，填写凭证并完成一次 10 条真实接口验收。
2. 按页面提醒在首次召回后 2、6、24 小时由用户确认复采，检查重复观测与未召回标记。
3. 用自有或明确授权的短媒体完成上传、识别、校对、确认和三种格式导出验收。
4. 复查不同关键词的系统 Top 10 与入榜解释，记录运营认可结果。

该切片验收标准：

- 可导入 300 条经复核候选并在页面排序、筛选和追溯来源。
- 缺失播放、分享或收藏时数据库和页面均保持 `null`。
- 重复导入同一作品不会产生重复候选，但可新增指标快照。
- 导入错误能定位到具体行和字段，不静默丢弃。
- 单元、仓储集成和 Streamlit 页面测试全部通过。
- 数据库、导入文件和运行产物不进入 Git。

完成该切片后，再根据真实样本校准热门阈值，不提前将观察排名宣传为已确认爆火。
