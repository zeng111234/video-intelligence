"""PublishService 独立测试。

覆盖：未知平台、异常传播、get_task 类型过滤、on_progress 回调。
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.adapters.publishers.douyin_browser import (
    DOUYIN_MUSIC_FALLBACK_MARKER,
    DOUYIN_VIDEO_DESCRIPTION_SELECTOR,
    DOUYIN_VIDEO_TITLE_SELECTOR,
    DouyinBrowserPublisher,
)
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

    def _local_browser_service(self, platform: PublishPlatform) -> PublishService:
        publisher = (
            DouyinBrowserPublisher()
            if platform == PublishPlatform.DOUYIN
            else LocalBrowserAutoPublisher(platform)
        )
        return PublishService(self.repo, {platform.value: publisher})

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

    def test_prepare_official_page_is_not_limited_by_failure_retry_count(self):
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="重新准备官方页")
        task = self.svc.publish(video_path=str(self._temp_video), target=target)
        saved = task.model_copy(
            update={
                "status": TaskStatus.PAUSED,
                "publish_status": PublishStatus.MANUAL_READY,
                "provider_name": "douyin_local_browser",
                "stage": "发布信息已保存，等待人工核对",
                "retry_count": 1,
            }
        )
        self.repo.save_task(saved)

        service = self._local_browser_service(PublishPlatform.DOUYIN)
        prepared = service.prepare_official_page(saved.task_id)

        assert prepared.status == TaskStatus.QUEUED
        assert prepared.publish_status == PublishStatus.PENDING
        assert prepared.stage == "正在准备抖音官方发布页"
        assert prepared.retry_count == 1
        assert service.prepare_official_page(saved.task_id).status == TaskStatus.QUEUED

    def test_prepare_official_page_stops_after_final_publish_click(self):
        target = PublishTarget(
            platform=PublishPlatform.DOUYIN, title="最终点击后不可重备"
        )
        task = self.svc.publish(video_path=str(self._temp_video), target=target)
        clicked = task.model_copy(
            update={
                "provider_name": "douyin_local_browser",
                "final_publish_started_at": task.created_at,
                "outputs": {"final_publish_clicked": "true"},
            }
        )
        self.repo.save_task(clicked)

        with pytest.raises(ValueError, match="已经点击最终发布"):
            self._local_browser_service(PublishPlatform.DOUYIN).prepare_official_page(
                clicked.task_id
            )

    def test_confirm_auto_publish_authorizes_only_the_selected_task(self):
        target = PublishTarget(
            platform=PublishPlatform.DOUYIN,
            account_id="pubacc-test",
            title="本次自动发布",
        )
        task = self.svc.publish(video_path=str(self._temp_video), target=target)
        saved = task.model_copy(
            update={
                "status": TaskStatus.PAUSED,
                "publish_status": PublishStatus.MANUAL_READY,
                "provider_name": "douyin_local_browser",
                "stage": "已在账号“测试号”的官方页面选择视频并填写内容",
            }
        )
        self.repo.save_task(saved)

        confirmed = self._local_browser_service(
            PublishPlatform.DOUYIN
        ).confirm_auto_publish(saved.task_id)

        assert confirmed.status == TaskStatus.QUEUED
        assert confirmed.publish_status == PublishStatus.PENDING
        assert confirmed.target.auto_publish_authorized is True
        assert confirmed.target.use_prepared_page is True
        assert confirmed.outputs["task_auto_publish_authorized"] == "true"
        assert saved.target.auto_publish_authorized is False

    def test_kuaishou_official_page_can_be_prepared_and_authorized(self):
        target = PublishTarget(
            platform=PublishPlatform.KUAISHOU,
            account_id="pubacc-kuaishou",
            title="快手多平台发布",
        )
        task = self.svc.publish(video_path=str(self._temp_video), target=target)
        saved = task.model_copy(
            update={
                "status": TaskStatus.PAUSED,
                "publish_status": PublishStatus.MANUAL_READY,
                "provider_name": "kuaishou_local_browser",
                "stage": "已在官方页面选择视频并填写内容，等待最终发布确认",
            }
        )
        self.repo.save_task(saved)
        service = self._local_browser_service(PublishPlatform.KUAISHOU)

        prepared = service.prepare_official_page(saved.task_id)
        assert prepared.stage == "正在准备快手官方发布页"

        self.repo.save_task(saved)
        confirmed = service.confirm_auto_publish(saved.task_id)
        assert confirmed.target.auto_publish_authorized is True
        assert confirmed.target.use_prepared_page is True

    def test_xiaohongshu_can_authorize_final_publish(self):
        target = PublishTarget(
            platform=PublishPlatform.XIAOHONGSHU,
            account_id="pubacc-xiaohongshu",
            title="小红书人工确认",
        )
        task = PublishTask(
            task_id="pub-xiaohongshu-manual",
            title="小红书人工确认",
            status=TaskStatus.PAUSED,
            progress=70,
            created_at=datetime.now().astimezone(),
            updated_at=datetime.now().astimezone(),
            video_path=str(self._temp_video),
            target=target,
            publish_status=PublishStatus.MANUAL_READY,
            provider_name="xiaohongshu_local_browser",
        )
        self.repo.save_task(task)

        confirmed = self._local_browser_service(
            PublishPlatform.XIAOHONGSHU
        ).confirm_auto_publish(task.task_id)

        assert confirmed.target.auto_publish_authorized is True
        assert confirmed.target.use_prepared_page is True

    def test_final_publish_task_cannot_be_retried(self):
        target = PublishTarget(
            platform=PublishPlatform.DOUYIN, title="最终点击后不可重试"
        )
        task = self.svc.publish(video_path=str(self._temp_video), target=target)
        clicked = task.model_copy(
            update={
                "final_publish_started_at": task.created_at,
                "outputs": {"final_publish_clicked": "true"},
            }
        )
        self.repo.save_task(clicked)
        with pytest.raises(ValueError, match="已经点击最终发布"):
            self.svc.retry_task(clicked.task_id)

    def test_action_required_task_can_resume_before_final_click(self):
        target = PublishTarget(platform=PublishPlatform.DOUYIN, title="验证后继续")
        task = self.svc.publish(video_path=str(self._temp_video), target=target)
        paused = task.model_copy(
            update={
                "status": TaskStatus.PAUSED,
                "publish_status": PublishStatus.ACTION_REQUIRED,
            }
        )
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
        queued = self.svc.create_batch(
            video_path=str(self._temp_video), targets=[target]
        )["tasks"][0]

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


def test_xiaohongshu_uses_local_browser_publisher_with_a_separate_account():
    publisher = build_publisher(PublishPlatform.XIAOHONGSHU)

    assert isinstance(publisher, LocalBrowserAutoPublisher)
    assert publisher.capabilities()["mode"] == "local_browser"
    assert publisher.capabilities()["requires_account"] is True
    assert publisher.capabilities()["manual_only"] is False


def test_preflight_requires_a_connected_account_for_every_local_browser_platform(
    tmp_path,
):
    video = tmp_path / "xiaohongshu.mp4"
    video.write_bytes(b"video")
    service = PublishService(
        MockRepository(),
        {"xiaohongshu": LocalBrowserAutoPublisher(PublishPlatform.XIAOHONGSHU)},
    )

    result = service.preflight(
        video_path=str(video),
        targets=[
            PublishTarget(platform=PublishPlatform.XIAOHONGSHU, title="小红书测试")
        ],
    )

    assert result["blocked"] is True
    assert result["platforms"][0]["issue"] == "请先选择已扫码连接的发布账号。"


def test_douyin_publisher_navigates_from_home_to_upload_page():
    assert DouyinBrowserPublisher._is_upload_page(
        "https://creator.douyin.com/creator-micro/content/upload"
    )
    assert not DouyinBrowserPublisher._is_upload_page(
        "https://creator.douyin.com/creator-micro/home"
    )
    assert DouyinBrowserPublisher._is_publish_page(
        "https://creator.douyin.com/creator-micro/content/post/video?enter_from=publish_page"
    )
    assert DouyinBrowserPublisher._is_publish_page(
        "https://creator.douyin.com/creator-micro/content/publish?enter_from=publish_page"
    )
    assert not DouyinBrowserPublisher._is_publish_page(
        "https://creator.douyin.com/creator-micro/content/upload"
    )
    assert not DouyinBrowserPublisher._is_publish_page(
        "https://creator.douyin.com/creator-micro/content/post/image?enter_from=publish_page"
    )
    assert not DouyinBrowserPublisher._is_publish_page(
        "https://creator.douyin.com/creator-micro/content/manage"
    )


def test_douyin_publish_guard_accepts_only_the_exact_video_form():
    target = PublishTarget(
        platform=PublishPlatform.DOUYIN,
        title="作品标题",
        description="作品描述",
        tags=["商业思维", "#企业经营"],
    )

    class FakeLocator:
        def __init__(self, *, value="", text="", count=1, visible=True):
            self._value = value
            self._text = text
            self._count = count
            self._visible = visible

        @property
        def first(self):
            return self

        def count(self):
            return self._count

        def is_visible(self):
            return self._visible

        def input_value(self):
            return self._value

        def inner_text(self):
            return self._text

    class FakePage:
        url = (
            "https://creator.douyin.com/creator-micro/content/post/video"
            "?enter_from=publish_page"
        )

        @staticmethod
        def locator(selector):
            if selector == DOUYIN_VIDEO_TITLE_SELECTOR:
                return FakeLocator(value="作品标题")
            if selector == DOUYIN_VIDEO_DESCRIPTION_SELECTOR:
                return FakeLocator(text="作品描述 #商业思维 #企业经营")
            raise AssertionError(f"不应查询通用编辑框: {selector}")

    matched, evidence = DouyinBrowserPublisher._prepared_form_matches(
        FakePage(),
        target,
    )

    assert matched is True
    assert "标题、描述和标签" in evidence


def test_douyin_publish_guard_rejects_a_generic_comment_editor():
    target = PublishTarget(
        platform=PublishPlatform.DOUYIN,
        title="作品标题",
        description="不能进入评论区",
    )

    class FakeLocator:
        def __init__(self, *, value="", count=1):
            self._value = value
            self._count = count

        @property
        def first(self):
            return self

        def count(self):
            return self._count

        @staticmethod
        def is_visible():
            return True

        def input_value(self):
            return self._value

    class FakePage:
        url = (
            "https://creator.douyin.com/creator-micro/content/post/video"
            "?enter_from=publish_page"
        )

        @staticmethod
        def locator(selector):
            if selector == DOUYIN_VIDEO_TITLE_SELECTOR:
                return FakeLocator(value="作品标题")
            if selector == DOUYIN_VIDEO_DESCRIPTION_SELECTOR:
                return FakeLocator(count=0)
            raise AssertionError(f"不应查询通用编辑框: {selector}")

    matched, evidence = DouyinBrowserPublisher._prepared_form_matches(
        FakePage(),
        target,
    )

    assert matched is False
    assert "不会使用通用编辑框" in evidence


def test_douyin_selects_and_reads_back_the_top_official_music_recommendation():
    target = PublishTarget(
        platform=PublishPlatform.DOUYIN,
        title="机器人会取代哪些岗位",
        native_music_mode="auto_recommended",
        native_music_hint="科技未来 克制",
    )

    class FakeLocator:
        def __init__(
            self,
            *,
            count=0,
            visible=True,
            enabled=True,
            evaluated="",
            on_click=None,
        ):
            self._count = count
            self._visible = visible
            self._enabled = enabled
            self._evaluated = evaluated
            self._on_click = on_click

        @property
        def first(self):
            return self

        def count(self):
            return self._count

        def nth(self, _index):
            return self

        def is_visible(self):
            return self._visible

        def is_enabled(self):
            return self._enabled

        def click(self, **_kwargs):
            if self._on_click:
                self._on_click()

        def evaluate(self, _script):
            return self._evaluated

        def inner_text(self, **_kwargs):
            return "作品发布页"

    class FakePage:
        url = "https://creator.douyin.com/creator-micro/content/post/video"

        def __init__(self):
            self.selected = False

        def locator(self, selector):
            assert selector == "body"
            return FakeLocator(count=1)

        def get_by_role(self, role, name, exact):
            assert role == "button"
            assert exact is True
            if name == "选择音乐":
                return FakeLocator(count=1)
            if name == "使用":
                return FakeLocator(
                    count=1,
                    evaluated="未来感轻节奏\n使用",
                    on_click=lambda: setattr(self, "selected", True),
                )
            return FakeLocator(count=0)

        def get_by_text(self, text, exact):
            assert exact is True
            if text == "推荐":
                return FakeLocator(count=1)
            if text == "未来感轻节奏" and self.selected:
                return FakeLocator(count=1)
            return FakeLocator(count=0)

        @staticmethod
        def wait_for_timeout(_milliseconds):
            return None

    selected, title, evidence = DouyinBrowserPublisher._select_recommended_music(
        FakePage(),
        target,
    )

    assert selected is True
    assert title == "未来感轻节奏"
    assert "官方推荐音乐" in evidence


def test_douyin_music_picker_fails_closed_when_entry_is_not_unique():
    target = PublishTarget(
        platform=PublishPlatform.DOUYIN,
        title="测试",
        native_music_mode="auto_recommended",
    )

    class FakeLocator:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 2

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def inner_text(**_kwargs):
            return "作品发布页"

    class FakePage:
        url = "https://creator.douyin.com/creator-micro/content/post/video"

        @staticmethod
        def locator(selector):
            assert selector == "body"
            return FakeLocator()

        @staticmethod
        def get_by_role(role, name, exact):
            assert role == "button"
            assert exact is True
            return (
                FakeLocator()
                if name == "选择音乐"
                else type(
                    "EmptyLocator",
                    (),
                    {
                        "count": staticmethod(lambda: 0),
                        "first": property(lambda self: self),
                    },
                )()
            )

    selected, title, evidence = DouyinBrowserPublisher._select_recommended_music(
        FakePage(),
        target,
    )

    assert selected is False
    assert title is None
    assert "没有猜测点击" in evidence


def test_local_browser_publisher_waits_for_async_video_input(tmp_path):
    video = tmp_path / "large-video.mp4"
    video.write_bytes(b"video")

    class FakeCandidate:
        def __init__(self):
            self.calls = []

        def wait_for(self, **kwargs):
            self.calls.append(("wait_for", kwargs))

        def set_input_files(self, path, **kwargs):
            self.calls.append(("set_input_files", path, kwargs))

    candidate = FakeCandidate()

    class FakeLocator:
        first = candidate

        @staticmethod
        def count():
            return 1

    class FakePage:
        @staticmethod
        def locator(selector):
            if selector == "#work-description-edit[contenteditable='true']":
                return type("AbsentLocator", (), {"count": staticmethod(lambda: 0)})()
            assert selector == "input[type='file'][accept*='video']"
            return FakeLocator()

    LocalBrowserAutoPublisher(PublishPlatform.KUAISHOU)._set_local_file(
        FakePage(), str(video)
    )

    assert candidate.calls == [
        ("wait_for", {"state": "attached", "timeout": 20_000}),
        (
            "set_input_files",
            str(video.resolve()),
            {"timeout": 20_000},
        ),
    ]


def test_local_browser_publisher_falls_back_to_unscoped_file_input(tmp_path):
    video = tmp_path / "fallback.mp4"
    video.write_bytes(b"video")

    class EmptyLocator:
        @staticmethod
        def count():
            return 0

    class FakeCandidate:
        def wait_for(self, **_kwargs):
            return None

        def set_input_files(self, path, **_kwargs):
            self.path = path

    candidate = FakeCandidate()

    class FallbackLocator:
        first = candidate

    class FakePage:
        @staticmethod
        def locator(selector):
            if selector == "#work-description-edit[contenteditable='true']":
                return EmptyLocator()
            if selector == "input[type='file'][accept*='video']":
                return EmptyLocator()
            assert selector == "input[type='file']"
            return FallbackLocator()

    LocalBrowserAutoPublisher(PublishPlatform.KUAISHOU)._set_local_file(
        FakePage(), str(video)
    )

    assert candidate.path == str(video.resolve())


def test_local_browser_publisher_reuses_an_already_uploaded_draft(tmp_path):
    video = tmp_path / "already-uploaded.mp4"
    video.write_bytes(b"video")

    class DraftCandidate:
        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return True

    class DraftLocator:
        first = DraftCandidate()

        @staticmethod
        def count():
            return 1

    class FakePage:
        @staticmethod
        def locator(selector):
            assert selector == "#work-description-edit[contenteditable='true']"
            return DraftLocator()

    LocalBrowserAutoPublisher(PublishPlatform.KUAISHOU)._set_local_file(
        FakePage(), str(video)
    )


def test_kuaishou_final_click_waits_for_official_upload_finish():
    calls = []

    class FakePage:
        @staticmethod
        def wait_for_function(expression, **kwargs):
            calls.append((expression, kwargs))

    LocalBrowserAutoPublisher(PublishPlatform.KUAISHOU)._wait_for_upload_ready(
        FakePage()
    )

    assert len(calls) == 1
    assert "/rest/cp/works/v2/video/pc/upload/finish" in calls[0][0]
    assert calls[0][1] == {"timeout": 180_000}


def test_non_kuaishou_final_click_does_not_wait_for_kuaishou_upload():
    class FakePage:
        @staticmethod
        def wait_for_function(*_args, **_kwargs):
            raise AssertionError("Bilibili must not use Kuaishou upload signals")

    LocalBrowserAutoPublisher(PublishPlatform.BILIBILI)._wait_for_upload_ready(
        FakePage()
    )


def test_xiaohongshu_browser_publisher_supports_authorized_publish_and_keeps_challenges_visible():
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

    assert publisher._content(target) == "标题\n描述\n#品牌 #活动"


def test_kuaishou_limits_topics_to_the_official_four_tag_maximum():
    publisher = LocalBrowserAutoPublisher(PublishPlatform.KUAISHOU)
    target = PublishTarget(
        platform=PublishPlatform.KUAISHOU,
        title="标题",
        description="描述",
        tags=["一", "二", "三", "四", "五", "六"],
    )

    assert publisher._content(target) == "标题\n描述\n#一 #二 #三 #四"


def test_kuaishou_uses_its_unique_description_editor_without_a_title_field():
    publisher = LocalBrowserAutoPublisher(PublishPlatform.KUAISHOU)

    assert publisher._spec.title_selectors == ()
    assert publisher._spec.description_selectors == (
        "#work-description-edit[contenteditable='true']",
    )
    assert publisher._spec.description_includes_title is True


def test_every_local_browser_platform_uses_scoped_content_selectors():
    for platform in (
        PublishPlatform.KUAISHOU,
        PublishPlatform.WECHAT_CHANNELS,
        PublishPlatform.XIAOHONGSHU,
        PublishPlatform.BILIBILI,
    ):
        spec = LocalBrowserAutoPublisher(platform)._spec
        assert spec.description_selectors
        assert "[contenteditable='true']" not in spec.description_selectors

    for platform in (
        PublishPlatform.WECHAT_CHANNELS,
        PublishPlatform.XIAOHONGSHU,
        PublishPlatform.BILIBILI,
    ):
        assert LocalBrowserAutoPublisher(platform)._spec.title_selectors


def test_creator_page_selectors_match_the_verified_platform_editors():
    wechat = LocalBrowserAutoPublisher(PublishPlatform.WECHAT_CHANNELS)
    xiaohongshu = LocalBrowserAutoPublisher(PublishPlatform.XIAOHONGSHU)
    bilibili = LocalBrowserAutoPublisher(PublishPlatform.BILIBILI)

    assert ".post-desc-box .input-editor[contenteditable]" in (
        wechat._spec.description_selectors
    )
    assert "div.tiptap.ProseMirror[contenteditable='true'][role='textbox']" in (
        xiaohongshu._spec.description_selectors
    )
    assert (
        "div.ql-editor[contenteditable='true'][data-placeholder*='相关信息']"
        in bilibili._spec.description_selectors
    )


def test_wechat_channels_uses_its_official_sixteen_character_title_limit():
    publisher = LocalBrowserAutoPublisher(PublishPlatform.WECHAT_CHANNELS)
    target = PublishTarget(
        platform=PublishPlatform.WECHAT_CHANNELS,
        title="广州新烧烤店模式：49元会员加共享店长",
    )

    assert publisher._title_value(target) == target.title[:16]


def test_kuaishou_finds_the_custom_div_publish_control():
    class FakeCandidate:
        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def is_enabled():
            return True

        @staticmethod
        def inner_text(**_kwargs):
            return "发布"

    candidate = FakeCandidate()

    class EmptyLocator:
        @staticmethod
        def count():
            return 0

    class CandidateLocator:
        @staticmethod
        def count():
            return 1

        @staticmethod
        def nth(_index):
            return candidate

    class FakePage:
        @staticmethod
        def get_by_role(*_args, **_kwargs):
            return EmptyLocator()

        @staticmethod
        def locator(selector):
            assert selector == "div[class*='_button-primary_']"
            return CandidateLocator()

    publisher = LocalBrowserAutoPublisher(PublishPlatform.KUAISHOU)

    assert publisher._final_button(FakePage()) is candidate


def test_local_browser_fill_requires_the_official_page_to_retain_content():
    class FakeCandidate:
        def wait_for(self, **_kwargs):
            return None

        def fill(self, value, **_kwargs):
            self.value = value

        def input_value(self, **_kwargs):
            return "平台没有保存这段正文"

    candidate = FakeCandidate()

    class FakeLocator:
        first = candidate

        @staticmethod
        def count():
            return 1

        @staticmethod
        def nth(_index):
            return candidate

    class FakePage:
        @staticmethod
        def locator(_selector):
            return FakeLocator()

    publisher = LocalBrowserAutoPublisher(PublishPlatform.KUAISHOU)
    with pytest.raises(RuntimeError, match="确认正文已填入"):
        publisher._fill_first(
            FakePage(),
            ("textarea",),
            "快手独立正文",
            required=True,
            field_label="正文",
        )


def test_xiaohongshu_uses_explicit_task_authorization_for_final_publish(
    tmp_path,
    monkeypatch,
):
    publisher = LocalBrowserAutoPublisher(PublishPlatform.XIAOHONGSHU)
    video = tmp_path / "authorized-once.mp4"
    video.write_bytes(b"video")
    account = SimpleNamespace(
        account_id="account-xhs",
        name="小红书主号",
        status="ready",
        last_message="",
        debug_port=9333,
        auto_publish_authorized=False,
    )
    monkeypatch.setattr(
        "src.adapters.publishers.local_browser.publish_account_manager.get",
        lambda *args, **kwargs: account,
    )
    monkeypatch.setattr(
        "src.adapters.publishers.local_browser.publish_account_manager.verify_session",
        lambda *args, **kwargs: account,
    )
    monkeypatch.setattr(publisher, "_prepare_draft", lambda *args: (True, "已填入"))
    monkeypatch.setattr(
        publisher,
        "_submit_and_verify",
        lambda *args: (True, "小红书成功页", True),
    )
    target = PublishTarget(
        platform=PublishPlatform.XIAOHONGSHU,
        title="标题",
        description="描述",
        account_id=account.account_id,
        auto_publish_authorized=True,
    )

    task = publisher.publish(str(video), target)

    assert task.status == TaskStatus.SUCCEEDED
    assert task.publish_status == PublishStatus.SUCCEEDED
    assert task.outputs["final_publish_clicked"] == "true"
    assert account.auto_publish_authorized is False


def test_douyin_keeps_original_audio_when_recommended_music_is_unavailable(
    tmp_path,
    monkeypatch,
):
    publisher = DouyinBrowserPublisher()
    video = tmp_path / "douyin-original-audio.mp4"
    video.write_bytes(b"video")
    account = SimpleNamespace(
        account_id="account-douyin",
        name="抖音主号",
        status="ready",
        last_message="",
        debug_port=9444,
        auto_publish_authorized=False,
    )
    monkeypatch.setattr(
        "src.adapters.publishers.douyin_browser.publish_account_manager.get",
        lambda *args, **kwargs: account,
    )
    monkeypatch.setattr(
        "src.adapters.publishers.douyin_browser.publish_account_manager.verify_session",
        lambda *args, **kwargs: account,
    )
    monkeypatch.setattr(
        publisher,
        "_prepare_official_draft",
        lambda **kwargs: (
            f"已在账号“抖音主号”的作品发布表单填写标题、描述和标签；"
            f"{DOUYIN_MUSIC_FALLBACK_MARKER}；系统将继续等待上传完成并提交。"
        ),
    )
    submitted_targets = []

    def submit(*, debug_port, target):
        submitted_targets.append(target)
        return True, True, "内容管理页", False

    monkeypatch.setattr(publisher, "_submit_final_publish", submit)
    target = PublishTarget(
        platform=PublishPlatform.DOUYIN,
        title="抖音保留原声",
        account_id=account.account_id,
        auto_publish_authorized=True,
        native_music_mode="auto_recommended",
    )

    task = publisher.publish(str(video), target)

    assert task.status == TaskStatus.SUCCEEDED
    assert submitted_targets[0].native_music_mode == "off"
    assert submitted_targets[0].selected_music_title is None


def test_publish_worker_executes_xiaohongshu_preparation_instead_of_forcing_a_manual_package():
    queued = PublishTask(
        task_id="xhs-queued",
        title="小红书发布",
        status=TaskStatus.QUEUED,
        publish_status=PublishStatus.PENDING,
        progress=0,
        created_at=datetime.now().astimezone(),
        updated_at=datetime.now().astimezone(),
        video_path="C:/example/video.mp4",
        target=PublishTarget(
            platform=PublishPlatform.XIAOHONGSHU,
            account_id="xhs-account",
            title="小红书测试",
        ),
        provider_name="xiaohongshu_local_browser",
        stage="等待本机发布队列",
    )

    class FakePublishService:
        def __init__(self) -> None:
            self.executed: list[str] = []

        def list_tasks(self) -> list[PublishTask]:
            return [queued]

        def execute_queued_task(self, task_id: str) -> PublishTask:
            self.executed.append(task_id)
            return queued

    service = FakePublishService()
    result = PublishWorker(service).tick_once()

    assert result is queued
    assert service.executed == ["xhs-queued"]


def test_publish_worker_does_not_claim_queue_without_active_owner_session():
    class FakePublishService:
        def __init__(self) -> None:
            self.listed = False

        def list_tasks(self):
            self.listed = True
            return []

    service = FakePublishService()
    worker = PublishWorker(service, can_process=lambda: False)

    assert worker.tick_once() is None
    assert service.listed is False


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
    remaining = [
        task
        for task in batch["tasks"]
        if service.get_task(task.task_id).status == TaskStatus.QUEUED
    ]
    assert len(remaining) == 1


def test_publish_worker_runs_two_different_accounts_in_parallel():
    now = datetime.now().astimezone()
    tasks = [
        PublishTask(
            task_id="parallel-douyin",
            title="抖音",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            video_path="C:/video.mp4",
            target=PublishTarget(
                platform=PublishPlatform.DOUYIN,
                account_id="douyin-main",
                title="抖音",
            ),
        ),
        PublishTask(
            task_id="parallel-bilibili",
            title="B站",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            video_path="C:/video.mp4",
            target=PublishTarget(
                platform=PublishPlatform.BILIBILI,
                account_id="bilibili-main",
                title="B站",
            ),
        ),
    ]
    barrier = threading.Barrier(2, timeout=3)

    class FakePublishService:
        def list_tasks(self):
            return tasks

        def execute_queued_task(self, task_id):
            barrier.wait()
            return next(task for task in tasks if task.task_id == task_id)

    completed = asyncio.run(PublishWorker(FakePublishService())._run_queued_batch())

    assert {task.task_id for task in completed if task is not None} == {
        "parallel-douyin",
        "parallel-bilibili",
    }


def test_publish_worker_does_not_parallelize_the_same_platform_account():
    now = datetime.now().astimezone()
    tasks = [
        PublishTask(
            task_id=f"same-account-{index}",
            title="同账号",
            status=TaskStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
            video_path="C:/video.mp4",
            target=PublishTarget(
                platform=PublishPlatform.DOUYIN,
                account_id="same-account",
                title="同账号",
            ),
        )
        for index in range(2)
    ]

    class FakePublishService:
        def list_tasks(self):
            return tasks

    worker = PublishWorker(FakePublishService())

    assert worker._next_task_ids() == ["same-account-0"]


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
