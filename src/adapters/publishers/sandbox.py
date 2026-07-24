"""沙箱发布适配器——不发起真实发布请求。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from src.models import (
    PublishPlatform,
    PublishStatus,
    PublishTask,
    PublishTarget,
    TaskStatus,
)


class SandboxPublisher:
    """离线人工发布包生成器，不发起真实平台请求。"""

    def __init__(self, platform: PublishPlatform = PublishPlatform.DOUYIN) -> None:
        self._platform = platform
        self._tasks: dict[str, PublishTask] = {}

    def platform(self) -> str:
        return self._platform.value

    def capabilities(self) -> dict[str, str | bool]:
        manual_only = self._platform in {
            PublishPlatform.WECHAT_CHANNELS,
            PublishPlatform.XIAOHONGSHU,
        }
        return {
            "provider_name": f"sandbox_{self._platform.value}",
            "display_name": f"{self._platform.value} 人工发布",
            "mode": "manual",
            "enabled": True,
            "manual_only": manual_only,
            "supports_scheduled": False,
            "supports_tags": True,
            "supports_cover": False,
        }

    def publish(
        self,
        video_path: str,
        target: PublishTarget,
    ) -> PublishTask:
        """生成发布包——等待人工去平台完成发布并回填结果。"""
        now = datetime.now().astimezone()
        task_id = f"publish-{uuid4().hex[:10]}"
        task = PublishTask(
            task_id=task_id,
            title=f"发布 · {target.title[:20]}",
            status=TaskStatus.SUBMITTED,
            progress=60,
            created_at=now,
            updated_at=now,
            video_path=video_path,
            target=target,
            publish_status=PublishStatus.MANUAL_READY,
            platform_video_id=None,
            platform_url=None,
            provider_name=f"sandbox_{self._platform.value}",
            stage="已生成发布包，等待人工发布确认",
            is_mock=True,
        )
        self._tasks[task_id] = task
        return task

    def check_status(self, task_id: str) -> PublishStatus:
        task = self._tasks.get(task_id)
        return task.publish_status if task else PublishStatus.FAILED

    def get_published_url(self, task_id: str) -> str | None:
        task = self._tasks.get(task_id)
        return task.platform_url if task else None


class PlatformPublisherAdapter:
    """单平台发布适配器骨架。

    真实发布需配置：
    - PUBLISH_{PLATFORM}_ACCESS_TOKEN
    - PUBLISH_{PLATFORM}_OPEN_ID
    等环境变量。当前未配置时返回禁用状态。
    """

    def __init__(self, platform: PublishPlatform) -> None:
        self._platform = platform
        self._enabled = False

    def platform(self) -> str:
        return self._platform.value

    def capabilities(self) -> dict[str, str | bool | list[str]]:
        token_key = f"PUBLISH_{self._platform.value.upper()}_ACCESS_TOKEN"
        open_id_key = f"PUBLISH_{self._platform.value.upper()}_OPEN_ID"
        return {
            "provider_name": self._platform.value,
            "display_name": f"{self._platform.value} 官方发布（未实测）",
            "mode": "official_unimplemented",
            "enabled": self._enabled,
            "supports_scheduled": False,
            "supports_tags": True,
            "supports_cover": True,
            "missing_configuration": (
                []
                if self._enabled
                else [token_key, open_id_key]
            ),
            "manual_fallback": True,
        }

    def publish(
        self,
        video_path: str,
        target: PublishTarget,
    ) -> PublishTask:
        if not self._enabled:
            raise RuntimeError(
                f"{self._platform.value} 发布适配器尚未配置。请配置相关环境变量后重试。"
            )
        # TODO: 实现真实发布逻辑
        raise NotImplementedError(
            f"{self._platform.value} 真实发布尚未实现，需要接入平台开放 API。"
        )

    def check_status(self, task_id: str) -> PublishStatus:
        raise NotImplementedError

    def get_published_url(self, task_id: str) -> str | None:
        raise NotImplementedError


def build_publisher(platform: PublishPlatform):
    """工厂：在真实官方适配器完成联调前，始终返回发布助手。

    旧配置可能把模式标记为 ``official``，但当前 ``PlatformPublisherAdapter``
    只是骨架。不能因为这个标记让普通用户创建一个必然失败的任务；真实适配器
    上线后再在这里显式切换，并配套验收其平台权限与回调链路。
    """
    if platform == PublishPlatform.DOUYIN:
        from src.adapters.publishers.douyin_browser import DouyinBrowserPublisher

        return DouyinBrowserPublisher()
    return SandboxPublisher(platform)
