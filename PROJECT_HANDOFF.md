# 爆火视频采集与音视频转写 Web MVP - 项目交接文档

## 1. 交接目的

本文件是新 GPT/Codex 接手项目时的首要上下文。开始任何规划或开发前，依次阅读：

1. 本文件。
2. `video-intelligence-production-dev-doc.md` 中与当前任务相关的章节。
3. 工作区或用户消息中的 `AGENTS.md` 约束。

当前工作区只有设计文档，尚未创建应用代码。不要把完整远期方案一次性实现。

## 2. 当前真实目标

第一阶段只做一个可扩展的网页 MVP：

```text
爆火候选采集/导入
→ 互动指标快照
→ 热度排序
→ 用户选择并上传有权处理的视频
→ FFmpeg 提取音频
→ faster-whisper 转成文字、JSON 和 SRT
```

数字人、文案改写、自动剪辑均为后续扩展，不阻塞当前 MVP。

网页模式是确定需求。首期使用 Streamlit，不先做 React、FastAPI 微服务、Docker 或云端任务集群。

## 3. 用户与环境约束

- 开发人员：用户本人 + Codex。
- 开发方式：vibe coding，优先快速得到可见结果。
- 开发期现金成本：尽量为 0。
- 当前系统：Windows 10、Python 3.12、Node 24、Git、Docker 已安装，FFmpeg 尚需安装。
- 当前机器：Ryzen 7 5800H、约 14GB 内存、GTX 1650 4GB。
- 适合：Streamlit、SQLite、FFmpeg、`faster-whisper tiny/base int8`、少量串行浏览器任务。
- 不适合：大型本地数字人模型、多任务并发、重型 Docker 服务和大规模全站采集。
- 网络/API/工具因连接问题失败时最多自动重试一次，然后明确报告，不循环重连。
- 初期不需要独立域名；GitHub 保存代码，Streamlit Community Cloud 可提供轻量演示链接。

## 4. MVP 页面

### 4.1 爆火视频检索页

输入：平台、关键词、采集数量、时间范围、最低互动量。

输出字段：标题、作者、发布时间、来源链接、点赞、评论、收藏、分享、播放、粉丝量、采样时间、数据来源、置信度、有效互动、热度分和热度等级。不可得字段保持 `null`，不得写成 0。

操作：排序、保存候选、导出 CSV、刷新指标、选择视频进入转写页。

### 4.2 音视频转文案页

输入：用户上传的 MP4、MOV、MP3 或 WAV。

```text
媒体探测
→ FFmpeg 转 16kHz 单声道 WAV
→ faster-whisper base/tiny + int8 + VAD
→ 低置信度片段标记
```

输出：

```text
speech.wav
transcript.txt
transcript.json
subtitles.srt
review_items.json
```

页面需要支持在线校正文案和下载结果。

### 4.3 任务记录页

记录搜索任务、指标快照、转写状态、耗时、输出文件和明确错误信息。

## 5. 数据来源策略

优先级：

1. 抖音官方热门榜、审核品牌关键词、客户授权账号 API。
2. 用户手工粘贴链接和指标、CSV/Excel 导入。
3. 小红书手工导入或未来的合规数据供应商。
4. 非官方网页采集器只用于本地研究 POC，生产环境默认关闭。

禁止把 Cookie 池、代理池、验证码绕过、签名破解、去水印和全站批量下载作为正式产品能力。

GitHub 项目判断：

- `NanmiCoder/MediaCrawler`：功能最接近多平台采集，但许可证限定非商业学习。可学习架构、字段和本地验证，不复制为商业核心。
- `ReaJason/xhs`：MIT，但调用非官方小红书 Web 接口；代码许可证不代表平台授权。
- `SYSTRAN/faster-whisper`：MIT，首期正式采用。
- `microsoft/playwright-python`：Apache-2.0，可作为本地研究适配器的浏览器基础。
- `TikTokDownload` 类项目：即使代码为 MIT，也不把去水印或批量下载能力放入产品。

## 6. 热度规则

```text
有效互动 EI = 点赞 + 3×评论 + 4×分享 + 4×收藏
互动率 ER = EI / max(播放量, 1)
账号穿透率 APR = 播放量 / max(粉丝量, 1)
增长速度 GV = (本次 EI - 上次 EI) / 间隔小时
```

不同平台、赛道、发布时间和账号规模分桶比较。缺字段时重新归一化并降低置信度。

首期阈值：

- S：总分不低于 85、赛道分位不低于 P99、增长分位不低于 P95、EI 不低于 1,000。
- A：总分不低于 75、赛道分位不低于 P95、增长分位不低于 P90、EI 不低于 500。
- B：总分不低于 65、赛道分位不低于 P90、增长分位不低于 P80、视频年龄不超过 24 小时。
- 对比桶少于 100 条时标记 `provisional`。
- 只有单次快照时显示“静态高热”，不声称具有增长动能。

建议采样时间为 `T0、T+2h、T+6h、T+24h`。

## 7. 合规和产品边界

- 候选发现与媒体处理分离。发现公开链接不代表取得媒体处理权。
- 正式转写只处理用户上传、客户自有账号或明确授权的媒体。
- 完整转写默认私有，不建立公开的竞品文案库。
- 原始音频和临时文件设置自动清理策略。
- 后续数字人只使用审核后的新文案，不克隆参考主播脸和声音。
- 开源代码许可证不等于平台数据授权，也不等于视频、声音和文案版权授权。

## 8. 推荐代码结构

```text
video-web/
├── app.py
├── pages/
│   ├── 1_爆火视频检索.py
│   ├── 2_音视频转文案.py
│   └── 3_任务记录.py
├── src/
│   ├── crawlers/
│   │   ├── base.py
│   │   ├── manual.py
│   │   ├── douyin.py
│   │   └── xiaohongshu.py
│   ├── services/
│   │   ├── crawl_service.py
│   │   ├── heat_service.py
│   │   └── transcription_service.py
│   ├── media/ffmpeg.py
│   └── repositories/sqlite.py
├── data/
├── uploads/
├── outputs/
├── tests/
├── requirements.txt
├── packages.txt
└── .gitignore
```

统一采集器接口：

```python
class CrawlerAdapter:
    def search(self, keyword: str, limit: int): ...
    def get_detail(self, url: str): ...
    def refresh_metrics(self, item_id: str): ...
```

页面不得直接依赖某个平台的返回字段，所有数据先进入统一模型。

## 9. 本地与在线模式

### 本地完整版

- Streamlit 页面。
- 手工导入和可选浏览器研究适配器。
- 本地 FFmpeg 和 faster-whisper。
- SQLite 和本地文件。

运行目标：

```powershell
streamlit run app.py
```

### Streamlit Cloud 轻量版

- 支持 CSV/链接导入、热度展示、小文件上传和轻量 ASR。
- 不依赖扫码登录、持久 Cookie 或长时间 Playwright 会话。
- `packages.txt` 安装 `ffmpeg`。
- 上传、SQLite 和输出均视为临时数据，结果允许用户下载。

## 10. 建议实施顺序

1. 建立 Streamlit 三页骨架、配置和 `.gitignore`。
2. 完成 SQLite 数据模型与 `ManualImportAdapter`。
3. 完成 FFmpeg 探测、音频提取和 faster-whisper 转写。
4. 完成 TXT、JSON、SRT、低置信度片段和网页校正。
5. 完成热度公式、快照和榜单。
6. 再决定第一个平台适配器；未明确时默认抖音。
7. 最后做 Streamlit Cloud 降级部署。

不要在第一个可运行版本中加入数字人、React、FastAPI、Docker、Redis、PostgreSQL、付费 API 或多用户鉴权。

## 11. MVP 验收

- 网页可以导入至少 20 条候选数据并按热度排序。
- 同一作品可保存多次指标快照并去重。
- 10 条授权口播视频中至少 9 条完成转写。
- 60 秒清晰普通话视频的人工校对时间不超过 5 分钟。
- 输出 TXT、JSON 和 SRT，可在页面下载。
- 连接失败最多自动重试一次，错误对用户可见。
- 未授权媒体、密钥、上传文件和本地数据库不提交到 GitHub。

## 12. 当前未决问题

- 第一个自动采集平台尚未由用户最终确认；默认建议抖音。
- 抖音官方数据权限是否已有企业主体和开发者账号尚未确认。
- 在官方权限未获批前，自动采集只能作为本地研究适配器，正式演示使用 Mock、手工链接或 CSV。

## 13. 下一位 GPT/Codex 的工作原则

- 先检查工作区真实文件，不假设代码已经存在。
- 用户要求规划时只更新计划；用户明确要求开发时才创建应用代码。
- 每次只完成一个可验证的纵向切片。
- 优先使用维护活跃且许可证清晰的基础组件。
- 修改后运行与风险相匹配的测试；没有测试时至少验证导入、页面启动和一个真实短视频转写样例。
- 保留用户已有修改，不执行破坏性 Git 操作。

