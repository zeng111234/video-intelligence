# VideoInsight 找素材爬虫验收记录

日期：2026-08-24（Asia/Shanghai）

## 范围与限制

- 使用临时本机桌面工作区实例 `127.0.0.1:2003` 验证新增后端路由；现有 `1001/2001` 服务未被停止或覆盖。
- 正常使用本机已有激活码登录；激活码和登录 token 未写入本记录。
- 只提交 1 个关键词、1 个公开平台（B 站）、每平台 1 条，未调用付费回退、未上传云端。
- 内置浏览器桥接不可用，页面截图改用本机 Chrome；最初未登录截图如实记录了激活码门槛。
- 随后通过仓库既有本地启动脚本按桌面演示模式重启了 `1001/2001`，完成了真实登录态页面验收；没有使用公司服务。

## 实际 API 响应摘要

临时实例：`http://127.0.0.1:2003`

```json
{
  "login": "ok",
  "capabilities_status": 200,
  "active_platforms": ["douyin"],
  "queue_create_status": 200,
  "queue_id": "crawler-queue-0bfff9a4433e",
  "status": "succeeded",
  "total": 1,
  "completed": 1,
  "item_status": "succeeded",
  "started_at_present": true,
  "finished_at_present": true,
  "batch_id_present": true,
  "history_list_status": 200,
  "export_status": 200,
  "export_content_type": "text/csv; charset=utf-8",
  "export_bytes": 737,
  "export_utf8_bom": true,
  "export_rows": 1
}
```

## 本轮吞吐与漏斗验收（2026-08-24）

- 冷采集证据：[cold-bilibili-api-v3.json](cold-bilibili-api-v3.json)。关键词“工厂短视频”，B 站，目标 30 条，cache_hit=false；接口墙钟耗时约 19.98 秒，适配器总耗时 8.063 秒，首个搜索响应约 3.206 秒到达后端，导航阶段 6.420 秒，解析/过滤阶段 3 毫秒。这个阶段时间不等同于浏览器 UI 已渲染候选。
- 漏斗：原始发现 143，解析池 100，去重后 143，关键词直匹配 7，相关性淘汰 94，时长淘汰 0，字段无效 0，最终页面展示 6。页面/接口均返回了 safety_limit；没有把“只剩 6 条”冒充为 30 条。
- 规则与会话：visible_browser_network_v4_funnel_adaptive_early_stop，复用既有 B 站浏览器页面，未做 Cookie 清理，未发生会话软恢复。安全边界下扫描到 143 条公开卡片后停止，30 条最终候选门槛未通过；原因是本次公开搜索的严格相关候选不足，不是把不相关内容放宽进榜。
- 修正后复核证据：[cold-bilibili-api-v4.json](cold-bilibili-api-v4.json)。同一关键词再次低频冷采集返回 11 条；原始发现 140、解析 100、直匹配 11、相关性淘汰 89、时长淘汰 0、字段无效 0，漏斗闭合为 `100 = 11 + 89`。首个搜索响应 3.645 秒、适配器总耗时 8.757 秒、接口墙钟 18.463 秒；30 条最终候选门槛仍未通过。
- 漏斗修正：B 站适配器的标题/话题/描述直匹配统一写入结构化证据，后端使用同一判定计算 `direct_match`，避免描述命中被错误淘汰或漏斗重复计数。
- v5 有界深扫证据：[cold-bilibili-api-v5.json](cold-bilibili-api-v5.json)。最多 10 页/300 条原始边界后，实际扫描 267 条、解析 100 条、直匹配 12 条、相关性淘汰 88 条、最终 12 条；首个搜索响应 3.471 秒、适配器 17.097 秒、接口墙钟 33.732 秒，仍低于 60 秒门槛。该响应是后端最终 JSON，未证明浏览器 UI 在 12 秒内已展示候选。最终候选从 v4 的 11 条提升到 12 条，但仍不足 30 条。
- v6 真实验证边界：[v6-safety-cap-api.json](v6-safety-cap-api.json)。在 v6 代码加载后，下一次请求被既有 B 站 24 小时 8 次真实采集上限拒绝，未进入平台采集；因此 v6 的排序修正只有定向测试证据，不能冒充新的真实页面吞吐数据。
- 安全窗口只读核对：SQLite 中 `bilibili_browser_search` 的 24 小时窗口起点为 `2026-08-24T11:05:53+08:00`，当时已累计 8 次；预计 `2026-08-25 11:05` 左右才允许下一次低频真实采集。本轮未修改计数或时间字段。
- 缓存对照：[cache-bilibili-api.json](cache-bilibili-api.json) 单独记录 cache_hit=true；缓存响应不用于冷采集耗时结论。
- 新页面截图：[crawler-optimized-live.png](crawler-optimized-live.png)。实际打开 http://localhost:1001/crawler，已目视确认首屏只有关键词输入和“找素材”主按钮，批量/历史为次级入口。
- v4 冷采集后页面刷新实际显示“2026/8/24 12:44:47 · 11 条素材”，并打开了该批次详情；本次 Chrome 截图调用两次超时，未重复施压，保留上述已目视截图与 v4 API/页面文本证据。
- 历史恢复与交付：[cold-bilibili-history.json](cold-bilibili-history.json) 重新 GET 同一批次并保留漏斗字段；[cold-bilibili-export.csv](cold-bilibili-export.csv) 实际导出 3516 bytes、UTF-8 BOM，字段仍为关键词、平台、标题、作者、链接、时间、互动、热度、相关性和质量说明。

## CSV 样例

实际导出内容保存在 [export-sample.csv](export-sample.csv)。固定字段包括关键词、平台、标题、作者、链接、时间、互动、热度、相关性和质量说明；未包含 Cookie、Token 或浏览器配置。

页面导出也已实际触发：从历史记录打开成功的“门店短视频”批次，浏览器下载 `crawler-batch-64db2a736c58.csv`，HTTP 200，3774 bytes，UTF-8 BOM，7 行数据，页面提示“CSV 已导出”。

## 登录态页面验收

- 旧单关键词：在 `http://localhost:1001/crawler` 输入“门店短视频”、勾选唯一可搜索的 B 站并点击“找素材”，POST `/api/v1/crawler/batches` 返回 200；批次 `batch-64db2a736c58`，B 站返回 7 条，状态 `partial`（公开页面部分字段缺失已如实保留）。
- 批量：页面建立 `crawler-queue-82edc450dae9`，2 个关键词、B 站、总数 2；完成 1/2 后因同平台冷却显示“部分成功”，没有扩大抓取强度。
- 恢复：页面点击“继续”，POST `/keyword-queues/{id}/resume` 返回 `queued`；已成功关键词保持 `succeeded`，失败关键词变为 `queued`，随后按安全冷却规则处理。
- 历史：历史抽屉显示上述单关键词和批量相关结果，点击“查看”可恢复批次详情。

## 页面证据

[crawler-page.png](crawler-page.png) 是 `http://localhost:1001/crawler` 的实际页面截图。它证明页面可达，并如实显示当前未登录状态；找素材主页面、批量弹窗和下载按钮未在该运行配置下冒充已验收。

## 测试与耗时

- 无触网验收审计：运行 `python scripts/audit_crawler_acceptance.py` 返回 `verified_partial`；已验证冷扫描时长、漏斗加总、冷缓存区分、安全暂停、CSV 字段/敏感字段排除和页面截图存在性；明确保留 30 条最终候选、页面首批 12 秒和 v6 新鲜冷采集三个未通过门槛。
- `project/backend/tests/test_crawler_keyword_queue.py`：7 passed。
- 定向爬虫 Python 测试（队列、平台适配器、抖音适配器、官方爬虫、浏览器安全）：全部通过。
- 前端全量 Vitest：23 个测试文件、184 个测试通过。
- 前端 `tsc --noEmit`：通过。
- 新增真实队列从创建到完成约 1.2 秒（命中现有缓存）；首次低频公开数据队列约 57 秒，最终成功。
- 页面 GET：`http://localhost:1001/crawler` HTTP 200；登录后单关键词页面请求约 45 秒返回，批量任务按平台冷却安全降级。
- 最新队列 API 返回 `worker_active`：已完成/部分完成任务均为 `false`；持久化的旧 `RUNNING` 任务在无 worker 时由页面显示“继续”，避免重启后无法恢复。

## 本轮测试补充

- 定向爬虫 Python 测试（队列、平台适配器、抖音适配器、商业搜索单次连接重试、官方爬虫、浏览器安全）：全部通过。
- 前端 KeywordCrawlerPage.test.tsx：21 tests passed；前端 tsc --noEmit：通过。
- 本轮页面 GET 为 HTTP 200；历史单关键词、批量、恢复、CSV 的旧路径已有真实证据，本轮新增首批可见/冷采集漏斗证据。
- 登录态安全边界补充：四个平台均提供需明确确认的单平台重置 API；只有页面软恢复失败后能力接口才展示前端“重置登录”，保留浏览器资料，不导出 Cookie。未确认的 API 请求和前端取消操作均不会连接或清理浏览器。

## 未验证与风险

- 未验证抖音、小红书、快手真实公开页面采集；它们仍受登录、浏览器资料、规则版本和本机网络状态影响。
- B 站本轮“目标 30 条最终候选”未达标：真实公开搜索只得到 6 条最终展示候选；扫描速度门槛通过，但结果量门槛不能宣称通过。
- 暂停按钮本身未在本次真实任务中捕获到：首个关键词很快结束，页面直接进入部分完成；恢复按钮和失败项重排队已真实验证，暂停/运行态重复批次边界另有定向测试覆盖。
- 本轮新增的“重置登录”仅完成定向 API/UI 与适配器边界测试，未在真实账号上点击，避免无必要地退出当前登录态。
- 新增平台登录重置域隔离测试：未确认时不连接浏览器；确认后只清理 `bilibili.com` Cookie 与同域页面存储，`example.com` 页面保持不变。
- 页面截图受内置浏览器桥接不可用影响，使用本机 Chrome fallback；已保存登录态单关键词、批量、恢复和历史导出截图。

## 2026-08-24 后续改动：默认次数限制与真实进度链路

- `BROWSER_MAX_REAL_RUNS_PER_WINDOW` 现默认为空值/关闭；只有管理员显式配置正整数才启用滚动 24 小时次数上限。SQLite 中已有的 `real_runs_in_window=8` 未被清理，在默认关闭时不会再作为日限阻断；同平台租约、验证码/访问异常安全暂停仍保留，浏览器采集不再额外等待 3 分钟。
- 能力状态默认显示“同平台不额外冷却（同平台仍一次只运行一个任务）；遇到验证码或访问异常会自动暂停”，不再显示“24 小时最多 8 次”或“3 分钟冷却”。
- 单关键词找素材现复用持久关键词队列：提交先返回 `queue_id`；后台按平台完成节点保存 `progress_stage`、扫描/解析/保留计数和部分批次 ID；前端约 1.5 秒轮询，按批次 ID合并去重，完成前不离开真实进度页，刷新可从队列恢复。当前粒度是逐平台完成批次，不是逐卡片流式。
- 本次新增代码的后端队列/安全定向测试与前端 `KeywordCrawlerPage.test.tsx`、TypeScript 检查均通过；未将这些测试当作真实页面验收。
- 新后端已重启在本机 2001 端口；本次应用内浏览器连接桥不可用，未能取得带现有登录态的三张新页面截图、首批前端时间或一次新的 B 站冷采集 API 响应。因此本节不能宣称默认限额解除后的真实 B 站页面验收完成，旧 v5/v6 数据仍是历史证据。

## 2026-08-24 本地客户 403 与初始化重复提示修复

## 2026-08-24 孤儿批次与真实增量候选修复

- 根因：多平台执行过程中曾把临时平台批次 ID 先写入 `partial_batch_ids`，随后合并批次时删除了临时批次，留下了不存在的 `batch-f2483a22eddd`。修复后只有批次已保存且可立即读取、关键词和平台归属校验通过，才会进入队列；历史孤儿 ID 读取时安全忽略并记录一次高级诊断。
- 前端轮询改为逐 ID 容错；一个 404 不会丢掉可读结果，也不会每 1.5 秒重复 Toast。任务进入终态后停止轮询。
- 候选快照持久化在队列项中，按 `video_id` 去重并允许后续字段补全；前端按平台和候选 ID 合并渐进快照与正式批次，避免同一候选因运行 ID 不同重复出现。当前 UI 展示真实的已找到、已扫描、已解析计数，不再使用定时器伪造 1 到 30。
- 定向后端测试：队列持久化、临时批次不泄漏、孤儿/跨队列 ID 清理、候选顺序和字段更新均通过；本轮相关 Python 测试共 175 项通过。
- 定向前端测试：`KeywordCrawlerPage.test.tsx` 全部 25 项通过；包含候选 1/2/3 增量、字段补全、孤儿 404 容错、终态停止轮询和刷新可恢复；`npx tsc --noEmit` 通过。
- 实际 1001 页面：统一脚本重启 2001 后刷新并进入 `http://localhost:1001/crawler`，页面 200 可达，无客户工作区 403 和重复初始化报错；打开最新 `ai获客` 历史结果后等待 3.2 秒，页面没有“搜索批次不存在” Toast。DOM 证据见 [crawler-history-aihuoke-dom.txt](crawler-history-aihuoke-dom.txt)，状态记录见 [crawler-page-live-status.json](crawler-page-live-status.json)。
- 本轮未做新的真实平台搜索：当前四个平台均显示“待登录”，没有安全登录态，不强行触发抖音或 B 站。故尚未在真实在线采集过程中观察到逐条/小批候选流入；本项代码和受控事件已通过，但真实流入验收仍不能宣称完成。

- 根因：此前为验收直接启动了裸 `uvicorn`，没有继承统一启动脚本设置的 `VIDEOINSIGHT_DESKTOP_CLIENT=true`，生产租户隔离守卫因此正确返回 403；守卫未删除。
- 修复：`start_all_services.ps1 -BackendOnly -SkipBrowser` 只管理 2001 后端，并在健康检查中校验本地桌面模式；`/health` 只返回非敏感模式布尔值，不返回 Token、Key 或 owner。
- 前端初始化将历史与能力错误按根因聚合；同一次进入在 React StrictMode 下不重复发起可见错误，改为一个内联错误和“重新加载”按钮；一项成功时保留另一项可用内容。
- 实际页面：刷新 `http://localhost:1001/crawler` 后无“客户工作区正在升级数据隔离”与重复 Toast；余额显示、B站显示“可搜索”、历史显示 20 条。
- API 日志证据与截图见 `crawler-init-api-evidence-20260824.txt`、`crawler-page-no-init-errors.png`。客户登录后的 `/api/v1/crawler/capabilities`、`/api/v1/crawler/batches` 均为 200。
