#!/usr/bin/env bash
set -Eeuo pipefail

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
readonly VIDEOINSIGHT_WHEELHOUSE="$VIDEOINSIGHT_ROOT/wheelhouse"
readonly VIDEOINSIGHT_SERVICE_PATH="/usr/local/bin:/usr/bin:/bin"

native_script_dir() {
  CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd
}

readonly VIDEOINSIGHT_WHEELHOUSE_MANIFEST="$(native_script_dir)/wheelhouse.sha256"

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

validate_trusted_executable_path() {
  local candidate="$1"
  local resolved mode owner group current
  [[ "$candidate" == /* ]] || die "受信任工具路径必须是绝对路径：$candidate"
  resolved=$(readlink -f -- "$candidate") || die "受信任工具路径无法解析：$candidate"
  [[ "$resolved" == /* && -f "$resolved" && ! -L "$resolved" && -x "$resolved" ]] || \
    die "受信任工具不是可执行普通文件：$candidate"
  read -r mode owner group < <(stat -c '%a %u %g' -- "$resolved")
  [[ "$owner" == "0" && "$group" == "0" ]] || \
    die "受信任工具必须属于 root:root：$resolved"
  (( (8#$mode & 0022) == 0 )) || \
    die "受信任工具不能由组或其他用户写入：$resolved"
  (( (8#$mode & 0001) != 0 )) || \
    die "受信任工具必须允许固定服务用户执行：$resolved"

  current=$(dirname -- "$resolved")
  while :; do
    [[ -d "$current" && ! -L "$current" ]] || \
      die "受信任工具祖先不是普通目录：$current"
    read -r mode owner group < <(stat -c '%a %u %g' -- "$current")
    [[ "$owner" == "0" && "$group" == "0" ]] || \
      die "受信任工具祖先必须属于 root:root：$current"
    (( (8#$mode & 0022) == 0 )) || \
      die "受信任工具祖先不能由组或其他用户写入：$current"
    (( (8#$mode & 0001) != 0 )) || \
      die "固定服务用户无法遍历受信任工具祖先：$current"
    [[ "$current" == "/" ]] && break
    current=$(dirname -- "$current")
  done
  printf '%s\n' "$resolved"
}

validate_trusted_media_tool() {
  local name="$1"
  case "$name" in
    ffmpeg|ffprobe) ;;
    *) die "未允许的媒体校验工具名：$name" ;;
  esac
  local candidate
  candidate=$(PATH="$VIDEOINSIGHT_SERVICE_PATH" command -v "$name") || \
    die "固定服务 PATH 缺少受信任媒体校验工具：$name"
  [[ "$candidate" == /* ]] || \
    die "固定服务 PATH 将媒体工具解析为非文件命令：$name"
  validate_trusted_executable_path "$candidate"
}

fsync_path() {
  local path="$1"
  [[ -e "$path" && ! -L "$path" ]] || die "无法落盘不存在或为符号链接的路径：$path"
  PYTHONDONTWRITEBYTECODE=1 "$VIDEOINSIGHT_OFFLINE_PYTHON" - "$path" <<'PY'
import os
import sys

path = sys.argv[1]
flags = os.O_RDONLY
if os.path.isdir(path):
    flags |= getattr(os, "O_DIRECTORY", 0)
descriptor = os.open(path, flags)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

fsync_tree() {
  local root="$1"
  [[ -d "$root" && ! -L "$root" ]] || die "无法落盘非普通目录树：$root"
  PYTHONDONTWRITEBYTECODE=1 "$VIDEOINSIGHT_OFFLINE_PYTHON" - "$root" <<'PY'
import os
import stat
import sys

root = os.path.realpath(sys.argv[1])


def fsync_entry(path: str, *, directory: bool) -> None:
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode):
        resolved = os.path.realpath(path)
        if os.path.commonpath((root, resolved)) != root:
            raise OSError(f"release tree symlink escapes its root: {path}")
        return
    expected = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if not expected:
        raise OSError(f"release tree contains a non-regular entry: {path}")
    flags = os.O_RDONLY | (getattr(os, "O_DIRECTORY", 0) if directory else 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


for current, directories, files in os.walk(root, topdown=False, followlinks=False):
    for filename in files:
        fsync_entry(os.path.join(current, filename), directory=False)
    for dirname in directories:
        fsync_entry(os.path.join(current, dirname), directory=True)
    fsync_entry(current, directory=True)
PY
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
  validate_root_directory "$VIDEOINSIGHT_ROOT"
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

open_native_release_lock() {
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
  fragment=$(systemctl show --property=FragmentPath "$VIDEOINSIGHT_SERVICE" | sed -n 's/^FragmentPath=//p')
  drop_ins=$(systemctl show --property=DropInPaths "$VIDEOINSIGHT_SERVICE" | sed -n 's/^DropInPaths=//p')
  working_directory=$(systemctl show --property=WorkingDirectory "$VIDEOINSIGHT_SERVICE" | sed -n 's/^WorkingDirectory=//p')
  service_user=$(systemctl show --property=User "$VIDEOINSIGHT_SERVICE" | sed -n 's/^User=//p')
  service_group=$(systemctl show --property=Group "$VIDEOINSIGHT_SERVICE" | sed -n 's/^Group=//p')
  exec_start=$(systemctl show --property=ExecStart "$VIDEOINSIGHT_SERVICE" | sed -n 's/^ExecStart=//p')
  configured_exec_start=$(sed -n 's/^ExecStart=//p' "$VIDEOINSIGHT_UNIT_PATH")

  [[ "$fragment" == "$VIDEOINSIGHT_UNIT_PATH" ]] || die "systemd 实际加载了其他 unit。"
  [[ -z "$drop_ins" ]] || die "存在未审计的 systemd drop-in，已停止发布。"
  [[ "$working_directory" == "$VIDEOINSIGHT_CURRENT/app" ]] || die "WorkingDirectory 未指向 current/app。"
  [[ "$service_user" == "$VIDEOINSIGHT_SERVICE_USER" ]] || die "systemd User 不正确。"
  [[ "$service_group" == "$VIDEOINSIGHT_SERVICE_GROUP" ]] || die "systemd Group 不正确。"
  [[ "$exec_start" == *"$configured_exec_start"* ]] || \
    die "systemd 实际 ExecStart 与受控 unit 不一致。"
}

validate_existing_unit_scope() {
  [[ -f "$VIDEOINSIGHT_UNIT_PATH" && ! -L "$VIDEOINSIGHT_UNIT_PATH" ]] || \
    die "控制层 unit 不在唯一允许路径。"
  validate_unit_file_safety "$VIDEOINSIGHT_UNIT_PATH" 0
  local fragment drop_ins working_directory service_user service_group exec_start configured_exec_start
  fragment=$(systemctl show --property=FragmentPath "$VIDEOINSIGHT_SERVICE" | sed -n 's/^FragmentPath=//p')
  drop_ins=$(systemctl show --property=DropInPaths "$VIDEOINSIGHT_SERVICE" | sed -n 's/^DropInPaths=//p')
  working_directory=$(systemctl show --property=WorkingDirectory "$VIDEOINSIGHT_SERVICE" | sed -n 's/^WorkingDirectory=//p')
  service_user=$(systemctl show --property=User "$VIDEOINSIGHT_SERVICE" | sed -n 's/^User=//p')
  service_group=$(systemctl show --property=Group "$VIDEOINSIGHT_SERVICE" | sed -n 's/^Group=//p')
  exec_start=$(systemctl show --property=ExecStart "$VIDEOINSIGHT_SERVICE" | sed -n 's/^ExecStart=//p')
  configured_exec_start=$(sed -n 's/^ExecStart=//p' "$VIDEOINSIGHT_UNIT_PATH")

  [[ "$fragment" == "$VIDEOINSIGHT_UNIT_PATH" ]] || die "systemd 实际加载了其他 unit。"
  [[ -z "$drop_ins" ]] || die "存在未审计的 systemd drop-in。"
  [[ "$working_directory" == "$VIDEOINSIGHT_ROOT"/* ]] || die "现有 WorkingDirectory 越过控制层根目录。"
  [[ "$service_user" == "$VIDEOINSIGHT_SERVICE_USER" ]] || die "现有 systemd User 不正确。"
  [[ "$service_group" == "$VIDEOINSIGHT_SERVICE_GROUP" ]] || die "现有 systemd Group 不正确。"
  [[ "$exec_start" == *"$configured_exec_start"* ]] || \
    die "现有 systemd 实际 ExecStart 与受控 unit 不一致。"
  grep -Fx "EnvironmentFile=$VIDEOINSIGHT_ENV_FILE" "$VIDEOINSIGHT_UNIT_PATH" >/dev/null || \
    die "现有 unit 未使用固定配置文件。"
}

validate_legacy_unit_file_binding() {
  local unit_path="$1"
  local expected_release_root="$2"
  [[ "$expected_release_root" =~ ^/opt/videoinsight-control-plane/releases/[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
    die "legacy 预期版本根目录格式无效。"
  validate_unit_file_directives "$unit_path" 0

  local configured_working_directory expected_exec_start
  configured_working_directory=$(sed -n 's/^WorkingDirectory=//p' "$unit_path")
  [[ "$configured_working_directory" == "$VIDEOINSIGHT_CURRENT" || \
      "$configured_working_directory" == "$expected_release_root/app" ]] || \
    die "legacy unit WorkingDirectory 未绑定 current 解析出的旧版本。"
  expected_exec_start="ExecStart=$expected_release_root/venv/bin/python -m uvicorn project.backend.app.control_plane:app --host 127.0.0.1 --port 18080 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1"
  [[ $(grep -Fxc -- "$expected_exec_start" "$unit_path") -eq 1 ]] || \
    die "legacy unit ExecStart 解释器或应用入口未绑定 current 解析出的旧版本。"
}

validate_legacy_unit_binding() {
  local expected_release_root="$1"
  release_app_path "$expected_release_root" >/dev/null
  validate_release_python "$expected_release_root"
  validate_existing_unit_scope
  validate_legacy_unit_file_binding "$VIDEOINSIGHT_UNIT_PATH" "$expected_release_root"

  local working_directory exec_start expected_exec_start
  working_directory=$(systemctl show --property=WorkingDirectory "$VIDEOINSIGHT_SERVICE" | sed -n 's/^WorkingDirectory=//p')
  exec_start=$(systemctl show --property=ExecStart "$VIDEOINSIGHT_SERVICE" | sed -n 's/^ExecStart=//p')
  expected_exec_start="$expected_release_root/venv/bin/python -m uvicorn project.backend.app.control_plane:app --host 127.0.0.1 --port 18080 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1"
  [[ "$working_directory" == "$VIDEOINSIGHT_CURRENT" || \
      "$working_directory" == "$expected_release_root/app" ]] || \
    die "systemd 实际 legacy WorkingDirectory 与 current 旧版本不一致。"
  [[ "$exec_start" == *"$expected_exec_start"* ]] || \
    die "systemd 实际 legacy ExecStart 与 current 旧版本不一致。"
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
      PYTHONDONTWRITEBYTECODE=1 "$VIDEOINSIGHT_OFFLINE_PYTHON" -c '
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
