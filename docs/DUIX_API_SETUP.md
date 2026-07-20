# Duix-Avatar 云端 API 配置指南

## 1. 申请 API Key

### 步骤 1：访问 Duix 官网

打开 https://duix.com，注册账号。

### 步骤 2：获取 API Key

1. 登录后进入控制台
2. 找到「API 密钥」或「开发者」选项
3. 创建新的 API Key
4. 复制保存（注意：密钥只显示一次）

### 步骤 3：配置环境变量

编辑 `.env` 文件：

```bash
AVATAR_MODE=cloud
AVATAR_API_URL=https://api.duix.com
AVATAR_API_KEY=your-api-key-here
```

## 2. API 接口说明

### 文本转语音 (TTS)

```
POST {API_URL}/tts/v1/invoke
Authorization: Bearer {API_KEY}

{
  "speaker": "uuid",
  "text": "要转换的文本",
  "format": "wav",
  "voice_id": "sweet_female"
}
```

### 视频合成

```
POST {API_URL}/video/easy/submit
Authorization: Bearer {API_KEY}

{
  "audio_url": "音频地址",
  "video_url": "形象视频地址",
  "code": "任务ID"
}
```

### 查询进度

```
GET {API_URL}/video/easy/query?code={task_id}
Authorization: Bearer {API_KEY}
```

## 3. 定价参考

| 服务 | 价格 | 说明 |
|------|------|------|
| 数字人视频 | ¥0.5-2/条 | 根据时长和清晰度 |
| 语音克隆 | ¥50-100/次 | 一次性费用 |
| API 调用 | 按量计费 | 有免费额度 |

## 4. 替代方案

如果 Duix API 不适合，还可以考虑：

### HeyGen
- 官网：https://heygen.com
- 优点：效果好，支持多语言
- 缺点：价格较高

### D-ID
- 官网：https://d-id.com
- 优点：简单易用，API 友好
- 缺点：主要面向海外市场

### Synthesia
- 官网：https://synthesia.io
- 优点：企业级，效果专业
- 缺点：价格高，需要企业账号

## 5. 测试 API 连接

配置完成后，运行以下命令测试：

```bash
# 测试 API 连接
curl -X GET https://api.duix.com/health \
  -H "Authorization: Bearer your-api-key"

# 测试 TTS
curl -X POST https://api.duix.com/tts/v1/invoke \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"text":"测试语音","format":"wav"}'
```

## 6. 常见问题

### Q1: API Key 无效

- 检查 Key 是否复制完整
- 确认 Key 是否已激活
- 检查账户余额

### Q2: 请求超时

- 检查网络连接
- 尝试使用代理
- 联系 Duix 技术支持

### Q3: 视频质量不佳

- 使用高质量的形象视频
- 确保音频清晰
- 选择更高的清晰度参数

## 7. 技术支持

- Duix 官网：https://duix.com
- 文档：https://docs.duix.com
- 邮箱：james@duix.com
- GitHub：https://github.com/duixcom/Duix-Avatar
