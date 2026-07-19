"""发布服务。

协调 Publisher 适配器与 TaskRepository，
支持单平台发布、多平台批量发布。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from src.contracts import Publisher, TaskRepository
from src.models import (
    PublishPlatform,
    PublishStatus,
    PublishTask,
    PublishTarget,
    TaskKind,
    TaskStatus,
)


class PublishService:
    def __init__(
        self,
        repository: TaskRepository,
        publishers: dict[str, Publisher],
    ) -> None:
        self.repository = repository
        self.publishers = publishers

    def available_platforms(self) -> list[dict[str, str | bool]]:
        result: list[dict[str, str | bool]] = []
        for pub in self.publishers.values():
            cap = pub.capabilities()
            result.append({
                "platform": pub.platform(),
                "enabled": bool(cap.get("enabled", False)),
                "display_name": str(cap.get("display_name", pub.platform())),
            })
        return result

    def publish(
        self,
        *,
        video_path: str,
        target: PublishTarget,
        source_pipeline_run_id: str | None = None,
        on_progress: Callable[[PublishTask], None] | None = None,
    ) -> PublishTask:
        """发布视频到指定平台。"""
        video = Path(video_path)

        platform_key = target.platform.value
        publisher = self.publishers.get(platform_key)
        if publisher is None:
            raise ValueError(f"未找到 {target.platform.value} 的发布适配器。")

        is_sandbox = bool(publisher.capabilities().get("mode") == "sandbox")

        # 沙箱模式跳过文件检查
        if not is_sandbox and not video.exists():
            raise ValueError("视频文件不存在。")

        now = datetime.now().astimezone()
        task = PublishTask(
            task_id=f"pub-{uuid4().hex[:10]}",
            title=f"发布 · {target.title[:20]}",
            status=TaskStatus.RUNNING,
            progress=10,
            created_at=now,
            updated_at=now,
            video_path=str(video),
            target=target,
            publish_status=PublishStatus.UPLOADING,
            provider_name=str(publisher.capabilities().get("provider_name", "")),
            source_pipeline_run_id=source_pipeline_run_id,
            stage="正在上传",
            is_mock=bool(publisher.capabilities().get("mode") == "sandbox"),
        )
        self._save(task, on_progress)
        try:
            result = publisher.publish(str(video), target)
            self._save(result, on_progress)
            return result
        except Exception as exc:
            failed = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "publish_status": PublishStatus.FAILED,
                    "stage": "发布失败",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": str(exc),
                }
            )
            self._save(failed, on_progress)
            return failed

    def multi_platform_publish(
        self,
        *,
        video_path: str,
        targets: list[PublishTarget],
        source_pipeline_run_id: str | None = None,
    ) -> list[PublishTask]:
        """多平台批量发布。"""
        tasks: list[PublishTask] = []
        for target in targets:
            task = self.publish(
                video_path=video_path,
                target=target,
                source_pipeline_run_id=source_pipeline_run_id,
            )
            tasks.append(task)
        return tasks

    def list_tasks(self) -> list[PublishTask]:
        return [
            t
            for t in self.repository.list_tasks()
            if isinstance(t, PublishTask)
        ]

    def get_task(self, task_id: str) -> PublishTask | None:
        task = self.repository.get_task(task_id)
        return task if isinstance(task, PublishTask) else None

    def _save(
        self,
        task: PublishTask,
        on_progress: Callable[[PublishTask], None] | None,
    ) -> None:
        self.repository.save_task(task)
        if on_progress is not None:
            try:
                on_progress(task)
            except Exception:
                pass
