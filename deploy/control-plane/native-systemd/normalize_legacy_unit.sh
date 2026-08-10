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
  printf '用法：%s <application版本> <interpreter版本> <原current绝对链接> <原unit SHA256> <bridge unit SHA256> <离线Python SHA256> <application文件数> <application树SHA256> <interpreter条目数> <interpreter树SHA256> <服务UID> <服务GID>\n' "$0" >&2
  exit 2
}

[[ $# -eq 12 ]] || usage
readonly APPLICATION_VERSION="$1"
readonly INTERPRETER_VERSION="$2"
readonly ORIGINAL_CURRENT_LINK="$3"
readonly ORIGINAL_UNIT_SHA256="${4,,}"
readonly BRIDGE_UNIT_SHA256="${5,,}"
readonly OFFLINE_PYTHON_SHA256="${6,,}"
readonly APPLICATION_FILE_COUNT="$7"
readonly APPLICATION_TREE_SHA256="${8,,}"
readonly INTERPRETER_TREE_ENTRY_COUNT="$9"
readonly INTERPRETER_TREE_SHA256="${10,,}"
readonly SERVICE_UID="${11}"
readonly SERVICE_GID="${12}"

# This is deliberately a single audited migration, not a version range or a generic importer.
readonly AUDITED_APPLICATION_VERSION="0.2.6"
readonly AUDITED_INTERPRETER_VERSION="0.2.4"
readonly AUDITED_ORIGINAL_CURRENT_LINK="$VIDEOINSIGHT_RELEASES_ROOT/0.2.6/app"
readonly AUDITED_ORIGINAL_UNIT_SHA256="98e7841399dcb1cb5654225bbfde65735fe0dc8cc3b56c0bd2336ad6f116a994"
readonly AUDITED_BRIDGE_UNIT_SHA256="839ad0602fd054d3d3d2eb5574c6184d4e6ceafa2d381d06f7a7a8dffe6aaf84"
readonly AUDITED_OFFLINE_PYTHON_SHA256="021044895e95be79dc2f110367607e684119afbc8ce75f6f0eec94844e0acec7"
readonly AUDITED_APPLICATION_FILE_COUNT="175"
readonly AUDITED_APPLICATION_TREE_SHA256="81c846d367b74d087fd845372f673a78011cdd0f48f2952c91a98f7e22ab2dc6"
readonly AUDITED_INTERPRETER_TREE_ENTRY_COUNT="1594"
readonly AUDITED_INTERPRETER_TREE_SHA256="bfd8ba051af78d812c9b39c1679b7843c14196cea77ee7457b8599e8797368da"

readonly APPLICATION_ROOT="$VIDEOINSIGHT_RELEASES_ROOT/$APPLICATION_VERSION/app"
readonly INTERPRETER_ROOT="$VIDEOINSIGHT_RELEASES_ROOT/$INTERPRETER_VERSION/venv"
readonly INTERPRETER_PYTHON="$INTERPRETER_ROOT/bin/python"
readonly ADOPTION_STATE_ROOT="$VIDEOINSIGHT_ROOT/state/legacy-adoption"
readonly ORIGINAL_UNIT_BACKUP_NAME="videoinsight-control-plane.service.legacy-evidence-$ORIGINAL_UNIT_SHA256"
readonly ORIGINAL_UNIT_BACKUP="$ADOPTION_STATE_ROOT/$ORIGINAL_UNIT_BACKUP_NAME"
readonly UNIT_TEMP="$VIDEOINSIGHT_UNIT_PATH.videoinsight-legacy-bridge.$$"
readonly RESTORE_TEMP="$VIDEOINSIGHT_UNIT_PATH.videoinsight-legacy-restore.$$"
readonly DESCRIPTOR_TEMP="$VIDEOINSIGHT_ROOT/state/.legacy-adoption.json.$$"
readonly ACTIVE_DESCRIPTOR_TEMP="$VIDEOINSIGHT_ROOT/state/.legacy-adoption.active.json.$$"

TRANSACTION_STARTED=0
FAIL_CLOSED_ONLY=0
SUCCESS=0

validate_exact_audited_parameters() {
  [[ "$APPLICATION_VERSION" == "$AUDITED_APPLICATION_VERSION" && \
      "$INTERPRETER_VERSION" == "$AUDITED_INTERPRETER_VERSION" ]] || \
    die "本脚本只允许一次性收养已审计的 application 0.2.6 + interpreter 0.2.4。"
  [[ "$ORIGINAL_CURRENT_LINK" == "$AUDITED_ORIGINAL_CURRENT_LINK" ]] || \
    die "原 current 参数与已审计绝对链接不一致。"
  [[ "$ORIGINAL_UNIT_SHA256" == "$AUDITED_ORIGINAL_UNIT_SHA256" ]] || \
    die "原 unit SHA256 与已审计证据不一致。"
  [[ "$BRIDGE_UNIT_SHA256" == "$AUDITED_BRIDGE_UNIT_SHA256" ]] || \
    die "bridge unit SHA256 与已审计 strict 模板不一致。"
  [[ "$OFFLINE_PYTHON_SHA256" == "$AUDITED_OFFLINE_PYTHON_SHA256" ]] || \
    die "离线 Python SHA256 与已审计证据不一致。"
  [[ "$APPLICATION_FILE_COUNT" == "$AUDITED_APPLICATION_FILE_COUNT" && \
      "$APPLICATION_TREE_SHA256" == "$AUDITED_APPLICATION_TREE_SHA256" ]] || \
    die "application 树参数与已审计证据不一致。"
  [[ "$INTERPRETER_TREE_ENTRY_COUNT" == "$AUDITED_INTERPRETER_TREE_ENTRY_COUNT" && \
      "$INTERPRETER_TREE_SHA256" == "$AUDITED_INTERPRETER_TREE_SHA256" ]] || \
    die "interpreter 树参数与已审计证据不一致。"
}

validate_original_runtime_binding() {
  [[ -L "$VIDEOINSIGHT_CURRENT" ]] || die "原 current 不是符号链接。"
  [[ $(readlink -- "$VIDEOINSIGHT_CURRENT") == "$ORIGINAL_CURRENT_LINK" ]] || \
    die "原 current 原始链接文本与显式证据不一致。"
  [[ $(readlink -f -- "$VIDEOINSIGHT_CURRENT") == "$APPLICATION_ROOT" ]] || \
    die "原 current 未解析到已审计 application。"
  validate_root_file "$VIDEOINSIGHT_UNIT_PATH"
  [[ $(sha256sum -- "$VIDEOINSIGHT_UNIT_PATH" | cut -d' ' -f1) == \
      "$ORIGINAL_UNIT_SHA256" ]] || die "原 unit 内容已改变。"
  [[ -L "$INTERPRETER_PYTHON" ]] || die "legacy interpreter Python 必须保留原始符号链接。"
  [[ $(readlink -f -- "$INTERPRETER_PYTHON") == "$VIDEOINSIGHT_OFFLINE_PYTHON" ]] || \
    die "legacy interpreter 未解析到固定离线 Python。"
  [[ $(sha256sum -- "$VIDEOINSIGHT_OFFLINE_PYTHON" | cut -d' ' -f1) == \
      "$OFFLINE_PYTHON_SHA256" ]] || die "固定离线 Python 内容已改变。"

  local fragment drop_ins working_directory service_user service_group exec_start
  local configured_exec_start expected_exec_path expected_exec_start
  local resolved_working_directory
  fragment=$(systemctl show --property=FragmentPath "$VIDEOINSIGHT_SERVICE" | sed -n 's/^FragmentPath=//p')
  drop_ins=$(systemctl show --property=DropInPaths "$VIDEOINSIGHT_SERVICE" | sed -n 's/^DropInPaths=//p')
  working_directory=$(systemctl show --property=WorkingDirectory "$VIDEOINSIGHT_SERVICE" | sed -n 's/^WorkingDirectory=//p')
  service_user=$(systemctl show --property=User "$VIDEOINSIGHT_SERVICE" | sed -n 's/^User=//p')
  service_group=$(systemctl show --property=Group "$VIDEOINSIGHT_SERVICE" | sed -n 's/^Group=//p')
  exec_start=$(systemctl show --property=ExecStart "$VIDEOINSIGHT_SERVICE" | sed -n 's/^ExecStart=//p')
  configured_exec_start=$(sed -n 's/^ExecStart=//p' "$VIDEOINSIGHT_UNIT_PATH")
  expected_exec_path="$INTERPRETER_ROOT/bin/uvicorn"
  expected_exec_start="$expected_exec_path project.backend.app.control_plane:app --host 127.0.0.1 --port 18080 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1"
  resolved_working_directory=$(readlink -f -- "$working_directory") || \
    die "原 unit WorkingDirectory 无法解析。"
  [[ "$fragment" == "$VIDEOINSIGHT_UNIT_PATH" && -z "$drop_ins" ]] || \
    die "原服务实际加载路径或 drop-in 与审计证据不一致。"
  [[ "$working_directory" == "$VIDEOINSIGHT_CURRENT" ]] || \
    die "原服务实际 WorkingDirectory 不是已审计的 current 字面路径。"
  [[ "$resolved_working_directory" == "$APPLICATION_ROOT" ]] || \
    die "原服务实际 WorkingDirectory 未绑定已审计 application。"
  [[ "$service_user" == "$VIDEOINSIGHT_SERVICE_USER" && \
      "$service_group" == "$VIDEOINSIGHT_SERVICE_GROUP" ]] || \
    die "原服务身份与固定 videoinsight 身份不一致。"
  [[ "$configured_exec_start" == "$expected_exec_start" ]] || \
    die "原服务 ExecStart 未精确绑定已审计 interpreter。"
  validate_effective_exec_start_record "$exec_start" "$expected_exec_path" \
    "$expected_exec_start"
  [[ -f "$INTERPRETER_ROOT/bin/uvicorn" && ! -L "$INTERPRETER_ROOT/bin/uvicorn" && \
      $(head -n 1 -- "$INTERPRETER_ROOT/bin/uvicorn" | tr -d '\r') == \
      "#!$INTERPRETER_PYTHON" ]] || \
    die "原 uvicorn 启动器 shebang 未精确绑定已审计 interpreter Python。"
}

validate_tree_evidence() {
  local summary=()
  mapfile -t summary < <(
    run_trusted_offline_python \
      "$SCRIPT_DIR/legacy_adoption_descriptor.py" summary "$VIDEOINSIGHT_ROOT" \
      "$APPLICATION_VERSION" "$INTERPRETER_VERSION" "$SERVICE_UID" "$SERVICE_GID"
  )
  [[ ${#summary[@]} -eq 4 ]] || die "legacy 树摘要输出无效。"
  [[ "${summary[0]}" == "$APPLICATION_FILE_COUNT" && \
      "${summary[1]}" == "$APPLICATION_TREE_SHA256" ]] || \
    die "服务器 application 树与已审计来源不一致。"
  [[ "${summary[2]}" == "$INTERPRETER_TREE_ENTRY_COUNT" && \
      "${summary[3]}" == "$INTERPRETER_TREE_SHA256" ]] || \
    die "服务器 interpreter 树与已审计来源不一致。"
}

adoption_fields_match_arguments() {
  local fields=("$@")
  [[ ${#fields[@]} -eq 11 && \
      "${fields[0]}" == "$APPLICATION_VERSION" && \
      "${fields[1]}" == "$INTERPRETER_VERSION" && \
      "${fields[2]}" == "$ORIGINAL_CURRENT_LINK" && \
      "${fields[3]}" == "$ORIGINAL_UNIT_BACKUP_NAME" && \
      "${fields[4]}" == "$ORIGINAL_UNIT_SHA256" && \
      "${fields[5]}" == "$BRIDGE_UNIT_SHA256" && \
      "${fields[7]}" == "$APPLICATION_FILE_COUNT" && \
      "${fields[8]}" == "$APPLICATION_TREE_SHA256" && \
      "${fields[9]}" == "$INTERPRETER_TREE_ENTRY_COUNT" && \
      "${fields[10]}" == "$INTERPRETER_TREE_SHA256" ]]
}

critical_fail_closed() {
  printf 'CRITICAL: %s；只停止控制层服务并拒绝猜测恢复。\n' "$*" >&2
  FAIL_CLOSED_ONLY=1
  return 1
}

ensure_service_healthy() {
  if ! systemctl is-active --quiet "$VIDEOINSIGHT_SERVICE"; then
    systemctl start "$VIDEOINSIGHT_SERVICE"
  fi
  health_check ""
}

promote_adoption_descriptor() {
  [[ ! -e "$ACTIVE_DESCRIPTOR_TEMP" && ! -L "$ACTIVE_DESCRIPTOR_TEMP" ]] || \
    die "active adoption 临时描述已存在。"
  run_trusted_offline_python \
    "$SCRIPT_DIR/legacy_adoption_descriptor.py" promote \
    "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR" "$ACTIVE_DESCRIPTOR_TEMP" \
    "$VIDEOINSIGHT_ROOT" "$VIDEOINSIGHT_UNIT_PATH" "$VIDEOINSIGHT_OFFLINE_PYTHON" \
    "$SERVICE_UID" "$SERVICE_GID"
  chown root:root -- "$ACTIVE_DESCRIPTOR_TEMP"
  chmod 0600 -- "$ACTIVE_DESCRIPTOR_TEMP"
  validate_root_file "$ACTIVE_DESCRIPTOR_TEMP"
  fsync_path "$ACTIVE_DESCRIPTOR_TEMP"
  durable_rename "$ACTIVE_DESCRIPTOR_TEMP" "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR" \
    "$VIDEOINSIGHT_ROOT/state"
  validate_active_legacy_adoption >/dev/null
}

restore_original_unit() {
  set +e
  local recovery_ok=1
  if ! stop_control_plane_fail_closed "恢复原 unit 前停止控制层"; then
    printf 'CRITICAL: 无法确认本服务已停止，未恢复原 unit。\n' >&2
    return 1
  fi
  rm -f -- "$RESTORE_TEMP"
  if install -o root -g root -m 0644 -- "$ORIGINAL_UNIT_BACKUP" "$RESTORE_TEMP" && \
    fsync_path "$RESTORE_TEMP" && \
    durable_rename "$RESTORE_TEMP" "$VIDEOINSIGHT_UNIT_PATH" "/etc/systemd/system" && \
    systemctl daemon-reload && validate_original_runtime_binding; then
    :
  else
    printf 'CRITICAL: 原 unit 恢复或取证校验失败，服务保持停止。\n' >&2
    recovery_ok=0
  fi
  if [[ "$recovery_ok" -eq 1 ]]; then
    if systemctl start "$VIDEOINSIGHT_SERVICE" && health_check ""; then
      printf 'legacy adoption 失败后已恢复原 unit，取证备份保持不变。\n' >&2
    else
      printf 'CRITICAL: 原 unit 恢复后服务未就绪，执行 fail-closed 停止。\n' >&2
      if stop_control_plane_fail_closed "原 unit 恢复健康失败后的停止"; then
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
  set +e
  if [[ "$SUCCESS" -ne 1 && "$TRANSACTION_STARTED" -eq 1 ]]; then
    local exit_status="" installed_sha=""
    if [[ -f "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR" && \
          ! -L "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR" ]]; then
      exit_status=$(run_trusted_offline_python \
        "$SCRIPT_DIR/legacy_adoption_descriptor.py" status \
        "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR" 2>/dev/null)
    fi
    if [[ -f "$VIDEOINSIGHT_UNIT_PATH" && ! -L "$VIDEOINSIGHT_UNIT_PATH" ]]; then
      installed_sha=$(sha256sum -- "$VIDEOINSIGHT_UNIT_PATH" | cut -d' ' -f1)
    fi
    if [[ "$exit_status" == "active" && "$installed_sha" == "$BRIDGE_UNIT_SHA256" ]]; then
      if validate_active_legacy_adoption >/dev/null 2>&1; then
        printf 'active adoption 已持久化，退出恢复保持 strict bridge 不变。\n' >&2
      else
        printf 'CRITICAL: active adoption 边界不完整，执行 fail-closed 停止。\n' >&2
        stop_control_plane_fail_closed "active adoption 边界失败后的停止" || true
      fi
    elif [[ "$exit_status" == "prepared" ]]; then
      restore_original_unit || true
    else
      printf 'CRITICAL: adoption 事务退出状态无法安全恢复，执行 fail-closed 停止。\n' >&2
      stop_control_plane_fail_closed "adoption 未知退出状态后的停止" || true
    fi
  elif [[ "$SUCCESS" -ne 1 && "$FAIL_CLOSED_ONLY" -eq 1 ]]; then
    stop_control_plane_fail_closed "adoption fail-closed 停止" || true
  fi
  for path in "$UNIT_TEMP" "$RESTORE_TEMP" "$DESCRIPTOR_TEMP" \
    "$ACTIVE_DESCRIPTOR_TEMP"; do
    case "$path" in
      /etc/systemd/system/videoinsight-control-plane.service.videoinsight-legacy-*|\
      "$VIDEOINSIGHT_ROOT/state/.legacy-adoption.json."*|\
      "$VIDEOINSIGHT_ROOT/state/.legacy-adoption.active.json."*) rm -f -- "$path" ;;
    esac
  done
  exit "$rc"
}
trap finish EXIT

require_root
require_stable_version "$APPLICATION_VERSION"
require_stable_version "$INTERPRETER_VERSION"
require_sha256 "$ORIGINAL_UNIT_SHA256"
require_sha256 "$BRIDGE_UNIT_SHA256"
require_sha256 "$OFFLINE_PYTHON_SHA256"
require_sha256 "$APPLICATION_TREE_SHA256"
require_sha256 "$INTERPRETER_TREE_SHA256"
require_uint "application 文件数" "$APPLICATION_FILE_COUNT"
require_uint "interpreter 条目数" "$INTERPRETER_TREE_ENTRY_COUNT"
for command_name in chmod chown curl cut find flock getent grep head id install ln mv \
  readlink rm sed sha256sum stat systemctl tr; do
  require_command "$command_name"
done
validate_exact_audited_parameters
validate_service_identity "$SERVICE_UID" "$SERVICE_GID"
validate_control_root
open_native_release_lock
if [[ -e "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR" || \
      -L "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR" ]]; then
  FAIL_CLOSED_ONLY=1
  ( validate_root_file "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR" ) || \
    critical_fail_closed "adoption 描述文件属主、权限或类型不安全"
  adoption_status=$(
    run_trusted_offline_python \
      "$SCRIPT_DIR/legacy_adoption_descriptor.py" status \
      "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR"
  ) || critical_fail_closed "adoption 描述格式或状态已被篡改"
  case "$adoption_status" in
    active)
      adoption_output=$(validate_active_legacy_adoption) || \
        critical_fail_closed "active adoption 与 current/unit/取证摘要不一致"
      adoption_fields=()
      mapfile -t adoption_fields <<< "$adoption_output"
      adoption_fields_match_arguments "${adoption_fields[@]}" || \
        critical_fail_closed "active adoption 与本次显式参数不一致"
      ensure_service_healthy || critical_fail_closed "active adoption 服务未就绪"
      /bin/bash "$SCRIPT_DIR/preflight.sh" "$SERVICE_UID" "$SERVICE_GID" || \
        critical_fail_closed "active adoption 发布前置检查失败"
      FAIL_CLOSED_ONLY=0
      SUCCESS=1
      printf '一次性 legacy adoption 已完整处于 active 状态，无需重复替换 unit。\n'
      printf '未操作反向代理、容器或其他 systemd 服务。\n'
      exit 0
      ;;
    prepared)
      adoption_output=$(validate_legacy_adoption_record prepared) || \
        critical_fail_closed "prepared adoption 与 current/interpreter/取证摘要不一致"
      adoption_fields=()
      mapfile -t adoption_fields <<< "$adoption_output"
      adoption_fields_match_arguments "${adoption_fields[@]}" || \
        critical_fail_closed "prepared adoption 与本次显式参数不一致"
      ( validate_root_file "$VIDEOINSIGHT_UNIT_PATH" ) || \
        critical_fail_closed "prepared adoption 下已安装 unit 类型或权限不安全"
      installed_unit_sha256=$(sha256sum -- "$VIDEOINSIGHT_UNIT_PATH" | cut -d' ' -f1)
      if [[ "$installed_unit_sha256" == "$ORIGINAL_UNIT_SHA256" ]]; then
        ( validate_original_runtime_binding ) || \
          critical_fail_closed "prepared adoption 的原 unit effective 配置不一致"
        ensure_service_healthy || \
          critical_fail_closed "prepared adoption 的原服务未就绪"
        /bin/bash "$SCRIPT_DIR/preflight.sh" "$SERVICE_UID" "$SERVICE_GID" \
          --legacy-prepared || critical_fail_closed "prepared adoption 前置检查失败"
        FAIL_CLOSED_ONLY=0
      elif [[ "$installed_unit_sha256" == "$BRIDGE_UNIT_SHA256" ]]; then
        if ! stop_control_plane_fail_closed "prepared bridge 恢复前停止控制层"; then
          critical_fail_closed "prepared bridge 恢复前无法确认服务已停止"
        fi
        systemctl daemon-reload
        validate_legacy_bridge_effective_config "$APPLICATION_VERSION" \
          "$INTERPRETER_VERSION" "$BRIDGE_UNIT_SHA256"
        systemctl start "$VIDEOINSIGHT_SERVICE"
        health_check "" || critical_fail_closed "prepared bridge 恢复后未就绪"
        /bin/bash "$SCRIPT_DIR/preflight.sh" "$SERVICE_UID" "$SERVICE_GID" \
          --legacy-prepared || critical_fail_closed "prepared bridge 前置检查失败"
        promote_adoption_descriptor
        FAIL_CLOSED_ONLY=0
        SUCCESS=1
        printf '检测到断电遗留的 strict bridge + prepared 描述，已安全晋升 active。\n'
        printf '未操作反向代理、容器或其他 systemd 服务。\n'
        exit 0
      else
        critical_fail_closed "prepared adoption 遇到原 unit/strict bridge 之外的混合状态"
      fi
      ;;
    *) critical_fail_closed "adoption 描述状态未被允许" ;;
  esac
else
  validate_original_runtime_binding
  validate_tree_evidence
  systemctl is-active --quiet "$VIDEOINSIGHT_SERVICE" || die "原控制层未运行。"
  health_check "" || die "原控制层未就绪。"

  prepare_secure_state_directory "legacy-adoption"
  if [[ -e "$ORIGINAL_UNIT_BACKUP" || -L "$ORIGINAL_UNIT_BACKUP" ]]; then
    validate_root_file "$ORIGINAL_UNIT_BACKUP"
    [[ $(sha256sum -- "$ORIGINAL_UNIT_BACKUP" | cut -d' ' -f1) == \
        "$ORIGINAL_UNIT_SHA256" ]] || die "既有原 unit 取证备份内容不一致。"
  else
    run_trusted_offline_python \
      "$SCRIPT_DIR/legacy_adoption_descriptor.py" copy-evidence \
      "$VIDEOINSIGHT_UNIT_PATH" "$ORIGINAL_UNIT_BACKUP" "$ORIGINAL_UNIT_SHA256"
    chown root:root -- "$ORIGINAL_UNIT_BACKUP"
    chmod 0600 -- "$ORIGINAL_UNIT_BACKUP"
    validate_root_file "$ORIGINAL_UNIT_BACKUP"
  fi
  fsync_path "$ORIGINAL_UNIT_BACKUP"
  fsync_path "$ADOPTION_STATE_ROOT"

  run_trusted_offline_python \
    "$SCRIPT_DIR/legacy_adoption_descriptor.py" create-prepared "$DESCRIPTOR_TEMP" \
    "$VIDEOINSIGHT_ROOT" "$APPLICATION_VERSION" "$INTERPRETER_VERSION" \
    "$ORIGINAL_CURRENT_LINK" "$ORIGINAL_UNIT_SHA256" "$ORIGINAL_UNIT_BACKUP_NAME" \
    "$BRIDGE_UNIT_SHA256" "$OFFLINE_PYTHON_SHA256" "$APPLICATION_FILE_COUNT" \
    "$APPLICATION_TREE_SHA256" "$INTERPRETER_TREE_ENTRY_COUNT" \
    "$INTERPRETER_TREE_SHA256"
  chown root:root -- "$DESCRIPTOR_TEMP"
  chmod 0600 -- "$DESCRIPTOR_TEMP"
  validate_root_file "$DESCRIPTOR_TEMP"
  fsync_path "$DESCRIPTOR_TEMP"
  durable_rename "$DESCRIPTOR_TEMP" "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR" \
    "$VIDEOINSIGHT_ROOT/state"
  FAIL_CLOSED_ONLY=1
  adoption_output=$(validate_legacy_adoption_record prepared) || \
    critical_fail_closed "新 prepared adoption 持久化校验失败"
  adoption_fields=()
  mapfile -t adoption_fields <<< "$adoption_output"
  adoption_fields_match_arguments "${adoption_fields[@]}" || \
    critical_fail_closed "新 prepared adoption 字段发生漂移"
  /bin/bash "$SCRIPT_DIR/preflight.sh" "$SERVICE_UID" "$SERVICE_GID" \
    --legacy-prepared || critical_fail_closed "新 prepared adoption 前置检查失败"
  FAIL_CLOSED_ONLY=0
fi

write_legacy_bridge_unit "$UNIT_TEMP" "$APPLICATION_VERSION" "$INTERPRETER_VERSION"
chown root:root -- "$UNIT_TEMP"
chmod 0644 -- "$UNIT_TEMP"
validate_legacy_bridge_unit_file "$UNIT_TEMP" "$APPLICATION_VERSION" \
  "$INTERPRETER_VERSION" "$BRIDGE_UNIT_SHA256"
fsync_path "$UNIT_TEMP"

TRANSACTION_STARTED=1
if ! stop_control_plane_fail_closed "安装 legacy bridge 前停止控制层"; then
  die "控制层未能停止，未安装 legacy bridge。"
fi
durable_rename "$UNIT_TEMP" "$VIDEOINSIGHT_UNIT_PATH" "/etc/systemd/system"
systemctl daemon-reload
validate_legacy_bridge_effective_config "$APPLICATION_VERSION" \
  "$INTERPRETER_VERSION" "$BRIDGE_UNIT_SHA256"
systemctl start "$VIDEOINSIGHT_SERVICE"
health_check "" || die "legacy bridge 启动后未就绪。"
/bin/bash "$SCRIPT_DIR/preflight.sh" "$SERVICE_UID" "$SERVICE_GID" \
  --legacy-prepared
promote_adoption_descriptor

SUCCESS=1
printf '一次性 legacy adoption 已完成；active 描述：%s\n' \
  "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR"
printf '原宽松 unit 仅保留为不可覆盖取证文件，后续升级和回滚只允许恢复 strict bridge。\n'
printf '未操作反向代理、容器或其他 systemd 服务。\n'
