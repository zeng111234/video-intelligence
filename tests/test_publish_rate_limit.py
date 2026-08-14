"""发布防封闸门测试：每日条数上限 + 条间最小间隔。

验证 execute_queued_task 对同一平台账号的发布频率保护：
- 当日已发布达到 PUBLISH_DAILY_LIMIT 后，新任务明确失败并提示
- 距最近一次发布不足 PUBLISH_MIN_INTERVAL_MINUTES 时，任务保持排队自动等待
- 间隔满足后正常执行
- 不同平台互不影响
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.adapters.publishers.sandbox import SandboxPublisher
from src.models import PublishPlatform, PublishTarget
from src.repositories.mock import MockRepository
from src.services import publisher as publisher_module
from src.services.publisher import PublishService


class TestPublishRateLimit:
    def setup_method(self):
        self.repo = MockRepository()
        self.publishers = {
            "douyin": SandboxPublisher(PublishPlatform.DOUYIN),
            "kuaishou": SandboxPublisher(PublishPlatform.KUAISHOU),
        }
        self.svc = PublishService(self.repo, self.publishers)
        self._temp_dir = tempfile.mkdtemp()
        self._temp_video = Path(self._temp_dir) / "test_video.mp4"
        self._temp_video.write_bytes(b"\x00" * 100)

    def teardown_method(self):
        self._temp_video.unlink(missing_ok=True)
        Path(self._temp_dir).rmdir()

    def _enqueue_douyin(self):
        target = PublishTarget(
            platform=PublishPlatform.DOUYIN,
            title=f"测试{len(self.repo.list_tasks()) + 1}",
        )
        return self.svc.enqueue(video_path=str(self._temp_video), target=target)

    def _mark_published(self, task, minutes_ago: int):
        """把任务标记为已真正发布（写入最终发布时间）。"""
        final_at = datetime.now().astimezone() - timedelta(minutes=minutes_ago)
        updated = task.model_copy(
            update={
                "status": "succeeded",
                "publish_status": "succeeded",
                "final_publish_started_at": final_at,
                "updated_at": datetime.now().astimezone(),
            }
        )
        self.repo.save_task(updated)

    def test_daily_limit_blocks_the_next_publish(self):
        """达到当前每日上限后，下一个任务被明确拒绝（FAILED + 提示）。"""
        for i in range(publisher_module.PUBLISH_DAILY_LIMIT):
            self._mark_published(self._enqueue_douyin(), minutes_ago=10 + i)
        task = self._enqueue_douyin()
        result = self.svc.execute_queued_task(task.task_id)
        assert result is not None
        assert result.status.value == "failed"
        assert "每日上限" in (result.error_message or "")

    def test_interval_short_keeps_task_queued(self):
        """距上次发布不足当前最小间隔：任务保持排队，不执行不失败。"""
        self._mark_published(self._enqueue_douyin(), minutes_ago=2)
        task = self._enqueue_douyin()
        result = self.svc.execute_queued_task(task.task_id)
        assert result is None  # 自动等待
        still = self.svc.get_task(task.task_id)
        assert still.status.value == "queued"

    def test_interval_elapsed_executes_normally(self):
        """间隔已满足（30 分钟前发布过）：新任务正常执行（进入发布流程）。"""
        self._mark_published(self._enqueue_douyin(), minutes_ago=30)
        task = self._enqueue_douyin()
        result = self.svc.execute_queued_task(task.task_id)
        assert result is not None
        # 沙箱发布默认停在"等待人工确认"，能进入发布流程即通过闸门
        assert result.status.value == "paused"
        assert result.publish_status.value == "manual_ready"

    def test_platforms_independent(self):
        """不同平台互不影响：抖音超限不影响快手。"""
        for i in range(publisher_module.PUBLISH_DAILY_LIMIT):
            self._mark_published(self._enqueue_douyin(), minutes_ago=10 + i)
        target = PublishTarget(
            platform=PublishPlatform.KUAISHOU,
            title="快手发布",
        )
        task = self.svc.enqueue(video_path=str(self._temp_video), target=target)
        result = self.svc.execute_queued_task(task.task_id)
        assert result is not None
        assert result.status.value == "paused"

    def test_daily_limit_env_override(self):
        """PUBLISH_DAILY_LIMIT 可调：设为 2 后第 3 条被拒。"""
        original = publisher_module.PUBLISH_DAILY_LIMIT
        publisher_module.PUBLISH_DAILY_LIMIT = 2
        try:
            for i in range(2):
                self._mark_published(self._enqueue_douyin(), minutes_ago=10 + i)
            task = self._enqueue_douyin()
            result = self.svc.execute_queued_task(task.task_id)
            assert result.status.value == "failed"
            assert "每日上限" in (result.error_message or "")
        finally:
            publisher_module.PUBLISH_DAILY_LIMIT = original


class _BlockingPublisher:
    """模拟慢速发布：持锁一段时间。"""

    def __init__(self):
        self.release_event = None

    def platform(self):
        return "douyin"

    def capabilities(self):
        return {"enabled": True, "mode": "sandbox", "provider_name": "sandbox_douyin"}

    def publish(self, video_path, target):
        import time

        time.sleep(0.5)
        return None


class TestPublishConcurrencyGuard:
    """同步发布与队列并发保护：同一账号同时只能有一个发布。"""

    def setup_method(self):
        self.repo = MockRepository()
        self.publishers = {
            "douyin": SandboxPublisher(PublishPlatform.DOUYIN),
        }
        self.svc = PublishService(self.repo, self.publishers)
        self._temp_dir = tempfile.mkdtemp()
        self._temp_video = Path(self._temp_dir) / "test_video.mp4"
        self._temp_video.write_bytes(b"\x00" * 100)

    def teardown_method(self):
        self._temp_video.unlink(missing_ok=True)
        Path(self._temp_dir).rmdir()

    def test_sync_publish_rejects_while_same_account_busy(self):
        """同一账号同步发布进行中：第二次发布被明确拒绝。"""
        import threading
        import time

        started = threading.Event()

        class _SlowPublisher:
            def platform(self):
                return "douyin"

            def capabilities(self):
                return {
                    "enabled": True,
                    "mode": "sandbox",
                    "provider_name": "sandbox_douyin",
                }

            def publish(self, video_path, target):
                started.set()
                time.sleep(0.6)
                return SandboxPublisher(PublishPlatform.DOUYIN).publish(
                    video_path, target
                )

            def check_status(self, task_id):
                return "failed"

            def get_published_url(self, task_id):
                return None

        svc = PublishService(self.repo, {"douyin": _SlowPublisher()})
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="并发1")
        errors: list[str] = []

        def first_publish():
            try:
                svc.publish(video_path=str(self._temp_video), target=target)
            except Exception as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=first_publish)
        thread.start()
        started.wait(3)
        # 第一次发布持锁期间，第二次同步发布被拒
        with pytest.raises(ValueError, match="已有发布任务正在执行"):
            svc.publish(video_path=str(self._temp_video), target=target)
        thread.join(3)
        assert not errors

    def test_queued_task_waits_when_same_account_busy(self):
        """账号被同步发布占用时，队列任务保持排队（不失败不执行）。"""
        import threading
        import time

        started = threading.Event()

        class _SlowPublisher:
            def platform(self):
                return "douyin"

            def capabilities(self):
                return {
                    "enabled": True,
                    "mode": "sandbox",
                    "provider_name": "sandbox_douyin",
                }

            def publish(self, video_path, target):
                started.set()
                time.sleep(0.6)
                return SandboxPublisher(PublishPlatform.DOUYIN).publish(
                    video_path, target
                )

            def check_status(self, task_id):
                return "failed"

            def get_published_url(self, task_id):
                return None

        svc = PublishService(self.repo, {"douyin": _SlowPublisher()})
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="并发2")

        def first_publish():
            try:
                svc.publish(video_path=str(self._temp_video), target=target)
            except Exception:
                pass

        thread = threading.Thread(target=first_publish)
        thread.start()
        started.wait(3)
        queued = svc.enqueue(video_path=str(self._temp_video), target=target)
        # 持锁期间：队列任务保持排队
        assert svc.execute_queued_task(queued.task_id) is None
        assert svc.get_task(queued.task_id).status.value == "queued"
        thread.join(3)
        # 锁释放后：任务正常执行
        result = svc.execute_queued_task(queued.task_id)
        assert result is not None
        assert result.status.value == "paused"
