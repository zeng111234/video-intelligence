#!/bin/bash
set -Eeuo pipefail
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
readonly PATH

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
  printf '用法：%s <目标旧版本> <该版本对应的停机快照文件名> <服务 UID> <服务 GID>\n' "$0" >&2
  exit 2
}

[[ $# -eq 4 ]] || usage
readonly TARGET_VERSION="$1"
readonly TARGET_SNAPSHOT_NAME="$2"
readonly SERVICE_UID="$3"
readonly SERVICE_GID="$4"
readonly TARGET_RELEASE="$VIDEOINSIGHT_RELEASES_ROOT/$TARGET_VERSION"
readonly TARGET_APP="$TARGET_RELEASE/app"
readonly TARGET_SNAPSHOT="$VIDEOINSIGHT_BACKUP_ROOT/$TARGET_SNAPSHOT_NAME"
readonly UNIT_TEMP="$VIDEOINSIGHT_UNIT_PATH.videoinsight-rollback.$$"

ORIGINAL_RELEASE=""
ORIGINAL_APP=""
ORIGINAL_CURRENT_LINK=""
ORIGINAL_VERSION=""
SAFETY_SNAPSHOT_NAME=""
LEGACY_ROLLBACK=0
LEGACY_CURRENT_LINK=""
LEGACY_UNIT_BACKUP=""
CURRENT_UNIT_SAFETY=""
TRANSACTION_STARTED=0
DATA_CHANGED=0
UNIT_CHANGED=0
SUCCESS=0

restore_with() {
  local snapshot_name="$1"
  local expected_source_version="$2"
  run_trusted_offline_python_for_service "$SERVICE_UID" "$SERVICE_GID" \
    "$ORIGINAL_APP/deploy/control-plane/restore_control_plane.py" \
    "$snapshot_name" "$expected_source_version"
}

rollback_failed_rollback() {
  set +e
  printf '回滚未通过，开始恢复回滚前状态。\n' >&2
  if ! stop_control_plane_fail_closed "回滚失败恢复前停止控制层"; then
    printf 'CRITICAL: 无法确认本服务已停止，未执行数据或链接恢复。\n' >&2
    return 1
  fi
  local recovery_ok=1
  if [[ "$DATA_CHANGED" -eq 1 ]]; then
    if ! restore_with "$SAFETY_SNAPSHOT_NAME" "$ORIGINAL_VERSION"; then
      printf 'CRITICAL: 回滚前安全快照恢复失败，服务保持停止。\n' >&2
      recovery_ok=0
    fi
  fi
  if ! ( atomic_restore_current_target "$ORIGINAL_CURRENT_LINK" ); then
    printf 'CRITICAL: 原 current 恢复失败，服务保持停止。\n' >&2
    recovery_ok=0
  fi
  if [[ "$UNIT_CHANGED" -eq 1 ]]; then
    if [[ -f "$CURRENT_UNIT_SAFETY" ]] && \
      install -o root -g root -m 0644 -- "$CURRENT_UNIT_SAFETY" "$UNIT_TEMP" && \
      fsync_path "$UNIT_TEMP" && \
      durable_rename "$UNIT_TEMP" "$VIDEOINSIGHT_UNIT_PATH" "/etc/systemd/system" && \
      systemctl daemon-reload && ( validate_unit_effective_config ); then
      :
    else
      printf 'CRITICAL: 回滚前 unit 恢复失败，服务保持停止。\n' >&2
      recovery_ok=0
    fi
  fi
  if [[ "$recovery_ok" -eq 1 ]]; then
    if systemctl start "$VIDEOINSIGHT_SERVICE" && health_check "$ORIGINAL_VERSION"; then
      printf '已恢复回滚前版本和数据。\n' >&2
    else
      printf 'CRITICAL: 回滚前版本未恢复就绪，执行 fail-closed 停止。\n' >&2
      if stop_control_plane_fail_closed "回滚恢复健康失败后的停止"; then
        printf '已复核唯一目标服务为 inactive。\n' >&2
      fi
      recovery_ok=0
    fi
  fi
  return $(( recovery_ok == 1 ? 0 : 1 ))
}

finish() {
  local rc=$?
  trap - EXIT
  if [[ "$SUCCESS" -ne 1 && "$TRANSACTION_STARTED" -eq 1 ]]; then
    rollback_failed_rollback
  fi
  case "$UNIT_TEMP" in
    /etc/systemd/system/videoinsight-control-plane.service.videoinsight-rollback.*)
      rm -f -- "$UNIT_TEMP"
      ;;
  esac
  exit "$rc"
}
trap finish EXIT

require_version "$TARGET_VERSION"
require_stable_version "$TARGET_VERSION"
[[ "$TARGET_SNAPSHOT_NAME" == "$(basename -- "$TARGET_SNAPSHOT_NAME")" ]] || \
  die "快照只允许填写文件名。"
[[ "$TARGET_SNAPSHOT_NAME" =~ ^[A-Za-z0-9._-]+\.zip$ ]] || die "快照文件名无效。"
require_root
for command_name in chmod chown cmp curl date find flock getent grep id install mv \
  readlink rm sed sort stat systemctl tr; do
  require_command "$command_name"
done
open_native_release_lock
/bin/bash "$SCRIPT_DIR/preflight.sh" "$SERVICE_UID" "$SERVICE_GID"
validate_unit_effective_config

ORIGINAL_RELEASE=$(current_release_root)
ORIGINAL_APP=$(release_app_path "$ORIGINAL_RELEASE")
ORIGINAL_CURRENT_LINK=$(readlink -- "$VIDEOINSIGHT_CURRENT")
ORIGINAL_VERSION=$(basename -- "$ORIGINAL_RELEASE")
require_stable_version "$ORIGINAL_VERSION"
validate_release_python "$ORIGINAL_RELEASE"
validate_release_version_file "$ORIGINAL_RELEASE" "$ORIGINAL_VERSION"
validate_tools_match_release "$ORIGINAL_APP/deploy/control-plane/native-systemd"
[[ "$ORIGINAL_RELEASE" != "$TARGET_RELEASE" ]] || die "目标版本已经是当前版本。"
validate_root_file "$TARGET_SNAPSHOT"
if [[ -d "$TARGET_APP" && ! -L "$TARGET_APP" && \
      -f "$TARGET_APP/release_version.txt" && \
      -d "$TARGET_RELEASE/venv" ]]; then
  release_app_path "$TARGET_RELEASE" >/dev/null
  validate_release_python "$TARGET_RELEASE"
  validate_release_version_file "$TARGET_RELEASE" "$TARGET_VERSION"
  validate_tools_match_release "$TARGET_APP/deploy/control-plane/native-systemd"
else
  LEGACY_ROLLBACK=1
  prepare_secure_state_directory "legacy-rollbacks"
  legacy_descriptor="$VIDEOINSIGHT_ROOT/state/legacy-rollbacks/$TARGET_SNAPSHOT_NAME.json"
  validate_root_file "$legacy_descriptor"
  legacy_output=$(
    run_trusted_offline_python \
      "$ORIGINAL_APP/deploy/control-plane/native-systemd/legacy_rollback_descriptor.py" \
      validate "$legacy_descriptor" "$VIDEOINSIGHT_ROOT" "$VIDEOINSIGHT_BACKUP_ROOT" \
      "$TARGET_VERSION" "$ORIGINAL_VERSION" "$TARGET_SNAPSHOT_NAME"
  ) || die "legacy 回滚描述校验失败。"
  mapfile -t legacy_fields <<< "$legacy_output"
  [[ ${#legacy_fields[@]} -eq 6 ]] || die "legacy 回滚描述输出无效。"
  LEGACY_CURRENT_LINK="${legacy_fields[0]}"
  prepare_secure_state_directory "unit-backups"
  LEGACY_UNIT_BACKUP="$VIDEOINSIGHT_ROOT/state/unit-backups/${legacy_fields[1]}"
  validate_root_file "$LEGACY_UNIT_BACKUP"
  adoption_fields=()
  mapfile -t adoption_fields < <(validate_legacy_adoption_record record)
  [[ ${#adoption_fields[@]} -eq 11 && \
      "${adoption_fields[0]}" == "${legacy_fields[3]}" && \
      "${adoption_fields[1]}" == "${legacy_fields[4]}" && \
      "${adoption_fields[2]}" == "$LEGACY_CURRENT_LINK" && \
      "${adoption_fields[5]}" == "${legacy_fields[5]}" ]] || \
    die "legacy 回滚 v2 描述与 active adoption 记录不一致。"
  validate_legacy_bridge_unit_file "$LEGACY_UNIT_BACKUP" \
    "${legacy_fields[3]}" "${legacy_fields[4]}" "${legacy_fields[5]}" 0
  CURRENT_UNIT_SAFETY="$VIDEOINSIGHT_ROOT/state/unit-backups/videoinsight-control-plane.service.pre-legacy-rollback-$TARGET_VERSION-$(date -u +%Y%m%dT%H%M%SZ).$$"
  install -o root -g root -m 0600 -- "$VIDEOINSIGHT_UNIT_PATH" "$CURRENT_UNIT_SAFETY"
  validate_root_file "$CURRENT_UNIT_SAFETY"
  fsync_path "$CURRENT_UNIT_SAFETY"
  fsync_path "$VIDEOINSIGHT_ROOT/state/unit-backups"
fi
health_check "$ORIGINAL_VERSION" || die "当前控制层未就绪。"

TRANSACTION_STARTED=1
if ! stop_control_plane_fail_closed "人工回滚前停止控制层"; then
  die "控制层未能停止，未开始回滚。"
fi

SAFETY_SNAPSHOT_NAME="videoinsight-control-plane-pre-rollback-$TARGET_VERSION-$(date -u +%Y%m%dT%H%M%SZ)-$$.zip"
run_trusted_offline_python \
  "$ORIGINAL_APP/deploy/control-plane/backup_control_plane.py" \
  "$SAFETY_SNAPSHOT_NAME" "$ORIGINAL_VERSION"
[[ -f "$VIDEOINSIGHT_BACKUP_ROOT/$SAFETY_SNAPSHOT_NAME" ]] || \
  die "回滚前安全快照未生成。"
validate_root_file "$VIDEOINSIGHT_BACKUP_ROOT/$SAFETY_SNAPSHOT_NAME"

DATA_CHANGED=1
restore_with "$TARGET_SNAPSHOT_NAME" "$TARGET_VERSION"
if [[ "$LEGACY_ROLLBACK" -eq 1 ]]; then
  atomic_restore_current_target "$LEGACY_CURRENT_LINK"
  UNIT_CHANGED=1
  install -o root -g root -m 0644 -- "$LEGACY_UNIT_BACKUP" "$UNIT_TEMP"
  fsync_path "$UNIT_TEMP"
  durable_rename "$UNIT_TEMP" "$VIDEOINSIGHT_UNIT_PATH" "/etc/systemd/system"
  systemctl daemon-reload
  validate_active_legacy_adoption >/dev/null
else
  atomic_switch_current "$TARGET_RELEASE"
fi
systemctl start "$VIDEOINSIGHT_SERVICE"
if [[ "$LEGACY_ROLLBACK" -eq 1 ]]; then
  health_check "" || die "legacy 旧版本恢复后未就绪。"
else
  health_check "$TARGET_VERSION" || die "旧版本恢复后未就绪。"
fi

SUCCESS=1
printf '已回滚到 %s；所有旧登录会话按恢复策略失效。\n' "$TARGET_VERSION"
printf '回滚前安全快照：%s\n' "$VIDEOINSIGHT_BACKUP_ROOT/$SAFETY_SNAPSHOT_NAME"
if [[ "$LEGACY_ROLLBACK" -eq 1 ]]; then
  printf '已按 adoption v2 描述恢复 legacy current 与 strict bridge；原宽松 unit 仅作取证、不会恢复。\n'
fi
printf '未操作反向代理、容器或其他 systemd 服务。\n'
