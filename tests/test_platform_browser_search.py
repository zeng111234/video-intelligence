"""小红书、快手、B站可见浏览器搜索的字段归一化测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.adapters.licensed import LicensedProviderError
from src.adapters.douyin_browser_search import BrowserSessionStatus
from src.adapters.platform_browser_search import LocalPlatformBrowserSearchProvider
from src.models import Platform


def _provider(platform: Platform) -> LocalPlatformBrowserSearchProvider:
    return LocalPlatformBrowserSearchProvider(
        platform=platform,
        enabled=True,
        profile_dir=Path("data/test-browser-profile"),
        debug_port=19999,
        clock=lambda: datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
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
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False
        self.listeners: list[tuple[str, object]] = []

    def on(self, event: str, callback) -> None:
        self.listeners.append((event, callback))

    def remove_listener(self, event: str, callback) -> None:
        self.listeners.remove((event, callback))

    def set_default_timeout(self, _timeout: int) -> None:
        pass

    def goto(self, url: str, *, wait_until: str):
        self.url = url
        assert wait_until == "domcontentloaded"
        return None

    def wait_for_timeout(self, _timeout: int) -> None:
        pass

    def evaluate(self, _script: str) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _CollectionContext:
    def __init__(self, pages: list[_CollectionPage], fallback_page: _CollectionPage) -> None:
        self.pages = pages
        self.fallback_page = fallback_page
        self.new_page_calls = 0

    def new_page(self) -> _CollectionPage:
        self.new_page_calls += 1
        self.pages.append(self.fallback_page)
        return self.fallback_page


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
        lambda _page: [
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
    monkeypatch.setattr("src.adapters.platform_browser_search.time.sleep", lambda _seconds: None)

    login.open_login_browser()

    assert public.anonymous_only is True
    assert login.anonymous_only is False
    assert login.is_xiaohongshu_login_profile is True
    assert public.profile_dir != login.profile_dir
    assert public.debug_port != login.debug_port
    assert login.capabilities().permission_status == "manual_login_optional"
    assert "--new-window" in launched[0]
    assert "--incognito" not in launched[0]
    assert f"--user-data-dir={login_profile}" in launched[0]
    with pytest.raises(LicensedProviderError, match="只用于人工登录"):
        login.start_public_browser()
    with pytest.raises(LicensedProviderError, match="只用于人工登录"):
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
    monkeypatch.setattr(provider, "_browser_executable", lambda: tmp_path / "chrome.exe")
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda _port: True,
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.subprocess.Popen",
        lambda args, **_kwargs: launched.append(args),
    )
    monkeypatch.setattr("src.adapters.platform_browser_search.time.sleep", lambda _seconds: None)

    provider.start_public_browser()

    assert "--incognito" in launched[0]
    assert "--start-minimized" in launched[0]
    assert "--new-window" not in launched[0]


def test_login_button_reveals_waiting_platform_window_for_login(tmp_path, monkeypatch):
    provider = LocalPlatformBrowserSearchProvider(
        platform=Platform.KUAISHOU, enabled=True, profile_dir=tmp_path / "profile", debug_port=29989
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
    closed = BrowserSessionStatus(
        True, False, True, False, "browser_closed", "未打开"
    )
    monkeypatch.setattr(provider, "session_status", lambda: closed)
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(provider, "_browser_executable", lambda: tmp_path / "chrome.exe")
    minimized: list[int] = []
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.minimize_browser_window",
        lambda port: minimized.append(port) or True,
    )
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.subprocess.Popen",
        lambda args, **kwargs: launched.append(args),
    )
    monkeypatch.setattr("src.adapters.platform_browser_search.time.sleep", lambda _seconds: None)

    provider.start_login_browser()

    assert "--start-minimized" in launched[0]
    assert "--window-position=-32000,-32000" in launched[0]
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

    rows = _collect_with_fake_browser(provider, _CollectionBrowser(context), monkeypatch)

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
        "src.adapters.platform_browser_search.minimize_browser_window", lambda _port: True
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


def test_xiaohongshu_public_metric_aliases_keep_explicit_zero_values():
    provider = _provider(Platform.XIAOHONGSHU)
    payload = {
        "data": {
            "items": [
                {
                    "id": "xhs-note-zero",
                    "noteCard": {
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


def test_bilibili_search_response_normalizes_visible_video_metadata():
    provider = _provider(Platform.BILIBILI)
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    payload = {
        "data": {
            "result": [
                {
                    "bvid": "BV1TEST2026",
                    "title": "<em class=\"keyword\">餐饮获客</em>爆火文案拆解",
                    "author": "经营有道",
                    "mid": 1024,
                    "pubdate": int((now - timedelta(hours=4)).timestamp()),
                    "play": "12.3万",
                    "video_review": 456,
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
    assert item.metrics.comments is None
    assert item.metrics.favorites == 789
    assert str(item.source_url) == "https://www.bilibili.com/video/BV1TEST2026"


@pytest.mark.parametrize("key", ["duration_ms", "durationMs"])
def test_millisecond_duration_keys_always_use_millisecond_units(key):
    assert LocalPlatformBrowserSearchProvider._first_duration_seconds(
        {key: 9500}, "duration", key
    ) == 9
    assert LocalPlatformBrowserSearchProvider._first_duration_seconds(
        {"duration": 9500}, "duration"
    ) == 9500


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
    assert any("未返回可靠发布时间" in warning for warning in items[0].data_quality_warnings)


def test_rendered_card_date_keeps_spaces_before_trailing_like_count():
    observed_at = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)

    published_at = LocalPlatformBrowserSearchProvider._parse_published_at(
        "餐饮行业怎么拍视频 作者 06-24 47",
        observed_at,
    )

    assert published_at == datetime(2026, 6, 24, tzinfo=timezone.utc)


def test_platform_search_collects_a_larger_raw_pool(monkeypatch):
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
        lambda _keyword, *, target: captured.update(target=target) or [],
    )

    provider.search(
        Platform.BILIBILI,
        "餐饮获客",
        None,
        30,
        "larger-pool",
    )

    assert captured["target"] == 90


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
        lambda _keyword, *, target: captured.update(target=target) or [],
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
