from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.models import (
    CandidateMatch,
    DiscoveryResult,
    KeywordTrendResult,
    RelevanceReview,
    SamplingCheckpoint,
    SourcePage,
    SourceCapability,
    SourceRequest,
    SyncReport,
    TaskRecord,
    TranscriptRevision,
    VideoCandidate,
    VideoMetricSnapshot,
)


class CrawlerAdapter(Protocol):
    def capabilities(self) -> SourceCapability: ...

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

    def save_discovery_result(self, result: DiscoveryResult) -> None: ...

    def list_discovery_results(self, limit: int = 20) -> list[DiscoveryResult]: ...

    def claim_discovery_request(
        self,
        fingerprint: str,
        request_id: str,
        claimed_at: datetime,
        ttl_seconds: int = 60,
    ) -> bool: ...

    def save_candidate_match(self, match: CandidateMatch) -> None: ...

    def list_candidate_matches(self, request_id: str) -> list[CandidateMatch]: ...

    def list_keyword_matches(
        self, keyword: str, since: datetime
    ) -> list[CandidateMatch]: ...

    def save_keyword_trend_results(self, results: list[KeywordTrendResult]) -> None: ...

    def clear_keyword_trend_results(self, keyword: str) -> None: ...

    def list_keyword_trend_results(
        self, keyword: str, limit: int = 10
    ) -> list[KeywordTrendResult]: ...

    def save_sampling_checkpoint(self, checkpoint: SamplingCheckpoint) -> None: ...

    def list_sampling_checkpoints(
        self, keyword: str | None = None
    ) -> list[SamplingCheckpoint]: ...


class TaskRepository(Protocol):
    def list_tasks(self) -> list[TaskRecord]: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def save_task(self, task: TaskRecord) -> None: ...

    def save_transcript_revision(self, revision: TranscriptRevision) -> None: ...

    def list_transcript_revisions(self, task_id: str) -> list[TranscriptRevision]: ...

    def get_transcript_revision(
        self, revision_id: str
    ) -> TranscriptRevision | None: ...
