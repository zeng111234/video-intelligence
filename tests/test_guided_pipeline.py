"""傻瓜式单条生产流水线的持久化与确认回归测试。"""

from datetime import datetime

from src.models import PipelineRun, PipelineRunStatus, PipelineStage, PipelineStepResult, TaskStatus
from src.repositories.mock import MockRepository
from src.services.pipeline import PipelineService


def test_guided_candidate_run_only_queues_until_worker_processes_it():
    repository = MockRepository()
    service = PipelineService(repository, None, None, None, None)

    run = service.start_guided_run(
        source_type="candidate",
        keyword="测试候选",
        candidate_id="candidate-1",
        profile={"avatar_id": "avatar-1", "voice_id": "voice-1", "edit_template_id": "template-1"},
        rights_holder="测试公司",
        publish_enabled=False,
        publish_platforms=["douyin"],
        idempotency_key="guided-test-1",
    )

    stored = repository.get_pipeline_run(run.run_id)
    assert stored is not None
    assert stored.status == PipelineRunStatus.PENDING
    assert stored.candidate_video_id == "candidate-1"
    assert stored.config["workflow"] == "guided_candidate"
    assert stored.config["publish_enabled"] is False
    assert stored.config["publish_platforms"] == []
    assert stored.config["guided_idempotency_key"] == "guided-test-1"

    duplicate = service.start_guided_run(
        source_type="candidate",
        keyword="不会创建第二条",
        candidate_id="candidate-1",
        profile={"avatar_id": "avatar-1", "voice_id": "voice-1", "edit_template_id": "template-1"},
        rights_holder="测试公司",
        publish_enabled=False,
        publish_platforms=[],
        idempotency_key="guided-test-1",
    )
    assert duplicate.run_id == run.run_id


def test_guided_run_approval_automatically_advances_to_avatar():
    repository = MockRepository()
    service = PipelineService(repository, None, None, None, None)
    now = datetime.now().astimezone()
    run = PipelineRun(
        keyword="待确认文案",
        status=PipelineRunStatus.PAUSED,
        current_stage=PipelineStage.HUMAN_REVIEW,
        config={"workflow": "guided_share_link"},
        stages=[
            PipelineStepResult(
                stage=PipelineStage.HUMAN_REVIEW,
                status=TaskStatus.RUNNING,
                task_id="copy-1",
                started_at=now,
            )
        ],
    )
    repository.save_pipeline_run(run)

    updated = service.review_candidate_script(
        run_id=run.run_id,
        approved=True,
        reviewer="客户",
        approved_text="确认后的最终口播文案",
    )

    assert updated.status == PipelineRunStatus.PENDING
    assert updated.current_stage == PipelineStage.AVATAR_GENERATION
    assert updated.config["approved_script_text"] == "确认后的最终口播文案"
