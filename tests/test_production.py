"""IP 配方与批量生产计划的持久化及状态边界测试。"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from project.backend.app.core import deps as backend_deps
from project.backend.app.main import app
from src.adapters.llm import SandboxCopywritingEngine
from src.models import (
    AvatarAsset,
    AvatarAssetKind,
    AvatarTask,
    CopywritingTask,
    DataSource,
    HeatLevel,
    HeatResult,
    PipelineRunStatus,
    PipelineStage,
    Platform,
    PublishStatus,
    ProductionBatchItemStatus,
    ProductionBatchStatus,
    TaskStatus,
    TranscriptSegment,
    TranscriptionTask,
    VideoCandidate,
    VideoEditTask,
    VideoMetricSnapshot,
)
from src.repositories.mock import MockRepository
from src.repositories.sqlite import SQLiteRepository
from src.services.pipeline import PipelineService
from src.services.pipeline_worker import PipelineWorker
from src.services.copywriting import CopywritingService
from src.services.publish_metadata import publish_draft_fingerprint
from src.services.production import (
    BUNDLED_DEFAULT_AVATAR_ID,
    BUNDLED_DEFAULT_PROFILE_NAME,
    BUNDLED_DEFAULT_VOICE_ID,
    DEFAULT_PRODUCTION_TEMPLATE_ID,
    ProductionService,
)
from tests.conftest import TEST_API_HEADERS


def _candidate(candidate_id: str = "candidate-production-1") -> VideoCandidate:
    now = datetime.now().astimezone()
    return VideoCandidate(
        video_id=candidate_id,
        platform_item_id=candidate_id,
        title="批量生产候选",
        author_id="author-1",
        author_name="测试作者",
        platform=Platform.DOUYIN,
        category="企业服务",
        published_at=now,
        source_type=DataSource.LICENSED_PROVIDER,
        metrics=VideoMetricSnapshot(item_id=candidate_id, sampled_at=now, likes=10, confidence=0.8),
        heat=HeatResult(score=50, level=HeatLevel.INSUFFICIENT, confidence=0.5),
    )


def test_profile_uses_universal_template_when_customer_does_not_choose(tmp_path):
    service = ProductionService(MockRepository(), tmp_path / "production")

    profile = service.create_profile(
        name="自动优化出镜人",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
    )

    assert profile.edit_template_id == DEFAULT_PRODUCTION_TEMPLATE_ID
    assert service.list_profiles()[0].edit_template_id == DEFAULT_PRODUCTION_TEMPLATE_ID


def test_first_start_bootstraps_bundled_dashu_profile_without_accepting_rights(
    tmp_path,
):
    service = ProductionService(
        MockRepository(),
        tmp_path / "production",
        bootstrap_bundled_default_profile=True,
    )

    profiles = service.list_profiles()

    assert len(profiles) == 1
    assert profiles[0].name == BUNDLED_DEFAULT_PROFILE_NAME
    assert profiles[0].avatar_id == BUNDLED_DEFAULT_AVATAR_ID
    assert profiles[0].voice_id == BUNDLED_DEFAULT_VOICE_ID
    assert profiles[0].edit_template_id == DEFAULT_PRODUCTION_TEMPLATE_ID
    assert service.get_workspace_configuration() is None


def test_existing_empty_profile_file_is_never_replaced_by_bundled_default(tmp_path):
    storage = tmp_path / "production"
    storage.mkdir()
    (storage / "profiles.json").write_text("[]", encoding="utf-8")

    service = ProductionService(
        MockRepository(),
        storage,
        bootstrap_bundled_default_profile=True,
    )

    assert service.list_profiles() == []


def test_bundled_profile_is_not_created_outside_formal_desktop_mode(tmp_path):
    service = ProductionService(
        MockRepository(),
        tmp_path / "production",
    )

    assert service.list_profiles() == []


def test_profile_and_batch_plan_persist_without_executing_generation(tmp_path):
    repository = MockRepository()
    candidate = _candidate()
    repository.save_candidate(candidate)
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(repository, tmp_path / "production")

    profile = service.create_profile(
        name="创始人专业口播",
        target_audience="企业老板",
        script_style="清晰、克制、避免效果承诺",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
        edit_template_id="template-professional",
        tags=["B2B", "获客"],
    )
    batch = service.create_batch(
        name="本周候选",
        profile_id=profile.profile_id,
        candidate_ids=[candidate.video_id, candidate.video_id],
        pipeline_service=pipeline_service,
    )

    assert len(batch.items) == 1
    run = repository.get_pipeline_run(batch.items[0].run_id)
    assert run is not None
    assert run.status.value == "pending"
    assert run.candidate_video_id == candidate.video_id
    assert run.config["source"] == "production_batch_plan"
    assert run.config["profile"]["avatar_id"] == "avatar-owner"
    assert run.stages == []
    assert run.events[-1].action == "batch_plan_created"

    reloaded = ProductionService(repository, tmp_path / "production")
    assert reloaded.list_profiles()[0].profile_id == profile.profile_id
    assert reloaded.list_batches()[0].batch_id == batch.batch_id
    assert reloaded.batch_progress(batch) == {
        "total": 1,
        "pending": 1,
        "running": 0,
        "paused": 0,
        "succeeded": 0,
        "failed": 0,
    }


def test_production_api_creates_profile_and_pending_batch(tmp_path):
    repository = MockRepository()
    candidate = _candidate("candidate-production-api")
    repository.save_candidate(candidate)
    production_service = ProductionService(repository, tmp_path / "production")
    pipeline_service = PipelineService(repository, None, None, None, None)
    app.dependency_overrides[backend_deps.get_production_service] = lambda: production_service
    app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: pipeline_service
    try:
        with TestClient(app, headers=TEST_API_HEADERS) as client:
            profile_response = client.post(
                "/api/v1/production/profiles",
                json={"name": "API IP 配方", "tags": ["测试"]},
            )
            assert profile_response.status_code == 201, profile_response.text
            profile = profile_response.json()

            batch_response = client.post(
                "/api/v1/production/batches",
                headers={"Idempotency-Key": "create-pending-batch"},
                json={
                    "name": "API 批次",
                    "profile_id": profile["profile_id"],
                    "candidate_ids": [candidate.video_id],
                },
            )
    finally:
        app.dependency_overrides.pop(backend_deps.get_production_service, None)
        app.dependency_overrides.pop(backend_deps.get_pipeline_service, None)

    assert batch_response.status_code == 201, batch_response.text
    batch = batch_response.json()
    assert batch["progress"]["pending"] == 1
    assert batch["items"][0]["status"] == "pending"


def test_mixed_source_batch_keeps_each_source_in_one_persistent_queue(tmp_path):
    repository = MockRepository()
    candidate = _candidate("candidate-mixed")
    repository.save_candidate(candidate)
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(repository, tmp_path / "production")
    profile = service.create_profile(name="混合来源配方")

    batch = service.create_batch(
        name="混合批次",
        profile_id=profile.profile_id,
        candidate_ids=[candidate.video_id],
        source_items=[
            {"source_type": "share_link", "source_value": "https://example.com/share/1"},
            {"source_type": "brief", "source_value": "面向企业老板讲解 AI 获客"},
            {"source_type": "script", "source_value": "这是一条已写好的完整口播稿。"},
            {"source_type": "brief", "source_value": "面向企业老板讲解 AI 获客"},
        ],
        pipeline_service=pipeline_service,
    )

    assert [item.source_type for item in batch.items] == ["candidate", "share_link", "brief", "script"]
    workflows = [repository.get_pipeline_run(item.run_id).config["workflow"] for item in batch.items]
    assert workflows == [
        "production_batch_candidate",
        "production_batch_share_link",
        "production_batch_brief",
        "production_batch_script",
    ]
    assert service.batch_progress(batch)["pending"] == 4


def test_production_api_accepts_mixed_source_items(tmp_path):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    production_service = ProductionService(repository, tmp_path / "production")
    app.dependency_overrides[backend_deps.get_production_service] = lambda: production_service
    app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: pipeline_service
    try:
        with TestClient(app, headers=TEST_API_HEADERS) as client:
            profile = client.post("/api/v1/production/profiles", json={"name": "混合 API 配方"}).json()
            response = client.post(
                "/api/v1/production/batches",
                headers={"Idempotency-Key": "create-mixed-batch"},
                json={
                    "name": "API 混合批次",
                    "profile_id": profile["profile_id"],
                    "items": [
                        {"source_type": "brief", "source_value": "讲解 AI 获客"},
                        {"source_type": "script", "source_value": "已写好的口播稿"},
                    ],
                },
            )
    finally:
        app.dependency_overrides.pop(backend_deps.get_production_service, None)
        app.dependency_overrides.pop(backend_deps.get_pipeline_service, None)

    assert response.status_code == 201, response.text
    assert [item["source_type"] for item in response.json()["items"]] == ["brief", "script"]


def test_keyword_auto_run_api_only_enqueues_after_preflight(tmp_path):
    repository = MockRepository()
    production_service = ProductionService(repository, tmp_path / "production")
    pipeline_service = PipelineService(repository, None, None, None, None)
    app.dependency_overrides[backend_deps.get_production_service] = lambda: production_service
    app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: pipeline_service
    try:
        with TestClient(app, headers=TEST_API_HEADERS) as client:
            profile_response = client.post(
                "/api/v1/production/profiles",
                json={
                    "name": "自动生产 IP 配方",
                    "avatar_id": "avatar-owner",
                    "voice_id": "voice-owner",
                    "edit_template_id": "template-professional",
                },
            )
            profile_id = profile_response.json()["profile_id"]
            request = {
                "keyword": "企业获客",
                "candidate_count": 2,
                "profile_id": profile_id,
                "rights_holder": "测试主体",
                "rights_confirmed": True,
                "publish_platforms": ["douyin"],
            }
            preflight_response = client.post("/api/v1/production/keyword-runs/preflight", json=request)
            start_response = client.post("/api/v1/production/keyword-runs", json=request)
    finally:
        app.dependency_overrides.pop(backend_deps.get_production_service, None)
        app.dependency_overrides.pop(backend_deps.get_pipeline_service, None)

    assert preflight_response.status_code == 200, preflight_response.text
    assert preflight_response.json()["ready"] is True
    assert start_response.status_code == 202, start_response.text
    run = repository.get_pipeline_run(start_response.json()["run_id"])
    assert run is not None
    assert run.config["workflow"] == "keyword_auto_master"
    assert run.status.value == "pending"


class _MediaPreview:
    def preview(self, candidate):
        blocked = candidate.video_id.endswith("blocked")
        return SimpleNamespace(
            resolvable=not blocked,
            estimated_cost_cny=0.2,
            monthly_budget_used_cny=0.4,
            block_reason="媒体结果待人工核对" if blocked else None,
        )


class _UnknownMediaPreview(_MediaPreview):
    def preview(self, candidate):
        preview = super().preview(candidate)
        return SimpleNamespace(
            **{
                **preview.__dict__,
                "estimated_cost_cny": None,
            }
        )


class _PaidLinkPreview:
    def preview(self, share_text):
        assert share_text.startswith("https://")
        return SimpleNamespace(
            parser_enabled=False,
            parser_message="本机解析未启用",
            oneapi_fallback_available=True,
            oneapi_estimated_cost_cny=0.3,
        )


class _LocalLinkPreview:
    def preview(self, share_text):
        assert share_text.startswith("https://")
        return SimpleNamespace(
            parser_enabled=True,
            parser_message="本机浏览器已连接",
            oneapi_fallback_available=False,
            oneapi_estimated_cost_cny=None,
        )


class _Assets:
    def list_assets(self):
        return [
            AvatarAsset(asset_id="avatar-owner", kind=AvatarAssetKind.AVATAR, name="形象", authorized=True),
            AvatarAsset(asset_id="voice-owner", kind=AvatarAssetKind.VOICE, name="音色", authorized=True),
        ]


class _Templates:
    def get_template(self, template_id):
        return SimpleNamespace(template_id=template_id) if template_id == "template-professional" else None


class _Publish:
    def available_platforms(self):
        return [{"platform": "douyin", "display_name": "抖音", "enabled": False, "manual_fallback": True, "mode": "manual"}]


class _LocalBrowserPublish:
    def available_platforms(self):
        return [
            {
                "platform": "douyin",
                "display_name": "抖音本机发布",
                "enabled": True,
                "manual_fallback": True,
                "mode": "local_browser",
                "requires_account": True,
                "provider_name": "douyin_local_browser",
            }
        ]


class _Copywriting:
    def __init__(self):
        self.rewrite_calls = 0

    def capabilities(self):
        return {
            "provider_name": "sandbox_copywriting",
            "mode": "sandbox",
            "enabled": True,
            "estimated_cost_cny": 0.0,
            "missing_configuration": [],
        }

    def generate(self, **kwargs):
        source_text = kwargs["content_brief"]
        return self.rewrite(source_text=source_text)

    def rewrite(self, **kwargs):
        self.rewrite_calls += 1
        now = datetime.now().astimezone()
        source_text = kwargs["source_text"]
        result = f"改写稿：{source_text}"
        return CopywritingTask(
            task_id="copy-transcript-review",
            title="转写改写",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            source_text=source_text,
            result_text=result,
            result_variants=[result],
            source_task_id=kwargs.get("source_task_id"),
            is_mock=True,
        )

    def audit_spoken_script(self, **kwargs):
        return {
            "status": "completed",
            "approved": True,
            "summary": "未发现需要阻止制作的问题。",
            "issues": [],
        }


class _UnknownCostCopywriting(_Copywriting):
    def capabilities(self):
        return {
            "provider_name": "paid_llm",
            "mode": "production",
            "enabled": True,
            "estimated_cost_cny": None,
            "missing_configuration": [],
        }


class _FailingCopywriting(_Copywriting):
    def rewrite(self, **kwargs):
        self.rewrite_calls += 1
        now = datetime.now().astimezone()
        return CopywritingTask(
            task_id="copy-review-failed",
            title="改写失败",
            status=TaskStatus.FAILED,
            progress=100,
            created_at=now,
            updated_at=now,
            source_text=kwargs["source_text"],
            error_message="供应商改写失败",
            is_mock=True,
        )


def test_share_link_preflight_prices_and_records_paid_fallback(tmp_path):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        link_transcription_service=_PaidLinkPreview(),
        copywriting_service=_Copywriting(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    profile = service.create_profile(
        name="链接生产配方",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
        edit_template_id="template-professional",
    )
    batch = service.create_batch(
        name="链接单条任务",
        profile_id=profile.profile_id,
        source_items=[
            {
                "source_type": "share_link",
                "source_value": "https://v.douyin.com/example/",
            }
        ],
        pipeline_service=pipeline_service,
    )
    options = {
        "rights_holder": "测试公司",
        "rights_confirmed": True,
        "publish_platforms": ["douyin"],
        "concurrency": 1,
        "max_total_cost_cny": 1,
        "paid_actions_confirmed": True,
    }

    preflight = service.preflight_batch(batch.batch_id, **options)
    started = service.start_batch(
        batch.batch_id,
        options=options,
        pipeline_service=pipeline_service,
    )
    run = repository.get_pipeline_run(started.items[0].run_id)

    assert preflight["cost_known"] is True
    assert preflight["estimated_cost_cny"] == 0.3
    assert preflight["items"][0]["use_paid_fallback"] is True
    assert run is not None
    assert run.config["use_paid_fallback"] is True


def test_batch_start_isolates_blocked_item_and_respects_single_concurrency(tmp_path):
    repository = MockRepository()
    ready = _candidate("candidate-ready")
    blocked = _candidate("candidate-blocked")
    repository.save_candidate(ready)
    repository.save_candidate(blocked)
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        media_resolution_service=_MediaPreview(),
        copywriting_service=_Copywriting(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    profile = service.create_profile(
        name="可启动配方",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
        edit_template_id="template-professional",
    )
    batch = service.create_batch(
        name="一键批次",
        profile_id=profile.profile_id,
        candidate_ids=[ready.video_id, blocked.video_id],
        pipeline_service=pipeline_service,
    )
    started = service.start_batch(
        batch.batch_id,
        options={
            "rights_holder": "测试公司",
            "rights_confirmed": True,
            "publish_platforms": ["douyin"],
            "concurrency": 1,
            "max_total_cost_cny": 1,
            "paid_actions_confirmed": True,
        },
        pipeline_service=pipeline_service,
    )

    assert started.status.value == "running"
    assert started.items[0].status.value == "queued"
    assert started.items[1].status.value == "blocked"
    assert service.can_run(started.items[0].run_id) is True
    assert service.can_run(started.items[1].run_id) is False


def test_auto_batch_keeps_reserve_idle_until_visual_only_primary_needs_it(tmp_path):
    repository = MockRepository()
    primary = _candidate("candidate-primary")
    reserve = _candidate("candidate-reserve")
    repository.save_candidate(primary)
    repository.save_candidate(reserve)
    copywriting = CopywritingService(repository, SandboxCopywritingEngine())
    pipeline_service = PipelineService(repository, None, copywriting, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        media_resolution_service=_MediaPreview(),
        copywriting_service=copywriting,
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    profile = service.create_profile(
        name="候补配方",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
        edit_template_id="template-professional",
    )
    batch = service.create_batch(
        name="候补批次",
        profile_id=profile.profile_id,
        source_items=[
            {
                "source_type": "candidate",
                "source_value": primary.video_id,
                "candidate_role": "primary",
            },
            {
                "source_type": "candidate",
                "source_value": reserve.video_id,
                "candidate_role": "reserve",
            },
        ],
        pipeline_service=pipeline_service,
    )
    started = service.start_batch(
        batch.batch_id,
        options={
            "rights_holder": "测试公司",
            "rights_confirmed": True,
            "publish_platforms": ["douyin"],
            "concurrency": 1,
            "max_total_cost_cny": 10,
            "paid_actions_confirmed": True,
            "automation_mode": "auto",
        },
        pipeline_service=pipeline_service,
    )

    assert started.items[0].status == ProductionBatchItemStatus.QUEUED
    assert started.items[1].status == ProductionBatchItemStatus.PLANNED
    reserve_run = repository.get_pipeline_run(started.items[1].run_id)
    assert reserve_run is not None
    assert reserve_run.status == PipelineRunStatus.PAUSED

    primary_run = repository.get_pipeline_run(started.items[0].run_id)
    assert primary_run is not None
    repository.save_pipeline_run(
        primary_run.model_copy(
            update={
                "status": PipelineRunStatus.FAILED,
                "error_message": "没有识别到足够可核验的口播。",
                "config": {
                    **primary_run.config,
                    "spoken_material_status": "visual_only",
                    "spoken_material_message": "素材仅作画面参考。",
                },
            }
        )
    )

    replenished = service.maybe_auto_review_batch(
        batch.batch_id,
        pipeline_service=pipeline_service,
    )

    assert replenished is not None
    assert replenished.execution_config["auto_reserve_activated_count"] == 1
    assert replenished.items[1].status == ProductionBatchItemStatus.QUEUED
    reserve_run = repository.get_pipeline_run(started.items[1].run_id)
    assert reserve_run is not None
    assert reserve_run.status == PipelineRunStatus.PENDING


def test_visual_only_transcription_stops_before_copywriting(tmp_path):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, _Copywriting(), None, None)
    run = pipeline_service.create_run(
        keyword="纯画面素材",
        config={"workflow": "production_batch_candidate"},
    )
    now = datetime.now().astimezone()
    transcription = TranscriptionTask(
        task_id="transcription-visual-only",
        title="纯画面转写",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        media_name="visual.mp4",
        media_type="video/mp4",
        rights_confirmed=True,
        duration_seconds=30,
        segments=[TranscriptSegment(text="嗯", confidence=0.4)],
        is_mock=True,
    )
    repository.save_task(transcription)

    stopped = pipeline_service.pause_for_transcript_review(
        run=run,
        transcription=transcription,
    )

    assert stopped.status == PipelineRunStatus.FAILED
    assert stopped.config["spoken_material_status"] == "visual_only"
    assert "不会据此编造文案" in stopped.error_message
    assert stopped.copywriting_task_id is None


def test_batch_preflight_isolates_an_invalid_single_item_profile_override(tmp_path):
    repository = MockRepository()
    candidate = _candidate("candidate-override")
    repository.save_candidate(candidate)
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        media_resolution_service=_MediaPreview(),
        copywriting_service=_Copywriting(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    profile = service.create_profile(
        name="默认配方",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
        edit_template_id="template-professional",
    )
    batch = service.create_batch(
        name="带单条覆盖",
        profile_id=profile.profile_id,
        source_items=[{
            "source_type": "candidate",
            "source_value": candidate.video_id,
            "profile_overrides": {"avatar_id": "avatar-missing"},
        }],
        pipeline_service=pipeline_service,
    )

    preflight = service.preflight_batch(
        batch.batch_id,
        rights_holder="测试公司",
        rights_confirmed=True,
        publish_platforms=["douyin"],
    )

    assert preflight["ready_count"] == 0
    assert "单条覆盖的数字人形象不存在。" in preflight["items"][0]["reasons"]


def test_batch_preflight_and_start_api_enqueue_only_ready_items(tmp_path):
    repository = MockRepository()
    candidate = _candidate("candidate-api-ready")
    repository.save_candidate(candidate)
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        media_resolution_service=_MediaPreview(),
        copywriting_service=_Copywriting(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    app.dependency_overrides[backend_deps.get_production_service] = lambda: service
    app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: pipeline_service
    try:
        with TestClient(app, headers=TEST_API_HEADERS) as client:
            profile = client.post("/api/v1/production/profiles", json={
                "name": "API 启动配方", "avatar_id": "avatar-owner", "voice_id": "voice-owner", "edit_template_id": "template-professional",
            }).json()
            batch = client.post(
                "/api/v1/production/batches",
                headers={"Idempotency-Key": "create-api-ready"},
                json={
                    "name": "API 一键批次",
                    "profile_id": profile["profile_id"],
                    "candidate_ids": [candidate.video_id],
                },
            ).json()
            body = {
                "rights_holder": "测试公司",
                "rights_confirmed": True,
                "publish_platforms": ["douyin"],
                "concurrency": 1,
                "max_total_cost_cny": 1,
                "paid_actions_confirmed": True,
            }
            preflight = client.post(f"/api/v1/production/batches/{batch['batch_id']}/preflight", json=body)
            started = client.post(
                f"/api/v1/production/batches/{batch['batch_id']}/start",
                json=body,
                headers={"Idempotency-Key": "production-start-api-1"},
            )
    finally:
        app.dependency_overrides.pop(backend_deps.get_production_service, None)
        app.dependency_overrides.pop(backend_deps.get_pipeline_service, None)

    assert preflight.status_code == 200, preflight.text
    assert preflight.json()["ready_count"] == 1
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "running"
    assert started.json()["items"][0]["status"] == "queued"


def test_workspace_requires_transcript_then_script_review_for_candidate(tmp_path):
    repository = MockRepository()
    candidate = _candidate("candidate-transcript-gate")
    repository.save_candidate(candidate)
    copywriting = _Copywriting()
    pipeline_service = PipelineService(
        repository,
        None,
        copywriting,
        None,
        None,
    )
    service = ProductionService(repository, tmp_path / "production")
    profile = service.create_profile(name="转写闸门配方")
    batch = service.create_batch(
        name="转写闸门批次",
        profile_id=profile.profile_id,
        candidate_ids=[candidate.video_id],
        pipeline_service=pipeline_service,
    )
    run = repository.get_pipeline_run(batch.items[0].run_id)
    assert run is not None
    now = datetime.now().astimezone()
    transcription = TranscriptionTask(
        task_id="transcription-workspace",
        title="候选转写",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        media_name="candidate.mp4",
        media_type="video/mp4",
        rights_confirmed=True,
        segments=[
            TranscriptSegment(text="原始转写第一句", confidence=0.55, needs_review=True),
            TranscriptSegment(text="原始转写第二句", confidence=0.95),
        ],
        uncertain_segment_count=1,
        is_mock=True,
    )
    repository.save_task(transcription)
    run = pipeline_service.update_stage(
        run,
        PipelineStage.TRANSCRIPTION,
        TaskStatus.SUCCEEDED,
        task_id=transcription.task_id,
    )
    run = run.model_copy(
        update={
            "config": {
                **run.config,
                "transcription_task_id": transcription.task_id,
                "source": "production_batch",
                "automation_mode": "manual",
            }
        }
    )
    repository.save_pipeline_run(run)
    pipeline_service.pause_for_transcript_review(
        run=run,
        transcription=transcription,
    )

    app.dependency_overrides[backend_deps.get_production_service] = lambda: service
    app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: pipeline_service
    try:
        with TestClient(app, headers=TEST_API_HEADERS) as client:
            workspace = client.get(
                f"/api/v1/production/batches/{batch.batch_id}/workspace"
            )
            empty_review = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/reviews",
                json={
                    "stage": "transcript",
                    "reviewer": "审核员",
                    "items": [{"run_id": run.run_id, "approved_text": "  "}],
                },
            )
            transcript_review = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/reviews",
                json={
                    "stage": "transcript",
                    "reviewer": "审核员",
                    "items": [
                        {
                            "run_id": run.run_id,
                            "approved_text": "确认后的真实转写",
                        }
                    ],
                },
            )
            repeated_transcript_review = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/reviews",
                json={
                    "stage": "transcript",
                    "reviewer": "审核员",
                    "items": [
                        {
                            "run_id": run.run_id,
                            "approved_text": "确认后的真实转写",
                        }
                    ],
                },
            )
            script_workspace = client.get(
                f"/api/v1/production/batches/{batch.batch_id}/workspace"
            )
            script_review = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/reviews",
                json={
                    "stage": "script",
                    "reviewer": "审核员",
                    "items": [
                        {
                            "run_id": run.run_id,
                            "approved_text": "确认后的最终口播稿",
                            "creative_plan": {
                                "hook": "先说客户最关心的问题",
                                "key_points": ["说明一个可执行的做法"],
                                "call_to_action": "留言获取清单",
                                "visual_sections": ["开场口播", "讲解做法", "收尾引导"],
                            },
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.pop(backend_deps.get_production_service, None)
        app.dependency_overrides.pop(backend_deps.get_pipeline_service, None)

    assert workspace.status_code == 200, workspace.text
    item = workspace.json()["items"][0]
    assert workspace.json()["next_action"] == "review_transcript"
    assert item["reviews"]["transcript"]["draft_text"].startswith("原始转写")
    assert item["reviews"]["transcript"]["low_confidence_count"] == 1
    assert empty_review.status_code == 400
    assert transcript_review.status_code == 200, transcript_review.text
    assert transcript_review.json()["results"][0]["ok"] is True
    assert repeated_transcript_review.json()["results"][0]["ok"] is True
    assert copywriting.rewrite_calls == 1
    assert script_workspace.status_code == 200, script_workspace.text
    assert script_workspace.json()["items"][0]["reviews"]["script"]["ai_audit"] == {
        "status": "completed",
        "approved": True,
        "summary": "未发现需要阻止制作的问题。",
        "issues": [],
    }
    assert script_review.status_code == 200, script_review.text
    stored = repository.get_pipeline_run(run.run_id)
    assert stored is not None
    assert stored.config["transcript_reviewed"] is True
    assert stored.config["script_reviewed"] is True
    assert stored.config["approved_script_text"] == "确认后的最终口播稿"
    assert stored.config["creative_plan"] == {
        "status": "approved",
        "hook": "先说客户最关心的问题",
        "key_points": ["说明一个可执行的做法"],
        "call_to_action": "留言获取清单",
        "visual_sections": ["开场口播", "讲解做法", "收尾引导"],
    }
    assert stored.current_stage == PipelineStage.AVATAR_GENERATION


def test_workspace_uses_public_media_url_and_reports_delayed_avatar_once(tmp_path):
    """客户工作台绝不把服务器文件路径给浏览器，并如实提示慢任务。"""
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(repository, tmp_path / "production")
    profile = service.create_profile(name="媒体地址配方")
    batch = service.create_batch(
        name="媒体地址批次",
        profile_id=profile.profile_id,
        source_items=[{"source_type": "script", "source_value": "确认后的口播稿"}],
        pipeline_service=pipeline_service,
    )
    run = repository.get_pipeline_run(batch.items[0].run_id)
    assert run is not None
    now = datetime.now().astimezone()
    result_path = tmp_path / "result.mp4"
    result_path.write_bytes(b"test media")
    edit_task = VideoEditTask(
        task_id="workspace-edit-result",
        title="工作台成片",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        source_video_path="source.mp4",
        result_path=str(result_path),
    )
    repository.save_task(edit_task)
    run = pipeline_service.update_stage(
        run,
        PipelineStage.VIDEO_EDITING,
        TaskStatus.SUCCEEDED,
        task_id=edit_task.task_id,
        outputs={"video_path": str(result_path)},
    )
    repository.save_pipeline_run(run)

    media_workspace = service.workspace(batch.batch_id)
    assert media_workspace["items"][0]["result_media_url"] == (
        f"/api/v1/pipelines/{run.run_id}/media"
    )
    assert media_workspace["items"][0]["video_path"] == str(result_path)

    delayed_avatar = AvatarTask(
        task_id="workspace-delayed-avatar",
        title="正在生成数字人",
        status=TaskStatus.RUNNING,
        progress=60,
        created_at=now - timedelta(seconds=181),
        updated_at=now,
        script_text="确认后的口播稿",
        avatar_id="avatar-a",
        avatar_name="形象",
        voice_id="voice-a",
        voice_name="音色",
        rights_holder="测试公司",
        rights_confirmed_at=now,
        idempotency_key="workspace-delayed-avatar-key",
        provider_name="cloud",
        provider_job_id="remote-job-1",
    )
    repository.save_task(delayed_avatar)
    repository.save_pipeline_run(
        run.model_copy(
            update={
                "status": PipelineRunStatus.RUNNING,
                "current_stage": PipelineStage.AVATAR_GENERATION,
                "avatar_task_id": delayed_avatar.task_id,
            }
        )
    )

    delayed_workspace = service.workspace(batch.batch_id)
    processing = delayed_workspace["items"][0]["processing"]
    assert processing is not None
    assert processing["stage"] == "avatar"
    assert processing["delayed"] is True
    assert processing["elapsed_seconds"] >= 181
    assert processing["provider_job_received"] is True


def test_cost_preflight_blocks_unknown_unconfirmed_and_over_limit(tmp_path):
    repository = MockRepository()
    candidate = _candidate("candidate-cost-boundary")
    repository.save_candidate(candidate)
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        media_resolution_service=_MediaPreview(),
        copywriting_service=_Copywriting(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    profile = service.create_profile(
        name="费用边界配方",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
        edit_template_id="template-professional",
    )
    batch = service.create_batch(
        name="费用边界批次",
        profile_id=profile.profile_id,
        candidate_ids=[candidate.video_id],
        pipeline_service=pipeline_service,
    )
    base = {
        "rights_holder": "测试公司",
        "rights_confirmed": True,
        "publish_platforms": ["douyin"],
        "concurrency": 1,
    }

    unconfirmed = service.preflight_batch(batch.batch_id, **base)
    over_limit = service.preflight_batch(
        batch.batch_id,
        **base,
        max_total_cost_cny=0.1,
        paid_actions_confirmed=True,
    )
    accepted = service.preflight_batch(
        batch.batch_id,
        **base,
        max_total_cost_cny=1,
        paid_actions_confirmed=True,
    )
    unknown_service = ProductionService(
        repository,
        tmp_path / "production",
        media_resolution_service=_UnknownMediaPreview(),
        copywriting_service=_Copywriting(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    unknown = unknown_service.preflight_batch(
        batch.batch_id,
        **base,
        max_total_cost_cny=1,
        paid_actions_confirmed=True,
    )
    with pytest.raises(ValueError, match="必须确认预计费用"):
        service.start_batch(
            batch.batch_id,
            options=base,
            pipeline_service=pipeline_service,
        )

    assert unconfirmed["cost_blocked"] is True
    assert "必须确认预计费用" in "；".join(unconfirmed["cost_issues"])
    assert over_limit["ready_count"] == 0
    assert "超过本次上限" in "；".join(over_limit["cost_issues"])
    assert accepted["cost_known"] is True
    assert accepted["estimated_cost_cny"] == 0.2
    assert accepted["ready_count"] == 1
    assert unknown["cost_known"] is False
    assert unknown["estimated_cost_cny"] is None
    assert unknown["ready_count"] == 0


def test_batch_create_and_start_idempotency_reuse_and_conflict(tmp_path):
    repository = MockRepository()
    candidate = _candidate("candidate-idempotency")
    repository.save_candidate(candidate)
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        media_resolution_service=_MediaPreview(),
        copywriting_service=_Copywriting(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    app.dependency_overrides[backend_deps.get_production_service] = lambda: service
    app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: pipeline_service
    try:
        with TestClient(app, headers=TEST_API_HEADERS) as client:
            profile = client.post(
                "/api/v1/production/profiles",
                json={
                    "name": "幂等配方",
                    "avatar_id": "avatar-owner",
                    "voice_id": "voice-owner",
                    "edit_template_id": "template-professional",
                },
            ).json()
            create_body = {
                "name": "幂等批次",
                "profile_id": profile["profile_id"],
                "candidate_ids": [candidate.video_id],
            }
            headers = {"Idempotency-Key": "production-create-idem"}
            first_create = client.post(
                "/api/v1/production/batches",
                json=create_body,
                headers=headers,
            )
            second_create = client.post(
                "/api/v1/production/batches",
                json=create_body,
                headers=headers,
            )
            conflict_create = client.post(
                "/api/v1/production/batches",
                json={**create_body, "name": "不同批次"},
                headers=headers,
            )
            batch_id = first_create.json()["batch_id"]
            start_body = {
                "rights_holder": "测试公司",
                "rights_confirmed": True,
                "publish_platforms": ["douyin"],
                "concurrency": 1,
                "max_total_cost_cny": 1,
                "paid_actions_confirmed": True,
            }
            start_headers = {"Idempotency-Key": "production-start-idem"}
            first_start = client.post(
                f"/api/v1/production/batches/{batch_id}/start",
                json=start_body,
                headers=start_headers,
            )
            second_start = client.post(
                f"/api/v1/production/batches/{batch_id}/start",
                json=start_body,
                headers=start_headers,
            )
            conflict_start = client.post(
                f"/api/v1/production/batches/{batch_id}/start",
                json={**start_body, "concurrency": 2},
                headers=start_headers,
            )
    finally:
        app.dependency_overrides.pop(backend_deps.get_production_service, None)
        app.dependency_overrides.pop(backend_deps.get_pipeline_service, None)

    assert first_create.status_code == 201, first_create.text
    assert second_create.status_code == 201, second_create.text
    assert first_create.json()["batch_id"] == second_create.json()["batch_id"]
    assert conflict_create.status_code == 409
    assert first_start.status_code == 200, first_start.text
    assert second_start.status_code == 200, second_start.text
    assert first_start.json()["batch_id"] == second_start.json()["batch_id"]
    assert conflict_start.status_code == 409


def test_publish_requires_output_review_and_persists_manual_targets_idempotently(
    tmp_path,
):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        publish_service=_Publish(),
    )
    profile = service.create_profile(name="发布配方")
    batch = service.create_batch(
        name="发布批次",
        profile_id=profile.profile_id,
        source_items=[
            {
                "source_type": "script",
                "source_value": "最终口播文案",
            }
        ],
        pipeline_service=pipeline_service,
    )
    video_path = tmp_path / "result.mp4"
    video_path.write_bytes(b"video")
    run = repository.get_pipeline_run(batch.items[0].run_id)
    assert run is not None
    run = pipeline_service.update_stage(
        run,
        PipelineStage.VIDEO_EDITING,
        TaskStatus.SUCCEEDED,
        outputs={"video_path": str(video_path)},
    )
    run = run.model_copy(
        update={
            "status": PipelineRunStatus.PAUSED,
            "current_stage": PipelineStage.PUBLISHING,
            "config": {
                **run.config,
                "approved_script_text": "最终口播文案",
                "video_path": str(video_path),
                "output_reviewed": False,
            },
        }
    )
    repository.save_pipeline_run(run)
    app.dependency_overrides[backend_deps.get_production_service] = lambda: service
    app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: pipeline_service
    publish_body = {
        "run_ids": [run.run_id],
        "targets": [
            {
                "platform": "douyin",
                "use_manual_fallback": True,
            }
        ],
        "confirmation_accepted": True,
    }
    try:
        with TestClient(app, headers=TEST_API_HEADERS) as client:
            blocked = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/publish/preflight",
                json=publish_body,
            )
            reviewed = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/reviews",
                json={
                    "stage": "output",
                    "reviewer": "成片审核员",
                    "items": [{"run_id": run.run_id}],
                },
            )
            missing_draft = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/publish/preflight",
                json=publish_body,
            )
            publish_review = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/reviews",
                json={
                    "stage": "publish",
                    "reviewer": "成片审核员",
                    "items": [{
                        "run_id": run.run_id,
                        "publish_draft": {
                            "title": "客户可见标题",
                            "description": "客户可见发布描述",
                            "tags": ["本地获客", "真实案例"],
                        },
                    }],
                },
            )
            ready = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/publish/preflight",
                json=publish_body,
            )
            workspace_ready = client.get(
                f"/api/v1/production/batches/{batch.batch_id}/workspace"
            )
            headers = {"Idempotency-Key": "production-publish-idem"}
            confirmed = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/publish",
                json=publish_body,
                headers=headers,
            )
            repeated = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/publish",
                json=publish_body,
                headers=headers,
            )
            conflict = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/publish",
                json={
                    **publish_body,
                    "targets": [
                        {
                            "platform": "kuaishou",
                            "use_manual_fallback": True,
                        }
                    ],
                },
                headers=headers,
            )
            workspace = client.get(
                f"/api/v1/production/batches/{batch.batch_id}/workspace"
            )
    finally:
        app.dependency_overrides.pop(backend_deps.get_production_service, None)
        app.dependency_overrides.pop(backend_deps.get_pipeline_service, None)

    assert blocked.status_code == 200
    assert blocked.json()["blocked"] is True
    assert "成片复核" in blocked.json()["items"][0]["issues"][0]
    assert reviewed.status_code == 200, reviewed.text
    assert missing_draft.json()["blocked"] is True
    assert "确认标题" in missing_draft.json()["items"][0]["issues"][0]
    assert publish_review.status_code == 200, publish_review.text
    assert ready.status_code == 200, ready.text
    assert ready.json()["blocked"] is False
    assert ready.json()["items"][0]["resolved_targets"][0]["mode"] == "manual"
    assert workspace_ready.status_code == 200, workspace_ready.text
    assert workspace_ready.json()["current_stage"] == "publish"
    assert workspace_ready.json()["next_action"] == "publish"
    assert "publish" in workspace_ready.json()["allowed_actions"]
    assert confirmed.status_code == 200, confirmed.text
    assert repeated.status_code == 200, repeated.text
    assert confirmed.json()["batch_id"] == repeated.json()["batch_id"]
    assert conflict.status_code == 409
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["items"][0]["publish"]["confirmed"] is True
    assert workspace.json()["next_action"] == "wait"


def test_retry_failed_uses_safe_stage_and_blocks_unknown_or_confirmed_publish(
    tmp_path,
):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(repository, tmp_path / "production")
    profile = service.create_profile(name="安全重试配方")
    batch = service.create_batch(
        name="安全重试批次",
        profile_id=profile.profile_id,
        source_items=[
            {"source_type": "script", "source_value": "第一条口播稿"},
            {"source_type": "script", "source_value": "第二条口播稿"},
            {"source_type": "script", "source_value": "第三条口播稿"},
        ],
        pipeline_service=pipeline_service,
    )
    now = datetime.now().astimezone()
    succeeded_avatar = CopywritingTask(
        task_id="avatar-result-known",
        title="已完成数字人替身记录",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        result_text="ok",
    )
    unknown_avatar = succeeded_avatar.model_copy(
        update={
            "task_id": "avatar-result-unknown",
            "status": TaskStatus.OUTCOME_UNKNOWN,
        }
    )
    repository.save_task(succeeded_avatar)
    repository.save_task(unknown_avatar)
    safe_run = repository.get_pipeline_run(batch.items[0].run_id)
    unknown_run = repository.get_pipeline_run(batch.items[1].run_id)
    publish_run = repository.get_pipeline_run(batch.items[2].run_id)
    assert safe_run and unknown_run and publish_run
    repository.save_pipeline_run(
        safe_run.model_copy(
            update={
                "status": PipelineRunStatus.FAILED,
                "current_stage": PipelineStage.VIDEO_EDITING,
                "avatar_task_id": succeeded_avatar.task_id,
                "error_message": "剪辑失败",
            }
        )
    )
    repository.save_pipeline_run(
        unknown_run.model_copy(
            update={
                "status": PipelineRunStatus.FAILED,
                "current_stage": PipelineStage.AVATAR_GENERATION,
                "avatar_task_id": unknown_avatar.task_id,
                "error_message": "供应商结果未知",
            }
        )
    )
    repository.save_pipeline_run(
        publish_run.model_copy(
            update={
                "status": PipelineRunStatus.FAILED,
                "current_stage": PipelineStage.PUBLISHING,
                "config": {**publish_run.config, "publish_confirmed": True},
                "error_message": "发布结果待核对",
            }
        )
    )
    service.sync_batch(batch.batch_id)

    retried = service.retry_failed(
        batch.batch_id,
        pipeline_service=pipeline_service,
    )

    assert retried.items[0].status.value == "queued"
    resumed = repository.get_pipeline_run(safe_run.run_id)
    assert resumed is not None
    assert resumed.status == PipelineRunStatus.RUNNING
    assert resumed.current_stage == PipelineStage.AVATAR_GENERATION
    assert retried.items[1].status.value == "blocked"
    assert "结果未知" in retried.items[1].blocked_reasons[0]
    assert retried.items[2].status.value == "blocked"
    assert "已经确认发布" in retried.items[2].blocked_reasons[0]


def test_retry_failed_allows_one_explicit_retry_when_asr_upload_never_submitted(
    tmp_path,
):
    candidate = _candidate("candidate-upload-failed")
    repository = MockRepository(candidates=[candidate], tasks=[])
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(repository, tmp_path / "production")
    profile = service.create_profile(name="转写上传恢复配方")
    batch = service.create_batch(
        name="转写上传恢复批次",
        profile_id=profile.profile_id,
        candidate_ids=[candidate.video_id],
        pipeline_service=pipeline_service,
    )
    run = repository.get_pipeline_run(batch.items[0].run_id)
    assert run is not None
    source = tmp_path / "preserved.mp4"
    source.write_bytes(b"video")
    now = datetime.now().astimezone()
    failed_task = TranscriptionTask(
        task_id="transcription-upload-failed",
        title="上传失败转写",
        status=TaskStatus.FAILED,
        progress=20,
        created_at=now,
        updated_at=now,
        media_name="candidate.mp4",
        media_type="video/mp4",
        rights_confirmed=True,
        provider_name="aliyun_fun_asr",
        provider_status="failed",
        provider_job_id=None,
        estimated_cost_cny=0.0062,
        outputs={"source_media_path": str(source)},
        error_message="素材上传连接失败，尚未创建云端识别任务。",
    )
    repository.save_task(failed_task)
    run = pipeline_service.update_stage(
        run,
        PipelineStage.TRANSCRIPTION,
        TaskStatus.FAILED,
        task_id=failed_task.task_id,
        error_message=failed_task.error_message,
    )
    repository.save_pipeline_run(
        run.model_copy(
            update={
                "config": {
                    **run.config,
                    "source": "production_batch",
                    "transcription_task_id": failed_task.task_id,
                }
            }
        )
    )
    service.sync_batch(batch.batch_id)

    workspace = service.workspace(batch.batch_id)
    assert workspace["next_action"] == "retry"
    assert workspace["items"][0]["recovery"] == {
        "kind": "transcription_upload_retry",
        "estimated_cost_cny": 0.0062,
        "currency": "CNY",
        "attempts_used": 0,
        "max_attempts": 1,
    }

    retried = service.retry_failed(
        batch.batch_id,
        pipeline_service=pipeline_service,
    )

    assert retried.items[0].status == ProductionBatchItemStatus.QUEUED
    queued = repository.get_pipeline_run(run.run_id)
    assert queued is not None
    assert queued.status == PipelineRunStatus.PENDING
    assert queued.current_stage is None
    assert "transcription_task_id" not in queued.config
    assert queued.config["retry_source_transcription_task_id"] == failed_task.task_id
    assert queued.config["stage_retry_counts"]["transcription"] == 1


def test_retry_repairs_a_legacy_avatar_attempt_that_never_reached_provider(tmp_path):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(repository, tmp_path / "production")
    profile = service.create_profile(name="旧重试修复配方")
    batch = service.create_batch(
        name="旧重试修复批次",
        profile_id=profile.profile_id,
        source_items=[{"source_type": "script", "source_value": "确认后的口播稿"}],
        pipeline_service=pipeline_service,
    )
    run = repository.get_pipeline_run(batch.items[0].run_id)
    assert run is not None
    now = datetime.now().astimezone()
    initial_avatar = AvatarTask(
        task_id="avatar-initial-failed",
        title="首次失败数字人",
        status=TaskStatus.FAILED,
        progress=0,
        created_at=now,
        updated_at=now,
        script_text="确认后的口播稿",
        avatar_id="avatar-a",
        avatar_name="形象",
        voice_id="voice-a",
        voice_name="音色",
        rights_holder="测试公司",
        rights_confirmed_at=now,
        idempotency_key=f"worker-avatar-{run.run_id}",
        provider_name="shuying_legacy_cloud",
        error_message="供应商拒绝请求",
    )
    repository.save_task(initial_avatar)
    repository.save_pipeline_run(
        run.model_copy(
            update={
                "status": PipelineRunStatus.FAILED,
                "current_stage": PipelineStage.AVATAR_GENERATION,
                "avatar_task_id": initial_avatar.task_id,
                "error_message": initial_avatar.error_message,
                "config": {
                    **run.config,
                    "stage_retry_counts": {"avatar_generation": 1},
                },
            }
        )
    )
    service.sync_batch(batch.batch_id)

    repaired = service.retry_failed(batch.batch_id, pipeline_service=pipeline_service)

    assert repaired.items[0].status.value == "queued"
    resumed = repository.get_pipeline_run(run.run_id)
    assert resumed is not None
    assert resumed.status == PipelineRunStatus.PENDING
    assert resumed.avatar_task_id is None
    assert resumed.config["stage_retry_counts"]["avatar_generation"] == 1

    rejected_retry = initial_avatar.model_copy(
        update={
            "task_id": "avatar-retry-rejected",
            "idempotency_key": f"worker-avatar-{run.run_id}-retry-1",
            "error_message": "任务名称不能超过50个字符",
        }
    )
    repository.save_task(rejected_retry)
    repository.save_pipeline_run(
        resumed.model_copy(
            update={
                "status": PipelineRunStatus.FAILED,
                "current_stage": PipelineStage.AVATAR_GENERATION,
                "avatar_task_id": rejected_retry.task_id,
                "error_message": rejected_retry.error_message,
            }
        )
    )
    service.sync_batch(batch.batch_id)

    corrected = service.retry_failed(batch.batch_id, pipeline_service=pipeline_service)

    assert corrected.items[0].status.value == "queued"
    corrected_run = repository.get_pipeline_run(run.run_id)
    assert corrected_run is not None
    assert corrected_run.config["stage_retry_counts"]["avatar_generation"] == 2


def test_manual_publish_fallback_creates_persistent_manual_ready_task(tmp_path):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    video_path = tmp_path / "result.mp4"
    video_path.write_bytes(b"video")
    run = pipeline_service.create_run(
        keyword="人工发布包",
        config={
            "workflow": "production_batch_script",
            "output_reviewed": True,
            "publish_confirmed": True,
            "video_path": str(video_path),
            "approved_script_text": "最终口播文案",
            "publish_draft": {
                "title": "人工发布包",
                "description": "最终口播文案",
                "tags": [],
            },
            "publish_draft_approved": True,
            "publish_draft_fingerprint": publish_draft_fingerprint({
                "title": "人工发布包",
                "description": "最终口播文案",
                "tags": [],
            }),
            "publish_targets": [
                {
                    "platform": "douyin",
                    "account_id": None,
                    "mode": "manual",
                    "display_name": "抖音人工发布助手",
                    "use_manual_fallback": True,
                },
                {
                    "platform": "bilibili",
                    "account_id": None,
                    "mode": "manual",
                    "display_name": "Bilibili 人工发布助手",
                    "use_manual_fallback": True,
                },
            ],
        },
    )
    run = run.model_copy(
        update={
            "status": PipelineRunStatus.PENDING,
            "current_stage": PipelineStage.PUBLISHING,
        }
    )
    repository.save_pipeline_run(run)
    worker = PipelineWorker(
        repository=repository,
        pipeline_service=pipeline_service,
        commercial_search_service=None,
        avatar_service=None,
        video_editing_service=None,
        publish_service=_Publish(),
        template_service=None,
        production_service=None,
    )

    worker._submit_publish(run)

    stored = repository.get_pipeline_run(run.run_id)
    assert stored is not None
    assert stored.status == PipelineRunStatus.PAUSED
    assert len(stored.publish_task_ids) == 2
    publish_tasks = [
        repository.get_task(task_id) for task_id in stored.publish_task_ids
    ]
    assert all(task is not None for task in publish_tasks)
    assert {task.target.platform.value for task in publish_tasks} == {
        "douyin",
        "bilibili",
    }
    assert all(task.publish_status.value == "manual_ready" for task in publish_tasks)
    assert all(task.source_pipeline_run_id == run.run_id for task in publish_tasks)

    service = ProductionService(repository, tmp_path / "production")
    profile = service.create_profile(
        name="人工发布状态",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
        edit_template_id="template-professional",
    )
    production_batch = service.create_batch(
        name="人工发布状态批次",
        profile_id=profile.profile_id,
        source_items=[{"source_type": "script", "source_value": "人工发布包"}],
        pipeline_service=pipeline_service,
    )
    production_batch = production_batch.model_copy(
        update={
            "items": [
                production_batch.items[0].model_copy(update={"run_id": stored.run_id})
            ]
        }
    )
    repository.save_production_batch(production_batch)

    synced = service.sync_batch(production_batch.batch_id)

    assert synced is not None
    assert synced.status == ProductionBatchStatus.AWAITING_PUBLISH
    assert synced.items[0].status == ProductionBatchItemStatus.AWAITING_PUBLISH

    for task in publish_tasks:
        assert task is not None
        repository.save_task(
            task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "publish_status": PublishStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "人工确认已发布",
                }
            )
        )

    completed = service.workspace(production_batch.batch_id)

    assert completed["status"] == "succeeded"
    assert completed["current_stage"] == "completed"
    assert completed["next_action"] == "view_result"
    assert completed["items"][0]["status"] == "succeeded"
    assert completed["items"][0]["publish"]["status"] == "succeeded"


def test_ready_local_browser_account_creates_prepare_only_real_task(
    tmp_path,
    monkeypatch,
):
    from src.services import publish_accounts

    account = SimpleNamespace(
        account_id="pubacc-test",
        platform="douyin",
        name="公司主号",
        status="ready",
        auto_publish_authorized=False,
    )
    manager = SimpleNamespace(
        get=lambda account_id, platform: account,
        status=lambda account_id: account,
    )
    monkeypatch.setattr(
        publish_accounts,
        "publish_account_manager",
        manager,
    )
    service = ProductionService(
        MockRepository(),
        tmp_path / "production",
        publish_service=_LocalBrowserPublish(),
    )
    request = [
        {
            "platform": "douyin",
            "account_id": account.account_id,
            "use_manual_fallback": True,
        }
    ]

    real, issues = service._resolve_publish_targets(request)
    strict, strict_issues = service._resolve_publish_targets(
        [{**request[0], "use_manual_fallback": False}]
    )

    assert not issues
    assert real[0]["mode"] == "real"
    assert real[0]["account_id"] == account.account_id
    assert not strict_issues
    assert strict[0]["mode"] == "real"


def test_paid_llm_unknown_cost_blocks_brief_preflight(tmp_path):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        copywriting_service=_UnknownCostCopywriting(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    profile = service.create_profile(
        name="未知文案费用",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
        edit_template_id="template-professional",
    )
    batch = service.create_batch(
        name="未知文案费用批次",
        profile_id=profile.profile_id,
        source_items=[{"source_type": "brief", "source_value": "讲解企业获客"}],
        pipeline_service=pipeline_service,
    )

    preflight = service.preflight_batch(
        batch.batch_id,
        rights_holder="测试公司",
        rights_confirmed=True,
        publish_platforms=["douyin"],
        max_total_cost_cny=10,
        paid_actions_confirmed=True,
    )

    assert preflight["cost_known"] is False
    assert preflight["ready_count"] == 0
    assert "文案生成" in "；".join(preflight["cost_issues"])


def test_workspace_cost_quote_unblocks_unknown_copywriting_cost(tmp_path):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        copywriting_service=_UnknownCostCopywriting(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    profile = service.create_profile(
        name="已报价文案",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
        edit_template_id="template-professional",
    )
    service.configure_workspace(
        rights_holder="测试公司",
        agreement_accepted=True,
        default_profile_id=profile.profile_id,
        copywriting_estimated_cost_cny=0.05,
        avatar_estimated_cost_cny=0,
        bundled_compute=False,
    )
    batch = service.create_batch(
        name="已报价文案批次",
        profile_id=profile.profile_id,
        source_items=[{"source_type": "brief", "source_value": "讲解企业获客"}],
        pipeline_service=pipeline_service,
    )

    preflight = service.preflight_batch(
        batch.batch_id,
        rights_holder="测试公司",
        rights_confirmed=True,
        publish_platforms=["douyin"],
        paid_actions_confirmed=True,
    )

    assert preflight["cost_known"] is True
    assert preflight["ready_count"] == 1
    # 手动模式会先改写、再做一次 AI 文案审核，两次均使用已配置的单次报价。
    assert preflight["estimated_cost_cny"] == 0.1
    assert preflight["items"][0]["manual_script_audit"] is True
    assert preflight["items"][0]["copy_call_count"] == 2
    assert preflight["items"][0]["transcript_review_reserved"] is False


def test_bundled_compute_treats_unknown_provider_prices_as_included(tmp_path):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        copywriting_service=_UnknownCostCopywriting(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    profile = service.create_profile(
        name="包算力配方",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
        edit_template_id="template-professional",
    )
    service.configure_workspace(
        rights_holder="测试公司",
        agreement_accepted=True,
        default_profile_id=profile.profile_id,
        bundled_compute=True,
    )
    batch = service.create_batch(
        name="包算力批次",
        profile_id=profile.profile_id,
        source_items=[{"source_type": "brief", "source_value": "讲解企业获客"}],
        pipeline_service=pipeline_service,
    )

    preflight = service.preflight_batch(
        batch.batch_id,
        rights_holder="测试公司",
        rights_confirmed=True,
        publish_platforms=["douyin"],
        paid_actions_confirmed=True,
    )

    assert preflight["cost_known"] is True
    assert preflight["estimated_cost_cny"] == 0
    assert preflight["ready_count"] == 1


def test_failed_transcript_rewrite_is_reported_as_review_failure(tmp_path):
    repository = MockRepository()
    candidate = _candidate("candidate-review-failure")
    repository.save_candidate(candidate)
    copywriting = _FailingCopywriting()
    pipeline_service = PipelineService(
        repository, None, copywriting, None, None
    )
    service = ProductionService(repository, tmp_path / "production")
    profile = service.create_profile(name="审核失败配方")
    batch = service.create_batch(
        name="审核失败批次",
        profile_id=profile.profile_id,
        candidate_ids=[candidate.video_id],
        pipeline_service=pipeline_service,
    )
    run = repository.get_pipeline_run(batch.items[0].run_id)
    assert run is not None
    now = datetime.now().astimezone()
    transcription = TranscriptionTask(
        task_id="transcription-review-failure",
        title="转写",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        media_name="candidate.mp4",
        media_type="video/mp4",
        rights_confirmed=True,
        segments=[TranscriptSegment(text="真实转写", confidence=0.9)],
        is_mock=True,
    )
    repository.save_task(transcription)
    run = run.model_copy(
        update={
            "config": {
                **run.config,
                "transcription_task_id": transcription.task_id,
            }
        }
    )
    repository.save_pipeline_run(run)
    pipeline_service.pause_for_transcript_review(
        run=run,
        transcription=transcription,
    )
    app.dependency_overrides[backend_deps.get_production_service] = lambda: service
    app.dependency_overrides[backend_deps.get_pipeline_service] = (
        lambda: pipeline_service
    )
    try:
        with TestClient(app, headers=TEST_API_HEADERS) as client:
            response = client.post(
                f"/api/v1/production/batches/{batch.batch_id}/reviews",
                json={
                    "stage": "transcript",
                    "reviewer": "审核员",
                    "items": [
                        {
                            "run_id": run.run_id,
                            "approved_text": "确认后的真实转写",
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.pop(
            backend_deps.get_production_service, None
        )
        app.dependency_overrides.pop(
            backend_deps.get_pipeline_service, None
        )

    assert response.status_code == 200
    assert response.json()["results"][0]["ok"] is False
    assert "供应商改写失败" in response.json()["results"][0]["error"]
    assert copywriting.rewrite_calls == 1


def test_outcome_unknown_is_blocked_and_never_claimed(tmp_path):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(repository, tmp_path / "production")
    profile = service.create_profile(name="未知结果配方")
    batch = service.create_batch(
        name="未知结果批次",
        profile_id=profile.profile_id,
        source_items=[{"source_type": "script", "source_value": "已确认口播稿"}],
        pipeline_service=pipeline_service,
    )
    run = repository.get_pipeline_run(batch.items[0].run_id)
    assert run is not None
    run = run.model_copy(
        update={
            "status": PipelineRunStatus.PAUSED,
            "current_stage": PipelineStage.AVATAR_GENERATION,
            "error_message": "数字人结果待核对",
            "config": {
                **run.config,
                "outcome_unknown": True,
                "recovery_blocked": True,
            },
        }
    )
    repository.save_pipeline_run(run)

    synced = service.sync_batch(batch.batch_id)
    assert synced is not None
    repository.save_production_batch(
        synced.model_copy(
            update={
                "is_paused": True,
                "status": ProductionBatchStatus.PAUSED,
            }
        )
    )
    workspace = service.workspace(batch.batch_id)

    assert synced.items[0].status.value == "blocked"
    assert service.can_run(run.run_id) is False
    assert workspace["status"] == "outcome_unknown"
    assert workspace["next_action"] == "manual_review"
    assert workspace["allowed_actions"] == []
    assert workspace["retry_allowed"] is False


def test_restart_blocks_uncertain_paid_stage_and_resumes_local_edit(tmp_path):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    worker = PipelineWorker(
        repository=repository,
        pipeline_service=pipeline_service,
        commercial_search_service=None,
        avatar_service=None,
        video_editing_service=None,
        publish_service=None,
        template_service=None,
    )
    stale = pipeline_service.create_run(
        keyword="重启未知阶段",
        config={"workflow": "production_batch_candidate"},
    ).model_copy(
        update={
            "status": PipelineRunStatus.RUNNING,
            "current_stage": PipelineStage.COPYWRITING,
            "updated_at": datetime.now().astimezone() - timedelta(minutes=3),
            "config": {
                "workflow": "production_batch_candidate",
                "transcript_review_in_progress": True,
            },
        }
    )
    repository.save_pipeline_run(stale)
    editing = pipeline_service.create_run(
        keyword="本地剪辑恢复",
        config={"workflow": "production_batch_script"},
    ).model_copy(
        update={
            "status": PipelineRunStatus.RUNNING,
            "current_stage": PipelineStage.VIDEO_EDITING,
            "avatar_task_id": "avatar-existing",
        }
    )
    repository.save_pipeline_run(editing)

    blocked = worker._recover_interrupted_run(stale)
    resumed = worker._recover_interrupted_run(editing)

    assert blocked.status == PipelineRunStatus.PAUSED
    assert blocked.config["outcome_unknown"] is True
    assert blocked.config["recovery_blocked"] is True
    assert resumed.status == PipelineRunStatus.RUNNING
    assert resumed.current_stage == PipelineStage.AVATAR_GENERATION
    assert resumed.avatar_task_id == "avatar-existing"


def test_sqlite_production_operation_claim_is_persistent_and_resource_locked(
    tmp_path,
):
    database = tmp_path / "production-idempotency.sqlite3"
    first = SQLiteRepository(database)
    second = SQLiteRepository(database)
    created_at = datetime.now().astimezone().isoformat()

    assert first.claim_production_operation(
        operation_type="start",
        idempotency_key="start-key-one",
        request_hash="hash-one",
        resource_id="batch-one",
        created_at=created_at,
    )
    assert not second.claim_production_operation(
        operation_type="start",
        idempotency_key="start-key-one",
        request_hash="hash-one",
        resource_id="batch-one",
        created_at=created_at,
    )
    assert not second.claim_production_operation(
        operation_type="publish",
        idempotency_key="publish-key-two",
        request_hash="hash-two",
        resource_id="batch-one",
        created_at=created_at,
    )
    stored = second.get_production_operation(
        operation_type="start",
        idempotency_key="start-key-one",
    )
    assert stored is not None
    assert stored["state"] == "pending"
    assert stored["request_hash"] == "hash-one"


def test_non_douyin_candidate_uses_local_link_preflight(tmp_path):
    repository = MockRepository()
    candidate = _candidate("candidate-xhs").model_copy(
        update={
            "platform": Platform.XIAOHONGSHU,
            "source_url": "https://www.xiaohongshu.com/explore/example",
        }
    )
    repository.save_candidate(candidate)
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        media_resolution_service=_MediaPreview(),
        link_transcription_service=_LocalLinkPreview(),
        copywriting_service=_Copywriting(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    profile = service.create_profile(
        name="平台限制配方",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
        edit_template_id="template-professional",
    )
    batch = service.create_batch(
        name="平台限制批次",
        profile_id=profile.profile_id,
        candidate_ids=[candidate.video_id],
        pipeline_service=pipeline_service,
    )

    preflight = service.preflight_batch(
        batch.batch_id,
        rights_holder="测试公司",
        rights_confirmed=True,
        publish_platforms=["douyin"],
        max_total_cost_cny=10,
        paid_actions_confirmed=True,
    )

    assert preflight["ready_count"] == 1
    assert preflight["items"][0]["reasons"] == []


def test_douyin_home_candidate_falls_back_to_its_saved_link(
    tmp_path,
    monkeypatch,
):
    repository = MockRepository()
    candidate = _candidate("candidate-home-blocked").model_copy(
        update={
            "source_url": "https://www.douyin.com/video/7390000000000000000",
        }
    )
    repository.save_candidate(candidate)
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        media_resolution_service=_MediaPreview(),
        link_transcription_service=_LocalLinkPreview(),
        copywriting_service=_Copywriting(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    profile = service.create_profile(
        name="主页候选配方",
        avatar_id="avatar-owner",
        voice_id="voice-owner",
        edit_template_id="template-professional",
    )
    batch = service.create_batch(
        name="主页候选批次",
        profile_id=profile.profile_id,
        candidate_ids=[candidate.video_id],
        pipeline_service=pipeline_service,
    )
    options = {
        "rights_holder": "测试公司",
        "rights_confirmed": True,
        "publish_platforms": ["douyin"],
        "concurrency": 1,
        "max_total_cost_cny": 10,
        "paid_actions_confirmed": True,
    }

    preflight = service.preflight_batch(batch.batch_id, **options)
    started = service.start_batch(
        batch.batch_id,
        options=options,
        pipeline_service=pipeline_service,
    )
    run = repository.get_pipeline_run(started.items[0].run_id)

    assert preflight["ready_count"] == 1
    assert preflight["items"][0]["use_candidate_link_fallback"] is True
    assert preflight["items"][0]["reasons"] == []
    assert run is not None
    assert run.config["candidate_link_fallback"] is True
    assert run.config["share_text"] == str(candidate.source_url)

    worker = PipelineWorker(
        repository=repository,
        pipeline_service=pipeline_service,
        commercial_search_service=None,
        avatar_service=None,
        video_editing_service=None,
        publish_service=None,
        template_service=None,
        production_service=service,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        worker,
        "_run_guided_share_link",
        lambda selected: calls.append(f"link:{selected.run_id}"),
    )
    monkeypatch.setattr(
        worker,
        "_run_candidate",
        lambda selected: calls.append(f"media:{selected.run_id}"),
    )

    worker._run_production_batch(run)

    assert calls == [f"link:{run.run_id}"]


def test_auto_batch_selects_one_transcript_and_skips_the_other_three(tmp_path):
    repository = MockRepository()
    candidates = [_candidate(f"candidate-auto-{index}") for index in range(4)]
    for candidate in candidates:
        repository.save_candidate(candidate)
    copywriting = CopywritingService(repository, SandboxCopywritingEngine())
    pipeline_service = PipelineService(
        repository,
        None,
        copywriting,
        None,
        None,
    )
    service = ProductionService(
        repository,
        tmp_path / "production",
        copywriting_service=copywriting,
    )
    profile = service.create_profile(name="自动选稿配方")
    batch = service.create_batch(
        name="自动选稿批次",
        profile_id=profile.profile_id,
        candidate_ids=[candidate.video_id for candidate in candidates],
        pipeline_service=pipeline_service,
    )
    repository.save_production_batch(
        batch.model_copy(
            update={
                "execution_config": {
                    "automation_mode": "auto",
                    "auto_review_state": "pending",
                }
            }
        )
    )
    now = datetime.now().astimezone()
    for index, item in enumerate(batch.items):
        run = repository.get_pipeline_run(item.run_id)
        assert run is not None
        transcription = TranscriptionTask(
            task_id=f"transcription-auto-{index}",
            title=f"自动候选转写 {index}",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            media_name=f"candidate-{index}.mp4",
            media_type="video/mp4",
            rights_confirmed=True,
            segments=[TranscriptSegment(text=f"第 {index + 1} 条真实口播转写", confidence=0.95)],
            is_mock=True,
        )
        repository.save_task(transcription)
        run = run.model_copy(
            update={
                "config": {
                    **run.config,
                    "transcription_task_id": transcription.task_id,
                }
            }
        )
        repository.save_pipeline_run(run)
        pipeline_service.pause_for_transcript_review(
            run=run,
            transcription=transcription,
        )

    updated = service.maybe_auto_review_batch(
        batch.batch_id,
        pipeline_service=pipeline_service,
    )

    assert updated is not None
    assert updated.execution_config["auto_review_state"] == "completed"
    assert updated.execution_config["auto_selected_run_id"] == batch.items[0].run_id
    assert sum(
        item.status == ProductionBatchItemStatus.SKIPPED
        for item in updated.items
    ) == 3
    winner = repository.get_pipeline_run(batch.items[0].run_id)
    assert winner is not None
    assert winner.status == PipelineRunStatus.PENDING
    assert winner.current_stage == PipelineStage.AVATAR_GENERATION


def test_active_pipeline_scan_does_not_drop_items_after_five_hundred():
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    run_ids = [
        pipeline_service.create_run(
            keyword=f"待恢复任务 {index}",
            config={"workflow": "unrelated_pending"},
        ).run_id
        for index in range(501)
    ]

    active = repository.list_active_pipeline_runs()

    assert len(active) == 501
    assert run_ids[0] in {run.run_id for run in active}
