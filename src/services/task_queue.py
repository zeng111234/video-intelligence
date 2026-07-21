"""任务队列系统

实现带状态机的任务队列，支持：
- 任务创建、暂停、恢复、取消
- 进度追踪（每个阶段）
- 失败重试（可配置次数）
- 批量任务管理

状态机：PENDING → RUNNING → PAUSED → DONE/FAILED
参考 SyncCaster 的任务队列设计。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"      # 等待执行
    RUNNING = "running"      # 执行中
    PAUSED = "paused"        # 已暂停
    SUCCESS = "success"      # 成功
    FAILED = "failed"        # 失败
    CANCELLED = "cancelled"  # 已取消


class TaskStage(str, Enum):
    """任务阶段"""
    INIT = "init"                    # 初始化
    AUTH_CHECK = "auth_check"        # 登录检查
    UPLOAD = "upload"                # 上传文件
    FILL_CONTENT = "fill_content"    # 填写内容
    PUBLISH = "publish"              # 发布
    VERIFY = "verify"                # 验证结果
    COMPLETE = "complete"            # 完成


@dataclass
class TaskProgress:
    """任务进度"""
    stage: TaskStage
    progress: int  # 0-100
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueueTask:
    """队列任务"""
    task_id: str
    task_type: str  # e.g., "publish", "transcribe", "generate"
    platform: str
    params: dict[str, Any] = field(default_factory=dict)

    # 状态
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    current_stage: TaskStage = TaskStage.INIT
    message: str = ""

    # 时间
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # 重试
    retry_count: int = 0
    max_retries: int = 3
    error: str | None = None

    # 结果
    result: dict[str, Any] = field(default_factory=dict)


class TaskQueue:
    """任务队列管理器"""

    def __init__(self, max_concurrent: int = 3):
        self._tasks: dict[str, QueueTask] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running: dict[str, asyncio.Task] = {}
        self._max_concurrent = max_concurrent
        self._handlers: dict[str, Callable] = {}
        self._progress_callbacks: dict[str, Callable] = {}

    def register_handler(self, task_type: str, handler: Callable):
        """注册任务处理器"""
        self._handlers[task_type] = handler
        logger.info(f"注册任务处理器: {task_type}")

    def create_task(
        self,
        task_type: str,
        platform: str,
        params: dict[str, Any],
        max_retries: int = 3,
    ) -> QueueTask:
        """创建任务"""
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        task = QueueTask(
            task_id=task_id,
            task_type=task_type,
            platform=platform,
            params=params,
            max_retries=max_retries,
        )
        self._tasks[task_id] = task
        logger.info(f"任务已创建: {task_id} ({task_type}/{platform})")
        return task

    async def submit_task(self, task_id: str, on_progress: Callable | None = None) -> QueueTask:
        """提交任务到队列"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")

        if task.status not in [TaskStatus.PENDING, TaskStatus.FAILED]:
            raise ValueError(f"任务状态不允许提交: {task.status}")

        # 重置状态
        task.status = TaskStatus.PENDING
        task.progress = 0
        task.current_stage = TaskStage.INIT
        task.message = "等待执行..."
        task.error = None

        # 保存进度回调
        if on_progress:
            self._progress_callbacks[task_id] = on_progress

        # 添加到队列
        await self._queue.put(task_id)
        logger.info(f"任务已提交: {task_id}")

        # 尝试执行
        await self._try_execute()

        return task

    async def _try_execute(self):
        """尝试执行队列中的任务"""
        while len(self._running) < self._max_concurrent and not self._queue.empty():
            task_id = await self._queue.get()
            task = self._tasks.get(task_id)

            if not task or task.status != TaskStatus.PENDING:
                continue

            # 创建异步任务
            async_task = asyncio.create_task(self._execute_task(task_id))
            self._running[task_id] = async_task

    async def _execute_task(self, task_id: str):
        """执行任务"""
        task = self._tasks.get(task_id)
        if not task:
            return

        handler = self._handlers.get(task.task_type)
        if not handler:
            task.status = TaskStatus.FAILED
            task.error = f"未注册的处理器: {task.task_type}"
            task.completed_at = datetime.now()
            self._notify_progress(task)
            return

        try:
            # 更新状态
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now()
            task.message = "开始执行..."
            self._notify_progress(task)

            # 执行处理器
            result = await handler(task)

            # 成功
            task.status = TaskStatus.SUCCESS
            task.progress = 100
            task.current_stage = TaskStage.COMPLETE
            task.message = "执行完成"
            task.result = result or {}
            task.completed_at = datetime.now()
            logger.info(f"任务完成: {task_id}")

        except Exception as e:
            logger.error(f"任务失败: {task_id} - {e}")

            # 检查是否可以重试
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                task.status = TaskStatus.PENDING
                task.message = f"重试 {task.retry_count}/{task.max_retries}..."
                task.error = str(e)
                logger.info(f"任务重试: {task_id} ({task.retry_count}/{task.max_retries})")
                await self._queue.put(task_id)
            else:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                task.message = f"执行失败: {str(e)}"
                task.completed_at = datetime.now()

        finally:
            # 从运行中移除
            self._running.pop(task_id, None)
            self._notify_progress(task)

            # 尝试执行下一个
            await self._try_execute()

    def _notify_progress(self, task: QueueTask):
        """通知进度更新"""
        callback = self._progress_callbacks.get(task.task_id)
        if callback:
            try:
                callback(task)
            except Exception as e:
                logger.warning(f"进度回调失败: {e}")

    def update_progress(self, task_id: str, stage: TaskStage, progress: int, message: str = ""):
        """更新任务进度"""
        task = self._tasks.get(task_id)
        if not task:
            return

        task.current_stage = stage
        task.progress = progress
        task.message = message
        self._notify_progress(task)

    def pause_task(self, task_id: str) -> bool:
        """暂停任务"""
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return False

        task.status = TaskStatus.PAUSED
        task.message = "任务已暂停"
        self._notify_progress(task)
        return True

    def resume_task(self, task_id: str) -> bool:
        """恢复任务"""
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.PAUSED:
            return False

        task.status = TaskStatus.PENDING
        task.message = "等待恢复执行..."
        asyncio.create_task(self._queue.put(task_id))
        asyncio.create_task(self._try_execute())
        return True

    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        task = self._tasks.get(task_id)
        if not task:
            return False

        if task.status in [TaskStatus.SUCCESS, TaskStatus.CANCELLED]:
            return False

        task.status = TaskStatus.CANCELLED
        task.message = "任务已取消"
        task.completed_at = datetime.now()

        # 取消正在运行的任务
        running_task = self._running.get(task_id)
        if running_task:
            running_task.cancel()
            self._running.pop(task_id, None)

        self._notify_progress(task)
        return True

    def get_task(self, task_id: str) -> QueueTask | None:
        """获取任务"""
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[QueueTask]:
        """获取所有任务"""
        return list(self._tasks.values())

    def get_tasks_by_status(self, status: TaskStatus) -> list[QueueTask]:
        """按状态获取任务"""
        return [t for t in self._tasks.values() if t.status == status]

    def get_tasks_by_platform(self, platform: str) -> list[QueueTask]:
        """按平台获取任务"""
        return [t for t in self._tasks.values() if t.platform == platform]

    def clear_completed(self):
        """清理已完成的任务"""
        completed_ids = [
            task_id for task_id, task in self._tasks.items()
            if task.status in [TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED]
        ]
        for task_id in completed_ids:
            del self._tasks[task_id]
            self._progress_callbacks.pop(task_id, None)
        logger.info(f"已清理 {len(completed_ids)} 个完成的任务")


# 全局实例
task_queue = TaskQueue()
