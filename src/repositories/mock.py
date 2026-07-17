from __future__ import annotations

from src.mock_data import build_mock_candidates, build_mock_tasks
from src.models import TaskRecord, VideoCandidate


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

    def list_candidates(self) -> list[VideoCandidate]:
        return list(self._candidates.values())

    def get_candidate(self, video_id: str) -> VideoCandidate | None:
        return self._candidates.get(video_id)

    def save_candidate(self, candidate: VideoCandidate) -> None:
        self._candidates[candidate.video_id] = candidate

    def list_tasks(self) -> list[TaskRecord]:
        return sorted(
            self._tasks.values(), key=lambda task: task.created_at, reverse=True
        )

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def save_task(self, task: TaskRecord) -> None:
        self._tasks[task.task_id] = task
