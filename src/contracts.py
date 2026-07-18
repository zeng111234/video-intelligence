from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.models import (
    AvatarAsset,
    AvatarCapability,
    AvatarJobSnapshot,
    AvatarSubmitRequest,
    CandidateMatch,
    DiscoveryResult,
    KeywordTrendResult,
    PlatformSearchRun,
    Platform,
    ProviderCapability,
    ProviderSearchPage,
    ProviderUsage,
    RelevanceReview,
    SamplingCheckpoint,
    SearchBatch,
    SourcePage,
    SourceCapability,
    SourceRequest,
    SyncReport,
    TaskRecord,
    TranscriptRevision,
    VideoCandidate,
    VideoMetricSnapshot,
)


class AvatarProvider(Protocol):
    def capabilities(self) -> AvatarCapability: ...

    def list_assets(self) -> list[AvatarAsset]: ...

    def submit(self, request: AvatarSubmitRequest) -> AvatarJobSnapshot: ...

    def get_job(self, job_id: str) -> AvatarJobSnapshot: ...

    def find_job(self, idempotency_key: str) -> AvatarJobSnapshot | None: ...

    def download_result(self, job_id: str) -> tuple[bytes, str]: ...


class CrawlerAdapter(Protocol):
    def capabilities(self) -> SourceCapability: ...

    def sync(self, request: SourceRequest) -> SourcePage: ...


class LicensedSearchProvider(Protocol):
    def capabilities(self) -> ProviderCapability: ...

    def search(
        self,
        platform: Platform,
        keyword: str,
        published_after: datetime,
        limit: int,
        idempotency_key: str,
    ) -> ProviderSearchPage: ...

    def refresh_metrics(
        self,
        platform: Platform,
        platform_item_ids: list[str],
        idempotency_key: str,
    ) -> ProviderSearchPage: ...

    def usage(self) -> ProviderUsage | None: ...


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
        self,
        keyword: str,
        since: datetime,
        platform: Platform = Platform.DOUYIN,
        provider_name: str | None = None,
    ) -> list[CandidateMatch]: ...

    def save_keyword_trend_results(self, results: list[KeywordTrendResult]) -> None: ...

    def clear_keyword_trend_results(
        self, keyword: str, platform: Platform = Platform.DOUYIN
    ) -> None: ...

    def list_keyword_trend_results(
        self,
        keyword: str,
        limit: int = 10,
        platform: Platform = Platform.DOUYIN,
    ) -> list[KeywordTrendResult]: ...

    def save_sampling_checkpoint(self, checkpoint: SamplingCheckpoint) -> None: ...

    def list_sampling_checkpoints(
        self, keyword: str | None = None
    ) -> list[SamplingCheckpoint]: ...

    def save_search_batch(self, batch: SearchBatch) -> None: ...

    def get_search_batch(self, batch_id: str) -> SearchBatch | None: ...

    def list_search_batches(self, limit: int = 20) -> list[SearchBatch]: ...

    def save_platform_search_run(self, run: PlatformSearchRun) -> None: ...

    def list_platform_search_runs(self, batch_id: str) -> list[PlatformSearchRun]: ...

    def find_cached_platform_search_run(
        self,
        *,
        provider: str,
        platform: Platform,
        keyword: str,
        published_window_days: int,
        requested_count: int,
        since: datetime,
    ) -> PlatformSearchRun | None: ...

    def monthly_platform_query_count(self, since: datetime) -> int: ...

    def claim_platform_search_request(
        self,
        fingerprint: str,
        run_id: str,
        claimed_at: datetime,
        ttl_seconds: int = 60,
    ) -> bool: ...

    def mark_platform_search_request(
        self, fingerprint: str, status: str, updated_at: datetime
    ) -> None: ...

    def has_unresolved_platform_search_request(self, fingerprint: str) -> bool: ...

    def resolve_platform_search_request(self, fingerprint: str) -> None: ...


class TaskRepository(Protocol):
    def list_tasks(self) -> list[TaskRecord]: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def save_task(self, task: TaskRecord) -> None: ...

    def save_transcript_revision(self, revision: TranscriptRevision) -> None: ...

    def list_transcript_revisions(self, task_id: str) -> list[TranscriptRevision]: ...

    def get_transcript_revision(
        self, revision_id: str
    ) -> TranscriptRevision | None: ...
