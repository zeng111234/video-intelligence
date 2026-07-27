# 数字人生成环境变量配置

客户版数字人页面只读取后端能力接口。真实供应商、资产 ID、音色 ID、结果目录和发布账号都通过 `.env` 配置，不需要改代码。

## 配置文件位置

后端启动时会自动读取：

1. 仓库根目录 `.env`
2. `project/backend/.env`

系统环境变量优先级最高，其次是根目录 `.env`，再其次是 `project/backend/.env`。

建议直接复制根目录 `.env.example` 为 `.env`，然后只填写你要启用的供应商。

## 本地演示模式

不产生费用，不生成真实成片，只验证页面、任务和轮询闭环。

```env
AVATAR_PROVIDER_MODE=sandbox
AVATAR_RESULT_DIRECTORY=data/avatar_results
```

## 百度曦灵真实生成

启用真实生成前，需要在百度曦灵开放平台取得应用凭证、可用人像 ID 和音色 ID。

```env
AVATAR_PROVIDER_MODE=baidu_xiling
AVATAR_RESULT_DIRECTORY=data/avatar_results

BAIDU_XILING_APP_ID=
BAIDU_XILING_APP_KEY=
BAIDU_XILING_FIGURE_ID=
BAIDU_XILING_VOICE_ID=
BAIDU_XILING_BASE_URL=https://open.xiling.baidu.com
BAIDU_XILING_FIGURE_NAME=百度曦灵公共数字人
BAIDU_XILING_VOICE_NAME=百度曦灵公共音色
BAIDU_XILING_CALLBACK_URL=
BAIDU_XILING_TRANSPARENT=false
BAIDU_XILING_TIMEOUT_SECONDS=20
BAIDU_XILING_MAX_SCRIPT_CHARS=20000
BAIDU_XILING_ESTIMATED_45S_COST_CNY=2.25
```

## 公司数影单 Key 云网关

从公司旧系统恢复出的协议使用一个 `api_code` 调用 `/video` 和
`/videoDetail`。当前 `aif` 线路没有 `/voice`，因此生产配置使用
Edge 云 TTS 生成音频，再通过公司原页面使用的上传接口取得公网音频地址。
旧环境若仍提供 `/voice`，可保留 `gateway_voice` 模式。

```env
AVATAR_PROVIDER_MODE=shuying_cloud
AVATAR_RESULT_DIRECTORY=data/avatar_results

SHUYING_AVATAR_ENABLED=true
SHUYING_AVATAR_BASE_URL=https://填写公司旧网关域名
SHUYING_AVATAR_API_CODE=
SHUYING_AVATAR_AVATARS_JSON=[{"asset_id":"填写数字人ID","name":"公司数字人","authorized":true}]
SHUYING_AVATAR_VOICES_JSON=[{"asset_id":"填写音色ID","name":"公司音色","authorized":true}]
SHUYING_AVATAR_RESULT_ALLOWED_HOSTS=填写结果文件域名
SHUYING_AVATAR_AUDIO_MODE=edge_tts_upload
SHUYING_AVATAR_AUDIO_UPLOAD_URL=https://填写公司音频上传接口
SHUYING_AVATAR_AUDIO_ALLOWED_HOSTS=填写音频文件域名
SHUYING_AVATAR_EDGE_TTS_VOICE=zh-CN-XiaoxiaoNeural
SHUYING_AVATAR_TIMEOUT_SECONDS=120
SHUYING_AVATAR_RESULT_TIMEOUT_SECONDS=120
SHUYING_AVATAR_MAX_SCRIPT_CHARS=2000
SHUYING_AVATAR_ESTIMATED_COST_CNY=
SHUYING_AVATAR_ESTIMATED_SECONDS=
```

安全约束：

- 网关必须使用证书有效的 HTTPS，Key 不会发送到 HTTP 或证书无效的地址。
- `edge_tts_upload` 不调用不存在的 `/aif/voice`；`gateway_voice` 只用于确实提供该接口的旧网关。
- `/video` 可能产生费用，提交连接失败时不会自动重发；任务会进入“待核对”。
- 音频上传结果只接受 `SHUYING_AVATAR_AUDIO_ALLOWED_HOSTS` 中列出的 HTTPS 域名。
- 下载只允许 `SHUYING_AVATAR_RESULT_ALLOWED_HOSTS` 中列出的 HTTPS 域名，并拒绝重定向。
- 配置缺少网关、Key、授权数字人、授权音色或结果域名时，页面保持禁用，不会偷偷回退到本地硬件。

## 内部数字人服务

仅当你要接公司自有数字人服务时使用。

```env
AVATAR_PROVIDER_MODE=internal
AVATAR_SERVICE_ENABLED=true
AVATAR_SERVICE_BASE_URL=
AVATAR_SERVICE_TOKEN=
AVATAR_SERVICE_TIMEOUT_SECONDS=20
AVATAR_RESULT_TIMEOUT_SECONDS=120
```

## 发布平台账号

未取得官方权限前保持为空。系统会走人工发布包或演示状态，不应伪造真实作品链接。

```env
PUBLISH_DOUYIN_ACCESS_TOKEN=
PUBLISH_DOUYIN_OPEN_ID=
PUBLISH_KUAISHOU_ACCESS_TOKEN=
PUBLISH_KUAISHOU_OPEN_ID=
PUBLISH_WECHAT_CHANNELS_ACCESS_TOKEN=
PUBLISH_WECHAT_CHANNELS_OPEN_ID=
PUBLISH_XIAOHONGSHU_ACCESS_TOKEN=
PUBLISH_XIAOHONGSHU_OPEN_ID=
```

## 当前 API

- `GET /api/v1/avatar/capabilities`
- `GET /api/v1/avatar/assets`
- `POST /api/v1/avatar/jobs`
- `GET /api/v1/avatar/jobs`
- `GET /api/v1/avatar/jobs/{task_id}`
- `GET /api/v1/avatar/jobs/{task_id}/media`

旧兼容入口 `POST /api/v1/avatar/generate` 仍保留，但新页面不再使用上传形象、上传音频和假下载逻辑。
