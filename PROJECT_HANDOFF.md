# VideoInsight 项目交接（0.2.40）

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
