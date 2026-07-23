"""生产任务上下文与数字人历史过滤回归测试。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from project.backend.app.core import deps as backend_deps
from project.backend.app.main import app
from src.models import (
    AvatarTask,
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
    PipelineStepResult,
    TaskStatus,
)


def _avatar_task(task_id: str, *, is_mock: bool) -> AvatarTask:
    now = datetime.now().astimezone()
    return AvatarTask(
        task_id=task_id,
        title="数字人视频",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        is_mock=is_mock,
        script_text="测试口播文案",
        avatar_id="avatar-1",
        avatar_name="测试形象",
        voice_id="voice-1",
        voice_name="测试音色",
        rights_holder="测试公司",
        rights_confirmed_at=now,
        idempotency_key=f"idem-{task_id}",
        provider_name="sandbox_avatar" if is_mock else "local_avatar",
    )


def test_pipeline_list_filters_by_candidate_and_returns_links():
    target = PipelineRun(
        run_id="pipeline-target",
        keyword="目标候选",
        status=PipelineRunStatus.PAUSED,
        candidate_video_id="candidate-target",
        copywriting_task_id="copy-target",
        avatar_task_id="avatar-target",
        publish_task_ids=["publish-target"],
        config={"source": "crawler_candidate"},
    )
    other = PipelineRun(
        run_id="pipeline-other",
        keyword="其他候选",
        candidate_video_id="candidate-other",
    )
    service = SimpleNamespace(list_runs=lambda limit: [target, other])
    app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/pipelines?candidate_id=candidate-target")
    finally:
        app.dependency_overrides.pop(backend_deps.get_pipeline_service, None)

    assert response.status_code == 200, response.text
    data = response.json()
    assert [item["run_id"] for item in data] == ["pipeline-target"]
    assert data[0]["candidate_video_id"] == "candidate-target"
    assert data[0]["copywriting_task_id"] == "copy-target"
    assert data[0]["avatar_task_id"] == "avatar-target"
    assert data[0]["publish_task_ids"] == ["publish-target"]


def test_avatar_history_hides_sandbox_unless_requested():
    repository = SimpleNamespace(
        list_tasks=lambda: [
            _avatar_task("avatar-real", is_mock=False),
            _avatar_task("avatar-sandbox", is_mock=True),
        ]
    )
    service = SimpleNamespace(repository=repository)
    app.dependency_overrides[backend_deps.get_avatar_service] = lambda: service
    try:
        with TestClient(app) as client:
            real_response = client.get("/api/v1/avatar/jobs")
            all_response = client.get("/api/v1/avatar/jobs?include_sandbox=true")
    finally:
        app.dependency_overrides.pop(backend_deps.get_avatar_service, None)

    assert real_response.status_code == 200, real_response.text
    assert [item["task_id"] for item in real_response.json()] == ["avatar-real"]
    assert all_response.status_code == 200, all_response.text
    assert {item["task_id"] for item in all_response.json()} == {
        "avatar-real",
        "avatar-sandbox",
    }


def test_pipeline_review_endpoint_returns_persisted_control_state():
    pending_review = PipelineRun(
        run_id="pipeline-review",
        keyword="审核测试",
        status=PipelineRunStatus.PAUSED,
        current_stage=PipelineStage.HUMAN_REVIEW,
        stages=[
            PipelineStepResult(
                stage=PipelineStage.HUMAN_REVIEW,
                status=TaskStatus.RUNNING,
                task_id="copy-review",
            )
        ],
    )
    reviewed = pending_review.model_copy(
        update={
            "status": PipelineRunStatus.PENDING,
            "current_stage": PipelineStage.AVATAR_GENERATION,
        }
    )
    received: dict[str, object] = {}

    def review_candidate_script(**kwargs):
        received.update(kwargs)
        return reviewed

    service = SimpleNamespace(review_candidate_script=review_candidate_script)
    app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/pipelines/pipeline-review/review",
                json={"approved": True, "reviewer": "审核员", "note": "可进入下一步"},
            )
    finally:
        app.dependency_overrides.pop(backend_deps.get_pipeline_service, None)

    assert response.status_code == 200, response.text
    assert response.json()["current_stage"] == "avatar_generation"
    assert received == {
        "run_id": "pipeline-review",
        "approved": True,
        "reviewer": "审核员",
        "note": "可进入下一步",
        "approved_text": "",
    }
