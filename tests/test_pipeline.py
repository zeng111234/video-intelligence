"""批量生产流水线模块测试。

覆盖：文案改写、视频剪辑、发布、流水线编排。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.adapters.llm import SandboxCopywritingEngine
from src.adapters.publishers.sandbox import SandboxPublisher, PlatformPublisherAdapter
from src.adapters.video_editor import FFmpegVideoEditor, VideoEditorError
from src.models import (
    CopywritingTask,
    DataSource,
    EligibilityStatus,
    HeatLevel,
    HeatResult,
    MediaResolutionAttempt,
    MediaResolutionStatus,
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
    Platform,
    PublishPlatform,
    PublishStatus,
    PublishTask,
    PublishTarget,
    TaskKind,
    TaskStatus,
    TranscriptSegment,
    TranscriptionTask,
    VideoCandidate,
    VideoEditConfig,
    VideoEditStep,
    VideoEditStepKind,
    VideoMetricSnapshot,
)
from src.adapters.licensed import SandboxLicensedSearchProvider
from src.adapters.video_editor import SandboxVideoEditor
from src.repositories.mock import MockRepository
from src.services.commercial_search import CommercialSearchService
from src.services.copywriting import CopywritingService
from src.services.keyword_trend import KeywordTrendService
from src.services.pipeline import PipelineService
from src.services.publisher import PublishService
from src.services.source import SourceService
from src.services.heat import HeatService
from src.services.media_resolution import MediaResolutionError, ResolvedMedia
from src.services.video_source import DirectVideo
from src.services.video_editor import VideoEditingService


# ---------------------------------------------------------------------------
# 文案改写引擎测试
# ---------------------------------------------------------------------------


class TestSandboxCopywritingEngine:
    def test_capabilities(self):
        engine = SandboxCopywritingEngine()
        cap = engine.capabilities()
        assert cap["enabled"] is True
        assert cap["mode"] == "sandbox"

    def test_rewrite_single(self):
        engine = SandboxCopywritingEngine()
        results = engine.rewrite("这是一段测试文案，用于验证改写功能。")
        assert len(results) == 1
        assert "演示" in results[0]

    def test_rewrite_multiple_variants(self):
        engine = SandboxCopywritingEngine()
        results = engine.rewrite("测试文案", variant_count=3)
        assert len(results) == 3
        # 每个变体应该不同
        assert len(set(results)) > 1

    def test_rewrite_max_variants_capped(self):
        engine = SandboxCopywritingEngine()
        results = engine.rewrite("测试", variant_count=100)
        assert len(results) <= 3  # 沙箱最多 3 个变体


# ---------------------------------------------------------------------------
# 文案改写服务测试
# ---------------------------------------------------------------------------


class TestCopywritingService:
    def setup_method(self):
        self.repo = MockRepository()
        self.engine = SandboxCopywritingEngine()
        self.svc = CopywritingService(self.repo, self.engine)

    def test_rewrite_success(self):
        task = self.svc.rewrite(source_text="原始文案内容")
        assert isinstance(task, CopywritingTask)
        assert task.status == TaskStatus.SUCCEEDED
        assert task.result_text is not None
        assert task.kind == TaskKind.COPYWRITING

    def test_rewrite_empty_source_raises(self):
        with pytest.raises(ValueError, match="源文案不能为空"):
            self.svc.rewrite(source_text="")

    def test_rewrite_stores_in_repository(self):
        self.svc.rewrite(source_text="测试存储")
        tasks = self.svc.list_tasks()
        assert len(tasks) == 1
        assert isinstance(tasks[0], CopywritingTask)

    def test_batch_rewrite(self):
        tasks = self.svc.batch_rewrite(
            source_texts=["文案一", "文案二", "文案三"],
            style_prompt="口播",
        )
        assert len(tasks) == 3
        assert all(t.status == TaskStatus.SUCCEEDED for t in tasks)

    def test_variant_count(self):
        task = self.svc.rewrite(source_text="测试变体", variant_count=2)
        assert len(task.result_variants) == 2


# ---------------------------------------------------------------------------
# 发布适配器测试
# ---------------------------------------------------------------------------


class TestSandboxPublisher:
    def test_capabilities(self):
        pub = SandboxPublisher(PublishPlatform.DOUYIN)
        cap = pub.capabilities()
        assert cap["enabled"] is True
        assert cap["mode"] == "manual"

    def test_publish_returns_success(self):
        pub = SandboxPublisher(PublishPlatform.KUAISHOU)
        target = PublishTarget(
            platform=PublishPlatform.KUAISHOU,
            title="测试视频",
            description="测试描述",
        )
        task = pub.publish("/fake/video.mp4", target)
        assert task.status == TaskStatus.SUBMITTED
        assert task.publish_status == PublishStatus.MANUAL_READY
        assert task.platform_url is None
        assert task.platform_video_id is None
        assert task.is_mock is True

    def test_check_status(self):
        pub = SandboxPublisher(PublishPlatform.WECHAT_CHANNELS)
        target = PublishTarget(
            platform=PublishPlatform.WECHAT_CHANNELS,
            title="视频号测试",
        )
        task = pub.publish("/fake/video.mp4", target)
        status = pub.check_status(task.task_id)
        assert status == PublishStatus.MANUAL_READY

    def test_get_published_url(self):
        pub = SandboxPublisher()
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="URL测试")
        task = pub.publish("/fake/video.mp4", target)
        url = pub.get_published_url(task.task_id)
        assert url is None


class TestPlatformPublisherAdapter:
    def test_disabled_by_default(self):
        adapter = PlatformPublisherAdapter(PublishPlatform.DOUYIN)
        cap = adapter.capabilities()
        assert cap["enabled"] is False
        assert len(cap["missing_configuration"]) > 0

    def test_publish_raises_when_disabled(self):
        adapter = PlatformPublisherAdapter(PublishPlatform.DOUYIN)
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="测试")
        with pytest.raises(RuntimeError, match="尚未配置"):
            adapter.publish("/fake/video.mp4", target)


# ---------------------------------------------------------------------------
# 发布服务测试
# ---------------------------------------------------------------------------


class TestPublishService:
    def setup_method(self):
        self.repo = MockRepository()
        self.publishers = {
            "douyin": SandboxPublisher(PublishPlatform.DOUYIN),
            "kuaishou": SandboxPublisher(PublishPlatform.KUAISHOU),
            "wechat_channels": SandboxPublisher(PublishPlatform.WECHAT_CHANNELS),
        }
        self.svc = PublishService(self.repo, self.publishers)
        # 创建临时视频文件用于测试
        import tempfile

        self._temp_files: list[Path] = []
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.write(b"\x00" * 100)
        tmp.close()
        self._temp_video = tmp.name
        self._temp_files.append(Path(tmp.name))

    def teardown_method(self):
        for f in self._temp_files:
            f.unlink(missing_ok=True)

    def test_available_platforms(self):
        platforms = self.svc.available_platforms()
        assert len(platforms) == 3
        assert all(p["enabled"] for p in platforms)

    def test_publish_single_platform(self):
        target = PublishTarget(
            platform=PublishPlatform.DOUYIN,
            title="单平台发布测试",
        )
        task = self.svc.publish(video_path=self._temp_video, target=target)
        assert isinstance(task, PublishTask)
        assert task.status == TaskStatus.SUBMITTED

    def test_multi_platform_publish(self):
        targets = [
            PublishTarget(platform=PublishPlatform.DOUYIN, title="多平台测试-抖音"),
            PublishTarget(platform=PublishPlatform.KUAISHOU, title="多平台测试-快手"),
        ]
        tasks = self.svc.multi_platform_publish(
            video_path=self._temp_video,
            targets=targets,
        )
        assert len(tasks) == 2
        assert all(t.status == TaskStatus.SUBMITTED for t in tasks)

    def test_publish_nonexistent_video_real_mode(self):
        """人工发布包模式下可先记录不存在的本机路径。"""
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="测试")
        task = self.svc.publish(video_path="/nonexistent/video.mp4", target=target)
        assert task.status == TaskStatus.SUBMITTED

    def test_list_tasks(self):
        # 完全隔离的测试
        fresh_repo = MockRepository()
        fresh_publishers = {
            "douyin": SandboxPublisher(PublishPlatform.DOUYIN),
        }
        fresh_svc = PublishService(fresh_repo, fresh_publishers)
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="列表测试")
        fresh_svc.publish(video_path=self._temp_video, target=target)
        tasks = fresh_svc.list_tasks()
        assert len(tasks) == 1
        assert isinstance(tasks[0], PublishTask)


# ---------------------------------------------------------------------------
# 视频编辑器测试（FFmpeg 依赖检查）
# ---------------------------------------------------------------------------


class TestFFmpegVideoEditor:
    def setup_method(self):
        self.editor = FFmpegVideoEditor()

    def test_capabilities(self):
        cap = self.editor.capabilities()
        assert "supports_trim" in cap
        assert "supports_subtitle" in cap
        assert "supports_watermark" in cap
        assert "supports_speed" in cap

    def test_apply_edit_nonexistent_raises(self):
        config = VideoEditConfig()
        with pytest.raises(VideoEditorError, match="源视频文件不存在"):
            self.editor.apply_edit("/nonexistent/video.mp4", config)

    def test_add_subtitles_nonexistent_video_raises(self):
        with pytest.raises(VideoEditorError, match="视频文件不存在"):
            self.editor.add_subtitles("/nonexistent/video.mp4", "/fake.srt")

    def test_add_subtitles_nonexistent_srt_raises(self):
        # 创建一个假的视频文件
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"\x00" * 100)
            video_path = f.name
        try:
            with pytest.raises(VideoEditorError, match="字幕文件不存在"):
                self.editor.add_subtitles(video_path, "/nonexistent.srt")
        finally:
            Path(video_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 视频编辑服务测试
# ---------------------------------------------------------------------------


class TestVideoEditingService:
    def setup_method(self):
        self.repo = MockRepository()
        self.editor = FFmpegVideoEditor()
        self.svc = VideoEditingService(self.repo, self.editor)

    def test_edit_nonexistent_video_raises(self):
        config = VideoEditConfig()
        with pytest.raises(ValueError, match="源视频文件不存在"):
            self.svc.edit_video(
                source_video_path="/nonexistent.mp4", edit_config=config
            )

    def test_list_tasks_empty(self):
        tasks = self.svc.list_tasks()
        assert len(tasks) == 0


# ---------------------------------------------------------------------------
# 流水线服务测试
# ---------------------------------------------------------------------------


def _pipeline_candidate() -> VideoCandidate:
    now = datetime.now().astimezone()
    return VideoCandidate(
        video_id="candidate-pipeline-1",
        platform_item_id="aweme-real-1",
        title="测试爆款视频",
        author_id="author-1",
        author_name="测试作者",
        platform=Platform.DOUYIN,
        category="关键词/测试",
        published_at=now,
        source_type=DataSource.LICENSED_PROVIDER,
        eligibility_status=EligibilityStatus.AUTO_MATCHED,
        metrics=VideoMetricSnapshot(
            item_id="candidate-pipeline-1",
            sampled_at=now,
            likes=1000,
            confidence=0.8,
        ),
        heat=HeatResult(
            score=60,
            level=HeatLevel.INSUFFICIENT,
            confidence=0.5,
        ),
    )


class FakeMediaResolutionService:
    def __init__(self, error: MediaResolutionError | None = None) -> None:
        self.error = error
        self.attached_task_id: str | None = None

    def resolve_video(self, candidate: VideoCandidate, *, idempotency_key: str):
        if self.error:
            raise self.error
        attempt = MediaResolutionAttempt(
            idempotency_key=idempotency_key,
            candidate_id=candidate.video_id,
            platform=candidate.platform,
            platform_item_id=candidate.platform_item_id or "",
            provider="fixture_oneapi",
            status=MediaResolutionStatus.SUCCEEDED,
            estimated_cost_cny=0.04,
            billable_units=0.04,
            api_call_count=1,
        )
        return ResolvedMedia(
            attempt=attempt,
            video=DirectVideo(
                name="candidate.mp4",
                media_type="video/mp4",
                content=b"0000ftypmp42",
            ),
        )

    def attach_task(self, attempt: MediaResolutionAttempt, task: TranscriptionTask):
        self.attached_task_id = task.task_id
        return attempt.model_copy(update={"task_id": task.task_id})


class FakeTranscriptionService:
    def __init__(self, repository: MockRepository) -> None:
        self.repository = repository

    def create_task(self, **kwargs) -> TranscriptionTask:
        now = datetime.now().astimezone()
        task = TranscriptionTask(
            task_id="transcript-pipeline-1",
            title=kwargs["media_name"],
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            media_name=kwargs["media_name"],
            media_type=kwargs["media_type"],
            rights_confirmed=True,
            rights_holder=kwargs["rights_holder"],
            candidate_id=kwargs["candidate_id"],
            segments=[
                TranscriptSegment(
                    start=0,
                    end=3,
                    text="这个视频讲的是低成本获客的三个关键动作。",
                    confidence=0.9,
                )
            ],
            stage="待校对",
            model_name=kwargs["model_name"],
            duration_seconds=4.0,
            is_mock=False,
        )
        self.repository.save_task(task)
        return task


class TestPipelineService:
    def setup_method(self):
        self.repo = MockRepository()
        source_svc = SourceService(self.repo, HeatService())
        trend_svc = KeywordTrendService(self.repo)
        search_svc = CommercialSearchService(
            self.repo, source_svc, trend_svc, SandboxLicensedSearchProvider()
        )
        copy_svc = CopywritingService(self.repo, SandboxCopywritingEngine())
        edit_svc = VideoEditingService(self.repo, SandboxVideoEditor())
        pub_publishers = {
            "douyin": SandboxPublisher(PublishPlatform.DOUYIN),
            "kuaishou": SandboxPublisher(PublishPlatform.KUAISHOU),
            "wechat_channels": SandboxPublisher(PublishPlatform.WECHAT_CHANNELS),
        }
        pub_svc = PublishService(self.repo, pub_publishers)
        self.svc = PipelineService(self.repo, search_svc, copy_svc, edit_svc, pub_svc)

    def test_candidate_script_pipeline_pauses_after_copywriting_review(self):
        candidate = _pipeline_candidate()
        self.repo.save_candidate(candidate)
        media_svc = FakeMediaResolutionService()
        transcription_svc = FakeTranscriptionService(self.repo)
        self.svc.media_resolution_service = media_svc
        self.svc.transcription_service = transcription_svc

        run = self.svc.execute_candidate_script_pipeline(
            candidate_id=candidate.video_id,
            rights_confirmed=True,
            rights_holder="测试公司",
            idempotency_key="pipeline-candidate-1",
        )

        assert run.status == PipelineRunStatus.PAUSED
        assert run.current_stage == PipelineStage.HUMAN_REVIEW
        assert run.copywriting_task_id
        assert media_svc.attached_task_id == run.stages[1].task_id
        stage_status = {stage.stage: stage.status for stage in run.stages}
        assert stage_status[PipelineStage.MEDIA_RESOLUTION] == TaskStatus.SUCCEEDED
        assert stage_status[PipelineStage.TRANSCRIPTION] == TaskStatus.SUCCEEDED
        assert stage_status[PipelineStage.COPYWRITING] == TaskStatus.SUCCEEDED
        assert stage_status[PipelineStage.HUMAN_REVIEW] == TaskStatus.RUNNING
        stored = self.repo.get_pipeline_run(run.run_id)
        assert stored is not None
        assert stored.status == PipelineRunStatus.PAUSED
        assert [event.action for event in stored.events][-1] == "stage_updated"

    def test_review_approval_persists_decision_without_submitting_avatar(self):
        candidate = _pipeline_candidate()
        self.repo.save_candidate(candidate)
        self.svc.media_resolution_service = FakeMediaResolutionService()
        self.svc.transcription_service = FakeTranscriptionService(self.repo)
        run = self.svc.execute_candidate_script_pipeline(
            candidate_id=candidate.video_id,
            rights_confirmed=True,
            rights_holder="测试公司",
            idempotency_key="pipeline-review-approval",
        )

        approved = self.svc.review_candidate_script(
            run_id=run.run_id,
            approved=True,
            reviewer="审核员",
            note="事实与授权已核对。",
            approved_text="确认后的最终口播文案。",
        )

        assert approved.status == PipelineRunStatus.PENDING
        assert approved.current_stage == PipelineStage.AVATAR_GENERATION
        review_step = next(item for item in approved.stages if item.stage == PipelineStage.HUMAN_REVIEW)
        assert review_step.status == TaskStatus.SUCCEEDED
        assert review_step.outputs["reviewer"] == "审核员"
        assert approved.avatar_task_id is None
        assert approved.config["approved_script_text"] == "确认后的最终口播文案。"
        assert approved.events[-1].action == "review_approved"

    def test_retry_failed_candidate_pipeline_reuses_run_id_and_keeps_events(self):
        candidate = _pipeline_candidate()
        self.repo.save_candidate(candidate)
        self.svc.media_resolution_service = FakeMediaResolutionService(
            error=MediaResolutionError("供应商未返回可用于转写的视频。")
        )
        self.svc.transcription_service = FakeTranscriptionService(self.repo)
        failed = self.svc.execute_candidate_script_pipeline(
            candidate_id=candidate.video_id,
            rights_confirmed=True,
            rights_holder="测试公司",
            idempotency_key="pipeline-retry-initial",
        )

        self.svc.media_resolution_service = FakeMediaResolutionService()
        retried = self.svc.retry_candidate_script_pipeline(
            run_id=failed.run_id,
            idempotency_key="pipeline-retry-second",
        )

        assert retried.run_id == failed.run_id
        assert retried.status == PipelineRunStatus.PAUSED
        assert retried.current_stage == PipelineStage.HUMAN_REVIEW
        actions = [event.action for event in retried.events]
        assert "completed" in actions
        assert "retry_started" in actions

    def test_candidate_script_pipeline_records_media_resolution_failure(self):
        candidate = _pipeline_candidate()
        self.repo.save_candidate(candidate)
        media_svc = FakeMediaResolutionService(
            error=MediaResolutionError("供应商未返回可用于转写的视频。")
        )
        self.svc.media_resolution_service = media_svc
        self.svc.transcription_service = FakeTranscriptionService(self.repo)

        run = self.svc.execute_candidate_script_pipeline(
            candidate_id=candidate.video_id,
            rights_confirmed=True,
            rights_holder="测试公司",
            idempotency_key="pipeline-candidate-failed",
        )

        assert run.status == PipelineRunStatus.FAILED
        assert run.current_stage == PipelineStage.MEDIA_RESOLUTION
        assert "供应商未返回" in (run.error_message or "")

    def test_candidate_script_pipeline_blocks_non_douyin_before_paid_resolution(self):
        candidate = _pipeline_candidate().model_copy(
            update={"platform": Platform.XIAOHONGSHU}
        )
        self.repo.save_candidate(candidate)
        media_svc = FakeMediaResolutionService()
        self.svc.media_resolution_service = media_svc
        self.svc.transcription_service = FakeTranscriptionService(self.repo)

        with pytest.raises(RuntimeError, match="仅支持抖音"):
            self.svc.execute_candidate_script_pipeline(
                candidate_id=candidate.video_id,
                rights_confirmed=True,
                rights_holder="测试公司",
                idempotency_key="pipeline-xhs-blocked",
            )

        assert self.repo.list_pipeline_runs() == []

    def test_create_run(self):
        run = self.svc.create_run(keyword="二手车")
        assert run.keyword == "二手车"
        assert run.status == PipelineRunStatus.PENDING
        assert run.run_id.startswith("pipeline-")

    def test_create_run_with_config(self):
        run = self.svc.create_run(
            keyword="AI",
            config={"tone": "professional", "platforms": ["douyin"]},
        )
        assert run.config["tone"] == "professional"

    def test_update_stage(self):
        run = self.svc.create_run(keyword="测试")
        updated = self.svc.update_stage(
            run,
            PipelineStage.COPYWRITING,
            TaskStatus.SUCCEEDED,
            task_id="copy-123",
        )
        assert updated.status == PipelineRunStatus.RUNNING
        assert updated.current_stage == PipelineStage.COPYWRITING
        assert updated.copywriting_task_id == "copy-123"

    def test_update_stage_failure(self):
        run = self.svc.create_run(keyword="测试")
        updated = self.svc.update_stage(
            run,
            PipelineStage.AVATAR_GENERATION,
            TaskStatus.FAILED,
            error_message="生成失败",
        )
        assert updated.status == PipelineRunStatus.FAILED
        assert updated.error_message == "生成失败"

    def test_resolve_source_video_requires_authorized_file(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="没有已授权源视频"):
            PipelineService._resolve_source_video({})

        missing = tmp_path / "missing.mp4"
        with pytest.raises(RuntimeError, match="源视频不存在或不可读取"):
            PipelineService._resolve_source_video({"source_video_path": str(missing)})

        source = tmp_path / "source.mp4"
        source.write_bytes(b"fake-video")
        assert PipelineService._resolve_source_video(
            {"source_video_path": str(source)}
        ) == str(source)

    def test_complete_run_success(self):
        run = self.svc.create_run(keyword="完成测试")
        completed = self.svc.complete_run(run, success=True)
        assert completed.status == PipelineRunStatus.SUCCEEDED
        assert completed.finished_at is not None

    def test_complete_run_failure(self):
        run = self.svc.create_run(keyword="失败测试")
        completed = self.svc.complete_run(run, success=False, error_message="整体失败")
        assert completed.status == PipelineRunStatus.FAILED

    def test_get_run(self):
        run = self.svc.create_run(keyword="获取测试")
        fetched = self.svc.get_run(run.run_id)
        assert fetched is not None
        assert fetched.keyword == "获取测试"

    def test_get_nonexistent_run(self):
        result = self.svc.get_run("nonexistent-id")
        assert result is None

    def test_list_runs(self):
        for kw in ["A", "B", "C"]:
            self.svc.create_run(keyword=kw)
        runs = self.svc.list_runs(limit=2)
        assert len(runs) == 2

    def test_build_publish_targets(self):
        targets = PipelineService.build_publish_targets(
            title="测试",
            description="描述",
            tags=["tag1"],
        )
        assert len(targets) == 3
        platforms = {t.platform for t in targets}
        assert PublishPlatform.DOUYIN in platforms
        assert PublishPlatform.KUAISHOU in platforms
        assert PublishPlatform.WECHAT_CHANNELS in platforms

    def test_build_publish_targets_custom_platforms(self):
        targets = PipelineService.build_publish_targets(
            title="自定义",
            platforms=[PublishPlatform.DOUYIN],
        )
        assert len(targets) == 1
        assert targets[0].platform == PublishPlatform.DOUYIN

    def test_publish_task_ids_accumulate(self):
        run = self.svc.create_run(keyword="发布测试")
        run = self.svc.update_stage(
            run, PipelineStage.PUBLISHING, TaskStatus.RUNNING, task_id="pub-1"
        )
        run = self.svc.update_stage(
            run, PipelineStage.PUBLISHING, TaskStatus.RUNNING, task_id="pub-2"
        )
        assert "pub-1" in run.publish_task_ids
        assert "pub-2" in run.publish_task_ids


# ---------------------------------------------------------------------------
# MockRepository 流水线方法测试
# ---------------------------------------------------------------------------


class TestMockRepositoryPipeline:
    def test_save_and_get_pipeline_run(self):
        repo = MockRepository()
        run = PipelineRun(keyword="测试")
        repo.save_pipeline_run(run)
        fetched = repo.get_pipeline_run(run.run_id)
        assert fetched is not None
        assert fetched.keyword == "测试"

    def test_get_nonexistent_pipeline_run(self):
        repo = MockRepository()
        assert repo.get_pipeline_run("nonexistent") is None

    def test_list_pipeline_runs(self):
        repo = MockRepository()
        for i in range(5):
            repo.save_pipeline_run(PipelineRun(keyword=f"KW-{i}"))
        runs = repo.list_pipeline_runs(limit=3)
        assert len(runs) == 3


# ---------------------------------------------------------------------------
# 模型测试
# ---------------------------------------------------------------------------


class TestNewModels:
    def test_video_edit_step(self):
        step = VideoEditStep(
            kind=VideoEditStepKind.TRIM,
            params={"start": 10, "duration": 30},
            order=0,
        )
        assert step.kind == VideoEditStepKind.TRIM
        assert step.params["start"] == 10

    def test_video_edit_config(self):
        config = VideoEditConfig(
            steps=[
                VideoEditStep(kind=VideoEditStepKind.TRIM, params={"start": 5}),
                VideoEditStep(kind=VideoEditStepKind.SPEED, params={"speed": 1.5}),
            ],
            output_resolution="720x1280",
        )
        assert len(config.steps) == 2
        assert config.output_resolution == "720x1280"

    def test_publish_target(self):
        target = PublishTarget(
            platform=PublishPlatform.DOUYIN,
            title="测试标题",
            description="描述",
            tags=["tag1", "tag2"],
        )
        assert target.platform == PublishPlatform.DOUYIN
        assert len(target.tags) == 2

    def test_pipeline_run(self):
        run = PipelineRun(keyword="二手车")
        assert run.status == PipelineRunStatus.PENDING
        assert run.run_id.startswith("pipeline-")
        assert len(run.stages) == 0

    def test_copywriting_task(self):
        task = CopywritingTask(
            task_id="copy-1",
            title="测试",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=datetime.now().astimezone(),
            updated_at=datetime.now().astimezone(),
            source_text="原文",
            result_text="改写结果",
        )
        assert task.kind == TaskKind.COPYWRITING
        assert task.result_text == "改写结果"
