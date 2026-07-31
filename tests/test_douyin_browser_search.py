from datetime import datetime

import pytest

from src.adapters.licensed import LicensedProviderError
from src.adapters.douyin_browser_search import (
    BrowserSessionStatus,
    LocalDouyinBrowserSearchProvider,
)
from src.models import Platform, ProviderMode


def test_visible_video_rows_become_canonical_douyin_candidates(tmp_path):
    observed_at = datetime.fromisoformat("2026-07-23T12:00:00+08:00")
    items, low_incremental_items, errors, filtered = LocalDouyinBrowserSearchProvider._to_items(
        [
            {
                "href": "https://www.douyin.com/video/7538955201693994298?foo=1",
                "text": "数字人口播实测：开头两秒怎么留人",
                "aria": "",
                "duration": 15,
                "plays": 1201,
                "likes": 120,
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

    assert errors == []
    assert low_incremental_items == []
    assert filtered == {"duration": 0, "incremental_plays": 0, "relevance": 0, "quality": 0}
    assert len(items) == 1
    assert items[0].platform == Platform.DOUYIN
    assert items[0].platform_item_id == "7538955201693994298"
    assert str(items[0].source_url) == "https://www.douyin.com/video/7538955201693994298"
    assert items[0].metrics.plays == 1201
    assert "新增播放量=1201" in items[0].evidence
    assert "日均点赞=" in items[0].evidence


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


def test_browser_provider_capabilities_do_not_probe_the_local_debug_port(tmp_path, monkeypatch):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29995,
    )
    monkeypatch.setattr(provider, "session_status", lambda: pytest.fail("capabilities must stay static"))

    capability = provider.capabilities()

    assert capability.provider_name == "douyin_local_browser"
    assert capability.permission_status == "local_browser_login_required"


def test_browser_provider_reports_missing_playwright_dependency(tmp_path, monkeypatch):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29997,
    )
    monkeypatch.setattr(provider, "_playwright_available", lambda: False)
    monkeypatch.setattr(provider, "_browser_executable", lambda: tmp_path / "chrome.exe")

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
    monkeypatch.setattr(provider, "_playwright_available", lambda: True)
    monkeypatch.setattr(provider, "_browser_executable", lambda: None)

    capability = provider.capabilities()

    assert capability.enabled is False
    assert capability.missing_configuration == ["Microsoft Edge"]


def test_login_button_opens_visible_hotspot_window_even_when_session_is_running(
    tmp_path, monkeypatch
):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29994,
    )
    launched: list[list[str]] = []
    ready = BrowserSessionStatus(True, True, False, True, "ready", "已连接")
    monkeypatch.setattr(provider, "session_status", lambda: ready)
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(provider, "_browser_executable", lambda: tmp_path / "chrome.exe")
    monkeypatch.setattr(
        "src.adapters.douyin_browser_search.subprocess.Popen",
        lambda args, **kwargs: launched.append(args),
    )
    monkeypatch.setattr("src.adapters.douyin_browser_search.time.sleep", lambda _seconds: None)

    status = provider.open_login_browser()

    assert status.ready_to_crawl is True
    assert len(launched) == 1
    assert "--new-window" in launched[0]
    assert "--window-position=80,80" in launched[0]
    assert "--start-minimized" not in launched[0]


def test_automatic_hotspot_start_stays_minimized(tmp_path, monkeypatch):
    provider = LocalDouyinBrowserSearchProvider(
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29993,
    )
    launched: list[list[str]] = []
    closed = BrowserSessionStatus(
        True, False, True, False, "browser_closed", "未打开"
    )
    monkeypatch.setattr(provider, "session_status", lambda: closed)
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(provider, "_browser_executable", lambda: tmp_path / "chrome.exe")
    monkeypatch.setattr(
        "src.adapters.douyin_browser_search.subprocess.Popen",
        lambda args, **kwargs: launched.append(args),
    )
    monkeypatch.setattr("src.adapters.douyin_browser_search.time.sleep", lambda _seconds: None)

    provider.start_login_browser()

    assert "--start-minimized" in launched[0]
    assert "--window-position=-32000,-32000" in launched[0]
    assert "--new-window" not in launched[0]


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


def test_hotspot_filters_image_posts_low_incremental_plays_and_irrelevant_rows():
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

    items, low_incremental_items, errors, filtered = LocalDouyinBrowserSearchProvider._to_items(rows, "租房", observed_at, 100)

    assert errors == []
    assert low_incremental_items == []
    assert [item.platform_item_id for item in items] == ["7538955201693994298"]
    assert filtered == {"duration": 1, "incremental_plays": 0, "relevance": 1, "quality": 1}
    assert "视频总榜|高点赞率" in items[0].evidence


def test_hotspot_keeps_related_low_incremental_rows_when_main_list_is_empty():
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

    items, low_incremental_items, errors, filtered = LocalDouyinBrowserSearchProvider._to_items(
        rows, "租房", observed_at, 100
    )

    assert items == []
    assert errors == []
    assert filtered["incremental_plays"] == 0
    assert filtered["quality"] == 2
    assert low_incremental_items == []


def test_exact_topic_keeps_videos_even_when_the_title_omits_the_keyword():
    observed_at = datetime.fromisoformat("2026-07-28T12:00:00+08:00")

    items, low_incremental_items, errors, filtered = LocalDouyinBrowserSearchProvider._to_items(
        [{
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
        }],
        "餐饮获客",
        observed_at,
        3,
    )

    assert errors == []
    assert low_incremental_items == []
    assert filtered == {"duration": 0, "incremental_plays": 0, "relevance": 0, "quality": 0}
    assert [item.platform_item_id for item in items] == ["7538955201693994310"]
    assert "严格话题=1" in items[0].evidence
    assert "来源=topic_board" in items[0].evidence


def test_douyin_search_keeps_exact_video_without_hotspot_incremental_plays():
    observed_at = datetime.fromisoformat("2026-07-28T12:00:00+08:00")

    items, low_incremental_items, errors, filtered = LocalDouyinBrowserSearchProvider._to_items(
        [{
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
        }],
        "餐饮获客",
        observed_at,
        3,
    )

    assert errors == []
    assert low_incremental_items == []
    assert filtered == {"duration": 0, "incremental_plays": 0, "relevance": 0, "quality": 0}
    assert items[0].metrics.plays is None
    assert "抖音搜索" in items[0].evidence


def test_douyin_search_collects_multiple_rendered_viewports(tmp_path, monkeypatch):
    provider = LocalDouyinBrowserSearchProvider(enabled=True, profile_dir=tmp_path / "profile")
    rounds = [
        [{"item_id": "one", "title": "第一条"}],
        [{"item_id": "one", "title": "第一条"}, {"item_id": "two", "title": "第二条"}],
        [{"item_id": "two", "title": "第二条"}, {"item_id": "three", "title": "第三条"}],
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


def test_quality_gate_requires_both_total_likes_and_daily_velocity():
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

    assert [item.platform_item_id for item in items] == ["7538955201693994314"]
    assert filtered["quality"] == 2
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

    items, _, _, _ = LocalDouyinBrowserSearchProvider._to_items(rows, "餐饮获客", observed_at, 3)

    assert [item.platform_item_id for item in items] == [
        "7538955201693994315",
        "7538955201693994316",
        "7538955201693994317",
    ]


def test_hotspot_uses_visible_publication_time_when_available():
    observed_at = datetime.fromisoformat("2026-07-24T12:00:00+08:00")
    items, _, _, _ = LocalDouyinBrowserSearchProvider._to_items(
        [{
            "item_id": "7538955201693994298",
            "href": "https://www.douyin.com/video/7538955201693994298",
            "title": "租房避坑：签合同前先看这三点",
            "duration": 33,
            "plays": 5000,
            "likes": 500,
            "published_text": "2026-07-23T09:30:00+08:00",
        }],
        "租房",
        observed_at,
        10,
    )

    assert items[0].published_at == datetime.fromisoformat("2026-07-23T09:30:00+08:00")
    assert items[0].data_quality_warnings == []


def test_search_candidate_without_publication_time_is_rejected_from_customer_results():
    observed_at = datetime.fromisoformat("2026-07-24T12:00:00+08:00")
    items, _, _, _ = LocalDouyinBrowserSearchProvider._to_items(
        [{
            "item_id": "7538955201693994298",
            "href": "https://www.douyin.com/video/7538955201693994298",
            "title": "租房避坑：签合同前先看这三点",
            "duration": 33,
                "likes": 500,
        }],
        "租房",
        observed_at,
        10,
    )

    assert items == []


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


def test_hotspot_search_exposes_low_incremental_items_only_when_main_list_is_empty(
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
        lambda keyword, *, window_hours, observed_at, target_limit: ([
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
        ], []),
    )

    page = provider.search(
        Platform.DOUYIN,
        "租房",
        published_after=None,
        limit=10,
        idempotency_key="low-incremental-test",
        hotspot_window_hours=1,
    )

    assert page.items == []
    assert [item.metrics.plays for item in page.low_incremental_items] == [800]
