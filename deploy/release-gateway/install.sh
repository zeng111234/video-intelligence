#!/bin/bash
set -euo pipefail

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  printf 'ERROR: 该安装脚本必须由服务器管理员以 root 执行。\n' >&2
  exit 1
}

readonly SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
readonly GATEWAY_SOURCE="$SOURCE_DIR/videoinsight-release-gateway.sh"
readonly GATEWAY_TARGET=/usr/local/sbin/videoinsight-release-gateway
readonly SUDOERS_TARGET=/etc/sudoers.d/videoinsight-release-gateway
readonly STAGING_ROOT=/home/devuser/.videoinsight-release-staging
readonly CONTROL_INCOMING=/opt/videoinsight-control-plane/incoming

[[ -f $GATEWAY_SOURCE && ! -L $GATEWAY_SOURCE ]] || {
  printf 'ERROR: 缺少发布入口脚本。\n' >&2
  exit 1
}
id devuser >/dev/null 2>&1 || {
  printf 'ERROR: 服务器不存在 devuser。\n' >&2
  exit 1
}
[[ -d /opt/videoinsight-control-plane && ! -L /opt/videoinsight-control-plane ]] || {
  printf 'ERROR: 控制层根目录不存在或不是普通目录。\n' >&2
  exit 1
}
[[ -d /www/wwwroot/xmt.syszr.cn/desktop-updates && ! -L /www/wwwroot/xmt.syszr.cn/desktop-updates ]] || {
  printf 'ERROR: 桌面更新目录不存在或不是普通目录。\n' >&2
  exit 1
}

/bin/bash -n "$GATEWAY_SOURCE"
install -d -o devuser -g devuser -m 0700 -- "$STAGING_ROOT"
install -d -o root -g root -m 0700 -- "$CONTROL_INCOMING"
install -o root -g root -m 0755 -- "$GATEWAY_SOURCE" "$GATEWAY_TARGET"

temporary_sudoers=$(mktemp /etc/sudoers.d/.videoinsight-release-gateway.XXXXXX)
trap 'rm -f -- "$temporary_sudoers"' EXIT
printf '%s\n' \
  'Defaults!/usr/local/sbin/videoinsight-release-gateway !setenv' \
  'devuser ALL=(root) NOPASSWD: /usr/local/sbin/videoinsight-release-gateway' \
  >"$temporary_sudoers"
chmod 0440 "$temporary_sudoers"
visudo -cf "$temporary_sudoers"
mv -f -- "$temporary_sudoers" "$SUDOERS_TARGET"
trap - EXIT

printf '发布入口已安装。请立即审查并删除 devuser 旧的任意 systemctl/cp/mv sudo 规则。\n'
printf '审查命令：sudo -l -U devuser\n'
