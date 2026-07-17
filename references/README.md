# GitHub 参考仓库清单

更新时间：2026-07-17

外部仓库以浅克隆方式保存在 `references/github/`，该目录已被根目录 `.gitignore` 忽略。它们只用于阅读、验证和借鉴设计，不作为本项目的 vendored 源码。

## 已下载

| 仓库 | 本地目录 | 当前提交 | 许可证 | 本项目用途 | 采用边界 |
| --- | --- | --- | --- | --- | --- |
| [streamlit/blank-app-template](https://github.com/streamlit/blank-app-template) | `github/streamlit-blank-app-template` | `441112cda6ec` | Apache-2.0 | 查看官方最小应用、依赖和 Community Cloud 文件布局 | 作为脚手架参考，不复制无关配置 |
| [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | `github/faster-whisper` | `ed9a06cd89a9` | MIT | ASR 调用、VAD、词级时间戳、CPU `int8` 和测试方式 | 首期正式依赖；优先 PyPI 固定版本，不直接嵌入源码 |
| [microsoft/playwright-python](https://github.com/microsoft/playwright-python) | `github/playwright-python` | `55fa500341cd` | Apache-2.0 | Streamlit 页面端到端测试和未来本地研究适配器 | 不用于验证码绕过、Cookie 池、批量抓取或平台风控规避 |
| [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | `github/MediaCrawler` | `0625e01a6bc7` | 非商业学习许可证 1.1 | 研究多平台适配器分层、字段模型和本地 POC 组织方式 | 只能学习研究；不得复制为商业产品核心，不做大规模采集 |

## 已安装的 Codex skills

| Skill | 来源 | 用途 |
| --- | --- | --- |
| `developing-with-streamlit` | [streamlit/agent-skills](https://github.com/streamlit/agent-skills) | 按 Streamlit 官方模式实现页面、状态和数据展示 |
| `playwright` | [openai/skills](https://github.com/openai/skills) curated | 对本地 Streamlit 页面做浏览器验收 |
| `security-best-practices` | [openai/skills](https://github.com/openai/skills) curated | 检查上传、临时文件、路径、密钥与敏感数据边界 |

新安装的 skills 从下一轮 Codex 任务开始可用。

## 当前不下载

- `Breakthrough/PySceneDetect`：镜头切分属于后续内容拆解，不阻塞“候选导入 → 热度排序 → 授权视频转写”MVP。
- `ReaJason/xhs`：首个平台尚未最终确定，且它调用非官方小红书 Web 接口；若首个平台确定为小红书，再单独做合规 POC。
- `transcribe` skill：面向托管转写流程，与当前确定的本地 `faster-whisper` 路线重复。
- TikTok/抖音批量下载、去水印项目：不符合产品合规边界。

## 本轮采集与评分参考（不下载、不作为依赖）

| 仓库 | 许可证 | 借鉴内容 | 明确不采用 |
| --- | --- | --- | --- |
| [tiktok/tiktok-research-api-wrapper](https://github.com/tiktok/tiktok-research-api-wrapper) | MIT | 官方客户端的字段白名单、分页、限流和审计边界 | 不假定商业项目可取得 Research API 权限 |
| [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | 主体 Unlicense，发行物许可证需单独核对 | extractor 协议、元数据归一化、站点变更测试 | 下载、Cookie、代理和绕过限制能力 |
| [coleifer/sqlite-web](https://github.com/coleifer/sqlite-web) | MIT | 导入预览、事务、回滚和错误定位 | 任意 SQL 与不受控文件路径 |
| [xai-org/x-algorithm](https://github.com/xai-org/x-algorithm) | Apache-2.0 | 候选、过滤、多信号评分、Top-K 管线 | 直接复用平台权重或个性化特征 |
| [google-marketing-solutions/abcds-detector](https://github.com/google-marketing-solutions/abcds-detector) | Apache-2.0 | 后续授权媒体的可解释创意特征 | 将创意属性分数直接当作爆火概率 |

以上项目只记录设计参考，不克隆到 `references/github/`，也不新增为运行时依赖。

## 当前方向

保持既定首期切片：

1. Streamlit 三页骨架与 SQLite 数据模型。
2. 手工链接、CSV/Excel 导入与统一候选字段。
3. 热度公式、快照和排序。
4. 用户上传有权处理的媒体，FFmpeg 提取音频。
5. faster-whisper 输出 TXT、JSON、SRT 和低置信度校对项。
6. Playwright 验收核心页面路径。

第一个自动平台适配器继续保留为决策门：优先确认抖音官方数据权限；权限不足时只做本地研究适配器，正式演示使用 Mock、手工链接或 CSV。
