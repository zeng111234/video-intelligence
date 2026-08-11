#!/bin/bash
set -Eeuo pipefail

readonly VIDEOINSIGHT_SYSTEM_PATH="/usr/sbin:/usr/bin:/sbin:/bin"
if [[ "${PATH:-}" != "$VIDEOINSIGHT_SYSTEM_PATH" ]] || \
  ( PATH=/videoinsight-untrusted-path ) 2>/dev/null; then
  printf 'ERROR: native-systemd 入口必须先固定并只读化 PATH。\n' >&2
  if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 1
  fi
  exit 1
fi

readonly VIDEOINSIGHT_ROOT="/opt/videoinsight-control-plane"
readonly VIDEOINSIGHT_SERVICE="videoinsight-control-plane.service"
readonly VIDEOINSIGHT_SERVICE_USER="videoinsight"
readonly VIDEOINSIGHT_SERVICE_GROUP="videoinsight"
readonly VIDEOINSIGHT_UNIT_PATH="/etc/systemd/system/videoinsight-control-plane.service"
readonly VIDEOINSIGHT_LOCAL_URL="http://127.0.0.1:18080"
readonly VIDEOINSIGHT_RELEASES_ROOT="$VIDEOINSIGHT_ROOT/releases"
readonly VIDEOINSIGHT_CURRENT="$VIDEOINSIGHT_ROOT/current"
readonly VIDEOINSIGHT_RUNTIME_ROOT="$VIDEOINSIGHT_ROOT/runtime"
readonly VIDEOINSIGHT_BACKUP_ROOT="$VIDEOINSIGHT_ROOT/backups"
readonly VIDEOINSIGHT_ENV_FILE="$VIDEOINSIGHT_ROOT/config/control-plane.env"
readonly VIDEOINSIGHT_OFFLINE_PYTHON="$VIDEOINSIGHT_ROOT/python/3.12.13/bin/python3.12"
readonly VIDEOINSIGHT_OFFLINE_PYTHON_ROOT="$VIDEOINSIGHT_ROOT/python/3.12.13"
readonly VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE_NAME="cpython-3.12.13+20260807-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
readonly VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE="$VIDEOINSIGHT_ROOT/incoming/$VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE_NAME"
readonly VIDEOINSIGHT_WHEELHOUSE="$VIDEOINSIGHT_ROOT/wheelhouse"
readonly VIDEOINSIGHT_MEDIA_ROOT="$VIDEOINSIGHT_ROOT/tools/media/bin"
readonly VIDEOINSIGHT_SERVICE_PATH="$VIDEOINSIGHT_MEDIA_ROOT:/usr/local/bin:/usr/bin:/bin"
readonly VIDEOINSIGHT_SYSTEM_ENV="/usr/bin/env"
readonly VIDEOINSIGHT_SYSTEM_TIMEOUT="/usr/bin/timeout"
readonly VIDEOINSIGHT_SYSTEM_RUNUSER="/usr/sbin/runuser"
readonly VIDEOINSIGHT_SYSTEM_SYNC="/usr/bin/sync"
readonly VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR="$VIDEOINSIGHT_ROOT/state/legacy-adoption.json"

native_script_dir() {
  CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd
}

readonly VIDEOINSIGHT_WHEELHOUSE_MANIFEST="$(native_script_dir)/wheelhouse.sha256"
readonly VIDEOINSIGHT_MEDIA_TOOLS_MANIFEST="$(native_script_dir)/media-tools.sha256"
readonly VIDEOINSIGHT_OFFLINE_PYTHON_MANIFEST="$(native_script_dir)/offline-python-tree.sha256"
readonly VIDEOINSIGHT_OFFLINE_PYTHON_ARCHIVE_MANIFEST="$(native_script_dir)/offline-python-archive.sha256"
readonly VIDEOINSIGHT_OFFLINE_PYTHON_LEGACY_MANIFEST="$(native_script_dir)/offline-python-legacy-drift-tree.sha256"

VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=0

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die "必须使用 root 执行原生控制层发布脚本。"
}

require_uint() {
  local label="$1"
  local value="$2"
  [[ "$value" =~ ^[0-9]+$ ]] || die "$label 必须是显式的非负整数。"
}

require_version() {
  local value="$1"
  [[ "$value" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] || \
    die "版本号格式无效：$value"
}

require_stable_version() {
  local value="$1"
  [[ "$value" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
    die "本轮原生发布只接受 x.y.z 稳定版本：$value"
}

version_is_strictly_greater() {
  local candidate="$1"
  local baseline="$2"
  local candidate_major candidate_minor candidate_patch
  local baseline_major baseline_minor baseline_patch
  IFS=. read -r candidate_major candidate_minor candidate_patch <<< "$candidate"
  IFS=. read -r baseline_major baseline_minor baseline_patch <<< "$baseline"
  local candidate_part baseline_part
  for candidate_part in "$candidate_major" "$candidate_minor" "$candidate_patch"; do
    [[ "$candidate_part" =~ ^[0-9]+$ ]] || return 1
  done
  for baseline_part in "$baseline_major" "$baseline_minor" "$baseline_patch"; do
    [[ "$baseline_part" =~ ^[0-9]+$ ]] || return 1
  done
  if (( 10#$candidate_major != 10#$baseline_major )); then
    (( 10#$candidate_major > 10#$baseline_major ))
  elif (( 10#$candidate_minor != 10#$baseline_minor )); then
    (( 10#$candidate_minor > 10#$baseline_minor ))
  else
    (( 10#$candidate_patch > 10#$baseline_patch ))
  fi
}

require_sha256() {
  local value="$1"
  [[ "$value" =~ ^[0-9A-Fa-f]{64}$ ]] || die "SHA256 必须是 64 位十六进制。"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "缺少系统命令：$1"
}

stop_control_plane_fail_closed() {
  local context="${1:-控制层 fail-closed 停止}"
  local initial_stop_rc=0 active_rc active_state main_pid_record
  if systemctl stop "$VIDEOINSIGHT_SERVICE"; then
    :
  else
    initial_stop_rc=$?
  fi
  if active_state=$(systemctl is-active "$VIDEOINSIGHT_SERVICE" 2>/dev/null); then
    active_rc=0
  else
    active_rc=$?
  fi
  main_pid_record=""
  if [[ "$active_rc" -eq 3 && \
        ( "$active_state" == "inactive" || "$active_state" == "failed" ) ]]; then
    main_pid_record=$(systemctl show --property=MainPID "$VIDEOINSIGHT_SERVICE" \
      2>/dev/null) || main_pid_record=""
  fi
  if [[ "$active_rc" -ne 3 || \
        ( "$active_state" != "inactive" && "$active_state" != "failed" ) || \
        "$main_pid_record" != "MainPID=0" ]]; then
    systemctl kill --kill-who=all --signal=KILL "$VIDEOINSIGHT_SERVICE" \
      >/dev/null 2>&1 || true
    systemctl stop "$VIDEOINSIGHT_SERVICE" >/dev/null 2>&1 || true
    if active_state=$(systemctl is-active "$VIDEOINSIGHT_SERVICE" 2>/dev/null); then
      active_rc=0
    else
      active_rc=$?
    fi
    main_pid_record=""
    if [[ "$active_rc" -eq 3 && \
          ( "$active_state" == "inactive" || "$active_state" == "failed" ) ]]; then
      main_pid_record=$(systemctl show --property=MainPID "$VIDEOINSIGHT_SERVICE" \
        2>/dev/null) || main_pid_record=""
    fi
  fi
  if [[ "$active_rc" -ne 3 || \
        ( "$active_state" != "inactive" && "$active_state" != "failed" ) || \
        "$main_pid_record" != "MainPID=0" ]]; then
    printf 'CRITICAL: %s失败；唯一目标服务未确认停止（state=%s rc=%s %s）。\n' \
      "$context" "${active_state:-<empty>}" "$active_rc" \
      "${main_pid_record:-MainPID=<unknown>}" >&2
    return 1
  fi
  if [[ "$initial_stop_rc" -ne 0 ]]; then
    printf 'WARNING: systemctl stop 返回 %s，但已复核唯一目标服务为 inactive。\n' \
      "$initial_stop_rc" >&2
  fi
  return 0
}

validate_trusted_executable_path() {
  local candidate="$1"
  local allowed_root="${2:-/}"
  local resolved resolved_allowed mode owner group current service_gid execute_mask
  [[ "$candidate" == /* ]] || die "受信任工具路径必须是绝对路径：$candidate"
  resolved=$(readlink -f -- "$candidate") || die "受信任工具路径无法解析：$candidate"
  resolved_allowed=$(readlink -f -- "$allowed_root") || \
    die "受信任工具允许根目录无法解析：$allowed_root"
  [[ "$resolved" == "$candidate" ]] || die "受信任工具路径不得经过符号链接：$candidate"
  [[ "$resolved_allowed" == "$allowed_root" && -d "$resolved_allowed" && \
      ! -L "$resolved_allowed" ]] || \
    die "受信任工具允许根目录不是规范普通目录：$allowed_root"
  if [[ "$resolved_allowed" != "/" ]]; then
    [[ "$resolved" == "$resolved_allowed/"* ]] || \
      die "受信任工具越过固定媒体目录：$resolved"
  fi
  [[ "$resolved" == /* && -f "$resolved" && ! -L "$resolved" && -x "$resolved" ]] || \
    die "受信任工具不是可执行普通文件：$candidate"
  service_gid=$(getent group "$VIDEOINSIGHT_SERVICE_GROUP" | cut -d: -f3)
  [[ "$service_gid" =~ ^[0-9]+$ ]] || die "固定服务组不存在或 GID 无效。"
  read -r mode owner group < <(stat -c '%a %u %g' -- "$resolved")
  [[ "$owner" == "0" ]] || die "受信任工具必须由 root 持有：$resolved"
  [[ "$group" == "0" || "$group" == "$service_gid" ]] || \
    die "受信任工具只能属于 root 或固定服务组：$resolved"
  (( (8#$mode & 0022) == 0 )) || \
    die "受信任工具不能由组或其他用户写入：$resolved"
  execute_mask=0001
  [[ "$group" != "$service_gid" ]] || execute_mask=0010
  (( (8#$mode & 8#$execute_mask) != 0 )) || \
    die "受信任工具必须允许固定服务用户执行：$resolved"

  current=$(dirname -- "$resolved")
  while :; do
    [[ -d "$current" && ! -L "$current" ]] || \
      die "受信任工具祖先不是普通目录：$current"
    read -r mode owner group < <(stat -c '%a %u %g' -- "$current")
    [[ "$owner" == "0" ]] || die "受信任工具祖先必须由 root 持有：$current"
    [[ "$group" == "0" || "$group" == "$service_gid" ]] || \
      die "受信任工具祖先只能属于 root 或固定服务组：$current"
    (( (8#$mode & 0022) == 0 )) || \
      die "受信任工具祖先不能由组或其他用户写入：$current"
    execute_mask=0001
    [[ "$group" != "$service_gid" ]] || execute_mask=0010
    (( (8#$mode & 8#$execute_mask) != 0 )) || \
      die "固定服务用户无法遍历受信任工具祖先：$current"
    [[ "$current" == "/" ]] && break
    current=$(dirname -- "$current")
  done
  printf '%s\n' "$resolved"
}

validate_media_tools_manifest() {
  validate_root_file "$VIDEOINSIGHT_MEDIA_TOOLS_MANIFEST"
  local line line_count=0 name hash
  local manifest_pattern='^([0-9a-f]{64})  (ffmpeg|ffprobe)$'
  local ffmpeg_hash="" ffprobe_hash=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    line_count=$((line_count + 1))
    [[ "$line" =~ $manifest_pattern ]] || \
      die "媒体工具 SHA256 清单格式无效。"
    hash="${BASH_REMATCH[1]}"
    name="${BASH_REMATCH[2]}"
    case "$name" in
      ffmpeg)
        [[ -z "$ffmpeg_hash" ]] || die "媒体工具 SHA256 清单包含重复 ffmpeg。"
        ffmpeg_hash="$hash"
        ;;
      ffprobe)
        [[ -z "$ffprobe_hash" ]] || die "媒体工具 SHA256 清单包含重复 ffprobe。"
        ffprobe_hash="$hash"
        ;;
    esac
  done < "$VIDEOINSIGHT_MEDIA_TOOLS_MANIFEST"
  [[ "$line_count" -eq 2 && -n "$ffmpeg_hash" && -n "$ffprobe_hash" ]] || \
    die "媒体工具 SHA256 清单必须精确包含 ffmpeg 和 ffprobe。"
}

validate_trusted_execution_dependencies() {
  local candidate
  for candidate in \
    "$VIDEOINSIGHT_SYSTEM_ENV" \
    "$VIDEOINSIGHT_SYSTEM_TIMEOUT" \
    "$VIDEOINSIGHT_SYSTEM_RUNUSER" \
    "$VIDEOINSIGHT_SYSTEM_SYNC"; do
    [[ $(validate_trusted_executable_path "$candidate" /) == "$candidate" ]] || \
      die "服务身份执行依赖不是固定受信任文件：$candidate"
  done
}

validate_effective_exec_start_record() {
  local serialized="$1"
  local expected_path="$2"
  local expected_argv="$3"
  local record_pattern='^\{ path=([^ ;]+) ; argv\[\]=([^;]+) ; ignore_errors=no ; start_time=\[[^]]*\] ; stop_time=\[[^]]*\] ; pid=([0-9]+) ; code=([^ ;]+) ; status=([^ ;]+) \}$'
  [[ -n "$serialized" && "$serialized" != *$'\n'* && "$serialized" != *$'\r'* ]] || \
    die "systemd 实际 ExecStart 必须是单行非空记录。"
  [[ "$expected_path" == /* && \
      ( "$expected_argv" == "$expected_path" || \
        "$expected_argv" == "$expected_path "* ) ]] || \
    die "受控 ExecStart 的 path/argv 绑定无效。"
  [[ "$serialized" =~ $record_pattern ]] || \
    die "systemd 实际 ExecStart 不是唯一且结构完整的单条记录。"
  [[ "${BASH_REMATCH[1]}" == "$expected_path" && \
      "${BASH_REMATCH[2]}" == "$expected_argv" ]] || \
    die "systemd 实际 ExecStart path 或完整 argv 与受控 unit 不一致。"
}

reject_mount_at_or_below() {
  local root="$1"
  local label="$2"
  [[ -r /proc/self/mountinfo ]] || die "$label 无法读取系统挂载信息。"
  local runtime_mount
  runtime_mount=$(awk -v root="$root" \
    'function decode(value) {
       gsub(/\\040/, " ", value)
       gsub(/\\011/, "\t", value)
       gsub(/\\012/, "\n", value)
       gsub(/\\134/, sprintf("%c", 92), value)
       return value
     }
     NF < 5 { exit 2 }
     { mount_point=decode($5) }
     mount_point == root || index(mount_point, root "/") == 1 { print "found"; exit }' \
    /proc/self/mountinfo) || die "$label 挂载边界校验失败。"
  [[ -z "$runtime_mount" ]] || die "$label 自身或后代包含独立挂载。"
}

validate_offline_python_tree() {
  local runtime_root="$1"
  local manifest_path="$2"
  local manifest_marker="$3"
  local label="$4"
  [[ "$runtime_root" == "$VIDEOINSIGHT_ROOT/python/"* ]] || \
    die "$label 根目录越过固定离线 Python 父目录。"
  [[ -d "$runtime_root" && ! -L "$runtime_root" && \
      $(readlink -f -- "$runtime_root") == "$runtime_root" ]] || \
    die "$label 根目录不是规范普通目录。"
  validate_root_file "$manifest_path"

  local manifest_lines=()
  mapfile -t manifest_lines < "$manifest_path"
  [[ ${#manifest_lines[@]} -eq 4 ]] || die "$label tree-v1 清单必须精确为 4 行。"
  local expected_digest expected_descendants expected_files expected_links
  [[ "${manifest_lines[0]}" =~ ^([0-9a-f]{64})\ \ ([a-z0-9-]+)$ && \
      "${BASH_REMATCH[2]}" == "$manifest_marker" ]] || \
    die "$label tree-v1 清单摘要行无效。"
  expected_digest="${BASH_REMATCH[1]}"
  [[ "${manifest_lines[1]}" =~ ^([0-9]+)\ \ descendants$ ]] || \
    die "$label tree-v1 清单条目数行无效。"
  expected_descendants="${BASH_REMATCH[1]}"
  [[ "${manifest_lines[2]}" =~ ^([0-9]+)\ \ regular-files$ ]] || \
    die "$label tree-v1 清单文件数行无效。"
  expected_files="${BASH_REMATCH[1]}"
  [[ "${manifest_lines[3]}" =~ ^([0-9]+)\ \ symlinks$ ]] || \
    die "$label tree-v1 清单链接数行无效。"
  expected_links="${BASH_REMATCH[1]}"

  reject_mount_at_or_below "$runtime_root" "$label"
  local actual_descendants actual_files actual_links
  actual_descendants=$(find "$runtime_root" -mindepth 1 -printf . | wc -c | tr -d '[:space:]')
  actual_files=$(find "$runtime_root" -mindepth 1 -type f -printf . | wc -c | tr -d '[:space:]')
  actual_links=$(find "$runtime_root" -mindepth 1 -type l -printf . | wc -c | tr -d '[:space:]')
  [[ "$actual_descendants" == "$expected_descendants" && \
      "$actual_files" == "$expected_files" && \
      "$actual_links" == "$expected_links" ]] || \
    die "$label 条目、文件或符号链接数量与 tree-v1 清单不一致。"

  local root_mode root_uid root_gid root_device service_gid actual_digest
  read -r root_mode root_uid root_gid root_device < <(stat -c '%a %u %g %d' -- "$runtime_root")
  service_gid=$(getent group "$VIDEOINSIGHT_SERVICE_GROUP" | cut -d: -f3)
  [[ "$root_uid" == "0" && "$service_gid" =~ ^[0-9]+$ ]] || \
    die "$label 根目录或固定服务组无效。"
  (( (8#$root_mode & 0022) == 0 )) || \
    die "$label 根目录可由组或其他用户修改。"
  if [[ "$root_gid" == "$service_gid" ]]; then
    (( (8#$root_mode & 0010) != 0 )) || die "固定服务身份无法遍历$label 根目录。"
  else
    (( (8#$root_mode & 0001) != 0 )) || die "固定服务身份无法遍历$label 根目录。"
  fi

  actual_digest=$(
    {
      printf 'd\t.\t%04o\t%s\t%s\t\n' "$((8#$root_mode))" "$root_uid" "$root_gid"
      local relative path mode uid gid device kind value target_lines resolved leaf
      while IFS= read -r -d '' relative; do
        if LC_ALL=C printf '%s' "$relative" | grep -q '[[:cntrl:]]'; then
          die "$label 路径包含控制字符。"
        fi
        path="$runtime_root/$relative"
        read -r mode uid gid device < <(stat -c '%a %u %g %d' -- "$path")
        [[ "$uid" == "0" && "$device" == "$root_device" ]] || \
          die "$label 条目不是 root 持有或跨越文件系统：$relative"
        if [[ -L "$path" ]]; then
          kind="l"
          target_lines=$(readlink -- "$path" | wc -l | tr -d '[:space:]')
          [[ "$target_lines" == "1" ]] || die "$label 符号链接目标包含换行。"
          value=$(readlink -- "$path")
          if LC_ALL=C printf '%s' "$value" | grep -q '[[:cntrl:]]'; then
            die "$label 符号链接目标包含控制字符。"
          fi
          resolved=$(readlink -f -- "$path") || die "$label 包含失效符号链接。"
          [[ "$resolved" == "$runtime_root" || "$resolved" == "$runtime_root/"* ]] || \
            die "$label 符号链接越过固定运行时根目录。"
        elif [[ -f "$path" ]]; then
          kind="f"
          (( (8#$mode & 0022) == 0 )) || \
            die "$label 文件可由组或其他用户修改：$relative"
          if [[ "$gid" == "$service_gid" ]]; then
            (( (8#$mode & 0040) != 0 )) || die "固定服务身份无法读取$label 文件。"
          else
            (( (8#$mode & 0004) != 0 )) || die "固定服务身份无法读取$label 文件。"
          fi
          leaf="${relative##*/}"
          if [[ "$leaf" == "sitecustomize.py" || "$leaf" == "usercustomize.py" ]]; then
            die "$label 包含自定义 site 启动代码。"
          fi
          value=$(sha256sum -- "$path" | cut -d' ' -f1)
        elif [[ -d "$path" ]]; then
          kind="d"
          (( (8#$mode & 0022) == 0 )) || \
            die "$label 目录可由组或其他用户修改：$relative"
          if [[ "$gid" == "$service_gid" ]]; then
            (( (8#$mode & 0010) != 0 )) || die "固定服务身份无法遍历$label 目录。"
          else
            (( (8#$mode & 0001) != 0 )) || die "固定服务身份无法遍历$label 目录。"
          fi
          value=""
        else
          die "$label 包含特殊文件：$relative"
        fi
        printf '%s\t%s\t%04o\t%s\t%s\t%s\n' \
          "$kind" "$relative" "$((8#$mode))" "$uid" "$gid" "$value"
      done < <(CDPATH= cd -- "$runtime_root" && \
        find . -mindepth 1 -printf '%P\0' | LC_ALL=C sort -z)
    } | sha256sum | cut -d' ' -f1
  ) || die "$label tree-v1 纯系统工具校验失败。"
  [[ "$actual_digest" == "$expected_digest" ]] || \
    die "$label tree-v1 聚合 SHA256 不一致。"
}

validate_offline_python_runtime() {
  [[ "$VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED" -eq 0 ]] || return 0
  if [[ -f "$VIDEOINSIGHT_OFFLINE_PYTHON_LEGACY_MANIFEST" && \
        ! -L "$VIDEOINSIGHT_OFFLINE_PYTHON_LEGACY_MANIFEST" ]] && \
     ( validate_offline_python_tree \
         "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" \
         "$VIDEOINSIGHT_OFFLINE_PYTHON_LEGACY_MANIFEST" \
         videoinsight-offline-python-legacy-drift-tree-v1 \
         "legacy pyc-drift 离线 Python" ) >/dev/null 2>&1; then
    die "检测到已取证的 legacy pyc-drift runtime；必须先执行 normalize_offline_python_runtime.sh。"
  fi
  validate_offline_python_tree \
    "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" \
    "$VIDEOINSIGHT_OFFLINE_PYTHON_MANIFEST" \
    videoinsight-offline-python-tree-v1 \
    "离线 Python 运行时"
  validate_trusted_execution_dependencies
  validate_trusted_executable_path "$VIDEOINSIGHT_OFFLINE_PYTHON" / >/dev/null
  VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=1
}

run_trusted_offline_python() {
  [[ "$VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED" -eq 1 ]] || \
    die "拒绝在完整离线 Python 运行时门禁前执行 Python。"
  local environment=(
    HOME=/nonexistent
    LANG=C
    LC_ALL=C
    PATH=/usr/bin:/bin
    PYTHONDONTWRITEBYTECODE=1
    "VIDEOINSIGHT_RUNTIME_ROOT=$VIDEOINSIGHT_RUNTIME_ROOT"
    "VIDEOINSIGHT_BACKUP_ROOT=$VIDEOINSIGHT_BACKUP_ROOT"
  )
  "$VIDEOINSIGHT_SYSTEM_ENV" -i "${environment[@]}" \
    "$VIDEOINSIGHT_OFFLINE_PYTHON" -B -I -S -X utf8 "$@"
}

run_trusted_offline_python_for_service() {
  local service_uid="$1"
  local service_gid="$2"
  shift 2
  [[ "$service_uid" =~ ^[0-9]+$ && "$service_gid" =~ ^[0-9]+$ ]] || \
    die "传给离线 Python 的服务 UID/GID 无效。"
  [[ "$VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED" -eq 1 ]] || \
    die "拒绝在完整离线 Python 运行时门禁前执行 Python。"
  "$VIDEOINSIGHT_SYSTEM_ENV" -i \
    HOME=/nonexistent LANG=C LC_ALL=C PATH=/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    "VIDEOINSIGHT_RUNTIME_ROOT=$VIDEOINSIGHT_RUNTIME_ROOT" \
    "VIDEOINSIGHT_BACKUP_ROOT=$VIDEOINSIGHT_BACKUP_ROOT" \
    "VIDEOINSIGHT_SERVICE_UID=$service_uid" \
    "VIDEOINSIGHT_SERVICE_GID=$service_gid" \
    "$VIDEOINSIGHT_OFFLINE_PYTHON" -B -I -S -X utf8 "$@"
}

run_trusted_offline_python_in_tmp() {
  local trusted_tmp="$1"
  shift
  [[ "$VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED" -eq 1 ]] || \
    die "拒绝在完整离线 Python 运行时门禁前执行 Python。"
  validate_secure_directory "$trusted_tmp" 0
  real_path_under "$trusted_tmp" "$VIDEOINSIGHT_ROOT" || \
    die "离线 Python 临时目录越过固定控制层根目录。"
  "$VIDEOINSIGHT_SYSTEM_ENV" -i \
    HOME=/nonexistent LANG=C LC_ALL=C PATH=/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    "TMPDIR=$trusted_tmp" \
    "VIDEOINSIGHT_RUNTIME_ROOT=$VIDEOINSIGHT_RUNTIME_ROOT" \
    "VIDEOINSIGHT_BACKUP_ROOT=$VIDEOINSIGHT_BACKUP_ROOT" \
    "$VIDEOINSIGHT_OFFLINE_PYTHON" -B -I -S -X utf8 "$@"
}

run_trusted_staging_python() {
  local python_path="$1"
  local staging_root="$2"
  local trusted_tmp="$3"
  shift 3
  [[ "$VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED" -eq 1 ]] || \
    die "拒绝在完整离线 Python 运行时门禁前执行 staging Python。"
  [[ -d "$staging_root" && ! -L "$staging_root" && \
      $(readlink -f -- "$staging_root") == "$staging_root" ]] || \
    die "staging Python 根目录无效。"
  validate_secure_directory "$staging_root" 0
  validate_secure_directory "$trusted_tmp" 0
  real_path_under "$trusted_tmp" "$staging_root" || die "staging Python 临时目录越界。"
  [[ -f "$python_path" && ! -L "$python_path" && -x "$python_path" ]] || \
    die "staging Python 不是可执行普通文件。"
  validate_secure_file "$python_path" 0
  real_path_under "$python_path" "$staging_root" || die "staging Python 路径越界。"
  local current mode owner
  current=$(dirname -- "$python_path")
  while :; do
    [[ -d "$current" && ! -L "$current" ]] || die "staging Python 祖先不是普通目录。"
    read -r mode owner < <(stat -c '%a %u' -- "$current")
    [[ "$owner" == "0" ]] || die "staging Python 祖先必须由 root 持有。"
    (( (8#$mode & 0022) == 0 )) || die "staging Python 祖先可由组或其他用户修改。"
    [[ "$current" != "$staging_root" ]] || break
    current=$(dirname -- "$current")
  done
  "$VIDEOINSIGHT_SYSTEM_ENV" -i \
    HOME=/nonexistent LANG=C LC_ALL=C PATH=/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    "TMPDIR=$trusted_tmp" PIP_CONFIG_FILE=/dev/null PIP_NO_CACHE_DIR=1 \
    PIP_NO_INDEX=1 \
    "$python_path" -B -I -X utf8 "$@"
}

resolve_command_in_explicit_path() {
  local name="$1"
  local explicit_path="$2"
  [[ "$name" =~ ^[A-Za-z0-9._+-]+$ ]] || return 1
  local directories=() directory candidate
  IFS=: read -r -a directories <<< "$explicit_path"
  for directory in "${directories[@]}"; do
    [[ "$directory" == /* ]] || return 1
    candidate="$directory/$name"
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

validate_trusted_media_tool() {
  local name="$1"
  case "$name" in
    ffmpeg|ffprobe) ;;
    *) die "未允许的媒体校验工具名：$name" ;;
  esac
  validate_media_tools_manifest
  [[ -d "$VIDEOINSIGHT_MEDIA_ROOT" && ! -L "$VIDEOINSIGHT_MEDIA_ROOT" ]] || \
    die "固定媒体工具目录不存在或是符号链接。"
  [[ $(readlink -f -- "$VIDEOINSIGHT_MEDIA_ROOT") == "$VIDEOINSIGHT_MEDIA_ROOT" ]] || \
    die "固定媒体工具目录路径不规范。"
  local actual_files
  actual_files=$(find "$VIDEOINSIGHT_MEDIA_ROOT" -mindepth 1 -maxdepth 1 \
    -type f -printf '%f\n' | LC_ALL=C sort)
  [[ "$actual_files" == $'ffmpeg\nffprobe' ]] || \
    die "固定媒体工具目录必须精确包含 ffmpeg 和 ffprobe 两个普通文件。"
  if find "$VIDEOINSIGHT_MEDIA_ROOT" -mindepth 1 -maxdepth 1 ! -type f \
    -print -quit | grep -q .; then
    die "固定媒体工具目录包含非普通文件。"
  fi
  local candidate expected expected_hash actual_hash
  expected="$VIDEOINSIGHT_MEDIA_ROOT/$name"
  candidate=$(resolve_command_in_explicit_path "$name" "$VIDEOINSIGHT_SERVICE_PATH") || \
    die "固定服务 PATH 缺少受信任媒体校验工具：$name"
  [[ "$candidate" == "$expected" ]] || \
    die "固定服务 PATH 未解析到隔离媒体工具：$name"
  candidate=$(validate_trusted_executable_path "$candidate" "$VIDEOINSIGHT_MEDIA_ROOT")
  expected_hash=$(sed -n "s/^\([0-9a-f]\{64\}\)  $name$/\1/p" \
    "$VIDEOINSIGHT_MEDIA_TOOLS_MANIFEST")
  [[ "$expected_hash" =~ ^[0-9a-f]{64}$ ]] || \
    die "媒体工具缺少唯一固定 SHA256：$name"
  actual_hash=$(sha256sum -- "$candidate" | cut -d ' ' -f1)
  [[ "$actual_hash" == "$expected_hash" ]] || \
    die "媒体工具 SHA256 与受跟踪清单不一致：$name"
  printf '%s\n' "$candidate"
}

validate_trusted_media_tool_execution() {
  local name="$1"
  local candidate="$2"
  local expected="$VIDEOINSIGHT_MEDIA_ROOT/$name"
  case "$name" in
    ffmpeg|ffprobe) ;;
    *) die "未允许执行的媒体校验工具名：$name" ;;
  esac
  [[ "$candidate" == "$expected" && -f "$candidate" && ! -L "$candidate" ]] || \
    die "媒体工具执行候选不是已校验的精确普通文件：$name"
  [[ $(readlink -f -- "$candidate") == "$expected" ]] || \
    die "媒体工具执行候选路径在哈希校验后发生变化：$name"
  "$VIDEOINSIGHT_SYSTEM_ENV" -i HOME=/nonexistent PATH="/usr/bin:/usr/sbin:/bin" \
    "$VIDEOINSIGHT_SYSTEM_TIMEOUT" --signal=KILL 10 \
      "$VIDEOINSIGHT_SYSTEM_RUNUSER" --user "$VIDEOINSIGHT_SERVICE_USER" \
          --group "$VIDEOINSIGHT_SERVICE_GROUP" -- \
          "$VIDEOINSIGHT_SYSTEM_ENV" -i HOME=/nonexistent \
            PATH="$VIDEOINSIGHT_SERVICE_PATH" \
            "$candidate" -hide_banner -version \
            </dev/null >/dev/null 2>&1 || \
    die "固定服务身份无法在 10 秒内执行受信任媒体工具：$name"
}

fsync_path() {
  local path="$1"
  [[ -e "$path" && ! -L "$path" ]] || die "无法落盘不存在或为符号链接的路径：$path"
  validate_trusted_executable_path "$VIDEOINSIGHT_SYSTEM_SYNC" / >/dev/null
  "$VIDEOINSIGHT_SYSTEM_SYNC" -f -- "$path" || die "路径 fsync 失败：$path"
}

fsync_tree() {
  local root="$1"
  [[ -d "$root" && ! -L "$root" ]] || die "无法落盘非普通目录树：$root"
  validate_trusted_executable_path "$VIDEOINSIGHT_SYSTEM_SYNC" / >/dev/null
  if find "$root" -xdev ! -type d ! -type f ! -type l -print -quit | grep -q .; then
    die "发布树包含无法安全 fsync 的特殊文件。"
  fi
  local link resolved
  while IFS= read -r -d '' link; do
    resolved=$(readlink -f -- "$link") || die "发布树包含失效符号链接。"
    [[ "$resolved" == "$root" || "$resolved" == "$root/"* ]] || \
      die "发布树符号链接越过根目录。"
  done < <(find "$root" -xdev -type l -print0)
  find "$root" -xdev -type f -exec "$VIDEOINSIGHT_SYSTEM_SYNC" -f -- {} +
  find "$root" -xdev -depth -type d -exec "$VIDEOINSIGHT_SYSTEM_SYNC" -f -- {} +
}

durable_rename() {
  local source="$1"
  local destination="$2"
  local parent="$3"
  [[ $(dirname -- "$source") == "$parent" && $(dirname -- "$destination") == "$parent" ]] || \
    die "持久化 rename 的源和目标必须位于同一固定父目录。"
  fsync_path "$parent"
  mv -Tf -- "$source" "$destination"
  fsync_path "$parent"
}

real_path_under() {
  local candidate="$1"
  local allowed_root="$2"
  local resolved
  resolved=$(readlink -f -- "$candidate") || return 1
  [[ "$resolved" == "$allowed_root" || "$resolved" == "$allowed_root/"* ]]
}

validate_service_identity() {
  local expected_uid="$1"
  local expected_gid="$2"
  require_uint "服务 UID" "$expected_uid"
  require_uint "服务 GID" "$expected_gid"

  local actual_uid actual_gid
  actual_uid=$(id -u "$VIDEOINSIGHT_SERVICE_USER") || die "服务账号不存在。"
  actual_gid=$(id -g "$VIDEOINSIGHT_SERVICE_USER") || die "服务账号不存在。"
  [[ "$actual_uid" == "$expected_uid" ]] || die "服务 UID 与显式参数不一致。"
  [[ "$actual_gid" == "$expected_gid" ]] || die "服务 GID 与显式参数不一致。"
  [[ $(getent group "$VIDEOINSIGHT_SERVICE_GROUP" | cut -d: -f3) == "$expected_gid" ]] || \
    die "服务组 GID 与显式参数不一致。"
}

current_target_path() {
  [[ -L "$VIDEOINSIGHT_CURRENT" ]] || die "current 必须是指向 releases 的符号链接。"
  local resolved
  resolved=$(readlink -f -- "$VIDEOINSIGHT_CURRENT") || die "current 链接无法解析。"
  [[ -d "$resolved" ]] || die "current 指向的版本目录不存在。"
  real_path_under "$resolved" "$VIDEOINSIGHT_RELEASES_ROOT" || \
    die "current 指向了控制层 releases 之外。"
  printf '%s\n' "$resolved"
}

current_release_root() {
  local target
  target=$(current_target_path)
  if [[ $(basename -- "$target") == "app" ]]; then
    target=$(dirname -- "$target")
  fi
  real_path_under "$target" "$VIDEOINSIGHT_RELEASES_ROOT" || \
    die "current 版本根目录越过 releases。"
  printf '%s\n' "$target"
}

release_app_path() {
  local release_root="$1"
  [[ -d "$release_root" && ! -L "$release_root" ]] || \
    die "版本根目录不存在或是符号链接。"
  local resolved_release app_root resolved_app
  resolved_release=$(readlink -f -- "$release_root") || die "版本根目录无法解析。"
  real_path_under "$resolved_release" "$VIDEOINSIGHT_RELEASES_ROOT" || \
    die "版本根目录越过 releases。"
  app_root="$resolved_release/app"
  [[ -d "$app_root" && ! -L "$app_root" ]] || \
    die "版本缺少普通 app 目录。"
  resolved_app=$(readlink -f -- "$app_root") || die "版本 app 目录无法解析。"
  [[ "$resolved_app" == "$app_root" ]] || die "版本 app 目录路径不规范。"
  real_path_under "$resolved_app" "$resolved_release" || \
    die "版本 app 目录越过版本根目录。"
  printf '%s\n' "$resolved_app"
}

validate_secure_file() {
  local path="$1"
  local expected_uid="$2"
  [[ -f "$path" && ! -L "$path" ]] || die "安全文件不存在或是符号链接：$path"
  local mode owner
  read -r mode owner < <(stat -c '%a %u' -- "$path")
  [[ "$owner" == "$expected_uid" ]] || die "文件所有者不符合要求：$path"
  (( (8#$mode & 0022) == 0 )) || die "文件不能由组或其他用户写入：$path"
}

validate_secure_directory() {
  local path="$1"
  local expected_uid="$2"
  [[ -d "$path" && ! -L "$path" ]] || die "安全目录不存在或是符号链接：$path"
  local mode owner
  read -r mode owner < <(stat -c '%a %u' -- "$path")
  [[ "$owner" == "$expected_uid" ]] || die "目录所有者不符合要求：$path"
  (( (8#$mode & 0022) == 0 )) || die "目录不能由组或其他用户写入：$path"
}

validate_root_file() {
  local path="$1"
  validate_secure_file "$path" 0
  local group
  group=$(stat -c '%g' -- "$path")
  [[ "$group" == "0" ]] || die "root 安全文件必须属于 root:root：$path"
}

validate_root_directory() {
  local path="$1"
  validate_secure_directory "$path" 0
  local group
  group=$(stat -c '%g' -- "$path")
  [[ "$group" == "0" ]] || die "root 安全目录必须属于 root:root：$path"
}

validate_control_root() {
  [[ -d "$VIDEOINSIGHT_ROOT" && ! -L "$VIDEOINSIGHT_ROOT" ]] || \
    die "固定控制层根目录不存在或是符号链接。"
  validate_secure_directory "$VIDEOINSIGHT_ROOT" 0
  local control_group service_group
  control_group=$(stat -c '%g' -- "$VIDEOINSIGHT_ROOT")
  service_group=$(getent group "$VIDEOINSIGHT_SERVICE_GROUP" | cut -d: -f3)
  [[ "$control_group" == "0" || \
      ( -n "$service_group" && "$control_group" == "$service_group" ) ]] || \
    die "固定控制层根目录只能属于 root 或固定服务组。"
  [[ $(readlink -f -- "$VIDEOINSIGHT_ROOT") == "$VIDEOINSIGHT_ROOT" ]] || \
    die "固定控制层根目录路径不规范。"
}

prepare_secure_state() {
  validate_control_root
  local state_root="$VIDEOINSIGHT_ROOT/state"
  local created=0
  if [[ -e "$state_root" || -L "$state_root" ]]; then
    [[ -d "$state_root" && ! -L "$state_root" ]] || \
      die "发布 state 不是普通目录。"
  else
    install -d -o root -g root -m 0700 -- "$state_root"
    created=1
  fi
  real_path_under "$state_root" "$VIDEOINSIGHT_ROOT" || \
    die "发布 state 越过控制层根目录。"
  validate_root_directory "$state_root"
  if [[ "$created" -eq 1 ]]; then
    fsync_path "$VIDEOINSIGHT_ROOT"
  fi
}

prepare_secure_state_directory() {
  local name="$1"
  [[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || die "发布 state 子目录名称无效。"
  prepare_secure_state
  local path="$VIDEOINSIGHT_ROOT/state/$name"
  local created=0
  if [[ -e "$path" || -L "$path" ]]; then
    [[ -d "$path" && ! -L "$path" ]] || die "发布 state 子路径不是普通目录。"
  else
    install -d -o root -g root -m 0700 -- "$path"
    created=1
  fi
  real_path_under "$path" "$VIDEOINSIGHT_ROOT/state" || \
    die "发布 state 子目录越界。"
  validate_root_directory "$path"
  if [[ "$created" -eq 1 ]]; then
    fsync_path "$VIDEOINSIGHT_ROOT/state"
  fi
}

open_native_release_lock_without_runtime_validation() {
  prepare_secure_state
  local lock_path="$VIDEOINSIGHT_ROOT/state/native-release.lock"
  if [[ -e "$lock_path" || -L "$lock_path" ]]; then
    validate_root_file "$lock_path"
  else
    ( umask 077; : >> "$lock_path" )
    chown root:root -- "$lock_path"
    chmod 0600 -- "$lock_path"
    validate_root_file "$lock_path"
    fsync_path "$lock_path"
    fsync_path "$VIDEOINSIGHT_ROOT/state"
  fi
  exec 9>>"$lock_path"
  flock -n 9 || die "已有一个控制层发布事务正在执行。"
}

open_native_release_lock() {
  validate_offline_python_runtime
  open_native_release_lock_without_runtime_validation
}

validate_release_python() {
  local release_root="$1"
  local resolved_release venv_root bin_root python_path resolved_python
  resolved_release=$(readlink -f -- "$release_root") || die "版本根目录无法解析。"
  real_path_under "$resolved_release" "$VIDEOINSIGHT_RELEASES_ROOT" || \
    die "版本根目录越过 releases。"
  venv_root="$resolved_release/venv"
  bin_root="$venv_root/bin"
  python_path="$bin_root/python"
  [[ -d "$venv_root" && ! -L "$venv_root" ]] || die "版本缺少普通 venv 目录。"
  [[ -d "$bin_root" && ! -L "$bin_root" ]] || die "版本缺少普通 venv/bin 目录。"
  validate_secure_directory "$venv_root" 0
  validate_secure_directory "$bin_root" 0
  [[ -f "$python_path" && ! -L "$python_path" && -x "$python_path" ]] || \
    die "版本 Python 不是可执行普通文件。"
  validate_secure_file "$python_path" 0
  resolved_python=$(readlink -f -- "$python_path") || die "版本 Python 无法解析。"
  [[ "$resolved_python" == "$python_path" ]] || die "版本 Python 路径不规范。"
  real_path_under "$resolved_python" "$venv_root" || die "版本 Python 越过独立 venv。"
}

validate_release_version_file() {
  local release_root="$1"
  local expected_version="$2"
  local app_root version_file
  app_root=$(release_app_path "$release_root")
  version_file="$app_root/release_version.txt"
  validate_secure_file "$version_file" 0
  [[ $(tr -d '\r\n' < "$version_file") == "$expected_version" ]] || \
    die "版本标识与版本目录不一致。"
}

validate_unit_file_directives() {
  local unit_path="$1"
  local require_service_path="${2:-1}"
  [[ "$require_service_path" == "0" || "$require_service_path" == "1" ]] || \
    die "systemd PATH 审计模式无效。"
  if grep -Eq '\\[[:space:]]*$' "$unit_path"; then
    die "systemd unit 不允许续行。"
  fi
  if grep -Eiq '^[[:space:]]*(ExecCondition|ExecStartPre|ExecStartPost|ExecReload|ExecStop|ExecStopPost|OnFailure|OnSuccess|PartOf|BindsTo|Conflicts|Requires|Requisite|Upholds|PropagatesStopTo|PropagatesReloadTo|ReloadPropagatedFrom|JoinsNamespaceOf|Also|Alias)[[:space:]]*=' "$unit_path"; then
    die "systemd unit 含有可执行额外代码或联动其他服务的指令。"
  fi
  if grep -Eiq 'PYTHONPATH|^[[:space:]]*(PassEnvironment|UnsetEnvironment)[[:space:]]*=' "$unit_path"; then
    die "systemd unit 含有未允许的 Python 或环境注入。"
  fi

  local line trimmed section="" environment_file_count=0 exec_start_count=0
  local unit_count=0 service_count=0 install_count=0 description_count=0
  local after_count=0 wants_count=0 wanted_by_count=0
  local key required_key
  declare -A service_seen=()
  declare -A environment_seen=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    trimmed="${line#"${line%%[![:space:]]*}"}"
    case "$trimmed" in
      ""|'#'*|';'*) continue ;;
      '[Unit]')
        section="Unit"
        unit_count=$((unit_count + 1))
        continue
        ;;
      '[Service]')
        section="Service"
        service_count=$((service_count + 1))
        continue
        ;;
      '[Install]')
        section="Install"
        install_count=$((install_count + 1))
        continue
        ;;
      '['*']') die "systemd unit 含有未允许的 section。" ;;
    esac
    [[ -n "$section" ]] || die "systemd unit 指令出现在 section 之外。"
    if [[ "$section" == "Unit" ]]; then
      case "$trimmed" in
        'Description=VideoInsight Control Plane')
          description_count=$((description_count + 1))
          ;;
        'After=network-online.target') after_count=$((after_count + 1)) ;;
        'Wants=network-online.target') wants_count=$((wants_count + 1)) ;;
        *) die "systemd [Unit] 含有未允许的指令。" ;;
      esac
      continue
    fi
    if [[ "$section" == "Install" ]]; then
      if [[ "$trimmed" == "WantedBy=multi-user.target" ]]; then
        wanted_by_count=$((wanted_by_count + 1))
      else
        die "systemd [Install] 只能包含固定 WantedBy。"
      fi
      continue
    fi
    [[ "$trimmed" == *=* ]] || die "systemd [Service] 指令格式无效。"
    key="${trimmed%%=*}"
    if [[ "$key" == "Environment" ]]; then
      case "$trimmed" in
        Environment=PYTHONDONTWRITEBYTECODE=1|\
        Environment=PYTHONUNBUFFERED=1|\
        Environment=PATH="$VIDEOINSIGHT_SERVICE_PATH"|\
        Environment=VIDEOINSIGHT_RUNTIME_ROOT="$VIDEOINSIGHT_RUNTIME_ROOT"|\
        Environment=VIDEOINSIGHT_BACKUP_ROOT="$VIDEOINSIGHT_BACKUP_ROOT"|\
        Environment=AUTH_SESSION_DATABASE_PATH="$VIDEOINSIGHT_RUNTIME_ROOT/data/video_intelligence.db")
          [[ -z ${environment_seen[$trimmed]+present} ]] || \
            die "systemd unit 含有重复 Environment。"
          environment_seen["$trimmed"]=1
          ;;
        *) die "systemd unit 含有未允许的 Environment。" ;;
      esac
      continue
    fi
    [[ -z ${service_seen[$key]+present} ]] || \
      die "systemd [Service] 含有重复指令：$key"
    case "$key" in
      Type) [[ "$trimmed" == "Type=simple" ]] ;;
      User) [[ "$trimmed" == "User=$VIDEOINSIGHT_SERVICE_USER" ]] ;;
      Group) [[ "$trimmed" == "Group=$VIDEOINSIGHT_SERVICE_GROUP" ]] ;;
      WorkingDirectory)
        [[ "$trimmed" =~ ^WorkingDirectory=/opt/videoinsight-control-plane/(current(/app)?|releases/[0-9]+\.[0-9]+\.[0-9]+/app)$ ]]
        ;;
      EnvironmentFile)
        [[ "$trimmed" == "EnvironmentFile=$VIDEOINSIGHT_ENV_FILE" ]] || \
          die "systemd unit 使用了额外 EnvironmentFile。"
        environment_file_count=$((environment_file_count + 1))
        ;;
      ExecStart)
        [[ "$trimmed" =~ ^ExecStart=/opt/videoinsight-control-plane/(current|releases/[0-9]+\.[0-9]+\.[0-9]+)/venv/bin/(python\ -m\ uvicorn|uvicorn)\ project\.backend\.app\.control_plane:app\ --host\ 127\.0\.0\.1\ --port\ 18080\ --workers\ 1\ --proxy-headers\ --forwarded-allow-ips\ 127\.0\.0\.1$ ]] || \
          die "systemd unit 的 ExecStart 不是唯一允许的控制层命令。"
        exec_start_count=$((exec_start_count + 1))
        ;;
      Restart) [[ "$trimmed" == "Restart=on-failure" ]] ;;
      RestartSec) [[ "$trimmed" == "RestartSec=5" ]] ;;
      TimeoutStartSec) [[ "$trimmed" == "TimeoutStartSec=60" ]] ;;
      TimeoutStopSec) [[ "$trimmed" == "TimeoutStopSec=30" ]] ;;
      UMask) [[ "$trimmed" == "UMask=0077" ]] ;;
      LimitNOFILE) [[ "$trimmed" == "LimitNOFILE=65536" ]] ;;
      LimitNPROC) [[ "$trimmed" == "LimitNPROC=128" ]] ;;
      CPUQuota) [[ "$trimmed" == "CPUQuota=100%" ]] ;;
      MemoryLimit) [[ "$trimmed" == "MemoryLimit=1G" ]] ;;
      NoNewPrivileges) [[ "$trimmed" == "NoNewPrivileges=true" ]] ;;
      PrivateTmp) [[ "$trimmed" == "PrivateTmp=true" ]] ;;
      PrivateDevices) [[ "$trimmed" == "PrivateDevices=true" ]] ;;
      ProtectHome) [[ "$trimmed" == "ProtectHome=true" ]] ;;
      ProtectSystem) [[ "$trimmed" == "ProtectSystem=full" ]] ;;
      CapabilityBoundingSet) [[ "$trimmed" == "CapabilityBoundingSet=" ]] ;;
      *) die "systemd [Service] 含有未允许的指令：$key" ;;
    esac || die "systemd [Service] 指令值不符合固定安全模板：$key"
    service_seen["$key"]=1
  done < "$unit_path"
  for required_key in Type User Group WorkingDirectory EnvironmentFile ExecStart \
    Restart RestartSec TimeoutStartSec TimeoutStopSec UMask LimitNOFILE LimitNPROC \
    CPUQuota MemoryLimit NoNewPrivileges PrivateTmp PrivateDevices ProtectHome \
    ProtectSystem CapabilityBoundingSet; do
    [[ -n ${service_seen[$required_key]+present} ]] || \
      die "systemd [Service] 缺少固定安全指令：$required_key"
  done
  for required_key in \
    "Environment=PYTHONDONTWRITEBYTECODE=1" \
    "Environment=PYTHONUNBUFFERED=1" \
    "Environment=VIDEOINSIGHT_RUNTIME_ROOT=$VIDEOINSIGHT_RUNTIME_ROOT" \
    "Environment=VIDEOINSIGHT_BACKUP_ROOT=$VIDEOINSIGHT_BACKUP_ROOT" \
    "Environment=AUTH_SESSION_DATABASE_PATH=$VIDEOINSIGHT_RUNTIME_ROOT/data/video_intelligence.db"; do
    [[ -n ${environment_seen[$required_key]+present} ]] || \
      die "systemd [Service] 缺少固定 Environment。"
  done
  if [[ "$require_service_path" == "1" ]]; then
    [[ -n ${environment_seen["Environment=PATH=$VIDEOINSIGHT_SERVICE_PATH"]+present} ]] || \
      die "systemd [Service] 缺少固定服务 PATH。"
  fi
  [[ "$unit_count" -eq 1 && "$service_count" -eq 1 && "$install_count" -eq 1 && \
      "$description_count" -eq 1 && "$after_count" -eq 1 && "$wants_count" -eq 1 && \
      "$wanted_by_count" -eq 1 ]] || \
    die "systemd unit 的 section、网络依赖或安装目标不完整。"
  [[ "$exec_start_count" -eq 1 ]] || die "systemd unit 必须且只能包含一个 ExecStart。"
  [[ "$environment_file_count" -eq 1 ]] || \
    die "systemd unit 必须且只能使用固定 EnvironmentFile。"
}

validate_unit_file_safety() {
  local unit_path="$1"
  local require_service_path="${2:-1}"
  validate_root_file "$unit_path"
  validate_unit_file_directives "$unit_path" "$require_service_path"
}

validate_tools_match_release() {
  local release_tools="$1"
  local current_tools
  current_tools=$(native_script_dir)
  [[ -d "$release_tools" && ! -L "$release_tools" ]] || \
    die "新版本缺少普通 native-systemd 工具目录。"
  if find "$current_tools" "$release_tools" \
    \( -type d -name '__pycache__' -o -type f \( -name '*.pyc' -o -name '*.pyo' \) \) \
    -print -quit | grep -q .; then
    die "native-systemd 工具目录含有 Python 缓存；请清理后重新打包。"
  fi
  if find "$current_tools" "$release_tools" -mindepth 1 ! -type d ! -type f \
    -print -quit | grep -q .; then
    die "native-systemd 工具目录包含非普通文件。"
  fi
  local current_entries release_entries current_files release_files relative
  current_entries=$(CDPATH= cd -- "$current_tools" && find . -mindepth 1 \
    \( -type d -printf 'd:%P\n' -o -type f -printf 'f:%P\n' \) | LC_ALL=C sort)
  release_entries=$(CDPATH= cd -- "$release_tools" && find . -mindepth 1 \
    \( -type d -printf 'd:%P\n' -o -type f -printf 'f:%P\n' \) | LC_ALL=C sort)
  [[ -n "$current_entries" && "$current_entries" == "$release_entries" ]] || \
    die "外置 native-systemd 工具与新部署 ZIP 文件或目录集合不一致。"
  current_files=$(CDPATH= cd -- "$current_tools" && find . -type f -printf '%P\n' | LC_ALL=C sort)
  release_files=$(CDPATH= cd -- "$release_tools" && find . -type f -printf '%P\n' | LC_ALL=C sort)
  [[ -n "$current_files" && "$current_files" == "$release_files" ]] || \
    die "外置 native-systemd 工具与新部署 ZIP 文件集合不一致。"
  while IFS= read -r relative; do
    cmp -s -- "$current_tools/$relative" "$release_tools/$relative" || \
      die "外置 native-systemd 工具与新部署 ZIP 内容不一致：$relative"
  done <<< "$current_files"
}

validate_service_directory() {
  local path="$1"
  local expected_uid="$2"
  local expected_gid="$3"
  [[ -d "$path" && ! -L "$path" ]] || die "服务目录不存在或是符号链接：$path"
  local mode owner group
  read -r mode owner group < <(stat -c '%a %u %g' -- "$path")
  [[ "$owner" == "$expected_uid" && "$group" == "$expected_gid" ]] || \
    die "服务目录 UID/GID 不符合要求：$path"
  (( (8#$mode & 0077) == 0 )) || die "服务目录向组或其他用户开放：$path"
}

reject_nested_mounts() {
  local root="$1"
  local resolved_root line mount_path
  resolved_root=$(readlink -f -- "$root") || die "运行数据目录无法解析。"
  if [[ ! -r /proc/self/mountinfo ]]; then
    case "${OSTYPE:-}" in
      msys*|cygwin*) return ;;
      *) die "无法读取系统挂载信息，拒绝递归处理运行数据。" ;;
    esac
  fi
  while IFS= read -r line; do
    read -r _ _ _ _ mount_path _ <<< "$line"
    mount_path=${mount_path//\\040/ }
    mount_path=${mount_path//\\011/$'\t'}
    mount_path=${mount_path//\\012/$'\n'}
    mount_path=${mount_path//\\134/\\}
    if [[ "$mount_path" == "$resolved_root/"* ]]; then
      die "运行数据目录包含子挂载点：$mount_path"
    fi
  done < /proc/self/mountinfo
}

validate_service_tree() {
  local root="$1"
  local expected_uid="$2"
  local expected_gid="$3"
  validate_service_directory "$root" "$expected_uid" "$expected_gid"
  reject_nested_mounts "$root"
  if find "$root" -xdev -type l -print -quit | grep -q .; then
    die "运行数据目录不能包含符号链接。"
  fi
  if find "$root" -xdev ! -type d ! -type f -print -quit | grep -q .; then
    die "运行数据目录只能包含普通目录和普通文件。"
  fi
  local root_device path mode owner group device
  root_device=$(stat -c '%d' -- "$root")
  while IFS= read -r -d '' path; do
    read -r mode owner group device < <(stat -c '%a %u %g %d' -- "$path")
    [[ "$owner" == "$expected_uid" && "$group" == "$expected_gid" ]] || \
      die "运行数据项 UID/GID 不符合服务身份：$path"
    [[ "$device" == "$root_device" ]] || \
      die "运行数据项位于子挂载或其他文件系统：$path"
    (( (8#$mode & 0077) == 0 )) || \
      die "运行数据项向组或其他用户开放：$path"
  done < <(find "$root" -xdev \( -type d -o -type f \) -print0)
}

validate_unit_effective_config() {
  local template
  template="$(native_script_dir)/videoinsight-control-plane.service"
  [[ -f "$VIDEOINSIGHT_UNIT_PATH" && ! -L "$VIDEOINSIGHT_UNIT_PATH" ]] || \
    die "固定 systemd unit 尚未安装。"
  validate_unit_file_safety "$VIDEOINSIGHT_UNIT_PATH"
  cmp -s -- "$template" "$VIDEOINSIGHT_UNIT_PATH" || \
    die "已安装 unit 与发布模板不一致。"

  local fragment drop_ins working_directory service_user service_group exec_start configured_exec_start
  local configured_exec_path
  fragment=$(systemctl show --property=FragmentPath "$VIDEOINSIGHT_SERVICE" | sed -n 's/^FragmentPath=//p')
  drop_ins=$(systemctl show --property=DropInPaths "$VIDEOINSIGHT_SERVICE" | sed -n 's/^DropInPaths=//p')
  working_directory=$(systemctl show --property=WorkingDirectory "$VIDEOINSIGHT_SERVICE" | sed -n 's/^WorkingDirectory=//p')
  service_user=$(systemctl show --property=User "$VIDEOINSIGHT_SERVICE" | sed -n 's/^User=//p')
  service_group=$(systemctl show --property=Group "$VIDEOINSIGHT_SERVICE" | sed -n 's/^Group=//p')
  exec_start=$(systemctl show --property=ExecStart "$VIDEOINSIGHT_SERVICE" | sed -n 's/^ExecStart=//p')
  configured_exec_start=$(sed -n 's/^ExecStart=//p' "$VIDEOINSIGHT_UNIT_PATH")
  configured_exec_path="${configured_exec_start%% *}"

  [[ "$fragment" == "$VIDEOINSIGHT_UNIT_PATH" ]] || die "systemd 实际加载了其他 unit。"
  [[ -z "$drop_ins" ]] || die "存在未审计的 systemd drop-in，已停止发布。"
  [[ "$working_directory" == "$VIDEOINSIGHT_CURRENT/app" ]] || die "WorkingDirectory 未指向 current/app。"
  [[ "$service_user" == "$VIDEOINSIGHT_SERVICE_USER" ]] || die "systemd User 不正确。"
  [[ "$service_group" == "$VIDEOINSIGHT_SERVICE_GROUP" ]] || die "systemd Group 不正确。"
  validate_effective_exec_start_record "$exec_start" "$configured_exec_path" \
    "$configured_exec_start"
}

validate_existing_unit_scope() {
  [[ -f "$VIDEOINSIGHT_UNIT_PATH" && ! -L "$VIDEOINSIGHT_UNIT_PATH" ]] || \
    die "控制层 unit 不在唯一允许路径。"
  validate_unit_file_safety "$VIDEOINSIGHT_UNIT_PATH" 0
  local fragment drop_ins working_directory service_user service_group exec_start configured_exec_start
  local configured_exec_path
  fragment=$(systemctl show --property=FragmentPath "$VIDEOINSIGHT_SERVICE" | sed -n 's/^FragmentPath=//p')
  drop_ins=$(systemctl show --property=DropInPaths "$VIDEOINSIGHT_SERVICE" | sed -n 's/^DropInPaths=//p')
  working_directory=$(systemctl show --property=WorkingDirectory "$VIDEOINSIGHT_SERVICE" | sed -n 's/^WorkingDirectory=//p')
  service_user=$(systemctl show --property=User "$VIDEOINSIGHT_SERVICE" | sed -n 's/^User=//p')
  service_group=$(systemctl show --property=Group "$VIDEOINSIGHT_SERVICE" | sed -n 's/^Group=//p')
  exec_start=$(systemctl show --property=ExecStart "$VIDEOINSIGHT_SERVICE" | sed -n 's/^ExecStart=//p')
  configured_exec_start=$(sed -n 's/^ExecStart=//p' "$VIDEOINSIGHT_UNIT_PATH")
  configured_exec_path="${configured_exec_start%% *}"

  [[ "$fragment" == "$VIDEOINSIGHT_UNIT_PATH" ]] || die "systemd 实际加载了其他 unit。"
  [[ -z "$drop_ins" ]] || die "存在未审计的 systemd drop-in。"
  [[ "$working_directory" == "$VIDEOINSIGHT_ROOT"/* ]] || die "现有 WorkingDirectory 越过控制层根目录。"
  [[ "$service_user" == "$VIDEOINSIGHT_SERVICE_USER" ]] || die "现有 systemd User 不正确。"
  [[ "$service_group" == "$VIDEOINSIGHT_SERVICE_GROUP" ]] || die "现有 systemd Group 不正确。"
  validate_effective_exec_start_record "$exec_start" "$configured_exec_path" \
    "$configured_exec_start"
  grep -Fx "EnvironmentFile=$VIDEOINSIGHT_ENV_FILE" "$VIDEOINSIGHT_UNIT_PATH" >/dev/null || \
    die "现有 unit 未使用固定配置文件。"
}

write_legacy_bridge_unit() {
  local output="$1"
  local application_version="$2"
  local interpreter_version="$3"
  require_stable_version "$application_version"
  require_stable_version "$interpreter_version"
  [[ ! -e "$output" && ! -L "$output" ]] || die "legacy bridge unit 临时路径已存在。"
  ( umask 077
    printf '%s\n' \
      '[Unit]' \
      'Description=VideoInsight Control Plane' \
      'After=network-online.target' \
      'Wants=network-online.target' \
      '' \
      '[Service]' \
      'Type=simple' \
      "User=$VIDEOINSIGHT_SERVICE_USER" \
      "Group=$VIDEOINSIGHT_SERVICE_GROUP" \
      "WorkingDirectory=$VIDEOINSIGHT_CURRENT" \
      "EnvironmentFile=$VIDEOINSIGHT_ENV_FILE" \
      'Environment=PYTHONDONTWRITEBYTECODE=1' \
      'Environment=PYTHONUNBUFFERED=1' \
      "Environment=PATH=$VIDEOINSIGHT_SERVICE_PATH" \
      "Environment=VIDEOINSIGHT_RUNTIME_ROOT=$VIDEOINSIGHT_RUNTIME_ROOT" \
      "Environment=VIDEOINSIGHT_BACKUP_ROOT=$VIDEOINSIGHT_BACKUP_ROOT" \
      "Environment=AUTH_SESSION_DATABASE_PATH=$VIDEOINSIGHT_RUNTIME_ROOT/data/video_intelligence.db" \
      "ExecStart=$VIDEOINSIGHT_RELEASES_ROOT/$interpreter_version/venv/bin/python -m uvicorn project.backend.app.control_plane:app --host 127.0.0.1 --port 18080 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1" \
      'Restart=on-failure' \
      'RestartSec=5' \
      'TimeoutStartSec=60' \
      'TimeoutStopSec=30' \
      'UMask=0077' \
      'LimitNOFILE=65536' \
      'LimitNPROC=128' \
      'CPUQuota=100%' \
      'MemoryLimit=1G' \
      'NoNewPrivileges=true' \
      'PrivateTmp=true' \
      'PrivateDevices=true' \
      'ProtectHome=true' \
      'ProtectSystem=full' \
      'CapabilityBoundingSet=' \
      '' \
      '[Install]' \
      'WantedBy=multi-user.target' > "$output"
  )
}

validate_legacy_bridge_unit_file() {
  local unit_path="$1"
  local application_version="$2"
  local interpreter_version="$3"
  local expected_sha256="$4"
  local require_current="${5:-1}"
  require_stable_version "$application_version"
  require_stable_version "$interpreter_version"
  require_sha256 "$expected_sha256"
  [[ "$require_current" == "0" || "$require_current" == "1" ]] || \
    die "legacy bridge current 校验模式无效。"
  validate_unit_file_safety "$unit_path"
  [[ $(sha256sum -- "$unit_path" | cut -d' ' -f1) == "${expected_sha256,,}" ]] || \
    die "legacy bridge unit SHA256 与 adoption 描述不一致。"
  [[ $(grep -Fxc -- "WorkingDirectory=$VIDEOINSIGHT_CURRENT" "$unit_path") -eq 1 ]] || \
    die "legacy bridge WorkingDirectory 未精确绑定 current application。"
  local expected_exec_start
  expected_exec_start="ExecStart=$VIDEOINSIGHT_RELEASES_ROOT/$interpreter_version/venv/bin/python -m uvicorn project.backend.app.control_plane:app --host 127.0.0.1 --port 18080 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1"
  [[ $(grep -Fxc -- "$expected_exec_start" "$unit_path") -eq 1 ]] || \
    die "legacy bridge ExecStart 未精确绑定 adoption interpreter。"
  if [[ "$require_current" == "1" ]]; then
    [[ $(readlink -f -- "$VIDEOINSIGHT_CURRENT") == \
        "$VIDEOINSIGHT_RELEASES_ROOT/$application_version/app" ]] || \
      die "legacy bridge current 未精确绑定 adoption application。"
  fi
}

validate_legacy_bridge_effective_config() {
  local application_version="$1"
  local interpreter_version="$2"
  local expected_sha256="$3"
  validate_legacy_bridge_unit_file "$VIDEOINSIGHT_UNIT_PATH" \
    "$application_version" "$interpreter_version" "$expected_sha256"
  local fragment drop_ins working_directory service_user service_group exec_start
  local effective_environment need_daemon_reload expected_environment
  local expected_exec_path expected_exec_start
  fragment=$(systemctl show --property=FragmentPath "$VIDEOINSIGHT_SERVICE" | sed -n 's/^FragmentPath=//p')
  drop_ins=$(systemctl show --property=DropInPaths "$VIDEOINSIGHT_SERVICE" | sed -n 's/^DropInPaths=//p')
  working_directory=$(systemctl show --property=WorkingDirectory "$VIDEOINSIGHT_SERVICE" | sed -n 's/^WorkingDirectory=//p')
  service_user=$(systemctl show --property=User "$VIDEOINSIGHT_SERVICE" | sed -n 's/^User=//p')
  service_group=$(systemctl show --property=Group "$VIDEOINSIGHT_SERVICE" | sed -n 's/^Group=//p')
  exec_start=$(systemctl show --property=ExecStart "$VIDEOINSIGHT_SERVICE" | sed -n 's/^ExecStart=//p')
  effective_environment=$(systemctl show --property=Environment "$VIDEOINSIGHT_SERVICE" | sed -n 's/^Environment=//p')
  need_daemon_reload=$(systemctl show --property=NeedDaemonReload "$VIDEOINSIGHT_SERVICE" | sed -n 's/^NeedDaemonReload=//p')
  expected_exec_path="$VIDEOINSIGHT_RELEASES_ROOT/$interpreter_version/venv/bin/python"
  expected_exec_start="$expected_exec_path -m uvicorn project.backend.app.control_plane:app --host 127.0.0.1 --port 18080 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1"
  expected_environment="PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=$VIDEOINSIGHT_SERVICE_PATH VIDEOINSIGHT_RUNTIME_ROOT=$VIDEOINSIGHT_RUNTIME_ROOT VIDEOINSIGHT_BACKUP_ROOT=$VIDEOINSIGHT_BACKUP_ROOT AUTH_SESSION_DATABASE_PATH=$VIDEOINSIGHT_RUNTIME_ROOT/data/video_intelligence.db"
  [[ "$fragment" == "$VIDEOINSIGHT_UNIT_PATH" ]] || die "systemd 实际加载了其他 unit。"
  [[ -z "$drop_ins" ]] || die "legacy bridge 存在未审计的 systemd drop-in。"
  [[ "$need_daemon_reload" == "no" ]] || \
    die "legacy bridge 的磁盘 unit 与 systemd 已加载配置不一致。"
  [[ "$working_directory" == "$VIDEOINSIGHT_CURRENT" ]] || \
    die "systemd 实际 legacy bridge WorkingDirectory 不正确。"
  [[ "$service_user" == "$VIDEOINSIGHT_SERVICE_USER" && \
      "$service_group" == "$VIDEOINSIGHT_SERVICE_GROUP" ]] || \
    die "systemd 实际 legacy bridge 服务身份不正确。"
  [[ "$effective_environment" == "$expected_environment" ]] || \
    die "systemd 实际 legacy bridge Environment 与固定安全配置不一致。"
  validate_effective_exec_start_record "$exec_start" "$expected_exec_path" \
    "$expected_exec_start"
}

validate_legacy_adoption_record() {
  local mode="${1:-record}"
  [[ "$mode" == "record" || "$mode" == "active" || "$mode" == "prepared" ]] || \
    die "legacy adoption 校验模式无效。"
  validate_root_file "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR"
  local command="validate-record"
  [[ "$mode" != "active" ]] || command="validate-active"
  [[ "$mode" != "prepared" ]] || command="validate-prepared"
  local output fields=()
  output=$(run_trusted_offline_python \
    "$(native_script_dir)/legacy_adoption_descriptor.py" "$command" \
    "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR" "$VIDEOINSIGHT_ROOT" \
    "$VIDEOINSIGHT_UNIT_PATH" "$VIDEOINSIGHT_OFFLINE_PYTHON" \
    "$SERVICE_UID" "$SERVICE_GID") || \
    die "legacy adoption 描述或绑定证据校验失败。"
  mapfile -t fields <<< "$output"
  [[ ${#fields[@]} -eq 11 ]] || die "legacy adoption 描述输出无效。"
  validate_root_directory "$VIDEOINSIGHT_ROOT/state/legacy-adoption"
  validate_root_file "$VIDEOINSIGHT_ROOT/state/legacy-adoption/${fields[3]}"
  printf '%s\n' "${fields[@]}"
}

validate_active_legacy_adoption() {
  local output
  output=$(validate_legacy_adoption_record active) || \
    die "legacy 布局缺少完整且 active 的 adoption 描述。"
  local fields=()
  mapfile -t fields <<< "$output"
  [[ ${#fields[@]} -eq 11 ]] || die "legacy adoption 描述输出无效。"
  validate_legacy_bridge_effective_config "${fields[0]}" "${fields[1]}" "${fields[5]}"
  printf '%s\n' "${fields[@]}"
}

control_plane_domain() {
  local domain
  domain=$(sed -n 's/^CONTROL_PLANE_DOMAIN=//p' "$VIDEOINSIGHT_ENV_FILE" | tail -n 1 | tr -d '\r')
  [[ "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$ ]] || \
    die "控制层域名配置无效。"
  printf '%s\n' "$domain"
}

health_check() {
  local expected_version="${1:-}"
  local attempt ready_payload health_payload domain
  domain=$(control_plane_domain)
  for attempt in 1 2; do
    if (( attempt == 2 )); then
      sleep 2
    fi
    ready_payload=$(curl --disable --noproxy '*' --fail --silent --show-error --max-time 5 --header "Host: $domain" \
      "$VIDEOINSIGHT_LOCAL_URL/ready") || continue
    health_payload=$(curl --disable --noproxy '*' --fail --silent --show-error --max-time 5 --header "Host: $domain" \
      "$VIDEOINSIGHT_LOCAL_URL/health") || continue
    if printf '%s\n%s' "$ready_payload" "$health_payload" | \
      run_trusted_offline_python -c '
import json
import sys

expected = sys.argv[1]
ready = json.loads(sys.stdin.readline())
health = json.loads(sys.stdin.readline())
valid = ready.get("status") == "ready" and health.get("status") == "ok"
if expected:
    valid = valid and health.get("release_version") == expected
raise SystemExit(0 if valid else 1)
' "$expected_version"; then
      return 0
    fi
  done
  return 1
}

atomic_switch_current() {
  local release_path="$1"
  real_path_under "$release_path" "$VIDEOINSIGHT_RELEASES_ROOT" || \
    die "拒绝切换到 releases 之外。"
  [[ -d "$release_path" ]] || die "目标发布目录不存在。"
  local temporary_link="$VIDEOINSIGHT_ROOT/.current-link-$$"
  [[ ! -e "$temporary_link" && ! -L "$temporary_link" ]] || die "临时 current 链接已存在。"
  ln -s -- "releases/$(basename -- "$release_path")" "$temporary_link"
  durable_rename "$temporary_link" "$VIDEOINSIGHT_CURRENT" "$VIDEOINSIGHT_ROOT"
}

atomic_restore_current_target() {
  local original_link_target="$1"
  local resolved
  if [[ "$original_link_target" == /* ]]; then
    resolved=$(readlink -f -- "$original_link_target") || die "旧 current 目标无法解析。"
  else
    resolved=$(readlink -f -- "$VIDEOINSIGHT_ROOT/$original_link_target") || \
      die "旧 current 目标无法解析。"
  fi
  real_path_under "$resolved" "$VIDEOINSIGHT_RELEASES_ROOT" || \
    die "拒绝恢复到 releases 之外。"
  local temporary_link="$VIDEOINSIGHT_ROOT/.current-restore-$$"
  [[ ! -e "$temporary_link" && ! -L "$temporary_link" ]] || die "临时恢复链接已存在。"
  ln -s -- "$original_link_target" "$temporary_link"
  durable_rename "$temporary_link" "$VIDEOINSIGHT_CURRENT" "$VIDEOINSIGHT_ROOT"
}
