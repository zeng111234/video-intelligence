"""Resolve one authorized public Douyin share link with a local browser.

The resolver deliberately observes normal page/network responses only.  It does
not import browser profiles, persist cookies, solve verification challenges, or
rewrite media URLs to remove watermarks.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_WORK_ID_RE = re.compile(r"/(?:share/)?video/(\d+)")
_ALLOWED_SHARE_HOSTS = {
    "v.douyin.com",
    "www.douyin.com",
    "douyin.com",
    "m.douyin.com",
    "www.iesdouyin.com",
    "iesdouyin.com",
}
_BROWSER_LOCK = threading.Lock()
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_PUBLIC_PAGE_TIMEOUT_SECONDS = 12.0
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class DouyinParserError(RuntimeError):
    def __init__(self, message: str, *, work_id: str | None = None) -> None:
        super().__init__(message)
        self.user_message = message
        self.work_id = work_id


@dataclass(frozen=True)
class ParsedDouyinLink:
    share_url: str
    work_id: str | None


@dataclass(frozen=True)
class ParsedDouyinMedia:
    share_url: str
    work_id: str
    media_url: str
    title: str

    @property
    def media_request_headers(self) -> dict[str, str]:
        return {
            "Referer": "https://www.douyin.com/",
            "User-Agent": _BROWSER_USER_AGENT,
        }


def parse_douyin_share_text(value: str) -> ParsedDouyinLink:
    """Extract exactly one public Douyin HTTPS URL from pasted share text."""

    matches = [match.rstrip("，。；、！!？?）)】]}>.,;") for match in _URL_RE.findall(value)]
    matches = [match for match in matches if match]
    if len(matches) != 1:
        raise DouyinParserError("请粘贴一条抖音分享链接或分享口令，不能同时包含多条链接。")
    parsed = urlparse(matches[0])
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if parsed.scheme != "https" or host not in _ALLOWED_SHARE_HOSTS:
        raise DouyinParserError("仅支持公开的抖音 HTTPS 分享链接。")
    work_id_match = _WORK_ID_RE.search(parsed.path)
    work_id = work_id_match.group(1) if work_id_match else parse_qs(parsed.query).get("modal_id", [None])[0]
    return ParsedDouyinLink(matches[0], work_id if work_id and work_id.isdigit() else None)


class LocalDouyinBrowserParserClient:
    """Single-link resolver using an ephemeral local Chrome/Edge context."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        browser_channel: str = "chrome",
        timeout_seconds: float = 35.0,
    ) -> None:
        if browser_channel not in {"chrome", "msedge"}:
            raise ValueError("DOUYIN_BROWSER_CHANNEL 仅支持 chrome 或 msedge。")
        self.enabled = enabled
        self.browser_channel = browser_channel
        self.timeout_seconds = max(10.0, min(timeout_seconds, 60.0))

    def capabilities(self) -> tuple[bool, str | None]:
        if not self.enabled:
            return False, "本机浏览器解析已关闭（DOUYIN_LOCAL_BROWSER_ENABLED=false）。"
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False, "缺少 Playwright Python 依赖，请安装后重启后端。"
        return True, None

    def resolve(self, share_text: str) -> ParsedDouyinMedia:
        link = parse_douyin_share_text(share_text)
        available, reason = self.capabilities()
        if not available:
            raise DouyinParserError(reason or "本机浏览器解析不可用。", work_id=link.work_id)
        if not _BROWSER_LOCK.acquire(timeout=2):
            raise DouyinParserError("已有本机抖音链接解析任务正在运行，请稍后再试。", work_id=link.work_id)
        try:
            last_error: Exception | None = None
            for attempt in range(2):
                try:
                    return self._resolve_once(link)
                except DouyinParserError:
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt == 0:
                        continue
            raise DouyinParserError(
                "本机浏览器解析连接失败，已自动重试一次。",
                work_id=link.work_id,
            ) from last_error
        finally:
            _BROWSER_LOCK.release()

    def _resolve_once(self, link: ParsedDouyinLink) -> ParsedDouyinMedia:
        """Prefer the public page payload, then use an ephemeral browser fallback."""
        try:
            return self._resolve_from_public_page(link)
        except Exception:
            # Public page markup changes frequently.  The browser fallback observes
            # the normal page load without relying on a private account session.
            return self._resolve_with_browser(link)

    def _resolve_from_public_page(self, link: ParsedDouyinLink) -> ParsedDouyinMedia:
        target_url = self._target_url(link)
        current_url, html = self._fetch_public_share_page(target_url)
        marker = re.search(r"window\._ROUTER_DATA\s*=\s*", html)
        if marker is None:
            raise RuntimeError("公开分享页未提供可读取的作品数据")
        payload, _ = json.JSONDecoder().raw_decode(html[marker.end() :].lstrip())
        captured: dict[str, str] = {}
        self._capture_router_payload(
            payload,
            captured,
            expected_work_id=link.work_id,
        )
        work_id = captured.get("work_id") or link.work_id
        media_url = captured.get("media_url")
        if not media_url:
            raise RuntimeError("公开分享页未提供可转写的视频流")
        if not work_id or not work_id.isdigit():
            raise RuntimeError("公开分享页未返回有效作品 ID")
        title = captured.get("title") or f"抖音作品 {work_id}"
        return ParsedDouyinMedia(
            share_url=current_url,
            work_id=work_id,
            media_url=media_url,
            title=title[:200],
        )

    @staticmethod
    def _fetch_public_share_page(share_url: str) -> tuple[str, str]:
        """Follow only public Douyin redirects before reading the public HTML."""
        current_url = share_url
        with httpx.Client(timeout=_PUBLIC_PAGE_TIMEOUT_SECONDS, follow_redirects=False) as client:
            for _ in range(5):
                response = client.get(current_url, headers={"User-Agent": _BROWSER_USER_AGENT})
                if response.status_code not in _REDIRECT_STATUS_CODES:
                    response.raise_for_status()
                    return current_url, response.text
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError("分享链接重定向缺少目标地址")
                next_url = urljoin(current_url, location)
                parsed = urlparse(next_url)
                host = parsed.hostname.casefold() if parsed.hostname else ""
                if parsed.scheme != "https" or host not in _ALLOWED_SHARE_HOSTS:
                    raise RuntimeError("分享链接重定向到了不受支持的地址")
                current_url = next_url
        raise RuntimeError("分享链接重定向次数过多")

    def _resolve_with_browser(self, link: ParsedDouyinLink) -> ParsedDouyinMedia:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        captured: dict[str, str] = {}
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel=self.browser_channel, headless=True)
                context = None
                try:
                    context = browser.new_context(
                        locale="zh-CN",
                        user_agent=_BROWSER_USER_AGENT,
                        viewport={"width": 1280, "height": 720},
                    )
                    page = context.new_page()
                    timeout_ms = int(self.timeout_seconds * 1000)
                    page.set_default_navigation_timeout(timeout_ms)
                    page.set_default_timeout(timeout_ms)

                    def capture_response(response) -> None:
                        if captured.get("media_url"):
                            return
                        response_url = response.url
                        try:
                            if "/aweme/v1/web/aweme/detail/" in response_url:
                                self._capture_detail_payload(
                                    response.json(),
                                    captured,
                                    expected_work_id=link.work_id,
                                )
                                return
                            content_type = response.headers.get("content-type", "").casefold()
                            if (
                                link.work_id is None
                                and "video/" in content_type
                                and response_url.startswith("https://")
                            ):
                                captured["media_url"] = response_url
                        except Exception:
                            # A non-JSON or unreadable response is not a parse failure by itself.
                            return

                    page.on("response", capture_response)
                    try:
                        page.goto(
                            self._target_url(link),
                            wait_until="domcontentloaded",
                            timeout=timeout_ms,
                        )
                    except PlaywrightError:
                        pass
                    page.wait_for_timeout(5_000)
                    if not captured.get("media_url"):
                        try:
                            media_url = page.eval_on_selector("video", "node => node.currentSrc || node.src || ''")
                        except PlaywrightError:
                            media_url = ""
                        if isinstance(media_url, str) and media_url.startswith("https://"):
                            captured["media_url"] = media_url
                    current_url = page.url
                    if not captured.get("work_id"):
                        try:
                            captured["work_id"] = parse_douyin_share_text(current_url).work_id or ""
                        except DouyinParserError:
                            pass
                    if not captured.get("title"):
                        title = page.title().strip()
                        captured["title"] = re.sub(r"\s*[-|｜]\s*抖音\s*$", "", title).strip()
                finally:
                    if context is not None:
                        context.close()
                    browser.close()
        except PlaywrightError as exc:
            raise RuntimeError("浏览器无法打开抖音分享页") from exc

        work_id = captured.get("work_id") or link.work_id
        media_url = captured.get("media_url")
        if not media_url:
            raise DouyinParserError("未从公开页面读取到可转写的视频流，可能受链接状态或平台限制影响。", work_id=work_id)
        if not work_id or not work_id.isdigit():
            raise DouyinParserError("页面未返回有效作品 ID，无法创建转写任务。")
        title = captured.get("title") or f"抖音作品 {work_id}"
        return ParsedDouyinMedia(link.share_url, work_id, media_url, title[:200])

    @staticmethod
    def _target_url(link: ParsedDouyinLink) -> str:
        if link.work_id:
            return f"https://www.douyin.com/video/{link.work_id}"
        return link.share_url

    @staticmethod
    def _capture_detail_payload(
        payload: Any,
        captured: dict[str, str],
        *,
        expected_work_id: str | None = None,
    ) -> None:
        detail = payload.get("aweme_detail", payload) if isinstance(payload, dict) else {}
        if not isinstance(detail, dict):
            return
        identifier = detail.get("aweme_id") or detail.get("awemeId")
        if expected_work_id and str(identifier or "") != expected_work_id:
            return
        if isinstance(identifier, (str, int)) and str(identifier).isdigit():
            captured["work_id"] = str(identifier)
        title = detail.get("desc") or detail.get("title")
        if isinstance(title, str) and title.strip():
            captured["title"] = title.strip()
        video = detail.get("video")
        if not isinstance(video, dict):
            return
        for address_key in ("play_addr", "playAddr", "download_addr", "downloadAddr"):
            address = video.get(address_key)
            if not isinstance(address, dict):
                continue
            urls = address.get("url_list") or address.get("urlList")
            if isinstance(urls, list):
                for value in urls:
                    if isinstance(value, str) and value.startswith("https://"):
                        captured["media_url"] = value
                        return

    @staticmethod
    def _capture_router_payload(
        payload: Any,
        captured: dict[str, str],
        *,
        expected_work_id: str | None = None,
    ) -> None:
        """Read the public page's original play address without altering it."""
        if not isinstance(payload, dict):
            return
        loader_data = payload.get("loaderData")
        if not isinstance(loader_data, dict):
            return
        for page_data in loader_data.values():
            if not isinstance(page_data, dict):
                continue
            detail = page_data.get("videoInfoRes")
            if not isinstance(detail, dict):
                continue
            items = detail.get("item_list") or detail.get("itemList")
            if not isinstance(items, list) or not items or not isinstance(items[0], dict):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                LocalDouyinBrowserParserClient._capture_detail_payload(
                    {"aweme_detail": item},
                    captured,
                    expected_work_id=expected_work_id,
                )
                if captured.get("media_url"):
                    return
