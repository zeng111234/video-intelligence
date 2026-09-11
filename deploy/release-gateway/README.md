# VideoInsight 受限 SSH 发布入口

该入口解决“开发电脑可以免密登录，但不能安全部署”的问题。它只接受固定暂存目录中的、经过 SHA256 核对的正式制品，只操作 VideoInsight 控制层、桌面更新目录和固定 systemd 服务。

## 一次性服务器配置

必须由服务器管理员审查后以 root 执行；不得利用任意 `cp`、`mv` 或 `systemctl` sudo 权限绕过管理员确认：

```bash
cd /path/to/reviewed/release-gateway
/bin/bash -n install.sh
/bin/bash -n videoinsight-release-gateway.sh
/bin/bash install.sh
sudo -l -U devuser
sudo -n -u root /usr/local/sbin/videoinsight-release-gateway probe
```

安装完成后，管理员必须从原 sudoers 文件中删除 `devuser` 的任意 `systemctl`、任意 `cp`、任意 `mv` 和旧 `/opt/app` 删除权限，只保留：

```text
devuser ALL=(root) NOPASSWD: /usr/local/sbin/videoinsight-release-gateway
```

在旧规则没有删除前，不得把服务器标记为“发布权限已修复”。

## 日常使用

管理员完成上述一次性安装后，开发电脑只需运行：

```powershell
.\scripts\deploy_videoinsight_release.ps1 -Version "x.y.z"
```

默认 `Auto` 模式会根据公网版本和本机已有的正式制品判断当前阶段：先部署控制层；Windows 正式构建完成后再次运行同一条命令，再发布安装包与 `latest.json`。也可以明确指定 `-Component ControlPlane` 或 `-Component Desktop`。脚本不会构建、猜测或修补制品，只上传已经通过正式发布登记和 SHA256 校验的文件。

## 安全边界

- SSH 只能使用本机既有密钥，禁止密码回退。
- 上传位置固定为 `/home/devuser/.videoinsight-release-staging/`。
- 控制层只接受 `VideoInsight-control-plane-x.y.z.zip`。
- 桌面端只接受 `VideoInsight-x.y.z-Setup.exe` 与严格匹配的 `latest.json`。
- 制品名称、版本、大小和 SHA256 任一不一致立即停止。
- 控制层沿用现有 `preflight.sh → upgrade.sh → verify.sh`，不操作 Docker、Caddy、Nginx 或其他服务。
- 桌面安装包先原子落盘，最后才替换 `latest.json`。
