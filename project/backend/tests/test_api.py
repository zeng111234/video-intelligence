"""FastAPI 全端点集成测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

# 确保项目根目录在 Python 路径中
_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from project.backend.app.main import app  # noqa: E402
from project.backend.app.api.v1 import crawler as crawler_api  # noqa: E402
from project.backend.app.api.v1 import publish as publish_api  # noqa: E402
from project.backend.app.api.v1 import transcriptions as transcriptions_api  # noqa: E402
from project.backend.app.core import deps as backend_deps  # noqa: E402
from project.backend.app.core.security import issue_auth_token  # noqa: E402
from project.backend.app.core.config import (  # noqa: E402
    CopywritingProviderMode,
    CrawlerProviderMode,
)
from src.adapters.licensed import (  # noqa: E402
    LicensedProviderError,
    SandboxLicensedSearchProvider,
)
from src.adapters.oneapi import OneApiLicensedSearchProvider  # noqa: E402
from project.backend.app.core import config as backend_config  # noqa: E402
from src.models import (  # noqa: E402
    AvatarProviderStatus,
    AvatarTask,
    CopywritingTask,
    HotWordRecord,
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
    PipelineStepResult,
    Platform,
    ProviderCapability,
    ProviderErrorKind,
    ProviderMode,
    ProviderSearchItem,
    ProviderSearchPage,
    PublishPlatform,
    PublishStatus,
    PublishTarget,
    SourceCapability,
    TaskStatus,
    TranscriptSegment,
    TranscriptionTask,
    VideoMetricSnapshot,
    VideoEditConfig,
    VideoEditTask,
)
from src.repositories import MockRepository  # noqa: E402
from src.mock_data import build_mock_candidates  # noqa: E402
from src.adapters.publishers.sandbox import SandboxPublisher  # noqa: E402
from src.adapters.publishers.douyin_browser import DouyinBrowserPublisher  # noqa: E402
from src.adapters.llm import SandboxCopywritingEngine  # noqa: E402
from src.services.copywriting import CopywritingService  # noqa: E402
from src.services.publisher import PublishService  # noqa: E402
from src.services.publish_accounts import PublishAccountError  # noqa: E402
from src.services import HeatService, KeywordTrendService, SourceService  # noqa: E402
from src.services.commercial_search import CommercialSearchService  # noqa: E402
from src.services.transcription import TranscriptionService  # noqa: E402

PUBLISH_DOUYIN_CONNECTION_KEYS = (
    "PUBLISH_DOUYIN_ACCESS_TOKEN",
    "PUBLISH_DOUYIN_REFRESH_TOKEN",
    "PUBLISH_DOUYIN_OPEN_ID",
    "PUBLISH_DOUYIN_TOKEN_EXPIRES_AT",
    "PUBLISH_DOUYIN_REFRESH_EXPIRES_AT",
    "PUBLISH_DOUYIN_CLIENT_KEY",
    "PUBLISH_DOUYIN_CLIENT_SECRET",
    "PUBLISH_DOUYIN_REDIRECT_URI",
)


@pytest.fixture()
def client():
    return TestClient(
        app,
        headers={"X-Admin-Token": issue_auth_token("admin", "pytest-admin")},
    )


@pytest.fixture()
def isolated_transcription_dependencies():
    """Keep mock transcription API cases out of the user's SQLite history."""
    repository = MockRepository(candidates=[], tasks=[])
    service = TranscriptionService(repository)
    repository_dependency = backend_deps.get_repository
    service_dependency = backend_deps.get_transcription_service
    previous_repository = app.dependency_overrides.get(repository_dependency)
    previous_service = app.dependency_overrides.get(service_dependency)
    app.dependency_overrides[repository_dependency] = lambda: repository
    app.dependency_overrides[service_dependency] = lambda: service
    try:
        yield
    finally:
        if previous_repository is None:
            app.dependency_overrides.pop(repository_dependency, None)
        else:
            app.dependency_overrides[repository_dependency] = previous_repository
        if previous_service is None:
            app.dependency_overrides.pop(service_dependency, None)
        else:
            app.dependency_overrides[service_dependency] = previous_service


@pytest.fixture()
def isolated_copywriting_dependencies():
    """Keep mock copywriting API cases out of the user's SQLite history."""
    repository = MockRepository(candidates=[], tasks=[])
    service = CopywritingService(repository, SandboxCopywritingEngine())
    service_dependency = backend_deps.get_copywriting_service
    previous_service = app.dependency_overrides.get(service_dependency)
    app.dependency_overrides[service_dependency] = lambda: service
    try:
        yield
    finally:
        if previous_service is None:
            app.dependency_overrides.pop(service_dependency, None)
        else:
            app.dependency_overrides[service_dependency] = previous_service


@pytest.fixture()
def copywriting_sandbox(monkeypatch: pytest.MonkeyPatch):
    backend_deps.get_copywriting_engine.cache_clear()
    backend_deps.get_copywriting_service.cache_clear()
    monkeypatch.setattr(
        backend_deps,
        "COPYWRITING_MODE",
        CopywritingProviderMode.SANDBOX,
    )
    yield
    backend_deps.get_copywriting_engine.cache_clear()
    backend_deps.get_copywriting_service.cache_clear()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_ok(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# /api/v1/admin/status
# ---------------------------------------------------------------------------


class TestAdminStatus:
    def test_status_returns_ok(self, client: TestClient):
        resp = client.get("/api/v1/admin/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
        assert "repository_type" in data

    def test_status_includes_counts(self, client: TestClient):
        resp = client.get("/api/v1/admin/status")
        data = resp.json()
        assert isinstance(data["candidate_count"], int)
        assert isinstance(data["task_count"], int)

    def test_status_has_database_path(self, client: TestClient):
        resp = client.get("/api/v1/admin/status")
        data = resp.json()
        # 数据库路径应存在（或为 None）
        assert data["database_path"] is None or isinstance(data["database_path"], str)


# ---------------------------------------------------------------------------
# /api/v1/candidates/search
# ---------------------------------------------------------------------------


class TestCandidatesSearch:
    def test_search_empty_keyword(self, client: TestClient):
        resp = client.post(
            "/api/v1/candidates/search", json={"keyword": "", "limit": 10}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)

    def test_search_with_keyword(self, client: TestClient):
        resp = client.post(
            "/api/v1/candidates/search", json={"keyword": "test", "limit": 5}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= len(data["items"])

    def test_search_respects_limit(self, client: TestClient):
        resp = client.post(
            "/api/v1/candidates/search", json={"keyword": "", "limit": 2}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) <= 2

    def test_search_default_params(self, client: TestClient):
        """不传参数使用默认值。"""
        resp = client.post("/api/v1/candidates/search", json={})
        assert resp.status_code == 200

    def test_search_item_fields(self, client: TestClient):
        resp = client.post(
            "/api/v1/candidates/search", json={"keyword": "", "limit": 1}
        )
        data = resp.json()
        if data["items"]:
            item = data["items"][0]
            assert "video_id" in item
            assert "title" in item
            assert "platform" in item
            assert "heat_score" in item
            assert "heat_level" in item
            assert "publication_time_state" in item
            assert "official_hot" in item
        assert "category_options" in data

    def test_search_limit_too_large(self, client: TestClient):
        resp = client.post(
            "/api/v1/candidates/search", json={"keyword": "", "limit": 999}
        )
        assert resp.status_code == 422  # Pydantic validation: le=100

    def test_search_respects_page(self, client: TestClient):
        first = client.post(
            "/api/v1/candidates/search", json={"keyword": "", "limit": 1, "page": 1}
        )
        second = client.post(
            "/api/v1/candidates/search", json={"keyword": "", "limit": 1, "page": 2}
        )
        assert first.status_code == 200
        assert second.status_code == 200
        if first.json()["total"] > 1:
            assert (
                first.json()["items"][0]["video_id"]
                != second.json()["items"][0]["video_id"]
            )

    def test_search_limit_zero(self, client: TestClient):
        resp = client.post(
            "/api/v1/candidates/search", json={"keyword": "", "limit": 0}
        )
        assert resp.status_code == 422  # Pydantic validation: ge=1


# ---------------------------------------------------------------------------
# /api/v1/transcriptions
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("isolated_transcription_dependencies")
class TestTranscriptions:
    def test_create_mock_task(self, client: TestClient):
        resp = client.post(
            "/api/v1/transcriptions",
            json={"media_name": "demo.mp4", "rights_confirmed": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "succeeded"
        assert data["progress"] == 100
        assert data["media_name"] == "demo.mp4"
        assert len(data["segments"]) > 0

    def test_create_task_without_rights(self, client: TestClient):
        resp = client.post(
            "/api/v1/transcriptions",
            json={"media_name": "demo.mp4", "rights_confirmed": False},
        )
        assert resp.status_code == 400

    def test_get_task_by_id(self, client: TestClient):
        # 先创建一个任务
        create_resp = client.post(
            "/api/v1/transcriptions",
            json={"media_name": "lookup.mp4", "rights_confirmed": True},
        )
        task_id = create_resp.json()["task_id"]

        # 查询该任务
        get_resp = client.get(f"/api/v1/transcriptions/{task_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["task_id"] == task_id

    def test_get_nonexistent_task(self, client: TestClient):
        resp = client.get("/api/v1/transcriptions/nonexistent-id")
        assert resp.status_code == 404

    def test_task_segments_have_fields(self, client: TestClient):
        resp = client.post(
            "/api/v1/transcriptions",
            json={"media_name": "segments.mp4", "rights_confirmed": True},
        )
        segments = resp.json()["segments"]
        assert len(segments) > 0
        seg = segments[0]
        assert "start" in seg
        assert "end" in seg
        assert "text" in seg
        assert "confidence" in seg
        assert "needs_review" in seg

    def test_task_segments_keep_word_timestamps(self):
        now = __import__("datetime").datetime.now().astimezone()
        task = TranscriptionTask(
            task_id="word-clock-task",
            title="word-clock.mp4",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            media_name="word-clock.mp4",
            media_type="video/mp4",
            rights_confirmed=True,
            segments=[
                TranscriptSegment(
                    start=0,
                    end=1,
                    text="测试字幕",
                    words=[
                        {"start": 0, "end": 0.4, "text": "测试"},
                        {"start": 0.4, "end": 1, "text": "字幕"},
                    ],
                )
            ],
        )

        response = transcriptions_api._to_response(task)

        assert response.segments[0]["words"] == [
            {"start": 0, "end": 0.4, "text": "测试"},
            {"start": 0.4, "end": 1, "text": "字幕"},
        ]

    def test_create_task_default_media_type(self, client: TestClient):
        resp = client.post(
            "/api/v1/transcriptions",
            json={"media_name": "default.mp4", "rights_confirmed": True},
        )
        assert resp.status_code == 200

    def test_list_transcriptions_and_export_mock(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/transcriptions",
            json={"media_name": "exportable.mp4", "rights_confirmed": True},
        )
        task_id = create_resp.json()["task_id"]

        list_resp = client.get("/api/v1/transcriptions")
        assert list_resp.status_code == 200
        assert all(item["task_id"] != task_id for item in list_resp.json())

        test_list_resp = client.get("/api/v1/transcriptions?include_mock=true")
        assert test_list_resp.status_code == 200
        assert any(item["task_id"] == task_id for item in test_list_resp.json())

        export_resp = client.get(f"/api/v1/transcriptions/{task_id}/export?format=srt")
        assert export_resp.status_code == 200
        assert b"00:00:00,000 -->" in export_resp.content

    def test_manual_text_import_is_free_untimed_and_blocks_subtitle_export(
        self, client: TestClient
    ):
        create_resp = client.post(
            "/api/v1/transcriptions/manual-text",
            json={
                "text": "第一段文案。\n\n第二段文案。",
                "rights_confirmed": True,
                "rights_holder": "测试公司",
                "media_name": "豆包回填",
                "source_url": "https://v.douyin.com/example/",
            },
        )
        assert create_resp.status_code == 200
        data = create_resp.json()
        task_id = data["task_id"]
        assert data["source_kind"] == "manual_text"
        assert data["timing_available"] is False
        assert data["segments"][0]["start"] is None
        assert data["segments"][0]["end"] is None

        approve_resp = client.post(
            f"/api/v1/transcriptions/{task_id}/revisions",
            json={
                "segments": [
                    {
                        **segment,
                        "reviewed": True,
                    }
                    for segment in data["segments"]
                ],
                "reviewer": "校对员",
                "approve": True,
            },
        )
        assert approve_resp.status_code == 200

        txt_resp = client.get(f"/api/v1/transcriptions/{task_id}/export?format=txt")
        assert txt_resp.status_code == 200
        assert "第一段文案。".encode("utf-8") in txt_resp.content

        srt_resp = client.get(f"/api/v1/transcriptions/{task_id}/export?format=srt")
        assert srt_resp.status_code == 400
        assert "没有时间轴" in srt_resp.json()["message"]


# ---------------------------------------------------------------------------
# /api/v1/pipelines
# ---------------------------------------------------------------------------


class TestPipelines:
    def test_create_pipeline(self, client: TestClient):
        resp = client.post("/api/v1/pipelines", json={"keyword": "汽车"})
        assert resp.status_code == 200
        data = resp.json()
        assert "run_id" in data
        assert data["keyword"] == "汽车"
        assert "status" in data

    def test_get_pipeline_by_id(self, client: TestClient):
        create_resp = client.post("/api/v1/pipelines", json={"keyword": "美食"})
        run_id = create_resp.json()["run_id"]

        get_resp = client.get(f"/api/v1/pipelines/{run_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["run_id"] == run_id

    def test_get_nonexistent_pipeline(self, client: TestClient):
        resp = client.get("/api/v1/pipelines/nonexistent-id")
        assert resp.status_code == 404

    def test_pipeline_stages_list(self, client: TestClient):
        resp = client.post("/api/v1/pipelines", json={"keyword": "科技"})
        data = resp.json()
        assert isinstance(data["stages"], list)

    def test_create_pipeline_with_config(self, client: TestClient):
        resp = client.post(
            "/api/v1/pipelines",
            json={"keyword": "教育", "config": {"mode": "sandbox"}},
        )
        assert resp.status_code == 200

    def test_create_pipeline_from_candidate(self, client: TestClient):
        class FakePipelineService:
            def execute_candidate_script_pipeline(self, **kwargs):
                assert kwargs["idempotency_key"] == "idem-pipeline-api"
                return PipelineRun(
                    run_id="pipeline-api-1",
                    keyword="候选标题",
                    status=PipelineRunStatus.PAUSED,
                    current_stage=PipelineStage.HUMAN_REVIEW,
                    stages=[
                        PipelineStepResult(
                            stage=PipelineStage.TRANSCRIPTION,
                            status=TaskStatus.SUCCEEDED,
                            task_id="transcript-api-1",
                            outputs={"task_id": "transcript-api-1"},
                        )
                    ],
                    copywriting_task_id="copy-api-1",
                )

        app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: (
            FakePipelineService()
        )
        try:
            resp = client.post(
                "/api/v1/pipelines/from-candidate",
                headers={"Idempotency-Key": "idem-pipeline-api"},
                json={
                    "candidate_id": "candidate-1",
                    "rights_confirmed": True,
                    "rights_holder": "测试公司",
                },
            )
        finally:
            app.dependency_overrides.pop(backend_deps.get_pipeline_service, None)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "paused"
        assert data["current_stage"] == "human_review"
        assert data["stages"][0]["outputs"]["task_id"] == "transcript-api-1"

    def test_create_pipeline_empty_keyword(self, client: TestClient):
        resp = client.post("/api/v1/pipelines", json={"keyword": ""})
        assert resp.status_code == 422  # min_length=1

    def test_list_pipelines(self, client: TestClient):
        client.post("/api/v1/pipelines", json={"keyword": "列表检查"})
        resp = client.get("/api/v1/pipelines")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(item["keyword"] == "列表检查" for item in data)

    def test_delete_pipeline_history(self, client: TestClient):
        create_resp = client.post("/api/v1/pipelines", json={"keyword": "可删除批次"})
        run_id = create_resp.json()["run_id"]

        deleted = client.delete(f"/api/v1/pipelines/{run_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"run_id": run_id, "deleted": True}
        assert client.get(f"/api/v1/pipelines/{run_id}").status_code == 404

    def test_delete_all_pipeline_history(self, client: TestClient):
        repository = MockRepository(candidates=[], tasks=[])
        repository.save_pipeline_run(
            PipelineRun(run_id="pipeline-clear-1", keyword="待清空 1")
        )
        repository.save_pipeline_run(
            PipelineRun(run_id="pipeline-clear-2", keyword="待清空 2")
        )
        app.dependency_overrides[backend_deps.get_repository] = lambda: repository
        try:
            blocked = client.delete("/api/v1/pipelines")
            assert blocked.status_code == 400
            assert len(repository.list_pipeline_runs()) == 2
            deleted = client.delete(
                "/api/v1/pipelines",
                headers={"X-Confirm-Reset": "yes-reset-all-pipelines"},
            )
        finally:
            app.dependency_overrides.pop(backend_deps.get_repository, None)

        assert deleted.status_code == 200
        assert deleted.json() == {"deleted_count": 2}
        assert repository.list_pipeline_runs() == []


# ---------------------------------------------------------------------------
# /api/v1/tasks
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("isolated_transcription_dependencies")
class TestTasks:
    def test_list_tasks(self, client: TestClient):
        resp = client.get("/api/v1/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)

    def test_task_item_fields(self, client: TestClient):
        # 先创建一个任务确保列表非空
        created = client.post(
            "/api/v1/transcriptions",
            json={"media_name": "task_fields.mp4", "rights_confirmed": True},
        )
        assert created.status_code == 200
        resp = client.get("/api/v1/tasks")
        items = resp.json()["items"]
        assert len(items) > 0
        item = items[0]
        assert "task_id" in item
        assert "kind" in item
        assert "title" in item
        assert "status" in item
        assert "progress" in item
        assert "finished_at" in item
        detail = client.get(f"/api/v1/tasks/{created.json()['task_id']}")
        assert detail.status_code == 200
        assert detail.json()["finished_at"] is not None

    def test_tasks_include_created_transcriptions(self, client: TestClient):
        """创建转写任务后应出现在任务列表中。"""
        before = client.get("/api/v1/tasks").json()["total"]
        created = client.post(
            "/api/v1/transcriptions",
            json={"media_name": "new_task.mp4", "rights_confirmed": True},
        )
        after = client.get("/api/v1/tasks").json()["total"]
        assert after >= before

        task_id = created.json()["task_id"]
        deleted = client.delete(f"/api/v1/tasks/{task_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"task_id": task_id, "deleted": True}
        assert client.get(f"/api/v1/tasks/{task_id}").status_code == 404


# ---------------------------------------------------------------------------
# /api/v1/crawler
# ---------------------------------------------------------------------------


def test_xiaohongshu_platform_hot_sort_does_not_require_duplicate_heat_floor():
    candidate = SimpleNamespace(
        platform=Platform.XIAOHONGSHU,
        evidence="xiaohongshu:browser_search_response;time=platform_filter",
        official_hot=False,
        metrics=SimpleNamespace(
            likes=None,
            comments=None,
            shares=None,
            favorites=None,
            plays=None,
        ),
    )

    assert crawler_api._passes_main_board_heat_floor(candidate) is True


class TestCrawlerBatches:
    @pytest.fixture(autouse=True)
    def crawler_sandbox(self):
        """集成测试禁止读取本机 Production Key 并产生付费请求。"""

        class FakeOfficialAdapter:
            def __init__(self, provider_name: str) -> None:
                self.provider_name = provider_name

            def capabilities(self):
                return SourceCapability(
                    provider_name=self.provider_name,
                    enabled=False,
                    permission_status="credentials_missing",
                    missing_configuration=[
                        "DOUYIN_CLIENT_KEY",
                        "DOUYIN_CLIENT_SECRET",
                    ],
                )

        class FakeBilibiliProvider:
            platform = Platform.BILIBILI
            provider_name = "bilibili_local_browser"
            browser_channel = "chrome"
            adapter_version = "test"

            def capabilities(self):
                return ProviderCapability(
                    provider_name=self.provider_name,
                    display_name="B站浏览器（测试）",
                    mode=ProviderMode.LOCAL_BROWSER,
                    supported_platforms=[Platform.BILIBILI],
                    max_page_size=30,
                    enabled=True,
                    permission_status="local_browser_session",
                )

            def session_status(self):
                return SimpleNamespace(
                    enabled=True,
                    running=True,
                    login_required=False,
                    ready_to_crawl=True,
                    phase="ready",
                    message="B站浏览器已连接。",
                )

            def search(
                self,
                platform,
                keyword,
                published_after,
                limit,
                idempotency_key,
                hotspot_window_hours=None,
            ):
                del published_after, idempotency_key, hotspot_window_hours
                now = __import__("datetime").datetime.now().astimezone()
                items = [
                    ProviderSearchItem(
                        platform=Platform.BILIBILI,
                        platform_item_id=f"BV1TEST{index:04d}",
                        source_url=f"https://www.bilibili.com/video/BV1TEST{index:04d}",
                        title=f"{keyword} B站新内容 {index + 1}",
                        author_id="test-author",
                        author_name="测试作者",
                        published_at=now,
                        metrics=VideoMetricSnapshot(
                            item_id=f"BV1TEST{index:04d}",
                            sampled_at=now,
                            plays=1000 + index,
                            comments=10,
                            favorites=20,
                            confidence=0.75,
                        ),
                        provider_rank=index + 1,
                    )
                    for index in range(limit)
                ]
                return ProviderSearchPage(
                    platform=platform,
                    provider=self.provider_name,
                    items=items,
                    observed_at=now,
                    request_id="fake-bilibili",
                    api_call_count=1,
                    billable_units=0,
                )

        class FakeBilibiliPublicMetricsProvider:
            """Read-only public-detail double; avoids network traffic in API tests."""

            provider_name = "bilibili_public_search"

            def __init__(self) -> None:
                self.refresh_calls: list[list[str]] = []

            def capabilities(self):
                return ProviderCapability(
                    provider_name=self.provider_name,
                    display_name="B站公开详情（测试）",
                    mode=ProviderMode.PUBLIC_WEB,
                    supported_platforms=[Platform.BILIBILI],
                    max_page_size=10,
                    enabled=True,
                    supports_metric_refresh=True,
                    permission_status="public_metadata_only",
                )

            def refresh_metrics(self, platform, platform_item_ids, idempotency_key):
                assert platform == Platform.BILIBILI
                assert idempotency_key
                assert len(platform_item_ids) <= 10
                self.refresh_calls.append(list(platform_item_ids))
                now = __import__("datetime").datetime.now().astimezone()
                items = [
                    ProviderSearchItem(
                        platform=Platform.BILIBILI,
                        platform_item_id=item_id,
                        source_url=f"https://www.bilibili.com/video/{item_id}",
                        title=f"公开详情 {item_id}",
                        author_id="detail-author",
                        author_name="详情作者",
                        published_at=now,
                        duration_seconds=60 + index,
                        metrics=VideoMetricSnapshot(
                            item_id=item_id,
                            sampled_at=now,
                            plays=10_000 + index,
                            likes=100 + index,
                            comments=20 + index,
                            shares=30 + index,
                            favorites=40 + index,
                            confidence=0.85,
                        ),
                        provider_rank=index + 1,
                        evidence="bilibili_public_detail:test",
                    )
                    for index, item_id in enumerate(platform_item_ids)
                ]
                return ProviderSearchPage(
                    platform=Platform.BILIBILI,
                    provider=self.provider_name,
                    items=items,
                    observed_at=now,
                    request_id="fake-bilibili-public-detail",
                    api_call_count=1,
                    billable_units=0,
                )

        class FakeBrowserProvider:
            def __init__(
                self,
                platform: Platform,
                *,
                xiaohongshu_login_profile: bool = False,
            ) -> None:
                self.platform = platform
                self.provider_name = f"{platform.value}_local_browser"
                self.browser_channel = "chrome"
                self.adapter_version = "test"
                self.xiaohongshu_login_profile = xiaohongshu_login_profile
                self.running = False
                self.start_calls = 0
                self.public_start_calls = 0
                self.visible_open_calls = 0
                self.published_after_values = []
                self.reset_calls = 0

            def capabilities(self):
                return ProviderCapability(
                    provider_name=self.provider_name,
                    display_name=f"{self.platform.value}浏览器（测试）",
                    mode=ProviderMode.LOCAL_BROWSER,
                    supported_platforms=[self.platform],
                    max_page_size=30,
                    enabled=True,
                    permission_status=(
                        "manual_login_required"
                        if self.xiaohongshu_login_profile
                        else "local_browser_login_required"
                    ),
                )

            def session_status(self):
                if self.xiaohongshu_login_profile:
                    return SimpleNamespace(
                        enabled=True,
                        running=self.running,
                        login_required=not self.running,
                        ready_to_crawl=self.running,
                        phase=("ready" if self.running else "waiting_login"),
                        message=(
                            "小红书已登录，可开始找素材。"
                            if self.running
                            else "小红书当前未登录，请先完成扫码。"
                        ),
                    )
                return SimpleNamespace(
                    enabled=True,
                    running=self.running,
                    login_required=False,
                    ready_to_crawl=self.running,
                    phase="ready" if self.running else "browser_closed",
                    message=(
                        f"{self.platform.value}公开页面已就绪。"
                        if self.running
                        else f"{self.platform.value}浏览器尚未打开。"
                    ),
                )

            def open_login_browser(self):
                if (
                    self.platform == Platform.XIAOHONGSHU
                    and not self.xiaohongshu_login_profile
                ):
                    pytest.fail("小红书匿名公开搜索不能调用 open_login_browser")
                self.visible_open_calls += 1
                self.running = True
                return self.session_status()

            def start_login_browser(self):
                if (
                    self.platform == Platform.XIAOHONGSHU
                    and not self.xiaohongshu_login_profile
                ):
                    pytest.fail("小红书未登录公开搜索不能调用 start_login_browser")
                self.start_calls += 1
                self.running = True
                return self.session_status()

            def reset_login_state(self, *, confirmed=False):
                if not confirmed:
                    raise LicensedProviderError(
                        "需要明确确认", kind=ProviderErrorKind.VALIDATION
                    )
                self.reset_calls += 1
                self.running = False
                return SimpleNamespace(
                    enabled=True,
                    running=False,
                    login_required=True,
                    ready_to_crawl=False,
                    phase="waiting_login",
                    message="已重置，请人工重新登录。",
                )

            def start_public_browser(self):
                self.public_start_calls += 1
                self.running = True
                return self.session_status()

            def search(
                self,
                platform,
                keyword,
                published_after,
                limit,
                idempotency_key,
                hotspot_window_hours=None,
            ):
                self.published_after_values.append(published_after)
                del idempotency_key, hotspot_window_hours
                now = __import__("datetime").datetime.now().astimezone()
                items = [
                    ProviderSearchItem(
                        platform=self.platform,
                        platform_item_id=f"{self.platform.value}-test-{index}",
                        source_url=(
                            f"https://www.xiaohongshu.com/explore/{self.platform.value}-test-{index}"
                            if self.platform == Platform.XIAOHONGSHU
                            else f"https://www.douyin.com/video/{self.platform.value}-test-{index}"
                            if self.platform == Platform.DOUYIN
                            else f"https://www.kuaishou.com/short-video/{self.platform.value}-test-{index}"
                        ),
                        title=f"{keyword} {self.platform.value}公开内容 {index + 1}",
                        author_id="test-author",
                        author_name="测试作者",
                        published_at=now,
                        metrics=VideoMetricSnapshot(
                            item_id=f"{self.platform.value}-test-{index}",
                            sampled_at=now,
                            likes=100 + index,
                            comments=10,
                            confidence=0.7,
                        ),
                        provider_rank=index + 1,
                    )
                    for index in range(limit)
                ]
                return ProviderSearchPage(
                    platform=platform,
                    provider=self.provider_name,
                    items=items,
                    observed_at=now,
                    request_id=f"fake-{self.platform.value}",
                    api_call_count=0,
                    billable_units=0,
                )

        class FakeHotspotProvider:
            provider_name = "douyin_local_browser"
            browser_channel = "chrome"
            adapter_version = "test"

            def capabilities(self):
                return ProviderCapability(
                    provider_name=self.provider_name,
                    display_name="热点宝浏览器（测试关闭）",
                    mode=ProviderMode.LOCAL_BROWSER,
                    supported_platforms=[],
                    max_page_size=100,
                    enabled=False,
                    permission_status="disabled",
                )

            def session_status(self):
                return SimpleNamespace(
                    enabled=False,
                    running=False,
                    login_required=False,
                    ready_to_crawl=False,
                    phase="disabled",
                    message="热点宝测试来源已关闭。",
                )

        repository = MockRepository(candidates=[], tasks=[])
        provider = SandboxLicensedSearchProvider()
        service = CommercialSearchService(
            repository,
            SourceService(repository, HeatService()),
            KeywordTrendService(repository),
            provider,
            active_platforms=(Platform.DOUYIN,),
        )
        bilibili_provider = FakeBilibiliProvider()
        bilibili_metrics_provider = FakeBilibiliPublicMetricsProvider()
        bilibili_service = CommercialSearchService(
            repository,
            SourceService(repository, HeatService()),
            KeywordTrendService(repository),
            bilibili_provider,
            active_platforms=(Platform.BILIBILI,),
        )
        xiaohongshu_provider = FakeBrowserProvider(
            Platform.XIAOHONGSHU,
            xiaohongshu_login_profile=True,
        )
        xiaohongshu_login_provider = xiaohongshu_provider
        xiaohongshu_service = CommercialSearchService(
            repository,
            SourceService(repository, HeatService()),
            KeywordTrendService(repository),
            xiaohongshu_provider,
            active_platforms=(Platform.XIAOHONGSHU,),
        )
        douyin_public_provider = FakeBrowserProvider(Platform.DOUYIN)
        douyin_public_provider.provider_name = "douyin_public_browser_v2"
        douyin_public_service = CommercialSearchService(
            repository,
            SourceService(repository, HeatService()),
            KeywordTrendService(repository),
            douyin_public_provider,
            active_platforms=(Platform.DOUYIN,),
        )
        kuaishou_provider = FakeBrowserProvider(Platform.KUAISHOU)
        kuaishou_service = CommercialSearchService(
            repository,
            SourceService(repository, HeatService()),
            KeywordTrendService(repository),
            kuaishou_provider,
            active_platforms=(Platform.KUAISHOU,),
        )
        hotspot_provider = FakeHotspotProvider()
        hotspot_service = CommercialSearchService(
            repository,
            SourceService(repository, HeatService()),
            KeywordTrendService(repository),
            hotspot_provider,
            active_platforms=(Platform.DOUYIN,),
        )
        app.dependency_overrides[backend_deps.get_repository] = lambda: repository
        app.dependency_overrides[backend_deps.get_licensed_search_provider] = lambda: (
            provider
        )
        app.dependency_overrides[backend_deps.get_discovery_search_provider] = lambda: (
            provider
        )
        app.dependency_overrides[backend_deps.get_commercial_search_service] = lambda: (
            service
        )
        app.dependency_overrides[backend_deps.get_bilibili_browser_provider] = lambda: (
            bilibili_provider
        )
        app.dependency_overrides[backend_deps.get_bilibili_browser_search_service] = (
            lambda: bilibili_service
        )
        app.dependency_overrides[backend_deps.get_bilibili_public_metrics_provider] = (
            lambda: bilibili_metrics_provider
        )
        app.dependency_overrides[backend_deps.get_xiaohongshu_browser_provider] = (
            lambda: xiaohongshu_provider
        )
        app.dependency_overrides[
            backend_deps.get_xiaohongshu_login_browser_provider
        ] = lambda: xiaohongshu_login_provider
        app.dependency_overrides[backend_deps.get_douyin_public_browser_provider] = (
            lambda: douyin_public_provider
        )
        app.dependency_overrides[backend_deps.get_douyin_public_search_service] = (
            lambda: douyin_public_service
        )
        app.dependency_overrides[
            backend_deps.get_xiaohongshu_browser_search_service
        ] = lambda: xiaohongshu_service
        app.dependency_overrides[backend_deps.get_kuaishou_browser_provider] = lambda: (
            kuaishou_provider
        )
        app.dependency_overrides[backend_deps.get_kuaishou_browser_search_service] = (
            lambda: kuaishou_service
        )
        app.dependency_overrides[backend_deps.get_hotspot_browser_provider] = lambda: (
            hotspot_provider
        )
        app.dependency_overrides[backend_deps.get_hotspot_search_service] = lambda: (
            hotspot_service
        )
        app.dependency_overrides[backend_deps.get_official_hot_billboard_adapter] = (
            lambda: FakeOfficialAdapter("douyin_hot_billboard")
        )
        app.dependency_overrides[backend_deps.get_official_hot_words_adapter] = lambda: (
            FakeOfficialAdapter("douyin_hot_words")
        )
        yield SimpleNamespace(
            douyin_provider=douyin_public_provider,
            xiaohongshu_provider=xiaohongshu_provider,
            xiaohongshu_login_provider=xiaohongshu_login_provider,
            bilibili_metrics_provider=bilibili_metrics_provider,
            kuaishou_provider=kuaishou_provider,
        )
        app.dependency_overrides.pop(backend_deps.get_repository, None)
        app.dependency_overrides.pop(
            backend_deps.get_licensed_search_provider,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_discovery_search_provider,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_commercial_search_service,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_bilibili_browser_provider,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_bilibili_browser_search_service,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_bilibili_public_metrics_provider,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_xiaohongshu_browser_provider,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_xiaohongshu_login_browser_provider,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_douyin_public_browser_provider,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_douyin_public_search_service,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_xiaohongshu_browser_search_service,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_kuaishou_browser_provider,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_kuaishou_browser_search_service,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_hotspot_browser_provider,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_hotspot_search_service,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_official_hot_billboard_adapter,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_official_hot_words_adapter,
            None,
        )

    def test_capabilities(self, client: TestClient, crawler_sandbox):
        resp = client.get("/api/v1/crawler/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] in {"sandbox", "production"}
        assert "monthly_query_count" in data
        assert "supported_platforms" in data
        assert data["active_platforms"] == ["douyin"]
        assert data["paused_platforms"] == ["xiaohongshu", "wechat_channels"]
        assert data["official_hot_billboard"]["enabled"] is False
        assert data["official_hot_words"]["provider_name"] == "douyin_hot_words"
        assert data["hotspot_browser"] is None
        assert [item["platform"] for item in data["platform_browsers"]] == [
            "douyin",
            "xiaohongshu",
            "kuaishou",
            "bilibili",
        ]
        xiaohongshu = next(
            item
            for item in data["platform_browsers"]
            if item["platform"] == "xiaohongshu"
        )
        assert xiaohongshu["running"] is False
        assert xiaohongshu["login_required"] is True
        assert xiaohongshu["phase"] == "waiting_login"
        assert "请先完成扫码" in xiaohongshu["message"]
        assert crawler_sandbox.xiaohongshu_provider.public_start_calls == 0

    def test_browser_discovery_capabilities_include_prerequisites(
        self, client: TestClient, crawler_sandbox
    ):
        resp = client.get("/api/v1/crawler/browser-discovery/capabilities")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["missing_configuration"], list)
        assert data["browser_channel"] in {"chrome", "msedge"}

        douyin_resp = client.get(
            "/api/v1/crawler/browser-discovery/douyin/capabilities"
        )
        assert douyin_resp.status_code == 200
        assert douyin_resp.json()["platform"] == "douyin"
        douyin_start = client.post("/api/v1/crawler/browser-discovery/douyin/start")
        assert douyin_start.status_code == 200
        assert douyin_start.json()["provider_name"] == "douyin_public_browser_v2"

        xiaohongshu_resp = client.get(
            "/api/v1/crawler/browser-discovery/xiaohongshu/capabilities"
        )
        assert xiaohongshu_resp.status_code == 200
        assert xiaohongshu_resp.json()["platform"] == "xiaohongshu"
        assert xiaohongshu_resp.json()["phase"] == "waiting_login"
        xiaohongshu_start = client.post(
            "/api/v1/crawler/browser-discovery/xiaohongshu/start"
        )
        assert xiaohongshu_start.status_code == 200
        assert xiaohongshu_start.json()["running"] is True
        assert xiaohongshu_start.json()["phase"] == "ready"
        assert crawler_sandbox.xiaohongshu_login_provider.visible_open_calls == 1
        assert crawler_sandbox.xiaohongshu_provider.public_start_calls == 0

        refreshed = client.get("/api/v1/crawler/capabilities")
        assert refreshed.status_code == 200
        refreshed_xiaohongshu = next(
            item
            for item in refreshed.json()["platform_browsers"]
            if item["platform"] == "xiaohongshu"
        )
        assert refreshed_xiaohongshu["running"] is True
        assert refreshed_xiaohongshu["phase"] == "ready"

        bilibili_resp = client.get(
            "/api/v1/crawler/browser-discovery/bilibili/capabilities"
        )
        assert bilibili_resp.status_code == 200
        assert bilibili_resp.json()["platform"] == "bilibili"

    def test_browser_login_reset_requires_confirmation_and_is_platform_scoped(
        self, client: TestClient, crawler_sandbox
    ):
        rejected = client.post(
            "/api/v1/crawler/browser-discovery/douyin/reset-login",
            json={"confirmed": False},
        )
        assert rejected.status_code == 400
        assert crawler_sandbox.douyin_provider.reset_calls == 0

        reset = client.post(
            "/api/v1/crawler/browser-discovery/douyin/reset-login",
            json={"confirmed": True},
        )
        assert reset.status_code == 200
        assert reset.json()["reset"] is True
        assert reset.json()["manual_login_required"] is True
        assert "人工重新登录" in reset.json()["message"]
        assert crawler_sandbox.douyin_provider.reset_calls == 1

    def test_browser_login_endpoint_prefers_visible_window(self, client: TestClient):
        class VisibleBrowserProvider:
            browser_channel = "chrome"
            adapter_version = "test"

            def __init__(self):
                self.visible_open_calls = 0

            def capabilities(self):
                return SimpleNamespace(
                    enabled=True,
                    missing_configuration=[],
                    provider_name="douyin_public_browser_v2",
                )

            def session_status(self):
                return SimpleNamespace(
                    enabled=True,
                    running=False,
                    login_required=True,
                    ready_to_crawl=False,
                    phase="browser_closed",
                    message="尚未打开",
                )

            def open_login_browser(self):
                self.visible_open_calls += 1
                return SimpleNamespace(
                    enabled=True,
                    running=True,
                    login_required=True,
                    ready_to_crawl=False,
                    phase="waiting_login",
                    message="登录窗口已显示",
                )

            def start_login_browser(self):
                raise AssertionError("手动登录入口不应使用后台启动")

        provider = VisibleBrowserProvider()
        app.dependency_overrides[backend_deps.get_douyin_public_browser_provider] = (
            lambda: provider
        )
        try:
            response = client.post("/api/v1/crawler/browser-discovery/start")
        finally:
            app.dependency_overrides.pop(
                backend_deps.get_douyin_public_browser_provider, None
            )

        assert response.status_code == 200
        assert response.json()["message"] == "登录窗口已显示"
        assert response.json()["provider_name"] == "douyin_public_browser_v2"
        assert provider.visible_open_calls == 1

    def test_browser_login_endpoint_returns_actionable_local_error(
        self, client: TestClient
    ):
        class FailingBrowserProvider:
            browser_channel = "chrome"
            adapter_version = "test"

            def capabilities(self):
                return SimpleNamespace(
                    enabled=True,
                    missing_configuration=[],
                    provider_name="douyin_public_browser_v2",
                )

            def session_status(self):
                return SimpleNamespace(
                    enabled=True,
                    running=False,
                    login_required=True,
                    ready_to_crawl=False,
                    phase="browser_closed",
                    message="尚未打开",
                )

            def open_login_browser(self):
                raise OSError("simulated local browser launch failure")

        app.dependency_overrides[backend_deps.get_douyin_public_browser_provider] = (
            FailingBrowserProvider
        )
        try:
            response = client.post("/api/v1/crawler/browser-discovery/start")
        finally:
            app.dependency_overrides.pop(
                backend_deps.get_douyin_public_browser_provider, None
            )

        assert response.status_code == 503
        assert response.json()["message"] == (
            "无法打开本机登录浏览器，请关闭该专用浏览器后重试；仍失败请重启后端。"
        )

    def test_browser_login_endpoint_keeps_newly_running_browser_after_launch_race(
        self, client: TestClient
    ):
        class LaunchRaceBrowserProvider:
            browser_channel = "chrome"
            adapter_version = "test"

            def __init__(self):
                self.status_calls = 0

            def capabilities(self):
                return SimpleNamespace(
                    enabled=True,
                    missing_configuration=[],
                    provider_name="douyin_public_browser_v2",
                )

            def session_status(self):
                self.status_calls += 1
                if self.status_calls == 1:
                    return SimpleNamespace(
                        enabled=True,
                        running=False,
                        login_required=True,
                        ready_to_crawl=False,
                        phase="browser_closed",
                        message="尚未打开",
                    )
                return SimpleNamespace(
                    enabled=True,
                    running=True,
                    login_required=True,
                    ready_to_crawl=False,
                    phase="waiting_login",
                    message="抖音官网正在等待扫码登录。",
                )

            def open_login_browser(self):
                raise RuntimeError("simulated post-launch status race")

        provider = LaunchRaceBrowserProvider()
        app.dependency_overrides[backend_deps.get_douyin_public_browser_provider] = (
            lambda: provider
        )
        try:
            response = client.post("/api/v1/crawler/browser-discovery/start")
        finally:
            app.dependency_overrides.pop(
                backend_deps.get_douyin_public_browser_provider, None
            )

        assert response.status_code == 200
        assert response.json()["running"] is True
        assert response.json()["message"] == "抖音官网正在等待扫码登录。"
        assert response.json()["started"] is True

    def test_recrawl_is_disabled(self, client: TestClient):
        rejected = client.post(
            "/api/v1/crawler/preview",
            json={
                "keyword": "二手车",
                "count_per_platform": 2,
                "force_refresh": False,
                "track_trend": True,
            },
        )
        assert rejected.status_code == 422

        disabled = client.post("/api/v1/crawler/batches/legacy-batch/tracking")
        assert disabled.status_code == 410

        due = client.post("/api/v1/crawler/recrawls/due")
        assert due.status_code == 410

    def test_preview_free_multi_platform(self, client: TestClient):
        resp = client.post(
            "/api/v1/crawler/preview",
            json={
                "keyword": "二手车",
                "published_window_days": 180,
                "count_per_platform": 2,
                "force_refresh": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert [item["platform"] for item in data["platforms"]] == [
            "douyin",
            "xiaohongshu",
            "kuaishou",
            "bilibili",
        ]
        assert data["ranking_mode"] == "platform_default_search_then_table_sort"
        assert data["published_window_days"] == 180
        assert [item["platform_label"] for item in data["platforms"]] == [
            "抖音登录搜索（最多30条）",
            "小红书登录搜索",
            "快手浏览器搜索（平台默认综合排序）",
            "B站浏览器搜索（平台默认综合排序）",
        ]
        assert data["provider_name"] == "抖音登录搜索 + 小红书登录搜索 + 快手/B站浏览器"
        assert data["hotspot_ready"] is False
        assert data["hotspot_time_strategy"] == "douyin_official_search_only"
        assert data["cache_ttl_minutes"] == 10
        assert data["sampling_offsets_hours"] == [0]
        assert data["max_api_calls_per_platform"] == 0
        assert data["trend_tracking_enabled"] is False
        assert "estimated_total_cost_cny" in data
        assert all("estimated_api_calls" in item for item in data["platforms"])
        assert all("estimated_cost_cny" in item for item in data["platforms"])

    def test_free_multi_platform_never_resolves_hotspot_dependencies(
        self, client: TestClient
    ):
        def unexpected_hotspot_dependency():
            raise AssertionError("常规找素材不应解析热点宝依赖")

        app.dependency_overrides[backend_deps.get_hotspot_browser_provider] = (
            unexpected_hotspot_dependency
        )
        app.dependency_overrides[backend_deps.get_hotspot_search_service] = (
            unexpected_hotspot_dependency
        )
        try:
            capabilities = client.get("/api/v1/crawler/capabilities")
            preview = client.post(
                "/api/v1/crawler/preview",
                json={
                    "keyword": "贴标机",
                    "platforms": ["douyin"],
                    "count_per_platform": 2,
                },
            )
            created = client.post(
                "/api/v1/crawler/batches",
                json={
                    "keyword": "贴标机",
                    "platforms": ["douyin"],
                    "count_per_platform": 2,
                },
            )
        finally:
            app.dependency_overrides.pop(
                backend_deps.get_hotspot_browser_provider,
                None,
            )
            app.dependency_overrides.pop(
                backend_deps.get_hotspot_search_service,
                None,
            )

        assert capabilities.status_code == 200
        assert capabilities.json()["hotspot_browser"] is None
        assert preview.status_code == 200
        assert preview.json()["hotspot_ready"] is False
        assert created.status_code == 200
        created_data = created.json()
        assert created_data["platforms"] == ["douyin"]
        assert [run["platform"] for run in created_data["platform_runs"]] == ["douyin"]
        assert created_data["hotspot_window_hours"] is None

    def test_create_and_read_persistent_batch(
        self, client: TestClient, crawler_sandbox
    ):
        keyword = f"露营{uuid4().hex[:6]}"
        create_resp = client.post(
            "/api/v1/crawler/batches",
            json={
                "keyword": keyword,
                "published_window_days": 180,
                "count_per_platform": 2,
                "force_refresh": True,
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["status"] in {"succeeded", "partial", "failed"}
        assert created["published_window_days"] == 180
        assert {run["platform"] for run in created["platform_runs"]} == {
            "douyin",
            "xiaohongshu",
            "kuaishou",
            "bilibili",
        }
        assert crawler_sandbox.kuaishou_provider.published_after_values == [None]
        batch_id = created["batch_id"]
        detail_resp = client.get(f"/api/v1/crawler/batches/{batch_id}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["batch_id"] == batch_id
        assert sum(len(run["candidates"]) for run in detail["platform_runs"]) >= 1
        first_candidate = next(
            candidate
            for run in detail["platform_runs"]
            for candidate in run["candidates"]
        )
        assert "provider_hot_rank" in first_candidate
        assert "system_rank" in first_candidate
        assert "component_scores" in first_candidate
        assert "data_quality_warnings" in first_candidate
        assert first_candidate["relevance_basis"] in {
            "title_or_hashtag",
            "platform_search",
        }
        assert first_candidate["relevance_reason"]
        assert "duration_seconds" in first_candidate
        assert "published_at_reliable" in first_candidate
        assert "heat_score" in first_candidate
        # B站公开详情会在搜索结果后补一次真实指标快照；其他平台仍只有首个快照。
        assert len(first_candidate["trend_points"]) >= 1
        assert first_candidate["trend_points"][0]["effective_interactions"] >= 0
        first_run = detail["platform_runs"][0]
        assert first_run["relevant_count"] == first_run["returned_count"]
        assert "irrelevant_count" in first_run
        assert first_run["relevance_rule_version"].startswith(
            "platform_search_final_eligible_v"
        )

        list_resp = client.get("/api/v1/crawler/batches")
        assert list_resp.status_code == 200
        assert any(item["batch_id"] == batch_id for item in list_resp.json()["items"])

    def test_bilibili_public_detail_enrichment_is_batched_beyond_top_ten(
        self,
        client: TestClient,
        crawler_sandbox,
    ):
        response = client.post(
            "/api/v1/crawler/batches",
            json={
                "keyword": "贴标机",
                "platforms": ["bilibili"],
                "count_per_platform": 12,
                "force_refresh": True,
            },
        )

        assert response.status_code == 200
        run = response.json()["platform_runs"][0]
        assert run["platform"] == "bilibili"
        assert len(run["candidates"]) == 12
        assert len(crawler_sandbox.bilibili_metrics_provider.refresh_calls) == 2
        assert len(crawler_sandbox.bilibili_metrics_provider.refresh_calls[0]) == 10
        assert len(crawler_sandbox.bilibili_metrics_provider.refresh_calls[1]) == 2
        assert "B站公开详情已补全前 12 条：成功 12 条" in run["payload_diagnostic"]

        enriched = [item for item in run["candidates"] if item["likes"] is not None]
        not_enriched = [item for item in run["candidates"] if item["likes"] is None]
        assert len(enriched) == 12
        assert len(not_enriched) == 0
        assert all(item["comments"] is not None for item in enriched)
        assert all(item["shares"] is not None for item in enriched)
        assert all(item["favorites"] is not None for item in enriched)
        assert all(item["duration_seconds"] is not None for item in enriched)

    def test_xiaohongshu_uses_login_profile_and_keeps_requested_count(
        self, client: TestClient, crawler_sandbox
    ):
        resp = client.post(
            "/api/v1/crawler/batches",
            json={
                "keyword": "贴标机",
                "platforms": ["xiaohongshu"],
                "count_per_platform": 30,
                "force_refresh": True,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["platforms"] == ["xiaohongshu"]
        assert len(data["platform_runs"]) == 1
        assert data["platform_runs"][0]["platform"] == "xiaohongshu"
        assert data["platform_runs"][0]["requested_count"] == 30
        assert crawler_sandbox.xiaohongshu_provider.start_calls == 1
        assert crawler_sandbox.xiaohongshu_provider.public_start_calls == 0
        assert crawler_sandbox.xiaohongshu_login_provider.visible_open_calls == 0

    def test_xiaohongshu_cache_does_not_restart_login_browser(
        self,
        client: TestClient,
        crawler_sandbox,
    ):
        payload = {
            "keyword": "缓存贴标机",
            "platforms": ["xiaohongshu"],
            "count_per_platform": 30,
            "published_window_days": 180,
        }
        first = client.post(
            "/api/v1/crawler/batches",
            json={**payload, "force_refresh": True},
        )
        assert first.status_code == 200
        assert crawler_sandbox.xiaohongshu_provider.public_start_calls == 0
        assert crawler_sandbox.xiaohongshu_provider.start_calls == 1

        crawler_sandbox.xiaohongshu_provider.running = False
        cached = client.post("/api/v1/crawler/batches", json=payload)

        assert cached.status_code == 200
        assert crawler_sandbox.xiaohongshu_provider.public_start_calls == 0
        assert crawler_sandbox.xiaohongshu_provider.start_calls == 1
        assert cached.json()["platform_runs"][0]["cache_hit"] is True

    @pytest.mark.parametrize(
        ("platform", "count"),
        [
            ("douyin", 30),
            ("xiaohongshu", 30),
            ("kuaishou", 30),
            ("bilibili", 30),
        ],
    )
    def test_cached_free_platform_does_not_call_browser_start_again(
        self,
        client: TestClient,
        crawler_sandbox,
        monkeypatch,
        platform: str,
        count: int,
    ):
        del crawler_sandbox
        original_start = crawler_api._start_browser_for_search
        start_calls: list[object] = []

        def tracked_start(provider, *, public_only: bool = False):
            start_calls.append(provider)
            return original_start(provider, public_only=public_only)

        monkeypatch.setattr(crawler_api, "_start_browser_for_search", tracked_start)
        payload = {
            "keyword": f"缓存保护-{platform}",
            "platforms": [platform],
            "count_per_platform": count,
            "published_window_days": 180,
        }

        first = client.post(
            "/api/v1/crawler/batches",
            json={**payload, "force_refresh": True},
        )
        assert first.status_code == 200
        first_start_count = len(start_calls)
        assert first_start_count == 1

        cached = client.post("/api/v1/crawler/batches", json=payload)

        assert cached.status_code == 200
        assert len(start_calls) == first_start_count
        assert cached.json()["platform_runs"][0]["cache_hit"] is True

    def test_sequential_collection_returns_waiting_instead_of_a_failed_batch(
        self, client: TestClient, crawler_sandbox
    ):
        """同平台正在顺序采集时应返回等待，而不是造一条 failed 批次。

        此前第二个任务拿不到租约就会立刻生成 failed 批次，文案是
        "抖音正在顺序采集，请等待结束。"，用户看到的是"抓取失败"。
        """
        del crawler_sandbox
        from datetime import datetime, timedelta

        repository = backend_deps.get_repository()
        provider = crawler_api._browser_provider_key(Platform.DOUYIN)
        now = datetime.now().astimezone()
        assert repository.claim_provider_safety_lease(
            provider=provider,
            run_id="running-run",
            now=now,
            lease_seconds=90,
            backend_instance_id="backend-alive",
        )
        before = len(repository.list_search_batches(limit=100))

        previous = app.dependency_overrides.get(backend_deps.get_repository)
        app.dependency_overrides[backend_deps.get_repository] = lambda: repository
        try:
            resp = client.post(
                "/api/v1/crawler/batches",
                json={
                    "keyword": "顺序采集等待",
                    "platforms": ["douyin"],
                    "count_per_platform": 30,
                    "published_window_days": 180,
                    "force_refresh": True,
                },
            )
        finally:
            if previous is None:
                app.dependency_overrides.pop(backend_deps.get_repository, None)
            else:
                app.dependency_overrides[backend_deps.get_repository] = previous

        assert resp.status_code == 200
        data = resp.json()
        # 关键：是等待，不是失败。
        assert data["waiting"] is True
        assert data["status"] == "pending"
        assert data["error"] is None
        assert data["waiting_platforms"] == ["douyin"]
        assert data["waiting_remaining_seconds"] > 0
        assert data["waiting_retry_at"] is not None
        assert "顺序采集" in (data["waiting_reason"] or "")
        # 不得新增任何批次——尤其是不能新增 failed 批次。
        assert len(repository.list_search_batches(limit=100)) == before

    def test_hotwords_endpoint_returns_cached_suggestions(self, client: TestClient):
        now = __import__("datetime").datetime.now().astimezone()

        class FakeHotPoolService:
            def hot_word_suggestions(self, limit: int = 50):
                return [HotWordRecord(word="AI数字人", hot_value=100, fetched_at=now)]

        app.dependency_overrides[backend_deps.get_official_hot_pool_service] = lambda: (
            FakeHotPoolService()
        )
        try:
            resp = client.get("/api/v1/crawler/hotwords")
        finally:
            app.dependency_overrides.pop(
                backend_deps.get_official_hot_pool_service,
                None,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["words"][0]["word"] == "AI数字人"
        assert data["words"][0]["hot_value"] == 100

    def test_official_hot_monitor_returns_candidates(self, client: TestClient):
        repository = MockRepository(candidates=[], tasks=[])
        candidate = build_mock_candidates(1)[0].model_copy(
            update={
                "title": "AI 数字人口播获客",
                "matched_by": ["AI数字人"],
                "official_hot": True,
                "official_rank": 1,
            }
        )
        repository.save_candidate(candidate)

        class FakeHotPoolService:
            def execute_due_recrawls(self):
                return []

            def sync_billboard(self, *, limit: int):
                assert limit == 50

            def search_hot_pool(self, **kwargs):
                assert kwargs["keyword"] == "数字人"
                assert kwargs["limit"] == 10
                return SimpleNamespace(
                    matched=[candidate],
                    trends=[],
                    result_state="官方热榜匹配",
                    user_notice=None,
                    next_recrawl_at=None,
                )

        app.dependency_overrides[backend_deps.get_repository] = lambda: repository
        app.dependency_overrides[backend_deps.get_official_hot_pool_service] = lambda: (
            FakeHotPoolService()
        )
        try:
            resp = client.post(
                "/api/v1/crawler/official-hot/monitor",
                json={"keyword": "数字人"},
            )
        finally:
            app.dependency_overrides.pop(
                backend_deps.get_official_hot_pool_service,
                None,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["matched_count"] == 1
        assert data["executed_recrawls"] == 0
        assert data["candidates"][0]["title"] == "AI 数字人口播获客"
        assert data["candidates"][0]["provider_hot_rank"] == 1

    def test_original_script_endpoint_marks_metadata_not_transcript(
        self, client: TestClient
    ):
        repository = MockRepository(candidates=[], tasks=[])
        candidate = build_mock_candidates(1)[0].model_copy(
            update={
                "title": "AI 数字人口播获客",
                "matched_by": ["AI数字人"],
            }
        )
        repository.save_candidate(candidate)
        copywriting = CopywritingService(repository, SandboxCopywritingEngine())
        app.dependency_overrides[backend_deps.get_repository] = lambda: repository
        app.dependency_overrides[backend_deps.get_copywriting_service] = lambda: (
            copywriting
        )
        try:
            resp = client.post(
                f"/api/v1/crawler/candidates/{candidate.video_id}/original-script"
            )
        finally:
            app.dependency_overrides.pop(backend_deps.get_copywriting_service, None)

        assert resp.status_code == 200
        data = resp.json()
        assert data["copy_source"] == "metadata_original"
        assert data["is_original_transcript"] is False
        assert data["needs_manual_review"] is True
        assert data["script"]

    def test_old_crawler_tasks_contract_removed(self, client: TestClient):
        resp = client.get("/api/v1/crawler/tasks")
        assert resp.status_code == 404

    def test_fastapi_oneapi_provider_requires_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        backend_deps.get_licensed_search_provider.cache_clear()
        monkeypatch.setattr(
            backend_deps,
            "CRAWLER_PROVIDER_MODE",
            CrawlerProviderMode.ONEAPI,
        )
        monkeypatch.setattr(backend_deps, "ONEAPI_API_KEY", "")

        provider = backend_deps.get_licensed_search_provider()
        assert isinstance(provider, OneApiLicensedSearchProvider)
        capability = provider.capabilities()
        assert capability.mode.value == "production"
        assert capability.enabled is False
        assert "OneAPI API Key" in capability.missing_configuration

    def test_fastapi_oneapi_provider_enables_with_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        backend_deps.get_licensed_search_provider.cache_clear()
        monkeypatch.setattr(
            backend_deps,
            "CRAWLER_PROVIDER_MODE",
            CrawlerProviderMode.ONEAPI,
        )
        monkeypatch.setattr(backend_deps, "ONEAPI_API_KEY", "configured-for-test")

        provider = backend_deps.get_licensed_search_provider()
        assert isinstance(provider, OneApiLicensedSearchProvider)
        capability = provider.capabilities()
        assert capability.mode.value == "production"
        assert capability.enabled is True
        assert capability.credential_alias == "ONEAPI_API_KEY"
        assert capability.missing_configuration == []
        assert capability.permission_status == "trial_unverified_commercial_rights"

        backend_deps.get_licensed_search_provider.cache_clear()

    def test_fastapi_production_mode_uses_oneapi_when_key_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        backend_deps.get_licensed_search_provider.cache_clear()
        monkeypatch.setattr(
            backend_deps,
            "CRAWLER_PROVIDER_MODE",
            CrawlerProviderMode.PRODUCTION,
        )
        monkeypatch.setattr(backend_deps, "ONEAPI_API_KEY", "configured-for-test")

        provider = backend_deps.get_licensed_search_provider()

        assert isinstance(provider, OneApiLicensedSearchProvider)
        assert provider.capabilities().enabled is True

        backend_deps.get_licensed_search_provider.cache_clear()


# ---------------------------------------------------------------------------
# OpenAPI / Swagger
# ---------------------------------------------------------------------------


class TestOpenAPI:
    def test_openapi_schema_available(self, client: TestClient):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "paths" in schema
        assert "/health" in schema["paths"]
        assert "/api/v1/candidates/search" in schema["paths"]
        assert "/api/v1/transcriptions" in schema["paths"]
        assert "/api/v1/pipelines" in schema["paths"]
        assert "/api/v1/tasks" in schema["paths"]
        assert "/api/v1/admin/status" in schema["paths"]
        assert "/api/v1/copywriting/capabilities" in schema["paths"]
        assert "/api/v1/copywriting/generate" in schema["paths"]


# ---------------------------------------------------------------------------
# GET /api/v1/tasks/{task_id}  —— 单任务查询
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("isolated_transcription_dependencies")
class TestGetTaskById:
    def test_get_existing_task(self, client: TestClient):
        """创建转写任务后，可通过 task_id 查询。"""
        create_resp = client.post(
            "/api/v1/transcriptions",
            json={"media_name": "single_task.mp4", "rights_confirmed": True},
        )
        assert create_resp.status_code == 200
        task_id = create_resp.json()["task_id"]

        get_resp = client.get(f"/api/v1/tasks/{task_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["task_id"] == task_id
        assert data["kind"] == "transcription"
        assert data["status"] == "succeeded"
        assert data["progress"] == 100

    def test_get_nonexistent_task(self, client: TestClient):
        """查询不存在的任务返回 404。"""
        resp = client.get("/api/v1/tasks/nonexistent-task-id-12345")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"] is True
        assert data["code"] == 404

    def test_task_item_has_all_fields(self, client: TestClient):
        """返回的 TaskItem 包含所有必要字段。"""
        create_resp = client.post(
            "/api/v1/transcriptions",
            json={"media_name": "fields_check.mp4", "rights_confirmed": True},
        )
        task_id = create_resp.json()["task_id"]

        resp = client.get(f"/api/v1/tasks/{task_id}")
        data = resp.json()
        assert "task_id" in data
        assert "kind" in data
        assert "title" in data
        assert "status" in data
        assert "progress" in data
        assert "created_at" in data


# ---------------------------------------------------------------------------
# /api/v1/copywriting  —— 文案改写
# ---------------------------------------------------------------------------


class TestCopywriting:
    pytestmark = pytest.mark.usefixtures(
        "copywriting_sandbox",
        "isolated_copywriting_dependencies",
    )

    def test_capabilities(self, client: TestClient):
        resp = client.get("/api/v1/copywriting/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["mode"] == "sandbox"
        assert data["supported_platforms"] == [
            "douyin",
            "kuaishou",
            "xiaohongshu",
            "wechat_channels",
            "bilibili",
        ]
        assert data["billing_label"] == "平台服务价"
        assert data["input_price_credits_per_1k_tokens"] == "0.0015"
        assert data["output_price_credits_per_1k_tokens"] == "0.003"
        assert data["minimum_charge_credits"] == "0.01"

    def test_generate_basic(self, client: TestClient):
        resp = client.post(
            "/api/v1/copywriting/generate",
            json={
                "content_brief": "介绍 AI 短视频获客系统",
                "platform": "douyin",
                "target_audience": "企业主",
                "selling_points": "降低内容制作成本",
                "call_to_action": "私信领取方案",
                "target_length": 100,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "succeeded"
        assert data["provider_name"] == "sandbox_copywriting"
        assert data["model_name"] == "sandbox-template"
        assert data["is_mock"] is True
        assert data["charged_credits"] == 0
        assert len(data["result_variants"]) == 1
        assert data["attention_terms"] == []
        assert data["compliance_status"] == "passed"
        assert data["compliance_notes"]

    def test_rewrite_basic(self, client: TestClient):
        """基本文案改写。"""
        resp = client.post(
            "/api/v1/copywriting/rewrite",
            json={
                "source_text": "这是一段测试文案，用于验证文案改写功能是否正常工作。",
                "target_length": 100,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "succeeded"
        assert data["result_text"] is not None
        assert len(data["result_text"]) > 0
        assert data["provider_name"] == "sandbox_copywriting"
        assert data["is_mock"] is True
        assert data["compliance_status"] == "passed"

    def test_rewrite_accepts_target_length_and_customer_skill(self, client: TestClient):
        resp = client.post(
            "/api/v1/copywriting/rewrite",
            json={
                "source_text": "一段需要按客户规则改写的原文。",
                "target_length": 150,
                "skill_prompt": "先讲结论，再给三个步骤。",
            },
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        detail = client.get(f"/api/v1/copywriting/{task_id}")
        assert detail.status_code == 200
        assert detail.json()["target_length"] == 150
        assert detail.json()["skill_prompt"] == "先讲结论，再给三个步骤。"

    def test_rewrite_with_variants(self, client: TestClient):
        """请求多个变体。"""
        resp = client.post(
            "/api/v1/copywriting/rewrite",
            json={
                "source_text": "测试变体生成功能。",
                "variant_count": 3,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["result_variants"]) == 3

    def test_rewrite_empty_text_rejected(self, client: TestClient):
        """空文案应被拒绝。"""
        resp = client.post(
            "/api/v1/copywriting/rewrite",
            json={"source_text": ""},
        )
        assert resp.status_code == 422  # Pydantic validation: min_length=1

    def test_rewrite_capabilities(self, client: TestClient):
        """获取文案改写引擎能力。"""
        resp = client.get("/api/v1/copywriting/capabilities")
        assert resp.status_code == 200

    def test_history_lists_independent_copywriting_and_detail(self, client: TestClient):
        repository = MockRepository(candidates=[], tasks=[])
        service = CopywritingService(repository, SandboxCopywritingEngine())
        independent = service.generate(
            content_brief="介绍 AI 短视频获客系统",
            platform="douyin",
            target_audience="企业主",
            selling_points="降低内容制作成本",
            call_to_action="私信领取方案",
            target_length=100,
        )
        voiceover = service.rewrite(
            source_text="已确认的转写成稿",
            source_task_id="transcription-history-test",
            source_revision_id="revision-1",
            target_length=100,
        )
        assert isinstance(repository.get_task(voiceover.task_id), CopywritingTask)

        app.dependency_overrides[backend_deps.get_copywriting_service] = lambda: service
        try:
            list_resp = client.get("/api/v1/copywriting")
            assert list_resp.status_code == 200
            items = list_resp.json()
            assert [item["task_id"] for item in items] == [independent.task_id]

            detail_resp = client.get(f"/api/v1/copywriting/{independent.task_id}")
            assert detail_resp.status_code == 200
            detail = detail_resp.json()
            assert detail["content_brief"] == "介绍 AI 短视频获客系统"
            assert detail["result_text"]

            hidden_resp = client.get(f"/api/v1/copywriting/{voiceover.task_id}")
            assert hidden_resp.status_code == 404
        finally:
            app.dependency_overrides.pop(backend_deps.get_copywriting_service, None)

    def test_clear_history_deletes_only_independent_copywriting(
        self, client: TestClient
    ):
        repository = MockRepository(candidates=[], tasks=[])
        service = CopywritingService(repository, SandboxCopywritingEngine())
        independent = service.generate(
            content_brief="介绍 AI 短视频获客系统",
            platform="douyin",
            target_length=100,
        )
        voiceover = service.rewrite(
            source_text="已确认的转写成稿",
            source_task_id="transcription-history-test",
            source_revision_id="revision-1",
            target_length=100,
        )

        app.dependency_overrides[backend_deps.get_copywriting_service] = lambda: service
        try:
            resp = client.delete("/api/v1/copywriting/history")
            assert resp.status_code == 200
            assert resp.json() == {"deleted_count": 1}
            assert repository.get_task(independent.task_id) is None
            assert repository.get_task(voiceover.task_id) is not None
        finally:
            app.dependency_overrides.pop(backend_deps.get_copywriting_service, None)


class TestCopywritingProductionConfig:
    def test_missing_key_disables_capabilities(
        self,
        monkeypatch: pytest.MonkeyPatch,
        client: TestClient,
    ):
        backend_deps.get_copywriting_engine.cache_clear()
        backend_deps.get_copywriting_service.cache_clear()
        monkeypatch.setattr(
            backend_deps,
            "COPYWRITING_MODE",
            CopywritingProviderMode.PRODUCTION,
        )
        monkeypatch.setattr(backend_deps, "COPYWRITING_API_KEY", "")
        monkeypatch.setattr(backend_deps, "COPYWRITING_MODEL", "deepseek-v4-flash")
        service_dependency = backend_deps.get_copywriting_service
        previous_service = app.dependency_overrides.get(service_dependency)
        app.dependency_overrides[service_dependency] = lambda: CopywritingService(
            MockRepository(candidates=[], tasks=[]),
            backend_deps.get_copywriting_engine(),
        )

        try:
            resp = client.get("/api/v1/copywriting/capabilities")
            assert resp.status_code == 200
            data = resp.json()
            assert data["enabled"] is False
            assert data["mode"] == "disabled"
            assert "COPYWRITING_API_KEY" in data["missing_configuration"]

            create_resp = client.post(
                "/api/v1/copywriting/generate",
                json={"content_brief": "未配置 Key 测试"},
            )
            assert create_resp.status_code == 200
            task = create_resp.json()
            # 未配置 Key 时模型一次都没有产出。此前这里断言 succeeded 且
            # result_text 等于用户输入的概要，等于把输入当成生成结果返回，
            # 界面上表现为"接口 200，但内容和原文一模一样"。必须如实报失败。
            assert task["status"] == "failed"
            assert task["result_text"] is None
            assert task["compliance_status"] == "model_unavailable"
            assert task["charged_credits"] == 0
            assert "没有返回可用的新内容" in task["error_message"]
            assert task["is_mock"] is False
        finally:
            if previous_service is None:
                app.dependency_overrides.pop(service_dependency, None)
            else:
                app.dependency_overrides[service_dependency] = previous_service
            backend_deps.get_copywriting_engine.cache_clear()
            backend_deps.get_copywriting_service.cache_clear()


# ---------------------------------------------------------------------------
# /api/v1/video-editor  —— 视频剪辑
# ---------------------------------------------------------------------------


class TestVideoEditor:
    def test_edit_video_nonexistent_file(self, client: TestClient):
        """源视频不存在时应返回 400。"""
        resp = client.post(
            "/api/v1/video-editor/edit",
            json={"source_video_path": "/nonexistent/video.mp4"},
        )
        assert resp.status_code == 400

    def test_capabilities(self, client: TestClient):
        """获取视频编辑器能力。"""
        resp = client.get("/api/v1/video-editor/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert "provider_name" in data
        assert "enabled" in data


# ---------------------------------------------------------------------------
# /api/v1/templates 与 /api/v1/subtitles
# ---------------------------------------------------------------------------


class TestEditingTemplatesAndSubtitles:
    def test_template_list_serializes_step_definitions(self, client: TestClient):
        resp = client.get("/api/v1/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 5
        assert isinstance(data["items"][0]["steps"][0], dict)
        assert "kind" in data["items"][0]["steps"][0]

    def test_subtitle_status_reports_cloud_engine_contract(self, client: TestClient):
        resp = client.get("/api/v1/subtitles/status")
        assert resp.status_code == 200
        data = resp.json()
        if data["asr_mode"] == "cloud":
            assert data["provider_name"] == "阿里云 Fun-ASR"
            assert data["default_model"] == "fun-asr"
            assert data["supported_models"] == ["fun-asr"]
        else:
            assert data["provider_name"] == "faster-whisper"
            assert data["default_model"] == "large-v3-turbo"
            assert data["supported_models"] == ["large-v3-turbo", "base"]
        assert data["whisper_available"] is True
        assert data["supported_formats"] == ["srt", "ass"]


# ---------------------------------------------------------------------------
# /api/v1/publish  —— 发布
# ---------------------------------------------------------------------------


class TestPublish:
    def install_publish_service_override(self):
        service = PublishService(
            MockRepository(),
            {
                "douyin": SandboxPublisher(PublishPlatform.DOUYIN),
                "kuaishou": SandboxPublisher(PublishPlatform.KUAISHOU),
                "wechat_channels": SandboxPublisher(PublishPlatform.WECHAT_CHANNELS),
                "xiaohongshu": SandboxPublisher(PublishPlatform.XIAOHONGSHU),
            },
        )
        app.dependency_overrides[backend_deps.get_publish_service] = lambda: service
        return service

    def test_list_platforms(self, client: TestClient):
        """列出可用发布平台。"""
        self.install_publish_service_override()
        try:
            resp = client.get("/api/v1/publish/platforms")
        finally:
            app.dependency_overrides.pop(backend_deps.get_publish_service, None)
        assert resp.status_code == 200
        data = resp.json()
        assert "platforms" in data
        platforms = data["platforms"]
        assert len(platforms) >= 3  # douyin, kuaishou, wechat_channels
        assert {"platform", "mode", "enabled"} <= set(platforms[0])

    def test_completed_local_editor_output_is_available_to_publish(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        now = __import__("datetime").datetime.now().astimezone()
        result_path = tmp_path / "editor-result.mp4"
        result_path.write_bytes(b"\x00\x00\x00\x18ftypmp42editor-result")
        repository = MockRepository()
        repository.save_task(
            VideoEditTask(
                task_id="edit-local-publish-1",
                title="本机导出 · 测试成片",
                status=TaskStatus.SUCCEEDED,
                progress=100,
                created_at=now,
                updated_at=now,
                source_video_path=str(tmp_path / "source.mp4"),
                edit_config=VideoEditConfig(),
                result_path=str(result_path),
                result_mime="video/mp4",
                result_size_bytes=result_path.stat().st_size,
                is_mock=False,
                outputs={
                    "workflow": "local_preview_export",
                    "publish_title": "剪辑页真实成片",
                },
            )
        )
        publish_assets = tmp_path / "publish-assets"
        monkeypatch.setattr(publish_api, "PUBLISH_ASSET_DIR", publish_assets)
        app.dependency_overrides[backend_deps.get_repository] = lambda: repository
        try:
            listed = client.get("/api/v1/publish/assets")
            assert listed.status_code == 200
            data = listed.json()
            assert data["total"] == 1
            assert data["items"][0]["path"] == str(result_path.resolve())
            assert data["items"][0]["recommended_title"] == "剪辑页真实成片"
            assert data["items"][0]["media_url"] == (
                "/api/v1/video-editor/jobs/edit-local-publish-1/media"
            )

            imported = client.post(
                "/api/v1/publish/assets/from-edit/edit-local-publish-1"
            )
            assert imported.status_code == 200
            assert (publish_assets / "ai-edit-edit-local-publish-1.mp4").is_file()

            relisted = client.get("/api/v1/publish/assets")
            assert relisted.status_code == 200
            assert relisted.json()["total"] == 1
        finally:
            app.dependency_overrides.pop(backend_deps.get_repository, None)

    def test_completed_avatar_output_is_available_to_publish(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        now = __import__("datetime").datetime.now().astimezone()
        result_path = tmp_path / "avatar-result.mp4"
        result_path.write_bytes(b"\x00\x00\x00\x18ftypmp42avatar-result")
        repository = MockRepository()
        repository.save_task(
            AvatarTask(
                task_id="avatar-publish-1",
                title="机器人也失业，如今到底谁输谁赢？",
                status=TaskStatus.SUCCEEDED,
                progress=100,
                created_at=now,
                updated_at=now,
                script_text="你发现没，机器人最近也被裁员了。",
                avatar_id="avatar-1",
                avatar_name="测试数字人",
                voice_id="voice-1",
                voice_name="测试声音",
                rights_holder="测试用户",
                rights_confirmed_at=now,
                idempotency_key="avatar-publish-1",
                provider_name="local",
                provider_status=AvatarProviderStatus.SUCCEEDED,
                result_path=str(result_path),
                result_mime="video/mp4",
                result_size_bytes=result_path.stat().st_size,
                is_mock=False,
            )
        )
        monkeypatch.setattr(
            publish_api, "PUBLISH_ASSET_DIR", tmp_path / "publish-assets"
        )
        app.dependency_overrides[backend_deps.get_repository] = lambda: repository
        try:
            listed = client.get("/api/v1/publish/assets")
            assert listed.status_code == 200
            data = listed.json()
            assert data["total"] == 1
            assert data["items"][0]["path"] == str(result_path.resolve())
            assert (
                data["items"][0]["recommended_title"]
                == "机器人也失业，如今到底谁输谁赢？"
            )
            assert data["items"][0]["source_text"] == "你发现没，机器人最近也被裁员了。"
            assert data["items"][0]["media_url"] == (
                "/api/v1/video-editor/sources/avatar%3Aavatar-publish-1/media"
            )
        finally:
            app.dependency_overrides.pop(backend_deps.get_repository, None)

    def test_publish_invalid_platform(self, client: TestClient):
        """无效平台应返回 400。"""
        resp = client.post(
            "/api/v1/publish",
            json={
                "video_path": "/some/video.mp4",
                "platform": "invalid_platform_xyz",
                "title": "测试",
            },
        )
        assert resp.status_code == 400

    def test_preflight_batch_and_manual_result(self, client: TestClient, tmp_path):
        """发布批次可预检、创建并人工回填结果。"""
        self.install_publish_service_override()
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"video")
        payload = {
            "video_path": str(video_path),
            "platforms": ["douyin", "kuaishou"],
            "title": "测试发布任务",
            "description": "测试描述",
            "tags": ["AI"],
        }
        try:
            preflight_resp = client.post("/api/v1/publish/preflight", json=payload)
            assert preflight_resp.status_code == 200
            preflight = preflight_resp.json()
            assert preflight["blocked"] is False
            assert len(preflight["platforms"]) == 2

            batch_resp = client.post(
                "/api/v1/publish/batches",
                json={**payload, "confirmation_accepted": True},
            )
            assert batch_resp.status_code == 200
            batch = batch_resp.json()
            assert batch["status"] == "manual_ready"
            assert batch["total"] == 2
            task_id = batch["tasks"][0]["task_id"]
            assert batch["tasks"][0]["publish_status"] == "pending"
            assert batch["tasks"][0]["native_music_mode"] == "auto_recommended"

            manual_resp = client.post(
                f"/api/v1/publish/tasks/{task_id}/manual-result",
                json={
                    "succeeded": True,
                    "platform_url": "https://example.com/published/1",
                    "note": "已在平台后台确认",
                },
            )
            assert manual_resp.status_code == 200
            manual = manual_resp.json()
            assert manual["status"] == "succeeded"
            assert manual["publish_status"] == "succeeded"
            assert manual["platform_url"] == "https://example.com/published/1"
        finally:
            app.dependency_overrides.pop(backend_deps.get_publish_service, None)

    def test_resume_paused_publish_task(self, client: TestClient):
        service = self.install_publish_service_override()
        try:
            batch = service.create_batch(
                video_path="/some/video.mp4",
                targets=[
                    PublishTarget(platform=PublishPlatform.DOUYIN, title="验证后继续")
                ],
            )
            task = batch["tasks"][0].model_copy(
                update={
                    "status": TaskStatus.PAUSED,
                    "publish_status": PublishStatus.ACTION_REQUIRED,
                }
            )
            service.repository.save_task(task)
            resp = client.post(f"/api/v1/publish/tasks/{task.task_id}/resume")
            assert resp.status_code == 200
            assert resp.json()["status"] == "queued"
            assert resp.json()["publish_status"] == "pending"
        finally:
            app.dependency_overrides.pop(backend_deps.get_publish_service, None)

    def test_confirm_auto_publish_requires_explicit_task_confirmation(
        self, client: TestClient
    ):
        service = PublishService(
            MockRepository(),
            {"douyin": DouyinBrowserPublisher()},
        )
        app.dependency_overrides[backend_deps.get_publish_service] = lambda: service
        try:
            batch = service.create_batch(
                video_path="/some/video.mp4",
                targets=[
                    PublishTarget(
                        platform=PublishPlatform.DOUYIN,
                        account_id="pubacc-test",
                        title="本次授权",
                    )
                ],
            )
            task = batch["tasks"][0].model_copy(
                update={
                    "status": TaskStatus.PAUSED,
                    "publish_status": PublishStatus.MANUAL_READY,
                    "stage": "已在账号“测试号”的官方页面选择视频并填写内容",
                }
            )
            service.repository.save_task(task)

            rejected = client.post(
                f"/api/v1/publish/tasks/{task.task_id}/confirm-auto-publish",
                json={"confirmation_accepted": False},
            )
            assert rejected.status_code == 400

            accepted = client.post(
                f"/api/v1/publish/tasks/{task.task_id}/confirm-auto-publish",
                json={"confirmation_accepted": True},
            )
            assert accepted.status_code == 200
            stored = service.get_task(task.task_id)
            assert stored is not None
            assert stored.target.auto_publish_authorized is True
            assert stored.target.use_prepared_page is True
        finally:
            app.dependency_overrides.pop(backend_deps.get_publish_service, None)

    def test_delete_publish_tasks(self, client: TestClient):
        service = self.install_publish_service_override()
        try:
            first = service.publish(
                video_path="/some/video.mp4",
                target=PublishTarget(platform=PublishPlatform.DOUYIN, title="删除一条"),
            )
            single = client.delete(f"/api/v1/publish/tasks/{first.task_id}")
            assert single.status_code == 200
            assert single.json() == {"task_id": first.task_id, "deleted": True}

            second = service.publish(
                video_path="/some/video.mp4",
                target=PublishTarget(
                    platform=PublishPlatform.DOUYIN, title="批量删除 A"
                ),
            )
            third = service.publish(
                video_path="/some/video.mp4",
                target=PublishTarget(
                    platform=PublishPlatform.KUAISHOU, title="批量删除 B"
                ),
            )
            batch = client.post(
                "/api/v1/publish/tasks/delete-batch",
                json={"task_ids": [second.task_id, third.task_id]},
            )
            assert batch.status_code == 200
            assert batch.json()["deleted_task_ids"] == [second.task_id, third.task_id]
        finally:
            app.dependency_overrides.pop(backend_deps.get_publish_service, None)

    def test_generate_publish_metadata_from_source_copy(self, client: TestClient):
        service = CopywritingService(MockRepository(), SandboxCopywritingEngine())
        app.dependency_overrides[backend_deps.get_copywriting_service] = lambda: service
        try:
            resp = client.post(
                "/api/v1/copywriting/publish-metadata",
                json={
                    "source_text": "新品活动，欢迎了解",
                    "platforms": ["douyin"],
                    "source_task_id": "copy-source-1",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["is_mock"] is True
            assert data["title"].startswith("【演示】")
            assert data["description"].startswith("【演示结果】")
            assert data["platforms"]["douyin"]["title"] == data["title"]
            task = service.get_task(data["task_id"])
            assert task is not None
            assert task.source_task_id == "copy-source-1"
        finally:
            app.dependency_overrides.pop(backend_deps.get_copywriting_service, None)

    def test_generate_publish_metadata_rejects_engine_input_over_limit(
        self, client: TestClient
    ):
        service = CopywritingService(MockRepository(), SandboxCopywritingEngine())
        app.dependency_overrides[backend_deps.get_copywriting_service] = lambda: service
        try:
            resp = client.post(
                "/api/v1/copywriting/publish-metadata",
                json={"source_text": "文" * 5001, "platforms": ["douyin"]},
            )
            assert resp.status_code == 400
            assert "超过最大长度限制" in resp.json()["message"]
        finally:
            app.dependency_overrides.pop(backend_deps.get_copywriting_service, None)

    def test_preflight_blocks_a_missing_local_browser_account(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        service = PublishService(
            MockRepository(),
            {"douyin": DouyinBrowserPublisher()},
        )

        class MissingAccountManager:
            @staticmethod
            def status(_account_id: str):
                raise PublishAccountError("发布账号不存在或不属于该平台。")

        app.dependency_overrides[backend_deps.get_publish_service] = lambda: service
        monkeypatch.setattr(
            publish_api, "publish_account_manager", MissingAccountManager()
        )
        payload = {
            "video_path": "/some/video.mp4",
            "platforms": ["douyin"],
            "title": "测试发布任务",
            "account_ids": {"douyin": "pubacc-stale"},
        }
        try:
            preflight = client.post("/api/v1/publish/preflight", json=payload)
            assert preflight.status_code == 200
            platform = preflight.json()["platforms"][0]
            assert preflight.json()["blocked"] is True
            assert platform["issue_code"] == "account_missing"
            assert platform["account_status"] == "missing"

            batch = client.post(
                "/api/v1/publish/batches",
                json={**payload, "confirmation_accepted": True},
            )
            assert batch.status_code == 400
        finally:
            app.dependency_overrides.pop(backend_deps.get_publish_service, None)

    def test_create_batch_requires_confirmation(self, client: TestClient):
        self.install_publish_service_override()
        try:
            resp = client.post(
                "/api/v1/publish/batches",
                json={
                    "video_path": "/some/video.mp4",
                    "platforms": ["douyin"],
                    "title": "未确认",
                    "confirmation_accepted": False,
                },
            )
        finally:
            app.dependency_overrides.pop(backend_deps.get_publish_service, None)
        assert resp.status_code == 400

    def test_confirmed_batch_authorizes_automatic_platforms_with_distinct_content(self):
        body = publish_api.PublishBatchRequest(
            video_path="/some/video.mp4",
            platforms=["douyin", "kuaishou", "xiaohongshu"],
            title="自动发布边界",
            platform_contents={
                "douyin": {
                    "title": "抖音标题",
                    "description": "抖音正文",
                    "tags": ["抖音"],
                },
                "kuaishou": {
                    "title": "快手标题",
                    "description": "快手正文",
                    "tags": ["快手"],
                },
                "xiaohongshu": {
                    "title": "小红书标题",
                    "description": "小红书正文",
                    "tags": ["小红书"],
                },
            },
            native_music_mode="auto_recommended",
            native_music_hint="商业表达 平稳",
            confirmation_accepted=True,
        )

        targets = publish_api._build_targets(body)

        assert targets[0].platform == PublishPlatform.DOUYIN
        assert targets[0].auto_publish_authorized is True
        assert targets[0].title == "抖音标题"
        assert targets[0].native_music_mode == "auto_recommended"
        assert targets[0].native_music_hint == "商业表达 平稳"
        assert targets[1].auto_publish_authorized is True
        assert targets[1].title == "快手标题"
        assert targets[1].description == "快手正文"
        assert targets[1].native_music_mode == "off"
        assert targets[2].auto_publish_authorized is False

    def test_publish_config_can_be_saved_to_root_env(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        env_path = tmp_path / ".env"
        env_path.write_text("COPYWRITING_MODEL=keep-me\n", encoding="utf-8")
        monkeypatch.setattr(backend_config, "ENV_PATH", env_path)
        for key in (
            "PUBLISH_DOUYIN_MODE",
            "PUBLISH_DOUYIN_ACCESS_TOKEN",
            "PUBLISH_DOUYIN_OPEN_ID",
            "PUBLISH_DOUYIN_CLIENT_KEY",
            "PUBLISH_DOUYIN_CLIENT_SECRET",
        ):
            monkeypatch.delenv(key, raising=False)

        resp = client.put(
            "/api/v1/publish/config/douyin",
            json={
                "mode": "official",
                "access_token": "token-secret-value",
                "open_id": "open-id-1234",
                "client_key": "client-key-5678",
                "client_secret": "client-secret-value",
            },
        )
        assert resp.status_code == 200
        assert "token-secret-value" not in resp.text
        data = resp.json()
        assert data["mode"] == "official"
        token_status = next(
            item for item in data["variables"] if item["field"] == "access_token"
        )
        assert token_status["configured"] is True
        assert token_status["secret"] is True

        saved = env_path.read_text(encoding="utf-8")
        assert "COPYWRITING_MODEL=keep-me" in saved
        assert "PUBLISH_DOUYIN_MODE=official" in saved
        assert "PUBLISH_DOUYIN_ACCESS_TOKEN=token-secret-value" in saved

        get_resp = client.get("/api/v1/publish/config")
        assert get_resp.status_code == 200
        assert "token-secret-value" not in get_resp.text

    def test_douyin_connection_requires_admin_oauth_setup(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(backend_config, "ENV_PATH", tmp_path / ".env")
        for key in PUBLISH_DOUYIN_CONNECTION_KEYS:
            monkeypatch.delenv(key, raising=False)

        status = client.get("/api/v1/publish/connections/douyin")
        assert status.status_code == 200
        assert status.json()["state"] == "not_configured"
        assert "access_token" not in status.text

        start = client.post("/api/v1/publish/connections/douyin/start")
        assert start.status_code == 410
        assert "旧版抖音开发者平台授权已停用" in start.json()["message"]

    def test_douyin_connection_start_is_disabled_in_local_browser_mode(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        start = client.post("/api/v1/publish/connections/douyin/start")
        assert start.status_code == 410
        assert "打开官方扫码窗口" in start.json()["message"]


# ---------------------------------------------------------------------------
# /api/v1/analytics
# ---------------------------------------------------------------------------


class TestAnalytics:
    def test_summary_returns_stable_aggregation(self, client: TestClient):
        resp = client.get("/api/v1/analytics/summary?time_range=7d")
        assert resp.status_code == 200
        data = resp.json()
        assert "overview" in data
        assert "trends" in data
        assert "competitors" in data
        assert "contentDistribution" in data
        assert isinstance(data["trends"], list)
        assert isinstance(data["competitors"], list)

    def test_summary_accepts_empty_result_keyword(self, client: TestClient):
        resp = client.get(
            "/api/v1/analytics/summary?time_range=24h&keyword=no-such-keyword-xyz"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["overview"]["totalViews"] == 0
        assert data["trends"] == []


# ---------------------------------------------------------------------------
# 统一错误处理
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_404_returns_unified_format(self, client: TestClient):
        """404 应返回统一错误格式。"""
        resp = client.get("/api/v1/tasks/nonexistent")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"] is True
        assert data["code"] == 404
        assert "message" in data

    def test_422_returns_unified_format(self, client: TestClient):
        """参数校验失败应返回统一错误格式。"""
        resp = client.post(
            "/api/v1/candidates/search",
            json={"keyword": "", "limit": 0},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"] is True
        assert data["code"] == 422
        assert data["message"] == "请求参数校验失败"
        assert "details" in data

    def test_method_not_allowed(self, client: TestClient):
        """不允许的 HTTP 方法应返回统一错误格式。"""
        resp = client.delete("/health")
        assert resp.status_code == 405
        data = resp.json()
        assert data["error"] is True
        assert data["code"] == 405


# ---------------------------------------------------------------------------
# /api/v1/admin/status  —— 增强系统状态
# ---------------------------------------------------------------------------


class TestAdminStatusEnhanced:
    def test_status_includes_new_fields(self, client: TestClient):
        """系统状态应包含新增字段。"""
        resp = client.get("/api/v1/admin/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "pipeline_count" in data
        assert isinstance(data["pipeline_count"], int)
        assert "migration_version" in data
        assert "python_version" in data
        assert data["python_version"] is not None
        assert "platform_info" in data
        assert data["platform_info"] is not None


class TestEnvConfig:
    def test_env_file_loading_respects_process_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        local_env = tmp_path / ".env"
        local_env.write_text(
            "COPYWRITING_MODEL=from-local\nQUOTED_VALUE='abc def'\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(backend_config, "ENV_PATH", local_env)
        monkeypatch.delenv("COPYWRITING_MODEL", raising=False)
        monkeypatch.delenv("QUOTED_VALUE", raising=False)

        backend_config._load_env_files()

        assert backend_config._env("COPYWRITING_MODEL") == "from-local"
        assert backend_config._env("QUOTED_VALUE") == "abc def"

        monkeypatch.setenv("COPYWRITING_MODEL", "from-process")
        backend_config._load_env_files()
        assert backend_config._env("COPYWRITING_MODEL") == "from-process"

    def test_backend_env_file_is_loaded_as_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        root_env = tmp_path / ".env"
        backend_env = tmp_path / "backend.env"
        root_env.write_text("", encoding="utf-8")
        backend_env.write_text("AVATAR_PROVIDER_MODE=baidu_xiling\n", encoding="utf-8")

        monkeypatch.setattr(backend_config, "ENV_PATH", root_env)
        monkeypatch.setattr(backend_config, "BACKEND_ENV_PATH", backend_env)
        monkeypatch.delenv("AVATAR_PROVIDER_MODE", raising=False)

        backend_config._load_env_files()

        assert backend_config._env("AVATAR_PROVIDER_MODE") == "baidu_xiling"
