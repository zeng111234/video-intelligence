"""Lightweight copywriting dependencies shared by desktop and control plane."""

from __future__ import annotations

import os
import sys
from functools import lru_cache

from project.backend.app.core.config import (
    COPYWRITING_API_KEY,
    COPYWRITING_BASE_URL,
    COPYWRITING_ESTIMATED_REQUEST_COST_CNY,
    COPYWRITING_MODE,
    COPYWRITING_MODEL,
    CopywritingProviderMode,
)
from project.backend.app.core.repository import get_repository
from src.adapters.llm import (
    DisabledCopywritingEngine,
    OpenAICompatibleCopywritingEngine,
    SandboxCopywritingEngine,
)
from src.services.copywriting import CopywritingService


def _legacy_override(name: str, default):
    """Honor the long-standing dependency-module configuration surface."""

    dependencies = sys.modules.get("project.backend.app.core.deps")
    return getattr(dependencies, name, default) if dependencies is not None else default


@lru_cache
def get_copywriting_engine():
    """Return the configured server-side copywriting engine.

    Production mode never falls back to mock output when the supplier key is
    missing.  The explicit sandbox mode remains available for local acceptance.
    """

    if os.getenv("VIDEOINSIGHT_DESKTOP_CLIENT", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        from project.backend.app.services.control_plane_client import (
            control_plane_enabled,
        )

        if control_plane_enabled():
            from project.backend.app.services.remote_copywriting import (
                RemoteCopywritingEngine,
            )

            return RemoteCopywritingEngine()
    mode = _legacy_override("COPYWRITING_MODE", COPYWRITING_MODE)
    api_key = _legacy_override("COPYWRITING_API_KEY", COPYWRITING_API_KEY)
    base_url = _legacy_override("COPYWRITING_BASE_URL", COPYWRITING_BASE_URL)
    model = _legacy_override("COPYWRITING_MODEL", COPYWRITING_MODEL)
    estimated_cost = _legacy_override(
        "COPYWRITING_ESTIMATED_REQUEST_COST_CNY",
        COPYWRITING_ESTIMATED_REQUEST_COST_CNY,
    )
    if mode == CopywritingProviderMode.SANDBOX:
        return SandboxCopywritingEngine()
    if api_key:
        return OpenAICompatibleCopywritingEngine(
            api_key=api_key,
            base_url=base_url,
            model=model,
            estimated_cost_cny=estimated_cost,
        )
    return DisabledCopywritingEngine(
        base_url=base_url,
        model=model,
    )


@lru_cache
def get_copywriting_service() -> CopywritingService:
    return CopywritingService(get_repository(), get_copywriting_engine())
