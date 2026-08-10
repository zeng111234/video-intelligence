# VideoInsight 公司服务器控制层

这套服务器只负责激活码、账号、积分、充值、定价和真实收费供应商。爬虫、浏览器登录、素材下载、预览及适合本机完成的处理仍在客户电脑上运行；爬虫不扣积分。客户安装包不包含公司供应商密钥。

从开发电脑准备上传文件时，先确定一个从未用于交付的新版本号，再运行 `scripts/build_control_plane_bundle.ps1 -Version "0.2.1"`（示例版本仅供说明，实际必须与本次正式版本一致）。它只生成一个小型服务器部署 ZIP，并把本机清单中已授权、训练完成的共享形象和声音编号安全带入首次部署；不会携带训练样本、触发重新训练或产生供应商费用。ZIP 不包含 `.env`、供应商密钥、数据库、日志、媒体、备份或 Windows 安装包；在服务器解压后进入 `deploy/control-plane` 即可按下方步骤部署。

## 第一次部署（只需一次）

1. 准备一个已解析到公司服务器公网 IP 的域名，例如 `video-api.company.com`。
2. 只上传 `build_control_plane_bundle.ps1` 生成的 `VideoInsight-control-plane-<版本号>.zip`，不要上传整个开发目录；在服务器解压后进入 `deploy/control-plane`，复制 `.env.example` 为 `.env`。
3. 先只填写 `CONTROL_PLANE_DOMAIN`、相同域名的 `CONTROL_PLANE_ALLOWED_HOSTS` 和随机的 `ADMIN_PASSWORD`，其余供应商保持 `sandbox`。
4. 执行下面一个命令：

```sh
chmod +x deploy.sh backup.sh restore.sh validate_env.sh authorize_asr.sh verify_live.sh && ./deploy.sh
```

脚本会自动检查配置、初始化目录权限、建库、启动服务并检查就绪状态。Caddy 会自动申请和续期 HTTPS 证书。服务器安全组只向公网开放 TCP 80、TCP/UDP 443；不要开放容器内部的 8080 端口。

部署后检查：

```text
https://你的域名/health
https://你的域名/ready
```

两处都返回 `ok/ready` 后，基础控制层已经可用；此时即使供应商仍为 `sandbox`，激活码、积分和管理员功能也可以工作，且不会产生真实供应商费用。

### 2 核 4G 共享服务器

默认 `.env.example` 已按 2 核 4G、同时运行另一个轻量系统的情况设置资源保护：控制层最多使用 1 核/1GB，Caddy 最多使用 0.25 核/256MB，并限制容器日志数量和大小。这些值是上限而不是预占；爬虫、浏览器、下载、FFmpeg 和本机预览仍在客户电脑执行。部署后根据实际监控调整 `CONTROL_PLANE_*_LIMIT`、`CADDY_*_LIMIT` 即可，不需要重新构建桌面安装包。若另一个系统本身持续占满 CPU 或内存，应先扩容，不能靠取消上限抢占资源。

DNS 与 HTTPS 证书生效后，再运行一次完整的外部验收：

```sh
./verify_live.sh
```

它检查 HTTPS 跳转、数据库、安全响应头、权限隔离、无效登录和更新清单；不创建内容、不扣积分、不调用任何供应商。正式安装包生成前还会通过桌面端完成内部测试激活码和管理员的只读登录验收，因此无需把这些账号值传给脚本，更不要把它们写入 Git、命令参数、截图或聊天记录。

## 后续开启真实能力

- AI 文案：将 `COPYWRITING_MODE` 改为 `production`，填写密钥、模型和单次预计费用。
- 云转写：将 `ASR_MODE` 改为 `cloud`，填写工作空间、DashScope、OSS 和阿里云访问凭证，再执行一次 `./authorize_asr.sh` 明确确认单条费用上限。授权操作本身不提交素材、不扣费，也不会顺带开启云剪辑。
- 云剪辑：将 `VIDEO_EDITOR_PROVIDER_MODE` 改为 `aliyun`，除上述阿里云配置外再填写 MPS 管道和 720P/1080P 模板；它不会顺带开启独立云转写入口。
- 数字人：将 `AVATAR_PROVIDER_MODE` 改为 `shuying_cloud`，填写数影网关、资产和允许域名，并设置 `SHUYING_AVATAR_ENABLED=true`。
- 每次改完 `.env`，再次执行 `./deploy.sh`。脚本只检查配置和重启服务，不会自动发起任何真实生成任务。

管理员可通过桌面端查看服务器能力状态。状态接口只返回“已配置/未配置”和缺失项名称，不返回密钥内容。

## 备份与恢复

备份使用 SQLite 在线备份 API，不直接复制正在写入的数据库；同时收纳云转写授权和数字人自定义素材等服务器数据：

```sh
./backup.sh
```

恢复前先确认备份文件，然后执行：

```sh
./restore.sh backups/对应的备份文件.zip
```

`data/`、`backups/`、`.env` 和 Caddy 证书目录均被 Git 忽略。控制层以 `umask 077` 运行，数据库和备份仅允许服务账号/部署账号读取；恢复脚本只接受 `backups/` 内的完整备份包，运行中的服务会先自动保存一份“恢复前快照”，随后检查路径、格式、大小和数据库完整性，再做精确恢复（备份中不存在的旧授权或旧素材会被移除）。恢复完成后所有旧登录会话统一失效，管理员和客户需要重新登录；校验失败时服务保持停止。ZIP 本身不带密码，复制到异地前必须交给公司已有的加密备份盘或备份系统保存，不得通过公开网盘或聊天工具发送。正式密钥不得写进 Dockerfile、Compose 文件、安装包或聊天记录。

## 桌面端构建

服务器健康检查及整套验收通过后，最终构建只需把公开域名传给统一脚本：

```powershell
.\scripts\build_final_windows_release.ps1 -ControlPlaneUrl "https://video-api.company.com" -Version "0.2.1"
```

示例中的 `0.2.1` 必须替换为本次从未生成过、且与服务器部署 ZIP 一致的新版本号。该命令只会写入公开服务器地址，不会写入任何密钥。脚本只生成一个给客户首次安装的 EXE，同时在 `deploy/control-plane/updates/` 生成同版本更新文件。把该目录同步到公司服务器后，已安装客户端会在下次启动时检查版本；只有发现更高版本并经客户确认，才会下载一次、校验大小和 SHA256 后覆盖安装。本次 Goal 会在所有验证完成后只运行一次最终构建。
