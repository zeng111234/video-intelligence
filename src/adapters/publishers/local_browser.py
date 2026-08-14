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

from src.models import (
    PublishPlatform,
    PublishStatus,
    PublishTarget,
    PublishTask,
    TaskStatus,
)
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
    final_button_selectors: tuple[str, ...] = ()
    description_includes_title: bool = False
    title_max_length: int | None = None


SPECS: dict[PublishPlatform, BrowserPublishSpec] = {
    PublishPlatform.KUAISHOU: BrowserPublishSpec(
        platform=PublishPlatform.KUAISHOU,
        display_name="快手",
        upload_url="https://cp.kuaishou.com/article/publish/video",
        host="cp.kuaishou.com",
        final_button_names=("发布",),
        confirm_button_names=("确认发布",),
        success_url_fragments=("/article/manage",),
        # 快手视频发布页没有独立标题框；标题需要放进作品描述。
        title_selectors=(),
        description_selectors=("#work-description-edit[contenteditable='true']",),
        # 当前官方页的最终操作是一个自定义 div，不具备 button 语义。
        final_button_selectors=("div[class*='_button-primary_']",),
        description_includes_title=True,
    ),
    PublishPlatform.WECHAT_CHANNELS: BrowserPublishSpec(
        platform=PublishPlatform.WECHAT_CHANNELS,
        display_name="视频号",
        upload_url="https://channels.weixin.qq.com/platform/post/create",
        host="channels.weixin.qq.com",
        final_button_names=("发表",),
        success_url_fragments=("/platform/post/list",),
        title_selectors=("input[placeholder*='标题']",),
        description_selectors=(
            ".post-desc-box .input-editor[contenteditable]",
            "textarea[placeholder*='描述']",
            "[contenteditable='true'][data-placeholder*='描述']",
        ),
        title_max_length=16,
    ),
    PublishPlatform.XIAOHONGSHU: BrowserPublishSpec(
        platform=PublishPlatform.XIAOHONGSHU,
        display_name="小红书",
        upload_url="https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video",
        host="creator.xiaohongshu.com",
        final_button_names=("发布",),
        success_url_fragments=("/publish/success",),
        title_selectors=(
            "input[placeholder*='填写标题']",
            "input[placeholder*='标题']",
        ),
        description_selectors=(
            "div.tiptap.ProseMirror[contenteditable='true'][role='textbox']",
            "p[data-placeholder*='输入正文描述']",
            "[contenteditable='true'][data-placeholder*='正文']",
            "textarea[placeholder*='描述']",
        ),
    ),
    PublishPlatform.BILIBILI: BrowserPublishSpec(
        platform=PublishPlatform.BILIBILI,
        display_name="Bilibili",
        upload_url="https://member.bilibili.com/platform/upload/video/frame",
        host="member.bilibili.com",
        final_button_names=("立即投稿", "投稿"),
        success_texts=("投稿成功", "稿件已提交"),
        title_selectors=(
            "input[placeholder*='稿件标题']",
            "input[placeholder*='标题']",
        ),
        description_selectors=(
            "div.ql-editor[contenteditable='true'][data-placeholder*='相关信息']",
            "textarea[placeholder*='简介']",
            "textarea[placeholder*='描述']",
            "[contenteditable='true'][data-placeholder*='简介']",
        ),
    ),
}

_ACTION_REQUIRED_MARKERS = (
    "扫码登录",
    "请登录",
    "安全验证",
    "请完成验证",
    "验证码",
    "滑块",
    "人机验证",
)


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
            "manual_only": False,
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
            account = publish_account_manager.get(
                target.account_id, platform=self._platform.value
            )
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
        if not (target.auto_publish_authorized or account.auto_publish_authorized):
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
                action_required="请在官方页面检查封面、可见范围和文案后手动点击最终发布；或为该账号开启“授权自动发布”。",
                is_mock=False,
                outputs={**base_outputs, "final_publish_requires_user": "true"},
            )

        submitted, evidence, succeeded = self._submit_and_verify(account.debug_port)
        if not submitted:
            return self._action_required_task(
                video, target, now, evidence, base_outputs
            )
        if succeeded:
            return PublishTask(
                task_id=f"publish-{uuid4().hex[:10]}",
                title=f"{self._spec.display_name}发布 · {target.title[:20]}",
                status=TaskStatus.SUCCEEDED,
                progress=100,
                created_at=now,
                updated_at=now,
                video_path=str(video),
                target=target,
                publish_status=PublishStatus.SUCCEEDED,
                provider_name=f"{self._platform.value}_local_browser",
                stage="平台页面已确认发布成功",
                final_publish_started_at=now,
                outcome_evidence=evidence,
                is_mock=False,
                outputs={**base_outputs, "final_publish_clicked": "true"},
            )
        return PublishTask(
            task_id=f"publish-{uuid4().hex[:10]}",
            title=f"{self._spec.display_name}发布待确认 · {target.title[:20]}",
            status=TaskStatus.OUTCOME_UNKNOWN,
            progress=90,
            created_at=now,
            updated_at=now,
            video_path=str(video),
            target=target,
            publish_status=PublishStatus.OUTCOME_UNKNOWN,
            provider_name=f"{self._platform.value}_local_browser",
            stage="已点击官方最终发布，等待平台处理结果",
            action_required="已执行最终发布点击，但未取得明确成功凭据；请在官方创作者后台确认。系统不会自动重试。",
            final_publish_started_at=now,
            outcome_evidence=evidence,
            is_mock=False,
            outputs={**base_outputs, "final_publish_clicked": "true"},
        )

    def check_status(self, task_id: str) -> PublishStatus:
        return PublishStatus.OUTCOME_UNKNOWN

    def get_published_url(self, task_id: str) -> str | None:
        return None

    def _action_required_task(
        self,
        video: Path,
        target: PublishTarget,
        now: datetime,
        detail: str,
        outputs: dict[str, str],
    ) -> PublishTask:
        return PublishTask(
            task_id=f"publish-{uuid4().hex[:10]}",
            title=f"{self._spec.display_name}需要处理 · {target.title[:20]}",
            status=TaskStatus.PAUSED,
            progress=30,
            created_at=now,
            updated_at=now,
            video_path=str(video),
            target=target,
            publish_status=PublishStatus.ACTION_REQUIRED,
            provider_name=f"{self._platform.value}_local_browser",
            stage="官方页面需要你处理后才能继续",
            action_required=detail,
            is_mock=False,
            outputs=outputs,
        )

    def _content(self, target: PublishTarget) -> str:
        parts: list[str] = []
        if self._spec.description_includes_title and target.title.strip():
            parts.append(target.title.strip())
        if target.description.strip() and target.description.strip() not in parts:
            parts.append(target.description.strip())
        if target.tags:
            tags = (
                target.tags[:4]
                if self._platform == PublishPlatform.KUAISHOU
                else target.tags
            )
            suffix = " ".join(f"#{tag.lstrip('#')}" for tag in tags)
            parts.append(suffix)
        return "\n".join(parts).strip()

    @staticmethod
    def _requires_user(text: str) -> bool:
        return any(marker in text for marker in _ACTION_REQUIRED_MARKERS)

    def _prepare_draft(
        self, debug_port: int | None, video_path: str, target: PublishTarget
    ) -> tuple[bool, str]:
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
                    return (
                        False,
                        "无法连接本机官方窗口；请保持专用 Chrome 打开后再继续。",
                    )
                page = self._find_page(browser)
                page.goto(
                    self._spec.upload_url, wait_until="domcontentloaded", timeout=30_000
                )
                page.wait_for_timeout(1_500)
                if self._requires_user(page.locator("body").inner_text(timeout=8_000)):
                    return (
                        False,
                        "官方页面要求扫码、验证码或安全验证；请在可见窗口完成后点击“验证后继续”。",
                    )
                self._set_local_file(page, video_path)
                if self._spec.title_selectors:
                    self._fill_first(
                        page,
                        self._spec.title_selectors,
                        self._title_value(target),
                        required=True,
                        field_label="标题",
                    )
                content = self._content(target)
                if content:
                    self._fill_first(
                        page,
                        self._spec.description_selectors,
                        content,
                        required=True,
                        field_label="正文",
                    )
                self._prepare_platform_fields(page)
                return True, "已完成上传与内容填写。"
        except PlaywrightTimeoutError:
            return (
                False,
                "官方页面仍在加载或上传，暂未找到预期表单；请在窗口中检查后继续。",
            )
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
                    return (
                        False,
                        "官方页面要求验证码或安全验证；请由你完成，系统未点击最终发布。",
                        False,
                    )
                self._wait_for_upload_ready(page)
                button = self._final_button(page)
                if button is None:
                    return (
                        False,
                        "未找到唯一且可用的最终发布按钮；可能仍在上传或页面已改版。",
                        False,
                    )
                button.click(timeout=10_000)
                for name in self._spec.confirm_button_names:
                    confirm = page.get_by_role("button", name=name, exact=True)
                    if (
                        confirm.count() == 1
                        and confirm.is_visible()
                        and confirm.is_enabled()
                    ):
                        confirm.click(timeout=10_000)
                        break
                for _ in range(5):
                    page.wait_for_timeout(1_000)
                    url = page.url
                    body = page.locator("body").inner_text(timeout=5_000)
                    if any(
                        fragment in url for fragment in self._spec.success_url_fragments
                    ) or any(text in body for text in self._spec.success_texts):
                        return True, f"已点击最终发布并检测到成功凭据：{url}", True
                return (
                    True,
                    f"已点击官方最终发布按钮，尚未检测到成功凭据：{page.url}",
                    False,
                )
        except Exception as exc:
            return False, f"最终发布前检查失败：{exc}", False

    def _wait_for_upload_ready(self, page) -> None:
        """Refuse the final click until the creator site confirms its upload.

        Kuaishou mounts the metadata form as soon as a local preview exists,
        several seconds before its chunked upload and server-side ``finish``
        request complete.  A visible description editor therefore is not an
        upload-complete signal.  The official page records the completed
        ``upload/finish`` request in Resource Timing; waiting for that exact
        first-party request keeps the final click behind the real upload
        boundary without calling a private API ourselves.
        """
        if self._platform != PublishPlatform.KUAISHOU:
            return
        try:
            page.wait_for_function(
                """
                () => performance.getEntriesByType('resource').some((entry) =>
                    entry.name.includes('/rest/cp/works/v2/video/pc/upload/finish') &&
                    entry.duration > 0
                )
                """,
                timeout=180_000,
            )
        except Exception as exc:
            raise RuntimeError(
                "快手视频仍在上传或处理，系统未点击最终发布；请保持官方窗口打开后再继续。"
            ) from exc

    @staticmethod
    def _connect(playwright, debug_port: int):
        for attempt in range(2):
            try:
                return playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{debug_port}"
                )
            except Exception:
                if attempt == 0:
                    time.sleep(0.6)
        return None

    def _find_page(self, browser):
        pages = [page for context in browser.contexts for page in context.pages]
        if not pages:
            raise RuntimeError("未找到官方创作者页面。")
        return next((page for page in pages if self._spec.host in page.url), pages[0])

    def _draft_form_ready(self, page, *, timeout: int = 0) -> bool:
        selectors = self._spec.description_selectors + self._spec.title_selectors
        for selector in selectors:
            locator = page.locator(selector)
            try:
                if timeout:
                    locator.first.wait_for(state="visible", timeout=timeout)
                if locator.count() and locator.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _title_value(self, target: PublishTarget) -> str:
        title = target.title.strip()
        if self._spec.title_max_length is not None:
            return title[: self._spec.title_max_length]
        return title

    def _prepare_platform_fields(self, page) -> None:
        if self._platform != PublishPlatform.BILIBILI:
            return

        cover = page.locator(".cover-main .cover-img")
        if not (cover.count() and cover.first.is_visible()):
            recommendations = page.locator(".cover-recommend-list .img-item-cover")
            visible_recommendations = [
                recommendations.nth(index)
                for index in range(recommendations.count())
                if recommendations.nth(index).is_visible()
            ]
            if not visible_recommendations:
                raise RuntimeError("B站没有提供可用的推荐封面，系统未继续发布。")
            visible_recommendations[0].click(timeout=10_000)
            cover.first.wait_for(state="visible", timeout=10_000)

        declaration = page.locator("input[placeholder*='创作声明']")
        if declaration.count() != 1 or not declaration.first.is_visible():
            raise RuntimeError("未找到B站唯一的创作声明字段，系统未继续发布。")
        if "含AI生成内容" in declaration.first.input_value(timeout=3_000):
            return

        selector = page.locator(".creation-statement-container .bcc-select-input-wrap")
        if selector.count() != 1 or not selector.first.is_visible():
            raise RuntimeError("未找到B站唯一的创作声明选择器，系统未继续发布。")
        selector.first.click(timeout=10_000)
        options = page.locator(".creation-statement-container li.bcc-option")
        matched = []
        for index in range(options.count()):
            option = options.nth(index)
            if (
                option.is_visible()
                and " ".join(option.inner_text(timeout=3_000).split()) == "含AI生成内容"
            ):
                matched.append(option)
        if len(matched) != 1:
            raise RuntimeError("未找到B站唯一的“含AI生成内容”声明，系统未继续发布。")
        matched[0].click(timeout=10_000)
        for _ in range(10):
            if "含AI生成内容" in declaration.first.input_value(timeout=3_000):
                return
            page.wait_for_timeout(300)
        raise RuntimeError("B站没有保存“含AI生成内容”声明，系统未继续发布。")

    def _set_local_file(self, page, video_path: str) -> None:
        # A resumed task may already be on the metadata form with its video
        # accepted.  Re-selecting the file would restart the upload and can
        # produce duplicate work on the creator site.
        if self._draft_form_ready(page):
            return
        # Creator pages mount their hidden file input asynchronously.  Querying the
        # initial CDP document immediately after navigation races that mount and
        # incorrectly reports that the upload control is missing.  Playwright's
        # attached-state wait works for hidden file inputs and still sets the local
        # path directly without opening a native file chooser.
        inputs = page.locator("input[type='file'][accept*='video']")
        if inputs.count() < 1:
            inputs = page.locator("input[type='file']")
        candidate = inputs.first
        try:
            candidate.wait_for(state="attached", timeout=20_000)
            candidate.set_input_files(str(Path(video_path).resolve()), timeout=20_000)
        except Exception as exc:
            # Some creator sites replace the upload DOM while Playwright is still
            # dispatching the file event.  If the verified metadata form appeared,
            # the upload was accepted and preparation should continue.
            if self._draft_form_ready(page, timeout=10_000):
                return
            raise RuntimeError(
                "未找到可用的视频上传控件，官方页面可能仍在加载或已经改版。"
            ) from exc

    def _fill_first(
        self,
        page,
        selectors: tuple[str, ...],
        value: str,
        *,
        required: bool,
        field_label: str,
    ) -> None:
        expected = " ".join(value.split())
        for selector in selectors:
            locator = page.locator(selector)
            # The metadata form is mounted only after the creator site accepts
            # the selected video.  Wait on the locator itself so a temporarily
            # absent editor is not mistaken for a permanent page change.
            try:
                locator.first.wait_for(state="visible", timeout=60_000)
            except Exception:
                continue
            count = locator.count()
            if count < 1:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    candidate.wait_for(state="visible", timeout=12_000)
                except Exception:
                    continue
                try:
                    candidate.fill(value, timeout=10_000)
                    actual = candidate.input_value(timeout=3_000)
                except Exception:
                    try:
                        candidate.fill(value, timeout=10_000)
                        actual = candidate.inner_text(timeout=3_000)
                    except Exception:
                        continue
                normalized_actual = " ".join(str(actual or "").split())
                if normalized_actual == expected or expected in normalized_actual:
                    return
        if required:
            raise RuntimeError(
                f"未能在{self._spec.display_name}官方页面确认{field_label}已填入，"
                "页面可能已改版，请更新软件后重试。"
            )

    def _final_button(self, page):
        for name in self._spec.final_button_names:
            button = page.get_by_role("button", name=name, exact=True)
            if button.count() == 1 and button.is_visible() and button.is_enabled():
                return button
        expected_names = {
            " ".join(name.split()) for name in self._spec.final_button_names
        }
        for selector in self._spec.final_button_selectors:
            candidates = page.locator(selector)
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                if not candidate.is_visible() or not candidate.is_enabled():
                    continue
                text = " ".join(candidate.inner_text(timeout=3_000).split())
                if text in expected_names:
                    return candidate
        return None


# Backwards-compatible name for integrations that imported the old class.
LocalBrowserManualPublisher = LocalBrowserAutoPublisher
