#!/usr/bin/env sh
set -eu

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "用法：./restore.sh backups/videoinsight-control-plane-YYYYMMDD-HHMMSS.zip"
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

BACKUP_PATH=$(CDPATH= cd -- "$(dirname -- "$1")" && pwd)/$(basename -- "$1")
BACKUPS_ROOT=$(CDPATH= cd -- backups && pwd)
case "$BACKUP_PATH" in
  "$BACKUPS_ROOT"/*) ;;
  *)
    echo "只允许恢复 deploy/control-plane/backups 目录内的备份。"
    exit 1
    ;;
esac
BACKUP_NAME=$(basename -- "$BACKUP_PATH")

if docker compose ps --status running --services control-plane | grep -Fx control-plane >/dev/null; then
  STAMP=$(date +%Y%m%d-%H%M%S)
  ROLLBACK_TARGET="videoinsight-before-restore-$STAMP.zip"
  docker compose exec -T control-plane \
    python /app/deploy/backup_control_plane.py "$ROLLBACK_TARGET"
  chmod 600 "backups/$ROLLBACK_TARGET"
  echo "已先保存当前状态：deploy/control-plane/backups/$ROLLBACK_TARGET"
fi

docker compose stop control-plane
if ! docker compose run --rm --no-deps --user 0:0 \
  --entrypoint python control-plane \
  /app/deploy/restore_control_plane.py "$BACKUP_NAME"
then
  echo "恢复校验失败，控制层仍保持停止；请检查备份文件，必要时使用恢复前快照。"
  exit 1
fi
docker compose start control-plane
CONTROL_PLANE_DOMAIN=$(sed -n 's/^CONTROL_PLANE_DOMAIN=//p' .env | tail -n 1 | tr -d '\r')
echo "恢复完成，请检查 https://$CONTROL_PLANE_DOMAIN/ready"
