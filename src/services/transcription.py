from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
import subprocess
import tempfile
from time import monotonic
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
ALLOWED_ASR_MODELS = {"base", "medium", "large-v3-turbo"}
ALLOWED_ASR_LANGUAGES = {"auto", "zh", "en", "ja", "ko"}
MAX_HOTWORDS_LENGTH = 500


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


class TranscriptionService:
    def __init__(
        self,
        repository: TaskRepository,
        *,
        model_loader=load_asr_model,
        command_runner=subprocess.run,
    ) -> None:
        self.repository = repository
        self.model_loader = model_loader
        self.command_runner = command_runner

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
                segments, language = self._transcribe(
                    wav_path,
                    model_name,
                    normalized_hotwords,
                    language=language,
                )
                self._validate_segments(segments)
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "stage": "待校对",
                    "progress": 100,
                    "updated_at": datetime.now().astimezone(),
                    "elapsed_seconds": round(monotonic() - started, 2),
                    "segments": segments,
                    "language": language,
                }
            )
            self._save_task(task, on_progress)
            self.save_revision(task.task_id, segments, reviewer=rights_holder)
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
        if result.returncode != 0 or not wav_path.exists():
            raise MediaValidationError("音频提取失败，请检查媒体文件。")

    def _transcribe(
        self,
        wav_path: Path,
        model_name: str,
        hotwords: str = "",
        *,
        language: str = "zh",
    ) -> tuple[list[TranscriptSegment], str]:
        try:
            model = retry_with_policy(
                lambda: self.model_loader(model_name),
                policy=RetryPolicy(max_attempts=3, base_delay=1.0),
                retry_for=(ConnectionError, TimeoutError, OSError, RuntimeError),
                error_message="识别模型加载失败，已自动重试 2 次；请稍后重新上传。",
            )
        except ExternalServiceError as exc:
            raise TranscriptionError(
                "识别模型加载失败，已自动重试 2 次；请稍后重新上传。",
                code="model_unavailable",
            ) from exc.__cause__
        try:
            options: dict[str, Any] = {"vad_filter": True, "beam_size": 5}
            if language != "auto":
                options["language"] = language
            if hotwords:
                options["hotwords"] = hotwords
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
            segments.append(
                TranscriptSegment(
                    start=float(item.start),
                    end=float(item.end),
                    text=text,
                    confidence=confidence,
                    needs_review=confidence < 0.75,
                )
            )
        if not segments:
            raise MediaValidationError("没有识别到有效语音内容。")
        return segments, str(getattr(info, "language", "zh"))

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
        has_timing = [segment.start is not None and segment.end is not None for segment in segments]
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
            raise TranscriptionError("请填写权利确认人。", code="rights_holder_required")
        paragraphs = [
            item.strip()
            for item in re.split(r"\n\s*\n", text.strip())
            if item.strip()
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
