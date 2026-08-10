#!/usr/bin/env sh
set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

./validate_env.sh

mkdir -p data backups caddy-data caddy-config updates
bootstrap_avatar_manifest="$SCRIPT_DIR/bootstrap/avatar_assets/shuying_cloud.json"
runtime_avatar_manifest="$SCRIPT_DIR/data/avatar_assets/shuying_cloud.json"
if [ -f "$bootstrap_avatar_manifest" ] && [ ! -f "$runtime_avatar_manifest" ]; then
  mkdir -p "$(dirname "$runtime_avatar_manifest")"
  cp "$bootstrap_avatar_manifest" "$runtime_avatar_manifest"
  echo "已导入现有共享数字人资产；未提交训练任务，也未产生供应商费用。"
fi
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose exec -T control-plane python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=5)"
CONTROL_PLANE_DOMAIN=$(sed -n 's/^CONTROL_PLANE_DOMAIN=//p' .env | tail -n 1 | tr -d '\r')
echo "控制层已启动；DNS 生效后访问 https://$CONTROL_PLANE_DOMAIN/health"
