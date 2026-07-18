from __future__ import annotations

import pytest

from src.app_state import (
    _sqlite_repository,
    initialize_state,
    select_candidate,
    selected_candidate_id,
)
from src.models import HeatLevel, Platform, TaskStatus, TranscriptionTask
from src.repositories import MockRepository
from src.retry import ExternalServiceError, run_with_single_retry
from src.services import CandidateService, HeatService, TranscriptionService


def test_mock_repository_contains_demo_records() -> None:
    repository = MockRepository()

    assert len(repository.list_candidates()) >= 20
    statuses = {task.status for task in repository.list_tasks()}
    assert {TaskStatus.RUNNING, TaskStatus.SUCCEEDED, TaskStatus.FAILED} <= statuses


def test_candidate_filters_and_heat_contract() -> None:
    repository = MockRepository()
    service = CandidateService(repository)
    douyin_items = service.search(
        platforms=[Platform.DOUYIN], published_within_hours=720
    )

    assert douyin_items
    assert all(item.platform == Platform.DOUYIN for item in douyin_items)
    assert douyin_items == sorted(
        douyin_items, key=lambda item: item.heat.score, reverse=True
    )

    result = HeatService().analyze(repository.list_candidates()[0].metrics)
    assert result.level in set(HeatLevel)
    assert result.model_version == "rule-v1"


def test_repository_save_round_trip() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    candidate = MockRepository().list_candidates()[0]
    repository.save_candidate(candidate)

    assert repository.get_candidate(candidate.video_id) == candidate


def test_transcription_requires_rights_and_exports_valid_formats() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    service = TranscriptionService(repository)

    with pytest.raises(ValueError, match="处理权"):
        service.create_mock_task(
            media_name="test.mp4",
            media_type="video/mp4",
            rights_confirmed=False,
        )

    task = service.create_mock_task(
        media_name="test.mp4",
        media_type="video/mp4",
        rights_confirmed=True,
    )
    assert task.status == TaskStatus.SUCCEEDED
    assert b"00:00:00,000 -->" in service.export_srt(task.segments)
    exported = service.export_json(task.segments)
    assert b'"segments"' in exported
    assert b'"mock"' not in exported
    assert service.export_txt(task.segments).decode("utf-8").startswith("很多人")


def test_failed_task_can_only_retry_once() -> None:
    repository = MockRepository()
    service = TranscriptionService(repository)
    failed = next(
        task for task in repository.list_tasks() if task.status == TaskStatus.FAILED
    )
    assert isinstance(failed, TranscriptionTask)

    retried = service.retry_failed_task(failed.task_id)
    assert retried.status == TaskStatus.SUCCEEDED
    assert retried.retry_count == 1
    with pytest.raises(ValueError, match="失败任务"):
        service.retry_failed_task(failed.task_id)


def test_connection_failure_retries_exactly_once() -> None:
    attempts = 0

    def failing_operation() -> None:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("offline")

    with pytest.raises(ExternalServiceError, match="已自动重试一次"):
        run_with_single_retry(failing_operation)

    assert attempts == 2


def test_selected_candidate_survives_page_state_boundary() -> None:
    state: dict[str, object] = {}
    initialize_state(state)
    select_candidate("mock-007", state)

    assert selected_candidate_id(state) == "mock-007"


def test_sqlite_runtime_does_not_seed_demo_data_by_default(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("VIDEO_SEED_DEMO_DATA", raising=False)

    repository = _sqlite_repository(str(tmp_path / "runtime.db"))

    assert repository.list_candidates() == []
    assert repository.list_tasks() == []
