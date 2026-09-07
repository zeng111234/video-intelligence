"""云端轻量剪辑 FastAPI 契约测试（不调用付费接口）。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from project.backend.app.api.v1 import video_editor as video_editor_api
from project.backend.app.main import app
from src.adapters.video_editor_cloud import build_cloud_providers
from src.repositories.mock import MockRepository
from src.services.video_editor_cloud import (
    CloudEditorConfiguration,
    CloudProviderMode,
)
from src.services.video_editor_workflow import VideoEditorWorkflowService


class _VideoEditingStub:
    def __init__(self, output_directory: Path):
        self.output_directory = output_directory


class _TranscriptionStub:
    def get_approved_revision(self, _task_id: str):
        return None


def _workflow(
    tmp_path: Path,
    *,
    configuration: CloudEditorConfiguration | None = None,
) -> tuple[VideoEditorWorkflowService, str]:
    config = configuration or CloudEditorConfiguration(
        provider_mode=CloudProviderMode.SANDBOX,
    )
    service = VideoEditorWorkflowService(
        MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
        cloud_configuration=config,
        cloud_providers=build_cloud_providers(config),
    )
    service._probe_media = lambda _path: {  # type: ignore[method-assign]
        "duration_seconds": 60.0,
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "orientation": "vertical",
        "has_audio": True,
        "size_bytes": 1024,
    }
    source = service.upload_source(
        file_name="authorized.mp4",
        media_type="video/mp4",
        media_bytes=b"test-video",
        rights_confirmed=True,
        rights_holder="测试公司",
    )
    return service, source["source_id"]


def test_cloud_api_preflight_create_review_and_publish_guard(tmp_path: Path):
    workflow, source_id = _workflow(tmp_path)
    debit_calls: list[dict] = []
    workflow._debit_credits = lambda *args, **kwargs: debit_calls.append(  # type: ignore[method-assign]
        {"args": args, "kwargs": kwargs}
    )
    app.dependency_overrides[video_editor_api.get_workflow_service] = (
        lambda: workflow
    )
    try:
        with TestClient(app) as client:
            preflight_response = client.post(
                "/api/v1/video-editor/preflight",
                json={
                    "source_id": source_id,
                    "output_profile": "720p",
                    "target_platform": "douyin",
                },
            )
            assert preflight_response.status_code == 200
            quote = preflight_response.json()
            assert quote["provider_mode"] == "sandbox"

            body = {
                "source_ids": [source_id],
                "target_platform": "douyin",
                "output_profile": "720p",
                "quote_id": quote["quote_id"],
                "billing_confirmation": {
                    "confirmed": True,
                    "max_cost_cny": float(quote["estimated_max"]),
                },
            }
            missing_key = client.post(
                "/api/v1/video-editor/batches",
                json=body,
            )
            assert missing_key.status_code == 400
            assert "Idempotency-Key" in json.dumps(
                missing_key.json(),
                ensure_ascii=False,
            )

            created_response = client.post(
                "/api/v1/video-editor/batches",
                headers={"Idempotency-Key": "api-cloud-create"},
                json=body,
            )
            assert created_response.status_code == 200
            created = created_response.json()
            assert debit_calls == []
            item = created["items"][0]
            assert item["status"] == "awaiting_subtitle_review"
            assert item["provider_payload"]["style_preset_id"] == (
                "talking-head-grammar-only-v1"
            )

            reviewed_response = client.post(
                (
                    f"/api/v1/video-editor/batches/{created['batch_id']}"
                    f"/items/{item['item_id']}/review"
                ),
                json={
                    "subtitle_segments": [
                        {
                            "start": 0,
                            "end": 2,
                            "text": "人工确认字幕",
                            "emphasis_terms": ["确认"],
                        }
                    ],
                    "enabled_plan_step_ids": item["edit_plan"]["enabled_steps"],
                    "selected_title": item["selected_title"],
                    "selected_bgm_id": None,
                    "confirmed": True,
                },
            )
            assert reviewed_response.status_code == 200
            reviewed = reviewed_response.json()
            assert reviewed["status"] == "configuration_required"
            assert reviewed["items"][0]["result_media_url"] is None
            assert reviewed["items"][0]["subtitle_segments"][0]["emphasis_terms"] == ["确认"]

            confirm_response = client.post(
                f"/api/v1/video-editor/batches/{created['batch_id']}/confirm-results",
                json={"item_ids": [item["item_id"]]},
            )
            assert confirm_response.status_code == 400
            assert "不能交接发布" in json.dumps(
                confirm_response.json(),
                ensure_ascii=False,
            )
    finally:
        app.dependency_overrides.pop(
            video_editor_api.get_workflow_service,
            None,
        )


def test_capabilities_expose_missing_production_configuration(tmp_path: Path):
    configuration = CloudEditorConfiguration(
        provider_mode=CloudProviderMode.ALIYUN,
    )
    workflow, _ = _workflow(tmp_path, configuration=configuration)
    app.dependency_overrides[video_editor_api.get_workflow_service] = (
        lambda: workflow
    )
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/video-editor/capabilities")
            assert response.status_code == 200
            capabilities = response.json()
            assert capabilities["provider_mode"] == "aliyun"
            assert capabilities["live_ready"] is False
            assert capabilities["is_mock"] is False
            assert "DASHSCOPE_API_KEY" in capabilities["missing_configuration"]
    finally:
        app.dependency_overrides.pop(
            video_editor_api.get_workflow_service,
            None,
        )


def test_capabilities_keep_local_renderer_when_cloud_probe_is_unavailable():
    class _Service:
        @staticmethod
        def capabilities():
            return {
                "provider_name": "ffmpeg_local",
                "enabled": False,
            }

    class _Workflow:
        @staticmethod
        def local_ffmpeg_capabilities():
            return {
                "provider_mode": "local_ffmpeg",
                "renderer_mode": "local_ffmpeg",
                "provider_name": "ffmpeg_local_audited",
                "enabled": True,
                "live_ready": True,
                "is_mock": False,
                "missing_configuration": [],
            }

        _cloud_configuration_override = None

        def cloud_capabilities(self):
            raise AssertionError("healthy local path must not probe the cloud")

    payload = video_editor_api.capabilities(
        service=_Service(),
        workflow=_Workflow(),
    )

    assert payload["provider_mode"] == "local_ffmpeg"
    assert payload["renderer_mode"] == "local_ffmpeg"
    assert payload["enabled"] is True
    assert payload["live_ready"] is True
    assert payload["cloud_backup"]["availability"] == "not_checked_local_default"
    assert payload["local_renderer"]["provider_name"] == "ffmpeg_local_audited"


def test_capabilities_classify_remote_probe_failure_without_failing_endpoint():
    class _Service:
        @staticmethod
        def capabilities():
            return {"provider_name": "sandbox_video_editor", "enabled": True}

    class _Workflow:
        _cloud_configuration_override = object()

        @staticmethod
        def local_ffmpeg_capabilities():
            return {
                "provider_mode": "local_ffmpeg",
                "renderer_mode": "local_ffmpeg",
                "enabled": False,
                "live_ready": False,
                "missing_configuration": ["本机受审媒体工具不可用"],
            }

        @staticmethod
        def cloud_capabilities():
            raise ConnectionError("WinError 10054")

    payload = video_editor_api.capabilities(
        service=_Service(),
        workflow=_Workflow(),
    )

    assert payload["provider_mode"] == "aliyun"
    assert payload["enabled"] is False
    assert payload["cloud_backup"]["availability"] == "control_plane_unavailable"
    assert payload["cloud_backup"]["missing_configuration"] == [
        "控制面能力暂不可用，请重新连接"
    ]


def test_brand_title_font_is_served_from_the_same_editor_api():
    with TestClient(app) as client:
        response = client.get("/api/v1/video-editor/brand-title-font")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("font/otf")
    assert len(response.content) > 100_000


def test_caption_font_is_served_from_the_same_editor_api():
    with TestClient(app) as client:
        response = client.get("/api/v1/video-editor/caption-font")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("font/ttf")
    assert len(response.content) > 100_000
