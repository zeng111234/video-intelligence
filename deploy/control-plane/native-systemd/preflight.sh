#!/usr/bin/env bash
set -Eeuo pipefail

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

require_root
for command_name in cmp curl cut find getent grep id readlink sed sha256sum sleep sort \
  stat systemctl tail tr; do
  require_command "$command_name"
done
for fixed_command in \
  "$VIDEOINSIGHT_SYSTEM_ENV" \
  "$VIDEOINSIGHT_SYSTEM_TIMEOUT" \
  "$VIDEOINSIGHT_SYSTEM_RUNUSER"; do
  require_command "$fixed_command"
done
validate_service_identity "$SERVICE_UID" "$SERVICE_GID"
validate_trusted_execution_dependencies

validate_control_root
real_path_under "$SCRIPT_DIR" "$VIDEOINSIGHT_ROOT" || die "发布工具目录越过控制层根目录。"
validate_root_directory "$SCRIPT_DIR"
if find "$SCRIPT_DIR" -maxdepth 1 -type f \
  \( -perm -0022 -o ! -user root -o ! -group root \) \
  -print -quit | grep -q .; then
  die "发布工具包含可被非 root 修改的文件。"
fi
if find "$SCRIPT_DIR" \
  \( -type d -name '__pycache__' -o -type f \( -name '*.pyc' -o -name '*.pyo' \) \) \
  -print -quit | grep -q .; then
  die "发布工具包含 Python 缓存，请清理后再执行。"
fi
if grep -l $'\r' "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR/videoinsight-control-plane.service" \
  >/dev/null; then
  die "发布工具必须使用 LF 换行。"
fi
for path in \
  "$VIDEOINSIGHT_RELEASES_ROOT" \
  "$VIDEOINSIGHT_RUNTIME_ROOT" \
  "$VIDEOINSIGHT_BACKUP_ROOT" \
  "$VIDEOINSIGHT_WHEELHOUSE" \
  "$VIDEOINSIGHT_ROOT/config" \
  "$VIDEOINSIGHT_ROOT/python" \
  "$VIDEOINSIGHT_ROOT/tools" \
  "$VIDEOINSIGHT_ROOT/tools/media" \
  "$VIDEOINSIGHT_MEDIA_ROOT"; do
  [[ -d "$path" && ! -L "$path" ]] || die "目录不存在或是符号链接：$path"
  real_path_under "$path" "$VIDEOINSIGHT_ROOT" || die "目录越过控制层根目录：$path"
done
for root_owned_path in \
  "$VIDEOINSIGHT_RELEASES_ROOT" \
  "$VIDEOINSIGHT_RUNTIME_ROOT" \
  "$VIDEOINSIGHT_WHEELHOUSE" \
  "$VIDEOINSIGHT_ROOT/config" \
  "$VIDEOINSIGHT_ROOT/python" \
  "$VIDEOINSIGHT_ROOT/tools" \
  "$VIDEOINSIGHT_ROOT/tools/media" \
  "$VIDEOINSIGHT_MEDIA_ROOT"; do
  validate_root_directory "$root_owned_path"
done
readonly TRUSTED_FFPROBE="$(validate_trusted_media_tool ffprobe)"
readonly TRUSTED_FFMPEG="$(validate_trusted_media_tool ffmpeg)"
validate_trusted_media_tool_execution ffprobe "$TRUSTED_FFPROBE"
validate_trusted_media_tool_execution ffmpeg "$TRUSTED_FFMPEG"
validate_service_tree "$VIDEOINSIGHT_RUNTIME_ROOT/data" "$SERVICE_UID" "$SERVICE_GID"
validate_secure_directory "$VIDEOINSIGHT_BACKUP_ROOT" 0
read -r backup_mode backup_uid backup_gid < <(
  stat -c '%a %u %g' -- "$VIDEOINSIGHT_BACKUP_ROOT"
)
[[ "$backup_uid" == "0" && "$backup_gid" == "0" ]] || \
  die "备份目录必须属于 root:root。"
(( (8#$backup_mode & 0077) == 0 )) || \
  die "备份目录权限必须限制为仅 root 可访问。"

validate_secure_file "$VIDEOINSIGHT_ENV_FILE" 0
read -r env_mode env_uid env_gid < <(stat -c '%a %u %g' -- "$VIDEOINSIGHT_ENV_FILE")
[[ "$env_uid" == "0" && "$env_gid" == "$SERVICE_GID" ]] || \
  die "控制层配置必须属于 root:服务组。"
(( (8#$env_mode & 0040) != 0 )) || die "服务组无法读取控制层配置。"
(( (8#$env_mode & 0037) == 0 )) || die "控制层配置权限必须限制为 root 和只读服务组。"
if grep -Eq '^[[:space:]]*(PATH|VIDEOINSIGHT_MEDIA_ROOT|VIDEOINSIGHT_SERVICE_PATH)[[:space:]]*=' \
  "$VIDEOINSIGHT_ENV_FILE"; then
  die "控制层配置不得覆盖固定的 systemd 服务 PATH 或媒体工具目录。"
fi
validate_secure_file "$VIDEOINSIGHT_OFFLINE_PYTHON" 0
[[ -x "$VIDEOINSIGHT_OFFLINE_PYTHON" ]] || die "离线 Python 不可执行。"
PYTHONDONTWRITEBYTECODE=1 "$VIDEOINSIGHT_OFFLINE_PYTHON" -c \
  'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' || \
  die "离线 Python 必须是 3.12。"

[[ -n $(find "$VIDEOINSIGHT_WHEELHOUSE" -maxdepth 1 -type f -name '*.whl' -print -quit) ]] || \
  die "离线 wheelhouse 中没有 wheel。"
if find "$VIDEOINSIGHT_WHEELHOUSE" -mindepth 1 -maxdepth 1 ! -type f \
  -print -quit | grep -q .; then
  die "wheelhouse 只能包含清单列出的普通 wheel 文件。"
fi
if find "$VIDEOINSIGHT_WHEELHOUSE" -maxdepth 1 -type f \
  \( -perm -0022 -o ! -user root -o ! -group root \) \
  -print -quit | grep -q .; then
  die "wheelhouse 包含可被非 root 修改的文件。"
fi
validate_root_file "$VIDEOINSIGHT_WHEELHOUSE_MANIFEST"
if grep -Ev '^[0-9a-f]{64}  [A-Za-z0-9][A-Za-z0-9._+-]*\.whl$' \
  "$VIDEOINSIGHT_WHEELHOUSE_MANIFEST" | grep -q .; then
  die "wheelhouse SHA256 清单格式无效。"
fi
actual_wheel_files=$(find "$VIDEOINSIGHT_WHEELHOUSE" -maxdepth 1 -type f \
  -printf '%f\n' | LC_ALL=C sort)
expected_wheel_files=$(sed -n 's/^[0-9a-f]\{64\}  //p' \
  "$VIDEOINSIGHT_WHEELHOUSE_MANIFEST" | LC_ALL=C sort)
[[ -n "$expected_wheel_files" && "$actual_wheel_files" == "$expected_wheel_files" ]] || \
  die "wheelhouse 文件集合与受跟踪清单不一致。"
( CDPATH= cd -- "$VIDEOINSIGHT_WHEELHOUSE" && \
  sha256sum --check --status "$VIDEOINSIGHT_WHEELHOUSE_MANIFEST" ) || \
  die "wheelhouse 文件哈希与受跟踪清单不一致。"
if find "$VIDEOINSIGHT_ROOT/python" -xdev \( -type f -o -type d \) \
  \( -perm -0022 -o ! -user root \) -print -quit | grep -q .; then
  die "离线 Python 运行时包含可被非 root 修改的内容。"
fi
while IFS= read -r -d '' python_link; do
  real_path_under "$python_link" "$VIDEOINSIGHT_ROOT/python" || \
    die "离线 Python 包含越界符号链接。"
done < <(find "$VIDEOINSIGHT_ROOT/python" -xdev -type l -print0)

readonly CURRENT_RELEASE="$(current_release_root)"
release_app_path "$CURRENT_RELEASE" >/dev/null
validate_existing_unit_scope
systemctl is-active --quiet "$VIDEOINSIGHT_SERVICE" || die "控制层当前未运行。"

printf '受信任付费验收媒体工具与固定 SHA256：ffprobe=%s ffmpeg=%s\n' \
  "$TRUSTED_FFPROBE" "$TRUSTED_FFMPEG"
printf '原生 systemd 发布前置检查通过；未访问外网，未调用供应商。\n'
