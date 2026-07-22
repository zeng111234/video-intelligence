from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from project.backend.app.api.v1 import transcriptions as transcription_api
from project.backend.app.main import app
from src.adapters.llm import SandboxCopywritingEngine
from src.models import (
    CopywritingTask,
    TaskStatus,
    TranscriptSegment,
    TranscriptionTask,
)
from src.repositories.mock import MockRepository
from src.services.copywriting import CopywritingService
from src.services.transcription import TranscriptionService


def _services(*, approved: bool):
    repository = MockRepository()
    now = datetime.now().astimezone()
    segments = [
        TranscriptSegment(
            start=0,
            end=2,
            text="这句话重复了",
            confidence=0.95,
            reviewed=True,
        ),
        TranscriptSegment(
            start=2,
            end=4,
            text="这句话重复了",
            confidence=0.95,
            reviewed=True,
        ),
        TranscriptSegment(
            start=4,
            end=8,
            text="核心事实是车辆应以实车检查结果为准",
            confidence=0.92,
            reviewed=True,
        ),
    ]
    task = TranscriptionTask(
        task_id="transcript-voiceover-test",
        title="测试视频.mp4",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        media_name="测试视频.mp4",
        media_type="video/mp4",
        rights_confirmed=True,
        rights_holder="测试公司",
        segments=segments,
        model_name="large-v3-turbo",
        media_sha256="abc123",
        duration_seconds=8,
        is_mock=False,
    )
    repository.save_task(task)
    transcription_service = TranscriptionService(repository)
    if approved:
        transcription_service.save_revision(
            task.task_id,
            segments,
            reviewer="测试校对员",
            approve=True,
        )
    copywriting_service = CopywritingService(
        repository,
        SandboxCopywritingEngine(),
    )
    return repository, transcription_service, copywriting_service


def test_voiceover_draft_requires_approved_revision() -> None:
    _, transcription_service, copywriting_service = _services(approved=False)
    app.dependency_overrides[transcription_api.get_transcription_service] = lambda: (
        transcription_service
    )
    app.dependency_overrides[transcription_api.get_copywriting_service] = lambda: (
        copywriting_service
    )
    try:
        response = TestClient(app).post(
            "/api/v1/transcriptions/transcript-voiceover-test/voiceover-drafts",
            json={"target_seconds": 45},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "确认成稿" in response.json()["message"]


def test_voiceover_draft_uses_approved_revision_and_preserves_source() -> None:
    repository, transcription_service, copywriting_service = _services(approved=True)
    approved = transcription_service.get_approved_revision("transcript-voiceover-test")
    assert approved is not None
    original_segments = approved.corrected_segments
    app.dependency_overrides[transcription_api.get_transcription_service] = lambda: (
        transcription_service
    )
    app.dependency_overrides[transcription_api.get_copywriting_service] = lambda: (
        copywriting_service
    )
    try:
        response = TestClient(app).post(
            "/api/v1/transcriptions/transcript-voiceover-test/voiceover-drafts",
            json={
                "target_seconds": 45,
                "speech_rate": 1,
                "variant_count": 2,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["target_characters"] == 180
    assert payload["source_revision_id"] == approved.revision_id
    assert len(payload["result_variants"]) == 2

    copy_task = repository.get_task(payload["copywriting_task_id"])
    assert isinstance(copy_task, CopywritingTask)
    assert copy_task.source_text.count("这句话重复了") == 1
    assert "删除口头禅" in copy_task.rewrite_goal
    assert copy_task.source_task_id == "transcript-voiceover-test"
    assert copy_task.source_revision_id == approved.revision_id
    assert (
        transcription_service.get_approved_revision(
            "transcript-voiceover-test"
        ).corrected_segments
        == original_segments
    )


def test_voiceover_draft_list_and_update_are_scoped_to_transcription() -> None:
    repository, transcription_service, copywriting_service = _services(approved=True)
    app.dependency_overrides[transcription_api.get_transcription_service] = lambda: (
        transcription_service
    )
    app.dependency_overrides[transcription_api.get_copywriting_service] = lambda: (
        copywriting_service
    )
    try:
        client = TestClient(app)
        created = client.post(
            "/api/v1/transcriptions/transcript-voiceover-test/voiceover-drafts",
            json={"target_seconds": 45, "speech_rate": 1, "variant_count": 2},
        )
        assert created.status_code == 200
        draft_id = created.json()["copywriting_task_id"]

        listed = client.get(
            "/api/v1/transcriptions/transcript-voiceover-test/voiceover-drafts"
        )
        assert listed.status_code == 200
        assert [item["copywriting_task_id"] for item in listed.json()] == [draft_id]

        updated = client.patch(
            f"/api/v1/transcriptions/transcript-voiceover-test/voiceover-drafts/{draft_id}",
            json={
                "result_text": "人工编辑后的第一版",
                "result_variants": ["旧第一版", "第二版"],
            },
        )
        assert updated.status_code == 200
        payload = updated.json()
        assert payload["result_text"] == "人工编辑后的第一版"
        assert payload["result_variants"][0] == "人工编辑后的第一版"

        saved = repository.get_task(draft_id)
        assert isinstance(saved, CopywritingTask)
        assert saved.result_text == "人工编辑后的第一版"
        assert saved.result_variants[0] == "人工编辑后的第一版"

        wrong_scope = client.patch(
            f"/api/v1/transcriptions/other-task/voiceover-drafts/{draft_id}",
            json={"result_text": "不应保存"},
        )
        assert wrong_scope.status_code == 404
    finally:
        app.dependency_overrides.clear()
