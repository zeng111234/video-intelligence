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


_VIDEO_ID_RE = re.compile(r"/video/(\d{10,})")
_LOGIN_MARKERS = ("安全验证", "扫码登录", "请完成验证")


@dataclass(frozen=True)
class BrowserSessionStatus:
    enabled: bool
    running: bool
    login_required: bool
    message: str


class LocalDouyinBrowserSearchProvider:
    """Render-only discovery provider backed by a dedicated Chrome profile."""

    provider_name = "douyin_local_browser"

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
            display_name="本机 Chrome 公开搜索采集",
            mode=ProviderMode.LOCAL_BROWSER,
            enabled=not missing,
            supported_platforms=[Platform.DOUYIN] if not missing else [],
            max_page_size=10,
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
            return BrowserSessionStatus(False, False, False, "本机浏览器发现已关闭。")
        try:
            with urlopen(self._debug_url(), timeout=1.5) as response:  # noqa: S310 - localhost only
                payload = json.loads(response.read().decode("utf-8"))
            product = str(payload.get("Browser") or "Chrome")
            return BrowserSessionStatus(
                True,
                True,
                False,
                f"已连接专用浏览器：{product}。请确认此专用窗口内已经登录抖音。",
            )
        except (URLError, OSError, ValueError, json.JSONDecodeError):
            return BrowserSessionStatus(
                True,
                False,
                True,
                "请先打开专用浏览器并登录抖音；验证出现时系统会暂停。",
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
                "https://www.douyin.com/",
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
            raise LicensedProviderError("每次最多采集 10 条可见结果。", kind=ProviderErrorKind.VALIDATION)
        status = self.session_status()
        if not status.running:
            raise LicensedProviderError(status.message, kind=ProviderErrorKind.AUTHORIZATION)

        observed_at = self.clock()
        raw_rows = self._collect_visible_rows(keyword)
        items, warnings = self._to_items(raw_rows, keyword, observed_at, limit)
        diagnostic = None
        if raw_rows and not items:
            diagnostic = "页面出现作品链接，但未能读取可用标题；页面结构可能已变化。"
        if not raw_rows:
            diagnostic = "页面未出现可见作品链接；可能没有结果、未登录或需要人工验证。"
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
            errors=warnings,
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

    def _collect_visible_rows(self, keyword: str) -> list[dict[str, str]]:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        endpoint = f"http://127.0.0.1:{self.debug_port}"
        search_url = f"https://www.douyin.com/search/{quote(keyword)}?type=video"
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.connect_over_cdp(endpoint)
                    context = browser.contexts[0]
                    page = context.new_page()
                    try:
                        page.set_default_timeout(int(self.timeout_seconds * 1000))
                        page.goto(search_url, wait_until="domcontentloaded")
                        page.wait_for_timeout(2_000)
                        body_text = page.locator("body").inner_text(timeout=3_000)
                        if any(marker in body_text for marker in _LOGIN_MARKERS):
                            raise LicensedProviderError(
                                "抖音要求登录或安全验证，已暂停采集，请在专用浏览器中人工处理。",
                                kind=ProviderErrorKind.AUTHORIZATION,
                            )
                        return page.locator("a[href*='/video/']").evaluate_all(
                            """nodes => nodes.map(node => ({
                                href: node.href || '',
                                text: (node.innerText || node.textContent || '').trim(),
                                aria: node.getAttribute('aria-label') || ''
                            }))"""
                        )
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
    def _to_items(
        rows: list[dict[str, Any]],
        keyword: str,
        observed_at: datetime,
        limit: int,
    ) -> tuple[list[ProviderSearchItem], list[ProviderSearchError]]:
        items: list[ProviderSearchItem] = []
        errors: list[ProviderSearchError] = []
        seen: set[str] = set()
        for index, row in enumerate(rows):
            href = str(row.get("href") or "")
            match = _VIDEO_ID_RE.search(href)
            if not match:
                continue
            item_id = match.group(1)
            if item_id in seen:
                continue
            title = " ".join(
                part.strip() for part in (str(row.get("text") or ""), str(row.get("aria") or "")) if part.strip()
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
            seen.add(item_id)
            items.append(
                ProviderSearchItem(
                    platform=Platform.DOUYIN,
                    platform_item_id=item_id,
                    title=title,
                    author_id=f"browser-unverified-{item_id}",
                    author_name="待人工核验",
                    published_at=observed_at,
                    source_url=HttpUrl(f"https://www.douyin.com/video/{item_id}"),
                    provider_rank=len(items) + 1,
                    metrics=VideoMetricSnapshot(
                        item_id=item_id,
                        sampled_at=observed_at,
                        confidence=0.4,
                    ),
                    evidence=f"browser_visible_search:{keyword}",
                    data_quality_warnings=[
                        "本机公开搜索仅读取可见卡片；发布时间和互动指标待后续人工或授权数据补充。"
                    ],
                )
            )
            if len(items) >= limit:
                break
        return items, errors

    def _debug_url(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}/json/version"

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
