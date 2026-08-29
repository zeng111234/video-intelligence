from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import subprocess
import tempfile
from threading import Lock
from time import monotonic, sleep
from typing import Any, Callable
from uuid import uuid4

from src.contracts import TaskRepository
from src.mock_data import demo_segments
from src.models import (
    TaskStatus,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptStatus,
    TranscriptionTask,
)
from src.resources import load_asr_model
from src.retry import ExternalServiceError, RetryPolicy, retry_with_policy

MAX_MEDIA_BYTES = 50 * 1024 * 1024
MAX_PROVIDER_MEDIA_BYTES = 300 * 1024 * 1024
MAX_DURATION_SECONDS = 15 * 60
ALLOWED_EXTENSIONS = {".mp4", ".mov"}
ALLOWED_ASR_MODELS = {"base", "medium", "large-v3-turbo", "fun-asr"}
ALLOWED_ASR_LANGUAGES = {"auto", "zh", "en", "ja", "ko"}
MAX_HOTWORDS_LENGTH = 500

logger = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    """A user-safe transcription error that never exposes internal details."""

    def __init__(
        self,
        user_message: str,
        *,
        code: str = "transcription_failed",
        task_id: str | None = None,
    ) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.task_id = task_id


class MediaValidationError(TranscriptionError):
    def __init__(
        self,
        user_message: str,
        *,
        code: str = "invalid_media",
        task_id: str | None = None,
    ) -> None:
        super().__init__(user_message, code=code, task_id=task_id)


def _has_valid_signature(extension: str, content: bytes) -> bool:
    if extension in ALLOWED_EXTENSIONS:
        return len(content) >= 12 and content[4:8] == b"ftyp"
    return False


def _cloud_failure_summary(exc: BaseException) -> str:
    """Return a short provider diagnostic without exposing a traceback or secret."""

    message = str(exc).strip()
    http_match = re.search(r"\bHTTP\s+(\d{3})\b", message, flags=re.IGNORECASE)
    provider_code = str(getattr(exc, "code", "") or "").strip()
    provider_kind = str(getattr(exc, "kind", "") or "").strip().casefold()
    if http_match and provider_code:
        return f"HTTP {http_match.group(1)} / {provider_code}"
    if http_match:
        return f"HTTP {http_match.group(1)}"
    if provider_code:
        return provider_code
    if provider_kind == "authorization":
        return "授权失败"
    if provider_kind == "rate_limit":
        return "请求过于频繁"
    if provider_kind == "connection":
        return "连接异常"
    if provider_kind == "validation":
        return "请求参数无效"
    return "云服务异常"


class TranscriptionService:
    def __init__(
        self,
        repository: TaskRepository,
        *,
        model_loader=load_asr_model,
        command_runner=subprocess.run,
        transcript_reviewer: Callable[..., dict[str, Any]] | None = None,
        transcript_batch_reviewer: Callable[..., dict[str, Any]] | None = None,
        cloud_runtime=None,
        cloud_storage_directory: str | Path | None = None,
        cloud_poll_interval_seconds: float = 5.0,
        cloud_timeout_seconds: float = 30 * 60,
    ) -> None:
        self.repository = repository
        self.model_loader = model_loader
        self.command_runner = command_runner
        self.transcript_reviewer = transcript_reviewer
        self.transcript_batch_reviewer = transcript_batch_reviewer
        self.cloud_runtime = cloud_runtime
        self.cloud_storage_directory = (
            Path(cloud_storage_directory)
            if cloud_storage_directory is not None
            else None
        )
        self.cloud_poll_interval_seconds = cloud_poll_interval_seconds
        self.cloud_timeout_seconds = cloud_timeout_seconds
        # The API path can process a cloud task synchronously while the
        # background worker is polling the same persisted queue.  Striped
        # locks keep one task from being submitted twice without growing an
        # unbounded lock dictionary over the lifetime of the service.
        self._cloud_task_locks = tuple(Lock() for _ in range(32))

    def _debit_credits(
        self,
        cost_cny: Decimal | float,
        *,
        reason: str,
        ref_type: str,
        ref_id: str,
    ) -> None:
        """按人民币费用扣积分；费用为 0/未知不扣，余额不足抛出用户可读错误。"""
        from src.services.credits import (
            CreditsService,
            InsufficientCreditsError,
            cny_to_credits,
        )

        credits = cny_to_credits(cost_cny)
        if credits <= 0:
            return
        try:
            CreditsService(self.repository).debit(
                credits,
                reason,
                ref_type=ref_type,
                ref_id=ref_id,
            )
        except InsufficientCreditsError as exc:
            raise TranscriptionError(
                exc.message,
                code="insufficient_credits",
            ) from exc

    def create_task(
        self,
        *,
        media_name: str,
        media_type: str,
        media_bytes: bytes,
        rights_confirmed: bool,
        rights_holder: str,
        candidate_id: str | None = None,
        model_name: str = "large-v3-turbo",
        language: str = "zh",
        hotwords: str | None = None,
        max_media_bytes: int = MAX_MEDIA_BYTES,
        source_kind: str = "asr",
        source_url: str | None = None,
        on_progress: Callable[[TranscriptionTask], None] | None = None,
        async_processing: bool = False,
        include_word_timestamps: bool = False,
    ) -> TranscriptionTask:
        if not rights_confirmed:
            raise TranscriptionError(
                "必须确认拥有媒体处理权后才能创建转写任务。",
                code="rights_not_confirmed",
            )
        if not rights_holder.strip():
            raise TranscriptionError(
                "请填写媒体权利主体。", code="rights_holder_required"
            )
        if model_name not in ALLOWED_ASR_MODELS:
            raise TranscriptionError(
                "不支持所选识别模型，请刷新页面后重试。",
                code="asr_model_invalid",
            )
        if language not in ALLOWED_ASR_LANGUAGES:
            raise TranscriptionError(
                "不支持所选语言，请刷新页面后重试。",
                code="asr_language_invalid",
            )
        normalized_hotwords = " ".join((hotwords or "").split())
        if len(normalized_hotwords) > MAX_HOTWORDS_LENGTH:
            raise TranscriptionError(
                "专有词提示不能超过 500 个字符。",
                code="asr_hotwords_too_long",
            )
        self._validate_upload(
            media_name,
            media_bytes,
            max_media_bytes=max_media_bytes,
        )
        if self.cloud_runtime is not None:
            return self._create_cloud_task(
                media_name=media_name,
                media_type=media_type,
                media_bytes=media_bytes,
                rights_holder=rights_holder,
                candidate_id=candidate_id,
                language=language,
                hotwords=normalized_hotwords,
                source_kind=source_kind,
                source_url=source_url,
                async_processing=async_processing,
                on_progress=on_progress,
            )
        now = datetime.now().astimezone()
        task = TranscriptionTask(
            task_id=f"transcript-{uuid4().hex[:10]}",
            title=media_name,
            status=TaskStatus.RUNNING,
            progress=5,
            created_at=now,
            updated_at=now,
            media_name=media_name,
            media_type=media_type,
            rights_confirmed=True,
            rights_holder=rights_holder.strip(),
            rights_confirmed_at=now,
            candidate_id=candidate_id,
            stage="视频检查",
            media_sha256=hashlib.sha256(media_bytes).hexdigest(),
            model_name=model_name,
            asr_hotwords=normalized_hotwords or None,
            source_kind=source_kind,
            source_url=source_url,
            is_mock=False,
        )
        try:
            self._save_task(task, on_progress)
        except Exception as exc:
            raise TranscriptionError(
                "任务记录无法保存，请稍后重新上传。",
                code="task_save_failed",
                task_id=task.task_id,
            ) from exc
        started = monotonic()
        extension = Path(media_name).suffix.casefold()
        try:
            with tempfile.TemporaryDirectory(prefix=f"video-{task.task_id}-") as temp:
                input_path = Path(temp) / f"input{extension}"
                wav_path = Path(temp) / "audio.wav"
                input_path.write_bytes(media_bytes)
                duration = self._probe(input_path)
                task = self._update_task(
                    task,
                    stage="音频提取",
                    progress=25,
                    duration_seconds=duration,
                    on_progress=on_progress,
                )
                self._extract_audio(input_path, wav_path)
                task = self._update_task(
                    task,
                    stage="语音识别",
                    progress=50,
                    on_progress=on_progress,
                )
                model = self._load_asr_model(model_name)
                segments, language = self._transcribe_with_model(
                    model,
                    wav_path,
                    normalized_hotwords,
                    language=language,
                    include_word_timestamps=include_word_timestamps,
                )
                self._validate_segments(segments)
                task = self._update_task(
                    task,
                    stage="低置信片段AI口播修订中",
                    progress=75,
                    on_progress=on_progress,
                )
                auto_segments, review_summary = self._auto_review_segments(
                    model,
                    wav_path,
                    segments,
                    language=language,
                )
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "stage": "AI口播成稿",
                    "progress": 100,
                    "updated_at": datetime.now().astimezone(),
                    "elapsed_seconds": round(monotonic() - started, 2),
                    "segments": segments,
                    "word_timestamps_available": any(
                        bool(segment.words) for segment in segments
                    ),
                    "language": language,
                    **review_summary,
                }
            )
            self._save_task(task, on_progress)
            _, task = self._save_auto_revision(task, auto_segments)
            self._save_task(task, on_progress)
            return task
        except Exception as exc:
            safe_error = self._safe_error(exc, task.task_id)
            failed = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "stage": "处理失败",
                    "updated_at": datetime.now().astimezone(),
                    "elapsed_seconds": round(monotonic() - started, 2),
                    "error_message": safe_error.user_message,
                }
            )
            try:
                self._save_task(failed, on_progress)
            except Exception as save_exc:
                raise TranscriptionError(
                    "处理失败，且任务状态无法保存；请稍后重新上传。",
                    code="failed_task_save_failed",
                    task_id=task.task_id,
                ) from save_exc
            if safe_error is exc:
                raise safe_error
            raise safe_error from exc

    def _create_cloud_task(
        self,
        *,
        media_name: str,
        media_type: str,
        media_bytes: bytes,
        rights_holder: str,
        candidate_id: str | None,
        language: str,
        hotwords: str,
        source_kind: str,
        source_url: str | None,
        async_processing: bool,
        on_progress: Callable[[TranscriptionTask], None] | None,
    ) -> TranscriptionTask:
        if hotwords:
            raise TranscriptionError(
                "当前阿里云识别暂未接入专有词表，请清空专有词后重试。",
                code="cloud_hotwords_not_supported",
            )
        if self.cloud_storage_directory is None:
            raise TranscriptionError(
                "云端转写存储目录未配置，请联系管理员。",
                code="cloud_storage_not_configured",
            )
        now = datetime.now().astimezone()
        task_id = f"transcript-{uuid4().hex[:10]}"
        extension = Path(media_name).suffix.casefold()
        task_directory = self.cloud_storage_directory / task_id
        task_directory.mkdir(parents=True, exist_ok=True)
        media_path = task_directory / f"input{extension}"
        media_path.write_bytes(media_bytes)
        try:
            duration = self._probe(media_path)
            if getattr(self.cloud_runtime, "billing_centrally_managed", False):
                estimated_cost = self.cloud_runtime.ensure_authorized(
                    duration,
                    operation_key=task_id,
                )
            else:
                estimated_cost = self.cloud_runtime.ensure_authorized(duration)
                self._debit_credits(
                    estimated_cost,
                    reason="云端转写费用",
                    ref_type="transcription",
                    ref_id=task_id,
                )
        except Exception:
            media_path.unlink(missing_ok=True)
            try:
                task_directory.rmdir()
            except OSError:
                pass
            raise
        task = TranscriptionTask(
            task_id=task_id,
            title=media_name,
            status=TaskStatus.QUEUED,
            progress=5,
            created_at=now,
            updated_at=now,
            media_name=media_name,
            media_type=media_type,
            rights_confirmed=True,
            rights_holder=rights_holder.strip(),
            rights_confirmed_at=now,
            candidate_id=candidate_id,
            stage="等待云端识别",
            media_sha256=hashlib.sha256(media_bytes).hexdigest(),
            model_name="fun-asr",
            provider_name="aliyun_fun_asr",
            provider_status="queued",
            estimated_cost_cny=float(estimated_cost),
            pricing_version=self.cloud_runtime.capability()["price_version"],
            billing_authorized=True,
            language=language,
            duration_seconds=duration,
            source_kind=source_kind,
            source_url=source_url,
            is_mock=False,
            outputs={"source_media_path": str(media_path)},
        )
        self._save_task(task, on_progress)
        if async_processing:
            return task
        return self.process_cloud_task(task.task_id, on_progress=on_progress)

    def process_cloud_task(
        self,
        task_id: str,
        *,
        on_progress: Callable[[TranscriptionTask], None] | None = None,
    ) -> TranscriptionTask:
        lock_index = hashlib.sha256(task_id.encode("utf-8")).digest()[0] % len(
            self._cloud_task_locks
        )
        with self._cloud_task_locks[lock_index]:
            return self._process_cloud_task_locked(
                task_id,
                on_progress=on_progress,
            )

    def review_completed_cloud_task(self, task_id: str) -> TranscriptionTask:
        """为升级前已完成的云端结果补做一次幂等 AI 校对。"""
        lock_index = hashlib.sha256(task_id.encode("utf-8")).digest()[0] % len(
            self._cloud_task_locks
        )
        with self._cloud_task_locks[lock_index]:
            task = self.repository.get_task(task_id)
            if not isinstance(task, TranscriptionTask):
                raise TranscriptionError("转写任务不存在。", code="task_not_found")
            if (
                task.provider_name != "aliyun_fun_asr"
                or task.status != TaskStatus.SUCCEEDED
            ):
                raise TranscriptionError(
                    "只有已完成的阿里云转写可以补做 AI 校对。",
                    code="task_not_reviewable",
                )
            if task.auto_reviewed or not any(
                segment.needs_review and not segment.reviewed
                for segment in task.segments
            ):
                return task
            segments, auto_review = self._auto_review_cloud_segments(
                list(task.segments),
                context_hint=task.title,
            )
            uncertain_count = int(auto_review["uncertain_segment_count"])
            updated = task.model_copy(
                update={
                    "segments": segments,
                    "stage": (
                        f"AI 已校对，有 {uncertain_count} 处事实需确认"
                        if auto_review["auto_reviewed"] and uncertain_count
                        else "AI 校对完成"
                        if auto_review["auto_reviewed"]
                        else f"有 {uncertain_count} 段待确认"
                    ),
                    "auto_reviewed": bool(auto_review["auto_reviewed"]),
                    "uncertain_segment_count": uncertain_count,
                    "llm_review_count": int(auto_review["llm_review_count"]),
                    "auto_review_error": auto_review["auto_review_error"],
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self.repository.save_task(updated)
            return updated

    def _process_cloud_task_locked(
        self,
        task_id: str,
        *,
        on_progress: Callable[[TranscriptionTask], None] | None = None,
    ) -> TranscriptionTask:
        from src.services.video_editor_cloud import ProviderJobStatus

        task = self.repository.get_task(task_id)
        if not isinstance(task, TranscriptionTask):
            raise TranscriptionError("转写任务不存在。", code="task_not_found")
        if task.provider_name != "aliyun_fun_asr" or self.cloud_runtime is None:
            raise TranscriptionError(
                "该任务不是阿里云转写任务。", code="provider_mismatch"
            )
        if task.status == TaskStatus.SUCCEEDED:
            return task

        media_path = Path(task.outputs.get("source_media_path", ""))
        started = monotonic()
        try:
            if task.provider_job_id:
                task = task.model_copy(
                    update={
                        "status": TaskStatus.RUNNING,
                        "stage": "正在查询云端结果",
                        "progress": max(task.progress, 60),
                        "provider_status": "running",
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                self._save_task(task, on_progress)
                snapshot = self.cloud_runtime.query(task.provider_job_id)
            else:
                if task.status not in {TaskStatus.QUEUED, TaskStatus.FAILED}:
                    raise TranscriptionError(
                        "上次云端提交结果无法确认，系统不会自动重复扣费。",
                        code="submission_outcome_unknown",
                        task_id=task.task_id,
                    )
                if not media_path.is_file():
                    raise TranscriptionError(
                        "待识别素材已过期，请重新上传。",
                        code="source_media_expired",
                    )
                task = task.model_copy(
                    update={
                        "status": TaskStatus.RUNNING,
                        "stage": "正在上传到公司云端",
                        "progress": 20,
                        "provider_status": "uploading",
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                self._save_task(task, on_progress)
                object_key = f"asr-input/{task.task_id}/{Path(task.media_name).name}"
                asset = self.cloud_runtime.upload(
                    media_path,
                    object_key=object_key,
                    media_type=task.media_type,
                )
                task = task.model_copy(
                    update={
                        "stage": "正在提交云端识别",
                        "progress": 40,
                        "provider_status": "submitting",
                        "provider_object_key": object_key,
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                self._save_task(task, on_progress)
                snapshot = self.cloud_runtime.submit(
                    asset,
                    language=task.language or "zh",
                )
                task = task.model_copy(
                    update={
                        "status": TaskStatus.SUBMITTED,
                        "stage": "云端识别中",
                        "progress": 55,
                        "provider_job_id": snapshot.provider_job_id,
                        "provider_status": snapshot.status.value,
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                self._save_task(task, on_progress)

            while snapshot.status in {
                ProviderJobStatus.PENDING,
                ProviderJobStatus.RUNNING,
            }:
                if monotonic() - started > self.cloud_timeout_seconds:
                    raise TranscriptionError(
                        "云端识别仍在处理中，请稍后点击重新连接查看结果。",
                        code="cloud_asr_timeout",
                        task_id=task.task_id,
                    )
                sleep(self.cloud_poll_interval_seconds)
                snapshot = self.cloud_runtime.query(snapshot.provider_job_id)
                task = task.model_copy(
                    update={
                        "status": TaskStatus.RUNNING,
                        "stage": "云端识别中",
                        "progress": min(90, max(task.progress, 60)),
                        "provider_status": snapshot.status.value,
                        "updated_at": datetime.now().astimezone(),
                    }
                )
                self._save_task(task, on_progress)

            if snapshot.status == ProviderJobStatus.FAILED:
                provider_detail = (
                    snapshot.detail if isinstance(snapshot.detail, dict) else {}
                )
                provider_code = str(provider_detail.get("code") or "").strip()
                failure_message = (
                    "音轨里没有识别到人声，请换成包含清晰讲话的视频后重试；"
                    "本次素材和任务记录已保留。"
                    if provider_code == "ASR_RESPONSE_HAVE_NO_WORDS"
                    else "阿里云语音识别明确失败，素材已保留，可手动重试。"
                )
                raise TranscriptionError(
                    failure_message,
                    code="cloud_asr_failed",
                    task_id=task.task_id,
                )
            if snapshot.status == ProviderJobStatus.OUTCOME_UNKNOWN:
                raise TranscriptionError(
                    "阿里云识别结果暂时无法确认，请先重新连接查询，系统不会重复提交。",
                    code="cloud_asr_outcome_unknown",
                    task_id=task.task_id,
                )

            cloud_transcript = self.cloud_runtime.fetch_result(snapshot)
            segments = [
                TranscriptSegment(
                    start=item.start,
                    end=item.end,
                    text=item.text,
                    confidence=item.confidence,
                    needs_review=(
                        item.confidence is not None and item.confidence < 0.75
                    ),
                    quality_status=(
                        "pending"
                        if item.confidence is not None and item.confidence < 0.75
                        else "accepted"
                        if item.confidence is not None
                        else "completed"
                    ),
                    quality_source="primary_asr",
                    quality_note=(
                        "阿里云标记为低置信度，请确认该片段。"
                        if item.confidence is not None and item.confidence < 0.75
                        else "阿里云识别完成，未返回片段置信度。"
                        if item.confidence is None
                        else "阿里云识别完成。"
                    ),
                )
                for item in cloud_transcript.segments
                if item.text.strip()
            ]
            self._validate_segments(segments)
            auto_review = {
                "auto_reviewed": False,
                "uncertain_segment_count": sum(
                    1 for segment in segments if segment.needs_review
                ),
                "llm_review_count": 0,
                "auto_review_error": None,
            }
            if self.transcript_batch_reviewer is not None and any(
                segment.needs_review for segment in segments
            ):
                segments, auto_review = self._auto_review_cloud_segments(
                    segments,
                    context_hint=task.title,
                )
            uncertain_segment_count = int(auto_review["uncertain_segment_count"])
            completed = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "stage": (
                        f"AI 已校对，有 {uncertain_segment_count} 处事实需确认"
                        if auto_review["auto_reviewed"] and uncertain_segment_count
                        else "AI 校对完成"
                        if auto_review["auto_reviewed"]
                        else f"有 {uncertain_segment_count} 段待确认"
                        if uncertain_segment_count
                        else "识别完成"
                    ),
                    "progress": 100,
                    "provider_status": "succeeded",
                    "error_message": None,
                    "segments": segments,
                    "duration_seconds": cloud_transcript.duration_seconds
                    or task.duration_seconds,
                    "language": cloud_transcript.language or task.language or "zh",
                    "uncertain_segment_count": uncertain_segment_count,
                    "auto_reviewed": bool(auto_review["auto_reviewed"]),
                    "secondary_asr_count": 0,
                    "llm_review_count": int(auto_review["llm_review_count"]),
                    "auto_review_error": auto_review["auto_review_error"],
                    "elapsed_seconds": round(monotonic() - started, 2),
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self._save_task(completed, on_progress)
            media_path.unlink(missing_ok=True)
            return completed
        except Exception as exc:
            code = getattr(exc, "code", "")
            upload_failed_before_submission = (
                not task.provider_job_id and task.provider_status == "uploading"
            )
            outcome_unknown = not upload_failed_before_submission and (
                bool(getattr(exc, "outcome_unknown", False))
                or code
                in {
                    "cloud_asr_timeout",
                    "cloud_asr_outcome_unknown",
                    "submission_outcome_unknown",
                }
            )
            failed = task.model_copy(
                update={
                    "status": (
                        TaskStatus.OUTCOME_UNKNOWN
                        if outcome_unknown
                        else TaskStatus.FAILED
                    ),
                    "stage": (
                        "结果待确认"
                        if outcome_unknown
                        else "素材上传失败"
                        if upload_failed_before_submission
                        else "云端识别失败"
                    ),
                    "provider_status": (
                        "outcome_unknown" if outcome_unknown else "failed"
                    ),
                    "error_message": (
                        "素材上传连接失败，尚未创建云端识别任务；素材已保留，确认费用后可重试一次。"
                        if upload_failed_before_submission
                        else exc.user_message
                        if isinstance(exc, TranscriptionError)
                        else (
                            "阿里云任务状态查询暂时失败（连接异常），素材和任务编号已保留；"
                            "请重新连接查询，系统不会重复提交。"
                            if outcome_unknown
                            else f"阿里云语音识别失败（{_cloud_failure_summary(exc)}），"
                            "素材已保留；请重新上传并识别。"
                        )
                    ),
                    "elapsed_seconds": round(monotonic() - started, 2),
                    "updated_at": datetime.now().astimezone(),
                }
            )
            self._save_task(failed, on_progress)
            if isinstance(exc, TranscriptionError):
                raise
            raise TranscriptionError(
                (
                    "阿里云任务状态查询暂时失败（连接异常），素材和任务编号已保留；"
                    "请重新连接查询，系统不会重复提交。"
                    if outcome_unknown
                    else f"阿里云语音识别失败（{_cloud_failure_summary(exc)}），"
                    "素材已保留；请重新上传并识别。"
                ),
                code=(
                    "cloud_asr_outcome_unknown"
                    if outcome_unknown
                    else "cloud_asr_failed"
                ),
                task_id=task.task_id,
            ) from exc

    @staticmethod
    def _validate_upload(
        media_name: str,
        content: bytes,
        *,
        max_media_bytes: int = MAX_MEDIA_BYTES,
    ) -> None:
        extension = Path(media_name).suffix.casefold()
        if extension not in ALLOWED_EXTENSIONS:
            raise MediaValidationError(
                "仅支持 MP4、MOV 视频文件；系统会从视频中提取音轨转写。"
            )
        if not content:
            raise MediaValidationError("上传文件为空。")
        if len(content) > max_media_bytes:
            limit_mb = max_media_bytes // (1024 * 1024)
            raise MediaValidationError(f"单个媒体文件不能超过{limit_mb}MB。")
        if not _has_valid_signature(extension, content):
            raise MediaValidationError("文件内容与扩展名不匹配，已拒绝处理。")

    def _probe(self, input_path: Path) -> float:
        try:
            result = self.command_runner(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(input_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except OSError as exc:
            logger.exception("转写视频检查组件不可用")
            raise MediaValidationError(
                "视频检查组件暂不可用，请重新打开软件后再试；仍失败请联系服务人员更新软件。",
                code="media_tools_unavailable",
            ) from exc
        if result.returncode != 0:
            raise MediaValidationError("媒体文件无法解析或已经损坏。")
        try:
            payload = json.loads(result.stdout or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise MediaValidationError("媒体文件无法解析或已经损坏。") from exc
        if not any(
            stream.get("codec_type") == "audio" for stream in payload.get("streams", [])
        ):
            raise MediaValidationError("媒体中没有可识别的音轨。")
        try:
            duration = float(payload.get("format", {}).get("duration", 0))
        except (TypeError, ValueError) as exc:
            raise MediaValidationError("无法读取媒体时长。") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise MediaValidationError("媒体时长无效，请检查文件后重新上传。")
        if duration > MAX_DURATION_SECONDS:
            raise MediaValidationError("媒体时长不能超过15分钟。")
        return duration

    def _extract_audio(self, input_path: Path, wav_path: Path) -> None:
        try:
            result = self.command_runner(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(input_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    "-y",
                    str(wav_path),
                ],
                capture_output=True,
                timeout=180,
                check=False,
            )
        except OSError as exc:
            logger.exception("转写音频处理组件不可用")
            raise MediaValidationError(
                "音频处理组件暂不可用，请重新打开软件后再试；仍失败请联系服务人员更新软件。",
                code="media_tools_unavailable",
            ) from exc
        if result.returncode != 0 or not wav_path.exists():
            raise MediaValidationError("音频提取失败，请检查媒体文件。")

    def _load_asr_model(self, model_name: str) -> Any:
        try:
            return retry_with_policy(
                lambda: self.model_loader(model_name),
                policy=RetryPolicy(max_attempts=2, base_delay=1.0),
                retry_for=(ConnectionError, TimeoutError, OSError, RuntimeError),
                error_message="识别模型加载失败，已自动重试 1 次；请稍后重新上传。",
            )
        except ExternalServiceError as exc:
            raise TranscriptionError(
                "识别模型加载失败，已自动重试 1 次；请稍后重新上传。",
                code="model_unavailable",
            ) from exc.__cause__

    def _transcribe(
        self,
        wav_path: Path,
        model_name: str,
        hotwords: str = "",
        *,
        language: str = "zh",
        include_word_timestamps: bool = False,
    ) -> tuple[list[TranscriptSegment], str]:
        model = self._load_asr_model(model_name)
        return self._transcribe_with_model(
            model,
            wav_path,
            hotwords,
            language=language,
            include_word_timestamps=include_word_timestamps,
        )

    def _transcribe_with_model(
        self,
        model: Any,
        wav_path: Path,
        hotwords: str = "",
        *,
        language: str = "zh",
        include_word_timestamps: bool = False,
    ) -> tuple[list[TranscriptSegment], str]:
        try:
            options: dict[str, Any] = {"vad_filter": True, "beam_size": 5}
            if language != "auto":
                options["language"] = language
            if hotwords:
                options["hotwords"] = hotwords
            if include_word_timestamps:
                options["word_timestamps"] = True
            raw_segments, info = model.transcribe(str(wav_path), **options)
        except Exception as exc:
            raise TranscriptionError(
                "语音识别失败，请检查音轨后重新上传。",
                code="asr_failed",
            ) from exc
        segments = []
        for item in raw_segments:
            text = str(item.text).strip()
            if not text:
                continue
            confidence = max(0.0, min(1.0, math.exp(float(item.avg_logprob))))
            words: list[dict[str, Any]] = []
            if include_word_timestamps:
                for raw_word in getattr(item, "words", None) or []:
                    try:
                        word_start = float(getattr(raw_word, "start"))
                        word_end = float(getattr(raw_word, "end"))
                    except (TypeError, ValueError, AttributeError):
                        continue
                    word_text = str(
                        getattr(raw_word, "word", getattr(raw_word, "text", ""))
                    ).strip()
                    if (
                        not word_text
                        or not math.isfinite(word_start)
                        or not math.isfinite(word_end)
                        or word_end <= word_start
                        or word_start < float(item.start)
                        or word_end > float(item.end)
                    ):
                        continue
                    probability = getattr(raw_word, "probability", None)
                    word: dict[str, Any] = {
                        "start": round(word_start, 3),
                        "end": round(word_end, 3),
                        "text": word_text,
                    }
                    if probability is not None:
                        try:
                            word["probability"] = max(
                                0.0, min(1.0, float(probability))
                            )
                        except (TypeError, ValueError):
                            pass
                    words.append(word)
            segments.append(
                TranscriptSegment(
                    start=float(item.start),
                    end=float(item.end),
                    text=text,
                    confidence=confidence,
                    needs_review=confidence < 0.75,
                    words=words,
                )
            )
        if not segments:
            raise MediaValidationError("没有识别到有效语音内容。")
        return segments, str(getattr(info, "language", "zh"))

    def _extract_review_clip(
        self,
        wav_path: Path,
        segment: TranscriptSegment,
        index: int,
    ) -> Path:
        assert segment.start is not None and segment.end is not None
        clip_start = max(0.0, segment.start - 0.6)
        clip_duration = max(0.1, (segment.end - segment.start) + 1.2)
        clip_path = wav_path.parent / f"review-{index}.wav"
        result = self.command_runner(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-ss",
                f"{clip_start:.3f}",
                "-t",
                f"{clip_duration:.3f}",
                "-i",
                str(wav_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-y",
                str(clip_path),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 or not clip_path.exists():
            raise TranscriptionError(
                "低置信片段二次识别失败。", code="segment_rerecognition_failed"
            )
        return clip_path

    @staticmethod
    def _candidate_from_clip(
        model: Any, clip_path: Path, language: str
    ) -> tuple[str, float] | None:
        options: dict[str, Any] = {
            "vad_filter": False,
            "beam_size": 8,
            "condition_on_previous_text": False,
        }
        if language != "auto":
            options["language"] = language
        raw_segments, _ = model.transcribe(str(clip_path), **options)
        items = [
            item for item in raw_segments if str(getattr(item, "text", "")).strip()
        ]
        if not items:
            return None
        text = "".join(str(item.text).strip() for item in items)
        confidence = max(
            max(0.0, min(1.0, math.exp(float(item.avg_logprob)))) for item in items
        )
        return text, confidence

    @staticmethod
    def _normalized_transcript_text(value: str) -> str:
        return re.sub(r"[\s，。！？、,.!?]", "", value).casefold()

    @classmethod
    def _has_sensitive_disagreement(cls, candidates: list[str]) -> bool:
        pattern = re.compile(r"\d+(?:\.\d+)?(?:元|块|%|号|岁|年|月|日|次|公里|斤)?")
        values = [
            {match.group(0) for match in pattern.finditer(item)} for item in candidates
        ]
        return len({tuple(sorted(value)) for value in values}) > 1

    def _call_transcript_reviewer(
        self,
        *,
        previous_text: str,
        next_text: str,
        candidates: list[str],
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self.transcript_reviewer is None:
            return None, "低置信口播修订未配置，已使用本地识别最佳结果。"
        for attempt in range(2):
            try:
                result = self.transcript_reviewer(
                    previous_text=previous_text,
                    next_text=next_text,
                    candidates=candidates,
                )
                if not isinstance(result, dict):
                    raise ValueError("无效的AI复核结果")
                return result, None
            except Exception as exc:
                retryable = bool(getattr(exc, "retryable", False)) or isinstance(
                    exc, (ConnectionError, TimeoutError)
                )
                if attempt == 0 and retryable:
                    continue
                return None, "低置信口播修订不可用，已使用本地识别最佳结果。"
        return None, "低置信口播修订不可用，已使用本地识别最佳结果。"

    @staticmethod
    def _sensitive_transcript_tokens(value: str) -> set[str]:
        pattern = re.compile(
            r"(?:\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万两]+)"
            r"(?:元|块|%|％|号|岁|年|月|日|次|公里|斤|万|千|百)?"
        )
        return {match.group(0) for match in pattern.finditer(value)}

    def _call_transcript_batch_reviewer(
        self,
        *,
        segments: list[dict[str, Any]],
        context_hint: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self.transcript_batch_reviewer is None:
            return None, "AI 自动校对未配置，原转写已保留。"
        for attempt in range(2):
            try:
                result = self.transcript_batch_reviewer(
                    segments=segments,
                    context_hint=context_hint,
                )
                if not isinstance(result, dict) or not isinstance(
                    result.get("corrections"), list
                ):
                    raise ValueError("无效的 AI 批量校对结果")
                return result, None
            except Exception as exc:
                retryable = bool(getattr(exc, "retryable", False)) or isinstance(
                    exc, (ConnectionError, TimeoutError)
                )
                if attempt == 0 and retryable:
                    continue
                return None, "AI 自动校对暂不可用，原转写已保留。"
        return None, "AI 自动校对暂不可用，原转写已保留。"

    def _auto_review_cloud_segments(
        self,
        segments: list[TranscriptSegment],
        *,
        context_hint: str,
    ) -> tuple[list[TranscriptSegment], dict[str, Any]]:
        payload = [
            {
                "index": index,
                "text": segment.text,
                "confidence": segment.confidence,
                "needs_review": segment.needs_review,
            }
            for index, segment in enumerate(segments)
        ]
        decision, review_error = self._call_transcript_batch_reviewer(
            segments=payload,
            context_hint=context_hint,
        )
        by_index = {
            int(item["index"]): item
            for item in (decision or {}).get("corrections", [])
            if isinstance(item, dict) and isinstance(item.get("index"), int)
        }
        reviewed: list[TranscriptSegment] = []
        uncertain_count = 0
        llm_review_count = 0
        for index, segment in enumerate(segments):
            if not segment.needs_review:
                reviewed.append(segment)
                continue
            correction = by_index.get(index)
            if correction is None:
                uncertain_count += 1
                reviewed.append(
                    segment.model_copy(
                        update={
                            "quality_status": "uncertain",
                            "quality_note": review_error or "AI 无法可靠恢复该处原话。",
                            "alternatives": [segment.text],
                        }
                    )
                )
                continue
            llm_review_count += 1
            proposed_text = str(correction.get("corrected_text") or "").strip()
            sensitive_changed = self._sensitive_transcript_tokens(
                proposed_text
            ) != self._sensitive_transcript_tokens(segment.text)
            requires_human_review = (
                bool(correction.get("requires_human_review")) or sensitive_changed
            )
            corrected_text = segment.text if sensitive_changed else proposed_text
            if not corrected_text:
                corrected_text = segment.text
                requires_human_review = True
            if requires_human_review:
                uncertain_count += 1
            note = str(correction.get("note") or "").strip()
            if sensitive_changed:
                note = "AI 检测到金额、数字或日期可能被改变，已保留原文。"
            elif requires_human_review:
                note = note or "AI 已先校对，但该处事实无法从上下文唯一确定。"
            else:
                note = note or "AI 已结合上下文完成保守校对。"
            reviewed.append(
                segment.model_copy(
                    update={
                        "text": corrected_text,
                        "needs_review": requires_human_review,
                        "quality_status": (
                            "uncertain" if requires_human_review else "llm_rewritten"
                        ),
                        "quality_source": "llm_context",
                        "quality_note": note[:120],
                        "alternatives": [segment.text],
                    }
                )
            )
        return reviewed, {
            "auto_reviewed": decision is not None,
            "uncertain_segment_count": uncertain_count,
            "llm_review_count": llm_review_count,
            "auto_review_error": review_error,
        }

    def _auto_review_segments(
        self,
        model: Any,
        wav_path: Path,
        segments: list[TranscriptSegment],
        *,
        language: str,
    ) -> tuple[list[TranscriptSegment], dict[str, Any]]:
        reviewed: list[TranscriptSegment] = []
        secondary_asr_count = 0
        llm_review_count = 0
        uncertain_segment_count = 0
        review_error: str | None = None
        for index, segment in enumerate(segments):
            confidence = segment.confidence or 0.0
            if confidence >= 0.75 or segment.start is None or segment.end is None:
                reviewed.append(
                    segment.model_copy(
                        update={
                            "needs_review": False,
                            "quality_status": "accepted",
                            "quality_source": "primary_asr",
                        }
                    )
                )
                continue

            candidates = [segment.text]
            secondary_confidence = confidence
            try:
                clip_path = self._extract_review_clip(wav_path, segment, index)
                retry_candidate = self._candidate_from_clip(model, clip_path, language)
                if retry_candidate is not None:
                    secondary_asr_count += 1
                    retry_text, secondary_confidence = retry_candidate
                    if self._normalized_transcript_text(
                        retry_text
                    ) != self._normalized_transcript_text(segment.text):
                        candidates.append(retry_text)
            except Exception:
                review_error = "部分低置信片段未能完成二次识别，已保留首次识别结果。"

            decision, decision_error = self._call_transcript_reviewer(
                previous_text=segments[index - 1].text if index else "",
                next_text=segments[index + 1].text if index + 1 < len(segments) else "",
                candidates=candidates,
            )
            if decision_error:
                review_error = decision_error
            if decision is not None:
                llm_review_count += 1
            corrected_text = str((decision or {}).get("corrected_text") or "").strip()
            if not corrected_text:
                uncertain_segment_count += 1
                corrected_text = candidates[0]
            reviewed.append(
                segment.model_copy(
                    update={
                        "text": corrected_text,
                        "confidence": max(confidence, secondary_confidence),
                        "needs_review": False,
                        "quality_status": "llm_rewritten"
                        if decision is not None
                        else "uncertain",
                        "quality_source": "llm_context"
                        if decision is not None
                        else "secondary_asr",
                        "quality_note": str(
                            (decision or {}).get("note")
                            or decision_error
                            or "低置信片段已保留本地识别最佳结果。"
                        )[:120],
                        "alternatives": candidates,
                    }
                )
            )
        return reviewed, {
            "auto_reviewed": True,
            "uncertain_segment_count": uncertain_segment_count,
            "secondary_asr_count": secondary_asr_count,
            "llm_review_count": llm_review_count,
            "auto_review_error": review_error,
        }

    def _update_task(
        self,
        task: TranscriptionTask,
        *,
        stage: str,
        progress: int,
        duration_seconds: float | None = None,
        on_progress: Callable[[TranscriptionTask], None] | None = None,
    ) -> TranscriptionTask:
        update = {
            "stage": stage,
            "progress": progress,
            "updated_at": datetime.now().astimezone(),
        }
        if duration_seconds is not None:
            update["duration_seconds"] = duration_seconds
        updated = task.model_copy(update=update)
        self._save_task(updated, on_progress)
        return updated

    def _save_task(
        self,
        task: TranscriptionTask,
        on_progress: Callable[[TranscriptionTask], None] | None,
    ) -> None:
        self.repository.save_task(task)
        if on_progress is not None:
            try:
                on_progress(task)
            except Exception:
                pass

    @staticmethod
    def _safe_error(exc: Exception, task_id: str) -> TranscriptionError:
        if isinstance(exc, TranscriptionError):
            exc.task_id = task_id
            return exc
        return TranscriptionError(
            "本地转写处理失败，请重新上传后再试。",
            code="processing_failed",
            task_id=task_id,
        )

    @staticmethod
    def _validate_segments(segments: list[TranscriptSegment]) -> None:
        if not segments:
            raise TranscriptionError("转写结果不能为空。", code="empty_transcript")
        has_timing = [
            segment.start is not None and segment.end is not None
            for segment in segments
        ]
        if any(has_timing) and not all(has_timing):
            raise TranscriptionError(
                "同一份文案不能混用有时间轴和无时间轴片段。",
                code="mixed_segment_timing",
            )
        if not any(has_timing):
            return
        previous_end = 0.0
        for segment in segments:
            assert segment.start is not None and segment.end is not None
            if not math.isfinite(segment.start) or not math.isfinite(segment.end):
                raise TranscriptionError(
                    "片段时间无效，请刷新后重新校对。",
                    code="invalid_segment_time",
                )
            if segment.start < previous_end:
                raise TranscriptionError(
                    "转写片段时间发生重叠，请刷新后重新校对。",
                    code="overlapping_segments",
                )
            previous_end = segment.end

    def save_revision(
        self,
        task_id: str,
        segments: list[TranscriptSegment],
        *,
        reviewer: str,
        approve: bool = False,
    ) -> TranscriptRevision:
        task = self.repository.get_task(task_id)
        if not isinstance(task, TranscriptionTask) or task.is_mock:
            raise TranscriptionError(
                "未找到可保存的真实转写任务。", code="task_not_found"
            )
        self._validate_segments(segments)
        reviewer = reviewer.strip()
        if not reviewer:
            raise TranscriptionError(
                "请填写校对人后再保存。", code="reviewer_required", task_id=task_id
            )
        if approve and any(
            (
                (segment.confidence is not None and segment.confidence < 0.75)
                or segment.needs_review
            )
            and not segment.reviewed
            for segment in segments
        ):
            raise TranscriptionError(
                "仍有低置信度片段未标记“已复核”，暂不能确认成稿。",
                code="review_required",
                task_id=task_id,
            )
        revisions = self.repository.list_transcript_revisions(task_id)
        now = datetime.now().astimezone()
        revision = TranscriptRevision(
            revision_id=f"revision-{uuid4().hex[:12]}",
            task_id=task_id,
            revision_number=len(revisions) + 1,
            status=TranscriptStatus.APPROVED if approve else TranscriptStatus.DRAFT,
            created_at=now,
            updated_at=now,
            reviewer=reviewer,
            approval_mode="manual",
            language=task.language or "zh",
            model_name=task.model_name or "base",
            media_sha256=task.media_sha256 or "",
            original_segments=task.segments,
            corrected_segments=segments,
        )
        try:
            self.repository.save_transcript_revision(revision)
        except ValueError as exc:
            raise TranscriptionError(
                "校对版本发生冲突，请刷新页面后基于最新版本继续校对。",
                code="revision_conflict",
                task_id=task_id,
            ) from exc
        if approve:
            self.repository.save_task(
                task.model_copy(
                    update={
                        "approved_revision_id": revision.revision_id,
                        "stage": "已完成",
                        "outputs": {
                            "transcript.txt": "ready",
                            "transcript.json": "ready",
                            "subtitles.srt": "ready",
                        },
                    }
                )
            )
        return revision

    def _save_auto_revision(
        self,
        task: TranscriptionTask,
        segments: list[TranscriptSegment],
    ) -> tuple[TranscriptRevision, TranscriptionTask]:
        """保存 AI 自动成稿，和人工版本保持可追溯的明确边界。"""
        revisions = self.repository.list_transcript_revisions(task.task_id)
        now = datetime.now().astimezone()
        revision = TranscriptRevision(
            revision_id=f"revision-{uuid4().hex[:12]}",
            task_id=task.task_id,
            revision_number=len(revisions) + 1,
            status=TranscriptStatus.APPROVED,
            created_at=now,
            updated_at=now,
            reviewer="AI自动质检",
            approval_mode="ai_auto",
            language=task.language or "zh",
            model_name=task.model_name or "base",
            media_sha256=task.media_sha256 or "",
            original_segments=task.segments,
            corrected_segments=segments,
        )
        try:
            self.repository.save_transcript_revision(revision)
        except ValueError as exc:
            raise TranscriptionError(
                "AI自动成稿保存冲突，请刷新后重试。",
                code="revision_conflict",
                task_id=task.task_id,
            ) from exc
        updated_task = task.model_copy(
            update={
                "approved_revision_id": revision.revision_id,
                "stage": "AI自动成稿",
                "outputs": {
                    "transcript.txt": "ready",
                    "transcript.json": "ready",
                    "subtitles.srt": "ready",
                },
            }
        )
        return revision, updated_task

    def import_manual_text(
        self,
        *,
        text: str,
        rights_confirmed: bool,
        rights_holder: str,
        media_name: str = "豆包人工转写",
        candidate_id: str | None = None,
        source_url: str | None = None,
    ) -> TranscriptionTask:
        """Create a reviewable, zero-cost transcript from user-pasted text.

        This intentionally records no artificial timestamps and never automates a
        third-party consumer product. The user remains responsible for the source
        and confirms the right to process it.
        """
        if not rights_confirmed:
            raise TranscriptionError("请先确认拥有媒体处理权。", code="rights_required")
        owner = rights_holder.strip()
        if not owner:
            raise TranscriptionError(
                "请填写权利确认人。", code="rights_holder_required"
            )
        paragraphs = [
            item.strip() for item in re.split(r"\n\s*\n", text.strip()) if item.strip()
        ]
        if not paragraphs:
            raise TranscriptionError("请粘贴需要回填的文案。", code="empty_transcript")
        now = datetime.now().astimezone()
        segments = [
            TranscriptSegment(
                start=None,
                end=None,
                text=paragraph,
                confidence=None,
                needs_review=True,
                reviewed=False,
            )
            for paragraph in paragraphs
        ]
        task = TranscriptionTask(
            task_id=f"transcript-{uuid4().hex[:10]}",
            title=f"人工导入：{media_name.strip() or '外部文案'}",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            media_name=media_name.strip() or "外部文案",
            media_type="text/plain",
            rights_confirmed=True,
            rights_holder=owner,
            rights_confirmed_at=now,
            candidate_id=candidate_id,
            segments=segments,
            stage="待人工复核",
            is_mock=False,
            model_name="manual_text",
            language="zh",
            source_kind="manual_text",
            source_url=source_url.strip() if source_url else None,
            timing_available=False,
            outputs={"transcript.txt": "draft", "transcript.json": "draft"},
        )
        self.repository.save_task(task)
        self.save_revision(task.task_id, segments, reviewer=owner)
        return task

    def create_mock_task(
        self,
        *,
        media_name: str,
        media_type: str,
        rights_confirmed: bool,
        candidate_id: str | None = None,
    ) -> TranscriptionTask:
        if not rights_confirmed:
            raise ValueError("必须确认拥有媒体处理权后才能创建转写任务。")
        now = datetime.now().astimezone()
        task = TranscriptionTask(
            task_id=f"transcript-{uuid4().hex[:8]}",
            title=media_name,
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            elapsed_seconds=2.4,
            media_name=media_name,
            media_type=media_type,
            rights_confirmed=True,
            candidate_id=candidate_id,
            segments=demo_segments(),
            stage="演示完成",
            is_mock=True,
            model_name="演示数据",
        )
        self.repository.save_task(task)
        return task

    def retry_failed_task(self, task_id: str) -> TranscriptionTask:
        task = self.repository.get_task(task_id)
        if not isinstance(task, TranscriptionTask):
            raise ValueError("未找到可重试的转写任务。")
        if task.status != TaskStatus.FAILED:
            raise ValueError("只有失败任务可以重试。")
        if task.retry_count >= 1:
            raise ValueError("该任务已经自动重试过一次。")
        if not task.is_mock:
            raise ValueError("真实媒体已安全清理，请重新上传后再试。")
        now = datetime.now().astimezone()
        updated = task.model_copy(
            update={
                "status": TaskStatus.SUCCEEDED,
                "progress": 100,
                "updated_at": now,
                "elapsed_seconds": (task.elapsed_seconds or 0) + 2.1,
                "error_message": None,
                "retry_count": 1,
                "segments": demo_segments(),
                "stage": "演示完成",
            }
        )
        self.repository.save_task(updated)
        return updated

    def get_approved_revision(self, task_id: str) -> TranscriptRevision | None:
        task = self.repository.get_task(task_id)
        if not isinstance(task, TranscriptionTask) or not task.approved_revision_id:
            return None
        revision = self.repository.get_transcript_revision(task.approved_revision_id)
        if (
            revision is None
            or revision.task_id != task.task_id
            or revision.status != TranscriptStatus.APPROVED
        ):
            return None
        return revision

    def list_tasks(self):
        return self.repository.list_tasks()

    @staticmethod
    def export_txt(segments: list[TranscriptSegment]) -> bytes:
        return "\n".join(segment.text for segment in segments).encode("utf-8")

    @staticmethod
    def export_json(segments: list[TranscriptSegment]) -> bytes:
        payload = {"segments": [segment.model_dump() for segment in segments]}
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    @staticmethod
    def export_srt(segments: list[TranscriptSegment]) -> bytes:
        def timestamp(seconds: float) -> str:
            milliseconds = round(seconds * 1_000)
            hours, remainder = divmod(milliseconds, 3_600_000)
            minutes, remainder = divmod(remainder, 60_000)
            secs, millis = divmod(remainder, 1_000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

        blocks = [
            f"{index}\n{timestamp(segment.start)} --> {timestamp(segment.end)}\n{segment.text}"
            for index, segment in enumerate(segments, start=1)
        ]
        return ("\n\n".join(blocks) + "\n").encode("utf-8")

    @staticmethod
    def export_ass(segments: list[TranscriptSegment]) -> bytes:
        """导出 ASS 字幕，保持原始文本，仅做格式所需转义。"""

        def timestamp(seconds: float) -> str:
            centiseconds = round(seconds * 100)
            hours, remainder = divmod(centiseconds, 360_000)
            minutes, remainder = divmod(remainder, 6_000)
            secs, cents = divmod(remainder, 100)
            return f"{hours}:{minutes:02d}:{secs:02d}.{cents:02d}"

        header = """[Script Info]
Title: VideoInsight Subtitle
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Microsoft YaHei,20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
        lines = []
        for segment in segments:
            text = segment.text.replace("\r", "").replace("\n", r"\N")
            lines.append(
                "Dialogue: 0,"
                f"{timestamp(segment.start)},{timestamp(segment.end)},"
                f"Default,,0,0,0,,{text}"
            )
        return (header + "\n".join(lines) + "\n").encode("utf-8-sig")
