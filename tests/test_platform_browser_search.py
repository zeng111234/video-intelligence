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


def test_optional_login_prompt_does_not_block_public_search_attempt():
    provider = _provider(Platform.BILIBILI)
    page = _TextPage("登录后可查看更多推荐内容 扫码登录")

    provider._raise_for_visible_block(page)

    with pytest.raises(LicensedProviderError, match="要求登录或人工验证"):
        provider._raise_for_login_gate(page)


def test_login_button_opens_visible_platform_window_even_when_session_is_running(
    tmp_path, monkeypatch
):
    provider = LocalPlatformBrowserSearchProvider(
        platform=Platform.XIAOHONGSHU,
        enabled=True,
        profile_dir=tmp_path / "profile",
        debug_port=29992,
    )
    launched: list[list[str]] = []
    ready = BrowserSessionStatus(True, True, False, True, "ready", "已连接")
    monkeypatch.setattr(provider, "session_status", lambda: ready)
    monkeypatch.setattr(provider, "_missing_prerequisites", lambda: [])
    monkeypatch.setattr(provider, "_browser_executable", lambda: tmp_path / "chrome.exe")
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.subprocess.Popen",
        lambda args, **kwargs: launched.append(args),
    )
    monkeypatch.setattr("src.adapters.platform_browser_search.time.sleep", lambda _seconds: None)

    status = provider.open_login_browser()

    assert status.ready_to_crawl is True
    assert len(launched) == 1
    assert "--new-window" in launched[0]
    assert "--window-position=80,80" in launched[0]
    assert "--start-minimized" not in launched[0]


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
    monkeypatch.setattr(
        "src.adapters.platform_browser_search.subprocess.Popen",
        lambda args, **kwargs: launched.append(args),
    )
    monkeypatch.setattr("src.adapters.platform_browser_search.time.sleep", lambda _seconds: None)

    provider.start_login_browser()

    assert "--start-minimized" in launched[0]
    assert "--window-position=-32000,-32000" in launched[0]
    assert "--new-window" not in launched[0]


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
    assert item.metrics.favorites == 320
    assert str(item.source_url) == "https://www.xiaohongshu.com/explore/xhs-note-1"
    assert "time=platform" in (item.evidence or "")


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
    assert item.metrics.comments == 456
    assert item.metrics.favorites == 789
    assert str(item.source_url) == "https://www.bilibili.com/video/BV1TEST2026"


def test_xiaohongshu_missing_publish_time_trusts_selected_week_filter():
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
    assert "time=platform_filter" in (items[0].evidence or "")
    assert any("已选择“一周内”" in warning for warning in items[0].data_quality_warnings)


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
