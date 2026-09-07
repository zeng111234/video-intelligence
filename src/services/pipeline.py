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
    PipelineEvent,
    PipelineStage,
    CopywritingTask,
    MediaResolutionStatus,
    PipelineStepResult,
    Platform,
    PublishPlatform,
    PublishStatus,
    PublishTask,
    PublishTarget,
    TaskStatus,
    TranscriptionTask,
    VideoEditConfig,
)
from src.services.media_resolution import MediaResolutionError, ResolvedMedia
from src.services.publish_metadata import publish_draft_fingerprint, validated_publish_draft
from src.services.transcription import MAX_PROVIDER_MEDIA_BYTES, TranscriptionError
from src.services.video_source import DirectVideo

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

    @staticmethod
    def _event(
        run: PipelineRun,
        *,
        action: str,
        message: str,
        stage: PipelineStage | None = None,
        details: dict[str, str] | None = None,
    ) -> PipelineRun:
        """追加不可变事件，不以阶段快照覆盖历史执行事实。"""
        event = PipelineEvent(
            action=action,
            status=run.status,
            stage=stage,
            message=message,
            details=details or {},
        )
        return run.model_copy(update={"events": [*run.events, event]})

    def create_run(
        self,
        *,
        keyword: str,
        config: dict[str, Any] | None = None,
    ) -> PipelineRun:
        """创建新的流水线执行记录。"""
        run = self.build_run(keyword=keyword, config=config)
        self.repository.save_pipeline_run(run)
        return run

    def build_run(
        self,
        *,
        keyword: str,
        config: dict[str, Any] | None = None,
    ) -> PipelineRun:
        """Build a run without persistence for an enclosing atomic transaction."""
        now = datetime.now().astimezone()
        run = PipelineRun(
            keyword=keyword,
            status=PipelineRunStatus.PENDING,
            created_at=now,
            updated_at=now,
            config=config or {},
        )
        run = self._event(
            run,
            action="created",
            message="已创建生产流水线，等待开始执行。",
        )
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
        updated = self._event(
            updated,
            action="stage_updated",
            stage=stage,
            message=f"{stage.value} 状态更新为 {status.value}。",
            details={
                "task_id": task_id or "",
                "error_message": error_message or "",
            },
        )
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
        updated = self._event(
            updated,
            action="completed",
            stage=updated.current_stage,
            message="流水线已完成。" if success else "流水线已失败，可在核对原因后重试。",
            details={"error_message": error_message or ""},
        )
        self.repository.save_pipeline_run(updated)
        return updated

    def review_candidate_script(
        self,
        *,
        run_id: str,
        approved: bool,
        reviewer: str,
        note: str = "",
        approved_text: str = "",
        creative_plan: dict[str, Any] | None = None,
    ) -> PipelineRun:
        """记录人工审核决策，并推进已批准的任务。

        自动关键词任务会由后台 worker 继续数字人、剪辑和发布包；旧版手动
        任务仍只更新阶段，避免改变已有工作流的授权边界。
        """
        run = self.get_run(run_id)
        if run is None:
            raise ValueError("流水线不存在。")
        if run.status != PipelineRunStatus.PAUSED or run.current_stage != PipelineStage.HUMAN_REVIEW:
            raise ValueError("当前流水线不处于等待人工审核状态。")
        if (
            approved
            and str(run.config.get("workflow") or "").startswith("production_batch_")
            and not approved_text.strip()
        ):
            raise ValueError("文案确认必须提交非空的最终口播文本。")

        now = datetime.now().astimezone()
        existing = next((item for item in run.stages if item.stage == PipelineStage.HUMAN_REVIEW), None)
        if existing is None:
            raise ValueError("流水线缺少待审核文案阶段。")
        review_outputs = {
            **existing.outputs,
            "reviewer": reviewer.strip(),
            "review_decision": "approved" if approved else "rework_required",
            "review_note": note.strip(),
            "approved_text": approved_text.strip(),
        }
        reviewed_step = existing.model_copy(
            update={
                "status": TaskStatus.SUCCEEDED if approved else TaskStatus.FAILED,
                "finished_at": now,
                "error_message": None if approved else (note.strip() or "人工审核要求返工。"),
                "outputs": review_outputs,
            }
        )
        stages = [reviewed_step if item.stage == PipelineStage.HUMAN_REVIEW else item for item in run.stages]
        updated = run.model_copy(
            update={
                "stages": stages,
                "status": PipelineRunStatus.PENDING if approved else PipelineRunStatus.PAUSED,
                "current_stage": PipelineStage.AVATAR_GENERATION if approved else PipelineStage.HUMAN_REVIEW,
                "updated_at": now,
                "config": {
                    **run.config,
                    "approved_script_text": approved_text.strip()
                    or str(run.config.get("approved_script_text") or ""),
                    "script_reviewed": bool(approved),
                    "review_stage": "script",
                    "creative_plan": (
                        {"status": "approved", **creative_plan}
                        if approved and creative_plan is not None
                        else run.config.get("creative_plan")
                    ),
                },
                "error_message": None if approved else (note.strip() or "人工审核要求返工。"),
            }
        )
        automatic_workflow = run.config.get("workflow") in {
            "keyword_auto_candidate",
            "production_batch_candidate",
            "production_batch_share_link",
            "production_batch_brief",
            "production_batch_script",
            "guided_candidate",
            "guided_share_link",
        }
        updated = self._event(
            updated,
            action="review_approved" if approved else "review_rejected",
            stage=PipelineStage.HUMAN_REVIEW,
            message=(
                (
                    "文案审核已通过；后台将继续数字人、剪辑和人工发布包。"
                    if automatic_workflow
                    else "文案审核已通过；下一步需显式配置并提交数字人任务。"
                )
                if approved
                else "文案审核要求返工；可使用重试重新执行媒体到文案链路。"
            ),
            details={"reviewer": reviewer.strip(), "note": note.strip()},
        )
        self.repository.save_pipeline_run(updated)
        return updated

    def start_keyword_auto_run(
        self,
        *,
        keyword: str,
        candidate_count: int,
        profile: dict[str, Any],
        rights_holder: str,
        publish_platforms: list[str],
    ) -> PipelineRun:
        """创建由后台 worker 执行的关键词生产母任务，不在请求内调用供应商。"""
        run = self.create_run(
            keyword=keyword,
            config={
                "workflow": "keyword_auto_master",
                "candidate_count": candidate_count,
                "profile": profile,
                "rights_holder": rights_holder,
                "publish_platforms": publish_platforms,
                "rights_confirmed": True,
            },
        )
        run = self._event(
            run,
            action="queued",
            stage=PipelineStage.KEYWORD_SEARCH,
            message="关键词生产任务已入队，等待后台检索与候选选择。",
        )
        self.repository.save_pipeline_run(run)
        return run

    def start_guided_run(
        self,
        *,
        source_type: str,
        keyword: str,
        profile: dict[str, Any],
        rights_holder: str,
        publish_enabled: bool,
        publish_platforms: list[str],
        candidate_id: str | None = None,
        share_text: str | None = None,
        use_paid_fallback: bool = False,
        idempotency_key: str = "",
    ) -> PipelineRun:
        """创建面向单个客户操作的可恢复生产任务。

        该方法只入队，不在 API 请求内调用解析、转写或生成供应商；由 worker
        依次执行，避免重复点击造成重复扣费或重复生成。
        """
        if source_type not in {"candidate", "share_link"}:
            raise ValueError("不支持的流水线来源。")
        if idempotency_key:
            for existing in self.repository.list_pipeline_runs(limit=100):
                if existing.config.get("guided_idempotency_key") == idempotency_key:
                    return existing
        workflow = "guided_candidate" if source_type == "candidate" else "guided_share_link"
        run = self.create_run(
            keyword=keyword[:200],
            config={
                "workflow": workflow,
                "source": source_type,
                "profile": profile,
                "rights_holder": rights_holder.strip(),
                "rights_confirmed": True,
                "publish_enabled": publish_enabled,
                "publish_platforms": list(publish_platforms) if publish_enabled else [],
                "share_text": share_text or "",
                "use_paid_fallback": use_paid_fallback,
                "guided_idempotency_key": idempotency_key,
                "candidate_request": {
                    "rights_confirmed": True,
                    "rights_holder": rights_holder.strip(),
                    "model_name": "large-v3-turbo",
                    "target_length": 300,
                    "tone": "casual",
                    "target_audience": str(profile.get("target_audience") or ""),
                    "style_prompt": str(profile.get("script_style") or ""),
                    "variant_count": 2,
                },
            },
        )
        if candidate_id:
            run = run.model_copy(update={"candidate_video_id": candidate_id})
        run = self._event(
            run,
            action="guided_queued",
            stage=PipelineStage.TRANSCRIPTION,
            message="已加入生产队列，将依次完成文案提取、改写、确认和数字人成片。",
            details={"source_type": source_type},
        )
        self.repository.save_pipeline_run(run)
        return run

    def get_run(self, run_id: str) -> PipelineRun | None:
        return self.repository.get_pipeline_run(run_id)

    def list_runs(self, limit: int = 20) -> list[PipelineRun]:
        return self.repository.list_pipeline_runs(limit)

    def _preserved_media_for_transcription_retry(
        self,
        *,
        run: PipelineRun,
        candidate,
        idempotency_key: str,
    ) -> ResolvedMedia | None:
        """Reuse media saved before a cloud-ASR submission failure."""

        source_task_id = str(
            run.config.get("retry_source_transcription_task_id") or ""
        ).strip()
        if not source_task_id:
            return None

        source_task = self.repository.get_task(source_task_id)
        source_path = (
            Path(str(source_task.outputs.get("source_media_path") or ""))
            if isinstance(source_task, TranscriptionTask)
            else None
        )
        previous = self.repository.find_media_resolution_by_idempotency_key(
            idempotency_key
        )
        valid_source = (
            isinstance(source_task, TranscriptionTask)
            and source_task.status == TaskStatus.FAILED
            and not source_task.provider_job_id
            and source_task.provider_status == "failed"
            and source_task.candidate_id == candidate.video_id
            and source_path is not None
            and source_path.is_file()
        )
        valid_resolution = (
            previous is not None
            and previous.status == MediaResolutionStatus.SUCCEEDED
            and previous.candidate_id == candidate.video_id
        )
        if not valid_source or not valid_resolution:
            raise MediaResolutionError(
                "已保存的素材无法安全恢复，请返回素材后重新确认。"
            )

        media_size = source_path.stat().st_size
        if media_size <= 0 or media_size > MAX_PROVIDER_MEDIA_BYTES:
            raise MediaResolutionError(
                "已保存的素材为空或超出转写大小限制，请返回素材后重新确认。"
            )
        return ResolvedMedia(
            attempt=previous,
            video=DirectVideo(
                name=source_task.media_name,
                media_type=source_task.media_type,
                content=source_path.read_bytes(),
            ),
        )

    def execute_candidate_script_pipeline(
        self,
        *,
        candidate_id: str,
        rights_confirmed: bool,
        rights_holder: str,
        idempotency_key: str,
        model_name: str = "large-v3-turbo",
        hotwords: str | None = None,
        target_length: int = 240,
        tone: str = "casual",
        target_audience: str = "",
        style_prompt: str = "",
        variant_count: int = 1,
        existing_run: PipelineRun | None = None,
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

        request_config = {
            "rights_confirmed": rights_confirmed,
            "rights_holder": rights_holder.strip(),
            "model_name": model_name,
            "hotwords": hotwords or "",
            "target_length": target_length,
            "tone": tone,
            "target_audience": target_audience,
            "style_prompt": style_prompt,
            "variant_count": variant_count,
        }
        if existing_run is None:
            run = self.create_run(
                keyword=candidate.title[:200],
                config={
                    "source": "crawler_candidate",
                    "candidate_id": candidate.video_id,
                    "platform": candidate.platform.value,
                    "platform_item_id": candidate.platform_item_id or "",
                    "rights_holder": rights_holder.strip(),
                    "flow": "media_to_asr_to_copy_review",
                    "candidate_request": request_config,
                },
            )
        else:
            run = existing_run.model_copy(
                update={
                    "config": {**existing_run.config, "candidate_request": request_config},
                    "finished_at": None,
                    "error_message": None,
                }
            )
        run = run.model_copy(
            update={
                "status": PipelineRunStatus.RUNNING,
                "candidate_video_id": candidate.video_id,
                "updated_at": datetime.now().astimezone(),
            }
        )
        if existing_run is not None:
            run = self._event(
                run,
                action="retry_started",
                message="已从失败或返工状态重试媒体到文案链路。",
                stage=PipelineStage.MEDIA_RESOLUTION,
            )
        self.repository.save_pipeline_run(run)

        try:
            run = self.update_stage(
                run,
                PipelineStage.MEDIA_RESOLUTION,
                TaskStatus.RUNNING,
                outputs={"candidate_id": candidate.video_id},
            )
            resolved = self._preserved_media_for_transcription_retry(
                run=run,
                candidate=candidate,
                idempotency_key=idempotency_key,
            ) or self.media_resolution_service.resolve_video(
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
                    "source": (
                        "preserved_transcription_media"
                        if run.config.get("retry_source_transcription_task_id")
                        else "provider_or_direct_url"
                    ),
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
            next_config = dict(run.config)
            next_config.pop("retry_source_transcription_task_id", None)
            next_config["transcription_task_id"] = transcription.task_id
            run = run.model_copy(
                update={
                    "config": next_config
                }
            )
            self.repository.save_pipeline_run(run)
        except TranscriptionError as exc:
            transcription_task = (
                self.repository.get_task(exc.task_id) if exc.task_id else None
            )
            if isinstance(transcription_task, TranscriptionTask):
                next_config = dict(run.config)
                next_config.pop("retry_source_transcription_task_id", None)
                next_config["transcription_task_id"] = transcription_task.task_id
                run = run.model_copy(update={"config": next_config})
                self.repository.save_pipeline_run(run)
            if (
                isinstance(transcription_task, TranscriptionTask)
                and transcription_task.status == TaskStatus.OUTCOME_UNKNOWN
            ):
                safe_message = (
                    "云端转写请求的结果暂时无法确认，素材和任务记录已保留；"
                    "系统不会自动重复提交或重复扣费。"
                )
                run = self.update_stage(
                    run,
                    PipelineStage.TRANSCRIPTION,
                    TaskStatus.OUTCOME_UNKNOWN,
                    task_id=transcription_task.task_id,
                    outputs={
                        "task_id": transcription_task.task_id,
                        "provider_status": "outcome_unknown",
                        "review_state": "provider_result_unconfirmed",
                    },
                    error_message=safe_message,
                )
                run = run.model_copy(
                    update={
                        "status": PipelineRunStatus.PAUSED,
                        "current_stage": PipelineStage.TRANSCRIPTION,
                        "error_message": safe_message,
                        "config": {
                            **run.config,
                            "transcription_task_id": transcription_task.task_id,
                            "outcome_unknown": True,
                            "recovery_blocked": True,
                            "recovery_reason": "provider_result_unconfirmed",
                        },
                    }
                )
                run = self._event(
                    run,
                    action="transcription_outcome_unknown",
                    stage=PipelineStage.TRANSCRIPTION,
                    message=safe_message,
                    details={"task_id": transcription_task.task_id},
                )
                self.repository.save_pipeline_run(run)
                return run
            run = self.update_stage(
                run,
                PipelineStage.TRANSCRIPTION,
                TaskStatus.FAILED,
                task_id=exc.task_id,
                error_message=exc.user_message,
            )
            return self.complete_run(run, success=False, error_message=exc.user_message)

        if str(run.config.get("workflow") or "") == "production_batch_candidate":
            return self.pause_for_transcript_review(
                run=run,
                transcription=transcription,
            )
        return self.create_copywriting_review(
            run=run,
            transcription=transcription,
            platform=candidate.platform,
            target_audience=target_audience,
            style_prompt=style_prompt,
            target_length=target_length,
            tone=tone,
            variant_count=variant_count,
        )

    def create_copywriting_review(
        self,
        *,
        run: PipelineRun,
        transcription: TranscriptionTask,
        platform: Platform,
        target_audience: str = "",
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "casual",
        variant_count: int = 2,
        source_text_override: str = "",
        user_request: str = "",
    ) -> PipelineRun:
        """将一条真实转写改写为待客户确认的口播稿。"""
        source_text = source_text_override.strip() or self._transcription_text(transcription)
        try:
            run = self.update_stage(run, PipelineStage.COPYWRITING, TaskStatus.RUNNING)
            rewrite_goal = (
                "仅在 AI 质检和人工确认转写后，基于真实转写整理数字人口播稿。"
                "将长转写压缩成一份不超过约60秒的最终口播稿，内容不足时自然缩短；保留可确认事实，"
                "用原文事实重新组织前3秒钩子、信息顺序和句式，改成自然口语；"
                "删除口头禅、重复句和噪声，避免连续照搬原文表达；"
                "没有强钩子时只能用问题、反差或痛点重组，不得补写未在转写中出现的事实、"
                "数据、案例或效果承诺。结尾补一个基于原文的自然行动引导或下一步建议；"
                "原文没有明确行动时使用中性总结，不得凭空添加购买、私信、关注、收益或效果承诺。"
                "只输出一份可人工审核的纯口播文案，不要多个版本或 Markdown。"
            )
            if user_request.strip():
                rewrite_goal += (
                    "客户本次改写要求（只调整表达、结构或语气，不能改变原文事实）："
                    + user_request.strip()[:500]
                )
            copy_task = self.copywriting_service.rewrite(
                source_text=source_text,
                platform=platform.value,
                target_audience=target_audience,
                style_prompt=style_prompt or "短视频口播，清晰直接，重组表达但不新增事实",
                target_length=target_length,
                tone=tone,
                rewrite_goal=rewrite_goal,
                variant_count=variant_count,
                source_task_id=transcription.task_id,
            )
            if copy_task.status == TaskStatus.FAILED:
                raise RuntimeError(copy_task.error_message or "文案改写失败。")
            return self.pause_for_copy_review(
                run=run,
                copy_task=copy_task,
                instruction="人工审核后再进入数字人、剪辑或发布。",
            )
        except Exception as exc:
            error_msg = str(exc)
            run = self.update_stage(
                run,
                PipelineStage.COPYWRITING,
                TaskStatus.FAILED,
                error_message=error_msg,
            )
            return self.complete_run(run, success=False, error_message=error_msg)

    def _manual_script_ai_audit(
        self,
        *,
        run: PipelineRun,
        script_text: str,
        target_audience: str,
        style_prompt: str,
    ) -> dict[str, Any] | None:
        """Audit manual batch copy once, but never turn an audit outage into a fake pass."""
        if (
            run.config.get("source") != "production_batch"
            or str(run.config.get("automation_mode") or "manual").casefold() == "auto"
        ):
            return None
        audit_method = getattr(self.copywriting_service, "audit_spoken_script", None)
        if not callable(audit_method):
            return {
                "status": "unavailable",
                "approved": False,
                "summary": "当前 AI 文案服务未提供审核能力，请人工核对后再制作。",
                "issues": [],
            }
        try:
            result = audit_method(
                script_text=script_text,
                target_audience=target_audience,
                style_prompt=style_prompt,
            )
        except Exception as exc:
            logger.warning("流水线 %s 的手动文案 AI 审核未完成: %s", run.run_id, exc)
            return {
                "status": "unavailable",
                "approved": False,
                "summary": f"AI 文案审核未完成：{str(exc)[:120]}。请人工核对后再制作。",
                "issues": [],
            }
        if not isinstance(result, dict):
            return {
                "status": "unavailable",
                "approved": False,
                "summary": "AI 文案审核未返回有效结果，请人工核对后再制作。",
                "issues": [],
            }
        return result

    @staticmethod
    def classify_spoken_material(
        transcription: TranscriptionTask,
    ) -> tuple[str, str]:
        """Keep visual-only media out of copy generation without guessing facts."""
        text = PipelineService._transcription_text(transcription)
        meaningful_characters = [character for character in text if character.isalnum()]
        duration = float(transcription.duration_seconds or 0)
        too_sparse = duration >= 20 and len(meaningful_characters) / duration < 0.2
        too_short = (
            len(meaningful_characters) < 8
            if duration > 0
            else len(meaningful_characters) < 2
        )
        if too_short or too_sparse:
            return (
                "visual_only",
                "没有识别到足够可核验的口播；素材保留作画面参考，不会据此编造文案。",
            )
        return (
            "rewriteable",
            "已识别到可用内容；AI 会保留事实并重组为自然口播。",
        )

    def pause_for_transcript_review(
        self,
        *,
        run: PipelineRun,
        transcription: TranscriptionTask,
    ) -> PipelineRun:
        """候选和链接必须先确认真实转写，再允许调用文案改写。"""
        now = datetime.now().astimezone()
        material_status, material_message = self.classify_spoken_material(
            transcription
        )
        run = run.model_copy(
            update={
                "config": {
                    **run.config,
                    "spoken_material_status": material_status,
                    "spoken_material_message": material_message,
                },
                "updated_at": now,
            }
        )
        if material_status == "visual_only":
            run = self._event(
                run,
                action="spoken_material_unavailable",
                stage=PipelineStage.TRANSCRIPTION,
                message=material_message,
            )
            self.repository.save_pipeline_run(run)
            return self.complete_run(
                run,
                success=False,
                error_message=material_message,
            )
        run = self.update_stage(
            run,
            PipelineStage.HUMAN_REVIEW,
            TaskStatus.RUNNING,
            task_id=transcription.task_id,
            outputs={
                "transcription_task_id": transcription.task_id,
                "review_stage": "transcript",
                "instruction": "请先核对原转写，再生成改写文案。",
            },
        )
        paused = run.model_copy(
            update={
                "status": PipelineRunStatus.PAUSED,
                "current_stage": PipelineStage.HUMAN_REVIEW,
                "updated_at": now,
                "config": {
                    **run.config,
                    "review_stage": "transcript",
                    "transcription_task_id": transcription.task_id,
                    "transcript_reviewed": False,
                },
            }
        )
        paused = self._event(
            paused,
            action="transcript_review_required",
            stage=PipelineStage.HUMAN_REVIEW,
            message="原转写等待人工确认；尚未调用文案改写服务。",
        )
        self.repository.save_pipeline_run(paused)
        return paused

    def review_transcript(
        self,
        *,
        run_id: str,
        reviewer: str,
        approved_text: str,
        note: str = "",
        rewrite_request: str = "",
    ) -> PipelineRun:
        """记录转写确认并生成待审核改写稿。"""
        text = approved_text.strip()
        if not text:
            raise ValueError("转写确认必须提交非空的最终文本。")
        run = self.get_run(run_id)
        if run is None:
            raise ValueError("流水线不存在。")
        previously_approved = str(
            run.config.get("approved_transcript_text") or ""
        ).strip()
        if bool(run.config.get("transcript_reviewed")):
            if previously_approved != text:
                raise ValueError("转写已使用另一版文本确认，不能重复提交不同内容。")
            if (
                run.status == PipelineRunStatus.PAUSED
                and run.current_stage == PipelineStage.HUMAN_REVIEW
                and run.config.get("review_stage") == "script"
                and run.copywriting_task_id
            ):
                return run
            if bool(run.config.get("transcript_review_in_progress")):
                raise ValueError("转写已确认，改写稿正在生成，请勿重复提交。")
        if (
            run.status != PipelineRunStatus.PAUSED
            or run.current_stage != PipelineStage.HUMAN_REVIEW
            or run.config.get("review_stage") != "transcript"
        ):
            raise ValueError("当前流水线不处于转写确认阶段。")
        task = self.repository.get_task(
            str(run.config.get("transcription_task_id") or "")
        )
        if not isinstance(task, TranscriptionTask):
            raise ValueError("找不到待确认的转写任务。")
        claimed_at = datetime.now().astimezone()
        reviewed = run.model_copy(
            update={
                "status": PipelineRunStatus.RUNNING,
                "current_stage": PipelineStage.COPYWRITING,
                "updated_at": claimed_at,
                "config": {
                    **run.config,
                    "transcript_reviewed": True,
                    "transcript_review_in_progress": True,
                    "transcript_review_claimed_at": claimed_at.isoformat(),
                    "approved_transcript_text": text,
                    "transcript_reviewer": reviewer.strip(),
                    "transcript_review_note": note.strip(),
                },
            }
        )
        reviewed = self._event(
            reviewed,
            action="transcript_review_approved",
            stage=PipelineStage.HUMAN_REVIEW,
            message="转写确认已通过，开始生成待审核口播稿。",
            details={"reviewer": reviewer.strip(), "note": note.strip()},
        )
        if not self.repository.claim_pipeline_run_transition(
            expected_run=run,
            claimed_run=reviewed,
        ):
            current = self.get_run(run_id)
            if current is not None:
                current_text = str(
                    current.config.get("approved_transcript_text") or ""
                ).strip()
                if (
                    current_text == text
                    and current.status == PipelineRunStatus.PAUSED
                    and current.current_stage == PipelineStage.HUMAN_REVIEW
                    and current.config.get("review_stage") == "script"
                    and current.copywriting_task_id
                ):
                    return current
                if (
                    current_text == text
                    and bool(
                        current.config.get("transcript_review_in_progress")
                    )
                ):
                    raise ValueError(
                        "转写已确认，改写稿正在生成，请勿重复提交。"
                    )
            raise ValueError("转写审核状态已变化，请刷新后重试。")
        profile = dict(reviewed.config.get("profile") or {})
        request = dict(reviewed.config.get("candidate_request") or {})
        return self.create_copywriting_review(
            run=reviewed,
            transcription=task,
            platform=Platform.DOUYIN,
            target_audience=str(
                request.get("target_audience")
                or profile.get("target_audience")
                or ""
            ),
            style_prompt=str(
                request.get("style_prompt")
                or profile.get("script_style")
                or ""
            ),
            target_length=int(request.get("target_length") or 240),
            tone=str(request.get("tone") or "casual"),
            variant_count=1,
            source_text_override=text,
            user_request=rewrite_request,
        )

    def pause_for_copy_review(
        self,
        *,
        run: PipelineRun,
        copy_task: CopywritingTask,
        instruction: str = "人工审核后再进入数字人、剪辑或发布。",
    ) -> PipelineRun:
        """把已生成或人工提供的文案统一送入人工审核闸门。"""
        now = datetime.now().astimezone()
        profile = dict(run.config.get("profile") or {})
        audit = self._manual_script_ai_audit(
            run=run,
            script_text=str(copy_task.result_text or ""),
            target_audience=str(profile.get("target_audience") or ""),
            style_prompt=str(profile.get("script_style") or ""),
        )
        if audit is not None:
            run = run.model_copy(
                update={
                    "config": {
                        **run.config,
                        "script_ai_audit": audit,
                    }
                }
            )
        run = self.update_stage(
            run,
            PipelineStage.COPYWRITING,
            TaskStatus.SUCCEEDED,
            task_id=copy_task.task_id,
            outputs={
                "task_id": copy_task.task_id,
                "variant_count": str(len(copy_task.result_variants or [copy_task.result_text or ""])),
                "review_state": "awaiting_human_approval",
                "ai_audit_status": str((audit or {}).get("status") or "not_required"),
            },
        )
        run = self.update_stage(
            run,
            PipelineStage.HUMAN_REVIEW,
            TaskStatus.RUNNING,
            task_id=copy_task.task_id,
            outputs={"copywriting_task_id": copy_task.task_id, "instruction": instruction},
        )
        paused = run.model_copy(
            update={
                "status": PipelineRunStatus.PAUSED,
                "current_stage": PipelineStage.HUMAN_REVIEW,
                "updated_at": now,
                "config": {
                    **run.config,
                    "review_stage": "script",
                    "script_reviewed": False,
                    "transcript_review_in_progress": False,
                },
            }
        )
        paused = self._event(
            paused,
            action="copy_review_required",
            stage=PipelineStage.HUMAN_REVIEW,
            message=instruction,
        )
        self.repository.save_pipeline_run(paused)
        return paused

    def confirm_output_review(self, *, run_id: str, reviewer: str, note: str = "") -> PipelineRun:
        """记录成片复核；该操作不会创建或提交发布任务。"""
        run = self.get_run(run_id)
        if run is None:
            raise ValueError("流水线不存在。")
        if run.status != PipelineRunStatus.PAUSED or run.current_stage != PipelineStage.PUBLISHING:
            raise ValueError("当前流水线尚未生成可复核成片。")
        now = datetime.now().astimezone()
        updated = run.model_copy(
            update={
                "updated_at": now,
                "config": {
                    **run.config,
                    "output_reviewed": True,
                    "output_reviewer": reviewer.strip(),
                    "output_review_note": note.strip(),
                },
            }
        )
        updated = self._event(
            updated,
            action="output_review_approved",
            stage=PipelineStage.PUBLISHING,
            message="成片复核已通过，已标记为待发布；未创建发布任务。",
            details={"reviewer": reviewer.strip(), "note": note.strip()},
        )
        self.repository.save_pipeline_run(updated)
        return updated

    def confirm_publish_draft(
        self,
        *,
        run_id: str,
        reviewer: str,
        title: str,
        description: str,
        tags: list[str],
        note: str = "",
    ) -> PipelineRun:
        """保存人工确认过的发布文案；该操作不创建发布任务。"""
        run = self.get_run(run_id)
        if run is None:
            raise ValueError("流水线不存在。")
        if (
            run.status not in {PipelineRunStatus.PAUSED, PipelineRunStatus.PARTIAL}
            or run.current_stage != PipelineStage.PUBLISHING
        ):
            raise ValueError("当前流水线尚未生成可确认发布的成片。")
        if not bool(run.config.get("output_reviewed")):
            raise ValueError("请先完成人工成片复核。")
        draft = validated_publish_draft(
            title=title,
            description=description,
            tags=tags,
        )
        now = datetime.now().astimezone()
        existing_tasks: list[PublishTask] = []
        if run.publish_task_ids:
            for task_id in run.publish_task_ids:
                task = self.repository.get_task(task_id)
                if not isinstance(task, PublishTask):
                    raise ValueError("发布任务记录不完整，请先刷新发布中心。")
                if (
                    task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.SUCCEEDED, TaskStatus.OUTCOME_UNKNOWN}
                    or task.final_publish_started_at
                    or task.outputs.get("final_publish_clicked") == "true"
                ):
                    raise ValueError("发布已经开始或结果待核对，不能再修改发布信息。")
                existing_tasks.append(task)
        elif bool(run.config.get("publish_confirmed")):
            raise ValueError("发布正在准备中，请稍后刷新再修改。")

        for task in existing_tasks:
            target = task.target.model_copy(
                update={
                    "title": draft["title"],
                    "description": draft["description"],
                    "tags": draft["tags"],
                }
            )
            update: dict[str, Any] = {
                "title": f"发布 · {draft['title'][:20]}",
                "target": target,
                "updated_at": now,
                "error_message": None,
            }
            if not task.is_mock:
                update.update(
                    {
                        "status": TaskStatus.PAUSED,
                        "publish_status": PublishStatus.MANUAL_READY,
                        "progress": 60,
                        "stage": "发布信息已保存，等待人工核对",
                        "action_required": "发布信息已更新；需要发布时再重新准备官方发布页，最终发布仍由你确认。",
                    }
                )
            self.repository.save_task(task.model_copy(update=update))

        updated = run.model_copy(
            update={
                "updated_at": now,
                "config": {
                    **run.config,
                    "publish_draft": draft,
                    "publish_draft_approved": True,
                    "publish_draft_fingerprint": publish_draft_fingerprint(draft),
                    "publish_draft_reviewer": reviewer.strip(),
                    "publish_draft_note": note.strip(),
                    "publish_draft_approved_at": now.isoformat(),
                },
            }
        )
        updated = self._event(
            updated,
            action="publish_draft_approved",
            stage=PipelineStage.PUBLISHING,
            message=(
                "发布信息已保存；尚未重新准备官方发布页，也未执行最终发布。"
                if existing_tasks
                else "发布标题、描述和标签已确认；尚未创建发布任务。"
            ),
            details={"reviewer": reviewer.strip(), "note": note.strip()},
        )
        self.repository.save_pipeline_run(updated)
        return updated

    def retry_candidate_script_pipeline(
        self,
        *,
        run_id: str,
        idempotency_key: str,
    ) -> PipelineRun:
        """以同一个运行 ID 重试失败或被要求返工的候选文案链路。"""
        run = self.get_run(run_id)
        if run is None:
            raise ValueError("流水线不存在。")
        if run.status not in {PipelineRunStatus.FAILED, PipelineRunStatus.PAUSED}:
            raise ValueError("只有失败或等待人工处理的流水线可以重试。")
        if run.config.get("source") not in {"crawler_candidate", "production_batch"} or not run.candidate_video_id:
            raise ValueError("当前仅支持重试由候选创建的媒体到文案流水线。")
        request = run.config.get("candidate_request")
        if not isinstance(request, dict):
            raise ValueError("该历史流水线未保存可重试参数；请从候选重新创建任务。")
        if not bool(request.get("rights_confirmed")) or not str(request.get("rights_holder") or "").strip():
            raise ValueError("重试前需要重新确认媒体处理授权信息。")
        return self.execute_candidate_script_pipeline(
            candidate_id=run.candidate_video_id,
            rights_confirmed=True,
            rights_holder=str(request["rights_holder"]),
            idempotency_key=idempotency_key,
            model_name=str(request.get("model_name") or "large-v3-turbo"),
            hotwords=str(request.get("hotwords") or "") or None,
            target_length=int(request.get("target_length") or 300),
            tone=str(request.get("tone") or "casual"),
            target_audience=str(request.get("target_audience") or ""),
            style_prompt=str(request.get("style_prompt") or ""),
            variant_count=int(request.get("variant_count") or 2),
            existing_run=run,
        )

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
        target_specs: list[dict[str, Any]] | None = None,
    ) -> list[PublishTarget]:
        """为多个平台构建发布目标。"""
        if target_specs is not None:
            return [
                PublishTarget(
                    platform=PublishPlatform(str(spec["platform"])),
                    account_id=str(spec.get("account_id") or "") or None,
                    title=title,
                    description=description,
                    tags=tags or [],
                    auto_publish_authorized=bool(
                        spec.get("auto_publish_authorized", False)
                    ),
                    use_prepared_page=bool(spec.get("use_prepared_page", False)),
                )
                for spec in target_specs
            ]
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
