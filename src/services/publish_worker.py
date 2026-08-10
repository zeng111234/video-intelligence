"""Persistent, deliberately serial local publishing worker."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from src.models import PublishTask, TaskStatus
from src.services.publisher import PublishService

logger = logging.getLogger(__name__)


class PublishWorker:
    """Runs at most one browser publish preparation at a time on this PC."""

    def __init__(
        self,
        publish_service: PublishService,
        *,
        interval_seconds: float = 2.0,
        can_process: Callable[[], bool] | None = None,
    ) -> None:
        self.publish_service = publish_service
        self.interval_seconds = interval_seconds
        self.can_process = can_process or (lambda: True)
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
                await asyncio.to_thread(self.tick_once)
            except Exception:
                logger.exception("本机发布队列扫描失败；将在下一轮继续")
            try:
                await asyncio.wait_for(stopping.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    def tick_once(self) -> PublishTask | None:
        if not self.can_process():
            return None
        queued = sorted(
            (task for task in self.publish_service.list_tasks() if task.status == TaskStatus.QUEUED),
            key=lambda task: task.created_at,
        )
        for task in queued:
            return self.publish_service.execute_queued_task(task.task_id)
        return None
