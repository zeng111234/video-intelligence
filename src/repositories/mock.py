from __future__ import annotations

from datetime import datetime, timedelta

from src.mock_data import build_mock_candidates, build_mock_tasks
from src.models import (
    CandidateMatch,
    DiscoveryResult,
    KeywordTrendResult,
    Platform,
    PlatformSearchRun,
    RelevanceReview,
    SamplingCheckpoint,
    SearchBatch,
    SyncReport,
    TaskRecord,
    TranscriptRevision,
    VideoCandidate,
    VideoMetricSnapshot,
)


class MockRepository:
    def __init__(
        self,
        candidates: list[VideoCandidate] | None = None,
        tasks: list[TaskRecord] | None = None,
    ) -> None:
        initial_candidates = (
            build_mock_candidates() if candidates is None else candidates
        )
        initial_tasks = build_mock_tasks() if tasks is None else tasks
        self._candidates = {item.video_id: item for item in initial_candidates}
        self._tasks = {item.task_id: item for item in initial_tasks}
        self._snapshots = {
            item.video_id: [item.metrics.model_copy(update={"item_id": item.video_id})]
            for item in initial_candidates
        }
        self._reviews: dict[str, RelevanceReview] = {}
        self._sync_reports: dict[str, SyncReport] = {}
        self._discovery_results: dict[str, DiscoveryResult] = {}
        self._candidate_matches: dict[tuple[str, str], CandidateMatch] = {}
        self._keyword_trends: dict[tuple[str, Platform], list[KeywordTrendResult]] = {}
        self._transcript_revisions: dict[str, TranscriptRevision] = {}
        self._sampling_checkpoints: dict[str, SamplingCheckpoint] = {}
        self._discovery_request_guards: dict[str, tuple[str, datetime]] = {}
        self._search_batches: dict[str, SearchBatch] = {}
        self._platform_search_runs: dict[str, PlatformSearchRun] = {}
        self._provider_request_guards: dict[str, tuple[str, datetime, str]] = {}

    def list_candidates(self) -> list[VideoCandidate]:
        return list(self._candidates.values())

    def get_candidate(self, video_id: str) -> VideoCandidate | None:
        return self._candidates.get(video_id)

    def save_candidate(self, candidate: VideoCandidate) -> str:
        self._candidates[candidate.video_id] = candidate
        self.append_snapshot(
            candidate.metrics.model_copy(update={"item_id": candidate.video_id})
        )
        return candidate.video_id

    def list_snapshots(self, item_id: str) -> list[VideoMetricSnapshot]:
        return sorted(
            self._snapshots.get(item_id, []), key=lambda item: item.sampled_at
        )

    def append_snapshot(self, snapshot: VideoMetricSnapshot) -> bool:
        snapshots = self._snapshots.setdefault(snapshot.item_id, [])
        if any(item.sampled_at == snapshot.sampled_at for item in snapshots):
            return False
        snapshots.append(snapshot)
        return True

    def save_review(self, review: RelevanceReview) -> None:
        self._reviews[review.candidate_id] = review

    def get_review(self, candidate_id: str) -> RelevanceReview | None:
        return self._reviews.get(candidate_id)

    def list_reviews(self) -> list[RelevanceReview]:
        return list(self._reviews.values())

    def save_sync_report(self, report: SyncReport) -> None:
        self._sync_reports[report.run_id] = report

    def list_sync_reports(self, limit: int = 20) -> list[SyncReport]:
        return sorted(
            self._sync_reports.values(), key=lambda item: item.finished_at, reverse=True
        )[:limit]

    def save_discovery_result(self, result: DiscoveryResult) -> None:
        self._discovery_results[result.request_id] = result

    def list_discovery_results(self, limit: int = 20) -> list[DiscoveryResult]:
        return sorted(
            self._discovery_results.values(),
            key=lambda item: item.finished_at,
            reverse=True,
        )[:limit]

    def claim_discovery_request(
        self,
        fingerprint: str,
        request_id: str,
        claimed_at: datetime,
        ttl_seconds: int = 60,
    ) -> bool:
        previous = self._discovery_request_guards.get(fingerprint)
        if previous and (claimed_at - previous[1]).total_seconds() < ttl_seconds:
            return False
        self._discovery_request_guards[fingerprint] = (request_id, claimed_at)
        return True

    def save_candidate_match(self, match: CandidateMatch) -> None:
        self._candidate_matches[(match.request_id, match.video_id)] = match

    def list_candidate_matches(self, request_id: str) -> list[CandidateMatch]:
        return [
            match
            for (stored_request_id, _), match in self._candidate_matches.items()
            if stored_request_id == request_id
        ]

    def list_keyword_matches(
        self,
        keyword: str,
        since,
        platform: Platform = Platform.DOUYIN,
        provider_name: str | None = None,
    ) -> list[CandidateMatch]:
        normalized = keyword.casefold()
        return [
            match
            for match in self._candidate_matches.values()
            if match.keyword.casefold() == normalized
            and match.observed_at >= since
            and match.platform == platform
            and (provider_name is None or match.provider_name == provider_name)
        ]

    def save_keyword_trend_results(self, results: list[KeywordTrendResult]) -> None:
        if results:
            key = (results[0].keyword.casefold(), results[0].platform)
            self._keyword_trends[key] = list(results)

    def clear_keyword_trend_results(
        self, keyword: str, platform: Platform = Platform.DOUYIN
    ) -> None:
        self._keyword_trends.pop((keyword.casefold(), platform), None)

    def list_keyword_trend_results(
        self,
        keyword: str,
        limit: int = 10,
        platform: Platform = Platform.DOUYIN,
    ) -> list[KeywordTrendResult]:
        return sorted(
            self._keyword_trends.get((keyword.casefold(), platform), []),
            key=lambda item: (-item.score, item.platform_rank),
        )[:limit]

    def list_tasks(self) -> list[TaskRecord]:
        return sorted(
            self._tasks.values(), key=lambda task: task.created_at, reverse=True
        )

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def save_task(self, task: TaskRecord) -> None:
        self._tasks[task.task_id] = task

    def save_transcript_revision(self, revision: TranscriptRevision) -> None:
        if revision.revision_id in self._transcript_revisions or any(
            item.task_id == revision.task_id
            and item.revision_number == revision.revision_number
            for item in self._transcript_revisions.values()
        ):
            raise ValueError("该校对版本号已经存在，请刷新页面后基于最新版本继续校对。")
        self._transcript_revisions[revision.revision_id] = revision

    def list_transcript_revisions(self, task_id: str) -> list[TranscriptRevision]:
        return sorted(
            (
                item
                for item in self._transcript_revisions.values()
                if item.task_id == task_id
            ),
            key=lambda item: item.revision_number,
        )

    def get_transcript_revision(self, revision_id: str) -> TranscriptRevision | None:
        return self._transcript_revisions.get(revision_id)

    def save_sampling_checkpoint(self, checkpoint: SamplingCheckpoint) -> None:
        self._sampling_checkpoints[checkpoint.checkpoint_id] = checkpoint

    def list_sampling_checkpoints(
        self, keyword: str | None = None
    ) -> list[SamplingCheckpoint]:
        items = self._sampling_checkpoints.values()
        if keyword:
            items = (
                item for item in items if item.keyword.casefold() == keyword.casefold()
            )
        return sorted(items, key=lambda item: item.due_at)

    def save_search_batch(self, batch: SearchBatch) -> None:
        self._search_batches[batch.batch_id] = batch

    def get_search_batch(self, batch_id: str) -> SearchBatch | None:
        return self._search_batches.get(batch_id)

    def list_search_batches(self, limit: int = 20) -> list[SearchBatch]:
        return sorted(
            self._search_batches.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

    def save_platform_search_run(self, run: PlatformSearchRun) -> None:
        self._platform_search_runs[run.run_id] = run

    def list_platform_search_runs(self, batch_id: str) -> list[PlatformSearchRun]:
        return sorted(
            (
                run
                for run in self._platform_search_runs.values()
                if run.batch_id == batch_id
            ),
            key=lambda item: (item.started_at, item.platform.value),
        )

    def find_cached_platform_search_run(
        self,
        *,
        provider: str,
        platform: Platform,
        keyword: str,
        published_window_days: int,
        requested_count: int,
        since: datetime,
    ) -> PlatformSearchRun | None:
        candidates: list[PlatformSearchRun] = []
        for run in self._platform_search_runs.values():
            batch = self._search_batches.get(run.batch_id)
            if (
                batch
                and run.provider == provider
                and run.platform == platform
                and run.status.value == "succeeded"
                and run.finished_at is not None
                and run.finished_at >= since
                and batch.keyword.casefold() == keyword.casefold()
                and batch.published_window_days == published_window_days
                and batch.requested_count_per_platform == requested_count
            ):
                candidates.append(run)
        return max(candidates, key=lambda item: item.finished_at or since, default=None)

    def monthly_platform_query_count(self, since: datetime) -> int:
        return sum(
            run.api_call_count
            for run in self._platform_search_runs.values()
            if run.started_at >= since
        )

    def claim_platform_search_request(
        self,
        fingerprint: str,
        run_id: str,
        claimed_at: datetime,
        ttl_seconds: int = 60,
    ) -> bool:
        previous = self._provider_request_guards.get(fingerprint)
        if previous:
            if previous[2] == "outcome_unknown":
                return False
            if claimed_at - previous[1] < timedelta(seconds=ttl_seconds):
                return False
        self._provider_request_guards[fingerprint] = (run_id, claimed_at, "claimed")
        return True

    def mark_platform_search_request(
        self, fingerprint: str, status: str, updated_at: datetime
    ) -> None:
        previous = self._provider_request_guards.get(fingerprint)
        if previous:
            self._provider_request_guards[fingerprint] = (
                previous[0],
                updated_at,
                status,
            )

    def has_unresolved_platform_search_request(self, fingerprint: str) -> bool:
        guard = self._provider_request_guards.get(fingerprint)
        return bool(guard and guard[2] == "outcome_unknown")

    def resolve_platform_search_request(self, fingerprint: str) -> None:
        self._provider_request_guards.pop(fingerprint, None)
