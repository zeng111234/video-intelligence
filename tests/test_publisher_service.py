"""PublishService 独立测试。

覆盖：未知平台、异常传播、get_task 类型过滤、on_progress 回调。
"""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from src.adapters.publishers.sandbox import SandboxPublisher
from src.models import (
    PublishPlatform,
    PublishStatus,
    PublishTarget,
    PublishTask,
    TaskKind,
    TaskStatus,
)
from src.repositories.mock import MockRepository
from src.services.publisher import PublishService


class _FailingPublisher:
    """模拟发布失败的 publisher。"""

    def platform(self) -> str:
        return "failing"

    def capabilities(self) -> dict[str, str | bool]:
        return {"enabled": True, "mode": "sandbox", "provider_name": "failing"}

    def publish(self, video_path, target):
        raise RuntimeError("发布模拟故障")

    def check_status(self, task_id):
        return PublishStatus.FAILED

    def get_published_url(self, task_id):
        return None


class TestPublishServiceEdgeCases:
    """补充 PublishService 边界和异常路径。"""

    def setup_method(self):
        self.repo = MockRepository()
        self.publishers: dict[str, SandboxPublisher] = {
            "douyin": SandboxPublisher(PublishPlatform.DOUYIN),
            "kuaishou": SandboxPublisher(PublishPlatform.KUAISHOU),
            "wechat_channels": SandboxPublisher(PublishPlatform.WECHAT_CHANNELS),
        }
        self.svc = PublishService(self.repo, self.publishers)
        # 创建临时视频文件
        self._temp_dir = tempfile.mkdtemp()
        self._temp_video = Path(self._temp_dir) / "test_video.mp4"
        self._temp_video.write_bytes(b"\x00" * 100)

    def teardown_method(self):
        self._temp_video.unlink(missing_ok=True)
        Path(self._temp_dir).rmdir()

    def test_publish_unknown_platform_raises(self):
        """不存在的平台应抛出 ValueError。"""
        target = PublishTarget(
            platform=PublishPlatform.DOUYIN,
            title="未知平台测试",
        )
        svc = PublishService(self.repo, {})  # 空 publishers
        with pytest.raises(ValueError, match="未找到"):
            svc.publish(video_path=str(self._temp_video), target=target)

    def test_publish_engine_exception_produces_failed_task(self):
        """publisher 异常应产生 FAILED 任务。"""
        svc = PublishService(self.repo, {"douyin": _FailingPublisher()})
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="异常测试")
        task = svc.publish(video_path=str(self._temp_video), target=target)
        assert task.status == TaskStatus.FAILED
        assert "发布模拟故障" in task.error_message

    def test_publish_with_source_pipeline_run_id(self):
        """source_pipeline_run_id 应正确存储。"""
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="流水线测试")
        task = self.svc.publish(
            video_path=str(self._temp_video),
            target=target,
            source_pipeline_run_id="pipeline-run-abc",
        )
        assert task.source_pipeline_run_id == "pipeline-run-abc"

    def test_publish_on_progress_called(self):
        """on_progress 回调应被调用。"""
        calls: list[PublishTask] = []
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="回调测试")
        self.svc.publish(
            video_path=str(self._temp_video),
            target=target,
            on_progress=lambda t: calls.append(t),
        )
        assert len(calls) >= 2

    def test_publish_on_progress_exception_swallowed(self):
        """on_progress 异常不应影响主流程。"""

        def bad_callback(t):
            raise RuntimeError("回调异常")

        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="异常回调测试")
        task = self.svc.publish(
            video_path=str(self._temp_video),
            target=target,
            on_progress=bad_callback,
        )
        assert task.status == TaskStatus.SUCCEEDED

    def test_get_task_returns_publish_task(self):
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="检索测试")
        task = self.svc.publish(video_path=str(self._temp_video), target=target)
        fetched = self.svc.get_task(task.task_id)
        assert fetched is not None
        assert isinstance(fetched, PublishTask)
        assert fetched.task_id == task.task_id

    def test_get_nonexistent_task_returns_none(self):
        assert self.svc.get_task("nonexistent") is None

    def test_list_tasks_excludes_other_types(self):
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="类型过滤")
        self.svc.publish(video_path=str(self._temp_video), target=target)
        tasks = self.svc.list_tasks()
        assert all(isinstance(t, PublishTask) for t in tasks)

    def test_task_kind_is_publishing(self):
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="kind 测试")
        task = self.svc.publish(video_path=str(self._temp_video), target=target)
        assert task.kind == TaskKind.PUBLISHING

    def test_available_platforms_count(self):
        platforms = self.svc.available_platforms()
        assert len(platforms) == 3
        assert all(p["enabled"] for p in platforms)

    def test_available_platforms_empty(self):
        svc = PublishService(self.repo, {})
        platforms = svc.available_platforms()
        assert len(platforms) == 0

    def test_multi_platform_publish_independent(self):
        """多平台发布应独立执行，一个失败不影响另一个。"""
        mixed_publishers = {
            "douyin": SandboxPublisher(PublishPlatform.DOUYIN),
            "kuaishou": _FailingPublisher(),
        }
        svc = PublishService(self.repo, mixed_publishers)
        targets = [
            PublishTarget(platform=PublishPlatform.DOUYIN, title="成功"),
            PublishTarget(platform=PublishPlatform.KUAISHOU, title="失败"),
        ]
        tasks = svc.multi_platform_publish(
            video_path=str(self._temp_video),
            targets=targets,
        )
        assert len(tasks) == 2
        assert tasks[0].status == TaskStatus.SUCCEEDED
        assert tasks[1].status == TaskStatus.FAILED

    def test_publish_sandbox_skips_file_check(self):
        """沙箱模式下不存在的文件应成功（跳过文件检查）。"""
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="沙箱跳过检查")
        task = self.svc.publish(video_path="/nonexistent/video.mp4", target=target)
        assert task.status == TaskStatus.SUCCEEDED
