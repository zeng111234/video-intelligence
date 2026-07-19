# 短视频热点洞察与智能生产系统

面向品牌营销团队的短视频热点发现、候选管理与授权视频音轨转文案 MVP。

## 当前阶段

项目已完成 **短视频热点洞察与批量生产系统** 的全链路搭建，包含：
1.  **热点发现**：三平台（抖音、小红书、视频号）商业 API 统一网关、热度计算与候选管理。
2.  **内容生产**：视频音轨转文案、文案改写引擎、数字人生成、AI 视频剪辑（FFmpeg 流水线）。
3.  **一键发布**：多平台发布适配器（支持沙箱与生产模式）。
4.  **系统韧性**：统一重试策略（退避与预算保护）、上下文资源监控、`outcome_unknown` 状态管理入口。
5.  **端到端流水线**：从关键词输入到视频发布的全流程自动化编排（`PipelineService`）。

已实现功能亮点：
- **智能重试**：所有外部调用（API、转写、数字人）统一使用 `retry_with_policy`，支持指数退避与超时预算。
- **资源保护**：新增 `ContextBudget` 模块，限制并发任务数与会话内存，防止 OOM。
- **管理后台**：新增“系统管理”页面，支持查看阻断任务、手动解除 `OUTCOME_UNKNOWN` 锁定。
- **AI 剪辑**：支持字幕嵌入、水印、裁剪、变速等步骤的自动化视频处理。
- **多平台发布**：支持抖音、快手、视频号的一键发布，具备状态追踪与回滚能力。

详细范围与约束见：

- [项目交接文档](PROJECT_HANDOFF.md)
- [产品与开发文档](docs/archive/video-intelligence-production-dev-doc-full-v1.6.md)
- [外部参考仓库说明](references/README.md)

## 协作方式

1. 从 `main` 创建短生命周期分支，例如 `feature/transcription-page`。
2. 在自己的分支完成改动并做本地验证。
3. 通过 Pull Request 合并到 `main`，避免直接在 `main` 上并行开发。
4. PR 说明应包含改动内容、验证方式和已知风险。

更具体的分支、提交和评审约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 数据与合规边界

- 只处理用户有权使用的媒体与数据。
- 三平台自动搜索只允许接入具有合同、数据来源说明和 B 端商业使用许可的供应商。
- 真实供应商未验收时只使用离线沙箱；小红书和视频号不会降级为登录态自动化或逆向采集。
- 抖音旧关键词适配器默认关闭，只有 OAuth、Scope 和真实调用均验收后才可启用。
- 不使用 Cookie、代理池、验证码/签名绕过、隐藏接口、去水印或自动媒体下载。
- 不提交密钥、Cookie、本地数据库、上传媒体、模型文件或生成物。
- `references/github/` 仅作本地阅读参考，不纳入本仓库版本控制。

## 本地启动

Windows 用户可以直接双击项目根目录的 `打开短视频系统.cmd`。启动文件会检查依赖、启动隐藏的 Streamlit 服务，并用默认浏览器打开网站。

也可以通过 PowerShell 手动启动：

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

首页提供“三平台一键查爆款”：默认近 7 天、每个平台 10 条，抖音、小红书和微信视频号分别形成独立 Top 10，不生成未经校准的跨平台总榜。默认配置为离线沙箱，按钮和所有结果都会明确标记“演示数据”，外部调用数为 0。可复制 `.streamlit/secrets.toml.example` 为 `.streamlit/secrets.toml`：

```toml
VIDEO_LICENSED_PROVIDER_MODE = "sandbox"
VIDEO_LICENSED_PROVIDER_NAME = ""

DOUYIN_CLIENT_KEY = "你的 ClientKey"
DOUYIN_CLIENT_SECRET = "你的 ClientSecret"
DOUYIN_OFFICIAL_VERIFIED = "false"
```

公司数字人服务单独使用环境变量，不把令牌写入 Streamlit Secrets 或 Git：

```powershell
$env:AVATAR_SERVICE_ENABLED="true"
$env:AVATAR_SERVICE_BASE_URL="http://127.0.0.1:8080"
$env:AVATAR_SERVICE_TOKEN="与 PHP_INTERNAL_AVATAR_SERVICE_TOKEN 相同的随机令牌"
$env:AVATAR_SERVICE_TIMEOUT_SECONDS="20"
$env:AVATAR_RESULT_TIMEOUT_SECONDS="120"
```

PHP 服务只有在私有网关、API code、已授权形象/音色清单、结果域名白名单和内部令牌全部配置后才报告可用。提交接口不自动重发；提交响应丢失时任务标记为 `outcome_unknown`，随后只按原幂等键核对，避免重复计费。

生产模式不会因为填写供应商名称而自动启用。必须先完成供应商适配器、沙箱契约测试、B 端商业授权和小流量验收。系统对相同请求执行 60 秒数据库级防重，成功结果缓存 10 分钟；本月 360 次预警、450 次硬停止。费用未知时只显示“按供应商账户结算”，不会显示免费或 0 元。连接故障最多重试一次，401/403/429 不重试，响应状态不明确时锁定该请求等待人工核对。

平台内热门分使用年龄归一化互动、供应商召回名次、新鲜度以及可信增长或持续入榜；缺失字段保持 `null` 并按可见权重重新归一化。历史池或增长快照不足时只显示“爆火候选排名”，不输出正式 S/A/B。分享页只记录元数据，不自动下载媒体。

CSV/XLSX 和手工入口支持 `douyin`、`xiaohongshu`、`wechat_channels`（也接受中文平台名）。抖音/小红书必须提供匹配平台常见域名的公开 URL；视频号可提供公开 URL，或同时提供 `feed_id` 与 `finder_user_name`。缺失互动指标保持空值，不伪造为 0。

“视频音轨转文案”可独立使用，不必等待爆火候选接口。用户可以上传确认有权处理的 MP4/MOV，或填写直接返回 MP4/MOV 文件的授权 HTTPS 公网直链；候选关联仅用于追溯。直链会拒绝本机、内网、保留地址、非标准端口、非视频响应与超过 50MB 的文件，连接故障最多重试一次。抖音、小红书、视频号等平台分享页不属于视频直链，系统不会使用非授权下载器抓取，需改为主动上传视频。

不接受单独音频文件，单文件不超过 50MB、15 分钟。系统先用 FFprobe 验证视频包含音轨且时长为有限正数，再由 FFmpeg 在随机临时目录提取 16kHz 单声道音频，最后使用所选本地 `faster-whisper` 模型和 CPU INT8 识别。系统不做抽帧或画面分析；上传原视频、直链读取内容与临时音频在成功或失败后均清理，直链地址本身也不写入任务。

本地准确率模式已用同一条 100 秒视频做相对基准：无提示的 `large-v3-turbo` 相比原 `base`，参考 CER 从 25.34% 降至 8.54%。参考文本来自第三方自动转写而非人工真值，因此该数字只用于方案筛选，不作为对外准确率承诺。热词实验出现漏句，当前页面不会启用。详细记录见 `docs/asr-benchmark-2026-07-18.md`。

页面同步显示文件检查、音频提取和语音识别的真实阶段，不提供后台任务或预计完成时间。识别结果以追加版本保存；低于 0.75 的片段在新确认成稿时必须人工勾选“已复核”，草稿可随时保存。下载严格使用任务 `approved_revision_id` 指向的成稿，支持 TXT、JSON、SRT；历史已批准版本保持可导出。

开发检查使用 `python -m pip install -r requirements-dev.txt`、`python -m ruff check .`、`python -m ruff format --check .` 和 `python -m pytest`。
