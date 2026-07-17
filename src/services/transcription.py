from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from src.contracts import TaskRepository
from src.mock_data import demo_segments
from src.models import TaskStatus, TranscriptSegment, TranscriptionTask


class TranscriptionService:
    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

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
            outputs={
                "transcript.txt": "ready",
                "transcript.json": "ready",
                "subtitles.srt": "ready",
            },
            media_name=media_name,
            media_type=media_type,
            rights_confirmed=True,
            candidate_id=candidate_id,
            segments=demo_segments(),
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
                "outputs": {
                    "transcript.txt": "ready",
                    "transcript.json": "ready",
                    "subtitles.srt": "ready",
                },
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
        payload = {
            "mock": True,
            "segments": [segment.model_dump() for segment in segments],
        }
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
