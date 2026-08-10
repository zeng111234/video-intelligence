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
  printf '用法：%s <新版本> <部署 ZIP 的 SHA256> <服务 UID> <服务 GID>\n' "$0" >&2
  exit 2
}

[[ $# -eq 4 ]] || usage
readonly VERSION="$1"
readonly EXPECTED_SHA256="$2"
readonly SERVICE_UID="$3"
readonly SERVICE_GID="$4"
readonly ARCHIVE="$VIDEOINSIGHT_ROOT/incoming/VideoInsight-control-plane-$VERSION.zip"
readonly RELEASE_DIR="$VIDEOINSIGHT_RELEASES_ROOT/$VERSION"
readonly STAGING_DIR="$VIDEOINSIGHT_RELEASES_ROOT/.staging-$VERSION-$$"
readonly STAGING_APP="$STAGING_DIR/app"
readonly STAGING_TMP="$STAGING_DIR/tmp"
readonly RELEASE_APP="$RELEASE_DIR/app"
readonly UNIT_TEMPLATE="$SCRIPT_DIR/videoinsight-control-plane.service"
readonly UNIT_TEMP="$VIDEOINSIGHT_UNIT_PATH.videoinsight-upgrade.$$"

PREVIOUS_RELEASE_ROOT=""
PREVIOUS_CURRENT_TARGET=""
PREVIOUS_CURRENT_LINK=""
PREVIOUS_VERSION=""
PREVIOUS_UNIT_BACKUP=""
SNAPSHOT_NAME=""
LEGACY_LAYOUT=0
LEGACY_INTERPRETER_VERSION=""
LEGACY_BRIDGE_UNIT_SHA256=""
LEGACY_ADOPTION_DESCRIPTOR_SHA256=""
LEGACY_DESCRIPTOR_TEMP=""
LEGACY_DESCRIPTOR_FINAL=""
TRANSACTION_STARTED=0
DATA_MAY_HAVE_CHANGED=0
SUCCESS=0

safe_remove_staging() {
  if [[ -e "$STAGING_DIR" || -L "$STAGING_DIR" ]]; then
    case "$STAGING_DIR" in
      "$VIDEOINSIGHT_RELEASES_ROOT"/.staging-"$VERSION"-*) rm -rf -- "$STAGING_DIR" ;;
      *) printf '拒绝清理未验证的暂存路径：%s\n' "$STAGING_DIR" >&2 ;;
    esac
  fi
  if [[ -e "$UNIT_TEMP" || -L "$UNIT_TEMP" ]]; then
    case "$UNIT_TEMP" in
      /etc/systemd/system/videoinsight-control-plane.service.videoinsight-upgrade.*)
        rm -f -- "$UNIT_TEMP"
        ;;
    esac
  fi
  if [[ "$SUCCESS" -ne 1 ]]; then
    local descriptor
    for descriptor in "$LEGACY_DESCRIPTOR_TEMP" "$LEGACY_DESCRIPTOR_FINAL"; do
      [[ -n "$descriptor" ]] || continue
      case "$descriptor" in
        "$VIDEOINSIGHT_ROOT/state/legacy-rollbacks/"*) rm -f -- "$descriptor" ;;
        *) printf '拒绝清理未验证的 legacy 描述路径：%s\n' "$descriptor" >&2 ;;
      esac
    done
  fi
}

restore_snapshot() {
  [[ -n "$SNAPSHOT_NAME" && -f "$VIDEOINSIGHT_BACKUP_ROOT/$SNAPSHOT_NAME" ]] || return 1
  run_trusted_offline_python_for_service "$SERVICE_UID" "$SERVICE_GID" \
    "$RELEASE_APP/deploy/control-plane/restore_control_plane.py" \
    "$SNAPSHOT_NAME" "$PREVIOUS_VERSION"
}

rollback_transaction() {
  set +e
  printf '升级未通过，开始恢复升级前状态。\n' >&2
  if ! stop_control_plane_fail_closed "升级失败恢复前停止控制层"; then
    printf 'CRITICAL: 无法确认本服务已停止，未执行数据、链接或 unit 恢复。\n' >&2
    return 1
  fi

  local rollback_ok=1
  if [[ "$DATA_MAY_HAVE_CHANGED" -eq 1 ]]; then
    if ! restore_snapshot; then
      printf 'CRITICAL: 停机快照恢复失败，服务保持停止。\n' >&2
      rollback_ok=0
    fi
  fi
  if ! ( atomic_restore_current_target "$PREVIOUS_CURRENT_LINK" ); then
    printf 'CRITICAL: current 恢复失败，服务保持停止。\n' >&2
    rollback_ok=0
  fi
  if [[ -f "$PREVIOUS_UNIT_BACKUP" ]]; then
    if install -o root -g root -m 0644 -- "$PREVIOUS_UNIT_BACKUP" "$UNIT_TEMP" && \
      fsync_path "$UNIT_TEMP" && \
      durable_rename "$UNIT_TEMP" "$VIDEOINSIGHT_UNIT_PATH" "/etc/systemd/system" && \
      systemctl daemon-reload; then
      :
    else
      printf 'CRITICAL: 原 systemd unit 恢复失败，服务保持停止。\n' >&2
      rollback_ok=0
    fi
  else
    printf 'CRITICAL: 缺少原 systemd unit 备份，服务保持停止。\n' >&2
    rollback_ok=0
  fi

  if [[ "$rollback_ok" -eq 1 && "$LEGACY_LAYOUT" -eq 1 ]] && \
    ! validate_active_legacy_adoption >/dev/null; then
    printf 'CRITICAL: strict bridge 与 active adoption 恢复校验失败，服务保持停止。\n' >&2
    rollback_ok=0
  fi

  if [[ "$rollback_ok" -eq 1 ]]; then
    if systemctl start "$VIDEOINSIGHT_SERVICE" && health_check ""; then
      printf '升级前代码、unit 和停机数据快照已恢复。\n' >&2
    else
      printf 'CRITICAL: 旧控制层恢复后仍未就绪，执行 fail-closed 停止。\n' >&2
      if stop_control_plane_fail_closed "升级恢复健康失败后的停止"; then
        printf '已复核唯一目标服务为 inactive，请人工检查。\n' >&2
      fi
      rollback_ok=0
    fi
  fi
  return $(( rollback_ok == 1 ? 0 : 1 ))
}

finish() {
  local rc=$?
  trap - EXIT
  set +e
  if [[ "$SUCCESS" -ne 1 && "$TRANSACTION_STARTED" -eq 1 ]]; then
    rollback_transaction
  fi
  safe_remove_staging
  exit "$rc"
}
trap finish EXIT

require_version "$VERSION"
require_stable_version "$VERSION"
require_sha256 "$EXPECTED_SHA256"
require_root
for command_name in chown chmod cmp curl cut date find flock getent grep id install ln mv \
  readlink rm sed sha256sum sort stat systemctl tr; do
  require_command "$command_name"
done
open_native_release_lock
/bin/bash "$SCRIPT_DIR/preflight.sh" "$SERVICE_UID" "$SERVICE_GID"

PREVIOUS_CURRENT_TARGET=$(current_target_path)
PREVIOUS_RELEASE_ROOT=$(current_release_root)
release_app_path "$PREVIOUS_RELEASE_ROOT" >/dev/null
PREVIOUS_CURRENT_LINK=$(readlink -- "$VIDEOINSIGHT_CURRENT")
PREVIOUS_VERSION=$(basename -- "$PREVIOUS_RELEASE_ROOT")
require_stable_version "$PREVIOUS_VERSION"
if [[ "$PREVIOUS_CURRENT_TARGET" == "$PREVIOUS_RELEASE_ROOT" ]]; then
  validate_release_python "$PREVIOUS_RELEASE_ROOT"
  validate_release_version_file "$PREVIOUS_RELEASE_ROOT" "$PREVIOUS_VERSION"
else
  [[ "$PREVIOUS_CURRENT_TARGET" == "$PREVIOUS_RELEASE_ROOT/app" ]] || \
    die "旧 current 既不是版本根目录，也不是 active adoption legacy app。"
  LEGACY_LAYOUT=1
  legacy_adoption_fields=()
  mapfile -t legacy_adoption_fields < <(validate_active_legacy_adoption)
  [[ ${#legacy_adoption_fields[@]} -eq 11 && \
      "${legacy_adoption_fields[0]}" == "$PREVIOUS_VERSION" && \
      "${legacy_adoption_fields[2]}" == "$PREVIOUS_CURRENT_LINK" ]] || \
    die "legacy active adoption 与当前 application/current 不一致。"
  LEGACY_INTERPRETER_VERSION="${legacy_adoption_fields[1]}"
  LEGACY_BRIDGE_UNIT_SHA256="${legacy_adoption_fields[5]}"
  LEGACY_ADOPTION_DESCRIPTOR_SHA256="${legacy_adoption_fields[6]}"
fi
version_is_strictly_greater "$VERSION" "$PREVIOUS_VERSION" || \
  die "新版本必须严格高于当前版本 $PREVIOUS_VERSION。"
health_check "" || die "当前控制层未就绪，拒绝在未知基线上升级。"
if [[ "$LEGACY_LAYOUT" -eq 1 ]]; then
  printf 'legacy 健康接口无版本字段；active adoption 已精确绑定 application=%s interpreter=%s 和 strict bridge。\n' \
    "$PREVIOUS_VERSION" "$LEGACY_INTERPRETER_VERSION"
fi

[[ ! -e "$RELEASE_DIR" && ! -L "$RELEASE_DIR" ]] || \
  die "版本 $VERSION 已存在，禁止覆盖或复用版本号。"
[[ ! -e "$STAGING_DIR" && ! -L "$STAGING_DIR" ]] || die "暂存目录已存在。"
validate_secure_directory "$VIDEOINSIGHT_ROOT/incoming" 0
real_path_under "$VIDEOINSIGHT_ROOT/incoming" "$VIDEOINSIGHT_ROOT" || \
  die "incoming 目录越过控制层根目录。"
validate_secure_file "$ARCHIVE" 0

actual_sha256=$(sha256sum -- "$ARCHIVE" | cut -d' ' -f1)
[[ "${actual_sha256,,}" == "${EXPECTED_SHA256,,}" ]] || die "部署 ZIP 的 SHA256 不一致。"

install -d -o root -g root -m 0700 -- "$STAGING_APP"
run_trusted_offline_python \
  "$SCRIPT_DIR/validate_release_archive.py" \
  "$ARCHIVE" "$STAGING_APP" "$VERSION"
validate_tools_match_release "$STAGING_APP/deploy/control-plane/native-systemd"

CONTROL_PLANE_ENV_FILE="$VIDEOINSIGHT_ENV_FILE" \
  /bin/bash "$STAGING_APP/deploy/control-plane/validate_env.sh"

install -d -o root -g root -m 0700 -- "$STAGING_TMP"
run_trusted_offline_python_in_tmp "$STAGING_TMP" -m venv --copies \
  "$STAGING_DIR/venv"
run_trusted_staging_python "$STAGING_DIR/venv/bin/python" "$STAGING_DIR" \
  "$STAGING_TMP" -m pip install \
  --disable-pip-version-check \
  --no-index \
  --only-binary=:all: \
  --find-links "$VIDEOINSIGHT_WHEELHOUSE" \
  --require-hashes \
  --requirement "$STAGING_APP/deploy/control-plane/requirements.lock"
run_trusted_staging_python "$STAGING_DIR/venv/bin/python" "$STAGING_DIR" \
  "$STAGING_TMP" -m pip check
run_trusted_staging_python "$STAGING_DIR/venv/bin/python" "$STAGING_DIR" \
  "$STAGING_TMP" -c 'import fastapi, httpx, pydantic, uvicorn'
rm -rf -- "$STAGING_TMP"

chown -R root:root -- "$STAGING_DIR"
find "$STAGING_DIR" -type d -exec chmod 0755 {} +
find "$STAGING_DIR" -type f -exec chmod 0644 {} +
find "$STAGING_DIR/venv/bin" -type f -exec chmod 0755 {} +
fsync_tree "$STAGING_DIR"
durable_rename "$STAGING_DIR" "$RELEASE_DIR" "$VIDEOINSIGHT_RELEASES_ROOT"
release_app_path "$RELEASE_DIR" >/dev/null
validate_release_python "$RELEASE_DIR"
validate_release_version_file "$RELEASE_DIR" "$VERSION"

prepare_secure_state_directory "unit-backups"
PREVIOUS_UNIT_BACKUP="$VIDEOINSIGHT_ROOT/state/unit-backups/videoinsight-control-plane.service.pre-$VERSION.$(date -u +%Y%m%dT%H%M%SZ).$$"
install -o root -g root -m 0600 -- "$VIDEOINSIGHT_UNIT_PATH" "$PREVIOUS_UNIT_BACKUP"
validate_root_file "$PREVIOUS_UNIT_BACKUP"
fsync_path "$PREVIOUS_UNIT_BACKUP"
fsync_path "$VIDEOINSIGHT_ROOT/state/unit-backups"
if [[ "$LEGACY_LAYOUT" -eq 1 ]]; then
  prepare_secure_state_directory "legacy-rollbacks"
fi

TRANSACTION_STARTED=1
if ! stop_control_plane_fail_closed "升级切换前停止控制层"; then
  die "控制层未能停止，未切换版本。"
fi

SNAPSHOT_NAME="videoinsight-control-plane-pre-upgrade-$VERSION-$(date -u +%Y%m%dT%H%M%SZ)-$$.zip"
run_trusted_offline_python \
  "$RELEASE_APP/deploy/control-plane/backup_control_plane.py" \
  "$SNAPSHOT_NAME" "$PREVIOUS_VERSION"
[[ -f "$VIDEOINSIGHT_BACKUP_ROOT/$SNAPSHOT_NAME" ]] || die "停机快照未生成。"
validate_root_file "$VIDEOINSIGHT_BACKUP_ROOT/$SNAPSHOT_NAME"
if [[ "$LEGACY_LAYOUT" -eq 1 ]]; then
  snapshot_sha256=$(sha256sum -- "$VIDEOINSIGHT_BACKUP_ROOT/$SNAPSHOT_NAME" | cut -d' ' -f1)
  unit_backup_sha256=$(sha256sum -- "$PREVIOUS_UNIT_BACKUP" | cut -d' ' -f1)
  LEGACY_DESCRIPTOR_TEMP="$VIDEOINSIGHT_ROOT/state/legacy-rollbacks/.$SNAPSHOT_NAME.json.$$"
  LEGACY_DESCRIPTOR_FINAL="$VIDEOINSIGHT_ROOT/state/legacy-rollbacks/$SNAPSHOT_NAME.json"
  [[ ! -e "$LEGACY_DESCRIPTOR_TEMP" && ! -L "$LEGACY_DESCRIPTOR_TEMP" && \
      ! -e "$LEGACY_DESCRIPTOR_FINAL" && ! -L "$LEGACY_DESCRIPTOR_FINAL" ]] || \
    die "legacy 回滚描述路径已存在。"
  run_trusted_offline_python \
    "$RELEASE_APP/deploy/control-plane/native-systemd/legacy_rollback_descriptor.py" \
    create "$LEGACY_DESCRIPTOR_TEMP" "$VIDEOINSIGHT_ROOT" \
    "$PREVIOUS_VERSION" "$VERSION" "$PREVIOUS_CURRENT_LINK" \
    "$(basename -- "$PREVIOUS_UNIT_BACKUP")" "$SNAPSHOT_NAME" \
    "$snapshot_sha256" "$unit_backup_sha256" \
    "$(basename -- "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR")" \
    "$LEGACY_ADOPTION_DESCRIPTOR_SHA256" "$LEGACY_INTERPRETER_VERSION" \
    "$LEGACY_BRIDGE_UNIT_SHA256"
  chown root:root -- "$LEGACY_DESCRIPTOR_TEMP"
  chmod 0600 -- "$LEGACY_DESCRIPTOR_TEMP"
  validate_root_file "$LEGACY_DESCRIPTOR_TEMP"
  fsync_path "$LEGACY_DESCRIPTOR_TEMP"
  durable_rename "$LEGACY_DESCRIPTOR_TEMP" "$LEGACY_DESCRIPTOR_FINAL" \
    "$VIDEOINSIGHT_ROOT/state/legacy-rollbacks"
  LEGACY_DESCRIPTOR_TEMP=""
  validate_root_file "$LEGACY_DESCRIPTOR_FINAL"
fi

install -o root -g root -m 0644 -- "$UNIT_TEMPLATE" "$UNIT_TEMP"
fsync_path "$UNIT_TEMP"
durable_rename "$UNIT_TEMP" "$VIDEOINSIGHT_UNIT_PATH" "/etc/systemd/system"
systemctl daemon-reload
( validate_unit_effective_config )

atomic_switch_current "$RELEASE_DIR"

DATA_MAY_HAVE_CHANGED=1
install -d -o "$SERVICE_UID" -g "$SERVICE_GID" -m 0700 -- \
  "$VIDEOINSIGHT_RUNTIME_ROOT/data"
install -d -o root -g root -m 0700 -- "$VIDEOINSIGHT_BACKUP_ROOT"
bootstrap_manifest="$RELEASE_DIR/app/deploy/control-plane/bootstrap/avatar_assets/shuying_cloud.json"
runtime_manifest="$VIDEOINSIGHT_RUNTIME_ROOT/data/avatar_assets/shuying_cloud.json"
if [[ -f "$bootstrap_manifest" && ! -e "$runtime_manifest" && ! -L "$runtime_manifest" ]]; then
  install -d -o "$SERVICE_UID" -g "$SERVICE_GID" -m 0700 -- "$(dirname -- "$runtime_manifest")"
  install -o "$SERVICE_UID" -g "$SERVICE_GID" -m 0600 -- "$bootstrap_manifest" "$runtime_manifest"
fi

systemctl start "$VIDEOINSIGHT_SERVICE"
health_check "$VERSION" || die "新版本健康检查失败（初次检查加一次重试）。"

SUCCESS=1
printf '原生控制层已升级到 %s。\n' "$VERSION"
printf '停机快照：%s\n' "$VIDEOINSIGHT_BACKUP_ROOT/$SNAPSHOT_NAME"
printf '旧 unit 备份：%s\n' "$PREVIOUS_UNIT_BACKUP"
if [[ -n "$LEGACY_DESCRIPTOR_FINAL" ]]; then
  printf 'legacy 人工回滚描述：%s\n' "$LEGACY_DESCRIPTOR_FINAL"
fi
printf '未操作反向代理、容器或其他 systemd 服务。\n'
