"""Low-frequency Douyin discovery through a user-controlled local Chrome.

This adapter deliberately reads only rendered search-result links.  It does not
reverse engineer request signatures, import personal browser profiles, solve
verification challenges, or download media.  A dedicated, local profile must
be opened and logged in by the operator before collection can run.
"""

from __future__ import annotations

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
from typing import Any, Callable
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen

from pydantic import HttpUrl

from src.adapters.drission_browser import ANTI_DETECTION_INIT_SCRIPT
from src.adapters.licensed import LicensedProviderError
from src.adapters.browser_window import (
    minimize_browser_window,
    reveal_browser_window,
)
from src.models import (
    Platform,
    ProviderCapability,
    ProviderErrorKind,
    ProviderMode,
    ProviderSearchError,
    ProviderSearchItem,
    ProviderSearchPage,
    ProviderUsage,
    VideoMetricSnapshot,
)
from src.services.commercial_search import title_matches_keyword


_VIDEO_ID_RE = re.compile(r"/video/(\d{10,})")
_LOGIN_MARKERS = ("安全验证", "扫码登录", "请完成验证")
_HOTSPOT_SUPPORTED_WINDOW_HOURS = {1, 24, 72, 168}
_HOTSPOT_DEFAULT_WINDOW_HOURS = 168
_HOTSPOT_LIST_TYPES = (1001, 1002, 1003, 1004, 1005)
_HOTSPOT_TOPIC_LIST_TYPES = (2001, 2002)
_HOTSPOT_SEARCH_LIST_TYPES = (3001, 3002)
_HOTSPOT_MAX_ROWS_PER_LIST = 50
_HOTSPOT_MAX_SCROLL_ROUNDS = 8
_HOTSPOT_MAX_RESULT_LIMIT = 100
_HOTSPOT_CUSTOMER_RESULT_LIMIT = 3
_PUBLIC_SEARCH_MAX_RESULT_LIMIT = 100
_PUBLIC_SEARCH_MAX_SCROLL_ROUNDS = 8
_PUBLIC_SEARCH_MAX_STAGNANT_ROUNDS = 2
_PUBLIC_SEARCH_MIN_RAW_SCAN_LIMIT = 100
_PUBLIC_SEARCH_MAX_RAW_SCAN_LIMIT = 150
_PUBLIC_SEARCH_RAW_SCAN_MULTIPLIER = 3
_MIN_QUALIFYING_LIKES = 100
_MIN_QUALIFYING_LIKES_PER_DAY = 1.0
_HOTSPOT_PAGE_SETTLE_RANGE_MS = (3_500, 5_500)
_HOTSPOT_SEARCH_SETTLE_RANGE_MS = (3_000, 5_000)
_HOTSPOT_SCROLL_REFRESH_RANGE_MS = (800, 2_500)
_HOTSPOT_LIST_COOLDOWN_RANGE_MS = (7_000, 11_000)
_HOTSPOT_SCROLL_PIXELS = 500
_HOTSPOT_KEYSTROKE_DELAY_MIN_MS = 120
_HOTSPOT_KEYSTROKE_DELAY_MAX_MS = 220
_HOTSPOT_LIST_LABELS = {
    1001: "视频总榜",
    1002: "低粉爆款",
    1003: "高完播率",
    1004: "高涨粉率",
    1005: "高点赞率",
    2001: "话题榜",
    2002: "话题飙升榜",
    3001: "搜索榜",
    3002: "搜索飙升榜",
}
_HOTSPOT_ENTRY_URL = (
    "https://douhot.douyin.com/square/hotspot?"
    "active_tab=hotspot_video&date_window=168&sub_type=1001"
)
_PUBLIC_DOUYIN_ENTRY_URL = "https://www.douyin.com/"
_HOTSPOT_ADAPTER_VERSION = "hotspot_fiber_v6_broad_recall"
_PUBLIC_SEARCH_ADAPTER_VERSION = "douyin_public_search_v6_visible_time_filters"
_PUBLIC_SEARCH_LOGIN_MARKERS = (
    "安全验证",
    "扫码登录",
    "请完成验证",
    "登录后查看更多",
    "登录后即可搜索更多精彩视频",
    "请先登录后继续",
)
_PUBLIC_SEARCH_RATE_LIMIT_MARKERS = ("访问频繁", "操作频繁", "请求过于频繁")
_PUBLIC_SEARCH_SERVICE_ERROR_MARKERS = ("服务出现异常", "服务异常", "系统繁忙")
_PUBLIC_SEARCH_MANUAL_REVIEW_CODES = frozenset(
    {"public_search_verification", "public_search_login_required"}
)
_PUBLIC_SEARCH_STOP_CODES = frozenset(
    {
        "public_search_platform_end",
        "public_search_safety_limit",
        "public_search_blocked",
        "public_search_verification",
        "public_search_login_required",
        "public_search_rate_limited",
        "public_search_service_unavailable",
    }
)


@dataclass(frozen=True)
class BrowserSessionStatus:
    enabled: bool
    running: bool
    login_required: bool
    ready_to_crawl: bool
    phase: str
    message: str


@dataclass(frozen=True)
class _PublicSearchFilterOutcome:
    receipt: str
    warning: str | None = None
    error_code: str | None = None


class LocalDouyinBrowserSearchProvider:
    """Render-only discovery provider backed by a dedicated Chrome profile."""

    # 保留既有来源标识，历史批次与缓存键无需迁移；evidence 区分热点宝记录。
    provider_name = "douyin_local_browser"
    adapter_version = _HOTSPOT_ADAPTER_VERSION
    browser_entry_url = _HOTSPOT_ENTRY_URL

    def __init__(
        self,
        *,
        enabled: bool,
        profile_dir: Path,
        browser_channel: str = "chrome",
        debug_port: int = 19222,
        timeout_seconds: float = 35.0,
        clock=None,
    ) -> None:
        self.enabled = enabled
        self.profile_dir = profile_dir
        self.browser_channel = browser_channel
        self.debug_port = debug_port
        self.timeout_seconds = max(10.0, min(float(timeout_seconds), 60.0))
        self.clock = clock or (lambda: datetime.now().astimezone())

    def capabilities(self) -> ProviderCapability:
        missing = self._missing_prerequisites()
        return ProviderCapability(
            provider_name=self.provider_name,
            display_name="本机 Chrome 抖音热点宝采集",
            mode=ProviderMode.LOCAL_BROWSER,
            enabled=not missing,
            supported_platforms=[Platform.DOUYIN] if not missing else [],
            max_page_size=_HOTSPOT_MAX_RESULT_LIMIT,
            supports_published_after=False,
            supports_metric_refresh=False,
            supports_usage=True,
            # Keep capability discovery side-effect free and fast.  The live
            # browser/session state is reported by ``session_status`` exactly
            # where the API needs it, rather than probing the debug port again
            # while assembling static capability metadata.
            permission_status="local_browser_login_required",
            credential_alias="local-dedicated-browser-profile",
            missing_configuration=missing,
        )

    def session_status(self) -> BrowserSessionStatus:
        missing = self._missing_prerequisites()
        if not self.enabled:
            return BrowserSessionStatus(
                False, False, False, False, "disabled", "本机浏览器发现已关闭。"
            )
        if missing:
            return BrowserSessionStatus(
                True,
                False,
                True,
                False,
                "dependency_missing",
                f"本机浏览器发现尚未就绪：{'；'.join(missing)}。",
            )
        try:
            with urlopen(self._debug_url(), timeout=1.5) as response:  # noqa: S310 - localhost only
                payload = json.loads(response.read().decode("utf-8"))
            product = str(payload.get("Browser") or "Chrome")
            page_urls: list[str] = []
            try:
                with urlopen(self._debug_pages_url(), timeout=1.5) as response:  # noqa: S310 - localhost only
                    pages = json.loads(response.read().decode("utf-8"))
                page_urls = [str(item.get("url") or "") for item in pages if isinstance(item, dict)]
            except (URLError, OSError, ValueError, json.JSONDecodeError):
                page_urls = []
            joined_urls = " ".join(page_urls).casefold()
            if "open.douyin.com" in joined_urls or "oauth" in joined_urls:
                return BrowserSessionStatus(
                    True,
                    True,
                    True,
                    False,
                    "waiting_login",
                    "热点宝正在等待抖音扫码授权；请在专用 Chrome 窗口完成登录。",
                )
            if "douhot.douyin.com" in joined_urls:
                return BrowserSessionStatus(
                    True,
                    True,
                    False,
                    True,
                    "ready",
                    f"热点宝已就绪：{product}。将只读取已渲染的榜单元数据。",
                )
            return BrowserSessionStatus(
                True,
                True,
                True,
                False,
                "browser_open",
                f"专用浏览器已打开：{product}。正在进入热点宝页面，请稍候检查授权状态。",
            )
        except (URLError, OSError, ValueError, json.JSONDecodeError):
            return BrowserSessionStatus(
                True, False, True, False, "browser_closed", "请先打开热点宝专用浏览器并登录抖音；验证出现时系统会暂停。"
            )

    def open_login_browser(self) -> BrowserSessionStatus:
        """Show a user-facing window for QR login or manual verification."""
        return self._start_browser(visible=True)

    def start_login_browser(self) -> BrowserSessionStatus:
        """Start the dedicated profile without interrupting background work."""
        return self._start_browser(visible=False)

    def _start_browser(self, *, visible: bool) -> BrowserSessionStatus:
        status = self.session_status()
        if status.running and visible:
            # Reusing the existing dedicated profile avoids killing Chrome and
            # immediately reopening it on the same port/profile, which can
            # race with Chrome's profile lock.  Login remains a user action in
            # the visible dedicated window.
            reveal_browser_window(self.debug_port)
            return status
        if status.running and not visible:
            self._minimize_browser_for_background()
            return status
        capability = self.capabilities()
        if not capability.enabled:
            missing = capability.missing_configuration
            if "Playwright Python 依赖" in missing:
                message = (
                    "缺少 Playwright Python 依赖。请在项目根目录运行 "
                    "`python -m pip install -r project/backend/requirements.txt`，然后重启后端。"
                )
            elif any(item in {"Google Chrome", "Microsoft Edge"} for item in missing):
                browser = "Google Chrome" if self.browser_channel == "chrome" else "Microsoft Edge"
                message = (
                    f"未找到 {browser}。请安装该浏览器，或将 "
                    "DOUYIN_BROWSER_CHANNEL 改为已安装的浏览器后重启后端。"
                )
            else:
                message = f"本机浏览器发现尚未就绪：{'；'.join(missing)}。"
            raise LicensedProviderError(
                message,
                kind=ProviderErrorKind.AUTHORIZATION,
            )
        executable = self._browser_executable()
        if executable is None:
            raise LicensedProviderError(
                "未找到 Chrome/Edge，请安装浏览器后再打开专用登录窗口。",
                kind=ProviderErrorKind.VALIDATION,
            )
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        browser_args = [
            str(executable),
            f"--remote-debugging-port={self.debug_port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--lang=zh-CN",
            "--disable-extensions",
            "--disable-plugins-discovery",
            "--disable-background-networking",
            "--disable-sync",
            "--metrics-recording-only",
            "--disable-default-apps",
            "--no-pings",
            "--disable-component-update",
        ]
        if visible:
            browser_args.extend(
                ["--new-window", "--window-position=80,80", "--window-size=1100,800"]
            )
        else:
            browser_args.extend(
                ["--start-minimized", "--window-size=900,700"]
            )
        browser_args.append(self.browser_entry_url)
        subprocess.Popen(  # noqa: S603 - executable is resolved from an allowlist
            browser_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # Chrome 进程启动和本地调试端口就绪并不同步。短暂等待后再返回真实
        # 连接状态，避免页面在用户刚点击按钮时错误地显示“未连接”。
        for delay_seconds in (0.5, 1.0, 1.5):
            time.sleep(delay_seconds)
            status = self.session_status()
            if status.running:
                if not visible:
                    self._minimize_browser_for_background()
                return status
        return BrowserSessionStatus(
            True,
            False,
            True,
            False,
            "starting",
            (
                "热点宝登录窗口正在打开，请在可见窗口中扫码或完成人工验证。"
                if visible
                else "热点宝专用浏览器正在后台启动；需要登录时请点击登录按钮。"
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
        capability = self.capabilities()
        if not capability.enabled:
            raise LicensedProviderError(
                "本机浏览器采集尚未就绪。",
                kind=ProviderErrorKind.AUTHORIZATION,
            )
        if platform != Platform.DOUYIN:
            raise LicensedProviderError("本机浏览器采集当前只支持抖音。", kind=ProviderErrorKind.VALIDATION)
        if not 1 <= limit <= capability.max_page_size:
            raise LicensedProviderError("每次最多保留 100 条热点宝合格结果。", kind=ProviderErrorKind.VALIDATION)
        status = self.session_status()
        if not status.running:
            raise LicensedProviderError(status.message, kind=ProviderErrorKind.AUTHORIZATION)

        self._minimize_browser_for_background()
        window_hours = self._resolve_hotspot_window_hours(hotspot_window_hours)
        observed_at = self.clock()
        raw_rows, collection_errors = self._collect_hotspot_rows(
            keyword,
            window_hours=window_hours,
            observed_at=observed_at,
            target_limit=limit,
        )
        items, low_incremental_items, warnings, filter_counts = self._to_items(
            raw_rows, keyword, observed_at, limit
        )
        diagnostic = None
        if raw_rows and not items:
            diagnostic = "已读取到页面结果，但其中没有可读标题或可用视频链接。"
        if not raw_rows:
            diagnostic = "热点宝未返回可识别视频；可能没有结果、未登录或需要人工验证。"
        return ProviderSearchPage(
            platform=platform,
            provider=self.provider_name,
            items=items,
            low_incremental_items=low_incremental_items if not items else [],
            observed_at=observed_at,
            request_id=f"browser-{idempotency_key[:20]}",
            api_call_count=0,
            billable_units=0,
            has_more=False,
            raw_item_count=len(raw_rows),
            parsed_item_count=len(items),
            payload_diagnostic=diagnostic,
            duration_filtered_count=filter_counts["duration"],
            incremental_play_filtered_count=filter_counts["incremental_plays"],
            relevance_filtered_count=filter_counts["relevance"],
            errors=[*collection_errors, *warnings],
        )

    def search_public(
        self,
        platform: Platform,
        keyword: str,
        published_after: datetime | None,
        limit: int,
        idempotency_key: str,
        hotspot_window_hours: int | None = None,
    ) -> ProviderSearchPage:
        """Read a limited set of already-rendered Douyin public search cards.

        This is deliberately separate from ``search``: Hotspot ranking and
        ordinary Douyin search are two visible sources with different quality
        signals.  The public source never reads network responses or tries to
        bypass a login, verification, or rate-limit page.
        """
        del hotspot_window_hours
        capability = self.capabilities()
        if not capability.enabled:
            raise LicensedProviderError(
                "本机浏览器采集尚未就绪。",
                kind=ProviderErrorKind.AUTHORIZATION,
            )
        if platform != Platform.DOUYIN:
            raise LicensedProviderError(
                "抖音官网搜索当前只支持抖音。",
                kind=ProviderErrorKind.VALIDATION,
            )
        if not 1 <= limit <= _PUBLIC_SEARCH_MAX_RESULT_LIMIT:
            raise LicensedProviderError(
                "抖音官网搜索每次最多保留 100 条候选。",
                kind=ProviderErrorKind.VALIDATION,
            )
        status = self.session_status()
        if not status.running:
            raise LicensedProviderError(
                status.message,
                kind=ProviderErrorKind.AUTHORIZATION,
            )

        self._minimize_browser_for_background()
        observed_at = self.clock()
        raw_scan_limit = max(
            _PUBLIC_SEARCH_MIN_RAW_SCAN_LIMIT,
            limit * _PUBLIC_SEARCH_RAW_SCAN_MULTIPLIER,
        )
        raw_scan_limit = min(raw_scan_limit, _PUBLIC_SEARCH_MAX_RAW_SCAN_LIMIT)
        raw_rows, collection_errors = self._collect_public_search_rows(
            keyword,
            target_limit=limit,
            scan_limit=raw_scan_limit,
            observed_at=observed_at,
            published_after=published_after,
        )
        manual_review_error = next(
            (
                error
                for error in collection_errors
                if error.code in _PUBLIC_SEARCH_MANUAL_REVIEW_CODES
            ),
            None,
        )
        if manual_review_error is not None:
            if self._reveal_browser_for_manual_review():
                manual_review_error.message = (
                    f"{manual_review_error.message}"
                    "已将抖音专用浏览器显示到前台，请完成处理后再重新搜索。"
                )
            else:
                manual_review_error.message = (
                    f"{manual_review_error.message}"
                    "未能定位抖音专用浏览器窗口；请在“账号连接”中点击“登录抖音”，"
                    "完成处理后再重新搜索。"
                )
        (
            items,
            warnings,
            filter_counts,
            published_filtered_count,
        ) = self._to_public_search_items(
            raw_rows,
            keyword=keyword,
            observed_at=observed_at,
            published_after=published_after,
            limit=limit,
        )
        stop_error = next(
            (
                error
                for error in collection_errors
                if error.code in _PUBLIC_SEARCH_STOP_CODES
            ),
            None,
        )
        crawl_stop_reason = None
        crawl_stop_message = None
        if len(items) >= limit:
            crawl_stop_reason = "target_reached"
            crawl_stop_message = f"已读取到目标 {limit} 条公开搜索结果。"
        elif stop_error is not None:
            crawl_stop_message = stop_error.message
            if stop_error.code == "public_search_safety_limit":
                crawl_stop_reason = "safety_limit"
            elif stop_error.code == "public_search_platform_end":
                crawl_stop_reason = "no_more_loaded" if not raw_rows else "platform_end"
            elif stop_error.code in {
                "public_search_blocked",
                "public_search_verification",
                "public_search_login_required",
                "public_search_rate_limited",
                "public_search_service_unavailable",
            }:
                crawl_stop_reason = "safety_limit"
        diagnostic = None
        if not raw_rows:
            diagnostic = (
                crawl_stop_message
                or "抖音官网搜索未返回可读取的视频；可能没有公开结果、需要人工登录或出现安全限制。"
            )
        elif not items:
            diagnostic = (
                f"抖音官网搜索已读取候选，但其中 {published_filtered_count} 条"
                "发布时间不在本次范围内。"
                if published_filtered_count
                else "抖音官网搜索已读取候选，但其中没有可读标题或可用视频链接。"
            )
        return ProviderSearchPage(
            platform=Platform.DOUYIN,
            provider=self.provider_name,
            items=items,
            observed_at=observed_at,
            request_id=f"browser-public-{idempotency_key[:16]}",
            api_call_count=0,
            billable_units=0,
            has_more=(
                crawl_stop_reason == "safety_limit"
                or (stop_error is None and len(items) < limit and len(raw_rows) > len(items))
            ),
            raw_item_count=len(raw_rows),
            parsed_item_count=len(items),
            crawl_stop_reason=crawl_stop_reason,
            crawl_stop_message=crawl_stop_message,
            payload_diagnostic=diagnostic,
            duration_filtered_count=filter_counts["duration"],
            relevance_filtered_count=filter_counts["relevance"],
            errors=[*collection_errors, *warnings],
        )

    def refresh_metrics(
        self,
        platform: Platform,
        platform_item_ids: list[str],
        idempotency_key: str,
    ) -> ProviderSearchPage:
        return ProviderSearchPage(
            platform=platform,
            provider=self.provider_name,
            observed_at=self.clock(),
            request_id=f"browser-refresh-{idempotency_key[:16]}",
            errors=[
                ProviderSearchError(
                    kind=ProviderErrorKind.VALIDATION,
                    message="本机发现器暂不单独刷新指标；会在下一次关键词低频采集时更新快照。",
                )
            ],
        )

    def usage(self) -> ProviderUsage:
        now = self.clock()
        return ProviderUsage(
            provider=self.provider_name,
            period_started_at=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
            period_ends_at=now,
            platform_queries=0,
            billable_units=0,
            estimated_cost=0,
        )

    def _minimize_browser_for_background(self) -> None:
        """Keep ordinary collection in a normal taskbar window without focus."""
        minimize_browser_window(self.debug_port)

    def _reveal_browser_for_manual_review(self) -> bool:
        """Bring a confirmed human-action page to the foreground."""
        return reveal_browser_window(self.debug_port)

    @staticmethod
    def _reuse_or_create_collection_page(
        context,
        *,
        preferred_url_fragments: tuple[str, ...],
    ) -> tuple[Any, bool]:
        """Prefer an existing dedicated Douyin tab without disturbing login pages."""
        try:
            pages = list(context.pages)
        except (AttributeError, TypeError):
            pages = []
        reusable_pages: list[tuple[Any, str]] = []
        for page in reversed(pages):
            try:
                if page.is_closed():
                    continue
            except AttributeError:
                pass
            except Exception:
                continue
            url = str(getattr(page, "url", "") or "")
            normalized_url = url.casefold()
            if any(marker in normalized_url for marker in ("open.douyin.com", "oauth", "passport", "login")):
                continue
            reusable_pages.append((page, normalized_url))

        for fragment in preferred_url_fragments:
            normalized_fragment = fragment.casefold()
            for page, url in reusable_pages:
                if normalized_fragment in url:
                    return page, False
        for page, url in reusable_pages:
            if "douyin.com" in url:
                return page, False
        for page, url in reusable_pages:
            if url in {"", "about:blank"}:
                return page, False
        return context.new_page(), True

    def _collect_hotspot_rows(
        self,
        keyword: str,
        *,
        window_hours: int = _HOTSPOT_DEFAULT_WINDOW_HOURS,
        observed_at: datetime,
        target_limit: int,
    ) -> tuple[list[dict[str, Any]], list[ProviderSearchError]]:
        """Collect visible candidates from video, topic and search surfaces.

        The customer-facing crawler uses the requested fallback order: video
        total leaderboard, topic total leaderboard, then search total
        leaderboard.  A later source is only opened when earlier qualifying
        candidates do not fill the requested result limit.
        """
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        endpoint = f"http://127.0.0.1:{self.debug_port}"
        last_error: Exception | None = None
        for attempt in range(2):
            playwright_manager = sync_playwright().start()
            try:
                browser = playwright_manager.chromium.connect_over_cdp(endpoint)
                context = browser.contexts[0]
                context.add_init_script(ANTI_DETECTION_INIT_SCRIPT)
                page, created_page = self._reuse_or_create_collection_page(
                    context,
                    preferred_url_fragments=("douhot.douyin.com",),
                )
                try:
                    page.set_default_timeout(int(self.timeout_seconds * 1000))
                    rows: list[dict[str, Any]] = []
                    errors: list[ProviderSearchError] = []
                    def ensure_visible_page(url: str) -> None:
                        response = page.goto(url, wait_until="domcontentloaded")
                        if response is not None and response.status in {403, 429}:
                            raise LicensedProviderError(
                                f"热点宝返回 {response.status}，已停止采集并进入安全暂停。",
                                kind=ProviderErrorKind.RATE_LIMIT,
                            )
                        page.wait_for_timeout(self._random_delay_ms(*_HOTSPOT_PAGE_SETTLE_RANGE_MS))
                        body_text = page.locator("body").inner_text(timeout=3_000)
                        if any(marker in body_text for marker in _LOGIN_MARKERS):
                            raise LicensedProviderError(
                                "热点宝要求登录或安全验证，已暂停采集，请在专用浏览器中人工处理。",
                                kind=ProviderErrorKind.AUTHORIZATION,
                            )
                        if any(marker in body_text for marker in ("访问频繁", "操作频繁", "请求过于频繁")):
                            raise LicensedProviderError(
                                "热点宝提示访问频繁，已停止采集并进入安全暂停。",
                                kind=ProviderErrorKind.RATE_LIMIT,
                            )

                    def append_video_board(list_type: int) -> None:
                        url = (
                            "https://douhot.douyin.com/square/hotspot?"
                            f"active_tab=hotspot_video&date_window={window_hours}&sub_type={list_type}"
                        )
                        try:
                            ensure_visible_page(url)
                            self._fill_hotspot_keyword(page, keyword)
                            page.wait_for_timeout(self._random_delay_ms(*_HOTSPOT_SEARCH_SETTLE_RANGE_MS))
                            list_rows: dict[str, dict[str, Any]] = {}
                            stagnant_rounds = 0
                            previous_count = -1
                            for _ in range(_HOTSPOT_MAX_SCROLL_ROUNDS):
                                for row in self._extract_hotspot_rows(page):
                                    item_id = str(row.get("item_id") or "")
                                    if item_id:
                                        list_rows[item_id] = row
                                current_count = len(list_rows)
                                if current_count >= _HOTSPOT_MAX_ROWS_PER_LIST:
                                    break
                                stagnant_rounds = stagnant_rounds + 1 if current_count == previous_count else 0
                                if stagnant_rounds >= 2:
                                    break
                                previous_count = current_count
                                page.evaluate(f"window.scrollBy(0, {_HOTSPOT_SCROLL_PIXELS})")
                                page.wait_for_timeout(
                                    self._random_delay_ms(
                                        *_HOTSPOT_SCROLL_REFRESH_RANGE_MS
                                    )
                                )
                            for row in list_rows.values():
                                row.update(
                                    {
                                        "window_hours": window_hours,
                                        "list_type": list_type,
                                        "list_label": _HOTSPOT_LIST_LABELS[list_type],
                                        "source_kind": "video_board",
                                    }
                                )
                                rows.append(row)
                        except LicensedProviderError:
                            raise
                        except Exception as exc:
                            errors.append(
                                ProviderSearchError(
                                    kind=ProviderErrorKind.CONNECTION,
                                    message=f"热点宝{_HOTSPOT_LIST_LABELS[list_type]}读取失败，已跳过该榜单：{exc}",
                                    retryable=False,
                                )
                            )

                    def has_enough_qualifying_rows() -> bool:
                        qualifying, _, _, _ = self._to_items(
                            rows,
                            keyword,
                            observed_at,
                            target_limit,
                        )
                        return len(qualifying) >= target_limit

                    # 1) Video total leaderboard is always first.
                    append_video_board(1001)
                    if has_enough_qualifying_rows():
                        return rows, errors

                    # 2) Topic leaderboard is essential for business terms such as
                    # "餐饮获客": the topic itself may match even when individual
                    # video titles do not repeat the full phrase.
                    topic_url = (
                        "https://douhot.douyin.com/square/hotspot?"
                        f"active_tab=hotspot_topic&date_window={window_hours}&sub_type=2001"
                    )
                    try:
                        ensure_visible_page(topic_url)
                        self._fill_hotspot_keyword(page, keyword)
                        page.wait_for_timeout(self._random_delay_ms(*_HOTSPOT_SEARCH_SETTLE_RANGE_MS))
                        topics = [
                            item for item in self._extract_hotspot_topic_rows(page)
                            if title_matches_keyword(title=str(item.get("topic_name") or ""), keyword=keyword)
                        ][:2]
                        for topic in topics:
                            topic_id = str(topic.get("topic_id") or "")
                            if not topic_id:
                                continue
                            ensure_visible_page(
                                "https://douhot.douyin.com/topic/detail?active_tab=topic_detail&topic_id="
                                + quote(topic_id, safe="")
                            )
                            for row in self._extract_topic_detail_rows(page):
                                row.update({
                                    "window_hours": window_hours,
                                    "list_type": 2001,
                                    "list_label": _HOTSPOT_LIST_LABELS[2001],
                                    "source_kind": "topic_board",
                                    "topic_exact": True,
                                    "topic_name": str(topic.get("topic_name") or keyword),
                                    "topic_id": topic_id,
                                })
                                rows.append(row)
                    except LicensedProviderError:
                        raise
                    except Exception as exc:
                        errors.append(ProviderSearchError(
                            kind=ProviderErrorKind.CONNECTION,
                            message=f"热点宝话题榜读取失败，已跳过：{exc}",
                            retryable=False,
                        ))

                    if has_enough_qualifying_rows():
                        return rows, errors

                    # 3) Keep Hotspot search-board rows in this provider.  The
                    # public Douyin website is collected by ``search_public`` so
                    # callers can label and schedule it as a separate source.
                    try:
                        ensure_visible_page(
                            "https://douhot.douyin.com/square/hotspot?"
                            f"active_tab=hotspot_search&date_window={window_hours}&sub_type=3001"
                        )
                        self._fill_hotspot_keyword(page, keyword)
                        page.wait_for_timeout(self._random_delay_ms(*_HOTSPOT_SEARCH_SETTLE_RANGE_MS))
                        for row in self._collect_scrolled_rows(
                            page,
                            self._extract_hotspot_rows,
                        ):
                            row.update({
                                "window_hours": window_hours,
                                "list_type": 3001,
                                "list_label": _HOTSPOT_LIST_LABELS[3001],
                                "source_kind": "search_board",
                            })
                            rows.append(row)
                    except LicensedProviderError:
                        raise
                    except Exception as exc:
                        errors.append(ProviderSearchError(
                            kind=ProviderErrorKind.CONNECTION,
                            message=f"抖音搜索读取失败，已跳过：{exc}",
                            retryable=False,
                        ))

                    return rows, errors
                finally:
                    if created_page:
                        page.close()
            except LicensedProviderError:
                raise
            except (PlaywrightError, OSError, ConnectionError, IndexError) as exc:
                last_error = exc
                if attempt == 0:
                    continue
            finally:
                playwright_manager.stop()
        raise LicensedProviderError(
            "连接本机 Chrome 失败，已自动重试一次。",
            kind=ProviderErrorKind.CONNECTION,
            retryable=False,
        ) from last_error

    def _collect_public_search_rows(
        self,
        keyword: str,
        *,
        target_limit: int,
        scan_limit: int,
        observed_at: datetime,
        published_after: datetime | None,
    ) -> tuple[list[dict[str, Any]], list[ProviderSearchError]]:
        """Collect only rendered cards from the public Douyin search result page."""
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        endpoint = f"http://127.0.0.1:{self.debug_port}"
        last_error: Exception | None = None
        navigation_started = False
        target_limit = max(1, min(target_limit, _PUBLIC_SEARCH_MAX_RESULT_LIMIT))
        for attempt in range(2):
            playwright_manager = sync_playwright().start()
            try:
                browser = playwright_manager.chromium.connect_over_cdp(endpoint)
                context = browser.contexts[0]
                context.add_init_script(ANTI_DETECTION_INIT_SCRIPT)
                page, created_page = self._reuse_or_create_collection_page(
                    context,
                    preferred_url_fragments=("www.douyin.com/search/",),
                )
                network_rows: dict[str, dict[str, Any]] = {}

                def capture_search_response(response) -> None:
                    if not self._is_public_search_api_url(response.url):
                        return
                    payloads: list[Any] = []
                    try:
                        payloads.append(response.json())
                    except Exception:
                        # 抖音搜索结果接口是流式块响应:
                        #   <hex长度>\r\n{json}\r\n<hex长度>\r\n{json}...
                        # 标准 json() 无法解析,逐行尝试解析。
                        try:
                            text = response.text()
                        except Exception:
                            return
                        for line in text.split("\n"):
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                payloads.append(json.loads(line))
                            except Exception:
                                continue
                    for payload in payloads:
                        for row in self._rows_from_public_search_payload(
                            payload, keyword=keyword
                        ):
                            item_id = str(row.get("item_id") or "")
                            if item_id:
                                network_rows[item_id] = row

                try:
                    page.set_default_timeout(int(self.timeout_seconds * 1000))
                    page.on("response", capture_search_response)
                    # Mark before calling goto: a transport error can occur after
                    # Chrome has already sent the request, so it must never cause a
                    # second navigation for the same business search.
                    navigation_started = True
                    response = page.goto(
                        "https://www.douyin.com/",
                        wait_until="domcontentloaded",
                    )
                    if response is not None and response.status in {403, 412, 429}:
                        raise LicensedProviderError(
                            (
                                f"抖音官网返回 {response.status}，后台检索已停止；"
                                "当前没有可处理的登录或安全验证，请稍后再搜索。"
                            ),
                            kind=ProviderErrorKind.RATE_LIMIT,
                            code="public_search_rate_limited",
                        )
                    page.wait_for_timeout(
                        self._random_delay_ms(*_HOTSPOT_SEARCH_SETTLE_RANGE_MS)
                    )
                    # 新版抖音搜索:URL 直带关键词不再发起搜索请求,页面只
                    # 显示作者卡片。必须像真人一样在搜索框输入关键词后回车,
                    # 才会触发 general/search 接口并返回完整视频数据。
                    search_focused = page.evaluate(
                        """() => {
                          const input = document.querySelector(
                            'input[placeholder*="搜索"], input[data-e2e*="search"]'
                          );
                          if (!input) return false;
                          input.focus();
                          return true;
                        }"""
                    )
                    if search_focused:
                        page.keyboard.type(keyword, delay=60)
                        page.wait_for_timeout(
                            self._random_delay_ms(400, 900)
                        )
                        page.keyboard.press("Enter")
                    else:
                        # 兜底:找不到搜索框时回退 URL 直访
                        fallback_response = page.goto(
                            self._public_search_url(keyword),
                            wait_until="domcontentloaded",
                        )
                        if fallback_response is not None and fallback_response.status in {
                            403,
                            412,
                            429,
                        }:
                            raise LicensedProviderError(
                                (
                                    f"抖音官网返回 {fallback_response.status}，后台检索已停止；"
                                    "当前没有可处理的登录或安全验证，请稍后再搜索。"
                                ),
                                kind=ProviderErrorKind.RATE_LIMIT,
                                code="public_search_rate_limited",
                            )
                    page.wait_for_timeout(
                        self._random_delay_ms(*_HOTSPOT_SEARCH_SETTLE_RANGE_MS)
                    )
                    errors: list[ProviderSearchError] = []
                    try:
                        self._raise_for_public_search_block(page)
                    except LicensedProviderError as exc:
                        errors.append(
                            ProviderSearchError(
                                kind=exc.kind,
                                code=exc.code or "public_search_blocked",
                                message=exc.args[0],
                                retryable=False,
                            )
                        )
                        return [], errors
                    # The official search page sometimes exposes a visible
                    # "single column" toggle. Its cards show more of the
                    # public interaction text than the compact grid, so
                    # prefer it when it is genuinely available. This is a
                    # normal rendered-page click; it does not inspect
                    # requests, cookies, or hidden content.
                    layout_mode = (
                        "single_column"
                        if self._prefer_public_search_single_column(page)
                        else "default"
                    )
                    if layout_mode == "single_column":
                        # 点击“单列”后页面重新渲染;等待新布局稳定(卡片或
                        # 搜索响应出现)再开始提取,避免读到空页面。
                        self._wait_for_public_layout_settle(page)
                    time_filter = self._apply_public_search_time_filter(
                        page,
                        published_after=published_after,
                        observed_at=observed_at,
                    )
                    if time_filter.warning:
                        errors.append(
                            ProviderSearchError(
                                kind=ProviderErrorKind.VALIDATION,
                                code=time_filter.error_code,
                                message=time_filter.warning,
                            )
                        )
                    rows, stop_error = self._collect_public_douyin_search_rows(
                        page,
                        target_limit=target_limit,
                        scan_limit=scan_limit,
                        layout_mode=layout_mode,
                        network_rows=network_rows,
                        qualifying_count=lambda candidate_rows: len(
                            self._to_public_search_items(
                                candidate_rows,
                                keyword=keyword,
                                observed_at=observed_at,
                                published_after=published_after,
                                limit=target_limit,
                            )[0]
                        ),
                    )
                    for row in rows:
                        row["search_layout"] = layout_mode
                        row["search_time_filter"] = time_filter.receipt
                        if time_filter.warning:
                            row["search_time_filter_warning"] = time_filter.warning
                    if stop_error is not None:
                        errors.append(stop_error)
                    return rows, errors
                finally:
                    page.remove_listener("response", capture_search_response)
                    if created_page:
                        page.close()
            except LicensedProviderError:
                raise
            except (PlaywrightError, OSError, ConnectionError, IndexError) as exc:
                last_error = exc
                if attempt == 0 and not navigation_started:
                    continue
                break
            finally:
                playwright_manager.stop()
        raise LicensedProviderError(
            (
                "抖音官网搜索页打开或读取失败；为避免重复请求，本次未再次打开搜索页。"
                if navigation_started
                else "连接本机 Chrome 失败，已自动重试一次。"
            ),
            kind=ProviderErrorKind.CONNECTION,
            retryable=False,
        ) from last_error

    @staticmethod
    def _public_search_url(keyword: str) -> str:
        # The official general results view is the single entry point that
        # exposes the visible 多列/单列/筛选 controls. Extraction below still
        # accepts only canonical /video/ cards from the rendered page.
        return "https://www.douyin.com/search/" + quote(keyword.strip(), safe="") + "?type=general"

    @staticmethod
    def _is_public_search_api_url(url: str) -> bool:
        """True for Douyin search API responses that carry video results."""
        return (
            "/aweme/v1/web/general/search" in url
            or "/aweme/v1/web/search/single" in url
            or "/aweme/v1/web/search/item" in url
            or "/aweme/v1/web/discover/search" in url
        )

    def _rows_from_public_search_payload(
        self, payload: Any, *, keyword: str
    ) -> list[dict[str, Any]]:
        """Parse search API JSON into rows with full interaction statistics.

        The rendered cards on the current Douyin web release are JS components
        without stable /video/ links or exposed ids, so the search API
        response is the reliable source for candidate ids and for 播放/点赞/
        评论/转发 statistics.
        """
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = (
                data.get("data")
                or data.get("aweme_list")
                or data.get("search_result")
                or []
            )
        else:
            return []
        if not isinstance(items, list):
            return []
        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            aweme = (
                item.get("aweme_info")
                if isinstance(item.get("aweme_info"), dict)
                else item.get("aweme")
                if isinstance(item.get("aweme"), dict)
                else item
            )
            item_id = str(
                aweme.get("aweme_id") or aweme.get("awemeId") or aweme.get("id") or ""
            )
            if not item_id or not item_id.isdigit():
                continue
            title = str(aweme.get("desc") or "").strip()
            if not title or not title_matches_keyword(
                title=title, keyword=keyword, require_intent=False
            ):
                continue
            stats = aweme.get("statistics") or aweme.get("interact_info") or {}
            if not isinstance(stats, dict):
                stats = {}
            author = aweme.get("author") or {}
            video = aweme.get("video") or {}
            if not isinstance(video, dict):
                video = {}
            duration_raw = aweme.get("duration") or video.get("duration")
            duration = None
            if duration_raw is not None:
                try:
                    duration_value = int(duration_raw)
                    duration = (
                        round(duration_value / 1000)
                        if duration_value > 1000
                        else duration_value
                    )
                except (TypeError, ValueError):
                    duration = None
            published = aweme.get("create_time") or aweme.get("createTime") or ""
            rows.append(
                {
                    "item_id": item_id,
                    "title": title,
                    "author_name": str(author.get("nickname") or ""),
                    "duration": duration,
                    "plays": self._as_int(
                        self._stat_value(stats, "play_count", "playCount")
                    ),
                    "likes": self._as_int(
                        self._stat_value(stats, "digg_count", "diggCount")
                    ),
                    "comments": self._as_int(
                        self._stat_value(stats, "comment_count", "commentCount")
                    ),
                    "shares": self._as_int(
                        self._stat_value(stats, "share_count", "shareCount")
                    ),
                    "published_text": str(published) if published else "",
                    "source_kind": "search_api",
                }
            )
        return rows

    @staticmethod
    def _stat_value(stats: dict[str, Any], *names: str) -> Any:
        """取统计字段:保留 0 值(or 会误吞 0)。"""
        for name in names:
            if name in stats and stats[name] is not None:
                return stats[name]
        return None

    def _wait_for_public_layout_settle(self, page) -> None:
        """轮询等待单列切换后的重渲染:出现结果卡片即认为布局稳定。

        点击“单列”后页面会整体重新渲染,若立即提取会读到空布局;
        这里最多等待约 6 秒,卡片出现即提前返回。
        """
        for _ in range(12):
            try:
                settled = bool(
                    page.evaluate(
                        """() => document.querySelectorAll(
                            ".search-result-card, img[class*='video-card-img']"
                        ).length > 0"""
                    )
                )
            except Exception:
                settled = False
            if settled:
                return
            page.wait_for_timeout(500)
        # 兜底:未检测到卡片也等一次网络往返再提取
        page.wait_for_timeout(1500)

    @staticmethod
    def _raise_for_public_search_block(page) -> None:
        if LocalDouyinBrowserSearchProvider._has_public_search_verification_frame(page):
            raise LicensedProviderError(
                "抖音官网出现可见安全验证，后台检索已停止。",
                kind=ProviderErrorKind.AUTHORIZATION,
                code="public_search_verification",
            )
        if LocalDouyinBrowserSearchProvider._has_visible_public_search_marker(
            page,
            _PUBLIC_SEARCH_LOGIN_MARKERS,
        ):
            raise LicensedProviderError(
                "抖音官网出现可见登录或安全提示，后台检索已停止。",
                kind=ProviderErrorKind.AUTHORIZATION,
                code="public_search_login_required",
            )
        if LocalDouyinBrowserSearchProvider._has_visible_public_search_marker(
            page,
            _PUBLIC_SEARCH_RATE_LIMIT_MARKERS,
        ):
            raise LicensedProviderError(
                "抖音官网搜索提示访问频繁，后台检索已停止；当前没有可处理的登录或安全验证，请稍后再搜索。",
                kind=ProviderErrorKind.RATE_LIMIT,
                code="public_search_rate_limited",
            )
        if LocalDouyinBrowserSearchProvider._has_visible_public_search_marker(
            page,
            _PUBLIC_SEARCH_SERVICE_ERROR_MARKERS,
        ):
            raise LicensedProviderError(
                "抖音官网显示服务出现异常，已停止本次搜索；请减少搜索次数，稍后再试。",
                kind=ProviderErrorKind.SERVICE,
                code="public_search_service_unavailable",
                retryable=False,
            )

    @staticmethod
    def _has_public_search_verification_frame(page) -> bool:
        """Detect a visible platform verification frame without interacting with it."""
        try:
            frames = page.locator(
                "iframe[src*='verifycenter'], iframe[src*='nocaptcha'], "
                "iframe[src*='captcha'], iframe[src*='geetest']"
            )
            return any(
                bool(
                    frames.nth(index).evaluate(
                        """frame => {
                          const style = window.getComputedStyle(frame);
                          if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                          const rect = frame.getBoundingClientRect();
                          return rect.width >= 80 && rect.height >= 80
                            && rect.bottom > 0 && rect.right > 0
                            && rect.top < window.innerHeight && rect.left < window.innerWidth;
                        }"""
                    )
                )
                for index in range(frames.count())
            )
        except Exception:
            # Do not turn an uninspectable or stale iframe into a fake challenge.
            return False

    @staticmethod
    def _has_visible_public_search_marker(page, markers: tuple[str, ...]) -> bool:
        """Read only visible login, verification, or rate-limit text from the page."""
        try:
            return bool(
                page.locator("body").evaluate(
                    """(markers) => {
                      const visible = element => {
                        for (let current = element; current; current = current.parentElement) {
                          const style = window.getComputedStyle(current);
                          if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                          const rect = current.getBoundingClientRect();
                          if (rect.width <= 0 || rect.height <= 0) return false;
                          if (current === element && (rect.bottom <= 0 || rect.right <= 0
                            || rect.top >= window.innerHeight || rect.left >= window.innerWidth)) return false;
                        }
                        return Boolean(element);
                      };
                      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                      let node;
                      while ((node = walker.nextNode())) {
                        const text = String(node.textContent || '');
                        if (!markers.some(marker => text.includes(marker))) continue;
                        if (visible(node.parentElement)) return true;
                      }
                      return false;
                    }""",
                    list(markers),
                )
            )
        except Exception:
            # A failed visibility read must not become a false prompt to handle
            # a challenge that the operator cannot actually see.
            return False

    @staticmethod
    def _apply_public_search_time_filter(
        page,
        *,
        published_after: datetime | None,
        observed_at: datetime,
    ) -> _PublicSearchFilterOutcome:
        """Use only rendered, visible controls for the requested time window."""
        requested_days = LocalDouyinBrowserSearchProvider._public_search_filter_days(
            published_after,
            observed_at,
        )
        if requested_days == 0:
            return _PublicSearchFilterOutcome("不限（未打开平台筛选）")
        if requested_days is None:
            return _PublicSearchFilterOutcome(
                "平台筛选未应用；仅本地过滤",
                (
                    "本次发布时间不是抖音官网可精确对应的 1 天、7 天或 180 天；"
                    "平台筛选未应用，仅按页面可核验发布时间在本地过滤。"
                ),
                "public_search_time_filter_unsupported",
            )

        option_labels = {
            1: ("一天内", "1天内", "近一天", "近1天", "最近一天", "24小时内"),
            7: ("一周内", "7天内", "近一周", "近7天", "最近一周"),
            180: ("半年内", "近半年", "最近半年", "6个月内", "近6个月", "180天内"),
        }[requested_days]
        button_status, _ = LocalDouyinBrowserSearchProvider._click_visible_public_search_text(
            page,
            ("筛选",),
        )
        if button_status != "clicked":
            restore_note = ""
            if button_status == "failed":
                restored = LocalDouyinBrowserSearchProvider._close_public_search_filter_menu(page)
                restore_note = (
                    "已关闭可能打开的筛选菜单并保留原筛选状态。"
                    if restored
                    else "未能确认筛选菜单仍为原状态。"
                )
            return _PublicSearchFilterOutcome(
                "平台筛选未应用；仅本地过滤",
                (
                    f"抖音官网的“筛选”控件{LocalDouyinBrowserSearchProvider._public_control_status_text(button_status)}；"
                    "平台筛选未应用，仅按页面可核验发布时间在本地过滤。"
                    f"{restore_note}"
                ),
                "public_search_time_filter_unavailable",
            )

        page.wait_for_timeout(250)
        option_status, selected_label = (
            LocalDouyinBrowserSearchProvider._click_visible_public_search_text(
                page,
                option_labels,
            )
        )
        if option_status != "clicked":
            restored = LocalDouyinBrowserSearchProvider._close_public_search_filter_menu(page)
            restore_note = "已关闭筛选菜单并保留原筛选状态。" if restored else "未能确认筛选菜单已关闭。"
            return _PublicSearchFilterOutcome(
                "平台筛选未应用；仅本地过滤",
                (
                    f"抖音官网没有可用的 {requested_days} 天发布时间选项"
                    f"（{LocalDouyinBrowserSearchProvider._public_control_status_text(option_status)}）；"
                    f"平台筛选未应用，仅按页面可核验发布时间在本地过滤。{restore_note}"
                ),
                "public_search_time_filter_unavailable",
            )

        page.wait_for_timeout(600)
        return _PublicSearchFilterOutcome(
            f"已应用平台筛选：{selected_label}（{requested_days}天）"
        )

    @staticmethod
    def _public_search_filter_days(
        published_after: datetime | None,
        observed_at: datetime,
    ) -> int | None:
        if published_after is None:
            return 0
        elapsed = observed_at - published_after
        tolerance = timedelta(minutes=15)
        for days in (1, 7, 180):
            if abs(elapsed - timedelta(days=days)) <= tolerance:
                return days
        return None

    @staticmethod
    def _click_visible_public_search_text(
        page,
        labels: tuple[str, ...],
    ) -> tuple[str, str | None]:
        """Click one exact visible and enabled text control without forcing it."""
        found = False
        hidden = False
        disabled = False
        failed = False
        for label in labels:
            try:
                matches = page.get_by_text(label, exact=True)
                count = matches.count()
            except Exception:
                failed = True
                continue
            found = found or count > 0
            for index in range(count - 1, -1, -1):
                control = matches.nth(index)
                try:
                    if not control.is_visible() or not bool(
                        control.evaluate(
                            """node => {
                              const rect = node.getBoundingClientRect();
                              return rect.width > 0 && rect.height > 0
                                && rect.bottom > 0 && rect.right > 0
                                && rect.top < window.innerHeight && rect.left < window.innerWidth;
                            }"""
                        )
                    ):
                        hidden = True
                        continue
                    if (
                        not control.is_enabled()
                        or control.get_attribute("aria-disabled") == "true"
                    ):
                        disabled = True
                        continue
                    control.click()
                    return "clicked", label
                except Exception:
                    failed = True
        if disabled:
            return "disabled", None
        if hidden:
            return "hidden", None
        if found or failed:
            return "failed", None
        return "missing", None

    @staticmethod
    def _close_public_search_filter_menu(page) -> bool:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)
            return True
        except Exception:
            return False

    @staticmethod
    def _public_control_status_text(status: str) -> str:
        return {
            "missing": "未找到",
            "hidden": "不可见",
            "disabled": "已禁用",
            "failed": "操作失败",
        }.get(status, "不可用")

    @staticmethod
    def _resolve_hotspot_window_hours(value: int | None) -> int:
        window_hours = _HOTSPOT_DEFAULT_WINDOW_HOURS if value is None else value
        if window_hours not in _HOTSPOT_SUPPORTED_WINDOW_HOURS:
            raise LicensedProviderError(
                "热点宝榜单周期只支持近 1 小时、近 1 天、近 3 天或近 7 天。",
                kind=ProviderErrorKind.VALIDATION,
            )
        return window_hours

    @staticmethod
    def _random_delay_ms(lower: int, upper: int) -> int:
        """Return one fresh delay for each visible browser action."""
        return random.SystemRandom().randint(lower, upper)

    @staticmethod
    def _fill_hotspot_keyword(page, keyword: str) -> None:
        """Enter the keyword gradually so the visible input receives normal key events."""
        selectors = (
            "input[placeholder*='搜索']",
            "input[placeholder*='搜']",
            "input",
        )
        for selector in selectors:
            locator = page.locator(selector)
            try:
                if locator.count() < 1:
                    continue
                target = locator.first
                if not target.is_visible():
                    continue
                target.click()
                target.press("Control+A")
                target.press("Backspace")
                target.type(
                    keyword,
                    delay=random.SystemRandom().randint(
                        _HOTSPOT_KEYSTROKE_DELAY_MIN_MS,
                        _HOTSPOT_KEYSTROKE_DELAY_MAX_MS,
                    ),
                )
                target.press("Enter")
                return
            except Exception:  # The page can expose several unrelated inputs.
                continue

    @staticmethod
    def _extract_hotspot_rows(page) -> list[dict[str, Any]]:
        """Extract visible video cards first, with a narrow React-record fallback.

        Hotspot is a React application.  The DOM path is deliberately preferred;
        the fallback only reads the same record already rendered for a table row
        when cards do not expose their video link directly.
        """
        return page.locator("body").evaluate(
            """() => {
                const number = value => {
                  const text = String(value ?? '').replace(/[,，\\s]/g, '');
                  const unit = text.includes('亿') ? 100000000 : text.includes('万') ? 10000 : 1;
                  const matched = text.match(/([0-9]+(?:\\.[0-9]+)?)/);
                  return matched ? Math.round(Number(matched[1]) * unit) : null;
                };
                const itemId = value => {
                  const matched = String(value ?? '').match(/(?:video|aweme)[\\/=_-]?(\\d{10,})/);
                  return matched ? matched[1] : '';
                };
                const fromText = (text, href = '') => ({
                  item_id: itemId(href),
                  href,
                  title: String(text || '').split('\\n').map(x => x.trim()).find(x => x && !/^(播放量|点赞|发布时间|总粉丝数)/.test(x)) || '',
                  author_name: '',
                  plays: number((String(text || '').match(/播放量[:：]?[\\s]*([0-9.]+[万亿]?)/) || [])[1]),
                  likes: number((String(text || '').match(/点赞[:：]?[\\s]*([0-9.]+[万亿]?)/) || [])[1]),
                  published_text: (String(text || '').match(/发布时间[:：]?[\\s]*([^\\n]+)/) || [])[1] || '',
                });
                const results = [];
                for (const link of document.querySelectorAll("a[href*='/video/'],a[href*='share/video']")) {
                  const card = link.closest('li, tr, [class*=card], [class*=Card], [class*=item], [class*=Item]') || link;
                  const row = fromText(card.innerText || link.innerText || '', link.href || '');
                  if (row.item_id && row.title) results.push(row);
                }
                if (results.length) return results;
                for (const node of document.querySelectorAll('tr[class*="table-row"], tr[data-row-key], tr, [role=row]')) {
                  const key = Object.keys(node).find(k => k.startsWith('__reactFiber'));
                  let fiber = key ? node[key] : null;
                  for (let depth = 0; fiber && depth < 30; depth += 1, fiber = fiber.return) {
                    const record = fiber.memoizedProps?.record || fiber.pendingProps?.record;
                    if (!record) continue;
                    const id = String(record.item_id || record.aweme_id || record.id || '');
                    const title = String(record.item_title || record.title || '');
                    if (!id || !title) continue;
                    results.push({
                      item_id: id,
                      href: `https://www.douyin.com/video/${id}`,
                      title,
                      author_name: String(record.nick_name || record.author_name || ''),
                      score: number(record.score || record.hot_value),
                      plays: number(record.play_cnt || record.play_count),
                      likes: number(record.like_cnt || record.like_count),
                      like_rate: Number(record.like_rate || 0) || null,
                      fans: number(record.fans_cnt || record.follower_count),
                      duration: (() => {
                        const raw = number(record.item_duration || record.duration);
                        // 热点宝的 item_duration 常以毫秒返回；页面展示统一使用秒。
                        return raw && raw > 1000 ? Math.round(raw / 1000) : raw;
                      })(),
                      published_text: String(record.publish_time || record.create_time || ''),
                    });
                    break;
                  }
                }
                return results;
              }"""
        )

    @staticmethod
    def _extract_hotspot_topic_rows(page) -> list[dict[str, Any]]:
        """Read rendered topic leaderboard records; no network payload is used."""
        return page.locator("body").evaluate(
            """() => {
              const number = value => {
                const text = String(value ?? '').replace(/[,，\\s]/g, '');
                const unit = text.includes('亿') ? 100000000 : text.includes('万') ? 10000 : 1;
                const matched = text.match(/([0-9]+(?:\\.[0-9]+)?)/);
                return matched ? Math.round(Number(matched[1]) * unit) : null;
              };
              const results = [], seen = new Set();
              for (const node of document.querySelectorAll('tr, [role=row], [class*=row], [class*=Row]')) {
                const key = Object.keys(node).find(k => k.startsWith('__reactFiber'));
                let fiber = key ? node[key] : null;
                for (let depth = 0; fiber && depth < 35; depth += 1, fiber = fiber.return) {
                  const record = fiber.memoizedProps?.record || fiber.pendingProps?.record;
                  if (!record) continue;
                  const topicId = String(record.challenge_id || record.topic_id || record.id || '');
                  const topicName = String(record.challenge_name || record.topic_name || record.name || '');
                  if (topicId && topicName && !seen.has(topicId)) {
                    seen.add(topicId);
                    results.push({
                      topic_id: topicId, topic_name: topicName,
                      score: number(record.score || record.hot_value),
                    });
                  }
                  break;
                }
              }
              return results;
            }"""
        )

    @staticmethod
    def _extract_topic_detail_rows(page) -> list[dict[str, Any]]:
        """Read videoData attached to already rendered topic-detail cards."""
        return page.locator("body").evaluate(
            """() => {
              const number = value => {
                const text = String(value ?? '').replace(/[,，\\s]/g, '');
                const unit = text.includes('亿') ? 100000000 : text.includes('万') ? 10000 : 1;
                const matched = text.match(/([0-9]+(?:\\.[0-9]+)?)/);
                return matched ? Math.round(Number(matched[1]) * unit) : null;
              };
              const results = [], seen = new Set();
              for (const node of document.querySelectorAll('div, li, article')) {
                const key = Object.keys(node).find(k => k.startsWith('__reactFiber'));
                let fiber = key ? node[key] : null;
                for (let depth = 0; fiber && depth < 35; depth += 1, fiber = fiber.return) {
                  const video = fiber.memoizedProps?.videoData || fiber.pendingProps?.videoData;
                  if (!video) continue;
                  const itemId = String(video.item_id || video.aweme_id || video.id || '');
                  const title = String(video.item_title || video.title || '');
                  if (itemId && title && !seen.has(itemId)) {
                    seen.add(itemId);
                    const rawDuration = number(video.item_duration || video.duration);
                    results.push({
                      item_id: itemId,
                      href: `https://www.douyin.com/video/${itemId}`,
                      title,
                      author_name: String(video.nick_name || video.author_name || ''),
                      duration: rawDuration && rawDuration > 1000 ? Math.round(rawDuration / 1000) : rawDuration,
                      likes: number(video.like_cnt || video.like_count),
                      comments: number(video.comment_cnt || video.comment_count),
                      shares: number(video.share_cnt || video.share_count),
                      published_text: String(video.create_time || video.publish_time || ''),
                    });
                  }
                  break;
                }
              }
              return results;
            }"""
        )

    @staticmethod
    def _extract_douyin_search_rows(page) -> list[dict[str, Any]]:
        """Extract rendered ordinary Douyin search cards for the exact term."""
        return page.locator("body").evaluate(
            """() => {
              const toSeconds = value => {
                const match = String(value || '').match(/^(\\d{1,2}):(\\d{2})$/);
                return match ? Number(match[1]) * 60 + Number(match[2]) : null;
              };
              const number = value => {
                const text = String(value ?? '').replace(/[,，\\s]/g, '');
                const unit = text.includes('亿') ? 100000000 : text.includes('万') ? 10000 : 1;
                const matched = text.match(/^([0-9]+(?:\\.[0-9]+)?)[万亿]?$/);
                return matched ? Math.round(Number(matched[1]) * unit) : null;
              };
              const firstPresent = (...values) => values.find(
                value => value !== undefined && value !== null && value !== ''
              );
              const metric = (video, ...names) => number(firstPresent(
                ...names.map(name => video?.statistics?.[name]),
                ...names.map(name => video?.[name]),
              ));
              const renderedVideo = node => {
                const key = Object.keys(node).find(k => k.startsWith('__reactFiber'));
                let fiber = key ? node[key] : null;
                for (let depth = 0; fiber && depth < 35; depth += 1, fiber = fiber.return) {
                  const props = fiber.memoizedProps || fiber.pendingProps || {};
                  const value = props.awemeInfo || props.itemData || props.aweme || props.record;
                  if (value && (value.aweme_id || value.awemeId || value.id)) return value;
                }
                return null;
              };
              const results = [], seen = new Set();
              for (const link of document.querySelectorAll("a[href*='/video/']")) {
                const href = link.href || '';
                const matched = href.match(/\\/video\\/(\\d{10,})/);
                if (!matched || seen.has(matched[1])) continue;
                const card = link.closest('[class*=feed], [class*=Feed], [class*=card], [class*=Card], li, article') || link;
                const video = renderedVideo(card) || renderedVideo(link);
                const lines = String(card.innerText || link.innerText || '').split('\\n').map(x => x.trim()).filter(Boolean);
                const durationText = lines.find(x => /^\\d{1,2}:\\d{2}$/.test(x));
                const publishedText = lines.find(x => /^(刚刚|昨天|\\d+分钟前|\\d+小时前|\\d+天前|\\d{4}-\\d{1,2}-\\d{1,2})$/.test(x)) || '';
                const likesText = lines.find(x => x !== durationText && x !== publishedText && /^\\d+(?:\\.\\d+)?[万亿]?$/.test(x));
                const title = lines
                  .filter(x => x !== durationText && !x.startsWith('@') && !/^\\d+(?:\\.\\d+)?[万亿]?$/.test(x) && !/^(今天|昨天|\\d+天前)$/.test(x))
                  .sort((a, b) => b.length - a.length)[0] || '';
                const rawDuration = number(video?.duration || video?.video?.duration);
                const resolvedTitle = String(video?.desc || video?.title || title || '');
                if (!resolvedTitle) continue;
                seen.add(matched[1]);
                results.push({
                  item_id: String(video?.aweme_id || video?.awemeId || video?.id || matched[1]), href,
                  title: resolvedTitle,
                  author_name: String(video?.author?.nickname || video?.author_name || (lines.find(x => x.startsWith('@')) || '').replace(/^@/, '')),
                  duration: rawDuration && rawDuration > 1000 ? Math.round(rawDuration / 1000) : rawDuration || toSeconds(durationText),
                  plays: metric(video, 'play_count', 'playCount', 'play_cnt', 'view_count', 'viewCount'),
                  likes: metric(video, 'digg_count', 'diggCount', 'like_count', 'likeCount', 'likes') ?? number(likesText),
                  comments: metric(video, 'comment_count', 'commentCount', 'comments'),
                  shares: metric(video, 'share_count', 'shareCount', 'shares'),
                  favorites: metric(video, 'collect_count', 'collectCount', 'favorite_count', 'favoriteCount', 'favorited_count', 'favoritedCount', 'favorites'),
                  published_text: String(video?.create_time || video?.createTime || publishedText || ''),
                });
              }
              return results;
            }"""
        )

    @staticmethod
    def _extract_public_douyin_search_rows(page) -> list[dict[str, Any]]:
        """Extract existing rendered card text and React card data; never read requests."""
        return page.locator("body").evaluate(
            """() => {
              const toSeconds = value => {
                const match = String(value || '').match(/^(\\d{1,2}):(\\d{2})$/);
                return match ? Number(match[1]) * 60 + Number(match[2]) : null;
              };
              const number = value => {
                const text = String(value ?? '').replace(/[,，\\s]/g, '');
                const unit = text.includes('亿') ? 100000000 : (text.includes('万') || /w$/i.test(text)) ? 10000 : 1;
                const matched = text.match(/^([0-9]+(?:\\.[0-9]+)?)(?:[万亿wW])?$/);
                return matched ? Math.round(Number(matched[1]) * unit) : null;
              };
              const firstPresent = (...values) => values.find(
                value => value !== undefined && value !== null && value !== ''
              );
              const metric = (video, ...names) => {
                const sources = [
                  video,
                  video?.statistics,
                  video?.aweme_statistics,
                  video?.awemeStatistics,
                  video?.interact_info,
                  video?.interactInfo,
                  video?.interaction,
                  video?.interactions,
                  video?.aweme?.statistics,
                  video?.itemData?.statistics,
                  video?.video?.statistics,
                ];
                return number(firstPresent(
                  ...sources.flatMap(source => names.map(name => source?.[name])),
                ));
              };
              const renderedVideo = node => {
                const key = Object.keys(node).find(key => key.startsWith('__reactFiber'));
                let fiber = key ? node[key] : null;
                for (let depth = 0; fiber && depth < 35; depth += 1, fiber = fiber.return) {
                  const props = fiber.memoizedProps || fiber.pendingProps || {};
                  const value = firstPresent(
                    props.awemeInfo,
                    props.itemData,
                    props.aweme,
                    props.record,
                    props.data,
                    props.item?.awemeInfo,
                    props.item?.aweme,
                  );
                  if (value && (value.aweme_id || value.awemeId || value.id)) return value;
                }
                return null;
              };
              const isPublishedText = value => /^(刚刚|昨天|前天|\\d+分钟前|\\d+小时前|\\d+天前|\\d{4}[-/.]\\d{1,2}[-/.]\\d{1,2})$/.test(value);
              const isCount = value => /^\\d+(?:\\.\\d+)?[万亿wW]?$/.test(value);
              const isMetricLine = value => /^(?:播放(?:量|次数)?|观看(?:量|次数)?|浏览(?:量|次数)?|点赞(?:数)?|赞|评论(?:数)?|分享(?:数)?|转发(?:数)?|收藏(?:数)?)\\s*[:：]?\\s*\\d+(?:\\.\\d+)?[万亿wW]?$/.test(value);
              const metricText = card => {
                const values = String(card.innerText || '').split('\\n').map(value => value.trim()).filter(Boolean);
                for (const node of card.querySelectorAll('[aria-label], [title], [data-e2e]')) {
                  values.push(
                    String(node.getAttribute('aria-label') || ''),
                    String(node.getAttribute('title') || ''),
                    String(node.innerText || ''),
                  );
                }
                return values.filter(Boolean);
              };
              const metricFromDom = (values, labels) => {
                const count = '([0-9]+(?:\\.[0-9]+)?(?:[万亿wW])?)';
                const label = `(?:${labels.join('|')})`;
                const direct = new RegExp(`${label}\\s*(?:数|量|次数)?\\s*[:：]?\\s*${count}`, 'i');
                const reverse = new RegExp(`${count}\\s*${label}(?:数|量|次数)?`, 'i');
                for (const value of values) {
                  const matched = value.match(direct) || value.match(reverse);
                  if (matched) return number(matched[1]);
                }
                return null;
              };
              const metricOrDom = (video, values, names, labels) => {
                const fromProps = metric(video, ...names);
                return fromProps !== null ? fromProps : metricFromDom(values, labels);
              };
              const results = [], seen = new Set();
              for (const link of document.querySelectorAll("a[href*='/video/']")) {
                const href = link.href || '';
                const matched = href.match(/\\/video\\/(\\d{10,})/);
                if (!matched || seen.has(matched[1])) continue;
                const card = link.closest('[class*=feed], [class*=Feed], [class*=card], [class*=Card], li, article') || link;
                const video = renderedVideo(card) || renderedVideo(link);
                const lines = String(card.innerText || link.innerText || '')
                  .split('\\n').map(value => value.trim()).filter(Boolean);
                const durationText = lines.find(value => /^\\d{1,2}:\\d{2}$/.test(value));
                const publishedText = lines.find(isPublishedText) || '';
                const likesText = lines.find(value => value !== durationText && value !== publishedText && isCount(value));
                const title = lines
                  .filter(value => value !== durationText && !value.startsWith('@') && !isCount(value) && !isMetricLine(value) && !isPublishedText(value))
                  .sort((left, right) => right.length - left.length)[0] || '';
                const resolvedTitle = String(video?.desc || video?.title || title || '');
                if (!resolvedTitle) continue;
                const values = metricText(card);
                const rawDuration = number(firstPresent(video?.duration, video?.video?.duration));
                seen.add(matched[1]);
                results.push({
                  item_id: String(video?.aweme_id || video?.awemeId || video?.id || matched[1]), href,
                  title: resolvedTitle,
                  author_name: String(video?.author?.nickname || video?.author_name || (lines.find(value => value.startsWith('@')) || '').replace(/^@/, '')),
                  duration: rawDuration && rawDuration > 1000 ? Math.round(rawDuration / 1000) : rawDuration || toSeconds(durationText),
                  plays: metricOrDom(video, values, ['play_count', 'playCount', 'play_cnt', 'view_count', 'viewCount', 'playNum'], ['播放量', '播放', '观看量', '观看', '浏览量', '浏览']),
                  likes: metricOrDom(video, values, ['digg_count', 'diggCount', 'like_count', 'likeCount', 'like_cnt', 'likes'], ['点赞数', '点赞', '赞']) ?? number(likesText),
                  comments: metricOrDom(video, values, ['comment_count', 'commentCount', 'comment_cnt', 'comments'], ['评论数', '评论']),
                  shares: metricOrDom(video, values, ['share_count', 'shareCount', 'share_cnt', 'forward_count', 'forwardCount', 'shares'], ['分享数', '分享', '转发数', '转发']),
                  favorites: metricOrDom(video, values, ['collect_count', 'collectCount', 'collect_cnt', 'favorite_count', 'favoriteCount', 'favorited_count', 'favoritedCount', 'favorites'], ['收藏数', '收藏']),
                  published_text: String(video?.create_time || video?.createTime || video?.publish_time || video?.publishTime || publishedText || ''),
                });
              }
              return results;
            }"""
        )

    def _collect_douyin_search_rows(self, page) -> list[dict[str, Any]]:
        """Collect the dynamically rendered viewports of a Douyin search result page."""
        return self._collect_scrolled_rows(page, self._extract_douyin_search_rows)

    def _collect_public_douyin_search_rows(
        self,
        page,
        *,
        target_limit: int,
        scan_limit: int | None = None,
        qualifying_count: Callable[[list[dict[str, Any]]], int] | None = None,
        layout_mode: str = "default",
        network_rows: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], ProviderSearchError | None]:
        """Load rendered public-search cards until the goal or a safe stop condition."""
        target_limit = max(1, min(target_limit, _PUBLIC_SEARCH_MAX_RESULT_LIMIT))
        maximum_scan_limit = _PUBLIC_SEARCH_MAX_RAW_SCAN_LIMIT
        scan_limit = max(target_limit, min(scan_limit or target_limit, maximum_scan_limit))
        rows_by_id: dict[str, dict[str, Any]] = {}
        stagnant_rounds = 0
        previous_count = -1

        for round_index in range(_PUBLIC_SEARCH_MAX_SCROLL_ROUNDS):
            try:
                self._raise_for_public_search_block(page)
            except LicensedProviderError as exc:
                return list(rows_by_id.values()), ProviderSearchError(
                    kind=exc.kind,
                    code=exc.code or "public_search_blocked",
                    message=exc.args[0],
                    retryable=False,
                )

            # 渲染卡片(DOM)与搜索 API 响应(网络)双路收集,按 item_id 去重。
            # 当前抖音网页版卡片是 JS 组件、无 /video/ 链接,网络响应是
            # 候选 id 与 播放/点赞/评论/转发 统计的可靠来源。
            for row in self._extract_public_douyin_search_rows(page):
                item_id = str(row.get("item_id") or "")
                if item_id and item_id not in rows_by_id and len(rows_by_id) < scan_limit:
                    rows_by_id[item_id] = row
            for row in (network_rows or {}).values():
                item_id = str(row.get("item_id") or "")
                if item_id and item_id not in rows_by_id and len(rows_by_id) < scan_limit:
                    rows_by_id[item_id] = row

            current_count = len(rows_by_id)
            current_rows = list(rows_by_id.values())
            current_qualified_count = (
                qualifying_count(current_rows)
                if qualifying_count is not None
                else current_count
            )
            if current_qualified_count >= target_limit:
                return list(rows_by_id.values()), None
            if current_count >= scan_limit:
                return current_rows, self._public_search_safety_limit_error(
                    scanned_count=current_count,
                    qualified_count=current_qualified_count,
                    target_limit=target_limit,
                    scan_limit=scan_limit,
                )

            stagnant_rounds = (
                stagnant_rounds + 1 if current_count == previous_count else 0
            )
            previous_count = current_count
            if stagnant_rounds >= _PUBLIC_SEARCH_MAX_STAGNANT_ROUNDS:
                try:
                    # Verification can render while the final visible result
                    # round is settling. Recheck before calling this an end.
                    self._raise_for_public_search_block(page)
                except LicensedProviderError as exc:
                    return list(rows_by_id.values()), ProviderSearchError(
                        kind=exc.kind,
                        code=exc.code or "public_search_blocked",
                        message=exc.args[0],
                        retryable=False,
                    )
                return list(rows_by_id.values()), ProviderSearchError(
                    kind=ProviderErrorKind.VALIDATION,
                    code="public_search_platform_end",
                    message=(
                        "抖音官网搜索低频模式已到达当前可见结果末尾，本次不再翻页，"
                        f"本次得到 {current_qualified_count} 条可用候选（扫描 {current_count} 条），"
                        f"未达到目标 {target_limit} 条。"
                    ),
                )

            if round_index + 1 >= _PUBLIC_SEARCH_MAX_SCROLL_ROUNDS:
                break
            # 懒加载:滚动后需要等待刷新才有新内容。轮询等待新行出现,
            # 而不是固定等几秒;单列模式布局卡片更高,天然需要更久。
            self._scroll_and_wait_for_new_rows(
                page, count_rows=lambda: len(rows_by_id)
            )

        final_rows = list(rows_by_id.values())
        final_qualified_count = (
            qualifying_count(final_rows)
            if qualifying_count is not None
            else len(final_rows)
        )
        return final_rows, self._public_search_safety_limit_error(
            scanned_count=len(final_rows),
            qualified_count=final_qualified_count,
            target_limit=target_limit,
            scan_limit=scan_limit,
        )

    def _scroll_and_wait_for_new_rows(
        self, page, *, count_rows, max_wait_ms: int = 10_000
    ) -> bool:
        """滚动后轮询等待新行出现,而不是固定等几秒。

        平台搜索结果采用懒加载:滚动到一定位置后需要等待刷新才会出现
        新内容。每次滚动 500px 后,每 500ms 检查一次行数,直到出现
        新行或超过 max_wait_ms。返回是否在等待期内出现新行。
        """
        before = count_rows()
        try:
            page.evaluate(f"window.scrollBy(0, {_HOTSPOT_SCROLL_PIXELS})")
        except Exception:
            return False
        waited = 0
        while waited < max_wait_ms:
            page.wait_for_timeout(500)
            waited += 500
            if count_rows() > before:
                return True
        return False

    @staticmethod
    def _public_search_safety_limit_error(
        *,
        scanned_count: int,
        qualified_count: int,
        target_limit: int,
        scan_limit: int,
    ) -> ProviderSearchError:
        return ProviderSearchError(
            kind=ProviderErrorKind.SERVICE,
            code="public_search_safety_limit",
            message=(
                "抖音官网搜索已达到安全加载上限，"
                f"本次得到 {qualified_count} 条可用候选（扫描 {scanned_count}/{scan_limit} 条），"
                f"未达到目标 {target_limit} 条。"
            ),
        )

    @staticmethod
    def _prefer_public_search_single_column(page) -> bool:
        """Select the site's visible single-column result layout when offered.

        The control is optional and changes across Douyin web releases. A
        missing, hidden, or disabled control deliberately means "leave the
        current layout alone" rather than trying alternate URLs or internal
        state. The resulting item evidence makes that data-availability
        boundary visible downstream.
        """
        try:
            return bool(
                page.locator("body").evaluate(
                    """() => {
                      const normalise = value => String(value || '').replace(/\\s+/g, '');
                       const isVisible = node => {
                         const style = window.getComputedStyle(node);
                         const rect = node.getBoundingClientRect();
                         return rect.width > 0 && rect.height > 0
                           && style.display !== 'none' && style.visibility !== 'hidden'
                           && style.opacity !== '0'
                           && rect.bottom > 0 && rect.right > 0
                           && rect.top < window.innerHeight && rect.left < window.innerWidth;
                      };
                      const controls = [...document.querySelectorAll(
                        'button, [role="button"], a, [aria-label], [title]'
                      )];
                      const control = controls.find(node => {
                         const text = normalise(node.innerText || node.textContent);
                         const aria = normalise(node.getAttribute('aria-label'));
                         const title = normalise(node.getAttribute('title'));
                         const namesSingleColumn = text === '单列' || aria.includes('单列') || title.includes('单列');
                         return namesSingleColumn && isVisible(node)
                           && !node.disabled
                           && node.getAttribute('aria-disabled') !== 'true';
                       });
                       if (!control) return false;
                       if (control.getAttribute('aria-pressed') === 'true'
                         || control.getAttribute('aria-selected') === 'true'
                         || control.getAttribute('aria-current') === 'true') {
                        return true;
                      }
                      control.click();
                      return true;
                    }"""
                )
            )
        except Exception:
            return False

    def _collect_scrolled_rows(
        self,
        page,
        extractor,
        *,
        target_limit: int = _HOTSPOT_MAX_ROWS_PER_LIST,
        max_scroll_rounds: int = _HOTSPOT_MAX_SCROLL_ROUNDS,
    ) -> list[dict[str, Any]]:
        """Accumulate virtualised cards while only reading the rendered page."""
        rows_by_id: dict[str, dict[str, Any]] = {}
        stagnant_rounds = 0
        previous_count = -1
        for _ in range(max_scroll_rounds):
            for row in extractor(page):
                item_id = str(row.get("item_id") or "")
                if item_id:
                    rows_by_id[item_id] = row

            current_count = len(rows_by_id)
            if current_count >= target_limit:
                break
            stagnant_rounds = stagnant_rounds + 1 if current_count == previous_count else 0
            if stagnant_rounds >= 2:
                break
            previous_count = current_count
            page.evaluate(f"window.scrollBy(0, {_HOTSPOT_SCROLL_PIXELS})")
            page.wait_for_timeout(
                self._random_delay_ms(*_HOTSPOT_SCROLL_REFRESH_RANGE_MS)
            )
        return list(rows_by_id.values())

    @staticmethod
    def _merge_hotspot_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep one row per video while retaining every visible discovery route."""
        merged: dict[str, dict[str, Any]] = {}
        for row in rows:
            item_id = str(row.get("item_id") or "")
            if not item_id:
                continue
            current = merged.get(item_id)
            if current is None:
                current = {**row, "list_types": set(), "list_labels": set(), "source_kinds": set()}
                merged[item_id] = current
            list_type = LocalDouyinBrowserSearchProvider._as_int(row.get("list_type"))
            if list_type:
                current["list_types"].add(list_type)
            current["list_labels"].add(str(row.get("list_label") or ""))
            current["source_kinds"].add(str(row.get("source_kind") or ""))
            current["topic_exact"] = bool(current.get("topic_exact")) or bool(row.get("topic_exact"))
            for field in ("score", "plays", "likes", "fans", "duration", "comments", "shares", "favorites"):
                value = row.get(field)
                if value is not None and (current.get(field) is None or value > current[field]):
                    current[field] = value
            if current.get("like_rate") is None and row.get("like_rate") is not None:
                current["like_rate"] = row["like_rate"]
        normalized = []
        for row in merged.values():
            row["list_types"] = sorted(value for value in row["list_types"] if value)
            row["list_labels"] = sorted(value for value in row["list_labels"] if value)
            row["source_kinds"] = sorted(value for value in row["source_kinds"] if value)
            normalized.append(row)
        return normalized

    @staticmethod
    def _source_priority(row: dict[str, Any]) -> int:
        source_kinds = set(row.get("source_kinds") or [row.get("source_kind")])
        list_type = LocalDouyinBrowserSearchProvider._as_int(row.get("list_type"))
        if "video_board" in source_kinds or list_type in _HOTSPOT_LIST_TYPES:
            return 0
        if "topic_board" in source_kinds or list_type in _HOTSPOT_TOPIC_LIST_TYPES:
            return 1
        return 2

    @staticmethod
    def _quality_details(
        row: dict[str, Any],
        observed_at: datetime,
    ) -> tuple[float, str] | None:
        """Return a comparable daily-like velocity for customer candidates."""
        likes = LocalDouyinBrowserSearchProvider._as_int(row.get("likes"))
        if likes is None or likes < _MIN_QUALIFYING_LIKES:
            return None
        if LocalDouyinBrowserSearchProvider._source_priority(row) == 0:
            window_hours = LocalDouyinBrowserSearchProvider._as_int(row.get("window_hours"))
            if window_hours is None or window_hours <= 0:
                return None
            daily_likes = likes / max(window_hours / 24, 1 / 24)
            basis = "榜单周期新增点赞/天"
        else:
            published_at = LocalDouyinBrowserSearchProvider._parse_published_at(
                row.get("published_text"), observed_at
            )
            if published_at is None or published_at > observed_at:
                return None
            age_days = max((observed_at - published_at).total_seconds() / 86_400, 1)
            daily_likes = likes / age_days
            basis = "作品累计点赞/发布天数"
        if daily_likes < _MIN_QUALIFYING_LIKES_PER_DAY:
            return None
        return round(daily_likes, 4), basis

    @staticmethod
    def _to_public_search_items(
        rows: list[dict[str, Any]],
        *,
        keyword: str,
        observed_at: datetime,
        published_after: datetime | None,
        limit: int,
    ) -> tuple[
        list[ProviderSearchItem],
        list[ProviderSearchError],
        dict[str, int],
        int,
    ]:
        """Keep readable public-search cards that fit the page-known time window."""
        items: list[ProviderSearchItem] = []
        errors: list[ProviderSearchError] = []
        filter_counts = {"duration": 0, "relevance": 0}
        published_filtered_count = 0
        seen: set[str] = set()

        for index, row in enumerate(rows):
            href = str(row.get("href") or "")
            item_id = str(row.get("item_id") or "")
            match = _VIDEO_ID_RE.search(href)
            if not item_id and not match:
                continue
            item_id = item_id or match.group(1)
            if item_id in seen:
                continue
            title = " ".join(
                part.strip()
                for part in (
                    str(row.get("title") or ""),
                    str(row.get("text") or ""),
                    str(row.get("aria") or ""),
                )
                if part.strip()
            )[:200]
            if not title:
                errors.append(
                    ProviderSearchError(
                        kind=ProviderErrorKind.VALIDATION,
                        message="公开搜索作品链接缺少可读标题，已跳过。",
                        item_index=index,
                    )
                )
                continue
            # 官网搜索卡片会同时露出作者名；不能因为作者昵称里有关键词就
            # 把无关视频带入。只保留标题/内联话题直接命中的作品。
            if not title_matches_keyword(
                title=title, keyword=keyword, require_intent=False
            ):
                filter_counts["relevance"] += 1
                continue
            duration_seconds = LocalDouyinBrowserSearchProvider._as_int(row.get("duration"))
            published_at = LocalDouyinBrowserSearchProvider._parse_published_at(
                row.get("published_text"), observed_at
            )
            if (
                published_after is not None
                and published_at is not None
                and published_at < published_after
            ):
                published_filtered_count += 1
                continue
            seen.add(item_id)
            items.append(
                LocalDouyinBrowserSearchProvider._to_public_provider_item(
                    row=row,
                    item_id=item_id,
                    title=title,
                    duration_seconds=duration_seconds,
                    observed_at=observed_at,
                    published_at=published_at,
                    keyword=keyword,
                    provider_rank=len(items) + 1,
                )
            )
            if len(items) >= limit:
                break
        return items, errors, filter_counts, published_filtered_count

    @staticmethod
    def _to_public_provider_item(
        *,
        row: dict[str, Any],
        item_id: str,
        title: str,
        duration_seconds: int | None,
        observed_at: datetime,
        published_at: datetime | None,
        keyword: str,
        provider_rank: int,
    ) -> ProviderSearchItem:
        warnings: list[str] = []
        layout_mode = str(row.get("search_layout") or "")
        time_filter_receipt = str(
            row.get("search_time_filter") or "平台筛选状态未记录"
        )
        time_filter_warning = str(row.get("search_time_filter_warning") or "")
        if layout_mode == "default":
            warnings.append(
                "抖音官网当前未提供可见的“单列”结果布局，已按默认卡片读取；"
                "未显示的指标会保留为空。"
            )
        if time_filter_warning:
            warnings.append(time_filter_warning)
        if published_at is None:
            published_at = observed_at
            warnings.append("公开搜索页未显示可核验发布时间，已保留但需要人工确认。")
        if duration_seconds is None or duration_seconds <= 0:
            duration_seconds = None
            warnings.append("公开搜索页未返回视频时长。")
        metrics = {
            "plays": LocalDouyinBrowserSearchProvider._as_int(row.get("plays")),
            "likes": LocalDouyinBrowserSearchProvider._as_int(row.get("likes")),
            "comments": LocalDouyinBrowserSearchProvider._as_int(row.get("comments")),
            "shares": LocalDouyinBrowserSearchProvider._as_int(row.get("shares")),
            "favorites": LocalDouyinBrowserSearchProvider._as_int(row.get("favorites")),
        }
        missing_metrics = [
            label
            for field, label in (
                ("plays", "播放"),
                ("likes", "点赞"),
                ("comments", "评论"),
                ("shares", "分享"),
                ("favorites", "收藏"),
            )
            if metrics[field] is None
        ]
        if missing_metrics:
            warnings.append(
                f"公开搜索页未显示互动指标：{'、'.join(missing_metrics)}。"
            )
        return ProviderSearchItem(
            platform=Platform.DOUYIN,
            platform_item_id=item_id,
            title=title,
            author_id=f"douyin-public-{item_id}",
            author_name=str(row.get("author_name") or "抖音作者待补充"),
            published_at=published_at,
            duration_seconds=duration_seconds,
            source_url=HttpUrl(f"https://www.douyin.com/video/{item_id}"),
            provider_rank=provider_rank,
            metrics=VideoMetricSnapshot(
                item_id=item_id,
                sampled_at=observed_at,
                **metrics,
                confidence=0.55,
            ),
            evidence=(
                f"douyin_public_search:关键词={keyword};来源=browser_rendered;"
                f"布局={'单列' if layout_mode == 'single_column' else '默认卡片' if layout_mode == 'default' else '未标记'};"
                f"发布时间筛选={time_filter_receipt};"
                f"发布时间={row.get('published_text') or '未返回'};"
                f"time={'platform' if row.get('published_text') else 'unknown'};"
                f"播放量={metrics['plays'] if metrics['plays'] is not None else '未返回'};"
                f"点赞数={metrics['likes'] if metrics['likes'] is not None else '未返回'};"
                f"评论数={metrics['comments'] if metrics['comments'] is not None else '未返回'};"
                f"分享数={metrics['shares'] if metrics['shares'] is not None else '未返回'};"
                f"收藏数={metrics['favorites'] if metrics['favorites'] is not None else '未返回'};"
                f"时长秒={duration_seconds if duration_seconds is not None else '未返回'}"
            ),
            data_quality_warnings=warnings,
        )

    @staticmethod
    def _to_items(
        rows: list[dict[str, Any]],
        keyword: str,
        observed_at: datetime,
        limit: int,
    ) -> tuple[
        list[ProviderSearchItem],
        list[ProviderSearchItem],
        list[ProviderSearchError],
        dict[str, int],
    ]:
        items: list[ProviderSearchItem] = []
        low_incremental_items: list[ProviderSearchItem] = []
        errors: list[ProviderSearchError] = []
        filter_counts = {
            "duration": 0,
            "incremental_plays": 0,
            "relevance": 0,
            "quality": 0,
        }
        seen: set[str] = set()
        sorted_rows = sorted(
            rows,
            key=lambda row: (
                LocalDouyinBrowserSearchProvider._source_priority(row),
                -(LocalDouyinBrowserSearchProvider._as_int(row.get("likes")) or 0),
                -(LocalDouyinBrowserSearchProvider._as_int(row.get("score")) or 0),
                str(row.get("item_id") or ""),
            ),
        )
        for index, row in enumerate(sorted_rows):
            href = str(row.get("href") or "")
            item_id = str(row.get("item_id") or "")
            match = _VIDEO_ID_RE.search(href)
            if not item_id and not match:
                continue
            item_id = item_id or match.group(1)
            if item_id in seen:
                continue
            title = " ".join(
                part.strip()
                for part in (
                    str(row.get("title") or ""),
                    str(row.get("text") or ""),
                    str(row.get("aria") or ""),
                )
                if part.strip()
            )[:200]
            if not title:
                errors.append(
                    ProviderSearchError(
                        kind=ProviderErrorKind.VALIDATION,
                        message="可见作品链接缺少可读标题，已跳过。",
                        item_index=index,
                    )
                )
                continue
            duration_seconds = LocalDouyinBrowserSearchProvider._as_int(row.get("duration"))
            quality = LocalDouyinBrowserSearchProvider._quality_details(row, observed_at)
            seen.add(item_id)
            incremental_plays = LocalDouyinBrowserSearchProvider._as_int(row.get("plays"))
            items.append(
                LocalDouyinBrowserSearchProvider._to_provider_item(
                    row=row,
                    item_id=item_id,
                    title=title,
                    duration_seconds=duration_seconds,
                    incremental_plays=incremental_plays,
                    observed_at=observed_at,
                    keyword=keyword,
                    provider_rank=len(items) + 1,
                    likes_per_day=quality[0] if quality else None,
                    quality_basis=quality[1] if quality else None,
                )
            )
            if len(items) >= limit:
                break
        return items, low_incremental_items, errors, filter_counts

    @staticmethod
    def _to_provider_item(
        *,
        row: dict[str, Any],
        item_id: str,
        title: str,
        duration_seconds: int | None,
        incremental_plays: int | None,
        observed_at: datetime,
        keyword: str,
        provider_rank: int,
        likes_per_day: float | None,
        quality_basis: str | None,
    ) -> ProviderSearchItem:
        list_labels = list(row.get("list_labels") or [row.get("list_label", "")])
        source_kinds = list(row.get("source_kinds") or [row.get("source_kind", "")])
        is_video_board = LocalDouyinBrowserSearchProvider._source_priority(row) == 0
        published_at = LocalDouyinBrowserSearchProvider._parse_published_at(
            row.get("published_text"), observed_at
        )
        warnings: list[str] = []
        if published_at is None:
            published_at = observed_at
            warnings.append("未取得有效发布时间，页面展示为采样时间。")
        if duration_seconds is None or duration_seconds <= 0:
            duration_seconds = None
            warnings.append("热点宝未返回视频时长。")
        if likes_per_day is None:
            warnings.append("互动数据不足，综合热度只能按已返回字段计算。")
        return ProviderSearchItem(
            platform=Platform.DOUYIN,
            platform_item_id=item_id,
            title=title,
            author_id=f"hotspot-{item_id}",
            author_name=str(row.get("author_name") or "热点宝作者待补充"),
            published_at=published_at,
            duration_seconds=duration_seconds,
            source_url=HttpUrl(f"https://www.douyin.com/video/{item_id}"),
            provider_rank=provider_rank,
            metrics=VideoMetricSnapshot(
                item_id=item_id,
                sampled_at=observed_at,
                plays=incremental_plays,
                likes=LocalDouyinBrowserSearchProvider._as_int(row.get("likes")),
                comments=LocalDouyinBrowserSearchProvider._as_int(row.get("comments")),
                shares=LocalDouyinBrowserSearchProvider._as_int(row.get("shares")),
                favorites=LocalDouyinBrowserSearchProvider._as_int(row.get("favorites")),
                confidence=(0.8 if row.get("topic_exact") or "video_board" in source_kinds else 0.6),
            ),
            evidence=(
                f"hotspot:{'|'.join(list_labels) or '爆款榜'}:"
                f"{row.get('window_hours', '?')}h:关键词={keyword};"
                f"来源={'|'.join(source_kinds) or 'browser_visible'};"
                f"time={'platform' if row.get('published_text') else 'unknown'};"
                f"日均点赞={likes_per_day if likes_per_day is not None else '未返回'};"
                f"质量口径={quality_basis or '未返回'};"
                f"严格话题={1 if row.get('topic_exact') else 0};"
                f"话题={row.get('topic_name') or '未返回'};"
                f"热度={row.get('score') if row.get('score') is not None else '未返回'};"
                f"{'新增播放量' if is_video_board else '播放量'}={incremental_plays if incremental_plays is not None else '未返回'};"
                f"{'新增点赞量' if is_video_board else '点赞数'}={row.get('likes') if row.get('likes') is not None else '未返回'};"
                f"点赞率={row.get('like_rate') if row.get('like_rate') is not None else '未返回'};"
                f"粉丝={row.get('fans') if row.get('fans') is not None else '未返回'};"
                f"时长秒={duration_seconds if duration_seconds is not None else '未返回'}"
            ),
            data_quality_warnings=warnings,
        )

    @staticmethod
    def _as_int(value: Any) -> int | None:
        try:
            return max(0, int(float(value))) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_published_at(value: Any, observed_at: datetime) -> datetime | None:
        """Parse a visible publication time for quality age calculations."""
        if value in (None, ""):
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.isdigit():
            timestamp = float(text)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            try:
                return datetime.fromtimestamp(timestamp, tz=observed_at.tzinfo)
            except (OSError, OverflowError, ValueError):
                return None
        relative = re.fullmatch(r"(\d+)\s*分钟前", text)
        if relative:
            return observed_at - timedelta(minutes=int(relative.group(1)))
        relative = re.fullmatch(r"(\d+)\s*小时前", text)
        if relative:
            return observed_at - timedelta(hours=int(relative.group(1)))
        relative = re.fullmatch(r"(\d+)\s*天前", text)
        if relative:
            return observed_at - timedelta(days=int(relative.group(1)))
        if text == "刚刚":
            return observed_at
        if text == "昨天":
            return observed_at - timedelta(days=1)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=observed_at.tzinfo)
        return parsed.astimezone(observed_at.tzinfo)

    def _debug_url(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}/json/version"

    def _debug_pages_url(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}/json/list"

    def _missing_prerequisites(self) -> list[str]:
        missing: list[str] = []
        if not self.enabled:
            missing.append("DOUYIN_BROWSER_DISCOVERY_ENABLED=true")
        if not self._browser_engine_available():
            missing.append("Playwright Python 依赖")
        if self._browser_executable() is None:
            missing.append("Google Chrome" if self.browser_channel == "chrome" else "Microsoft Edge")
        return missing

    @staticmethod
    def _browser_engine_available() -> bool:
        try:
            import playwright.sync_api  # noqa: F401
            return True
        except ImportError:
            return False

    def _browser_executable(self) -> Path | None:
        names = ["chrome", "chrome.exe"] if self.browser_channel == "chrome" else ["msedge", "msedge.exe"]
        for name in names:
            found = shutil.which(name)
            if found:
                return Path(found)
        if self.browser_channel == "chrome":
            base_paths = [
                Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            ]
            local_app_data = os.getenv("LOCALAPPDATA")
            if local_app_data:
                base_paths.append(
                    Path(local_app_data) / "Google/Chrome/Application/chrome.exe"
                )
        else:
            base_paths = [
                Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
                Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
            ]
        for path in base_paths:
            if path.exists():
                return path
        return None


class LocalDouyinPublicSearchProvider(LocalDouyinBrowserSearchProvider):
    """A separately attributable, render-only adapter for Douyin public search."""

    # Keep login-wall empty responses from the first adapter revision out of
    # the normal 10-minute cache after the user completes manual login.
    provider_name = "douyin_public_browser_v2"
    adapter_version = _PUBLIC_SEARCH_ADAPTER_VERSION
    browser_entry_url = _PUBLIC_DOUYIN_ENTRY_URL

    def capabilities(self) -> ProviderCapability:
        missing = self._missing_prerequisites()
        return ProviderCapability(
            provider_name=self.provider_name,
            display_name="本机 Chrome 抖音官网搜索",
            mode=ProviderMode.LOCAL_BROWSER,
            enabled=not missing,
            supported_platforms=[Platform.DOUYIN] if not missing else [],
            max_page_size=_PUBLIC_SEARCH_MAX_RESULT_LIMIT,
            supports_published_after=True,
            supports_metric_refresh=False,
            supports_usage=True,
            permission_status="local_browser_public_search",
            credential_alias="local-dedicated-browser-profile",
            missing_configuration=missing,
        )

    def session_status(self) -> BrowserSessionStatus:
        return self._public_session_status(super().session_status())

    def _start_browser(self, *, visible: bool) -> BrowserSessionStatus:
        """Keep the official-search login response free of legacy Hotspot wording."""
        return self._public_session_status(super()._start_browser(visible=visible))

    @staticmethod
    def _public_session_status(status: BrowserSessionStatus) -> BrowserSessionStatus:
        if status.phase == "browser_closed":
            return BrowserSessionStatus(
                status.enabled,
                False,
                True,
                False,
                status.phase,
                "抖音官网登录浏览器尚未打开；点击“登录抖音”后在可见窗口扫码或完成验证。",
            )
        if status.phase == "waiting_login":
            return BrowserSessionStatus(
                status.enabled,
                status.running,
                True,
                False,
                status.phase,
                "抖音官网正在等待扫码登录；请在可见窗口完成登录或安全验证。",
            )
        if status.phase == "starting":
            return BrowserSessionStatus(
                status.enabled,
                status.running,
                True,
                False,
                status.phase,
                "抖音官网登录窗口正在打开；请稍候在可见窗口扫码或完成验证。",
            )
        if status.running and status.phase in {"browser_open", "ready"}:
            return BrowserSessionStatus(
                True,
                True,
                False,
                True,
                "ready",
                "抖音官网搜索浏览器已启动；实际搜索时会核验登录或安全验证。",
            )
        return status

    def search(
        self,
        platform: Platform,
        keyword: str,
        published_after: datetime | None,
        limit: int,
        idempotency_key: str,
        hotspot_window_hours: int | None = None,
    ) -> ProviderSearchPage:
        return self.search_public(
            platform=platform,
            keyword=keyword,
            published_after=published_after,
            limit=limit,
            idempotency_key=idempotency_key,
            hotspot_window_hours=hotspot_window_hours,
        )
