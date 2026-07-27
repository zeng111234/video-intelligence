from datetime import datetime

import pytest

from src.adapters.licensed import LicensedProviderError
from src.adapters.douyin_browser_search import LocalDouyinBrowserSearchProvider
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
                "likes": 88,
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
    assert filtered == {"duration": 0, "incremental_plays": 0, "relevance": 0}
    assert len(items) == 1
    assert items[0].platform == Platform.DOUYIN
    assert items[0].platform_item_id == "7538955201693994298"
    assert str(items[0].source_url) == "https://www.douyin.com/video/7538955201693994298"
    assert items[0].metrics.plays == 1201
    assert "新增播放量=1201" in items[0].evidence


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
    assert [item.platform_item_id for item in low_incremental_items] == ["7538955201693994300"]
    assert [item.platform_item_id for item in items] == ["7538955201693994298"]
    assert filtered == {"duration": 1, "incremental_plays": 1, "relevance": 1}
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
    assert filtered["incremental_plays"] == 2
    assert [item.metrics.plays for item in low_incremental_items] == [1000, 800]
    assert all(":1h:" in item.evidence for item in low_incremental_items)


def test_hotspot_uses_visible_publication_time_when_available():
    observed_at = datetime.fromisoformat("2026-07-24T12:00:00+08:00")
    items, _, _, _ = LocalDouyinBrowserSearchProvider._to_items(
        [{
            "item_id": "7538955201693994298",
            "href": "https://www.douyin.com/video/7538955201693994298",
            "title": "租房避坑：签合同前先看这三点",
            "duration": 33,
            "plays": 5000,
            "published_text": "2026-07-23T09:30:00+08:00",
        }],
        "租房",
        observed_at,
        10,
    )

    assert items[0].published_at == datetime.fromisoformat("2026-07-23T09:30:00+08:00")
    assert items[0].data_quality_warnings == []


def test_hotspot_marks_sample_time_when_publication_time_is_missing():
    observed_at = datetime.fromisoformat("2026-07-24T12:00:00+08:00")
    items, _, _, _ = LocalDouyinBrowserSearchProvider._to_items(
        [{
            "item_id": "7538955201693994298",
            "href": "https://www.douyin.com/video/7538955201693994298",
            "title": "租房避坑：签合同前先看这三点",
            "duration": 33,
            "plays": 5000,
        }],
        "租房",
        observed_at,
        10,
    )

    assert items[0].published_at == observed_at
    assert items[0].data_quality_warnings == ["未取得有效发布时间，页面展示为采样时间。"]


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

    def collect(keyword: str, *, window_hours: int):
        captured["window_hours"] = window_hours
        return [
            {
                "item_id": "7538955201693994298",
                "href": "https://www.douyin.com/video/7538955201693994298",
                "title": "数字人近况",
                "duration": 15,
                "plays": 1201,
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
        lambda keyword, *, window_hours: ([
            {
                "item_id": "7538955201693994300",
                "href": "https://www.douyin.com/video/7538955201693994300",
                "title": "租房预算怎么做",
                "duration": 20,
                "plays": 800,
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
