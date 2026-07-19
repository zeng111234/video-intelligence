"""文案改写服务。

协调 CopywritingEngine 适配器与 TaskRepository，
支持单次改写、批量改写、变体生成。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable
from uuid import uuid4

from src.contracts import CopywritingEngine, TaskRepository
from src.models import CopywritingTask, TaskKind, TaskStatus


class CopywritingService:
    def __init__(
        self,
        repository: TaskRepository,
        engine: CopywritingEngine,
    ) -> None:
        self.repository = repository
        self.engine = engine

    def capabilities(self) -> dict[str, str | bool | int]:
        return self.engine.capabilities()

    def rewrite(
        self,
        *,
        source_text: str,
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
        variant_count: int = 1,
        source_task_id: str | None = None,
        source_revision_id: str | None = None,
        on_progress: Callable[[CopywritingTask], None] | None = None,
    ) -> CopywritingTask:
        """创建文案改写任务并同步执行。"""
        if not source_text.strip():
            raise ValueError("源文案不能为空。")
        cap = self.engine.capabilities()
        max_input = int(cap.get("max_input_chars", 5000))
        if len(source_text) > max_input:
            raise ValueError(f"源文案超过最大长度限制（{max_input}字符）。")
        max_variants = int(cap.get("max_variants", 3))
        variant_count = max(1, min(variant_count, max_variants))

        now = datetime.now().astimezone()
        task = CopywritingTask(
            task_id=f"copy-{uuid4().hex[:10]}",
            title=f"文案改写 · {source_text[:20]}...",
            status=TaskStatus.RUNNING,
            progress=10,
            created_at=now,
            updated_at=now,
            source_text=source_text,
            style_prompt=style_prompt,
            target_length=target_length,
            tone=tone,
            provider_name=str(cap.get("provider_name", "unknown")),
            model_name=str(cap.get("model", "")),
            source_task_id=source_task_id,
            source_revision_id=source_revision_id,
            stage="正在改写",
            is_mock=bool(cap.get("mode") == "sandbox"),
        )
        self._save(task, on_progress)
        try:
            results = self.engine.rewrite(
                source_text,
                style_prompt=style_prompt,
                target_length=target_length,
                tone=tone,
                variant_count=variant_count,
            )
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "改写完成",
                    "updated_at": datetime.now().astimezone(),
                    "result_text": results[0] if results else None,
                    "result_variants": results,
                }
            )
            self._save(task, on_progress)
            return task
        except Exception as exc:
            task = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "stage": "改写失败",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": str(exc),
                }
            )
            self._save(task, on_progress)
            return task

    def batch_rewrite(
        self,
        *,
        source_texts: list[str],
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
    ) -> list[CopywritingTask]:
        """批量文案改写。"""
        tasks: list[CopywritingTask] = []
        for text in source_texts:
            task = self.rewrite(
                source_text=text,
                style_prompt=style_prompt,
                target_length=target_length,
                tone=tone,
            )
            tasks.append(task)
        return tasks

    def list_tasks(self) -> list[CopywritingTask]:
        return [
            t
            for t in self.repository.list_tasks()
            if isinstance(t, CopywritingTask)
        ]

    def get_task(self, task_id: str) -> CopywritingTask | None:
        task = self.repository.get_task(task_id)
        return task if isinstance(task, CopywritingTask) else None

    def _save(
        self,
        task: CopywritingTask,
        on_progress: Callable[[CopywritingTask], None] | None,
    ) -> None:
        self.repository.save_task(task)
        if on_progress is not None:
            try:
                on_progress(task)
            except Exception:
                pass
