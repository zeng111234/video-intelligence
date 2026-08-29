"""Candidate-row entrypoint tests for free local Douyin transcription."""

from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from project.backend.app.api.v1.crawler import (  # noqa: E402
    _candidate_to_response,
    _spoken_seed_quality,
)
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

        def _fallback_price(self, platform=Platform.DOUYIN):
            del platform
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
    assert mapped.audio_status == "checking"


def test_candidate_response_distinguishes_topic_only_from_manual_text_reference():
    seeded_repo = MockRepository()
    candidate = _douyin_candidate(seeded_repo)
    repo = MockRepository(candidates=seeded_repo.list_candidates(), tasks=[])

    topic_only = _candidate_to_response(candidate, repo=repo)
    assert topic_only.spoken_material_status == "topic_only"
    assert "不能提取原视频文案" in topic_only.spoken_material_message

    manual_reference = candidate.model_copy(
        update={
            "evidence": "人工素材箱；可见文案=贴标机选型先看瓶型、速度和标签材质，再确认售后培训是否到位。",
        }
    )
    text_reference = _candidate_to_response(manual_reference, repo=repo)
    assert text_reference.spoken_material_status == "text_reference"
    assert "可作为改写参考" in text_reference.spoken_material_message


def test_xiaohongshu_topic_only_response_explains_login_fallback():
    seeded_repo = MockRepository()
    candidate = _douyin_candidate(seeded_repo).model_copy(
        update={"platform": Platform.XIAOHONGSHU}
    )
    repo = MockRepository(candidates=[candidate], tasks=[])

    response = _candidate_to_response(candidate, repo=repo)

    assert response.spoken_material_status == "topic_only"
    assert "已登录浏览器" in response.spoken_material_message
    assert "上传已获授权的视频" in response.spoken_material_message


def test_spoken_seed_quality_matches_the_user_visible_rules():
    problem = _spoken_seed_quality(
        title="切标鼓角度不对，导致标签带胶并频繁掉标 #贴标机",
        keyword="贴标机",
    )
    application = _spoken_seed_quality(
        title="异形瓶贴标机一机多用，可贴不同形状的瓶子",
        keyword="贴标机",
    )
    promotion = _spoken_seed_quality(
        title="贴标机源头厂家 #贴标机 #机械设备",
        keyword="贴标机",
    )
    empty_title = _spoken_seed_quality(title="贴标机", keyword="贴标机")

    assert problem["status"] == "writeable"
    assert application["status"] == "writeable"
    assert promotion["status"] == "low_information"
    assert empty_title["status"] == "low_information"
