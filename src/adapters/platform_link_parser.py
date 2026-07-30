"""Resolve one authorized platform share link through its normal browser page.

Douyin keeps its existing public-page resolver.  Xiaohongshu, Kuaishou and
Bilibili reuse the dedicated browser sessions already opened by the crawler.
The resolver reads only media URLs returned to, or rendered by, that visible
session.  It does not export cookies, solve verification challenges or alter
platform media URLs.
"""

from __future__ import annotations

import importlib.util
import re
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.adapters.douyin_parser import (
    DouyinParserError,
    LocalDouyinBrowserParserClient,
    parse_douyin_share_text,
)
from src.models import Platform
from src.platforms import platform_label


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SUPPORTED_HOSTS = {
    Platform.XIAOHONGSHU: {
        "xiaohongshu.com",
        "www.xiaohongshu.com",
        "xhslink.com",
        "www.xhslink.com",
    },
    Platform.KUAISHOU: {
        "kuaishou.com",
        "www.kuaishou.com",
        "v.kuaishou.com",
    },
    Platform.BILIBILI: {
        "bilibili.com",
        "www.bilibili.com",
        "m.bilibili.com",
        "b23.tv",
        "www.b23.tv",
    },
}
_WECHAT_HOSTS = {
    "channels.weixin.qq.com",
    "finder.video.qq.com",
    "weixin.qq.com",
    "www.weixin.qq.com",
}
_PLATFORM_HOME = {
    Platform.DOUYIN: "https://www.douyin.com/",
    Platform.XIAOHONGSHU: "https://www.xiaohongshu.com/",
    Platform.KUAISHOU: "https://www.kuaishou.com/",
    Platform.BILIBILI: "https://www.bilibili.com/",
}
_WORK_ID_PATTERNS = {
    Platform.XIAOHONGSHU: (
        re.compile(r"/explore/([a-zA-Z0-9]+)"),
        re.compile(r"/discovery/item/([a-zA-Z0-9]+)"),
    ),
    Platform.KUAISHOU: (
        re.compile(r"/short-video/([a-zA-Z0-9_-]+)"),
        re.compile(r"/f/([a-zA-Z0-9_-]+)"),
    ),
    Platform.BILIBILI: (re.compile(r"/video/(BV[a-zA-Z0-9]+)", re.IGNORECASE),),
}
_MEDIA_KEYS = {
    Platform.XIAOHONGSHU: {
        "master_url",
        "masterurl",
        "backup_urls",
        "backupurls",
        "play_url",
        "playurl",
    },
    Platform.KUAISHOU: {
        "photourl",
        "photoh265url",
        "playurl",
        "play_url",
        "masterurl",
        "master_url",
    },
}
_TITLE_KEYS = {"title", "desc", "caption", "description"}
_BROWSER_LOCK = threading.Lock()
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class PlatformLinkParserError(DouyinParserError):
    def __init__(
        self,
        message: str,
        *,
        platform: Platform | None = None,
        work_id: str | None = None,
    ) -> None:
        super().__init__(message, work_id=work_id)
        self.platform = platform


@dataclass(frozen=True)
class ParsedPlatformLink:
    platform: Platform
    share_url: str
    work_id: str | None


@dataclass(frozen=True)
class ParsedPlatformMedia:
    platform: Platform
    share_url: str
    work_id: str
    media_url: str
    title: str

    @property
    def media_request_headers(self) -> dict[str, str]:
        return {
            "Referer": _PLATFORM_HOME[self.platform],
            "User-Agent": _BROWSER_USER_AGENT,
        }


def parse_platform_share_text(value: str) -> ParsedPlatformLink:
    """Extract and strictly classify exactly one supported HTTPS share URL."""

    matches = [
        match.rstrip("，。；、！!？?）)】]}>.,;") for match in _URL_RE.findall(value)
    ]
    matches = [match for match in matches if match]
    if len(matches) != 1:
        raise PlatformLinkParserError(
            "请粘贴一条平台分享链接或分享口令，不能同时包含多条链接。"
        )
    share_url = matches[0]
    parsed = urlparse(share_url)
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if parsed.scheme != "https":
        raise PlatformLinkParserError("分享链接必须使用 HTTPS。")
    try:
        douyin = parse_douyin_share_text(share_url)
    except DouyinParserError:
        douyin = None
    if douyin is not None:
        return ParsedPlatformLink(Platform.DOUYIN, douyin.share_url, douyin.work_id)
    if host in _WECHAT_HOSTS:
        raise PlatformLinkParserError(
            "视频号分享页仍在微信客户端内部，暂不能自动解析；请上传有权处理的视频文件。",
            platform=Platform.WECHAT_CHANNELS,
        )
    platform = next(
        (
            item
            for item, allowed_hosts in _SUPPORTED_HOSTS.items()
            if host in allowed_hosts
        ),
        None,
    )
    if platform is None:
        raise PlatformLinkParserError(
            "当前仅支持抖音、小红书、快手和B站的公开分享链接。"
        )
    work_id = _work_id_from_url(platform, share_url)
    return ParsedPlatformLink(platform, share_url, work_id)


def _work_id_from_url(platform: Platform, url: str) -> str | None:
    parsed = urlparse(url)
    for pattern in _WORK_ID_PATTERNS.get(platform, ()):
        match = pattern.search(parsed.path)
        if match:
            return match.group(1)
    if platform == Platform.BILIBILI:
        bvid = parse_qs(parsed.query).get("bvid", [None])[0]
        if isinstance(bvid, str) and re.fullmatch(r"BV[a-zA-Z0-9]+", bvid):
            return bvid
    return None


class LocalPlatformLinkParserClient:
    """Single-link parser combining Douyin and dedicated platform browsers."""

    def __init__(
        self,
        *,
        douyin_parser: LocalDouyinBrowserParserClient,
        platform_providers: dict[Platform, Any],
        timeout_seconds: float = 35.0,
    ) -> None:
        self.douyin_parser = douyin_parser
        self.platform_providers = dict(platform_providers)
        self.timeout_seconds = max(10.0, min(float(timeout_seconds), 60.0))

    def capabilities(self) -> tuple[bool, str | None]:
        try:
            playwright_spec = importlib.util.find_spec("playwright.sync_api")
        except ModuleNotFoundError:
            playwright_spec = None
        if playwright_spec is None:
            return False, "缺少 Playwright Python 依赖，请安装后重启后端。"
        return True, None

    def capabilities_for(self, platform: Platform) -> tuple[bool, str | None]:
        if platform == Platform.DOUYIN:
            return self.douyin_parser.capabilities()
        available, message = self.capabilities()
        if not available:
            return available, message
        provider = self.platform_providers.get(platform)
        if provider is None:
            return False, f"{platform_label(platform)}链接解析器未配置。"
        status = getattr(provider, "session_status", lambda: None)()
        if status is None or not status.ready_to_crawl:
            return False, (
                status.message
                if status is not None
                else f"请先连接{platform_label(platform)}专用浏览器。"
            )
        return True, None

    def parse(self, share_text: str) -> ParsedPlatformLink:
        return parse_platform_share_text(share_text)

    def resolve(self, share_text: str) -> ParsedPlatformMedia:
        link = self.parse(share_text)
        if link.platform == Platform.DOUYIN:
            media = self.douyin_parser.resolve(share_text)
            return ParsedPlatformMedia(
                platform=Platform.DOUYIN,
                share_url=media.share_url,
                work_id=media.work_id,
                media_url=media.media_url,
                title=media.title,
            )
        available, message = self.capabilities_for(link.platform)
        if not available:
            raise PlatformLinkParserError(
                message or f"{platform_label(link.platform)}浏览器未就绪。",
                platform=link.platform,
                work_id=link.work_id,
            )
        if not _BROWSER_LOCK.acquire(timeout=2):
            raise PlatformLinkParserError(
                "已有平台链接解析任务正在运行，请稍后再试。",
                platform=link.platform,
                work_id=link.work_id,
            )
        try:
            return self._resolve_with_connected_browser(link)
        finally:
            _BROWSER_LOCK.release()

    def _resolve_with_connected_browser(
        self, link: ParsedPlatformLink
    ) -> ParsedPlatformMedia:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        provider = self.platform_providers[link.platform]
        endpoint = f"http://127.0.0.1:{provider.debug_port}"
        captured: dict[str, str] = {}
        page = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(
                    endpoint,
                    timeout=int(self.timeout_seconds * 1000),
                )
                if not browser.contexts:
                    raise PlatformLinkParserError(
                        f"{platform_label(link.platform)}浏览器没有可用会话。",
                        platform=link.platform,
                        work_id=link.work_id,
                    )
                page = browser.contexts[0].new_page()
                try:
                    timeout_ms = int(self.timeout_seconds * 1000)
                    page.set_default_timeout(timeout_ms)
                    page.set_default_navigation_timeout(timeout_ms)

                    def capture_response(response) -> None:
                        try:
                            content_type = response.headers.get(
                                "content-type", ""
                            ).casefold()
                            if (
                                not captured.get("media_url")
                                and content_type.startswith("video/")
                                and response.url.startswith("https://")
                                and ".m3u8" not in response.url.casefold()
                            ):
                                captured["media_url"] = response.url
                            if "json" in content_type or self._is_media_api_url(
                                link.platform, response.url
                            ):
                                self._capture_media_payload(
                                    response.json(), link.platform, captured
                                )
                        except Exception:
                            return

                    page.on("response", capture_response)
                    response = page.goto(
                        link.share_url, wait_until="domcontentloaded"
                    )
                    if response is not None and response.status in {403, 412, 429}:
                        raise PlatformLinkParserError(
                            f"{platform_label(link.platform)}返回 {response.status}，已停止解析。",
                            platform=link.platform,
                            work_id=link.work_id,
                        )
                    page.wait_for_timeout(5_000)
                    check_block = getattr(provider, "_raise_for_visible_block", None)
                    if callable(check_block):
                        try:
                            check_block(page)
                        except Exception as exc:
                            raise PlatformLinkParserError(
                                str(exc),
                                platform=link.platform,
                                work_id=link.work_id,
                            ) from exc
                    video = page.locator("video")
                    if video.count() > 0:
                        media_url = video.first.evaluate(
                            "node => node.currentSrc || node.src || ''"
                        )
                        if (
                            isinstance(media_url, str)
                            and media_url.startswith("https://")
                            and ".m3u8" not in media_url.casefold()
                        ):
                            captured["media_url"] = media_url
                    final_url = page.url
                    final_link = parse_platform_share_text(final_url)
                    if final_link.platform != link.platform:
                        raise PlatformLinkParserError(
                            "分享链接跳转到了其他平台，已停止解析。",
                            platform=link.platform,
                            work_id=link.work_id,
                        )
                    work_id = (
                        captured.get("work_id")
                        or final_link.work_id
                        or link.work_id
                    )
                    title = captured.get("title") or self._clean_page_title(
                        page.title(), link.platform
                    )
                finally:
                    try:
                        page.close()
                    except PlaywrightError:
                        pass
                    page = None
        except PlatformLinkParserError:
            raise
        except PlaywrightError as exc:
            raise PlatformLinkParserError(
                f"{platform_label(link.platform)}浏览器连接或页面读取失败。",
                platform=link.platform,
                work_id=link.work_id,
            ) from exc
        media_url = captured.get("media_url")
        if not media_url:
            raise PlatformLinkParserError(
                "该分享页没有返回可转写的视频流；可能是图文内容、链接失效或平台要求人工验证。",
                platform=link.platform,
                work_id=work_id,
            )
        if not work_id:
            raise PlatformLinkParserError(
                "页面未返回有效作品 ID，无法创建转写任务。",
                platform=link.platform,
            )
        return ParsedPlatformMedia(
            platform=link.platform,
            share_url=final_url,
            work_id=work_id,
            media_url=media_url,
            title=(title or f"{platform_label(link.platform)}作品 {work_id}")[:200],
        )

    @staticmethod
    def _is_media_api_url(platform: Platform, url: str) -> bool:
        normalized = str(url or "").casefold()
        if platform == Platform.BILIBILI:
            return "bilibili.com" in normalized and "/playurl" in normalized
        if platform == Platform.XIAOHONGSHU:
            return "xiaohongshu.com" in normalized and "/feed" in normalized
        return "kuaishou.com" in normalized and "/graphql" in normalized

    @classmethod
    def _capture_media_payload(
        cls,
        payload: Any,
        platform: Platform,
        captured: dict[str, str],
    ) -> None:
        if not isinstance(payload, dict):
            return
        if platform == Platform.BILIBILI:
            cls._capture_bilibili_payload(payload, captured)
            return
        allowed_keys = _MEDIA_KEYS.get(platform, set())

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized_key = str(key).casefold()
                    if (
                        normalized_key in _TITLE_KEYS
                        and isinstance(child, str)
                        and child.strip()
                        and not captured.get("title")
                    ):
                        captured["title"] = child.strip()
                    if normalized_key in allowed_keys:
                        cls._capture_https_value(child, captured)
                    if not captured.get("media_url") or not captured.get("title"):
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    if not captured.get("media_url") or not captured.get("title"):
                        walk(child)

        walk(payload)

    @staticmethod
    def _capture_https_value(value: Any, captured: dict[str, str]) -> None:
        candidates = [value] if isinstance(value, str) else value
        if not isinstance(candidates, list):
            return
        for candidate in candidates:
            if (
                isinstance(candidate, str)
                and candidate.startswith("https://")
                and ".m3u8" not in candidate.casefold()
            ):
                captured["media_url"] = candidate
                return

    @staticmethod
    def _capture_bilibili_payload(
        payload: dict[str, Any], captured: dict[str, str]
    ) -> None:
        data = payload.get("data")
        if not isinstance(data, dict):
            return
        durl = data.get("durl")
        if isinstance(durl, list):
            for item in durl:
                if not isinstance(item, dict):
                    continue
                url = item.get("url")
                if isinstance(url, str) and url.startswith("https://"):
                    captured["media_url"] = url
                    break
        dash = data.get("dash")
        if not captured.get("media_url") and isinstance(dash, dict):
            audio = dash.get("audio")
            if isinstance(audio, list):
                for item in audio:
                    if not isinstance(item, dict):
                        continue
                    url = item.get("baseUrl") or item.get("base_url")
                    if isinstance(url, str) and url.startswith("https://"):
                        captured["media_url"] = url
                        break
        bvid = data.get("bvid")
        if isinstance(bvid, str) and re.fullmatch(r"BV[a-zA-Z0-9]+", bvid):
            captured["work_id"] = bvid
        title = data.get("title")
        if isinstance(title, str) and title.strip():
            captured["title"] = title.strip()

    @staticmethod
    def _clean_page_title(title: str, platform: Platform) -> str:
        cleaned = " ".join(str(title or "").split())
        suffixes = {
            Platform.XIAOHONGSHU: (" - 小红书", "｜小红书"),
            Platform.KUAISHOU: (" - 快手", "｜快手"),
            Platform.BILIBILI: ("_哔哩哔哩_bilibili", " - 哔哩哔哩"),
        }
        for suffix in suffixes.get(platform, ()):
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)].strip()
        return cleaned
