# 找素材爬虫可靠性调研记录（2026-08-24）

本轮只为现有 VideoInsight“输入关键词→找素材→选结果→转写/进入流水线”主路径吸收可靠工程做法，不建设通用无代码爬虫产品，不复制第三方实现。

## 候选项目与取舍

| 项目 | 当前观察 | 许可证 | 复用点 | 本项目不直接引入的原因 |
|---|---|---|---|---|
| [Crawlee for Python](https://github.com/apify/crawlee-python) | GitHub 页面显示约 9.5k stars，近期仍有提交；支持 Playwright、请求队列、重试和持久化存储 | Apache-2.0 | 持久队列、请求幂等键、失败后有限重试、状态统计、遇到阻断时停止 | 当前项目已有 Playwright + SQLite + 平台安全闸门；引入完整框架会扩大依赖和架构边界，也不能替代平台专用公开浏览器策略 |
| [Scrapy](https://github.com/scrapy/scrapy) | GitHub 页面标为 BSD-3-Clause，官方文档持续更新；RetryMiddleware 默认有明确的重试上限，Feed Export 支持固定列 CSV | BSD-3-Clause | 按错误类型区分是否可重试、固定字段顺序导出、结果过滤和诊断 | 主要面向 HTTP spider；本项目核心是用户可见浏览器和人工登录，不迁移 Scrapy runtime |
| [Playwright Python](https://github.com/microsoft/playwright-python) | GitHub 页面标为 Apache-2.0；官方文档强调 locator 自动等待/重试，并提醒动态列表不能立即 `locator.all()` | Apache-2.0 | 保留现有可见浏览器，使用有限候选选择器、可等待动作和显式安全终止 | 已是现有依赖，不增加引擎；不使用反检测、代理轮换或绕过挑战能力 |
| [RQ](https://github.com/rq/rq) | GitHub 页面显示约 10.7k stars、最新版本页面活跃；依赖 Redis/Valkey，默认 pickle 还有安全注意事项 | BSD-2-Clause（以仓库 LICENSE 为准） | 后续若桌面产品有独立 Redis，可参考 worker/队列/JSON serializer 边界 | 当前桌面版没有 Redis 前置条件；本轮用 SQLite 持久队列保证开箱可用 |
| [arq](https://github.com/python-arq/arq) | 官方文档提供延迟任务、重试、取消、健康检查和 pessimistic execution；PyPI 页面标注 maintenance only | MIT | 任务幂等、进程中断后保留任务、健康状态 | 依赖 Redis 且处于维护模式；不引入运行时依赖，仅采用“中断保留、恢复时重置 running”原则 |

## 已采用的设计

- 关键词队列保存到现有 SQLite，每个关键词保留独立状态和关联搜索批次；同一队列串行执行，平台之间不扩大并发。
- 任务创建时去重，worker 每个关键词最多执行一次正常搜索；刷新/重启后由恢复接口继续未完成项，不重复抓已成功项。
- 连接问题只允许一次受控恢复；登录、验证码、访问频繁和规则识别失败直接安全暂停并保留结果，不将空结果包装成成功。
- CSV 使用固定 UTF-8（带 BOM 便于表格软件打开）字段：关键词、平台、标题、作者、链接、时间、互动、热度、相关性、质量说明；不写入 Cookie、Token、浏览器配置。
- 平台适配器保留主选择器，增加有限备用选择器；全部选择器均无结果时返回“规则可能失效”的诊断。

## 参考官方依据

- [Crawlee RequestQueue 官方示例](https://github.com/apify/crawlee/blob/master/docs/introduction/02-first-crawler.mdx)
- [Scrapy RetryMiddleware](https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#retry-middleware)
- [Scrapy Feed exports](https://docs.scrapy.org/en/latest/topics/feed-exports.html)
- [Playwright Python Locator](https://playwright.dev/python/docs/api/class-locator)
- [Playwright Python Auto-waiting](https://playwright.dev/python/docs/actionability)
- [arq 官方文档](https://github.com/python-arq/arq/blob/main/docs/index.rst)
