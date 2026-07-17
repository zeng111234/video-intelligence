# 短视频热点洞察与智能生产系统

面向品牌营销团队的短视频热点发现、候选管理与授权音视频转写 MVP。

## 当前阶段

项目已完成首个可点击 Streamlit 框架，当前以 Mock 数据演示三页业务闭环。首期聚焦：

- 手工链接、CSV/Excel 导入与统一候选字段
- 热度计算、快照与排序
- 用户上传有权处理的媒体
- FFmpeg 音频提取与 faster-whisper 转写
- TXT、JSON、SRT 与低置信度校对结果导出
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

当前框架不依赖本地 FFmpeg、Whisper 或真实平台 API；页面中的候选、转写和任务记录均明确标记为 Mock。

开发检查使用 `python -m pip install -r requirements-dev.txt` 和 `python -m pytest`。
