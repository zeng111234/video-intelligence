"""Lightweight shared repository dependencies.

The company control plane imports only authentication and billing code.  Keep
these dependencies separate from the desktop service graph so the server image
does not need browser automation, media models or Windows-only tooling.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends

from project.backend.app.core.config import DATABASE_PATH
from src.repositories.sqlite import SQLiteRepository
from src.services.credits import CreditsService


@lru_cache
def get_repository() -> SQLiteRepository:
    return SQLiteRepository(str(DATABASE_PATH))


def get_credits_service(
    repo: SQLiteRepository = Depends(get_repository),
) -> CreditsService:
    return CreditsService(repo)
