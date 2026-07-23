# 一键爆款发现与低成本文案流水线

## Summary

当前不能直接改代码，因为本任务仍在 Plan Mode。执行时按这个方案落地：

用“官方/授权数据 + 复爬增长分析”解决爆款判断，用“元数据原创脚本 / 手机豆包转写 / 授权 ASR”三档文案来源解决成本问题。默认不做高频浏览器爬取、验证码绕过、cookie 池、无头规避风控；这些不是稳定产品能力，且封号和合规风险不可控。

官方依据已核对：抖音有“热门视频榜”接口，scope 是 `data.external.billboard_hot_video`，不需要用户授权，数据统计最近 24 小时且每天 10 点前产出；有“实时热点词”接口，约每 2 小时刷新；关键词视频搜索只适合“与我相关”的业务关键词，且只返回最近 1 天公开视频，不等同于普通抖音搜索。参考：[热门视频榜](https://open.douyin.com/platform/resource/docs/openapi/data-open-service/tops-data/hot-video-list/hot-video-list/)、[实时热点词](https://open.douyin.com/platform/resource/docs/openapi/data-open-service/hot-video-data/get-current-hot-words/)、[视频搜索管理](https://open.douyin.com/platform/resource/docs/ability/search-management/video/)、[付费服务协议](https://open.douyin.com/platform/resource/docs/operation-standard/paid-services)。

## Key Changes

- 实现 `src/adapters/official.py` 里的官方热榜适配器，不再让 `DouyinHotBillboardAdapter` 只是禁用占位：
  - 复用现有 `client_token` 逻辑。
  - 拉取 `/data/extern/billboard/hot_video/`。
  - 保存 `rank/title/author/digg_count/comment_count/play_count/hot_words/hot_value/share_url`。
  - `share_url` 作为官方返回链接保存，标记为 `official_share_url`，不伪装成 `v.douyin.com` 短链。

- 新增官方热点词同步：
  - 拉取 `/hotsearch/sentences/`。
  - 存热点词、热度、刷新时间。
  - 前端搜索框给“官方热点词建议”，默认让用户从热词里选，减少冷门词搜不到热门视频的误解。

- 修改 `/crawler` 的搜索逻辑：
  - 默认先查官方热榜池，再按 `标题 + hot_words` 做本地关键词匹配。
  - 如果返回 0，显示“官方热门池中没有匹配，不代表抖音搜索无视频”。
  - 可选启用 `video.search`，但只用于已审批的品牌/业务关键词，并明确只覆盖最近 1 天。

- 保留现有增长评分骨架：
  - 继续用 3 次及以上复爬作为“爆款确认”门槛。
  - 复爬时间默认：近 1 天为 2h/6h/12h，近 7 天为 6h/24h/48h。
  - 排行默认按加权互动增长：点赞 1、评论 3、分享 4、收藏 4。
  - 没有分享/收藏字段时保存为 `null`，前端显示“未返回”，不当成 0。

- 做真正的一键入口：
  - `/crawler` 新增按钮“官方热榜一键监测”。
  - 一次点击完成：同步热榜、匹配关键词、保存快照、安排复爬、计算当前观察分、展示下一次复爬时间。
  - 如果已有到期复爬，同一个按钮先执行到期复爬，再刷新排行。
  - 页面文案区分“观察样本”“增长确认中”“热门候选”“爆发候选”。

- 文案提取改成三档，不再默认让用户花 8 分钱：
  - `metadata_original`：基于标题、热点词、互动数据生成原创口播结构，成本最低，但明确不是原视频转写。
  - `doubao_mobile_transcript`：使用当前已有手机豆包任务链路，打开抖音复制分享短链，再发豆包 App，费用显示 `¥0`；但首次需要已登录安卓设备/Appium，验证码或登录弹窗会失败并等待人工。
  - `authorized_asr_transcript`：用户确认授权后才走媒体解析 + ASR，显示预计成本，保留现在“付费自动解析”按钮。

- 前端调整：
  - 把“手机豆包免费提取”改名为“手机豆包 0 元转写”，旁边显示前置条件状态。
  - 对不能自动转写的候选，默认给“生成原创脚本”，而不是把它包装成原版文案。
  - 结果卡片新增字段：`文案来源`、`是否原版转写`、`是否需要人工复核`、`下一次复爬时间`。

- 配置保持一个根 `.env`：
  - 新增 `DOUYIN_CLIENT_KEY`、`DOUYIN_CLIENT_SECRET`、`DOUYIN_OFFICIAL_HOT_ENABLED=true`、`DOUYIN_HOT_WORDS_ENABLED=true`。
  - 保留 `DOUBAO_MOBILE_APPIUM_URL`、`ADB_PATH`、`DOUBAO_ANDROID_PACKAGE`，但它们只影响手机豆包链路。
  - 不覆盖现有 `.env`，只更新 `.env.example`。

## Test Plan

- 后端单元测试：
  - 用 fake transport 测官方热榜成功、空榜、字段缺失、接口错误、token 失败。
  - 验证官方热榜返回的 `share_url` 能保存到候选，不要求它一定是 `v.douyin.com`。
  - 验证关键词匹配 0 条时 `result_state` 是“官方热榜无匹配”，不是供应商异常。
  - 验证分享/收藏缺失时为 `null`，不进入 0 值增长计算。

- 趋势测试：
  - 单次快照只能显示观察中。
  - 3 次复爬后才可进入热门/爆发候选。
  - 增长倒退、异常暴增后失速继续降权。

- API 测试：
  - `/api/v1/crawler/capabilities` 返回官方热榜配置状态。
  - `/api/v1/crawler/batches` 能跑官方热榜模式。
  - `/api/v1/crawler/recrawls/due` 能执行到期复爬并更新排行。
  - 手机豆包链路创建任务时费用恒为 0，但缺少 ADB/Appium/包名时状态必须明确失败原因。

- 前端验证：
  - `npm run build`。
  - `/crawler` 页面能显示官方热榜、0 条解释、复爬状态、文案来源。
  - 不再把“系统长链接”当成豆包可读短链。

## Assumptions

- 默认只做抖音官方/授权数据源，不实现高频浏览器爬虫、验证码绕过、cookie 池、代理池或风控规避。
- “爆火”按增长判断，不承诺第一次搜索就找出爆款；第一次只叫“候选/观察中”。
- “0 成本文案”只在手机豆包成功读取视频时可叫原版转写；否则只能生成原创脚本或进入付费 ASR。
- 官方开放平台是否实际收费，以抖音开放平台管理中心套餐和账单为准；公开文档没有给出稳定单次价格。
