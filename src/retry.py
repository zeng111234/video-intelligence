from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class ExternalServiceError(RuntimeError):
    """A user-displayable external service failure."""


def run_with_single_retry(
    operation: Callable[[], T],
    *,
    retry_for: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError),
) -> T:
    """Run a network-like operation at most twice in total."""
    last_error: BaseException | None = None
    for _ in range(2):
        try:
            return operation()
        except retry_for as exc:
            last_error = exc
    raise ExternalServiceError(
        f"连接失败，已自动重试一次：{last_error}"
    ) from last_error
