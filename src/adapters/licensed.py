from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import HttpUrl

from src.models import (
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


class LicensedProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: ProviderErrorKind,
        code: str | None = None,
        retryable: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown


class DisabledLicensedSearchProvider:
    """Production placeholder that cannot access a network until a vendor is approved."""

    def __init__(self, provider_name: str = "commercial_provider_pending") -> None:
        self.provider_name = provider_name.strip() or "commercial_provider_pending"

    def capabilities(self) -> ProviderCapability:
        return ProviderCapability(
            provider_name=self.provider_name,
            display_name="商业数据接口（待签约）",
            mode=ProviderMode.PRODUCTION,
            enabled=False,
            supported_platforms=[],
            permission_status="contract_and_adapter_pending",
            missing_configuration=[
                "供应商沙箱文档",
                "API 凭证",
                "B端商业使用授权",
                "生产小流量验收",
            ],
        )

    def search(
        self,
        platform: Platform,
        keyword: str,
        published_after: datetime | None,
        limit: int,
        idempotency_key: str,
    ) -> ProviderSearchPage:
        raise LicensedProviderError(
            "商业数据接口尚未完成签约和生产验收，未发起真实请求。",
            kind=ProviderErrorKind.AUTHORIZATION,
        )

    def refresh_metrics(
        self,
        platform: Platform,
        platform_item_ids: list[str],
        idempotency_key: str,
    ) -> ProviderSearchPage:
        raise LicensedProviderError(
            "商业数据接口尚未完成签约和生产验收，未发起真实请求。",
            kind=ProviderErrorKind.AUTHORIZATION,
        )

    def usage(self) -> ProviderUsage | None:
        return None


class SandboxLicensedSearchProvider:
    """Deterministic, offline three-platform provider used only for UI validation."""

    provider_name = "licensed_sandbox"

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock or (lambda: datetime.now().astimezone())

    def capabilities(self) -> ProviderCapability:
        return ProviderCapability(
            provider_name=self.provider_name,
            display_name="三平台商业接口沙箱",
            mode=ProviderMode.SANDBOX,
            enabled=True,
            supported_platforms=[
                Platform.DOUYIN,
                Platform.XIAOHONGSHU,
                Platform.WECHAT_CHANNELS,
            ],
            max_page_size=10,
            supports_published_after=True,
            supports_metric_refresh=False,
            supports_usage=True,
            permission_status="sandbox_only",
            credential_alias="sandbox-no-secret",
        )

    def search(
        self,
        platform: Platform,
        keyword: str,
        published_after: datetime | None,
        limit: int,
        idempotency_key: str,
    ) -> ProviderSearchPage:
        capability = self.capabilities()
        keyword = keyword.strip()
        if platform not in capability.supported_platforms:
            raise LicensedProviderError(
                "沙箱不支持该平台。",
                kind=ProviderErrorKind.VALIDATION,
            )
        if not keyword:
            raise LicensedProviderError(
                "关键词不能为空。",
                kind=ProviderErrorKind.VALIDATION,
            )
        if not 1 <= limit <= capability.max_page_size:
            raise LicensedProviderError(
                "每个平台每次只能获取 1 到 10 条。",
                kind=ProviderErrorKind.VALIDATION,
            )

        observed_at = self.clock()
        window_hours = (
            max(1, int((observed_at - published_after).total_seconds() / 3600))
            if published_after is not None
            else 180 * 24
        )
        platform_labels = {
            Platform.DOUYIN: "抖音",
            Platform.XIAOHONGSHU: "小红书",
            Platform.WECHAT_CHANNELS: "微信视频号",
        }
        items: list[ProviderSearchItem] = []
        for rank in range(1, limit + 1):
            digest = hashlib.sha256(
                f"{platform.value}|{keyword.casefold()}|{rank}".encode("utf-8")
            ).hexdigest()
            item_id = f"sandbox-{digest[:18]}"
            age_hours = min(window_hours - 0.1, max(0.5, rank * window_hours / 12))
            published_at = observed_at - timedelta(hours=age_hours)
            base = int(digest[18:26], 16)
            time_bucket = int(observed_at.timestamp() // (2 * 3600))
            likes = 120 + base % 18000 + (time_bucket % 40) * (11 - rank)
            comments = 8 + base % 900
            favorites = 15 + base % 1600
            shares = 5 + base % 700
            plays = likes * (8 + base % 17)
            items.append(
                ProviderSearchItem(
                    platform=platform,
                    platform_item_id=item_id,
                    title=f"{keyword}热门内容案例 {rank}",
                    author_id=f"sandbox-author-{digest[26:38]}",
                    author_name=f"{platform_labels[platform]}示例创作者 {rank}",
                    published_at=published_at,
                    source_url=self._source_url(platform, item_id),
                    provider_rank=rank,
                    metrics=VideoMetricSnapshot(
                        item_id=item_id,
                        sampled_at=observed_at,
                        plays=plays,
                        likes=likes,
                        comments=comments,
                        shares=shares,
                        favorites=favorites,
                        confidence=0.65,
                    ),
                    evidence=f"sandbox:{idempotency_key[:16]}",
                )
            )
        return ProviderSearchPage(
            platform=platform,
            provider=self.provider_name,
            items=items,
            observed_at=observed_at,
            request_id=f"sandbox-{idempotency_key[:20]}",
            api_call_count=0,
            billable_units=0,
            has_more=False,
            raw_item_count=len(items),
            parsed_item_count=len(items),
        )

    def refresh_metrics(
        self,
        platform: Platform,
        platform_item_ids: list[str],
        idempotency_key: str,
    ) -> ProviderSearchPage:
        observed_at = self.clock()
        return ProviderSearchPage(
            platform=platform,
            provider=self.provider_name,
            observed_at=observed_at,
            request_id=f"sandbox-refresh-{idempotency_key[:12]}",
            errors=[
                ProviderSearchError(
                    kind=ProviderErrorKind.VALIDATION,
                    message="当前沙箱不提供独立指标刷新，请重新执行关键词演示查询。",
                )
            ],
        )

    def usage(self) -> ProviderUsage:
        now = self.clock()
        return ProviderUsage(
            provider=self.provider_name,
            period_started_at=now.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ),
            period_ends_at=(
                now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=32)
            ).replace(day=1),
            platform_queries=0,
            billable_units=0,
            estimated_cost=0,
        )

    @staticmethod
    def _source_url(platform: Platform, item_id: str) -> HttpUrl:
        if platform == Platform.DOUYIN:
            return HttpUrl(f"https://www.douyin.com/video/{item_id}")
        if platform == Platform.XIAOHONGSHU:
            return HttpUrl(f"https://www.xiaohongshu.com/explore/{item_id}")
        return HttpUrl(f"https://channels.weixin.qq.com/platform/post/{item_id}")
