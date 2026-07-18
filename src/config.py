from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from src.adapters.licensed import (
    DisabledLicensedSearchProvider,
    SandboxLicensedSearchProvider,
)
from src.adapters.official import DouyinKeywordAdapter


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
    provider_name = runtime_value("VIDEO_LICENSED_PROVIDER_NAME", secrets)
    return DisabledLicensedSearchProvider(provider_name)
