# 短视频热点洞察与智能生产系统

面向品牌营销团队的短视频热点发现、候选管理与授权音视频转写 MVP。

## 当前阶段

项目已完成 Streamlit 三页业务框架，并接入 SQLite 候选库、三平台商业 API 统一网关、离线沙箱、费用保护、平台独立热门排名和真实本地转写。首次启动会写入演示种子数据；在取得合规供应商文档、凭证和商业授权前，三平台一键查询只运行离线演示，不访问真实平台。首期聚焦：

- 手工链接、CSV/Excel 导入与统一候选字段
- 热度计算、快照与排序
- 抖音、小红书、微信视频号三平台批次、缓存、配额和独立 Top 10
- 用户上传有权处理的媒体
- FFmpeg 音频提取与 faster-whisper 转写
- 校对版本持久化，以及确认成稿后导出 TXT、JSON、SRT
- Streamlit 三页 Web MVP

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

生产模式不会因为填写供应商名称而自动启用。必须先完成供应商适配器、沙箱契约测试、B 端商业授权和小流量验收。系统对相同请求执行 60 秒数据库级防重，成功结果缓存 10 分钟；本月 360 次预警、450 次硬停止。费用未知时只显示“按供应商账户结算”，不会显示免费或 0 元。连接故障最多重试一次，401/403/429 不重试，响应状态不明确时锁定该请求等待人工核对。

平台内热门分使用年龄归一化互动、供应商召回名次、新鲜度以及可信增长或持续入榜；缺失字段保持 `null` 并按可见权重重新归一化。历史池或增长快照不足时只显示“爆火候选排名”，不输出正式 S/A/B。分享页只记录元数据，不自动下载媒体。

CSV/XLSX 和手工入口支持 `douyin`、`xiaohongshu`、`wechat_channels`（也接受中文平台名）。抖音/小红书必须提供匹配平台常见域名的公开 URL；视频号可提供公开 URL，或同时提供 `feed_id` 与 `finder_user_name`。缺失互动指标保持空值，不伪造为 0。

转文案只接受用户主动上传且确认有权处理的 MP4、MOV、M4A、MP3、WAV，单文件不超过 50MB、15 分钟。系统用 FFmpeg 在随机临时目录提取 16kHz 单声道音频，再用本机 `faster-whisper base/int8` 识别；原媒体与临时音频在成功或失败后均清理。转写结果先保存为待校对版本，只有“确认成稿”的版本才能导出。

开发检查使用 `python -m pip install -r requirements-dev.txt` 和 `python -m pytest`。当前商业 API 网关增量验证为 67 项测试通过。
