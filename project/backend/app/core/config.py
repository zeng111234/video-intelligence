"""后端配置 —— 不依赖 streamlit secrets。"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

# config.py 在 project/backend/app/core/ 下，需要 5 层 parent 才能到仓库根
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
DATABASE_PATH = PROJECT_ROOT / "data" / "video_intelligence.db"


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
    PRODUCTION = "production"


CRAWLER_PROVIDER_MODE: CrawlerProviderMode = CrawlerProviderMode(
    _env("CRAWLER_PROVIDER_MODE", "sandbox") or "sandbox"
)
CRAWLER_PROVIDER_NAME: str = _env("CRAWLER_PROVIDER_NAME", "commercial_provider_pending")
