from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


class ExternalServiceError(RuntimeError):
    """A user-displayable external service failure."""


@dataclass
class RetryPolicy:
    """配置化重试策略，支持指数退避和预算保护。

    Attributes:
        max_attempts: 最大尝试次数（含首次），默认 2 表示 1 次重试。
        base_delay: 首次重试的基础延迟（秒），默认 0.5。
        max_delay: 指数退避最大延迟上限（秒），默认 5.0。
        budget_seconds: 整体超时预算（秒），None 表示不限制。
        backoff_factor: 退避倍率，默认 2.0（即指数退避）。
    """

    max_attempts: int = 2
    base_delay: float = 0.5
    max_delay: float = 5.0
    budget_seconds: float | None = None
    backoff_factor: float = 2.0


def retry_with_policy(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    retry_for: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError),
    retryable: Callable[[BaseException], bool] | None = None,
    on_retry: Callable[[int, BaseException, float], Any] | None = None,
    error_message: str | None = None,
) -> T:
    """使用配置化策略执行操作，支持指数退避和预算保护。

    Args:
        operation: 要执行的可调用对象。
        policy: 重试策略配置，为 None 时使用默认值。
        retry_for: 可重试的异常类型元组。
        retryable: 额外的异常判断函数，返回 True 才会重试。
        on_retry: 重试回调函数，签名 (attempt, error, delay)。

    Returns:
        操作的返回值。

    Raises:
        ExternalServiceError: 所有重试耗尽或预算超时后抛出。
    """
    if policy is None:
        policy = RetryPolicy()

    start_time = time.monotonic()
    last_error: BaseException | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation()
        except retry_for as exc:
            last_error = exc

            # 检查是否还有重试机会
            if attempt >= policy.max_attempts:
                break

            # 检查额外的可重试判断
            if retryable is not None and not retryable(exc):
                break

            # 计算退避延迟（首次重试用 base_delay，之后指数增长）
            if attempt == 1:
                delay = policy.base_delay
            else:
                delay = min(
                    policy.base_delay * (policy.backoff_factor ** (attempt - 1)),
                    policy.max_delay,
                )

            # 预算检查
            if policy.budget_seconds is not None:
                elapsed = time.monotonic() - start_time
                remaining = policy.budget_seconds - elapsed
                if remaining <= 0:
                    raise ExternalServiceError(
                        f"操作超时，已用时 {elapsed:.1f}s 超过预算 {policy.budget_seconds}s"
                    ) from last_error
                # 确保延迟不超过剩余预算
                delay = min(delay, remaining)

            # 触发回调
            if on_retry is not None:
                on_retry(attempt, exc, delay)

            # 等待退避时间
            if delay > 0:
                time.sleep(delay)

    raise ExternalServiceError(
        error_message
        or f"连接失败，已自动重试 {policy.max_attempts - 1} 次：{last_error}"
    ) from last_error


def run_with_single_retry(
    operation: Callable[[], T],
    *,
    retry_for: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError),
) -> T:
    """Run a network-like operation at most twice in total.

    向后兼容的包装函数，内部调用 retry_with_policy，保持原有行为：
    - max_attempts=2（1 次重试）
    - base_delay=0（无延迟，保持原有行为）
    """
    try:
        return retry_with_policy(
            operation,
            policy=RetryPolicy(max_attempts=2, base_delay=0),
            retry_for=retry_for,
        )
    except ExternalServiceError as exc:
        # 保持原始错误消息格式以兼容旧调用
        raise ExternalServiceError(
            f"连接失败，已自动重试一次：{exc.__cause__}"
        ) from exc.__cause__
