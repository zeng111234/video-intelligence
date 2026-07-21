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
