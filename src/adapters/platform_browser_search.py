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
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlparse, urlunparse
from urllib.request import ProxyHandler, build_opener

from pydantic import HttpUrl

from src.adapters.browser_process import (
    close_owned_browser,
    record_browser_process,
    reclaim_orphan_browser,
)
from src.adapters.browser_window import (
    minimize_browser_window,
    reveal_browser_window,
)
from src.adapters.drission_browser import ANTI_DETECTION_INIT_SCRIPT
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


_LOCAL_DEBUG_OPENER = build_opener(ProxyHandler({}))


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
    fallback_link_selectors: tuple[str, ...] = ()


_SPECS = {
    Platform.XIAOHONGSHU: _PlatformSpec(
        platform=Platform.XIAOHONGSHU,
        label="小红书",
        home_url="https://www.xiaohongshu.com/",
        search_url=(
            "https://www.xiaohongshu.com/search_result?keyword={keyword}&source=web_search_result_notes&type=51"
        ),
        page_host="xiaohongshu.com",
        link_selector="a[href*='/explore/'], a[href*='/note/']",
        item_id_pattern=re.compile(r"/(?:explore|note)/([a-zA-Z0-9_-]+)"),
        login_markers=(
            "扫码登录",
            "登录后查看更多",
            "登录后查看",
            "请通过验证",
            "安全验证",
        ),
        fallback_link_selectors=("a[href*='/note/']", "a[href*='/explore/']"),
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
        fallback_link_selectors=("a[href*='/video/']", "a[href*='/short-video/']"),
    ),
    Platform.BILIBILI: _PlatformSpec(
        platform=Platform.BILIBILI,
        label="B站",
        home_url="https://www.bilibili.com/",
        search_url="https://search.bilibili.com/all?keyword={keyword}",
        page_host="bilibili.com",
        link_selector="a[href*='/video/BV']",
        item_id_pattern=re.compile(r"/video/(BV[a-zA-Z0-9]+)"),
        login_markers=("扫码登录", "请完成验证", "安全验证"),
    ),
}

_ACCESS_MARKERS = ("访问频繁", "操作频繁", "请求过于频繁", "网络环境存在风险")
_HARD_VERIFICATION_MARKERS = ("请通过验证", "请完成验证", "安全验证")
_MAX_SCROLL_ROUNDS = 18
_MAX_STAGNANT_SCROLL_ROUNDS = 3
# 快手的结果流每次懒加载通常只补充少量卡片。对需要按发布时间筛选的
# 搜索，18 轮不足以填满最多 300 条的原始扫描池；仍保留有限次数和
# 每轮间隔，并在页面末尾或出现安全提示时立即停止。
_KUAISHOU_MAX_SCROLL_ROUNDS = 60
_MAX_RESULT_LIMIT = 100
_XIAOHONGSHU_PUBLIC_RESULT_LIMIT = 15
_XIAOHONGSHU_PUBLIC_RAW_TARGET_LIMIT = 20
_XIAOHONGSHU_PUBLIC_MAX_SCROLL_ROUNDS = 3
# B 站搜索页一次返回一页公开卡片。找素材默认目标是 30 条，最多看两页
#（约 60 条原始卡片）作为有限的相关性筛选缓冲；直匹配达到目标立即早停。
# 不能为了凑“严格命中”把用户的一次搜索扩成数百条页面扫描。
_BILIBILI_MAX_SEARCH_PAGES = 2
_BILIBILI_RAW_SCAN_LIMIT = 60
_PUBLIC_SEARCH_END_MARKERS = (
    "没有更多",
    "没有更多了",
    "已加载全部",
    "已经到底了",
)
_ADAPTER_VERSION = "visible_browser_network_v8"


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
        anonymous_only: bool = False,
        allow_xiaohongshu_login: bool = False,
        clock=None,
    ) -> None:
        if platform not in _SPECS:
            raise ValueError(f"不支持的本机浏览器平台：{platform.value}")
        self.platform = platform
        self.spec = _SPECS[platform]
        self.adapter_version = f"{_ADAPTER_VERSION}_{platform.value}"
        # 小红书默认只能使用全新、隔离的未登录公开资料目录。只有专门的
        # 人工登录依赖显式声明 allow_xiaohongshu_login，才能打开可见登录窗口。
        self.anonymous_only = anonymous_only or (
            platform == Platform.XIAOHONGSHU and not allow_xiaohongshu_login
        )
        self.is_xiaohongshu_login_profile = bool(
            platform == Platform.XIAOHONGSHU
            and allow_xiaohongshu_login
            and not self.anonymous_only
        )
        self.enabled = bool(enabled)
        self.profile_dir = profile_dir
        self.browser_channel = browser_channel
        self.debug_port = debug_port
        self.timeout_seconds = max(10.0, min(float(timeout_seconds), 60.0))
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.provider_name = f"{platform.value}_local_browser"
        self._collection_stop_reason: str | None = None
        self._collection_stop_message: str | None = None
        self._collection_filter_notes: list[str] = []
        self._collection_metrics: dict[str, Any] = {}
        self._collection_rule_failure: str | None = None
        self._xiaohongshu_video_filter_confirmed = False
        self._xiaohongshu_time_filter_confirmed = False
        self._xiaohongshu_search_response_seen = False
        self._xiaohongshu_video_response_seen = False
        # Only expose login reset after the page soft-recovery path fails;
        # an initial login prompt is not a reason to clear state.
        self.login_reset_available = False
        # 由本实例启动的浏览器进程（用于只回收"自己的"浏览器）。
        self._browser_process: subprocess.Popen[bytes] | None = None
        self._browser_pid: int | None = None

    def capabilities(self) -> ProviderCapability:
        missing = self._missing_prerequisites()
        return ProviderCapability(
            provider_name=self.provider_name,
            display_name=(
                f"本机 Chrome {self.spec.label}未登录公开搜索"
                if self.anonymous_only
                else (
                    "本机 Chrome 小红书登录搜索"
                    if self.is_xiaohongshu_login_profile
                    else f"本机 Chrome {self.spec.label}搜索"
                )
            ),
            mode=ProviderMode.LOCAL_BROWSER,
            enabled=not missing,
            supported_platforms=[self.platform] if not missing else [],
            max_page_size=(
                _XIAOHONGSHU_PUBLIC_RESULT_LIMIT
                if self.anonymous_only
                else _MAX_RESULT_LIMIT
            ),
            supports_published_after=True,
            supports_metric_refresh=False,
            supports_usage=False,
            permission_status=(
                "public_browser_anonymous_only"
                if self.anonymous_only
                else (
                    "manual_login_required"
                    if self.is_xiaohongshu_login_profile
                    else "public_browser_optional_login"
                )
            ),
            credential_alias=(
                f"isolated-{self.platform.value}-public-browser-profile"
                if self.anonymous_only
                else (
                    "isolated-xiaohongshu-login-browser-profile"
                    if self.is_xiaohongshu_login_profile
                    else f"local-{self.platform.value}-browser-profile"
                )
            ),
            missing_configuration=missing,
        )

    def reclaim_orphan_browser(self) -> tuple[bool, str]:
        """启动前回收上一次后端留下的孤儿浏览器。

        只有记录的 PID 与 --user-data-dir 同时匹配才回收；不会按进程名批量
        结束 Chrome，因此不会影响用户自己开着的浏览器。
        """
        return reclaim_orphan_browser(self.profile_dir)

    def close_owned_browser(self) -> tuple[bool, str]:
        """后端退出时只关闭本实例启动的浏览器，并等待 profile 锁释放。

        超时会返回明确说明，不静默失败：否则下次启动会撞上一个锁住的 profile，
        而用户完全不知道发生过什么。
        """
        pid = self._browser_pid
        if not pid:
            record = read_browser_process_record(self.profile_dir)
            pid = int(record["pid"]) if record and record.get("pid") else None
        if not pid:
            return True, "本次没有由本进程启动的浏览器。"
        closed, message = close_owned_browser(pid, self.profile_dir)
        if closed:
            self._browser_process = None
            self._browser_pid = None
        return closed, message

    def session_status(self) -> BrowserSessionStatus:
        missing = self._missing_prerequisites()
        if not self.enabled:
            return BrowserSessionStatus(
                False,
                False,
                False if self.anonymous_only else True,
                False,
                "disabled",
                (
                    "小红书尚未登录；请先打开登录窗口完成扫码，再开始找素材。"
                    if self.is_xiaohongshu_login_profile
                    else f"{self.spec.label}浏览器搜索已关闭。"
                ),
            )
        if missing:
            return BrowserSessionStatus(
                True,
                False,
                False if self.anonymous_only else True,
                False,
                "dependency_missing",
                (
                    f"小红书登录浏览器尚未就绪：{'；'.join(missing)}。"
                    if self.is_xiaohongshu_login_profile
                    else f"{self.spec.label}浏览器尚未就绪：{'；'.join(missing)}。"
                ),
            )
        try:
            with _LOCAL_DEBUG_OPENER.open(self._debug_url(), timeout=0.6) as response:
                payload = json.loads(response.read().decode("utf-8"))
            with _LOCAL_DEBUG_OPENER.open(
                self._debug_pages_url(), timeout=0.6
            ) as response:
                pages = json.loads(response.read().decode("utf-8"))
        except (URLError, OSError, ValueError, json.JSONDecodeError):
            if self.is_xiaohongshu_login_profile:
                return BrowserSessionStatus(
                    True,
                    False,
                    False,
                    False,
                    "browser_closed",
                    "小红书素材浏览器已关闭；下次查看、搜索或转写时会使用已保存的登录资料自动重启。",
                )
            return BrowserSessionStatus(
                True,
                False,
                False if self.anonymous_only else True,
                False,
                "waiting_login"
                if self.is_xiaohongshu_login_profile
                else "browser_closed",
                (
                    f"{self.spec.label}未登录公开浏览器尚未打开；开始找素材时会使用隔离会话启动。"
                    if self.anonymous_only
                    else (
                        "小红书尚未登录；请点击“登录小红书”并完成扫码后再找素材。"
                        if self.is_xiaohongshu_login_profile
                        else f"{self.spec.label}浏览器尚未打开；开始找素材时会自动打开。"
                    )
                ),
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
                False if self.anonymous_only else True,
                True if self.anonymous_only else False,
                (
                    "ready"
                    if self.anonymous_only
                    else (
                        "login_browser_open"
                        if self.is_xiaohongshu_login_profile
                        else "browser_open"
                    )
                ),
                (
                    f"{product} 已打开；将使用隔离的未登录会话进入{self.spec.label}公开搜索页。"
                    if self.anonymous_only
                    else (
                        "小红书登录浏览器已打开，正在检查登录状态。"
                        if self.is_xiaohongshu_login_profile
                        else f"{product} 已打开，正在进入{self.spec.label}公开页面。"
                    )
                ),
            )
        login_required = self._visible_login_required()
        if login_required is True:
            return BrowserSessionStatus(
                True,
                True,
                True,
                False,
                "blocked_verification" if self.anonymous_only else "waiting_login",
                (
                    f"{self.spec.label}要求安全验证，未登录公开搜索已停止。"
                    if self.anonymous_only
                    else (
                        "小红书登录浏览器已打开；请在该窗口完成扫码或人工验证。"
                        if self.is_xiaohongshu_login_profile
                        else f"请在专用 Chrome 窗口完成{self.spec.label}扫码登录或人工验证。"
                    )
                ),
            )
        if login_required is None:
            return BrowserSessionStatus(
                True,
                True,
                False,
                True,
                "login_browser_open" if self.is_xiaohongshu_login_profile else "ready",
                (
                    "小红书登录浏览器已打开；当前未见登录拦截，可开始找素材。"
                    if self.is_xiaohongshu_login_profile
                    else f"{self.spec.label}公开页面已打开；将直接尝试读取公开搜索结果。"
                ),
            )
        return BrowserSessionStatus(
            True,
            True,
            False,
            True,
            "ready",
            (
                f"{self.spec.label}未登录公开浏览器已就绪；只读取搜索页已加载的作品元数据。"
                if self.anonymous_only
                else (
                    "小红书已登录，可读取搜索页已加载的作品元数据。"
                    if self.is_xiaohongshu_login_profile
                    else f"{self.spec.label}公开浏览器已就绪；只读取搜索页已加载的作品元数据。"
                )
            ),
        )

    def open_login_browser(self) -> BrowserSessionStatus:
        """Show a user-facing window for QR login or manual verification."""
        if self.anonymous_only:
            raise LicensedProviderError(
                f"{self.spec.label}只允许隔离的未登录公开搜索，不能打开登录或扫码窗口。",
                kind=ProviderErrorKind.AUTHORIZATION,
                retryable=False,
            )
        return self._start_browser(visible=True)

    def start_login_browser(self) -> BrowserSessionStatus:
        """Start the dedicated profile without interrupting background work."""
        if self.anonymous_only:
            raise LicensedProviderError(
                f"{self.spec.label}只允许隔离的未登录公开搜索，不能启动登录资料目录。",
                kind=ProviderErrorKind.AUTHORIZATION,
                retryable=False,
            )
        return self._start_browser(visible=False)

    def reset_login_state(self, *, confirmed: bool = False) -> BrowserSessionStatus:
        """Clear only this platform's visible login state after explicit confirmation.

        The dedicated browser profile is preserved.  Cookies are cleared by the
        platform domain and storage is cleared only on currently open pages of
        that same domain; no other platform profile is touched and no login or
        verification flow is automated.
        """
        if not confirmed:
            raise LicensedProviderError(
                f"重置{self.spec.label}登录状态前需要明确确认。",
                kind=ProviderErrorKind.VALIDATION,
                retryable=False,
            )
        if self.anonymous_only:
            raise LicensedProviderError(
                f"{self.spec.label}使用隔离的未登录公开会话，不需要重置登录状态。",
                kind=ProviderErrorKind.AUTHORIZATION,
                retryable=False,
            )
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{self.debug_port}",
                    timeout=2500,
                )
                for context in browser.contexts:
                    context.clear_cookies(domain=self.spec.page_host)
                    for page in context.pages:
                        if self.spec.page_host not in str(page.url or "").casefold():
                            continue
                        try:
                            page.evaluate(
                                """() => {
                                    window.localStorage.clear();
                                    window.sessionStorage.clear();
                                }"""
                            )
                        except Exception:
                            # Cookie clearing remains the authoritative reset;
                            # a closed page must not make the operation broader.
                            continue
        except (PlaywrightError, OSError) as exc:
            raise LicensedProviderError(
                f"{self.spec.label}登录状态重置失败；未删除浏览器资料，请稍后重试。",
                kind=ProviderErrorKind.CONNECTION,
                retryable=True,
            ) from exc
        self.login_reset_available = False
        return self.session_status()

    def start_public_browser(self) -> BrowserSessionStatus:
        """Start a background public-only browser; it never opens a login flow."""
        if self.is_xiaohongshu_login_profile:
            raise LicensedProviderError(
                "小红书登录资料目录只用于人工登录，不能用于公开素材搜索。",
                kind=ProviderErrorKind.AUTHORIZATION,
                retryable=False,
            )
        if not self.anonymous_only:
            return self.start_login_browser()
        return self._start_browser(visible=False)

    def _start_browser(self, *, visible: bool) -> BrowserSessionStatus:
        if self.anonymous_only and visible:
            raise LicensedProviderError(
                f"{self.spec.label}未登录公开搜索不允许打开可见登录窗口。",
                kind=ProviderErrorKind.AUTHORIZATION,
                retryable=False,
            )
        status = self.session_status()
        if status.running and visible:
            # Preserve the existing dedicated profile instead of killing and
            # immediately reopening Chrome on the same profile/port.
            reveal_browser_window(self.debug_port)
            return status
        if status.running and not visible:
            minimize_browser_window(self.debug_port)
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
        if self.anonymous_only:
            browser_args.append("--incognito")
        if visible:
            browser_args.extend(
                ["--new-window", "--window-position=80,80", "--window-size=1100,800"]
            )
        else:
            browser_args.extend(
                [
                    "--start-minimized",
                    "--window-size=900,700",
                ]
            )
        browser_args.append(self.spec.home_url)
        # 必须保留 Popen 句柄：Popen 的返回值此前被直接丢弃，导致浏览器 PID
        # 从未记录，后端崩溃后只能靠"按进程名批量结束 Chrome"补救——那会连用户
        # 自己的浏览器一起杀掉。这里记下 PID 与 --user-data-dir 的对应关系。
        self._browser_process = subprocess.Popen(  # noqa: S603 - executable is resolved from an allowlist
            browser_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._browser_pid = self._browser_process.pid
        record_browser_process(
            self.profile_dir, self._browser_pid, platform=self.platform.value
        )
        if not visible:
            # Chromium can restore its window while opening the initial tab,
            # even when --start-minimized is present.  Keep routine discovery
            # in the background without touching an explicit login window.
            minimize_browser_window(self.debug_port)
        for delay_seconds in (0.5, 1.0, 1.5):
            time.sleep(delay_seconds)
            status = self.session_status()
            if status.running:
                if not visible:
                    minimize_browser_window(self.debug_port)
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
                else (
                    f"{self.spec.label}未登录公开浏览器正在后台启动；系统随后会自动开始搜索。"
                    if self.anonymous_only
                    else f"{self.spec.label}公开浏览器正在后台启动；系统随后会自动开始搜索。"
                )
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
        *,
        search_filters: dict[str, str] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ProviderSearchPage:
        del hotspot_window_hours
        if platform != self.platform:
            raise LicensedProviderError(
                f"{self.spec.label}适配器不能搜索其他平台。",
                kind=ProviderErrorKind.VALIDATION,
            )
        max_result_limit = (
            _XIAOHONGSHU_PUBLIC_RESULT_LIMIT
            if self.anonymous_only
            else _MAX_RESULT_LIMIT
        )
        if not 1 <= limit <= max_result_limit:
            raise LicensedProviderError(
                f"{self.spec.label}每次最多保留 {max_result_limit} 条候选。",
                kind=ProviderErrorKind.VALIDATION,
            )
        requested_kuaishou_filters = self._normalize_kuaishou_filters(search_filters)
        kuaishou_filters = requested_kuaishou_filters or (
            {
                "kuaishou_sort": "platform",
                "kuaishou_duration_bucket": "all",
            }
            if self.platform == Platform.KUAISHOU
            else {}
        )
        status = self.session_status()
        if not status.running or status.login_required:
            raise LicensedProviderError(
                status.message,
                kind=ProviderErrorKind.AUTHORIZATION,
                retryable=False,
            )

        observed_at = self.clock()
        search_started = time.perf_counter()
        raw_target = (
            min(_XIAOHONGSHU_PUBLIC_RAW_TARGET_LIMIT, limit + 5)
            if self.anonymous_only
            else (
                min(_BILIBILI_RAW_SCAN_LIMIT, max(30, limit * 2))
                if self.platform == Platform.BILIBILI
                else max(300, limit * 5)
            )
        )
        self._collection_stop_reason = None
        self._collection_stop_message = None
        self._collection_filter_notes = []
        self._collection_metrics = {}
        self._collection_rule_failure = None
        self._xiaohongshu_video_filter_confirmed = False
        self._xiaohongshu_time_filter_confirmed = False
        self._xiaohongshu_search_response_seen = False
        self._xiaohongshu_video_response_seen = False
        collection_kwargs: dict[str, Any] = {}
        if self.platform in {Platform.XIAOHONGSHU, Platform.BILIBILI}:
            collection_kwargs["published_filter_days"] = self._platform_filter_days(
                published_after,
                observed_at,
            )
        if requested_kuaishou_filters:
            collection_kwargs["search_filters"] = kuaishou_filters
        if self.platform == Platform.KUAISHOU:
            collection_kwargs.update(
                qualified_target=limit,
                qualifying_count=lambda rows: self._count_kuaishou_qualified_rows(
                    rows,
                    published_after=published_after,
                    duration_bucket=kuaishou_filters.get(
                        "kuaishou_duration_bucket", "all"
                    ),
                ),
            )
        elif self.platform == Platform.XIAOHONGSHU and (
            published_after is None or self._xiaohongshu_time_filter_confirmed
        ):
            # Unlimited searches (and searches whose platform time filter was
            # confirmed) already have a reliable final-row boundary. Do not
            # keep scanning the large authenticated raw buffer after the user
            # requested count has been reached.
            collection_kwargs.update(
                qualified_target=limit,
                qualifying_count=lambda rows: len(rows),
            )
        elif self.platform == Platform.BILIBILI and published_after is not None:
            collection_kwargs["prefer_recent"] = True
        if self.platform == Platform.BILIBILI:
            collection_kwargs.update(
                qualified_target=limit,
                qualifying_count=lambda rows: sum(
                    bool(row.get("direct_match")) for row in rows
                ),
            )
        if progress_callback is not None:
            collection_kwargs["progress_callback"] = progress_callback
        raw_rows = self._collect_rows(keyword, target=raw_target, **collection_kwargs)
        parse_started = time.perf_counter()
        candidate_rows = raw_rows
        if (
            self.platform == Platform.XIAOHONGSHU
            and published_after is not None
            and not self._xiaohongshu_time_filter_confirmed
        ):
            candidate_rows = [
                row
                for row in raw_rows
                if row.get("time_confident") is True
                and isinstance(row.get("published_at"), datetime)
                and row["published_at"] >= published_after
            ]
        if self.platform == Platform.KUAISHOU:
            candidate_rows, _ = self._filter_kuaishou_published_rows(
                raw_rows,
                published_after=published_after,
            )
        parsed_items = self._to_items(
            candidate_rows,
            observed_at=observed_at,
            # 原始池可以为了筛选多读一些卡片，但候选模型的排序号最多为
            # 100。这里保留足够的最终候选，不把第 101 条当作可返回项。
            limit=min(len(raw_rows), _MAX_RESULT_LIMIT),
            platform_sort=(
                kuaishou_filters.get("kuaishou_sort") if kuaishou_filters else None
            ),
        )
        parsed_items, duration_filtered_count = self._filter_kuaishou_duration_items(
            parsed_items,
            duration_bucket=kuaishou_filters.get("kuaishou_duration_bucket", "all"),
        )
        timings = dict(self._collection_metrics.get("stage_timings_ms") or {})
        timings["parse_filter_ms"] = max(
            0, round((time.perf_counter() - parse_started) * 1000)
        )
        timings["total_ms"] = max(
            timings.get("total_ms", 0),
            round((time.perf_counter() - search_started) * 1000),
        )
        raw_discovered_count = int(
            self._collection_metrics.get("raw_discovered_count") or len(raw_rows)
        )
        deduped_item_count = int(
            self._collection_metrics.get("deduped_item_count") or len(raw_rows)
        )
        direct_match_count = int(
            self._collection_metrics.get("direct_match_count")
            or sum(
                bool(row.get("direct_match"))
                for row in raw_rows[: int(
                    self._collection_metrics.get("parsed_item_count")
                    or _MAX_RESULT_LIMIT
                )]
            )
        )
        parsed_item_count = int(
            self._collection_metrics.get("parsed_item_count") or len(parsed_items)
        )
        if raw_discovered_count > parsed_item_count:
            self._collection_filter_notes.append(
                f"公开页面发现 {raw_discovered_count} 条；按本次候选上限解析其中 {parsed_item_count} 条。"
            )
        final_filter_label = (
            "发布时间范围和时长筛后"
            if self.platform == Platform.KUAISHOU and published_after is not None
            else "筛后"
        )
        qualifying_count = (
            direct_match_count if self.platform == Platform.BILIBILI else len(parsed_items)
        )
        if qualifying_count >= limit:
            self._set_collection_stop(
                "target_reached",
                "已获得目标数量的符合条件视频。",
            )
        elif self._collection_stop_reason in {"safety_limit", "target_reached"}:
            # 原始卡片数不能替代时间范围和时长筛后的可用数；例如快手
            # 默认排序可能先加载大量较早作品。即使扫描达到安全边界，也
            # 必须如实告诉用户最终保留了多少条。
            self._collection_stop_reason = "safety_limit"
            self._collection_stop_message = (
                f"为避免过度加载，已扫描 {raw_discovered_count} 条页面结果；"
                f"原始解析池保留 {parsed_item_count} 条，最终关键词和字段校验结果见漏斗。"
                if self.platform == Platform.BILIBILI
                else f"为避免过度加载，已扫描 {len(raw_rows)} 条页面结果，{final_filter_label}保留 {len(parsed_items)} 条。"
            )
        elif self._collection_stop_reason == "platform_end":
            self._collection_stop_message = (
                f"平台已经没有更多公开搜索结果；已扫描 {len(raw_rows)} 条页面结果，"
                f"{final_filter_label}保留 {len(parsed_items)} 条。"
            )
        return ProviderSearchPage(
            platform=self.platform,
            provider=self.provider_name,
            items=parsed_items,
            observed_at=observed_at,
            request_id=f"browser-{self.platform.value}-{idempotency_key[:16]}",
            api_call_count=0,
            billable_units=0,
            has_more=(
                self._collection_stop_reason not in {"platform_end", "no_more_loaded"}
                and (
                    len(parsed_items) > limit
                    or self._collection_stop_reason == "safety_limit"
                )
            ),
            raw_item_count=raw_discovered_count,
            parsed_item_count=parsed_item_count,
            raw_discovered_count=raw_discovered_count,
            deduped_item_count=deduped_item_count,
            direct_match_count=direct_match_count,
            duration_filtered_count=duration_filtered_count,
            crawl_stop_reason=self._collection_stop_reason,
            crawl_stop_message=self._collection_stop_message,
            payload_diagnostic=self._collection_diagnostic(requested_kuaishou_filters),
            stage_timings_ms=timings,
            adapter_rule_version=self.adapter_version,
            browser_reused=self._collection_metrics.get("browser_reused"),
            session_recovered=bool(self._collection_metrics.get("session_recovered")),
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

    def _collect_rows(
        self,
        keyword: str,
        *,
        target: int,
        search_filters: dict[str, str] | None = None,
        qualified_target: int | None = None,
        qualifying_count: Callable[[list[dict[str, Any]]], int] | None = None,
        prefer_recent: bool = False,
        published_filter_days: int | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[dict[str, Any]]:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        collection_started = time.perf_counter()
        stage_timings: dict[str, int] = {}
        raw_discovered_count = 0
        first_response_marked = False
        session_recovered = False
        endpoint = f"http://127.0.0.1:{self.debug_port}"
        network_rows: dict[str, dict[str, Any]] = {}
        rendered_rows: dict[str, dict[str, Any]] = {}
        bilibili_page_count: int | None = None
        bilibili_search_response_seen = False

        def notify_progress(current_rows: list[dict[str, Any]]) -> None:
            """Expose bounded funnel progress while the visible page is scanned.

            The final relevance/time validation still happens in
            ``CommercialSearchService``.  The live count is therefore an
            intentionally conservative page-scan snapshot, not a final result
            contract; the completed batch remains the source of truth.
            """
            if progress_callback is None:
                return
            scanned_count = max(raw_discovered_count, len(current_rows))
            parsed_count = len(current_rows)
            if self.platform == Platform.BILIBILI:
                retained_count = sum(
                    bool(row.get("direct_match")) for row in current_rows
                )
                irrelevant_count = max(0, parsed_count - retained_count)
            else:
                retained_count = parsed_count
                irrelevant_count = 0
            try:
                progress_callback(
                    {
                        "platform": self.platform.value,
                        "stage": "scanning",
                        "message": (
                            f"{self.spec.label}已扫描 {scanned_count} 条，"
                            f"解析 {parsed_count} 条，暂保留 {retained_count} 条。"
                        ),
                        "scanned_count": scanned_count,
                        "parsed_count": parsed_count,
                        "deduped_count": len(current_rows),
                        "direct_match_count": retained_count,
                        "retained_count": retained_count,
                        "irrelevant_count": irrelevant_count,
                    }
                )
            except Exception:
                # Progress is observational only and must never break a crawl.
                return

        with sync_playwright() as playwright:
            try:
                attach_started = time.perf_counter()
                browser = playwright.chromium.connect_over_cdp(endpoint)
                stage_timings["browser_attach_or_reuse_ms"] = max(
                    0, round((time.perf_counter() - attach_started) * 1000)
                )
                for context in browser.contexts:
                    context.add_init_script(ANTI_DETECTION_INIT_SCRIPT)
                page, owns_page = self._acquire_collection_page(browser)
            except PlaywrightError as exc:
                raise LicensedProviderError(
                    f"{self.spec.label}浏览器连接失败；请重新打开专用浏览器。",
                    kind=ProviderErrorKind.CONNECTION,
                    retryable=True,
                ) from exc

            def capture_response(response) -> None:
                nonlocal bilibili_page_count, bilibili_search_response_seen
                nonlocal first_response_marked, raw_discovered_count
                if not self._is_search_response_url(response.url):
                    return
                try:
                    payload = response.json()
                except Exception:
                    return
                if self.platform == Platform.BILIBILI:
                    bilibili_search_response_seen = True
                if not first_response_marked:
                    stage_timings["first_response_ms"] = max(
                        0, round((time.perf_counter() - collection_started) * 1000)
                    )
                    first_response_marked = True
                raw_discovered_count += self._payload_row_count(payload)
                rows = self._rows_from_payload(payload, keyword=keyword)
                raw_discovered_count = max(raw_discovered_count, len(rows))
                if self.platform == Platform.XIAOHONGSHU:
                    self._xiaohongshu_search_response_seen = True
                    if rows and all(row.get("is_video") is True for row in rows):
                        self._xiaohongshu_video_response_seen = True
                for row in rows:
                    item_id = str(row.get("item_id") or "")
                    if item_id:
                        network_rows[item_id] = row
                if rows and "first_result_ms" not in stage_timings:
                    stage_timings["first_result_ms"] = max(
                        0, round((time.perf_counter() - collection_started) * 1000)
                    )
                if self.platform == Platform.BILIBILI:
                    page_count = self._bilibili_page_count(payload)
                    if page_count is not None:
                        bilibili_page_count = max(bilibili_page_count or 0, page_count)

            def goto_with_soft_recovery(url: str):
                """Navigate once, rebuilding only the page on a CDP/page fault.

                The same browser context/profile is retained, so this never clears
                cookies or creates a fresh login state. At most one page recovery
                is attempted for this keyword/platform run.
                """
                nonlocal page, owns_page, session_recovered
                navigation_started = time.perf_counter()
                try:
                    response = page.goto(url, wait_until="domcontentloaded")
                except PlaywrightError as exc:
                    if session_recovered:
                        raise LicensedProviderError(
                            f"{self.spec.label}页面连接失败；已停止本次搜索，请重新连接专用浏览器。",
                            kind=ProviderErrorKind.CONNECTION,
                            retryable=True,
                        ) from exc
                    try:
                        page.remove_listener("response", capture_response)
                    except Exception:
                        pass
                    if owns_page:
                        try:
                            page.close()
                        except Exception:
                            pass
                    page, owns_page = self._recover_collection_page(browser)
                    page.on("response", capture_response)
                    session_recovered = True
                    try:
                        response = page.goto(url, wait_until="domcontentloaded")
                    except PlaywrightError as retry_exc:
                        self.login_reset_available = True
                        raise LicensedProviderError(
                            f"{self.spec.label}页面连接失败；已保留登录态但本次停止，请重新连接专用浏览器。",
                            kind=ProviderErrorKind.CONNECTION,
                            retryable=False,
                        ) from retry_exc
                stage_timings["navigation_ms"] = stage_timings.get(
                    "navigation_ms", 0
                ) + max(0, round((time.perf_counter() - navigation_started) * 1000))
                return response

            page.on("response", capture_response)
            try:
                minimize_browser_window(self.debug_port)
                page.set_default_timeout(int(self.timeout_seconds * 1000))
                # Keep the human-entered keyword intact here.  Playwright will
                # encode it once; pre-encoding makes Xiaohongshu encode the
                # percent signs again and search for the wrong literal text.
                if self.platform == Platform.BILIBILI:
                    date_filter_applied = False
                    active_filter_url: str | None = None
                    max_bilibili_pages = _BILIBILI_MAX_SEARCH_PAGES
                    for page_number in range(1, max_bilibili_pages + 1):
                        response = goto_with_soft_recovery(
                            (
                                self._bilibili_page_url(active_filter_url, page_number)
                                if active_filter_url
                                else self._search_url(keyword, page=page_number)
                            ),
                        )
                        minimize_browser_window(self.debug_port)
                        self._raise_for_search_response(response)
                        if page_number == 1 and prefer_recent:
                            # B 站的日期筛选有时只更新页面地址，刷新后的
                            # 接口响应并不会再次返回搜索卡片。保留首屏快照，
                            # 避免“已经扫描到结果”在筛选刷新失败时变成 0 条。
                            initial_network_rows = dict(network_rows)
                            network_rows.clear()
                            rendered_rows.clear()
                            bilibili_search_response_seen = False
                            bilibili_page_count = None
                            filter_notes = self._apply_platform_filters(
                                page,
                                search_filters={
                                    "bilibili_sort": "platform",
                                    "published_days": str(published_filter_days),
                                },
                            )
                            self._collection_filter_notes.extend(filter_notes)
                            date_filter_applied = self._bilibili_date_filter_active(
                                page
                            )
                            if date_filter_applied:
                                active_filter_url = str(page.url or "")
                                response = goto_with_soft_recovery(
                                    active_filter_url,
                                )
                                minimize_browser_window(self.debug_port)
                                self._raise_for_search_response(response)
                                if self._restore_bilibili_initial_rows(
                                    network_rows, initial_network_rows
                                ):
                                    self._collection_filter_notes.append(
                                        "B站时间筛选页未返回新的搜索卡片，已保留首屏结果并继续按发布时间校验。"
                                    )
                            elif self._restore_bilibili_initial_rows(
                                network_rows, initial_network_rows
                            ):
                                self._collection_filter_notes.append(
                                    "B站未确认日期筛选已生效，已保留首屏结果并改用本地发布时间校验。"
                                )
                        # 接口响应可能缺少发布时间；始终读取同页可见卡片，
                        # 按 BV 号合并后用页面日期补齐接口字段。
                        self._wait_for_bilibili_cards_ready(page)
                        self._raise_for_visible_block(page)
                        try:
                            visible_rows = self._rendered_rows(page, keyword=keyword)
                        except Exception:
                            # 页面卡片只是补充发布时间；补充失败不能让已拿到的
                            # 公开搜索接口结果整批丢失。
                            visible_rows = []
                        for row in visible_rows:
                            self._merge_rendered_row(rendered_rows, row)
                        current_count = len(set(network_rows) | set(rendered_rows))
                        if current_count > 0 and "first_result_ms" not in stage_timings:
                            stage_timings["first_result_ms"] = max(
                                0,
                                round((time.perf_counter() - collection_started) * 1000),
                            )
                        current_rows = self._merge_collected_rows(
                            network_rows, rendered_rows
                        )
                        notify_progress(current_rows)
                        if (
                            qualified_target is not None
                            and qualifying_count is not None
                            and qualifying_count(current_rows) >= qualified_target
                        ):
                            self._set_collection_stop(
                                "target_reached",
                                "已获得目标数量的关键词直匹配视频。",
                            )
                            break
                        if (
                            bilibili_page_count is not None
                            and page_number >= bilibili_page_count
                        ):
                            self._set_collection_stop(
                                "platform_end",
                                "已经没有更多符合条件的视频。",
                            )
                            break
                        if current_count >= target:
                            self._set_collection_stop(
                                "safety_limit",
                                "已达到本次扫描安全上限，正在整理符合条件的结果。",
                            )
                            break
                    else:
                        self._set_collection_stop(
                            "safety_limit",
                            "为避免过度翻页，达到安全上限后已停止。",
                        )
                else:
                    response = goto_with_soft_recovery(self._search_url(keyword))
                    minimize_browser_window(self.debug_port)
                    self._raise_for_search_response(response)
                    page.wait_for_timeout(2500)
                    if self.platform == Platform.XIAOHONGSHU:
                        search_confirmed = self._confirm_xiaohongshu_search(page, keyword)
                        self._raise_for_login_gate(page)
                        if not search_confirmed and not self._xiaohongshu_search_response_seen:
                            self._collection_filter_notes.append(
                                "未能确认搜索框状态，已继续读取页面中可核验的视频候选。"
                            )
                    effective_filters = dict(search_filters or {})
                    if (
                        self.platform == Platform.XIAOHONGSHU
                        and published_filter_days is not None
                    ):
                        effective_filters["published_days"] = str(published_filter_days)
                    if self.platform == Platform.XIAOHONGSHU:
                        # Discard the unfiltered response emitted by the initial
                        # navigation. The video-tab response is likewise cleared
                        # immediately before selecting the requested time window.
                        network_rows.clear()
                    self._collection_filter_notes.extend(
                        self._apply_platform_filters(
                            page,
                            search_filters=effective_filters or None,
                            before_time_filter=(
                                network_rows.clear
                                if self.platform == Platform.XIAOHONGSHU
                                else None
                            ),
                        )
                    )
                    if self._collection_rule_failure:
                        max_scroll_rounds = 0
                    else:
                        page.wait_for_timeout(1500)
                        self._raise_for_visible_block(page)
                    stagnant_rounds = 0
                    previous_count = -1
                    if not self._collection_rule_failure:
                        max_scroll_rounds = (
                            _XIAOHONGSHU_PUBLIC_MAX_SCROLL_ROUNDS
                            if self.anonymous_only
                            else (
                                _KUAISHOU_MAX_SCROLL_ROUNDS
                                if self.platform == Platform.KUAISHOU
                                else _MAX_SCROLL_ROUNDS
                            )
                        )
                    for _ in range(max_scroll_rounds):
                        for row in self._rendered_rows(page):
                            self._merge_rendered_row(rendered_rows, row)
                        current_count = len(set(network_rows) | set(rendered_rows))
                        if current_count > 0 and "first_result_ms" not in stage_timings:
                            stage_timings["first_result_ms"] = max(
                                0,
                                round((time.perf_counter() - collection_started) * 1000),
                            )
                        current_rows = self._merge_collected_rows(
                            network_rows, rendered_rows
                        )
                        notify_progress(current_rows)
                        if (
                            qualified_target is not None
                            and qualifying_count is not None
                            and qualifying_count(current_rows) >= qualified_target
                        ):
                            self._set_collection_stop(
                                "target_reached",
                                "已获得目标数量的符合条件视频。",
                            )
                            break
                        if current_count >= target:
                            self._set_collection_stop(
                                "safety_limit",
                                "已达到本次扫描安全上限，正在整理符合条件的结果。",
                            )
                            break
                        if self._visible_platform_end(page):
                            self._set_collection_stop(
                                "platform_end",
                                "已经没有更多符合条件的视频。",
                            )
                            break
                        stagnant_rounds = (
                            stagnant_rounds + 1
                            if current_count == previous_count
                            else 0
                        )
                        if (
                            self.platform != Platform.KUAISHOU
                            and stagnant_rounds >= _MAX_STAGNANT_SCROLL_ROUNDS
                        ):
                            self._set_collection_stop(
                                "no_more_loaded",
                                "连续多次未发现新视频，已停止。",
                            )
                            break
                        previous_count = current_count
                        scroll_started = time.perf_counter()
                        self._scroll_one_viewport(page)
                        # 平台结果懒加载:滚动后需要等待刷新才有新内容。
                        # 轮询读取并合并新卡片,出现新行即提前进入下一轮。
                        self._wait_for_new_rows_after_scroll(
                            page,
                            current_count=current_count,
                            rendered_rows=rendered_rows,
                            network_rows=network_rows,
                        )
                        stage_timings["scroll_loading_ms"] = stage_timings.get(
                            "scroll_loading_ms", 0
                        ) + max(0, round((time.perf_counter() - scroll_started) * 1000))
                        self._raise_for_visible_block(page)
                    else:
                        # The last ordinary scroll may have populated the
                        # visible feed after the final loop body ran. Read it
                        # once before declaring the bounded scan complete.
                        for row in self._rendered_rows(page):
                            self._merge_rendered_row(rendered_rows, row)
                        self._set_collection_stop(
                            "safety_limit",
                            "为避免过度滚动，达到安全上限后已停止。",
                        )
                if (
                    not self._collection_rule_failure
                    and not network_rows
                    and not rendered_rows
                    and not (
                        self.platform == Platform.BILIBILI
                        and bilibili_search_response_seen
                    )
                ):
                    self._raise_for_login_gate(page)
            finally:
                page.remove_listener("response", capture_response)
                if owns_page:
                    page.close()
                minimize_browser_window(self.debug_port)

        rows = self._merge_collected_rows(network_rows, rendered_rows)
        self._collection_metrics = {
            "raw_discovered_count": max(raw_discovered_count, len(rows)),
            "deduped_item_count": len(rows),
            "parsed_item_count": min(
                len(rows),
                _XIAOHONGSHU_PUBLIC_RESULT_LIMIT
                if self.anonymous_only
                else _MAX_RESULT_LIMIT,
            ),
            "direct_match_count": min(
                sum(bool(row.get("direct_match")) for row in rows),
                (
                    _XIAOHONGSHU_PUBLIC_RESULT_LIMIT
                    if self.anonymous_only
                    else _MAX_RESULT_LIMIT
                ),
            ),
            "browser_reused": not owns_page,
            "session_recovered": session_recovered,
            "stage_timings_ms": {
                **stage_timings,
                "total_ms": max(
                    0, round((time.perf_counter() - collection_started) * 1000)
                ),
            },
        }
        return rows

    @staticmethod
    def _merge_rendered_row(
        rendered_rows: dict[str, dict[str, Any]], row: dict[str, Any]
    ) -> None:
        """字段级合并渲染行:新行的非 None 字段覆盖旧值,None 保留旧值。

        多页/多轮提取时同一视频可能重复出现;后提取的行若互动数字
        尚未渲染(为空),不应清掉先前已提取的指标。
        """
        item_id = str(row.get("item_id") or "")
        if not item_id:
            return
        existing = rendered_rows.get(item_id)
        if existing is None:
            rendered_rows[item_id] = row
            return
        combined = dict(existing)
        for key, value in row.items():
            if key == "source_url" and (
                LocalPlatformBrowserSearchProvider._xiaohongshu_source_url_rank(
                    value
                )
                < LocalPlatformBrowserSearchProvider._xiaohongshu_source_url_rank(
                    combined.get(key)
                )
            ):
                continue
            if value is not None:
                combined[key] = value
        rendered_rows[item_id] = combined

    @staticmethod
    def _merge_collected_rows(
        network_rows: dict[str, dict[str, Any]],
        rendered_rows: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged = dict(rendered_rows)
        for item_id, row in network_rows.items():
            base = merged.get(item_id, {})
            combined = dict(base)
            for key, value in row.items():
                if key == "source_url" and (
                    LocalPlatformBrowserSearchProvider._xiaohongshu_source_url_rank(
                        value
                    )
                    < LocalPlatformBrowserSearchProvider._xiaohongshu_source_url_rank(
                        base.get(key)
                    )
                ):
                    continue
                if key == "source_url" and row.get("source_url_verified") is True:
                    # 网络响应可能没有临时 xsec_token，但 DOM 仍提供了可交给
                    # 已登录浏览器解析的直接 /explore/ 链接。只有新值存在，或
                    # 旧值本身不是受支持的小红书笔记链接时，才覆盖它。
                    if value is not None or not LocalPlatformBrowserSearchProvider._is_actionable_xiaohongshu_source_url(base.get(key)):
                        combined[key] = value
                    continue
                if (
                    key == "source_url"
                    and row.get("source_url_verified") is False
                    and LocalPlatformBrowserSearchProvider._is_actionable_xiaohongshu_source_url(
                        base.get(key)
                    )
                ):
                    # 网络层只有 note id 时的 canonical URL 不能覆盖 DOM/分享
                    # 层已经拿到的真实小红书入口。
                    continue
                if (
                    key == "time_confident"
                    and value is False
                    and base.get("time_confident") is True
                ):
                    continue
                if value is not None:
                    combined[key] = value
            merged[item_id] = combined
        return list(merged.values())

    @staticmethod
    def _restore_bilibili_initial_rows(
        network_rows: dict[str, dict[str, Any]],
        initial_network_rows: dict[str, dict[str, Any]],
    ) -> bool:
        """Keep the first response when a date-filter reload returns no cards."""
        if network_rows or not initial_network_rows:
            return False
        network_rows.update(initial_network_rows)
        return True

    def _search_url(
        self,
        keyword: str,
        *,
        page: int | None = None,
    ) -> str:
        url = self.spec.search_url.format(keyword=keyword.strip())
        if self.platform == Platform.BILIBILI:
            return f"{url}&page={page or 1}"
        return url

    @staticmethod
    def _bilibili_page_url(active_url: str, page_number: int) -> str:
        """Keep the sort/date query selected in the page while changing pages."""
        parsed = urlparse(active_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["page"] = str(page_number)
        return urlunparse(parsed._replace(query=urlencode(query)))

    @staticmethod
    def _platform_filter_days(
        published_after: datetime | None,
        observed_at: datetime,
    ) -> int | None:
        if published_after is None:
            return 0
        try:
            days = (observed_at - published_after).total_seconds() / 86400
        except (TypeError, ValueError):
            return None
        for supported in (1, 7, 180):
            if abs(days - supported) <= (15 / 1440):
                return supported
        return None

    def _raise_for_search_response(self, response) -> None:
        if response is not None and response.status in {403, 412, 429}:
            raise LicensedProviderError(
                f"{self.spec.label}返回 {response.status}，已停止搜索。",
                kind=ProviderErrorKind.RATE_LIMIT,
                retryable=False,
            )

    @staticmethod
    def _bilibili_page_count(payload: Any) -> int | None:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None
        try:
            page_count = int(data.get("numPages") or 0)
        except (TypeError, ValueError):
            return None
        return page_count if page_count > 0 else None

    def _payload_row_count(self, payload: Any) -> int:
        """Count raw public records before ID de-duplication or relevance filtering."""
        if not isinstance(payload, dict):
            return 0
        if self.platform == Platform.BILIBILI:
            data = payload.get("data")
            items = data.get("result") if isinstance(data, dict) else None
        elif self.platform == Platform.XIAOHONGSHU:
            data = payload.get("data")
            items = (
                data.get("items") or data.get("notes") or data.get("results")
                if isinstance(data, dict)
                else data
                if isinstance(data, list)
                else payload.get("items") or payload.get("notes")
            )
        else:
            items = payload.get("data")
        return len(items) if isinstance(items, list) else 0

    def _set_collection_stop(self, reason: str, message: str) -> None:
        self._collection_stop_reason = reason
        self._collection_stop_message = message

    def _scroll_for_more_results(self, page) -> None:
        """Scroll the platform's real results container, not only the window.

        Kuaishou's current search shell fixes ``html``/``body`` at viewport
        height and keeps the result feed in ``.wb-content``.  Scrolling the
        window therefore repeatedly reads the first twenty cards.  This still
        performs an ordinary visible scroll, but targets the actual container.
        """
        if self.platform == Platform.KUAISHOU:
            page.evaluate(
                """() => {
                  const container = document.querySelector('.wb-content');
                  const amount = Math.max(window.innerHeight * 0.85, 600);
                  if (container && container.scrollHeight > container.clientHeight) {
                    container.scrollBy(0, amount);
                    return;
                  }
                  window.scrollBy(0, amount);
                }"""
            )
            return
        page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.85, 600))")

    def _scroll_one_viewport(self, page) -> None:
        """Scroll down by one viewport height."""
        self._scroll_for_more_results(page)

    def _wait_for_bilibili_cards_ready(self, page, max_wait_ms: int = 2_500) -> None:
        """等待 B 站搜索结果卡片 stats 渲染完成。

        B 站搜索页是服务端渲染,互动数字(stats 行)在页面加载后延迟
        渲染;固定等待 1.8 秒时部分卡片的指标仍是空的。轮询直到
        stats 项数量达到视频卡片数量的一半(允许个别卡片缺失),
        最多等 max_wait_ms。
        """
        waited = 0
        while waited < max_wait_ms:
            try:
                ready = bool(
                    page.evaluate(
                        """() => {
                          const cardLinks = document.querySelectorAll(
                            'a[href*="/video/"]'
                          ).length;
                          const statsItems = document.querySelectorAll(
                            '.bili-video-card__stats--item'
                          ).length;
                          return cardLinks > 0
                            && statsItems >= Math.max(1, cardLinks / 2);
                        }"""
                    )
                )
            except Exception:
                ready = False
            if ready:
                return
            try:
                card_count = int(
                    page.evaluate(
                        "() => document.querySelectorAll(\"a[href*='/video/BV']\").length"
                    )
                    or 0
                )
            except Exception:
                card_count = 0
            # 标题和链接出现后即可解析，互动数字缺失时由候选质量说明标注；
            # 不为每张卡片等待二次渲染，避免页面改版/弱网把每页拖到 8 秒。
            if card_count > 0:
                return
            page.wait_for_timeout(250)
            waited += 250

    def _wait_for_new_rows_after_scroll(
        self,
        page,
        *,
        current_count: int,
        rendered_rows: dict[str, dict[str, Any]],
        network_rows: dict[str, dict[str, Any]],
        max_wait_ms: int = 3_000,
    ) -> None:
        """滚动后轮询读取新卡片,直到出现新行或超时。

        平台结果流是懒加载:滚动到一定位置后需要等待刷新才会补充新卡片,
        固定等待几秒容易误判“没有更多内容”。这里每 500ms 读取一次页面
        并把新卡片合并进集合,出现新行即提前返回;超过 max_wait_ms 才
        认为本轮没有新内容(由上层停滞判断决定是否停止)。
        """
        waited = 0
        while waited < max_wait_ms:
            page.wait_for_timeout(250)
            waited += 250
            for row in self._rendered_rows(page):
                self._merge_rendered_row(rendered_rows, row)
            if len(set(network_rows) | set(rendered_rows)) > current_count:
                return

    @staticmethod
    def _visible_platform_end(page) -> bool:
        try:
            body_text = page.locator("body").inner_text(timeout=1200)
        except Exception:
            return False
        return any(marker in body_text for marker in _PUBLIC_SEARCH_END_MARKERS)

    def _acquire_collection_page(self, browser) -> tuple[Any, bool]:
        """Prefer an existing page for this platform; own only a fallback page."""
        for context in browser.contexts:
            for page in context.pages:
                if self.spec.page_host in str(page.url or "").casefold():
                    return page, False
        if not browser.contexts:
            raise LicensedProviderError(
                f"{self.spec.label}浏览器没有可用会话；请重新打开专用浏览器。",
                kind=ProviderErrorKind.CONNECTION,
                retryable=False,
            )
        return browser.contexts[0].new_page(), True

    def _recover_collection_page(self, browser) -> tuple[Any, bool]:
        """Rebuild one broken page inside the existing authorized context."""
        for context in browser.contexts:
            return context.new_page(), True
        raise LicensedProviderError(
            f"{self.spec.label}浏览器会话已失效；请重新连接专用浏览器。",
            kind=ProviderErrorKind.CONNECTION,
            retryable=True,
        )

    def _normalize_kuaishou_filters(
        self, search_filters: dict[str, str] | None
    ) -> dict[str, str]:
        if self.platform != Platform.KUAISHOU or not search_filters:
            return {}
        if not isinstance(search_filters, dict):
            raise LicensedProviderError(
                "快手筛选参数格式不正确。",
                kind=ProviderErrorKind.VALIDATION,
            )
        sort_aliases = {
            "platform": "platform",
            "default": "platform",
            "newest": "newest",
            "latest": "newest",
            "likes": "likes",
            "most_liked": "likes",
        }
        duration_aliases = {
            "all": "all",
            "under_60": "under_60",
            "<60": "under_60",
            "between_60_300": "between_60_300",
            "60-300": "between_60_300",
            "over_300": "over_300",
            ">300": "over_300",
        }
        raw_sort = str(search_filters.get("kuaishou_sort") or "platform").strip()
        raw_duration = str(
            search_filters.get("kuaishou_duration_bucket") or "all"
        ).strip()
        sort = sort_aliases.get(raw_sort)
        duration_bucket = duration_aliases.get(raw_duration)
        if sort is None or duration_bucket is None:
            raise LicensedProviderError(
                "快手筛选只支持平台综合、最新发布、最多点赞，以及不限、1分钟以下、1到5分钟、5分钟以上。",
                kind=ProviderErrorKind.VALIDATION,
            )
        return {
            "kuaishou_sort": sort,
            "kuaishou_duration_bucket": duration_bucket,
        }

    def _collection_diagnostic(self, kuaishou_filters: dict[str, str]) -> str:
        if self.platform == Platform.BILIBILI:
            diagnostic = (
                "使用B站专用浏览器正常搜索；优先读取浏览器收到的搜索元数据，"
                "先记录全部公开搜索卡片，再保留标题、话题或描述直接命中，"
                "以及明确命中行业/对象维度的相关公开视频。"
            )
        elif self.platform == Platform.KUAISHOU:
            diagnostic = "使用快手专用浏览器正常搜索；优先读取浏览器收到的搜索元数据，保留平台当前筛选后的公开视频。"
        elif self.platform == Platform.XIAOHONGSHU:
            if self._collection_rule_failure:
                return self._collection_rule_failure
            diagnostic = (
                "使用小红书专用浏览器正常搜索；先选择页面“视频”筛选，"
                "并只保留带视频标记的公开作品。"
            )
        else:
            diagnostic = (
                f"使用{self.spec.label}专用浏览器正常搜索；优先读取浏览器收到的搜索元数据，"
                "发现数量是页面搜索结果；系统只去重和校验链接，不会因互动、时长或标题未直接命中而丢弃候选。"
            )
        if kuaishou_filters:
            labels = {
                "platform": "平台综合",
                "newest": "最新发布",
                "likes": "最多点赞",
                "all": "不限时长",
                "under_60": "1分钟以下",
                "between_60_300": "1-5分钟",
                "over_300": "5分钟以上",
            }
            diagnostic = (
                f"{diagnostic}；请求筛选：{labels[kuaishou_filters['kuaishou_sort']]}、"
                f"{labels[kuaishou_filters['kuaishou_duration_bucket']]}。"
            )
        if self._collection_filter_notes:
            diagnostic = f"{diagnostic}；{'；'.join(self._collection_filter_notes)}"
        return diagnostic

    def _apply_platform_filters(
        self,
        page,
        *,
        search_filters: dict[str, str] | None = None,
        before_time_filter: Callable[[], None] | None = None,
    ) -> list[str]:
        """Use visible platform controls before reading search metadata."""
        if self.platform == Platform.XIAOHONGSHU:
            video_selected = self._select_xiaohongshu_video_filter(page)
            self._xiaohongshu_video_filter_confirmed = video_selected
            if not video_selected:
                return [
                    "小红书未能确认“视频”筛选，已仅保留页面或搜索响应明确标记为视频的候选。"
                ]
            notes = ["已选择小红书“视频”筛选"]
            if search_filters and "published_days" in search_filters:
                if before_time_filter is not None:
                    before_time_filter()
                try:
                    published_days = int(search_filters["published_days"])
                except (TypeError, ValueError):
                    published_days = -1
                time_label = self._select_xiaohongshu_time_filter(page, published_days)
                self._xiaohongshu_time_filter_confirmed = bool(time_label)
                notes.append(
                    f"已选择小红书发布时间“{time_label}”"
                    if time_label
                    else "平台时间筛选未生效，正在本地过滤"
                )
            return notes
        if self.platform == Platform.BILIBILI:
            if not search_filters:
                return []
            sort = search_filters.get("bilibili_sort", "platform")
            notes = ["使用B站综合排序"]
            if sort == "newest":
                selected = self._select_bilibili_newest_filter(page)
                notes = [
                    "已选择B站“最新发布”"
                    if selected
                    else "B站页面未确认“最新发布”，已保留当前排序"
                ]
                if selected:
                    page.wait_for_timeout(800)
            if "published_days" in search_filters:
                try:
                    published_days = int(search_filters["published_days"])
                except (TypeError, ValueError):
                    published_days = -1
                label = {
                    0: "全部日期",
                    1: "最近一天",
                    7: "最近一周",
                    180: "最近半年",
                }.get(published_days)
                date_selected = bool(
                    label and self._select_bilibili_date_filter(page, label)
                )
                notes.append(
                    f"已选择B站发布时间“{label}”"
                    if date_selected
                    else "B站页面未确认发布时间筛选；仍会按可核验时间在本地过滤"
                )
            return notes
        if self.platform != Platform.KUAISHOU or not search_filters:
            return []
        controls = (
            (
                "排序",
                search_filters["kuaishou_sort"],
                {
                    "platform": ("平台综合", "综合", "默认排序"),
                    "newest": ("最新发布", "最新"),
                    "likes": ("最多点赞", "点赞最多"),
                },
            ),
            (
                "时长",
                search_filters["kuaishou_duration_bucket"],
                {
                    "all": ("不限", "全部"),
                    "under_60": ("1分钟以下", "1分钟以内", "60秒以下"),
                    "between_60_300": (
                        "1-5分钟",
                        "1～5分钟",
                        "1到5分钟",
                        "60秒-5分钟",
                    ),
                    "over_300": ("5分钟以上", "超过5分钟", "5分钟及以上"),
                },
            ),
        )
        notes: list[str] = []
        for category, value, label_map in controls:
            labels = label_map[value]
            if self._click_visible_text(page, labels):
                notes.append(f"已选择快手{category}“{labels[0]}”")
            else:
                notes.append(f"快手页面未显示{category}“{labels[0]}”，保留当前平台结果")
        return notes

    @staticmethod
    def _xiaohongshu_control_selected(locator, expected_text: str) -> bool:
        try:
            text = LocalPlatformBrowserSearchProvider._clean_text(locator.inner_text())
            if expected_text not in text:
                return False
            nodes = [locator]
            try:
                nodes.append(locator.locator(".."))
            except Exception:
                pass
            for node in nodes:
                class_names = str(node.get_attribute("class") or "").casefold()
                if any(token in class_names.split() for token in ("active", "selected")):
                    return True
                for attribute in (
                    "aria-selected",
                    "aria-checked",
                    "data-selected",
                    "data-active",
                ):
                    if str(node.get_attribute(attribute) or "").casefold() == "true":
                        return True
                if str(node.get_attribute("aria-current") or "").casefold() in {
                    "true",
                    "page",
                    "step",
                }:
                    return True
            return False
        except Exception:
            return False

    def _confirm_xiaohongshu_search(self, page, keyword: str) -> bool:
        """Confirm the visible search input before applying platform filters."""
        try:
            inputs = page.locator("#search-input, input.search-input")
            if inputs.count() < 1:
                return False
            input_box = inputs.nth(0)
            value = str(input_box.input_value() or "").strip()
            if value != keyword.strip():
                input_box.fill(keyword)
                input_box.press("Enter")
                page.wait_for_timeout(800)
                value = str(input_box.input_value() or "").strip()
            query_keyword = parse_qs(urlparse(str(page.url or "")).query).get(
                "keyword", [""]
            )[0]
            return value == keyword.strip() and (
                query_keyword == keyword.strip()
                or self._xiaohongshu_search_response_seen
            )
        except Exception:
            return False

    def _select_xiaohongshu_video_filter(self, page) -> bool:
        try:
            candidates = page.locator(
                "#video.channel, [data-type='video'], [data-note-type='video']"
            )
            before_url = str(page.url or "")
            for index in range(min(candidates.count(), 8)):
                locator = candidates.nth(index)
                if not locator.is_visible():
                    continue
                if self._xiaohongshu_control_selected(locator, "视频"):
                    return True
                try:
                    locator.click(timeout=3000)
                except Exception:
                    try:
                        locator.evaluate("node => node.click()")
                    except Exception:
                        continue
                for _ in range(8):
                    current_url = str(page.url or "")
                    query = parse_qs(urlparse(current_url).query)
                    if self._xiaohongshu_control_selected(locator, "视频"):
                        return True
                    if self._xiaohongshu_video_response_seen:
                        return True
                    if current_url != before_url and any(
                        value.casefold() in {"video", "51"}
                        for key in ("type", "note_type", "noteType")
                        for value in query.get(key, [])
                    ):
                        return True
                    page.wait_for_timeout(250)
                return False
            return False
        except Exception:
            return False

    def _select_xiaohongshu_time_filter(self, page, days: int) -> str | None:
        label = {0: "不限", 1: "一天内", 7: "一周内", 180: "半年内"}.get(days)
        if label is None:
            return None
        try:
            trigger = page.locator("div.filter")
            for index in range(min(trigger.count(), 5)):
                candidate = trigger.nth(index)
                if candidate.is_visible():
                    try:
                        candidate.evaluate("node => node.click()")
                    except Exception:
                        candidate.click(timeout=3000)
                    break
            page.wait_for_timeout(400)
            groups = page.locator(".filters")
            for _ in range(8):
                if groups.count() > 0:
                    break
                page.wait_for_timeout(250)
            for group_index in range(min(groups.count(), 12)):
                group = groups.nth(group_index)
                if not group.is_visible() or "发布时间" not in group.inner_text():
                    continue
                options = group.locator(
                    "[data-hp-bound='1'], .tags:not([aria-hidden='true']), "
                    "[role='option'], [data-value]"
                )
                for option_index in range(min(options.count(), 12)):
                    option = options.nth(option_index)
                    if not option.is_visible() or self._clean_text(option.inner_text()) != label:
                        continue
                    if not self._xiaohongshu_control_selected(option, label):
                        try:
                            option.click(timeout=3000)
                        except Exception:
                            option.evaluate("node => node.click()")
                    for _ in range(8):
                        if self._xiaohongshu_control_selected(option, label):
                            return label
                        page.wait_for_timeout(250)
                    return None
            return None
        except Exception:
            return None

    @staticmethod
    def _select_bilibili_date_filter(page, label: str) -> bool:
        try:
            date_visible = False
            for attempt in range(8):
                candidates = page.get_by_text(label, exact=True)
                date_visible = any(
                    candidates.nth(index).is_visible()
                    for index in range(candidates.count())
                )
                if date_visible:
                    break
                more_filters = page.get_by_text("更多筛选", exact=True)
                for index in range(more_filters.count()):
                    trigger = more_filters.nth(index)
                    if not trigger.is_visible():
                        continue
                    trigger.evaluate("node => node.click()")
                    page.wait_for_timeout(500)
                    break
                if attempt < 7:
                    page.wait_for_timeout(500)
            if not date_visible:
                return False
            candidates = page.get_by_text(label, exact=True)
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                if not candidate.is_visible():
                    continue
                try:
                    candidate.click(timeout=3000)
                except Exception:
                    try:
                        candidate.evaluate("node => node.click()")
                    except Exception:
                        pass
                for _ in range(8):
                    query = parse_qs(urlparse(str(page.url or "")).query)
                    if LocalPlatformBrowserSearchProvider._visible_text_selected(
                        page, label
                    ) or (
                        label != "全部日期"
                        and "pubtime_begin_s" in query
                        and "pubtime_end_s" in query
                    ):
                        return True
                    page.wait_for_timeout(250)
            return False
        except Exception:
            return False

    @staticmethod
    def _select_bilibili_newest_filter(page) -> bool:
        if LocalPlatformBrowserSearchProvider._bilibili_recent_filter_active(page):
            return True
        try:
            for attempt in range(8):
                candidates = page.get_by_text("最新发布", exact=True)
                for index in range(candidates.count()):
                    candidate = candidates.nth(index)
                    if not candidate.is_visible():
                        continue
                    try:
                        candidate.click(timeout=3000)
                    except Exception:
                        try:
                            candidate.evaluate("node => node.click()")
                        except Exception:
                            pass
                    for _ in range(8):
                        if LocalPlatformBrowserSearchProvider._bilibili_recent_filter_active(
                            page
                        ):
                            return True
                        page.wait_for_timeout(250)
                    return False
                if attempt < 7:
                    page.wait_for_timeout(500)
            return False
        except Exception:
            return False

    @staticmethod
    def _visible_text_selected(page, label: str) -> bool:
        try:
            candidates = page.get_by_text(label, exact=True)
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                if not candidate.is_visible():
                    continue
                class_names = " ".join(
                    (
                        str(candidate.get_attribute("class") or ""),
                        str(candidate.locator("..").get_attribute("class") or ""),
                    )
                ).casefold()
                if "active" in class_names or "selected" in class_names:
                    return True
            return False
        except Exception:
            return False

    @staticmethod
    def _bilibili_recent_filter_active(page) -> bool:
        try:
            if parse_qs(urlparse(str(page.url or "")).query).get("order") == [
                "pubdate"
            ]:
                return True
            return LocalPlatformBrowserSearchProvider._visible_text_selected(
                page, "最新发布"
            )
        except Exception:
            return False

    @staticmethod
    def _bilibili_date_filter_active(page) -> bool:
        try:
            query = parse_qs(urlparse(str(page.url or "")).query)
            return "pubtime_begin_s" in query and "pubtime_end_s" in query
        except Exception:
            return False

    @staticmethod
    def _click_visible_text(page, labels: tuple[str, ...]) -> bool:
        for label in labels:
            try:
                locator = page.get_by_text(label, exact=True)
                for index in range(min(locator.count(), 3)):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=1000):
                        candidate.click(timeout=1500)
                        page.wait_for_timeout(400)
                        return True
            except Exception:
                continue
        return False

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
        if self._page_requires_login(body_text):
            raise LicensedProviderError(
                f"{self.spec.label}没有返回公开搜索结果，平台要求登录或人工验证。",
                kind=ProviderErrorKind.AUTHORIZATION,
                retryable=False,
            )

    def _rendered_rows(
        self, page, *, keyword: str | None = None
    ) -> list[dict[str, Any]]:
        if self.platform == Platform.BILIBILI:
            raw = page.locator(self._bilibili_result_link_selector()).evaluate_all(
                """elements => elements.map(element => {
                    const container = element.closest(
                        '[class*="bili-video-card"], [class*="video-item"], li, article'
                    ) || element;
                    const image = element.querySelector("img") || container.querySelector("img");
                    const statsItems = [...(container.querySelectorAll(
                        '.bili-video-card__stats--item'
                    ) || [])].map(n => (n.innerText || '').trim());
                    return {
                        href: element.href || element.getAttribute("href") || "",
                        title: element.getAttribute("title") || image?.alt || "",
                        text: (container.innerText || element.innerText || "").trim(),
                        stats: statsItems,
                    };
                })"""
            )
        else:
            selector = self.spec.link_selector
            try:
                primary_count = page.locator(selector).count()
            except Exception:
                primary_count = 0
            if primary_count < 1 and self.spec.fallback_link_selectors:
                selector = ", ".join(
                    dict.fromkeys((selector, *self.spec.fallback_link_selectors))
                )
                self._collection_filter_notes.append(
                    f"{self.spec.label}已启用备用页面规则；规则版本 {self.adapter_version}。"
                )
            raw = page.locator(selector).evaluate_all(
                """elements => elements.map(element => {
                    let container = element;
                    for (let i = 0; i < 5 && container?.parentElement; i += 1) {
                        const candidate = container.parentElement;
                        const text = (candidate.innerText || "").trim();
                        container = candidate;
                        if (text.length >= 8 && text.length <= 600) break;
                    }
                    const image = element.querySelector("img") || container?.querySelector("img");
                    const countNodes = [...(container?.querySelectorAll(
                        'span.count, [class*="count"]'
                    ) || [])].map(n => (n.innerText || '').trim());
                    return {
                        href: element.href || element.getAttribute("href") || "",
                        title: element.getAttribute("title") || image?.alt || "",
                        text: (container?.innerText || element.innerText || "").trim(),
                        counts: countNodes,
                        isVideo: Boolean(
                          container?.querySelector(
                            'video, .play-icon, [class*="play-icon"], [class*="video-icon"], [data-type*="video"]'
                          )
                          || [...(container?.querySelectorAll('[data-type], [data-note-type], [aria-label], [title]') || [])]
                            .some(node => /video|视频/i.test(
                              [node.getAttribute('data-type'), node.getAttribute('data-note-type'),
                               node.getAttribute('aria-label'), node.getAttribute('title')].join(' ')
                            ))
                        ),
                        xsecToken: (() => {
                          const nodes = [element, container, ...(container?.querySelectorAll(
                            '[data-xsec-token], [data-xsec_token], [data-xsec-source], [data-xsec_source]'
                          ) || [])];
                          for (const node of nodes) {
                            const value = node?.getAttribute?.('data-xsec-token')
                              || node?.getAttribute?.('data-xsec_token')
                              || node?.getAttribute?.('xsec-token')
                              || node?.getAttribute?.('xsec_token');
                            if (value) return value;
                          }
                          return '';
                        })(),
                        xsecSource: (() => {
                          const nodes = [element, container, ...(container?.querySelectorAll(
                            '[data-xsec-source], [data-xsec_source]'
                          ) || [])];
                          for (const node of nodes) {
                            const value = node?.getAttribute?.('data-xsec-source')
                              || node?.getAttribute?.('data-xsec_source')
                              || node?.getAttribute?.('xsec-source')
                              || node?.getAttribute?.('xsec_source');
                            if (value) return value;
                          }
                          return '';
                        })(),
                    };
                })"""
            )
            if not raw:
                self._collection_filter_notes.append(
                    f"{self.spec.label}没有匹配到已知页面规则，规则可能失效；本次未将空结果当作成功。"
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
            if (
                self.platform == Platform.XIAOHONGSHU
                and item.get("isVideo") is not True
            ):
                continue
            source_url = href
            if self.platform == Platform.XIAOHONGSHU:
                if href.startswith("/"):
                    href = f"https://www.xiaohongshu.com{href}"
                source_url = self._xiaohongshu_source_url(
                    {
                        "href": href,
                        "xsec_token": item.get("xsecToken"),
                        "xsec_source": item.get("xsecSource"),
                    },
                    {},
                    match.group(1),
                )
            published_at = self._parse_published_at(text, self.clock())
            title = self._clean_title(item.get("title"), text)
            direct_match = bool(
                self.platform != Platform.BILIBILI
                or not keyword
                or self._bilibili_text_matches(keyword, title, text)
            )
            # B 站搜索结果是服务端渲染(自动化下不发搜索接口请求)。第一个 stats
            # 数字可作为播放数；第二个数字在不同卡片布局下语义不稳定，不能猜成点赞。
            # 小红书卡片通过 span.count 暴露点赞数(无播放数)。
            raw_stats = item.get("stats")
            raw_counts = item.get("counts")
            if isinstance(raw_stats, list):
                plays_from_stats = (
                    LocalPlatformBrowserSearchProvider._parse_count_text(raw_stats[0])
                    if raw_stats
                    else None
                )
                likes_from_stats = None
                counts_likes = None
            elif isinstance(raw_counts, list):
                plays_from_stats = None
                likes_from_stats = None
                counts_likes = (
                    LocalPlatformBrowserSearchProvider._parse_count_text(raw_counts[0])
                    if raw_counts
                    else None
                )
            else:
                plays_from_stats = None
                likes_from_stats = None
                counts_likes = None
            rows.append(
                {
                    "item_id": match.group(1),
                    "source_url": source_url,
                    "title": title,
                    "author_name": self._extract_author(text),
                    "plays": plays_from_stats,
                    "likes": (
                        likes_from_stats
                        if likes_from_stats is not None
                        else counts_likes
                        if counts_likes is not None
                        else self._labeled_count(text, ("赞", "点赞"))
                    ),
                    "comments": self._labeled_count(text, ("评论",)),
                    "published_at": published_at,
                    "time_confident": published_at is not None,
                    "direct_match": direct_match,
                    "is_video": (
                        True if self.platform == Platform.XIAOHONGSHU else None
                    ),
                    "strict_topic": bool(
                        self.platform == Platform.BILIBILI
                        and direct_match
                        and not self._bilibili_text_matches(keyword, title)
                    ),
                    "match_text": f"{title} {text}".strip(),
                    "evidence": "rendered_search_card",
                }
            )
        return rows

    @staticmethod
    def _bilibili_result_link_selector() -> str:
        return (
            "#all-list .bili-video-card a[href*='/video/BV'], "
            "#all-list .video-item a[href*='/video/BV'], "
            ".search-all-list .bili-video-card a[href*='/video/BV'], "
            ".search-all-list .video-item a[href*='/video/BV'], "
            ".search-content .bili-video-card a[href*='/video/BV'], "
            ".search-content .video-item a[href*='/video/BV']"
        )

    def _rows_from_payload(
        self, payload: Any, *, keyword: str | None = None
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        if self.platform == Platform.XIAOHONGSHU:
            return self._xiaohongshu_rows(payload)
        if self.platform == Platform.KUAISHOU:
            return self._kuaishou_rows(payload)
        return self._bilibili_rows(payload, keyword=keyword)

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
    def _xiaohongshu_is_video_card(
        entry: dict[str, Any], card: dict[str, Any]
    ) -> bool:
        explicit_types = [
            card.get("type"),
            card.get("note_type"),
            card.get("noteType"),
            entry.get("type"),
            entry.get("note_type"),
            entry.get("noteType"),
            card.get("media_type"),
            card.get("mediaType"),
        ]
        if any("video" in str(value or "").casefold() for value in explicit_types):
            return True
        for key in ("video", "video_info", "videoInfo", "video_url", "videoUrl"):
            value = card.get(key, entry.get(key))
            if isinstance(value, dict) and value:
                return True
            if isinstance(value, str) and value.strip():
                return True
        return False

    @staticmethod
    def _is_actionable_xiaohongshu_source_url(value: object) -> bool:
        """Return whether a Xiaohongshu URL can enter the logged-browser resolver."""
        if not isinstance(value, str) or not value.strip():
            return False
        candidate = value.strip()
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").casefold()
        is_short_share = host == "xhslink.com" or host.endswith(".xhslink.com")
        is_xiaohongshu = host == "xiaohongshu.com" or host.endswith(
            ".xiaohongshu.com"
        )
        is_note_path = "/explore/" in parsed.path or "/discovery/item/" in parsed.path
        return bool(
            parsed.scheme == "https"
            and (is_short_share or (is_xiaohongshu and is_note_path))
        )

    @staticmethod
    def _xiaohongshu_source_url_rank(value: object) -> int:
        """Prefer the richest reusable XHS entry when duplicate rows are merged."""
        if not LocalPlatformBrowserSearchProvider._is_actionable_xiaohongshu_source_url(
            value
        ):
            return 0
        parsed = urlparse(str(value).strip())
        query = {key.casefold() for key, _ in parse_qsl(parsed.query)}
        if "xsec_token" in query:
            return 3
        host = (parsed.hostname or "").casefold()
        if host == "xhslink.com" or host.endswith(".xhslink.com"):
            return 2
        return 1

    @staticmethod
    def _xiaohongshu_source_url(
        entry: dict[str, Any], card: dict[str, Any], item_id: str
    ) -> str | None:
        """Keep a valid Xiaohongshu note link for the logged-browser resolver.

        Direct note links may need the user's active Xiaohongshu session to
        resolve, but dropping them at ingestion prevents normal link transcription.
        """
        url_keys = (
            "share_url",
            "shareUrl",
            "source_url",
            "sourceUrl",
            "note_url",
            "noteUrl",
            "href",
            "url",
            "link",
        )
        token = None
        token_source = None

        def walk(value: Any, depth: int = 0) -> str | None:
            nonlocal token, token_source
            if depth > 6 or not isinstance(value, (dict, list)):
                return None
            found_url = None
            values = value.items() if isinstance(value, dict) else enumerate(value)
            for key, child in values:
                normalized_key = str(key).casefold()
                if isinstance(child, str) and normalized_key in {
                    str(item).casefold() for item in url_keys
                }:
                    candidate = child.strip()
                    if LocalPlatformBrowserSearchProvider._is_actionable_xiaohongshu_source_url(
                        candidate
                    ) and (
                        found_url is None
                        or LocalPlatformBrowserSearchProvider._xiaohongshu_source_url_rank(
                            candidate
                        )
                        > LocalPlatformBrowserSearchProvider._xiaohongshu_source_url_rank(
                            found_url
                        )
                    ):
                        # 一个卡片可能同时带 href(裸链接) 和 share_url(带
                        # xsec_token)。不能因遍历顺序先遇到裸链接就丢掉后者。
                        found_url = candidate
                if isinstance(child, dict) and normalized_key in {
                    "url",
                    "href",
                    "share_info",
                    "shareinfo",
                    "note_card",
                    "notecard",
                    "note",
                }:
                    direct = walk(child, depth + 1)
                    if direct and (
                        found_url is None
                        or LocalPlatformBrowserSearchProvider._xiaohongshu_source_url_rank(
                            direct
                        )
                        > LocalPlatformBrowserSearchProvider._xiaohongshu_source_url_rank(
                            found_url
                        )
                    ):
                        found_url = direct
                if normalized_key in {"xsec_token", "xsectoken"} and isinstance(child, str):
                    token = token or child.strip()
                if normalized_key in {"xsec_source", "xsecsource"} and isinstance(child, str):
                    token_source = token_source or child.strip()
                if isinstance(child, (dict, list)):
                    direct = walk(child, depth + 1)
                    if direct and (
                        found_url is None
                        or LocalPlatformBrowserSearchProvider._xiaohongshu_source_url_rank(
                            direct
                        )
                        > LocalPlatformBrowserSearchProvider._xiaohongshu_source_url_rank(
                            found_url
                        )
                    ):
                        found_url = direct
            return found_url

        direct_url = walk(entry)
        card_url = walk(card)
        if card_url and (
            direct_url is None
            or LocalPlatformBrowserSearchProvider._xiaohongshu_source_url_rank(
                card_url
            )
            > LocalPlatformBrowserSearchProvider._xiaohongshu_source_url_rank(
                direct_url
            )
        ):
            # entry 常带裸 href，而 note_card/share_info 才带完整分享地址；
            # 两个对象分开传入时也必须按丰富度择优，不能被 or 提前截断。
            direct_url = card_url
        if direct_url and token:
            parsed = urlparse(direct_url)
            query = parse_qsl(parsed.query, keep_blank_values=True)
            query_keys = {key.casefold() for key, _ in query}
            if "xsec_token" not in query_keys:
                query.append(("xsec_token", str(token)))
                if token_source and "xsec_source" not in query_keys:
                    query.append(("xsec_source", str(token_source)))
                direct_url = urlunparse(parsed._replace(query=urlencode(query)))
        if direct_url:
            return direct_url
        canonical = f"https://www.xiaohongshu.com/explore/{item_id}"
        if token:
            query = [("xsec_token", str(token))]
            if token_source:
                query.append(("xsec_source", str(token_source)))
            return f"{canonical}?{urlencode(query)}"
        # 没有分享参数时仍保存标准笔记链接，交给已登录浏览器尝试；
        # 该链接只是候选入口，不代表已经确认可播放。
        return canonical

    @staticmethod
    def _xiaohongshu_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data")
        if isinstance(data, dict):
            items = data.get("items") or data.get("notes") or data.get("results")
        elif isinstance(data, list):
            items = data
        else:
            items = payload.get("items") or payload.get("notes")
        if not isinstance(items, list):
            return []
        rows: list[dict[str, Any]] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            card = (
                entry.get("note_card")
                or entry.get("noteCard")
                or entry.get("card")
                or entry
            )
            if not isinstance(card, dict) or not LocalPlatformBrowserSearchProvider._xiaohongshu_is_video_card(entry, card):
                continue
            item_id = str(
                entry.get("id")
                or entry.get("note_id")
                or entry.get("noteId")
                or card.get("note_id")
                or card.get("noteId")
                or card.get("id")
                or ""
            )
            if not item_id:
                continue
            user = (
                card.get("user")
                if isinstance(card.get("user"), dict)
                else card.get("userInfo")
                if isinstance(card.get("userInfo"), dict)
                else {}
            )
            metrics = (
                card.get("interact_info")
                if isinstance(card.get("interact_info"), dict)
                else card.get("interactInfo")
                if isinstance(card.get("interactInfo"), dict)
                else {}
            )
            published_at = LocalPlatformBrowserSearchProvider._timestamp_from_mapping(
                card
            )
            resolved_source_url = LocalPlatformBrowserSearchProvider._xiaohongshu_source_url(
                entry, card, item_id
            )
            rows.append(
                {
                    "item_id": item_id,
                    "source_url": resolved_source_url,
                    "source_url_verified": bool(
                        resolved_source_url
                        and "xsec_token"
                        in {
                            key.casefold()
                            for key, _ in parse_qsl(
                                urlparse(resolved_source_url).query,
                                keep_blank_values=True,
                            )
                        }
                    ),
                    "title": LocalPlatformBrowserSearchProvider._clean_text(
                        card.get("display_title")
                        or card.get("displayTitle")
                        or card.get("title")
                        or card.get("desc")
                    ),
                    "author_id": str(user.get("user_id") or user.get("userId") or user.get("id") or ""),
                    "author_name": LocalPlatformBrowserSearchProvider._clean_text(
                        user.get("nickname") or user.get("nick_name") or user.get("nickName")
                    ),
                    "likes": LocalPlatformBrowserSearchProvider._first_count(
                        metrics,
                        "liked_count",
                        "likedCount",
                        "like_count",
                        "likeCount",
                        "likes",
                    ),
                    "comments": LocalPlatformBrowserSearchProvider._first_count(
                        metrics, "comment_count", "commentCount", "comments"
                    ),
                    "shares": LocalPlatformBrowserSearchProvider._first_count(
                        metrics, "share_count", "shareCount", "shares"
                    ),
                    "favorites": LocalPlatformBrowserSearchProvider._first_count(
                        metrics,
                        "collected_count",
                        "collectedCount",
                        "collect_count",
                        "collectCount",
                        "favorite_count",
                        "favoriteCount",
                        "favorites",
                    ),
                    "published_at": published_at,
                    "time_confident": published_at is not None,
                    "is_video": True,
                    "video_confirmed": True,
                    "evidence": "browser_search_response",
                }
            )
        return rows

    @staticmethod
    def _kuaishou_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data")
        search = data.get("visionSearchPhoto") if isinstance(data, dict) else None
        feeds = (
            search.get("feeds") if isinstance(search, dict) else payload.get("feeds")
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
            published_at = LocalPlatformBrowserSearchProvider._timestamp_from_mapping(
                photo
            )
            rows.append(
                {
                    "item_id": item_id,
                    "source_url": f"https://www.kuaishou.com/short-video/{item_id}",
                    "title": LocalPlatformBrowserSearchProvider._clean_text(
                        photo.get("caption")
                        or photo.get("title")
                        or photo.get("description")
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
                        photo,
                        "viewCount",
                        "view_count",
                        "playCount",
                        "play_count",
                        "views",
                    ),
                    "likes": LocalPlatformBrowserSearchProvider._first_count(
                        photo,
                        "likeCount",
                        "like_count",
                        "realLikeCount",
                        "real_like_count",
                        "likedCount",
                        "liked_count",
                        "likes",
                    ),
                    "comments": LocalPlatformBrowserSearchProvider._first_count(
                        photo, "commentCount", "comment_count", "comments"
                    ),
                    "shares": LocalPlatformBrowserSearchProvider._first_count(
                        photo, "shareCount", "share_count", "shares"
                    ),
                    "favorites": LocalPlatformBrowserSearchProvider._first_count(
                        photo,
                        "collectCount",
                        "collect_count",
                        "collectedCount",
                        "collected_count",
                        "favoriteCount",
                        "favorite_count",
                        "favorites",
                    ),
                    "duration_seconds": LocalPlatformBrowserSearchProvider._kuaishou_duration_seconds(
                        photo
                    ),
                    "published_at": published_at,
                    "time_confident": published_at is not None,
                    "evidence": "browser_search_response",
                }
            )
        return rows

    @staticmethod
    def _bilibili_rows(
        payload: dict[str, Any], *, keyword: str | None = None
    ) -> list[dict[str, Any]]:
        data = payload.get("data")
        items = data.get("result") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        rows: list[dict[str, Any]] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            direct_match = bool(
                not keyword
                or LocalPlatformBrowserSearchProvider._bilibili_entry_matches_keyword(
                    entry, keyword
                )
            )
            item_id = str(entry.get("bvid") or "").strip()
            if not item_id:
                continue
            title = LocalPlatformBrowserSearchProvider._clean_markup(
                entry.get("title")
            ) or LocalPlatformBrowserSearchProvider._clean_markup(
                entry.get("description") or entry.get("desc") or entry.get("tag")
            )
            strict_topic = bool(
                direct_match
                and keyword
                and not LocalPlatformBrowserSearchProvider._bilibili_text_matches(
                    keyword, title
                )
            )
            match_text = LocalPlatformBrowserSearchProvider._bilibili_entry_match_text(
                entry
            )
            published_at = LocalPlatformBrowserSearchProvider._timestamp_from_mapping(
                entry
            )
            rows.append(
                {
                    "item_id": item_id,
                    "source_url": f"https://www.bilibili.com/video/{item_id}",
                    "title": title,
                    "author_id": str(entry.get("mid") or ""),
                    "author_name": LocalPlatformBrowserSearchProvider._clean_text(
                        entry.get("author")
                    ),
                    "plays": LocalPlatformBrowserSearchProvider._first_count(
                        entry,
                        "play",
                        "play_count",
                        "playCount",
                        "view",
                        "view_count",
                        "viewCount",
                        "views",
                    ),
                    "likes": LocalPlatformBrowserSearchProvider._first_count(
                        entry,
                        "like",
                        "like_count",
                        "likeCount",
                        "liked_count",
                        "likedCount",
                        "likes",
                    ),
                    # B 站搜索响应中的 ``review`` / ``reply`` 才是评论数；
                    # ``video_review`` 是另一类互动字段，不能把它伪装成评论。
                    "comments": LocalPlatformBrowserSearchProvider._first_count(
                        entry,
                        "review",
                        "review_count",
                        "reviewCount",
                        "reply",
                        "reply_count",
                        "replyCount",
                        "comment",
                        "comment_count",
                        "commentCount",
                        "comments",
                    ),
                    "favorites": LocalPlatformBrowserSearchProvider._first_count(
                        entry,
                        "favorites",
                        "favorite",
                        "favorite_count",
                        "favoriteCount",
                    ),
                    "duration_seconds": LocalPlatformBrowserSearchProvider._first_duration_seconds(
                        entry,
                        "duration",
                        "durationSeconds",
                        "duration_ms",
                        "durationMs",
                    ),
                    "published_at": published_at,
                    "time_confident": published_at is not None,
                    "direct_match": direct_match,
                    "strict_topic": strict_topic,
                    "match_text": match_text,
                    "evidence": "browser_search_response",
                }
            )
        return rows

    @staticmethod
    def _kuaishou_duration_seconds(photo: dict[str, Any]) -> int | None:
        duration = LocalPlatformBrowserSearchProvider._duration_seconds(
            photo.get("duration"), milliseconds=True
        )
        if duration is not None:
            return duration
        return LocalPlatformBrowserSearchProvider._first_duration_seconds(
            photo, "durationSeconds", "duration_ms", "durationMs"
        )

    @staticmethod
    def _bilibili_entry_matches_keyword(entry: dict[str, Any], keyword: str) -> bool:
        needle = LocalPlatformBrowserSearchProvider._normalized_match_text(keyword)
        if not needle:
            return False
        return any(
            needle in LocalPlatformBrowserSearchProvider._normalized_match_text(value)
            for value in LocalPlatformBrowserSearchProvider._bilibili_match_values(
                entry
            )
        )

    @staticmethod
    def _bilibili_text_matches(keyword: str, *values: Any) -> bool:
        needle = LocalPlatformBrowserSearchProvider._normalized_match_text(keyword)
        return bool(needle) and any(
            needle in LocalPlatformBrowserSearchProvider._normalized_match_text(value)
            for value in values
        )

    @staticmethod
    def _bilibili_match_values(entry: dict[str, Any]) -> list[Any]:
        values = [
            entry.get("title"),
            entry.get("tag"),
            entry.get("topic"),
            entry.get("description"),
            entry.get("desc"),
        ]
        for field in ("tags", "topics"):
            nested = entry.get(field)
            if isinstance(nested, dict):
                nested = [nested]
            if not isinstance(nested, list):
                continue
            for value in nested:
                if isinstance(value, dict):
                    values.extend(
                        value.get(name)
                        for name in ("name", "title", "tag", "description")
                    )
                else:
                    values.append(value)
        return values

    @staticmethod
    def _bilibili_entry_match_text(entry: dict[str, Any]) -> str:
        values = LocalPlatformBrowserSearchProvider._bilibili_match_values(entry)
        return " ".join(
            LocalPlatformBrowserSearchProvider._clean_text(value)
            for value in values
            if value is not None
        ).strip()

    @staticmethod
    def _normalized_match_text(value: Any) -> str:
        text = html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))
        normalized = unicodedata.normalize("NFKC", text).casefold()
        return "".join(character for character in normalized if character.isalnum())

    def _to_items(
        self,
        rows: list[dict[str, Any]],
        *,
        observed_at: datetime,
        limit: int,
        platform_sort: str | None = None,
    ) -> list[ProviderSearchItem]:
        normalized: list[tuple[dict[str, Any], datetime, bool]] = []
        for row in rows:
            title = self._clean_text(row.get("title"))
            item_id = self._clean_text(row.get("item_id"))
            if not title or not item_id:
                continue
            published_at = row.get("published_at")
            time_confident = bool(
                row.get("time_confident") and isinstance(published_at, datetime)
            )
            effective_time = published_at if time_confident else observed_at
            normalized.append((row, effective_time, time_confident))

        if self.platform == Platform.BILIBILI:
            # B 站优先收集平台标记的匹配候选。先按时间截断再做相关性
            # 过滤会把后页的标题/话题/描述命中项丢掉，因此在仍有界的
            # 解析池内优先放入直匹配；中心层随后做三档相关性判定。
            normalized.sort(
                key=lambda entry: (
                    bool(entry[0].get("direct_match")),
                    entry[1],
                    int(entry[0].get("likes") or 0)
                    + int(entry[0].get("comments") or 0) * 3
                    + int(entry[0].get("shares") or 0) * 4
                    + int(entry[0].get("favorites") or 0) * 4,
                ),
                reverse=True,
            )
        elif self.platform == Platform.KUAISHOU and platform_sort == "likes":
            normalized.sort(
                key=lambda entry: (int(entry[0].get("likes") or 0), entry[1]),
                reverse=True,
            )
        elif self.platform == Platform.KUAISHOU and platform_sort == "newest":
            normalized.sort(
                key=lambda entry: (entry[2], entry[1]),
                reverse=True,
            )
        elif not (self.platform == Platform.KUAISHOU and platform_sort == "platform"):
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
            author_name = (
                self._clean_text(row.get("author_name")) or f"{self.spec.label}作者"
            )
            author_id = (
                self._clean_text(row.get("author_id"))
                or f"{self.platform.value}-{item_id}"
            )
            warnings: list[str] = []
            if not time_confident:
                warnings.append(
                    "搜索结果未返回可靠发布时间；按平台搜索顺序作为近期候选。"
                )
            if author_name == f"{self.spec.label}作者":
                warnings.append("搜索卡片未返回作者名。")
            duration_seconds = self._duration_seconds(row.get("duration_seconds"))
            if duration_seconds is None:
                warnings.append("搜索结果未返回视频时长。")
            match_text_evidence = ""
            if self.platform == Platform.BILIBILI:
                match_text = self._clean_text(row.get("match_text"))
                if match_text:
                    match_text_evidence = (
                        f"bilibili_match_text={quote(match_text[:1200], safe='')};"
                    )
            raw_source_url = row.get("source_url")
            items.append(
                ProviderSearchItem(
                    platform=self.platform,
                    platform_item_id=item_id,
                    title=self._clean_text(row.get("title")),
                    author_id=author_id,
                    author_name=author_name,
                    published_at=published_at,
                    duration_seconds=duration_seconds,
                    source_url=(
                        HttpUrl(str(raw_source_url)) if raw_source_url else None
                    ),
                    provider_rank=len(items) + 1,
                    metrics=VideoMetricSnapshot(
                        item_id=item_id,
                        sampled_at=observed_at,
                        plays=self._as_count(row.get("plays")),
                        likes=self._as_count(row.get("likes")),
                        comments=self._as_count(row.get("comments")),
                        shares=self._as_count(row.get("shares")),
                        favorites=self._as_count(row.get("favorites")),
                        confidence=0.7
                        if row.get("evidence") == "browser_search_response"
                        else 0.45,
                    ),
                    evidence=(
                        f"{self.platform.value}:{row.get('evidence')};"
                        f"{match_text_evidence}"
                        f"{'direct_match=1;' if row.get('direct_match') else ''}"
                        f"{'严格话题=1;' if row.get('strict_topic') else ''}"
                        f"time={'platform' if time_confident else 'search_order_fallback'};"
                        f"时长秒={duration_seconds if duration_seconds is not None else '未返回'}"
                    ),
                    data_quality_warnings=warnings,
                )
            )
        return items

    def _filter_kuaishou_duration_items(
        self,
        items: list[ProviderSearchItem],
        *,
        duration_bucket: str,
    ) -> tuple[list[ProviderSearchItem], int]:
        if self.platform != Platform.KUAISHOU or duration_bucket == "all":
            return items, 0

        def in_bucket(item: ProviderSearchItem) -> bool:
            duration = item.duration_seconds
            if duration is None:
                return False
            if duration_bucket == "under_60":
                return duration < 60
            if duration_bucket == "between_60_300":
                return 60 <= duration <= 300
            return duration > 300

        filtered = [item for item in items if in_bucket(item)]
        return (
            [
                item.model_copy(update={"provider_rank": index})
                for index, item in enumerate(filtered, start=1)
            ],
            len(items) - len(filtered),
        )

    def _filter_kuaishou_published_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        published_after: datetime | None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Keep only rows the provider can prove are inside the requested window."""
        if self.platform != Platform.KUAISHOU or published_after is None:
            return rows, 0
        retained: list[dict[str, Any]] = []
        filtered_count = 0
        for row in rows:
            published_at = row.get("published_at")
            if (
                bool(row.get("time_confident"))
                and isinstance(published_at, datetime)
                and published_at >= published_after
            ):
                retained.append(row)
            else:
                filtered_count += 1
        return retained, filtered_count

    def _count_kuaishou_qualified_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        published_after: datetime | None,
        duration_bucket: str,
    ) -> int:
        time_matched_rows, _ = self._filter_kuaishou_published_rows(
            rows,
            published_after=published_after,
        )
        return sum(
            bool(
                self._clean_text(row.get("item_id"))
                and self._clean_text(row.get("title"))
                and self._row_matches_kuaishou_duration(
                    row,
                    duration_bucket=duration_bucket,
                )
            )
            for row in time_matched_rows
        )

    def _row_matches_kuaishou_duration(
        self,
        row: dict[str, Any],
        *,
        duration_bucket: str,
    ) -> bool:
        if duration_bucket == "all":
            return True
        duration = self._duration_seconds(row.get("duration_seconds"))
        if duration is None:
            return False
        if duration_bucket == "under_60":
            return duration < 60
        if duration_bucket == "between_60_300":
            return 60 <= duration <= 300
        return duration > 300

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
                return self._page_requires_login(body_text)
        except Exception:
            return None

    def _page_requires_login(self, body_text: str) -> bool:
        """Use the same login markers for readiness and search-time checks."""
        return any(
            marker in body_text
            for marker in (*_HARD_VERIFICATION_MARKERS, *self.spec.login_markers)
        )

    def _missing_prerequisites(self) -> list[str]:
        if not self.enabled:
            return [f"{self.spec.label}浏览器发现开关"]
        missing: list[str] = []
        if not self.is_xiaohongshu_login_profile:
            try:
                playwright_spec = importlib.util.find_spec("playwright.sync_api")
            except ModuleNotFoundError:
                playwright_spec = None
            if playwright_spec is None:
                missing.append("Playwright Python 依赖")
        if self._browser_executable() is None:
            missing.append(
                "Microsoft Edge"
                if self.browser_channel == "msedge"
                else "Google Chrome"
            )
        return missing

    def _browser_executable(self) -> Path | None:
        if self.browser_channel == "msedge":
            candidates = [
                shutil.which("msedge"),
                Path(os.environ.get("PROGRAMFILES(X86)", ""))
                / "Microsoft/Edge/Application/msedge.exe",
                Path(os.environ.get("PROGRAMFILES", ""))
                / "Microsoft/Edge/Application/msedge.exe",
            ]
        else:
            candidates = [
                shutil.which("chrome"),
                Path(os.environ.get("PROGRAMFILES", ""))
                / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("PROGRAMFILES(X86)", ""))
                / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "Google/Chrome/Application/chrome.exe",
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
        # 平台卡片文本不可控，标题/描述可能混入超大数字；相对时间
        # 超出可表示范围时按不可靠处理，而不是让整次采集崩溃。
        try:
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
        except (OverflowError, ValueError):
            return None
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
    def _parse_count_text(text: str | None) -> int | None:
        """Parse a raw card count like "1.2万", "1234" or "0" into an int.

        B 站/小红书卡片上的数字没有标签文字(如"点赞"),直接用正则
        解析数字与"万"单位。
        """
        if text is None:
            return None
        match = re.search(r"^\s*([\d.]+)\s*([万亿wW]?)\s*$", str(text))
        if not match:
            return None
        unit = "万" if match.group(2) in {"w", "W"} else match.group(2)
        return LocalPlatformBrowserSearchProvider._as_count(f"{match.group(1)}{unit}")

    @staticmethod
    def _first_duration_seconds(mapping: dict[str, Any], *keys: str) -> int | None:
        for key in keys:
            duration = LocalPlatformBrowserSearchProvider._duration_seconds(
                mapping.get(key), milliseconds=key in {"duration_ms", "durationMs"}
            )
            if duration is not None:
                return duration
        return None

    @staticmethod
    def _duration_seconds(value: Any, *, milliseconds: bool = False) -> int | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if re.fullmatch(r"\d{1,3}:\d{2}(?::\d{2})?", text):
            parts = [int(part) for part in text.split(":")]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        try:
            seconds = int(float(text))
        except (TypeError, ValueError):
            return None
        if seconds <= 0:
            return None
        seconds = seconds // 1000 if milliseconds else seconds
        return seconds if seconds > 0 else None

    @staticmethod
    def _as_count(value: Any) -> int | None:
        if value is None:
            return None
        raw = str(value).replace(",", "").strip().casefold()
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
