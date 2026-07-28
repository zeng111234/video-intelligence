# 阿里云轻量智能剪辑配置手册

适用范围：本项目的“轻量智能剪辑”真实云端出片链路。

目标链路为：上传素材到私有 OSS → Fun-ASR 识别 → qwen-flash 生成受约束方案 → 人工确认 → MPS 生成成片。

> 先看结论：`sandbox` 是免费流程体验，不调用真实识别或出片；把 `VIDEO_EDITOR_PROVIDER_MODE` 改成 `aliyun` 前，下面的 8 项必须全部就绪。缺一项时系统会如实阻止真实任务，不会假装成功。

## 0. 先准备什么

| 项目 | 是否收费 | 你最终需要拿到的内容 |
|---|---:|---|
| 阿里云百炼 | 按调用量收费 | API Key、业务空间 ID |
| OSS | 按存储和流量收费 | 北京 Bucket 名称 |
| RAM | 通常不单独计费 | 服务器专用 AccessKey ID / Secret |
| MPS | 按成片转码时长收费 | 标准管道 ID、720P 模板 ID、1080P 模板 ID |

所有资源都选择 **华北 2（北京）**。百炼北京地域的 API Key、业务空间专属域名和模型列表不能与其他地域混用；OSS 和 MPS 也必须同地域。[北京接入信息](https://help.aliyun.com/zh/model-studio/beijing-access-information)

不要做这些事：

- 不要创建主账号 AccessKey。
- 不要把任何 Key 发到聊天、截图、工单或 Git。
- 不要把 OSS Bucket 改成公共读；本项目会使用服务端签名临时地址。
- 不要填写旧的 `ALIYUN_ASR_ACCESS_KEY_ID`、`ALIYUN_ASR_ACCESS_KEY_SECRET`、`ALIYUN_ASR_APP_KEY`；它们不属于本剪辑链路。

## 1. 开通百炼并创建 API Key

1. 登录 [阿里云百炼控制台](https://bailian.console.aliyun.com/)。
2. 右上角选择 **华北 2（北京）**。
3. 在“业务空间管理”创建一个空间，建议名称：`video-editor-prod`。
4. 进入该空间，确认可以调用 `fun-asr` 和 `qwen-flash`。
5. 进入“密钥管理 / API Key”，单击“创建 API Key”。归属业务空间选择刚才的空间；说明可填“视频剪辑服务端”。
6. 如已知服务器出口公网 IP，设置 IP 白名单；模型权限只勾选 `fun-asr` 与 `qwen-flash`。测试阶段不确定出口 IP 时，可先不设白名单，确认跑通后立刻收紧。
7. 创建成功后立即复制 Key 到本机根目录 `.env` 的 `DASHSCOPE_API_KEY`。Key 明文只显示一次，丢失后只能重置或新建。
8. 在业务空间详情复制 API Host，例如 `llm-abc123.cn-beijing.maas.aliyuncs.com`；本项目只填写其中前缀 `llm-abc123` 到 `ALIYUN_MODEL_STUDIO_WORKSPACE_ID`，不要填完整 URL。

百炼 API Key 的创建、业务空间归属、模型/IP 限制和“只显示一次”的规则以[官方 API Key 文档](https://help.aliyun.com/zh/model-studio/get-api-key)为准。

## 2. 创建私有 OSS Bucket

1. 打开 [OSS 控制台](https://oss.console.aliyun.com/)。
2. 单击“创建 Bucket”。
3. Bucket 名称使用全小写、数字和连字符，例如 `video-editor-test-你的随机后缀`；名称需要全局唯一。
4. 地域选择 **华北 2（北京）**。
5. 存储类型先选“标准存储”；读写权限保持“私有”。
6. 保留“阻止公共访问”为开启状态。
7. 创建完成后复制 Bucket 名称，不要复制 Endpoint，也不要建立公开读 Bucket。

项目会将原视频和出片保存在同一私有 Bucket 中。当前版本不会自动删除临时文件；测试完成后再单独制定生命周期规则，避免误删待确认成片。

OSS 默认安全策略和私有 Bucket 说明见[官方 Bucket 创建文档](https://help.aliyun.com/zh/oss/developer-reference/create-a-bucket)。

## 3. 创建服务器专用 RAM 用户和 AccessKey

这一步是让服务器上传素材、读取签名地址、提交和查询 MPS 作业。它不是百炼 API Key，二者不能互换。

1. 打开 [RAM 控制台](https://ram.console.aliyun.com/)。
2. “身份管理 → 用户 → 创建用户”。登录名称建议填 `video-editor-server`。
3. 不需要给这个用户控制台登录密码；它只供服务器程序调用 API。
4. 创建后，进入该用户“权限管理”，绑定一个自定义策略。将下面 JSON 中的 `你的Bucket名称` 替换为第 2 步创建的 Bucket 名称。

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "oss:PutObject",
        "oss:GetObject"
      ],
      "Resource": [
        "acs:oss:*:*:你的Bucket名称/video-editor-input/*",
        "acs:oss:*:*:你的Bucket名称/video-editor-output/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "mts:SubmitJobs",
        "mts:QueryJobList"
      ],
      "Resource": "*"
    }
  ]
}
```

5. 点击 `video-editor-server` 这一行的**登录名称**（不要停留在 RAM 首页或“工作流”页），进入该用户的详情页。
6. 打开详情页上方的 **认证管理** 标签；部分旧版控制台将它显示为“凭证管理”。
7. 找到 **AccessKey** 区域，点击 **创建 AccessKey**。在安全提示中选择最符合“服务器程序调用 API”的使用场景（不同版本的文案会略有不同），勾选“我确认必须创建 AccessKey”，点击继续创建并完成安全验证。
8. 立即将 AccessKey ID 和 AccessKey Secret 填入根目录 `.env`；Secret 只显示一次。

```env
ALIBABA_CLOUD_ACCESS_KEY_ID=这里填AccessKeyID
ALIBABA_CLOUD_ACCESS_KEY_SECRET=这里填AccessKeySecret
```

不要创建主账号 AccessKey。阿里云明确建议每个独立业务程序使用独立 RAM 用户和最小权限；RAM 用户最多可创建两把 AccessKey，便于以后轮换。[官方 AccessKey 指南](https://help.aliyun.com/zh/ram/user-guide/create-an-accesskey-pair)

### 找不到“创建 AccessKey”时

- 你现在如果看到的是 RAM 的“概览”或“工作流”页面，先点击左侧 **身份管理 → 用户**，再点击 `video-editor-server` 的名称；按钮不在概览页。
- 新版页面的入口是 **认证管理 → AccessKey**，不是“凭证管理”；旧版才会使用后一个名称。
- 如果进入用户详情仍看不到“认证管理”或“创建 AccessKey”，当前登录身份没有管理 RAM 用户凭证的权限，需要主账号或管理员授予 RAM 管理权限后再操作。
- 若“创建 AccessKey”不可点，检查该用户是否已经有两把启用的 AccessKey；先停用并删除一把不用的旧密钥，确认业务已迁移后再创建。

> 若控制台拒绝 `mts:*` 动作，请确认当前账号已开通 MPS，并在 RAM 策略编辑器中选择“媒体处理（MPS / MTS）”服务后按动作名搜索；不要为了省事给 `AdministratorAccess`。

## 4. 开通 MPS、标准管道和模板

1. 打开 [MPS 控制台](https://mts.console.aliyun.com/)，顶部地域选择 **华北 2（北京）**。
2. 首次进入如出现服务授权提示，按控制台提示授权 MPS 服务角色访问第 2 步的 OSS Bucket；它需要读取输入视频并写入出片文件。
3. 在“全局设置 → 管道及回调”开启或创建一个 **标准管道**，复制它的管道 ID。
4. 在“模板管理 → 转码模板 → 自定义模板”创建两套模板，输出格式均选 MP4、视频编码 H.264、音频 AAC：

| 模板名称建议 | 分辨率 | 帧率 | 视频码率 | 对应环境变量 |
|---|---:|---:|---:|---|
| `video-editor-720p` | 720 × 1280 | 30 fps | 2.5 Mbps | `ALIYUN_MPS_TEMPLATE_ID_720P` |
| `video-editor-1080p` | 1080 × 1920 | 30 fps | 5 Mbps | `ALIYUN_MPS_TEMPLATE_ID_1080P` |

5. 在两个模板的详情页分别复制模板 ID。

MPS 普通转码要求使用标准管道、模板和 OSS 中的输入素材；模板 ID 是每套模板唯一的标识。[普通转码准备](https://help.aliyun.com/zh/mps/media-transcoding) [模板说明](https://help.aliyun.com/zh/mps/overview-of-transcoding-templates)

## 5. 填写项目根目录 `.env`

只编辑项目根目录的 `.env`，不要编辑 `.env.example`，更不要提交 `.env`。

```env
# 切换前先确认下方所有值都已填完
VIDEO_EDITOR_PROVIDER_MODE=aliyun

# 百炼（北京）
DASHSCOPE_API_KEY=新建的百炼APIKey
ALIYUN_MODEL_STUDIO_WORKSPACE_ID=业务空间ID前缀
ALIYUN_VIDEO_EDITOR_REGION=cn-beijing

# OSS（北京、私有）
ALIYUN_OSS_BUCKET=你的Bucket名称

# MPS（北京）
ALIYUN_MPS_PIPELINE_ID=标准管道ID
ALIYUN_MPS_TEMPLATE_ID_720P=720P模板ID
ALIYUN_MPS_TEMPLATE_ID_1080P=1080P模板ID

# 服务器专用 RAM 用户
ALIBABA_CLOUD_ACCESS_KEY_ID=RAM用户AccessKeyID
ALIBABA_CLOUD_ACCESS_KEY_SECRET=RAM用户AccessKeySecret
```

以下值可保持默认，不必改动：

```env
VIDEO_EDITOR_PRICE_VERSION=aliyun-cn-mainland-2026-07-28
VIDEO_EDITOR_QUOTE_TTL_SECONDS=900
```

## 6. 重启后先做无费用检查

1. 重启实际运行在 2001 端口的后端服务。
2. 在 PowerShell 执行：

```powershell
Invoke-RestMethod http://localhost:2001/api/v1/video-editor/capabilities |
  ConvertTo-Json -Depth 6
```

3. 只在结果同时满足下列条件时再打开剪辑页：

```text
provider_mode: aliyun
live_ready: true
is_mock: false
missing_configuration: []
```

4. 打开 `/video-editor`，上传一条你有权使用的短视频。先点击“查看费用”，检查 15 分钟报价；不确认就不会提交付费任务。

## 7. 开发测试花费预期

按北京地域、60 秒输入且约 60 秒输出估算：

| 项目 | 720P | 1080P |
|---|---:|---:|
| Fun-ASR | 0.0132 元 | 0.0132 元 |
| qwen-flash 剪辑方案（项目默认 3000 输入 / 1000 输出 Token 估算） | 约 0.00195 元 | 约 0.00195 元 |
| MPS H.264 渲染 | 0.0326 元 | 0.0651 元 |
| 预计合计 | **约 0.048 元** | **约 0.080 元** |

报价不包含 OSS 存储、公网播放流量和失败后你主动确认的重试。官方原价请以控制台账单为准：[Fun-ASR](https://help.aliyun.com/zh/model-studio/fun-asr) [qwen-flash](https://help.aliyun.com/zh/model-studio/qwen-flash) [MPS 转码](https://help.aliyun.com/zh/mps/product-overview/audio-and-video-transcoding-fees)。

## 8. 常见问题

| 现象 | 先检查什么 |
|---|---|
| 页面提示“云端出片尚未开通” | `.env` 是否漏填；修改后是否重启了实际 2001 后端 |
| `live_ready` 是 `false` | 查看 `missing_configuration`；不要只靠页面猜原因 |
| 百炼返回鉴权或地域错误 | API Key、业务空间 ID、服务端点是否均为北京地域 |
| OSS 403 | RAM 策略中的 Bucket 名称、输入/输出目录前缀、AccessKey 是否对应同一个 RAM 用户 |
| MPS 失败 | 标准管道、模板 ID、Bucket 地域以及 MPS 服务角色对 Bucket 的读写授权 |
| Key 发到聊天或截图 | 立即在控制台重置该 Key；不要继续使用旧 Key |

## 配置完成检查清单

- [ ] 百炼、OSS、MPS 都选择华北 2（北京）
- [ ] OSS Bucket 为私有，未开放公共读
- [ ] 已使用独立 RAM 用户，而非主账号 AccessKey
- [ ] `.env` 中 8 项真实云配置均已填写
- [ ] 已重启实际 2001 后端
- [ ] 能力接口显示 `live_ready: true`
- [ ] 第一次真实任务先查看报价，再明确确认
