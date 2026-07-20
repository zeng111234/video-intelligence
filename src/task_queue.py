"""统一任务队列接口 + 内存沙箱实现。

为批量转写、数字人、剪辑、发布提供异步编排基础。
当前提供内存沙箱实现；后续可替换为 Celery/RQ/Dramatiq。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Callable
from uuid import uuid4


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    job_id: str
    task_type: str
    payload: dict[str, Any]
    status: JobStatus = JobStatus.PENDING
    result: Any = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_count: int = 0
    max_retries: int = 3


class TaskQueue(ABC):
    """任务队列抽象接口。"""

    @abstractmethod
    def enqueue(
        self, task_type: str, payload: dict[str, Any], *, max_retries: int = 3
    ) -> Job:
        """提交任务到队列，返回 Job。"""
        ...

    @abstractmethod
    def get_job(self, job_id: str) -> Job | None:
        """查询任务状态。"""
        ...

    @abstractmethod
    def list_jobs(self, task_type: str | None = None, limit: int = 50) -> list[Job]:
        """列出任务。"""
        ...

    @abstractmethod
    def register_handler(
        self, task_type: str, handler: Callable[[dict[str, Any]], Any]
    ) -> None:
        """注册任务类型处理器。"""
        ...

    @abstractmethod
    def process_next(self) -> bool:
        """处理队列中的下一个任务。返回是否处理了任务。"""
        ...


class InMemoryTaskQueue(TaskQueue):
    """内存沙箱任务队列 —— 同步执行，用于开发和测试。"""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._queue: list[str] = []
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}

    def enqueue(
        self, task_type: str, payload: dict[str, Any], *, max_retries: int = 3
    ) -> Job:
        job_id = f"job-{uuid4().hex[:12]}"
        job = Job(
            job_id=job_id,
            task_type=task_type,
            payload=payload,
            max_retries=max_retries,
        )
        self._jobs[job_id] = job
        self._queue.append(job_id)
        return job

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(self, task_type: str | None = None, limit: int = 50) -> list[Job]:
        jobs = list(self._jobs.values())
        if task_type:
            jobs = [j for j in jobs if j.task_type == task_type]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def register_handler(
        self, task_type: str, handler: Callable[[dict[str, Any]], Any]
    ) -> None:
        self._handlers[task_type] = handler

    def process_next(self) -> bool:
        if not self._queue:
            return False
        job_id = self._queue.pop(0)
        job = self._jobs.get(job_id)
        if job is None or job.status != JobStatus.PENDING:
            return False

        handler = self._handlers.get(job.task_type)
        if handler is None:
            job.status = JobStatus.FAILED
            job.error = f"未注册的任务类型处理器: {job.task_type}"
            job.finished_at = datetime.now().astimezone()
            return True

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now().astimezone()
        try:
            job.result = handler(job.payload)
            job.status = JobStatus.SUCCEEDED
        except Exception as exc:
            job.error = str(exc)
            job.status = JobStatus.FAILED
            if job.retry_count < job.max_retries:
                job.retry_count += 1
                job.status = JobStatus.PENDING
                self._queue.append(job_id)
        finally:
            if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
                job.finished_at = datetime.now().astimezone()
        return True
