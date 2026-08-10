#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

./validate_env.sh
CONTROL_PLANE_DOMAIN=$(sed -n 's/^CONTROL_PLANE_DOMAIN=//p' .env | tail -n 1 | tr -d '\r')
docker compose exec -T control-plane \
  python /app/deploy/verify_control_plane.py \
  --base-url "https://$CONTROL_PLANE_DOMAIN"
