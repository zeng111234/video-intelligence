#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

printf "该操作只确认以后允许按上限使用云端转写，不会立即提交素材或扣费。\n"
printf "如确认，请输入 AUTHORIZE："
IFS= read -r confirmation
if [ "$confirmation" != "AUTHORIZE" ]; then
  echo "已取消。"
  exit 1
fi

printf "单条转写费用上限（元，直接回车默认 0.20）："
IFS= read -r cap
cap=${cap:-0.20}

printf "管理员密码："
stty -echo
trap 'stty echo' EXIT INT TERM
IFS= read -r password
stty echo
trap - EXIT INT TERM
printf "\n"

printf '%s\n%s\n' "$password" "$cap" | \
  docker compose exec -T control-plane python /app/deploy/authorize_asr.py
