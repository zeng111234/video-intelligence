"""Server-only digital-avatar provider dependency."""

from __future__ import annotations

from functools import lru_cache

from src.adapters.avatar import build_avatar_provider


@lru_cache
def get_server_avatar_provider():
    return build_avatar_provider()
