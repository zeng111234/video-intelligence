"""发布安全暂停测试：风控提示/连续失败自动暂停账号 + 手动恢复。

验证 execute_queued_task 的账号防封逻辑：
- 错误消息命中风控标记（发布频繁/操作频繁等）→ 立即暂停该账号 24 小时
- 连续 2 次失败 → 自动暂停该账号
- 暂停期间新任务明确失败并提示暂停原因
- 发布成功清零失败计数
- resume_publish_account 手动恢复
- 不同账号互不影响
"""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from src.adapters.publishers.sandbox import SandboxPublisher
from src.models import PublishPlatform, PublishTarget
from src.repositories.mock import MockRepository
from src.services.publisher import (
    PublishService,
)


class _RiskPublisher:
    """模拟触发风控提示的发布失败。"""

    def platform(self) -> str:
        return "douyin"

    def capabilities(self) -> dict[str, str | bool]:
        return {"enabled": True, "mode": "sandbox", "provider_name": "sandbox_douyin"}

    def publish(self, video_path, target):
        raise RuntimeError("平台提示：发布频繁，请稍后再试")

    def check_status(self, task_id):
        return "failed"

    def get_published_url(self, task_id):
        return None


class TestPublishSafetyPause:
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

    def _enqueue(self, title="测试发布"):
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title=title)
        return self.svc.enqueue(video_path=str(self._temp_video), target=target)

    def test_risk_marker_pauses_account_immediately(self):
        """错误命中风控标记：账号立即暂停，后续任务被拒绝。"""
        svc = PublishService(self.repo, {"douyin": _RiskPublisher()})
        task1 = self._enqueue("风控测试1")
        result1 = svc.execute_queued_task(task1.task_id)
        assert result1.status.value == "failed"
        state = self.repo.get_publish_safety_state("douyin", "default")
        assert state is not None
        assert state.blocked_until is not None
        assert state.blocked_until > datetime.now().astimezone()
        assert "发布频繁" in (state.blocked_reason or "")
        # 暂停期间：新任务明确失败并提示暂停
        task2 = self._enqueue("风控测试2")
        result2 = svc.execute_queued_task(task2.task_id)
        assert result2.status.value == "failed"
        assert "已暂停" in (result2.error_message or "")

    def test_consecutive_failures_pause_after_two(self):
        """连续 2 次失败自动暂停，第 1 次不暂停。"""
        class _FailPublisher:
            def platform(self):
                return "douyin"

            def capabilities(self):
                return {"enabled": True, "mode": "sandbox", "provider_name": "sandbox_douyin"}

            def publish(self, video_path, target):
                raise RuntimeError("普通发布失败")

            def check_status(self, task_id):
                return "failed"

            def get_published_url(self, task_id):
                return None

        svc = PublishService(self.repo, {"douyin": _FailPublisher()})
        task1 = self._enqueue("失败1")
        svc.execute_queued_task(task1.task_id)
        state = self.repo.get_publish_safety_state("douyin", "default")
        assert state.consecutive_failures == 1
        assert state.blocked_until is None  # 第 1 次不暂停
        task2 = self._enqueue("失败2")
        svc.execute_queued_task(task2.task_id)
        state = self.repo.get_publish_safety_state("douyin", "default")
        assert state.consecutive_failures == 2
        assert state.blocked_until is not None  # 第 2 次暂停
        task3 = self._enqueue("失败3")
        result3 = svc.execute_queued_task(task3.task_id)
        assert result3.status.value == "failed"
        assert "已暂停" in (result3.error_message or "")

    def test_success_resets_failure_count(self):
        """一次失败后成功发布：失败计数清零，不触发暂停。"""
        class _OnceFailPublisher:
            def __init__(self):
                self.fail_first = True

            def platform(self):
                return "douyin"

            def capabilities(self):
                return {"enabled": True, "mode": "sandbox", "provider_name": "sandbox_douyin"}

            def publish(self, video_path, target):
                if self.fail_first:
                    self.fail_first = False
                    raise RuntimeError("偶发失败")
                return SandboxPublisher(PublishPlatform.DOUYIN).publish(video_path, target)

            def check_status(self, task_id):
                return "failed"

            def get_published_url(self, task_id):
                return None

        svc = PublishService(self.repo, {"douyin": _OnceFailPublisher()})
        task1 = self._enqueue("偶发1")
        svc.execute_queued_task(task1.task_id)
        task2 = self._enqueue("偶发2")
        result2 = svc.execute_queued_task(task2.task_id)
        assert result2.status.value == "paused"  # 成功进入发布流程
        state = self.repo.get_publish_safety_state("douyin", "default")
        assert state.consecutive_failures == 0
        assert state.blocked_until is None

    def test_resume_publish_account(self):
        """手动恢复：清暂停与失败计数。"""
        svc = PublishService(self.repo, {"douyin": _RiskPublisher()})
        task1 = self._enqueue("恢复测试")
        svc.execute_queued_task(task1.task_id)
        state = self.repo.get_publish_safety_state("douyin", "default")
        assert state.blocked_until is not None
        result = svc.resume_publish_account(PublishPlatform.DOUYIN)
        assert result["resumed"] is True
        state = self.repo.get_publish_safety_state("douyin", "default")
        assert state.blocked_until is None
        assert state.consecutive_failures == 0

    def test_safety_status_reports_remaining_and_blocked(self):
        """safety_status 返回剩余条数与暂停信息。"""
        svc = PublishService(self.repo, {"douyin": _RiskPublisher()})
        task1 = self._enqueue("状态测试")
        svc.execute_queued_task(task1.task_id)
        status = svc.publish_safety_status()
        douyin_items = [item for item in status if item["platform"] == "douyin"]
        assert douyin_items
        item = douyin_items[0]
        assert item["daily_limit"] >= 1
        assert item["blocked"] is True
        assert item["blocked_reason"]
        # 发布成功后剩余条数减少（手动标记为已真正发布）
        svc.resume_publish_account(PublishPlatform.DOUYIN)
        svc2 = PublishService(self.repo, self.publishers)
        task2 = self._enqueue("正常发布")
        result = svc2.execute_queued_task(task2.task_id)
        assert result.status.value == "paused"
        marked = result.model_copy(
            update={
                "status": "succeeded",
                "publish_status": "succeeded",
                "final_publish_started_at": datetime.now().astimezone(),
                "updated_at": datetime.now().astimezone(),
            }
        )
        self.repo.save_task(marked)
        assert svc2.publish_safety_status()[0]["today_published"] >= 1

    def test_accounts_isolated(self):
        """不同账号互不影响：default 暂停不影响指定账号。"""
        svc = PublishService(self.repo, {"douyin": _RiskPublisher()})
        task1 = self._enqueue("账号A")
        svc.execute_queued_task(task1.task_id)
        # 指定账号发布：不受 default 暂停影响（沙箱成功）
        svc2 = PublishService(self.repo, self.publishers)
        target = PublishTarget(
            platform=PublishPlatform.DOUYIN,
            title="账号B",
            account_id="account-b",
        )
        task2 = svc2.enqueue(video_path=str(self._temp_video), target=target)
        result = svc2.execute_queued_task(task2.task_id)
        assert result.status.value == "paused"  # 正常执行
