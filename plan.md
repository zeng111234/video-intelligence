# 执行计划：一键爆款发现与低成本文案流水线

原始方案：`doc/plan-viral-pipeline.md`（唯一权威需求来源，所有子代理必须阅读相关段落）。

## 架构事实（已勘察）

- 根目录 `src/` 是核心服务层：适配器在 `src/adapters/`，服务在 `src/services/`，数据模型在 `src/models.py`，持久化在 `src/repositories/sqlite.py`。
- `project/backend/` 是 FastAPI 后端，通过 sys.path 复用根 `src/`；crawler 路由在 `project/backend/app/api/v1/crawler.py`（前缀 `/api/v1/crawler`）。
- `project/frontend/` 是 React+TS+Vite 前端；爬虫页是 `src/pages/KeywordCrawlerPage.tsx`，API 封装在 `src/api/client.ts`、`src/api/types.ts`。
- 根 `tests/` 是服务层 pytest；`project/backend/tests/` 是 API 层 pytest。
- 现状：`DouyinHotBillboardAdapter` 是禁用占位；`DouyinKeywordAdapter` 已实现 client_token 逻辑可复用；复爬骨架在 `src/services/discovery.py`（RECRAWL_OFFSETS_BY_WINDOW、SamplingCheckpoint）；手机豆包链路在 `src/services/doubao_browser.py`。

## Stage 1（并行，3 个子代理，文件不重叠）

| 代理 | 角色 | 负责文件 | 禁止触碰 |
|---|---|---|---|
| A | 适配器工程师 | 仅 `src/adapters/official.py` + 新建 `tests/test_official_billboard.py` | src/models.py、src/services/、repositories |
| B | 服务层工程师 | `src/models.py`、`src/contracts.py`、`src/repositories/`、`src/services/`（discovery/commercial_search/keyword_trend/copywriting/heat/candidate）+ 根 `tests/` 中服务层测试 | src/adapters/official.py、project/ |
| C | 前端工程师 | 仅 `project/frontend/**` | 后端与 src/ |

热点词数据结构：A 在 official.py 内定义轻量返回结构（如 dataclass `HotWordEntry`），持久化由 B 负责；契约在各自 prompt 中写死。

## Stage 2（1 个子代理，Stage 1 全部完成后）

- D 后端 API 工程师：`project/backend/**`（crawler.py、deps.py、config.py、schemas）+ 根与后端的 `.env.example`（不覆盖现有 `.env`）+ `project/backend/tests/` API 测试。读取 Stage 1 实际代码做对接。

## Stage 3（1 个子代理，Stage 2 完成后）

- E 集成验证工程师：跑根 pytest、后端 pytest、前端 `npm run build`，修跨层不一致，输出最终验证报告。

## 验收标准（来自方案 Test Plan）

- 适配器 fake-transport 单测：成功/空榜/字段缺失/接口错误/token 失败；share_url 原样保存；0 匹配 result_state 为"官方热榜无匹配"；分享/收藏缺失为 null 不参与 0 值增长。
- 趋势：单快照=观察中；≥3 次复爬才可进热门/爆发候选；倒退/失速降权。
- API：capabilities 返回官方配置状态；batches 支持官方热榜模式；recrawls/due 执行到期复爬；手机豆包费用恒 0 且缺 ADB/Appium/包名时失败原因明确。
- 前端：`npm run build` 通过；/crawler 显示热榜、0 条解释、复爬状态、文案来源。
