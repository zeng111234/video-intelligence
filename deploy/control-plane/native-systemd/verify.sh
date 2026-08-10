#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
  printf '用法：%s <预期版本> <服务 UID> <服务 GID>\n' "$0" >&2
  exit 2
}

[[ $# -eq 3 ]] || usage
readonly EXPECTED_VERSION="$1"
readonly SERVICE_UID="$2"
readonly SERVICE_GID="$3"

require_version "$EXPECTED_VERSION"
require_root
bash "$SCRIPT_DIR/preflight.sh" "$SERVICE_UID" "$SERVICE_GID"
validate_trusted_execution_dependencies
readonly TRUSTED_FFPROBE="$(validate_trusted_media_tool ffprobe)"
readonly TRUSTED_FFMPEG="$(validate_trusted_media_tool ffmpeg)"
validate_trusted_media_tool_execution ffprobe "$TRUSTED_FFPROBE"
validate_trusted_media_tool_execution ffmpeg "$TRUSTED_FFMPEG"
validate_unit_effective_config

readonly CURRENT_TARGET="$(current_target_path)"
readonly CURRENT_RELEASE="$(current_release_root)"
readonly CURRENT_APP="$(release_app_path "$CURRENT_RELEASE")"
[[ "$CURRENT_TARGET" == "$CURRENT_RELEASE" ]] || \
  die "current 仍是旧 app 子目录布局。"
[[ $(basename -- "$CURRENT_RELEASE") == "$EXPECTED_VERSION" ]] || \
  die "current 版本与预期版本不一致。"
validate_release_python "$CURRENT_RELEASE"
validate_release_version_file "$CURRENT_RELEASE" "$EXPECTED_VERSION"
validate_tools_match_release "$CURRENT_APP/deploy/control-plane/native-systemd"
validate_service_tree "$VIDEOINSIGHT_RUNTIME_ROOT/data" "$SERVICE_UID" "$SERVICE_GID"

CONTROL_PLANE_ENV_FILE="$VIDEOINSIGHT_ENV_FILE" \
  bash "$CURRENT_APP/deploy/control-plane/validate_env.sh"
systemctl is-active --quiet "$VIDEOINSIGHT_SERVICE" || die "控制层未运行。"
health_check "$EXPECTED_VERSION" || \
  die "本机健康检查失败（初次检查加一次重试）。"

readonly DATABASE_PATH="$VIDEOINSIGHT_RUNTIME_ROOT/data/video_intelligence.db"
[[ -f "$DATABASE_PATH" && ! -L "$DATABASE_PATH" ]] || die "运行数据库不存在或是符号链接。"
read -r database_mode database_uid database_gid < <(stat -c '%a %u %g' -- "$DATABASE_PATH")
[[ "$database_uid" == "$SERVICE_UID" && "$database_gid" == "$SERVICE_GID" ]] || \
  die "运行数据库 UID/GID 不正确。"
(( (8#$database_mode & 0077) == 0 )) || die "运行数据库向组或其他用户开放。"
PYTHONDONTWRITEBYTECODE=1 "$VIDEOINSIGHT_OFFLINE_PYTHON" - "$DATABASE_PATH" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=5)
try:
    result = connection.execute("PRAGMA quick_check").fetchone()
finally:
    connection.close()
raise SystemExit(0 if result == ("ok",) else 1)
PY

printf '原生 systemd 发布验收通过：%s\n' "$EXPECTED_VERSION"
printf '付费验收媒体门禁与固定 SHA256 可用：ffprobe=%s ffmpeg=%s\n' \
  "$TRUSTED_FFPROBE" "$TRUSTED_FFMPEG"
printf '服务仅监听本机 127.0.0.1:18080；健康检查使用配置域名 Host。\n'
printf '未访问外网、未调用供应商、未检查或修改其他服务。\n'
