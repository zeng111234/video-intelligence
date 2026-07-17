from __future__ import annotations

from typing import Protocol

from src.models import (
    RelevanceReview,
    SourcePage,
    SourceRequest,
    SyncReport,
    TaskRecord,
    VideoCandidate,
    VideoMetricSnapshot,
)


class CrawlerAdapter(Protocol):
    def sync(self, request: SourceRequest) -> SourcePage: ...


class CandidateRepository(Protocol):
    def list_candidates(self) -> list[VideoCandidate]: ...

    def get_candidate(self, video_id: str) -> VideoCandidate | None: ...

    def save_candidate(self, candidate: VideoCandidate) -> str: ...

    def list_snapshots(self, item_id: str) -> list[VideoMetricSnapshot]: ...

    def append_snapshot(self, snapshot: VideoMetricSnapshot) -> bool: ...

    def save_review(self, review: RelevanceReview) -> None: ...

    def get_review(self, candidate_id: str) -> RelevanceReview | None: ...

    def list_reviews(self) -> list[RelevanceReview]: ...

    def save_sync_report(self, report: SyncReport) -> None: ...

    def list_sync_reports(self, limit: int = 20) -> list[SyncReport]: ...


class TaskRepository(Protocol):
    def list_tasks(self) -> list[TaskRecord]: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def save_task(self, task: TaskRecord) -> None: ...
