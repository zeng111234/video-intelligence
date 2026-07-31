"""Persistent, deliberately serial local publishing worker."""

from __future__ import annotations

import asyncio
import logging

from datetime import datetime

from src.models import PublishPlatform, PublishStatus, PublishTask, TaskStatus
from src.services.publisher import PublishService

logger = logging.getLogger(__name__)


class PublishWorker:
    """Runs at most one browser publish preparation at a time on this PC."""

    def __init__(self, publish_service: PublishService, *, interval_seconds: float = 2.0) -> None:
        self.publish_service = publish_service
        self.interval_seconds = interval_seconds
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
        queued = sorted(
            (task for task in self.publish_service.list_tasks() if task.status == TaskStatus.QUEUED),
            key=lambda task: task.created_at,
        )
        for task in queued:
            if task.target.platform == PublishPlatform.XIAOHONGSHU:
                paused = task.model_copy(
                    update={
                        "status": TaskStatus.PAUSED,
                        "publish_status": PublishStatus.MANUAL_READY,
                        "stage": "小红书人工发布包已就绪",
                        "action_required": "小红书安全模式已开启：请人工打开官方创作端上传并发布；系统不会再操作账号。",
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                self.publish_service.repository.save_task(paused)
                continue
            return self.publish_service.execute_queued_task(task.task_id)
        return None
