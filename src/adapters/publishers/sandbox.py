"""沙箱发布适配器——不发起真实发布请求。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from src.models import (
    PublishPlatform,
    PublishStatus,
    PublishTask,
    PublishTarget,
    TaskKind,
    TaskStatus,
)


class SandboxPublisher:
    """离线沙箱发布器，用于演示和测试。"""

    def __init__(self, platform: PublishPlatform = PublishPlatform.DOUYIN) -> None:
        self._platform = platform
        self._tasks: dict[str, PublishTask] = {}

    def platform(self) -> str:
        return self._platform.value

    def capabilities(self) -> dict[str, str | bool]:
        return {
            "provider_name": f"sandbox_{self._platform.value}",
            "display_name": f"{self._platform.value} 发布（演示）",
            "mode": "sandbox",
            "enabled": True,
            "supports_scheduled": False,
            "supports_tags": True,
            "supports_cover": False,
        }

    def publish(
        self,
        video_path: str,
        target: PublishTarget,
    ) -> PublishTask:
        """模拟发布——直接返回成功状态。"""
        now = datetime.now().astimezone()
        task_id = f"publish-{uuid4().hex[:10]}"
        task = PublishTask(
            task_id=task_id,
            title=f"发布 · {target.title[:20]}",
            status=TaskStatus.SUCCEEDED,
            progress=100,
            created_at=now,
            updated_at=now,
            video_path=video_path,
            target=target,
            publish_status=PublishStatus.SUCCEEDED,
            platform_video_id=f"sandbox-{uuid4().hex[:8]}",
            platform_url=f"https://sandbox.example.com/video/{task_id}",
            provider_name=f"sandbox_{self._platform.value}",
            stage="演示发布成功",
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

    def capabilities(self) -> dict[str, str | bool]:
        return {
            "provider_name": self._platform.value,
            "display_name": f"{self._platform.value} 发布",
            "mode": "production" if self._enabled else "disabled",
            "enabled": self._enabled,
            "supports_scheduled": False,
            "supports_tags": True,
            "supports_cover": True,
            "missing_configuration": (
                []
                if self._enabled
                else [
                    f"PUBLISH_{self._platform.value.upper()}_ACCESS_TOKEN",
                    f"PUBLISH_{self._platform.value.upper()}_OPEN_ID",
                ]
            ),
        }

    def publish(
        self,
        video_path: str,
        target: PublishTarget,
    ) -> PublishTask:
        if not self._enabled:
            raise RuntimeError(
                f"{self._platform.value} 发布适配器尚未配置。"
                "请配置相关环境变量后重试。"
            )
        # TODO: 实现真实发布逻辑
        raise NotImplementedError(
            f"{self._platform.value} 真实发布尚未实现，"
            "需要接入平台开放 API。"
        )

    def check_status(self, task_id: str) -> PublishStatus:
        raise NotImplementedError

    def get_published_url(self, task_id: str) -> str | None:
        raise NotImplementedError


def build_publisher(platform: PublishPlatform):
    """工厂：根据配置返回合适的发布器。"""
    import os

    token_key = f"PUBLISH_{platform.value.upper()}_ACCESS_TOKEN"
    if os.getenv(token_key, "").strip():
        adapter = PlatformPublisherAdapter(platform)
        adapter._enabled = True
        return adapter
    return SandboxPublisher(platform)
