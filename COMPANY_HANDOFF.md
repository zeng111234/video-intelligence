# VideoInsight 公司交接说明

更新时间：2026-09-11（Asia/Shanghai）

## 1. 交付结论

本次以**公司环境变量版脱敏源码包**为交付源：包内只保留源码、锁文件、运行资源、`.env.example` 和文档，不含开发机 `.env`、`.env.production`、`.streamlit/secrets.toml`、数据库、日志、浏览器登录态或 SSH 私钥。公司已经在 Windows 系统/用户环境中配置的非空变量，启动脚本会优先读取；没有配置的变量才回退到本机 `.env`。因此公司电脑如果已有公司密钥，安装后不需要再把密钥写入项目目录。

此前生成的 `VideoInsight-company-configured-handoff-2026-09-11.zip` 曾包含本机配置文件，已标记为**不可交付**；不要把它上传 GitHub 或发给客户。

当前交付源是已经推送的脱敏交接分支：

- 分支：`codex/company-handoff-20260911`
- 交付提交：以该分支远端 `HEAD` 为准；接收方应先记录 `git rev-parse HEAD`
- 交付分支工作区：已验证为干净
- 运行资源：已显式纳入 `data/templates/builtin.json`，不依赖开发机被忽略的本地目录
- 交付包：源码 ZIP 与包外 SHA256 记录同步更新

这份版本适合“源码接手、继续维护和二开”。它不应被描述为已经完成公司具体电脑、真实供应商账号和正式生产环境的最终验收；公司仍应按本文档做一次独立验收。

## 2. 软件做什么

VideoInsight 是短视频热点洞察与智能生产工作台，主要包含：

- 视频上传、转写、字幕和时间轴处理；
- 文案生成、内容生产队列和任务状态；
- 本地 FFmpeg 剪辑，以及可选的云端供应商适配；
- 自动精剪：字幕强调、语义视觉事件、B-roll/PiP 规划、音效和导出；
- 抖音、小红书、视频号、B 站等平台的浏览器辅助与候选管理；
- 数字人、发布和公司控制层适配（是否可用取决于公司配置）。

默认原则是：没有公司授权的供应商密钥时运行本地沙箱/本地处理，不应自动产生第三方费用。

## 3. 接收方首次使用

1. 将脱敏 ZIP 解压到一个新的目录，例如 `C:\VideoInsight`。
2. 确认 Windows 上有 Python 3.12+、Node.js 18+；本地视频处理还需要 FFmpeg。
3. 双击根目录 `start.bat`。
4. 首次启动会创建项目专用 `.venv`、安装 Python/前端依赖，并从 `.env.example` 创建本机 `.env`。
5. 打开：
   - 工作台：`http://localhost:1001`
   - 后端健康检查：`http://localhost:2001/health`
   - API 文档：`http://localhost:2001/docs`

首次建议保持 `.env` 中的沙箱模式，先验证页面、上传、转写和本地导出；不要直接填入个人账号或未知来源的密钥。

## 4. 公司服务接入

公司接手后应由公司管理员单独提供并保管以下内容：

- 公司控制层地址、公司账号和激活/计费配置；
- 公司名下的 ASR、文案、云剪辑、数字人、素材或图像供应商账号；
- 公司自己的域名、回调地址、对象存储和发布平台应用配置。

公司电脑如果已经通过系统环境变量或用户环境变量配置了公司账号，直接运行 `start.bat` 即可；启动脚本会保留这些非空变量，不会被包内 `.env.example` 的空值覆盖。若某变量没有配置，程序会使用沙箱/本地默认值，或在功能页明确提示缺少配置。公司管理员只需按“密钥边界清单”核对变量名和归属，不要把真实值写进源码包。

源码预览可以使用 `scripts\\start_all_services.ps1 -UseCompanyServer`，但必须先确认公司控制层地址和权限，且先用低成本/沙箱模式验收。

正式 Windows 安装包不应把永久供应商密钥放进客户端；正式密钥应留在公司控制层或公司的密钥管理系统中。启用公司控制层的安装包不是供应商 Sandbox：启动器会把核心云能力设为 `ASR_MODE=cloud`、`VIDEO_EDITOR_PROVIDER_MODE=aliyun`、`COPYWRITING_MODE=production`；Electron 的 `webPreferences.sandbox=true` 只是桌面渲染进程隔离，不能与业务演示模式混淆。

## 5. 代码结构

| 路径 | 用途 |
|---|---|
| `src/` | 核心业务服务、适配器、剪辑工作流和领域逻辑 |
| `project/backend/` | FastAPI 后端与 API |
| `project/frontend/` | React + TypeScript + Vite 前端及 Electron 壳 |
| `database/` | SQLite/PostgreSQL 迁移和数据库脚本 |
| `scripts/` | 启动、安装、检查、验收和发布辅助脚本 |
| `assets/` | 项目内素材、图标、贴纸、音效及许可说明 |
| `tests/` | 领域、后端和前端测试 |
| `README.md` | 开发、启动、端口和功能总览 |
| `AGENTS.md` | 项目协作、成本、重试和验收约束（开发维护时阅读） |

## 6. 日常维护入口

```powershell
cd C:\VideoInsight
git status --short --branch
.\scripts\setup_windows.ps1 -CheckOnly
.\scripts\start_all_services.ps1 -SkipBrowser
```

定向检查：

```powershell
python -m compileall -q app.py app_pages src scripts tests
python -m pytest -q
cd project\frontend
npm test
npx tsc --noEmit
npm run build
```

涉及页面流程的改动必须在实际运行页面上检查，单元测试、构建成功或 HTTP 200 不能代替页面和最终媒体验收。

## 7. 不随包交付的内容

以下内容属于本机运行状态、个人数据、客户数据或敏感凭据，不应进入公司环境变量版脱敏源码包：

- `.env`、`.env.production`、`project/backend/.env`、`deploy/control-plane/.env`、`.streamlit/secrets.toml`；
- `.venv/`、`venv/`、`node_modules/`、`.git/`、`.setup/`、缓存目录；
- `data/`、`outputs/`、`work/`、运行日志、SQLite 数据库、任务记录和客户媒体；
- 浏览器 profile、Cookies、登录会话、下载目录和个人头像/声音素材；
- 旧安装包、旧发布日志、服务器备份、服务器私钥和任何密码；
- 未核对许可的参考素材或个人购买/个人账号下载内容。

公司密钥应通过 Windows 系统/用户环境变量或公司的密钥管理流程提供；源码包本身不承载任何真实供应商凭据。

## 8. 交接后的验收标准

公司接收后至少完成以下检查并保存结果：

1. 干净电脑解压后能通过 `start.bat` 启动；
2. `/health` 显示正确的桌面运行模式；
3. 页面能完成一次本地沙箱任务，不产生第三方扣费；
4. 本地导出包含正确的视频、音频和字幕轨；
5. 需要公司服务的功能使用公司新建凭据完成一次低成本验收；
6. 公司建立自己的 Git 提交、发布版本、备份和回滚记录；
7. 公司确认第三方素材、字体、音效和图标的许可范围。

## 9. 仍未确认的事项

- 当前工作树不是干净的正式发布提交；
- 当前未证明这份源码快照在公司电脑上已完成全流程页面验收；
- 原开发机 `.env` 中的各供应商账号归属不能仅凭变量名判断，见 `交付文件/密钥边界-不含值-2026-09-11.md`；
- 公司是否接收历史数据、旧素材和服务器控制层，需要由公司负责人另行确认。

---

## 10. 二开接手必读：当前真实状态

本节是对前面旧版交接内容的补充，优先级高于历史文档中的笼统描述。

### 10.1 已验证、部分完成和未完成

| 状态 | 事项 | 证据或说明 |
|---|---|---|
| 已验证 | 管理员页面的“积分消费”前端展示已从源码移除 | AdminPage.tsx、AdminPage.css、api/client.ts、api/types.ts 已同步修改；CreditUsageSection.tsx 已删除 |
| 已验证 | 前端测试通过 | 23 个测试文件、204 个测试通过 |
| 已验证 | TypeScript 检查通过 | npx tsc --noEmit |
| 已验证 | Vite 构建通过 | npm run build |
| 部分完成 | 后端仍保留积分核算和管理员消费 API | 这是兼容性设计；移除页面不等于删除计费能力 |
| 部分完成 | 客户激活码管理仍在源码中 | 支持生成、列表、启用/停用、续期 |
| 未完成 | 客户激活码删除 | 当前后端没有 DELETE /api/v1/admin/codes/{code} |
| 未完成 | 一次性清空测试客户、激活码和积分流水 | 需要后端维护脚本或受保护的一次性清理接口 |
| 未完成 | 旧 EXE 反映最新前端源码 | 旧安装包是在最近 UI 改动之前生成的 |
| 待接手验收 | 正式剪辑端到端发布流程 | quote -> user_confirm -> generation -> export -> 页面播放 -> 下载尚未形成正式 PASS 证据 |

当前不要把“源码构建成功”“接口返回 200”或“旧 EXE 能打开”写成正式产品验收通过。

### 10.2 测试数据清理边界

当前曾使用过本机开发库和远端测试控制面，两者不是同一份数据。清理前必须先确认数据源：

- 本地开发模式通常使用 data/video_intelligence.db 或由运行时配置指定的 SQLite；
- 公司控制面模式通过 VIDEOINSIGHT_CONTROL_PLANE_URL 转发账号、激活码、积分和部分供应商请求；
- Electron 安装包的后端配置位于安装目录 resources/backend/_internal/config/desktop-control-plane.json；
- 安装包 release.json 只描述版本和控制面地址，不等于数据库位置。

如果需要清空测试数据，建议先输出只读数量，再备份，再在事务中按以下顺序处理：

1. 客户会话；
2. recharge_requests；
3. avatar_billing_reservations；
4. 客户相关 credit_transactions；
5. 客户相关 credit_accounts；
6. customer_codes；
7. 除 admin 以外的测试管理员账号；
8. 最后重新统计并确认 admin 仍可登录。

不要通过删除整个数据库文件、删除系统配置或删除源码来实现“干净交付”。价格配置、数据库迁移版本、公司控制面配置结构和正式管理员账号应单独保留。

## 11. 二开架构与调用链

### 11.1 前端到后端

~~~text
用户操作
  -> project/frontend/src/pages 或 components
  -> project/frontend/src/api/client.ts
  -> 本地 FastAPI /api/v1/*
  -> 鉴权中间件 project/backend/app/main.py
  -> 本地服务或 control_plane_client.py
  -> SQLite / 公司控制面 / 外部供应商
~~~

修改接口时必须同步检查：

- 后端路由和请求模型；
- 前端 api/client.ts；
- 前端 api/types.ts；
- 页面 loading、error、empty 和 success 状态；
- 权限边界；
- 相关 Vitest/Pytest 测试；
- 本地模式和控制面模式是否都还能运行。

### 11.2 控制面会话

控制面启用时，前端拿到的是本地会话，后端在进程内保存本地 Token 到上游 Token 的映射。这个映射不会持久化到源码或数据库。

因此：

- 重启本地后端可能要求重新登录；
- 不能把 localStorage、Cookie 或日志复制给公司；
- 不能把本地 Token 直接当成控制面的 Token；
- 控制面管理接口失败时先区分本地鉴权失败、上游鉴权失败和上游数据权限失败。

### 11.3 主要服务调用链

#### 转写

上传文件或分享链接
  -> 媒体探测和音频提取
  -> 本地 faster-whisper 或远端 ASR
  -> transcript/task 状态
  -> 字幕 manifest 和页面展示

#### 文案

文本/转写结果
  -> copywriting service
  -> 本地或公司控制面供应商
  -> 成本提示
  -> 用户确认后保存或进入生产流程

#### 剪辑

素材和字幕
  -> quote
  -> 用户确认
  -> director plan
  -> provider search / image generation / authorized assets
  -> FFmpeg 或云端剪辑
  -> subtitle manifest、quality report、contact sheet
  -> export、页面播放、下载

## 12. 视频剪辑二开说明

### 12.1 关键文件

- project/frontend/src/pages/VideoEditorPage.tsx：页面状态、用户输入、报价确认和结果展示。
- project/backend/app/api/v1/video_editor.py：剪辑 HTTP 接口。
- src/services/video_editor_workflow.py：本地/云端流程编排。
- src/services/video_editor_cloud.py：供应商请求、报价、操作状态和回调/轮询。
- src/services/director_plan.py：导演计划和时间线结构。
- src/services/semantic_director.py：从转写/语义事件生成视觉意图。
- src/services/creative_director_compiler.py：把模型提案编译成可审计的候选动作。
- src/services/director_preview_review.py：预览审核和质量门。
- src/services/editorial_stickers.py：字幕锚定的视觉强调。
- src/services/motion_graphics.py：局部动态和安全降级。
- src/services/transcript_review_gate.py：事实、数字和字幕复核。

### 12.2 MiniMax 或其他模型的正确边界

模型输出只能作为受约束的创意提案，必须经过本地编译和审计：

- provider/model；
- 覆盖范围；
- event_id；
- 使用的授权素材或安全降级；
- 预计成本；
- 拒绝原因；
- fallback；
- 最终可执行时间线。

不能只让模型返回一个标签，然后由旧的硬编码逻辑决定真正剪什么。模型不可用、素材未授权或内容不确定时，应返回可解释的跳过/形状/安全素材结果。

### 12.3 正式验收硬门

正式验收必须由实际 /video-editor 页面完成，不得使用 work/ 下的手工 concat 脚本代替。至少保存：

- 最终 MP4；
- H.264 视频轨和 AAC 音频轨证据；
- 烧录字幕；
- 不可变 subtitle_manifest 及 sha256；
- director_timeline；
- provider_search_log；
- image_generation_log；
- quality_report；
- contact_sheet；
- 页面播放和下载成功证据；
- 真人观看结论。

如果其中任何一项缺失，只能写“阶段性预览”或“部分验证”。

## 13. Windows 打包二开说明

### 13.1 打包入口

主要脚本：

- scripts/build_windows_installer.ps1：构建前端、PyInstaller 后端、Electron 目录版或 NSIS 安装包；
- scripts/build_offline_windows_installer.ps1：离线安装包；
- scripts/build_final_windows_release.ps1：正式发布门禁；
- scripts/verify_windows_release_payload.py：安装包内容检查；
- scripts/scan_release_secrets.py：源代码密钥扫描；
- project/frontend/package.json：Electron Builder 配置和 npm scripts。

project/frontend/package.json 中的主要命令：

~~~powershell
npm test
npx tsc --noEmit
npm run build
npm run desktop:dir
npm run desktop:dist
~~~

完整构建优先使用 scripts/build_windows_installer.ps1，因为它会先执行媒体工具校验、前端测试、更新测试、Python 发布测试和前端构建，并生成后端打包输入。

### 13.2 打包前检查

~~~powershell
git status --short --branch
.\.venv\Scripts\python.exe .\scripts\scan_release_secrets.py --repository-root (Get-Location).Path
.\.venv\Scripts\python.exe -m pytest -q
cd project\frontend
npm test
npm run test:update
npx tsc --noEmit
npm run build
~~~

打包前必须确认：

- 当前源码和目标版本对应；
- .env、数据库、浏览器 profile 和客户媒体没有进入发布候选；
- 控制面地址是公司 HTTPS 地址；
- 版本号已更新；
- ffmpeg、ffprobe 和许可证校验通过；
- 没有复用被旧进程或杀毒软件锁定的旧输出目录。

### 13.3 现有旧包的限制

当前已有的 0.2.48-dev.1 安装包是在最近一次管理员页面源码改动前生成的。它可以作为历史安装测试件，但不能证明包含最新源码，也不能用它验证“积分消费”页面已经移除。

重新构建后必须记录：

- 版本；
- 构建时间；
- 源码 commit；
- 安装包绝对路径；
- SHA256；
- 安装、启动、卸载结果；
- 已知未完成项。

## 14. 前端管理员页面接手说明

管理员页面的组合关系主要在 AdminPage.tsx，客户和管理员管理区主要在 CustomerAdminSection.tsx。

当前源码层面：

- “积分消费”展示组件已经移除；
- 后端 credits admin usage 接口仍存在，供兼容性和账务核对使用；
- 客户激活码区仍存在；
- 管理员账号删除能力仍存在，但后端会保护当前账号和最后一个管理员；
- 客户激活码没有删除按钮，也没有对应 DELETE API。

如果公司只想交付“干净的测试状态”，应清理测试数据，但保留客户管理能力；如果公司想把客户计费产品整体下线，才需要另行设计权限、路由、数据库和前端的完整移除方案。不能把这两种需求混为一谈。

## 15. 配置和密钥交接清单

只把变量名交给接手方，不把值写进文档：

- 基础：APP_ENV、APP_DEBUG、APP_SECRET_KEY、API_KEY；
- 管理员：ADMIN_PASSWORD；
- 数据库：DATABASE_PATH、POSTGRES_HOST、POSTGRES_PORT、POSTGRES_DB、POSTGRES_USER、POSTGRES_PASSWORD；
- 控制面：VIDEOINSIGHT_CONTROL_PLANE_ENABLED、VIDEOINSIGHT_CONTROL_PLANE_URL、CONTROL_PLANE_TIMEOUT_SECONDS；
- ASR：ASR_MODE、WHISPER_MODEL、WHISPER_DEVICE；
- 剪辑：VIDEO_EDITOR_PROVIDER_MODE、VIDEO_EDITOR_PRICE_VERSION；
- 文案：COPYWRITING_MODE、COPYWRITING_BASE_URL、COPYWRITING_MODEL、COPYWRITING_API_KEY；
- 图像/素材：VIDEO_IMAGE_*、PEXELS_API_KEY、PIXABAY_API_KEY；
- 数字人：SHUYING_*、AVATAR_SERVICE_*、LOCAL_AVATAR_*；
- 发布：PUBLISH_*、DOUYIN_*、KUAISHOU_*、PUBLISH_WECHAT_*、PUBLISH_XIAOHONGSHU_*；
- 个人/已清理边界：DASHSCOPE_*、ALIYUN_*、ALIBABA_CLOUD_*、个人 DeepSeek/OneAPI 相关值。

接手方应：

1. 在公司密码库创建公司自己的凭据；
2. 先使用沙箱或低成本模式；
3. 只把密钥放在公司控制层；
4. 为每个供应商记录负责人、计费主体、预算和轮换日期；
5. 不要把供应商密钥打进 Electron 客户端。

密钥边界参考：

交付文件/密钥边界-不含值-2026-09-11.md

## 16. 交接后第一周计划

### 第一天：可启动

1. 复制源码到公司目录；
2. 检查 Python、Node、FFmpeg、Git；
3. 创建公司自己的 .env；
4. 以本地沙箱模式启动；
5. 验证 /health、首页和一个本地任务；
6. 保存启动日志。

### 第二天：可修改

1. 阅读本文件、README.md、交接文档.md 和 AGENTS.md；
2. 建立公司 Git 分支和保护规则；
3. 运行前端、后端和 Electron 测试；
4. 修改一个非关键 UI 文案并做页面验收；
5. 确认前端 API 类型、后端路由和数据库迁移流程。

### 第三天：可接公司服务

1. 使用公司测试管理员和客户账号；
2. 配置公司控制面地址；
3. 用低成本供应商请求验证登录、报价和失败恢复；
4. 验证客户/管理员隔离；
5. 验证激活码创建、停用、续期和客户登录。

### 正式发布前

1. 选定干净源码提交；
2. 清理或明确测试数据；
3. 扫描密钥和客户数据；
4. 重新打包；
5. 在干净电脑安装；
6. 完成页面、API、视频和下载验收；
7. 建立版本标签、备份和回滚；
8. 由公司负责人签字。

## 17. 最终验收表

| 项目 | 当前状态 | 接手方证据 |
|---|---|---|
| 源码可解压、可建立 Git 分支 | 待接手方复核 |  |
| 本地沙箱可启动 | 待接手方复核 |  |
| /health 模式正确 | 待接手方复核 |  |
| 前端测试通过 | 已验证一次，需复跑 |  |
| 后端测试通过 | 待复跑 |  |
| Electron 测试通过 | 待复跑 |  |
| Windows 目录版可启动 | 待复核 |  |
| 安装包不含密钥和客户数据 | 待扫描 |  |
| 公司控制面可登录 | 待公司账号 |  |
| 客户/管理员权限隔离 | 待真实验收 |  |
| 激活码和积分测试数据处理完毕 | 当前未完成 |  |
| 剪辑 quote 到 export 通过 | 当前未形成正式 PASS |  |
| 页面播放和下载通过 | 待真实验收 |  |
| MP4 轨道、字幕和 hash 通过 | 待真实验收 |  |
| 数据库备份/恢复通过 | 待演练 |  |
| 公司已接管域名、账号、密钥和仓库 | 待负责人确认 |  |

## 18. 相关文档

- README.md：开发启动、端口和基础结构。
- 交接文档.md：较早版本的长篇部署、目录和 FAQ 说明，阅读时以当前源码为准。
- PROJECT_HANDOFF.md：此前开发任务的阶段性上下文，不替代本公司交接文档。
- ACTIVE_RELEASE_HANDOFF.md：历史发布续作记录，不代表当前发布状态。
- 交付前本地真实验收与剩余事项-2026-08-10.md：历史验收缺口。
- 交付文件/密钥边界-不含值-2026-09-11.md：只列密钥名称和边界，不含值。

## 19. 服务器维护 SSH 命令手册

本节用于公司工程师维护当前测试控制面和后续正式控制面。命令默认先读状态，涉及重启、部署、数据库写入和删除时必须有变更确认、备份和回滚方案。

### 19.1 当前测试环境已核对信息

| 项目 | 当前记录 | 交接要求 |
|---|---|---|
| SSH 主机别名 | videoinsight-server | 优先使用本机 SSH config 中的别名 |
| 登录用户 | devuser | 公司接手后应换成公司专用账号 |
| 当前测试主机 | 47.121.114.248 | 交接前确认是否仍为测试服务器 |
| systemd 服务 | videoinsight-control-plane.service | 不要凭服务名猜测，先 systemctl cat 核对 |
| 进程 | uvicorn control_plane | 以 systemctl show 和 ps 实际结果为准 |
| 内部监听 | 127.0.0.1:18080 | 外部访问通常经过 HTTPS 反向代理 |
| 运行目录 | /opt/videoinsight-control-plane/current/app | 仅作为当前记录，部署前重新核对 |
| 环境文件 | /opt/videoinsight-control-plane/config/control-plane.env | 只能查看权限和路径，不能把内容写入文档 |

已知权限边界：当前 devuser 可以做部分服务状态检查，但不能直接读取应用目录、环境文件或数据库；sudo 需要服务器管理员授权。没有 sudo 权限时，不要尝试猜密码或反复重试。

本文件不记录私钥路径、私钥内容、服务器密码、控制面 Token 或环境文件内容。

### 19.2 连接和身份确认

优先使用已经配置好密钥的 SSH 别名：

~~~powershell
ssh -o BatchMode=yes -o ConnectTimeout=10 videoinsight-server "hostname && whoami && date -Is"
~~~

如果公司没有 SSH 别名，使用公司密码库中登记的私钥路径，不要把实际路径写入文档：

~~~powershell
ssh -i "<公司受控私钥路径>" -o BatchMode=yes -o ConnectTimeout=10 devuser@<公司服务器地址> "hostname && whoami && date -Is"
~~~

本机只读检查 SSH 别名：

~~~powershell
ssh -G videoinsight-server | Select-String "^(hostname|user|port|identityfile) "
~~~

连接后必须确认三项：

1. hostname 是预期的测试或生产主机；
2. whoami 是公司授权的运维账号；
3. 当前时间和环境与变更窗口一致。

不要使用 StrictHostKeyChecking=no，不要把密码放在命令行、脚本参数或聊天中，不要把私钥复制到项目目录。

### 19.3 服务状态和健康检查

查看服务摘要：

~~~powershell
ssh videoinsight-server "systemctl status videoinsight-control-plane.service --no-pager"
~~~

只看是否运行：

~~~powershell
ssh videoinsight-server "systemctl is-active videoinsight-control-plane.service"
~~~

查看 systemd 实际配置，但不要把 EnvironmentFile 内容贴到聊天：

~~~powershell
ssh videoinsight-server "systemctl show videoinsight-control-plane.service -p MainPID -p User -p Group -p WorkingDirectory -p EnvironmentFiles -p FragmentPath"
ssh videoinsight-server "systemctl cat videoinsight-control-plane.service"
~~~

查看内部健康接口：

~~~powershell
ssh videoinsight-server "curl --fail --silent --show-error http://127.0.0.1:18080/health"
~~~

查看外部 HTTPS 健康接口时，使用公司登记的控制面域名：

~~~powershell
curl.exe --fail --silent --show-error https://<公司控制面域名>/health
~~~

如果外部地址失败但内部地址正常，优先检查反向代理、证书、DNS和防火墙；如果内部地址也失败，检查 systemd、应用日志和端口。

### 19.4 进程、端口、磁盘和资源

~~~powershell
ssh videoinsight-server "ps -ef | grep '[u]vicorn.*control_plane'"
ssh videoinsight-server "ss -ltnp | grep -E ':18080|:443'"
ssh videoinsight-server "df -h"
ssh videoinsight-server "free -h"
ssh videoinsight-server "uptime"
~~~

查看应用目录大小前先确认目录：

~~~powershell
ssh videoinsight-server "du -sh /opt/videoinsight-control-plane/current /opt/videoinsight-control-plane/config 2>/dev/null"
~~~

不要直接对 /、/opt 或整个服务器执行递归删除。清理日志、缓存或临时包前要先确认具体路径、所有者和恢复方式。

### 19.5 日志查看

最近 200 行：

~~~powershell
ssh videoinsight-server "journalctl -u videoinsight-control-plane.service -n 200 --no-pager"
~~~

最近一小时：

~~~powershell
ssh videoinsight-server "journalctl -u videoinsight-control-plane.service --since '1 hour ago' --no-pager"
~~~

只看错误：

~~~powershell
ssh videoinsight-server "journalctl -u videoinsight-control-plane.service -p warning..alert --since '2 hours ago' --no-pager"
~~~

现场跟踪：

~~~powershell
ssh videoinsight-server "journalctl -u videoinsight-control-plane.service -f"
~~~

日志排查时重点搜索：

- traceback、Exception、database、migration；
- 401/403 鉴权失败；
- 429 限流；
- 502/503 上游或供应商不可用；
- timeout、connection refused；
- credit、customer、admin、video editor。

日志中如果出现 Token、Cookie、Authorization、API key 或完整客户码，只记录脱敏后的时间、接口、状态码和错误类型，不复制完整值。

### 19.6 安全重启

只有在确认当前没有重要任务、已有备份或允许中断时才重启：

~~~powershell
ssh videoinsight-server "sudo systemctl restart videoinsight-control-plane.service"
ssh videoinsight-server "systemctl is-active videoinsight-control-plane.service"
ssh videoinsight-server "curl --fail --silent --show-error http://127.0.0.1:18080/health"
~~~

如果重启后健康检查失败，立即查看：

~~~powershell
ssh videoinsight-server "systemctl status videoinsight-control-plane.service --no-pager"
ssh videoinsight-server "journalctl -u videoinsight-control-plane.service -n 200 --no-pager"
~~~

同一故障最多自动重启一次。连续重启不能替代定位原因，可能造成任务重复、上游状态不一致或数据锁。

如果修改了 systemd unit 文件：

~~~powershell
ssh videoinsight-server "sudo systemctl daemon-reload"
ssh videoinsight-server "sudo systemctl restart videoinsight-control-plane.service"
~~~

### 19.7 发布和回滚的推荐流程

正式部署不要在服务器上直接 git pull，也不要直接覆盖 current 目录。推荐使用经过扫描和哈希核对的版本包：

1. 本地完成测试、密钥扫描和发布包检查；
2. 记录源码 commit、版本、包 SHA256；
3. 上传到服务器临时目录；
4. 服务器管理员核对 SHA256；
5. 解压到带版本号的新目录；
6. 安装依赖并做离线启动检查；
7. 原子切换 current 指向新版本；
8. 重启 systemd 服务；
9. 检查内部和外部健康接口；
10. 观察日志和关键 API；
11. 保留上一版本用于回滚。

上传示例，路径和包名必须替换成实际已审核文件：

~~~powershell
scp .\build\<已审核发布包>.zip videoinsight-server:/tmp/
ssh videoinsight-server "sha256sum /tmp/<已审核发布包>.zip"
~~~

服务器端的解压、切换目录和 systemd 操作需要公司管理员权限。没有 sudo 权限时，只能把包上传到约定位置并通知管理员，不能绕过权限。

回滚原则：

- 只回滚到已经通过健康检查的上一版本；
- 回滚前保存当前失败日志；
- 不回滚数据库迁移，除非迁移有明确的逆向脚本；
- 数据库迁移和应用版本必须配套；
- 回滚后再次检查登录、积分查询、报价和任务状态。

### 19.8 数据库和备份维护

数据库路径不能从猜测得出，应先从 systemd 的 EnvironmentFile、应用配置或公司运维记录确认。查看路径时只输出变量名和文件权限，不输出环境文件内容：

~~~powershell
ssh videoinsight-server "systemctl show videoinsight-control-plane.service -p EnvironmentFiles"
ssh videoinsight-server "sudo -u videoinsight find /opt/videoinsight-control-plane -maxdepth 4 -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -print"
~~~

如果是 SQLite，先确认没有正在写入的任务，再使用公司认可的备份目录。示例中的数据库路径和版本名只是占位符：

~~~powershell
ssh videoinsight-server "sudo install -d -m 700 /opt/videoinsight-control-plane/backups"
ssh videoinsight-server "sudo cp --reflink=auto --preserve=mode,timestamps <数据库路径> /opt/videoinsight-control-plane/backups/<数据库名>-before-<变更编号>.db"
ssh videoinsight-server "sudo sha256sum /opt/videoinsight-control-plane/backups/<数据库名>-before-<变更编号>.db"
ssh videoinsight-server "sudo -u videoinsight sqlite3 <数据库路径> 'PRAGMA integrity_check;'"
~~~

客户、激活码、积分和流水清理属于不可逆数据变更，必须同时满足：

- 公司负责人明确授权；
- 已确认是测试库；
- 已完成数据库备份并记录哈希；
- 已做只读数量统计；
- 在事务中执行；
- 执行后重新查询 admin、customer_codes、credit_accounts、credit_transactions、recharge_requests 和 auth_sessions；
- 保留变更时间、操作者、版本和结果；
- 不把数据库备份放入源码压缩包。

不要在 SSH 命令行中直接粘贴一串未经审查的 DELETE。不要用 DROP TABLE、删除整个数据库或删除整个数据目录代替业务清理。

### 19.9 常见故障处理

#### 服务显示 inactive 或 failed

~~~powershell
ssh videoinsight-server "systemctl status videoinsight-control-plane.service --no-pager"
ssh videoinsight-server "journalctl -u videoinsight-control-plane.service -n 200 --no-pager"
ssh videoinsight-server "systemctl show videoinsight-control-plane.service -p ExecMainStatus -p ExecMainCode -p MainPID"
~~~

先修复首个启动错误，再重启一次。不要连续重启掩盖错误。

#### 外部接口 502/504

~~~powershell
ssh videoinsight-server "curl --fail --silent --show-error http://127.0.0.1:18080/health"
ssh videoinsight-server "ss -ltnp | grep -E ':18080|:443'"
curl.exe -I https://<公司控制面域名>/health
~~~

内部健康正常而外部失败时，检查反向代理、证书、DNS和防火墙；内部也失败时，回到应用日志和 systemd。

#### 登录或管理接口返回 401/403

不要尝试猜密码、重复登录或复制 Token。先确认：

- 客户端和控制面是否同一版本；
- admin 是否仍存在且未被删除；
- 会话是否过期；
- systemd 是否加载了预期的环境文件；
- 当前请求是否到达正确的控制面。

#### 磁盘空间不足

~~~powershell
ssh videoinsight-server "df -h"
ssh videoinsight-server "du -xhd1 /opt/videoinsight-control-plane 2>/dev/null | sort -h"
~~~

先找出具体大文件，再由管理员按保留策略清理旧日志、旧包或临时文件。不要删除数据库、备份、当前版本或浏览器/媒体目录。

#### 服务正常但任务卡住

先查看任务状态、应用日志、数据库锁和上游供应商状态。不要直接重复提交可能计费的请求；先确认幂等键、操作记录和当前扣费状态。

### 19.10 服务器接管验收清单

- [ ] 公司专用 SSH 账号已创建；
- [ ] 私钥存于公司密码库或受控密钥系统；
- [ ] SSH 别名或主机地址经过人工核对；
- [ ] BatchMode 只读连接成功；
- [ ] 公司账号具备所需的 systemctl/journalctl 权限；
- [ ] 是否需要 sudo 已明确；
- [ ] service unit、WorkingDirectory、EnvironmentFile 已登记；
- [ ] 控制面内部健康检查成功；
- [ ] 外部 HTTPS 健康检查成功；
- [ ] 数据库实际路径和所有者已登记；
- [ ] 备份目录、保留周期和恢复演练已确认；
- [ ] 发布目录、上一版本和回滚方法已确认；
- [ ] 日志脱敏和访问权限已确认；
- [ ] 生产与测试主机、域名、数据库已明确区分；
- [ ] 公司负责人已确认谁有权执行客户数据删除。

## 20. SHA256 与 SSH 公钥/私钥交接

### 20.1 当前交付物 SHA256

以下值是当前本机交付文件的 SHA256 校验值。接收方复制文件后应重新计算，并与这里的值逐字比对；值不一致时不要运行或打包发布。

| 文件 | SHA256 |
|---|---|
| `交付文件/exports/VideoInsight-source-handoff-company-env-2026-09-11.zip` | 见同目录 `.sha256.txt` 记录 |
| `交付文件/exports/VideoInsight-company-configured-handoff-2026-09-11.zip` | 已撤销，不得交付 |

上表第二行的旧“配置版” ZIP 已作废，不得交付。源码包的哈希记录放在包外同目录，避免把哈希文本重新写入压缩包后改变压缩包自身哈希。曾生成的 `0.2.49-company-handoff` 候选安装包仅作本机历史构建证据，不属于本次公司交付物；当前部署正在更新时，应以公司最终确认的部署版本重新构建和验收正式安装包。

Windows PowerShell 计算方式：

~~~powershell
Get-FileHash -LiteralPath "C:\path\to\file" -Algorithm SHA256
~~~

Linux 计算方式：

~~~bash
sha256sum /path/to/file
~~~

SHA256 只能证明文件内容与记录值一致，不能证明文件来源可信。接收方还应通过独立渠道确认交付人、版本号和文件用途。

### 20.2 SSH 凭据交接边界

- 当前测试服务器使用过一把本机部署密钥；这把密钥的私钥不随源码、安装包、交接文档或聊天交付。
- 当前旧部署公钥只记录指纹，不把公钥正文作为公司的长期凭据：`SHA256:rJ0DLUWnPtQbjYszr4cAXMkuZQRjdGLMSDE7O0HFEgA`。
- 旧密钥属于原维护环境的历史凭据。公司接手后应新建公司名下密钥，验证新密钥可用后，再从服务器 `authorized_keys` 中移除旧公钥。
- 私钥不得提交 Git、放入 ZIP、写入 `.env`、粘贴到工单或聊天，也不得通过邮件明文传输。
- 如果旧私钥曾被复制到非受控电脑、网盘、日志或聊天中，应按已暴露处理：撤销服务器上的旧公钥并生成新密钥。

### 20.3 公司新建 ED25519 密钥

Windows PowerShell：

~~~powershell
$keyPath = "$env:USERPROFILE\.ssh\videoinsight_company_ed25519"
ssh-keygen -t ed25519 -C "videoinsight-company-<env>-<yyyymmdd>" -f $keyPath
ssh-keygen -lf "$keyPath.pub" -E sha256
~~~

Linux/macOS：

~~~bash
umask 077
ssh-keygen -t ed25519 -C "videoinsight-company-<env>-<yyyymmdd>" -f "$HOME/.ssh/videoinsight_company_ed25519"
ssh-keygen -lf "$HOME/.ssh/videoinsight_company_ed25519.pub" -E sha256
~~~

生成时设置公司密码库管理的 passphrase。私钥文件只保存在运维人员受控设备或公司的密码库/HSM；公钥文件可以通过公司的受控渠道交给服务器管理员。

核对公钥与私钥确实匹配：

~~~powershell
ssh-keygen -y -f "$env:USERPROFILE\.ssh\videoinsight_company_ed25519" | ssh-keygen -lf - -E sha256
~~~

Linux/macOS 将路径替换为 `$HOME/.ssh/videoinsight_company_ed25519`。该命令只输出指纹，不输出私钥内容。

### 20.4 在服务器安装公钥

由有权限的服务器管理员把公司 `.pub` 文件的一整行加入目标账号的 `~/.ssh/authorized_keys`。只安装公钥，不上传私钥。安装后先用新密钥验证只读连接：

~~~powershell
ssh -o BatchMode=yes -o IdentitiesOnly=yes -i "$env:USERPROFILE\.ssh\videoinsight_company_ed25519" <company-user>@<host> "id; hostname; systemctl is-active videoinsight-control-plane.service"
~~~

验证通过后再撤销旧公钥。不要使用 `StrictHostKeyChecking=no` 绕过主机身份校验，也不要把未知主机指纹直接写入 `known_hosts`。

### 20.5 当前服务器主机指纹

以下指纹来自当前本机 `known_hosts` 记录，仅作为交接时的比对线索；接收方必须从云厂商控制台、服务器管理员或其他独立可信渠道确认，不能仅凭本文件信任：

| 主机 | 类型 | SHA256 指纹 |
|---|---|---|
| `47.121.114.248` | ED25519 | `SHA256:i5AlvDjx65mUJRyrNIqzC3hxizUkGxWGT2KW+IZbds` |
| `47.121.114.248` | ECDSA | `SHA256:yVS/9nd9gvy0sY2lg9VouchvHEQaC5cRZB/n+vrmBKo` |

检查当前连接拿到的主机指纹：

~~~powershell
ssh-keyscan -t ed25519 <host> | ssh-keygen -lf - -E sha256
~~~

主机重装、迁移或更换 SSH 服务后指纹可能变化。出现变化时先暂停连接并人工核对原因，不要为了继续操作而删除旧记录或关闭校验。

### 20.6 SSH 交接验收

- [ ] 公司已创建独立 SSH 账号或确认服务账号的责任人；
- [ ] 公司新密钥的私钥未进入源码、ZIP、聊天、日志或工单；
- [ ] 公司新密钥的公钥已通过受控渠道安装；
- [ ] 新密钥指纹已登记在公司的密码库/资产台账；
- [ ] 使用新密钥的 `BatchMode` 只读连接成功；
- [ ] 新账号具备实际维护所需的最小权限；
- [ ] 已从服务器移除旧部署公钥，或明确记录移除时间和负责人；
- [ ] 主机 ED25519 指纹已通过独立渠道确认；
- [ ] 交付 ZIP 和安装包的 SHA256 已重新计算并一致；
- [ ] 若需要保留旧测试环境，旧密钥已标记为仅测试用途并设置失效/轮换日期。

本节故意不包含任何私钥正文、密码、Token、`.env` 内容或可直接登录的秘密值。交接时应通过公司批准的密钥管理流程完成实际凭据转移。

## 21. 干净电脑验收与 GitHub 交付硬门槛

### 21.1 本次已验证的范围

2026-09-11 已从 GitHub 的 `codex/company-handoff-20260911` 分支完成一次全新克隆，未复用原开发目录的 `.git`、`.venv`、`venv`、`node_modules`、`.env`、数据库、日志、`work/`、`outputs/`、`build/` 或浏览器状态。运行时必需的 `data/templates/builtin.json` 已作为版本文件随分支交付。

已确认：

- `scripts/setup_windows.ps1` 能从零创建 `.venv`；
- 根目录和后端两份 Python 运行依赖安装成功；
- 前端依赖按 `package-lock.json` 安装成功；
- `.env` 能从 `.env.example` 自动生成，没有带入个人配置；
- FFmpeg、FFprobe、Chrome/Edge 检测通过；
- 后端 `/health` 返回 200，桌面协议和公司控制层标志正确；源码本地演示模式与正式桌面模式边界清晰；
- 前端 `http://127.0.0.1:1001` 返回 200；
- 前端 23 个测试文件、204 个测试通过；
- `npx tsc --noEmit` 和 `npm run build` 通过。
- 在同一干净副本生成了 Windows x64 安装包；安装包内含 Electron、打包后的 FastAPI 后端及 FFmpeg/FFprobe，不要求接收方另外安装 Python/Node/FFmpeg 才能启动客户端；
- 直接启动解包版 EXE 后，本地 `/health` 返回 200，且 `control_plane_enabled=true`，已绑定公司控制层地址 `https://xmt.syszr.cn`。
- 正式桌面启动器回归通过：控制层有效时使用 `ASR_MODE=cloud`、`VIDEO_EDITOR_PROVIDER_MODE=aliyun`、`COPYWRITING_MODE=production`；不会把个人供应商密钥注入客户端。

这证明“当前工作区复制成干净源码后，在一台具备网络和基础运行时的 Windows 电脑上可以自举启动”。这不等于已经证明公司的具体电脑、网络、杀毒软件策略和供应商账号一定没有环境差异。

### 21.2 当前验证结果与剩余门槛

补装 `requirements-dev.txt` 后，当前交付分支全量 Python 测试通过；少量依赖 `work/` 阶段性证据目录的历史测试在证据不存在时按明确原因跳过。前端测试、TypeScript、Vite 构建和桌面后端启动也已通过。测试输出仍可能出现第三方依赖的弃用警告，不是启动失败。二开验收应区分：

- 运行门：先执行 `start.bat`、页面和 `/health` 检查；
- 常规代码门：执行后端目标模块测试和前端 204 个测试；
- 历史证据门：只有在另行提供 `work/` 证据目录时才运行对应验收脚本；
- 开发测试依赖：执行 `python -m pip install -r requirements-dev.txt` 后再运行需要的 Pytest/Ruff 检查。

因此当前结论是：

`源码首次安装和前后端启动：PASS（已在干净副本验证）`；

`全量 Python 测试：PASS（可选历史 work/证据缺失时按规则跳过）`；

`本机正式桌面包启动与客户包安全校验：PASS（control_plane_enabled=true，未包含个人密钥）`；

`公司控制层当前可用性：FAIL（2026-09-11 检查 https://xmt.syszr.cn/health 两次均返回 502；只读 SSH 显示 videoinsight-control-plane.service 为 inactive/dead，进程被 SIGTERM 结束）`；

`正式视频剪辑 quote → user_confirm → generation → export → 页面播放 → 下载：PENDING（尚未在公司账号和公司电脑完成）`；

`公司实际电脑验收：PENDING（尚未在客户电脑完成）`。

在控制层服务恢复前，不要把“客户端能启动”表述成“云端业务已跑通”，也不要把客户端改回供应商 Sandbox 作为绕过方案。公司运维应先恢复控制层进程和 HTTPS 反向代理，再重新执行 `/health`、客户登录、激活码、积分、转写授权和正式剪辑验收。

### 21.3 GitHub 分支与工作区一致性

本次已完成“提交、推送、全新 GitHub 克隆、依赖安装和启动检查”。公司接收时必须固定使用 `codex/company-handoff-20260911` 分支或同一提交的源码 ZIP，不要再从旧的 `feature/brand-emphasis-style` 远端分支取代码。完成公司电脑实装验收前，仍不要承诺所有网络、杀毒软件策略和供应商账号环境都无差异。

### 21.4 公司电脑首次验收表

- [ ] 记录公司电脑 Windows 版本、CPU、内存、磁盘空间和网络限制；
- [ ] 安装或确认 Python 3.12+、Node.js 18+、PowerShell、FFmpeg/FFprobe；
- [ ] 使用 `codex/company-handoff-20260911` 或最终源码 ZIP，不使用旧远端分支；
- [ ] 确认源码中只有 `.env.example`，没有个人 `.env`、数据库、日志、浏览器目录和 SSH 私钥；
- [ ] 确认 `data/templates/builtin.json` 存在，模板页不依赖开发机目录；
- [ ] 双击 `start.bat`，首次安装完成且没有报错；
- [ ] `http://localhost:1001` 页面可打开；
- [ ] `http://localhost:2001/health` 返回正常；
- [ ] 目标核心页面实际打开并完成一次不计费的沙箱流程；
- [ ] 本地视频样例能被 FFmpeg/FFprobe 读取；
- [ ] 若使用公司控制层，HTTPS 地址、公司账号和公司供应商配置已单独确认；
- [ ] 记录验收结果、日志路径、最终 ZIP/EXE SHA256 和负责人。

### 21.5 Windows 安装包状态

本次曾生成一个 Windows x64 候选安装包：

- 文件：`交付文件/exports/VideoInsight-Windows-installer-0.2.49-company-handoff.exe`；
- SHA256：`376930092cb5c6c5b71e963b6ec55acd4ab35cbe123aeab8a58af74f9cc84d60`；
- 构建版本：`0.2.49-company-handoff`；
- 控制层地址：`https://xmt.syszr.cn`；
- 交付包不含永久供应商密钥、密码、Token、个人 `.env` 或 SSH 私钥；
- 安装包绑定公司控制层并使用其云端能力，密钥仍由公司密钥管理系统保管；它不依赖本机 Sandbox 才能完成正式云流程。

该文件名是为避免覆盖旧的 `0.2.48-dev.*` 测试构建而使用的临时候选标识，不代表 `0.2.49` 已正式发布。由于公司部署仍在更新，本次源码交接不包含这个候选 EXE。公司完成部署版本确认后，应从本交付源码重新生成与部署版本一致的正式安装包。双击安装包只能验证“客户端能否启动”；登录、激活码、云端供应商调用和正式剪辑链路仍必须在公司提供的账号、网络和权限下做一次业务验收，不能用本机健康检查替代。
