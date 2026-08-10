#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
  printf '用法：%s <服务 UID> <服务 GID>\n' "$0" >&2
  exit 2
}

[[ $# -eq 2 ]] || usage
readonly SERVICE_UID="$1"
readonly SERVICE_GID="$2"
readonly UNIT_TEMPLATE="$SCRIPT_DIR/videoinsight-control-plane.service"
readonly UNIT_TEMP="$VIDEOINSIGHT_UNIT_PATH.videoinsight.$$"
UNIT_BACKUP=""
UNIT_WAS_PRESENT=0

cleanup_temp() {
  if [[ "$UNIT_TEMP" == "/etc/systemd/system/videoinsight-control-plane.service.videoinsight."* ]]; then
    rm -f -- "$UNIT_TEMP"
  fi
}
trap cleanup_temp EXIT

restore_unit() (
  set +e
  local restore_ok=1
  if [[ "$UNIT_WAS_PRESENT" -eq 1 && -n "$UNIT_BACKUP" && -f "$UNIT_BACKUP" ]]; then
    if install -o root -g root -m 0644 -- "$UNIT_BACKUP" "$UNIT_TEMP" && \
      fsync_path "$UNIT_TEMP" && \
      durable_rename "$UNIT_TEMP" "$VIDEOINSIGHT_UNIT_PATH" "/etc/systemd/system" && \
      systemctl daemon-reload && \
      cmp -s -- "$UNIT_BACKUP" "$VIDEOINSIGHT_UNIT_PATH" && \
      ( validate_existing_unit_scope ); then
      :
    else
      restore_ok=0
    fi
  elif [[ "$UNIT_WAS_PRESENT" -eq 0 ]]; then
    if rm -f -- "$VIDEOINSIGHT_UNIT_PATH" && \
      fsync_path "/etc/systemd/system" && \
      systemctl daemon-reload && \
      [[ ! -e "$VIDEOINSIGHT_UNIT_PATH" && ! -L "$VIDEOINSIGHT_UNIT_PATH" ]]; then
      :
    else
      restore_ok=0
    fi
  else
    restore_ok=0
  fi
  [[ "$restore_ok" -eq 1 ]]
)

require_root
for command_name in chmod chown cmp find flock getent grep id install mv readlink \
  rm sed sort stat systemctl; do
  require_command "$command_name"
done
validate_service_identity "$SERVICE_UID" "$SERVICE_GID"
open_native_release_lock
real_path_under "$SCRIPT_DIR" "$VIDEOINSIGHT_ROOT" || die "发布工具目录越过控制层根目录。"
validate_root_directory "$SCRIPT_DIR"
CURRENT_RELEASE=$(current_release_root)
CURRENT_APP=$(release_app_path "$CURRENT_RELEASE")
CURRENT_VERSION=$(basename -- "$CURRENT_RELEASE")
require_stable_version "$CURRENT_VERSION"
validate_release_python "$CURRENT_RELEASE" || \
  die "当前版本尚未迁移到 releases/<版本>/{app,venv}，请先使用 upgrade.sh。"
validate_release_version_file "$CURRENT_RELEASE" "$CURRENT_VERSION"
validate_tools_match_release "$CURRENT_APP/deploy/control-plane/native-systemd"
[[ -f "$UNIT_TEMPLATE" && ! -L "$UNIT_TEMPLATE" ]] || die "unit 模板不存在。"
validate_root_file "$UNIT_TEMPLATE"

if [[ -e "$VIDEOINSIGHT_UNIT_PATH" || -L "$VIDEOINSIGHT_UNIT_PATH" ]]; then
  [[ -f "$VIDEOINSIGHT_UNIT_PATH" && ! -L "$VIDEOINSIGHT_UNIT_PATH" ]] || \
    die "现有 unit 不是普通文件，拒绝覆盖。"
  validate_existing_unit_scope
  UNIT_WAS_PRESENT=1
  prepare_secure_state_directory "unit-backups"
  UNIT_BACKUP="$VIDEOINSIGHT_ROOT/state/unit-backups/videoinsight-control-plane.service.$(date -u +%Y%m%dT%H%M%SZ).$$"
  install -o root -g root -m 0600 -- "$VIDEOINSIGHT_UNIT_PATH" "$UNIT_BACKUP"
  fsync_path "$UNIT_BACKUP"
  fsync_path "$VIDEOINSIGHT_ROOT/state/unit-backups"
fi

if cmp -s -- "$UNIT_TEMPLATE" "$VIDEOINSIGHT_UNIT_PATH"; then
  systemctl daemon-reload
  validate_unit_effective_config
  printf '固定 systemd unit 已是目标版本；未重启任何服务。\n'
  exit 0
fi

if ! install -o root -g root -m 0644 -- "$UNIT_TEMPLATE" "$UNIT_TEMP" || \
  ! fsync_path "$UNIT_TEMP" || \
  ! durable_rename "$UNIT_TEMP" "$VIDEOINSIGHT_UNIT_PATH" "/etc/systemd/system"; then
  if restore_unit; then
    die "新 unit 写入或落盘失败，已恢复并复核旧 unit。"
  fi
  die "CRITICAL: 新 unit 写入或落盘失败，且旧 unit 未确认恢复；服务未重启。"
fi
if ! systemctl daemon-reload; then
  if restore_unit; then
    die "systemd daemon-reload 失败，unit 已恢复并复核。"
  fi
  die "CRITICAL: systemd daemon-reload 失败，且旧 unit 未确认恢复；服务未重启。"
fi
if ! ( validate_unit_effective_config ); then
  if restore_unit; then
    die "新 unit 未通过有效配置检查，已恢复并复核旧 unit。"
  fi
  die "CRITICAL: 新 unit 校验失败，且旧 unit 未确认恢复；服务未重启。"
fi

printf '固定 systemd unit 已安装并加载；未重启控制层或任何其他服务。\n'
if [[ -n "$UNIT_BACKUP" ]]; then
  printf '旧 unit 备份：%s\n' "$UNIT_BACKUP"
fi
