"""文案改写服务。

协调 CopywritingEngine 适配器与 TaskRepository，
支持单次改写、批量改写、变体生成。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from src.contracts import CopywritingEngine, TaskRepository
from src.models import CopywritingTask, Platform, TaskStatus


SUPPORTED_COPYWRITING_PLATFORMS = {
    Platform.DOUYIN,
    Platform.XIAOHONGSHU,
    Platform.WECHAT_CHANNELS,
}


class CopywritingService:
    def __init__(
        self,
        repository: TaskRepository,
        engine: CopywritingEngine,
    ) -> None:
        self.repository = repository
        self.engine = engine

    def capabilities(self) -> dict[str, Any]:
        return self.engine.capabilities()

    def rewrite(
        self,
        *,
        source_text: str,
        platform: str = "douyin",
        target_audience: str = "",
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
        platform_enum = self._validate_platform(platform)
        cap = self.engine.capabilities()
        max_input = int(cap.get("max_input_chars", 5000))
        if len(source_text) > max_input:
            raise ValueError(f"源文案超过最大长度限制（{max_input}字符）。")
        max_variants = int(cap.get("max_variants", 3))
        variant_count = max(1, min(variant_count, max_variants))
        target_length = max(50, min(target_length, 800))

        now = datetime.now().astimezone()
        task = CopywritingTask(
            task_id=f"copy-{uuid4().hex[:10]}",
            title=f"文案改写 · {source_text[:20]}...",
            status=TaskStatus.RUNNING,
            progress=10,
            created_at=now,
            updated_at=now,
            creation_mode="rewrite",
            source_text=source_text,
            platform=platform_enum,
            target_audience=target_audience,
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
                platform=platform_enum.value,
                target_audience=target_audience,
                style_prompt=style_prompt,
                target_length=target_length,
                tone=tone,
                variant_count=variant_count,
            )
            if not results:
                raise RuntimeError("LLM 未返回有效内容。")
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "改写完成",
                    "updated_at": datetime.now().astimezone(),
                    "token_usage": self._last_usage(),
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

    def generate(
        self,
        *,
        content_brief: str,
        platform: str = "douyin",
        target_audience: str = "",
        selling_points: str = "",
        call_to_action: str = "",
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
        variant_count: int = 1,
        on_progress: Callable[[CopywritingTask], None] | None = None,
    ) -> CopywritingTask:
        """创建从需求生成文案任务并同步执行。"""
        if not content_brief.strip():
            raise ValueError("内容概要不能为空。")
        platform_enum = self._validate_platform(platform)
        cap = self.engine.capabilities()
        max_input = int(cap.get("max_input_chars", 5000))
        if len(content_brief) > max_input:
            raise ValueError(f"内容概要超过最大长度限制（{max_input}字符）。")
        max_variants = int(cap.get("max_variants", 3))
        variant_count = max(1, min(variant_count, max_variants))
        target_length = max(50, min(target_length, 800))

        now = datetime.now().astimezone()
        task = CopywritingTask(
            task_id=f"copy-{uuid4().hex[:10]}",
            title=f"文案生成 · {content_brief[:20]}...",
            status=TaskStatus.RUNNING,
            progress=10,
            created_at=now,
            updated_at=now,
            creation_mode="generate",
            content_brief=content_brief,
            platform=platform_enum,
            target_audience=target_audience,
            selling_points=selling_points,
            call_to_action=call_to_action,
            style_prompt=style_prompt,
            target_length=target_length,
            tone=tone,
            provider_name=str(cap.get("provider_name", "unknown")),
            model_name=str(cap.get("model", "")),
            stage="正在生成",
            is_mock=bool(cap.get("mode") == "sandbox"),
        )
        self._save(task, on_progress)
        try:
            results = self.engine.generate(
                content_brief=content_brief,
                platform=platform_enum.value,
                target_audience=target_audience,
                selling_points=selling_points,
                call_to_action=call_to_action,
                style_prompt=style_prompt,
                target_length=target_length,
                tone=tone,
                variant_count=variant_count,
            )
            if not results:
                raise RuntimeError("LLM 未返回有效内容。")
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "生成完成",
                    "updated_at": datetime.now().astimezone(),
                    "token_usage": self._last_usage(),
                    "result_text": results[0],
                    "result_variants": results,
                }
            )
            self._save(task, on_progress)
            return task
        except Exception as exc:
            task = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "stage": "生成失败",
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
            t for t in self.repository.list_tasks() if isinstance(t, CopywritingTask)
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

    @staticmethod
    def _validate_platform(platform: str) -> Platform:
        try:
            platform_enum = Platform(platform)
        except ValueError as exc:
            raise ValueError("文案平台只支持 douyin、xiaohongshu、wechat_channels。") from exc
        if platform_enum not in SUPPORTED_COPYWRITING_PLATFORMS:
            raise ValueError("文案平台只支持 douyin、xiaohongshu、wechat_channels。")
        return platform_enum

    def _last_usage(self) -> dict[str, int]:
        usage = getattr(self.engine, "last_usage", {})
        return dict(usage) if isinstance(usage, dict) else {}
