#!/bin/bash
set -Eeuo pipefail
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
readonly PATH

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

readonly PYTHON_PARENT="$VIDEOINSIGHT_ROOT/python"
readonly LEGACY_BACKUP="$PYTHON_PARENT/3.12.13.legacy-pyc-drift-3f3407b97c487aaf0dedd632590fd7731486675cf77000827918274bab07b8b0"
readonly ACTIVE_CONTAMINATED_BACKUP="$PYTHON_PARENT/3.12.13.active-contaminated-runtime"
readonly CLEAN_STAGE="$PYTHON_PARENT/.3.12.13.clean-runtime-stage"
readonly EXTRACT_ROOT="$PYTHON_PARENT/.3.12.13.clean-runtime-extract"
readonly REPAIR_DESCRIPTOR="$VIDEOINSIGHT_ROOT/state/offline-python-runtime-repair"
readonly DESCRIPTOR_TEMP="$VIDEOINSIGHT_ROOT/state/.offline-python-runtime-repair.new"
readonly SYSTEM_TAR="/usr/bin/tar"
readonly ARCHIVE_SHA256="506191be3ee7bd190a8834dcdc1b3bc70aab50608deccc711935aa007239cabd"
readonly ARCHIVE_BYTES="34163738"
readonly ARCHIVE_SOURCE="https://releases.astral.sh/github/python-build-standalone/releases/download/20260807/cpython-3.12.13%2B20260807-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
readonly LEGACY_TREE_SHA256="3f3407b97c487aaf0dedd632590fd7731486675cf77000827918274bab07b8b0"
readonly CLEAN_TREE_SHA256="f4446ac8e57f0a85d2bd0851fc05acb0cd5e8137f6752df428116ddc8e4fab01"
readonly AUDITED_APPLICATION_VERSION="0.2.6"
readonly AUDITED_INTERPRETER_VERSION="0.2.4"
readonly AUDITED_APPLICATION_ROOT="$VIDEOINSIGHT_RELEASES_ROOT/$AUDITED_APPLICATION_VERSION/app"
readonly AUDITED_INTERPRETER_ROOT="$VIDEOINSIGHT_RELEASES_ROOT/$AUDITED_INTERPRETER_VERSION/venv"
readonly AUDITED_ORIGINAL_CURRENT_LINK="$AUDITED_APPLICATION_ROOT"
readonly AUDITED_ORIGINAL_UNIT_SHA256="98e7841399dcb1cb5654225bbfde65735fe0dc8cc3b56c0bd2336ad6f116a994"
readonly AUDITED_BRIDGE_UNIT_SHA256="839ad0602fd054d3d3d2eb5574c6184d4e6ceafa2d381d06f7a7a8dffe6aaf84"
readonly AUDITED_APPLICATION_FILE_COUNT="175"
readonly AUDITED_APPLICATION_TREE_SHA256="81c846d367b74d087fd845372f673a78011cdd0f48f2952c91a98f7e22ab2dc6"
readonly AUDITED_INTERPRETER_TREE_ENTRY_COUNT="1594"
readonly AUDITED_INTERPRETER_TREE_SHA256="bfd8ba051af78d812c9b39c1679b7843c14196cea77ee7457b8599e8797368da"

TRANSACTION_STARTED=0
PREPARED_CREATED=0
DESCRIPTOR_TEMP_CREATED=0
SUCCESS=0
ACTIVE_REFRESH=0
REPAIR_SERVICE_BINDING_KIND=""

usage() {
  printf '用法：%s <服务 UID> <服务 GID>\n' "$0" >&2
  exit 2
}

validate_audited_original_current_binding() {
  [[ -L "$VIDEOINSIGHT_CURRENT" ]] || die "原 current 不是符号链接。"
  [[ $(readlink -- "$VIDEOINSIGHT_CURRENT") == "$AUDITED_ORIGINAL_CURRENT_LINK" ]] || \
    die "原 current 原始链接文本与一次性迁移证据不一致。"
  [[ $(readlink -f -- "$VIDEOINSIGHT_CURRENT") == "$AUDITED_APPLICATION_ROOT" ]] || \
    die "原 current 未解析到已审计 application。"
}

validate_audited_original_service_unit_binding() {
  validate_root_file "$VIDEOINSIGHT_UNIT_PATH"
  [[ $(sha256sum -- "$VIDEOINSIGHT_UNIT_PATH" | cut -d' ' -f1) == \
      "$AUDITED_ORIGINAL_UNIT_SHA256" ]] || \
    die "原 unit SHA256 与一次性迁移证据不一致。"
  validate_audited_original_current_binding
  [[ $(grep -Fxc -- 'Environment=PYTHONDONTWRITEBYTECODE=1' \
      "$VIDEOINSIGHT_UNIT_PATH") -eq 1 ]] || \
    die "原 unit 未精确启用 PYTHONDONTWRITEBYTECODE。"

  local fragment drop_ins need_daemon_reload working_directory
  local service_user service_group exec_start effective_environment
  local configured_exec_start expected_exec_path expected_exec_start
  local expected_environment resolved_working_directory
  fragment=$(systemctl show --property=FragmentPath "$VIDEOINSIGHT_SERVICE" | sed -n 's/^FragmentPath=//p')
  drop_ins=$(systemctl show --property=DropInPaths "$VIDEOINSIGHT_SERVICE" | sed -n 's/^DropInPaths=//p')
  need_daemon_reload=$(systemctl show --property=NeedDaemonReload "$VIDEOINSIGHT_SERVICE" | sed -n 's/^NeedDaemonReload=//p')
  working_directory=$(systemctl show --property=WorkingDirectory "$VIDEOINSIGHT_SERVICE" | sed -n 's/^WorkingDirectory=//p')
  service_user=$(systemctl show --property=User "$VIDEOINSIGHT_SERVICE" | sed -n 's/^User=//p')
  service_group=$(systemctl show --property=Group "$VIDEOINSIGHT_SERVICE" | sed -n 's/^Group=//p')
  exec_start=$(systemctl show --property=ExecStart "$VIDEOINSIGHT_SERVICE" | sed -n 's/^ExecStart=//p')
  effective_environment=$(systemctl show --property=Environment "$VIDEOINSIGHT_SERVICE" | sed -n 's/^Environment=//p')
  configured_exec_start=$(sed -n 's/^ExecStart=//p' "$VIDEOINSIGHT_UNIT_PATH")
  expected_exec_path="$AUDITED_INTERPRETER_ROOT/bin/uvicorn"
  expected_exec_start="$expected_exec_path project.backend.app.control_plane:app --host 127.0.0.1 --port 18080 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1"
  expected_environment="PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VIDEOINSIGHT_RUNTIME_ROOT=$VIDEOINSIGHT_RUNTIME_ROOT AUTH_SESSION_DATABASE_PATH=$VIDEOINSIGHT_RUNTIME_ROOT/data/video_intelligence.db"
  resolved_working_directory=$(readlink -f -- "$working_directory") || \
    die "原 unit WorkingDirectory 无法解析。"

  [[ "$fragment" == "$VIDEOINSIGHT_UNIT_PATH" ]] || \
    die "原服务实际加载了其他 unit。"
  [[ -z "$drop_ins" ]] || die "原服务存在未审计的 systemd drop-in。"
  [[ "$need_daemon_reload" == "no" ]] || \
    die "原 unit 的磁盘内容与 systemd 已加载配置不一致。"
  [[ "$working_directory" == "$VIDEOINSIGHT_CURRENT" && \
      "$resolved_working_directory" == "$AUDITED_APPLICATION_ROOT" ]] || \
    die "原服务 WorkingDirectory 未精确绑定已审计 application。"
  [[ "$service_user" == "$VIDEOINSIGHT_SERVICE_USER" && \
      "$service_group" == "$VIDEOINSIGHT_SERVICE_GROUP" ]] || \
    die "原服务身份与固定 videoinsight 身份不一致。"
  [[ "$configured_exec_start" == "$expected_exec_start" ]] || \
    die "原 unit ExecStart 未精确绑定已审计 interpreter。"
  [[ "$effective_environment" == "$expected_environment" ]] || \
    die "原服务 effective Environment 与受审配置不一致。"
  validate_effective_exec_start_record "$exec_start" "$expected_exec_path" \
    "$expected_exec_start"
}

validate_repair_service_unit_for_control() {
  validate_root_file "$VIDEOINSIGHT_UNIT_PATH"
  local installed_unit_sha256
  installed_unit_sha256=$(sha256sum -- "$VIDEOINSIGHT_UNIT_PATH" | cut -d' ' -f1)
  if [[ "$installed_unit_sha256" == "$AUDITED_ORIGINAL_UNIT_SHA256" ]]; then
    validate_audited_original_service_unit_binding
    REPAIR_SERVICE_BINDING_KIND="original"
  elif [[ "$installed_unit_sha256" == "$AUDITED_BRIDGE_UNIT_SHA256" ]]; then
    validate_legacy_bridge_effective_config "$AUDITED_APPLICATION_VERSION" \
      "$AUDITED_INTERPRETER_VERSION" "$AUDITED_BRIDGE_UNIT_SHA256"
    REPAIR_SERVICE_BINDING_KIND="strict-bridge"
  elif cmp -s -- "$SCRIPT_DIR/videoinsight-control-plane.service" \
      "$VIDEOINSIGHT_UNIT_PATH"; then
    validate_audited_standard_release_service_unit_binding
    REPAIR_SERVICE_BINDING_KIND="standard-release"
  else
    die "已安装 unit 不是受审原 unit、active strict bridge 或受审标准发布 unit。"
  fi
}

validate_audited_standard_release_service_unit_binding() {
  local current_release current_version
  current_release=$(current_release_root)
  current_version="${current_release##*/}"
  [[ -n "$current_version" && "$current_release" == \
      "$VIDEOINSIGHT_RELEASES_ROOT/$current_version" ]] || \
    die "标准 unit 的 current 未精确绑定版本化发布目录。"
  validate_secure_directory "$current_release" 0
  validate_release_python "$current_release"
  validate_release_version_file "$current_release" "$current_version"
  validate_unit_effective_config
}

validate_audited_legacy_trees_for_restart() {
  validate_root_file "$SCRIPT_DIR/legacy_adoption_descriptor.py"
  local summary=()
  mapfile -t summary < <(
    run_trusted_offline_python \
      "$SCRIPT_DIR/legacy_adoption_descriptor.py" summary "$VIDEOINSIGHT_ROOT" \
      "$AUDITED_APPLICATION_VERSION" "$AUDITED_INTERPRETER_VERSION" \
      "$SERVICE_UID" "$SERVICE_GID"
  )
  [[ ${#summary[@]} -eq 4 && \
      "${summary[0]}" == "$AUDITED_APPLICATION_FILE_COUNT" && \
      "${summary[1]}" == "$AUDITED_APPLICATION_TREE_SHA256" && \
      "${summary[2]}" == "$AUDITED_INTERPRETER_TREE_ENTRY_COUNT" && \
      "${summary[3]}" == "$AUDITED_INTERPRETER_TREE_SHA256" ]] || \
    die "legacy application/interpreter 树与一次性迁移证据不一致。"
}

validate_repair_service_start_binding() {
  VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=0
  validate_offline_python_runtime
  validate_repair_service_unit_for_control
  if [[ "$REPAIR_SERVICE_BINDING_KIND" == "original" ]]; then
    validate_audited_legacy_trees_for_restart
  elif [[ "$REPAIR_SERVICE_BINDING_KIND" == "strict-bridge" ]]; then
    validate_active_legacy_adoption >/dev/null
    REPAIR_SERVICE_BINDING_KIND="strict-bridge-active"
  elif [[ "$REPAIR_SERVICE_BINDING_KIND" == "standard-release" ]]; then
    :
  else
    die "控制层启动绑定类型无效。"
  fi
}

stop_repair_service_fail_closed() {
  local reason="$1"
  local previously_validated_kind="$REPAIR_SERVICE_BINDING_KIND"
  if ! ( trap - EXIT
         validate_repair_service_unit_for_control || exit $?
         if [[ "$previously_validated_kind" == "original" || \
               "$previously_validated_kind" == "standard-release" ]]; then
           [[ "$REPAIR_SERVICE_BINDING_KIND" == "$previously_validated_kind" ]] || \
             exit 18
         elif [[ "$REPAIR_SERVICE_BINDING_KIND" == "strict-bridge" && \
                 "$previously_validated_kind" == "strict-bridge-active" ]]; then
           VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=0
           validate_offline_python_runtime || exit $?
           validate_active_legacy_adoption >/dev/null || exit $?
         elif [[ "$REPAIR_SERVICE_BINDING_KIND" == "strict-bridge" && \
                 "$previously_validated_kind" == "strict-bridge" ]]; then
           VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=0
           validate_offline_python_runtime || exit $?
           validate_active_legacy_adoption >/dev/null || exit $?
         else
           exit 18
         fi
       ) \
      >/dev/null 2>&1; then
    printf 'CRITICAL: unit 无法绑定到受审原配置或 strict bridge；未调用 systemctl stop。\n' >&2
    return 1
  fi
  stop_control_plane_fail_closed "$reason"
}

revalidate_clean_runtime_after_service_health() {
  VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=0
  validate_offline_python_runtime
}

repair_descriptor_content() {
  local status="$1"
  [[ "$status" == "prepared" || "$status" == "active" ]] || return 1
  printf '%s\n' \
    "schema=videoinsight-offline-python-runtime-repair-v1" \
    "status=$status" \
    "archive=$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE" \
    "archive-bytes=$ARCHIVE_BYTES" \
    "archive-sha256=$ARCHIVE_SHA256" \
    "archive-source=$ARCHIVE_SOURCE" \
    "legacy-tree-sha256=$LEGACY_TREE_SHA256" \
    "clean-tree-sha256=$CLEAN_TREE_SHA256" \
    "runtime=$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" \
    "legacy-backup=$LEGACY_BACKUP"
}

repair_descriptor_file_matches() {
  local path="$1"
  local status="$2"
  [[ -f "$path" && ! -L "$path" ]] || return 1
  ( validate_root_file "$path" ) >/dev/null 2>&1 || return 1
  cmp -s -- "$path" <(repair_descriptor_content "$status")
}

repair_descriptor_matches() {
  local status="$1"
  repair_descriptor_file_matches "$REPAIR_DESCRIPTOR" "$status"
}

repair_descriptor_state() {
  if [[ ! -e "$REPAIR_DESCRIPTOR" && ! -L "$REPAIR_DESCRIPTOR" ]]; then
    printf 'missing\n'
  elif repair_descriptor_matches prepared; then
    printf 'prepared\n'
  elif repair_descriptor_matches active; then
    printf 'active\n'
  else
    printf 'invalid\n'
  fi
}

discard_uncommitted_descriptor_temp() {
  [[ -e "$DESCRIPTOR_TEMP" || -L "$DESCRIPTOR_TEMP" ]] || return 0
  if ! repair_descriptor_file_matches "$DESCRIPTOR_TEMP" prepared && \
     ! repair_descriptor_file_matches "$DESCRIPTOR_TEMP" active; then
    stop_repair_service_fail_closed "离线 Python 描述临时文件异常后的停止" || true
    die "离线 Python 描述临时文件不是受审的 prepared/active 内容。"
  fi
  rm -f -- "$DESCRIPTOR_TEMP"
  fsync_path "$VIDEOINSIGHT_ROOT/state"
}

offline_python_tree_state() {
  local path="$1"
  if [[ ! -e "$path" && ! -L "$path" ]]; then
    printf 'missing\n'
  elif [[ ! -d "$path" || -L "$path" ]]; then
    printf 'invalid\n'
  elif ( validate_offline_python_tree \
      "$path" "$VIDEOINSIGHT_OFFLINE_PYTHON_MANIFEST" \
      videoinsight-offline-python-tree-v1 "clean 离线 Python" ) \
      >/dev/null 2>&1; then
    printf 'clean\n'
  elif ( validate_offline_python_tree \
      "$path" "$VIDEOINSIGHT_OFFLINE_PYTHON_LEGACY_MANIFEST" \
      videoinsight-offline-python-legacy-drift-tree-v1 \
      "legacy pyc-drift 离线 Python" ) >/dev/null 2>&1; then
    printf 'legacy\n'
  else
    printf 'invalid\n'
  fi
}

active_contaminated_backup_state() {
  if [[ ! -e "$ACTIVE_CONTAMINATED_BACKUP" && \
        ! -L "$ACTIVE_CONTAMINATED_BACKUP" ]]; then
    printf 'missing\n'
    return 0
  fi
  if [[ ! -d "$ACTIVE_CONTAMINATED_BACKUP" || \
        -L "$ACTIVE_CONTAMINATED_BACKUP" || \
        $(readlink -f -- "$ACTIVE_CONTAMINATED_BACKUP") != \
          "$ACTIVE_CONTAMINATED_BACKUP" ]]; then
    printf 'invalid\n'
    return 1
  fi
  if ! ( validate_root_directory "$ACTIVE_CONTAMINATED_BACKUP" && \
         reject_mount_at_or_below "$ACTIVE_CONTAMINATED_BACKUP" \
           "active 污染 runtime 取证目录" ) >/dev/null 2>&1; then
    printf 'invalid\n'
    return 1
  fi
  printf 'present\n'
}

classify_repair_state() {
  local descriptor_state="$1"
  local runtime_state="$2"
  local backup_state="$3"
  local stage_state="$4"
  local contaminated_state="${5:-missing}"
  case "$descriptor_state:$runtime_state:$backup_state:$stage_state:$contaminated_state" in
    missing:legacy:missing:missing:missing|missing:legacy:missing:clean:missing)
      printf 'prepare\n'
      ;;
    prepared:legacy:missing:clean:missing)
      printf 'replace\n'
      ;;
    prepared:missing:legacy:clean:missing)
      printf 'install-clean\n'
      ;;
    prepared:clean:legacy:missing:missing)
      printf 'finalize\n'
      ;;
    active:clean:legacy:missing:missing|active:clean:legacy:missing:present)
      printf 'done\n'
      ;;
    active:invalid:legacy:missing:missing)
      printf 'refresh-active\n'
      ;;
    active:missing:legacy:clean:present)
      printf 'refresh-install-clean\n'
      ;;
    *)
      printf 'invalid\n'
      return 1
      ;;
  esac
}

write_repair_descriptor() {
  local status="$1"
  [[ ! -e "$DESCRIPTOR_TEMP" && ! -L "$DESCRIPTOR_TEMP" ]] || \
    die "离线 Python 修复描述临时文件已存在。"
  if [[ "$status" == "prepared" ]]; then
    [[ ! -e "$REPAIR_DESCRIPTOR" && ! -L "$REPAIR_DESCRIPTOR" ]] || \
      die "离线 Python 修复描述已存在。"
  else
    repair_descriptor_matches prepared || \
      die "离线 Python 修复 active 晋升缺少精确 prepared 描述。"
  fi
  DESCRIPTOR_TEMP_CREATED=1
  ( set -o noclobber; umask 077; repair_descriptor_content "$status" > "$DESCRIPTOR_TEMP" )
  chown root:root -- "$DESCRIPTOR_TEMP"
  chmod 0600 -- "$DESCRIPTOR_TEMP"
  validate_root_file "$DESCRIPTOR_TEMP"
  fsync_path "$DESCRIPTOR_TEMP"
  durable_rename "$DESCRIPTOR_TEMP" "$REPAIR_DESCRIPTOR" "$VIDEOINSIGHT_ROOT/state"
  DESCRIPTOR_TEMP_CREATED=0
  repair_descriptor_matches "$status" || die "离线 Python 修复描述落盘后不一致。"
}

validate_official_archive() {
  validate_root_file "$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE_MANIFEST"
  local archive_manifest=()
  mapfile -t archive_manifest < "$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE_MANIFEST"
  [[ ${#archive_manifest[@]} -eq 3 && \
      "${archive_manifest[0]}" == "$ARCHIVE_SHA256  $VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE_NAME" && \
      "${archive_manifest[1]}" == "$ARCHIVE_BYTES  bytes" && \
      "${archive_manifest[2]}" == "$ARCHIVE_SOURCE  source" ]] || \
    die "官方离线 Python 归档清单与受审证据不一致。"

  local incoming_root="$VIDEOINSIGHT_ROOT/incoming"
  validate_root_directory "$incoming_root"
  [[ $(readlink -f -- "$incoming_root") == "$incoming_root" ]] || \
    die "固定 incoming 目录路径不规范。"
  validate_root_file "$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE"
  [[ $(readlink -f -- "$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE") == \
      "$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE" ]] || \
    die "官方离线 Python 归档路径不得经过符号链接。"
  [[ $(stat -c '%s' -- "$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE") == \
      "$ARCHIVE_BYTES" ]] || die "官方离线 Python 归档大小不一致。"
  [[ $(sha256sum -- "$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE" | cut -d' ' -f1) == \
      "$ARCHIVE_SHA256" ]] || die "官方离线 Python 归档 SHA256 不一致。"
  [[ $(validate_trusted_executable_path "$SYSTEM_TAR" /) == "$SYSTEM_TAR" ]] || \
    die "固定系统 tar 不可信。"
  "$SYSTEM_TAR" --list --gzip \
    --file "$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE" >/dev/null || \
    die "固定系统 tar 无法完整读取官方离线 Python 归档。"

  local listed_count type_counts member
  listed_count=0
  while IFS= read -r member || [[ -n "$member" ]]; do
    listed_count=$((listed_count + 1))
    [[ -n "$member" && "$member" == python/* && \
        "$member" != *'//'* && "$member" != *\\* && \
        "$member" != */../* && \
        "$member" != ../* && "$member" != */.. && \
        "$member" != */./* && "$member" != */. && \
        "$member" != ./* ]] || \
      die "官方离线 Python 归档成员路径不安全。"
    if LC_ALL=C printf '%s' "$member" | grep -q '[[:cntrl:]]'; then
      die "官方离线 Python 归档成员路径包含控制字符。"
    fi
  done < <("$SYSTEM_TAR" --list --gzip \
    --file "$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE")
  [[ "$listed_count" -eq 4533 ]] || die "官方离线 Python 归档成员数量不一致。"
  type_counts=$("$SYSTEM_TAR" --list --verbose --gzip \
    --file "$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE" | \
    awk 'BEGIN { files=0; links=0; other=0 }
         /^-/ { files++; next }
         /^l/ { links++; next }
         { other++ }
         END { printf "%d %d %d", files, links, other }') || \
    die "官方离线 Python 归档类型检查失败。"
  [[ "$type_counts" == "3485 1048 0" ]] || \
    die "官方离线 Python 归档包含未受审类型。"
}

remove_fixed_tree() {
  local path="$1"
  [[ "$path" == "$CLEAN_STAGE" || "$path" == "$EXTRACT_ROOT" ]] || \
    die "拒绝删除非固定离线 Python 修复目录。"
  [[ -d "$path" && ! -L "$path" && $(dirname -- "$path") == "$PYTHON_PARENT" && \
      $(readlink -f -- "$path") == "$path" ]] || \
    die "固定离线 Python 修复目录类型或路径无效。"
  validate_root_directory "$path"
  reject_mount_at_or_below "$path" "固定离线 Python 修复目录"
  find "$path" -xdev -mindepth 1 -depth -delete
  rmdir -- "$path"
  fsync_path "$PYTHON_PARENT"
}

build_clean_stage() {
  [[ ! -e "$EXTRACT_ROOT" && ! -L "$EXTRACT_ROOT" ]] || \
    die "离线 Python 解包目录未清理。"
  [[ ! -e "$CLEAN_STAGE" && ! -L "$CLEAN_STAGE" ]] || \
    die "离线 Python clean stage 已存在。"
  install -d -o root -g root -m 0755 -- "$EXTRACT_ROOT"
  fsync_path "$PYTHON_PARENT"
  (
    umask 022
    "$SYSTEM_TAR" --extract --gzip \
      --file "$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE" \
      --directory "$EXTRACT_ROOT" \
      --strip-components=1 \
      --no-same-owner --no-same-permissions
  )
  chown -hR root:root -- "$EXTRACT_ROOT"
  find "$EXTRACT_ROOT" -xdev -type d -exec chmod 0755 -- {} +
  find "$EXTRACT_ROOT" -xdev -type f -exec chmod go-w -- {} +
  validate_offline_python_tree \
    "$EXTRACT_ROOT" "$VIDEOINSIGHT_OFFLINE_PYTHON_MANIFEST" \
    videoinsight-offline-python-tree-v1 "官方 clean 离线 Python stage"
  fsync_tree "$EXTRACT_ROOT"
  durable_rename "$EXTRACT_ROOT" "$CLEAN_STAGE" "$PYTHON_PARENT"
  validate_offline_python_tree \
    "$CLEAN_STAGE" "$VIDEOINSIGHT_OFFLINE_PYTHON_MANIFEST" \
    videoinsight-offline-python-tree-v1 "官方 clean 离线 Python stage"
  fsync_tree "$CLEAN_STAGE"
  fsync_path "$PYTHON_PARENT"
}

repair_health_check_without_python() {
  local domain attempt ready_payload health_payload
  domain=$(control_plane_domain)
  for attempt in 1 2; do
    if (( attempt == 2 )); then
      sleep 2
    fi
    ready_payload=$(curl --disable --noproxy '*' --fail --silent --show-error \
      --max-time 5 --header "Host: $domain" "$VIDEOINSIGHT_LOCAL_URL/ready") || continue
    health_payload=$(curl --disable --noproxy '*' --fail --silent --show-error \
      --max-time 5 --header "Host: $domain" "$VIDEOINSIGHT_LOCAL_URL/health") || continue
    if printf '%s\n' "$ready_payload" | \
        grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"' && \
       printf '%s\n' "$health_payload" | \
        grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      return 0
    fi
  done
  return 1
}

restore_legacy_runtime() {
  printf 'WARNING: 离线 Python clean 切换未完成，开始恢复原 runtime。\n' >&2
  stop_repair_service_fail_closed "恢复原离线 Python 前停止控制层" || return 1
  local runtime_state backup_state stage_state
  runtime_state=$(offline_python_tree_state "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT")
  backup_state=$(offline_python_tree_state "$LEGACY_BACKUP")
  stage_state=$(offline_python_tree_state "$CLEAN_STAGE")

  if [[ "$runtime_state" == "clean" && "$backup_state" == "legacy" && \
        "$stage_state" == "missing" ]]; then
    durable_rename "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" "$CLEAN_STAGE" "$PYTHON_PARENT"
    runtime_state="missing"
    stage_state="clean"
  fi
  if [[ "$runtime_state" == "missing" && "$backup_state" == "legacy" && \
        "$stage_state" == "clean" ]]; then
    durable_rename "$LEGACY_BACKUP" "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" "$PYTHON_PARENT"
    runtime_state="legacy"
    backup_state="missing"
  fi
  [[ "$runtime_state" == "legacy" && "$backup_state" == "missing" && \
      "$stage_state" == "clean" ]] || {
    printf 'CRITICAL: 原离线 Python 无法无歧义恢复，固定服务保持停止。\n' >&2
    return 1
  }
  ( validate_offline_python_tree \
      "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" \
      "$VIDEOINSIGHT_OFFLINE_PYTHON_LEGACY_MANIFEST" \
      videoinsight-offline-python-legacy-drift-tree-v1 \
      "已恢复 legacy pyc-drift 离线 Python" ) >/dev/null 2>&1 || {
    printf 'CRITICAL: 已恢复 runtime 与 legacy 取证摘要不一致，固定服务保持停止。\n' >&2
    return 1
  }
  stop_repair_service_fail_closed "legacy runtime 恢复后的 fail-closed 停止" || return 1
  printf '已恢复原离线 Python 目录；固定服务保持停止，prepared 描述与 clean stage 保留供安全重跑。\n' >&2
  return 0
}

finish() {
  local rc=$?
  trap - EXIT
  set +e
  if [[ "$SUCCESS" -ne 1 && "$TRANSACTION_STARTED" -eq 1 ]]; then
    local descriptor_state runtime_state backup_state stage_state
    local contaminated_state action
    descriptor_state=$(repair_descriptor_state)
    runtime_state=$(offline_python_tree_state "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT")
    backup_state=$(offline_python_tree_state "$LEGACY_BACKUP")
    stage_state=$(offline_python_tree_state "$CLEAN_STAGE")
    contaminated_state=$(active_contaminated_backup_state)
    action=$(classify_repair_state \
      "$descriptor_state" "$runtime_state" "$backup_state" "$stage_state" \
      "$contaminated_state" 2>/dev/null)
    if [[ "$ACTIVE_REFRESH" -eq 1 ]]; then
      stop_repair_service_fail_closed \
        "active runtime 刷新异常后的 fail-closed 停止" || true
      printf 'CRITICAL: active runtime 刷新未完成；保留固定事务状态并保持服务停止。\n' >&2
    elif [[ "$action" == "done" ]]; then
      if ( trap - EXIT
           fsync_path "$VIDEOINSIGHT_ROOT/state" && \
             validate_repair_service_start_binding && \
             systemctl is-active --quiet "$VIDEOINSIGHT_SERVICE" && \
             health_check "" && \
             revalidate_clean_runtime_after_service_health
         ); then
        printf 'active 修复描述已持久化且 clean runtime 健康，退出时保持新 runtime。\n' >&2
        rc=0
      else
        stop_repair_service_fail_closed "active clean runtime 退出健康失败后的停止" || true
        printf 'CRITICAL: active 提交点已落盘但服务不健康；保留 clean runtime 并保持服务停止。\n' >&2
      fi
    else
      restore_legacy_runtime || true
    fi
  fi
  if [[ "$DESCRIPTOR_TEMP_CREATED" -eq 1 ]]; then
    case "$DESCRIPTOR_TEMP" in
      "$VIDEOINSIGHT_ROOT/state/.offline-python-runtime-repair.new")
        rm -f -- "$DESCRIPTOR_TEMP"
        ;;
    esac
  fi
  exit "$rc"
}

main() {
  [[ $# -eq 2 ]] || usage
  readonly SERVICE_UID="$1"
  readonly SERVICE_GID="$2"

  require_root
  for command_name in awk chmod chown cmp curl cut find flock getent grep id \
    install mv readlink rmdir sed sha256sum sleep sort stat systemctl tr; do
    require_command "$command_name"
  done
  validate_service_identity "$SERVICE_UID" "$SERVICE_GID"
  validate_control_root
  validate_root_directory "$PYTHON_PARENT"
  [[ $(readlink -f -- "$PYTHON_PARENT") == "$PYTHON_PARENT" ]] || \
    die "离线 Python 父目录路径不规范。"
  validate_secure_file "$VIDEOINSIGHT_ENV_FILE" 0
  open_native_release_lock_without_runtime_validation
  validate_repair_service_unit_for_control
  discard_uncommitted_descriptor_temp

  local descriptor_state runtime_state backup_state stage_state
  local contaminated_state action
  descriptor_state=$(repair_descriptor_state)
  runtime_state=$(offline_python_tree_state "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT")
  backup_state=$(offline_python_tree_state "$LEGACY_BACKUP")
  stage_state=$(offline_python_tree_state "$CLEAN_STAGE")
  contaminated_state=$(active_contaminated_backup_state) || contaminated_state="invalid"
  action=$(classify_repair_state \
    "$descriptor_state" "$runtime_state" "$backup_state" "$stage_state" \
    "$contaminated_state" 2>/dev/null) || \
    action="invalid"

  if [[ "$descriptor_state" == "invalid" || \
        "$contaminated_state" == "invalid" || "$action" == "invalid" ]]; then
    stop_repair_service_fail_closed "离线 Python 修复混合状态后的停止" || true
    die "离线 Python 修复状态不是已审计的 prepared/active 组合。"
  fi
  if [[ "$action" == "prepare" || "$action" == "replace" || \
        "$action" == "install-clean" ]]; then
    [[ "$REPAIR_SERVICE_BINDING_KIND" == "original" ]] || \
      die "未完成的离线 Python 修复只接受受审原 unit。"
  fi
  if [[ -e "$EXTRACT_ROOT" || -L "$EXTRACT_ROOT" ]]; then
    if [[ "$descriptor_state" == "missing" && -d "$EXTRACT_ROOT" && \
          ! -L "$EXTRACT_ROOT" ]]; then
      remove_fixed_tree "$EXTRACT_ROOT"
    else
      stop_repair_service_fail_closed "离线 Python 解包残留混合状态后的停止" || true
      die "离线 Python 修复描述存在时出现未受审解包残留。"
    fi
  fi

  if [[ "$action" == "refresh-active" ]]; then
    validate_official_archive
    if [[ "$stage_state" == "invalid" ]]; then
      [[ -d "$CLEAN_STAGE" && ! -L "$CLEAN_STAGE" ]] || \
        die "active 刷新 clean stage 类型不安全。"
      remove_fixed_tree "$CLEAN_STAGE"
      stage_state="missing"
    fi
    if [[ "$stage_state" == "missing" ]]; then
      build_clean_stage
    else
      validate_offline_python_tree \
        "$CLEAN_STAGE" "$VIDEOINSIGHT_OFFLINE_PYTHON_MANIFEST" \
        videoinsight-offline-python-tree-v1 "active 刷新 clean stage"
    fi
    ACTIVE_REFRESH=1
    TRANSACTION_STARTED=1
    stop_repair_service_fail_closed "active 污染 runtime 刷新前停止控制层"
    validate_root_directory "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT"
    reject_mount_at_or_below "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" \
      "active 污染 runtime"
    durable_rename "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" \
      "$ACTIVE_CONTAMINATED_BACKUP" "$PYTHON_PARENT"
    runtime_state="missing"
    contaminated_state="present"
    action="refresh-install-clean"
  fi

  if [[ "$action" == "refresh-install-clean" ]]; then
    ACTIVE_REFRESH=1
    TRANSACTION_STARTED=1
    stop_repair_service_fail_closed "active clean runtime 安装前停止控制层"
    [[ ! -e "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" && \
       ! -L "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" ]] || \
      die "active 刷新目标 runtime 已被占用。"
    validate_offline_python_tree \
      "$CLEAN_STAGE" "$VIDEOINSIGHT_OFFLINE_PYTHON_MANIFEST" \
      videoinsight-offline-python-tree-v1 "active 刷新 clean stage"
    durable_rename "$CLEAN_STAGE" "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" \
      "$PYTHON_PARENT"
    validate_repair_service_start_binding
    systemctl start "$VIDEOINSIGHT_SERVICE"
    health_check "" || {
      stop_repair_service_fail_closed \
        "active clean runtime 刷新健康失败后的停止" || true
      die "active clean runtime 刷新后服务未就绪。"
    }
    revalidate_clean_runtime_after_service_health
    SUCCESS=1
    printf 'active 污染 runtime 已由官方 clean runtime 原子替换。\n'
    printf '污染 runtime 取证备份：%s\n' "$ACTIVE_CONTAMINATED_BACKUP"
    return 0
  fi

  if [[ "$action" == "done" ]]; then
    TRANSACTION_STARTED=1
    validate_repair_service_start_binding
    systemctl is-active --quiet "$VIDEOINSIGHT_SERVICE" || \
      systemctl start "$VIDEOINSIGHT_SERVICE"
    health_check "" || {
      stop_repair_service_fail_closed "active clean runtime 健康失败后的停止" || true
      die "clean 离线 Python active 状态服务未就绪。"
    }
    revalidate_clean_runtime_after_service_health
    SUCCESS=1
    printf '官方 clean 离线 Python 已处于 active 状态，无需重复替换。\n'
    printf '旧 pyc-drift runtime 备份：%s\n' "$LEGACY_BACKUP"
    return 0
  fi

  if [[ "$action" == "prepare" ]]; then
    validate_official_archive
    if [[ "$stage_state" == "invalid" ]]; then
      [[ -d "$CLEAN_STAGE" && ! -L "$CLEAN_STAGE" ]] || \
        die "无描述的 clean stage 类型不安全。"
      remove_fixed_tree "$CLEAN_STAGE"
      stage_state="missing"
    fi
    if [[ "$stage_state" == "missing" ]]; then
      build_clean_stage
    else
      validate_offline_python_tree \
        "$CLEAN_STAGE" "$VIDEOINSIGHT_OFFLINE_PYTHON_MANIFEST" \
        videoinsight-offline-python-tree-v1 "既有官方 clean 离线 Python stage"
      fsync_tree "$CLEAN_STAGE"
      fsync_path "$PYTHON_PARENT"
    fi
    TRANSACTION_STARTED=1
    write_repair_descriptor prepared
    PREPARED_CREATED=1
    descriptor_state="prepared"
    runtime_state="legacy"
    backup_state="missing"
    stage_state="clean"
    action="replace"
  fi

  if [[ "$action" == "finalize" ]]; then
    TRANSACTION_STARTED=1
    validate_repair_service_start_binding
    systemctl is-active --quiet "$VIDEOINSIGHT_SERVICE" || \
      systemctl start "$VIDEOINSIGHT_SERVICE"
    health_check "" || {
      stop_repair_service_fail_closed "断电恢复 clean runtime 健康失败后的停止" || true
      die "断电恢复后的 clean 离线 Python 服务未就绪。"
    }
    revalidate_clean_runtime_after_service_health
    write_repair_descriptor active
    SUCCESS=1
    printf '断电遗留的 clean runtime 已验证并晋升 active。\n'
    printf '旧 pyc-drift runtime 备份：%s\n' "$LEGACY_BACKUP"
    return 0
  fi

  TRANSACTION_STARTED=1
  if [[ "$action" == "replace" && "$PREPARED_CREATED" -eq 1 ]]; then
    systemctl is-active --quiet "$VIDEOINSIGHT_SERVICE" || \
      die "首次替换前 legacy 控制层不是既有 active 状态；拒绝启动 drift Python。"
    repair_health_check_without_python || die "首次替换前 legacy 控制层未就绪。"
  fi
  stop_repair_service_fail_closed "离线 Python 原子替换前停止控制层"
  if [[ "$action" == "replace" ]]; then
    durable_rename "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" "$LEGACY_BACKUP" "$PYTHON_PARENT"
  elif [[ "$action" != "install-clean" ]]; then
    die "离线 Python 修复事务动作无效。"
  fi
  durable_rename "$CLEAN_STAGE" "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" "$PYTHON_PARENT"
  validate_repair_service_start_binding
  systemctl start "$VIDEOINSIGHT_SERVICE"
  health_check "" || {
    stop_repair_service_fail_closed "clean runtime 启动健康失败后的停止" || true
    die "官方 clean 离线 Python 切换后服务未就绪。"
  }
  revalidate_clean_runtime_after_service_health
  write_repair_descriptor active
  SUCCESS=1
  printf '官方 clean 离线 Python 已完成原子替换并处于 active 状态。\n'
  printf '旧 pyc-drift runtime 备份：%s\n' "$LEGACY_BACKUP"
  printf 'active 描述：%s\n' "$REPAIR_DESCRIPTOR"
  printf '未操作反向代理、容器或其他 systemd 服务。\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  trap finish EXIT
  main "$@"
fi
