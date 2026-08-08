"""云端轻量剪辑的报价、门禁、幂等与沙箱边界测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.adapters.video_editor_cloud import build_cloud_providers
from src.models import VideoEditorBatch, VideoEditorBatchItem
from src.repositories.mock import MockRepository
from src.repositories.sqlite import SQLiteRepository
from src.services.video_editor_cloud import (
    CloudEditorConfiguration,
    CloudProviderMode,
)
from src.services.video_editor_workflow import (
    VideoEditorWorkflowError,
    VideoEditorWorkflowService,
)


class _VideoEditingStub:
    def __init__(self, output_directory: Path):
        self.output_directory = output_directory


class _TranscriptionStub:
    def get_approved_revision(self, _task_id: str):
        return None


def _service(tmp_path: Path, repository=None) -> VideoEditorWorkflowService:
    configuration = CloudEditorConfiguration(
        provider_mode=CloudProviderMode.SANDBOX,
    )
    service = VideoEditorWorkflowService(
        repository or MockRepository(tasks=[]),
        _VideoEditingStub(tmp_path / "edits"),
        _TranscriptionStub(),
        None,
        cloud_configuration=configuration,
        cloud_providers=build_cloud_providers(configuration),
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
    return service


def _source(service: VideoEditorWorkflowService) -> str:
    uploaded = service.upload_source(
        file_name="authorized.mp4",
        media_type="video/mp4",
        media_bytes=b"not-a-real-video",
        rights_confirmed=True,
        rights_holder="测试公司",
    )
    return uploaded["source_id"]


def _quote(
    service: VideoEditorWorkflowService,
    source_id: str,
    output_profile: str = "720p",
) -> dict:
    return service.create_cloud_preflight(
        source_id=source_id,
        output_profile=output_profile,
        target_platform="douyin",
    )


def _create(
    service: VideoEditorWorkflowService,
    source_id: str,
    quote: dict,
    *,
    key: str = "cloud-create-key",
) -> dict:
    return service.create_cloud_batch(
        source_ids=[source_id],
        target_platform="douyin",
        output_profile=quote["output_profile"],
        quote_id=quote["quote_id"],
        billing_confirmation={
            "confirmed": True,
            "max_cost_cny": float(quote["estimated_max"]),
        },
        idempotency_key=key,
    )


def test_preflight_is_free_persisted_and_uses_profile_price(tmp_path: Path):
    service = _service(tmp_path)
    source_id = _source(service)

    quote_720 = _quote(service, source_id, "720p")
    quote_1080 = _quote(service, source_id, "1080p")

    assert quote_720["provider_mode"] == "sandbox"
    assert quote_720["is_mock"] is True
    assert quote_720["ttl_seconds"] == 900
    assert float(quote_720["estimated_total"]) == pytest.approx(0.048611)
    assert float(quote_1080["estimated_total"]) == pytest.approx(0.081869)
    assert service.repository.get_video_editor_quote(quote_720["quote_id"])


def test_cloud_create_requires_confirmation_and_sufficient_cap(tmp_path: Path):
    service = _service(tmp_path)
    source_id = _source(service)
    quote = _quote(service, source_id)

    with pytest.raises(VideoEditorWorkflowError, match="明确确认"):
        service.create_cloud_batch(
            source_ids=[source_id],
            target_platform="douyin",
            output_profile="720p",
            quote_id=quote["quote_id"],
            billing_confirmation={"confirmed": False, "max_cost_cny": 1},
            idempotency_key="not-confirmed",
        )

    with pytest.raises(VideoEditorWorkflowError, match="费用上限低于"):
        service.create_cloud_batch(
            source_ids=[source_id],
            target_platform="douyin",
            output_profile="720p",
            quote_id=quote["quote_id"],
            billing_confirmation={"confirmed": True, "max_cost_cny": 0},
            idempotency_key="too-low",
        )


def test_expired_quote_is_rejected(tmp_path: Path):
    service = _service(tmp_path)
    source_id = _source(service)
    quote = _quote(service, source_id)
    stored = service.repository.get_video_editor_quote(quote["quote_id"])
    assert stored is not None
    payload = dict(stored["payload"])
    payload["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    service.repository.save_video_editor_quote(
        quote_id=stored["quote_id"],
        source_id=stored["source_id"],
        output_profile=stored["output_profile"],
        target_platform=stored["target_platform"],
        expires_at=payload["expires_at"],
        created_at=stored["created_at"],
        payload=payload,
    )

    with pytest.raises(VideoEditorWorkflowError, match="已过期"):
        _create(service, source_id, payload, key="expired")


def test_sandbox_flow_is_idempotent_and_never_publishable(tmp_path: Path):
    service = _service(tmp_path)
    source_id = _source(service)
    quote = _quote(service, source_id, "720p")

    created = _create(service, source_id, quote)
    repeated = _create(service, source_id, quote)

    assert repeated["batch_id"] == created["batch_id"]
    assert created["output_profile"] == "720p"
    assert created["output_resolution"] == "720x1280"
    assert created["output_fps"] == 30
    assert created["output_bitrate"] == "2.5M"
    assert created["visual_spec"]["style_id"] == "business_talking_head_v8"
    assert created["visual_spec"]["canvas"]["pixel_aspect_ratio"] == "1:1"
    item = created["items"][0]
    assert item["status"] == "awaiting_subtitle_review"
    assert item["is_mock"] is True
    assert item["publish_allowed"] is False
    assert item["edit_plan"]["provider_name"] == "sandbox_edit_plan"

    reviewed = service.review_cloud_batch_item(
        created["batch_id"],
        item["item_id"],
        subtitle_segments=[
            {
                "start": 0,
                "end": 2,
                "text": "人工确认的演示字幕",
                "emphasis_terms": ["演示"],
            }
        ],
        enabled_plan_step_ids=item["edit_plan"]["enabled_steps"],
        selected_title=item["selected_title"],
        selected_bgm_id=None,
        confirmed=True,
    )
    reviewed_item = reviewed["items"][0]
    assert reviewed_item["review_snapshot"]["approval_mode"] == "manual"
    assert reviewed_item["subtitle_segments"][0]["emphasis_terms"] == ["演示"]
    assert reviewed_item["status"] == "configuration_required"
    assert reviewed_item["render_manifest"] == {
            "visual_style_id": "business_talking_head_v8",
        "subtitle_format": "ass",
        "title_render_mode": "png_watermark",
        "title_font": "Source Han Serif CN Heavy",
        "title_burned_in": True,
        "subtitles_burned_in": True,
        "expected_resolution": "720x1280",
        "rough_cut_burned_in": True,
        "source_kept_ranges": [{"start": 0.0, "end": 2.25}],
        "estimated_output_seconds": 2.25,
    }
    assert reviewed_item["result_media_url"] is None
    assert reviewed_item["publish_allowed"] is False

    with pytest.raises(VideoEditorWorkflowError, match="真实且可发布"):
        service.confirm_batch_results(
            created["batch_id"],
            [item["item_id"]],
        )


def test_cloud_flow_automatically_selects_bgm_from_transcript_and_title(tmp_path: Path):
    service = _service(tmp_path)
    service._probe_bgm_duration = lambda _path: 120.0  # type: ignore[method-assign]
    bgm = service.upload_bgm(
        file_name="Tech-Voiceover.mp3",
        media_type="audio/mpeg",
        media_bytes=b"authorized-music",
        mood="科技氛围",
        rights_confirmed=True,
        rights_holder="测试授权主体",
    )
    source_id = _source(service)
    quote = _quote(service, source_id)

    created = _create(service, source_id, quote, key="cloud-auto-bgm")
    item = created["items"][0]

    assert created["bgm_enabled"] is True
    assert item["selected_bgm_id"] == bgm["asset_id"]
    assert "bgm" in item["edit_plan"]["enabled_steps"]
    assert "AI 阅读转写文案与标题" in item["bgm_reason"]
    assert "《Tech-Voiceover》" in item["bgm_reason"]


def test_sqlite_restores_quotes_operations_jobs_and_batch(tmp_path: Path):
    database_path = tmp_path / "editor.db"
    first_repository = SQLiteRepository(database_path)
    # 测试用 SQLite 真实余额为 0，先充值以便走完整确认扣费流程
    first_repository.adjust_credit_balance(amount=Decimal("100"), reason="test-funding")
    first = _service(tmp_path, first_repository)
    source_id = _source(first)
    quote = _quote(first, source_id)
    created = _create(first, source_id, quote, key="sqlite-create")

    restored_repository = SQLiteRepository(database_path)
    restored = _service(tmp_path, restored_repository)

    assert restored_repository.get_video_editor_quote(quote["quote_id"])
    operation = restored_repository.get_video_editor_operation("sqlite-create")
    assert operation is not None
    assert operation["resource_id"] == created["batch_id"]
    assert restored_repository.list_video_editor_cloud_jobs(created["batch_id"])
    assert restored.get_batch(created["batch_id"])["batch_id"] == created["batch_id"]


def test_real_cloud_confirmation_materializes_publish_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    configuration = CloudEditorConfiguration(
        provider_mode=CloudProviderMode.ALIYUN,
        workspace_id="workspace",
        dashscope_api_key="test-key",
        oss_bucket="private-bucket",
        oss_location="oss-cn-beijing",
        aliyun_region="cn-beijing",
        access_key_id="test-access-key",
        access_key_secret="test-access-secret",
        mps_pipeline_id="pipeline",
        mps_template_id_720p="template-720",
        mps_template_id_1080p="template-1080",
    )
    service = _service(tmp_path)
    service._cloud_configuration_override = configuration
    service._cloud_providers_override = build_cloud_providers(configuration)
    source_id = _source(service)
    item = VideoEditorBatchItem(
        source_id=source_id,
        title="真实云成片",
        status="awaiting_output_confirmation",
        provider_stage="render_complete",
        selected_title="确认后的标题",
        provider_payload={
            "output_uri": "oss://private-bucket/video-editor/batch/output/720p.mp4"
        },
        is_mock=False,
        publish_allowed=True,
    )
    batch = VideoEditorBatch(
        provider_mode="aliyun",
        output_profile="720p",
        output_resolution="720x1280",
        output_fps=30,
        output_bitrate="2.5M",
        items=[item],
    )
    service.repository.save_video_editor_batch(batch)

    class _Response:
        headers = {"Content-Length": "16"}

        def __enter__(self):
            self._read = False
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size: int):
            if self._read:
                return b""
            self._read = True
            return b"\x00\x00\x00\x18ftypmp42"

    monkeypatch.setattr(
        "src.services.video_editor_workflow.urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(),
    )

    preview = service.get_batch(batch.batch_id)
    assert preview["items"][0]["result_media_url"].startswith("https://")
    confirmed = service.confirm_batch_results(batch.batch_id, [item.item_id])

    confirmed_item = confirmed["items"][0]
    assert confirmed_item["status"] == "ready_to_publish"
    assert confirmed_item["edit_task_id"]
    task = service.repository.get_task(confirmed_item["edit_task_id"])
    assert task is not None
    assert task.is_mock is False
    assert Path(task.result_path).is_file()


def test_real_cloud_output_can_download_before_publish_confirmation(tmp_path: Path):
    configuration = CloudEditorConfiguration(
        provider_mode=CloudProviderMode.ALIYUN,
        workspace_id="workspace",
        dashscope_api_key="test-key",
        oss_bucket="private-bucket",
        oss_location="oss-cn-beijing",
        aliyun_region="cn-beijing",
        access_key_id="test-access-key",
        access_key_secret="test-access-secret",
        mps_pipeline_id="pipeline",
        mps_template_id_720p="template-720",
        mps_template_id_1080p="template-1080",
    )
    service = _service(tmp_path)
    service._cloud_configuration_override = configuration
    service._cloud_providers_override = build_cloud_providers(configuration)
    item = VideoEditorBatchItem(
        source_id="source-1",
        title="不能用于文件名:测试",
        status="awaiting_output_confirmation",
        provider_stage="render_complete",
        selected_title="确认前也能下载:成片",
        provider_payload={
            "output_uri": "oss://private-bucket/video-editor/batch/output/720p.mp4"
        },
        is_mock=False,
        publish_allowed=True,
    )
    batch = VideoEditorBatch(
        provider_mode="aliyun",
        output_profile="720p",
        items=[item],
    )
    service.repository.save_video_editor_batch(batch)

    download = service.prepare_batch_item_download(batch.batch_id, item.item_id)

    assert download["media_url"].startswith("https://private-bucket.")
    assert download["filename"] == "确认前也能下载成片.mp4"
    stored = service.repository.get_video_editor_batch(batch.batch_id)
    assert stored is not None
    assert stored.items[0].status == "awaiting_output_confirmation"
