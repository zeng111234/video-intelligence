"""User-visible, local-browser publishing assistant for Douyin.

This adapter opens only the official creator site in a dedicated local profile.
It clicks the final button only when that specific account has an explicit
local authorization; verification/CAPTCHA always remains user-operated.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
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
DOUYIN_VIDEO_TITLE_SELECTOR = "input[placeholder*='填写作品标题']"
DOUYIN_VIDEO_DESCRIPTION_SELECTOR = "div.zone-container[contenteditable='true']"


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
        ) if not target.use_prepared_page else (
            f"已在账号“{account.name}”的官方页面等待视频上传完成。"
        )
        selected_music = re.search(r"推荐配乐《([^》]+)》", stage)
        if selected_music:
            target = target.model_copy(
                update={"selected_music_title": selected_music.group(1)[:100]}
            )
        now = datetime.now().astimezone()
        if stage.startswith(("需要你完成验证：", "自动配乐未完成：")):
            return PublishTask(
                task_id=f"publish-{uuid4().hex[:10]}",
                title=f"抖音等待处理 · {target.title[:20]}",
                status=TaskStatus.PAUSED,
                progress=55,
                created_at=now,
                updated_at=now,
                video_path=str(video),
                target=target,
                publish_status=PublishStatus.ACTION_REQUIRED,
                provider_name="douyin_local_browser",
                stage="等待登录验证" if stage.startswith("需要你") else "自动配乐已安全停止",
                action_required=stage.split("：", 1)[-1],
                is_mock=False,
                outputs={
                    "account_id": account.account_id,
                    "account_name": account.name,
                    "native_music_mode": target.native_music_mode,
                },
            )
        auto_publish_authorized = (
            target.auto_publish_authorized or account.auto_publish_authorized
        )
        if auto_publish_authorized and stage.startswith("已在账号"):
            submitted, succeeded, evidence, needs_attention = (
                self._submit_final_publish(
                    debug_port=account.debug_port,
                    target=target,
                )
            )
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
                        "task_auto_publish_authorized": (
                            "true" if target.auto_publish_authorized else "false"
                        ),
                        "selected_music_title": target.selected_music_title or "",
                    },
                )
            if needs_attention:
                return PublishTask(
                    task_id=f"publish-{uuid4().hex[:10]}",
                    title=f"抖音等待处理 · {target.title[:20]}",
                    status=TaskStatus.PAUSED,
                    progress=90,
                    created_at=now,
                    updated_at=now,
                    video_path=str(video),
                    target=target,
                    publish_status=PublishStatus.ACTION_REQUIRED,
                    provider_name="douyin_local_browser",
                    stage="发布前需要你处理",
                    action_required=evidence,
                    is_mock=False,
                    outputs={
                        "account_id": account.account_id,
                        "account_name": account.name,
                        "task_auto_publish_authorized": (
                            "true" if target.auto_publish_authorized else "false"
                        ),
                        "selected_music_title": target.selected_music_title or "",
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
            action_required=(
                "请检查官方页面后点击“确认并自动发布”；系统会等待上传完成并且只点击一次。"
                if not auto_publish_authorized
                else "自动发布条件尚未满足，请检查官方页面后再确认一次。"
            ),
            is_mock=False,
            outputs={
                "account_id": account.account_id,
                "account_name": account.name,
                "final_publish_requires_user": (
                    "true" if not auto_publish_authorized else "false"
                ),
                "selected_music_title": target.selected_music_title or "",
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
                login_markers = ("扫码登录", "请登录", "安全验证", "请完成验证")
                verification_deadline = time.monotonic() + 120
                page_text = page.locator("body").inner_text(timeout=8_000)
                while (
                    any(marker in page_text for marker in login_markers)
                    and time.monotonic() < verification_deadline
                ):
                    page.bring_to_front()
                    page.wait_for_timeout(1_000)
                    page_text = page.locator("body").inner_text(timeout=8_000)
                if any(marker in page_text for marker in login_markers):
                    return (
                        "需要你完成验证："
                        f"账号“{account_name}”正在等待你在官方窗口扫码或输入验证码；"
                        "完成后系统可继续处理。"
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

                title_input = page.locator(DOUYIN_VIDEO_TITLE_SELECTOR)
                try:
                    title_input.first.wait_for(state="visible", timeout=120_000)
                except PlaywrightTimeoutError as exc:
                    raise RuntimeError(
                        "视频已交给官方页面，但未找到作品标题输入框；"
                        "为避免误填评论区，系统已停止。"
                    ) from exc
                if title_input.count() != 1 or not DouyinBrowserPublisher._is_publish_page(
                    page.url
                ):
                    raise RuntimeError(
                        "未确认进入唯一的视频发布表单；为避免误填评论区，系统已停止。"
                    )
                title_input.first.fill(target.title[:30], timeout=10_000)

                content = DouyinBrowserPublisher._content(target)
                if content:
                    description_input = page.locator(
                        DOUYIN_VIDEO_DESCRIPTION_SELECTOR
                    )
                    try:
                        description_input.first.wait_for(
                            state="visible", timeout=120_000
                        )
                    except PlaywrightTimeoutError as exc:
                        raise RuntimeError(
                            "未找到作品描述输入框；为避免把文案发成评论，系统已停止。"
                        ) from exc
                    if description_input.count() != 1:
                        raise RuntimeError(
                            "作品描述输入框不是唯一项；为避免把文案发成评论，系统已停止。"
                        )
                    description_input.first.fill(content, timeout=10_000)
                music_title = None
                if target.native_music_mode == "auto_recommended":
                    music_selected, music_title, music_evidence = (
                        DouyinBrowserPublisher._select_recommended_music(page, target)
                    )
                    if not music_selected:
                        page.bring_to_front()
                        return f"自动配乐未完成：{music_evidence}"
                verified_target = (
                    target.model_copy(update={"selected_music_title": music_title})
                    if music_title
                    else target
                )
                form_matches, mismatch_reason = (
                    DouyinBrowserPublisher._prepared_form_matches(page, verified_target)
                )
                if not form_matches:
                    raise RuntimeError(mismatch_reason)
                page.bring_to_front()
                music_summary = (
                    f"并自动选择抖音推荐配乐《{music_title}》"
                    if music_title
                    else ""
                )
                return (
                    f"已在账号“{account_name}”的作品发布表单填写标题、描述和标签"
                    f"{music_summary}；系统将继续等待上传完成并提交。"
                )
        except PlaywrightTimeoutError:
            return "官方页面仍在上传或加载，请在打开的窗口中继续等待并检查内容；系统不会代替你点击发布。"

    @staticmethod
    def _is_upload_page(url: str) -> bool:
        return "/creator-micro/content/upload" in url

    @staticmethod
    def _is_publish_page(url: str) -> bool:
        return any(
            marker in url
            for marker in (
                "/creator-micro/content/publish",
                "/creator-micro/content/post/video",
            )
        )

    @staticmethod
    def _submit_final_publish(
        *,
        debug_port: int | None,
        target: PublishTarget,
    ) -> tuple[bool, bool, str, bool]:
        """Wait for one unambiguous submit control, click once, then verify."""
        if not debug_port:
            return False, False, "未连接到本机官方窗口。", True
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False, False, "Playwright 未安装。", True
        click_attempted = False
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
                    return False, False, "官方窗口连接失败。", True
                pages = [page for context in browser.contexts for page in context.pages]
                page = next(
                    (
                        item
                        for item in pages
                        if "creator.douyin.com" in item.url
                        and DouyinBrowserPublisher._is_publish_page(item.url)
                    ),
                    None,
                )
                if page is None:
                    return False, False, "未找到抖音作品发布页面。", True

                deadline = time.monotonic() + 120
                button = None
                form_evidence = "作品发布表单仍在加载。"
                challenge_seen = False
                while time.monotonic() < deadline:
                    body_text = page.locator("body").inner_text(timeout=8_000)
                    challenge_markers = (
                        "安全验证",
                        "短信验证码",
                        "拖动滑块",
                        "请完成验证",
                    )
                    if any(marker in body_text for marker in challenge_markers):
                        challenge_seen = True
                        page.bring_to_front()
                        page.wait_for_timeout(1_000)
                        continue
                    form_matches, form_evidence = (
                        DouyinBrowserPublisher._prepared_form_matches(page, target)
                    )
                    if not form_matches:
                        page.wait_for_timeout(1_000)
                        continue
                    candidate = page.get_by_role("button", name="发布", exact=True)
                    if (
                        candidate.count() == 1
                        and candidate.is_visible()
                        and candidate.is_enabled()
                    ):
                        button = candidate
                        break
                    page.wait_for_timeout(1_000)
                if button is None:
                    page.bring_to_front()
                    if challenge_seen:
                        return (
                            False,
                            False,
                            "抖音仍在等待安全验证；请在官方窗口完成后，系统将从现有任务继续，不会重复发布。",
                            True,
                        )
                    return (
                        False,
                        False,
                        f"自动发布已停止：{form_evidence} "
                        "请检查作品发布表单、上传、封面和可见范围。",
                        True,
                    )

                click_attempted = True
                button.click(timeout=10_000)
                for _ in range(45):
                    page.wait_for_timeout(1_000)
                    if "/creator-micro/content/manage" in page.url:
                        return (
                            True,
                            True,
                            f"已点击唯一发布按钮并进入内容管理页：{page.url}",
                            False,
                        )
                return (
                    True,
                    False,
                    f"已点击官方页面唯一的“发布”按钮，尚未检测到成功页：{page.url}",
                    False,
                )
        except Exception as exc:
            if click_attempted:
                return (
                    True,
                    False,
                    f"已经尝试点击最终发布，但结果无法确认：{exc}",
                    False,
                )
            return False, False, f"自动点击前检查失败：{exc}", True

    @staticmethod
    def _content(target: PublishTarget) -> str:
        content = target.description.strip()
        if target.tags:
            suffix = " ".join(f"#{tag.lstrip('#')}" for tag in target.tags)
            content = f"{content}\n{suffix}".strip()
        return content

    @staticmethod
    def _select_recommended_music(
        page,
        target: PublishTarget,
    ) -> tuple[bool, str | None, str]:
        """Select the top official recommendation and verify its visible title.

        Music choice is reversible, but the selector still fails closed: the
        adapter never clicks a generic element or continues to final publish
        without reading a selected title back from the official page.
        """
        if not DouyinBrowserPublisher._is_publish_page(page.url):
            return False, None, "当前页面不是抖音视频作品发布页。"

        challenge_markers = (
            "扫码登录",
            "请登录",
            "安全验证",
            "短信验证码",
            "请完成验证",
        )
        verification_deadline = time.monotonic() + 120
        body_text = page.locator("body").inner_text(timeout=8_000)
        while (
            any(marker in body_text for marker in challenge_markers)
            and time.monotonic() < verification_deadline
        ):
            page.bring_to_front()
            page.wait_for_timeout(1_000)
            body_text = page.locator("body").inner_text(timeout=8_000)
        if any(marker in body_text for marker in challenge_markers):
            return False, None, "抖音要求先完成登录或安全验证。"

        picker_candidates = []
        for name in ("选择音乐", "添加音乐", "配乐"):
            candidate = page.get_by_role("button", name=name, exact=True)
            if candidate.count() == 1 and candidate.first.is_visible():
                picker_candidates.append(candidate.first)
        if len(picker_candidates) != 1:
            return (
                False,
                None,
                "未找到唯一的抖音音乐入口，页面可能已改版；系统没有猜测点击。",
            )
        picker_candidates[0].click(timeout=10_000)
        page.wait_for_timeout(800)

        recommended_tab = page.get_by_text("推荐", exact=True)
        if recommended_tab.count() == 1 and recommended_tab.first.is_visible():
            recommended_tab.first.click(timeout=10_000)
            page.wait_for_timeout(500)

        use_buttons = page.get_by_role("button", name="使用", exact=True)
        chosen = None
        for index in range(use_buttons.count()):
            candidate = use_buttons.nth(index)
            if candidate.is_visible() and candidate.is_enabled():
                chosen = candidate
                break
        if chosen is None:
            return False, None, "抖音推荐音乐列表中没有可用曲目。"

        raw_candidate_text = str(
            chosen.evaluate(
                """element => {
                    let node = element;
                    for (let depth = 0; depth < 6 && node; depth += 1, node = node.parentElement) {
                        const text = (node.innerText || "").trim();
                        if (text && text !== "使用" && text.length <= 160) return text;
                    }
                    return "";
                }"""
            )
            or ""
        )
        ignored = {"使用", "推荐", "热门", "试听", "收藏"}
        music_title = next(
            (
                line.strip()
                for line in raw_candidate_text.splitlines()
                if line.strip()
                and line.strip() not in ignored
                and not line.strip().replace(":", "").isdigit()
            ),
            "",
        )
        if not music_title:
            return False, None, "无法读取推荐音乐名称，系统没有继续提交。"

        chosen.click(timeout=10_000)
        page.wait_for_timeout(600)
        for name in ("完成", "确认", "应用"):
            confirm = page.get_by_role("button", name=name, exact=True)
            if (
                confirm.count() == 1
                and confirm.first.is_visible()
                and confirm.first.is_enabled()
            ):
                confirm.first.click(timeout=10_000)
                page.wait_for_timeout(500)
                break

        selected_label = page.get_by_text(music_title, exact=True)
        selected_visible = any(
            selected_label.nth(index).is_visible()
            for index in range(selected_label.count())
        )
        if not selected_visible:
            return (
                False,
                None,
                f"已尝试选择《{music_title}》，但官方页面没有回显选中结果；系统没有继续提交。",
            )
        return (
            True,
            music_title[:100],
            f"已选择抖音官方推荐音乐《{music_title[:100]}》。",
        )

    @staticmethod
    def _prepared_form_matches(
        page,
        target: PublishTarget,
    ) -> tuple[bool, str]:
        """Verify the exact video form before allowing the final publish click."""
        if not DouyinBrowserPublisher._is_publish_page(page.url):
            return False, "当前页面不是抖音视频作品发布页。"

        title_input = page.locator(DOUYIN_VIDEO_TITLE_SELECTOR)
        if (
            title_input.count() != 1
            or not title_input.first.is_visible()
            or title_input.first.input_value().strip() != target.title[:30].strip()
        ):
            return False, "作品标题未写入唯一的标题输入框。"

        if target.native_music_mode == "auto_recommended":
            if not target.selected_music_title:
                return False, "自动配乐任务没有保存已选音乐名称。"
            selected_music = page.get_by_text(
                target.selected_music_title,
                exact=True,
            )
            if selected_music.count() < 1 or not any(
                selected_music.nth(index).is_visible()
                for index in range(selected_music.count())
            ):
                return False, "官方页面没有回显已选抖音音乐；系统不会继续发布。"

        expected_content = DouyinBrowserPublisher._content(target)
        if not expected_content:
            return True, "作品发布表单已核对。"

        description_input = page.locator(DOUYIN_VIDEO_DESCRIPTION_SELECTOR)
        if description_input.count() != 1 or not description_input.first.is_visible():
            return False, "未找到唯一的作品描述输入框；系统不会使用通用编辑框。"

        actual_content = " ".join(description_input.first.inner_text().split())
        expected_description = " ".join(target.description.strip().split())
        if expected_description and expected_description not in actual_content:
            return False, "作品描述没有写入作品发布表单。"
        for tag in target.tags:
            normalized_tag = tag.lstrip("#").strip()
            if normalized_tag and normalized_tag not in actual_content:
                return False, f"作品标签“{normalized_tag}”没有写入作品发布表单。"
        return True, "作品标题、描述和标签已写入作品发布表单。"

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
