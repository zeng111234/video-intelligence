#!/usr/bin/env sh
set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
mkdir -p backups

STAMP=$(date +%Y%m%d-%H%M%S)
TARGET="videoinsight-control-plane-$STAMP.zip"

docker compose exec -T control-plane \
  python /app/deploy/backup_control_plane.py "$TARGET"
chmod 600 "backups/$TARGET"
echo "备份完成：deploy/control-plane/backups/$TARGET"
