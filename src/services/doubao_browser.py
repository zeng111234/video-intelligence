from __future__ import annotations

import re
from datetime import datetime
from dataclasses import dataclass
from uuid import uuid4

from src.contracts import TaskRepository
from src.models import (
    Platform,
    TaskStatus,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptStatus,
    TranscriptionTask,
    VideoCandidate,
)
from src.services.transcription import TranscriptionError

DOUBAO_BROWSER_SOURCE_KIND = "doubao_browser"
DOUBAO_BROWSER_MODEL_NAME = "doubao_browser"
DOUBAO_PROMPT_VERSION = "doubao-original-transcript-v1"
DOUBAO_MOBILE_SOURCE_KIND = "doubao_mobile"
DOUBAO_MOBILE_MODEL_NAME = "doubao_mobile"
DOUBAO_MOBILE_PROMPT_VERSION = "doubao-mobile-original-transcript-v1"
DOUYIN_SHORT_URL_RE = re.compile(r"https://v\.douyin\.com/[A-Za-z0-9_-]+/?")


class DoubaoBrowserAutomationError(RuntimeError):
    def __init__(self, user_message: str, *, status_code: int = 400) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.status_code = status_code


@dataclass(frozen=True)
class DoubaoAutomationProfile:
    source_kind: str = DOUBAO_BROWSER_SOURCE_KIND
    model_name: str = DOUBAO_BROWSER_MODEL_NAME
    prompt_version: str = DOUBAO_PROMPT_VERSION
    rights_holder: str = "本地浏览器豆包链路"
    initial_stage: str = "等待专用浏览器接管"
    claimed_stage: str = "专用浏览器正在打开抖音"
    reviewer: str = "本地浏览器豆包链路"


class DoubaoBrowserAutomationService:
    """Queue and persist zero-cost Doubao browser transcript jobs.

    The service only manages local task state. A separate local browser worker
    performs user-authorized page interactions and calls claim/complete/fail.
    """

    def __init__(
        self,
        repository: TaskRepository,
        profile: DoubaoAutomationProfile | None = None,
    ) -> None:
        self.repository = repository
        self.profile = profile or DoubaoAutomationProfile()

    def create_job(self, candidate: VideoCandidate) -> TranscriptionTask:
        if candidate.platform != Platform.DOUYIN:
            raise DoubaoBrowserAutomationError("零成本豆包自动提取目前只支持抖音候选。")
        if not candidate.source_url:
            raise DoubaoBrowserAutomationError("当前候选没有原视频链接，无法打开抖音页面取分享短链。")

        existing = self.find_latest_job_for_candidate(candidate.video_id)
        if existing and existing.status in {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.SUCCEEDED,
        }:
            return existing

        now = datetime.now().astimezone()
        task = TranscriptionTask(
            task_id=f"transcript-{uuid4().hex[:10]}",
            title=f"豆包免费提取：{candidate.title[:80]}",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            media_name=candidate.title,
            media_type="text/plain",
            rights_confirmed=True,
            rights_holder=self.profile.rights_holder,
            rights_confirmed_at=now,
            rights_purpose="仅用于本次本地自动化文案提取与人工复核",
            candidate_id=candidate.video_id,
            segments=[],
            stage=self.profile.initial_stage,
            is_mock=False,
            model_name=self.profile.model_name,
            language="zh",
            source_kind=self.profile.source_kind,
            source_url=str(candidate.source_url),
            timing_available=False,
            outputs={
                "candidate_id": candidate.video_id,
                "candidate_title": candidate.title,
                "source_url": str(candidate.source_url),
                "prompt_version": self.profile.prompt_version,
                "fee_cny": "0",
                "review_required": "true",
            },
        )
        self.repository.save_task(task)
        return task

    def list_jobs(
        self, *, candidate_id: str | None = None, limit: int = 50
    ) -> list[TranscriptionTask]:
        tasks = [
            task
            for task in self.repository.list_tasks()
            if isinstance(task, TranscriptionTask)
            and task.source_kind == self.profile.source_kind
        ]
        if candidate_id:
            tasks = [task for task in tasks if task.candidate_id == candidate_id]
        return tasks[: max(1, min(limit, 100))]

    def find_latest_job_for_candidate(
        self, candidate_id: str
    ) -> TranscriptionTask | None:
        for task in self.list_jobs(candidate_id=candidate_id, limit=100):
            return task
        return None

    def claim_next_job(self, worker_id: str) -> TranscriptionTask | None:
        worker = worker_id.strip()[:80] or "local-browser-worker"
        for task in self.list_jobs(limit=100):
            if task.status != TaskStatus.QUEUED:
                continue
            now = datetime.now().astimezone()
            claimed = task.model_copy(
                update={
                    "status": TaskStatus.RUNNING,
                    "progress": 10,
                    "stage": self.profile.claimed_stage,
                    "updated_at": now,
                    "outputs": {**task.outputs, "worker_id": worker},
                }
            )
            self.repository.save_task(claimed)
            return claimed
        return None

    def mark_stage(
        self,
        task_id: str,
        *,
        stage: str,
        progress: int | None = None,
        outputs: dict[str, str] | None = None,
    ) -> TranscriptionTask:
        task = self._get_job(task_id)
        update = {
            "stage": stage.strip()[:120] or task.stage,
            "updated_at": datetime.now().astimezone(),
            "outputs": {**task.outputs, **(outputs or {})},
        }
        if progress is not None:
            update["progress"] = max(0, min(99, int(progress)))
        updated = task.model_copy(update=update)
        self.repository.save_task(updated)
        return updated

    def complete_job(
        self,
        task_id: str,
        *,
        transcript_text: str,
        short_url: str,
        doubao_conversation_url: str | None = None,
        doubao_message_id: str | None = None,
    ) -> TranscriptionTask:
        task = self._get_job(task_id)
        paragraphs = self._split_paragraphs(transcript_text)
        if not paragraphs:
            raise DoubaoBrowserAutomationError("豆包返回内容为空，无法回填文案。")
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
        source_url = self._normalize_short_url(short_url) or short_url.strip()
        completed = task.model_copy(
            update={
                "status": TaskStatus.SUCCEEDED,
                "progress": 100,
                "stage": "待人工复核",
                "updated_at": now,
                "segments": segments,
                "source_url": source_url,
                "timing_available": False,
                "outputs": {
                    **task.outputs,
                    "douyin_short_url": source_url,
                    "doubao_conversation_url": doubao_conversation_url or "",
                    "doubao_message_id": doubao_message_id or "",
                    "review_required": "true",
                    "fee_cny": "0",
                },
            }
        )
        self.repository.save_task(completed)
        self._save_initial_revision(completed, segments)
        return completed

    def fail_job(
        self,
        task_id: str,
        *,
        error_message: str,
        stage: str = "浏览器自动化失败",
        retryable: bool = True,
    ) -> TranscriptionTask:
        task = self._get_job(task_id)
        now = datetime.now().astimezone()
        failed = task.model_copy(
            update={
                "status": TaskStatus.FAILED,
                "progress": task.progress,
                "stage": stage,
                "updated_at": now,
                "error_message": error_message.strip()[:500] or "豆包自动提取失败。",
                "outputs": {
                    **task.outputs,
                    "retryable": "true" if retryable else "false",
                },
            }
        )
        self.repository.save_task(failed)
        return failed

    def requeue_job(self, task_id: str) -> TranscriptionTask:
        task = self._get_job(task_id)
        if task.status not in {TaskStatus.FAILED, TaskStatus.RUNNING}:
            return task
        updated = task.model_copy(
            update={
                "status": TaskStatus.QUEUED,
                "progress": 0,
                "stage": self.profile.initial_stage,
                "updated_at": datetime.now().astimezone(),
                "error_message": None,
            }
        )
        self.repository.save_task(updated)
        return updated

    def _get_job(self, task_id: str) -> TranscriptionTask:
        task = self.repository.get_task(task_id)
        if (
            not isinstance(task, TranscriptionTask)
            or task.source_kind != self.profile.source_kind
        ):
            raise DoubaoBrowserAutomationError("豆包浏览器任务不存在。", status_code=404)
        return task

    def _save_initial_revision(
        self, task: TranscriptionTask, segments: list[TranscriptSegment]
    ) -> None:
        revision = TranscriptRevision(
            revision_id=f"revision-{uuid4().hex[:12]}",
            task_id=task.task_id,
            revision_number=len(self.repository.list_transcript_revisions(task.task_id))
            + 1,
            status=TranscriptStatus.DRAFT,
            created_at=datetime.now().astimezone(),
            updated_at=datetime.now().astimezone(),
            reviewer=self.profile.reviewer,
            language="zh",
            model_name=self.profile.model_name,
            media_sha256="",
            original_segments=segments,
            corrected_segments=segments,
        )
        try:
            self.repository.save_transcript_revision(revision)
        except ValueError as exc:
            raise TranscriptionError(
                "豆包文案已回填，但校对版本保存冲突，请刷新后检查任务。",
                code="revision_conflict",
                task_id=task.task_id,
            ) from exc

    @staticmethod
    def _split_paragraphs(text: str) -> list[str]:
        cleaned = text.strip()
        if not cleaned:
            return []
        paragraphs = [
            item.strip()
            for item in re.split(r"\n\s*\n", cleaned)
            if item.strip()
        ]
        if len(paragraphs) == 1:
            paragraphs = [
                item.strip()
                for item in cleaned.splitlines()
                if item.strip()
            ] or paragraphs
        return paragraphs

    @staticmethod
    def _normalize_short_url(value: str) -> str | None:
        match = DOUYIN_SHORT_URL_RE.search(value or "")
        return match.group(0) if match else None


class DoubaoMobileAutomationService(DoubaoBrowserAutomationService):
    """Queue and persist zero-cost Doubao mobile transcript jobs."""

    def __init__(self, repository: TaskRepository) -> None:
        super().__init__(
            repository,
            DoubaoAutomationProfile(
                source_kind=DOUBAO_MOBILE_SOURCE_KIND,
                model_name=DOUBAO_MOBILE_MODEL_NAME,
                prompt_version=DOUBAO_MOBILE_PROMPT_VERSION,
                rights_holder="本地安卓手机豆包链路",
                initial_stage="等待安卓手机执行器接管",
                claimed_stage="安卓手机正在打开抖音",
                reviewer="本地安卓手机豆包链路",
            ),
        )
