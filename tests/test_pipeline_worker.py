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
    TranscriptSegment,
    TranscriptionTask,
    VideoCandidate,
    VideoMetricSnapshot,
)
from src.adapters.avatar import AvatarProviderError
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


def test_pipeline_worker_explains_pending_queue_when_login_is_required():
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    run = pipeline_service.start_keyword_auto_run(
        keyword="AI",
        candidate_count=1,
        profile={"avatar_id": "a", "voice_id": "v", "edit_template_id": "t"},
        rights_holder="测试公司",
        publish_platforms=["douyin"],
    )
    worker = PipelineWorker(
        repository=repository,
        pipeline_service=pipeline_service,
        commercial_search_service=None,
        avatar_service=None,
        video_editing_service=None,
        publish_service=None,
        template_service=None,
        can_process=lambda: False,
        processing_block_reason=lambda: "等待已绑定客户登录后继续制作。",
    )

    worker.tick_once()

    waiting = repository.get_pipeline_run(run.run_id)
    assert waiting is not None
    assert waiting.status == PipelineRunStatus.PENDING
    assert waiting.config["processing_wait_reason"] == "等待已绑定客户登录后继续制作。"
    assert waiting.events[-1].action == "processing_waiting_for_authorization"


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
                    "speech_rate": 1.1,
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
    assert submitted_requests[0].speech_rate == 1.1
    assert submitted_requests[0].idempotency_key.endswith("-retry-1")


def test_avatar_session_loss_pauses_before_supplier_submission():
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    now = datetime.now().astimezone()
    copy_task = CopywritingTask(
        task_id="copy-avatar-session-loss",
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
        profile={"avatar_id": "avatar-a", "voice_id": "voice-a"},
        rights_holder="测试公司",
        publish_platforms=["douyin"],
    ).model_copy(
        update={
            "config": {
                "workflow": "keyword_auto_candidate",
                "profile": {"avatar_id": "avatar-a", "voice_id": "voice-a"},
                "rights_holder": "测试公司",
            },
            "status": PipelineRunStatus.PENDING,
            "current_stage": PipelineStage.AVATAR_GENERATION,
            "copywriting_task_id": copy_task.task_id,
        }
    )
    repository.save_pipeline_run(run)
    submitted = []
    avatar_service = SimpleNamespace(
        list_assets=lambda: [
            SimpleNamespace(asset_id="avatar-a", name="形象"),
            SimpleNamespace(asset_id="voice-a", name="音色"),
        ],
        submit=lambda *_args, **_kwargs: submitted.append(True),
    )
    worker = PipelineWorker(
        repository=repository,
        pipeline_service=pipeline_service,
        commercial_search_service=None,
        avatar_service=avatar_service,
        video_editing_service=None,
        publish_service=None,
        template_service=None,
        can_process=lambda: False,
    )

    worker._submit_avatar(run)

    paused = repository.get_pipeline_run(run.run_id)
    assert paused is not None
    assert paused.status == PipelineRunStatus.PAUSED
    assert paused.current_stage == PipelineStage.AVATAR_GENERATION
    assert paused.config["recovery_reason"] == "avatar_submission_not_started"
    assert "未提交供应商任务" in paused.config["manual_action_required"]
    assert submitted == []


def test_avatar_asset_authorization_failure_pauses_before_supplier_submission():
    repository = MockRepository()
    pipeline_service = PipelineService(repository, None, None, None, None)
    now = datetime.now().astimezone()
    copy_task = CopywritingTask(
        task_id="copy-avatar-assets-unavailable",
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
        profile={"avatar_id": "avatar-a", "voice_id": "voice-a"},
        rights_holder="测试公司",
        publish_platforms=["douyin"],
    ).model_copy(
        update={
            "config": {
                "workflow": "keyword_auto_candidate",
                "profile": {"avatar_id": "avatar-a", "voice_id": "voice-a"},
                "rights_holder": "测试公司",
            },
            "status": PipelineRunStatus.PENDING,
            "current_stage": PipelineStage.AVATAR_GENERATION,
            "copywriting_task_id": copy_task.task_id,
        }
    )
    repository.save_pipeline_run(run)
    avatar_service = SimpleNamespace(
        list_assets=lambda: (_ for _ in ()).throw(
            AvatarProviderError(
                "请先登录客户账号或管理员账号，再使用数字人。"
            )
        )
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

    worker._submit_avatar(run)

    paused = repository.get_pipeline_run(run.run_id)
    assert paused is not None
    assert paused.status == PipelineRunStatus.PAUSED
    assert paused.error_message == "请先登录客户账号或管理员账号，再使用数字人。"


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

    subtitle_segments = [
        {"start": 0.8, "end": 3.2, "text": copy_task.result_text or ""}
    ]
    worker._edit_and_package(
        run,
        avatar_task,
        subtitle_segments=subtitle_segments,
    )

    assert legacy_calls == []
    assert len(smart_calls) == 1
    assert smart_calls[0]["avatar_task"] == avatar_task
    assert smart_calls[0]["script_text"] == copy_task.result_text
    assert smart_calls[0]["subtitle_segments"] == subtitle_segments
    assert smart_calls[0]["subtitle_task_id"] is None
    stored = repository.get_pipeline_run(run.run_id)
    assert stored is not None
    assert stored.status == PipelineRunStatus.SUCCEEDED


def test_avatar_success_reuses_persisted_asr_timing_before_smart_render(tmp_path):
    repository = MockRepository()
    transcription_service = SimpleNamespace()
    pipeline_service = PipelineService(
        repository,
        None,
        None,
        None,
        None,
        transcription_service=transcription_service,
    )
    now = datetime.now().astimezone()
    script = "餐饮门店想做同城获客"
    copy_task = CopywritingTask(
        task_id="copy-aligned-avatar",
        title="文案",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        result_text=script,
    )
    repository.save_task(copy_task)
    avatar_path = tmp_path / "avatar.mp4"
    avatar_path.write_bytes(b"avatar")
    avatar_task = AvatarTask(
        task_id="avatar-aligned",
        title="数字人",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        script_text=script,
        avatar_id="avatar-1",
        avatar_name="大树1",
        voice_id="voice-1",
        voice_name="大树1",
        rights_holder="测试用户",
        rights_confirmed_at=now,
        idempotency_key="avatar-aligned-key",
        provider_name="production",
        provider_status=AvatarProviderStatus.SUCCEEDED,
        result_path=str(avatar_path),
    )
    repository.save_task(avatar_task)
    caption_task = TranscriptionTask(
        task_id="transcript-avatar-aligned",
        title=avatar_path.name,
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        media_name=avatar_path.name,
        media_type="video/mp4",
        rights_confirmed=True,
        rights_holder="测试用户",
        candidate_id=avatar_task.task_id,
        source_kind="avatar_caption_alignment",
        segments=[TranscriptSegment(start=0.8, end=3.2, text=script)],
    )
    repository.save_task(caption_task)
    run = pipeline_service.create_run(
        keyword="餐饮获客",
        config={
            "workflow": "production_batch_candidate",
            "approved_script_text": script,
            "publish_enabled": False,
            "profile": {"tags": ["餐饮获客"]},
        },
    ).model_copy(
        update={
            "status": PipelineRunStatus.RUNNING,
            "current_stage": PipelineStage.AVATAR_GENERATION,
            "copywriting_task_id": copy_task.task_id,
            "avatar_task_id": avatar_task.task_id,
        }
    )
    repository.save_pipeline_run(run)
    rendered_path = tmp_path / "aligned.mp4"
    rendered_path.write_bytes(b"aligned")
    rendered = SimpleNamespace(
        status=TaskStatus.SUCCEEDED,
        task_id="edit-aligned",
        result_path=str(rendered_path),
        error_message=None,
    )
    smart_calls: list[dict[str, object]] = []
    workflow = SimpleNamespace(
        approved_script_segments_from_asr=lambda _approved, segments: segments,
        render_production_export=lambda **kwargs: (
            smart_calls.append(kwargs) or rendered
        ),
    )
    worker = PipelineWorker(
        repository=repository,
        pipeline_service=pipeline_service,
        commercial_search_service=None,
        avatar_service=SimpleNamespace(
            refresh_task=lambda _task_id: avatar_task,
            download_result=lambda _task_id: avatar_task,
        ),
        video_editing_service=None,
        publish_service=None,
        template_service=None,
        video_editor_workflow_service=workflow,
    )

    worker._poll_avatar_and_continue(run)

    assert len(smart_calls) == 1
    submitted_segments = smart_calls[0]["subtitle_segments"]
    assert isinstance(submitted_segments, list)
    assert submitted_segments[0]["start"] == 0.8
    assert submitted_segments[0]["end"] == 3.2
    assert submitted_segments[0]["text"] == script
    assert smart_calls[0]["subtitle_task_id"] == caption_task.task_id
    stored = repository.get_pipeline_run(run.run_id)
    assert stored is not None
    assert stored.status == PipelineRunStatus.SUCCEEDED
