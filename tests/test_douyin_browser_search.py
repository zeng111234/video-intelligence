from datetime import datetime

from src.adapters.douyin_browser_search import LocalDouyinBrowserSearchProvider
from src.models import Platform, ProviderMode


def test_visible_video_rows_become_canonical_douyin_candidates(tmp_path):
    observed_at = datetime.fromisoformat("2026-07-23T12:00:00+08:00")
    items, errors = LocalDouyinBrowserSearchProvider._to_items(
        [
            {
                "href": "https://www.douyin.com/video/7538955201693994298?foo=1",
                "text": "数字人口播实测：开头两秒怎么留人",
                "aria": "",
            },
            {
                "href": "https://www.douyin.com/video/7538955201693994298",
                "text": "重复卡片",
                "aria": "",
            },
        ],
        "数字人",
        observed_at,
        10,
    )

    assert errors == []
    assert len(items) == 1
    assert items[0].platform == Platform.DOUYIN
    assert items[0].platform_item_id == "7538955201693994298"
    assert str(items[0].source_url) == "https://www.douyin.com/video/7538955201693994298"
    assert items[0].metrics.confidence == 0.4


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
