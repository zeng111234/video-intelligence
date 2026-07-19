"""上下文预算保护模块。

提供会话状态大小估算和并发任务数限制，防止 Streamlit 应用因内存膨胀或并发过高而崩溃。
"""
from __future__ import annotations

import sys
import threading
from collections.abc import MutableMapping
from contextlib import contextmanager
from typing import Any


def estimate_object_size(obj: Any, seen: set[int] | None = None) -> int:
    """递归估算 Python 对象的内存占用（字节）。

    重点监控 bytes、str、list、dict、set 等容器类型。
    对于不可变的标量类型（int、float、bool、None），返回 sys.getsizeof 的值。
    """
    if seen is None:
        seen = set()

    obj_id = id(obj)
    if obj_id in seen:
        return 0
    seen.add(obj_id)

    size = sys.getsizeof(obj)

    if isinstance(obj, (bytes, bytearray)):
        return size
    if isinstance(obj, str):
        return size
    if isinstance(obj, dict):
        size += sum(
            estimate_object_size(k, seen) + estimate_object_size(v, seen)
            for k, v in obj.items()
        )
        return size
    if isinstance(obj, (list, tuple, set, frozenset)):
        size += sum(estimate_object_size(item, seen) for item in obj)
        return size

    return size


def estimate_session_state_size(state: MutableMapping[str, Any]) -> int:
    """估算 session_state 的总内存占用。

    对于大型容器（list、dict），递归计算其内部元素大小。
    """
    total = 0
    seen: set[int] = set()
    for key, value in state.items():
        total += estimate_object_size(key, seen)
        total += estimate_object_size(value, seen)
    return total


class ContextBudget:
    """上下文预算管理器，提供会话状态监控和并发任务限制。

    Attributes:
        max_concurrent_tasks: 最大并发任务数，默认 3。
        session_state_budget_mb: session_state 内存预算（MB），默认 200MB。
    """

    def __init__(
        self,
        max_concurrent_tasks: int = 3,
        session_state_budget_mb: float = 200.0,
    ) -> None:
        self.max_concurrent_tasks = max_concurrent_tasks
        self.session_state_budget_mb = session_state_budget_mb
        self._task_semaphore = threading.Semaphore(max_concurrent_tasks)
        self._active_tasks = 0
        self._lock = threading.Lock()

    @property
    def active_tasks(self) -> int:
        """当前活跃任务数。"""
        with self._lock:
            return self._active_tasks

    @property
    def available_slots(self) -> int:
        """可用任务槽位数。"""
        with self._lock:
            return self.max_concurrent_tasks - self._active_tasks

    def acquire_task_slot(self, timeout: float | None = None) -> bool:
        """获取一个任务槽位。

        Args:
            timeout: 等待超时时间（秒），None 表示无限等待。

        Returns:
            True 表示成功获取，False 表示超时。
        """
        acquired = self._task_semaphore.acquire(timeout=timeout)
        if acquired:
            with self._lock:
                self._active_tasks += 1
        return acquired

    def release_task_slot(self) -> None:
        """释放一个任务槽位。"""
        with self._lock:
            if self._active_tasks > 0:
                self._active_tasks -= 1
            else:
                raise RuntimeError("没有活跃任务可以释放")
        self._task_semaphore.release()

    @contextmanager
    def task_slot(self, timeout: float | None = None):
        """上下文管理器，自动获取和释放任务槽位。

        Args:
            timeout: 等待超时时间（秒），None 表示无限等待。

        Raises:
            RuntimeError: 获取槽位超时。
        """
        acquired = self.acquire_task_slot(timeout=timeout)
        if not acquired:
            raise RuntimeError(
                f"等待任务槽位超时（当前活跃任务: {self.active_tasks}/{self.max_concurrent_tasks}）"
            )
        try:
            yield
        finally:
            self.release_task_slot()

    def check_session_budget(
        self, state: MutableMapping[str, Any] | dict[str, Any]
    ) -> tuple[bool, float, float]:
        """检查 session_state 是否超出内存预算。

        Args:
            state: Streamlit session_state 或兼容的可变映射。

        Returns:
            (is_within_budget, current_mb, budget_mb) 三元组。
        """
        current_bytes = estimate_session_state_size(state)
        current_mb = current_bytes / (1024 * 1024)
        budget_mb = self.session_state_budget_mb
        return (current_mb <= budget_mb, current_mb, budget_mb)

    def get_status(self) -> dict[str, Any]:
        """获取当前预算状态摘要。"""
        return {
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "active_tasks": self.active_tasks,
            "available_slots": self.available_slots,
            "session_state_budget_mb": self.session_state_budget_mb,
        }
