from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
import subprocess
import tempfile
from time import monotonic
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

MAX_MEDIA_BYTES = 50 * 1024 * 1024
MAX_DURATION_SECONDS = 15 * 60
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4a", ".mp3", ".wav"}


class MediaValidationError(ValueError):
    pass


def _has_valid_signature(extension: str, content: bytes) -> bool:
    if extension in {".mp4", ".mov", ".m4a"}:
        return len(content) >= 12 and content[4:8] == b"ftyp"
    if extension == ".wav":
        return (
            len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WAVE"
        )
    if extension == ".mp3":
        return content.startswith(b"ID3") or (
            len(content) >= 2 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0
        )
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
        model_name: str = "base",
    ) -> TranscriptionTask:
        if not rights_confirmed:
            raise ValueError("必须确认拥有媒体处理权后才能创建转写任务。")
        if not rights_holder.strip():
            raise ValueError("请填写媒体权利主体。")
        self._validate_upload(media_name, media_bytes)
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
            stage="文件检查",
            media_sha256=hashlib.sha256(media_bytes).hexdigest(),
            model_name=model_name,
            is_mock=False,
        )
        self.repository.save_task(task)
        started = monotonic()
        extension = Path(media_name).suffix.casefold()
        try:
            with tempfile.TemporaryDirectory(prefix=f"video-{task.task_id}-") as temp:
                input_path = Path(temp) / f"input{extension}"
                wav_path = Path(temp) / "audio.wav"
                input_path.write_bytes(media_bytes)
                duration = self._probe(input_path)
                if duration > MAX_DURATION_SECONDS:
                    raise MediaValidationError("媒体时长不能超过15分钟。")
                task = self._update_task(task, stage="音频提取", progress=25)
                self._extract_audio(input_path, wav_path)
                task = self._update_task(task, stage="语音识别", progress=50)
                segments, language = self._transcribe(wav_path, model_name)
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
            self.repository.save_task(task)
            self.save_revision(task.task_id, segments, reviewer=rights_holder)
            return task
        except Exception as exc:
            failed = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "stage": "处理失败",
                    "updated_at": datetime.now().astimezone(),
                    "elapsed_seconds": round(monotonic() - started, 2),
                    "error_message": str(exc),
                }
            )
            self.repository.save_task(failed)
            raise

    @staticmethod
    def _validate_upload(media_name: str, content: bytes) -> None:
        extension = Path(media_name).suffix.casefold()
        if extension not in ALLOWED_EXTENSIONS:
            raise MediaValidationError("仅支持 MP4、MOV、M4A、MP3、WAV 文件。")
        if not content:
            raise MediaValidationError("上传文件为空。")
        if len(content) > MAX_MEDIA_BYTES:
            raise MediaValidationError("单个媒体文件不能超过50MB。")
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
        payload = json.loads(result.stdout or "{}")
        if not any(
            stream.get("codec_type") == "audio" for stream in payload.get("streams", [])
        ):
            raise MediaValidationError("媒体中没有可识别的音轨。")
        try:
            return float(payload.get("format", {}).get("duration", 0))
        except (TypeError, ValueError) as exc:
            raise MediaValidationError("无法读取媒体时长。") from exc

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
        self, wav_path: Path, model_name: str
    ) -> tuple[list[TranscriptSegment], str]:
        model = None
        last_error: BaseException | None = None
        for _ in range(2):
            try:
                model = self.model_loader(model_name)
                break
            except (ConnectionError, TimeoutError, OSError, RuntimeError) as exc:
                last_error = exc
        if model is None:
            raise RuntimeError(
                f"识别模型加载失败，已自动重试一次：{last_error}"
            ) from last_error
        raw_segments, info = model.transcribe(
            str(wav_path), language="zh", vad_filter=True, beam_size=5
        )
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
        self, task: TranscriptionTask, *, stage: str, progress: int
    ) -> TranscriptionTask:
        updated = task.model_copy(
            update={
                "stage": stage,
                "progress": progress,
                "updated_at": datetime.now().astimezone(),
            }
        )
        self.repository.save_task(updated)
        return updated

    @staticmethod
    def _validate_segments(segments: list[TranscriptSegment]) -> None:
        if not segments:
            raise ValueError("转写结果不能为空。")
        previous_end = 0.0
        for segment in segments:
            if segment.start < previous_end:
                raise ValueError("转写片段时间发生重叠。")
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
            raise ValueError("未找到可保存的真实转写任务。")
        self._validate_segments(segments)
        revisions = self.repository.list_transcript_revisions(task_id)
        now = datetime.now().astimezone()
        revision = TranscriptRevision(
            revision_id=f"revision-{uuid4().hex[:12]}",
            task_id=task_id,
            revision_number=len(revisions) + 1,
            status=TranscriptStatus.APPROVED if approve else TranscriptStatus.DRAFT,
            created_at=now,
            updated_at=now,
            reviewer=reviewer.strip() or "内容审核员",
            language=task.language or "zh",
            model_name=task.model_name or "base",
            media_sha256=task.media_sha256 or "",
            original_segments=task.segments,
            corrected_segments=segments,
        )
        self.repository.save_transcript_revision(revision)
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
