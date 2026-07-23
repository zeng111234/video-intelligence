from __future__ import annotations

import json

import pytest

from src.adapters.official import (
    DouyinHotBillboardAdapter,
    DouyinHotWordsAdapter,
    HotWordEntry,
    OfficialAdapterDisabledError,
    OfficialApiError,
)
from src.models import DataSource, Platform, SourceRequest


def _token_payload() -> dict[str, object]:
    return {
        "data": {
            "access_token": "test-token",
            "expires_in": 7200,
            "error_code": 0,
        }
    }


def _billboard_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "rank": 1,
        "item_id": "7000000000000000001",
        "title": "热门视频标题",
        "nickname": "测试作者",
        "avatar": "https://p3.douyinpic.com/avatar.jpeg",
        "digg_count": 12345,
        "comment_count": 678,
        "play_count": 987654,
        "hot_words": ["AI 数字人", "企业服务"],
        "hot_value": 12345678,
        "share_url": "https://www.douyin.com/share/video/7000000000000000001",
    }
    entry.update(overrides)
    return entry


def _billboard_payload(entries: list[object]) -> dict[str, object]:
    return {
        "err_no": 0,
        "err_msg": "success",
        "data": {"error_code": 0, "description": "success", "list": entries},
    }


def _make_transport(payload: dict[str, object]) -> tuple[list[tuple], object]:
    calls: list[tuple] = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if method == "POST":
            return _token_payload()
        return payload

    return calls, transport


def test_billboard_success_parses_entries() -> None:
    calls, transport = _make_transport(_billboard_payload([_billboard_entry()]))
    adapter = DouyinHotBillboardAdapter("key", "secret", transport=transport)

    page = adapter.sync(SourceRequest(source=DataSource.OFFICIAL))

    assert calls[0][0] == "POST"
    method, url, headers, body = calls[1]
    assert method == "GET"
    assert url == "https://open.douyin.com/data/extern/billboard/hot_video/"
    assert headers["access-token"] == "test-token"
    assert body is None

    assert not page.errors
    assert page.has_more is False
    assert len(page.items) == 1
    item = page.items[0]
    assert item.platform_item_id == "7000000000000000001"
    assert item.title == "热门视频标题"
    assert item.author_name == "测试作者"
    assert item.platform == Platform.DOUYIN
    assert item.source_type == DataSource.OFFICIAL
    assert str(item.source_url) == "https://www.douyin.com/share/video/7000000000000000001"
    assert item.metrics.likes == 12345
    assert item.metrics.comments == 678
    assert item.metrics.plays == 987654
    assert item.metrics.shares is None
    assert item.metrics.favorites is None
    assert item.official_hot is True
    assert item.official_rank == 1
    assert item.official_hot_value == 12345678
    assert item.matched_by == ["AI 数字人", "企业服务"]
    assert item.evidence is not None
    assert item.evidence.startswith("official_billboard:")
    evidence = json.loads(item.evidence.removeprefix("official_billboard:"))
    assert evidence["rank"] == 1
    assert evidence["hot_words"] == ["AI 数字人", "企业服务"]
    assert evidence["hot_value"] == 12345678
    assert evidence["official_share_url"] == (
        "https://www.douyin.com/share/video/7000000000000000001"
    )
    assert evidence["play_count"] == 987654
    assert evidence["author_avatar"] == "https://p3.douyinpic.com/avatar.jpeg"


def test_billboard_share_url_is_saved_verbatim() -> None:
    """官方 share_url 原样进入 source_url/evidence，不伪装成 v.douyin.com 短链。"""
    share_url = "https://www.douyin.com/share/video/abc123xyz"
    _, transport = _make_transport(_billboard_payload([_billboard_entry(share_url=share_url)]))
    adapter = DouyinHotBillboardAdapter("key", "secret", transport=transport)

    item = adapter.sync(SourceRequest(source=DataSource.OFFICIAL)).items[0]

    assert str(item.source_url) == share_url
    evidence = json.loads(item.evidence.removeprefix("official_billboard:"))
    assert evidence["official_share_url"] == share_url


def test_billboard_empty_list_returns_empty_page() -> None:
    _, transport = _make_transport(_billboard_payload([]))
    adapter = DouyinHotBillboardAdapter("key", "secret", transport=transport)

    page = adapter.sync(SourceRequest(source=DataSource.OFFICIAL))

    assert page.items == []
    assert page.errors == []
    assert page.has_more is False


def test_billboard_skips_bad_entries_with_errors() -> None:
    entries = [
        {"rank": 1},  # 缺 title，跳过
        "not-a-dict",  # 非对象，跳过
        _billboard_entry(rank=3, title="有效条目"),
    ]
    _, transport = _make_transport(_billboard_payload(entries))
    adapter = DouyinHotBillboardAdapter("key", "secret", transport=transport)

    page = adapter.sync(SourceRequest(source=DataSource.OFFICIAL))

    assert len(page.items) == 1
    assert page.items[0].title == "有效条目"
    assert len(page.errors) == 2
    assert page.errors[0].row == 1
    assert page.errors[1].row == 2


def test_billboard_tolerates_missing_optional_fields() -> None:
    _, transport = _make_transport(_billboard_payload([{"title": "只有标题"}]))
    adapter = DouyinHotBillboardAdapter("key", "secret", transport=transport)

    page = adapter.sync(SourceRequest(source=DataSource.OFFICIAL))

    assert not page.errors
    item = page.items[0]
    assert item.platform_item_id.startswith("billboard-")
    assert item.source_url is None
    assert item.metrics.likes is None
    assert item.metrics.comments is None
    assert item.metrics.plays is None
    assert item.official_rank == 1
    assert item.official_hot_value is None
    assert item.matched_by == []


def test_billboard_err_no_raises_official_api_error() -> None:
    _, transport = _make_transport({"err_no": 28001001, "err_msg": "access_token 无效"})
    adapter = DouyinHotBillboardAdapter("key", "secret", transport=transport)

    with pytest.raises(OfficialApiError, match="28001001"):
        adapter.sync(SourceRequest(source=DataSource.OFFICIAL))


def test_billboard_inner_error_code_raises_official_api_error() -> None:
    _, transport = _make_transport(
        {"err_no": 0, "data": {"error_code": 4002001, "description": "权限不足"}}
    )
    adapter = DouyinHotBillboardAdapter("key", "secret", transport=transport)

    with pytest.raises(OfficialApiError, match="4002001"):
        adapter.sync(SourceRequest(source=DataSource.OFFICIAL))


def test_billboard_token_failure_raises_official_api_error() -> None:
    def transport(method, url, headers, body):
        return {
            "data": {
                "error_code": 28000001,
                "description": "client_secret 错误",
            }
        }

    adapter = DouyinHotBillboardAdapter("key", "bad-secret", transport=transport)

    with pytest.raises(OfficialApiError, match="client_token"):
        adapter.sync(SourceRequest(source=DataSource.OFFICIAL))


def test_billboard_disabled_without_credentials() -> None:
    adapter = DouyinHotBillboardAdapter()

    capability = adapter.capabilities()
    assert capability.enabled is False
    assert capability.permission_status == "credentials_missing"
    assert capability.missing_configuration == [
        "DOUYIN_CLIENT_KEY",
        "DOUYIN_CLIENT_SECRET",
    ]
    with pytest.raises(OfficialAdapterDisabledError, match="尚未配置"):
        adapter.sync(SourceRequest(source=DataSource.OFFICIAL))


def test_billboard_configured_permission_unverified() -> None:
    adapter = DouyinHotBillboardAdapter("key", "secret", transport=lambda *a: {})

    capability = adapter.capabilities()
    assert capability.enabled is True
    assert capability.permission_status == "configured_permission_unverified"
    assert capability.missing_configuration == []


def _hot_words_payload() -> dict[str, object]:
    return {
        "err_no": 0,
        "err_msg": "success",
        "data": {
            "error_code": 0,
            "description": "success",
            "sentence_list": [
                {"sentence": "AI 数字人口播", "hot_value": 987654},
                {"sentence": "企业服务获客", "hot_value": 123456},
                {"sentence": "无热度词条"},
            ],
        },
    }


def test_hot_words_success_returns_entries() -> None:
    calls, transport = _make_transport(_hot_words_payload())
    adapter = DouyinHotWordsAdapter("key", "secret", transport=transport)

    entries = adapter.fetch_hot_words()

    method, url, headers, body = calls[1]
    assert method == "GET"
    assert url == "https://open.douyin.com/hotsearch/sentences/"
    assert headers["access-token"] == "test-token"
    assert len(entries) == 3
    first = entries[0]
    assert isinstance(first, HotWordEntry)
    assert first.word == "AI 数字人口播"
    assert first.hot_value == 987654
    assert first.fetched_at.tzinfo is not None
    assert first.raw == {"sentence": "AI 数字人口播", "hot_value": 987654}
    assert entries[2].hot_value is None


def test_hot_words_err_no_raises_official_api_error() -> None:
    _, transport = _make_transport({"err_no": 28001001, "err_msg": "access_token 无效"})
    adapter = DouyinHotWordsAdapter("key", "secret", transport=transport)

    with pytest.raises(OfficialApiError, match="28001001"):
        adapter.fetch_hot_words()


def test_hot_words_token_failure_raises_official_api_error() -> None:
    def transport(method, url, headers, body):
        return {"data": {"error_code": 28000001, "description": "client_secret 错误"}}

    adapter = DouyinHotWordsAdapter("key", "bad-secret", transport=transport)

    with pytest.raises(OfficialApiError, match="client_token"):
        adapter.fetch_hot_words()


def test_hot_words_disabled_without_credentials() -> None:
    adapter = DouyinHotWordsAdapter(client_secret="only-secret")

    capability = adapter.capabilities()
    assert capability.enabled is False
    assert capability.missing_configuration == ["DOUYIN_CLIENT_KEY"]
    with pytest.raises(OfficialAdapterDisabledError, match="尚未配置"):
        adapter.fetch_hot_words()
