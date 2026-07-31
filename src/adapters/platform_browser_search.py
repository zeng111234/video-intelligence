"""Low-frequency multi-platform discovery in dedicated Chrome profiles.

The browser itself performs the normal platform search and creates any required
request headers.  This adapter only reads metadata returned to, or rendered by,
that visible browser session.  It does not copy private signing algorithms,
export cookies, solve verification challenges, or download media.
"""

from __future__ import annotations

import html
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from pydantic import HttpUrl

from src.adapters.douyin_browser_search import BrowserSessionStatus
from src.adapters.licensed import LicensedProviderError
from src.models import (
    DataSource,
    Platform,
    ProviderCapability,
    ProviderErrorKind,
    ProviderMode,
    ProviderSearchItem,
    ProviderSearchPage,
    ProviderUsage,
    VideoMetricSnapshot,
)
@dataclass(frozen=True)
class _PlatformSpec:
    platform: Platform
    label: str
    home_url: str
    search_url: str
    page_host: str
    link_selector: str
    item_id_pattern: re.Pattern[str]
    login_markers: tuple[str, ...]


_SPECS = {
    Platform.XIAOHONGSHU: _PlatformSpec(
        platform=Platform.XIAOHONGSHU,
        label="小红书",
        home_url="https://www.xiaohongshu.com/",
        search_url=(
            "https://www.xiaohongshu.com/search_result?"
            "keyword={keyword}&source=web_search_result_notes&type=51"
        ),
        page_host="xiaohongshu.com",
        link_selector="a[href*='/explore/']",
        item_id_pattern=re.compile(r"/explore/([a-zA-Z0-9]+)"),
        login_markers=("扫码登录", "登录后查看更多", "请通过验证", "安全验证"),
    ),
    Platform.KUAISHOU: _PlatformSpec(
        platform=Platform.KUAISHOU,
        label="快手",
        home_url="https://www.kuaishou.com/",
        search_url="https://www.kuaishou.com/search/video?searchKey={keyword}",
        page_host="kuaishou.com",
        link_selector="a[href*='/short-video/']",
        item_id_pattern=re.compile(r"/short-video/([a-zA-Z0-9_-]+)"),
        login_markers=("扫码登录", "登录后查看更多", "请完成验证", "安全验证"),
    ),
    Platform.BILIBILI: _PlatformSpec(
        platform=Platform.BILIBILI,
        label="B站",
        home_url="https://www.bilibili.com/",
        search_url="https://search.bilibili.com/all?keyword={keyword}&order=click",
        page_host="bilibili.com",
        link_selector="a[href*='/video/BV']",
        item_id_pattern=re.compile(r"/video/(BV[a-zA-Z0-9]+)"),
        login_markers=("扫码登录", "请完成验证", "安全验证"),
    ),
}

_ACCESS_MARKERS = ("访问频繁", "操作频繁", "请求过于频繁", "网络环境存在风险")
_HARD_VERIFICATION_MARKERS = ("请通过验证", "请完成验证", "安全验证")
_MAX_SCROLL_ROUNDS = 18
_RAW_TARGET_FLOOR = 90
_ADAPTER_VERSION = "visible_browser_network_v1"


class LocalPlatformBrowserSearchProvider:
    """Metadata-only browser provider for one supported platform."""

    source_type = DataSource.PUBLIC_RESEARCH
    adapter_version = _ADAPTER_VERSION

    def __init__(
        self,
        *,
        platform: Platform,
        enabled: bool,
        profile_dir: Path,
        browser_channel: str = "chrome",
        debug_port: int,
        timeout_seconds: float = 35.0,
        clock=None,
    ) -> None:
        if platform not in _SPECS:
            raise ValueError(f"不支持的本机浏览器平台：{platform.value}")
        self.platform = platform
        self.spec = _SPECS[platform]
        # 小红书已切换为人工素材模式。把限制放在适配器内部，避免其他调用方
        # 通过旧环境变量或直接构造实例重新启用浏览器自动化。
        self.enabled = enabled and platform != Platform.XIAOHONGSHU
        self.profile_dir = profile_dir
        self.browser_channel = browser_channel
        self.debug_port = debug_port
        self.timeout_seconds = max(10.0, min(float(timeout_seconds), 60.0))
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.provider_name = f"{platform.value}_local_browser"

    def capabilities(self) -> ProviderCapability:
        missing = self._missing_prerequisites()
        return ProviderCapability(
            provider_name=self.provider_name,
            display_name=f"本机 Chrome {self.spec.label}搜索",
            mode=ProviderMode.LOCAL_BROWSER,
            enabled=not missing,
            supported_platforms=[self.platform] if not missing else [],
            max_page_size=30,
            supports_published_after=True,
            supports_metric_refresh=False,
            supports_usage=False,
            permission_status="public_browser_optional_login",
            credential_alias=f"local-{self.platform.value}-browser-profile",
            missing_configuration=missing,
        )

    def session_status(self) -> BrowserSessionStatus:
        missing = self._missing_prerequisites()
        if not self.enabled:
            return BrowserSessionStatus(
                False,
                False,
                True,
                False,
                "disabled",
                (
                    "小红书安全模式已开启：不会自动打开、浏览或读取小红书账号。"
                    if self.platform == Platform.XIAOHONGSHU
                    else f"{self.spec.label}浏览器搜索已关闭。"
                ),
            )
        if missing:
            return BrowserSessionStatus(
                True,
                False,
                True,
                False,
                "dependency_missing",
                f"{self.spec.label}浏览器尚未就绪：{'；'.join(missing)}。",
            )
        try:
            with urlopen(self._debug_url(), timeout=0.6) as response:  # noqa: S310 - localhost only
                payload = json.loads(response.read().decode("utf-8"))
            with urlopen(self._debug_pages_url(), timeout=0.6) as response:  # noqa: S310 - localhost only
                pages = json.loads(response.read().decode("utf-8"))
        except (URLError, OSError, ValueError, json.JSONDecodeError):
            return BrowserSessionStatus(
                True,
                False,
                True,
                False,
                "browser_closed",
                f"{self.spec.label}浏览器尚未打开；开始找素材时会自动打开。",
            )

        product = str(payload.get("Browser") or "Chrome")
        platform_pages = [
            item
            for item in pages
            if isinstance(item, dict)
            and self.spec.page_host in str(item.get("url") or "").casefold()
        ]
        if not platform_pages:
            return BrowserSessionStatus(
                True,
                True,
                True,
                False,
                "browser_open",
                f"{product} 已打开，正在进入{self.spec.label}公开页面。",
            )
        login_required = self._visible_login_required()
        if login_required is True:
            return BrowserSessionStatus(
                True,
                True,
                True,
                False,
                "waiting_login",
                f"请在专用 Chrome 窗口完成{self.spec.label}扫码登录或人工验证。",
            )
        if login_required is None:
            return BrowserSessionStatus(
                True,
                True,
                False,
                True,
                "ready",
                f"{self.spec.label}公开页面已打开；将直接尝试读取公开搜索结果。",
            )
        return BrowserSessionStatus(
            True,
            True,
            False,
            True,
            "ready",
            f"{self.spec.label}公开浏览器已就绪；只读取搜索页已加载的作品元数据。",
        )

    def open_login_browser(self) -> BrowserSessionStatus:
        """Show a user-facing window for QR login or manual verification."""
        return self._start_browser(visible=True)

    def start_login_browser(self) -> BrowserSessionStatus:
        """Start the dedicated profile without interrupting background work."""
        return self._start_browser(visible=False)

    def _start_browser(self, *, visible: bool) -> BrowserSessionStatus:
        status = self.session_status()
        if status.running and not visible:
            return status
        capability = self.capabilities()
        if not capability.enabled:
            raise LicensedProviderError(
                f"{self.spec.label}浏览器尚未就绪：{'；'.join(capability.missing_configuration)}。",
                kind=ProviderErrorKind.AUTHORIZATION,
            )
        executable = self._browser_executable()
        if executable is None:
            raise LicensedProviderError(
                "未找到 Chrome/Edge，请安装浏览器后重试。",
                kind=ProviderErrorKind.VALIDATION,
            )
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        browser_args = [
            str(executable),
            f"--remote-debugging-port={self.debug_port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if visible:
            browser_args.extend(
                ["--new-window", "--window-position=80,80", "--window-size=1100,800"]
            )
        else:
            browser_args.extend(
                ["--start-minimized", "--window-position=-32000,-32000", "--window-size=900,700"]
            )
        browser_args.append(self.spec.home_url)
        subprocess.Popen(  # noqa: S603 - executable is resolved from an allowlist
            browser_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for delay_seconds in (0.5, 1.0, 1.5):
            time.sleep(delay_seconds)
            status = self.session_status()
            if status.running:
                return status
        return BrowserSessionStatus(
            True,
            False,
            False,
            False,
            "starting",
            (
                f"{self.spec.label}登录窗口正在打开，请在可见窗口中扫码或完成人工验证。"
                if visible
                else f"{self.spec.label}公开浏览器正在后台启动；系统随后会自动开始搜索。"
            ),
        )

    def search(
        self,
        platform: Platform,
        keyword: str,
        published_after: datetime | None,
        limit: int,
        idempotency_key: str,
        hotspot_window_hours: int | None = None,
    ) -> ProviderSearchPage:
        del hotspot_window_hours, published_after
        if platform != self.platform:
            raise LicensedProviderError(
                f"{self.spec.label}适配器不能搜索其他平台。",
                kind=ProviderErrorKind.VALIDATION,
            )
        if not 1 <= limit <= 30:
            raise LicensedProviderError(
                f"{self.spec.label}每次最多保留 30 条候选。",
                kind=ProviderErrorKind.VALIDATION,
            )
        status = self.session_status()
        if not status.running or status.login_required:
            raise LicensedProviderError(
                status.message,
                kind=ProviderErrorKind.AUTHORIZATION,
                retryable=False,
            )

        observed_at = self.clock()
        raw_rows = self._collect_rows(keyword, target=max(_RAW_TARGET_FLOOR, limit + 10))
        parsed_items = self._to_items(
            raw_rows,
            observed_at=observed_at,
            limit=len(raw_rows),
        )
        return ProviderSearchPage(
            platform=self.platform,
            provider=self.provider_name,
            items=parsed_items,
            observed_at=observed_at,
            request_id=f"browser-{self.platform.value}-{idempotency_key[:16]}",
            api_call_count=0,
            billable_units=0,
            has_more=len(parsed_items) > limit or len(raw_rows) > len(parsed_items),
            raw_item_count=len(raw_rows),
            parsed_item_count=len(parsed_items),
            payload_diagnostic=(
                f"使用{self.spec.label}专用浏览器正常搜索；优先读取浏览器收到的搜索元数据，"
                "发现数量是页面搜索结果，系统会再按关键词、发布时间和字段完整性筛选。"
            ),
        )

    def refresh_metrics(
        self,
        platform: Platform,
        platform_item_ids: list[str],
        idempotency_key: str,
    ) -> ProviderSearchPage:
        del platform_item_ids, idempotency_key
        return ProviderSearchPage(
            platform=platform,
            provider=self.provider_name,
            observed_at=self.clock(),
            request_id=f"{self.platform.value}-refresh-not-supported",
            payload_diagnostic="免费浏览器来源不自动复采，避免增加账号风控。",
        )

    def usage(self) -> ProviderUsage | None:
        return None

    def _collect_rows(self, keyword: str, *, target: int) -> list[dict[str, Any]]:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        endpoint = f"http://127.0.0.1:{self.debug_port}"
        network_rows: dict[str, dict[str, Any]] = {}
        rendered_rows: dict[str, dict[str, Any]] = {}
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.connect_over_cdp(endpoint)
                context = browser.contexts[0]
                page = context.new_page()
            except PlaywrightError as exc:
                raise LicensedProviderError(
                    f"{self.spec.label}浏览器连接失败；请重新打开专用浏览器。",
                    kind=ProviderErrorKind.CONNECTION,
                    retryable=False,
                ) from exc

            def capture_response(response) -> None:
                if not self._is_search_response_url(response.url):
                    return
                try:
                    payload = response.json()
                except Exception:
                    return
                for row in self._rows_from_payload(payload):
                    item_id = str(row.get("item_id") or "")
                    if item_id:
                        network_rows[item_id] = row

            page.on("response", capture_response)
            try:
                page.set_default_timeout(int(self.timeout_seconds * 1000))
                # Keep the human-entered keyword intact here.  Playwright will
                # encode it once; pre-encoding makes Xiaohongshu encode the
                # percent signs again and search for the wrong literal text.
                url = self.spec.search_url.format(keyword=keyword.strip())
                response = page.goto(url, wait_until="domcontentloaded")
                if response is not None and response.status in {403, 412, 429}:
                    raise LicensedProviderError(
                        f"{self.spec.label}返回 {response.status}，已停止搜索。",
                        kind=ProviderErrorKind.RATE_LIMIT,
                        retryable=False,
                    )
                page.wait_for_timeout(2500)
                self._apply_platform_filters(page)
                page.wait_for_timeout(1500)
                self._raise_for_visible_block(page)
                stagnant_rounds = 0
                previous_count = -1
                for _ in range(_MAX_SCROLL_ROUNDS):
                    for row in self._rendered_rows(page):
                        item_id = str(row.get("item_id") or "")
                        if item_id:
                            rendered_rows[item_id] = row
                    current_count = len(set(network_rows) | set(rendered_rows))
                    if current_count >= target:
                        break
                    stagnant_rounds = (
                        stagnant_rounds + 1 if current_count == previous_count else 0
                    )
                    if stagnant_rounds >= 3:
                        break
                    previous_count = current_count
                    page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.85, 600))")
                    page.wait_for_timeout(random.randint(900, 1400))
                    self._raise_for_visible_block(page)
                if not network_rows and not rendered_rows:
                    self._raise_for_login_gate(page)
            finally:
                page.close()

        merged = dict(rendered_rows)
        for item_id, row in network_rows.items():
            merged[item_id] = {**merged.get(item_id, {}), **row}
        return list(merged.values())

    def _apply_platform_filters(self, page) -> None:
        """Use the visible platform controls before reading result metadata."""
        if self.platform == Platform.XIAOHONGSHU:
            for group, option in (
                ("排序依据", "最多点赞"),
                ("笔记类型", "视频"),
                ("发布时间", "一周内"),
            ):
                panel = page.locator(".filter-panel")
                if not panel.count() or not panel.is_visible():
                    filter_control = page.locator("div.filter")
                    if not filter_control.count():
                        raise LicensedProviderError(
                            "小红书页面没有显示筛选按钮，已停止本次搜索。",
                            kind=ProviderErrorKind.VALIDATION,
                            retryable=False,
                        )
                    for attempt in range(2):
                        filter_control.first.click(force=True)
                        page.wait_for_timeout(500)
                        panel = page.locator(".filter-panel")
                        if panel.count() and panel.is_visible():
                            break
                        if attempt == 0:
                            # Xiaohongshu sometimes renders results before the
                            # filter component has finished hydrating.
                            page.wait_for_timeout(6500)
                    else:
                        raise LicensedProviderError(
                            "小红书筛选按钮尚未就绪，已停止本次搜索。",
                            kind=ProviderErrorKind.CONNECTION,
                            retryable=False,
                        )
                selected = page.evaluate(
                    """({group, option}) => {
                        const sections = [...document.querySelectorAll(
                            ".filter-panel .filters"
                        )];
                        const section = sections.find((element) =>
                            (element.querySelector(":scope > span")?.textContent || "")
                                .trim() === group
                        );
                        const tag = section && [...section.querySelectorAll(".tags")]
                            .find((element) => (element.innerText || "").trim() === option);
                        if (!tag) return false;
                        tag.click();
                        return true;
                    }""",
                    {"group": group, "option": option},
                )
                if not selected:
                    raise LicensedProviderError(
                        f"小红书页面没有找到“{group} / {option}”筛选项，已停止本次搜索。",
                        kind=ProviderErrorKind.VALIDATION,
                        retryable=False,
                    )
                page.wait_for_timeout(500)
            return

        if self.platform == Platform.BILIBILI:
            for option in ("最多播放", "更多筛选", "最近一周"):
                target = page.get_by_text(option, exact=True)
                if not target.count():
                    raise LicensedProviderError(
                        f"B站页面没有找到“{option}”筛选项，已停止本次搜索。",
                        kind=ProviderErrorKind.VALIDATION,
                        retryable=False,
                    )
                target.first.click(force=True)
                page.wait_for_timeout(500)

    def _raise_for_visible_block(self, page) -> None:
        body_text = page.locator("body").inner_text(timeout=3000)
        if any(marker in body_text for marker in _HARD_VERIFICATION_MARKERS):
            raise LicensedProviderError(
                f"{self.spec.label}要求人工验证，已停止搜索。",
                kind=ProviderErrorKind.AUTHORIZATION,
                retryable=False,
            )
        if any(marker in body_text for marker in _ACCESS_MARKERS):
            raise LicensedProviderError(
                f"{self.spec.label}提示访问频繁，已停止搜索。",
                kind=ProviderErrorKind.RATE_LIMIT,
                retryable=False,
            )

    def _raise_for_login_gate(self, page) -> None:
        body_text = page.locator("body").inner_text(timeout=3000)
        if any(marker in body_text for marker in self.spec.login_markers):
            raise LicensedProviderError(
                f"{self.spec.label}没有返回公开搜索结果，平台要求登录或人工验证。",
                kind=ProviderErrorKind.AUTHORIZATION,
                retryable=False,
            )

    def _rendered_rows(self, page) -> list[dict[str, Any]]:
        raw = page.locator(self.spec.link_selector).evaluate_all(
            """elements => elements.map(element => {
                let container = element;
                for (let i = 0; i < 5 && container?.parentElement; i += 1) {
                    const candidate = container.parentElement;
                    const text = (candidate.innerText || "").trim();
                    container = candidate;
                    if (text.length >= 8 && text.length <= 600) break;
                }
                const image = element.querySelector("img") || container?.querySelector("img");
                return {
                    href: element.href || element.getAttribute("href") || "",
                    title: element.getAttribute("title") || image?.alt || "",
                    text: (container?.innerText || element.innerText || "").trim(),
                };
            })"""
        )
        rows: list[dict[str, Any]] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            href = str(item.get("href") or "")
            match = self.spec.item_id_pattern.search(href)
            if not match:
                continue
            text = self._clean_text(item.get("text"))
            published_at = self._parse_published_at(text, self.clock())
            rows.append(
                {
                    "item_id": match.group(1),
                    "source_url": href,
                    "title": self._clean_title(item.get("title"), text),
                    "author_name": self._extract_author(text),
                    "likes": self._labeled_count(text, ("赞", "点赞")),
                    "comments": self._labeled_count(text, ("评论",)),
                    "published_at": published_at,
                    "time_confident": published_at is not None,
                    "evidence": "rendered_search_card",
                }
            )
        return rows

    def _rows_from_payload(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        if self.platform == Platform.XIAOHONGSHU:
            return self._xiaohongshu_rows(payload)
        if self.platform == Platform.KUAISHOU:
            return self._kuaishou_rows(payload)
        return self._bilibili_rows(payload)

    def _is_search_response_url(self, url: str) -> bool:
        normalized = str(url or "").casefold()
        if self.platform == Platform.XIAOHONGSHU:
            return "/api/sns/web/v1/search/notes" in normalized
        if self.platform == Platform.KUAISHOU:
            return self.spec.page_host in normalized and (
                "/graphql" in normalized or "/rest/v/search/feed" in normalized
            )
        return self.spec.page_host in normalized and "/search/type" in normalized

    @staticmethod
    def _xiaohongshu_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        rows: list[dict[str, Any]] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            card = entry.get("note_card") or entry.get("noteCard") or {}
            if not isinstance(card, dict):
                continue
            item_id = str(entry.get("id") or card.get("note_id") or card.get("id") or "")
            if not item_id:
                continue
            user = card.get("user") if isinstance(card.get("user"), dict) else {}
            metrics = (
                card.get("interact_info")
                if isinstance(card.get("interact_info"), dict)
                else {}
            )
            published_at = LocalPlatformBrowserSearchProvider._timestamp_from_mapping(card)
            rows.append(
                {
                    "item_id": item_id,
                    "source_url": f"https://www.xiaohongshu.com/explore/{item_id}",
                    "title": LocalPlatformBrowserSearchProvider._clean_text(
                        card.get("display_title") or card.get("title") or card.get("desc")
                    ),
                    "author_id": str(user.get("user_id") or user.get("id") or ""),
                    "author_name": LocalPlatformBrowserSearchProvider._clean_text(
                        user.get("nickname") or user.get("nick_name")
                    ),
                    "likes": LocalPlatformBrowserSearchProvider._as_count(
                        metrics.get("liked_count") or metrics.get("likedCount")
                    ),
                    "comments": LocalPlatformBrowserSearchProvider._as_count(
                        metrics.get("comment_count") or metrics.get("commentCount")
                    ),
                    "favorites": LocalPlatformBrowserSearchProvider._as_count(
                        metrics.get("collected_count") or metrics.get("collectedCount")
                    ),
                    "published_at": published_at,
                    "time_confident": published_at is not None,
                    "evidence": "browser_search_response",
                }
            )
        return rows

    @staticmethod
    def _kuaishou_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data")
        search = data.get("visionSearchPhoto") if isinstance(data, dict) else None
        feeds = (
            search.get("feeds")
            if isinstance(search, dict)
            else payload.get("feeds")
        )
        if not isinstance(feeds, list):
            return []
        rows: list[dict[str, Any]] = []
        for feed in feeds:
            if not isinstance(feed, dict):
                continue
            photo = feed.get("photo") if isinstance(feed.get("photo"), dict) else feed
            author = (
                feed.get("author")
                if isinstance(feed.get("author"), dict)
                else photo.get("user")
                if isinstance(photo.get("user"), dict)
                else {}
            )
            item_id = str(photo.get("id") or photo.get("photoId") or "")
            if not item_id:
                continue
            published_at = LocalPlatformBrowserSearchProvider._timestamp_from_mapping(photo)
            rows.append(
                {
                    "item_id": item_id,
                    "source_url": f"https://www.kuaishou.com/short-video/{item_id}",
                    "title": LocalPlatformBrowserSearchProvider._clean_text(
                        photo.get("caption") or photo.get("title") or photo.get("description")
                    ),
                    "author_id": str(
                        author.get("id")
                        or author.get("userId")
                        or photo.get("userId")
                        or ""
                    ),
                    "author_name": LocalPlatformBrowserSearchProvider._clean_text(
                        author.get("name")
                        or author.get("userName")
                        or photo.get("userName")
                    ),
                    "plays": LocalPlatformBrowserSearchProvider._first_count(
                        photo, "viewCount", "playCount", "views"
                    ),
                    "likes": LocalPlatformBrowserSearchProvider._first_count(
                        photo, "likeCount", "realLikeCount", "likes"
                    ),
                    "comments": LocalPlatformBrowserSearchProvider._first_count(
                        photo, "commentCount", "comments"
                    ),
                    "shares": LocalPlatformBrowserSearchProvider._first_count(
                        photo, "shareCount", "shares"
                    ),
                    "published_at": published_at,
                    "time_confident": published_at is not None,
                    "evidence": "browser_search_response",
                }
            )
        return rows

    @staticmethod
    def _bilibili_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data")
        items = data.get("result") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        rows: list[dict[str, Any]] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("bvid") or "").strip()
            if not item_id:
                continue
            published_at = LocalPlatformBrowserSearchProvider._timestamp_from_mapping(
                entry
            )
            rows.append(
                {
                    "item_id": item_id,
                    "source_url": f"https://www.bilibili.com/video/{item_id}",
                    "title": LocalPlatformBrowserSearchProvider._clean_markup(
                        entry.get("title")
                    ),
                    "author_id": str(entry.get("mid") or ""),
                    "author_name": LocalPlatformBrowserSearchProvider._clean_text(
                        entry.get("author")
                    ),
                    "plays": LocalPlatformBrowserSearchProvider._as_count(
                        entry.get("play")
                    ),
                    "comments": LocalPlatformBrowserSearchProvider._as_count(
                        entry.get("video_review")
                    ),
                    "favorites": LocalPlatformBrowserSearchProvider._as_count(
                        entry.get("favorites")
                    ),
                    "published_at": published_at,
                    "time_confident": published_at is not None,
                    "evidence": "browser_search_response",
                }
            )
        return rows

    def _to_items(
        self,
        rows: list[dict[str, Any]],
        *,
        observed_at: datetime,
        limit: int,
    ) -> list[ProviderSearchItem]:
        normalized: list[tuple[dict[str, Any], datetime, bool]] = []
        for row in rows:
            title = self._clean_text(row.get("title"))
            item_id = self._clean_text(row.get("item_id"))
            if not title or not item_id:
                continue
            published_at = row.get("published_at")
            time_confident = bool(row.get("time_confident") and isinstance(published_at, datetime))
            effective_time = published_at if time_confident else observed_at
            normalized.append((row, effective_time, time_confident))

        normalized.sort(
            key=lambda entry: (
                entry[1],
                int(entry[0].get("likes") or 0)
                + int(entry[0].get("comments") or 0) * 3
                + int(entry[0].get("shares") or 0) * 4
                + int(entry[0].get("favorites") or 0) * 4,
            ),
            reverse=True,
        )
        items: list[ProviderSearchItem] = []
        for row, published_at, time_confident in normalized[:limit]:
            item_id = self._clean_text(row.get("item_id"))
            author_name = self._clean_text(row.get("author_name")) or f"{self.spec.label}作者"
            author_id = self._clean_text(row.get("author_id")) or f"{self.platform.value}-{item_id}"
            warnings: list[str] = []
            if not time_confident:
                if self.platform == Platform.XIAOHONGSHU:
                    warnings.append(
                        "小红书已选择“一周内”；搜索卡片未返回精确发布时间。"
                    )
                else:
                    warnings.append("搜索结果未返回可靠发布时间；按平台搜索顺序作为近期候选。")
            if author_name == f"{self.spec.label}作者":
                warnings.append("搜索卡片未返回作者名。")
            items.append(
                ProviderSearchItem(
                    platform=self.platform,
                    platform_item_id=item_id,
                    title=self._clean_text(row.get("title")),
                    author_id=author_id,
                    author_name=author_name,
                    published_at=published_at,
                    source_url=HttpUrl(str(row.get("source_url"))),
                    provider_rank=len(items) + 1,
                    metrics=VideoMetricSnapshot(
                        item_id=item_id,
                        sampled_at=observed_at,
                        plays=self._as_count(row.get("plays")),
                        likes=self._as_count(row.get("likes")),
                        comments=self._as_count(row.get("comments")),
                        shares=self._as_count(row.get("shares")),
                        favorites=self._as_count(row.get("favorites")),
                        confidence=0.7 if row.get("evidence") == "browser_search_response" else 0.45,
                    ),
                    evidence=(
                        f"{self.platform.value}:{row.get('evidence')};"
                        f"time={'platform' if time_confident else 'platform_filter' if self.platform == Platform.XIAOHONGSHU else 'search_order_fallback'}"
                    ),
                    data_quality_warnings=warnings,
                )
            )
        return items

    def _visible_login_required(self) -> bool | None:
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{self.debug_port}",
                    timeout=2500,
                )
                context = browser.contexts[0]
                page = next(
                    (
                        item
                        for item in context.pages
                        if self.spec.page_host in item.url.casefold()
                    ),
                    None,
                )
                if page is None:
                    return None
                body_text = page.locator("body").inner_text(timeout=2000)
                return any(
                    marker in body_text for marker in _HARD_VERIFICATION_MARKERS
                )
        except Exception:
            return None

    def _missing_prerequisites(self) -> list[str]:
        if not self.enabled:
            return [
                "小红书安全模式（仅支持人工导入）"
                if self.platform == Platform.XIAOHONGSHU
                else f"{self.spec.label}浏览器发现开关"
            ]
        missing: list[str] = []
        try:
            playwright_spec = importlib.util.find_spec("playwright.sync_api")
        except ModuleNotFoundError:
            playwright_spec = None
        if playwright_spec is None:
            missing.append("Playwright Python 依赖")
        if self._browser_executable() is None:
            missing.append(
                "Microsoft Edge" if self.browser_channel == "msedge" else "Google Chrome"
            )
        return missing

    def _browser_executable(self) -> Path | None:
        if self.browser_channel == "msedge":
            candidates = [
                shutil.which("msedge"),
                Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
                Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
            ]
        else:
            candidates = [
                shutil.which("chrome"),
                Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return Path(candidate)
        return None

    def _debug_url(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}/json/version"

    def _debug_pages_url(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}/json/list"

    @staticmethod
    def _timestamp_from_mapping(value: dict[str, Any]) -> datetime | None:
        for key in (
            "publish_time",
            "publishTime",
            "create_time",
            "createTime",
            "uploadTime",
            "pubdate",
            "timestamp",
            "time",
        ):
            raw = value.get(key)
            try:
                timestamp = float(raw)
            except (TypeError, ValueError):
                continue
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            if timestamp < 946684800:
                continue
            try:
                return datetime.fromtimestamp(timestamp).astimezone()
            except (OSError, OverflowError, ValueError):
                continue
        return None

    @staticmethod
    def _parse_published_at(text: str, observed_at: datetime) -> datetime | None:
        compact = text.replace(" ", "")
        if "刚刚" in compact:
            return observed_at
        match = re.search(r"(\d+)分钟前", compact)
        if match:
            return observed_at - timedelta(minutes=int(match.group(1)))
        match = re.search(r"(\d+)小时前", compact)
        if match:
            return observed_at - timedelta(hours=int(match.group(1)))
        match = re.search(r"(\d+)天前", compact)
        if match:
            return observed_at - timedelta(days=int(match.group(1)))
        if "昨天" in compact:
            return observed_at - timedelta(days=1)
        if "前天" in compact:
            return observed_at - timedelta(days=2)
        match = re.search(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})(?!\d)", text)
        if match:
            try:
                candidate = observed_at.replace(
                    month=int(match.group(1)),
                    day=int(match.group(2)),
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                if candidate > observed_at + timedelta(days=1):
                    candidate = candidate.replace(year=candidate.year - 1)
                return candidate
            except ValueError:
                return None
        return None

    @staticmethod
    def _clean_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _clean_markup(value: Any) -> str:
        return html.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).strip()

    @classmethod
    def _clean_title(cls, title: Any, text: str) -> str:
        cleaned = cls._clean_text(title)
        if cleaned:
            return cleaned
        for line in (item.strip() for item in str(text or "").splitlines()):
            if (
                len(line) >= 2
                and not re.fullmatch(r"[\d.,万亿+\s]+", line)
                and not re.search(r"(刚刚|分钟前|小时前|天前|昨天|前天)$", line)
            ):
                return line
        return ""

    @staticmethod
    def _extract_author(text: str) -> str:
        lines = [item.strip() for item in str(text or "").splitlines() if item.strip()]
        return lines[1] if len(lines) > 1 and len(lines[1]) <= 40 else ""

    @staticmethod
    def _labeled_count(text: str, labels: tuple[str, ...]) -> int | None:
        label_pattern = "|".join(re.escape(label) for label in labels)
        patterns = (
            rf"(?:{label_pattern})[：:\s]*([\d.]+[万亿]?)",
            rf"([\d.]+[万亿]?)\s*(?:{label_pattern})",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return LocalPlatformBrowserSearchProvider._as_count(match.group(1))
        return None

    @staticmethod
    def _first_count(mapping: dict[str, Any], *keys: str) -> int | None:
        for key in keys:
            count = LocalPlatformBrowserSearchProvider._as_count(mapping.get(key))
            if count is not None:
                return count
        return None

    @staticmethod
    def _as_count(value: Any) -> int | None:
        raw = str(value or "").replace(",", "").strip().casefold()
        if not raw:
            return None
        multiplier = 1
        if raw.endswith("万"):
            raw, multiplier = raw[:-1], 10_000
        elif raw.endswith("亿"):
            raw, multiplier = raw[:-1], 100_000_000
        try:
            return max(0, int(float(raw) * multiplier))
        except ValueError:
            return None
