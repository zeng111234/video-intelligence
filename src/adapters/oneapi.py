from __future__ import annotations

import hashlib
import json
import socket

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import HttpUrl, ValidationError

from src.adapters.licensed import LicensedProviderError
from src.models import (
    Platform,
    ProviderCapability,
    ProviderErrorKind,
    ProviderMode,
    ProviderMediaResult,
    ProviderSearchError,
    ProviderSearchItem,
    ProviderSearchPage,
    ProviderUsage,
    VideoMetricSnapshot,
)
from utils.common.errors import (
    analyze_404_error,
    create_error_context,
    log_http_request_response,
)

OneApiTransport = Callable[
    [str, dict[str, Any], dict[str, str], float],
    tuple[int, Mapping[str, Any]],
]


class OneApiLicensedSearchProvider:
    """OneAPI trial adapter for authorized three-platform metadata searches.

    It intentionally does not download media or use platform login state. OneAPI
    reports business success in the JSON ``code`` field even when HTTP is 200.
    """

    provider_name = "oneapi"
    base_url = "https://api.getoneapi.com"
    endpoint_prices_cny = {
        Platform.DOUYIN: 0.03,
        Platform.XIAOHONGSHU: 0.12,
        Platform.WECHAT_CHANNELS: 0.15,
    }
    media_endpoint_prices_cny = {
        # OneAPI's current public list prices this exact detail endpoint at ¥0.04.
        Platform.DOUYIN: 0.04,
        Platform.XIAOHONGSHU: 0.12,
        Platform.WECHAT_CHANNELS: 0.15,
    }

    def __init__(
        self,
        api_key: str = "",
        *,
        timeout_seconds: float = 65.0,
        transport: OneApiTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._timeout_seconds = max(10.0, float(timeout_seconds))
        self._transport = transport or self._post_json
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._base_url = (base_url or self.base_url).rstrip("/")

    def capabilities(self) -> ProviderCapability:
        enabled = bool(self._api_key)
        return ProviderCapability(
            provider_name=self.provider_name,
            display_name="OneAPI 三平台试点接口",
            mode=ProviderMode.PRODUCTION,
            enabled=enabled,
            supported_platforms=(
                [
                    Platform.DOUYIN,
                    Platform.XIAOHONGSHU,
                    Platform.WECHAT_CHANNELS,
                ]
                if enabled
                else []
            ),
            max_page_size=10,
            supports_published_after=True,
            supports_metric_refresh=False,
            supports_usage=True,
            permission_status=(
                "trial_unverified_commercial_rights" if enabled else "api_key_missing"
            ),
            credential_alias="ONEAPI_API_KEY" if enabled else None,
            missing_configuration=(["OneAPI API Key"] if not enabled else []),
        )

    def search(
        self,
        platform: Platform,
        keyword: str,
        published_after: datetime,
        limit: int,
        idempotency_key: str,
    ) -> ProviderSearchPage:
        self._validate_search(platform, keyword, limit)
        observed_at = self._clock()
        endpoint, payload = self._search_request(
            platform,
            keyword.strip(),
            published_after,
            observed_at,
            limit,
        )
        body = self._request(endpoint, payload, timeout_unknown=True)
        data = body.get("data")
        raw_items = self._extract_candidate_list(data)
        request_id = self._request_id(body, idempotency_key)
        items: list[ProviderSearchItem] = []
        errors: list[ProviderSearchError] = []
        for index, raw_item in enumerate(raw_items[:limit]):
            try:
                normalized = self._normalize_item(
                    platform,
                    raw_item,
                    rank=index + 1,
                    observed_at=observed_at,
                    request_id=request_id,
                )
                # Keep all successfully parsed items. The commercial-search
                # service applies the requested window once and records why an
                # item was filtered, instead of turning a non-empty provider
                # response into an unexplained zero-result batch here.
                items.append(normalized)
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                errors.append(
                    ProviderSearchError(
                        kind=ProviderErrorKind.VALIDATION,
                        message=f"第 {index + 1} 条供应商数据字段不完整：{exc}",
                        item_index=index,
                    )
                )

        return ProviderSearchPage(
            platform=platform,
            provider=self.provider_name,
            items=items,
            observed_at=observed_at,
            request_id=request_id,
            api_call_count=1,
            billable_units=self.endpoint_prices_cny[platform],
            has_more=self._as_bool(self._first(data, "has_more", "hasMore", "more")),
            raw_item_count=len(raw_items),
            parsed_item_count=len(items),
            payload_diagnostic=(
                "供应商成功响应但未发现可识别的视频列表。"
                if not raw_items and isinstance(data, Mapping) and data
                else None
            ),
            errors=errors,
        )

    def refresh_metrics(
        self,
        platform: Platform,
        platform_item_ids: list[str],
        idempotency_key: str,
    ) -> ProviderSearchPage:
        raise LicensedProviderError(
            "OneAPI 试点暂不自动逐条补查指标，避免产生未确认的额外费用。",
            kind=ProviderErrorKind.VALIDATION,
            code="metric_refresh_disabled",
        )

    def usage(self) -> ProviderUsage:
        if not self._api_key:
            raise LicensedProviderError(
                "尚未配置 OneAPI API Key，无法查询账户用量。",
                kind=ProviderErrorKind.AUTHORIZATION,
                code="api_key_missing",
            )
        now = self._clock()
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_end = (period_start + timedelta(days=32)).replace(day=1)

        usage_body = self._request(
            "/back/user/usage_record",
            {
                "startDate": period_start.strftime("%Y-%m-%d"),
                "endDate": now.strftime("%Y-%m-%d"),
            },
            timeout_unknown=False,
        )
        records = self._extract_usage_records(usage_body.get("data"))
        estimated_cost = sum(self._record_cost(item) for item in records)
        platform_queries = sum(
            max(
                0,
                self._to_int(
                    self._first(
                        item,
                        "successCount",
                        "success_count",
                        "success",
                        "count",
                        "request_count",
                        "times",
                    )
                )
                or 0,
            )
            for item in records
        )
        return ProviderUsage(
            provider=self.provider_name,
            period_started_at=period_start,
            period_ends_at=period_end,
            platform_queries=platform_queries,
            billable_units=estimated_cost,
            estimated_cost=estimated_cost,
            currency="CNY",
        )

    def media_resolution_price(self, platform: Platform) -> float | None:
        return self.media_endpoint_prices_cny.get(platform)

    def resolve_media_url(
        self,
        platform: Platform,
        platform_item_id: str,
        idempotency_key: str,
    ) -> ProviderMediaResult:
        self._validate_media_resolution(platform, platform_item_id)
        observed_at = self._clock()
        endpoint, payload = self._media_request(platform, platform_item_id)
        body = self._request(endpoint, payload, timeout_unknown=True)
        data = body.get("data")
        media_url = self._extract_media_url(
            data,
            prefer_low_quality=platform == Platform.DOUYIN,
        )
        if media_url is None:
            raise LicensedProviderError(
                "供应商详情接口未返回可用于转写的视频文件直链。",
                kind=ProviderErrorKind.VALIDATION,
                code="media_url_missing",
            )
        try:
            parsed_url = HttpUrl(media_url)
        except ValueError as exc:
            raise LicensedProviderError(
                "供应商返回的视频文件直链格式无效。",
                kind=ProviderErrorKind.VALIDATION,
                code="media_url_invalid",
            ) from exc
        return ProviderMediaResult(
            platform=platform,
            provider=self.provider_name,
            platform_item_id=platform_item_id,
            media_url=parsed_url,
            observed_at=observed_at,
            request_id=self._request_id(body, idempotency_key),
            api_call_count=1,
            billable_units=self.media_endpoint_prices_cny[platform],
            warnings=(
                ["douyin_detail_low_bitrate_media"]
                if platform == Platform.DOUYIN
                else []
            ),
        )

    def account_balance_cny(self) -> float | None:
        if not self._api_key:
            raise LicensedProviderError(
                "尚未配置 OneAPI API Key，无法查询账户余额。",
                kind=ProviderErrorKind.AUTHORIZATION,
                code="api_key_missing",
            )
        body = self._request("/back/user/balance", {}, timeout_unknown=False)
        return self._to_float(
            self._first(body.get("data"), "balance", "amount", "money", "value")
        )

    def _validate_search(self, platform: Platform, keyword: str, limit: int) -> None:
        capability = self.capabilities()
        if not capability.enabled:
            raise LicensedProviderError(
                "尚未配置 OneAPI API Key，未发起真实请求。",
                kind=ProviderErrorKind.AUTHORIZATION,
                code="api_key_missing",
            )
        if platform not in self.endpoint_prices_cny:
            raise LicensedProviderError(
                "OneAPI 试点只支持抖音、小红书和微信视频号。",
                kind=ProviderErrorKind.VALIDATION,
            )
        if not keyword.strip():
            raise LicensedProviderError(
                "关键词不能为空。",
                kind=ProviderErrorKind.VALIDATION,
            )
        if not 1 <= limit <= 10:
            raise LicensedProviderError(
                "每个平台每次只能获取 1 到 10 条。",
                kind=ProviderErrorKind.VALIDATION,
            )

    def _validate_media_resolution(
        self, platform: Platform, platform_item_id: str
    ) -> None:
        capability = self.capabilities()
        if not capability.enabled:
            raise LicensedProviderError(
                "尚未配置 OneAPI API Key，未发起真实请求。",
                kind=ProviderErrorKind.AUTHORIZATION,
                code="api_key_missing",
            )
        if platform not in self.media_endpoint_prices_cny:
            raise LicensedProviderError(
                "OneAPI 媒体解析暂只支持抖音、小红书和微信视频号。",
                kind=ProviderErrorKind.VALIDATION,
            )
        if not platform_item_id.strip():
            raise LicensedProviderError(
                "作品 ID 为空，不能补媒体直链。",
                kind=ProviderErrorKind.VALIDATION,
            )
        if platform_item_id.startswith("proxy-"):
            raise LicensedProviderError(
                "该候选只有本地代理 ID，供应商无法按真实作品 ID 补媒体直链。",
                kind=ProviderErrorKind.VALIDATION,
                code="proxy_id_not_resolvable",
            )

    @staticmethod
    def _window_days(published_after: datetime, observed_at: datetime) -> int:
        return 1 if observed_at - published_after <= timedelta(hours=25) else 7

    def _search_request(
        self,
        platform: Platform,
        keyword: str,
        published_after: datetime,
        observed_at: datetime,
        limit: int,
    ) -> tuple[str, dict[str, Any]]:
        window_days = self._window_days(published_after, observed_at)
        if platform == Platform.DOUYIN:
            return "/api/douyin/search_video", {
                "keyword": keyword,
                "count": limit,
                "offset": "0",
                "publish_time": str(1 if window_days == 1 else 7),
                "filter_duration": "",
                "sort_type": "1",
                "search_id": "",
            }
        if platform == Platform.XIAOHONGSHU:
            return "/api/xiaohongshu-v2/search_notes", {
                "keyword": keyword,
                "page": 1,
                "sort_type": "popularity_descending",
                "note_type": "不限",
                "time_filter": "一天内" if window_days == 1 else "一周内",
                "search_id": "",
                "search_session_id": "",
                "source": "explore_feed",
                "ai_mode": 0,
            }
        return "/api/wechat-search/fetch_search_video", {
            "keyword": keyword,
            "duration": 0,
            "sort": 2,
            "publish_time": 1 if window_days == 1 else 2,
            "offset": 0,
            "raw": False,
        }

    @staticmethod
    def _media_request(
        platform: Platform,
        platform_item_id: str,
    ) -> tuple[str, dict[str, Any]]:
        if platform == Platform.DOUYIN:
            return "/api/douyin-app/fetch_video_detail", {
                "aweme_id": platform_item_id,
                "share_text": "",
            }
        if platform == Platform.XIAOHONGSHU:
            return "/api/xiaohongshu-v2/fetch_video_note_detail", {
                "note_id": platform_item_id,
                "share_text": "",
            }
        return "/api/wechat-channels-v2/fetch_video_detail", {
            "object_id": platform_item_id,
            "raw": False,
        }

    def _request(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        timeout_unknown: bool,
    ) -> Mapping[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "video-intelligence-oneapi/1.0",
        }
        url = f"{self._base_url}{endpoint}"

        log_http_request_response(
            url=url,
            method="POST",
            request_headers=headers,
            request_body=payload,
            additional_info={"endpoint": endpoint, "timeout_unknown": timeout_unknown},
        )

        status: int = 0
        body: Mapping[str, Any] = {}
        try:
            status, body = self._transport(
                url,
                payload,
                headers,
                self._timeout_seconds,
            )

            # 记录响应详情
            log_http_request_response(
                url=url,
                method="POST",
                request_headers=headers,
                request_body=payload,
                response_status=status,
                response_body=body,
                additional_info={"endpoint": endpoint},
            )

        except HTTPError as exc:
            # 记录HTTP错误详情
            error_context = create_error_context(
                error=exc,
                request_url=url,
                request_method="POST",
                request_headers=headers,
                request_body=payload,
                response_status=exc.code,
                additional_info={
                    "endpoint": endpoint,
                    "timeout_unknown": timeout_unknown,
                },
            )
            error_context.log_error()

            self._raise_http_error(exc.code)
        except (socket.timeout, TimeoutError) as exc:
            # 记录超时错误
            error_context = create_error_context(
                error=exc,
                request_url=url,
                request_method="POST",
                request_headers=headers,
                request_body=payload,
                additional_info={
                    "endpoint": endpoint,
                    "timeout_unknown": timeout_unknown,
                },
            )
            error_context.log_error()

            kind = (
                ProviderErrorKind.OUTCOME_UNKNOWN
                if timeout_unknown
                else ProviderErrorKind.CONNECTION
            )
            raise LicensedProviderError(
                (
                    "OneAPI 请求超时，费用状态待核对；系统不会立即重发。"
                    if timeout_unknown
                    else "查询 OneAPI 账户信息超时。"
                ),
                kind=kind,
                code="timeout",
                outcome_unknown=timeout_unknown,
                retryable=not timeout_unknown,
            ) from exc
        except URLError as exc:
            # 记录URL错误
            error_context = create_error_context(
                error=exc,
                request_url=url,
                request_method="POST",
                request_headers=headers,
                request_body=payload,
                additional_info={
                    "endpoint": endpoint,
                    "timeout_unknown": timeout_unknown,
                },
            )
            error_context.log_error()

            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                kind = (
                    ProviderErrorKind.OUTCOME_UNKNOWN
                    if timeout_unknown
                    else ProviderErrorKind.CONNECTION
                )
                raise LicensedProviderError(
                    (
                        "OneAPI 请求超时，费用状态待核对；系统不会立即重发。"
                        if timeout_unknown
                        else "查询 OneAPI 账户信息超时。"
                    ),
                    kind=kind,
                    code="timeout",
                    outcome_unknown=timeout_unknown,
                    retryable=not timeout_unknown,
                ) from exc
            raise LicensedProviderError(
                "无法连接 OneAPI，最多只会自动重试一次。",
                kind=ProviderErrorKind.CONNECTION,
                code="connection_error",
                retryable=True,
            ) from exc
        except OSError as exc:
            # 记录操作系统错误
            error_context = create_error_context(
                error=exc,
                request_url=url,
                request_method="POST",
                request_headers=headers,
                request_body=payload,
                additional_info={"endpoint": endpoint},
            )
            error_context.log_error()

            raise LicensedProviderError(
                "无法连接 OneAPI，最多只会自动重试一次。",
                kind=ProviderErrorKind.CONNECTION,
                code="connection_error",
                retryable=True,
            ) from exc

        if not 200 <= status < 300:
            # 记录HTTP状态码错误
            error_context = create_error_context(
                error=Exception(f"HTTP 状态码错误: {status}"),
                request_url=url,
                request_method="POST",
                request_headers=headers,
                request_body=payload,
                response_status=status,
                response_body=body,
                additional_info={"endpoint": endpoint},
            )
            error_context.log_error()

            self._raise_http_error(status)
        code = str(body.get("code", "")).strip()
        if code != "200":
            # 记录业务逻辑错误
            error_context = create_error_context(
                error=Exception(f"业务逻辑错误: {code}"),
                request_url=url,
                request_method="POST",
                request_headers=headers,
                request_body=payload,
                response_status=status,
                response_body=body,
                additional_info={"endpoint": endpoint, "business_code": code},
            )
            error_context.log_error()

            self._raise_business_error(code, body, endpoint)
        return body

    @staticmethod
    def _post_json(
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, Mapping[str, Any]]:
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LicensedProviderError(
                    "OneAPI 返回内容无法解析，费用状态待核对。",
                    kind=ProviderErrorKind.OUTCOME_UNKNOWN,
                    code="invalid_json",
                    outcome_unknown=True,
                ) from exc
            if not isinstance(body, Mapping):
                raise LicensedProviderError(
                    "OneAPI 返回格式异常，费用状态待核对。",
                    kind=ProviderErrorKind.OUTCOME_UNKNOWN,
                    code="invalid_response",
                    outcome_unknown=True,
                )
            return response.status, body

    @staticmethod
    def _raise_http_error(status: int) -> None:
        if status in {401, 403}:
            kind = ProviderErrorKind.AUTHORIZATION
            message = "OneAPI 授权失败，请检查 API Key 或账户状态。"
        elif status == 429:
            kind = ProviderErrorKind.RATE_LIMIT
            message = "OneAPI 当前限制请求频率，本次不会自动重试。"
        elif status == 404:
            # 分析 404 错误原因（端点变更、资源不存在等）
            error_analysis = analyze_404_error(
                url="OneAPI HTTP 404 错误",
                response_body=None,
                headers=None,
            )

            # 创建错误上下文
            error_context = create_error_context(
                error=Exception("HTTP 404 错误"),
                additional_info={
                    "status_code": status,
                    "error_analysis": error_analysis,
                    "service": "oneapi",
                },
            )

            # 记录错误
            error_context.log_error()

            # 404 是服务层错误（端点不存在或已变更），不是参数校验错误
            # 根据分析结果决定是否可重试
            raise LicensedProviderError(
                "OneAPI 请求的资源未找到(404)，接口可能已变更。",
                kind=ProviderErrorKind.SERVICE,
                code=str(status),
                retryable=error_analysis.get("is_retryable", False),
            )
        elif status >= 500:
            # 创建错误上下文
            error_context = create_error_context(
                error=Exception(f"HTTP {status} 服务错误"),
                additional_info={
                    "status_code": status,
                    "service": "oneapi",
                },
            )

            # 记录错误
            error_context.log_error()

            raise LicensedProviderError(
                "OneAPI 服务暂时不可用，系统最多自动重试一次。",
                kind=ProviderErrorKind.SERVICE,
                code=str(status),
                retryable=True,
            )
        else:
            # 创建错误上下文
            error_context = create_error_context(
                error=Exception(f"HTTP {status} 错误"),
                additional_info={
                    "status_code": status,
                    "service": "oneapi",
                },
            )

            # 记录错误
            error_context.log_error()

            kind = ProviderErrorKind.VALIDATION
            message = "OneAPI 拒绝了本次请求，请检查查询参数。"
        raise LicensedProviderError(message, kind=kind, code=str(status))

    @staticmethod
    def _raise_business_error(
        code: str, body: Mapping[str, Any], endpoint: str = ""
    ) -> None:
        if code == "401":
            error = (ProviderErrorKind.AUTHORIZATION, "OneAPI API Key 无效。")
        elif code == "403":
            error = (ProviderErrorKind.AUTHORIZATION, "OneAPI 账户当前不可用。")
        elif code == "301":
            error = (ProviderErrorKind.AUTHORIZATION, "OneAPI 账户余额不足。")
        elif code == "429":
            error = (ProviderErrorKind.RATE_LIMIT, "OneAPI 当前限制请求频率。")
        elif code == "404":
            error = (ProviderErrorKind.SERVICE, "OneAPI 接口不可用或已变更。")
        elif code == "0" and "wechat-search" in endpoint:
            error = (
                ProviderErrorKind.VALIDATION,
                "视频号查询参数未通过供应商校验，本次没有有效结果。",
            )
        else:
            error = (
                ProviderErrorKind.VALIDATION,
                "OneAPI 未接受本次查询参数，请查看页面中的平台诊断信息。",
            )
        raise LicensedProviderError(error[1], kind=error[0], code=code or "missing")

    def _normalize_item(
        self,
        platform: Platform,
        raw_item: Mapping[str, Any],
        *,
        rank: int,
        observed_at: datetime,
        request_id: str,
    ) -> ProviderSearchItem:
        item = self._unwrap_item(raw_item)
        warnings: list[str] = []
        item_id = self._optional_text(
            self._first(
                item,
                "aweme_id",
                "awemeId",
                "note_id",
                "noteId",
                "object_id",
                "objectId",
                "finder_feed_id",
                "finderFeedId",
                "feed_id",
                "feedId",
                "video_id",
                "videoId",
                "item_id",
                "itemId",
                "id",
            ),
        )
        title = self._required_text(
            self._first(
                item,
                "desc",
                "title",
                "display_title",
                "displayTitle",
                "description",
                "content",
            ),
            "标题",
        )
        if item_id is None:
            item_id = self._proxy_id(
                platform,
                "missing-item-id",
                raw_item,
                title,
                str(rank),
            )
            warnings.append("供应商未返回作品ID，已使用本地稳定代理ID。")
        author = self._first_mapping(
            item,
            "author",
            "user",
            "user_info",
            "userInfo",
            "contact",
            "finder_info",
            "finderInfo",
        )
        author_id = self._optional_text(
            self._first(
                author,
                "sec_uid",
                "uid",
                "user_id",
                "userId",
                "author_id",
                "authorId",
                "finder_username",
                "finderUsername",
                "username",
                "id",
            )
            or self._first(
                item,
                "author_id",
                "authorId",
                "user_id",
                "userId",
                "finder_username",
                "finderUsername",
            ),
        )
        author_name = self._optional_text(
            self._first(
                author,
                "nickname",
                "nick_name",
                "nickName",
                "name",
                "username",
            )
            or self._first(item, "author_name", "authorName", "nickname"),
        )
        if author_id is None:
            author_id = self._proxy_id(
                platform,
                "missing-author-id",
                raw_item,
                title,
                item_id,
            )
            warnings.append("供应商未返回作者ID，已使用本地稳定代理ID。")
        if author_name is None:
            author_name = "未知作者"
            warnings.append("供应商未返回作者名称。")
        published_at = self._to_datetime(
            self._first(
                item,
                "create_time",
                "createTime",
                "publish_time",
                "publishTime",
                "published_at",
                "publishedAt",
                "timestamp",
                "time",
            )
        )
        if published_at is None:
            published_at = observed_at
            warnings.append("供应商未返回有效发布时间，使用采样时间；时间窗无法核验。")

        statistics = self._first_mapping(
            item,
            "statistics",
            "stats",
            "interact_info",
            "interactInfo",
            "interaction",
        )
        metrics_source = {**item, **statistics}
        share_info = self._first_mapping(
            item,
            "share_info",
            "shareInfo",
            "share",
            "shareInfoV2",
        )
        source_url = self._first(
            item,
            "share_url",
            "shareUrl",
            "note_url",
            "noteUrl",
            "source_url",
            "sourceUrl",
            "web_url",
            "webUrl",
            "url",
        ) or self._first(
            share_info,
            "share_url",
            "shareUrl",
            "url",
            "link",
        )
        parsed_source_url = None
        if source_url:
            try:
                parsed_source_url = HttpUrl(str(source_url))
            except ValueError:
                warnings.append("供应商返回的原视频链接无效，已置为空。")
        else:
            warnings.append("供应商未返回原视频链接，未生成替代链接。")
        return ProviderSearchItem(
            platform=platform,
            platform_item_id=item_id,
            title=title,
            author_id=author_id,
            author_name=author_name,
            published_at=published_at,
            source_url=parsed_source_url,
            provider_rank=rank,
            metrics=VideoMetricSnapshot(
                item_id=item_id,
                sampled_at=observed_at,
                plays=self._to_int(
                    self._first(
                        metrics_source,
                        "play_count",
                        "playCount",
                        "view_count",
                        "viewCount",
                        "read_count",
                        "readCount",
                        "plays",
                    )
                ),
                likes=self._to_int(
                    self._first(
                        metrics_source,
                        "digg_count",
                        "diggCount",
                        "like_count",
                        "likeCount",
                        "liked_count",
                        "likedCount",
                        "likes",
                    )
                ),
                comments=self._to_int(
                    self._first(
                        metrics_source, "comment_count", "commentCount", "comments"
                    )
                ),
                shares=self._to_int(
                    self._first(
                        metrics_source,
                        "share_count",
                        "shareCount",
                        "forward_count",
                        "forwardCount",
                        "shares",
                    )
                ),
                favorites=self._to_int(
                    self._first(
                        metrics_source,
                        "collect_count",
                        "collectCount",
                        "collected_count",
                        "collectedCount",
                        "favorite_count",
                        "favoriteCount",
                        "favorites",
                    )
                ),
                confidence=0.7,
            ),
            evidence=f"oneapi:{request_id}",
            data_quality_warnings=warnings,
        )

    @classmethod
    def _unwrap_item(cls, raw_item: Mapping[str, Any]) -> dict[str, Any]:
        merged = dict(raw_item)

        def merge_known(current: Mapping[str, Any], depth: int) -> None:
            if depth > 4:
                return
            for key in (
                "note_card",
                "noteCard",
                "note",
                "aweme_info",
                "awemeInfo",
                "video",
                "object",
                "item",
                "feed",
            ):
                nested = current.get(key)
                if isinstance(nested, Mapping):
                    merged.update(nested)
                    merge_known(nested, depth + 1)

        merge_known(raw_item, 0)
        return merged

    @classmethod
    def _extract_candidate_list(cls, value: Any) -> list[Mapping[str, Any]]:
        candidates: list[list[Mapping[str, Any]]] = []

        def visit(current: Any, depth: int) -> None:
            if depth > 4:
                return
            if isinstance(current, list):
                mappings = [item for item in current if isinstance(item, Mapping)]
                if mappings:
                    candidates.append(mappings)
                return
            if isinstance(current, Mapping):
                preferred = (
                    "aweme_list",
                    "items",
                    "notes",
                    "object_list",
                    "videos",
                    "list",
                    "result",
                    "data",
                    "records",
                )
                for key in preferred:
                    if key in current:
                        visit(current[key], depth + 1)
                for key, nested in current.items():
                    if key not in preferred and isinstance(nested, (Mapping, list)):
                        visit(nested, depth + 1)

        visit(value, 0)
        if not candidates:
            return []
        id_keys = {
            "aweme_id",
            "awemeId",
            "note_id",
            "noteId",
            "object_id",
            "objectId",
            "finder_feed_id",
            "finderFeedId",
            "feed_id",
            "feedId",
            "video_id",
            "videoId",
            "item_id",
            "itemId",
            "id",
        }
        title_keys = {
            "desc",
            "title",
            "display_title",
            "displayTitle",
            "description",
        }

        def score(items: list[Mapping[str, Any]]) -> tuple[int, int, int]:
            unwrapped = [cls._unwrap_item(item) for item in items]
            id_hits = sum(bool(id_keys.intersection(item)) for item in unwrapped)
            title_hits = sum(bool(title_keys.intersection(item)) for item in unwrapped)
            return id_hits, title_hits, len(items)

        return max(candidates, key=score)

    @classmethod
    def _extract_media_url(
        cls,
        value: Any,
        *,
        prefer_low_quality: bool = False,
    ) -> str | None:
        media_keys = {
            "play_url",
            "playUrl",
            "play_addr",
            "playAddr",
            "url_list",
            "urlList",
            "video_url",
            "videoUrl",
            "audio_url",
            "audioUrl",
            "audio_addr",
            "audioAddr",
            "download_url",
            "downloadUrl",
            "media_url",
            "mediaUrl",
            "master_url",
            "masterUrl",
            "backup_url",
            "backupUrl",
            "src",
        }
        candidates: list[tuple[int, float, int, str]] = []

        def visit(
            current: Any,
            depth: int,
            key_path: tuple[str, ...] = (),
            quality_hint: float | None = None,
        ) -> None:
            if depth > 8:
                return
            if isinstance(current, str):
                text = current.strip()
                key_hint = key_path[-1] if key_path else ""
                if text.startswith(("https://", "http://")) and (
                    key_hint in media_keys or cls._looks_like_media_url(text)
                ):
                    path_text = ".".join(key_path).casefold()
                    if any(
                        marker in text.casefold()
                        for marker in (
                            "douyin.com/video/",
                            "xiaohongshu.com/explore/",
                            "weixin.qq.com/sph/",
                            "channels.weixin.qq.com/",
                        )
                    ):
                        return
                    media_priority = 0
                    if "music" in path_text:
                        media_priority = 3
                    elif "audio" in path_text:
                        # 详情响应里的音频字段可能是背景音乐，而不是作品口播。
                        # 第一版自动化闭环只选最低码率视频，再由 FFmpeg
                        # 提取其实际音轨。
                        media_priority = 2
                    elif not prefer_low_quality:
                        media_priority = 0
                    quality = quality_hint if quality_hint is not None else float("inf")
                    https_priority = 0 if text.startswith("https://") else 1
                    candidates.append((media_priority, quality, https_priority, text))
                return
            if isinstance(current, Mapping):
                next_quality = quality_hint
                for key in (
                    "bit_rate",
                    "bitRate",
                    "bitrate",
                    "data_size",
                    "dataSize",
                    "file_size",
                    "fileSize",
                    "size",
                ):
                    numeric = cls._to_float(current.get(key))
                    if numeric is not None:
                        next_quality = numeric
                        break
                for key, nested in current.items():
                    visit(nested, depth + 1, (*key_path, str(key)), next_quality)
                return
            if isinstance(current, list):
                for nested in current:
                    visit(nested, depth + 1, key_path, quality_hint)

        visit(value, 0)
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][3]

    @staticmethod
    def _looks_like_media_url(url: str) -> bool:
        text = url.casefold()
        if text.endswith((".mp4", ".mov")) or ".mp4?" in text or ".mov?" in text:
            return True
        media_markers = (
            "video",
            "play",
            "byte",
            "ixigua",
            "douyinvod",
            "xhscdn",
            "sns-video",
            "qpic.cn",
        )
        page_markers = (
            "douyin.com/video/",
            "xiaohongshu.com/explore/",
            "weixin.qq.com/sph/",
            "channels.weixin.qq.com/",
        )
        return any(marker in text for marker in media_markers) and not any(
            marker in text for marker in page_markers
        )

    @classmethod
    def _extract_usage_records(cls, value: Any) -> list[Mapping[str, Any]]:
        if isinstance(value, Mapping):
            records = cls._first(value, "records", "items", "list", "data")
            if isinstance(records, list):
                return [item for item in records if isinstance(item, Mapping)]
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        return []

    def _record_cost(self, item: Mapping[str, Any]) -> float:
        direct = self._to_float(self._first(item, "cost", "amount", "fee"))
        if direct is not None:
            return direct
        return self._estimate_record_cost(item)

    def _estimate_record_cost(self, item: Mapping[str, Any]) -> float:
        count = self._to_int(
            self._first(
                item,
                "successCount",
                "success_count",
                "success",
                "count",
                "request_count",
                "times",
            )
        )
        if not count:
            return 0.0
        record_text = json.dumps(item, ensure_ascii=False).casefold()
        platform = self._platform_from_record_text(record_text)
        if platform is None:
            return 0.0
        return round(count * self.endpoint_prices_cny[platform], 4)

    @staticmethod
    def _platform_from_record_text(text: str) -> Platform | None:
        if "douyin" in text or "抖音" in text:
            return Platform.DOUYIN
        if "xiaohongshu" in text or "xhs" in text or "小红书" in text:
            return Platform.XIAOHONGSHU
        if "wechat" in text or "weixin" in text or "视频号" in text:
            return Platform.WECHAT_CHANNELS
        return None

    @staticmethod
    def _first(value: Any, *keys: str) -> Any:
        if not isinstance(value, Mapping):
            return None
        for key in keys:
            if key in value and value[key] not in (None, ""):
                return value[key]
        return None

    @classmethod
    def _first_mapping(cls, value: Any, *keys: str) -> Mapping[str, Any]:
        found = cls._first(value, *keys)
        return found if isinstance(found, Mapping) else {}

    @staticmethod
    def _required_text(value: Any, label: str) -> str:
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ValueError(f"缺少{label}")
        return text

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        text = str(value).strip() if value is not None else ""
        return text or None

    @staticmethod
    def _proxy_id(
        platform: Platform,
        reason: str,
        raw_item: Mapping[str, Any],
        *stable_parts: str,
    ) -> str:
        payload = json.dumps(raw_item, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(
            "|".join((platform.value, reason, payload, *stable_parts)).encode("utf-8")
        ).hexdigest()[:20]
        return f"proxy-{reason}-{digest}"

    @staticmethod
    def _to_datetime(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.astimezone()
        if isinstance(value, (int, float)) or str(value).strip().isdigit():
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            try:
                return datetime.fromtimestamp(number).astimezone()
            except (OSError, OverflowError, ValueError):
                return None
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.astimezone()
        except ValueError:
            return None

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value in (None, "", "-"):
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return max(0, int(value))
        text = str(value).strip().replace(",", "")
        multiplier = 1
        if text.endswith("万"):
            text, multiplier = text[:-1], 10_000
        elif text.endswith("亿"):
            text, multiplier = text[:-1], 100_000_000
        try:
            return max(0, int(float(text) * multiplier))
        except ValueError:
            return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, "", "-"):
            return None
        try:
            return max(0.0, float(str(value).replace(",", "")))
        except ValueError:
            return None

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().casefold() in {"1", "true", "yes"}
        return bool(value)

    @staticmethod
    def _source_url(platform: Platform, item_id: str) -> str:
        if platform == Platform.DOUYIN:
            return f"https://www.douyin.com/video/{item_id}"
        if platform == Platform.XIAOHONGSHU:
            return f"https://www.xiaohongshu.com/explore/{item_id}"
        return f"https://channels.weixin.qq.com/platform/post/{item_id}"

    @staticmethod
    def _request_id(body: Mapping[str, Any], idempotency_key: str) -> str:
        data = body.get("data")
        for value in (
            body.get("request_id"),
            body.get("trace_id"),
            OneApiLicensedSearchProvider._first(data, "request_id", "trace_id"),
        ):
            if value not in (None, ""):
                return str(value)
        return "oneapi-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:20]
