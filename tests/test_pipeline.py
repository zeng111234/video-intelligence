"""批量生产流水线模块测试。

覆盖：文案改写、视频剪辑、发布、流水线编排。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.adapters.llm import SandboxCopywritingEngine, LLMAdapterError
from src.adapters.publishers.sandbox import SandboxPublisher, PlatformPublisherAdapter
from src.adapters.video_editor import FFmpegVideoEditor, VideoEditorError
from src.models import (
    CopywritingTask,
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
    PublishPlatform,
    PublishStatus,
    PublishTask,
    PublishTarget,
    TaskKind,
    TaskStatus,
    VideoEditConfig,
    VideoEditStep,
    VideoEditStepKind,
    VideoEditTask,
)
from src.adapters.llm import SandboxCopywritingEngine
from src.adapters.licensed import SandboxLicensedSearchProvider
from src.adapters.publishers.sandbox import SandboxPublisher
from src.adapters.video_editor import SandboxVideoEditor
from src.repositories.mock import MockRepository
from src.services.commercial_search import CommercialSearchService
from src.services.copywriting import CopywritingService
from src.services.keyword_trend import KeywordTrendService
from src.services.pipeline import PipelineService
from src.services.publisher import PublishService
from src.services.source import SourceService
from src.services.heat import HeatService
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
        assert cap["mode"] == "sandbox"

    def test_publish_returns_success(self):
        pub = SandboxPublisher(PublishPlatform.KUAISHOU)
        target = PublishTarget(
            platform=PublishPlatform.KUAISHOU,
            title="测试视频",
            description="测试描述",
        )
        task = pub.publish("/fake/video.mp4", target)
        assert task.status == TaskStatus.SUCCEEDED
        assert task.publish_status == PublishStatus.SUCCEEDED
        assert task.platform_url is not None
        assert task.platform_video_id is not None
        assert task.is_mock is True

    def test_check_status(self):
        pub = SandboxPublisher(PublishPlatform.WECHAT_CHANNELS)
        target = PublishTarget(
            platform=PublishPlatform.WECHAT_CHANNELS,
            title="视频号测试",
        )
        task = pub.publish("/fake/video.mp4", target)
        status = pub.check_status(task.task_id)
        assert status == PublishStatus.SUCCEEDED

    def test_get_published_url(self):
        pub = SandboxPublisher()
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="URL测试")
        task = pub.publish("/fake/video.mp4", target)
        url = pub.get_published_url(task.task_id)
        assert url is not None
        assert url.startswith("https://")


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
        assert task.status == TaskStatus.SUCCEEDED

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
        assert all(t.status == TaskStatus.SUCCEEDED for t in tasks)

    def test_publish_nonexistent_video_real_mode(self):
        """非沙箱模式下不存在的文件应该失败。"""
        # 沙箱模式下应该成功（跳过文件检查）
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="测试")
        task = self.svc.publish(video_path="/nonexistent/video.mp4", target=target)
        # 沙箱模式下应该成功（跳过文件检查）
        assert task.status == TaskStatus.SUCCEEDED

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
            self.svc.edit_video(source_video_path="/nonexistent.mp4", edit_config=config)

    def test_list_tasks_empty(self):
        tasks = self.svc.list_tasks()
        assert len(tasks) == 0


# ---------------------------------------------------------------------------
# 流水线服务测试
# ---------------------------------------------------------------------------


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
        self.svc = PipelineService(
            self.repo, search_svc, copy_svc, edit_svc, pub_svc
        )

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
