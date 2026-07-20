# ASR 语音识别配置指南

## 当前状态

系统当前运行在 **演示模式 (sandbox)**，返回模拟数据。

## 启用真实语音识别

### 方式一：本地模式 (推荐)

使用 faster-whisper 进行本地语音识别，无需联网。

#### 1. 安装 faster-whisper

```bash
pip install faster-whisper
```

#### 2. 安装 FFmpeg

**Windows:**
```bash
# 使用 winget
winget install ffmpeg

# 或使用 chocolatey
choco install ffmpeg

# 或手动下载
# 访问 https://ffmpeg.org/download.html
# 解压后添加到 PATH 环境变量
```

**Linux/macOS:**
```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

#### 3. 设置环境变量

```bash
# Windows PowerShell
$env:ASR_MODE = "local"

# Linux/macOS
export ASR_MODE=local
```

或创建 `.env` 文件：
```
ASR_MODE=local
```

#### 4. 重启后端服务

```bash
# 停止现有服务
# 重新启动
python -m uvicorn project.backend.app.main:app --host 0.0.0.0 --port 2001
```

### 方式二：云端模式

使用阿里云 ASR 服务。

#### 1. 获取阿里云凭证

- 访问 https://nls-portal.console.aliyun.com/
- 创建项目，获取 AccessKey 和 AppKey

#### 2. 设置环境变量

```bash
$env:ASR_MODE = "cloud"
$env:ASR_CLOUD_PROVIDER = "aliyun"
$env:ALIYUN_ASR_ACCESS_KEY_ID = "your-key-id"
$env:ALIYUN_ASR_ACCESS_KEY_SECRET = "your-key-secret"
$env:ALIYUN_ASR_APP_KEY = "your-app-key"
```

## 验证配置

启动后端后，访问 http://localhost:2001/api/v1/transcriptions/config 查看当前配置：

```json
{
  "asr_mode": "local",
  "supports_upload": true,
  "description": "本地模型 —— 使用 faster-whisper 识别"
}
```

## 模型选择

本地模式支持以下模型：

| 模型 | 大小 | 速度 | 准确率 | 推荐场景 |
|------|------|------|--------|----------|
| base | ~150MB | 快 | 一般 | 快速预览 |
| medium | ~500MB | 中 | 较好 | 日常使用 |
| large-v3-turbo | ~1.5GB | 慢 | 最佳 | 准确率优先 |

默认使用 `base` 模型，可在前端页面选择其他模型。

## 故障排除

### Q1: 提示 "faster-whisper 未安装"

```bash
pip install faster-whisper
```

### Q2: 提示 "FFmpeg 未安装"

```bash
# 检查 FFmpeg
ffmpeg -version

# 如果未安装，参考上方安装步骤
```

### Q3: 模型下载失败

faster-whisper 模型会自动下载，如果网络问题导致失败：

```bash
# 使用镜像源
export HF_ENDPOINT=https://hf-mirror.com
python -c "from faster_whisper import WhisperModel; model = WhisperModel('base')"
```

### Q4: 内存不足

使用 `base` 模型（最小）或升级服务器内存。
