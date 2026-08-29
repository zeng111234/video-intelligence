from datetime import datetime, timedelta
from pathlib import Path

import pytest
from urllib.request import ProxyHandler

from src.adapters import douyin_browser_search as douyin_browser_module
from src.adapters.licensed import LicensedProviderError
from src.adapters.douyin_browser_search import (
    BrowserSessionStatus,
    LocalDouyinBrowserSearchProvider,
    LocalDouyinPublicSearchProvider,
)
from src.models import Platform, ProviderErrorKind, ProviderMode, ProviderSearchError


def test_local_debug_status_bypasses_environment_proxies():
    assert not any(
        isinstance(handler, ProxyHandler)
        for handler in douyin_browser_module._LOCAL_DEBUG_OPENER.handlers
    )


def test_visible_video_rows_become_canonical_douyin_candidates(tmp_path):
    observed_at = datetime.fromisoformat("2026-07-23T12:00:00+08:00")
    items, low_incremental_items, errors, filtered = (
        LocalDouyinBrowserSearchProvider._to_items(
            [
                {
                    "href": "https://www.douyin.com/video/7538955201693994298?foo=1",
                    "text": "数字人口播实测：开头两秒怎么留人",
                    "aria": "",
                    "duration": 15,
                    "plays": 1201,
                    "likes": 120,
                    "comments": 12,
                    "shares": 3,
                    "favorites": 5,
                    "list_type": 1001,
                    "list_label": "视频总榜",
                    "window_hours": 168,
                },
                {
                    "href": "https://www.douyin.com/video/7538955201693994298",
                    "text": "重复卡片",
                    "aria": "",
                    "duration": 15,
                    "plays": 1201,
                },
            ],
            "数字人",
            observed_at,
            10,
        )
    )

    assert errors == []
    assert low_incremental_items == []
    assert filtered == {
        "duration": 0,
        "incremental_plays": 0,
        "relevance": 0,
        "quality": 0,
    }
    assert len(items) == 1
    assert items[0].platform == Platform.DOUYIN
    assert items[0].platform_item_id == "7538955201693994298"
    assert (
        str(items[0].source_url) == "https://www.douyin.com/video/7538955201693994298"
    )
    assert items[0].metrics.plays == 1201
    assert items[0].metrics.comments == 12
    assert items[0].metrics.shares == 3
    assert items[0].metrics.favorites == 5
    assert "新增播放量=1201" in items[0].evidence
    assert "日均点赞=" in items[0].evidence


def test_public_search_metrics_preserve_returned_zero_and_missing_values():
    observed_at = datetime.fromisoformat("2026-08-03T12:00:00+08:00")
    rows = [
        {
            "item_id": "7538955201693994391",
            "href": "https://www.douyin.com/video/7538955201693994391",
            "title": "抖音公开搜索的完整互动指标",
            "duration": 18,
            "plays": 1200,
            "likes": 120,
            "comments": 12,
            "shares": 3,
            "favorites": 4,
            "published_text": "2026-08-02",
        },
        {
            "item_id": "7538955201693994392",
            "href": "https://www.douyin.com/video/7538955201693994392",
            "title": "抖音公开搜索的零值互动指标",
            "duration": 20,
            "plays": 0,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "favorites": 0,
            "published_text": "2026-08-02",
        },
        {
            "item_id": "7538955201693994394",
            "href": "https://www.douyin.com/video/7538955201693994394",
            "title": "抖音公开搜索未返回互动指标",
            "duration": 20,
            "published_text": "2026-08-02",
        },
    ]

    items, errors, _, _ = LocalDouyinBrowserSearchProvider._to_public_search_items(
        rows,
        keyword="抖音公开搜索",
        observed_at=observed_at,
        published_after=None,
        limit=3,
    )

    assert errors == []
    assert (
        items[0].metrics.plays,
        items[0].metrics.likes,
        items[0].metrics.comments,
        items[0].metrics.shares,
        items[0].metrics.favorites,
    ) == (1200, 120, 12, 3, 4)
    assert (
        items[1].metrics.plays,
        items[1].metrics.likes,
        items[1].metrics.comments,
        items[1].metrics.shares,
        items[1].metrics.favorites,
    ) == (0, 0, 0, 0, 0)
    assert (
        items[2].metrics.plays,
        items[2].metrics.likes,
        items[2].metrics.comments,
        items[2].metrics.shares,
        items[2].metrics.favorites,
    ) == (None, None, None, None, None)
    assert "播放量=1200" in items[0].evidence
    assert "评论数=12" in items[0].evidence
    assert "抖音搜索页未显示互动指标：播放、点赞、评论、分享、收藏。" in (
        items[2].data_quality_warnings
    )


class _TextControl:
    def __init__(self, page, label, *, visible=True, enabled=True, fails=False):
        self.page = page
        self.label = label
        self.visible = visible
        self.enabled = enabled
        self.fails = fails

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def evaluate(self, script):
        assert "getBoundingClientRect" in script
        return self.visible

    def get_attribute(self, name):
        assert name == "aria-disabled"
        return "true" if not self.enabled else None

    def click(self):
        if self.fails:
            raise RuntimeError("click failed")
        self.page.clicked.append(self.label)
        if self.label == "筛选":
            self.page.filter_menu_open = True
        if self.label in self.page.time_filter_indexes and self.page.confirm_filter:
            self.page.selected_time_index = self.page.time_filter_indexes[self.label]
            if self.page.close_filter_on_option:
                self.page.filter_menu_open = False
        if self.label in {"单列", "多列"} and self.page.confirm_layout:
            self.page.selected_layout = self.label


class _TextMatches:
    def __init__(self, controls):
        self.controls = controls

    def count(self):
        return len(self.controls)

    def nth(self, index):
        return self.controls[index]


class _VisibleFilterPage:
    time_filter_indexes = {"不限": "0", "一天内": "1", "一周内": "2", "半年内": "3"}

    def __init__(
        self,
        controls,
        *,
        confirm_filter=True,
        confirm_layout=True,
        close_filter_on_option=False,
    ):
        self.clicked: list[str] = []
        self.pressed: list[str] = []
        self.waits: list[int] = []
        self.confirm_filter = confirm_filter
        self.confirm_layout = confirm_layout
        self.close_filter_on_option = close_filter_on_option
        self.filter_menu_open = False
        self.selected_time_index: str | None = None
        self.selected_layout = "单列"
        self.layout_controls_ready = True
        self.controls = {
            label: [_TextControl(self, label, **settings) for settings in definitions]
            for label, definitions in controls.items()
        }
        self.keyboard = type(
            "Keyboard",
            (),
            {"press": lambda keyboard, key: self._press(key)},
        )()

    def _press(self, key):
        self.pressed.append(key)
        if key == "Escape":
            self.filter_menu_open = False

    def get_by_text(self, label, *, exact):
        assert exact is True
        return _TextMatches(self.controls.get(label, []))

    def locator(self, selector):
        assert 'data-index1="1"' in selector
        for label, option_index in self.time_filter_indexes.items():
            if f'data-index2="{option_index}"' in selector:
                return _TextMatches(self.controls.get(label, []))
        return _TextMatches([])

    def wait_for_timeout(self, delay):
        self.waits.append(delay)

    def evaluate(self, script, *args):
        if "__PUBLIC_SEARCH_TIME_FILTER_MENU_VISIBLE__" in script:
            return self.filter_menu_open
        if "__PUBLIC_SEARCH_TIME_FILTER_SELECTED__" in script:
            return self.selected_time_index == args[0]
        if "__PUBLIC_SEARCH_MULTI_SELECTED__" in script:
            return self.selected_layout == "多列"
        if "__PUBLIC_SEARCH_LAYOUT_CONTROLS_READY__" in script:
            return self.layout_controls_ready
        raise AssertionError("unexpected page evaluation")


@pytest.mark.parametrize(
    ("days", "visible_label"),
    [(1, "一天内"), (7, "一周内"), (180, "半年内")],
)
def test_public_search_applies_only_the_exact_visible_time_filter(days, visible_label):
    observed_at = datetime.fromisoformat("2026-08-04T12:00:00+08:00")
    page = _VisibleFilterPage(
        {
            "筛选": [{}],
            visible_label: [{}],
        }
    )

    outcome = LocalDouyinBrowserSearchProvider._apply_public_search_time_filter(
        page,
        published_after=observed_at - timedelta(days=days),
        observed_at=observed_at,
    )

    assert page.clicked == ["筛选", visible_label]
    assert outcome.receipt == f"已应用平台筛选：{visible_label}（{days}天）"
    assert outcome.warning is None


def test_public_search_unlimited_is_explicitly_applied_and_confirmed():
    observed_at = datetime.fromisoformat("2026-08-04T12:00:00+08:00")
    page = _VisibleFilterPage({"筛选": [{}], "不限": [{}]})

    outcome = LocalDouyinBrowserSearchProvider._apply_public_search_time_filter(
        page,
        published_after=None,
        observed_at=observed_at,
    )

    assert page.clicked == ["筛选", "不限"]
    assert outcome.receipt == "已应用平台筛选：不限"
    assert outcome.warning is None


def test_public_search_does_not_claim_filter_when_selected_state_is_unconfirmed():
    observed_at = datetime.fromisoformat("2026-08-04T12:00:00+08:00")
    page = _VisibleFilterPage(
        {"筛选": [{}], "一周内": [{}]},
        confirm_filter=False,
    )

    outcome = LocalDouyinBrowserSearchProvider._apply_public_search_time_filter(
        page,
        published_after=observed_at - timedelta(days=7),
        observed_at=observed_at,
    )

    assert page.clicked == ["筛选", "一周内"]
    assert outcome.receipt == "平台筛选未应用；仅本地过滤"
    assert outcome.error_code == "public_search_time_filter_unconfirmed"
    assert "没有显示该发布时间条件为选中状态" in outcome.warning


def test_public_search_waits_for_time_filter_options_to_render():
    observed_at = datetime.fromisoformat("2026-08-04T12:00:00+08:00")

    class DelayedFilterPage(_VisibleFilterPage):
        menu_polls = 0

        def evaluate(self, script, *args):
            if (
                "__PUBLIC_SEARCH_TIME_FILTER_MENU_VISIBLE__" in script
                and self.filter_menu_open
            ):
                self.menu_polls += 1
                if self.menu_polls < 3:
                    return False
            return super().evaluate(script, *args)

    page = DelayedFilterPage({"筛选": [{}], "一周内": [{}]})

    outcome = LocalDouyinBrowserSearchProvider._apply_public_search_time_filter(
        page,
        published_after=observed_at - timedelta(days=7),
        observed_at=observed_at,
    )

    assert page.clicked == ["筛选", "一周内"]
    assert page.waits[:2] == [100, 100]
    assert outcome.warning is None


def test_public_search_reopens_filter_once_when_first_panel_does_not_render(
    monkeypatch,
):
    observed_at = datetime.fromisoformat("2026-08-04T12:00:00+08:00")
    page = _VisibleFilterPage({"筛选": [{}], "一周内": [{}]})
    render_attempts = iter((False, True))
    monkeypatch.setattr(
        LocalDouyinBrowserSearchProvider,
        "_wait_for_public_search_time_filter_options",
        lambda _page: next(render_attempts),
    )

    outcome = LocalDouyinBrowserSearchProvider._apply_public_search_time_filter(
        page,
        published_after=observed_at - timedelta(days=7),
        observed_at=observed_at,
    )

    assert page.clicked == ["筛选", "筛选", "一周内"]
    assert outcome.warning is None


def test_public_search_reopens_filter_to_confirm_auto_closed_selection():
    observed_at = datetime.fromisoformat("2026-08-04T12:00:00+08:00")
    page = _VisibleFilterPage(
        {"筛选": [{}], "一周内": [{}]},
        close_filter_on_option=True,
    )

    outcome = LocalDouyinBrowserSearchProvider._apply_public_search_time_filter(
        page,
        published_after=observed_at - timedelta(days=7),
        observed_at=observed_at,
    )

    assert page.clicked == ["筛选", "一周内", "筛选"]
    assert outcome.warning is None
    assert outcome.receipt == "已应用平台筛选：一周内（7天）"


def test_public_search_retries_one_stale_time_filter_control():
    observed_at = datetime.fromisoformat("2026-08-04T12:00:00+08:00")

    class StaleFilterPage(_VisibleFilterPage):
        option_locator_calls = 0

        def locator(self, selector):
            if 'data-index1="1"' in selector:
                self.option_locator_calls += 1
                if self.option_locator_calls == 1:
                    return _TextMatches([_TextControl(self, "不限", fails=True)])
            return super().locator(selector)

    page = StaleFilterPage({"筛选": [{}], "不限": [{}]})

    outcome = LocalDouyinBrowserSearchProvider._apply_public_search_time_filter(
        page,
        published_after=None,
        observed_at=observed_at,
    )

    assert page.clicked == ["筛选", "筛选", "不限"]
    assert page.option_locator_calls == 2
    assert outcome.warning is None


def test_public_search_selects_and_confirms_multi_column_layout():
    page = _VisibleFilterPage({"多列": [{}], "单列": [{}]})

    outcome = LocalDouyinBrowserSearchProvider._ensure_public_search_multi_column(page)

    assert page.clicked == ["多列"]
    assert outcome.mode == "multi_column"
    assert outcome.warning is None


def test_public_search_selects_and_confirms_video_filter():
    class VideoLocator:
        def __init__(self, page):
            self.page = page

        def count(self):
            return 1

        def is_visible(self):
            return True

        def click(self, *, timeout):
            assert timeout == 3000
            self.page.url = "https://www.douyin.com/search/test?type=video"

    class VideoPage:
        url = "https://www.douyin.com/search/test?type=general"

        def locator(self, selector):
            assert selector == 'span[data-key="video"]'
            return VideoLocator(self)

        def wait_for_timeout(self, timeout):
            assert timeout == 800

    outcome = LocalDouyinBrowserSearchProvider._ensure_public_search_video_filter(
        VideoPage()
    )

    assert outcome.receipt == "已应用平台筛选：视频"
    assert outcome.warning is None


def test_public_search_waits_for_delayed_video_filter():
    class VideoLocator:
        def __init__(self, page):
            self.page = page

        def count(self):
            return 1 if self.page.polls >= 2 else 0

        def is_visible(self):
            return True

        def click(self, *, timeout):
            assert timeout == 3000
            self.page.url = "https://www.douyin.com/jingxuan/search/test?type=video"

    class DelayedVideoPage:
        url = "https://www.douyin.com/jingxuan/search/test?type=general"
        polls = 0
        waits = []

        def locator(self, selector):
            assert selector == 'span[data-key="video"]'
            self.polls += 1
            return VideoLocator(self)

        def wait_for_timeout(self, timeout):
            self.waits.append(timeout)

    page = DelayedVideoPage()
    outcome = LocalDouyinBrowserSearchProvider._ensure_public_search_video_filter(page)

    assert outcome.receipt == "已应用平台筛选：视频"
    assert outcome.warning is None
    assert page.waits == [500, 800]


def test_public_search_stops_if_video_filter_cannot_be_confirmed():
    class MissingLocator:
        def count(self):
            return 0

    class MissingVideoPage:
        url = "https://www.douyin.com/search/test?type=general"

        def locator(self, _selector):
            return MissingLocator()

    outcome = LocalDouyinBrowserSearchProvider._ensure_public_search_video_filter(
        MissingVideoPage()
    )

    assert outcome.error_code == "public_search_video_filter_unavailable"
    assert "避免混入图文" in outcome.warning


def test_public_search_does_not_reclick_an_already_selected_multi_layout():
    page = _VisibleFilterPage({"多列": [{}], "单列": [{}]})
    page.selected_layout = "多列"

    outcome = LocalDouyinBrowserSearchProvider._ensure_public_search_multi_column(page)

    assert page.clicked == []
    assert outcome.mode == "multi_column"


def test_public_search_waits_for_layout_controls_after_navigation():
    class DelayedLayoutPage(_VisibleFilterPage):
        layout_polls = 0

        def evaluate(self, script, *args):
            if "__PUBLIC_SEARCH_LAYOUT_CONTROLS_READY__" in script:
                self.layout_polls += 1
                return self.layout_polls >= 3
            return super().evaluate(script, *args)

    page = DelayedLayoutPage({"多列": [{}], "单列": [{}]})

    outcome = LocalDouyinBrowserSearchProvider._ensure_public_search_multi_column(page)

    assert page.waits[:2] == [200, 200]
    assert outcome.mode == "multi_column"


def test_public_search_continues_when_multi_column_state_is_unconfirmed():
    page = _VisibleFilterPage(
        {"多列": [{}], "单列": [{}]},
        confirm_layout=False,
    )

    outcome = LocalDouyinBrowserSearchProvider._ensure_public_search_multi_column(page)

    assert outcome.mode == "unconfirmed"
    assert outcome.error_code is None
    assert outcome.warning is None


def test_public_search_continues_when_layout_controls_are_missing():
    page = _VisibleFilterPage({})

    outcome = LocalDouyinBrowserSearchProvider._ensure_public_search_multi_column(page)

    assert outcome.mode == "unconfirmed"
    assert outcome.error_code is None
    assert outcome.warning is None


def test_public_search_does_not_map_legacy_three_days_to_one_week():
    observed_at = datetime.fromisoformat("2026-08-04T12:00:00+08:00")
    page = _VisibleFilterPage({"筛选": [{}], "一周内": [{}]})

    outcome = LocalDouyinBrowserSearchProvider._apply_public_search_time_filter(
        page,
        published_after=observed_at - timedelta(days=3),
        observed_at=observed_at,
    )

    assert page.clicked == []
    assert outcome.receipt == "平台筛选未应用；仅本地过滤"
    assert "不是抖音官网可精确对应" in outcome.warning


@pytest.mark.parametrize(
    ("settings", "expected_text"),
    [
        (None, "未找到"),
        ({"visible": False}, "不可见"),
        ({"enabled": False}, "已禁用"),
        ({"fails": True}, "操作失败"),
    ],
)
def test_public_search_leaves_page_unchanged_when_filter_control_is_unavailable(
    settings,
    expected_text,
):
    observed_at = datetime.fromisoformat("2026-08-04T12:00:00+08:00")
    controls = {} if settings is None else {"筛选": [settings]}
    page = _VisibleFilterPage(controls)

    outcome = LocalDouyinBrowserSearchProvider._apply_public_search_time_filter(
        page,
        published_after=observed_at - timedelta(days=7),
        observed_at=observed_at,
    )

    assert page.clicked == []
    assert outcome.receipt == "平台筛选未应用；仅本地过滤"
    assert expected_text in outcome.warning
    assert "仅按页面可核验发布时间在本地过滤" in outcome.warning


def test_public_search_closes_filter_menu_when_requested_option_is_unavailable():
    observed_at = datetime.fromisoformat("2026-08-04T12:00:00+08:00")
    page = _VisibleFilterPage({"筛选": [{}]})

    outcome = LocalDouyinBrowserSearchProvider._apply_public_search_time_filter(
        page,
        published_after=observed_at - timedelta(days=180),
        observed_at=observed_at,
    )

    assert page.clicked == ["筛选", "筛选"]
    assert page.pressed == ["Escape", "Escape"]
    assert outcome.receipt == "平台筛选未应用；仅本地过滤"
    assert "已关闭筛选菜单并保留原筛选状态" in outcome.warning


def test_public_search_confirms_multi_layout_then_filter_before_scrolling(
    tmp_path,
    monkeypatch,
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
    )
    observed_at = datetime.fromisoformat("2026-08-04T12:00:00+08:00")
    calls: list[str] = []

    class Page:
        def set_default_timeout(self, _timeout):
            pass

        def goto(self, _url, *, wait_until):
            assert wait_until == "domcontentloaded"
            return None

        def wait_for_timeout(self, _delay):
            pass

        def on(self, _event, _handler):
            pass

        def remove_listener(self, _event, _handler):
            pass

        def evaluate(self, _script):
            return True

        class keyboard:
            @staticmethod
            def type(_text, **_kwargs):
                pass

            @staticmethod
            def press(_key):
                pass

    page = Page()

    class Browser:
        contexts = [
            type(
                "Context",
                (),
                {"add_init_script": lambda _self, _script: None},
            )()
        ]

    class Playwright:
        chromium = type(
            "Chromium",
            (),
            {"connect_over_cdp": lambda _chromium, _endpoint: Browser()},
        )()

        def stop(self):
            return None

    class SyncPlaywright:
        def __enter__(self):
            return Playwright()

        def __exit__(self, *_args):
            return False

        def start(self):
            return Playwright()

        def stop(self):
            return None

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: SyncPlaywright())
    monkeypatch.setattr(
        provider,
        "_reuse_or_create_collection_page",
        lambda *_args, **_kwargs: (page, False),
    )
    monkeypatch.setattr(provider, "_raise_for_public_search_block", lambda _page: None)
    monkeypatch.setattr(provider, "_random_delay_ms", lambda *_args: 1)
    monkeypatch.setattr(
        provider,
        "_ensure_public_search_video_filter",
        lambda _page: (
            calls.append("video_filter")
            or type("Outcome", (), {"receipt": "已应用", "warning": None})()
        ),
    )
    monkeypatch.setattr(
        provider,
        "_ensure_public_search_multi_column",
        lambda _page: (
            calls.append("layout")
            or type("Layout", (), {"mode": "multi_column", "warning": None})()
        ),
    )
    monkeypatch.setattr(
        provider,
        "_apply_public_search_time_filter",
        lambda _page, **_kwargs: (
            calls.append("time_filter")
            or type("Outcome", (), {"receipt": "已应用", "warning": None})()
        ),
    )
    monkeypatch.setattr(
        provider,
        "_collect_public_douyin_search_rows",
        lambda _page, **_kwargs: (calls.append("scroll") or [], None),
    )

    rows, errors = provider._collect_public_search_rows(
        "贴标机",
        target_limit=10,
        scan_limit=100,
        observed_at=observed_at,
        published_after=observed_at - timedelta(days=7),
    )

    assert rows == []
    assert errors == []
    assert calls == ["video_filter", "layout", "time_filter", "scroll"]


def test_public_search_does_not_navigate_again_after_goto_error(tmp_path, monkeypatch):
    from playwright.sync_api import Error as PlaywrightError

    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
    )
    goto_calls: list[str] = []
    connection_calls: list[str] = []

    class Page:
        def set_default_timeout(self, _timeout):
            pass

        def goto(self, url, *, wait_until):
            assert wait_until == "domcontentloaded"
            goto_calls.append(url)
            raise PlaywrightError("navigation failed")

        def on(self, _event, _handler):
            pass

        def remove_listener(self, _event, _handler):
            pass

        def evaluate(self, _script):
            return True

        class keyboard:
            @staticmethod
            def type(_text, **_kwargs):
                pass

            @staticmethod
            def press(_key):
                pass

    page = Page()

    class Browser:
        contexts = [
            type(
                "Context",
                (),
                {"add_init_script": lambda _self, _script: None},
            )()
        ]

    class Chromium:
        def connect_over_cdp(self, endpoint):
            connection_calls.append(endpoint)
            return Browser()

    class Playwright:
        chromium = Chromium()

        def stop(self):
            return None

    class SyncPlaywright:
        def __enter__(self):
            return Playwright()

        def __exit__(self, *_args):
            return False

        def start(self):
            return Playwright()

        def stop(self):
            return None

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: SyncPlaywright())
    monkeypatch.setattr(
        provider,
        "_reuse_or_create_collection_page",
        lambda *_args, **_kwargs: (page, False),
    )

    with pytest.raises(LicensedProviderError, match="未再次打开搜索页"):
        provider._collect_public_search_rows(
            "贴标机",
            target_limit=10,
            scan_limit=100,
            observed_at=datetime.fromisoformat("2026-08-04T12:00:00+08:00"),
            published_after=None,
        )

    assert len(connection_calls) == 1
    assert len(goto_calls) == 1


def test_public_search_reuses_healthy_same_domain_tab_without_navigation(tmp_path):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
    )
    events: list[tuple[str, str]] = []

    class Keyboard:
        def press(self, key):
            events.append(("press", key))

        def type(self, value):
            events.append(("type", value))

    class Page:
        url = "https://www.douyin.com/search/旧词?type=general"
        keyboard = Keyboard()

        def goto(self, *_args, **_kwargs):
            raise AssertionError("healthy search tab must not navigate")

        def evaluate(self, _script):
            return True

        def wait_for_timeout(self, _milliseconds):
            return None

    provider._prepare_public_search_input(Page(), "新词")

    assert events == [
        ("press", "Control+A"),
        ("press", "Backspace"),
        ("type", "新词"),
        ("press", "Enter"),
    ]


def test_crawler_browser_paths_keep_required_anti_detection_setup():
    root = Path(__file__).resolve().parents[1]
    drission_source = (
        root / "src/adapters/drission_browser.py"
    ).read_text(encoding="utf-8")
    platform_source = (
        root / "src/adapters/platform_browser_search.py"
    ).read_text(encoding="utf-8")
    assert "ANTI_DETECTION_INIT_SCRIPT" in drission_source
    assert "--disable-blink-features=AutomationControlled" in drission_source
    assert "--disable-infobars" in drission_source
    assert "context.add_init_script(ANTI_DETECTION_INIT_SCRIPT)" in platform_source


def test_public_search_multi_layout_marks_data_availability_without_fake_metrics():
    observed_at = datetime.fromisoformat("2026-08-03T12:00:00+08:00")
    items, errors, _, _ = LocalDouyinBrowserSearchProvider._to_public_search_items(
        [
            {
                "item_id": "7538955201693994395",
                "href": "https://www.douyin.com/video/7538955201693994395",
                "title": "贴标机默认布局测试",
                "search_layout": "multi_column",
                "search_time_filter": "已应用平台筛选：一周内（7天）",
            }
        ],
        keyword="贴标机",
        observed_at=observed_at,
        published_after=None,
        limit=1,
    )

    assert errors == []
    assert items[0].metrics.plays is None
    assert items[0].metrics.comments is None
    assert "布局=多列" in items[0].evidence
    assert "发布时间筛选=已应用平台筛选：一周内（7天）" in items[0].evidence
    assert all("默认卡片" not in warning for warning in items[0].data_quality_warnings)


def test_public_search_item_records_local_only_filter_without_fake_metrics():
    observed_at = datetime.fromisoformat("2026-08-04T12:00:00+08:00")
    warning = "抖音官网没有可用的 7 天发布时间选项；平台筛选未应用，仅按页面可核验发布时间在本地过滤。"
    items, errors, _, _ = LocalDouyinBrowserSearchProvider._to_public_search_items(
        [
            {
                "item_id": "7538955201693994499",
                "href": "https://www.douyin.com/video/7538955201693994499",
                "title": "贴标机本地时间过滤回退",
                "search_layout": "single_column",
                "search_time_filter": "平台筛选未应用；仅本地过滤",
                "search_time_filter_warning": warning,
            }
        ],
        keyword="贴标机",
        observed_at=observed_at,
        published_after=observed_at - timedelta(days=7),
        limit=1,
    )

    assert errors == []
    assert "发布时间筛选=平台筛选未应用；仅本地过滤" in items[0].evidence
    assert warning in items[0].data_quality_warnings
    assert items[0].metrics.plays is None
    assert items[0].metrics.likes is None


def test_public_search_discards_author_only_keyword_matches():
    observed_at = datetime.fromisoformat("2026-08-03T12:00:00+08:00")
    items, errors, filtered, _ = (
        LocalDouyinBrowserSearchProvider._to_public_search_items(
            [
                {
                    "item_id": "7538955201693994401",
                    "href": "https://www.douyin.com/video/7538955201693994401",
                    "title": "贴标机源头厂家现场演示 #贴标机",
                    "author_name": "包装设备工厂",
                },
                {
                    "item_id": "7538955201693994402",
                    "href": "https://www.douyin.com/video/7538955201693994402",
                    "title": "积木零件检测设备演示",
                    "author_name": "即时打印贴标机小张",
                },
            ],
            keyword="贴标机",
            observed_at=observed_at,
            published_after=None,
            limit=30,
        )
    )

    assert errors == []
    assert [item.platform_item_id for item in items] == ["7538955201693994401"]
    assert filtered["relevance"] == 1


@pytest.mark.parametrize(
    "extractor",
    [
        LocalDouyinBrowserSearchProvider._extract_douyin_search_rows,
        LocalDouyinBrowserSearchProvider._extract_public_douyin_search_rows,
    ],
)
def test_rendered_douyin_search_extractors_read_complete_react_statistics(extractor):
    scripts: list[str] = []

    class Body:
        def evaluate(self, script):
            scripts.append(script)
            return []

    class Page:
        def locator(self, selector):
            assert selector == "body"
            return Body()

    assert extractor(Page()) == []
    script = scripts[0]
    for statistic in (
        "play_count",
        "digg_count",
        "comment_count",
        "share_count",
        "collect_count",
    ):
        assert statistic in script
    assert "value !== undefined && value !== null && value !== ''" in script
    if extractor is LocalDouyinBrowserSearchProvider._extract_public_douyin_search_rows:
        for statistic in (
            "aweme_statistics",
            "interact_info",
            "forward_count",
            "collect_cnt",
        ):
            assert statistic in script
        assert "metricFromDom" in script
        assert "播放量" in script
        assert "评论数" in script
        assert "video?.statistics" in script
        assert "video?.aweme_statistics" in script
        assert "video?.interact_info" in script
        assert "comment_count', 'commentCount', 'comment_cnt', 'comments" in script


def test_browser_provider_reports_login_requirement_without_running_session(tmp_path):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29998,
    )

    status = provider.session_status()

    assert status.running is False
    assert status.login_required is True
    capability = provider.capabilities()
    assert capability.mode == ProviderMode.LOCAL_BROWSER
    assert capability.provider_name == "douyin_local_browser"
    assert capability.max_page_size == 100


def test_browser_provider_capabilities_do_not_probe_the_local_debug_port(
    tmp_path, monkeypatch
):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29995,
    )
    monkeypatch.setattr(
        provider, "session_status", lambda: pytest.fail("capabilities must stay static")
    )

    capability = provider.capabilities()

    assert capability.provider_name == "douyin_local_browser"
    assert capability.permission_status == "local_browser_login_required"


def test_browser_provider_reports_missing_playwright_dependency(tmp_path, monkeypatch):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29997,
    )
    monkeypatch.setattr(provider, "_browser_engine_available", lambda: False)
    monkeypatch.setattr(
        provider, "_browser_executable", lambda: tmp_path / "chrome.exe"
    )

    capability = provider.capabilities()
    status = provider.session_status()

    assert capability.enabled is False
    assert capability.missing_configuration == ["Playwright Python 依赖"]
    assert status.phase == "dependency_missing"
    assert "Playwright Python 依赖" in status.message


def test_browser_provider_checks_the_configured_browser_channel(tmp_path, monkeypatch):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        browser_channel="msedge",
        debug_port=29996,
    )
    monkeypatch.setattr(provider, "_browser_engine_available", lambda: True)
    monkeypatch.setattr(provider, "_browser_executable", lambda: None)

    capability = provider.capabilities()

    assert capability.enabled is False
    assert capability.missing_configuration == ["Microsoft Edge"]


def test_browser_login_reset_never_runs_without_explicit_confirmation(tmp_path, monkeypatch):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29991,
    )
    with pytest.raises(LicensedProviderError, match="需要明确确认"):
        provider.reset_login_state()


def test_login_button_reveals_existing_browser_when_session_is_running(
    tmp_path, monkeypatch
):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29994,
    )
    revealed: list[int] = []
    ready = BrowserSessionStatus(True, True, False, True, "ready", "已连接")
    monkeypatch.setattr(provider, "session_status", lambda: ready)
    monkeypatch.setattr(
        "src.adapters.douyin_browser_search.reveal_browser_window",
        revealed.append,
    )

    status = provider.open_login_browser()

    assert status.ready_to_crawl is True
    assert revealed == [29994]


def test_login_button_reveals_existing_waiting_login_window(tmp_path, monkeypatch):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile", debug_port=29990
    )
    ready = BrowserSessionStatus(True, True, True, False, "waiting_login", "等待登录")
    revealed: list[int] = []
    monkeypatch.setattr(provider, "session_status", lambda: ready)
    monkeypatch.setattr(
        "src.adapters.douyin_browser_search.reveal_browser_window",
        revealed.append,
    )

    status = provider.open_login_browser()

    assert status.login_required is True
    assert revealed == [29990]


def test_automatic_hotspot_start_stays_minimized(tmp_path, monkeypatch):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29993,
    )
    launched: list[list[str]] = []
    closed = BrowserSessionStatus(True, False, True, False, "browser_closed", "未打开")
    monkeypatch.setattr(provider, "session_status", lambda: closed)
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(
        provider, "_browser_executable", lambda: tmp_path / "chrome.exe"
    )
    monkeypatch.setattr(
        "src.adapters.douyin_browser_search.subprocess.Popen",
        lambda args, **kwargs: launched.append(args),
    )
    monkeypatch.setattr(
        "src.adapters.douyin_browser_search.time.sleep", lambda _seconds: None
    )

    provider.start_login_browser()

    assert "--start-minimized" in launched[0]
    assert not any(arg.startswith("--window-position=") for arg in launched[0])
    assert "--new-window" not in launched[0]


def test_public_douyin_login_browser_opens_official_site_not_hotspot(
    tmp_path, monkeypatch
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29992,
    )
    launched: list[list[str]] = []
    closed = BrowserSessionStatus(True, False, True, False, "browser_closed", "未打开")
    monkeypatch.setattr(provider, "session_status", lambda: closed)
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(
        provider, "_browser_executable", lambda: tmp_path / "chrome.exe"
    )
    monkeypatch.setattr(
        "src.adapters.douyin_browser_search.subprocess.Popen",
        lambda args, **kwargs: launched.append(args),
    )
    monkeypatch.setattr(
        "src.adapters.douyin_browser_search.time.sleep", lambda _seconds: None
    )

    provider.open_login_browser()

    assert launched[0][-1] == "https://www.douyin.com/"
    assert "douhot.douyin.com" not in launched[0][-1]


@pytest.mark.parametrize(
    ("phase", "running"),
    [
        ("browser_closed", False),
        ("waiting_login", True),
    ],
)
def test_public_douyin_status_never_uses_hotspot_wording(
    tmp_path, monkeypatch, phase, running
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29987,
    )
    raw_status = BrowserSessionStatus(
        True,
        running,
        True,
        False,
        phase,
        "热点宝专用浏览器状态。",
    )
    monkeypatch.setattr(
        LocalDouyinBrowserSearchProvider,
        "session_status",
        lambda _provider: raw_status,
    )

    status = provider.session_status()

    assert "热点宝" not in status.message
    assert "抖音官网" in status.message


def test_running_public_douyin_browser_is_ready_for_one_search_attempt(
    tmp_path, monkeypatch
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29986,
    )
    raw_status = BrowserSessionStatus(
        True,
        True,
        True,
        False,
        "browser_open",
        "Chrome 已打开。",
    )
    monkeypatch.setattr(
        LocalDouyinBrowserSearchProvider,
        "session_status",
        lambda _provider: raw_status,
    )

    class _CleanPages:
        def open(self, url, timeout=1.5):
            return self

        def read(self):
            return '[{"title": "抖音 - 搜索结果"}]'.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        douyin_browser_module, "_LOCAL_DEBUG_OPENER", _CleanPages()
    )

    status = provider.session_status()

    assert status.running is True
    assert status.login_required is False
    assert status.ready_to_crawl is True
    assert status.phase == "ready"
    assert "实际搜索时会核验登录状态或安全验证" in status.message


def test_open_public_douyin_browser_requires_login_when_unconfirmed(
    tmp_path, monkeypatch
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29986,
    )
    raw_status = BrowserSessionStatus(
        True,
        True,
        True,
        False,
        "browser_open",
        "Chrome 已打开。",
    )
    monkeypatch.setattr(
        LocalDouyinBrowserSearchProvider,
        "session_status",
        lambda _provider: raw_status,
    )

    class _Unreachable:
        def open(self, url, timeout=1.5):
            raise OSError("connection refused")

    monkeypatch.setattr(douyin_browser_module, "_LOCAL_DEBUG_OPENER", _Unreachable())

    status = provider.session_status()

    assert status.running is True
    assert status.login_required is True
    assert status.ready_to_crawl is False
    assert status.phase == "browser_open"


class _WaitFakePage:
    """Fake Playwright page; wait_for_timeout advances the challenge counter."""

    def __init__(self, counter: dict[str, int]):
        self.counter = counter
        self.closed = False

    def wait_for_timeout(self, milliseconds):
        self.counter["calls"] += 1

    def is_closed(self):
        return self.closed


def _raise_while_count_lt(counter: dict[str, int], threshold: int):
    def fake_block(_provider, page):
        if counter["calls"] < threshold:
            raise LicensedProviderError(
                "抖音官网出现可见安全验证，后台检索已停止。",
                kind=ProviderErrorKind.AUTHORIZATION,
                code="public_search_verification",
            )
        return None

    return fake_block


def test_wait_for_manual_review_continues_once_slider_cleared(tmp_path, monkeypatch):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29986,
    )
    counter = {"calls": 0}
    monkeypatch.setattr(
        LocalDouyinBrowserSearchProvider,
        "_raise_for_public_search_block",
        _raise_while_count_lt(counter, threshold=2),
    )
    monkeypatch.setattr(
        douyin_browser_module, "reveal_browser_window", lambda *args, **kwargs: True
    )

    resolved = provider._wait_for_public_search_manual_review(
        _WaitFakePage(counter), timeout_seconds=30
    )

    assert resolved is True
    assert counter["calls"] >= 2


def test_wait_for_manual_review_times_out_when_slider_never_clears(
    tmp_path, monkeypatch
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29986,
    )
    counter = {"calls": 0}
    monkeypatch.setattr(
        LocalDouyinBrowserSearchProvider,
        "_raise_for_public_search_block",
        _raise_while_count_lt(counter, threshold=10 ** 9),
    )
    monkeypatch.setattr(
        douyin_browser_module, "reveal_browser_window", lambda *args, **kwargs: True
    )

    resolved = provider._wait_for_public_search_manual_review(
        _WaitFakePage(counter), timeout_seconds=2
    )

    assert resolved is False


def test_wait_for_manual_review_aborts_when_window_closed(tmp_path, monkeypatch):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29986,
    )
    counter = {"calls": 0}
    monkeypatch.setattr(
        LocalDouyinBrowserSearchProvider,
        "_raise_for_public_search_block",
        _raise_while_count_lt(counter, threshold=10 ** 9),
    )
    monkeypatch.setattr(
        douyin_browser_module, "reveal_browser_window", lambda *args, **kwargs: True
    )

    page = _WaitFakePage(counter)
    page.closed = True

    resolved = provider._wait_for_public_search_manual_review(
        page, timeout_seconds=30
    )

    assert resolved is False


def test_hotspot_keyword_is_typed_gradually_before_search():
    actions: list[tuple[str, object]] = []

    class FakeTarget:
        def is_visible(self):
            return True

        def click(self):
            actions.append(("click", None))

        def press(self, key):
            actions.append(("press", key))

        def type(self, value, *, delay):
            actions.append(("type", (value, delay)))

    class FakeLocator:
        first = FakeTarget()

        def count(self):
            return 1

    class FakePage:
        def locator(self, selector):
            assert selector == "input[placeholder*='搜索']"
            return FakeLocator()

    LocalDouyinBrowserSearchProvider._fill_hotspot_keyword(FakePage(), "法国油画")

    assert actions[:3] == [
        ("click", None),
        ("press", "Control+A"),
        ("press", "Backspace"),
    ]
    assert actions[-1] == ("press", "Enter")
    keyword, delay = actions[3][1]
    assert keyword == "法国油画"
    assert 120 <= delay <= 220


def test_hotspot_browser_action_delay_uses_fresh_random_range(monkeypatch):
    requested_bounds: list[tuple[int, int]] = []

    class FakeRandom:
        def randint(self, lower, upper):
            requested_bounds.append((lower, upper))
            return 617

    monkeypatch.setattr(
        "src.adapters.douyin_browser_search.random.SystemRandom",
        FakeRandom,
    )

    assert LocalDouyinBrowserSearchProvider._random_delay_ms(450, 850) == 617
    assert requested_bounds == [(450, 850)]


def test_hotspot_keeps_readable_cards_for_table_side_filtering():
    observed_at = datetime.fromisoformat("2026-07-24T12:00:00+08:00")
    rows = [
        {
            "item_id": "7538955201693994298",
            "href": "https://www.douyin.com/video/7538955201693994298",
            "title": "租房避坑：签合同前先看这三点",
            "duration": 33,
            "plays": 5000,
            "likes": 300,
            "list_type": 1001,
            "list_labels": ["视频总榜", "高点赞率"],
            "window_hours": 168,
        },
        {
            "item_id": "7538955201693994299",
            "href": "https://www.douyin.com/video/7538955201693994299",
            "title": "租房图片清单",
            "duration": 0,
            "plays": 9000,
        },
        {
            "item_id": "7538955201693994300",
            "href": "https://www.douyin.com/video/7538955201693994300",
            "title": "租房预算怎么做",
            "duration": 20,
            "plays": 1000,
        },
        {
            "item_id": "7538955201693994301",
            "href": "https://www.douyin.com/video/7538955201693994301",
            "title": "买房签约流程",
            "duration": 20,
            "plays": 9000,
        },
    ]

    items, low_incremental_items, errors, filtered = (
        LocalDouyinBrowserSearchProvider._to_items(rows, "租房", observed_at, 100)
    )

    assert errors == []
    assert low_incremental_items == []
    assert [item.platform_item_id for item in items] == [
        "7538955201693994298",
        "7538955201693994299",
        "7538955201693994300",
        "7538955201693994301",
    ]
    assert filtered == {
        "duration": 0,
        "incremental_plays": 0,
        "relevance": 0,
        "quality": 0,
    }
    assert "视频总榜|高点赞率" in items[0].evidence
    assert items[1].duration_seconds is None
    assert "热点宝未返回视频时长。" in items[1].data_quality_warnings


def test_hotspot_keeps_low_interaction_rows_in_the_main_result_table():
    observed_at = datetime.fromisoformat("2026-07-24T12:00:00+08:00")
    rows = [
        {
            "item_id": "7538955201693994300",
            "href": "https://www.douyin.com/video/7538955201693994300",
            "title": "租房预算怎么做",
            "duration": 20,
            "plays": 1000,
            "list_type": 1001,
            "list_label": "视频总榜",
            "window_hours": 1,
        },
        {
            "item_id": "7538955201693994302",
            "href": "https://www.douyin.com/video/7538955201693994302",
            "title": "租房合同如何看",
            "duration": 22,
            "plays": 800,
            "list_type": 1001,
            "list_label": "视频总榜",
            "window_hours": 1,
        },
    ]

    items, low_incremental_items, errors, filtered = (
        LocalDouyinBrowserSearchProvider._to_items(rows, "租房", observed_at, 100)
    )

    assert [item.platform_item_id for item in items] == [
        "7538955201693994300",
        "7538955201693994302",
    ]
    assert errors == []
    assert filtered["incremental_plays"] == 0
    assert filtered["quality"] == 0
    assert low_incremental_items == []


def test_exact_topic_keeps_videos_even_when_the_title_omits_the_keyword():
    observed_at = datetime.fromisoformat("2026-07-28T12:00:00+08:00")

    items, low_incremental_items, errors, filtered = (
        LocalDouyinBrowserSearchProvider._to_items(
            [
                {
                    "item_id": "7538955201693994310",
                    "href": "https://www.douyin.com/video/7538955201693994310",
                    "title": "老板别再靠打折拉新了",
                    "duration": 28,
                    "likes": 500,
                    "published_text": "2026-07-20T12:00:00+08:00",
                    "topic_exact": True,
                    "topic_name": "餐饮获客",
                    "source_kind": "topic_board",
                    "list_type": 2001,
                    "list_label": "话题榜",
                    "window_hours": 168,
                }
            ],
            "餐饮获客",
            observed_at,
            3,
        )
    )

    assert errors == []
    assert low_incremental_items == []
    assert filtered == {
        "duration": 0,
        "incremental_plays": 0,
        "relevance": 0,
        "quality": 0,
    }
    assert [item.platform_item_id for item in items] == ["7538955201693994310"]
    assert "严格话题=1" in items[0].evidence
    assert "来源=topic_board" in items[0].evidence


def test_douyin_search_keeps_exact_video_without_hotspot_incremental_plays():
    observed_at = datetime.fromisoformat("2026-07-28T12:00:00+08:00")

    items, low_incremental_items, errors, filtered = (
        LocalDouyinBrowserSearchProvider._to_items(
            [
                {
                    "item_id": "7538955201693994311",
                    "href": "https://www.douyin.com/video/7538955201693994311",
                    "title": "餐饮获客的三个低成本方法",
                    "duration": 36,
                    "likes": 500,
                    "published_text": "2026-07-20T12:00:00+08:00",
                    "source_kind": "douyin_search",
                    "list_type": 3001,
                    "list_label": "抖音搜索",
                    "window_hours": 168,
                }
            ],
            "餐饮获客",
            observed_at,
            3,
        )
    )

    assert errors == []
    assert low_incremental_items == []
    assert filtered == {
        "duration": 0,
        "incremental_plays": 0,
        "relevance": 0,
        "quality": 0,
    }
    assert items[0].metrics.plays is None
    assert "抖音搜索" in items[0].evidence


def test_douyin_search_collects_multiple_rendered_viewports(tmp_path, monkeypatch):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )
    rounds = [
        [{"item_id": "one", "title": "第一条"}],
        [{"item_id": "one", "title": "第一条"}, {"item_id": "two", "title": "第二条"}],
        [
            {"item_id": "two", "title": "第二条"},
            {"item_id": "three", "title": "第三条"},
        ],
        [{"item_id": "three", "title": "第三条"}],
        [{"item_id": "three", "title": "第三条"}],
    ]
    calls = []

    class FakePage:
        def evaluate(self, script):
            calls.append(("scroll", script))

        def wait_for_timeout(self, delay):
            calls.append(("wait", delay))

    monkeypatch.setattr(
        provider,
        "_extract_douyin_search_rows",
        lambda page: rounds.pop(0),
    )
    monkeypatch.setattr(provider, "_random_delay_ms", lambda *_: 1)

    rows = provider._collect_douyin_search_rows(FakePage())

    assert [row["item_id"] for row in rows] == ["one", "two", "three"]
    assert len([call for call in calls if call[0] == "scroll"]) == 4


def test_quality_signals_are_retained_without_hiding_lower_quality_rows():
    observed_at = datetime.fromisoformat("2026-07-28T12:00:00+08:00")
    rows = [
        {
            "item_id": "7538955201693994312",
            "href": "https://www.douyin.com/video/7538955201693994312",
            "title": "餐饮获客低赞新视频",
            "duration": 30,
            "likes": 99,
            "published_text": "2026-07-27T12:00:00+08:00",
            "source_kind": "topic_board",
            "list_type": 2001,
        },
        {
            "item_id": "7538955201693994313",
            "href": "https://www.douyin.com/video/7538955201693994313",
            "title": "餐饮获客旧视频",
            "duration": 30,
            "likes": 100,
            "published_text": "2025-01-01T12:00:00+08:00",
            "source_kind": "topic_board",
            "list_type": 2001,
        },
        {
            "item_id": "7538955201693994314",
            "href": "https://www.douyin.com/video/7538955201693994314",
            "title": "餐饮获客优质视频",
            "duration": 30,
            "likes": 500,
            "published_text": "2026-07-20T12:00:00+08:00",
            "source_kind": "topic_board",
            "list_type": 2001,
        },
    ]

    items, _, _, filtered = LocalDouyinBrowserSearchProvider._to_items(
        rows, "餐饮获客", observed_at, 3
    )

    assert [item.platform_item_id for item in items] == [
        "7538955201693994314",
        "7538955201693994313",
        "7538955201693994312",
    ]
    assert filtered["quality"] == 0
    assert "质量口径=作品累计点赞/发布天数" in items[0].evidence


def test_source_order_keeps_video_total_before_topic_and_search():
    observed_at = datetime.fromisoformat("2026-07-28T12:00:00+08:00")
    rows = [
        {
            "item_id": "7538955201693994315",
            "href": "https://www.douyin.com/video/7538955201693994315",
            "title": "餐饮获客视频总榜",
            "duration": 30,
            "likes": 100,
            "plays": 5000,
            "window_hours": 168,
            "source_kind": "video_board",
            "list_type": 1001,
        },
        {
            "item_id": "7538955201693994316",
            "href": "https://www.douyin.com/video/7538955201693994316",
            "title": "话题视频",
            "duration": 30,
            "likes": 10000,
            "published_text": "2026-07-20T12:00:00+08:00",
            "topic_exact": True,
            "source_kind": "topic_board",
            "list_type": 2001,
        },
        {
            "item_id": "7538955201693994317",
            "href": "https://www.douyin.com/video/7538955201693994317",
            "title": "餐饮获客搜索视频",
            "duration": 30,
            "likes": 100000,
            "published_text": "2026-07-20T12:00:00+08:00",
            "source_kind": "douyin_search",
            "list_type": 3001,
        },
    ]

    items, _, _, _ = LocalDouyinBrowserSearchProvider._to_items(
        rows, "餐饮获客", observed_at, 3
    )

    assert [item.platform_item_id for item in items] == [
        "7538955201693994315",
        "7538955201693994316",
        "7538955201693994317",
    ]


def test_hotspot_uses_visible_publication_time_when_available():
    observed_at = datetime.fromisoformat("2026-07-24T12:00:00+08:00")
    items, _, _, _ = LocalDouyinBrowserSearchProvider._to_items(
        [
            {
                "item_id": "7538955201693994298",
                "href": "https://www.douyin.com/video/7538955201693994298",
                "title": "租房避坑：签合同前先看这三点",
                "duration": 33,
                "plays": 5000,
                "likes": 500,
                "published_text": "2026-07-23T09:30:00+08:00",
            }
        ],
        "租房",
        observed_at,
        10,
    )

    assert items[0].published_at == datetime.fromisoformat("2026-07-23T09:30:00+08:00")
    assert items[0].data_quality_warnings == []


def test_search_candidate_without_publication_time_is_retained_and_marked_unreliable():
    observed_at = datetime.fromisoformat("2026-07-24T12:00:00+08:00")
    items, _, _, _ = LocalDouyinBrowserSearchProvider._to_items(
        [
            {
                "item_id": "7538955201693994298",
                "href": "https://www.douyin.com/video/7538955201693994298",
                "title": "租房避坑：签合同前先看这三点",
                "duration": 33,
                "likes": 500,
            }
        ],
        "租房",
        observed_at,
        10,
    )

    assert len(items) == 1
    assert items[0].published_at == observed_at
    assert items[0].published_at_reliable is False
    assert "未取得有效发布时间" in items[0].data_quality_warnings[0]


@pytest.mark.parametrize("window_hours", [1, 24, 72, 168])
def test_hotspot_search_passes_the_selected_statistical_window(
    tmp_path, monkeypatch, window_hours
):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29995,
        clock=lambda: datetime.fromisoformat("2026-07-24T12:00:00+08:00"),
    )
    captured: dict[str, int] = {}
    monkeypatch.setattr(
        provider,
        "capabilities",
        lambda: type("Capability", (), {"enabled": True, "max_page_size": 100})(),
    )
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: type("Status", (), {"running": True, "message": "ready"})(),
    )

    def collect(keyword: str, *, window_hours: int, observed_at, target_limit: int):
        captured["window_hours"] = window_hours
        return [
            {
                "item_id": "7538955201693994298",
                "href": "https://www.douyin.com/video/7538955201693994298",
                "title": "数字人近况",
                "duration": 15,
                "plays": 1201,
                "likes": 120,
                "list_type": 1001,
                "list_label": "视频总榜",
                "window_hours": window_hours,
            }
        ], []

    monkeypatch.setattr(provider, "_collect_hotspot_rows", collect)

    page = provider.search(
        Platform.DOUYIN,
        "数字人",
        published_after=datetime.fromisoformat("2026-07-01T12:00:00+08:00"),
        limit=10,
        idempotency_key="window-test",
        hotspot_window_hours=window_hours,
    )

    assert captured["window_hours"] == window_hours
    assert f":{window_hours}h:" in page.items[0].evidence


def test_hotspot_search_rejects_an_unsupported_statistical_window(tmp_path):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29994,
    )

    with pytest.raises(LicensedProviderError, match="榜单周期"):
        provider._resolve_hotspot_window_hours(2)


def test_hotspot_search_keeps_low_incremental_items_in_the_main_result_table(
    tmp_path, monkeypatch
):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29993,
        clock=lambda: datetime.fromisoformat("2026-07-24T12:00:00+08:00"),
    )
    monkeypatch.setattr(
        provider,
        "capabilities",
        lambda: type("Capability", (), {"enabled": True, "max_page_size": 100})(),
    )
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: type("Status", (), {"running": True, "message": "ready"})(),
    )
    monkeypatch.setattr(
        provider,
        "_collect_hotspot_rows",
        lambda keyword, *, window_hours, observed_at, target_limit: (
            [
                {
                    "item_id": "7538955201693994300",
                    "href": "https://www.douyin.com/video/7538955201693994300",
                    "title": "租房预算怎么做",
                    "duration": 20,
                    "plays": 800,
                    "likes": 120,
                    "list_type": 1001,
                    "list_label": "视频总榜",
                    "window_hours": window_hours,
                }
            ],
            [],
        ),
    )

    page = provider.search(
        Platform.DOUYIN,
        "租房",
        published_after=None,
        limit=10,
        idempotency_key="low-incremental-test",
        hotspot_window_hours=1,
    )

    assert [item.metrics.plays for item in page.items] == [800]
    assert page.low_incremental_items == []


def test_automatic_start_minimizes_an_already_running_browser(tmp_path, monkeypatch):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29988,
    )
    minimized_ports: list[int] = []
    ready = BrowserSessionStatus(True, True, False, True, "ready", "已连接")
    monkeypatch.setattr(provider, "session_status", lambda: ready)
    monkeypatch.setattr(
        "src.adapters.douyin_browser_search.minimize_browser_window",
        minimized_ports.append,
    )

    status = provider.start_login_browser()

    assert status.ready_to_crawl is True
    assert minimized_ports == [29988]


def test_normal_hotspot_search_minimizes_the_dedicated_browser(tmp_path, monkeypatch):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29987,
        clock=lambda: datetime.fromisoformat("2026-08-01T12:00:00+08:00"),
    )
    minimized_ports: list[int] = []
    monkeypatch.setattr(
        provider,
        "capabilities",
        lambda: type("Capability", (), {"enabled": True, "max_page_size": 100})(),
    )
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: type("Status", (), {"running": True, "message": "ready"})(),
    )
    monkeypatch.setattr(
        provider,
        "_collect_hotspot_rows",
        lambda keyword, *, window_hours, observed_at, target_limit: ([], []),
    )
    monkeypatch.setattr(
        "src.adapters.douyin_browser_search.minimize_browser_window",
        minimized_ports.append,
    )

    provider.search(
        Platform.DOUYIN,
        "贴标机",
        published_after=None,
        limit=3,
        idempotency_key="minimize-hotspot",
    )

    assert minimized_ports == [29987]


def test_collection_reuses_existing_douyin_tab_without_closing_it(tmp_path):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )

    class ExistingPage:
        url = "https://www.douyin.com/search/%E8%B4%B4%E6%A0%87%E6%9C%BA?type=general"
        closed = False

        def is_closed(self):
            return self.closed

    class Context:
        pages = [ExistingPage()]

        def new_page(self):
            pytest.fail("an existing dedicated Douyin tab should be reused")

    page, created_page = provider._reuse_or_create_collection_page(
        Context(),
        preferred_url_fragments=("www.douyin.com/search/",),
    )

    assert page is Context.pages[0]
    assert created_page is False


def test_collection_never_reuses_a_login_or_oauth_tab(tmp_path):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )

    class Page:
        def __init__(self, url):
            self.url = url

        def is_closed(self):
            return False

    created_page = Page("about:blank")

    class Context:
        pages = [Page("https://open.douyin.com/platform/oauth/connect")]

        def new_page(self):
            return created_page

    page, was_created = provider._reuse_or_create_collection_page(
        Context(),
        preferred_url_fragments=("www.douyin.com/search/",),
    )

    assert page is created_page
    assert was_created is True


def test_public_provider_returns_rendered_candidates_without_hotspot_quality_gate(
    tmp_path, monkeypatch
):
    observed_at = datetime.fromisoformat("2026-08-01T12:00:00+08:00")
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29986,
        clock=lambda: observed_at,
    )
    minimized_ports: list[int] = []
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: type("Status", (), {"running": True, "message": "ready"})(),
    )
    monkeypatch.setattr(
        provider,
        "_collect_public_search_rows",
        lambda keyword, **_: (
            [
                {
                    "item_id": "7538955201693994321",
                    "href": "https://www.douyin.com/video/7538955201693994321",
                    "title": "贴标机常见掉标问题怎么排查",
                    "duration": 26,
                    "likes": 2,
                    "published_text": "2026-07-31",
                },
                {
                    "item_id": "7538955201693994322",
                    "href": "https://www.douyin.com/video/7538955201693994322",
                    "title": "贴标机使用前的三个检查点",
                    "duration": 22,
                    "published_text": "",
                },
                {
                    "item_id": "7538955201693994323",
                    "href": "https://www.douyin.com/video/7538955201693994323",
                    "title": "贴标机旧款操作说明",
                    "duration": 24,
                    "published_text": "2026-07-31",
                },
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        "src.adapters.douyin_browser_search.minimize_browser_window",
        minimized_ports.append,
    )

    page = provider.search(
        Platform.DOUYIN,
        "贴标机",
        published_after=datetime.fromisoformat("2026-07-28T12:00:00+08:00"),
        limit=3,
        idempotency_key="public-search",
    )

    assert provider.capabilities().provider_name == "douyin_public_browser_v2"
    assert provider.capabilities().max_page_size == 100
    assert page.provider == "douyin_public_browser_v2"
    assert [item.platform_item_id for item in page.items] == [
        "7538955201693994321",
        "7538955201693994322",
        "7538955201693994323",
    ]
    assert page.items[0].metrics.likes == 2
    assert page.items[0].evidence.startswith("douyin_public_search:")
    assert page.items[1].data_quality_warnings
    assert page.crawl_stop_reason == "target_reached"
    assert page.crawl_stop_message == "已读取到目标 3 条登录搜索结果。"
    assert page.payload_diagnostic is None
    assert minimized_ports == [29986]


def test_public_provider_accepts_up_to_100_candidates(tmp_path, monkeypatch):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: type("Status", (), {"running": True, "message": "ready"})(),
    )
    monkeypatch.setattr(provider, "_minimize_browser_for_background", lambda: None)
    captured: dict[str, int] = {}

    def collect(keyword, *, target_limit, scan_limit, **_):
        captured["target_limit"] = target_limit
        captured["scan_limit"] = scan_limit
        return [], []

    monkeypatch.setattr(provider, "_collect_public_search_rows", collect)

    page = provider.search(
        Platform.DOUYIN,
        "公开搜索",
        published_after=None,
        limit=100,
        idempotency_key="public-limit",
    )

    assert page.items == []
    assert captured == {"target_limit": 100, "scan_limit": 200}
    with pytest.raises(LicensedProviderError, match="100 条"):
        provider.search(
            Platform.DOUYIN,
            "公开搜索",
            published_after=None,
            limit=101,
            idempotency_key="public-limit-over",
        )


def test_public_search_filters_known_out_of_window_rows_before_returning_items():
    observed_at = datetime.fromisoformat("2026-08-03T12:00:00+08:00")
    published_after = datetime.fromisoformat("2026-08-01T12:00:00+08:00")
    rows = [
        {
            "item_id": "7538955201693994401",
            "href": "https://www.douyin.com/video/7538955201693994401",
            "title": "贴标机旧作品",
            "duration": 18,
            "published_text": "2026-07-31",
        },
        {
            "item_id": "7538955201693994402",
            "href": "https://www.douyin.com/video/7538955201693994402",
            "title": "贴标机新作品",
            "duration": 18,
            "published_text": "2026-08-02",
        },
    ]

    items, errors, _, published_filtered_count = (
        LocalDouyinBrowserSearchProvider._to_public_search_items(
            rows,
            keyword="贴标机",
            observed_at=observed_at,
            published_after=published_after,
            limit=2,
        )
    )

    assert errors == []
    assert [item.platform_item_id for item in items] == ["7538955201693994402"]
    assert published_filtered_count == 1


def test_public_search_scans_past_raw_target_until_qualified_target(
    tmp_path, monkeypatch
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )
    observed_at = datetime.fromisoformat("2026-08-03T12:00:00+08:00")
    published_after = datetime.fromisoformat("2026-08-01T12:00:00+08:00")
    rounds = [
        [
            {
                "item_id": "7538955201693994403",
                "href": "https://www.douyin.com/video/7538955201693994403",
                "title": "贴标机旧作品",
                "duration": 18,
                "published_text": "2026-07-31",
            }
        ],
        [
            {
                "item_id": "7538955201693994403",
                "href": "https://www.douyin.com/video/7538955201693994403",
                "title": "贴标机旧作品",
                "duration": 18,
                "published_text": "2026-07-31",
            },
            {
                "item_id": "7538955201693994404",
                "href": "https://www.douyin.com/video/7538955201693994404",
                "title": "贴标机新作品一",
                "duration": 18,
                "published_text": "2026-08-02",
            },
        ],
        [
            {
                "item_id": "7538955201693994403",
                "href": "https://www.douyin.com/video/7538955201693994403",
                "title": "贴标机旧作品",
                "duration": 18,
                "published_text": "2026-07-31",
            },
            {
                "item_id": "7538955201693994404",
                "href": "https://www.douyin.com/video/7538955201693994404",
                "title": "贴标机新作品一",
                "duration": 18,
                "published_text": "2026-08-02",
            },
            {
                "item_id": "7538955201693994405",
                "href": "https://www.douyin.com/video/7538955201693994405",
                "title": "贴标机新作品二",
                "duration": 18,
                "published_text": "2026-08-02",
            },
        ],
    ]
    calls: list[tuple[str, object]] = []

    class Page:
        def evaluate(self, script):
            calls.append(("scroll", script))

        def wait_for_timeout(self, delay):
            calls.append(("wait", delay))

    def qualifying_count(candidate_rows):
        items, _, _, _ = LocalDouyinBrowserSearchProvider._to_public_search_items(
            candidate_rows,
            keyword="贴标机",
            observed_at=observed_at,
            published_after=published_after,
            limit=2,
        )
        return len(items)

    monkeypatch.setattr(provider, "_raise_for_public_search_block", lambda page: None)
    monkeypatch.setattr(
        provider,
        "_extract_public_douyin_search_rows",
        lambda page: rounds.pop(0),
    )
    monkeypatch.setattr(provider, "_random_delay_ms", lambda *_: 1)

    rows, stop_error = provider._collect_public_douyin_search_rows(
        Page(),
        target_limit=2,
        scan_limit=150,
        qualifying_count=qualifying_count,
    )

    assert [row["item_id"] for row in rows] == [
        "7538955201693994403",
        "7538955201693994404",
        "7538955201693994405",
    ]
    assert stop_error is None
    assert len([call for call in calls if call[0] == "scroll"]) == 2


def test_public_provider_does_not_mark_raw_count_as_target_after_filtering(
    tmp_path, monkeypatch
):
    observed_at = datetime.fromisoformat("2026-08-03T12:00:00+08:00")
    published_after = datetime.fromisoformat("2026-08-01T12:00:00+08:00")
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        clock=lambda: observed_at,
    )
    raw_rows = [
        {
            "item_id": "7538955201693994406",
            "href": "https://www.douyin.com/video/7538955201693994406",
            "title": "贴标机旧作品",
            "duration": 18,
            "published_text": "2026-07-31",
        },
        {
            "item_id": "7538955201693994407",
            "href": "https://www.douyin.com/video/7538955201693994407",
            "title": "贴标机新作品",
            "duration": 18,
            "published_text": "2026-08-02",
        },
    ]
    captured: dict[str, int] = {}
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: type("Status", (), {"running": True, "message": "ready"})(),
    )
    monkeypatch.setattr(provider, "_minimize_browser_for_background", lambda: None)
    monkeypatch.setattr(
        provider,
        "_collect_public_search_rows",
        lambda keyword, *, target_limit, scan_limit, **_: (
            captured.update(target_limit=target_limit, scan_limit=scan_limit)
            or raw_rows,
            [
                ProviderSearchError(
                    kind=ProviderErrorKind.VALIDATION,
                    code="public_search_platform_end",
                    message="公开结果已结束。",
                )
            ],
        ),
    )

    page = provider.search(
        Platform.DOUYIN,
        "贴标机",
        published_after=published_after,
        limit=2,
        idempotency_key="filtered-target",
    )

    assert captured == {"target_limit": 2, "scan_limit": 200}
    assert [item.platform_item_id for item in page.items] == ["7538955201693994407"]
    assert page.crawl_stop_reason == "platform_end"
    assert page.crawl_stop_reason != "target_reached"


@pytest.mark.parametrize(
    ("rows", "error_code", "expected_reason"),
    [
        (
            [
                {
                    "item_id": "7538955201693994393",
                    "href": "https://www.douyin.com/video/7538955201693994393",
                    "title": "公开搜索可见结果已到底",
                    "duration": 18,
                }
            ],
            "public_search_platform_end",
            "platform_end",
        ),
        ([], "public_search_platform_end", "no_more_loaded"),
        ([], "public_search_safety_limit", "safety_limit"),
        ([], "public_search_blocked", "safety_limit"),
        ([], "public_search_service_unavailable", "safety_limit"),
    ],
)
def test_public_provider_carries_structured_collection_stop_reason(
    tmp_path,
    monkeypatch,
    rows,
    error_code,
    expected_reason,
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        clock=lambda: datetime.fromisoformat("2026-08-03T12:00:00+08:00"),
    )
    error_kind = {
        "public_search_safety_limit": ProviderErrorKind.SERVICE,
        "public_search_service_unavailable": ProviderErrorKind.SERVICE,
        "public_search_blocked": ProviderErrorKind.AUTHORIZATION,
    }.get(error_code, ProviderErrorKind.VALIDATION)
    stop_message = f"停止原因：{error_code}"
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: type("Status", (), {"running": True, "message": "ready"})(),
    )
    monkeypatch.setattr(provider, "_minimize_browser_for_background", lambda: None)
    monkeypatch.setattr(
        provider,
        "_collect_public_search_rows",
        lambda keyword, **_: (
            rows,
            [
                ProviderSearchError(
                    kind=error_kind,
                    code=error_code,
                    message=stop_message,
                )
            ],
        ),
    )

    page = provider.search(
        Platform.DOUYIN,
        "公开搜索",
        published_after=None,
        limit=5,
        idempotency_key="stop-reason",
    )

    assert page.crawl_stop_reason == expected_reason
    assert page.crawl_stop_message == stop_message
    if not rows:
        assert page.payload_diagnostic == stop_message


@pytest.mark.parametrize(
    ("error_code", "error_kind", "should_reveal"),
    [
        ("public_search_verification", ProviderErrorKind.AUTHORIZATION, True),
        ("public_search_login_required", ProviderErrorKind.AUTHORIZATION, True),
        ("public_search_rate_limited", ProviderErrorKind.RATE_LIMIT, False),
        ("public_search_blocked", ProviderErrorKind.AUTHORIZATION, False),
    ],
)
def test_public_provider_reveals_only_confirmed_manual_review_pages(
    tmp_path,
    monkeypatch,
    error_code,
    error_kind,
    should_reveal,
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        clock=lambda: datetime.fromisoformat("2026-08-03T12:00:00+08:00"),
    )
    revealed: list[bool] = []
    message = f"停止原因：{error_code}"
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(
        provider,
        "session_status",
        lambda: type("Status", (), {"running": True, "message": "ready"})(),
    )
    monkeypatch.setattr(provider, "_minimize_browser_for_background", lambda: None)
    monkeypatch.setattr(
        provider,
        "_reveal_browser_for_manual_review",
        lambda: revealed.append(True) or True,
    )
    monkeypatch.setattr(
        provider,
        "_collect_public_search_rows",
        lambda keyword, **_: (
            [],
            [
                ProviderSearchError(
                    kind=error_kind,
                    code=error_code,
                    message=message,
                )
            ],
        ),
    )

    page = provider.search(
        Platform.DOUYIN,
        "公开搜索",
        published_after=None,
        limit=5,
        idempotency_key="manual-review",
    )

    assert revealed == ([True] if should_reveal else [])
    if should_reveal:
        assert "已将抖音专用浏览器显示到前台" in page.errors[0].message
    else:
        assert page.errors[0].message == message


@pytest.mark.parametrize("marker", ["安全验证", "登录后即可搜索更多精彩视频"])
def test_public_search_url_and_safety_stop_are_explicit(tmp_path, marker):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )
    assert provider._public_search_url("贴标机") == (
        "https://www.douyin.com/search/%E8%B4%B4%E6%A0%87%E6%9C%BA?type=general"
    )

    class Body:
        def evaluate(self, script, markers):
            assert "getBoundingClientRect" in script
            assert marker in markers
            return True

    class Page:
        def locator(self, selector):
            assert selector == "body"
            return Body()

    with pytest.raises(LicensedProviderError, match="可见登录或安全提示") as exc_info:
        provider._raise_for_public_search_block(Page())

    assert exc_info.value.kind == ProviderErrorKind.AUTHORIZATION
    assert exc_info.value.code == "public_search_login_required"


def test_public_search_stops_on_visible_service_error(tmp_path):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )

    class FrameLocator:
        def count(self):
            return 0

    class Body:
        def evaluate(self, _script, markers):
            return "服务出现异常" in markers

    class Page:
        def locator(self, selector):
            if selector.startswith("iframe["):
                return FrameLocator()
            assert selector == "body"
            return Body()

    with pytest.raises(LicensedProviderError, match="减少搜索次数") as exc_info:
        provider._raise_for_public_search_block(Page())

    assert exc_info.value.kind == ProviderErrorKind.SERVICE
    assert exc_info.value.code == "public_search_service_unavailable"
    assert exc_info.value.retryable is False


def test_public_search_stops_when_a_verification_iframe_is_present(tmp_path):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )

    class Frame:
        def evaluate(self, script):
            assert "rect.width >= 80" in script
            return True

    class FrameLocator:
        def count(self):
            return 1

        def nth(self, index):
            assert index == 0
            return Frame()

    class Page:
        def locator(self, selector):
            assert selector.startswith("iframe[")
            return FrameLocator()

    with pytest.raises(LicensedProviderError, match="出现可见安全验证") as exc_info:
        provider._raise_for_public_search_block(Page())

    assert exc_info.value.kind == ProviderErrorKind.AUTHORIZATION
    assert exc_info.value.code == "public_search_verification"


def test_public_search_ignores_a_hidden_verification_iframe(tmp_path):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )

    class Frame:
        def evaluate(self, _script):
            return False

    class FrameLocator:
        def count(self):
            return 1

        def nth(self, index):
            assert index == 0
            return Frame()

    class Body:
        def evaluate(self, _script, _markers):
            return False

    class Page:
        def locator(self, selector):
            if selector.startswith("iframe["):
                return FrameLocator()
            assert selector == "body"
            return Body()

    provider._raise_for_public_search_block(Page())


def test_public_search_ignores_hidden_login_marker_text(tmp_path):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )

    class FrameLocator:
        def count(self):
            return 0

    class Body:
        def evaluate(self, _script, _markers):
            return False

    class Page:
        def locator(self, selector):
            if selector.startswith("iframe["):
                return FrameLocator()
            assert selector == "body"
            return Body()

    provider._raise_for_public_search_block(Page())


def test_public_search_collects_until_the_requested_count(tmp_path, monkeypatch):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )
    rounds = [
        [{"item_id": "one", "title": "第一条"}],
        [
            {"item_id": "one", "title": "第一条"},
            {"item_id": "two", "title": "第二条"},
        ],
        [
            {"item_id": "two", "title": "第二条"},
            {"item_id": "three", "title": "第三条"},
        ],
    ]
    calls: list[tuple[str, object]] = []

    class Page:
        def evaluate(self, script):
            calls.append(("scroll", script))

        def wait_for_timeout(self, delay):
            calls.append(("wait", delay))

    monkeypatch.setattr(provider, "_raise_for_public_search_block", lambda page: None)
    monkeypatch.setattr(
        provider,
        "_extract_public_douyin_search_rows",
        lambda page: rounds.pop(0),
    )
    monkeypatch.setattr(provider, "_random_delay_ms", lambda *_: 1)

    rows, stop_error = provider._collect_public_douyin_search_rows(
        Page(),
        target_limit=3,
    )

    assert [row["item_id"] for row in rows] == ["one", "two", "three"]
    assert stop_error is None
    assert len([call for call in calls if call[0] == "scroll"]) == 2


def test_public_search_keeps_scrolling_while_the_internal_container_moves(
    tmp_path, monkeypatch
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )
    rounds = [
        [{"item_id": "one", "title": "第一条"}],
        [{"item_id": "one", "title": "第一条"}],
        [{"item_id": "one", "title": "第一条"}],
        [
            {"item_id": "one", "title": "第一条"},
            {"item_id": "two", "title": "第二条"},
        ],
    ]

    class Page:
        def evaluate(self, _script):
            return True

        def wait_for_timeout(self, _delay):
            pass

    monkeypatch.setattr(provider, "_raise_for_public_search_block", lambda page: None)
    monkeypatch.setattr(
        provider,
        "_extract_public_douyin_search_rows",
        lambda page: rounds.pop(0),
    )

    rows, stop_error = provider._collect_public_douyin_search_rows(
        Page(),
        target_limit=2,
    )

    assert [row["item_id"] for row in rows] == ["one", "two"]
    assert stop_error is None


def test_public_search_reports_platform_end_after_visible_results_stop_loading(
    tmp_path, monkeypatch
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )
    rounds = [[{"item_id": "one", "title": "第一条"}]] * 3
    calls: list[tuple[str, object]] = []

    class Page:
        def evaluate(self, script):
            calls.append(("scroll", script))

        def wait_for_timeout(self, delay):
            calls.append(("wait", delay))

    monkeypatch.setattr(provider, "_raise_for_public_search_block", lambda page: None)
    monkeypatch.setattr(
        provider,
        "_extract_public_douyin_search_rows",
        lambda page: rounds.pop(0),
    )
    monkeypatch.setattr(provider, "_random_delay_ms", lambda *_: 1)

    rows, stop_error = provider._collect_public_douyin_search_rows(
        Page(),
        target_limit=5,
    )

    assert [row["item_id"] for row in rows] == ["one"]
    assert stop_error is not None
    assert stop_error.code == "public_search_platform_end"
    assert stop_error.kind == ProviderErrorKind.VALIDATION
    assert "目标 5 条" in stop_error.message
    assert len([call for call in calls if call[0] == "scroll"]) == 2


def test_public_search_reports_verification_that_appears_at_visible_end(
    tmp_path, monkeypatch
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )
    checks = [
        None,
        None,
        None,
        LicensedProviderError("需要人工验证", kind=ProviderErrorKind.AUTHORIZATION),
    ]

    class Page:
        def evaluate(self, _script):
            pass

        def wait_for_timeout(self, _delay):
            pass

    def check_for_block(_page):
        result = checks.pop(0)
        if result is not None:
            raise result

    monkeypatch.setattr(provider, "_raise_for_public_search_block", check_for_block)
    monkeypatch.setattr(
        provider, "_extract_public_douyin_search_rows", lambda _page: []
    )
    monkeypatch.setattr(provider, "_random_delay_ms", lambda *_: 1)

    rows, stop_error = provider._collect_public_douyin_search_rows(
        Page(), target_limit=5
    )

    assert rows == []
    assert stop_error is not None
    assert stop_error.code == "public_search_blocked"
    assert stop_error.kind == ProviderErrorKind.AUTHORIZATION


def test_public_search_does_not_turn_page_when_scrolling_stagnates(
    tmp_path, monkeypatch
):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )
    rounds = [[{"item_id": "one", "title": "第一条"}]] * 3

    class Page:
        def evaluate(self, script):
            pass

        def wait_for_timeout(self, delay):
            pass

    monkeypatch.setattr(provider, "_raise_for_public_search_block", lambda page: None)
    monkeypatch.setattr(
        provider,
        "_extract_public_douyin_search_rows",
        lambda page: rounds.pop(0),
    )
    monkeypatch.setattr(provider, "_random_delay_ms", lambda *_: 1)

    rows, stop_error = provider._collect_public_douyin_search_rows(
        Page(),
        target_limit=3,
    )

    assert [row["item_id"] for row in rows] == ["one"]
    assert stop_error is not None
    assert stop_error.code == "public_search_platform_end"
    assert "本次不再翻页" in stop_error.message


def test_public_search_reports_verification_without_loading_more(tmp_path, monkeypatch):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )
    checks = [
        None,
        LicensedProviderError("需要人工验证", kind=ProviderErrorKind.AUTHORIZATION),
    ]
    calls: list[tuple[str, object]] = []

    class Page:
        def evaluate(self, script):
            calls.append(("scroll", script))

        def wait_for_timeout(self, delay):
            calls.append(("wait", delay))

    def check_for_block(page):
        result = checks.pop(0)
        if result is not None:
            raise result

    monkeypatch.setattr(provider, "_raise_for_public_search_block", check_for_block)
    monkeypatch.setattr(
        provider,
        "_extract_public_douyin_search_rows",
        lambda page: [{"item_id": "one", "title": "第一条"}],
    )
    monkeypatch.setattr(provider, "_random_delay_ms", lambda *_: 1)

    rows, stop_error = provider._collect_public_douyin_search_rows(
        Page(),
        target_limit=5,
    )

    assert [row["item_id"] for row in rows] == ["one"]
    assert stop_error is not None
    assert stop_error.code == "public_search_blocked"
    assert stop_error.kind == ProviderErrorKind.AUTHORIZATION
    assert len([call for call in calls if call[0] == "scroll"]) == 1


def test_public_search_reports_the_safety_loading_limit(tmp_path, monkeypatch):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )
    rounds = [
        [{"item_id": "one", "title": "第一条"}],
        [
            {"item_id": "one", "title": "第一条"},
            {"item_id": "two", "title": "第二条"},
        ],
        [
            {"item_id": "one", "title": "第一条"},
            {"item_id": "two", "title": "第二条"},
            {"item_id": "three", "title": "第三条"},
        ],
    ]

    class Page:
        def evaluate(self, script):
            pass

        def wait_for_timeout(self, delay):
            pass

    monkeypatch.setattr(provider, "_raise_for_public_search_block", lambda page: None)
    monkeypatch.setattr(
        provider,
        "_extract_public_douyin_search_rows",
        lambda page: rounds.pop(0),
    )
    monkeypatch.setattr(
        "src.adapters.douyin_browser_search._PUBLIC_SEARCH_MAX_SCROLL_ROUNDS",
        3,
    )
    monkeypatch.setattr(provider, "_random_delay_ms", lambda *_: 1)

    rows, stop_error = provider._collect_public_douyin_search_rows(
        Page(),
        target_limit=4,
    )

    assert [row["item_id"] for row in rows] == ["one", "two", "three"]
    assert stop_error is not None
    assert stop_error.code == "public_search_safety_limit"
    assert stop_error.kind == ProviderErrorKind.SERVICE


def test_public_search_caps_an_unmet_target_at_200_scanned_rows(tmp_path, monkeypatch):
    provider = LocalDouyinPublicSearchProvider(
        enabled=True, profile_dir=tmp_path / "profile"
    )
    rows = [
        {"item_id": f"row-{index}", "title": f"无关结果 {index}"}
        for index in range(200)
    ]

    class Page:
        def evaluate(self, script):
            return None

        def wait_for_timeout(self, delay):
            return None

    monkeypatch.setattr(provider, "_raise_for_public_search_block", lambda page: None)
    monkeypatch.setattr(
        provider,
        "_extract_public_douyin_search_rows",
        lambda page: rows,
    )

    collected, stop_error = provider._collect_public_douyin_search_rows(
        Page(),
        target_limit=30,
        scan_limit=999,
        qualifying_count=lambda candidate_rows: 0,
    )

    assert len(collected) == 200
    assert stop_error is not None
    assert stop_error.code == "public_search_safety_limit"
    assert "扫描 200/200 条" in stop_error.message


def test_public_search_api_url_detection():
    from src.adapters.douyin_browser_search import (
        LocalDouyinBrowserSearchProvider,
    )

    assert LocalDouyinBrowserSearchProvider._is_public_search_api_url(
        "https://www.douyin.com/aweme/v1/web/general/search/single/?keyword=x"
    )
    assert LocalDouyinBrowserSearchProvider._is_public_search_api_url(
        "https://www.douyin.com/aweme/v1/web/search/item/?keyword=x"
    )
    assert not LocalDouyinBrowserSearchProvider._is_public_search_api_url(
        "https://www.douyin.com/aweme/v1/web/solution/resource/list/?spot_keys=1"
    )
    assert not LocalDouyinBrowserSearchProvider._is_public_search_api_url(
        "https://www.douyin.com/search/abc"
    )


def test_public_search_payload_parsing(tmp_path):

    from project.backend.app.core.config import (
        DOUYIN_BROWSER_DISCOVERY_ENABLED,
        DOUYIN_BROWSER_CHANNEL,
    )
    from src.adapters.douyin_browser_search import (
        LocalDouyinPublicSearchProvider,
    )

    provider = LocalDouyinPublicSearchProvider(
        enabled=DOUYIN_BROWSER_DISCOVERY_ENABLED,
        profile_dir=tmp_path / "profile",
        browser_channel=DOUYIN_BROWSER_CHANNEL,
        debug_port=29998,
        timeout_seconds=5.0,
    )
    payload = {
        "status_code": 0,
        "data": {
            "data": [
                {
                    "aweme_id": "7351234567890123456",
                    "desc": "餐饮获客新思路分享 #餐饮",
                    "create_time": 1735000000,
                    "duration": 23500,
                    "author": {"nickname": "测试作者"},
                    "statistics": {
                        "play_count": 10000,
                        "digg_count": 888,
                        "comment_count": 66,
                        "share_count": 7,
                    },
                },
                {
                    "aweme_id": "7351234567890123457",
                    "desc": "不相关标题",
                    "create_time": 1735000001,
                    "author": {"nickname": "无关作者"},
                    "statistics": {
                        "play_count": 1,
                        "digg_count": 2,
                        "comment_count": 3,
                        "share_count": 4,
                    },
                },
                {"aweme_id": "not-a-number", "desc": "餐饮坏数据"},
                "not-a-dict",
                {"desc": "餐饮无 id"},
            ],
            "has_more": True,
        },
    }
    rows = provider._rows_from_public_search_payload(payload, keyword="餐饮获客")
    assert len(rows) == 2
    assert rows[1]["title"]
    row = rows[0]
    assert row["item_id"] == "7351234567890123456"
    assert row["title"] == "餐饮获客新思路分享 #餐饮"
    assert row["author_name"] == "测试作者"
    assert row["duration"] == 24  # 23500ms -> 24s(四舍五入)
    assert row["plays"] == 10000
    assert row["likes"] == 888
    assert row["comments"] == 66
    assert row["shares"] == 7
    assert row["source_kind"] == "search_api"
    # 不匹配关键词的标题被过滤;无效 id 与非 dict 被过滤
    items, errors, counts, _ = provider._to_public_search_items(
        rows,
        keyword=row["title"],
        observed_at=datetime.fromisoformat("2026-08-03T12:00:00+08:00"),
        published_after=None,
        limit=10,
    )
    assert errors == []
    assert len(items) == 1
    assert counts["relevance"] == 1


def test_public_search_allows_split_keyword_across_description_and_topics():
    observed_at = datetime.fromisoformat("2026-08-03T12:00:00+08:00")
    items, errors, counts, _ = LocalDouyinPublicSearchProvider._to_public_search_items(
        [
            {
                "item_id": "7538955201693994999",
                "title": "餐饮门店怎么做短视频",
                "description": "帮助老板选择设备",
                "hashtags": ["餐饮", "门店经营"],
            }
        ],
        keyword="餐饮设备",
        observed_at=observed_at,
        published_after=None,
        limit=30,
    )

    assert errors == []
    assert counts["relevance"] == 0
    assert len(items) == 1
    assert "关键词联合命中=1" in (items[0].evidence or "")


def test_scroll_and_wait_for_new_rows_returns_when_rows_appear(tmp_path, monkeypatch):
    from project.backend.app.core.config import (
        DOUYIN_BROWSER_DISCOVERY_ENABLED,
        DOUYIN_BROWSER_CHANNEL,
    )
    from src.adapters.douyin_browser_search import (
        LocalDouyinPublicSearchProvider,
    )

    provider = LocalDouyinPublicSearchProvider(
        enabled=DOUYIN_BROWSER_DISCOVERY_ENABLED,
        profile_dir=tmp_path / "profile",
        browser_channel=DOUYIN_BROWSER_CHANNEL,
        debug_port=29999,
        timeout_seconds=5.0,
    )

    class Page:
        def __init__(self):
            self.scrolls = 0
            self.waits = 0

        def evaluate(self, _script):
            self.scrolls += 1

        def wait_for_timeout(self, _ms):
            self.waits += 1

    page = Page()
    counts = iter([5, 5, 5, 9])
    monkeypatch.setattr(provider, "_random_delay_ms", lambda *_: 500)

    result = provider._scroll_and_wait_for_new_rows(
        page, count_rows=lambda: next(counts), max_wait_ms=6_000
    )
    assert result is True
    assert page.scrolls == 1
    assert page.waits >= 2  # 轮询到第 3 次出现新行


def test_scroll_and_wait_for_new_rows_times_out(tmp_path, monkeypatch):
    from project.backend.app.core.config import (
        DOUYIN_BROWSER_DISCOVERY_ENABLED,
        DOUYIN_BROWSER_CHANNEL,
    )
    from src.adapters.douyin_browser_search import (
        LocalDouyinPublicSearchProvider,
    )

    provider = LocalDouyinPublicSearchProvider(
        enabled=DOUYIN_BROWSER_DISCOVERY_ENABLED,
        profile_dir=tmp_path / "profile",
        browser_channel=DOUYIN_BROWSER_CHANNEL,
        debug_port=30000,
        timeout_seconds=5.0,
    )

    class Page:
        def evaluate(self, _script):
            pass

        def wait_for_timeout(self, _ms):
            pass

    result = provider._scroll_and_wait_for_new_rows(
        Page(), count_rows=lambda: 3, max_wait_ms=600
    )
    assert result is False


def test_public_search_payload_stream_format(tmp_path):
    """抖音搜索结果接口是流式块(hex长度行 + JSON 行,含 aweme_info 键)。"""
    import json as _json

    from project.backend.app.core.config import (
        DOUYIN_BROWSER_DISCOVERY_ENABLED,
        DOUYIN_BROWSER_CHANNEL,
    )
    from src.adapters.douyin_browser_search import (
        LocalDouyinPublicSearchProvider,
    )

    provider = LocalDouyinPublicSearchProvider(
        enabled=DOUYIN_BROWSER_DISCOVERY_ENABLED,
        profile_dir=tmp_path / "profile",
        browser_channel=DOUYIN_BROWSER_CHANNEL,
        debug_port=30001,
        timeout_seconds=5.0,
    )
    payload = {
        "status_code": 0,
        "data": [
            {
                "type": 1,
                "aweme_info": {
                    "aweme_id": "7668630123502931252",
                    "desc": "餐饮获客实战分享 #餐饮",
                    "create_time": 1785492093,
                    "author": {"nickname": "餐饮运营笔记"},
                    "statistics": {
                        "play_count": 0,
                        "digg_count": 35394,
                        "comment_count": 7600,
                        "share_count": 22665,
                    },
                    "duration": 213000,
                },
            },
            {"type": 2, "aweme_info": {"aweme_id": "x", "desc": "话题"}},
        ],
        "has_more": True,
    }
    stream_text = f"1bee5\r\n{_json.dumps(payload)}\r\n"
    rows = []
    for line in stream_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
        except Exception:
            continue
        rows.extend(provider._rows_from_public_search_payload(obj, keyword="餐饮获客"))
    assert len(rows) == 1
    row = rows[0]
    assert row["item_id"] == "7668630123502931252"
    assert row["author_name"] == "餐饮运营笔记"
    assert row["comments"] == 7600
    assert row["likes"] == 35394
    assert row["shares"] == 22665
    assert row["plays"] == 0
    assert row["duration"] == 213


def test_title_matches_keyword_lax_accepts_root_without_intent():
    """require_intent=False 时,标题含词根但无意图词也可通过。"""
    from src.services.commercial_search import title_matches_keyword

    title = "开一家餐饮店,新模式才是王道 #餐饮 #餐饮行业"
    assert title_matches_keyword(title=title, keyword="餐饮获客", require_intent=False)
    # 默认(严格)仍然要求意图词
    assert not title_matches_keyword(title=title, keyword="餐饮获客")
    # 含意图词的标题在两种模式下都通过
    intent_title = "餐饮店引流方法分享 #餐饮 #引流"
    assert title_matches_keyword(
        title=intent_title, keyword="餐饮获客", require_intent=False
    )
    assert title_matches_keyword(title=intent_title, keyword="餐饮获客")
