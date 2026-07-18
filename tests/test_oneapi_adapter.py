from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from src.adapters.licensed import LicensedProviderError, SandboxLicensedSearchProvider
from src.adapters.oneapi import OneApiLicensedSearchProvider
from src.config import build_licensed_search_provider
from src.models import Platform, ProviderErrorKind, ProviderMode


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


class FixedTransport:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any], dict[str, str], float]] = []

    def __call__(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, payload, headers, timeout))
        return 200, self.responses.pop(0)


def build_provider(transport: FixedTransport) -> OneApiLicensedSearchProvider:
    return OneApiLicensedSearchProvider(
        "trial-secret",
        transport=transport,
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("platform", "response", "endpoint", "expected_id", "expected_price"),
    [
        (
            Platform.DOUYIN,
            {
                "code": 200,
                "message": "success",
                "data": {
                    "aweme_list": [
                        {
                            "aweme_id": "dy-1",
                            "desc": "二手车避坑",
                            "author": {"sec_uid": "dy-u1", "nickname": "抖音作者"},
                            "create_time": int(NOW.timestamp()) - 3600,
                            "share_url": "https://www.douyin.com/video/dy-1",
                            "statistics": {
                                "play_count": 20000,
                                "digg_count": 1500,
                                "comment_count": 80,
                                "share_count": 30,
                                "collect_count": 120,
                            },
                        }
                    ],
                    "has_more": True,
                },
            },
            "/api/douyin/search_video",
            "dy-1",
            0.03,
        ),
        (
            Platform.XIAOHONGSHU,
            {
                "code": 200,
                "data": {
                    "items": [
                        {
                            "note_card": {
                                "note_id": "xhs-1",
                                "display_title": "二手车怎么选",
                                "user": {
                                    "user_id": "xhs-u1",
                                    "nickname": "小红书作者",
                                },
                                "publish_time": int(NOW.timestamp()) - 7200,
                                "interact_info": {
                                    "liked_count": "1.2万",
                                    "comment_count": "120",
                                    "collected_count": "860",
                                },
                            }
                        }
                    ]
                },
            },
            "/api/xiaohongshu-v2/search_notes",
            "xhs-1",
            0.12,
        ),
        (
            Platform.WECHAT_CHANNELS,
            {
                "code": 200,
                "data": {
                    "object_list": [
                        {
                            "object_id": "wx-1",
                            "description": "二手车真实行情",
                            "finder_info": {
                                "finder_username": "wx-u1",
                                "nickname": "视频号作者",
                            },
                            "publish_time": int(NOW.timestamp()) - 1800,
                            "like_count": 700,
                        }
                    ]
                },
            },
            "/api/wechat-search/fetch_search_video",
            "wx-1",
            0.15,
        ),
    ],
)
def test_search_maps_three_platforms_without_pagination(
    platform: Platform,
    response: dict[str, Any],
    endpoint: str,
    expected_id: str,
    expected_price: float,
) -> None:
    transport = FixedTransport([response])
    provider = build_provider(transport)

    page = provider.search(
        platform,
        "二手车",
        NOW - timedelta(days=7),
        10,
        "idem-12345678",
    )

    assert len(transport.calls) == 1
    assert transport.calls[0][0].endswith(endpoint)
    assert transport.calls[0][2]["Authorization"] == "Bearer trial-secret"
    assert page.api_call_count == 1
    assert page.billable_units == expected_price
    assert page.has_more is (platform == Platform.DOUYIN)
    assert [item.platform_item_id for item in page.items] == [expected_id]
    assert page.items[0].platform == platform


def test_search_request_defaults_to_general_sort_and_seven_day_window() -> None:
    responses = [{"code": 200, "data": []} for _ in range(3)]
    transport = FixedTransport(responses)
    provider = build_provider(transport)

    for platform in (
        Platform.DOUYIN,
        Platform.XIAOHONGSHU,
        Platform.WECHAT_CHANNELS,
    ):
        provider.search(
            platform,
            "二手车",
            NOW - timedelta(days=7),
            10,
            f"idem-{platform.value}",
        )

    douyin = transport.calls[0][1]
    xhs = transport.calls[1][1]
    wechat = transport.calls[2][1]
    assert douyin == {
        "keyword": "二手车",
        "count": 10,
        "offset": "0",
        "publish_time": "7",
        "filter_duration": "",
        "sort_type": "0",
        "search_id": "",
    }
    assert xhs["page"] == 1
    assert xhs["sort_type"] == "general"
    assert xhs["time_filter"] == "一周内"
    assert wechat["offset"] == 0
    assert wechat["sort"] == 0
    assert wechat["publish_time"] == 0


def test_xhs_camel_case_nested_card_is_selected_over_filter_lists() -> None:
    transport = FixedTransport(
        [
            {
                "code": 200,
                "data": {
                    "filters": [{"name": f"筛选{i}"} for i in range(10)],
                    "feeds": [
                        {
                            "noteCard": {
                                "noteId": "xhs-camel-1",
                                "displayTitle": "二手车避坑清单",
                                "userInfo": {
                                    "userId": "xhs-user-1",
                                    "nickName": "小红书作者",
                                },
                                "publishTime": int(NOW.timestamp()) - 3600,
                                "interactInfo": {
                                    "likedCount": "3200",
                                    "commentCount": "88",
                                    "collectedCount": "510",
                                },
                            }
                        }
                    ],
                },
            }
        ]
    )

    page = build_provider(transport).search(
        Platform.XIAOHONGSHU,
        "二手车",
        NOW - timedelta(days=7),
        10,
        "idem-xhs-camel",
    )

    assert [item.platform_item_id for item in page.items] == ["xhs-camel-1"]
    assert page.items[0].metrics.likes == 3200
    assert page.items[0].metrics.favorites == 510
    assert not page.errors


def test_wechat_business_code_zero_uses_chinese_parameter_message() -> None:
    transport = FixedTransport([{"code": 0, "message": "Request failed, Please retry"}])

    with pytest.raises(LicensedProviderError) as caught:
        build_provider(transport).search(
            Platform.WECHAT_CHANNELS,
            "二手车",
            NOW - timedelta(days=7),
            10,
            "idem-wechat-invalid",
        )

    assert caught.value.kind == ProviderErrorKind.VALIDATION
    assert "视频号查询参数" in str(caught.value)
    assert "Request failed" not in str(caught.value)


def test_missing_metrics_remain_none_and_bad_items_are_reported() -> None:
    transport = FixedTransport(
        [
            {
                "code": 200,
                "data": {
                    "items": [
                        {
                            "note_card": {
                                "note_id": "xhs-null",
                                "display_title": "没有互动字段",
                                "user": {"user_id": "u1", "nickname": "作者"},
                                "publish_time": int(NOW.timestamp()),
                            }
                        },
                        {"note_card": {"display_title": "缺少ID"}},
                    ]
                },
            }
        ]
    )

    page = build_provider(transport).search(
        Platform.XIAOHONGSHU,
        "二手车",
        NOW - timedelta(days=1),
        10,
        "idem-null-fields",
    )

    assert len(page.items) == 1
    assert page.items[0].metrics.likes is None
    assert page.items[0].metrics.comments is None
    assert page.items[0].metrics.favorites is None
    assert len(page.errors) == 1
    assert page.errors[0].item_index == 1


def test_business_authorization_error_is_not_retryable_or_secret_bearing() -> None:
    transport = FixedTransport([{"code": 401, "message": "bad trial-secret"}])
    provider = build_provider(transport)

    with pytest.raises(LicensedProviderError) as caught:
        provider.search(
            Platform.DOUYIN,
            "二手车",
            NOW - timedelta(days=1),
            10,
            "idem-auth",
        )

    assert caught.value.kind == ProviderErrorKind.AUTHORIZATION
    assert caught.value.retryable is False
    assert "trial-secret" not in str(caught.value)
    assert len(transport.calls) == 1


def test_search_timeout_is_outcome_unknown_and_must_not_be_retried() -> None:
    calls = 0

    def timeout_transport(*args: Any) -> tuple[int, dict[str, Any]]:
        nonlocal calls
        calls += 1
        raise socket.timeout("slow")

    provider = OneApiLicensedSearchProvider(
        "trial-secret",
        transport=timeout_transport,
        clock=lambda: NOW,
    )

    with pytest.raises(LicensedProviderError) as caught:
        provider.search(
            Platform.WECHAT_CHANNELS,
            "二手车",
            NOW - timedelta(days=7),
            10,
            "idem-timeout",
        )

    assert caught.value.kind == ProviderErrorKind.OUTCOME_UNKNOWN
    assert caught.value.outcome_unknown is True
    assert caught.value.retryable is False
    assert calls == 1


def test_usage_and_balance_are_read_only_account_calls() -> None:
    transport = FixedTransport(
        [
            {
                "code": 200,
                "data": {
                    "records": [
                        {"cost": "0.15", "count": 2},
                        {"amount": 0.03, "times": 1},
                    ]
                },
            },
            {"code": 200, "data": {"balance": "12.34"}},
        ]
    )
    provider = build_provider(transport)

    usage = provider.usage()
    balance = provider.account_balance_cny()

    assert usage.platform_queries == 3
    assert usage.estimated_cost == pytest.approx(0.18)
    assert usage.currency == "CNY"
    assert balance == pytest.approx(12.34)
    assert transport.calls[0][0].endswith("/back/user/usage_record")
    assert transport.calls[1][0].endswith("/back/user/balance")


def test_capability_and_config_keep_network_disabled_until_key_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = OneApiLicensedSearchProvider().capabilities()
    assert missing.mode == ProviderMode.PRODUCTION
    assert missing.enabled is False
    assert missing.supported_platforms == []
    with pytest.raises(LicensedProviderError) as caught:
        OneApiLicensedSearchProvider().account_balance_cny()
    assert caught.value.code == "api_key_missing"

    monkeypatch.setenv("VIDEO_LICENSED_PROVIDER_MODE", "sandbox")
    assert isinstance(build_licensed_search_provider(), SandboxLicensedSearchProvider)

    monkeypatch.setenv("VIDEO_LICENSED_PROVIDER_MODE", "oneapi")
    monkeypatch.delenv("ONEAPI_API_KEY", raising=False)
    disabled = build_licensed_search_provider()
    assert isinstance(disabled, OneApiLicensedSearchProvider)
    assert disabled.capabilities().enabled is False

    monkeypatch.setenv("ONEAPI_API_KEY", "configured-later")
    enabled = build_licensed_search_provider()
    assert enabled.capabilities().enabled is True
    assert enabled.capabilities().credential_alias == "ONEAPI_API_KEY"


def test_oneapi_page_stays_disabled_until_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIDEO_LICENSED_PROVIDER_MODE", "oneapi")
    monkeypatch.delenv("ONEAPI_API_KEY", raising=False)

    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py"))
    app.secrets["VIDEO_LICENSED_PROVIDER_MODE"] = "oneapi"
    app.secrets["ONEAPI_API_KEY"] = ""
    app.run(timeout=15)

    assert not app.exception
    discover = next(
        button for button in app.button if button.label == "三平台一键查爆款"
    )
    assert discover.disabled is True
    assert any("不会发起平台请求" in item.value for item in app.error)
    assert any("OneAPI API Key" in item.value for item in app.caption)


def test_paid_search_form_disables_enter_to_submit() -> None:
    source = (ROOT / "app_pages" / "candidates.py").read_text(encoding="utf-8")

    assert 'st.form("three_platform_search", enter_to_submit=False)' in source
