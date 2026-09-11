"""本地持久化流水线 worker：扫描 SQLite 中可继续的运行记录。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlparse
from uuid import uuid4

from src.adapters.avatar import AvatarProviderError
from src.adapters.douyin_parser import DouyinParserError
from src.adapters.publishers.sandbox import SandboxPublisher
from src.models import (
    AvatarSubmitRequest,
    AvatarTask,
    CopywritingTask,
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
    Platform,
    PublishPlatform,
    PublishTask,
    TaskStatus,
    TranscriptionTask,
)
from src.services.avatar import AvatarServiceUnavailableError
from src.services.production import candidate_xiaohongshu_search_keyword
from src.services.publish_metadata import suggested_publish_draft
from src.services.style_presets import PRESET_SEMANTIC_ADAPTIVE

logger = logging.getLogger(__name__)


def _has_xiaohongshu_xsec_token(value: object) -> bool:
    """Return whether a saved XHS entry is richer than a bare note URL."""
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").casefold()
    if host != "xiaohongshu.com" and not host.endswith(".xiaohongshu.com"):
        return False
    return "xsec_token" in {
        key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    }


class PipelineWorker:
    """单进程 worker；运行状态写入 SQLite，重启后会重新扫描未完成任务。"""

    def __init__(
        self,
        *,
        repository,
        pipeline_service,
        commercial_search_service,
        avatar_service,
        video_editing_service,
        publish_service,
        template_service,
        video_editor_workflow_service=None,
        production_service=None,
        douyin_link_transcription_service=None,
        interval_seconds: float = 2.0,
        can_process: Callable[[], bool] | None = None,
        processing_block_reason: Callable[[], str | None] | None = None,
    ) -> None:
        self.repository = repository
        self.pipeline_service = pipeline_service
        self.commercial_search_service = commercial_search_service
        self.avatar_service = avatar_service
        self.video_editing_service = video_editing_service
        self.publish_service = publish_service
        self.template_service = template_service
        self.video_editor_workflow_service = video_editor_workflow_service
        self.production_service = production_service
        self.douyin_link_transcription_service = douyin_link_transcription_service
        self.interval_seconds = interval_seconds
        self.can_process = can_process or (lambda: True)
        self.processing_block_reason = processing_block_reason or (lambda: None)
        self._task: asyncio.Task | None = None
        # 该 worker 由依赖缓存复用，但 TestClient 和服务重启可能使用新的事件循环。
        # 因此事件对象必须在 start 时按当前循环创建，不能在构造函数中固定绑定。
        self._stopping: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="pipeline-worker")

    async def stop(self) -> None:
        stopping = self._stopping
        if stopping is not None:
            stopping.set()
        if self._task:
            await self._task
            self._task = None
        self._stopping = None

    async def _loop(self) -> None:
        stopping = self._stopping
        if stopping is None:
            return
        while not stopping.is_set():
            try:
                await asyncio.to_thread(self.tick_once)
            except Exception:
                logger.exception("流水线 worker 扫描失败")
            try:
                await asyncio.wait_for(stopping.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass

    def tick_once(self) -> None:
        # 正式桌面端重启后，持久任务会早于客户重新登录被扫描。没有公司
        # 会话时必须保持原状态，避免把可恢复任务误判失败或越过发布授权。
        if not self.can_process():
            self._record_processing_wait(self.processing_block_reason())
            return
        for run in self.repository.list_active_pipeline_runs():
            workflow = str(run.config.get("workflow") or "")
            try:
                run = self._clear_processing_wait(run)
                if workflow.startswith("production_batch_"):
                    run = self._recover_interrupted_run(run)
                if (
                    workflow == "keyword_auto_master"
                    and run.status == PipelineRunStatus.PENDING
                ):
                    self._run_keyword_master(run)
                elif workflow == "keyword_auto_candidate":
                    self._run_candidate(run)
                elif workflow == "guided_candidate":
                    self._run_candidate(run)
                elif workflow == "guided_share_link":
                    self._run_guided_share_link(run)
                elif workflow in {
                    "production_batch_candidate",
                    "production_batch_share_link",
                    "production_batch_brief",
                    "production_batch_script",
                }:
                    self._run_production_batch(run)
            except Exception as exc:
                logger.exception("流水线 %s 执行异常", run.run_id)
                self._fail(
                    run, run.current_stage or PipelineStage.KEYWORD_SEARCH, str(exc)
                )
            finally:
                if (
                    workflow.startswith("production_batch_")
                    and self.production_service is not None
                ):
                    batch_id = str(run.config.get("batch_id") or "")
                    self.production_service.sync_batch(batch_id)
                    self.production_service.maybe_auto_review_batch(
                        batch_id,
                        pipeline_service=self.pipeline_service,
                    )

    def _record_processing_wait(self, reason: str | None) -> None:
        """Expose a blocked desktop queue once, without replaying it."""
        if not reason:
            return
        for run in self.repository.list_active_pipeline_runs():
            if run.status != PipelineRunStatus.PENDING:
                continue
            if run.config.get("processing_wait_reason") == reason:
                continue
            waiting = run.model_copy(
                update={
                    "updated_at": datetime.now().astimezone(),
                    "config": {
                        **run.config,
                        "processing_wait_reason": reason,
                    },
                }
            )
            waiting = self.pipeline_service._event(
                waiting,
                action="processing_waiting_for_authorization",
                stage=run.current_stage,
                message=reason,
            )
            self.repository.save_pipeline_run(waiting)

    def _clear_processing_wait(self, run: PipelineRun) -> PipelineRun:
        if "processing_wait_reason" not in run.config:
            return run
        config = dict(run.config)
        config.pop("processing_wait_reason", None)
        resumed = run.model_copy(
            update={"updated_at": datetime.now().astimezone(), "config": config}
        )
        resumed = self.pipeline_service._event(
            resumed,
            action="processing_authorization_restored",
            stage=run.current_stage,
            message="本机登录状态已恢复，队列可继续执行。",
        )
        self.repository.save_pipeline_run(resumed)
        return resumed

    def _recover_interrupted_run(self, run: PipelineRun) -> PipelineRun:
        """Recover only stages whose replay is provably free of duplicate charges."""
        if run.status != PipelineRunStatus.RUNNING:
            return run
        stage = run.current_stage
        if (
            stage == PipelineStage.COPYWRITING
            and bool(run.config.get("transcript_review_in_progress"))
            and datetime.now().astimezone() - run.updated_at < timedelta(minutes=2)
        ):
            # A review API request may legitimately be inside its bounded LLM
            # call while the worker scans. Only stale claims imply a restart.
            return run
        if stage == PipelineStage.AVATAR_GENERATION:
            # The provider task id is persisted and refresh_task only polls it.
            return run
        if stage == PipelineStage.VIDEO_EDITING and run.avatar_task_id:
            resumed = run.model_copy(
                update={
                    "status": PipelineRunStatus.RUNNING,
                    "current_stage": PipelineStage.AVATAR_GENERATION,
                    "updated_at": datetime.now().astimezone(),
                    "error_message": None,
                    "config": {
                        **run.config,
                        "restart_recovery": "resume_local_edit_from_existing_avatar",
                    },
                }
            )
            resumed = self.pipeline_service._event(
                resumed,
                action="restart_edit_resume",
                stage=PipelineStage.VIDEO_EDITING,
                message="服务重启后复用已存在的数字人结果，重新执行本地剪辑。",
            )
            self.repository.save_pipeline_run(resumed)
            return resumed
        if stage == PipelineStage.PUBLISHING:
            persisted_tasks = [
                task
                for task in self.repository.list_tasks()
                if isinstance(task, PublishTask)
                and task.source_pipeline_run_id == run.run_id
            ]
            if persisted_tasks:
                task_ids = list(
                    dict.fromkeys(
                        [
                            *run.publish_task_ids,
                            *(task.task_id for task in persisted_tasks),
                        ]
                    )
                )
                paused = run.model_copy(
                    update={
                        "status": PipelineRunStatus.PAUSED,
                        "current_stage": PipelineStage.PUBLISHING,
                        "publish_task_ids": task_ids,
                        "updated_at": datetime.now().astimezone(),
                        "error_message": "服务重启后已找回发布任务，等待人工或发布中心核对结果。",
                        "config": {
                            **run.config,
                            "recovery_blocked": True,
                            "recovery_reason": "publishing_interrupted",
                        },
                    }
                )
                self.repository.save_pipeline_run(paused)
                return paused
            return self._pause_for_manual_recovery(
                run,
                "发布提交在服务重启时中断，结果无法证明；已停止自动重提，请人工核对平台后台。",
                outcome_unknown=True,
            )
        if stage is None:
            resumed = run.model_copy(
                update={
                    "status": PipelineRunStatus.PENDING,
                    "updated_at": datetime.now().astimezone(),
                    "error_message": None,
                }
            )
            self.repository.save_pipeline_run(resumed)
            return resumed
        if stage == PipelineStage.HUMAN_REVIEW:
            paused = run.model_copy(
                update={
                    "status": PipelineRunStatus.PAUSED,
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self.repository.save_pipeline_run(paused)
            return paused
        return self._pause_for_manual_recovery(
            run,
            "服务重启时该阶段可能已调用付费供应商，无法证明未扣费；已停止自动重提，请人工核对供应商记录。",
            outcome_unknown=stage
            in {
                PipelineStage.MEDIA_RESOLUTION,
                PipelineStage.TRANSCRIPTION,
                PipelineStage.COPYWRITING,
            },
        )

    def _pause_for_manual_recovery(
        self,
        run: PipelineRun,
        message: str,
        *,
        outcome_unknown: bool,
    ) -> PipelineRun:
        paused = run.model_copy(
            update={
                "status": PipelineRunStatus.PAUSED,
                "updated_at": datetime.now().astimezone(),
                "error_message": message,
                "config": {
                    **run.config,
                    "recovery_blocked": True,
                    "recovery_reason": "outcome_unknown"
                    if outcome_unknown
                    else "manual_recovery_required",
                    "outcome_unknown": bool(outcome_unknown),
                },
            }
        )
        paused = self.pipeline_service._event(
            paused,
            action="restart_manual_recovery_required",
            stage=run.current_stage,
            message=message,
        )
        self.repository.save_pipeline_run(paused)
        return paused

    def _run_keyword_master(self, run: PipelineRun) -> None:
        run = self.pipeline_service.update_stage(
            run, PipelineStage.KEYWORD_SEARCH, TaskStatus.RUNNING
        )
        config = run.config
        search = self.commercial_search_service.execute(
            keyword=run.keyword,
            count=min(10, int(config.get("candidate_count") or 1)),
        )
        candidates = [
            item
            for item in self.repository.list_candidates()
            if item.platform == Platform.DOUYIN
            and run.keyword.casefold()
            in {value.casefold() for value in item.matched_by}
            and item.platform_item_id
        ]
        candidates.sort(
            key=lambda item: (
                item.heat.score,
                item.heat.confidence,
                item.metrics.likes or 0,
            ),
            reverse=True,
        )
        selected = candidates[: int(config.get("candidate_count") or 1)]
        if not selected:
            self._fail(
                run,
                PipelineStage.KEYWORD_SEARCH,
                "检索未得到可进入媒体处理的抖音候选。",
            )
            return
        child_ids: list[str] = []
        for candidate in selected:
            child = self.pipeline_service.create_run(
                keyword=candidate.title[:200],
                config={
                    "workflow": "keyword_auto_candidate",
                    "parent_run_id": run.run_id,
                    "profile": config["profile"],
                    "rights_holder": config["rights_holder"],
                    "rights_confirmed": True,
                    "publish_platforms": config.get("publish_platforms", ["douyin"]),
                    "candidate_request": {
                        "rights_confirmed": True,
                        "rights_holder": config["rights_holder"],
                        "model_name": "large-v3-turbo",
                        "target_length": 240,
                        "tone": "casual",
                        "target_audience": str(
                            config["profile"].get("target_audience") or ""
                        ),
                        "style_prompt": str(
                            config["profile"].get("script_style") or ""
                        ),
                        "skill_prompt": "",
                        "variant_count": 1,
                    },
                },
            )
            child = child.model_copy(update={"candidate_video_id": candidate.video_id})
            child = self.pipeline_service._event(
                child,
                action="candidate_selected",
                stage=PipelineStage.MEDIA_RESOLUTION,
                message="已由关键词任务选中，等待媒体到文案链路执行。",
                details={
                    "parent_run_id": run.run_id,
                    "candidate_id": candidate.video_id,
                },
            )
            self.repository.save_pipeline_run(child)
            child_ids.append(child.run_id)
        run = self.pipeline_service.update_stage(
            run,
            PipelineStage.KEYWORD_SEARCH,
            TaskStatus.SUCCEEDED,
            task_id=search.batch_id,
            outputs={
                "search_batch_id": search.batch_id,
                "selected_runs": ",".join(child_ids),
            },
        )
        self.pipeline_service.complete_run(run, success=True)

    def _run_candidate(self, run: PipelineRun) -> None:
        if run.status == PipelineRunStatus.PENDING and run.current_stage is None:
            request = dict(run.config.get("candidate_request") or {})
            self.pipeline_service.execute_candidate_script_pipeline(
                candidate_id=run.candidate_video_id or "",
                rights_confirmed=True,
                rights_holder=str(
                    request.get("rights_holder")
                    or run.config.get("rights_holder")
                    or ""
                ),
                idempotency_key=f"worker-media-{run.run_id}",
                model_name=str(request.get("model_name") or "large-v3-turbo"),
                hotwords=str(request.get("hotwords") or "") or None,
                target_length=int(request.get("target_length") or 240),
                tone=str(request.get("tone") or "casual"),
                target_audience=str(request.get("target_audience") or ""),
                style_prompt=str(request.get("style_prompt") or ""),
                skill_prompt=str(request.get("skill_prompt") or ""),
                variant_count=1,
                existing_run=run,
            )
            return
        if (
            run.status == PipelineRunStatus.PENDING
            and run.current_stage == PipelineStage.AVATAR_GENERATION
        ):
            self._submit_avatar(run)
            return
        if (
            run.status == PipelineRunStatus.RUNNING
            and run.current_stage == PipelineStage.AVATAR_GENERATION
        ):
            self._poll_avatar_and_continue(run)
            return
        if (
            run.status == PipelineRunStatus.PAUSED
            and run.current_stage == PipelineStage.PUBLISHING
        ):
            self._reconcile_publish(run)

    def _run_guided_share_link(self, run: PipelineRun) -> None:
        """从客户明确提供的平台分享链接开始，不经过关键词发现。"""
        share_text = str(run.config.get("share_text") or "")
        candidate_platform = str(run.config.get("candidate_platform") or "").lower()
        if run.status == PipelineRunStatus.PENDING and run.current_stage is None:
            if self.douyin_link_transcription_service is None:
                self._fail(run, PipelineStage.TRANSCRIPTION, "分享链接转写服务未配置。")
                return
            request = dict(run.config.get("candidate_request") or {})
            search_keyword = (
                str(
                    request.get("search_keyword")
                    or run.config.get("search_keyword")
                    or ""
                ).strip()
                or None
            )
            if candidate_platform == Platform.XIAOHONGSHU.value:
                candidate_id = str(
                    getattr(run, "candidate_video_id", "")
                    or run.config.get("candidate_id")
                    or ""
                ).strip()
                candidate = (
                    self.repository.get_candidate(candidate_id)
                    if candidate_id
                    else None
                )
                current_source_url = str(getattr(candidate, "source_url", "") or "")
                # A customer may prepare/open the material after an older production
                # batch was created.  Always upgrade that stale bare URL to the richer
                # signed entry now stored on the candidate; never downgrade the reverse.
                if (
                    _has_xiaohongshu_xsec_token(current_source_url)
                    and not _has_xiaohongshu_xsec_token(share_text)
                ):
                    share_text = current_source_url
                if not search_keyword:
                    search_keyword = candidate_xiaohongshu_search_keyword(candidate)
            if not share_text:
                self._fail(run, PipelineStage.TRANSCRIPTION, "缺少平台分享链接。")
                return
            run = self.pipeline_service.update_stage(
                run, PipelineStage.TRANSCRIPTION, TaskStatus.RUNNING
            )
            try:
                if bool(run.config.get("use_paid_fallback")):
                    if candidate_platform == Platform.XIAOHONGSHU.value:
                        raise DouyinParserError(
                            "小红书仅使用已登录浏览器处理，不能使用付费链接回退。"
                        )
                    preview = self.douyin_link_transcription_service.preview(share_text)
                    if not preview.work_id:
                        raise DouyinParserError("链接缺少可用于授权回退的作品 ID。")
                    transcription = self.douyin_link_transcription_service.transcribe_oneapi_fallback(
                        share_text=share_text,
                        work_id=preview.work_id,
                        rights_holder=str(
                            request.get("rights_holder")
                            or run.config.get("rights_holder")
                            or ""
                        ),
                        rights_confirmed=True,
                        idempotency_key=f"worker-link-{run.run_id}",
                        model_name=str(request.get("model_name") or "large-v3-turbo"),
                    )
                else:
                    transcription = (
                        self.douyin_link_transcription_service.transcribe_experimental(
                            share_text=share_text,
                            rights_holder=str(
                                request.get("rights_holder")
                                or run.config.get("rights_holder")
                                or ""
                            ),
                            rights_confirmed=True,
                            model_name=str(
                                request.get("model_name") or "large-v3-turbo"
                            ),
                            candidate_id=run.candidate_video_id,
                            search_keyword=search_keyword,
                        )
                    )
            except DouyinParserError as exc:
                task_id = str(
                    getattr(exc, "task_id", "")
                    or getattr(getattr(exc, "__cause__", None), "task_id", "")
                    or ""
                ).strip()
                task = self.repository.get_task(task_id) if task_id else None
                if (
                    isinstance(task, TranscriptionTask)
                    and task.status == TaskStatus.OUTCOME_UNKNOWN
                ):
                    message = task.error_message or (
                        "云端转写结果暂时无法确认，素材和原任务编号已保留；"
                        "请重新连接恢复，系统不会创建第二个本地任务。"
                    )
                    paused = self.pipeline_service.update_stage(
                        run,
                        PipelineStage.TRANSCRIPTION,
                        TaskStatus.OUTCOME_UNKNOWN,
                        task_id=task.task_id,
                        outputs={
                            "task_id": task.task_id,
                            "provider_status": "outcome_unknown",
                            "review_state": "provider_result_unconfirmed",
                        },
                        error_message=message,
                    )
                    paused = paused.model_copy(
                        update={
                            "status": PipelineRunStatus.PAUSED,
                            "current_stage": PipelineStage.TRANSCRIPTION,
                            "error_message": message,
                            "config": {
                                **paused.config,
                                "transcription_task_id": task.task_id,
                                "outcome_unknown": True,
                                "recovery_blocked": True,
                                "recovery_reason": "provider_result_unconfirmed",
                            },
                        }
                    )
                    self.repository.save_pipeline_run(paused)
                    return
                self._fail(run, PipelineStage.TRANSCRIPTION, exc.user_message)
                return
            run = self.pipeline_service.update_stage(
                run,
                PipelineStage.TRANSCRIPTION,
                TaskStatus.SUCCEEDED,
                task_id=transcription.task_id,
                outputs={
                    "task_id": transcription.task_id,
                    "duration_seconds": str(transcription.duration_seconds or ""),
                    "segment_count": str(len(transcription.segments or [])),
                    "review_state": "unapproved_asr",
                    "source": transcription.source_kind,
                },
            )
            run = run.model_copy(
                update={
                    "config": {
                        **run.config,
                        "transcription_task_id": transcription.task_id,
                    }
                }
            )
            self.repository.save_pipeline_run(run)
            if str(run.config.get("workflow") or "") in {
                "production_batch_share_link",
                "production_batch_candidate",
            }:
                self.pipeline_service.pause_for_transcript_review(
                    run=run,
                    transcription=transcription,
                )
                return
            profile = dict(run.config.get("profile") or {})
            try:
                copywriting_platform = Platform(candidate_platform)
            except ValueError:
                copywriting_platform = (
                    Platform.XIAOHONGSHU
                    if "xiaohongshu.com" in share_text.lower()
                    else Platform.DOUYIN
                )
            self.pipeline_service.create_copywriting_review(
                run=run,
                transcription=transcription,
                platform=copywriting_platform,
                target_audience=str(profile.get("target_audience") or ""),
                style_prompt=str(profile.get("script_style") or ""),
            )
            return
        if (
            run.status == PipelineRunStatus.PENDING
            and run.current_stage == PipelineStage.AVATAR_GENERATION
        ):
            self._submit_avatar(run)
            return
        if (
            run.status == PipelineRunStatus.RUNNING
            and run.current_stage == PipelineStage.AVATAR_GENERATION
        ):
            self._poll_avatar_and_continue(run)
            return
        if (
            run.status == PipelineRunStatus.PAUSED
            and run.current_stage == PipelineStage.PUBLISHING
        ):
            self._reconcile_publish(run)

    def _run_production_batch(self, run: PipelineRun) -> None:
        """批次项统一领取并发槽位，再按来源进入对应的合规流水线。"""
        if (
            run.status == PipelineRunStatus.PAUSED
            and run.current_stage == PipelineStage.PUBLISHING
        ):
            # 人工发布结果回填后无需重新占用生产并发槽位，只需汇总终态。
            if run.publish_task_ids:
                self._reconcile_publish(run)
            return
        if self.production_service is None or not self.production_service.can_run(
            run.run_id
        ):
            return
        if (
            run.status == PipelineRunStatus.PENDING
            and run.current_stage == PipelineStage.PUBLISHING
        ):
            if bool(run.config.get("publish_confirmed")):
                self._submit_publish(run)
            return
        source_type = str(run.config.get("source_type") or "candidate")
        if source_type == "candidate":
            candidate_platform = str(run.config.get("candidate_platform") or "douyin")
            if bool(run.config.get("candidate_link_fallback")):
                self._run_guided_share_link(run)
            elif candidate_platform == Platform.DOUYIN.value:
                self._run_candidate(run)
            else:
                self._run_guided_share_link(run)
        elif source_type == "share_link":
            self._run_guided_share_link(run)
        else:
            self._run_production_batch_text(run, source_type)

    def _run_production_batch_text(self, run: PipelineRun, source_type: str) -> None:
        """选题生成或人工成稿都必须进入同一个文案审核阶段。"""
        if run.status == PipelineRunStatus.PENDING and run.current_stage is None:
            source_value = str(run.config.get("source_value") or "").strip()
            if not source_value:
                self._fail(run, PipelineStage.COPYWRITING, "批次项缺少选题或文案内容。")
                return
            profile = dict(run.config.get("profile") or {})
            if source_type == "brief":
                copy_service = self.pipeline_service.copywriting_service
                if copy_service is None:
                    self._fail(run, PipelineStage.COPYWRITING, "文案生成服务未配置。")
                    return
                task = copy_service.generate(
                    content_brief=source_value,
                    target_audience=str(profile.get("target_audience") or ""),
                    style_prompt=str(profile.get("script_style") or ""),
                    target_length=300,
                    tone="casual",
                    variant_count=2,
                )
                if task.status != TaskStatus.SUCCEEDED:
                    self._fail(
                        run,
                        PipelineStage.COPYWRITING,
                        task.error_message or "选题文案生成失败。",
                    )
                    return
            elif source_type == "script":
                now = datetime.now().astimezone()
                task = CopywritingTask(
                    task_id=f"copy-manual-{uuid4().hex[:10]}",
                    title=f"人工成稿 · {source_value[:20]}",
                    status=TaskStatus.SUCCEEDED,
                    progress=100,
                    created_at=now,
                    updated_at=now,
                    creation_mode="manual",
                    source_text=source_value,
                    result_text=source_value,
                    result_variants=[source_value],
                    stage="等待人工审核",
                    needs_manual_review=True,
                )
                self.repository.save_task(task)
            else:
                self._fail(run, PipelineStage.COPYWRITING, "不支持的文本批次来源。")
                return
            self.pipeline_service.pause_for_copy_review(run=run, copy_task=task)
            return
        self._run_candidate(run)

    def _submit_avatar(self, run: PipelineRun) -> None:
        profile = dict(run.config.get("profile") or {})
        copy_task = self.repository.get_task(run.copywriting_task_id or "")
        if not isinstance(copy_task, CopywritingTask):
            self._fail(run, PipelineStage.AVATAR_GENERATION, "找不到已审核文案任务。")
            return
        script = str(
            run.config.get("approved_script_text") or copy_task.result_text or ""
        ).strip()
        if not script:
            self._fail(
                run, PipelineStage.AVATAR_GENERATION, "审核后没有可用的最终口播文案。"
            )
            return
        try:
            assets = {item.asset_id: item for item in self.avatar_service.list_assets()}
        except AvatarProviderError as exc:
            self._pause_avatar_submission(run, str(exc))
            return
        avatar = assets.get(str(profile.get("avatar_id") or ""))
        voice = assets.get(str(profile.get("voice_id") or ""))
        if avatar is None or voice is None:
            self._fail(
                run,
                PipelineStage.AVATAR_GENERATION,
                "IP 配方绑定的数字人形象或音色不可用。",
            )
            return
        run = self.pipeline_service.update_stage(
            run, PipelineStage.AVATAR_GENERATION, TaskStatus.RUNNING
        )
        avatar_retry_number = int(
            (run.config.get("stage_retry_counts") or {}).get(
                PipelineStage.AVATAR_GENERATION.value
            )
            or 0
        )
        avatar_idempotency_key = f"worker-avatar-{run.run_id}"
        if avatar_retry_number > 0:
            avatar_idempotency_key += f"-retry-{avatar_retry_number}"
        request = AvatarSubmitRequest(
            script_text=script,
            keyword=run.keyword,
            source_task_id=copy_task.task_id,
            avatar_id=avatar.asset_id,
            voice_id=voice.asset_id,
            speech_rate=float(profile.get("speech_rate", 1.0)),
            # The legacy company gateway accepts the same safe default used by
            # the standalone avatar entry.  Its transparent-background option
            # is not supported by every configured avatar model.
            background="solid",
            rights_holder=str(run.config.get("rights_holder") or "current_tenant"),
            script_rights_confirmed=True,
            avatar_rights_confirmed=True,
            voice_rights_confirmed=True,
            idempotency_key=avatar_idempotency_key,
        )
        if not self.can_process():
            self._pause_avatar_submission(run, "登录会话已失效，请重新登录后继续制作。")
            return
        try:
            task = self.avatar_service.submit(
                request, avatar_name=avatar.name, voice_name=voice.name
            )
        except AvatarServiceUnavailableError as exc:
            self._pause_avatar_submission(run, str(exc))
            return
        updated_run = self.pipeline_service.update_stage(
            run,
            PipelineStage.AVATAR_GENERATION,
            task.status,
            task_id=task.task_id,
            outputs={"avatar_task_id": task.task_id, "provider": task.provider_name},
            error_message=task.error_message,
        )
        # 部分供应商可能在 submit 时就同步返回成功，不能等下一次轮询，
        # 否则流水线会停在已完成的数字人阶段。
        if task.status == TaskStatus.SUCCEEDED:
            self._poll_avatar_and_continue(updated_run)
        elif task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            self.pipeline_service.complete_run(
                updated_run,
                success=False,
                error_message=task.error_message or "数字人生成未完成。",
            )
        elif task.status == TaskStatus.OUTCOME_UNKNOWN:
            paused = updated_run.model_copy(
                update={
                    "status": PipelineRunStatus.PAUSED,
                    "updated_at": datetime.now().astimezone(),
                    "error_message": task.error_message or "数字人提交结果待人工核对。",
                    "config": {
                        **updated_run.config,
                        "outcome_unknown": True,
                        "recovery_blocked": True,
                        "recovery_reason": "avatar_outcome_unknown",
                    },
                }
            )
            paused = self.pipeline_service._event(
                paused,
                action="avatar_outcome_unknown",
                stage=PipelineStage.AVATAR_GENERATION,
                message="数字人提交结果待人工核对，未自动重试以避免重复扣费。",
            )
            self.repository.save_pipeline_run(paused)

    def _poll_avatar_and_continue(self, run: PipelineRun) -> None:
        task_id = run.avatar_task_id
        if not task_id:
            self._fail(run, PipelineStage.AVATAR_GENERATION, "数字人阶段缺少任务 ID。")
            return
        task = self.avatar_service.refresh_task(task_id)
        if task.status in {TaskStatus.QUEUED, TaskStatus.SUBMITTED, TaskStatus.RUNNING}:
            return
        if task.status != TaskStatus.SUCCEEDED:
            self._fail(
                run,
                PipelineStage.AVATAR_GENERATION,
                task.error_message or "数字人生成失败。",
            )
            return
        if not task.result_path:
            task = self.avatar_service.download_result(task_id)
        if not task.result_path:
            self._fail(
                run, PipelineStage.AVATAR_GENERATION, "数字人结果未保存为本地视频。"
            )
            return
        if self.video_editor_workflow_service is None:
            self._fail(
                run,
                PipelineStage.VIDEO_EDITING,
                "新版智能剪辑服务未配置，已停止避免生成旧模板。",
            )
            return
        transcription_service = self.pipeline_service.transcription_service
        if transcription_service is None:
            self._fail(
                run,
                PipelineStage.VIDEO_EDITING,
                "真实语音字幕对齐服务未配置，已停止避免生成错位字幕。",
            )
            return
        caption_candidates = [
            candidate
            for candidate in self.repository.list_tasks()
            if isinstance(candidate, TranscriptionTask)
            and candidate.candidate_id == task.task_id
            and candidate.source_kind == "avatar_caption_alignment"
        ]
        requested_caption_retry_id = str(
            run.config.get("caption_word_timestamps_retry_task_id") or ""
        )
        caption_task = next(
            (
                candidate
                for candidate in reversed(caption_candidates)
                if candidate.task_id == requested_caption_retry_id
            ),
            None,
        )
        if caption_task is None:
            caption_task = next(
                (
                    candidate
                    for candidate in reversed(caption_candidates)
                    if bool(candidate.word_timestamps_available)
                    or any(
                        bool(getattr(segment, "words", None))
                        for segment in candidate.segments
                    )
                ),
                None,
            )
        if caption_task is None and caption_candidates:
            caption_task = caption_candidates[-1]
        if caption_task is None:
            source_path = Path(task.result_path)
            caption_task = transcription_service.create_task(
                media_name=source_path.name,
                media_type="video/mp4",
                media_bytes=source_path.read_bytes(),
                rights_confirmed=True,
                rights_holder=task.rights_holder,
                candidate_id=task.task_id,
                language="zh",
                source_kind="avatar_caption_alignment",
                async_processing=True,
                # The production editor has a hard word-clock gate.  Keep
                # this alignment task on the same real word timeline as the
                # avatar audio instead of falling back to sentence estimates.
                include_word_timestamps=True,
            )
            updated = run.model_copy(
                update={
                    "updated_at": datetime.now().astimezone(),
                    "config": {
                        **run.config,
                        "caption_timing_task_id": caption_task.task_id,
                        "caption_timing_source": "pending_avatar_asr",
                    },
                }
            )
            self.repository.save_pipeline_run(updated)
            return

        if caption_task.status in {
            TaskStatus.QUEUED,
            TaskStatus.SUBMITTED,
            TaskStatus.RUNNING,
        }:
            return
        if caption_task.status != TaskStatus.SUCCEEDED:
            self._fail(
                run,
                PipelineStage.VIDEO_EDITING,
                caption_task.error_message or "真实语音字幕对齐失败。",
            )
            return
        caption_has_words = bool(
            caption_task.word_timestamps_available
            or any(
                bool(getattr(segment, "words", None))
                for segment in caption_task.segments
            )
        )
        if (
            not caption_has_words
            and caption_task.provider_name == "faster_whisper_local"
            and not requested_caption_retry_id
        ):
            # Older production runs created this local alignment task without
            # word timestamps. Recreate it once with the real word-clock
            # option, retaining all existing avatar/output state.
            source_path = Path(task.result_path)
            refreshed_caption_task = transcription_service.create_task(
                media_name=source_path.name,
                media_type="video/mp4",
                media_bytes=source_path.read_bytes(),
                rights_confirmed=True,
                rights_holder=task.rights_holder,
                candidate_id=task.task_id,
                language="zh",
                source_kind="avatar_caption_alignment",
                async_processing=True,
                include_word_timestamps=True,
            )
            updated = run.model_copy(
                update={
                    "updated_at": datetime.now().astimezone(),
                    "config": {
                        **run.config,
                        "caption_timing_task_id": refreshed_caption_task.task_id,
                        "caption_word_timestamps_retry_task_id": refreshed_caption_task.task_id,
                        "caption_timing_source": "word_timestamps_retry",
                    },
                }
            )
            self.repository.save_pipeline_run(updated)
            if refreshed_caption_task.status in {
                TaskStatus.QUEUED,
                TaskStatus.SUBMITTED,
                TaskStatus.RUNNING,
            }:
                return
            caption_task = refreshed_caption_task
        script = str(run.config.get("approved_script_text") or task.script_text or "")
        timed_segments = (
            self.video_editor_workflow_service.approved_script_segments_from_asr(
                script,
                [segment.model_dump(mode="json") for segment in caption_task.segments],
            )
        )
        self._edit_and_package(
            run,
            task,
            subtitle_segments=timed_segments,
            subtitle_task_id=caption_task.task_id,
        )

    def _pause_avatar_submission(self, run: PipelineRun, message: str) -> None:
        """Keep an unsubmitted avatar stage recoverable after readiness changes."""

        paused = run.model_copy(
            update={
                "status": PipelineRunStatus.PAUSED,
                "current_stage": PipelineStage.AVATAR_GENERATION,
                "updated_at": datetime.now().astimezone(),
                "error_message": message,
                "config": {
                    **run.config,
                    "recovery_blocked": True,
                    "recovery_reason": "avatar_submission_not_started",
                    "outcome_unknown": False,
                    "manual_action_required": "请确认数字人服务状态或重新登录后重试；本次未提交供应商任务。",
                },
            }
        )
        paused = self.pipeline_service._event(
            paused,
            action="avatar_submission_paused",
            stage=PipelineStage.AVATAR_GENERATION,
            message=message,
            details={"provider_job_submitted": "false"},
        )
        self.repository.save_pipeline_run(paused)

    def _edit_and_package(
        self,
        run: PipelineRun,
        avatar_task: AvatarTask,
        *,
        subtitle_segments: list[dict[str, object]] | None = None,
        subtitle_task_id: str | None = None,
    ) -> None:
        profile = dict(run.config.get("profile") or {})
        copy_task = self.repository.get_task(run.copywriting_task_id or "")
        script = str(
            run.config.get("approved_script_text")
            or getattr(copy_task, "result_text", "")
            or ""
        )
        draft = suggested_publish_draft(
            approved_script=script,
            creative_plan=(
                dict(plan)
                if isinstance(plan := run.config.get("creative_plan"), dict)
                else None
            ),
            profile_tags=list(profile.get("tags") or []),
        )
        run = self.pipeline_service.update_stage(
            run, PipelineStage.VIDEO_EDITING, TaskStatus.RUNNING
        )
        if self.video_editor_workflow_service is None:
            self._fail(
                run,
                PipelineStage.VIDEO_EDITING,
                "新版智能剪辑服务未配置，已停止以避免生成旧模板。",
            )
            return
        try:
            edit_task = self.video_editor_workflow_service.render_production_export(
                avatar_task=avatar_task,
                script_text=script,
                publish_title=draft["title"],
                subtitle_segments=subtitle_segments,
                subtitle_task_id=subtitle_task_id,
                style_preset_id=str(
                    run.config.get("style_preset_id") or PRESET_SEMANTIC_ADAPTIVE
                ),
            )
        except Exception as exc:
            self._fail(
                run, PipelineStage.VIDEO_EDITING, str(exc) or "新版智能剪辑失败。"
            )
            return
        if edit_task.status != TaskStatus.SUCCEEDED or not edit_task.result_path:
            self._fail(
                run,
                PipelineStage.VIDEO_EDITING,
                edit_task.error_message or "视频剪辑失败。",
            )
            return
        run = self.pipeline_service.update_stage(
            run,
            PipelineStage.VIDEO_EDITING,
            TaskStatus.SUCCEEDED,
            task_id=edit_task.task_id,
            outputs={
                "edit_task_id": edit_task.task_id,
                "video_path": edit_task.result_path,
            },
        )
        if run.config.get("publish_enabled") is False:
            self.pipeline_service.complete_run(run, success=True)
            return
        if str(run.config.get("workflow") or "").startswith("production_batch_"):
            completed_config = dict(run.config)
            completed_config.pop("local_edit_retry_pending", None)
            paused = run.model_copy(
                update={
                    "status": PipelineRunStatus.PAUSED,
                    "current_stage": PipelineStage.PUBLISHING,
                    "updated_at": datetime.now().astimezone(),
                    "config": {
                        **completed_config,
                        "video_path": edit_task.result_path,
                        "publish_confirmed": False,
                    },
                }
            )
            paused = self.pipeline_service._event(
                paused,
                action="publish_confirmation_required",
                stage=PipelineStage.PUBLISHING,
                message="成片已生成，等待人工确认发布。",
            )
            self.repository.save_pipeline_run(paused)
            return
        platforms = [
            PublishPlatform(item)
            for item in run.config.get("publish_platforms", ["douyin"])
        ]
        targets = self.pipeline_service.build_publish_targets(
            title=draft["title"],
            description=draft["description"],
            tags=draft["tags"],
            platforms=platforms,
        )
        run = self.pipeline_service.update_stage(
            run, PipelineStage.PUBLISHING, TaskStatus.RUNNING
        )
        summary = self.publish_service.create_batch(
            video_path=edit_task.result_path,
            targets=targets,
            source_pipeline_run_id=run.run_id,
        )
        for publish_task in summary["tasks"]:
            run = self.pipeline_service.update_stage(
                run,
                PipelineStage.PUBLISHING,
                TaskStatus.SUBMITTED,
                task_id=publish_task.task_id,
                outputs={
                    "platform": publish_task.target.platform.value,
                    "publish_status": publish_task.publish_status.value,
                },
            )
        paused = run.model_copy(
            update={
                "status": PipelineRunStatus.PAUSED,
                "current_stage": PipelineStage.PUBLISHING,
                "updated_at": datetime.now().astimezone(),
            }
        )
        paused = self.pipeline_service._event(
            paused,
            action="manual_publish_ready",
            stage=PipelineStage.PUBLISHING,
            message="成片与人工发布包已准备，等待平台后台发布结果回填。",
        )
        self.repository.save_pipeline_run(paused)

    def _submit_publish(self, run: PipelineRun) -> None:
        """仅在批次页明确确认后，才创建真实或人工发布任务。"""
        if not bool(run.config.get("output_reviewed")):
            paused = run.model_copy(
                update={
                    "status": PipelineRunStatus.PAUSED,
                    "updated_at": datetime.now().astimezone(),
                    "error_message": "请先完成人工成片复核。",
                }
            )
            self.repository.save_pipeline_run(paused)
            return
        if run.publish_task_ids:
            paused = run.model_copy(
                update={
                    "status": PipelineRunStatus.PAUSED,
                    "updated_at": datetime.now().astimezone(),
                    "error_message": "发布任务已经创建，不能重复提交。",
                }
            )
            self.repository.save_pipeline_run(paused)
            return
        video_path = str(run.config.get("video_path") or "")
        if not video_path:
            edit_task = self.repository.get_task(run.edit_task_id or "")
            video_path = str(getattr(edit_task, "result_path", "") or "")
        if not video_path:
            self._fail(
                run, PipelineStage.PUBLISHING, "找不到已生成的成片，不能提交发布。"
            )
            return
        if not Path(video_path).is_file():
            self._fail(
                run,
                PipelineStage.PUBLISHING,
                "成片文件不存在或已被移动，不能创建发布任务。",
            )
            return
        try:
            target_specs = list(run.config.get("publish_targets") or [])
            if not target_specs:
                target_specs = [
                    {
                        "platform": item,
                        "account_id": None,
                        "mode": "manual",
                    }
                    for item in run.config.get("publish_platforms", ["douyin"])
                ]
            draft = run.config.get("publish_draft")
            if not bool(run.config.get("publish_draft_approved")) or not isinstance(
                draft, dict
            ):
                self._fail(
                    run,
                    PipelineStage.PUBLISHING,
                    "请先确认标题、描述和标签，再准备发布。",
                )
                return
            targets = self.pipeline_service.build_publish_targets(
                title=str(draft.get("title") or ""),
                description=str(draft.get("description") or ""),
                tags=list(draft.get("tags") or []),
                target_specs=target_specs,
            )
            real_targets = [
                target
                for target, spec in zip(targets, target_specs)
                if spec.get("mode") != "manual"
            ]
            if real_targets:
                preflight = self.publish_service.preflight(
                    video_path=video_path,
                    targets=real_targets,
                )
                if preflight["blocked"]:
                    paused = run.model_copy(
                        update={
                            "status": PipelineRunStatus.PAUSED,
                            "updated_at": datetime.now().astimezone(),
                            "error_message": "；".join(preflight["issues"]),
                        }
                    )
                    self.repository.save_pipeline_run(paused)
                    return
            run = self.pipeline_service.update_stage(
                run, PipelineStage.PUBLISHING, TaskStatus.RUNNING
            )
            publish_tasks = []
            if real_targets:
                summary = self.publish_service.create_batch(
                    video_path=video_path,
                    targets=real_targets,
                    source_pipeline_run_id=run.run_id,
                )
                publish_tasks.extend(summary["tasks"])
            for target, spec in zip(targets, target_specs):
                if spec.get("mode") != "manual":
                    continue
                manual_task = SandboxPublisher(target.platform).publish(
                    video_path,
                    target,
                )
                manual_task = manual_task.model_copy(
                    update={"source_pipeline_run_id": run.run_id}
                )
                self.repository.save_task(manual_task)
                publish_tasks.append(manual_task)
            for publish_task in publish_tasks:
                run = self.pipeline_service.update_stage(
                    run,
                    PipelineStage.PUBLISHING,
                    publish_task.status,
                    task_id=publish_task.task_id,
                    outputs={
                        "platform": publish_task.target.platform.value,
                        "publish_status": publish_task.publish_status.value,
                    },
                )
            if publish_tasks and all(
                task.status == TaskStatus.SUCCEEDED for task in publish_tasks
            ):
                self.pipeline_service.complete_run(run, success=True)
                return
            paused = run.model_copy(
                update={
                    "status": PipelineRunStatus.PAUSED,
                    "current_stage": PipelineStage.PUBLISHING,
                    "updated_at": datetime.now().astimezone(),
                }
            )
            paused = self.pipeline_service._event(
                paused,
                action="publish_submitted",
                stage=PipelineStage.PUBLISHING,
                message="已创建发布任务，等待官方结果或人工回填。",
            )
            self.repository.save_pipeline_run(paused)
        except Exception as exc:
            self._fail(run, PipelineStage.PUBLISHING, str(exc))

    def _reconcile_publish(self, run: PipelineRun) -> None:
        tasks = [self.repository.get_task(task_id) for task_id in run.publish_task_ids]
        statuses = [task.status for task in tasks if task is not None]
        if not statuses or any(
            status in {TaskStatus.SUBMITTED, TaskStatus.RUNNING, TaskStatus.QUEUED}
            for status in statuses
        ):
            return
        if all(status == TaskStatus.SUCCEEDED for status in statuses):
            self.pipeline_service.complete_run(run, success=True)
            return
        if all(status == TaskStatus.FAILED for status in statuses):
            self._fail(run, PipelineStage.PUBLISHING, "所有平台均人工确认发布失败。")
            return
        updated = run.model_copy(
            update={
                "status": PipelineRunStatus.PARTIAL,
                "updated_at": datetime.now().astimezone(),
            }
        )
        updated = self.pipeline_service._event(
            updated,
            action="publish_partial",
            stage=PipelineStage.PUBLISHING,
            message="发布结果部分成功或仍待核对。",
        )
        self.repository.save_pipeline_run(updated)

    def _fail(self, run: PipelineRun, stage: PipelineStage, message: str) -> None:
        failed = self.pipeline_service.update_stage(
            run, stage, TaskStatus.FAILED, error_message=message
        )
        self.pipeline_service.complete_run(failed, success=False, error_message=message)
