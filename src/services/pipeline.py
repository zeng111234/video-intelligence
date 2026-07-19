"""端到端流水线编排服务。

串联：关键词搜索 → 候选选择 → 文案改写 → 视频剪辑 → 多平台发布。
支持单条执行和批量任务。
"""

from __future__ import annotations

import logging
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
    VideoEditConfig,
)

logger = logging.getLogger(__name__)


class PipelineService:
    """端到端视频生产流水线。"""

    def __init__(
        self,
        repository: TaskRepository,
        commercial_search_service,
        copywriting_service,
        video_editing_service,
        publish_service,
    ) -> None:
        self.repository = repository
        self.commercial_search_service = commercial_search_service
        self.copywriting_service = copywriting_service
        self.video_editing_service = video_editing_service
        self.publish_service = publish_service

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

    # -- 端到端执行 --

    def execute_run(
        self,
        *,
        run_id: str,
        keyword: str,
        platforms: list[PublishPlatform],
        edit_config: VideoEditConfig | None = None,
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
    ) -> PipelineRun:
        """执行端到端流水线。

        阶段 1: 关键词搜索 → 获取候选素材
        阶段 2: 文案改写 → 生成口播文案
        阶段 3: 视频剪辑 → 生成最终视频
        阶段 4: 多平台发布 → 分发到各平台
        """
        run = self.get_run(run_id)
        if run is None:
            raise ValueError(f"流水线 {run_id} 不存在。")

        logger.info("流水线 %s 开始执行，关键词: %s", run_id, keyword)

        # ========================
        # 阶段 1: 关键词搜索
        # ========================
        search_outputs: dict[str, str] = {}
        try:
            logger.info("流水线 %s → 阶段 1: 关键词搜索", run_id)
            run = self.update_stage(
                run, PipelineStage.KEYWORD_SEARCH, TaskStatus.RUNNING
            )

            search_batch = self.commercial_search_service.execute(
                keyword=keyword,
            )
            search_outputs = {
                "batch_id": search_batch.batch_id,
                "status": search_batch.status.value,
                "keyword": keyword,
            }

            run = self.update_stage(
                run,
                PipelineStage.KEYWORD_SEARCH,
                TaskStatus.SUCCEEDED,
                task_id=search_batch.batch_id,
                outputs=search_outputs,
            )
            logger.info(
                "流水线 %s → 阶段 1 完成，批次: %s", run_id, search_batch.batch_id
            )
        except Exception as exc:
            error_msg = f"关键词搜索失败: {exc}"
            logger.error("流水线 %s → %s", run_id, error_msg)
            run = self.update_stage(
                run,
                PipelineStage.KEYWORD_SEARCH,
                TaskStatus.FAILED,
                error_message=error_msg,
            )
            return self.complete_run(run, success=False, error_message=error_msg)

        # ========================
        # 阶段 2: 文案改写
        # ========================
        copywriting_task_id = None
        result_text = ""
        try:
            logger.info("流水线 %s → 阶段 2: 文案改写", run_id)
            run = self.update_stage(
                run, PipelineStage.COPYWRITING, TaskStatus.RUNNING
            )

            # 使用关键词生成源文案
            source_text = style_prompt if style_prompt else f"{keyword}相关短视频文案"

            copy_task = self.copywriting_service.rewrite(
                source_text=source_text,
                style_prompt=style_prompt,
                target_length=target_length,
                tone=tone,
                variant_count=1,
            )

            if copy_task.status == TaskStatus.FAILED:
                raise RuntimeError(copy_task.error_message or "文案改写任务失败")

            copywriting_task_id = copy_task.task_id
            result_text = copy_task.result_text or ""

            run = self.update_stage(
                run,
                PipelineStage.COPYWRITING,
                TaskStatus.SUCCEEDED,
                task_id=copywriting_task_id,
                outputs={
                    "task_id": copy_task.task_id,
                    "result_text": result_text[:200],
                },
            )
            logger.info(
                "流水线 %s → 阶段 2 完成，任务: %s", run_id, copywriting_task_id
            )
        except Exception as exc:
            error_msg = f"文案改写失败: {exc}"
            logger.error("流水线 %s → %s", run_id, error_msg)
            run = self.update_stage(
                run,
                PipelineStage.COPYWRITING,
                TaskStatus.FAILED,
                error_message=error_msg,
            )
            return self.complete_run(run, success=False, error_message=error_msg)

        # ========================
        # 阶段 3: 视频剪辑
        # ========================
        edit_task_id = None
        result_video_path = ""
        try:
            logger.info("流水线 %s → 阶段 3: 视频剪辑", run_id)
            run = self.update_stage(
                run, PipelineStage.VIDEO_EDITING, TaskStatus.RUNNING
            )

            # 使用文案作为字幕文本进行剪辑
            # 在沙箱模式下，SandboxVideoEditor 会生成占位文件
            source_video = self._resolve_source_video(keyword)

            edit_task = self.video_editing_service.edit_video(
                source_video_path=source_video,
                edit_config=edit_config or VideoEditConfig(),
                subtitle_text=result_text if result_text.strip() else None,
            )

            if edit_task.status == TaskStatus.FAILED:
                raise RuntimeError(edit_task.error_message or "视频剪辑任务失败")

            edit_task_id = edit_task.task_id
            result_video_path = edit_task.result_path or source_video

            run = self.update_stage(
                run,
                PipelineStage.VIDEO_EDITING,
                TaskStatus.SUCCEEDED,
                task_id=edit_task_id,
                outputs={
                    "task_id": edit_task.task_id,
                    "result_path": result_video_path,
                },
            )
            logger.info(
                "流水线 %s → 阶段 3 完成，任务: %s", run_id, edit_task_id
            )
        except Exception as exc:
            error_msg = f"视频剪辑失败: {exc}"
            logger.error("流水线 %s → %s", run_id, error_msg)
            run = self.update_stage(
                run,
                PipelineStage.VIDEO_EDITING,
                TaskStatus.FAILED,
                error_message=error_msg,
            )
            return self.complete_run(run, success=False, error_message=error_msg)

        # ========================
        # 阶段 4: 多平台发布
        # ========================
        try:
            logger.info("流水线 %s → 阶段 4: 多平台发布", run_id)
            run = self.update_stage(
                run, PipelineStage.PUBLISHING, TaskStatus.RUNNING
            )

            # 构建发布目标
            targets = self.build_publish_targets(
                title=keyword,
                description=result_text[:200] if result_text else "",
                tags=[keyword],
                platforms=platforms,
            )

            publish_tasks = self.publish_service.multi_platform_publish(
                video_path=result_video_path,
                targets=targets,
                source_pipeline_run_id=run_id,
            )

            failed_tasks = [t for t in publish_tasks if t.status == TaskStatus.FAILED]
            if len(failed_tasks) == len(publish_tasks):
                # 全部失败
                error_msg = f"所有平台发布失败 ({len(failed_tasks)}/{len(publish_tasks)})"
                raise RuntimeError(error_msg)

            # 记录发布任务 ID
            for pt in publish_tasks:
                run = self.update_stage(
                    run,
                    PipelineStage.PUBLISHING,
                    TaskStatus.SUCCEEDED,
                    task_id=pt.task_id,
                    outputs={
                        "platform": pt.target.platform.value,
                        "url": pt.platform_url or "",
                    },
                )

            logger.info(
                "流水线 %s → 阶段 4 完成，发布 %d 个平台",
                run_id,
                len(publish_tasks),
            )
        except Exception as exc:
            error_msg = f"多平台发布失败: {exc}"
            logger.error("流水线 %s → %s", run_id, error_msg)
            run = self.update_stage(
                run,
                PipelineStage.PUBLISHING,
                TaskStatus.FAILED,
                error_message=error_msg,
            )
            return self.complete_run(run, success=False, error_message=error_msg)

        # ========================
        # 标记流水线成功
        # ========================
        run = self.complete_run(run, success=True)
        logger.info("流水线 %s 全部完成 ✓", run_id)
        return run

    # -- 辅助方法 --

    @staticmethod
    def _resolve_source_video(keyword: str) -> str:
        """获取源视频路径。

        在沙箱模式下生成一个临时占位文件用于演示，
        实际生产环境中应从候选素材中选取或由数字人生成。
        """
        from tempfile import NamedTemporaryFile

        data_dir = Path("data/video_edits")
        data_dir.mkdir(parents=True, exist_ok=True)

        placeholder = data_dir / "sandbox_source.mp4"
        if not placeholder.exists():
            # 创建最小占位 MP4 文件（仅用于沙箱演示，不实际播放）
            placeholder.write_bytes(b"\x00" * 128)
        return str(placeholder)

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
