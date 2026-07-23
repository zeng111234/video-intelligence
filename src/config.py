from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from src.adapters.licensed import (
    DisabledLicensedSearchProvider,
    SandboxLicensedSearchProvider,
)
from src.adapters.oneapi import OneApiLicensedSearchProvider
from src.adapters.official import (
    DouyinHotBillboardAdapter,
    DouyinHotWordsAdapter,
    DouyinKeywordAdapter,
)


def runtime_value(name: str, secrets: Mapping[str, Any] | None = None) -> str:
    if secrets is not None:
        try:
            value = secrets[name]
            if value is not None:
                return str(value).strip()
        except Exception:
            pass
    return os.getenv(name, "").strip()


def build_douyin_keyword_adapter(
    secrets: Mapping[str, Any] | None = None,
) -> DouyinKeywordAdapter:
    verified = runtime_value("DOUYIN_OFFICIAL_VERIFIED", secrets).casefold() in {
        "1",
        "true",
        "yes",
    }
    if not verified:
        return DouyinKeywordAdapter()
    return DouyinKeywordAdapter(
        runtime_value("DOUYIN_CLIENT_KEY", secrets),
        runtime_value("DOUYIN_CLIENT_SECRET", secrets),
    )


def build_licensed_search_provider(secrets: Mapping[str, Any] | None = None):
    mode = runtime_value("VIDEO_LICENSED_PROVIDER_MODE", secrets).casefold()
    if mode in {"", "sandbox"}:
        return SandboxLicensedSearchProvider()
    if mode == "oneapi":
        return OneApiLicensedSearchProvider(
            runtime_value("ONEAPI_API_KEY", secrets),
        )
    provider_name = runtime_value("VIDEO_LICENSED_PROVIDER_NAME", secrets)
    return DisabledLicensedSearchProvider(provider_name)


def _official_flag(
    name: str,
    secrets: Mapping[str, Any] | None = None,
    *,
    default: bool = True,
) -> bool:
    raw = runtime_value(name, secrets).casefold()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def build_douyin_hot_billboard_adapter(
    secrets: Mapping[str, Any] | None = None,
) -> DouyinHotBillboardAdapter | None:
    """构造官方热门视频榜适配器；DOUYIN_OFFICIAL_HOT_ENABLED=false 时返回 None。"""
    if not _official_flag("DOUYIN_OFFICIAL_HOT_ENABLED", secrets):
        return None
    return DouyinHotBillboardAdapter(
        runtime_value("DOUYIN_CLIENT_KEY", secrets),
        runtime_value("DOUYIN_CLIENT_SECRET", secrets),
    )


def build_douyin_hot_words_adapter(
    secrets: Mapping[str, Any] | None = None,
) -> DouyinHotWordsAdapter | None:
    """构造官方实时热点词适配器；DOUYIN_HOT_WORDS_ENABLED=false 时返回 None。"""
    if not _official_flag("DOUYIN_HOT_WORDS_ENABLED", secrets):
        return None
    return DouyinHotWordsAdapter(
        runtime_value("DOUYIN_CLIENT_KEY", secrets),
        runtime_value("DOUYIN_CLIENT_SECRET", secrets),
    )
