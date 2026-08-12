"""关键词自动流水线 worker 的持久化入队测试。"""

from datetime import datetime
from types import SimpleNamespace

from src.models import (
    AvatarProviderStatus,
    AvatarTask,
    CopywritingTask,
    DataSource,
    HeatLevel,
    HeatResult,
    PipelineRunStatus,
    PipelineStage,
    Platform,
    TaskStatus,
    VideoCandidate,
    VideoMetricSnapshot,
)
from src.repositories.mock import MockRepository
from src.services.pipeline import PipelineService
from src.services.pipeline_worker import PipelineWorker


def _candidate() -> VideoCandidate:
    now = datetime.now().astimezone()
    return VideoCandidate(
        video_id="worker-candidate",
        platform_item_id="worker-item",
        title="AI 获客案例",
        author_id="author",
        author_name="作者",
        platform=Platform.DOUYIN,
        category="企业服务",
        published_at=now,
        source_type=DataSource.LICENSED_PROVIDER,
        matched_by=["AI"],
        metrics=VideoMetricSnapshot(
            item_id="worker-candidate", sampled_at=now, likes=100, confidence=0.8
        ),
        heat=HeatResult(score=80, level=HeatLevel.INSUFFICIENT, confidence=0.7),
    )


def test_keyword_master_selects_candidate_and_creates_persisted_child_run():
    repository = MockRepository()
    repository.save_candidate(_candidate())
    pipeline_service = PipelineService(repository, None, None, None, None)
    master = pipeline_service.start_keyword_auto_run(
        keyword="AI",
        candidate_count=1,
        profile={"avatar_id": "a", "voice_id": "v", "edit_template_id": "t"},
        rights_holder="测试公司",
        publish_platforms=["douyin"],
    )
    worker = PipelineWorker(
        repository=repository,
        pipeline_service=pipeline_service,
        commercial_search_service=SimpleNamespace(
            execute=lambda **_: SimpleNamespace(batch_id="search-worker")
        ),
        avatar_service=None,
        video_editing_service=None,
        publish_service=None,
        template_service=None,
    )
    worker.tick_once()
    runs = repository.list_pipeline_runs(limit=10)
    child = next(item for item in runs if item.run_id != master.run_id)
    finished_master = repository.get_pipeline_run(master.run_id)
    assert finished_master is not None and finished_master.status.value == "succeeded"
    assert child.config["workflow"] == "keyword_auto_candidate"
    assert child.candidate_video_id == "worker-candidate"
    assert child.status.value == "pending"


def test_pipeline_worker_keeps_pending_run_untouched_until_owner_session_is_ready():
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    run = pipeline_service.start_keyword_auto_run(
        keyword="AI",
        candidate_count=1,
        profile={"avatar_id": "a", "voice_id": "v", "edit_template_id": "t"},
        rights_holder="测试公司",
        publish_platforms=["douyin"],
    )
    searched: list[str] = []
    worker = PipelineWorker(
        repository=repository,
        pipeline_service=pipeline_service,
        commercial_search_service=SimpleNamespace(
            execute=lambda **_: searched.append("called")
        ),
        avatar_service=None,
        video_editing_service=None,
        publish_service=None,
        template_service=None,
        can_process=lambda: False,
    )

    worker.tick_once()

    unchanged = repository.get_pipeline_run(run.run_id)
    assert unchanged is not None
    assert unchanged.status == PipelineRunStatus.PENDING
    assert searched == []


def test_synchronously_completed_avatar_continues_without_waiting_for_next_tick():
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    now = datetime.now().astimezone()
    copy_task = CopywritingTask(
        task_id="copy-approved",
        title="审核稿",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        result_text="这是最终口播文案。",
    )
    repository.save_task(copy_task)
    run = pipeline_service.start_keyword_auto_run(
        keyword="AI",
        candidate_count=1,
        profile={
            "avatar_id": "avatar-a",
            "voice_id": "voice-a",
            "edit_template_id": "template-a",
        },
        rights_holder="测试公司",
        publish_platforms=["douyin"],
    ).model_copy(
        update={
            "config": {
                "workflow": "keyword_auto_candidate",
                "profile": {
                    "avatar_id": "avatar-a",
                    "voice_id": "voice-a",
                    "edit_template_id": "template-a",
                },
                "rights_holder": "测试公司",
                "stage_retry_counts": {"avatar_generation": 1},
            },
            "status": PipelineRunStatus.PENDING,
            "current_stage": PipelineStage.AVATAR_GENERATION,
            "copywriting_task_id": copy_task.task_id,
        }
    )
    repository.save_pipeline_run(run)
    submitted_requests = []
    avatar_service = SimpleNamespace(
        list_assets=lambda: [
            SimpleNamespace(asset_id="avatar-a", name="形象"),
            SimpleNamespace(asset_id="voice-a", name="音色"),
        ],
        submit=lambda request, **_: (
            submitted_requests.append(request)
            or SimpleNamespace(
                status=TaskStatus.SUCCEEDED,
                task_id="avatar-sync",
                provider_name="synchronous-provider",
                error_message=None,
            )
        ),
    )
    worker = PipelineWorker(
        repository=repository,
        pipeline_service=pipeline_service,
        commercial_search_service=None,
        avatar_service=avatar_service,
        video_editing_service=None,
        publish_service=None,
        template_service=None,
    )
    continued = []
    worker._poll_avatar_and_continue = lambda updated: continued.append(updated)  # type: ignore[method-assign]

    worker._submit_avatar(run)

    assert len(continued) == 1
    assert continued[0].avatar_task_id == "avatar-sync"
    assert continued[0].current_stage == PipelineStage.AVATAR_GENERATION
    assert submitted_requests[0].background == "solid"
    assert submitted_requests[0].idempotency_key.endswith("-retry-1")


def test_production_edit_uses_current_smart_template_and_never_legacy_editor(tmp_path):
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    now = datetime.now().astimezone()
    copy_task = CopywritingTask(
        task_id="copy-smart-template",
        title="审核稿",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        result_text="你发现没，餐饮获客真正难的不是流量。",
    )
    repository.save_task(copy_task)
    avatar_path = tmp_path / "avatar.mp4"
    avatar_path.write_bytes(b"real-avatar")
    avatar_task = AvatarTask(
        task_id="avatar-smart-template",
        title="数字人成片",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        script_text=copy_task.result_text or "",
        avatar_id="avatar-1",
        avatar_name="大树1",
        voice_id="voice-1",
        voice_name="大树1",
        rights_holder="测试用户",
        rights_confirmed_at=now,
        idempotency_key="avatar-smart-template-key",
        provider_name="production",
        provider_status=AvatarProviderStatus.SUCCEEDED,
        result_path=str(avatar_path),
    )
    repository.save_task(avatar_task)
    run = pipeline_service.create_run(
        keyword="餐饮获客",
        config={
            "workflow": "production_batch_candidate",
            "approved_script_text": copy_task.result_text,
            "publish_enabled": False,
            "profile": {"tags": ["餐饮获客"]},
        },
    ).model_copy(
        update={
            "status": PipelineRunStatus.RUNNING,
            "current_stage": PipelineStage.VIDEO_EDITING,
            "copywriting_task_id": copy_task.task_id,
            "avatar_task_id": avatar_task.task_id,
        }
    )
    repository.save_pipeline_run(run)
    legacy_calls: list[str] = []
    rendered = SimpleNamespace(
        status=TaskStatus.SUCCEEDED,
        task_id="edit-smart-template",
        result_path=str(tmp_path / "smart.mp4"),
        error_message=None,
    )
    smart_calls: list[dict[str, object]] = []
    worker = PipelineWorker(
        repository=repository,
        pipeline_service=pipeline_service,
        commercial_search_service=None,
        avatar_service=None,
        video_editing_service=SimpleNamespace(
            edit_video=lambda **_: legacy_calls.append("legacy")
        ),
        publish_service=None,
        template_service=None,
        video_editor_workflow_service=SimpleNamespace(
            render_production_export=lambda **kwargs: (
                smart_calls.append(kwargs) or rendered
            )
        ),
    )

    worker._edit_and_package(run, avatar_task)

    assert legacy_calls == []
    assert len(smart_calls) == 1
    assert smart_calls[0]["avatar_task"] == avatar_task
    assert smart_calls[0]["script_text"] == copy_task.result_text
    stored = repository.get_pipeline_run(run.run_id)
    assert stored is not None
    assert stored.status == PipelineRunStatus.SUCCEEDED
