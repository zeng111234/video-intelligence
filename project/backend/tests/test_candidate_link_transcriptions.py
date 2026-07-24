"""Candidate-row entrypoint tests for free local Douyin transcription."""

from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from project.backend.app.api.v1.crawler import _candidate_to_response  # noqa: E402
from project.backend.app.core import deps as backend_deps  # noqa: E402
from project.backend.app.main import app  # noqa: E402
from src.models import Platform, TranscriptionTask  # noqa: E402
from src.repositories import MockRepository  # noqa: E402


def _douyin_candidate(repo: MockRepository):
    return next(
        item
        for item in repo._candidates.values()  # noqa: SLF001 - test fixture setup
        if item.platform == Platform.DOUYIN and item.source_url is not None
    )


def test_candidate_endpoint_uses_the_saved_link_and_associates_task():
    repo = MockRepository()
    candidate = _douyin_candidate(repo)
    base_task = next(
        task for task in repo.list_tasks() if isinstance(task, TranscriptionTask)
    )
    task = base_task.model_copy(
        update={
            "task_id": "candidate-local-link-1",
            "candidate_id": candidate.video_id,
            "source_kind": "douyin_local_browser",
        }
    )
    received: dict[str, object] = {}

    class FakeService:
        transcription_service = None

        def transcribe_experimental(self, **kwargs):
            received.update(kwargs)
            repo.save_task(task)
            return task

        def _fallback_price(self):
            return 0.04

    app.dependency_overrides[backend_deps.get_repository] = lambda: repo
    app.dependency_overrides[backend_deps.get_douyin_link_transcription_service] = (
        lambda: FakeService()
    )
    try:
        response = TestClient(app).post(
            f"/api/v1/crawler/link-transcriptions/candidates/{candidate.video_id}",
            json={"rights_holder": "测试公司", "rights_confirmed": True},
        )
    finally:
        app.dependency_overrides.pop(backend_deps.get_repository, None)
        app.dependency_overrides.pop(
            backend_deps.get_douyin_link_transcription_service, None
        )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert received["share_text"] == str(candidate.source_url)
    assert received["candidate_id"] == candidate.video_id

    mapped = _candidate_to_response(candidate, repo=repo)
    assert mapped.media_transcription_task_id == task.task_id
    assert mapped.media_resolution_status == task.status.value
