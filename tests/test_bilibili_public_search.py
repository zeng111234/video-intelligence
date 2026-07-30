"""B站公开搜索适配器的免费、时效与字段降级测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from src.adapters.bilibili_public_search import BilibiliPublicSearchProvider
from src.models import Platform


def _response(payload: dict) -> httpx.Response:
    request = httpx.Request("GET", "https://api.bilibili.com/x/web-interface/search/type")
    return httpx.Response(200, json=payload, request=request)


def test_search_keeps_recent_items_and_normalizes_public_metrics(monkeypatch):
    now = datetime.now(timezone.utc)
    payload = {
        "code": 0,
        "data": {
            "result": [
                {
                    "bvid": "BV1recent",
                    "title": "<em class=\"keyword\">餐饮获客</em>的新方法",
                    "author": "测试作者",
                    "pubdate": int(now.timestamp()),
                    "play": "1.2万",
                    "favorites": 23,
                    "video_review": 8,
                },
                {
                    "bvid": "BV1old",
                    "title": "餐饮获客旧内容",
                    "author": "旧作者",
                    "pubdate": int((now - timedelta(days=5)).timestamp()),
                    "play": 9999,
                },
            ]
        },
    }
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _response(payload))

    result = BilibiliPublicSearchProvider().search(
        platform=Platform.BILIBILI,
        keyword="餐饮获客",
        published_after=now - timedelta(days=3),
        limit=10,
        idempotency_key="test-recent-items",
    )

    assert result.api_call_count == 1
    assert result.billable_units == 0
    assert len(result.items) == 1
    item = result.items[0]
    assert str(item.source_url) == "https://www.bilibili.com/video/BV1recent"
    assert item.title == "餐饮获客的新方法"
    assert item.metrics.plays == 12000
    assert item.metrics.favorites == 23
    assert item.metrics.comments == 8
    assert any("点赞" in warning for warning in item.data_quality_warnings)


def test_search_retries_one_time_after_connection_failure(monkeypatch):
    calls = 0

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary connection failure")
        return _response({"code": 0, "data": {"result": []}})

    monkeypatch.setattr(httpx, "get", fake_get)
    result = BilibiliPublicSearchProvider().search(
        platform=Platform.BILIBILI,
        keyword="测试",
        published_after=None,
        limit=1,
        idempotency_key="test-retry",
    )

    assert calls == 2
    assert result.items == []
