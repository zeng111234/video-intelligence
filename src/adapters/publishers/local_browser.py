"""Visible, conservative browser publishers for supported creator centres.

The adapter deliberately uses the user's *already-open*, dedicated Chrome
profile through CDP.  It does not export cookies, call private web APIs, run
headless, or attempt to solve QR/SMS/CAPTCHA challenges.  A selector mismatch
or a challenge pauses the task before the final click.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import time
from uuid import uuid4

from src.models import PublishPlatform, PublishStatus, PublishTarget, PublishTask, TaskStatus
from src.services.publish_accounts import PublishAccountError, publish_account_manager


@dataclass(frozen=True)
class BrowserPublishSpec:
    platform: PublishPlatform
    display_name: str
    upload_url: str
    host: str
    final_button_names: tuple[str, ...]
    success_url_fragments: tuple[str, ...] = ()
    success_texts: tuple[str, ...] = ()
    confirm_button_names: tuple[str, ...] = ()
    title_selectors: tuple[str, ...] = ()
    description_selectors: tuple[str, ...] = ()


SPECS: dict[PublishPlatform, BrowserPublishSpec] = {
    PublishPlatform.KUAISHOU: BrowserPublishSpec(
        platform=PublishPlatform.KUAISHOU,
        display_name="快手",
        upload_url="https://cp.kuaishou.com/article/publish/video",
        host="cp.kuaishou.com",
        final_button_names=("发布",),
        confirm_button_names=("确认发布",),
        success_url_fragments=("/article/manage",),
        title_selectors=("input[placeholder*='标题']", "textarea[placeholder*='标题']"),
        description_selectors=("textarea[placeholder*='简介']", "textarea[placeholder*='描述']", "[contenteditable='true']"),
    ),
    PublishPlatform.WECHAT_CHANNELS: BrowserPublishSpec(
        platform=PublishPlatform.WECHAT_CHANNELS,
        display_name="视频号",
        upload_url="https://channels.weixin.qq.com/platform/post/create",
        host="channels.weixin.qq.com",
        final_button_names=("发表",),
        success_url_fragments=("/platform/post/list",),
        title_selectors=("input[placeholder*='标题']",),
        description_selectors=("textarea[placeholder*='描述']", "[contenteditable='true']"),
    ),
    PublishPlatform.XIAOHONGSHU: BrowserPublishSpec(
        platform=PublishPlatform.XIAOHONGSHU,
        display_name="小红书",
        upload_url="https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video",
        host="creator.xiaohongshu.com",
        final_button_names=("发布",),
        success_url_fragments=("/publish/success",),
        title_selectors=("input[placeholder*='填写标题']", "input[placeholder*='标题']"),
        description_selectors=("p[data-placeholder*='输入正文描述']", "[contenteditable='true']", "textarea[placeholder*='描述']"),
    ),
    PublishPlatform.BILIBILI: BrowserPublishSpec(
        platform=PublishPlatform.BILIBILI,
        display_name="Bilibili",
        upload_url="https://member.bilibili.com/platform/upload/video/frame",
        host="member.bilibili.com",
        final_button_names=("立即投稿", "投稿"),
        success_texts=("投稿成功", "稿件已提交"),
        title_selectors=("input[placeholder*='稿件标题']", "input[placeholder*='标题']"),
        description_selectors=("textarea[placeholder*='简介']", "textarea[placeholder*='描述']", "[contenteditable='true']"),
    ),
}

_ACTION_REQUIRED_MARKERS = ("扫码登录", "请登录", "安全验证", "请完成验证", "验证码", "滑块", "人机验证")


class LocalBrowserAutoPublisher:
    """Upload, fill and optionally submit one task on an official web form.

    The selector lists are intentionally short and exact-ish.  A platform DOM
    update is an action-required task, never a best-effort click around an
    unknown control.
    """

    def __init__(self, platform: PublishPlatform) -> None:
        if platform not in SPECS:
            raise ValueError(f"{platform.value} 尚未配置本机浏览器发布适配器。")
        self._platform = platform
        self._spec = SPECS[platform]

    def platform(self) -> str:
        return self._platform.value

    def capabilities(self) -> dict[str, str | bool | list[str]]:
        return {
            "provider_name": f"{self._platform.value}_local_browser",
            "display_name": f"{self._spec.display_name}本机扫码发布",
            "mode": "local_browser",
            "enabled": True,
            "requires_account": True,
            "setup_required": True,
            # 小红书当前仅支持在官方页面准备内容，最终发布必须由操作者点击。
            "manual_only": self._platform == PublishPlatform.XIAOHONGSHU,
            "manual_fallback": True,
            "supports_scheduled": False,
            "supports_tags": True,
            "supports_cover": False,
            "missing_configuration": [],
        }

    def publish(self, video_path: str, target: PublishTarget) -> PublishTask:
        if not target.account_id:
            raise RuntimeError("请先选择已核验的发布账号。")
        video = Path(video_path)
        if not video.is_file():
            raise RuntimeError("视频文件不存在。")
        try:
            account = publish_account_manager.get(target.account_id, platform=self._platform.value)
            account = publish_account_manager.verify_session(account.account_id)
        except PublishAccountError as exc:
            raise RuntimeError(str(exc)) from exc
        if account.status != "ready":
            raise RuntimeError(account.last_message)

        prepared, detail = self._prepare_draft(account.debug_port, str(video), target)
        now = datetime.now().astimezone()
        base_outputs = {"account_id": account.account_id, "account_name": account.name}
        if not prepared:
            return self._action_required_task(video, target, now, detail, base_outputs)
        if (
            self._platform == PublishPlatform.XIAOHONGSHU
            or not (target.auto_publish_authorized or account.auto_publish_authorized)
        ):
            final_action = (
                "请在小红书官方页面检查封面、可见范围和文案后手动点击发布。"
                if self._platform == PublishPlatform.XIAOHONGSHU
                else "请在官方页面检查封面、可见范围和文案后手动点击最终发布；或为该账号开启“授权自动发布”。"
            )
            return PublishTask(
                task_id=f"publish-{uuid4().hex[:10]}",
                title=f"{self._spec.display_name}待确认 · {target.title[:20]}",
                status=TaskStatus.PAUSED,
                progress=70,
                created_at=now,
                updated_at=now,
                video_path=str(video),
                target=target,
                publish_status=PublishStatus.MANUAL_READY,
                provider_name=f"{self._platform.value}_local_browser",
                stage="已在官方页面选择视频并填写内容，等待最终发布确认",
                action_required=final_action,
                is_mock=False,
                outputs={**base_outputs, "final_publish_requires_user": "true"},
            )

        submitted, evidence, succeeded = self._submit_and_verify(account.debug_port)
        if not submitted:
            return self._action_required_task(video, target, now, evidence, base_outputs)
        if succeeded:
            return PublishTask(
                task_id=f"publish-{uuid4().hex[:10]}", title=f"{self._spec.display_name}发布 · {target.title[:20]}",
                status=TaskStatus.SUCCEEDED, progress=100, created_at=now, updated_at=now,
                video_path=str(video), target=target, publish_status=PublishStatus.SUCCEEDED,
                provider_name=f"{self._platform.value}_local_browser", stage="平台页面已确认发布成功",
                final_publish_started_at=now, outcome_evidence=evidence, is_mock=False,
                outputs={**base_outputs, "final_publish_clicked": "true"},
            )
        return PublishTask(
            task_id=f"publish-{uuid4().hex[:10]}", title=f"{self._spec.display_name}发布待确认 · {target.title[:20]}",
            status=TaskStatus.OUTCOME_UNKNOWN, progress=90, created_at=now, updated_at=now,
            video_path=str(video), target=target, publish_status=PublishStatus.OUTCOME_UNKNOWN,
            provider_name=f"{self._platform.value}_local_browser", stage="已点击官方最终发布，等待平台处理结果",
            action_required="已执行最终发布点击，但未取得明确成功凭据；请在官方创作者后台确认。系统不会自动重试。",
            final_publish_started_at=now, outcome_evidence=evidence, is_mock=False,
            outputs={**base_outputs, "final_publish_clicked": "true"},
        )

    def check_status(self, task_id: str) -> PublishStatus:
        return PublishStatus.OUTCOME_UNKNOWN

    def get_published_url(self, task_id: str) -> str | None:
        return None

    def _action_required_task(self, video: Path, target: PublishTarget, now: datetime, detail: str, outputs: dict[str, str]) -> PublishTask:
        return PublishTask(
            task_id=f"publish-{uuid4().hex[:10]}", title=f"{self._spec.display_name}需要处理 · {target.title[:20]}",
            status=TaskStatus.PAUSED, progress=30, created_at=now, updated_at=now,
            video_path=str(video), target=target, publish_status=PublishStatus.ACTION_REQUIRED,
            provider_name=f"{self._platform.value}_local_browser", stage="官方页面需要你处理后才能继续",
            action_required=detail, is_mock=False, outputs=outputs,
        )

    @staticmethod
    def _content(target: PublishTarget) -> str:
        content = target.description.strip()
        if target.tags:
            suffix = " ".join(f"#{tag.lstrip('#')}" for tag in target.tags)
            content = f"{content}\n{suffix}".strip()
        return content

    @staticmethod
    def _requires_user(text: str) -> bool:
        return any(marker in text for marker in _ACTION_REQUIRED_MARKERS)

    def _prepare_draft(self, debug_port: int | None, video_path: str, target: PublishTarget) -> tuple[bool, str]:
        if not debug_port:
            return False, "未连接到本机官方窗口；请打开官方扫码窗口后核验账号。"
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False, "本机未安装 Playwright，无法自动填写；请在官方页面手动发布。"
        try:
            with sync_playwright() as playwright:
                browser = self._connect(playwright, debug_port)
                if browser is None:
                    return False, "无法连接本机官方窗口；请保持专用 Chrome 打开后再继续。"
                page = self._find_page(browser)
                page.goto(self._spec.upload_url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(1_500)
                if self._requires_user(page.locator("body").inner_text(timeout=8_000)):
                    return False, "官方页面要求扫码、验证码或安全验证；请在可见窗口完成后点击“验证后继续”。"
                self._set_local_file(page, video_path)
                self._fill_first(page, self._spec.title_selectors, target.title, required=True)
                content = self._content(target)
                if content:
                    self._fill_first(page, self._spec.description_selectors, content, required=False)
                return True, "已完成上传与内容填写。"
        except PlaywrightTimeoutError:
            return False, "官方页面仍在加载或上传，暂未找到预期表单；请在窗口中检查后继续。"
        except Exception as exc:
            return False, f"自动填写在最终发布前停止：{exc}"

    def _submit_and_verify(self, debug_port: int | None) -> tuple[bool, str, bool]:
        if not debug_port:
            return False, "未连接到本机官方窗口。", False
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False, "本机未安装 Playwright。", False
        try:
            with sync_playwright() as playwright:
                browser = self._connect(playwright, debug_port)
                if browser is None:
                    return False, "无法连接本机官方窗口。", False
                page = self._find_page(browser)
                if self._requires_user(page.locator("body").inner_text(timeout=8_000)):
                    return False, "官方页面要求验证码或安全验证；请由你完成，系统未点击最终发布。", False
                button = self._final_button(page)
                if button is None:
                    return False, "未找到唯一且可用的最终发布按钮；可能仍在上传或页面已改版。", False
                button.click(timeout=10_000)
                for name in self._spec.confirm_button_names:
                    confirm = page.get_by_role("button", name=name, exact=True)
                    if confirm.count() == 1 and confirm.is_visible() and confirm.is_enabled():
                        confirm.click(timeout=10_000)
                        break
                for _ in range(5):
                    page.wait_for_timeout(1_000)
                    url = page.url
                    body = page.locator("body").inner_text(timeout=5_000)
                    if any(fragment in url for fragment in self._spec.success_url_fragments) or any(text in body for text in self._spec.success_texts):
                        return True, f"已点击最终发布并检测到成功凭据：{url}", True
                return True, f"已点击官方最终发布按钮，尚未检测到成功凭据：{page.url}", False
        except Exception as exc:
            return False, f"最终发布前检查失败：{exc}", False

    @staticmethod
    def _connect(playwright, debug_port: int):
        for attempt in range(2):
            try:
                return playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
            except Exception:
                if attempt == 0:
                    time.sleep(0.6)
        return None

    def _find_page(self, browser):
        pages = [page for context in browser.contexts for page in context.pages]
        if not pages:
            raise RuntimeError("未找到官方创作者页面。")
        return next((page for page in pages if self._spec.host in page.url), pages[0])

    @staticmethod
    def _set_local_file(page, video_path: str) -> None:
        session = page.context.new_cdp_session(page)
        document = session.send("DOM.getDocument", {"depth": 1})
        node = session.send("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": "input[type='file']"})
        if not node.get("nodeId"):
            raise RuntimeError("未找到视频上传控件，官方页面可能已改版。")
        session.send("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(Path(video_path).resolve())]})

    @staticmethod
    def _fill_first(page, selectors: tuple[str, ...], value: str, *, required: bool) -> None:
        for selector in selectors:
            locator = page.locator(selector)
            if locator.count() < 1:
                continue
            candidate = locator.first
            try:
                candidate.wait_for(state="visible", timeout=12_000)
            except Exception:
                continue
            candidate.fill(value, timeout=10_000)
            return
        if required:
            raise RuntimeError("未找到标题输入框，官方页面可能已改版。")

    def _final_button(self, page):
        for name in self._spec.final_button_names:
            button = page.get_by_role("button", name=name, exact=True)
            if button.count() == 1 and button.is_visible() and button.is_enabled():
                return button
        return None


# Backwards-compatible name for integrations that imported the old class.
LocalBrowserManualPublisher = LocalBrowserAutoPublisher
