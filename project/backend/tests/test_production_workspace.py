"""客户工作台 API 的最小契约回归。"""

from fastapi.testclient import TestClient

from project.backend.app.core import deps as backend_deps
from project.backend.app.main import app
from src.repositories.mock import MockRepository
from src.services.pipeline import PipelineService
from src.services.production import ProductionService


def test_workspace_exposes_one_item_batch_actions_and_create_idempotency(
    tmp_path,
):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    production_service = ProductionService(
        repository,
        tmp_path / "production",
    )
    app.dependency_overrides[backend_deps.get_production_service] = (
        lambda: production_service
    )
    app.dependency_overrides[backend_deps.get_pipeline_service] = (
        lambda: pipeline_service
    )
    try:
        with TestClient(app) as client:
            profile = client.post(
                "/api/v1/production/profiles",
                json={"name": "工作台 API 配方"},
            ).json()
            body = {
                "name": "单条工作台",
                "profile_id": profile["profile_id"],
                "items": [
                    {
                        "source_type": "script",
                        "source_value": "客户已经确认可用的原始口播稿。",
                    }
                ],
            }
            headers = {"Idempotency-Key": "workspace-create-idem"}
            first = client.post(
                "/api/v1/production/batches",
                json=body,
                headers=headers,
            )
            repeated = client.post(
                "/api/v1/production/batches",
                json=body,
                headers=headers,
            )
            workspace = client.get(
                f"/api/v1/production/batches/{first.json()['batch_id']}/workspace"
            )
            client.post(
                f"/api/v1/production/batches/{first.json()['batch_id']}/pause"
            )
            paused_workspace = client.get(
                f"/api/v1/production/batches/{first.json()['batch_id']}/workspace"
            )
    finally:
        app.dependency_overrides.pop(
            backend_deps.get_production_service,
            None,
        )
        app.dependency_overrides.pop(
            backend_deps.get_pipeline_service,
            None,
        )

    assert first.status_code == 201, first.text
    assert repeated.status_code == 201, repeated.text
    assert first.json()["batch_id"] == repeated.json()["batch_id"]
    assert workspace.status_code == 200, workspace.text
    payload = workspace.json()
    assert payload["current_stage"] == "source"
    assert payload["next_action"] == "preflight"
    assert payload["allowed_actions"] == ["preflight", "start"]
    assert payload["items"][0]["reviews"]["transcript"]["required"] is False
    assert payload["items"][0]["reviews"]["script"]["draft_text"]
    assert paused_workspace.status_code == 200, paused_workspace.text
    assert paused_workspace.json()["next_action"] == "resume"
    assert paused_workspace.json()["allowed_actions"] == ["resume"]


def test_workspace_configuration_is_saved_once_and_reused_for_preflight(tmp_path):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    production_service = ProductionService(repository, tmp_path / "production")
    app.dependency_overrides[backend_deps.get_production_service] = (
        lambda: production_service
    )
    app.dependency_overrides[backend_deps.get_pipeline_service] = (
        lambda: pipeline_service
    )
    try:
        with TestClient(app) as client:
            before = client.get("/api/v1/production/workspace/configuration")
            saved = client.put(
                "/api/v1/production/workspace/configuration",
                json={
                    "rights_holder": "上海小店",
                    "agreement_accepted": True,
                    "default_publish_platforms": ["douyin"],
                },
            )
            after = client.get("/api/v1/production/workspace/configuration")
    finally:
        app.dependency_overrides.pop(backend_deps.get_production_service, None)
        app.dependency_overrides.pop(backend_deps.get_pipeline_service, None)

    assert before.status_code == 200
    assert before.json() == {"configured": False}
    assert saved.status_code == 200, saved.text
    assert saved.json()["rights_holder"] == "上海小店"
    assert saved.json()["bundled_compute"] is True
    assert after.json()["configured"] is True
