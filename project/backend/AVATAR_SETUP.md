# 数字人视频生成配置指南

## 当前状态

系统当前运行在 **演示模式**，返回模拟任务和进度。

## 启用真实数字人生成

### 方案一：Duix-Avatar（推荐）

开源 AI 数字人工具包，支持离线视频生成和数字人克隆。

**GitHub**: https://github.com/duixcom/Duix-Avatar (14k stars)

#### 特点
- 完全开源，可离线运行
- 支持数字人克隆
- 支持视频生成
- 活跃的社区维护

#### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/duixcom/Duix-Avatar.git
cd Duix-Avatar

# 安装依赖
pip install -r requirements.txt

# 下载模型
python download_models.py
```

#### 集成方式

1. 启动 Duix-Avatar 服务
2. 在 `.env` 中配置：
```
AVATAR_SERVICE_ENABLED=true
AVATAR_SERVICE_BASE_URL=http://localhost:8080
```

3. 修改 `project/backend/app/api/v1/avatar.py`，调用真实服务

### 方案二：Linly-Talker

中文数字人对话系统，集成 SadTalker。

**GitHub**: https://github.com/Kedreamix/Linly-Talker (3.4k stars)

#### 特点
- 中文优化
- 集成 SadTalker 唇形同步
- 支持多种 TTS 引擎
- 支持实时对话

#### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/Kedreamix/Linly-Talker.git
cd Linly-Talker

# 安装依赖
pip install -r requirements.txt

# 下载模型
python download_models.py
```

### 方案三：SadTalker

专注于唇形同步的说话头像生成。

**GitHub**: https://github.com/OpenTalker/SadTalker

#### 特点
- 单张照片 + 音频 → 视频
- 唇形同步效果好
- 轻量级

#### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/OpenTalker/SadTalker.git
cd SadTalker

# 安装依赖
pip install -r requirements.txt

# 下载模型
bash scripts/download_models.sh
```

## API 接口

当前已实现的 API 接口：

### 生成视频
```
POST /api/v1/avatar/generate
Content-Type: application/json

{
  "avatar_type": "image",
  "audio_type": "tts",
  "tts_text": "大家好，欢迎观看今天的视频",
  "tts_voice": "sweet_female",
  "speech_rate": 1.0
}
```

### 上传形象
```
POST /api/v1/avatar/upload-avatar
Content-Type: multipart/form-data

file: <image-file>
```

### 上传音频
```
POST /api/v1/avatar/upload-audio
Content-Type: multipart/form-data

file: <audio-file>
```

### 查询任务状态
```
GET /api/v1/avatar/tasks/{task_id}
```

### 获取音色列表
```
GET /api/v1/avatar/voices
```

## TTS 集成

### 方案一：Edge TTS（推荐）

免费、高质量的 TTS 服务。

```bash
pip install edge-tts
```

使用示例：
```python
import edge_tts
import asyncio

async def generate_speech(text, voice="zh-CN-XiaoxiaoNeural", output="output.mp3"):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output)

asyncio.run(generate_speech("大家好，欢迎观看今天的视频"))
```

### 方案二：阿里云 TTS

需要阿里云账号和 API Key。

### 方案三：本地 TTS

使用 Coqui TTS 或其他本地 TTS 引擎。

```bash
pip install TTS
```

## 故障排除

### Q1: GPU 内存不足

- 使用 CPU 模式：设置 `device=cpu`
- 使用较小的模型
- 减少批处理大小

### Q2: 生成速度慢

- 使用 GPU 加速
- 使用较小的模型
- 降低输出分辨率

### Q3: 唇形不同步

- 确保音频清晰
- 使用 SadTalker 的最新版本
- 调整唇形同步参数
