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
from typing import Any
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen

from pydantic import HttpUrl

from src.adapters.licensed import LicensedProviderError
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
_MIN_QUALIFYING_LIKES = 100
_MIN_QUALIFYING_LIKES_PER_DAY = 1.0
_HOTSPOT_PAGE_SETTLE_RANGE_MS = (3_500, 5_500)
_HOTSPOT_SEARCH_SETTLE_RANGE_MS = (3_000, 5_000)
_HOTSPOT_SCROLL_REFRESH_RANGE_MS = (450, 850)
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
_HOTSPOT_ADAPTER_VERSION = "hotspot_fiber_v5_three_boards"


@dataclass(frozen=True)
class BrowserSessionStatus:
    enabled: bool
    running: bool
    login_required: bool
    ready_to_crawl: bool
    phase: str
    message: str


class LocalDouyinBrowserSearchProvider:
    """Render-only discovery provider backed by a dedicated Chrome profile."""

    # 保留既有来源标识，历史批次与缓存键无需迁移；evidence 区分热点宝记录。
    provider_name = "douyin_local_browser"
    adapter_version = _HOTSPOT_ADAPTER_VERSION

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

    def start_login_browser(self) -> BrowserSessionStatus:
        status = self.session_status()
        if status.running:
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
        subprocess.Popen(  # noqa: S603 - executable is resolved from an allowlist
            [
                str(executable),
                f"--remote-debugging-port={self.debug_port}",
                f"--user-data-dir={self.profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                _HOTSPOT_ENTRY_URL,
            ],
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
                return status
        return BrowserSessionStatus(
            True,
            False,
            True,
            False,
            "starting",
            "专用 Chrome 正在启动；请等待几秒后点击“检查连接状态”，再在该窗口登录抖音。",
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
            diagnostic = (
                "已读取到相关素材，但没有同时达到点赞不少于 100、"
                "日均点赞不少于 1 的固定质量线。"
            )
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
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.connect_over_cdp(endpoint)
                    context = browser.contexts[0]
                    page = context.new_page()
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
                            except PlaywrightError as exc:
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
                        except PlaywrightError as exc:
                            errors.append(ProviderSearchError(
                                kind=ProviderErrorKind.CONNECTION,
                                message=f"热点宝话题榜读取失败，已跳过：{exc}",
                                retryable=False,
                            ))

                        if has_enough_qualifying_rows():
                            return rows, errors

                        # 3) Search leaderboard is consulted for visibility, while the
                        # rendered public Douyin search result supplies the actual
                        # exact-keyword video cards (Hotspot search can be fuzzy).
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
                            ensure_visible_page(
                                "https://www.douyin.com/search/"
                                + quote(keyword, safe="") + "?type=video"
                            )
                            # Douyin virtualises its result list.  Reading the DOM once
                            # only sees the first rendered card, which made the search
                            # source look like it had a single result.  Collect every
                            # rendered viewport before applying the quality gate.
                            for row in self._collect_douyin_search_rows(page):
                                row.update({
                                    "window_hours": window_hours,
                                    "list_type": 3001,
                                    "list_label": "抖音搜索",
                                    "source_kind": "douyin_search",
                                })
                                rows.append(row)
                        except LicensedProviderError:
                            raise
                        except PlaywrightError as exc:
                            errors.append(ProviderSearchError(
                                kind=ProviderErrorKind.CONNECTION,
                                message=f"抖音搜索读取失败，已跳过：{exc}",
                                retryable=False,
                            ))

                        return rows, errors
                    finally:
                        page.close()
            except LicensedProviderError:
                raise
            except (PlaywrightError, OSError, ConnectionError) as exc:
                last_error = exc
                if attempt == 0:
                    continue
        raise LicensedProviderError(
            "连接本机 Chrome 失败，已自动重试一次。",
            kind=ProviderErrorKind.CONNECTION,
            retryable=False,
        ) from last_error

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
                  likes: number(video?.statistics?.digg_count || video?.digg_count || video?.like_count) ?? number(likesText),
                  published_text: String(video?.create_time || video?.createTime || publishedText || ''),
                });
              }
              return results;
            }"""
        )

    def _collect_douyin_search_rows(self, page) -> list[dict[str, Any]]:
        """Collect the dynamically rendered viewports of a Douyin search result page."""
        return self._collect_scrolled_rows(page, self._extract_douyin_search_rows)

    def _collect_scrolled_rows(self, page, extractor) -> list[dict[str, Any]]:
        """Accumulate virtualised cards while only reading the rendered page."""
        rows_by_id: dict[str, dict[str, Any]] = {}
        stagnant_rounds = 0
        previous_count = -1
        for _ in range(_HOTSPOT_MAX_SCROLL_ROUNDS):
            for row in extractor(page):
                item_id = str(row.get("item_id") or "")
                if item_id:
                    rows_by_id[item_id] = row

            current_count = len(rows_by_id)
            if current_count >= _HOTSPOT_MAX_ROWS_PER_LIST:
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
            for field in ("score", "plays", "likes", "fans", "duration", "comments", "shares"):
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
                -((LocalDouyinBrowserSearchProvider._quality_details(row, observed_at) or (0.0, ""))[0]),
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
            if duration_seconds is None or duration_seconds <= 0:
                filter_counts["duration"] += 1
                continue
            topic_exact = bool(row.get("topic_exact"))
            if not topic_exact and not title_matches_keyword(title=title, keyword=keyword):
                filter_counts["relevance"] += 1
                continue
            quality = LocalDouyinBrowserSearchProvider._quality_details(row, observed_at)
            if quality is None:
                filter_counts["quality"] += 1
                continue
            seen.add(item_id)
            incremental_plays = LocalDouyinBrowserSearchProvider._as_int(row.get("plays"))
            source_kinds = row.get("source_kinds") or [row.get("source_kind")]
            list_type = LocalDouyinBrowserSearchProvider._as_int(row.get("list_type"))
            is_non_video_source = (
                bool({"topic_board", "search_board", "douyin_search"}.intersection(source_kinds))
                or list_type in (*_HOTSPOT_TOPIC_LIST_TYPES, *_HOTSPOT_SEARCH_LIST_TYPES)
            )
            if not is_non_video_source and (incremental_plays is None or incremental_plays <= 1000):
                filter_counts["incremental_plays"] += 1
                if incremental_plays is not None and len(low_incremental_items) < limit:
                    low_incremental_items.append(
                        LocalDouyinBrowserSearchProvider._to_provider_item(
                            row=row,
                            item_id=item_id,
                            title=title,
                            duration_seconds=duration_seconds,
                            incremental_plays=incremental_plays,
                            observed_at=observed_at,
                            keyword=keyword,
                            provider_rank=len(low_incremental_items) + 1,
                            likes_per_day=quality[0],
                            quality_basis=quality[1],
                        )
                    )
                continue
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
                    likes_per_day=quality[0],
                    quality_basis=quality[1],
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
        duration_seconds: int,
        incremental_plays: int | None,
        observed_at: datetime,
        keyword: str,
        provider_rank: int,
        likes_per_day: float,
        quality_basis: str,
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
        return ProviderSearchItem(
            platform=Platform.DOUYIN,
            platform_item_id=item_id,
            title=title,
            author_id=f"hotspot-{item_id}",
            author_name=str(row.get("author_name") or "热点宝作者待补充"),
            published_at=published_at,
            source_url=HttpUrl(f"https://www.douyin.com/video/{item_id}"),
            provider_rank=provider_rank,
            metrics=VideoMetricSnapshot(
                item_id=item_id,
                sampled_at=observed_at,
                plays=incremental_plays,
                likes=LocalDouyinBrowserSearchProvider._as_int(row.get("likes")),
                comments=LocalDouyinBrowserSearchProvider._as_int(row.get("comments")),
                shares=LocalDouyinBrowserSearchProvider._as_int(row.get("shares")),
                confidence=(0.8 if row.get("topic_exact") or "video_board" in source_kinds else 0.6),
            ),
            evidence=(
                f"hotspot:{'|'.join(list_labels) or '爆款榜'}:"
                f"{row.get('window_hours', '?')}h:关键词={keyword};"
                f"来源={'|'.join(source_kinds) or 'browser_visible'};"
                f"日均点赞={likes_per_day};"
                f"质量口径={quality_basis};"
                f"严格话题={1 if row.get('topic_exact') else 0};"
                f"话题={row.get('topic_name') or '未返回'};"
                f"热度={row.get('score') if row.get('score') is not None else '未返回'};"
                f"{'新增播放量' if is_video_board else '播放量'}={incremental_plays if incremental_plays is not None else '未返回'};"
                f"{'新增点赞量' if is_video_board else '点赞数'}={row.get('likes') if row.get('likes') is not None else '未返回'};"
                f"点赞率={row.get('like_rate') if row.get('like_rate') is not None else '未返回'};"
                f"粉丝={row.get('fans') if row.get('fans') is not None else '未返回'};"
                f"时长秒={duration_seconds}"
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
        if not self._playwright_available():
            missing.append("Playwright Python 依赖")
        if self._browser_executable() is None:
            missing.append("Google Chrome" if self.browser_channel == "chrome" else "Microsoft Edge")
        return missing

    @staticmethod
    def _playwright_available() -> bool:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False
        return True

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
