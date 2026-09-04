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
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import httpx

from src.adapters.douyin_parser import (
    DouyinParserError,
    LocalDouyinBrowserParserClient,
    parse_douyin_share_text,
)
from src.adapters.browser_window import reveal_browser_window
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
_XIAOHONGSHU_BROWSER_MEDIA_MAX_BYTES = 300 * 1024 * 1024


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
    browser_user_agent: str | None = None
    media_bytes: bytes | None = None
    media_type: str | None = None

    @property
    def media_request_headers(self) -> dict[str, str]:
        return {
            "Referer": (
                self.share_url
                if self.platform == Platform.BILIBILI
                else _PLATFORM_HOME[self.platform]
            ),
            "User-Agent": self.browser_user_agent or _BROWSER_USER_AGENT,
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


def _is_xiaohongshu_search_page(url: str) -> bool:
    """Accept both /search_result and /search_result/ browser URL variants."""
    return urlparse(str(url or "")).path.rstrip("/") == "/search_result"


def _xiaohongshu_needs_search_recovery(link: ParsedPlatformLink) -> bool:
    return bool(
        link.platform == Platform.XIAOHONGSHU
        and "xsec_token" not in parse_qs(urlparse(link.share_url).query)
    )


class LocalPlatformLinkParserClient:
    """Single-link parser combining Douyin and dedicated platform browsers."""

    def __init__(
        self,
        *,
        douyin_parser: LocalDouyinBrowserParserClient,
        platform_providers: dict[Platform, Any],
        timeout_seconds: float = 35.0,
        http_client_factory: Any = httpx.Client,
    ) -> None:
        self.douyin_parser = douyin_parser
        self.platform_providers = dict(platform_providers)
        self.timeout_seconds = max(10.0, min(float(timeout_seconds), 60.0))
        self.http_client_factory = http_client_factory
        self._xiaohongshu_media_cache: dict[
            str, tuple[float, ParsedPlatformMedia]
        ] = {}
        self._xiaohongshu_media_cache_lock = threading.Lock()

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

    def _ensure_session_for_resolution(
        self, platform: Platform
    ) -> tuple[bool, str | None]:
        """Restart one persisted browser profile once before resolving a link."""
        available, message = self.capabilities_for(platform)
        if available or platform == Platform.DOUYIN:
            return available, message
        provider = self.platform_providers.get(platform)
        if provider is None:
            return available, message
        status = getattr(provider, "session_status", lambda: None)()
        if status is None or bool(getattr(status, "running", False)):
            return available, message
        start = (
            getattr(provider, "start_public_browser", None)
            if bool(getattr(provider, "anonymous_only", False))
            else getattr(provider, "start_login_browser", None)
        )
        if not callable(start):
            return available, message
        try:
            start()
        except Exception:
            # The provider status below remains the user-facing source of truth;
            # never loop browser launches or hide a real login/verification gate.
            pass
        return self.capabilities_for(platform)

    def parse(self, share_text: str) -> ParsedPlatformLink:
        return parse_platform_share_text(share_text)

    def resolve(
        self,
        share_text: str,
        *,
        search_keyword: str | None = None,
        include_media_bytes: bool = False,
    ) -> ParsedPlatformMedia:
        link = self.parse(share_text)
        if link.platform == Platform.XIAOHONGSHU and link.work_id:
            cached = self._get_cached_xiaohongshu_media(link.work_id)
            if cached is not None and (
                not include_media_bytes or cached.media_bytes is not None
            ):
                return cached
        if link.platform == Platform.DOUYIN:
            media = self.douyin_parser.resolve(share_text)
            return ParsedPlatformMedia(
                platform=Platform.DOUYIN,
                share_url=media.share_url,
                work_id=media.work_id,
                media_url=media.media_url,
                title=media.title,
            )
        if link.platform == Platform.BILIBILI and link.work_id:
            try:
                return self._resolve_bilibili_public(link)
            except (httpx.HTTPError, PlatformLinkParserError, ValueError):
                # The visible browser remains a no-cost fallback when B站临时
                # limits its public metadata endpoint.
                pass
        available, message = self._ensure_session_for_resolution(link.platform)
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
            return self._resolve_with_connected_browser(
                link,
                search_keyword=search_keyword,
                include_media_bytes=include_media_bytes,
            )
        finally:
            _BROWSER_LOCK.release()

    def open_in_connected_browser(
        self, share_text: str, *, search_keyword: str | None = None
    ) -> ParsedPlatformLink:
        """Navigate the existing authorized browser to one saved share link.

        This is a viewing action, not media resolution: it reuses the platform
        browser page, preserves its login session, and leaves the page open for
        the customer to inspect.  It never creates a separate app popup or
        returns a signed media URL to the frontend.
        """
        link = self.parse(share_text)
        if link.platform != Platform.XIAOHONGSHU:
            raise PlatformLinkParserError(
                "当前浏览器查看入口仅支持小红书候选。",
                platform=link.platform,
                work_id=link.work_id,
            )
        available, message = self._ensure_session_for_resolution(link.platform)
        if not available:
            raise PlatformLinkParserError(
                message or "小红书素材浏览器未就绪。",
                platform=link.platform,
                work_id=link.work_id,
            )
        if not _BROWSER_LOCK.acquire(timeout=2):
            raise PlatformLinkParserError(
                "已有小红书解析任务正在运行，请稍后再试。",
                platform=link.platform,
                work_id=link.work_id,
            )
        try:
            return self._open_xiaohongshu_in_connected_browser(
                link, search_keyword=search_keyword
            )
        finally:
            _BROWSER_LOCK.release()

    def _open_xiaohongshu_in_connected_browser(
        self,
        link: ParsedPlatformLink,
        *,
        search_keyword: str | None = None,
    ) -> ParsedPlatformLink:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        provider = self.platform_providers[Platform.XIAOHONGSHU]
        endpoint = f"http://127.0.0.1:{provider.debug_port}"
        page = None
        owns_page = False
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(
                    endpoint,
                    timeout=int(self.timeout_seconds * 1000),
                )
                if not browser.contexts:
                    raise PlatformLinkParserError(
                        "小红书素材浏览器没有可用会话。",
                        platform=link.platform,
                        work_id=link.work_id,
                    )
                context = browser.contexts[0]
                captured: dict[str, str] = {}

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
                            Platform.XIAOHONGSHU, response.url
                        ):
                            self._capture_media_payload(
                                response.json(), Platform.XIAOHONGSHU, captured
                            )
                    except Exception:
                        return

                existing_target = self._existing_xiaohongshu_detail_page(
                    context, link
                )
                if existing_target is not None:
                    page, final_link = existing_target
                    if self._xiaohongshu_access_error(page) is None:
                        try:
                            page.bring_to_front()
                        except PlaywrightError:
                            pass
                        self._cache_xiaohongshu_page_media(
                            page, final_link, captured
                        )
                        if self._wait_xiaohongshu_page_content(page, captured):
                            reveal_browser_window(provider.debug_port)
                            return final_link
                if "xsec_token" not in parse_qs(urlparse(link.share_url).query):
                    opened_target = self._open_xiaohongshu_search_result(
                        context,
                        link,
                        search_keyword=search_keyword,
                        response_callback=capture_response,
                    )
                    if opened_target is not None:
                        page, final_link = opened_target
                        try:
                            page.bring_to_front()
                        except PlaywrightError:
                            pass
                        self._cache_xiaohongshu_page_media(
                            page, final_link, captured
                        )
                        reveal_browser_window(provider.debug_port)
                        return final_link
                    if search_keyword:
                        raise PlatformLinkParserError(
                            "小红书搜索结果中未找到这条作品，已停止继续扫描。",
                            platform=link.platform,
                            work_id=link.work_id,
                        )
                # 新抓取候选已经保存平台生成的完整入口时直接打开；只有旧裸链接
                # 或失效入口才回到搜索上下文恢复一次，避免每次点击都重新扫描。
                target_url = self._xiaohongshu_context_url(context, link)
                page_host = str(
                    getattr(getattr(provider, "spec", None), "page_host", "xiaohongshu.com")
                ).casefold()
                search_pages = [
                    existing_page
                    for existing_page in list(context.pages)
                    if _is_xiaohongshu_search_page(str(existing_page.url or ""))
                ]
                for existing_page in reversed(list(context.pages)):
                    current_url = str(existing_page.url or "")
                    if (
                        page_host in current_url.casefold()
                        and existing_page not in search_pages
                    ):
                        page = existing_page
                        break
                if page is None:
                    page = context.new_page()
                    owns_page = True
                page.set_default_navigation_timeout(
                    min(int(self.timeout_seconds * 1000), 15_000)
                )
                listener_page = page
                listener_page.on("response", capture_response)

                def navigate_once(url: str) -> ParsedPlatformLink:
                    response = page.goto(url, wait_until="domcontentloaded")
                    if response is not None and response.status in {403, 404, 412, 429}:
                        raise PlatformLinkParserError(
                            f"小红书页面返回 {response.status}，请在素材浏览器中确认登录状态。",
                            platform=link.platform,
                            work_id=link.work_id,
                        )
                    access_error = self._xiaohongshu_access_error(page)
                    if access_error:
                        raise PlatformLinkParserError(
                            access_error,
                            platform=link.platform,
                            work_id=link.work_id,
                        )
                    if not self._wait_xiaohongshu_page_content(page, captured):
                        raise PlatformLinkParserError(
                            "小红书详情页没有加载出视频内容，请保持登录后重试。",
                            platform=link.platform,
                            work_id=link.work_id,
                        )
                    final_link = parse_platform_share_text(str(page.url or url))
                    if (
                        final_link.platform != link.platform
                        or final_link.work_id != link.work_id
                    ):
                        raise PlatformLinkParserError(
                            "小红书页面跳转的作品与所选候选不一致，已停止打开。",
                            platform=link.platform,
                            work_id=link.work_id,
                        )
                    return final_link

                try:
                    final_link = navigate_once(target_url)
                except PlatformLinkParserError as direct_error:
                    opened_target = self._open_xiaohongshu_search_result(
                        context,
                        link,
                        search_keyword=search_keyword,
                        response_callback=capture_response,
                    )
                    if opened_target is None:
                        raise direct_error
                    page, final_link = opened_target
                finally:
                    try:
                        listener_page.remove_listener("response", capture_response)
                    except Exception:
                        pass
                self._cache_xiaohongshu_page_media(page, final_link, captured)
                reveal_browser_window(provider.debug_port)
                return final_link
        except PlatformLinkParserError:
            if owns_page and page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            raise
        except (PlaywrightError, OSError) as exc:
            if owns_page and page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            raise PlatformLinkParserError(
                "小红书素材浏览器打开失败，请确认专用浏览器仍在运行。",
                platform=link.platform,
                work_id=link.work_id,
            ) from exc

    def _resolve_bilibili_public(
        self,
        link: ParsedPlatformLink,
    ) -> ParsedPlatformMedia:
        work_id = link.work_id or ""
        headers = {
            "Referer": f"https://www.bilibili.com/video/{work_id}",
            "User-Agent": _BROWSER_USER_AGENT,
        }
        with self.http_client_factory(
            timeout=min(self.timeout_seconds, 20.0),
            headers=headers,
        ) as client:
            view_response = client.get(
                "https://api.bilibili.com/x/web-interface/view",
                params={"bvid": work_id},
            )
            view_response.raise_for_status()
            view_payload = view_response.json()
            view_data = view_payload.get("data")
            if (
                view_payload.get("code") != 0
                or not isinstance(view_data, dict)
                or str(view_data.get("bvid") or "").casefold() != work_id.casefold()
            ):
                raise PlatformLinkParserError(
                    "B站没有返回目标作品信息。",
                    platform=link.platform,
                    work_id=work_id,
                )
            cid = view_data.get("cid")
            if not isinstance(cid, int) or cid <= 0:
                raise PlatformLinkParserError(
                    "B站没有返回目标作品的音频编号。",
                    platform=link.platform,
                    work_id=work_id,
                )
            play_response = client.get(
                "https://api.bilibili.com/x/player/playurl",
                params={
                    "bvid": work_id,
                    "cid": cid,
                    "fnval": 16,
                },
            )
            play_response.raise_for_status()
            play_payload = play_response.json()
        play_data = play_payload.get("data")
        dash = play_data.get("dash") if isinstance(play_data, dict) else None
        audio_items = dash.get("audio") if isinstance(dash, dict) else None
        media_url = ""
        if isinstance(audio_items, list):
            for item in audio_items:
                if not isinstance(item, dict):
                    continue
                candidates = [
                    item.get("baseUrl") or item.get("base_url"),
                    *(item.get("backupUrl") or item.get("backup_url") or []),
                ]
                for candidate in candidates:
                    if not isinstance(candidate, str) or not candidate.startswith("https://"):
                        continue
                    parsed_candidate = urlparse(candidate)
                    try:
                        candidate_port = parsed_candidate.port
                    except ValueError:
                        continue
                    if (
                        parsed_candidate.hostname
                        and not parsed_candidate.username
                        and not parsed_candidate.password
                        and candidate_port in {None, 443}
                    ):
                        media_url = candidate
                        break
                if media_url:
                    break
        if play_payload.get("code") != 0 or not media_url:
            raise PlatformLinkParserError(
                "B站没有返回可转写的独立音频流。",
                platform=link.platform,
                work_id=work_id,
            )
        title = str(view_data.get("title") or f"B站作品 {work_id}").strip()
        return ParsedPlatformMedia(
            platform=link.platform,
            share_url=f"https://www.bilibili.com/video/{work_id}",
            work_id=work_id,
            media_url=media_url,
            title=title[:200],
        )

    def _resolve_with_connected_browser(
        self,
        link: ParsedPlatformLink,
        *,
        search_keyword: str | None = None,
        include_media_bytes: bool = False,
    ) -> ParsedPlatformMedia:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        provider = self.platform_providers[link.platform]
        endpoint = f"http://127.0.0.1:{provider.debug_port}"
        captured: dict[str, str] = {}
        media_payloads: list[Any] = []
        page = None
        owns_page = False
        media_bytes: bytes | None = None
        media_type: str | None = None
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
                context = browser.contexts[0]
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
                            payload = response.json()
                            if link.platform == Platform.KUAISHOU:
                                if len(media_payloads) < 50:
                                    media_payloads.append(payload)
                            else:
                                self._capture_media_payload(
                                    payload, link.platform, captured
                                )
                    except Exception:
                        return

                existing_target = None
                if link.platform == Platform.XIAOHONGSHU:
                    existing_target = self._existing_xiaohongshu_detail_page(
                        context, link
                    )
                    if (
                        existing_target is not None
                        and self._xiaohongshu_access_error(existing_target[0])
                        is not None
                    ):
                        existing_target = None
                if existing_target is not None:
                    page, _ = existing_target
                    target_url = str(page.url)
                else:
                    opened_target = None
                    needs_search_recovery = _xiaohongshu_needs_search_recovery(link)
                    if needs_search_recovery:
                        opened_target = self._open_xiaohongshu_search_result(
                            context,
                            link,
                            search_keyword=search_keyword,
                            response_callback=capture_response,
                        )
                    if opened_target is not None:
                        page, _ = opened_target
                        target_url = str(page.url)
                    elif needs_search_recovery and search_keyword:
                        raise PlatformLinkParserError(
                            "小红书搜索结果中未找到这条作品，已停止继续扫描。",
                            platform=link.platform,
                            work_id=link.work_id,
                        )
                    else:
                        target_url = self._xiaohongshu_context_url(context, link)
                        page = context.new_page()
                        owns_page = True
                try:
                    timeout_ms = int(self.timeout_seconds * 1000)
                    page.set_default_timeout(timeout_ms)
                    page.set_default_navigation_timeout(timeout_ms)
                    browser_user_agent = self._safe_browser_user_agent(
                        page.evaluate("navigator.userAgent")
                    )

                    response = None
                    if owns_page:
                        page.on("response", capture_response)
                        response = page.goto(target_url, wait_until="domcontentloaded")
                    blocked_statuses = {403, 412, 429}
                    if link.platform == Platform.XIAOHONGSHU:
                        blocked_statuses.add(404)
                    if response is not None and response.status in blocked_statuses:
                        if link.platform == Platform.XIAOHONGSHU and owns_page:
                            opened_target = self._open_xiaohongshu_search_result(
                                context,
                                link,
                                search_keyword=search_keyword,
                            )
                            if opened_target is not None:
                                try:
                                    page.close()
                                except PlaywrightError:
                                    pass
                                page, _ = opened_target
                                owns_page = False
                                captured.clear()
                                media_payloads.clear()
                                target_url = str(page.url)
                                response = None
                        if response is not None and response.status in blocked_statuses:
                            raise PlatformLinkParserError(
                                f"{platform_label(link.platform)}返回 {response.status}，已停止解析。",
                                platform=link.platform,
                                work_id=link.work_id,
                            )
                    # Kuaishou needs a conservative initial hydration window. For
                    # Xiaohongshu, continue as soon as its response or player exists;
                    # retain the same bounded wait only as a slow-network fallback.
                    if link.platform == Platform.XIAOHONGSHU:
                        waited_ms = 0
                        while waited_ms < 5_000 and not captured.get("media_url"):
                            try:
                                if page.locator("video").count() > 0:
                                    break
                            except PlaywrightError:
                                pass
                            page.wait_for_timeout(250)
                            waited_ms += 250
                    else:
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
                    final_url = page.url
                    if link.platform == Platform.XIAOHONGSHU:
                        access_error = self._xiaohongshu_access_error(page)
                        if access_error:
                            raise PlatformLinkParserError(
                                access_error,
                                platform=link.platform,
                                work_id=link.work_id,
                            )
                    final_link = parse_platform_share_text(final_url)
                    if final_link.platform != link.platform:
                        raise PlatformLinkParserError(
                            "分享链接跳转到了其他平台，已停止解析。",
                            platform=link.platform,
                            work_id=link.work_id,
                        )
                    if (
                        link.platform == Platform.XIAOHONGSHU
                        and link.work_id
                        and final_link.work_id != link.work_id
                    ):
                        raise PlatformLinkParserError(
                            "小红书页面跳转的作品与所选候选不一致，已停止转写。",
                            platform=link.platform,
                            work_id=link.work_id,
                        )
                    if link.platform == Platform.KUAISHOU:
                        expected_work_id = final_link.work_id or link.work_id
                        page_title = self._clean_page_title(page.title(), link.platform)
                        if expected_work_id:
                            media_url = self._wait_for_kuaishou_page_video_source(
                                page,
                                timeout_ms=min(timeout_ms, 12_000),
                            )
                            if self._capture_kuaishou_page_video(
                                captured,
                                expected_work_id=expected_work_id,
                                media_url=media_url,
                                title=page_title,
                            ):
                                page.locator("video").first.evaluate(
                                    "node => node.pause()"
                                )
                        if expected_work_id and not captured.get("media_url"):
                            for payload in media_payloads:
                                self._capture_media_payload(
                                    payload,
                                    link.platform,
                                    captured,
                                    expected_work_id=expected_work_id,
                                )
                                if captured.get("media_url"):
                                    break
                    else:
                        if link.platform == Platform.XIAOHONGSHU and not captured.get(
                            "media_url"
                        ):
                            # 小红书网页播放器常把真实地址藏在结构化状态里，
                            # DOM 只暴露 blob:，不能把 blob 当作可转写媒体。
                            self._capture_xiaohongshu_page_state(page, captured)
                        video = page.locator("video")
                        if not captured.get("media_url") and video.count() > 0:
                            media_url = video.first.evaluate(
                                "node => node.currentSrc || node.src || ''"
                            )
                            if (
                                isinstance(media_url, str)
                                and media_url.startswith("https://")
                                and ".m3u8" not in media_url.casefold()
                            ):
                                captured["media_url"] = media_url
                    work_id = (
                        captured.get("work_id") or final_link.work_id or link.work_id
                    )
                    title = captured.get("title") or self._clean_page_title(
                        page.title(), link.platform
                    )
                    if include_media_bytes and link.platform == Platform.XIAOHONGSHU:
                        resolved_media_url = captured.get("media_url")
                        if not resolved_media_url:
                            raise PlatformLinkParserError(
                                "小红书页面已打开，但没有确认可读取的视频流。",
                                platform=Platform.XIAOHONGSHU,
                                work_id=work_id,
                            )
                        media_bytes, media_type = self._fetch_media_with_browser_context(
                            context,
                            resolved_media_url,
                            final_url,
                            browser_user_agent,
                        )
                finally:
                    if owns_page:
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
            if link.platform == Platform.KUAISHOU:
                raise PlatformLinkParserError(
                    "未能确认视频流属于目标快手作品，系统已停止解析。",
                    platform=link.platform,
                    work_id=work_id,
                )
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
            browser_user_agent=browser_user_agent,
            media_bytes=media_bytes,
            media_type=media_type,
        )

    @staticmethod
    def _fetch_media_with_browser_context(
        context: Any,
        media_url: str,
        referer: str,
        browser_user_agent: str | None,
    ) -> tuple[bytes, str]:
        """Read XHS media with the already authorized browser context.

        A CDN URL captured from a logged-in page is not necessarily readable by
        a backend urllib request: XHS can require the browser context's
        cookies/session state in addition to the signed URL.  Browser-context
        requests reuse that session without exporting cookies to the backend.
        """

        response = None
        try:
            response = context.request.get(
                media_url,
                headers={
                    "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.1",
                    "Referer": referer,
                    "User-Agent": browser_user_agent or _BROWSER_USER_AGENT,
                },
                timeout=60_000,
                fail_on_status_code=False,
            )
            if response.status not in {200, 206}:
                raise PlatformLinkParserError(
                    "小红书视频已打开，但登录浏览器读取视频失败，请保持登录后重试。",
                    platform=Platform.XIAOHONGSHU,
                )
            content_type = str(response.headers.get("content-type", "")).split(
                ";", 1
            )[0].strip().casefold()
            if not (
                content_type.startswith("video/")
                or content_type in {"audio/mp4", "application/octet-stream"}
            ):
                raise PlatformLinkParserError(
                    "小红书返回的内容不是可识别的视频文件，无法转写。",
                    platform=Platform.XIAOHONGSHU,
                )
            raw_length = str(response.headers.get("content-length", "") or "")
            try:
                content_length = int(raw_length)
            except ValueError:
                content_length = 0
            if content_length > _XIAOHONGSHU_BROWSER_MEDIA_MAX_BYTES:
                raise PlatformLinkParserError(
                    "小红书视频超过 300MB，无法直接转写。",
                    platform=Platform.XIAOHONGSHU,
                )
            content = response.body()
            if len(content) > _XIAOHONGSHU_BROWSER_MEDIA_MAX_BYTES:
                raise PlatformLinkParserError(
                    "小红书视频超过 300MB，无法直接转写。",
                    platform=Platform.XIAOHONGSHU,
                )
            if not content:
                raise PlatformLinkParserError(
                    "小红书视频返回了空文件，无法转写。",
                    platform=Platform.XIAOHONGSHU,
                )
            return content, content_type or "video/mp4"
        except PlatformLinkParserError:
            raise
        except Exception as exc:
            raise PlatformLinkParserError(
                "小红书视频已打开，但登录浏览器读取视频失败，请保持登录后重试。",
                platform=Platform.XIAOHONGSHU,
            ) from exc
        finally:
            if response is not None:
                try:
                    response.dispose()
                except Exception:
                    pass

    @staticmethod
    def _xiaohongshu_context_url(
        context: Any,
        link: ParsedPlatformLink,
        *,
        force_search_context: bool = False,
    ) -> str:
        """Recover the tokenized URL by using the visible logged-in search card."""
        if link.platform != Platform.XIAOHONGSHU or not link.work_id:
            return link.share_url
        if (
            "xsec_token" in parse_qs(urlparse(link.share_url).query)
            and not force_search_context
        ):
            return link.share_url
        escaped_work_id = link.work_id.replace('"', "")
        selectors = (
            f'a[href*="/explore/{escaped_work_id}"]',
            f'a[href*="/discovery/item/{escaped_work_id}"]',
        )
        pages = list(getattr(context, "pages", ()) or ())
        for existing_page in reversed(pages):
            current_url = str(getattr(existing_page, "url", "") or "")
            is_search_page = _is_xiaohongshu_search_page(current_url)
            # 详情页会保留当前作品的旧授权链接，不能把它当成新的入口。
            # 测试替身可能没有 URL，因此仅在 URL 已知且明确不是搜索页时跳过。
            if current_url and not is_search_page:
                continue
            if (
                is_search_page
                and "xsec_token=" in current_url
                and not force_search_context
            ):
                try:
                    current_link = parse_platform_share_text(current_url)
                except PlatformLinkParserError:
                    current_link = None
                if current_link is not None and current_link.work_id == link.work_id:
                    return current_link.share_url
            if not force_search_context:
                for selector in selectors:
                    try:
                        hrefs = existing_page.locator(selector).evaluate_all(
                            "nodes => nodes.map(node => node.href || node.getAttribute('href') || '')"
                        )
                    except Exception:
                        continue
                    for href in hrefs if isinstance(hrefs, list) else ():
                        if not isinstance(href, str) or "xsec_token=" not in href:
                            continue
                        try:
                            candidate = parse_platform_share_text(href)
                        except PlatformLinkParserError:
                            continue
                        if (
                            candidate.platform == Platform.XIAOHONGSHU
                            and candidate.work_id == link.work_id
                        ):
                            return candidate.share_url
            if current_url and not is_search_page:
                continue
            for round_index in range(14):
                for selector in selectors:
                    try:
                        anchor = existing_page.locator(selector)
                        card = existing_page.locator("section.note-item").filter(
                            has=anchor
                        )
                        if card.count() != 1:
                            continue
                        previous_url = str(existing_page.url)
                        card.click(timeout=5_000)
                        existing_page.wait_for_timeout(800)
                        routed_url = str(existing_page.url)
                        try:
                            routed_link = parse_platform_share_text(routed_url)
                        except PlatformLinkParserError:
                            routed_link = None
                        if previous_url != routed_url:
                            try:
                                existing_page.go_back(
                                    wait_until="domcontentloaded", timeout=5_000
                                )
                            except Exception:
                                pass
                        if (
                            routed_link is not None
                            and routed_link.platform == Platform.XIAOHONGSHU
                            and routed_link.work_id == link.work_id
                            and "xsec_token=" in routed_link.share_url
                        ):
                            return routed_link.share_url
                    except Exception:
                        continue
                try:
                    if round_index == 0:
                        existing_page.evaluate("window.scrollTo(0, 0)")
                    else:
                        existing_page.evaluate(
                            "window.scrollBy(0, Math.max(window.innerHeight * 1.5, 900))"
                        )
                    existing_page.wait_for_timeout(350)
                except Exception:
                    break
        return link.share_url

    @staticmethod
    def _existing_xiaohongshu_detail_page(
        context: Any, link: ParsedPlatformLink
    ) -> tuple[Any, ParsedPlatformLink] | None:
        """Return an already-open exact detail page without navigating it again."""
        if link.platform != Platform.XIAOHONGSHU or not link.work_id:
            return None
        for existing_page in reversed(list(getattr(context, "pages", ()) or ())):
            current_url = str(getattr(existing_page, "url", "") or "")
            if _is_xiaohongshu_search_page(current_url):
                continue
            try:
                current_link = parse_platform_share_text(current_url)
            except PlatformLinkParserError:
                continue
            if (
                current_link.platform == Platform.XIAOHONGSHU
                and current_link.work_id == link.work_id
            ):
                return existing_page, current_link
        return None

    @staticmethod
    def _open_xiaohongshu_search_result(
        context: Any,
        link: ParsedPlatformLink,
        *,
        search_keyword: str | None = None,
        response_callback: Any | None = None,
    ) -> tuple[Any, ParsedPlatformLink] | None:
        """Open the exact visible search card and keep that generated page alive."""
        if link.platform != Platform.XIAOHONGSHU or not link.work_id:
            return None
        escaped_work_id = link.work_id.replace('"', "")
        selectors = (
            f'a[href*="/explore/{escaped_work_id}"]',
            f'a[href*="/discovery/item/{escaped_work_id}"]',
        )
        search_pages = [
            page
            for page in reversed(list(getattr(context, "pages", ()) or ()))
            if _is_xiaohongshu_search_page(str(getattr(page, "url", "") or ""))
        ]
        if not search_pages:
            # Opening one result turns the material browser's search tab into a
            # detail page.  For the next candidate, restore that same browser
            # history entry before considering a fresh keyword search.  This keeps
            # the already-loaded result set and avoids a new scan on every click.
            for existing_page in reversed(
                list(getattr(context, "pages", ()) or ())
            ):
                current_url = str(getattr(existing_page, "url", "") or "")
                try:
                    current_link = parse_platform_share_text(current_url)
                except PlatformLinkParserError:
                    continue
                if current_link.platform != Platform.XIAOHONGSHU:
                    continue
                try:
                    existing_page.go_back(
                        wait_until="domcontentloaded", timeout=5_000
                    )
                    existing_page.wait_for_timeout(250)
                except Exception:
                    continue
                if _is_xiaohongshu_search_page(
                    str(getattr(existing_page, "url", "") or "")
                ):
                    search_pages.append(existing_page)
                    break
        if search_keyword:
            # Legacy candidates may be opened after the original search tab has
            # moved elsewhere. Recreate only the recorded keyword search; the
            # exact work_id check below remains mandatory.
            keyword = str(search_keyword).strip()[:80]
            if keyword:
                search_page = None
                try:
                    target_visible = False
                    for existing_page in search_pages:
                        for selector in selectors:
                            anchor = existing_page.locator(selector)
                            card = existing_page.locator("section.note-item").filter(
                                has=anchor
                            )
                            if card.count() == 1:
                                target_visible = True
                                break
                        if target_visible:
                            break
                    if target_visible:
                        search_page = None
                    elif search_pages:
                        search_page = search_pages[0]
                    else:
                        search_page = context.new_page()
                    if search_page is None:
                        pass
                    else:
                        search_page.set_default_navigation_timeout(15_000)
                        response = search_page.goto(
                            "https://www.xiaohongshu.com/search_result/?"
                            f"keyword={quote(keyword, safe='')}&type=51"
                            "&source=web_search_result_notes",
                            wait_until="domcontentloaded",
                        )
                        if response is None or response.status < 400:
                            search_page.wait_for_timeout(1_200)
                            if search_page not in search_pages:
                                search_pages.append(search_page)
                        else:
                            if search_page not in search_pages:
                                search_page.close()
                except Exception:
                    if search_page is not None and search_page not in search_pages:
                        try:
                            search_page.close()
                        except Exception:
                            pass
        for search_page in search_pages:
            if response_callback is not None:
                search_page.on("response", response_callback)
            try:
                for round_index in range(14):
                    for selector in selectors:
                        try:
                            anchor = search_page.locator(selector)
                            card = search_page.locator("section.note-item").filter(
                                has=anchor
                            )
                            if card.count() == 1:
                                clickable = card
                            else:
                                # 小红书改版后可能保留精确链接，但不再使用
                                # section.note-item 外层容器；此时直接点该链接。
                                if anchor.count() < 1:
                                    continue
                                clickable = anchor.first
                            previous_url = str(search_page.url)
                            clickable.click(timeout=5_000)
                            search_page.wait_for_timeout(
                                1_200 if response_callback is not None else 800
                            )
                            try:
                                final_link = parse_platform_share_text(
                                    str(search_page.url)
                                )
                            except PlatformLinkParserError:
                                final_link = None
                            if (
                                final_link is not None
                                and final_link.platform == Platform.XIAOHONGSHU
                                and final_link.work_id == link.work_id
                            ):
                                if LocalPlatformLinkParserClient._wait_xiaohongshu_page_content(
                                    search_page
                                ):
                                    return search_page, final_link
                                try:
                                    search_page.go_back(
                                        wait_until="domcontentloaded", timeout=5_000
                                    )
                                except Exception:
                                    pass
                                return None
                            if str(search_page.url) != previous_url:
                                try:
                                    search_page.go_back(
                                        wait_until="domcontentloaded", timeout=5_000
                                    )
                                except Exception:
                                    return None
                        except Exception:
                            continue
                    try:
                        if round_index == 0:
                            search_page.evaluate("window.scrollTo(0, 0)")
                        else:
                            search_page.evaluate(
                                "window.scrollBy(0, Math.max(window.innerHeight * 1.5, 900))"
                            )
                        search_page.wait_for_timeout(350)
                    except Exception:
                        break
            finally:
                if response_callback is not None:
                    try:
                        search_page.remove_listener("response", response_callback)
                    except Exception:
                        pass
        return None

    @staticmethod
    def _wait_xiaohongshu_page_content(
        page: Any,
        captured: dict[str, str] | None = None,
        *,
        timeout_ms: int = 5_000,
    ) -> bool:
        """Wait for a video/detail page instead of treating a bare URL as success."""
        captured = captured or {}
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        video_probe_supported = False
        while True:
            if captured.get("media_url"):
                return True
            if LocalPlatformLinkParserClient._xiaohongshu_access_error(page):
                return False
            try:
                videos = page.locator("video")
                video_count = videos.count()
                video_probe_supported = True
                if video_count > 0:
                    return True
            except Exception:
                pass
            if not video_probe_supported:
                # Keep compatibility with lightweight browser test doubles and
                # older adapters that only expose URL navigation.
                return True
            if time.monotonic() >= deadline:
                return False
            try:
                page.wait_for_timeout(250)
            except Exception:
                return False

    def _get_cached_xiaohongshu_media(
        self, work_id: str
    ) -> ParsedPlatformMedia | None:
        now = time.monotonic()
        with self._xiaohongshu_media_cache_lock:
            for key, (expires_at, _) in list(
                self._xiaohongshu_media_cache.items()
            ):
                if expires_at <= now:
                    self._xiaohongshu_media_cache.pop(key, None)
            cached = self._xiaohongshu_media_cache.get(work_id)
            return cached[1] if cached is not None else None

    def _cache_xiaohongshu_page_media(
        self,
        page: Any,
        link: ParsedPlatformLink,
        captured: dict[str, str],
    ) -> None:
        if not link.work_id:
            return
        if not captured.get("media_url"):
            self._capture_xiaohongshu_page_state(page, captured)
        if not captured.get("media_url"):
            try:
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
            except Exception:
                pass
        media_url = captured.get("media_url")
        if not media_url:
            return
        try:
            browser_user_agent = self._safe_browser_user_agent(
                page.evaluate("navigator.userAgent")
            )
        except Exception:
            browser_user_agent = None
        title = captured.get("title")
        if not title:
            try:
                title = self._clean_page_title(page.title(), Platform.XIAOHONGSHU)
            except Exception:
                title = None
        media = ParsedPlatformMedia(
            platform=Platform.XIAOHONGSHU,
            share_url=link.share_url,
            work_id=link.work_id,
            media_url=media_url,
            title=(title or f"小红书作品 {link.work_id}")[:200],
            browser_user_agent=browser_user_agent,
        )
        with self._xiaohongshu_media_cache_lock:
            if len(self._xiaohongshu_media_cache) >= 32:
                oldest = min(
                    self._xiaohongshu_media_cache,
                    key=lambda key: self._xiaohongshu_media_cache[key][0],
                )
                self._xiaohongshu_media_cache.pop(oldest, None)
            self._xiaohongshu_media_cache[link.work_id] = (
                time.monotonic() + 120.0,
                media,
            )

    @staticmethod
    def _safe_browser_user_agent(value: object) -> str | None:
        user_agent = str(value or "").strip()
        if (
            not user_agent
            or len(user_agent) > 512
            or "\r" in user_agent
            or "\n" in user_agent
        ):
            return None
        return user_agent

    @staticmethod
    def _is_media_api_url(platform: Platform, url: str) -> bool:
        normalized = str(url or "").casefold()
        if platform == Platform.BILIBILI:
            return "bilibili.com" in normalized and "/playurl" in normalized
        if platform == Platform.XIAOHONGSHU:
            return "xiaohongshu.com" in normalized and "/feed" in normalized
        return "kuaishou.com" in normalized and "/graphql" in normalized

    @staticmethod
    def _xiaohongshu_access_error(page) -> str | None:
        """Turn an inaccessible-note page into an actionable error."""
        final_url = str(getattr(page, "url", "") or "")
        query = parse_qs(urlparse(final_url).query)
        if "300031" in query.get("error_code", []):
            return (
                "这条小红书笔记当前无法浏览，请在小红书 App 重新复制分享链接后重试，"
                "或上传已获授权的视频转写。"
            )
        try:
            body_text = str(page.locator("body").inner_text(timeout=1000) or "")
        except Exception:
            body_text = ""
        if "当前笔记暂时无法浏览" in body_text:
            return (
                "这条小红书笔记当前无法浏览，请在小红书 App 重新复制分享链接后重试，"
                "或上传已获授权的视频转写。"
            )
        return None

    @staticmethod
    def _may_capture_generic_video_response(platform: Platform) -> bool:
        """B站的普通 video 响应可能是无声画面，必须等待 playurl 音频。"""
        return platform not in {Platform.KUAISHOU, Platform.BILIBILI}

    @classmethod
    def _capture_media_payload(
        cls,
        payload: Any,
        platform: Platform,
        captured: dict[str, str],
        *,
        expected_work_id: str | None = None,
    ) -> None:
        if not isinstance(payload, dict):
            return
        if platform == Platform.BILIBILI:
            cls._capture_bilibili_payload(payload, captured)
            return
        if platform == Platform.KUAISHOU and expected_work_id:
            cls._capture_kuaishou_payload(
                payload,
                captured,
                expected_work_id=expected_work_id,
            )
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
    def _capture_xiaohongshu_page_state(page: Any, captured: dict[str, str]) -> None:
        """Extract a real XHS CDN video from the already authorized page.

        XHS frequently exposes only a ``blob:`` URL on the video element.  The
        hydrated page state or an inline state script can still contain the
        signed ``xhscdn.com`` MP4 URL.  This reads page-local state only; it does
        not export cookies, solve challenges, or manufacture a media URL.
        """

        try:
            state = page.evaluate(
                r"""
                () => {
                  const media = [];
                  const titles = [];
                  const seen = new WeakSet();
                  const addUrl = (value) => {
                    if (typeof value !== 'string' || !value.trim()) return;
                    const raw = value.trim()
                      .replace(/\\u002F/g, '/')
                      .replace(/\\\//g, '/');
                    try {
                      const url = new URL(raw, window.location.href);
                      const host = (url.hostname || '').toLowerCase();
                      const path = url.pathname || '';
                      if ((host === 'xhscdn.com' || host.endsWith('.xhscdn.com')) &&
                          (/\.mp4(?:$|[?#])/i.test(path) || /\/stream\//i.test(path))) {
                        media.push(url.href);
                      }
                    } catch (_) {}
                  };
                  const walk = (value, depth) => {
                    if (value == null || depth > 10) return;
                    if (typeof value === 'string') {
                      addUrl(value);
                      return;
                    }
                    if (typeof value !== 'object') return;
                    if (seen.has(value)) return;
                    seen.add(value);
                    if (!Array.isArray(value)) {
                      for (const [key, child] of Object.entries(value)) {
                        if (/^(title|displaytitle|desc|description|caption)$/i.test(key) &&
                            typeof child === 'string' && child.trim()) {
                          titles.push(child.trim());
                        }
                        walk(child, depth + 1);
                      }
                    } else {
                      for (const child of value) walk(child, depth + 1);
                    }
                  };
                  for (const root of [
                    window.__INITIAL_STATE__,
                    window.__UNIVERSAL_DATA_FOR_REHYDRATION__,
                    window.__NEXT_DATA__,
                  ]) walk(root, 0);
                  for (const script of Array.from(document.scripts || [])) {
                    const text = script.textContent || '';
                    if (!/xhscdn\.com|INITIAL_STATE|UNIVERSAL_DATA/i.test(text)) continue;
                    for (const match of text.match(/https?:\/\/[^"'\s<>]+/g) || []) {
                      addUrl(match);
                    }
                  }
                  return {
                    media_urls: Array.from(new Set(media)).slice(0, 20),
                    titles: Array.from(new Set(titles)).slice(0, 10),
                  };
                }
                """
            )
        except Exception:
            return
        if not isinstance(state, dict):
            return
        titles = state.get("titles")
        if not captured.get("title") and isinstance(titles, list):
            for value in titles:
                if isinstance(value, str) and value.strip():
                    captured["title"] = value.strip()
                    break
        media_urls = state.get("media_urls")
        if not isinstance(media_urls, list):
            return
        for value in media_urls:
            if not isinstance(value, str):
                continue
            parsed = urlparse(value)
            host = (parsed.hostname or '').casefold()
            if (
                parsed.scheme == 'https'
                and (host == 'xhscdn.com' or host.endswith('.xhscdn.com'))
                and ('.mp4' in parsed.path.casefold() or '/stream/' in parsed.path.casefold())
            ):
                captured["media_url"] = value
                return

    @classmethod
    def _capture_kuaishou_payload(
        cls,
        payload: dict[str, Any],
        captured: dict[str, str],
        *,
        expected_work_id: str,
    ) -> None:
        target = expected_work_id.casefold()
        id_keys = {"id", "photoid", "photo_id"}

        def matching_photo(value: Any) -> dict[str, Any] | None:
            if isinstance(value, dict):
                identifiers = {
                    str(child).casefold()
                    for key, child in value.items()
                    if str(key).casefold() in id_keys and isinstance(child, (str, int))
                }
                if target in identifiers:
                    return value
                for child in value.values():
                    match = matching_photo(child)
                    if match is not None:
                        return match
            elif isinstance(value, list):
                for child in value:
                    match = matching_photo(child)
                    if match is not None:
                        return match
            return None

        photo = matching_photo(payload)
        if photo is None:
            return

        scoped: dict[str, str] = {}
        cls._capture_media_payload(photo, Platform.KUAISHOU, scoped)
        media_url = scoped.get("media_url")
        if not media_url:
            return
        captured["media_url"] = media_url
        captured["work_id"] = expected_work_id
        if scoped.get("title"):
            captured["title"] = scoped["title"]

    @staticmethod
    def _capture_kuaishou_page_video(
        captured: dict[str, str],
        *,
        expected_work_id: str | None,
        media_url: Any,
        title: str,
    ) -> bool:
        """Bind the sole detail-page video to the work id from that exact URL.

        A visible page title is presentation metadata and may be empty while the
        player is already ready.  Requiring it caused valid Kuaishou videos to be
        rejected even though the exact work id and sole player were both known.
        """
        if (
            not expected_work_id
            or not isinstance(media_url, str)
            or not media_url.startswith("https://")
            or ".m3u8" in media_url.casefold()
        ):
            return False
        captured["media_url"] = media_url
        captured["work_id"] = expected_work_id
        if title.strip():
            captured["title"] = title.strip()
        return True

    @staticmethod
    def _wait_for_kuaishou_page_video_source(
        page: Any,
        *,
        timeout_ms: int,
    ) -> str:
        """Wait for the one visible player on the exact Kuaishou detail page.

        The work identity is still bound by the canonical detail-page URL in the
        caller.  This wait only removes the brittle assumption that ``currentSrc``
        is populated within a fixed five seconds.
        """

        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        try:
            page.wait_for_function(
                """
                () => {
                  const videos = Array.from(document.querySelectorAll('video'))
                    .filter((node) => {
                      const box = node.getBoundingClientRect();
                      const style = window.getComputedStyle(node);
                      return box.width > 0 && box.height > 0 &&
                        style.display !== 'none' && style.visibility !== 'hidden';
                    });
                  return videos.length === 1 &&
                    Boolean(videos[0].currentSrc || videos[0].src);
                }
                """,
                timeout=max(1_000, min(int(timeout_ms), 12_000)),
            )
        except PlaywrightTimeoutError:
            return ""
        videos = page.locator("video")
        if videos.count() != 1:
            return ""
        value = videos.first.evaluate("node => node.currentSrc || node.src || ''")
        return value if isinstance(value, str) else ""

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
