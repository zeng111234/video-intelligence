"""IP 配方与批量生产计划的持久化及状态边界测试。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from project.backend.app.core import deps as backend_deps
from project.backend.app.main import app
from src.models import AvatarAsset, AvatarAssetKind, DataSource, HeatLevel, HeatResult, Platform, VideoCandidate, VideoMetricSnapshot
from src.repositories.mock import MockRepository
from src.services.pipeline import PipelineService
from src.services.production import ProductionService


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
        with TestClient(app) as client:
            profile_response = client.post(
                "/api/v1/production/profiles",
                json={"name": "API IP 配方", "tags": ["测试"]},
            )
            assert profile_response.status_code == 201, profile_response.text
            profile = profile_response.json()

            batch_response = client.post(
                "/api/v1/production/batches",
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


def test_keyword_auto_run_api_only_enqueues_after_preflight(tmp_path):
    repository = MockRepository()
    production_service = ProductionService(repository, tmp_path / "production")
    pipeline_service = PipelineService(repository, None, None, None, None)
    app.dependency_overrides[backend_deps.get_production_service] = lambda: production_service
    app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: pipeline_service
    try:
        with TestClient(app) as client:
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
        options={"rights_holder": "测试公司", "rights_confirmed": True, "publish_platforms": ["douyin"], "concurrency": 1},
        pipeline_service=pipeline_service,
    )

    assert started.status.value == "running"
    assert started.items[0].status.value == "queued"
    assert started.items[1].status.value == "blocked"
    assert service.can_run(started.items[0].run_id) is True
    assert service.can_run(started.items[1].run_id) is False


def test_batch_preflight_and_start_api_enqueue_only_ready_items(tmp_path):
    repository = MockRepository()
    candidate = _candidate("candidate-api-ready")
    repository.save_candidate(candidate)
    pipeline_service = PipelineService(repository, None, None, None, None)
    service = ProductionService(
        repository,
        tmp_path / "production",
        media_resolution_service=_MediaPreview(),
        avatar_service=_Assets(),
        template_service=_Templates(),
        publish_service=_Publish(),
    )
    app.dependency_overrides[backend_deps.get_production_service] = lambda: service
    app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: pipeline_service
    try:
        with TestClient(app) as client:
            profile = client.post("/api/v1/production/profiles", json={
                "name": "API 启动配方", "avatar_id": "avatar-owner", "voice_id": "voice-owner", "edit_template_id": "template-professional",
            }).json()
            batch = client.post("/api/v1/production/batches", json={
                "name": "API 一键批次", "profile_id": profile["profile_id"], "candidate_ids": [candidate.video_id],
            }).json()
            body = {"rights_holder": "测试公司", "rights_confirmed": True, "publish_platforms": ["douyin"], "concurrency": 1}
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
