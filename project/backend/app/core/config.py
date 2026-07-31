"""后端配置 —— 不依赖 streamlit secrets。"""

from __future__ import annotations

import os
import tomllib
from enum import StrEnum
from pathlib import Path

# config.py 在 project/backend/app/core/ 下，需要 5 层 parent 才能到仓库根
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
DATABASE_PATH = PROJECT_ROOT / "data" / "video_intelligence.db"
ENV_PATH = PROJECT_ROOT / ".env"
BACKEND_ENV_PATH = PROJECT_ROOT / "project" / "backend" / ".env"
STREAMLIT_SECRETS_PATH = PROJECT_ROOT / ".streamlit" / "secrets.toml"


def _parse_env_file(path: Path) -> dict[str, str]:
    """解析本机 .env 文件，不支持 shell 展开，也不打印密钥。"""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _load_env_files() -> None:
    """加载 Git 忽略的 .env 文件。

    优先级：系统环境变量 > 根目录 .env > project/backend/.env > legacy streamlit secrets。
    """
    for path in (ENV_PATH, BACKEND_ENV_PATH):
        for key, value in _parse_env_file(path).items():
            os.environ.setdefault(key, value)


_load_env_files()


# ---------------------------------------------------------------------------
# ASR 配置
# ---------------------------------------------------------------------------

class ASRMode(StrEnum):
    """ASR 运行模式。"""
    SANDBOX = "sandbox"       # 演示模式，返回模拟数据
    LOCAL = "local"           # 本地 faster-whisper 模型
    CLOUD = "cloud"           # 云端 ASR 供应商


class ASRCloudProvider(StrEnum):
    """云端 ASR 供应商类型。"""
    ALIYUN = "aliyun"
    TENCENT = "tencent"


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _legacy_streamlit_secret(key: str) -> str:
    """读取旧 8501 Streamlit secrets，供 1001/2001 迁移期复用凭证。

    环境变量仍然优先；这里仅作为兼容来源，避免把旧系统里已配置的
    OneAPI Key 丢失。调用方不得打印密钥值。
    """
    if not STREAMLIT_SECRETS_PATH.exists():
        return ""
    try:
        value = tomllib.loads(STREAMLIT_SECRETS_PATH.read_text(encoding="utf-8")).get(
            key,
            "",
        )
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    return str(value).strip() if value is not None else ""


def _secret(key: str, default: str = "") -> str:
    return _env(key) or _legacy_streamlit_secret(key) or default


# ---------------------------------------------------------------------------
# 云端智能剪辑配置
# ---------------------------------------------------------------------------


class VideoEditorProviderMode(StrEnum):
    """新剪辑工作台的供应商模式。

    该模式与全局 ASR_MODE 完全分离，防止云端剪辑在缺少配置时静默回退
    到本地 Whisper，或把旧剪辑器的本地能力误报为云端已就绪。
    """

    SANDBOX = "sandbox"
    ALIYUN = "aliyun"


def _video_editor_provider_mode() -> VideoEditorProviderMode:
    raw_mode = (
        _secret("VIDEO_EDITOR_PROVIDER_MODE", "sandbox") or "sandbox"
    ).casefold()
    try:
        return VideoEditorProviderMode(raw_mode)
    except ValueError as exc:
        raise ValueError(
            "VIDEO_EDITOR_PROVIDER_MODE 仅支持 sandbox 或 aliyun。",
        ) from exc


def _secret_int(key: str, default: int) -> int:
    try:
        return int(_secret(key, str(default)) or default)
    except ValueError:
        return default


VIDEO_EDITOR_PROVIDER_MODE: VideoEditorProviderMode = _video_editor_provider_mode()
VIDEO_EDITOR_PRICE_VERSION: str = _secret(
    "VIDEO_EDITOR_PRICE_VERSION",
    "aliyun-cn-mainland-2026-07-28",
)
VIDEO_EDITOR_QUOTE_TTL_SECONDS: int = max(
    60,
    _secret_int("VIDEO_EDITOR_QUOTE_TTL_SECONDS", 900),
)

# 百炼 Fun-ASR / qwen-flash
DASHSCOPE_API_KEY: str = _secret("DASHSCOPE_API_KEY")
ALIYUN_MODEL_STUDIO_WORKSPACE_ID: str = _secret(
    "ALIYUN_MODEL_STUDIO_WORKSPACE_ID"
)

# OSS 与 MPS 必须在同一地域。凭证沿用阿里云 SDK 的标准环境变量名。
ALIYUN_VIDEO_EDITOR_REGION: str = _secret(
    "ALIYUN_VIDEO_EDITOR_REGION",
    "cn-beijing",
)
ALIYUN_OSS_BUCKET: str = _secret("ALIYUN_OSS_BUCKET")
ALIYUN_MPS_PIPELINE_ID: str = _secret("ALIYUN_MPS_PIPELINE_ID")
ALIYUN_MPS_TEMPLATE_ID_720P: str = _secret(
    "ALIYUN_MPS_TEMPLATE_ID_720P"
)
ALIYUN_MPS_TEMPLATE_ID_1080P: str = _secret(
    "ALIYUN_MPS_TEMPLATE_ID_1080P"
)
ALIBABA_CLOUD_ACCESS_KEY_ID: str = _secret(
    "ALIBABA_CLOUD_ACCESS_KEY_ID"
)
ALIBABA_CLOUD_ACCESS_KEY_SECRET: str = _secret(
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET"
)


# ASR 模式：sandbox / local / cloud（默认 sandbox）
ASR_MODE: ASRMode = ASRMode(_env("ASR_MODE", "sandbox") or "sandbox")

# 云端供应商选择（默认 aliyun）
ASR_CLOUD_PROVIDER: ASRCloudProvider = ASRCloudProvider(
    _env("ASR_CLOUD_PROVIDER", "aliyun") or "aliyun"
)

# 阿里云 ASR 凭证
ALIYUN_ASR_ACCESS_KEY_ID: str = _env("ALIYUN_ASR_ACCESS_KEY_ID")
ALIYUN_ASR_ACCESS_KEY_SECRET: str = _env("ALIYUN_ASR_ACCESS_KEY_SECRET")
ALIYUN_ASR_APP_KEY: str = _env("ALIYUN_ASR_APP_KEY")


# ---------------------------------------------------------------------------
# 关键词商业搜索配置
# ---------------------------------------------------------------------------

class CrawlerProviderMode(StrEnum):
    """关键词商业搜索供应商模式。"""
    SANDBOX = "sandbox"
    ONEAPI = "oneapi"
    PRODUCTION = "production"


_crawler_mode_value = (
    _secret("CRAWLER_PROVIDER_MODE")
    or _secret("VIDEO_LICENSED_PROVIDER_MODE")
    or "sandbox"
)
CRAWLER_PROVIDER_MODE: CrawlerProviderMode = CrawlerProviderMode(
    _crawler_mode_value
)
CRAWLER_PROVIDER_NAME: str = _secret(
    "CRAWLER_PROVIDER_NAME",
    "commercial_provider_pending",
)
ONEAPI_API_KEY: str = _secret("ONEAPI_API_KEY")


def _crawler_active_platforms() -> tuple[str, ...]:
    """返回本阶段允许自动调用的平台。

    供应商可以同时支持多平台，但自动化流程可以只开放其中一部分，
    避免未验收平台产生付费请求。
    """

    supported = {"douyin", "xiaohongshu", "wechat_channels"}
    configured = _secret("CRAWLER_ACTIVE_PLATFORMS", "douyin")
    values = tuple(
        dict.fromkeys(
            item.strip().casefold()
            for item in configured.split(",")
            if item.strip().casefold() in supported
        )
    )
    return values or ("douyin",)


CRAWLER_ACTIVE_PLATFORMS: tuple[str, ...] = _crawler_active_platforms()

# ---------------------------------------------------------------------------
# 抖音开放平台官方数据（热门视频榜 + 实时热点词）
# ---------------------------------------------------------------------------


def _env_flag(key: str, default: bool) -> bool:
    raw = _env(key)
    if not raw:
        return default
    return raw.casefold() in {"1", "true", "yes", "on"}


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)) or default)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)) or default)
    except ValueError:
        return default


# 发现与付费解析分离：默认用专用本机 Chrome 发现公开作品；OneAPI
# 仍保留给明确确认的媒体解析和关闭浏览器发现后的人工兜底。
DOUYIN_BROWSER_DISCOVERY_ENABLED: bool = _env_flag(
    # 仅在用户点击“连接热点宝”时启动独立浏览器；默认开启能力不等于自动登录或采集。
    "DOUYIN_BROWSER_DISCOVERY_ENABLED", True
)
DOUYIN_BROWSER_DISCOVERY_PROFILE_DIR: Path = Path(
    _env(
        "DOUYIN_BROWSER_DISCOVERY_PROFILE_DIR",
        str(PROJECT_ROOT / "data" / "browser_profiles" / "douyin"),
    )
)
DOUYIN_BROWSER_DISCOVERY_DEBUG_PORT: int = _env_int(
    "DOUYIN_BROWSER_DISCOVERY_DEBUG_PORT", 19222
)
XIAOHONGSHU_BROWSER_DISCOVERY_ENABLED: bool = _env_flag(
    "XIAOHONGSHU_BROWSER_DISCOVERY_ENABLED", False
)
XIAOHONGSHU_BROWSER_DISCOVERY_PROFILE_DIR: Path = Path(
    _env(
        "XIAOHONGSHU_BROWSER_DISCOVERY_PROFILE_DIR",
        str(PROJECT_ROOT / "data" / "browser_profiles" / "xiaohongshu"),
    )
)
XIAOHONGSHU_BROWSER_DISCOVERY_DEBUG_PORT: int = _env_int(
    "XIAOHONGSHU_BROWSER_DISCOVERY_DEBUG_PORT", 19223
)
KUAISHOU_BROWSER_DISCOVERY_ENABLED: bool = _env_flag(
    "KUAISHOU_BROWSER_DISCOVERY_ENABLED", True
)
KUAISHOU_BROWSER_DISCOVERY_PROFILE_DIR: Path = Path(
    _env(
        "KUAISHOU_BROWSER_DISCOVERY_PROFILE_DIR",
        str(PROJECT_ROOT / "data" / "browser_profiles" / "kuaishou"),
    )
)
KUAISHOU_BROWSER_DISCOVERY_DEBUG_PORT: int = _env_int(
    "KUAISHOU_BROWSER_DISCOVERY_DEBUG_PORT", 19224
)
BILIBILI_BROWSER_DISCOVERY_ENABLED: bool = _env_flag(
    "BILIBILI_BROWSER_DISCOVERY_ENABLED", True
)
BILIBILI_BROWSER_DISCOVERY_PROFILE_DIR: Path = Path(
    _env(
        "BILIBILI_BROWSER_DISCOVERY_PROFILE_DIR",
        str(PROJECT_ROOT / "data" / "browser_profiles" / "bilibili"),
    )
)
BILIBILI_BROWSER_DISCOVERY_DEBUG_PORT: int = _env_int(
    "BILIBILI_BROWSER_DISCOVERY_DEBUG_PORT", 19225
)
# 不允许后台定时器在未明确打开预算开关时调用付费 OneAPI 搜索。
CRAWLER_ONEAPI_AUTO_ENABLED: bool = _env_flag(
    "CRAWLER_ONEAPI_AUTO_ENABLED", False
)


# One authorized public Douyin share link is resolved by a temporary local
# browser context.  The API never reads browser profiles or session material.
DOUYIN_LOCAL_BROWSER_ENABLED: bool = _env_flag("DOUYIN_LOCAL_BROWSER_ENABLED", True)
DOUYIN_BROWSER_CHANNEL: str = _env("DOUYIN_BROWSER_CHANNEL", "chrome").casefold()
DOUYIN_BROWSER_TIMEOUT_SECONDS: float = _env_float(
    "DOUYIN_BROWSER_TIMEOUT_SECONDS", 35.0
)


# 抖音开放平台应用凭证（scope: data.external.billboard_hot_video，无需用户授权）
DOUYIN_CLIENT_KEY: str = _secret("DOUYIN_CLIENT_KEY")
DOUYIN_CLIENT_SECRET: str = _secret("DOUYIN_CLIENT_SECRET")
# 官方热榜 / 官方实时热点词开关；false 时 capabilities 报未启用且不调用官方接口
DOUYIN_OFFICIAL_HOT_ENABLED: bool = _env_flag("DOUYIN_OFFICIAL_HOT_ENABLED", True)
DOUYIN_HOT_WORDS_ENABLED: bool = _env_flag("DOUYIN_HOT_WORDS_ENABLED", True)


# ---------------------------------------------------------------------------
# AI 文案大模型配置
# ---------------------------------------------------------------------------


class CopywritingProviderMode(StrEnum):
    """AI 文案供应商模式。"""

    SANDBOX = "sandbox"
    PRODUCTION = "production"


_copywriting_mode_value = _secret("COPYWRITING_MODE", "production") or "production"
COPYWRITING_MODE: CopywritingProviderMode = CopywritingProviderMode(
    _copywriting_mode_value
)
COPYWRITING_API_KEY: str = (
    _secret("COPYWRITING_API_KEY") or _secret("OPENAI_API_KEY")
)
COPYWRITING_BASE_URL: str = (
    _secret("COPYWRITING_BASE_URL")
    or _secret("OPENAI_BASE_URL")
    or "https://api.deepseek.com"
)
COPYWRITING_MODEL: str = (
    _secret("COPYWRITING_MODEL")
    or _secret("COPYWRITING_LLM_MODEL")
    or "deepseek-v4-flash"
)
_copywriting_estimated_cost_raw = _secret(
    "COPYWRITING_ESTIMATED_REQUEST_COST_CNY"
)
try:
    COPYWRITING_ESTIMATED_REQUEST_COST_CNY: float | None = (
        max(0.0, float(_copywriting_estimated_cost_raw))
        if _copywriting_estimated_cost_raw
        else None
    )
except ValueError:
    COPYWRITING_ESTIMATED_REQUEST_COST_CNY = None
