"""User-visible, local-browser publishing assistant for Douyin.

This adapter opens only the official creator site in a dedicated local profile.
It clicks the final button only when that specific account has an explicit
local authorization; verification/CAPTCHA always remains user-operated.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
from uuid import uuid4

from src.models import (
    PublishPlatform,
    PublishStatus,
    PublishTarget,
    PublishTask,
    TaskStatus,
)
from src.services.publish_accounts import PublishAccountError, publish_account_manager

DOUYIN_UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"


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
            account = publish_account_manager.verify_session(account.account_id)
        except PublishAccountError as exc:
            raise RuntimeError(str(exc)) from exc
        if account.status != "ready":
            raise RuntimeError(account.last_message)

        stage = self._prepare_official_draft(
            debug_port=account.debug_port,
            video_path=str(video),
            target=target,
            account_name=account.name,
        )
        now = datetime.now().astimezone()
        if account.auto_publish_authorized and stage.startswith("已在账号"):
            submitted, succeeded, evidence = self._submit_final_publish(debug_port=account.debug_port)
            if submitted:
                return PublishTask(
                    task_id=f"publish-{uuid4().hex[:10]}",
                    title=f"抖音发布 · {target.title[:20]}",
                    status=TaskStatus.SUCCEEDED if succeeded else TaskStatus.OUTCOME_UNKNOWN,
                    progress=100 if succeeded else 90,
                    created_at=now,
                    updated_at=now,
                    video_path=str(video),
                    target=target,
                    publish_status=PublishStatus.SUCCEEDED if succeeded else PublishStatus.OUTCOME_UNKNOWN,
                    provider_name="douyin_local_browser",
                    stage="平台页面已确认发布成功" if succeeded else "已点击官方发布，等待平台处理结果",
                    action_required=None if succeeded else "已执行最终发布点击；请在抖音创作者后台确认结果后回填。系统不会自动重试。",
                    final_publish_started_at=now,
                    outcome_evidence=evidence,
                    is_mock=False,
                    outputs={
                        "account_id": account.account_id,
                        "account_name": account.name,
                        "final_publish_clicked": "true",
                    },
                )
            stage = f"{stage} 自动点击未执行：{evidence}"
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
            action_required="请等待上传完成，检查内容后在官方页面手动点击发布。若需自动点击，请先为该账号开启明确授权。",
            is_mock=False,
            outputs={
                "account_id": account.account_id,
                "account_name": account.name,
                "final_publish_requires_user": "true" if not account.auto_publish_authorized else "false",
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
            return (
                "本机浏览器已打开，但 Playwright 未安装；请在官方页面手动上传并发布。"
            )

        try:
            with sync_playwright() as playwright:
                browser = None
                # One initial attempt and one retry, matching the workstation
                # network/tool retry policy.
                for _ in range(2):
                    try:
                        browser = playwright.chromium.connect_over_cdp(
                            f"http://127.0.0.1:{debug_port}"
                        )
                        break
                    except Exception:
                        time.sleep(0.6)
                if browser is None:
                    return "官方窗口正在启动；请完成扫码后重新点击开始发布。"
                pages = [page for context in browser.contexts for page in context.pages]
                if not pages:
                    return "未找到官方创作者页面；请在打开的浏览器中完成登录后重试。"
                page = next(
                    (item for item in pages if "creator.douyin.com" in item.url),
                    pages[0],
                )
                # The creator home page has no file input. Always enter the
                # dedicated upload route before querying the publishing form.
                if not DouyinBrowserPublisher._is_upload_page(page.url):
                    page.goto(
                        DOUYIN_UPLOAD_URL,
                        wait_until="domcontentloaded",
                        timeout=20_000,
                    )
                page.wait_for_timeout(1500)
                page_text = page.locator("body").inner_text(timeout=8_000)
                login_markers = ("扫码登录", "请登录", "安全验证", "请完成验证")
                if any(marker in page_text for marker in login_markers):
                    return (
                        f"账号“{account_name}”正在等待你在官方窗口扫码或完成验证；"
                        "完成后重新点击开始发布即可自动填写。"
                    )

                upload_input = page.locator("input[type='file']")
                try:
                    upload_input.first.wait_for(state="attached", timeout=12_000)
                except PlaywrightTimeoutError as exc:
                    raise RuntimeError(
                        "未找到视频上传控件，抖音创作者页面可能已改版。"
                    ) from exc
                if upload_input.count() < 1:
                    raise RuntimeError("未找到视频上传控件，抖音创作者页面可能已改版。")

                # connect_over_cdp treats Chrome as a remote browser and refuses
                # to transfer files larger than 50MB. Chrome runs on this same
                # workstation, so ask CDP to select the local path directly.
                DouyinBrowserPublisher._set_local_file(page, video_path)

                title_input = page.locator(
                    "input[placeholder*='标题'], textarea[placeholder*='标题']"
                )
                try:
                    title_input.first.wait_for(state="visible", timeout=20_000)
                except PlaywrightTimeoutError as exc:
                    raise RuntimeError(
                        "视频已交给官方页面，但未找到标题输入框；请在窗口中手动填写。"
                    ) from exc
                if title_input.count() < 1:
                    raise RuntimeError(
                        "视频已交给官方页面，但未找到标题输入框；请在窗口中手动填写。"
                    )
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
                    f"已在账号“{account_name}”的官方页面选择视频并填写内容；"
                    "请等待上传完成，检查封面、可见范围和文案，然后由你点击最终发布。"
                )
        except PlaywrightTimeoutError:
            return "官方页面仍在上传或加载，请在打开的窗口中继续等待并检查内容；系统不会代替你点击发布。"

    @staticmethod
    def _is_upload_page(url: str) -> bool:
        return "/creator-micro/content/upload" in url

    @staticmethod
    def _submit_final_publish(*, debug_port: int | None) -> tuple[bool, bool, str]:
        """Click one unambiguous official submit control once, then stop."""
        if not debug_port:
            return False, False, "未连接到本机官方窗口。"
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False, False, "Playwright 未安装。"
        try:
            with sync_playwright() as playwright:
                browser = None
                for _ in range(2):
                    try:
                        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
                        break
                    except Exception:
                        time.sleep(0.6)
                if browser is None:
                    return False, False, "官方窗口连接失败。"
                pages = [page for context in browser.contexts for page in context.pages]
                page = next((item for item in pages if DouyinBrowserPublisher._is_upload_page(item.url)), None)
                if page is None:
                    return False, False, "未找到抖音上传页面。"
                button = page.get_by_role("button", name="发布", exact=True)
                if button.count() != 1 or not button.is_visible() or not button.is_enabled():
                    return False, False, "最终发布按钮不可用，可能仍在上传或页面已改版。"
                button.click(timeout=10_000)
                for _ in range(5):
                    page.wait_for_timeout(1_000)
                    if "/creator-micro/content/manage" in page.url:
                        return True, True, f"已点击唯一发布按钮并进入内容管理页：{page.url}"
                return True, False, f"已点击官方页面唯一的“发布”按钮，尚未检测到成功页：{page.url}"
        except Exception as exc:
            return False, False, f"自动点击前检查失败：{exc}"

    @staticmethod
    def _set_local_file(page, video_path: str) -> None:
        session = page.context.new_cdp_session(page)
        document = session.send("DOM.getDocument", {"depth": 1})
        node = session.send(
            "DOM.querySelector",
            {
                "nodeId": document["root"]["nodeId"],
                "selector": "input[type='file']",
            },
        )
        node_id = node.get("nodeId")
        if not node_id:
            raise RuntimeError("未找到视频上传控件，抖音创作者页面可能已改版。")
        session.send(
            "DOM.setFileInputFiles",
            {"nodeId": node_id, "files": [str(Path(video_path).resolve())]},
        )
