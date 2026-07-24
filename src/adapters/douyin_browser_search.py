"""Low-frequency Douyin discovery through a user-controlled local Chrome.

This adapter deliberately reads only rendered search-result links.  It does not
reverse engineer request signatures, import personal browser profiles, solve
verification challenges, or download media.  A dedicated, local profile must
be opened and logged in by the operator before collection can run.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
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
_HOTSPOT_WINDOW_HOURS = 168
_HOTSPOT_LIST_TYPES = (1001, 1002, 1003, 1004, 1005)
_HOTSPOT_MAX_ROWS_PER_LIST = 50
_HOTSPOT_MAX_SCROLL_ROUNDS = 8
_HOTSPOT_MAX_RESULT_LIMIT = 100
_HOTSPOT_PAGE_SETTLE_MS = 4_000
_HOTSPOT_SEARCH_SETTLE_MS = 4_000
_HOTSPOT_SCROLL_SETTLE_MS = 2_000
_HOTSPOT_LIST_COOLDOWN_MS = 8_000
_HOTSPOT_SCROLL_PIXELS = 500
_HOTSPOT_LIST_LABELS = {
    1001: "视频总榜",
    1002: "低粉爆款",
    1003: "高完播率",
    1004: "高涨粉率",
    1005: "高点赞率",
}
_HOTSPOT_ENTRY_URL = (
    "https://douhot.douyin.com/square/hotspot?"
    "active_tab=hotspot_video&date_window=168&sub_type=1001"
)
_HOTSPOT_ADAPTER_VERSION = "hotspot_fiber_v4_safe_pacing"


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
        missing: list[str] = []
        if not self.enabled:
            missing.append("DOUYIN_BROWSER_DISCOVERY_ENABLED=true")
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            missing.append("Playwright Python 依赖")
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
            permission_status=(
                "local_browser_session_ready"
                if not missing and self.session_status().running
                else "local_browser_login_required"
            ),
            credential_alias="local-dedicated-browser-profile",
            missing_configuration=missing,
        )

    def session_status(self) -> BrowserSessionStatus:
        if not self.enabled:
            return BrowserSessionStatus(
                False, False, False, False, "disabled", "本机浏览器发现已关闭。"
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
        if not self.capabilities().enabled:
            raise LicensedProviderError(
                "本机浏览器发现尚未就绪，请检查开关和 Playwright 依赖。",
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

        observed_at = self.clock()
        raw_rows, collection_errors = self._collect_hotspot_rows(keyword)
        items, warnings, filter_counts = self._to_items(raw_rows, keyword, observed_at, limit)
        diagnostic = None
        if raw_rows and not items:
            diagnostic = "热点宝页面出现榜单内容，但未能读取可用视频标题；页面结构可能已变化。"
        if not raw_rows:
            diagnostic = "热点宝未返回可识别视频；可能没有结果、未登录或需要人工验证。"
        return ProviderSearchPage(
            platform=platform,
            provider=self.provider_name,
            items=items,
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
    ) -> tuple[list[dict[str, Any]], list[ProviderSearchError]]:
        """Read all five visible 7-day Hotspot leaderboards and merge duplicate videos.

        The operator owns the login step. This routine never reads browser
        cookies, calls private APIs, or attempts to defeat a verification page.
        Each leaderboard is visited even when earlier lists already contain
        enough eligible videos, so list-specific discovery is not biased by
        the video total leaderboard.
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
                        for list_type in _HOTSPOT_LIST_TYPES:
                            url = (
                                "https://douhot.douyin.com/square/hotspot?"
                                f"active_tab=hotspot_video&date_window={_HOTSPOT_WINDOW_HOURS}"
                                f"&sub_type={list_type}"
                            )
                            try:
                                response = page.goto(url, wait_until="domcontentloaded")
                                if response is not None and response.status in {403, 429}:
                                    raise LicensedProviderError(
                                        f"热点宝返回 {response.status}，已停止采集并进入安全暂停。",
                                        kind=ProviderErrorKind.RATE_LIMIT,
                                    )
                                page.wait_for_timeout(_HOTSPOT_PAGE_SETTLE_MS)
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
                                self._fill_hotspot_keyword(page, keyword)
                                page.wait_for_timeout(_HOTSPOT_SEARCH_SETTLE_MS)
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
                                    page.wait_for_timeout(_HOTSPOT_SCROLL_SETTLE_MS)
                                for row in list_rows.values():
                                    row.update(
                                        {
                                            "window_hours": _HOTSPOT_WINDOW_HOURS,
                                            "list_type": list_type,
                                            "list_label": _HOTSPOT_LIST_LABELS[list_type],
                                        }
                                    )
                                    rows.append(row)
                                if list_type != _HOTSPOT_LIST_TYPES[-1]:
                                    page.wait_for_timeout(_HOTSPOT_LIST_COOLDOWN_MS)
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
                        return self._merge_hotspot_rows(rows), errors
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
    def _fill_hotspot_keyword(page, keyword: str) -> None:
        """Use the visible Hotspot search box; failure leaves the page intact."""
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
                target.fill(keyword)
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
    def _merge_hotspot_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep one row per video while retaining every leaderboard it matched."""
        merged: dict[str, dict[str, Any]] = {}
        for row in rows:
            item_id = str(row.get("item_id") or "")
            if not item_id:
                continue
            current = merged.get(item_id)
            if current is None:
                current = {**row, "list_types": set(), "list_labels": set()}
                merged[item_id] = current
            current["list_types"].add(int(row.get("list_type") or 0))
            current["list_labels"].add(str(row.get("list_label") or ""))
            for field in ("score", "plays", "likes", "fans", "duration"):
                value = row.get(field)
                if value is not None and (current.get(field) is None or value > current[field]):
                    current[field] = value
            if current.get("like_rate") is None and row.get("like_rate") is not None:
                current["like_rate"] = row["like_rate"]
        normalized = []
        for row in merged.values():
            row["list_types"] = sorted(value for value in row["list_types"] if value)
            row["list_labels"] = sorted(value for value in row["list_labels"] if value)
            normalized.append(row)
        return normalized

    @staticmethod
    def _to_items(
        rows: list[dict[str, Any]],
        keyword: str,
        observed_at: datetime,
        limit: int,
    ) -> tuple[list[ProviderSearchItem], list[ProviderSearchError], dict[str, int]]:
        items: list[ProviderSearchItem] = []
        errors: list[ProviderSearchError] = []
        filter_counts = {"duration": 0, "incremental_plays": 0, "relevance": 0}
        seen: set[str] = set()
        sorted_rows = sorted(
            rows,
            key=lambda row: (
                -(LocalDouyinBrowserSearchProvider._as_int(row.get("plays")) or 0),
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
            incremental_plays = LocalDouyinBrowserSearchProvider._as_int(row.get("plays"))
            if incremental_plays is None or incremental_plays <= 1000:
                filter_counts["incremental_plays"] += 1
                continue
            if not title_matches_keyword(title=title, keyword=keyword):
                filter_counts["relevance"] += 1
                continue
            seen.add(item_id)
            list_labels = list(row.get("list_labels") or [row.get("list_label", "")])
            items.append(
                ProviderSearchItem(
                    platform=Platform.DOUYIN,
                    platform_item_id=item_id,
                    title=title,
                    author_id=f"hotspot-{item_id}",
                    author_name=str(row.get("author_name") or "热点宝作者待补充"),
                    published_at=observed_at,
                    source_url=HttpUrl(f"https://www.douyin.com/video/{item_id}"),
                    provider_rank=len(items) + 1,
                    metrics=VideoMetricSnapshot(
                        item_id=item_id,
                        sampled_at=observed_at,
                        plays=incremental_plays,
                        likes=LocalDouyinBrowserSearchProvider._as_int(row.get("likes")),
                        confidence=(0.8 if row.get("list_type") is not None else 0.4),
                    ),
                    evidence=(
                        f"hotspot:{'|'.join(list_labels) or '爆款榜'}:"
                        f"{row.get('window_hours', '?')}h:关键词={keyword};"
                        f"热度={row.get('score') if row.get('score') is not None else '未返回'};"
                        f"新增播放量={incremental_plays};"
                        f"新增点赞量={row.get('likes') if row.get('likes') is not None else '未返回'};"
                        f"点赞率={row.get('like_rate') if row.get('like_rate') is not None else '未返回'};"
                        f"粉丝={row.get('fans') if row.get('fans') is not None else '未返回'};"
                        f"时长秒={duration_seconds}"
                        if row.get("list_type") is not None
                        else f"browser_visible_search:{keyword}"
                    ),
                    data_quality_warnings=[],
                )
            )
            if len(items) >= limit:
                break
        return items, errors, filter_counts

    @staticmethod
    def _as_int(value: Any) -> int | None:
        try:
            return max(0, int(float(value))) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _debug_url(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}/json/version"

    def _debug_pages_url(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}/json/list"

    def _browser_executable(self) -> Path | None:
        names = ["chrome", "chrome.exe"] if self.browser_channel == "chrome" else ["msedge", "msedge.exe"]
        for name in names:
            found = shutil.which(name)
            if found:
                return Path(found)
        base_paths = [
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
            Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        ]
        for path in base_paths:
            if path.exists():
                return path
        return None
