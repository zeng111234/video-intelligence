#!/bin/bash
set -euo pipefail

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

readonly CONTROL_ROOT=/opt/videoinsight-control-plane
readonly CONTROL_INCOMING="$CONTROL_ROOT/incoming"
readonly NATIVE_TOOLS="$CONTROL_ROOT/tools/native-systemd"
readonly UPDATE_ROOT=/www/wwwroot/xmt.syszr.cn/desktop-updates
readonly STAGING_ROOT=/home/devuser/.videoinsight-release-staging
readonly SERVICE_NAME=videoinsight-control-plane.service

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

require_root() {
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die '发布入口必须通过受限 sudo 规则以 root 执行。'
}

validate_version() {
  [[ $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die '版本号格式无效。'
}

validate_sha256() {
  [[ $1 =~ ^[0-9a-f]{64}$ ]] || die 'SHA256 格式无效。'
}

validate_staged_file() {
  local candidate=$1
  local expected_name=$2
  local expected_sha=$3
  local resolved owner mode actual_sha

  [[ -f $candidate && ! -L $candidate ]] || die '暂存制品必须是普通文件。'
  [[ $(basename -- "$candidate") == "$expected_name" ]] || die '暂存制品文件名不匹配。'
  resolved=$(readlink -f -- "$candidate") || die '无法解析暂存制品路径。'
  [[ $resolved == "$STAGING_ROOT/"* ]] || die '暂存制品不在固定上传目录内。'
  owner=$(stat -c '%U' -- "$resolved")
  [[ $owner == devuser ]] || die '暂存制品必须属于 devuser。'
  mode=$(stat -c '%a' -- "$resolved")
  (( (8#$mode & 8#022) == 0 )) || die '暂存制品不能由组或其他用户写入。'
  actual_sha=$(sha256sum -- "$resolved" | cut -d' ' -f1)
  [[ $actual_sha == "$expected_sha" ]] || die '暂存制品 SHA256 不匹配。'
  printf '%s\n' "$resolved"
}

install_immutable_file() {
  local source=$1
  local destination=$2
  local expected_sha=$3
  local temporary="${destination}.partial.$$"
  local actual_sha

  if [[ -e $destination || -L $destination ]]; then
    [[ -f $destination && ! -L $destination ]] || die '目标路径已被非普通文件占用。'
    actual_sha=$(sha256sum -- "$destination" | cut -d' ' -f1)
    [[ $actual_sha == "$expected_sha" ]] || die '目标文件已存在但哈希不同，禁止覆盖。'
    return
  fi
  [[ ! -e $temporary && ! -L $temporary ]] || die '临时目标已存在。'
  install -o root -g root -m 0644 -- "$source" "$temporary"
  actual_sha=$(sha256sum -- "$temporary" | cut -d' ' -f1)
  [[ $actual_sha == "$expected_sha" ]] || die '服务器临时副本 SHA256 不匹配。'
  mv -- "$temporary" "$destination"
}

probe() {
  require_root
  [[ -d $STAGING_ROOT && ! -L $STAGING_ROOT ]] || die '固定上传目录未安装。'
  [[ -d $CONTROL_ROOT && ! -L $CONTROL_ROOT ]] || die '控制层根目录不存在或不安全。'
  [[ -d $CONTROL_INCOMING && ! -L $CONTROL_INCOMING ]] || die '控制层 incoming 目录未安装。'
  [[ -d $UPDATE_ROOT && ! -L $UPDATE_ROOT ]] || die '桌面更新目录不存在或不安全。'
  printf 'VIDEOINSIGHT_RELEASE_GATEWAY_READY\n'
}

deploy_control_plane() {
  local version=$1
  local expected_sha=$2
  local staged_path=$3
  local expected_name source destination uid gid script

  validate_version "$version"
  validate_sha256 "$expected_sha"
  expected_name="VideoInsight-control-plane-$version.zip"
  source=$(validate_staged_file "$staged_path" "$expected_name" "$expected_sha")
  destination="$CONTROL_INCOMING/$expected_name"

  for script in common.sh install_unit.sh normalize_offline_python_runtime.sh \
    normalize_legacy_unit.sh preflight.sh rollback.sh upgrade.sh verify.sh; do
    [[ -f "$NATIVE_TOOLS/$script" && ! -L "$NATIVE_TOOLS/$script" ]] || \
      die "缺少受审发布脚本：$script"
    /bin/bash -n "$NATIVE_TOOLS/$script" || die "服务器 Bash 无法解析：$script"
  done

  install_immutable_file "$source" "$destination" "$expected_sha"
  uid=$(id -u videoinsight) || die '缺少 videoinsight 服务用户。'
  gid=$(id -g videoinsight) || die '缺少 videoinsight 服务组。'
  /bin/bash "$NATIVE_TOOLS/preflight.sh" "$uid" "$gid"
  /bin/bash "$NATIVE_TOOLS/upgrade.sh" "$version" "$expected_sha" "$uid" "$gid"
  /bin/bash "$NATIVE_TOOLS/verify.sh" "$version" "$uid" "$gid"
  rm -f -- "$source"
  printf 'CONTROL_PLANE_DEPLOYED version=%s sha256=%s\n' "$version" "$expected_sha"
}

validate_update_manifest() {
  local manifest=$1
  local version=$2
  local installer_name=$3
  local installer_sha=$4
  local installer_size=$5

  /usr/bin/python3 -I -S - "$manifest" "$version" "$installer_name" "$installer_sha" "$installer_size" <<'PY'
import json
import pathlib
import sys

path, version, installer, sha256, size = sys.argv[1:]
payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))
required = {"version", "installer", "sha256", "size_bytes", "notes", "published_at"}
if set(payload) != required:
    raise SystemExit("latest.json 字段集合无效")
if payload["version"] != version or payload["installer"] != installer:
    raise SystemExit("latest.json 版本或安装包名称不匹配")
if payload["sha256"] != sha256 or payload["size_bytes"] != int(size):
    raise SystemExit("latest.json 安装包证据不匹配")
if not isinstance(payload["notes"], str) or not payload["notes"].strip():
    raise SystemExit("latest.json 更新说明无效")
if not isinstance(payload["published_at"], str) or not payload["published_at"].strip():
    raise SystemExit("latest.json 发布时间无效")
PY
}

publish_desktop() {
  local version=$1
  local installer_sha=$2
  local manifest_sha=$3
  local staged_installer=$4
  local staged_manifest=$5
  local installer_name installer_source manifest_source installer_size
  local target_installer target_manifest temporary_manifest current_version='' current_sha

  validate_version "$version"
  validate_sha256 "$installer_sha"
  validate_sha256 "$manifest_sha"
  installer_name="VideoInsight-$version-Setup.exe"
  installer_source=$(validate_staged_file "$staged_installer" "$installer_name" "$installer_sha")
  manifest_source=$(validate_staged_file "$staged_manifest" 'latest.json' "$manifest_sha")
  installer_size=$(stat -c '%s' -- "$installer_source")
  (( installer_size > 0 && installer_size <= 1073741824 )) || die '安装包大小无效。'
  validate_update_manifest "$manifest_source" "$version" "$installer_name" "$installer_sha" "$installer_size"

  target_installer="$UPDATE_ROOT/$installer_name"
  target_manifest="$UPDATE_ROOT/latest.json"
  if [[ -f $target_manifest && ! -L $target_manifest ]]; then
    current_version=$(/usr/bin/python3 -I -S - "$target_manifest" <<'PY'
import json, pathlib, sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8-sig"))["version"])
PY
)
    validate_version "$current_version"
    /usr/bin/python3 -I -S - "$current_version" "$version" <<'PY'
import sys
old = tuple(map(int, sys.argv[1].split(".")))
new = tuple(map(int, sys.argv[2].split(".")))
if new < old:
    raise SystemExit("禁止把桌面更新回退到旧版本")
PY
    if [[ $current_version == "$version" ]]; then
      current_sha=$(sha256sum -- "$target_manifest" | cut -d' ' -f1)
      [[ $current_sha == "$manifest_sha" ]] || die '同版本 latest.json 已存在但内容不同。'
    fi
  fi

  install_immutable_file "$installer_source" "$target_installer" "$installer_sha"
  if [[ ! -f $target_manifest || $current_version != "$version" ]]; then
    temporary_manifest="$UPDATE_ROOT/.latest.$$.tmp"
    [[ ! -e $temporary_manifest && ! -L $temporary_manifest ]] || die 'latest.json 临时目标已存在。'
    install -o root -g root -m 0644 -- "$manifest_source" "$temporary_manifest"
    [[ $(sha256sum -- "$temporary_manifest" | cut -d' ' -f1) == "$manifest_sha" ]] || \
      die '服务器 latest.json 临时副本 SHA256 不匹配。'
    mv -f -- "$temporary_manifest" "$target_manifest"
  fi
  rm -f -- "$installer_source" "$manifest_source"
  printf 'DESKTOP_UPDATE_PUBLISHED version=%s sha256=%s\n' "$version" "$installer_sha"
}

require_root
case ${1:-} in
  probe)
    [[ $# -eq 1 ]] || die 'probe 不接受其他参数。'
    probe
    ;;
  deploy-control-plane)
    [[ $# -eq 4 ]] || die 'deploy-control-plane 参数数量错误。'
    deploy_control_plane "$2" "$3" "$4"
    ;;
  publish-desktop)
    [[ $# -eq 6 ]] || die 'publish-desktop 参数数量错误。'
    publish_desktop "$2" "$3" "$4" "$5" "$6"
    ;;
  *)
    die '只允许 probe、deploy-control-plane 或 publish-desktop。'
    ;;
esac
