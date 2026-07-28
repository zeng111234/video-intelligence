"""PublishService 独立测试。

覆盖：未知平台、异常传播、get_task 类型过滤、on_progress 回调。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from src.adapters.publishers.douyin_browser import DouyinBrowserPublisher
from src.adapters.publishers.local_browser import LocalBrowserAutoPublisher
from src.adapters.publishers.sandbox import SandboxPublisher, build_publisher
from src.models import (
    PublishPlatform,
    PublishStatus,
    PublishTarget,
    PublishTask,
    TaskKind,
    TaskStatus,
)
from src.repositories.mock import MockRepository
from src.repositories.sqlite import SQLiteRepository
from src.services.publisher import PublishService
from src.services.publish_worker import PublishWorker


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
        assert task.status == TaskStatus.SUBMITTED

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
        assert all("mode" in p for p in platforms)
        assert all("requires_account" in p for p in platforms)
        assert all("setup_required" in p for p in platforms)

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
        assert tasks[0].status == TaskStatus.SUBMITTED
        assert tasks[1].status == TaskStatus.FAILED

    def test_publish_sandbox_skips_file_check(self):
        """人工发布包模式下不存在的文件路径也能先记录。"""
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="沙箱跳过检查")
        task = self.svc.publish(video_path="/nonexistent/video.mp4", target=target)
        assert task.status == TaskStatus.SUBMITTED

    def test_preflight_manual_platforms(self):
        targets = [
            PublishTarget(platform=PublishPlatform.DOUYIN, title="预检测试"),
            PublishTarget(platform=PublishPlatform.KUAISHOU, title="预检测试"),
        ]
        result = self.svc.preflight(video_path=str(self._temp_video), targets=targets)
        assert result["blocked"] is False
        assert len(result["platforms"]) == 2
        assert all(item["manual_required"] for item in result["platforms"])

    def test_create_batch_groups_tasks(self):
        targets = [
            PublishTarget(platform=PublishPlatform.DOUYIN, title="批次测试"),
            PublishTarget(platform=PublishPlatform.KUAISHOU, title="批次测试"),
        ]
        batch = self.svc.create_batch(video_path=str(self._temp_video), targets=targets)
        assert batch["status"] == "manual_ready"
        assert batch["total"] == 2
        assert all(task.batch_id == batch["batch_id"] for task in batch["tasks"])

    def test_record_manual_result_success(self):
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="人工确认")
        task = self.svc.publish(video_path=str(self._temp_video), target=target)
        updated = self.svc.record_manual_result(
            task_id=task.task_id,
            succeeded=True,
            platform_url="https://example.com/video/1",
            note="平台后台已确认",
        )
        assert updated.status == TaskStatus.SUCCEEDED
        assert updated.publish_status == PublishStatus.SUCCEEDED
        assert updated.platform_url == "https://example.com/video/1"
        assert updated.is_mock is False

    def test_retry_failed_task_once(self):
        svc = PublishService(self.repo, {"douyin": _FailingPublisher()})
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="失败后重试")
        task = svc.publish(video_path=str(self._temp_video), target=target)
        retried = svc.retry_task(task.task_id)
        assert retried.retry_count == 1
        with pytest.raises(ValueError, match="已重试过一次"):
            svc.retry_task(retried.task_id)

    def test_final_publish_task_cannot_be_retried(self):
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="最终点击后不可重试")
        task = self.svc.publish(video_path=str(self._temp_video), target=target)
        clicked = task.model_copy(update={"final_publish_started_at": task.created_at, "outputs": {"final_publish_clicked": "true"}})
        self.repo.save_task(clicked)
        with pytest.raises(ValueError, match="已经点击最终发布"):
            self.svc.retry_task(clicked.task_id)

    def test_action_required_task_can_resume_before_final_click(self):
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="验证后继续")
        task = self.svc.publish(video_path=str(self._temp_video), target=target)
        paused = task.model_copy(update={"status": TaskStatus.PAUSED, "publish_status": PublishStatus.ACTION_REQUIRED})
        self.repo.save_task(paused)
        resumed = self.svc.resume_task(paused.task_id)
        assert resumed.status == TaskStatus.QUEUED
        assert resumed.publish_status == PublishStatus.PENDING

    def test_delete_publish_task_removes_a_paused_or_finished_record(self):
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="删除历史任务")
        task = self.svc.publish(video_path=str(self._temp_video), target=target)

        assert self.svc.delete_task(task.task_id) == task.task_id
        assert self.svc.get_task(task.task_id) is None

    def test_delete_publish_tasks_rejects_an_active_queue_item(self):
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="排队任务")
        queued = self.svc.create_batch(video_path=str(self._temp_video), targets=[target])["tasks"][0]

        with pytest.raises(ValueError, match="正在队列中"):
            self.svc.delete_tasks([queued.task_id])


def test_sqlite_repository_reads_publish_task(tmp_path):
    repo = SQLiteRepository(tmp_path / "publish.db")
    svc = PublishService(repo, {"douyin": SandboxPublisher(PublishPlatform.DOUYIN)})
    target = PublishTarget(platform=PublishPlatform.DOUYIN, title="SQLite发布")
    created = svc.publish(video_path="/tmp/video.mp4", target=target)

    fresh_repo = SQLiteRepository(tmp_path / "publish.db")
    fetched = fresh_repo.get_task(created.task_id)
    assert isinstance(fetched, PublishTask)
    assert fetched.publish_status == PublishStatus.MANUAL_READY


def test_douyin_uses_local_browser_publisher_even_when_old_official_mode_exists(
    monkeypatch,
):
    """旧官方模式配置不能覆盖已验证的本机扫码发布流程。"""
    monkeypatch.setenv("PUBLISH_DOUYIN_MODE", "official")

    publisher = build_publisher(PublishPlatform.DOUYIN)

    assert isinstance(publisher, DouyinBrowserPublisher)
    assert publisher.capabilities()["mode"] == "local_browser"
    assert publisher.capabilities()["requires_account"] is True
    assert publisher.capabilities()["setup_required"] is True


def test_douyin_publisher_navigates_from_home_to_upload_page():
    assert DouyinBrowserPublisher._is_upload_page(
        "https://creator.douyin.com/creator-micro/content/upload"
    )
    assert not DouyinBrowserPublisher._is_upload_page(
        "https://creator.douyin.com/creator-micro/home"
    )


def test_douyin_publisher_selects_large_file_by_local_cdp_path(tmp_path):
    video = tmp_path / "large-video.mp4"
    video.write_bytes(b"video")

    class FakeSession:
        def __init__(self):
            self.calls = []

        def send(self, method, params):
            self.calls.append((method, params))
            if method == "DOM.getDocument":
                return {"root": {"nodeId": 1}}
            if method == "DOM.querySelector":
                return {"nodeId": 9}
            return {}

    session = FakeSession()

    class FakeContext:
        @staticmethod
        def new_cdp_session(_page):
            return session

    class FakePage:
        context = FakeContext()

    DouyinBrowserPublisher._set_local_file(FakePage(), str(video))

    assert session.calls[-1] == (
        "DOM.setFileInputFiles",
        {"nodeId": 9, "files": [str(video.resolve())]},
    )


def test_local_browser_auto_publisher_is_explicit_and_does_not_hide_challenges():
    publisher = LocalBrowserAutoPublisher(PublishPlatform.XIAOHONGSHU)

    assert publisher.capabilities()["manual_only"] is False
    assert publisher._requires_user("请完成短信验证码和安全验证")
    assert not publisher._requires_user("正常的发布表单")


def test_local_browser_auto_publisher_formats_tags_without_private_api():
    publisher = LocalBrowserAutoPublisher(PublishPlatform.KUAISHOU)
    target = PublishTarget(
        platform=PublishPlatform.KUAISHOU,
        title="标题",
        description="描述",
        tags=["品牌", "#活动"],
    )

    assert publisher._content(target) == "描述\n#品牌 #活动"


def test_publish_worker_claims_only_one_queued_task(tmp_path):
    repo = MockRepository()
    service = PublishService(
        repo,
        {
            "douyin": SandboxPublisher(PublishPlatform.DOUYIN),
            "kuaishou": SandboxPublisher(PublishPlatform.KUAISHOU),
        },
    )
    video = tmp_path / "queued.mp4"
    video.write_bytes(b"video")
    batch = service.create_batch(
        video_path=str(video),
        targets=[
            PublishTarget(platform=PublishPlatform.DOUYIN, title="第一条"),
            PublishTarget(platform=PublishPlatform.KUAISHOU, title="第二条"),
        ],
    )
    worker = PublishWorker(service)

    first = worker.tick_once()

    assert first is not None
    assert first.status == TaskStatus.PAUSED
    remaining = [task for task in batch["tasks"] if service.get_task(task.task_id).status == TaskStatus.QUEUED]
    assert len(remaining) == 1


def test_publish_worker_can_restart_on_a_new_event_loop():
    worker = PublishWorker(
        PublishService(
            MockRepository(),
            {"douyin": SandboxPublisher(PublishPlatform.DOUYIN)},
        ),
        interval_seconds=60,
    )

    async def cycle() -> None:
        await worker.start()
        await worker.stop()

    asyncio.run(cycle())
    asyncio.run(cycle())


def test_publish_preflight_blocks_missing_video_file():
    service = PublishService(
        MockRepository(),
        {"douyin": SandboxPublisher(PublishPlatform.DOUYIN)},
    )
    result = service.preflight(
        video_path="/missing/final-video.mp4",
        targets=[
            PublishTarget(
                platform=PublishPlatform.DOUYIN,
                title="缺失成片",
            )
        ],
    )

    assert result["blocked"] is True
    assert "文件不存在" in "；".join(result["issues"])
