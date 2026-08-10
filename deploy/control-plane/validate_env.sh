#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE="${CONTROL_PLANE_ENV_FILE:-$SCRIPT_DIR/.env}"

if [ ! -f "$ENV_FILE" ]; then
  echo "缺少 $ENV_FILE，请先复制 .env.example。"
  exit 1
fi

value() {
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r'
}

require_value() {
  key="$1"
  if [ -z "$(value "$key")" ]; then
    echo "配置缺少 $key。"
    exit 1
  fi
}

domain=$(value CONTROL_PLANE_DOMAIN)
case "$domain" in
  ""|*://*|*/*|*:*|*" "*)
    echo "CONTROL_PLANE_DOMAIN 必须只填写域名，不能带协议、端口或路径。"
    exit 1
    ;;
esac
if ! printf '%s\n' "$domain" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$'; then
  echo "CONTROL_PLANE_DOMAIN 不是有效的公网域名。"
  exit 1
fi
domain_lower=$(printf '%s' "$domain" | tr '[:upper:]' '[:lower:]')
case "$domain_lower" in
  localhost|testserver|example.com|example.net|example.org|\
  *.localhost|*.test|*.invalid|*.example|*.example.com|*.example.net|*.example.org)
    echo "CONTROL_PLANE_DOMAIN 不能使用本机、测试或示例域名。"
    exit 1
    ;;
esac

allowed_hosts=$(value CONTROL_PLANE_ALLOWED_HOSTS)
if ! printf '%s\n' "$allowed_hosts" | tr ',' '\n' | grep -Fx "$domain" >/dev/null; then
  echo "CONTROL_PLANE_ALLOWED_HOSTS 必须包含 CONTROL_PLANE_DOMAIN。"
  exit 1
fi
old_ifs=$IFS
IFS=,
for allowed_host in $allowed_hosts; do
  allowed_host_lower=$(printf '%s' "$allowed_host" | tr '[:upper:]' '[:lower:]')
  case "$allowed_host_lower" in
    ""|"*"|*"*"*|localhost|testserver|example.com|example.net|example.org|\
    *.localhost|*.test|*.invalid|*.example|*.example.com|*.example.net|*.example.org)
      IFS=$old_ifs
      echo "CONTROL_PLANE_ALLOWED_HOSTS 不能包含通配符、本机、测试或示例域名。"
      exit 1
      ;;
  esac
done
IFS=$old_ifs

if [ "$(value APP_ENV)" != "production" ] || [ "$(value ENABLE_DOCS)" != "false" ]; then
  echo "正式控制层必须设置 APP_ENV=production 且 ENABLE_DOCS=false。"
  exit 1
fi
if [ "$(value AUTH_SESSION_STORE)" != "sqlite" ]; then
  echo "正式控制层必须设置 AUTH_SESSION_STORE=sqlite。"
  exit 1
fi

password=$(value ADMIN_PASSWORD)
if [ "${#password}" -lt 16 ] || [ "$password" = "CHANGE_ME_AT_LEAST_16_CHARACTERS" ]; then
  echo "ADMIN_PASSWORD 必须换成至少 16 位的随机密码。"
  exit 1
fi

copy_mode=$(value COPYWRITING_MODE)
case "$copy_mode" in
  sandbox|production) ;;
  *) echo "COPYWRITING_MODE 仅支持 sandbox 或 production。"; exit 1 ;;
esac
if [ "$copy_mode" = "production" ]; then
  require_value COPYWRITING_API_KEY
  require_value COPYWRITING_ESTIMATED_REQUEST_COST_CNY
  case "$(value COPYWRITING_BASE_URL)" in
    https://*) ;;
    *) echo "正式 AI 文案地址必须使用 HTTPS。"; exit 1 ;;
  esac
fi

asr_mode=$(value ASR_MODE)
case "$asr_mode" in
  sandbox) ;;
  cloud)
    for key in \
      DASHSCOPE_API_KEY ALIYUN_MODEL_STUDIO_WORKSPACE_ID ALIYUN_OSS_BUCKET \
      ALIBABA_CLOUD_ACCESS_KEY_ID ALIBABA_CLOUD_ACCESS_KEY_SECRET
    do
      require_value "$key"
    done
    ;;
  *) echo "服务器 ASR_MODE 仅支持 sandbox 或 cloud。"; exit 1 ;;
esac

editor_mode=$(value VIDEO_EDITOR_PROVIDER_MODE)
case "$editor_mode" in
  sandbox|aliyun) ;;
  *) echo "VIDEO_EDITOR_PROVIDER_MODE 仅支持 sandbox 或 aliyun。"; exit 1 ;;
esac
if [ "$editor_mode" = "aliyun" ]; then
  for key in \
    DASHSCOPE_API_KEY ALIYUN_MODEL_STUDIO_WORKSPACE_ID ALIYUN_OSS_BUCKET \
    ALIBABA_CLOUD_ACCESS_KEY_ID ALIBABA_CLOUD_ACCESS_KEY_SECRET \
    ALIYUN_MPS_PIPELINE_ID ALIYUN_MPS_TEMPLATE_ID_720P \
    ALIYUN_MPS_TEMPLATE_ID_1080P
  do
    require_value "$key"
  done
fi

avatar_mode=$(value AVATAR_PROVIDER_MODE)
case "$avatar_mode" in
  sandbox|shuying_cloud) ;;
  *) echo "AVATAR_PROVIDER_MODE 仅支持 sandbox 或 shuying_cloud。"; exit 1 ;;
esac
if [ "$avatar_mode" = "shuying_cloud" ]; then
  for key in \
    SHUYING_AVATAR_BASE_URL SHUYING_AVATAR_API_CODE \
    SHUYING_AVATAR_RESULT_ALLOWED_HOSTS SHUYING_AVATAR_ESTIMATED_SECONDS
  do
    require_value "$key"
  done
  bootstrap_avatar_manifest="$SCRIPT_DIR/bootstrap/avatar_assets/shuying_cloud.json"
  runtime_avatar_manifest="$SCRIPT_DIR/data/avatar_assets/shuying_cloud.json"
  if [ ! -f "$bootstrap_avatar_manifest" ] && [ ! -f "$runtime_avatar_manifest" ]; then
    avatars_json=$(value SHUYING_AVATAR_AVATARS_JSON)
    voices_json=$(value SHUYING_AVATAR_VOICES_JSON)
    if [ -z "$avatars_json" ] || [ "$avatars_json" = "[]" ] || \
       [ -z "$voices_json" ] || [ "$voices_json" = "[]" ]; then
      echo "数影云必须配置已有形象和声音，或使用部署包内的共享资产清单。"
      exit 1
    fi
  fi
  if [ "$(value SHUYING_AVATAR_ENABLED)" != "true" ]; then
    echo "AVATAR_PROVIDER_MODE=shuying_cloud 时必须设置 SHUYING_AVATAR_ENABLED=true。"
    exit 1
  fi
  case "$(value SHUYING_AVATAR_BASE_URL)" in
    https://*) ;;
    *) echo "正式数字人网关必须使用 HTTPS。"; exit 1 ;;
  esac
fi

if [ "$(value CRAWLER_PROVIDER_MODE)" != "sandbox" ]; then
  echo "正式桌面版爬虫在客户电脑本地运行；服务器 CRAWLER_PROVIDER_MODE 必须保持 sandbox。"
  exit 1
fi

echo "配置结构检查通过；未调用任何真实供应商。"
