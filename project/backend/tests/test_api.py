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
from project.backend.app.api.v1 import publish as publish_api  # noqa: E402
from project.backend.app.core import deps as backend_deps  # noqa: E402
from project.backend.app.core.config import (  # noqa: E402
    CopywritingProviderMode,
    CrawlerProviderMode,
)
from src.adapters.licensed import SandboxLicensedSearchProvider  # noqa: E402
from src.adapters.oneapi import OneApiLicensedSearchProvider  # noqa: E402
from project.backend.app.core import config as backend_config  # noqa: E402
from src.models import (  # noqa: E402
    CopywritingTask,
    HotWordRecord,
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
    PipelineStepResult,
    Platform,
    PublishPlatform,
    PublishStatus,
    PublishTarget,
    SourceCapability,
    TaskStatus,
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
    return TestClient(app)


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

    def test_search_limit_zero(self, client: TestClient):
        resp = client.post(
            "/api/v1/candidates/search", json={"keyword": "", "limit": 0}
        )
        assert resp.status_code == 422  # Pydantic validation: ge=1


# ---------------------------------------------------------------------------
# /api/v1/transcriptions
# ---------------------------------------------------------------------------


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
        assert any(item["task_id"] == task_id for item in list_resp.json())

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
        repository.save_pipeline_run(PipelineRun(run_id="pipeline-clear-1", keyword="待清空 1"))
        repository.save_pipeline_run(PipelineRun(run_id="pipeline-clear-2", keyword="待清空 2"))
        app.dependency_overrides[backend_deps.get_repository] = lambda: repository
        try:
            deleted = client.delete("/api/v1/pipelines")
        finally:
            app.dependency_overrides.pop(backend_deps.get_repository, None)

        assert deleted.status_code == 200
        assert deleted.json() == {"deleted_count": 2}
        assert repository.list_pipeline_runs() == []


# ---------------------------------------------------------------------------
# /api/v1/tasks
# ---------------------------------------------------------------------------


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
        client.post(
            "/api/v1/transcriptions",
            json={"media_name": "task_fields.mp4", "rights_confirmed": True},
        )
        resp = client.get("/api/v1/tasks")
        items = resp.json()["items"]
        assert len(items) > 0
        item = items[0]
        assert "task_id" in item
        assert "kind" in item
        assert "title" in item
        assert "status" in item
        assert "progress" in item

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

        repository = MockRepository(candidates=[], tasks=[])
        provider = SandboxLicensedSearchProvider()
        service = CommercialSearchService(
            repository,
            SourceService(repository, HeatService()),
            KeywordTrendService(repository),
            provider,
            active_platforms=(Platform.DOUYIN,),
        )
        app.dependency_overrides[backend_deps.get_repository] = lambda: repository
        app.dependency_overrides[backend_deps.get_licensed_search_provider] = lambda: (
            provider
        )
        app.dependency_overrides[backend_deps.get_commercial_search_service] = lambda: (
            service
        )
        app.dependency_overrides[backend_deps.get_official_hot_billboard_adapter] = (
            lambda: FakeOfficialAdapter("douyin_hot_billboard")
        )
        app.dependency_overrides[backend_deps.get_official_hot_words_adapter] = lambda: (
            FakeOfficialAdapter("douyin_hot_words")
        )
        yield
        app.dependency_overrides.pop(backend_deps.get_repository, None)
        app.dependency_overrides.pop(
            backend_deps.get_licensed_search_provider,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_commercial_search_service,
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

    def test_capabilities(self, client: TestClient):
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

    def test_browser_discovery_capabilities_include_prerequisites(
        self, client: TestClient
    ):
        resp = client.get("/api/v1/crawler/browser-discovery/capabilities")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["missing_configuration"], list)
        assert data["browser_channel"] in {"chrome", "msedge"}

    def test_preview_only_douyin(self, client: TestClient):
        resp = client.post(
            "/api/v1/crawler/preview",
            json={
                "keyword": "二手车",
                "count_per_platform": 2,
                "force_refresh": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert [item["platform"] for item in data["platforms"]] == ["douyin"]
        assert data["ranking_mode"] == "keyword_hot"
        assert data["published_window_days"] == 0
        assert data["sampling_offsets_hours"] == [0]
        assert data["max_api_calls_per_platform"] == 1
        assert data["trend_tracking_enabled"] is False
        assert "estimated_total_cost_cny" in data
        assert all("estimated_api_calls" in item for item in data["platforms"])
        assert all("estimated_cost_cny" in item for item in data["platforms"])

    def test_create_and_read_persistent_batch(self, client: TestClient):
        keyword = f"露营{uuid4().hex[:6]}"
        create_resp = client.post(
            "/api/v1/crawler/batches",
            json={
                "keyword": keyword,
                "published_window_days": 1,
                "count_per_platform": 2,
                "force_refresh": True,
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["status"] in {"succeeded", "partial", "failed"}
        assert [run["platform"] for run in created["platform_runs"]] == ["douyin"]

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
        assert first_candidate["relevance_basis"] == "title_or_hashtag"
        assert "标题/话题包含" in first_candidate["relevance_reason"]
        assert len(first_candidate["trend_points"]) == 1
        assert first_candidate["trend_points"][0]["effective_interactions"] >= 0
        first_run = detail["platform_runs"][0]
        assert first_run["relevant_count"] == first_run["returned_count"]
        assert "irrelevant_count" in first_run
        assert first_run["relevance_rule_version"] == "title_or_hashtag_strict_v1"

        list_resp = client.get("/api/v1/crawler/batches")
        assert list_resp.status_code == 200
        assert any(item["batch_id"] == batch_id for item in list_resp.json()["items"])

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

            def monitor(self, **kwargs):
                assert kwargs["keyword"] == "数字人"
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
    pytestmark = pytest.mark.usefixtures("copywriting_sandbox")

    def test_capabilities(self, client: TestClient):
        resp = client.get("/api/v1/copywriting/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["mode"] == "sandbox"
        assert data["supported_platforms"] == [
            "douyin",
            "xiaohongshu",
            "wechat_channels",
        ]

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
        assert len(data["result_variants"]) == 1
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
        assert task["status"] == "failed"
        assert "COPYWRITING_API_KEY" in task["error_message"]
        assert task["is_mock"] is False

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

    def test_subtitle_status_reports_local_engine_contract(self, client: TestClient):
        resp = client.get("/api/v1/subtitles/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider_name"] == "faster-whisper"
        assert data["default_model"] == "large-v3-turbo"
        assert data["supported_formats"] == ["srt", "ass"]
        assert "ffmpeg_available" in data


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

    def test_preflight_batch_and_manual_result(self, client: TestClient):
        """发布批次可预检、创建并人工回填结果。"""
        self.install_publish_service_override()
        payload = {
            "video_path": "/some/video.mp4",
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
                targets=[PublishTarget(platform=PublishPlatform.DOUYIN, title="验证后继续")],
            )
            task = batch["tasks"][0].model_copy(
                update={"status": TaskStatus.PAUSED, "publish_status": PublishStatus.ACTION_REQUIRED}
            )
            service.repository.save_task(task)
            resp = client.post(f"/api/v1/publish/tasks/{task.task_id}/resume")
            assert resp.status_code == 200
            assert resp.json()["status"] == "queued"
            assert resp.json()["publish_status"] == "pending"
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
                target=PublishTarget(platform=PublishPlatform.DOUYIN, title="批量删除 A"),
            )
            third = service.publish(
                video_path="/some/video.mp4",
                target=PublishTarget(platform=PublishPlatform.KUAISHOU, title="批量删除 B"),
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
            task = service.get_task(data["task_id"])
            assert task is not None
            assert task.source_task_id == "copy-source-1"
        finally:
            app.dependency_overrides.pop(backend_deps.get_copywriting_service, None)

    def test_generate_publish_metadata_rejects_engine_input_over_limit(self, client: TestClient):
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
            json={"keyword": "", "limit": 999},
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
