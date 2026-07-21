"""端到端流水线编排服务。

串联：关键词搜索 → 候选选择 → 文案改写 → 视频剪辑 → 多平台发布。
支持单条执行和批量任务。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from src.contracts import TaskRepository
from src.models import (
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
    PipelineStepResult,
    Platform,
    PublishPlatform,
    PublishTarget,
    TaskStatus,
    TranscriptionTask,
    VideoEditConfig,
)
from src.services.media_resolution import MediaResolutionError
from src.services.transcription import MAX_PROVIDER_MEDIA_BYTES, TranscriptionError

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
        media_resolution_service=None,
        transcription_service=None,
    ) -> None:
        self.repository = repository
        self.commercial_search_service = commercial_search_service
        self.copywriting_service = copywriting_service
        self.video_editing_service = video_editing_service
        self.publish_service = publish_service
        self.media_resolution_service = media_resolution_service
        self.transcription_service = transcription_service

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
            finished_at=now
            if status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}
            else None,
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
            PipelineRunStatus.PENDING,
            PipelineRunStatus.RUNNING,
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
                "status": PipelineRunStatus.SUCCEEDED
                if success
                else PipelineRunStatus.FAILED,
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

    def execute_candidate_script_pipeline(
        self,
        *,
        candidate_id: str,
        rights_confirmed: bool,
        rights_holder: str,
        idempotency_key: str,
        model_name: str = "large-v3-turbo",
        hotwords: str | None = None,
        target_length: int = 300,
        tone: str = "casual",
        target_audience: str = "",
        style_prompt: str = "",
        variant_count: int = 2,
    ) -> PipelineRun:
        """从单条候选执行：补媒体 -> 转写 -> 文案改写 -> 等待人工审核。

        该流程不自动进入数字人、剪辑或发布。真实转写和改写结果必须由用户
        校对/确认后，才能作为下游生产输入。
        """

        if self.media_resolution_service is None or self.transcription_service is None:
            raise RuntimeError("流水线未配置媒体解析或转写服务。")
        if not rights_confirmed:
            raise RuntimeError("必须确认拥有媒体处理权。")

        getter = getattr(self.repository, "get_candidate", None)
        if getter is None:
            raise RuntimeError("当前仓库不支持按候选创建生产流水线。")
        candidate = getter(candidate_id)
        if candidate is None:
            raise ValueError("候选不存在。")
        if candidate.platform != Platform.DOUYIN:
            raise RuntimeError(
                "当前自动化试运行仅支持抖音候选；"
                "小红书和视频号暂停媒体解析与转写，未发起付费请求。"
            )

        run = self.create_run(
            keyword=candidate.title[:200],
            config={
                "source": "crawler_candidate",
                "candidate_id": candidate.video_id,
                "platform": candidate.platform.value,
                "platform_item_id": candidate.platform_item_id or "",
                "rights_holder": rights_holder.strip(),
                "flow": "media_to_asr_to_copy_review",
            },
        )
        run = run.model_copy(
            update={
                "status": PipelineRunStatus.RUNNING,
                "candidate_video_id": candidate.video_id,
                "updated_at": datetime.now().astimezone(),
            }
        )
        self.repository.save_pipeline_run(run)

        try:
            run = self.update_stage(
                run,
                PipelineStage.MEDIA_RESOLUTION,
                TaskStatus.RUNNING,
                outputs={"candidate_id": candidate.video_id},
            )
            resolved = self.media_resolution_service.resolve_video(
                candidate,
                idempotency_key=idempotency_key,
            )
            run = self.update_stage(
                run,
                PipelineStage.MEDIA_RESOLUTION,
                TaskStatus.SUCCEEDED,
                task_id=resolved.attempt.resolution_id,
                outputs={
                    "resolution_id": resolved.attempt.resolution_id,
                    "provider": resolved.attempt.provider,
                    "billable_units": str(resolved.attempt.billable_units or 0.0),
                    "source": "provider_or_direct_url",
                },
            )
        except MediaResolutionError as exc:
            run = self.update_stage(
                run,
                PipelineStage.MEDIA_RESOLUTION,
                TaskStatus.FAILED,
                error_message=exc.user_message,
            )
            return self.complete_run(run, success=False, error_message=exc.user_message)

        try:
            run = self.update_stage(
                run,
                PipelineStage.TRANSCRIPTION,
                TaskStatus.RUNNING,
                task_id=resolved.attempt.resolution_id,
            )
            transcription = self.transcription_service.create_task(
                media_name=resolved.video.name,
                media_type=resolved.video.media_type,
                media_bytes=resolved.video.content,
                rights_confirmed=rights_confirmed,
                rights_holder=rights_holder,
                candidate_id=candidate.video_id,
                model_name=model_name,
                hotwords=hotwords or None,
                max_media_bytes=MAX_PROVIDER_MEDIA_BYTES,
            )
            self.media_resolution_service.attach_task(resolved.attempt, transcription)
            run = self.update_stage(
                run,
                PipelineStage.TRANSCRIPTION,
                TaskStatus.SUCCEEDED,
                task_id=transcription.task_id,
                outputs={
                    "task_id": transcription.task_id,
                    "duration_seconds": str(transcription.duration_seconds or ""),
                    "segment_count": str(len(transcription.segments or [])),
                    "review_state": "unapproved_asr",
                },
            )
        except TranscriptionError as exc:
            run = self.update_stage(
                run,
                PipelineStage.TRANSCRIPTION,
                TaskStatus.FAILED,
                error_message=exc.user_message,
            )
            return self.complete_run(run, success=False, error_message=exc.user_message)

        source_text = self._transcription_text(transcription)
        try:
            run = self.update_stage(run, PipelineStage.COPYWRITING, TaskStatus.RUNNING)
            rewrite_goal = (
                "基于真实 ASR 转写提炼爆款视频口播结构并改写。"
                "保留可确认事实和表达逻辑，删除口头禅、重复句和噪声；"
                "不得补写未在转写中出现的事实、数据、案例或效果承诺。"
                "输出可人工审核的口播文案，不要 Markdown。"
            )
            copy_task = self.copywriting_service.rewrite(
                source_text=source_text,
                platform=candidate.platform.value,
                target_audience=target_audience,
                style_prompt=style_prompt or "短视频口播，清晰直接，保留原视频爆款表达结构",
                target_length=target_length,
                tone=tone,
                rewrite_goal=rewrite_goal,
                variant_count=variant_count,
                source_task_id=transcription.task_id,
            )
            if copy_task.status == TaskStatus.FAILED:
                raise RuntimeError(copy_task.error_message or "文案改写失败。")
            run = self.update_stage(
                run,
                PipelineStage.COPYWRITING,
                TaskStatus.SUCCEEDED,
                task_id=copy_task.task_id,
                outputs={
                    "task_id": copy_task.task_id,
                    "source_task_id": transcription.task_id,
                    "variant_count": str(len(copy_task.result_variants)),
                    "review_state": "awaiting_human_approval",
                },
            )
            run = self.update_stage(
                run,
                PipelineStage.HUMAN_REVIEW,
                TaskStatus.RUNNING,
                task_id=copy_task.task_id,
                outputs={
                    "copywriting_task_id": copy_task.task_id,
                    "instruction": "人工审核后再进入数字人、剪辑或发布。",
                },
            )
            paused = run.model_copy(
                update={
                    "status": PipelineRunStatus.PAUSED,
                    "copywriting_task_id": copy_task.task_id,
                    "current_stage": PipelineStage.HUMAN_REVIEW,
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self.repository.save_pipeline_run(paused)
            return paused
        except Exception as exc:
            error_msg = str(exc)
            run = self.update_stage(
                run,
                PipelineStage.COPYWRITING,
                TaskStatus.FAILED,
                error_message=error_msg,
            )
            return self.complete_run(run, success=False, error_message=error_msg)

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
            run = self.update_stage(run, PipelineStage.COPYWRITING, TaskStatus.RUNNING)

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

            # 使用文案作为字幕文本进行剪辑；必须由上游提供授权源视频。
            source_video = self._resolve_source_video(run.config)

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
            logger.info("流水线 %s → 阶段 3 完成，任务: %s", run_id, edit_task_id)
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
            run = self.update_stage(run, PipelineStage.PUBLISHING, TaskStatus.RUNNING)

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
                error_msg = (
                    f"所有平台发布失败 ({len(failed_tasks)}/{len(publish_tasks)})"
                )
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
    def _resolve_source_video(config: dict[str, Any]) -> str:
        """获取已授权源视频路径。

        流水线不能生成占位视频来制造“已生产”结果。调用方必须传入
        source_video_path，通常来自用户上传、数字人生成结果或人工确认的成片。
        """

        source_video_path = str(config.get("source_video_path") or "").strip()
        if not source_video_path:
            raise RuntimeError(
                "没有已授权源视频，不能进入视频剪辑和发布；请先上传视频、完成数字人生成，或在流水线配置中提供 source_video_path。"
            )

        source_path = Path(source_video_path)
        if not source_path.is_file():
            raise RuntimeError(f"源视频不存在或不可读取: {source_video_path}")
        return str(source_path)

    @staticmethod
    def _transcription_text(task: TranscriptionTask) -> str:
        lines = [" ".join(segment.text.split()).strip() for segment in task.segments]
        text = "\n".join(line for line in lines if line)
        if not text.strip():
            raise RuntimeError("转写结果为空，不能生成文案。")
        return text

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
