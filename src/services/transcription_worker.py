from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from src.models import TaskStatus, TranscriptionTask

logger = logging.getLogger(__name__)


class TranscriptionWorker:
    """Run one persisted cloud transcription at a time."""

    def __init__(
        self,
        service,
        *,
        interval_seconds: float = 2.0,
        can_process: Callable[[], bool] | None = None,
    ) -> None:
        self.service = service
        self.interval_seconds = interval_seconds
        self.can_process = can_process or (lambda: True)
        self._task: asyncio.Task | None = None
        self._stopping: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(
            self._loop(),
            name="transcription-worker",
        )

    async def stop(self) -> None:
        if self._stopping is not None:
            self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        while self._stopping is not None and not self._stopping.is_set():
            try:
                await asyncio.to_thread(self.tick_once)
            except Exception:
                logger.exception("云端转写任务处理失败；将在登录后或下一轮继续")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.interval_seconds,
                )
            except TimeoutError:
                pass

    def tick_once(self) -> TranscriptionTask | None:
        """Process one persisted cloud task when the desktop owner is active."""

        if not self.can_process():
            return None
        task = next(
            (
                item
                for item in self.service.repository.list_tasks()
                if isinstance(item, TranscriptionTask)
                and item.status
                in {
                    TaskStatus.QUEUED,
                    TaskStatus.SUBMITTED,
                    TaskStatus.RUNNING,
                }
                and item.provider_name == "aliyun_fun_asr"
            ),
            None,
        )
        if task is None:
            return None
        return self.service.process_cloud_task(task.task_id)
