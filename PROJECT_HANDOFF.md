# 短视频热点洞察与智能生产系统 - 项目交接

## 1. 交接目的

本文件是新 GPT/Codex 接手项目时的首要上下文。开始工作前依次阅读：

1. 本文件。
2. `docs/archive/video-intelligence-production-dev-doc-full-v1.6.md` 中与当前任务相关的章节。
3. 工作区或用户消息中的 `AGENTS.md` 约束。

本文记录当前仓库的已验证状态，不替代产品与开发文档。实时工作区与 Git 状态和本文冲突时，以实时状态为准。

### 2026-07-18 视频音轨转文案第二页增量

- 准确率模式新增 `large-v3-turbo`，快速预览保留 `base`。同一 100 秒视频、同一第三方参考文本下，参考 CER 从 25.34% 降至 8.54%，相对下降约 66.3%；参考不是人工真值，不作为对外承诺。
- 热词与 initial prompt 实验导致重复或漏句，已从正式页面移除；当前只做忠实 ASR，不做智能改写。模型已缓存，首次下载 420.95 秒，缓存后加载约 5.8 秒。
- 新增 `scripts/benchmark_asr.py` 和 `src/asr_quality.py`，记录 CER、数字准确率、热词命中、耗时与实时率；云端 ASR 因没有配置任何供应商凭证，尚未调用或验收。
- 顶部导航为“视频音轨转文案”，页头为“爆火视频音轨转文案”；候选关联改为可选，没有候选也可直接进入独立体验模式。
- 页面采用原生 Streamlit 单列四步：可选候选摘要、授权视频上传或直链、同步真实处理状态、全宽校对与导出。只接受 MP4/MOV 视频并提取其中音轨，不接受单独音频，不做抽帧或画面分析。
- 直链仅接受直接返回 MP4/MOV 的 HTTPS 公网地址，拒绝本机/内网/保留地址、非标准端口、非视频响应及超过 50MB 的内容；连接失败最多重试一次。平台分享页不使用非授权下载器，提示改为上传。
- `create_task` 通过同步进度回调报告文件检查、音频提取、语音识别、完成或失败；首次回调锁定本次任务，失败后要求重新上传，不自动串用旧结果。
- FFprobe 时长必须是有限值且 `0 < duration <= 900`；任务持久化媒体时长。页面错误只使用带 `code/user_message/task_id` 的安全领域信息，不显示原始异常、临时路径或 traceback；模型连接失败最多重试一次。
- 校对片段新增“已复核”；低于 0.75 的片段允许保存草稿，但新确认成稿前必须全部复核。SQLite 校对版本只追加插入，版本冲突拒绝覆盖。
- 当前候选可选择最近真实任务继续校对；下载只读取任务 `approved_revision_id` 指向的版本，并显示片段、待复核、语言、模型、时长与版本信息。历史已批准版本保持兼容导出。

### 2026-07-18 多行业准确率边界与后续方案

- 当前 `large-v3-turbo` 是通用语音模型，并非使用二手车语料训练或微调；二手车视频只是当前唯一一条真实链路测试样本，不能据此声称系统已适配汽车行业或其他行业。
- 当前没有人工逐字真值。Soundwise 文本同样是第三方自动转写并包含错词，8.54% 仅是同一样本下的参考 CER，不能作为真实准确率或对外承诺。
- 面向不同行业 B 端客户的建议路径是“通用模型 + 企业/行业词库 + 低置信短片段二次识别 + 人工确认纠错”，而不是立即为每个行业重新训练模型。
- 企业词库、行业模板、低置信片段二次识别、纠错反馈学习均尚未实现。整段音频全局注入热词已经在本地实验中导致明显漏句，不得直接恢复到正式页面。
- 云端 ASR 双通道仍待供应商凭证和真实 A/B；在实际调用、成本和准确率验收前，不声称腾讯云、火山引擎或 OpenAI 比当前本地模型更准确。

### 2026-07-18 公司数字人系统真实接入增量

- 顶部导航第四页“数字人生成”已从页面骨架升级为真实异步任务入口，位于“视频音轨转文案”和“任务记录”之间。
- 文案来源优先读取真实转写任务 `approved_revision_id` 指向的已确认成稿；没有批准版本时自动降级为当前浏览器会话内的临时手工输入，不读取草稿或原始识别结果。
- 新增 `TaskKind.AVATAR`、`AvatarTask`、`AvatarProvider`、`InternalAvatarProvider` 和 `AvatarService`；继续复用 SQLite `tasks.payload_json`，不新增重复任务表。
- 页面从公司 PHP 内部 API 读取真实能力、已授权形象和音色；服务、文案长度、三项授权和权利主体全部满足后才启用“开始生成”。提交后持久化不可变文案快照、幂等键、供应商状态、费用、耗时和结果元数据。
- GET 类连接失败最多自动重试一次；付费 POST 不自动重发。提交响应丢失时保存 `outcome_unknown` 并按原幂等键核对，避免重复计费。
- 成功视频经内部鉴权结果接口下载，先检查大小、MIME、MP4 `ftyp` 和 FFprobe 视频流，再原子保存到 `data/avatar_results/`；生成物不进入 Git。
- 公司数字人系统是仓库外部依赖，实际位置由部署方自行确定；该系统已新增 `/internal/v1/avatar/*`、独立 Bearer 服务令牌、MySQL 幂等任务表和旧私有网关 adapter，不复用会员 JWT、积分或旧编译前端。
- Docker 已降级为可选兼容部署；原生 PHP 7.4/Apache/MySQL 是支持路径。当前机器没有 PHP CLI，Docker daemon 启动后仍不可用，因此 PHP 运行时和真实供应商端到端尚未在本机验收。
- 页面明确区分“转写校对完成”和“拥有改编/合成/发布权”，真实接入前仍需供应商授权、肖像与声音授权、文案版权与事实审核，以及目标平台 AI 内容标识与广告规则确认。

### 2026-07-18 三平台商业 API 网关增量

- 新增 `LicensedSearchProvider`、供应商能力/搜索页/用量模型，以及父级 `SearchBatch` 和三个 `PlatformSearchRun` 子任务。
- 默认 `VIDEO_LICENSED_PROVIDER_MODE=sandbox`，离线生成三平台固定演示候选，不访问平台、不计外部调用；生产模式在真实供应商适配器和商业授权验收前保持关闭。
- SQLite 新增批次、平台运行、供应商请求锁和用量账本；相同请求 60 秒防重，成功结果缓存 10 分钟，360 次预警、450 次硬停止，结果状态不明确时阻止重发。
- 趋势表已迁移为 `(keyword, platform, video_id, computed_at)` 主键；匹配记录新增供应商维度，平台、供应商和时间窗口不再共用趋势池。
- 首页改为关键词、近 7 天、每平台 10 条和中文确认弹窗；结果按抖音、小红书、微信视频号三个页签独立展示，历史库与备用导入折叠保留。
- 旧抖音适配器默认关闭，只有设置 `DOUYIN_OFFICIAL_VERIFIED=true` 且真实 OAuth/Scope 已验收后才会由配置工厂启用。
- Ruff、格式、编译、diff 检查和 90 项自动化测试通过；100.37 秒真实视频完整服务链路成功，耗时 84.66 秒并生成 86 个片段。

### 2026-07-18 三平台合规基础增量

- `Platform` 新增 `WECHAT_CHANNELS`，当前业务平台为抖音、小红书、微信视频号；UI 使用中文标签并恢复平台列与三平台过滤。
- CSV/XLSX 和手工入口支持三平台。抖音/小红书校验常见公开域名；视频号可使用公开 URL，或 `feed_id + finder_user_name` 追溯，不伪造缺失 URL，不自动抓取或下载媒体。
- 平台能力卡明确：抖音为“待授权 / 受限自动获取”，配置凭证仍不代表授权通过；小红书/视频号为“仅手工 / CSV”，自动入口不可点击且不会触发网络请求。
- 候选仍以 `(platform, platform_item_id)` 去重；缺失互动指标、证据和来源 URL 保持空值语义。
- 关键词命中、趋势计算和趋势仓储查询新增平台维度，同关键词不得跨平台共享样本池、分位数或榜单。
- 转写页在有候选时显示平台与视频号追溯字段；候选只做可选关联。媒体必须由用户授权上传或通过授权直链提供，系统不会根据候选平台分享链接自动下载。
- 固定响应自动化验证覆盖三平台状态、零网络调用、三平台导入、SQLite 追溯字段、平台隔离排名和转写候选平台；共 55 项测试通过。

### 2026-07-17 关键词发现增量

- 已实现候选页顶部低调用量关键词入口：输入如“二手车”，每次固定一次综合排序搜索，可选召回 1–10 条、默认 10 条，平台有下一页也不继续请求。
- 凭证默认留空；复制 `.streamlit/secrets.toml.example` 为 `.streamlit/secrets.toml` 后填写 `DOUYIN_CLIENT_KEY` 与 `DOUYIN_CLIENT_SECRET`，或使用同名环境变量。真实凭证不得提交。
- 未配置时按钮禁用且不发起网络请求。连接类失败只自动重试一次；权限类错误直接明确返回。
- 每次发现任务与关键词命中关系持久化到 `discovery_runs`、`candidate_matches`，包含平台综合位置、采样时间、发布时间范围和排序模式。
- 新增近 7 天关键词趋势榜：按点赞增长、年龄归一化点赞、综合名次、新鲜度和持续入榜次数重排，自有榜与平台召回位置明确分开。
- 首轮置信度最高 0.45；少于 30 条只显示“观察中”。疑似点赞结构或增长轨迹异常执行 0.75 惩罚并保留待核验，不自动剔除。
- 关键词归属以匹配记录为准，同一视频可属于多个关键词而不互相覆盖；获取后自动重算近 7 天榜单。
- 2026-07-17 已完成主流程收敛：关键词趋势榜默认展示；转写页现可独立上传授权视频或使用授权视频直链，也可关联候选，真实执行 FFmpeg 音轨提取与 faster-whisper 转写，校对版本持久化，确认成稿后导出。
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

数字人内部适配层、任务持久化和页面交互已实现；真实视频生成仍取决于另行提供的供应商文档、合法凭证、资产 ID、结果域名和原生 PHP/MySQL 运行环境。文案改写、自动剪辑、React、FastAPI、Redis、PostgreSQL、多用户鉴权和大规模采集仍为当前非目标。

## 3. 当前已实现状态

仓库已经包含可运行的 Streamlit 1.58 四页应用：

- **爆火视频检索**：三平台一键批次、中文费用确认、沙箱/生产状态、缓存与配额；三个独立 Top 10，每个平台最多返回 10 条。
- **视频音轨转文案**：无需先选候选，可上传授权 MP4/MOV 或填写授权 HTTPS MP4/MOV 直链；系统只提取视频音轨，经媒体双重校验、FFmpeg 标准化和 faster-whisper 本地识别后，提供真实同步进度、低置信度复核、追加校对版本和确认成稿下载。
- **数字人生成**：选择已确认成稿或临时手工输入，从内部服务加载真实授权形象/音色、报价和预计耗时；满足服务与授权门禁后提交异步任务，刷新状态并保存验证后的视频。
- **任务记录**：包含检索、转写与数字人任务，覆盖排队、处理中、成功和失败状态；数字人任务显示形象、音色、权利主体、供应商状态和结果。
- **统一领域层**：`VideoCandidate`、`VideoMetricSnapshot`、`HeatResult`、`TaskRecord`、`TranscriptionTask` 等 Pydantic 模型。
- **接口隔离**：`CrawlerAdapter`、`LicensedSearchProvider`、`AvatarProvider`、候选/任务仓储及对应服务；页面不直接依赖平台或数字人供应商返回字段。
- **Mock 仓储**：保存在每个 Streamlit 会话的 `st.session_state` 中，不跨用户共享可变状态。
- **资源管理**：每个 Streamlit 会话持有独立 SQLite 仓储连接，数据库文件跨会话持久化；ASR 模型通过 `st.cache_resource` 延迟加载，当前启动不会导入或调用 faster-whisper。
- **失败策略**：外部连接类操作最多执行两次，即首次失败后自动重试一次，再失败则返回安全的用户可见错误；真实媒体失败后必须重新上传。
- **SQLite 持久化**：候选、指标快照、热度结果、来源批次、人工复核和任务均持久化；候选按平台作品 ID 去重，指标和校对版本均追加，任务原位更新。
- **合规数据入口**：三平台支持 CSV/XLSX、手工链接与可见指标；视频号可用 `feed_id + finder_user_name` 追溯。显式公开 URL 元数据读取仍只限抖音；官方关键词客户端已实现但在凭证缺失时保持禁用，热门榜仍待单独权限。
- **通用关键词范围**：数字人、B2B/AI 和营销 CTA 字段仅为历史兼容，不再限制主流程候选。
- **热度规则**：关键词趋势榜与通用热度分开；样本或增长快照不足时仅显示观察排名，异常候选不得输出高等级。
- **UI**：顶部四页导航、原生 Streamlit 组件、Material Symbols 和中性浅色 B 端主题；未使用自定义 CSS 或第三方 UI 组件。

当前系统状态（2026-07-23 更新，以 `git status` 和实际运行结果为准）：

```text
branch: main
当前 worktree: 存在未提交改动；生产验证以 FastAPI 2001 + React 1001 为主
Streamlit 8501 已废弃，不再作为受支持的运行入口
FastAPI 后端当前有 110 个 API/服务测试
React 前端当前有 15 个业务路由页面
数据库迁移框架已建立（runner.py + 001 至 004 版本化迁移）
ASR 桥接层已实现（本地/云端统一接口）
当前验证：后端 110 项通过、非 Streamlit 的核心 Python 421 项通过、前端 7 项通过
```

## 4. 当前已实现与未实现状态

### 已实现（2026-07-23 更新）

- **SQLite 版本化迁移工具**：`database/migrations/` 已建立迁移框架（runner.py + 001 至 004 迁移脚本），`user_version` 追踪机制已实现；v004 会隔离缺失候选的历史复采检查点，恢复外键完整性而不丢弃 payload。
- **FastAPI 后端**：`project/backend/` 已实现完整的 REST API 服务（端口 2001），包含 9 个路由模块、DI 容器、统一错误处理。
- **React 前端**：`project/frontend/` 已实现完整的前端应用（端口 1001），包含 15 个业务路由页面、API 客户端、类型定义。
- **文案改写服务**：`src/services/copywriting.py` 和后端 `/api/v1/copywriting/rewrite` 端点已实现。
- **视频编辑服务**：`src/services/video_editor.py` 和后端 `/api/v1/video-editor/*` 端点已实现。
- **多平台发布服务**：`src/services/publisher.py` 和后端 `/api/v1/publish/*` 端点已实现。
- **云端 ASR 桥接层**：`src/adapters/asr_bridge.py` 统一本地/云端 ASR 接口，阿里云适配器已实现。

### 仍为未实现状态

- 后台定时调度；当前按页面提示在 T+2/T+6/T+24 小时手工或重新导入快照。
- 通用热度模型的已确认异常检测；关键词趋势模型仅实现"疑似异常待核验"降权，不声称证明刷赞。
- 真实授权媒体的人工验收样本库；自动化测试使用固定媒体响应，不提交用户媒体。
- 跨行业人工真值集；当前只有一条二手车视频及第三方自动参考文本，不能计算可信的跨行业 CER。
- 企业/租户级行业词库、词库审核页面、低置信短片段二次识别和人工纠错反馈学习。
- 真实数字人供应商凭证、生产资产清单和本机端到端生成验收；接口不会在缺少这些配置时伪造成功或费用。
- 形象克隆、声音克隆、批量生成、旧会员计费和公开素材上传；首期只支持低并发文本驱动视频。
- 新榜或数说故事真实适配器；当前没有供应商文档和凭证，不会伪造网络字段或提前启用生产开关。
- 抖音热门榜或正式 OAuth 关键词搜索；旧客户端保留但默认关闭，尚待真实应用凭证与 Scope 验证。
- Streamlit Community Cloud 部署与云端降级验证。

## 5. 架构与数据流

```text
app.py / app_pages
        ↓
CommercialSearchService / CandidateService / HeatService / TranscriptionService / AvatarService
        ↓
CopywritingService / VideoEditingService / PublisherService / PipelineService
        ↓
CandidateRepository / TaskRepository 协议
        ↓
SQLiteRepository（当前默认） / MockRepository（测试与演示降级）

FastAPI 后端路径（端口 2001）：
React 前端 → API 网关 → DI 容器 → src/services 层 → SQLiteRepository

当前媒体路径：
授权上传 → FFmpeg → faster-whisper / ASRBridge（云端） → 校对 → TXT / JSON / SRT

数字人路径：
确认成稿 / 临时手工文案
→ AvatarService → InternalAvatarProvider
→ 公司 PHP /internal/v1/avatar API
→ AvatarProviderInterface → 真实供应商
→ 异步任务 → MP4/FFprobe 验证 → 本地视频结果
```

重要文件：

- `app.py`：页面配置、顶部导航、会话初始化和运行能力提示。
- `app_pages/`：候选检索、视频音轨转文案、数字人生成、任务记录四个 UI 页面。
- `src/models.py`：统一领域模型与枚举。
- `src/contracts.py`：采集器和仓储协议。
- `src/repositories/sqlite.py`：当前默认仓储；`mock.py` 保留给测试和演示降级。
- `src/services/`：候选、热度、转写、数字人、文案、视频编辑、发布和流水线服务（14 个模块）。
- `src/adapters/avatar.py`：公司 PHP 内部 API 客户端；安全 GET 最多重试一次，付费提交不自动重发。
- `src/resources.py`：FFmpeg 能力检测及 ASR 资源预留。
- `src/asr_quality.py`：ASR 参考文本清洗、CER、数字准确率和词汇命中评测。
- `scripts/benchmark_asr.py`：对同一媒体、模型和参考文本执行可重复的本地基准。
- `docs/asr-benchmark-2026-07-18.md`：当前单样本实验、限制、模型选择和完整服务链路证据。
- `tests/`：领域、仓储、服务、重试与 Streamlit 页面测试。
- `docs/archive/video-intelligence-production-dev-doc-full-v1.6.md`：完整产品、技术、商业和合规设计。

## 6. 关键决策与理由

- 首期固定使用 Streamlit，优先快速获得可见、可验证结果。
- 页面目录使用 `app_pages/` 和 `st.Page + st.navigation`，避免旧式 `pages/` 自动发现冲突。
- 数字人页采用供应商无关模型与服务；配置不完整时保持禁用。真实提交使用独立服务令牌和幂等键，不嵌入旧站、不走旧会员积分，也不提供 Mock 成功视频。
- 真实转写的“准确率优先”使用本地 `large-v3-turbo/int8`，“快速预览”使用 `base/int8`；首次识别时按需下载模型权重。
- 准确率模式不传热词或 initial prompt，不做智能改写；全局热词实验导致漏句，只有新的分段方案通过人工真值 A/B 后才可启用。
- 模型是跨行业通用底座。行业适配优先通过租户隔离词库与可复核纠错实现，不把单条二手车样本描述为行业训练。
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

Windows 直接双击仓库根目录的 `start.bat`。它会检查并安装项目依赖、启动 React/FastAPI 服务、等待健康检查通过并打开默认浏览器。

默认地址：`http://127.0.0.1:8501/`。CSV、手工导入与本地转写不需要平台密钥；抖音关键词搜索需配置应用凭证。

安装开发依赖并验证：

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m compileall -q app.py app_pages src scripts tests
```

最近一次验证结果（2026-07-23）：FastAPI `/health`、83 条 OpenAPI 路径和 17 个安全 GET 端点正常；后端 110 项测试通过，非 Streamlit 的核心 Python 421 项测试通过，前端 Vitest 7 项、TypeScript 和生产构建通过。业务目录 Ruff 与编译通过。SQLite 已升级到 v004，`integrity_check=ok`、`foreign_key_check` 无违规；621 条缺失候选的历史检查点已隔离到可恢复表，63 条有效检查点保留。Streamlit 8501 已废弃，未作为当前能力验收；未执行真实供应商生成、付费调用或真实平台发布。

## 8. 数据、密钥与外部依赖

- 不提交 `.env`、Streamlit Secrets、Cookie、数据库、媒体、模型权重或生成物。
- `packages.txt` 已声明云端系统依赖 `ffmpeg`；本机安装在 `%LOCALAPPDATA%\Programs\ffmpeg`，并已加入用户级 `PATH`。
- 数据模式通过 `VIDEO_LICENSED_PROVIDER_MODE` 配置，默认 `sandbox`；真实供应商密钥只能通过环境变量、Streamlit Secrets 或 Windows 凭证管理器提供，数据库只保存凭证别名。
- Streamlit 数字人配置为 `AVATAR_SERVICE_ENABLED`、`AVATAR_SERVICE_BASE_URL`、`AVATAR_SERVICE_TOKEN`、`AVATAR_SERVICE_TIMEOUT_SECONDS`、`AVATAR_RESULT_TIMEOUT_SECONDS` 和可选 `AVATAR_RESULT_DIRECTORY`。
- PHP 内部服务配置以 `PHP_INTERNAL_AVATAR_*` 和既有 `PHP_MODEL_GATEWAY_LEGACY_*` 环境变量提供；只记录变量名，不记录令牌、API code、资产 ID 或供应商密钥值。
- 抖音旧接口还需显式设置 `DOUYIN_OFFICIAL_VERIFIED=true` 才可能启用，仓库不保存真实凭证。
- 云端 ASR 尚未配置。预留或待评估的密钥名称包括 `TENCENT_ASR_APP_ID`、`TENCENT_SECRET_ID`、`TENCENT_SECRET_KEY`、`VOLCENGINE_APP_ID`、`VOLCENGINE_ACCESS_TOKEN` 和 `OPENAI_API_KEY`；只记录名称，不得把值写入仓库或交接文档。
- `references/github/` 仅作本地阅读参考，已在 `.gitignore` 中排除。

## 9. 阻塞项与未决问题

离线沙箱、转写框架和数字人适配层没有代码阻塞项。进入真实数字人生成或三平台商业数据验收前存在以下待办：

- FFmpeg 8.1.2 已安装并通过 `ffmpeg -version` 与 `ffprobe -version` 验证。
- 尚未取得新榜或数说故事的沙箱文档、固定响应、报价、API 凭证和 B 端商业授权条款。
- 未完成真实适配器与小流量验收前，候选检索只能使用演示数据、客户自有数据、手工链接或 CSV；本地转写可使用已授权媒体。
- 转写准确率改进缺少跨行业、人工逐字校准的授权样本；没有该真值集时只能做相对筛选，不能验收真实 CER、专有词准确率或漏句率。
- 云端 ASR 没有供应商账号、凭证和预算授权，因此尚不能进行真实调用或与本地模型比较。
- 数字人真实供应商文档、合法凭证、形象/音色资产 ID、报价和结果域名白名单尚未写入部署环境；缺少这些值时页面按设计保持禁用。
- 当前机器没有 PHP CLI，PHP 控制器、迁移和假供应商契约仅完成静态验证；应在公司原生 PHP 7.4/Apache/MySQL 环境运行 `php -l`、契约脚本、迁移和带 Bearer 的 HTTP 检查。

## 10. 下一步与验收标准

**当前第一优先项：在公司原生 PHP 7.4/Apache/MySQL 环境配置真实供应商与一组已授权形象/音色，完成内部 API 和 Streamlit 的首次沙箱端到端生成。**

实施内容：

1. 在原生 MySQL 执行 `deploy/migrations/20260718_add_internal_avatar_jobs.sql`，配置 Apache Authorization 透传和 `/internal/v1/avatar/*` 内网访问限制。
2. 按公司供应商文档填写旧网关或新 `AvatarProviderInterface` adapter 所需环境变量；内部 Bearer 令牌和供应商密钥不得进入仓库。
3. 配置至少 1 个已授权数字人形象、1 个已授权音色和结果 HTTPS 域名白名单，先以 `sandbox` 模式调用能力、资产、提交、状态和结果接口。
4. 使用一段已批准成稿提交一次真实任务，验证幂等键重复提交不重复计费、页面刷新后任务可恢复、结果通过 FFprobe 并出现在任务记录。
5. 核对供应商账单、生成耗时、AI 内容标识、肖像/声音授权和失败错误映射后，再决定是否开启生产模式。
6. 跨行业人工真值集和企业词库仍是转写产品线的下一优先项；三平台真实商业数据继续保持授权前只用离线沙箱、手工链接或 CSV。

数字人首次沙箱验收标准：

- 未带令牌返回 401；配置缺失时 `enabled=false` 且不调用供应商。
- 同一幂等键与同一请求只产生一个内部任务和一次供应商提交；不同请求复用同一键返回冲突。
- 页面只展示已授权资产，三项授权缺一不可；批准版本与手工文案均可形成不可变任务快照。
- 排队、运行、成功、失败和结果未知状态都能刷新后恢复，不自动重复付费 POST。
- 成功视频通过大小、MIME、MP4 签名与 FFprobe 检查并写入受控目录，任务记录可播放。
- Python 全量检查、PHP 语法/契约脚本、MySQL 迁移、内部 HTTP 和至少一次授权沙箱生成全部通过。
- 数据库、密钥、媒体和生成物不进入 Git；未实际验收的能力不得对外描述为已上线。
