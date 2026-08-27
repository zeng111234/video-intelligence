"""小红书、快手、B站可见浏览器搜索的字段归一化测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import ProxyHandler

import pytest

from src.adapters.licensed import LicensedProviderError
from src.adapters.douyin_browser_search import BrowserSessionStatus
from src.adapters.platform_browser_search import LocalPlatformBrowserSearchProvider
from src.adapters import platform_browser_search as platform_browser_module
from src.models import Platform


def _provider(platform: Platform) -> LocalPlatformBrowserSearchProvider:
    return LocalPlatformBrowserSearchProvider(
        platform=platform,
        enabled=True,
        profile_dir=Path("data/test-browser-profile"),
        debug_port=19999,
        clock=lambda: datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
    )


def test_local_debug_status_bypasses_environment_proxies():
    assert not any(
        isinstance(handler, ProxyHandler)
        for handler in platform_browser_module._LOCAL_DEBUG_OPENER.handlers
    )


class _TextPage:
    def __init__(self, text: str) -> None:
        self.text = text

    def locator(self, selector: str):
        assert selector == "body"
        return self

    def inner_text(self, timeout: int):
        assert timeout == 3000
        return self.text


class _CollectionPage:
    def __init__(
        self,
        url: str,
        *,
        response_payloads: dict[int, list[dict[str, object]]] | None = None,
        body_text: str = "",
    ) -> None:
        self.url = url
        self.closed = False
        self.listeners: list[tuple[str, object]] = []
        self.goto_urls: list[str] = []
        self.response_payloads = response_payloads or {}
        self.body_text = body_text
        self.evaluations: list[str] = []

    def on(self, event: str, callback) -> None:
        self.listeners.append((event, callback))

    def remove_listener(self, event: str, callback) -> None:
        self.listeners.remove((event, callback))

    def set_default_timeout(self, _timeout: int) -> None:
        pass

    def goto(self, url: str, *, wait_until: str):
        self.url = url
        self.goto_urls.append(url)
        assert wait_until == "domcontentloaded"
        page_number = int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
        for payload in self.response_payloads.get(page_number, []):
            response = _CollectionResponse(
                (
                    f"https://api.bilibili.com/x/web-interface/wbi/search/type?search_type=video&page={page_number}"
                ),
                payload,
            )
            for event, callback in list(self.listeners):
                if event == "response":
                    callback(response)
        return None

    def wait_for_timeout(self, _timeout: int) -> None:
        pass

    def evaluate(self, script: str) -> None:
        self.evaluations.append(script)

    def locator(self, selector: str):
        assert selector == "body"
        return _TextPage(self.body_text)

    def close(self) -> None:
        self.closed = True


class _CollectionResponse:
    def __init__(self, url: str, payload: dict[str, object]) -> None:
        self.url = url
        self.payload = payload

    def json(self) -> dict[str, object]:
        return self.payload


class _CollectionContext:
    def __init__(
        self, pages: list[_CollectionPage], fallback_page: _CollectionPage
    ) -> None:
        self.pages = pages
        self.fallback_page = fallback_page
        self.new_page_calls = 0

    def new_page(self) -> _CollectionPage:
        self.new_page_calls += 1
        self.pages.append(self.fallback_page)
        return self.fallback_page

    def add_init_script(self, _script: str) -> None:
        """Real Playwright BrowserContexts expose add_init_script."""
        pass


class _CollectionBrowser:
    def __init__(self, context: _CollectionContext) -> None:
        self.contexts = [context]


class _CollectionPlaywright:
    def __init__(self, browser: _CollectionBrowser) -> None:
        self.chromium = self
        self.browser = browser

    def connect_over_cdp(self, _endpoint: str) -> _CollectionBrowser:
        return self.browser


class _CollectionPlaywrightManager:
    def __init__(self, browser: _CollectionBrowser) -> None:
        self.playwright = _CollectionPlaywright(browser)

    def __enter__(self) -> _CollectionPlaywright:
        return self.playwright

    def __exit__(self, *_args) -> None:
        pass


class _FilterCandidate:
    def __init__(self, page: "_FilterPage", label: str) -> None:
        self.page = page
        self.label = label

    def is_visible(self, *, timeout: int) -> bool:
        assert timeout == 1000
        return True

    def click(self, *, timeout: int) -> None:
        assert timeout == 1500
        self.page.selected.append(self.label)


class _FilterLocator:
    def __init__(self, page: "_FilterPage", label: str, present: bool) -> None:
        self.page = page
        self.label = label
        self.present = present

    def count(self) -> int:
        return 1 if self.present else 0

    def nth(self, index: int) -> _FilterCandidate:
        assert index == 0
        return _FilterCandidate(self.page, self.label)


class _FilterPage:
    def __init__(self, labels: set[str]) -> None:
        self.labels = labels
        self.selected: list[str] = []
        self.waits: list[int] = []

    def get_by_text(self, label: str, *, exact: bool) -> _FilterLocator:
        assert exact is True
        return _FilterLocator(self, label, label in self.labels)

    def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)


def _collect_with_fake_browser(
    provider: LocalPlatformBrowserSearchProvider,
    browser: _CollectionBrowser,
    monkeypatch,
) -> list[dict[str, object]]:
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _CollectionPlaywrightManager(browser),
    )
    monkeypatch.setattr(provider, "_apply_platform_filters", lambda _page: None)
    monkeypatch.setattr(provider, "_raise_for_visible_block", lambda _page: None)
    monkeypatch.setattr(provider, "_raise_for_login_gate", lambda _page: None)
    monkeypatch.setattr(
        provider,
        "_rendered_rows",
        lambda _page, **_kwargs: [
            {
                "item_id": "BVREUSE0001",
                "title": "贴标机使用方法",
                "source_url": "https://www.bilibili.com/video/BVREUSE0001",
            }
        ],
    )
    return provider._collect_rows("贴标机", target=1)


def test_optional_login_prompt_does_not_block_public_search_attempt():
    provider = _provider(Platform.BILIBILI)
    page = _TextPage("登录后可查看更多推荐内容 扫码登录")

    provider._raise_for_visible_block(page)

    with pytest.raises(LicensedProviderError, match="要求登录或人工验证"):
        provider._raise_for_login_gate(page)


def test_xiaohongshu_public_profile_rejects_visible_and_login_profile_starts(tmp_path):
    provider = LocalPlatformBrowserSearchProvider(
        platform=Platform.XIAOHONGSHU,
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29992,
    )

    with pytest.raises(LicensedProviderError, match="未登录公开搜索"):
        provider.open_login_browser()
    with pytest.raises(LicensedProviderError, match="未登录公开搜索"):
        provider.start_login_browser()


def test_xiaohongshu_explicit_login_profile_is_visible_and_separate(
    tmp_path, monkeypatch
):
    public_profile = tmp_path / "public-profile"
    login_profile = tmp_path / "login-profile"
    public = LocalPlatformBrowserSearchProvider(
        platform=Platform.XIAOHONGSHU,
        enabled=True,
        profile_dir=public_profile,
        debug_port=29992,
    )
    login = LocalPlatformBrowserSearchProvider(
        platform=Platform.XIAOHONGSHU,
        enabled=True,
        profile_dir=login_profile,
        debug_port=29993,
        allow_xiaohongshu_login=True,
    )
    launched: list[list[str]] = []
    closed = BrowserSessionStatus(
        True,
        False,
        True,
        False,
        "optional_login",
        "小红书当前未登录",
    )
    monkeypatch.setattr(login, "session_status", lambda: closed)
    monkeypatch.setattr(login, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(login, "_browser_executable", lambda: tmp_path / "chrome.exe")
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.subprocess.Popen",
        lambda args, **_kwargs: launched.append(args),
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.time.sleep", lambda _seconds: None
    )

    login.open_login_browser()

    assert public.anonymous_only is True
    assert login.anonymous_only is False
    assert login.is_xiaohongshu_login_profile is True
    assert public.profile_dir != login.profile_dir
    assert public.debug_port != login.debug_port
    assert login.capabilities().permission_status == "manual_login_required"
    assert "--new-window" in launched[0]
    assert "--incognito" not in launched[0]
    assert f"--user-data-dir={login_profile}" in launched[0]
    with pytest.raises(LicensedProviderError, match="只用于人工登录"):
        login.start_public_browser()
    with pytest.raises(LicensedProviderError, match="当前未登录"):
        login.search(Platform.XIAOHONGSHU, "贴标机", None, 1, "login-profile-test")


def test_xiaohongshu_public_start_stays_minimized_and_incognito(tmp_path, monkeypatch):
    provider = LocalPlatformBrowserSearchProvider(
        platform=Platform.XIAOHONGSHU,
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29992,
    )
    launched: list[list[str]] = []
    closed = BrowserSessionStatus(True, False, False, False, "browser_closed", "未打开")
    monkeypatch.setattr(provider, "session_status", lambda: closed)
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(
        provider, "_browser_executable", lambda: tmp_path / "chrome.exe"
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda _port: True,
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.subprocess.Popen",
        lambda args, **_kwargs: launched.append(args),
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.time.sleep", lambda _seconds: None
    )

    provider.start_public_browser()

    assert "--incognito" in launched[0]
    assert "--start-minimized" in launched[0]
    assert "--new-window" not in launched[0]


def test_login_button_reveals_waiting_platform_window_for_login(tmp_path, monkeypatch):
    provider = LocalPlatformBrowserSearchProvider(
        platform=Platform.KUAISHOU,
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29989,
    )
    ready = BrowserSessionStatus(True, True, True, False, "waiting_login", "等待登录")
    monkeypatch.setattr(provider, "session_status", lambda: ready)
    revealed: list[int] = []
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.reveal_browser_window",
        lambda port: revealed.append(port) or True,
    )

    provider.open_login_browser()

    assert revealed == [29989]


def test_automatic_platform_start_minimizes_existing_window(tmp_path, monkeypatch):
    provider = LocalPlatformBrowserSearchProvider(
        platform=Platform.KUAISHOU,
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29990,
    )
    ready = BrowserSessionStatus(True, True, False, True, "ready", "已连接")
    minimized: list[int] = []
    monkeypatch.setattr(provider, "session_status", lambda: ready)
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda port: minimized.append(port) or True,
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.subprocess.Popen",
        lambda *_args, **_kwargs: pytest.fail("已运行浏览器不应重复启动"),
    )

    status = provider.start_login_browser()

    assert status is ready
    assert minimized == [29990]


def test_automatic_platform_start_stays_minimized(tmp_path, monkeypatch):
    provider = LocalPlatformBrowserSearchProvider(
        platform=Platform.KUAISHOU,
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29991,
    )
    launched: list[list[str]] = []
    closed = BrowserSessionStatus(True, False, True, False, "browser_closed", "未打开")
    monkeypatch.setattr(provider, "session_status", lambda: closed)
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(
        provider, "_browser_executable", lambda: tmp_path / "chrome.exe"
    )
    minimized: list[int] = []
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda port: minimized.append(port) or True,
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.subprocess.Popen",
        lambda args, **kwargs: launched.append(args),
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.time.sleep", lambda _seconds: None
    )

    provider.start_login_browser()

    assert "--start-minimized" in launched[0]
    assert not any(arg.startswith("--window-position=") for arg in launched[0])
    assert "--new-window" not in launched[0]
    assert minimized == [29991]


def test_collection_reuses_existing_platform_page_and_keeps_it_open(monkeypatch):
    provider = _provider(Platform.BILIBILI)
    existing = _CollectionPage("https://www.bilibili.com/")
    fallback = _CollectionPage("about:blank")
    context = _CollectionContext([existing], fallback)
    minimized: list[int] = []
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda port: minimized.append(port) or True,
    )

    rows = _collect_with_fake_browser(
        provider, _CollectionBrowser(context), monkeypatch
    )

    assert [row["item_id"] for row in rows] == ["BVREUSE0001"]
    assert context.new_page_calls == 0
    assert existing.closed is False
    assert minimized and set(minimized) == {provider.debug_port}


def test_collection_only_closes_the_page_it_created(monkeypatch):
    provider = _provider(Platform.BILIBILI)
    unrelated = _CollectionPage("https://example.com/")
    fallback = _CollectionPage("about:blank")
    context = _CollectionContext([unrelated], fallback)
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda _port: True,
    )

    _collect_with_fake_browser(provider, _CollectionBrowser(context), monkeypatch)

    assert context.new_page_calls == 1
    assert fallback.closed is True
    assert unrelated.closed is False


def test_hard_verification_stops_public_search_attempt():
    provider = _provider(Platform.XIAOHONGSHU)

    with pytest.raises(LicensedProviderError, match="要求人工验证"):
        provider._raise_for_visible_block(_TextPage("请完成验证"))


def test_xiaohongshu_browser_response_normalizes_visible_search_metadata():
    provider = _provider(Platform.XIAOHONGSHU)
    payload = {
        "data": {
            "items": [
                {
                    "id": "xhs-note-1",
                    "note_card": {
                        "type": "video",
                        "display_title": "餐饮获客的三个新方法",
                        "user": {"user_id": "xhs-user-1", "nickname": "餐饮老板说"},
                        "interact_info": {
                            "liked_count": "1.2万",
                            "comment_count": "88",
                            "collected_count": "320",
                            "share_count": "16",
                        },
                        "time": 1785380400000,
                    },
                },
                {
                    "id": "xhs-image-1",
                    "note_card": {
                        "type": "normal",
                        "display_title": "餐饮获客图文笔记",
                    },
                },
            ]
        }
    }

    rows = provider._rows_from_payload(payload)
    items = provider._to_items(
        rows,
        observed_at=datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
        limit=30,
    )

    assert len(items) == 1
    item = items[0]
    assert item.platform == Platform.XIAOHONGSHU
    assert item.title == "餐饮获客的三个新方法"
    assert item.author_name == "餐饮老板说"
    assert item.metrics.likes == 12000
    assert item.metrics.comments == 88
    assert item.metrics.shares == 16
    assert item.metrics.favorites == 320
    assert str(item.source_url) == "https://www.xiaohongshu.com/explore/xhs-note-1"
    assert "time=platform" in (item.evidence or "")


def test_xiaohongshu_payload_rejects_items_without_video_type():
    provider = _provider(Platform.XIAOHONGSHU)
    payload = {
        "data": {
            "items": [
                {
                    "id": "xhs-unknown-type",
                    "note_card": {"display_title": "未标明类型的笔记"},
                },
                {
                    "id": "xhs-image-type",
                    "note_card": {"type": "normal", "display_title": "图文笔记"},
                },
            ]
        }
    }

    assert provider._rows_from_payload(payload) == []


def test_xiaohongshu_video_filter_requires_and_confirms_selected_state():
    provider = _provider(Platform.XIAOHONGSHU)

    class Control:
        def __init__(self):
            self.class_name = "channel"

        def is_visible(self):
            return True

        def inner_text(self):
            return "视频"

        def get_attribute(self, name):
            return self.class_name if name == "class" else None

        def locator(self, selector):
            assert selector == ".."
            return self

        def click(self, *, timeout):
            assert timeout == 3000
            self.class_name = "channel active"

    class Matches:
        def __init__(self, control):
            self.control = control

        def count(self):
            return 1

        def nth(self, _index):
            return self.control

    class Page:
        url = "https://www.xiaohongshu.com/search_result?keyword=贴标机&type=51"

        def __init__(self):
            self.control = Control()

        def locator(self, selector):
            assert "#video.channel" in selector
            return Matches(self.control)

        def wait_for_timeout(self, _timeout):
            pass

    page = Page()
    assert provider._select_xiaohongshu_video_filter(page) is True
    assert page.control.class_name == "channel active"


def test_xiaohongshu_filter_flow_reports_time_success_and_failure_fallback(
    monkeypatch,
):
    provider = _provider(Platform.XIAOHONGSHU)
    calls: list[str] = []
    monkeypatch.setattr(
        provider,
        "_select_xiaohongshu_video_filter",
        lambda _page: calls.append("video") or True,
    )
    monkeypatch.setattr(
        provider,
        "_select_xiaohongshu_time_filter",
        lambda _page, _days: calls.append("time") or "最近一周",
    )

    notes = provider._apply_platform_filters(
        object(),
        search_filters={"published_days": "7"},
    )

    assert calls == ["video", "time"]
    assert provider._xiaohongshu_video_filter_confirmed is True
    assert provider._xiaohongshu_time_filter_confirmed is True
    assert notes == ["已选择小红书“视频”筛选", "已选择小红书发布时间“最近一周”"]

    monkeypatch.setattr(
        provider,
        "_select_xiaohongshu_time_filter",
        lambda _page, _days: None,
    )
    notes = provider._apply_platform_filters(
        object(),
        search_filters={"published_days": "7"},
    )
    assert provider._xiaohongshu_time_filter_confirmed is False
    assert notes[-1] == "平台时间筛选未生效，正在本地过滤"


def test_xiaohongshu_time_filter_failure_uses_local_publish_time_filter(monkeypatch):
    provider = _provider(Platform.XIAOHONGSHU)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: BrowserSessionStatus(True, True, False, True, "ready", "已连接"),
    )
    provider._xiaohongshu_time_filter_confirmed = False
    monkeypatch.setattr(
        provider,
        "_collect_rows",
        lambda _keyword, **_kwargs: [
            {
                "item_id": "xhs-recent-video",
                "title": "最近视频",
                "source_url": "https://www.xiaohongshu.com/explore/xhs-recent-video",
                "published_at": now - timedelta(days=2),
                "time_confident": True,
                "is_video": True,
                "evidence": "browser_search_response",
            },
            {
                "item_id": "xhs-old-video",
                "title": "较早视频",
                "source_url": "https://www.xiaohongshu.com/explore/xhs-old-video",
                "published_at": now - timedelta(days=9),
                "time_confident": True,
                "is_video": True,
                "evidence": "browser_search_response",
            },
        ],
    )

    page = provider.search(
        Platform.XIAOHONGSHU,
        "贴标机",
        now - timedelta(days=7),
        1,
        "xhs-local-time-fallback",
    )

    assert [item.platform_item_id for item in page.items] == ["xhs-recent-video"]


def test_xiaohongshu_unknown_page_is_payload_invalid_diagnostic(monkeypatch):
    provider = _provider(Platform.XIAOHONGSHU)
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: BrowserSessionStatus(True, True, False, True, "ready", "已连接"),
    )

    def collect(_keyword, **_kwargs):
        provider._collection_rule_failure = "页面结构发生变化，请重新连接"
        return []

    monkeypatch.setattr(provider, "_collect_rows", collect)
    page = provider.search(
        Platform.XIAOHONGSHU,
        "贴标机",
        None,
        1,
        "xhs-unknown-page",
    )

    assert page.items == []
    assert page.payload_diagnostic == "页面结构发生变化，请重新连接"


def test_platform_filter_days_only_maps_native_date_options():
    observed_at = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)

    assert (
        LocalPlatformBrowserSearchProvider._platform_filter_days(None, observed_at) == 0
    )
    assert (
        LocalPlatformBrowserSearchProvider._platform_filter_days(
            observed_at - timedelta(days=7), observed_at
        )
        == 7
    )
    assert (
        LocalPlatformBrowserSearchProvider._platform_filter_days(
            observed_at - timedelta(days=3), observed_at
        )
        is None
    )


def test_bilibili_next_page_keeps_native_filter_query():
    next_url = LocalPlatformBrowserSearchProvider._bilibili_page_url(
        "https://search.bilibili.com/all?keyword=ai&order=pubdate&pubtime_begin_s=1&page=1",
        2,
    )
    query = parse_qs(urlparse(next_url).query)

    assert query["order"] == ["pubdate"]
    assert query["pubtime_begin_s"] == ["1"]
    assert query["page"] == ["2"]


def test_bilibili_newest_filter_waits_for_delayed_controls():
    class Control:
        def __init__(self, page):
            self.page = page

        def is_visible(self):
            return True

        def click(self, *, timeout):
            assert timeout == 3000
            self.page.url += "&order=pubdate"

    class Matches:
        def __init__(self, page, visible):
            self.page = page
            self.visible = visible

        def count(self):
            return 1 if self.visible else 0

        def nth(self, _index):
            return Control(self.page)

    class Page:
        url = "https://search.bilibili.com/all?keyword=ai"
        polls = 0
        waits = []

        def get_by_text(self, label, *, exact):
            assert label == "最新发布"
            assert exact is True
            self.polls += 1
            return Matches(self, self.polls >= 3)

        def wait_for_timeout(self, timeout):
            self.waits.append(timeout)

    page = Page()
    assert LocalPlatformBrowserSearchProvider._select_bilibili_newest_filter(page)
    assert parse_qs(urlparse(page.url).query)["order"] == ["pubdate"]
    assert page.waits == [500]


def test_bilibili_comprehensive_sort_keeps_date_filter_without_clicking_newest(
    monkeypatch,
):
    provider = _provider(Platform.BILIBILI)
    labels = []
    monkeypatch.setattr(
        provider,
        "_select_bilibili_newest_filter",
        lambda _page: pytest.fail("综合排序不应点击最新发布"),
    )
    monkeypatch.setattr(
        provider,
        "_select_bilibili_date_filter",
        lambda _page, label: labels.append(label) or True,
    )

    notes = provider._apply_platform_filters(
        object(),
        search_filters={"bilibili_sort": "platform", "published_days": "7"},
    )

    assert labels == ["最近一周"]
    assert notes == ["使用B站综合排序", "已选择B站发布时间“最近一周”"]


def test_xiaohongshu_public_metric_aliases_keep_explicit_zero_values():
    provider = _provider(Platform.XIAOHONGSHU)
    payload = {
        "data": {
            "items": [
                {
                    "id": "xhs-note-zero",
                    "noteCard": {
                        "type": "video",
                        "display_title": "互动数据为零的公开笔记",
                        "user": {"id": "xhs-user-zero", "nick_name": "零互动作者"},
                        "interactInfo": {
                            "likedCount": 0,
                            "commentCount": 0,
                            "shareCount": 0,
                            "collectCount": 0,
                        },
                    },
                }
            ]
        }
    }

    rows = provider._rows_from_payload(payload)
    items = provider._to_items(
        rows,
        observed_at=datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
        limit=30,
    )

    assert len(items) == 1
    assert items[0].metrics.likes == 0
    assert items[0].metrics.comments == 0
    assert items[0].metrics.shares == 0
    assert items[0].metrics.favorites == 0


def test_kuaishou_graphql_response_keeps_recent_keyword_matches():
    provider = _provider(Platform.KUAISHOU)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    payload = {
        "data": {
            "visionSearchPhoto": {
                "feeds": [
                    {
                        "author": {"id": "ks-user-1", "name": "快手经营课"},
                        "photo": {
                            "id": "ks-video-1",
                            "caption": "餐饮获客最近爆火的做法",
                            "timestamp": int((now - timedelta(hours=5)).timestamp()),
                            "viewCount": "8.6万",
                            "likeCount": "5200",
                            "commentCount": "136",
                            "shareCount": "92",
                            "collectCount": "73",
                            "durationMs": 9200,
                        },
                    },
                    {
                        "author": {"id": "ks-user-2", "name": "旧内容"},
                        "photo": {
                            "id": "ks-video-old",
                            "caption": "餐饮获客旧做法",
                            "timestamp": int((now - timedelta(days=8)).timestamp()),
                        },
                    },
                ]
            }
        }
    }

    rows = provider._rows_from_payload(payload)
    items = provider._to_items(
        rows,
        observed_at=now,
        limit=30,
    )

    assert [item.platform_item_id for item in items] == [
        "ks-video-1",
        "ks-video-old",
    ]
    item = items[0]
    assert item.metrics.plays == 86000
    assert item.metrics.likes == 5200
    assert item.metrics.comments == 136
    assert item.metrics.shares == 92
    assert item.metrics.favorites == 73
    assert item.duration_seconds == 9
    assert str(item.source_url) == "https://www.kuaishou.com/short-video/ks-video-1"


def test_kuaishou_current_rest_feed_response_is_recognized_and_normalized():
    provider = _provider(Platform.KUAISHOU)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    payload = {
        "result": 1,
        "feeds": [
            {
                "author": {"id": "ks-user-rest", "name": "快手餐饮观察"},
                "photo": {
                    "id": "ks-rest-video",
                    "caption": "餐饮门店如何低成本获客",
                    "timestamp": int((now - timedelta(days=2)).timestamp() * 1000),
                    "duration": 121000,
                    "viewCount": 680,
                    "likeCount": 31,
                },
            }
        ],
    }

    assert provider._is_search_response_url(
        "https://www.kuaishou.com/rest/v/search/feed?keyword=餐饮获客"
    )
    rows = provider._rows_from_payload(payload)
    items = provider._to_items(rows, observed_at=now, limit=30)

    assert len(items) == 1
    assert items[0].platform_item_id == "ks-rest-video"
    assert items[0].title == "餐饮门店如何低成本获客"
    assert items[0].metrics.plays == 680
    assert items[0].metrics.likes == 31
    assert items[0].published_at == now - timedelta(days=2)
    assert items[0].duration_seconds == 121


def test_kuaishou_search_filters_are_normalized_and_use_visible_controls():
    provider = _provider(Platform.KUAISHOU)
    page = _FilterPage({"最新发布", "1-5分钟"})

    filters = provider._normalize_kuaishou_filters(
        {
            "kuaishou_sort": "latest",
            "kuaishou_duration_bucket": "60-300",
        }
    )
    notes = provider._apply_platform_filters(page, search_filters=filters)

    assert filters == {
        "kuaishou_sort": "newest",
        "kuaishou_duration_bucket": "between_60_300",
    }
    assert page.selected == ["最新发布", "1-5分钟"]
    assert page.waits == [400, 400]
    assert notes == ["已选择快手排序“最新发布”", "已选择快手时长“1-5分钟”"]


def test_kuaishou_search_forwards_optional_filters_without_changing_contract(
    monkeypatch,
):
    provider = _provider(Platform.KUAISHOU)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: BrowserSessionStatus(True, True, False, True, "ready", "已连接"),
    )
    monkeypatch.setattr(
        provider,
        "_collect_rows",
        lambda _keyword, *, target, search_filters=None, **collection_options: (
            captured.update(
                target=target,
                search_filters=search_filters,
                qualified_target=collection_options.get("qualified_target"),
            )
            or []
        ),
    )

    page = provider.search(
        Platform.KUAISHOU,
        "餐饮获客",
        None,
        30,
        "kuaishou-filtered-search",
        search_filters={
            "kuaishou_sort": "newest",
            "kuaishou_duration_bucket": "between_60_300",
        },
    )

    assert captured == {
        "target": 300,
        "search_filters": {
            "kuaishou_sort": "newest",
            "kuaishou_duration_bucket": "between_60_300",
        },
        "qualified_target": 30,
    }
    assert "请求筛选：最新发布、1-5分钟" in (page.payload_diagnostic or "")


def test_kuaishou_likes_sort_does_not_get_overwritten_by_recentness():
    provider = _provider(Platform.KUAISHOU)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)

    items = provider._to_items(
        [
            {
                "item_id": "ks-recent-low-like",
                "title": "餐饮获客最新做法",
                "source_url": "https://www.kuaishou.com/short-video/ks-recent-low-like",
                "published_at": now - timedelta(hours=1),
                "time_confident": True,
                "likes": 10,
                "evidence": "browser_search_response",
            },
            {
                "item_id": "ks-old-high-like",
                "title": "餐饮获客高赞做法",
                "source_url": "https://www.kuaishou.com/short-video/ks-old-high-like",
                "published_at": now - timedelta(days=2),
                "time_confident": True,
                "likes": 1000,
                "evidence": "browser_search_response",
            },
        ],
        observed_at=now,
        limit=30,
        platform_sort="likes",
    )

    assert [item.platform_item_id for item in items] == [
        "ks-old-high-like",
        "ks-recent-low-like",
    ]


def test_kuaishou_newest_sort_uses_reliable_publish_time():
    provider = _provider(Platform.KUAISHOU)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)

    items = provider._to_items(
        [
            {
                "item_id": "ks-unknown-time",
                "title": "餐饮获客未标时间",
                "source_url": "https://www.kuaishou.com/short-video/ks-unknown-time",
                "time_confident": False,
                "evidence": "browser_search_response",
            },
            {
                "item_id": "ks-old",
                "title": "餐饮获客旧内容",
                "source_url": "https://www.kuaishou.com/short-video/ks-old",
                "published_at": now - timedelta(days=2),
                "time_confident": True,
                "evidence": "browser_search_response",
            },
            {
                "item_id": "ks-newest",
                "title": "餐饮获客新内容",
                "source_url": "https://www.kuaishou.com/short-video/ks-newest",
                "published_at": now - timedelta(hours=1),
                "time_confident": True,
                "evidence": "browser_search_response",
            },
        ],
        observed_at=now,
        limit=30,
        platform_sort="newest",
    )

    assert [item.platform_item_id for item in items] == [
        "ks-newest",
        "ks-old",
        "ks-unknown-time",
    ]


def test_kuaishou_duration_filter_sets_final_counts_and_target_stop(monkeypatch):
    provider = _provider(Platform.KUAISHOU)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    raw_rows = [
        {
            "item_id": f"ks-duration-{duration or 'unknown'}",
            "title": "餐饮获客时长测试",
            "source_url": f"https://www.kuaishou.com/short-video/ks-duration-{duration or 'unknown'}",
            "published_at": now,
            "time_confident": True,
            "duration_seconds": duration,
            "evidence": "browser_search_response",
        }
        for duration in (59, 60, 300, 301, None)
    ]
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: BrowserSessionStatus(True, True, False, True, "ready", "已连接"),
    )

    def collect(_keyword, *, target, search_filters=None, **_collection_options):
        assert target == 300
        assert search_filters == {
            "kuaishou_sort": "platform",
            "kuaishou_duration_bucket": "between_60_300",
        }
        provider._set_collection_stop("safety_limit", "临时扫描上限")
        return raw_rows

    monkeypatch.setattr(provider, "_collect_rows", collect)

    page = provider.search(
        Platform.KUAISHOU,
        "餐饮获客",
        None,
        2,
        "kuaishou-duration-filter",
        search_filters={
            "kuaishou_sort": "platform",
            "kuaishou_duration_bucket": "between_60_300",
        },
    )

    assert [item.duration_seconds for item in page.items] == [60, 300]
    assert page.raw_item_count == 5
    assert page.parsed_item_count == 2
    assert page.duration_filtered_count == 3
    assert page.crawl_stop_reason == "target_reached"
    assert page.crawl_stop_message == "已获得目标数量的符合条件视频。"


def test_kuaishou_keeps_safety_stop_when_final_duration_matches_are_insufficient(
    monkeypatch,
):
    provider = _provider(Platform.KUAISHOU)
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: BrowserSessionStatus(True, True, False, True, "ready", "已连接"),
    )

    def collect(_keyword, *, target, search_filters=None, **_collection_options):
        assert target == 300
        provider._set_collection_stop("safety_limit", "临时扫描上限")
        return [
            {
                "item_id": "ks-too-short",
                "title": "餐饮获客短视频",
                "source_url": "https://www.kuaishou.com/short-video/ks-too-short",
                "duration_seconds": 30,
                "evidence": "browser_search_response",
            }
        ]

    monkeypatch.setattr(provider, "_collect_rows", collect)

    page = provider.search(
        Platform.KUAISHOU,
        "餐饮获客",
        None,
        1,
        "kuaishou-insufficient-duration",
        search_filters={"kuaishou_duration_bucket": "over_300"},
    )

    assert page.raw_item_count == 1
    assert page.parsed_item_count == 0
    assert page.duration_filtered_count == 1
    assert page.crawl_stop_reason == "safety_limit"
    assert (
        page.crawl_stop_message
        == "为避免过度加载，已扫描 1 条页面结果，筛后保留 0 条。"
    )


def test_kuaishou_time_window_filters_before_target_stop(monkeypatch):
    provider = _provider(Platform.KUAISHOU)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    raw_rows = [
        {
            "item_id": "ks-old",
            "title": "餐饮获客旧视频",
            "source_url": "https://www.kuaishou.com/short-video/ks-old",
            "published_at": now - timedelta(days=8),
            "time_confident": True,
            "evidence": "browser_search_response",
        },
        {
            "item_id": "ks-new-1",
            "title": "餐饮获客新视频一",
            "source_url": "https://www.kuaishou.com/short-video/ks-new-1",
            "published_at": now - timedelta(hours=2),
            "time_confident": True,
            "evidence": "browser_search_response",
        },
        {
            "item_id": "ks-new-2",
            "title": "餐饮获客新视频二",
            "source_url": "https://www.kuaishou.com/short-video/ks-new-2",
            "published_at": now - timedelta(hours=1),
            "time_confident": True,
            "evidence": "browser_search_response",
        },
    ]
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: BrowserSessionStatus(True, True, False, True, "ready", "已连接"),
    )

    def collect(_keyword, *, target, qualified_target, qualifying_count, **_kwargs):
        assert target == 300
        assert qualified_target == 2
        assert qualifying_count(raw_rows[:2]) == 1
        assert qualifying_count(raw_rows) == 2
        provider._set_collection_stop("target_reached", "raw rows reached")
        return raw_rows

    monkeypatch.setattr(provider, "_collect_rows", collect)

    page = provider.search(
        Platform.KUAISHOU,
        "餐饮获客",
        now - timedelta(days=7),
        2,
        "kuaishou-time-window",
    )

    assert [item.platform_item_id for item in page.items] == ["ks-new-1", "ks-new-2"]
    assert page.raw_item_count == 3
    assert page.parsed_item_count == 2
    assert page.crawl_stop_reason == "target_reached"
    assert page.crawl_stop_message == "已获得目标数量的符合条件视频。"


def test_kuaishou_does_not_claim_target_when_time_window_leaves_too_few_rows(
    monkeypatch,
):
    provider = _provider(Platform.KUAISHOU)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    raw_rows = [
        {
            "item_id": f"ks-old-{index}",
            "title": "餐饮获客旧视频",
            "source_url": f"https://www.kuaishou.com/short-video/ks-old-{index}",
            "published_at": now - timedelta(days=8),
            "time_confident": True,
            "evidence": "browser_search_response",
        }
        for index in range(98)
    ] + [
        {
            "item_id": "ks-new-1",
            "title": "餐饮获客新视频一",
            "source_url": "https://www.kuaishou.com/short-video/ks-new-1",
            "published_at": now - timedelta(hours=2),
            "time_confident": True,
            "evidence": "browser_search_response",
        },
        {
            "item_id": "ks-new-2",
            "title": "餐饮获客新视频二",
            "source_url": "https://www.kuaishou.com/short-video/ks-new-2",
            "published_at": now - timedelta(hours=1),
            "time_confident": True,
            "evidence": "browser_search_response",
        },
    ]
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: BrowserSessionStatus(True, True, False, True, "ready", "已连接"),
    )

    def collect(_keyword, *, qualifying_count, **_kwargs):
        assert qualifying_count(raw_rows) == 2
        provider._set_collection_stop("safety_limit", "扫描上限")
        return raw_rows

    monkeypatch.setattr(provider, "_collect_rows", collect)

    page = provider.search(
        Platform.KUAISHOU,
        "餐饮获客",
        now - timedelta(days=7),
        30,
        "kuaishou-insufficient-time-window",
    )

    assert [item.platform_item_id for item in page.items] == ["ks-new-1", "ks-new-2"]
    assert page.crawl_stop_reason == "safety_limit"
    assert page.crawl_stop_message == (
        "为避免过度加载，已扫描 100 条页面结果，发布时间范围和时长筛后保留 2 条。"
    )
    assert "目标数量" not in (page.crawl_stop_message or "")


def test_kuaishou_scrolls_until_time_qualified_target_is_reached(monkeypatch):
    provider = _provider(Platform.KUAISHOU)
    page = _CollectionPage("https://www.kuaishou.com/")
    context = _CollectionContext([page], _CollectionPage("about:blank"))
    old_row = {
        "item_id": "ks-old",
        "title": "餐饮获客旧视频",
        "source_url": "https://www.kuaishou.com/short-video/ks-old",
    }
    new_rows = [
        {
            "item_id": "ks-new-1",
            "title": "餐饮获客新视频一",
            "source_url": "https://www.kuaishou.com/short-video/ks-new-1",
        },
        {
            "item_id": "ks-new-2",
            "title": "餐饮获客新视频二",
            "source_url": "https://www.kuaishou.com/short-video/ks-new-2",
        },
    ]
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _CollectionPlaywrightManager(_CollectionBrowser(context)),
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda _port: True,
    )
    monkeypatch.setattr(
        provider,
        "_rendered_rows",
        lambda _page: [old_row] if len(page.evaluations) < 18 else [old_row, *new_rows],
    )

    rows = provider._collect_rows(
        "餐饮获客",
        target=300,
        qualified_target=2,
        qualifying_count=lambda candidate_rows: sum(
            row["item_id"].startswith("ks-new") for row in candidate_rows
        ),
    )

    assert {row["item_id"] for row in rows} == {"ks-old", "ks-new-1", "ks-new-2"}
    assert len(page.evaluations) == 18
    assert provider._collection_stop_reason == "target_reached"


def test_kuaishou_empty_page_scrolls_until_the_safe_limit(monkeypatch):
    provider = _provider(Platform.KUAISHOU)
    page = _CollectionPage("https://www.kuaishou.com/")
    context = _CollectionContext([page], _CollectionPage("about:blank"))
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _CollectionPlaywrightManager(_CollectionBrowser(context)),
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda _port: True,
    )
    monkeypatch.setattr(provider, "_rendered_rows", lambda _page: [])

    rows = provider._collect_rows("餐饮获客", target=1)

    assert rows == []
    assert len(page.evaluations) == 60
    assert provider._collection_stop_reason == "safety_limit"


def test_kuaishou_scroll_targets_its_internal_results_container():
    provider = _provider(Platform.KUAISHOU)
    scripts: list[str] = []

    class Page:
        def evaluate(self, script: str) -> None:
            scripts.append(script)

    provider._scroll_for_more_results(Page())

    assert len(scripts) == 1
    assert ".wb-content" in scripts[0]
    assert "container.scrollBy" in scripts[0]


def test_bilibili_search_response_normalizes_visible_video_metadata():
    provider = _provider(Platform.BILIBILI)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    payload = {
        "data": {
            "result": [
                {
                    "bvid": "BV1TEST2026",
                    "title": '<em class="keyword">餐饮获客</em>爆火文案拆解',
                    "author": "经营有道",
                    "mid": 1024,
                    "pubdate": int((now - timedelta(hours=4)).timestamp()),
                    "play": "12.3万",
                    "video_review": 456,
                    "review": 78,
                    "favorites": 789,
                }
            ]
        }
    }

    rows = provider._rows_from_payload(payload)
    items = provider._to_items(
        rows,
        observed_at=now,
        limit=30,
    )

    assert len(items) == 1
    item = items[0]
    assert item.platform == Platform.BILIBILI
    assert item.title == "餐饮获客爆火文案拆解"
    assert item.author_name == "经营有道"
    assert item.metrics.plays == 123000
    assert item.metrics.comments == 78
    assert item.metrics.favorites == 789
    assert str(item.source_url) == "https://www.bilibili.com/video/BV1TEST2026"


def test_bilibili_network_rows_require_direct_normalized_keyword_matches():
    provider = _provider(Platform.BILIBILI)
    payload = {
        "data": {
            "result": [
                {
                    "bvid": "BV1TITLE2026",
                    "title": "AI·智能 营销入门",
                },
                {
                    "bvid": "BV1TOPIC2026",
                    "title": "门店增长案例",
                    "tag": "#ai 智能-营销",
                },
                {
                    "bvid": "BV1DESC02026",
                    "title": "经营复盘",
                    "description": "这一期讲 AI，智能营销 的执行步骤。",
                },
                {
                    "bvid": "BV1AUTHOR026",
                    "title": "完全无关的视频",
                    "author": "AI智能营销讲师",
                },
            ]
        }
    }

    rows = provider._rows_from_payload(payload, keyword="ai 智能-营销")

    assert [row["item_id"] for row in rows] == [
        "BV1TITLE2026",
        "BV1TOPIC2026",
        "BV1DESC02026",
        "BV1AUTHOR026",
    ]
    assert [row["direct_match"] for row in rows] == [True, True, True, False]
    items = provider._to_items(
        rows,
        observed_at=datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
        limit=30,
    )
    evidence_by_id = {item.platform_item_id: item.evidence or "" for item in items}
    assert "direct_match=1" in evidence_by_id["BV1TITLE2026"]
    assert "direct_match=1" in evidence_by_id["BV1TOPIC2026"]
    assert "direct_match=1" in evidence_by_id["BV1DESC02026"]
    assert "严格话题=1" not in evidence_by_id["BV1TITLE2026"]
    assert "严格话题=1" in evidence_by_id["BV1TOPIC2026"]
    assert "严格话题=1" in evidence_by_id["BV1DESC02026"]


def test_bilibili_collection_prefers_network_rows_and_loads_later_pages(monkeypatch):
    provider = _provider(Platform.BILIBILI)
    page = _CollectionPage(
        "https://www.bilibili.com/",
        response_payloads={
            1: [
                {
                    "data": {
                        "numPages": 2,
                        "result": [
                            {
                                "bvid": "BV1NETWORK01",
                                "title": "餐饮 获客-实操课",
                            },
                            {
                                "bvid": "BV1UNRELATED",
                                "title": "无关视频",
                                "author": "餐饮获客老师",
                            },
                        ],
                    }
                }
            ],
            2: [
                {
                    "data": {
                        "numPages": 2,
                        "result": [
                            {
                                "bvid": "BV1NETWORK02",
                                "title": "门店增长分享",
                                "description": "餐饮获客的可执行案例。",
                            }
                        ],
                    }
                }
            ],
        },
    )
    context = _CollectionContext([page], _CollectionPage("about:blank"))
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _CollectionPlaywrightManager(_CollectionBrowser(context)),
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda _port: True,
    )
    monkeypatch.setattr(
        provider,
        "_rendered_rows",
        lambda *_args, **_kwargs: pytest.fail("B站已有网络搜索结果时不应读取整页锚点"),
    )

    rows = provider._collect_rows("餐饮获客", target=3)

    assert [row["item_id"] for row in rows] == [
        "BV1NETWORK01",
        "BV1UNRELATED",
        "BV1NETWORK02",
    ]
    assert [row["direct_match"] for row in rows] == [True, False, True]
    assert [parse_qs(urlparse(url).query)["page"][0] for url in page.goto_urls] == [
        "1",
        "2",
    ]
    assert provider._collection_stop_reason == "platform_end"
    assert provider._collection_stop_message == "已经没有更多符合条件的视频。"


def test_bilibili_collection_does_not_scan_beyond_two_pages_for_sparse_matches(monkeypatch):
    provider = _provider(Platform.BILIBILI)
    page = _CollectionPage(
        "https://www.bilibili.com/",
        response_payloads={
            page_number: [
                {
                    "data": {
                        "numPages": 10,
                        "result": [
                            {
                                "bvid": f"BV1SPARSE{page_number:02d}",
                                "title": "没有严格命中的公开视频",
                            }
                        ],
                    }
                }
            ]
            for page_number in (1, 2, 3)
        },
    )
    context = _CollectionContext([page], _CollectionPage("about:blank"))
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _CollectionPlaywrightManager(_CollectionBrowser(context)),
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda _port: True,
    )

    rows = provider._collect_rows(
        "稀疏关键词",
        target=60,
        qualified_target=30,
        qualifying_count=lambda values: sum(bool(value.get("direct_match")) for value in values),
    )

    assert len(rows) == 2
    assert [parse_qs(urlparse(url).query)["page"][0] for url in page.goto_urls] == ["1", "2"]
    assert provider._collection_stop_reason == "safety_limit"


@pytest.mark.parametrize("key", ["duration_ms", "durationMs"])
def test_millisecond_duration_keys_always_use_millisecond_units(key):
    assert (
        LocalPlatformBrowserSearchProvider._first_duration_seconds(
            {key: 9500}, "duration", key
        )
        == 9
    )
    assert (
        LocalPlatformBrowserSearchProvider._first_duration_seconds(
            {"duration": 9500}, "duration"
        )
        == 9500
    )


def test_xiaohongshu_missing_publish_time_keeps_platform_search_order_warning():
    provider = _provider(Platform.XIAOHONGSHU)
    observed_at = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    items = provider._to_items(
        [
            {
                "item_id": "xhs-note-no-time",
                "title": "餐饮获客当天能用的标题",
                "source_url": "https://www.xiaohongshu.com/explore/xhs-note-no-time",
                "evidence": "rendered_search_card",
                "time_confident": False,
            }
        ],
        observed_at=observed_at,
        limit=30,
    )

    assert len(items) == 1
    assert items[0].published_at == observed_at
    assert "time=search_order_fallback" in (items[0].evidence or "")
    assert any(
        "未返回可靠发布时间" in warning for warning in items[0].data_quality_warnings
    )


def test_rendered_card_date_keeps_spaces_before_trailing_like_count():
    observed_at = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)

    published_at = LocalPlatformBrowserSearchProvider._parse_published_at(
        "餐饮行业怎么拍视频 作者 06-24 47",
        observed_at,
    )

    assert published_at == datetime(2026, 6, 24, tzinfo=timezone.utc)


def test_rendered_card_huge_relative_time_does_not_crash_collection():
    """卡片文案里超大"X天前"等相对时间不得让整次采集抛 date value out of range。

    平台卡片文本不可控，标题/描述里可能混入超大数字。过去该异常会从
    _rendered_rows 一路冒到 commercial_search，被兜底成"供应商响应状态
    不明确"，导致整次小红书搜索 0 条。相对时间超出可表示范围时应忽略。
    """
    observed_at = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)

    for text in (
        "餐饮获客 99999999999天前 作者",
        "餐饮获客 888888888888888888888888888888分钟前 作者",
        "餐饮获客 777777777777777777777小时前 作者",
        "餐饮获客 1234567890123456789天前 点赞 47",
    ):
        assert (
            LocalPlatformBrowserSearchProvider._parse_published_at(text, observed_at)
            is None
        )


def test_bilibili_search_keeps_raw_scan_pool_close_to_requested_target(monkeypatch):
    provider = _provider(Platform.BILIBILI)
    captured: dict[str, int] = {}
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: BrowserSessionStatus(True, True, False, True, "ready", "已连接"),
    )
    monkeypatch.setattr(
        provider,
        "_collect_rows",
        lambda _keyword, *, target, **_kwargs: captured.update(target=target) or [],
    )

    provider.search(
        Platform.BILIBILI,
        "餐饮获客",
        None,
        30,
        "larger-pool",
    )

    assert captured["target"] == 60


def test_bilibili_search_exposes_funnel_and_stage_timings(monkeypatch):
    provider = _provider(Platform.BILIBILI)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: BrowserSessionStatus(True, True, False, True, "ready", "已连接"),
    )
    rows = [
        {
            "item_id": "BV1funnel01",
            "title": "贴标机应用案例",
            "source_url": "https://www.bilibili.com/video/BV1funnel01",
            "published_at": now,
            "time_confident": True,
            "direct_match": True,
        }
    ]

    def collect(_keyword, *, target, **_kwargs):
        assert target == 30
        provider._collection_metrics = {
            "raw_discovered_count": 86,
            "deduped_item_count": 60,
            "direct_match_count": 20,
            "browser_reused": True,
            "stage_timings_ms": {
                "browser_attach_or_reuse_ms": 20,
                "navigation_ms": 900,
                "first_response_ms": 1100,
                "scroll_loading_ms": 0,
                "total_ms": 1200,
            },
        }
        return rows

    monkeypatch.setattr(provider, "_collect_rows", collect)
    page = provider.search(Platform.BILIBILI, "贴标机", None, 10, "funnel")

    assert page.raw_discovered_count == 86
    assert page.deduped_item_count == 60
    assert page.direct_match_count == 20
    assert page.stage_timings_ms["first_response_ms"] == 1100
    assert page.adapter_rule_version == provider.adapter_version


def test_bilibili_direct_matches_are_prioritized_inside_bounded_parse_pool():
    provider = _provider(Platform.BILIBILI)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    rows = [
        {
            "item_id": f"BVnoise{index:03d}",
            "title": f"无关视频 {index}",
            "source_url": f"https://www.bilibili.com/video/BVnoise{index:03d}",
            "published_at": now,
            "time_confident": True,
            "direct_match": False,
        }
        for index in range(100)
    ] + [
        {
            "item_id": f"BVdirect{index:03d}",
            "title": f"工厂短视频应用案例 {index}",
            "source_url": f"https://www.bilibili.com/video/BVdirect{index:03d}",
            "published_at": now - timedelta(days=30),
            "time_confident": True,
            "direct_match": True,
        }
        for index in range(5)
    ]

    items = provider._to_items(rows, observed_at=now, limit=100)

    assert len(items) == 100
    assert sum("direct_match=1" in (item.evidence or "") for item in items) == 5
    assert all("BVdirect" in str(item.platform_item_id) for item in items[:5])


def test_bilibili_collection_stops_when_direct_match_target_is_reached(monkeypatch):
    provider = _provider(Platform.BILIBILI)
    page = _CollectionPage(
        "https://www.bilibili.com/",
        response_payloads={
            1: [
                {
                    "data": {
                        "numPages": 5,
                        "result": [
                            {"bvid": "BV1EARLY01", "title": "Alpha 门店案例"},
                            {"bvid": "BV1EARLY02", "title": "Alpha 工厂案例"},
                            {"bvid": "BV1NOPE01", "title": "完全无关"},
                        ],
                    }
                }
            ]
        },
    )
    context = _CollectionContext([page], _CollectionPage("about:blank"))
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _CollectionPlaywrightManager(_CollectionBrowser(context)),
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda _port: True,
    )

    rows = provider._collect_rows(
        "Alpha",
        target=8,
        qualified_target=2,
        qualifying_count=lambda values: sum(bool(value.get("direct_match")) for value in values),
    )

    assert [row["item_id"] for row in rows] == [
        "BV1EARLY01",
        "BV1EARLY02",
        "BV1NOPE01",
    ]
    assert [parse_qs(urlparse(url).query)["page"][0] for url in page.goto_urls] == ["1"]
    assert provider._collection_stop_reason == "target_reached"


def test_bilibili_page_fault_rebuilds_page_without_clearing_context(monkeypatch):
    from playwright.sync_api import Error as PlaywrightError

    class BrokenPage(_CollectionPage):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.failed_once = False

        def goto(self, url: str, *, wait_until: str):
            if not self.failed_once:
                self.failed_once = True
                raise PlaywrightError("page closed")
            return super().goto(url, wait_until=wait_until)

    provider = _provider(Platform.BILIBILI)
    broken = BrokenPage("https://www.bilibili.com/")
    recovered = _CollectionPage(
        "about:blank",
        response_payloads={
            1: [
                {
                    "data": {
                        "numPages": 1,
                        "result": [{"bvid": "BV1RECOVER", "title": "Alpha 案例"}],
                    }
                }
            ]
        },
    )
    context = _CollectionContext([broken], recovered)
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: _CollectionPlaywrightManager(_CollectionBrowser(context)),
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda _port: True,
    )

    rows = provider._collect_rows("Alpha", target=1)

    assert [row["item_id"] for row in rows] == ["BV1RECOVER"]
    assert context.new_page_calls == 1
    assert provider._collection_metrics["session_recovered"] is True
    assert recovered.closed is True


def test_platform_login_reset_is_confirmed_and_domain_scoped(monkeypatch):
    class ResetPage:
        def __init__(self, url: str) -> None:
            self.url = url
            self.evaluations: list[str] = []

        def evaluate(self, script: str) -> None:
            self.evaluations.append(script)

    class ResetContext:
        def __init__(self) -> None:
            self.pages = [
                ResetPage("https://www.bilibili.com/search?keyword=test"),
                ResetPage("https://example.com/other-platform"),
            ]
            self.cookie_domains: list[str] = []

        def clear_cookies(self, *, domain: str) -> None:
            self.cookie_domains.append(domain)

    class ResetBrowser:
        def __init__(self, context: ResetContext) -> None:
            self.contexts = [context]

    class ResetPlaywright:
        def __init__(self, browser: ResetBrowser) -> None:
            self.chromium = self
            self.browser = browser

        def connect_over_cdp(self, _endpoint: str, *, timeout: int):
            assert timeout == 2500
            return self.browser

    context = ResetContext()
    browser = ResetBrowser(context)

    class ResetManager:
        def __enter__(self):
            return ResetPlaywright(browser)

        def __exit__(self, *_args) -> None:
            pass

    provider = _provider(Platform.BILIBILI)
    provider.login_reset_available = True
    ready = BrowserSessionStatus(True, True, False, True, "ready", "已连接")
    monkeypatch.setattr(provider, "session_status", lambda: ready)
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright", lambda: ResetManager()
    )

    with pytest.raises(LicensedProviderError, match="需要明确确认"):
        provider.reset_login_state()
    assert context.cookie_domains == []

    status = provider.reset_login_state(confirmed=True)

    assert status == ready
    assert context.cookie_domains == ["bilibili.com"]
    assert len(context.pages[0].evaluations) == 1
    assert context.pages[1].evaluations == []
    assert provider.login_reset_available is False


def test_bilibili_raw_pool_never_assigns_a_provider_rank_above_100(monkeypatch):
    provider = _provider(Platform.BILIBILI)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: BrowserSessionStatus(True, True, False, True, "ready", "已连接"),
    )
    raw_rows = [
        {
            "item_id": f"BV1rank{index:03d}",
            "title": "贴标机应用案例",
            "source_url": f"https://www.bilibili.com/video/BV1rank{index:03d}",
            "published_at": now,
            "time_confident": True,
            "evidence": "browser_search_response",
        }
        for index in range(101)
    ]
    monkeypatch.setattr(
        provider,
        "_collect_rows",
        lambda _keyword, *, target, **_kwargs: raw_rows,
    )

    page = provider.search(
        Platform.BILIBILI,
        "贴标机",
        None,
        30,
        "bilibili-rank-cap",
    )

    assert page.raw_item_count == 101
    assert len(page.items) == 100
    assert page.items[-1].provider_rank == 100


def test_xiaohongshu_anonymous_search_has_a_tight_public_budget(monkeypatch):
    provider = _provider(Platform.XIAOHONGSHU)
    captured: dict[str, int] = {}
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: BrowserSessionStatus(True, True, False, True, "ready", "已连接"),
    )
    monkeypatch.setattr(
        provider,
        "_collect_rows",
        lambda _keyword, *, target, **_kwargs: captured.update(target=target) or [],
    )

    provider.search(
        Platform.XIAOHONGSHU,
        "餐饮获客",
        None,
        15,
        "xhs-tight-pool",
    )

    assert provider.capabilities().permission_status == "public_browser_anonymous_only"
    assert provider.capabilities().max_page_size == 15
    assert captured["target"] == 20
    with pytest.raises(LicensedProviderError, match="最多保留 15 条"):
        provider.search(
            Platform.XIAOHONGSHU,
            "餐饮获客",
            None,
            16,
            "xhs-over-limit",
        )


def test_browser_provider_keeps_parsed_rows_for_shared_relevance_filtering():
    provider = _provider(Platform.BILIBILI)
    observed_at = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)

    items = provider._to_items(
        [
            {
                "item_id": "BVRECENT001",
                "title": "餐饮店在抖音怎么做才能获客",
                "source_url": "https://www.bilibili.com/video/BVRECENT001",
                "published_at": observed_at - timedelta(hours=2),
                "time_confident": True,
                "evidence": "browser_search_response",
            },
            {
                "item_id": "BVOLD00001",
                "title": "餐饮获客旧方法",
                "source_url": "https://www.bilibili.com/video/BVOLD00001",
                "published_at": observed_at - timedelta(days=8),
                "time_confident": True,
                "evidence": "browser_search_response",
            },
        ],
        observed_at=observed_at,
        limit=30,
    )

    assert [item.platform_item_id for item in items] == [
        "BVRECENT001",
        "BVOLD00001",
    ]


def test_parse_count_text_handles_card_numbers():
    from src.adapters.platform_browser_search import (
        LocalPlatformBrowserSearchProvider,
    )

    parse = LocalPlatformBrowserSearchProvider._parse_count_text
    assert parse("1.2万") == 12000
    assert parse("1234") == 1234
    assert parse("0") == 0
    assert parse("2.5w") == 25000
    assert parse(None) is None
    assert parse("") is None


def test_rendered_rows_only_uses_confirmed_bilibili_play_stats(monkeypatch, tmp_path):
    """B站第二个卡片数字没有稳定语义，不能伪装成点赞数。"""
    from project.backend.app.core.config import (
        BILIBILI_BROWSER_DISCOVERY_ENABLED,
        DOUYIN_BROWSER_CHANNEL,
    )
    from src.adapters.platform_browser_search import (
        LocalPlatformBrowserSearchProvider,
    )
    from src.models import Platform

    provider = LocalPlatformBrowserSearchProvider(
        platform=Platform.BILIBILI,
        enabled=BILIBILI_BROWSER_DISCOVERY_ENABLED,
        profile_dir=tmp_path / "profile",
        browser_channel=DOUYIN_BROWSER_CHANNEL,
        debug_port=29990,
        timeout_seconds=5.0,
    )

    class Locator:
        def evaluate_all(self, _script):
            return [
                {
                    "href": "https://www.bilibili.com/video/BV1ti5e62EBZ/",
                    "title": "餐饮获客视频",
                    "text": "121 | 0 | 04:12 | 餐饮获客视频 | 作者 | · 05-17",
                    "stats": ["121", "0"],
                }
            ]

    class Page:
        def locator(self, _selector):
            return Locator()

    rows = provider._rendered_rows(Page(), keyword="餐饮获客")
    assert len(rows) == 1
    assert rows[0]["plays"] == 121
    assert rows[0]["likes"] is None
    assert rows[0]["item_id"] == "BV1ti5e62EBZ"


def test_rendered_rows_extracts_xiaohongshu_count(monkeypatch, tmp_path):
    """小红书 span.count 点赞数应被提取。"""
    from project.backend.app.core.config import (
        XIAOHONGSHU_LOGIN_BROWSER_ENABLED,
        DOUYIN_BROWSER_CHANNEL,
    )
    from src.adapters.platform_browser_search import (
        LocalPlatformBrowserSearchProvider,
    )
    from src.models import Platform

    provider = LocalPlatformBrowserSearchProvider(
        platform=Platform.XIAOHONGSHU,
        enabled=XIAOHONGSHU_LOGIN_BROWSER_ENABLED,
        profile_dir=tmp_path / "profile",
        browser_channel=DOUYIN_BROWSER_CHANNEL,
        debug_port=29991,
        timeout_seconds=5.0,
        allow_xiaohongshu_login=True,
    )

    class Locator:
        def evaluate_all(self, _script):
            return [
                {
                    "href": "https://www.xiaohongshu.com/explore/64f2a1c2000000001a003456",
                    "title": "餐饮获客笔记",
                    "text": "别小瞧:餐饮店老板靠同城种草天天满座 | 作者 | 2天前 | 22",
                    "counts": ["22"],
                    "isVideo": True,
                }
            ]

    class Page:
        def locator(self, _selector):
            return Locator()

    rows = provider._rendered_rows(Page())
    assert len(rows) == 1
    assert rows[0]["likes"] == 22


def test_merge_rendered_row_keeps_existing_metrics():
    """字段级合并:后提取行指标为空时不清掉已有指标。"""
    from src.adapters.platform_browser_search import (
        LocalPlatformBrowserSearchProvider,
    )

    rendered = {}
    LocalPlatformBrowserSearchProvider._merge_rendered_row(
        rendered,
        {"item_id": "BV1", "title": "旧标题", "plays": 110, "likes": 5},
    )
    LocalPlatformBrowserSearchProvider._merge_rendered_row(
        rendered,
        {"item_id": "BV1", "title": "新标题", "plays": None, "likes": None},
    )
    assert rendered["BV1"]["title"] == "新标题"
    assert rendered["BV1"]["plays"] == 110
    assert rendered["BV1"]["likes"] == 5


def test_merge_collected_rows_network_none_does_not_clobber():
    """网络行指标为 None 时不应覆盖 DOM 行已有指标。"""
    from src.adapters.platform_browser_search import (
        LocalPlatformBrowserSearchProvider,
    )

    rendered = {"BV1": {"item_id": "BV1", "plays": 110, "likes": 5}}
    network = {"BV1": {"item_id": "BV1", "plays": None, "likes": None, "extra": "x"}}
    merged = LocalPlatformBrowserSearchProvider._merge_collected_rows(network, rendered)
    row = next(r for r in merged if r["item_id"] == "BV1")
    assert row["plays"] == 110
    assert row["likes"] == 5
    assert row["extra"] == "x"
