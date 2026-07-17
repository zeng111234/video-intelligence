# 短视频热点洞察与智能生产系统

面向品牌营销团队的短视频热点发现、候选管理与授权音视频转写 MVP。

## 当前阶段

项目已完成 Streamlit 三页业务框架，并接入 SQLite 候选库、抖音关键词候选、复采计划、可解释热门排名和真实本地转写。首次启动会写入演示种子数据，之后可通过官方接口、CSV/XLSX 或手工链接导入真实元数据。首期聚焦：

- 手工链接、CSV/Excel 导入与统一候选字段
- 热度计算、快照与排序
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
- 不提交密钥、Cookie、本地数据库、上传媒体、模型文件或生成物。
- `references/github/` 仅作本地阅读参考，不纳入本仓库版本控制。

## 本地启动

Windows 用户可以直接双击项目根目录的 `打开短视频系统.cmd`。启动文件会检查依赖、启动隐藏的 Streamlit 服务，并用默认浏览器打开网站。

也可以通过 PowerShell 手动启动：

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

页面顶部已提供低调用量关键词入口。每次手动点击固定使用一次综合排序搜索，可选择召回 1–10 条候选，默认 10 条；即使平台返回下一页也不会继续分页。系统保存近 7 天历史、平台召回位置和点赞快照，再用自有公式输出 Top 10。未配置凭证时获取按钮保持禁用且不会发起网络请求；获得抖音开放平台应用权限后，可复制 `.streamlit/secrets.toml.example` 为 `.streamlit/secrets.toml` 并填写：

```toml
DOUYIN_CLIENT_KEY = "你的 ClientKey"
DOUYIN_CLIENT_SECRET = "你的 ClientSecret"
```

也可使用同名环境变量。应用通过官方 `client_token` 接口获取访问令牌，并调用 `aweme.dy.video_search_v2`。召回时间范围可选近 24 小时或近 7 天，默认近 24 小时。关键词趋势分使用点赞增长、年龄归一化点赞、平台综合名次、新鲜度和持续入榜次数；疑似异常只降权并标记待核验，不自动删除。官方接口当前可见指标只映射点赞数，因此历史池少于 30 条或缺少两小时间隔快照时只显示“观察排名”。分享页只记录元数据，不自动下载媒体。

转文案只接受用户主动上传且确认有权处理的 MP4、MOV、M4A、MP3、WAV，单文件不超过 50MB、15 分钟。系统用 FFmpeg 在随机临时目录提取 16kHz 单声道音频，再用本机 `faster-whisper base/int8` 识别；原媒体与临时音频在成功或失败后均清理。转写结果先保存为待校对版本，只有“确认成稿”的版本才能导出。

开发检查使用 `python -m pip install -r requirements-dev.txt` 和 `python -m pytest`。
