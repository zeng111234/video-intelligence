# VideoInsight 项目交接（0.2.40）

## Luna 立即执行：通用“动态口播精剪”模板（2026-09-03）

### 目标

把同事用剪映 AI 生成的下列视频作为**正向质量标杆**，在正式 `/video-editor`
链路中做成可迁移到陌生客户视频的通用模板，而不是写死样片文案：

`C:\Users\zeng\xwechat_files\wxid_72l72v76f70a22_385d\msg\video\2026-09\f55c8b06254cfbd208e5b4054cc350d7.mp4`

标杆特征：大号单行字幕、黄色关键词、与口播语义匹配的丰富贴纸、入场动效、
声音标点、真实全屏 B-roll，整体节奏明显优于现有成片。根目录
`v4-burned.mp4` 不是标杆，它是叠加了错误字幕的本地二次渲染。

### 实时工作区（必须保留）

- 分支：`feature/brand-emphasis-style`。
- 禁止 `reset/checkout/clean/push`，不部署，不调用付费接口。
- 既有未跟踪 `v4-frame-*.png`、`v4.ass` 属于用户文件，不删除。
- 本轮修改了：
  - `src/services/style_presets.py`
  - `src/services/motion_design.py`
  - `src/services/video_editor_cloud.py`
  - `src/services/video_editor_workflow.py`
  - `project/backend/app/api/v1/video_editor.py`
  - `project/frontend/src/api/client.ts`
  - `project/frontend/src/pages/VideoEditorPage.tsx`
- 已验证：上述 Python 文件 `py_compile` 通过，`git diff --check` 通过（只有
  LF/CRLF 警告）。**尚未跑单测、前端构建、正式渲染和页面验收，不能宣称完成。**

### 本轮已经写入但尚未验收的内容

1. `talking-head-brand-emphasis-v1` 改为“动态口播精剪”：取消错误的常驻品牌头和
   中央大卡片；加入 54px 单行字幕、黄色关键词、贴纸/音效/B-roll 策略。
2. 新任务由前端和 API 默认携带该 preset，并持久化进 `provider_payload` 和
   `edit_plan`；旧任务仍可使用 pure-adaptive。
3. 删除 `_local_title_candidates` 中“会员制/小店长/烧烤店”样片特判。
4. 陌生领域字幕增加 transcript-grounded 关键词事件；关键词必须来自对应 ASR
   segment，不允许虚构或固定答案。
5. 正式 release director 开启已有授权矢量素材库；B-roll 仍先匹配素材库，缺口才
   走现有生图能力，失败再降级。贴纸不计作真实 B-roll。
6. 关键词贴纸增加黄色弹出样式，并把部分贴纸移到上半屏；音效从统一正弦音扩展为
   本地合成的 pop/impact/whoosh/chime 组合，不引入版权音频文件。

### Luna 的最短执行顺序

1. 先运行 `git status --short --branch`，阅读本节和根目录 `AGENTS.md`；不得重做探索。
2. 审查当前 diff，优先修正参数透传和重复字段：
   - 确认 `style_preset_id` 从页面 → API → `create_cloud_batch/create_batch` →
     item → analysis → review → local renderer 全程不丢失；
   - `_batch_payload` 的 preview 与最终 ASS 必须使用同一 preset；
   - preset 的 `playback_rate=1.08` 必须真正写入本地导出 task，不能仍被常量覆盖。
3. 补最小回归测试：
   - `tests/test_style_presets.py`：深拷贝、动态模板参数、未知 ID 回退；
   - `tests/test_motion_design.py`：生活/美业/教程三类陌生文本均产生少量 grounded
     关键词事件，`semantic_text` 必须出现在 `source_text`；
   - `tests/test_video_editor_workflow.py`：生产代码不含上述三个样片固定标题；音效
     有延迟且包含多种本地合成 profile；
   - 后端 API 和前端测试：默认请求包含 `talking-head-brand-emphasis-v1`。
4. 只运行定向测试、前端对应测试/build、`py_compile`、`git diff --check`；失败只修
   根因，不扩大重构。
5. 用陌生源片
   `C:\Users\zeng\Videos\大树老师无剪辑素材7.13版.mp4` 截取 20–30 秒，必须从
   `http://localhost:1001/video-editor` 正式走 quote → 确认 → generation → export；
   使用本地模型和本地 FFmpeg，不发起付费云调用。
6. 实际验收输出：H.264 + AAC + 烧录字幕；至少 3 个与口播绑定的视觉事件；至少
   1 个真实授权全屏 B-roll、1 个 PiP 或局部强调；有可听但不盖人声的音效；无固定
   样片词、无双字幕、无黑边/遮脸/遮字幕。页面必须能播放并下载 200。
7. 若素材库没有语义匹配的真实 B-roll，明确记录缺口，**不得拿贴纸或生图静帧冒充
   真实 B-roll，也不得宣称全部完成**。

### 验收输出只报告

- 修改文件；定向测试/前端构建结果；正式任务 ID；最终 MP4 路径与 ffprobe；
- 素材库、生成图、程序化兜底各用了多少；音效事件数及来源；
- 页面播放/下载状态；与正向标杆仍存在的可见差距；是否产生第三方费用（默认应为 0）。


## 当前视频精剪暂停点（2026-08-25）

> 本节是当前自动口播精剪 Goal 的最新交接；下方 0.2.40 发布信息是历史发布交接，不能覆盖本节的实时状态。

### 目标与边界

- 当前 Goal 仍为 `active`：修复 104.7 秒完整自动口播的生产路径，用真实源音频和统一导演时间线生成可验收成片，修正视觉事件分布、字幕错词/断词/标点与质量门假阳性，并完成 localhost:1001 页面播放、关键时段检查和下载验收。
- 不得在未实际观看完整片和页面证据齐全前宣称完成；不调用新的付费/云端任务，不发布，不 push，不 reset/checkout/clean，不覆盖其他 crawler/frontend 改动。
- 用户最新决定：继续使用现有 `jieba==0.42.1` 通用断句；核心规则只保留普适中文边界，业务术语改为可注入的 transcript glossary，不为样片新增固定答案。

### 暂停时实时状态

- 工作树：`main...origin/main`，存在大量既有未提交改动和未跟踪证据/测试文件，必须保留。
- 当前没有 `ffmpeg`、`ffprobe`、`pytest` 或云端任务在运行；只观察到 Node 前后端常驻进程。
- 最近一次轻量核验：`git diff --check` 退出成功，仅有 Git 的 LF/CRLF 警告。
- 不需要恢复或终止任何渲染任务；下次可直接从“核心字幕词表收窄与 glossary 注入”开始。

### 最近成功产物（不是最终通过）

- MP4：`C:\Users\zeng\Desktop\video\data\video_edits\edit-local-74e45f22bf.mp4`
- ASS：`C:\Users\zeng\Desktop\video\data\video_edits\edit-local-74e45f22bf.ass`
- 字幕 manifest：`C:\Users\zeng\Desktop\video\data\video_edits\edit-local-74e45f22bf.subtitle-manifest.json`
- FFprobe：104.633008 秒，720x1280，H.264 + AAC，文件 54,151,227 字节。
- SHA256：
  - MP4 `B03BEE3CE7D045AA10F4B98F966D3EA50F5BA9FDB7F82F1B91F3BA39FDCA5267`
  - ASS `F51D7F3C1C96EB44C6BB41EE0A09015281DF316AF4F83895E8D542857ED21415`
  - manifest `58EC9C3D4759CEA66D3C80FBEBF739CBEA300445AA61847FB6EB4BF37FE05AE5`
- 上一轮已记录定向结果：字幕云服务相关 14 项通过，工作流相关 6 项通过；这些测试不能替代完整片和页面验收。

### 尚未通过的硬门

- 最近质量结果仍为整体 `passed=false`、`publish_claim_allowed=false`。
- `transcript_source_identity_gate` 的 source SHA、时长、时间源通过，但 transcript SHA 未通过；需要让任务输出的 reviewed transcript、字幕 manifest、烧录 ASS 和 identity hash 使用同一份事实源，不能放宽门禁。
- 视觉门仍未通过：最近记录的真实 B-roll 为 3 个、覆盖约 6.88%，虽然视觉节奏事件和安全重构图存在，但尚未完成后半段真实语义视觉的完整人工验收。
- 当前没有本轮修复后的 104.7 秒新片、10 秒抽帧证据、页面播放/拖拽/下载证据；因此 Goal 必须保持 active。

### 下次安全执行顺序

1. 检查 `src/services/video_editor_cloud.py` 的 `_CAPTION_BREAK_*`、`_CAPTION_COMPOUND_WORDS`，移除样本驱动的客户/企业/数据库/工厂等业务词，只保留通用规则。
2. 给现有 jieba 词法辅助函数增加可选 `transcript_glossary` 注入，并沿 preview、ASS、质量报告使用同一参数；不新增整句特判。
3. 补生活、美业、知识教程三类陌生文本测试，以及 glossary 复合词不拆、反过拟合静态测试。
4. 修复 reviewed transcript 到任务 `subtitle_segments_json` 的持久化，使 transcript identity hash 与实际烧录源一致；再跑定向测试。
5. 仅在代码门通过后，重渲染同一 104.7 秒源片，生成新的独立证据目录；用 ffprobe、关键帧和实际页面验证，失败则如实保持 active。

### 重要恢复命令

```powershell
cd C:\Users\zeng\Desktop\video
git status --short --branch
$env:VIDEO_EDITOR_LOCAL_ACCEPTANCE_NO_PROVIDER='1'
python -u -c "from project.backend.app.core.deps import get_repository,get_video_editing_service; from src.services.video_editor_workflow import VideoEditorWorkflowService; repo=get_repository(); svc=VideoEditorWorkflowService(repo,get_video_editing_service(),None,None); svc._run_local_preview_export('edit-local-74e45f22bf'); print('rerendered')"
```

上述渲染命令会重新生成同一任务产物，只有在确认要继续验收时运行；不要重复发起云端任务。

> 最后核验：2026-08-15 18:25（Asia/Shanghai）
> 这是后续维护的首要交接文档。开始操作前先读根目录 `AGENTS.md`；实时 Git、服务器和公网状态与本文冲突时，以实时只读检查为准。

## 1. 交接目的

让没有旧聊天记录、也不使用 GPT/Codex 的维护者能够：

- 找到当前正式源码、安装包和服务器；
- 启动本地开发环境并运行发布检查；
- 判断线上是否健康、客户拿到的是否是正确 Setup；
- 在不泄露凭据、不重复付费、不破坏共享服务器其他站点的前提下继续维护；
- 收集朋友电脑上的真实安装结果并处理后续问题。

## 2. 当前权威状态

| 项目 | 已验证状态 |
|---|---|
| GitHub | 私有仓库 `https://github.com/zeng111234/video-intelligence` |
| 分支 | `main`，本地与 `origin/main` 一致，工作区干净 |
| 0.2.40 发布完成提交 | `31a975ff006c81d19b42d03b7f0225fa5d7f09e4` (`release: complete 0.2.40 windows package`) |
| 0.2.40 源码提交 | `1d2b0ae9ab23404b8a4361702dfcc5db849bdbbc`；之后三个提交只记录 Windows 发布状态 |
| 发布登记 | `deploy/control-plane/release_versions.json`：0.2.40 为 `complete`，无进行中版本，下一候选为 0.2.41 |
| 正式域名 | `https://xmt.syszr.cn` |
| 公网健康 | `/health` 返回 `status=ok`、`release_version=0.2.40` |
| Windows 更新 | `/desktop-updates/latest.json` 返回 0.2.40 |
| 正式 Setup | 370,769,920 字节；SHA256 `83991ccb801edb8e035a1dc106cd52735cf4177f50e0a03281ee94f139465789` |
| 控制层 ZIP | 820,145 字节；SHA256 `aea0b3a43f7b3ea5297c8dfba218217fc722ccd40532e432c86833d546205b1a` |
| 真实付费验收 | 文案、云转写、云剪辑、数字人全部通过；实际消费 0.12 积分 |

0.2.40 已部署、已发布、已推送 GitHub。不得重新构建、重新部署或再次执行同一版本付费验收。

## 3. 当前唯一现实验证缺口

0.2.40 尚未收到最初安装失败的朋友电脑上的双击安装回报。自动化、正式构建、公网整包哈希均已通过，但这不能替代受影响电脑的真实安装。

下一位维护者的第一步：让一台曾失败的电脑直接下载并双击 0.2.40 Setup。成功后保存验收报告；失败时只收集日志，不让朋友输入 PowerShell 命令，也不要立刻重打同版本。

- 公网下载：`https://xmt.syszr.cn/desktop-updates/VideoInsight-0.2.40-Setup.exe`
- 引导程序日志：`%LOCALAPPDATA%\VideoInsight\data\logs\installer-bootstrap.log`
- 安装验收报告：所选运行目录下 `data\logs\install-acceptance-*.txt`
- 若失败，先保留旧数据、完整日志和截图，再判断是安装器问题、终端安全策略还是本机目录异常。

## 4. 0.2.40 完成的修复

1. 安装版不再固定占用端口；Electron 选择空闲回环端口并传给 Python，实际端口写入 `data\desktop-runtime.json`。
2. 管理端新增真实扣费流水汇总，可按功能和客户查看，不用手工翻账目。
3. 云剪辑首屏不再等待历史任务和 BGM 等非必要请求。
4. 安装验收报告统一写入运行数据的 `data\logs`，便于普通用户找到。
5. 智能创作首屏不再被批次历史、发布账号和数字人附加信息阻塞。
6. FFmpeg/FFprobe 子进程统一隐藏控制台窗口。
7. AI 转写提示改为互斥状态，不再同时显示旧成功提示和当前处理中提示。
8. 安装器允许接管预先创建但为空的 `VideoInsight` 程序目录。

同时修复了安装失败的关键边界：

- 不再创建容易被终端策略拒绝的“卸载 VideoInsight”开始菜单快捷方式；卸载仍保留在 Windows“应用和功能”中。
- 验收器先等待服务健康，再检查数据库。
- 新增 `.videoinsight-runtime.json` 运行目录标记，并严格兼容 0.2.39 的真实 SQLite 旧数据。
- 自动验收前检查运行状态新鲜度，避免读取上一次启动残留状态。
- 卸载脚本不再要求不存在的卸载快捷方式。

主要修复提交：`77fa77c`、`1d2b0ae`。

## 5. 架构与端口

```text
Windows Electron 客户端
  ├─ 本机动态 127.0.0.1 端口：同时提供已构建前端和本机 FastAPI
  ├─ 本机数据、浏览器、下载、FFmpeg 和预览
  └─ HTTPS → https://xmt.syszr.cn
                  └─ 公司控制层：登录、激活码、积分、定价、真实收费供应商
```

- 源码开发前端：`http://127.0.0.1:1001`
- 源码开发后端：`http://127.0.0.1:2001`
- 安装版：没有固定端口；以所选运行目录的 `data\desktop-runtime.json` 为准。
- 公网统一使用 HTTPS 443。
- legacy Streamlit 源码仍保留，但不是正式入口。

## 6. 重要文件和目录

| 路径 | 用途 |
|---|---|
| `AGENTS.md` | 产品、成本、重试和真实页面验收规则 |
| `README.md` | 开发启动、结构和功能总览 |
| `project/backend/` | FastAPI 后端与 API 测试 |
| `project/frontend/` | React、Electron 与前端测试 |
| `src/` | 领域模型、仓储、服务和供应商适配器 |
| `scripts/start_all_services.ps1` | 本地开发一键启动 |
| `scripts/check_release.ps1` | 正式发布全量自动门禁 |
| `scripts/install_windows_desktop.ps1` | Windows 安装主脚本 |
| `scripts/verify_windows_install.ps1` | 安装后自动验收 |
| `scripts/build_final_windows_release.ps1` | 有状态且不可重复的正式 Windows 构建入口 |
| `scripts/run_paid_release_acceptance.py` | 真实付费发布验收，默认不执行付费 |
| `deploy/control-plane/release_versions.json` | 正式版本唯一登记表 |
| `deploy/control-plane/native-systemd/README.md` | 当前共享服务器唯一允许的部署/验证/回滚手册 |
| `PRODUCTION_RELEASE_CHECKLIST.md` | 正式交付检查清单 |
| `build/final-windows-release-0.2.40/VideoInsight-0.2.40-Setup.exe` | 本机构建机的唯一客户 Setup；被 Git 忽略 |
| `build/control-plane-ready-0.2.40/VideoInsight-control-plane-0.2.40.zip` | 0.2.40 控制层发布包；被 Git 忽略 |
| `build/paid-release-acceptance-0.2.40.json` | 0.2.40 付费验收证据；被 Git 忽略 |

重新克隆 GitHub 仓库不会带回 `build/`、`.venv/`、`node_modules/`、数据库、媒体或安装包，这是刻意的安全设计。需要给客户 Setup 时优先使用上面的公网地址；不要在另一台电脑重建 0.2.40。

## 7. 本地开发与验证

环境：Windows、PowerShell、Python 3.12+、Node.js 18+、Chrome/Edge；本地视频处理需要 FFmpeg。

推荐启动：

```powershell
cd C:\Users\zeng\Desktop\video
.\scripts\start_all_services.ps1
```

也可以双击根目录 `start.bat`。启动后访问前端 1001、后端 2001。

常用只读检查：

```powershell
git status -sb
git fetch origin main
git rev-parse HEAD
git rev-parse origin/main
Invoke-RestMethod https://xmt.syszr.cn/health
Invoke-RestMethod https://xmt.syszr.cn/desktop-updates/latest.json
```

完整发布检查（耗时较长，但不应调用真实收费供应商）：

```powershell
.\scripts\check_release.ps1
```

前端单独验证：

```powershell
cd project\frontend
npm test
npm run test:update
npm run build
```

涉及前端流程的改动，除了测试和构建，还必须在实际运行页面验证核心路径。

## 8. 当前服务器边界

服务器为共享主机，只允许操作 VideoInsight 自己的范围：

- 公网 IP：`47.121.114.248`
- systemd 服务：`videoinsight-control-plane.service`
- unit：`/etc/systemd/system/videoinsight-control-plane.service`
- 当前版本：`/opt/videoinsight-control-plane/releases/0.2.40`
- 当前链接：`/opt/videoinsight-control-plane/current`
- 配置：`/opt/videoinsight-control-plane/config/control-plane.env`
- 数据：`/opt/videoinsight-control-plane/runtime/data/video_intelligence.db`
- 备份：`/opt/videoinsight-control-plane/backups/`
- 本机监听：`127.0.0.1:18080`
- 服务 UID/GID：`996/994`
- 0.2.40 升级前快照：`/opt/videoinsight-control-plane/backups/videoinsight-control-plane-pre-upgrade-0.2.40-20260815T090911Z-9895.zip`
- 本域名更新目录：`/www/wwwroot/xmt.syszr.cn/desktop-updates/`
- 本域名 vhost 文件：`/www/server/panel/vhost/nginx/xmt.syszr.cn.conf`

绝对禁止：

- 使用 Docker/Caddy 版 `deploy.sh` 操作当前服务器；
- 修改其他域名、其他 vhost、其他 systemd 服务或全局 Nginx 配置；
- 把服务器密码、`.env`、激活码、管理员密码或供应商密钥写进 Git、命令输出、截图或文档；
- 为了“试试看”重复上传、重复付费、重复构建同一版本。

SSH 用户是 `root`，但密码不在本文记录。需要时向项目所有者获取，并只在交互式密码提示中输入。

服务器只读验收命令：

```sh
cd /opt/videoinsight-control-plane/tools/native-systemd
/bin/bash verify.sh 0.2.40 996 994
systemctl show videoinsight-control-plane.service \
  -p ActiveState -p SubState -p NeedDaemonReload -p FragmentPath --no-pager
```

升级、备份或回滚前必须完整阅读 `deploy/control-plane/native-systemd/README.md`，不得凭记忆拼命令。

## 9. 凭据、成本与外部依赖

只记录名称，不记录值：

- 正式构建只读验证：`VIDEOINSIGHT_VERIFY_ACTIVATION_CODE`、`VIDEOINSIGHT_VERIFY_ADMIN_USERNAME`、`VIDEOINSIGHT_VERIFY_ADMIN_PASSWORD`
- 付费报告验证：`VIDEOINSIGHT_ACCEPTANCE_ACTIVATION_CODE`
- 真实付费二次确认：`VIDEOINSIGHT_AUTHORIZE_PAID_ACCEPTANCE`
- 供应商配置名称以 `.env.example`、`deploy/control-plane/.env.example` 和配置校验脚本为准。

任何可能收费的操作必须先说明预计费用、上限和是否由第三方收费，再获得明确授权。网络或工具连接失败最多自动重试一次。付费 POST 结果未知时不得直接重发，先按幂等键核对。

0.2.40 的真实付费验收已经完成且消费 0.12 积分，不得重复。真实平台发布客户内容从未获得本次交接授权。

## 10. 发布纪律

- 0.2.40 已烧录完成，永远不要同版重打；下一次代码发布使用登记表候选 0.2.41。
- 控制层和 Windows 必须使用同一版本；先部署并验证控制层，再做该版本的付费验收和 Windows Setup。
- `build_control_plane_bundle.ps1`、`build_final_windows_release.ps1` 都有防重复状态机。每次状态变化必须单独提交，脚本提示重新运行时才可继续。
- 正式 Windows 构建只能由一台构建机执行一次。失败后检查登记状态，不能删除锁、复制旧包或换机器重建同版。
- 上传顺序始终是：临时文件 → 远端大小/SHA256 → 原子落位 EXE → 最后原子替换 `latest.json` → 公网整包下载复核。
- 安装包、控制层 ZIP、付费报告、更新 EXE、数据库和媒体都被 Git 忽略，不要用 `git add -f` 强行提交。

详细流程见 `PRODUCTION_RELEASE_CHECKLIST.md` 和 `deploy/control-plane/native-systemd/README.md`。

## 11. 已完成验证

- Python/FastAPI/控制层完整发布测试通过。
- 服务、适配器和 legacy 分组测试通过。
- React：23 个测试文件、184 项测试通过。
- Electron 更新：11 项测试通过。
- TypeScript 检查和生产前端构建通过。
- 管理端积分用量、云剪辑页、智能创作页已在真实运行页面验收。
- 原生 systemd `verify.sh 0.2.40 996 994` 通过，服务为 active/running。
- 文案、ASR、云剪辑、数字人真实闭环验收全部通过，实际消费 0.12 积分。
- 公网 Setup 已完整下载，大小和 SHA256 与本地唯一制品一致。
- 一次性凭据文件、上传临时文件和公网验收临时下载均已删除。

未验证或不能宣称：

- 尚无受影响朋友电脑对 0.2.40 的安装回报；
- 未购买代码签名证书时仍可能出现 Windows SmartScreen 提示；
- 未授权真实发布客户内容到任何平台；
- 自动测试通过不等于所有客户电脑环境都不会被安全软件拦截。

## 12. 下一步安全执行顺序

1. 让一台曾失败的朋友电脑下载并双击 0.2.40 Setup。
2. 成功：保存 `install-acceptance-*.txt`，记录系统版本和安装目录即可，不做额外付费测试。
3. 失败：收集 `installer-bootstrap.log`、验收报告和截图；先判断失败发生在解包、目录迁移、服务健康还是快捷方式阶段。
4. 只有确认需要代码修改时，才从最新 `main` 开始；版本使用 0.2.41，不改写 0.2.40。
5. 修复后运行相关测试、完整发布门禁和真实页面验收；涉及费用、服务器写入、发布或删除时再次向所有者确认。

## 13. 交接验收标准

- 新维护者能从 GitHub 克隆仓库并用 `start.bat` 或 `scripts/start_all_services.ps1` 启动 1001/2001。
- 能通过公网接口确认线上版本和 Setup 清单均为 0.2.40。
- 能指出正式 Setup 的精确大小与 SHA256，并知道 GitHub 不保存该 EXE。
- 能找到朋友安装失败时需要的两类日志，不要求朋友输入技术命令。
- 能明确区分已完成验证、朋友电脑待复测、真实平台发布未授权三种状态。
- 不需要任何旧聊天记录，也不会接触其他域名、重复 0.2.40 发布或泄露凭据。
