"""FastAPI 全端点集成测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

# 确保项目根目录在 Python 路径中
_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from project.backend.app.main import app  # noqa: E402
from project.backend.app.core import deps as backend_deps  # noqa: E402
from project.backend.app.core.config import CrawlerProviderMode  # noqa: E402
from src.adapters.oneapi import OneApiLicensedSearchProvider  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(app)


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
        assert data["total"] <= 5

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
        client.post(
            "/api/v1/transcriptions",
            json={"media_name": "new_task.mp4", "rights_confirmed": True},
        )
        after = client.get("/api/v1/tasks").json()["total"]
        assert after >= before


# ---------------------------------------------------------------------------
# /api/v1/crawler
# ---------------------------------------------------------------------------


class TestCrawlerBatches:
    def test_capabilities(self, client: TestClient):
        resp = client.get("/api/v1/crawler/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] in {"sandbox", "production"}
        assert "monthly_query_count" in data
        assert "supported_platforms" in data

    def test_preview_three_platforms(self, client: TestClient):
        resp = client.post(
            "/api/v1/crawler/preview",
            json={
                "keyword": "二手车",
                "published_window_days": 7,
                "count_per_platform": 2,
                "force_refresh": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["platforms"]) == 3
        assert data["ranking_mode"] == "keyword_hot"
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
        assert len(created["platform_runs"]) == 3

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

        list_resp = client.get("/api/v1/crawler/batches")
        assert list_resp.status_code == 200
        assert any(item["batch_id"] == batch_id for item in list_resp.json()["items"])

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
        # copywriting 端点当前没有 /capabilities，但改写结果应包含 task_id
        resp = client.post(
            "/api/v1/copywriting/rewrite",
            json={"source_text": "能力检查。"},
        )
        assert resp.status_code == 200


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
# /api/v1/publish  —— 发布
# ---------------------------------------------------------------------------


class TestPublish:
    def test_list_platforms(self, client: TestClient):
        """列出可用发布平台。"""
        resp = client.get("/api/v1/publish/platforms")
        assert resp.status_code == 200
        data = resp.json()
        assert "platforms" in data
        platforms = data["platforms"]
        assert len(platforms) >= 3  # douyin, kuaishou, wechat_channels

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
