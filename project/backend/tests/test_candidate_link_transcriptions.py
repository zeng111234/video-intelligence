"""Candidate-row entrypoint tests for free local Douyin transcription."""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from project.backend.app.api.v1.crawler import (  # noqa: E402
    _candidate_to_response,
    _spoken_seed_quality,
)
from project.backend.app.api.v1.link_transcriptions import (  # noqa: E402
    _has_usable_xiaohongshu_share_url,
)
from project.backend.app.core import deps as backend_deps  # noqa: E402
from project.backend.app.api.v1 import crawler as crawler_api  # noqa: E402
from project.backend.app.main import app  # noqa: E402
from project.backend.app.core.security import issue_auth_token  # noqa: E402
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


def test_xiaohongshu_direct_note_link_enters_link_transcription():
    seeded_repo = MockRepository()
    candidate = _douyin_candidate(seeded_repo).model_copy(
        update={
            "platform": Platform.XIAOHONGSHU,
            "platform_item_id": None,
            "video_id": "xiaohongshu-xhs-bare-link",
            "source_url": None,
        }
    )
    repo = MockRepository(candidates=[candidate], tasks=[])

    base_task = next(
        task for task in seeded_repo.list_tasks() if isinstance(task, TranscriptionTask)
    )
    task = base_task.model_copy(
        update={
            "task_id": "candidate-xiaohongshu-link-1",
            "candidate_id": candidate.video_id,
            "source_kind": "xiaohongshu_local_browser",
        }
    )

    class FakeService:
        transcription_service = None

        def transcribe_experimental(self, **kwargs):
            assert kwargs["share_text"] == (
                "https://www.xiaohongshu.com/explore/xhs-bare-link"
            )
            repo.save_task(task)
            return task

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


def test_xiaohongshu_share_link_readiness_accepts_direct_note_links():
    assert _has_usable_xiaohongshu_share_url(
        "https://www.xiaohongshu.com/explore/xhs-bare-link"
    )
    assert _has_usable_xiaohongshu_share_url(
        "https://www.xiaohongshu.com/explore/xhs-ready?xsec_token=token"
    )
    assert _has_usable_xiaohongshu_share_url("https://www.xhslink.com/m/short")


def test_xiaohongshu_original_media_route_proxies_legacy_candidate(monkeypatch):
    seeded_repo = MockRepository()
    candidate = _douyin_candidate(seeded_repo).model_copy(
        update={
            "platform": Platform.XIAOHONGSHU,
            "platform_item_id": "xhs-route-note",
            "video_id": "xiaohongshu-xhs-route-note",
            "source_url": None,
        }
    )
    repo = MockRepository(candidates=[candidate], tasks=[])
    calls: list[str] = []

    class FakeParser:
        def resolve(self, share_url):
            calls.append(share_url)
            return SimpleNamespace(
                platform=Platform.XIAOHONGSHU,
                media_url="https://sns-video-ak.xhscdn.com/stream/xhs-route.mp4",
                media_request_headers={
                    "Referer": "https://www.xiaohongshu.com/",
                    "User-Agent": "test-browser",
                },
            )

    class FakeService:
        parser = FakeParser()

    class FakeResponse:
        status_code = 206
        headers = {
            "content-type": "video/mp4",
            "content-length": "4",
            "content-range": "bytes 0-3/4",
            "accept-ranges": "bytes",
        }

        def iter_bytes(self, chunk_size=0):
            del chunk_size
            yield b"test"

        def close(self):
            pass

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.request_headers = None

        def build_request(self, method, url, headers):
            self.request_headers = {"method": method, "url": url, **headers}
            return self.request_headers

        def send(self, request, stream=False):
            assert stream is True
            assert request["Range"] == "bytes=0-3"
            assert request["Referer"] == "https://www.xiaohongshu.com/"
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr(crawler_api.httpx, "Client", FakeClient)

    app.dependency_overrides[backend_deps.get_repository] = lambda: repo
    app.dependency_overrides[backend_deps.get_douyin_link_transcription_service] = (
        lambda: FakeService()
    )
    try:
        response = TestClient(app).get(
            "/api/v1/crawler/candidates/xiaohongshu-xhs-route-note/original-media",
            headers={
                "Accept": "video/mp4",
                "Range": "bytes=0-3",
                "X-Admin-Token": issue_auth_token("admin", "pytest-media"),
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(backend_deps.get_repository, None)
        app.dependency_overrides.pop(
            backend_deps.get_douyin_link_transcription_service, None
        )

    assert response.status_code == 206
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.headers["content-range"] == "bytes 0-3/4"
    assert response.content == b"test"
    assert calls == ["https://www.xiaohongshu.com/explore/xhs-route-note"]

    app.dependency_overrides[backend_deps.get_repository] = lambda: repo
    app.dependency_overrides[backend_deps.get_douyin_link_transcription_service] = (
        lambda: FakeService()
    )
    try:
        json_response = TestClient(app).get(
            "/api/v1/crawler/candidates/xiaohongshu-xhs-route-note/original-media",
            headers={
                "Accept": "application/json",
                "X-Admin-Token": issue_auth_token("admin", "pytest-media-json"),
            },
        )
    finally:
        app.dependency_overrides.pop(backend_deps.get_repository, None)
        app.dependency_overrides.pop(
            backend_deps.get_douyin_link_transcription_service, None
        )

    assert json_response.status_code == 200
    assert json_response.json() == {
        "candidate_id": "xiaohongshu-xhs-route-note",
        "platform": "xiaohongshu",
        "media_url": "https://sns-video-ak.xhscdn.com/stream/xhs-route.mp4",
    }
    assert calls == [
        "https://www.xiaohongshu.com/explore/xhs-route-note",
        "https://www.xiaohongshu.com/explore/xhs-route-note",
    ]


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
