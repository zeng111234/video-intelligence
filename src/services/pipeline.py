"""端到端流水线编排服务。

串联：关键词搜索 → 候选选择 → 文案改写 → 数字人生成 → 视频剪辑 → 多平台发布。
支持单条执行和批量任务。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from src.contracts import TaskRepository
from src.models import (
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
    PipelineStepResult,
    PublishPlatform,
    PublishTarget,
    TaskStatus,
)


class PipelineService:
    """端到端视频生产流水线。"""

    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

    # -- 流水线生命周期 --

    def create_run(
        self,
        *,
        keyword: str,
        config: dict[str, Any] | None = None,
    ) -> PipelineRun:
        """创建新的流水线执行记录。"""
        now = datetime.now().astimezone()
        run = PipelineRun(
            keyword=keyword,
            status=PipelineRunStatus.PENDING,
            created_at=now,
            updated_at=now,
            config=config or {},
        )
        self.repository.save_pipeline_run(run)
        return run

    def update_stage(
        self,
        run: PipelineRun,
        stage: PipelineStage,
        status: TaskStatus,
        *,
        task_id: str | None = None,
        outputs: dict[str, str] | None = None,
        error_message: str | None = None,
    ) -> PipelineRun:
        """更新流水线某阶段状态。"""
        now = datetime.now().astimezone()
        step = PipelineStepResult(
            stage=stage,
            status=status,
            task_id=task_id,
            started_at=now,
            finished_at=now if status in {
                TaskStatus.SUCCEEDED, TaskStatus.FAILED
            } else None,
            error_message=error_message,
            outputs=outputs or {},
        )
        # 更新或追加步骤
        existing = [s for s in run.stages if s.stage != stage]
        existing.append(step)

        # 确定流水线整体状态
        pipeline_status = run.status
        if status == TaskStatus.FAILED:
            pipeline_status = PipelineRunStatus.FAILED
        elif status == TaskStatus.RUNNING:
            pipeline_status = PipelineRunStatus.RUNNING
        elif status == TaskStatus.SUCCEEDED and run.status in {
            PipelineRunStatus.PENDING, PipelineRunStatus.RUNNING
        }:
            pipeline_status = PipelineRunStatus.RUNNING

        # 更新关联的任务 ID
        task_id_field = {
            PipelineStage.KEYWORD_SEARCH: None,
            PipelineStage.COPYWRITING: "copywriting_task_id",
            PipelineStage.AVATAR_GENERATION: "avatar_task_id",
            PipelineStage.VIDEO_EDITING: "edit_task_id",
            PipelineStage.PUBLISHING: "publish_task_ids",
        }

        update_kwargs: dict[str, Any] = {
            "stages": existing,
            "current_stage": stage,
            "status": pipeline_status,
            "updated_at": now,
            "error_message": error_message,
        }
        field = task_id_field.get(stage)
        if field and task_id:
            if field == "publish_task_ids":
                current_ids = list(run.publish_task_ids)
                current_ids.append(task_id)
                update_kwargs[field] = current_ids
            else:
                update_kwargs[field] = task_id

        updated = run.model_copy(update=update_kwargs)
        self.repository.save_pipeline_run(updated)
        return updated

    def complete_run(
        self,
        run: PipelineRun,
        *,
        success: bool = True,
        error_message: str | None = None,
    ) -> PipelineRun:
        """标记流水线完成。"""
        now = datetime.now().astimezone()
        updated = run.model_copy(
            update={
                "status": PipelineRunStatus.SUCCEEDED if success else PipelineRunStatus.FAILED,
                "finished_at": now,
                "updated_at": now,
                "error_message": error_message,
            }
        )
        self.repository.save_pipeline_run(updated)
        return updated

    def get_run(self, run_id: str) -> PipelineRun | None:
        return self.repository.get_pipeline_run(run_id)

    def list_runs(self, limit: int = 20) -> list[PipelineRun]:
        return self.repository.list_pipeline_runs(limit)

    # -- 构建发布目标 --

    @staticmethod
    def build_publish_targets(
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        platforms: list[PublishPlatform] | None = None,
    ) -> list[PublishTarget]:
        """为多个平台构建发布目标。"""
        if platforms is None:
            platforms = [
                PublishPlatform.DOUYIN,
                PublishPlatform.KUAISHOU,
                PublishPlatform.WECHAT_CHANNELS,
            ]
        return [
            PublishTarget(
                platform=p,
                title=title,
                description=description,
                tags=tags or [],
            )
            for p in platforms
        ]
