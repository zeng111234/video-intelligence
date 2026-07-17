from __future__ import annotations

from src.mock_data import build_mock_candidates, build_mock_tasks
from src.models import (
    RelevanceReview,
    SyncReport,
    TaskRecord,
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

    def list_tasks(self) -> list[TaskRecord]:
        return sorted(
            self._tasks.values(), key=lambda task: task.created_at, reverse=True
        )

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def save_task(self, task: TaskRecord) -> None:
        self._tasks[task.task_id] = task
