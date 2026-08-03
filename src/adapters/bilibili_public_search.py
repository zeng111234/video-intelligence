from __future__ import annotations

import html
import math
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import HttpUrl

from src.adapters.licensed import LicensedProviderError
from src.models import (
    DataSource,
    Platform,
    ProviderCapability,
    ProviderErrorKind,
    ProviderMode,
    ProviderSearchError,
    ProviderSearchItem,
    ProviderSearchPage,
    ProviderUsage,
    VideoMetricSnapshot,
)
from src.retry import ExternalServiceError, run_with_single_retry


class BilibiliPublicSearchProvider:
    """Small, read-only adapter for Bilibili's public video search endpoint.

    It intentionally collects only visible search metadata.  It does not log in,
    download media, resolve playback URLs, or bypass an access check.
    """

    provider_name = "bilibili_public_search"
    source_type = DataSource.PUBLIC_RESEARCH
    _endpoint = "https://api.bilibili.com/x/web-interface/search/type"
    _detail_endpoint = "https://api.bilibili.com/x/web-interface/view"
    _page_size = 20
    _max_pages = 2

    def __init__(self, *, timeout_seconds: float = 12.0) -> None:
        self.timeout_seconds = timeout_seconds

    def capabilities(self) -> ProviderCapability:
        return ProviderCapability(
            provider_name=self.provider_name,
            display_name="B站公开搜索（免费）",
            mode=ProviderMode.PUBLIC_WEB,
            enabled=True,
            supported_platforms=[Platform.BILIBILI],
            max_page_size=30,
            supports_published_after=True,
            supports_metric_refresh=True,
            supports_usage=False,
            permission_status="public_metadata_only",
        )

    def search(
        self,
        platform: Platform,
        keyword: str,
        published_after: datetime | None,
        limit: int,
        idempotency_key: str,
        hotspot_window_hours: int | None = None,
    ) -> ProviderSearchPage:
        del idempotency_key, hotspot_window_hours
        if platform != Platform.BILIBILI:
            raise LicensedProviderError(
                "B站公开搜索只支持B站。",
                kind=ProviderErrorKind.VALIDATION,
            )
        if not 1 <= limit <= 30:
            raise LicensedProviderError(
                "B站公开搜索一次最多返回 30 条。",
                kind=ProviderErrorKind.VALIDATION,
            )

        observed_at = datetime.now().astimezone()
        items: list[ProviderSearchItem] = []
        raw_item_count = 0
        parsed_item_count = 0
        page_count = min(self._max_pages, max(1, math.ceil(limit / self._page_size)))
        has_more = False

        for page in range(1, page_count + 1):
            payload = self._fetch_page(keyword=keyword, page=page)
            data = payload.get("data") if isinstance(payload, dict) else None
            raw_items = data.get("result") if isinstance(data, dict) else None
            if not isinstance(raw_items, list):
                break
            raw_item_count += len(raw_items)
            for raw in raw_items:
                item = self._to_item(raw, observed_at=observed_at, rank=len(items) + 1)
                if item is None:
                    continue
                parsed_item_count += 1
                if published_after is not None and item.published_at < published_after:
                    continue
                items.append(item)
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                has_more = len(raw_items) > 0
                break
            has_more = bool(isinstance(data, dict) and data.get("numPages", 0) > page)
            if not raw_items:
                break

        return ProviderSearchPage(
            platform=Platform.BILIBILI,
            provider=self.provider_name,
            items=items,
            observed_at=observed_at,
            request_id=f"bilibili-{int(observed_at.timestamp())}",
            api_call_count=1,
            billable_units=0,
            has_more=has_more,
            raw_item_count=raw_item_count,
            parsed_item_count=parsed_item_count,
            payload_diagnostic=(
                "仅使用B站公开搜索元数据；点赞、转发未返回，不把单次数据当作增长趋势。"
            ),
        )

    def refresh_metrics(
        self,
        platform: Platform,
        platform_item_ids: list[str],
        idempotency_key: str,
    ) -> ProviderSearchPage:
        if platform != Platform.BILIBILI:
            raise LicensedProviderError(
                "B站公开详情只支持B站。",
                kind=ProviderErrorKind.VALIDATION,
            )
        if len(platform_item_ids) > 10:
            raise LicensedProviderError(
                "B站公开指标刷新一次最多查询 10 条。",
                kind=ProviderErrorKind.VALIDATION,
            )

        observed_at = datetime.now().astimezone()
        items: list[ProviderSearchItem] = []
        errors: list[ProviderSearchError] = []
        raw_item_count = 0
        detail_request_attempted = False

        for index, raw_item_id in enumerate(platform_item_ids):
            bvid = str(raw_item_id or "").strip()
            if not bvid:
                errors.append(
                    ProviderSearchError(
                        kind=ProviderErrorKind.VALIDATION,
                        code="invalid_item_id",
                        message="B站作品 ID 不能为空，已跳过该条刷新。",
                        item_index=index,
                    )
                )
                continue

            detail_request_attempted = True
            try:
                payload = self._fetch_detail(bvid=bvid)
                raw_item_count += 1
                data = payload.get("data") if isinstance(payload, dict) else None
                item = self._to_detail_item(
                    data,
                    bvid=bvid,
                    observed_at=observed_at,
                    rank=index + 1,
                )
            except LicensedProviderError as exc:
                errors.append(
                    ProviderSearchError(
                        kind=exc.kind,
                        code=exc.code,
                        message=f"B站作品 {bvid} 指标刷新失败：{exc}",
                        item_index=index,
                        retryable=exc.retryable,
                    )
                )
                continue
            except ValueError as exc:
                errors.append(
                    ProviderSearchError(
                        kind=ProviderErrorKind.VALIDATION,
                        code="invalid_detail_payload",
                        message=f"B站作品 {bvid} 详情字段不完整，已跳过：{exc}",
                        item_index=index,
                    )
                )
                continue

            items.append(item)

        return ProviderSearchPage(
            platform=Platform.BILIBILI,
            provider=self.provider_name,
            items=items,
            observed_at=observed_at,
            request_id=f"bilibili-refresh-{idempotency_key[:20]}",
            # ``ProviderSearchPage`` represents one logical provider operation.
            # The public detail endpoint itself accepts one BVID at a time.
            api_call_count=1 if detail_request_attempted else 0,
            billable_units=0,
            raw_item_count=raw_item_count,
            parsed_item_count=len(items),
            payload_diagnostic=(
                "仅使用B站公开作品详情元数据；逐条读取当前可见指标，不下载媒体或解析播放地址。"
            ),
            errors=errors,
        )

    def usage(self) -> ProviderUsage | None:
        return None

    def _fetch_page(self, *, keyword: str, page: int) -> dict[str, Any]:
        def request() -> dict[str, Any]:
            response = httpx.get(
                self._endpoint,
                params={
                    "search_type": "video",
                    "keyword": keyword,
                    "page": page,
                    "order": "pubdate",
                },
                headers={
                    # This endpoint rejects the bare ``Mozilla/5.0`` value
                    # used by generic HTTP clients.  These are ordinary
                    # content-negotiation headers only: no cookie, account,
                    # signature, or access-control bypass is used.
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/136.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Origin": "https://search.bilibili.com",
                    "Referer": "https://search.bilibili.com/",
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("code") != 0:
                message = (
                    payload.get("message")
                    if isinstance(payload, dict)
                    else "响应格式错误"
                )
                raise LicensedProviderError(
                    f"B站公开搜索暂不可用：{message}",
                    kind=ProviderErrorKind.SERVICE,
                )
            return payload

        try:
            return run_with_single_retry(
                request,
                retry_for=(httpx.RequestError, TimeoutError),
            )
        except ExternalServiceError as exc:
            raise LicensedProviderError(
                "B站公开搜索连接失败，已自动重试一次。",
                kind=ProviderErrorKind.CONNECTION,
                retryable=False,
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 412:
                raise LicensedProviderError(
                    "B站当前拒绝此公开搜索请求；系统不会绕过访问验证或伪造结果。",
                    kind=ProviderErrorKind.AUTHORIZATION,
                    retryable=False,
                ) from exc
            raise LicensedProviderError(
                f"B站公开搜索返回 HTTP {exc.response.status_code}。",
                kind=ProviderErrorKind.SERVICE,
            ) from exc

    def _fetch_detail(self, *, bvid: str) -> dict[str, Any]:
        def request() -> dict[str, Any]:
            response = httpx.get(
                self._detail_endpoint,
                params={"bvid": bvid},
                headers={
                    # This endpoint rejects the bare ``Mozilla/5.0`` value
                    # used by generic HTTP clients.  These are ordinary
                    # content-negotiation headers only: no cookie, account,
                    # signature, or access-control bypass is used.
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/136.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Origin": "https://search.bilibili.com",
                    "Referer": "https://search.bilibili.com/",
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("code") != 0:
                message = (
                    payload.get("message")
                    if isinstance(payload, dict)
                    else "响应格式错误"
                )
                raise LicensedProviderError(
                    f"B站公开作品详情暂不可用：{message}",
                    kind=ProviderErrorKind.SERVICE,
                )
            return payload

        try:
            return run_with_single_retry(
                request,
                retry_for=(httpx.RequestError, TimeoutError),
            )
        except ExternalServiceError as exc:
            raise LicensedProviderError(
                "B站公开作品详情连接失败，已自动重试一次。",
                kind=ProviderErrorKind.CONNECTION,
                retryable=False,
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 412:
                raise LicensedProviderError(
                    "B站当前拒绝此公开详情请求；系统不会绕过访问验证或伪造结果。",
                    kind=ProviderErrorKind.AUTHORIZATION,
                    retryable=False,
                ) from exc
            raise LicensedProviderError(
                f"B站公开作品详情返回 HTTP {exc.response.status_code}。",
                kind=ProviderErrorKind.SERVICE,
            ) from exc
        except ValueError as exc:
            raise LicensedProviderError(
                "B站公开作品详情响应无法解析。",
                kind=ProviderErrorKind.SERVICE,
            ) from exc

    @staticmethod
    def _to_item(
        raw: Any,
        *,
        observed_at: datetime,
        rank: int,
    ) -> ProviderSearchItem | None:
        if not isinstance(raw, dict):
            return None
        bvid = str(raw.get("bvid") or "").strip()
        title = BilibiliPublicSearchProvider._clean_text(raw.get("title"))
        author_name = BilibiliPublicSearchProvider._clean_text(raw.get("author"))
        try:
            published_at = datetime.fromtimestamp(
                int(raw.get("pubdate")), tz=timezone.utc
            ).astimezone()
        except (TypeError, ValueError, OSError):
            return None
        if not bvid or not title or not author_name:
            return None
        return ProviderSearchItem(
            platform=Platform.BILIBILI,
            platform_item_id=bvid,
            title=title,
            author_id=str(raw.get("mid") or f"bilibili-{bvid}"),
            author_name=author_name,
            published_at=published_at,
            source_url=HttpUrl(f"https://www.bilibili.com/video/{bvid}"),
            provider_rank=rank,
            metrics=VideoMetricSnapshot(
                item_id=bvid,
                sampled_at=observed_at,
                plays=BilibiliPublicSearchProvider._as_count(raw.get("play")),
                # ``video_review`` is the public search payload's danmaku
                # count, not the number of comments.  Keep comments unknown
                # until the public detail response provides ``stat.reply``.
                comments=None,
                favorites=BilibiliPublicSearchProvider._as_count(raw.get("favorites")),
                confidence=0.75,
            ),
            evidence="bilibili_public_search:order=pubdate",
            data_quality_warnings=[
                "B站公开搜索未返回评论、点赞、转发；video_review 为弹幕数，未作为评论使用。"
            ],
        )

    @staticmethod
    def _to_detail_item(
        raw: Any,
        *,
        bvid: str,
        observed_at: datetime,
        rank: int,
    ) -> ProviderSearchItem:
        if not isinstance(raw, dict):
            raise ValueError("未返回作品详情。")
        owner = raw.get("owner")
        if not isinstance(owner, dict):
            raise ValueError("未返回作者信息。")

        title = BilibiliPublicSearchProvider._clean_text(raw.get("title"))
        author_name = BilibiliPublicSearchProvider._clean_text(owner.get("name"))
        try:
            published_at = datetime.fromtimestamp(
                int(raw.get("pubdate")), tz=timezone.utc
            ).astimezone()
        except (TypeError, ValueError, OSError) as exc:
            raise ValueError("发布时间无效。") from exc
        if not title or not author_name:
            raise ValueError("标题或作者名称为空。")

        stat = raw.get("stat")
        if not isinstance(stat, dict):
            raise ValueError("未返回互动指标。")

        return ProviderSearchItem(
            platform=Platform.BILIBILI,
            platform_item_id=bvid,
            title=title,
            author_id=str(owner.get("mid") or f"bilibili-{bvid}"),
            author_name=author_name,
            published_at=published_at,
            duration_seconds=BilibiliPublicSearchProvider._as_duration_seconds(
                raw.get("duration")
            ),
            source_url=HttpUrl(f"https://www.bilibili.com/video/{bvid}"),
            provider_rank=rank,
            metrics=VideoMetricSnapshot(
                item_id=bvid,
                sampled_at=observed_at,
                plays=BilibiliPublicSearchProvider._as_count(stat.get("view")),
                likes=BilibiliPublicSearchProvider._as_count(stat.get("like")),
                comments=BilibiliPublicSearchProvider._as_count(stat.get("reply")),
                shares=BilibiliPublicSearchProvider._as_count(stat.get("share")),
                favorites=BilibiliPublicSearchProvider._as_count(stat.get("favorite")),
                confidence=0.85,
            ),
            evidence="bilibili_public_detail:x/web-interface/view",
            data_quality_warnings=[
                "B站公开详情仅反映本次采样时的可见指标，需结合后续采样判断增长。"
            ],
        )

    @staticmethod
    def _clean_text(value: Any) -> str:
        return html.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).strip()

    @staticmethod
    def _as_count(value: Any) -> int | None:
        if value is None:
            return None
        raw = str(value).replace(",", "").strip().casefold()
        if not raw:
            return None
        multiplier = 1
        if raw.endswith("万"):
            raw, multiplier = raw[:-1], 10_000
        elif raw.endswith("亿"):
            raw, multiplier = raw[:-1], 100_000_000
        try:
            return max(0, int(float(raw) * multiplier))
        except ValueError:
            return None

    @staticmethod
    def _as_duration_seconds(value: Any) -> int | None:
        if value is None:
            return None
        try:
            duration_seconds = int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
        return duration_seconds if duration_seconds > 0 else None
