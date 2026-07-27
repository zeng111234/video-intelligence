"""User-visible, local-browser publishing assistant for Douyin.

This adapter opens only the official creator site in a dedicated local profile.
It deliberately stops before the platform's final publish button.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
from uuid import uuid4

from src.models import PublishPlatform, PublishStatus, PublishTarget, PublishTask, TaskStatus
from src.services.publish_accounts import PublishAccountError, publish_account_manager


class DouyinBrowserPublisher:
    def platform(self) -> str:
        return PublishPlatform.DOUYIN.value

    def capabilities(self) -> dict[str, str | bool | list[str]]:
        return {
            "provider_name": "douyin_local_browser",
            "display_name": "抖音本机扫码发布",
            "mode": "local_browser",
            "enabled": True,
            "requires_account": True,
            "setup_required": True,
            "manual_only": False,
            "manual_fallback": True,
            "supports_scheduled": False,
            "supports_tags": True,
            "supports_cover": False,
            "missing_configuration": [],
        }

    def publish(self, video_path: str, target: PublishTarget) -> PublishTask:
        if not target.account_id:
            raise RuntimeError("请先选择用于发布的抖音账号。")
        video = Path(video_path)
        if not video.is_file():
            raise RuntimeError("视频文件不存在。")
        try:
            account = publish_account_manager.get(target.account_id, platform="douyin")
            account = publish_account_manager.open_login_browser(account.account_id)
        except PublishAccountError as exc:
            raise RuntimeError(str(exc)) from exc

        stage = self._prepare_official_draft(
            debug_port=account.debug_port,
            video_path=str(video),
            target=target,
            account_name=account.name,
        )
        now = datetime.now().astimezone()
        return PublishTask(
            task_id=f"publish-{uuid4().hex[:10]}",
            title=f"抖音待确认 · {target.title[:20]}",
            status=TaskStatus.SUBMITTED,
            progress=60,
            created_at=now,
            updated_at=now,
            video_path=str(video),
            target=target,
            publish_status=PublishStatus.MANUAL_READY,
            provider_name="douyin_local_browser",
            stage=stage,
            is_mock=False,
            outputs={
                "account_id": account.account_id,
                "account_name": account.name,
                "final_publish_requires_user": "true",
            },
        )

    def check_status(self, task_id: str) -> PublishStatus:
        return PublishStatus.MANUAL_READY

    def get_published_url(self, task_id: str) -> str | None:
        return None

    @staticmethod
    def _prepare_official_draft(
        *,
        debug_port: int | None,
        video_path: str,
        target: PublishTarget,
        account_name: str,
    ) -> str:
        """Upload and fill the official creator form, stopping before submit.

        Platform DOMs change frequently, so this is deliberately conservative:
        any missing expected control becomes a clear task failure, never a
        guessed click near the final publish action.
        """
        if not debug_port:
            return f"已打开账号“{account_name}”的官方窗口；请完成扫码后重新开始发布。"
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return "本机浏览器已打开，但 Playwright 未安装；请在官方页面手动上传并发布。"

        try:
            with sync_playwright() as playwright:
                browser = None
                for _ in range(3):
                    try:
                        browser = playwright.chromium.connect_over_cdp(
                            f"http://127.0.0.1:{debug_port}"
                        )
                        break
                    except Exception:
                        time.sleep(0.6)
                if browser is None:
                    return "官方窗口正在启动；请完成扫码后重新点击开始发布。"
                pages = [
                    page
                    for context in browser.contexts
                    for page in context.pages
                ]
                if not pages:
                    return "未找到官方创作者页面；请在打开的浏览器中完成登录后重试。"
                page = next(
                    (item for item in pages if "creator.douyin.com" in item.url),
                    pages[0],
                )
                if "creator.douyin.com" not in page.url:
                    page.goto(
                        "https://creator.douyin.com/creator-micro/content/upload",
                        wait_until="domcontentloaded",
                        timeout=20_000,
                    )
                page.wait_for_timeout(1200)
                page_text = page.locator("body").inner_text(timeout=8_000)
                login_markers = ("扫码登录", "请登录", "安全验证", "请完成验证")
                if any(marker in page_text for marker in login_markers):
                    return (
                        f"账号“{account_name}”正在等待你在官方窗口扫码或完成验证；"
                        "完成后重新点击开始发布即可自动填写。"
                    )

                upload_input = page.locator("input[type='file']")
                if upload_input.count() < 1:
                    raise RuntimeError("未找到视频上传控件，抖音创作者页面可能已改版。")
                upload_input.first.set_input_files(video_path, timeout=30_000)

                title_input = page.locator("input[placeholder*='标题'], textarea[placeholder*='标题']")
                if title_input.count() < 1:
                    raise RuntimeError("视频已交给官方页面，但未找到标题输入框；请在窗口中手动填写。")
                title_input.first.fill(target.title, timeout=10_000)

                content = target.description.strip()
                if target.tags:
                    suffix = " ".join(f"#{tag.lstrip('#')}" for tag in target.tags)
                    content = f"{content}\n{suffix}".strip()
                if content:
                    description_input = page.locator(
                        "textarea[placeholder*='简介'], textarea[placeholder*='描述'], [contenteditable='true']"
                    )
                    if description_input.count() > 0:
                        description_input.first.fill(content, timeout=10_000)
                return (
                    f"已在账号“{account_name}”的官方页面上传视频并填写内容；"
                    "请检查封面、可见范围和文案，然后由你点击最终发布。"
                )
        except PlaywrightTimeoutError:
            return "官方页面仍在上传或加载，请在打开的窗口中继续等待并检查内容；系统不会代替你点击发布。"
