from datetime import datetime

from src.adapters.douyin_browser_search import LocalDouyinBrowserSearchProvider
from src.models import Platform, ProviderMode


def test_visible_video_rows_become_canonical_douyin_candidates(tmp_path):
    observed_at = datetime.fromisoformat("2026-07-23T12:00:00+08:00")
    items, errors, filtered = LocalDouyinBrowserSearchProvider._to_items(
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

    items, errors, filtered = LocalDouyinBrowserSearchProvider._to_items(rows, "租房", observed_at, 100)

    assert errors == []
    assert [item.platform_item_id for item in items] == ["7538955201693994298"]
    assert filtered == {"duration": 1, "incremental_plays": 1, "relevance": 1}
    assert "视频总榜|高点赞率" in items[0].evidence
