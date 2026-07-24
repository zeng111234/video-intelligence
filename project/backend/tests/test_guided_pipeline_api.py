"""引导式流水线接口的关键授权边界。"""

from fastapi.testclient import TestClient

from project.backend.app.core import deps as backend_deps
from project.backend.app.main import app
from src.models import PipelineRun, PipelineRunStatus, PipelineStage, PipelineStepResult, TaskStatus


def test_guided_pipeline_requires_explicit_final_script_before_approval():
    run = PipelineRun(
        run_id="guided-review",
        keyword="抖音分享视频",
        status=PipelineRunStatus.PAUSED,
        current_stage=PipelineStage.HUMAN_REVIEW,
        config={"workflow": "guided_share_link"},
        stages=[PipelineStepResult(stage=PipelineStage.HUMAN_REVIEW, status=TaskStatus.RUNNING)],
    )
    service = type("Service", (), {"get_run": lambda self, run_id: run if run_id == run.run_id else None})()
    app.dependency_overrides[backend_deps.get_pipeline_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/pipelines/guided-review/review",
                json={"approved": True, "reviewer": "客户", "approved_text": ""},
            )
    finally:
        app.dependency_overrides.pop(backend_deps.get_pipeline_service, None)

    assert response.status_code == 400
    assert "最终口播文案" in response.json()["message"]
