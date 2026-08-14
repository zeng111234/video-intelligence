"""Persistent local publishing worker with bounded cross-account concurrency."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from src.models import PublishTask, TaskStatus
from src.services.publisher import PublishService

logger = logging.getLogger(__name__)


class PublishWorker:
    """Runs two independent accounts at once while each account stays serial."""

    def __init__(
        self,
        publish_service: PublishService,
        *,
        interval_seconds: float = 2.0,
        can_process: Callable[[], bool] | None = None,
        max_concurrency: int = 2,
    ) -> None:
        self.publish_service = publish_service
        self.interval_seconds = interval_seconds
        self.can_process = can_process or (lambda: True)
        self.max_concurrency = max(1, min(max_concurrency, 2))
        self._task: asyncio.Task | None = None
        # Cached workers can be restarted under a different event loop (for
        # example by consecutive FastAPI TestClient lifespans).  Create the
        # loop-bound event in start(), not in the cached constructor.
        self._stopping: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="publish-worker")

    async def stop(self) -> None:
        stopping = self._stopping
        if stopping is not None:
            stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._stopping = None

    async def _run(self) -> None:
        stopping = self._stopping
        if stopping is None:
            return
        while not stopping.is_set():
            try:
                await self._run_queued_batch()
            except Exception:
                logger.exception("本机发布队列扫描失败；将在下一轮继续")
            try:
                await asyncio.wait_for(stopping.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    def _next_task_ids(self) -> list[str]:
        if not self.can_process():
            return []
        queued = sorted(
            (
                task
                for task in self.publish_service.list_tasks()
                if task.status == TaskStatus.QUEUED
            ),
            key=lambda task: task.created_at,
        )
        selected: list[str] = []
        account_keys: set[tuple[str, str]] = set()
        for task in queued:
            key = (
                task.target.platform.value,
                task.target.account_id or "__default__",
            )
            if key in account_keys:
                continue
            account_keys.add(key)
            selected.append(task.task_id)
            if len(selected) >= self.max_concurrency:
                break
        return selected

    async def _run_queued_batch(self) -> list[PublishTask | None]:
        task_ids = self._next_task_ids()
        if not task_ids:
            return []
        results = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self.publish_service.execute_queued_task,
                    task_id,
                )
                for task_id in task_ids
            ),
            return_exceptions=True,
        )
        completed: list[PublishTask | None] = []
        for task_id, result in zip(task_ids, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "发布任务 %s 执行失败",
                    task_id,
                    exc_info=(type(result), result, result.__traceback__),
                )
                continue
            completed.append(result)
        return completed

    def tick_once(self) -> PublishTask | None:
        if not self.can_process():
            return None
        queued = sorted(
            (
                task
                for task in self.publish_service.list_tasks()
                if task.status == TaskStatus.QUEUED
            ),
            key=lambda task: task.created_at,
        )
        for task in queued:
            return self.publish_service.execute_queued_task(task.task_id)
        return None
