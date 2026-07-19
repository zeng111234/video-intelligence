from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.adapters.licensed import LicensedProviderError
from src.contracts import CandidateRepository, LicensedSearchProvider
from src.models import (
    CandidateMatch,
    DataSource,
    DiscoveryResult,
    EligibilityStatus,
    ImportErrorDetail,
    NormalizedCandidate,
    Platform,
    PlatformRunStatus,
    PlatformSearchRun,
    ProviderErrorKind,
    ProviderMode,
    ProviderSearchError,
    ProviderSearchPage,
    SamplingCheckpoint,
    SearchBatch,
    SearchBatchStatus,
    SourcePage,
)
from src.platforms import SUPPORTED_PLATFORMS
from src.retry import ExternalServiceError, RetryPolicy, retry_with_policy
from src.services.keyword_trend import KeywordTrendService
from src.services.source import SourceService

CACHE_TTL_MINUTES = 10
DUPLICATE_GUARD_SECONDS = 60
MONTHLY_WARNING_QUERIES = 360
MONTHLY_HARD_LIMIT_QUERIES = 450


@dataclass(frozen=True)
class PlatformSearchPreview:
    platform: Platform
    cache_hit: bool
    estimated_api_calls: int
    blocked_reason: str | None = None


class CommercialSearchService:
    def __init__(
        self,
        repository: CandidateRepository,
        source_service: SourceService,
        trend_service: KeywordTrendService,
        provider: LicensedSearchProvider,
        *,
        clock=None,
    ) -> None:
        self.repository = repository
        self.source_service = source_service
        self.trend_service = trend_service
        self.provider = provider
        self.clock = clock or (lambda: datetime.now().astimezone())

    def preview(
        self,
        *,
        keyword: str,
        published_window_days: int = 7,
        count: int = 10,
        force_refresh: bool = False,
    ) -> list[PlatformSearchPreview]:
        keyword = self._validate_request(keyword, published_window_days, count)
        capability = self.provider.capabilities()
        now = self.clock()
        monthly_queries = self.monthly_query_count(now)
        previews: list[PlatformSearchPreview] = []
        pending_calls = 0
        for platform in SUPPORTED_PLATFORMS:
            cached = (
                None
                if force_refresh
                else self._cached_run(
                    provider=capability.provider_name,
                    platform=platform,
                    keyword=keyword,
                    published_window_days=published_window_days,
                    count=count,
                    now=now,
                )
            )
            estimated_calls = (
                0 if cached or capability.mode == ProviderMode.SANDBOX else 1
            )
            blocked_reason = None
            if not capability.enabled:
                blocked_reason = "商业接口尚未完成签约与生产验收"
            elif platform not in capability.supported_platforms:
                blocked_reason = "供应商未开放该平台"
            elif (
                monthly_queries + pending_calls + estimated_calls
                > MONTHLY_HARD_LIMIT_QUERIES
            ):
                blocked_reason = "已达到本月 450 次平台查询上限"
            if not blocked_reason:
                pending_calls += estimated_calls
            previews.append(
                PlatformSearchPreview(
                    platform=platform,
                    cache_hit=cached is not None,
                    estimated_api_calls=estimated_calls,
                    blocked_reason=blocked_reason,
                )
            )
        return previews

    def execute(
        self,
        *,
        keyword: str,
        published_window_days: int = 7,
        count: int = 10,
        force_refresh: bool = False,
    ) -> SearchBatch:
        keyword = self._validate_request(keyword, published_window_days, count)
        capability = self.provider.capabilities()
        if not capability.enabled:
            raise ValueError("商业数据接口尚未配置或验收，未发起任何真实请求。")
        batch = SearchBatch(
            keyword=keyword,
            published_window_days=published_window_days,
            requested_count_per_platform=count,
            provider=capability.provider_name,
            mode=capability.mode,
            force_refresh=force_refresh,
        )
        self.repository.save_search_batch(batch)
        batch = batch.model_copy(update={"status": SearchBatchStatus.RUNNING})
        self.repository.save_search_batch(batch)

        runs: list[PlatformSearchRun] = []
        for platform in SUPPORTED_PLATFORMS:
            runs.append(
                self._execute_platform(
                    batch=batch,
                    platform=platform,
                    keyword=keyword,
                    published_window_days=published_window_days,
                    count=count,
                    force_refresh=force_refresh,
                )
            )

        successful = sum(
            run.status in {PlatformRunStatus.SUCCEEDED, PlatformRunStatus.CACHED}
            for run in runs
        )
        if successful == len(runs):
            status = SearchBatchStatus.SUCCEEDED
        elif successful:
            status = SearchBatchStatus.PARTIAL
        else:
            status = SearchBatchStatus.FAILED
        errors = [run.error for run in runs if run.error]
        batch = batch.model_copy(
            update={
                "status": status,
                "platform_run_ids": [run.run_id for run in runs],
                "finished_at": self.clock(),
                "error": "；".join(errors) if errors else None,
            }
        )
        self.repository.save_search_batch(batch)
        return batch

    def monthly_query_count(self, now: datetime | None = None) -> int:
        current = now or self.clock()
        month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self.repository.monthly_platform_query_count(month_start)

    def _execute_platform(
        self,
        *,
        batch: SearchBatch,
        platform: Platform,
        keyword: str,
        published_window_days: int,
        count: int,
        force_refresh: bool,
    ) -> PlatformSearchRun:
        capability = self.provider.capabilities()
        started_at = self.clock()
        fingerprint = self._fingerprint(
            capability.provider_name,
            platform,
            keyword,
            published_window_days,
            count,
        )
        idempotency_key = hashlib.sha256(
            f"{batch.batch_id}|{fingerprint}".encode("utf-8")
        ).hexdigest()
        run = PlatformSearchRun(
            batch_id=batch.batch_id,
            platform=platform,
            provider=capability.provider_name,
            mode=capability.mode,
            requested_count=count,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            credential_alias=capability.credential_alias,
            started_at=started_at,
        )

        if platform not in capability.supported_platforms:
            return self._finish_run(
                run,
                status=PlatformRunStatus.BLOCKED,
                error="供应商未开放该平台，未发起请求。",
            )

        cached = (
            None
            if force_refresh
            else self._cached_run(
                provider=capability.provider_name,
                platform=platform,
                keyword=keyword,
                published_window_days=published_window_days,
                count=count,
                now=started_at,
            )
        )
        if cached:
            return self._finish_run(
                run,
                status=PlatformRunStatus.CACHED,
                returned_count=cached.returned_count,
                cached_from_run_id=cached.run_id,
                cache_hit=True,
            )

        if (
            capability.mode == ProviderMode.PRODUCTION
            and self.monthly_query_count(started_at) >= MONTHLY_HARD_LIMIT_QUERIES
        ):
            return self._finish_run(
                run,
                status=PlatformRunStatus.BLOCKED,
                error="已达到本月 450 次平台查询上限，未发起请求。",
            )
        if self.repository.has_unresolved_platform_search_request(fingerprint):
            return self._finish_run(
                run,
                status=PlatformRunStatus.BLOCKED,
                error="上次请求费用状态待核对，请联系管理员确认后再试。",
            )
        if not self.repository.claim_platform_search_request(
            fingerprint,
            run.run_id,
            started_at,
            ttl_seconds=DUPLICATE_GUARD_SECONDS,
        ):
            return self._finish_run(
                run,
                status=PlatformRunStatus.BLOCKED,
                error="相同请求刚刚执行过，请等待 60 秒，防止重复计费。",
            )

        run = run.model_copy(update={"status": PlatformRunStatus.RUNNING})
        self.repository.save_platform_search_run(run)
        published_after = started_at - timedelta(days=published_window_days)
        try:
            page = self._search_with_retry(
                platform=platform,
                keyword=keyword,
                published_after=published_after,
                limit=count,
                idempotency_key=idempotency_key,
            )
            normalized, validation_errors = self._normalize_page(
                page,
                platform=platform,
                keyword=keyword,
                provider=capability.provider_name,
                published_after=published_after,
                limit=count,
            )
            provider_errors = [*page.errors, *validation_errors]
            import_errors = [
                ImportErrorDetail(
                    row=(error.item_index or 0) + 1,
                    field="provider",
                    message=error.message,
                )
                for error in provider_errors
            ]
            source_page = SourcePage(items=normalized, errors=import_errors)
            report = (
                self.source_service.import_page(source_page) if normalized else None
            )
            discovery = DiscoveryResult(
                request_id=run.run_id,
                batch_id=batch.batch_id,
                keyword=keyword,
                provider_name=capability.provider_name,
                platform=platform,
                requested_count=count,
                fetched_count=len(page.items),
                unique_count=len(normalized),
                duplicate_count=max(0, len(page.items[:count]) - len(normalized)),
                exhausted=not page.has_more,
                partial=bool(provider_errors) or len(normalized) < count,
                permission_status=capability.permission_status,
                publish_time=published_window_days,
                sort_type=0,
                api_call_count=page.api_call_count,
                started_at=started_at,
                finished_at=self.clock(),
                import_report=report,
                errors=import_errors,
                request_fingerprint=fingerprint,
                provider_request_id=page.request_id,
                billable_units=page.billable_units,
            )
            self.repository.save_discovery_result(discovery)
            self._save_matches_and_checkpoints(
                discovery=discovery,
                normalized=normalized,
                platform=platform,
                provider=capability.provider_name,
                keyword=keyword,
                published_window_days=published_window_days,
                rank_by_item={
                    item.platform_item_id: item.provider_rank
                    for item in page.items[:count]
                    if item.platform == platform
                },
            )
            self.trend_service.recompute(
                keyword,
                platform=platform,
                provider_name=capability.provider_name,
            )
            finished = self._finish_run(
                run,
                status=PlatformRunStatus.SUCCEEDED,
                returned_count=len(normalized),
                api_call_count=page.api_call_count,
                billable_units=page.billable_units,
                quota_remaining=page.quota_remaining,
                provider_request_id=page.request_id,
                errors=provider_errors,
            )
            self.repository.mark_platform_search_request(
                fingerprint, "succeeded", finished.finished_at or self.clock()
            )
            return finished
        except LicensedProviderError as exc:
            unknown = (
                exc.outcome_unknown or exc.kind == ProviderErrorKind.OUTCOME_UNKNOWN
            )
            status = (
                PlatformRunStatus.OUTCOME_UNKNOWN
                if unknown
                else PlatformRunStatus.FAILED
            )
            api_calls = 1 if capability.mode == ProviderMode.PRODUCTION else 0
            finished = self._finish_run(
                run,
                status=status,
                api_call_count=api_calls,
                error=str(exc),
                errors=[
                    ProviderSearchError(
                        kind=exc.kind,
                        code=exc.code,
                        message=str(exc),
                        retryable=exc.retryable,
                    )
                ],
            )
            self.repository.mark_platform_search_request(
                fingerprint,
                "outcome_unknown" if unknown else "failed",
                finished.finished_at or self.clock(),
            )
            return finished
        except Exception as exc:
            api_calls = 1 if capability.mode == ProviderMode.PRODUCTION else 0
            finished = self._finish_run(
                run,
                status=PlatformRunStatus.OUTCOME_UNKNOWN,
                api_call_count=api_calls,
                error=f"供应商响应状态不明确：{exc}",
                errors=[
                    ProviderSearchError(
                        kind=ProviderErrorKind.OUTCOME_UNKNOWN,
                        message="供应商响应状态不明确，请先核对用量再重试。",
                    )
                ],
            )
            self.repository.mark_platform_search_request(
                fingerprint, "outcome_unknown", finished.finished_at or self.clock()
            )
            return finished

    def _search_with_retry(self, **kwargs) -> ProviderSearchPage:
        def _is_retryable(exc: BaseException) -> bool:
            if isinstance(exc, LicensedProviderError):
                return (
                    exc.retryable
                    and not exc.outcome_unknown
                    and exc.kind
                    in {ProviderErrorKind.CONNECTION, ProviderErrorKind.SERVICE}
                )
            return isinstance(exc, (ConnectionError, TimeoutError))

        try:
            return retry_with_policy(
                lambda: self.provider.search(**kwargs),
                policy=RetryPolicy(max_attempts=2, base_delay=0),
                retry_for=(LicensedProviderError, ConnectionError, TimeoutError),
                retryable=_is_retryable,
                error_message="商业数据接口调用失败。",
            )
        except ExternalServiceError as exc:
            cause = exc.__cause__
            if isinstance(cause, LicensedProviderError):
                raise cause
            if isinstance(cause, (ConnectionError, TimeoutError)):
                raise LicensedProviderError(
                    f"连接商业数据接口失败：{cause}",
                    kind=ProviderErrorKind.CONNECTION,
                    retryable=False,
                    outcome_unknown=True,
                ) from cause
            raise LicensedProviderError(
                "商业数据接口调用失败。",
                kind=ProviderErrorKind.SERVICE,
            ) from exc

    @staticmethod
    def _normalize_page(
        page: ProviderSearchPage,
        *,
        platform: Platform,
        keyword: str,
        provider: str,
        published_after: datetime,
        limit: int,
    ) -> tuple[list[NormalizedCandidate], list[ProviderSearchError]]:
        errors: list[ProviderSearchError] = []
        if page.platform != platform or page.provider != provider:
            raise LicensedProviderError(
                "供应商响应的平台或供应商标识与请求不一致。",
                kind=ProviderErrorKind.VALIDATION,
            )
        normalized: list[NormalizedCandidate] = []
        seen: set[str] = set()
        for index, item in enumerate(page.items[:limit]):
            reason = None
            if item.platform != platform:
                reason = "作品平台与当前子任务不一致。"
            elif item.platform_item_id in seen:
                reason = "供应商返回了重复作品ID。"
            elif item.published_at < published_after:
                reason = "作品发布时间超出本次查询范围。"
            elif not CommercialSearchService._url_matches_platform(
                str(item.source_url), platform
            ):
                reason = "作品链接与平台不匹配。"
            if reason:
                errors.append(
                    ProviderSearchError(
                        kind=ProviderErrorKind.VALIDATION,
                        message=reason,
                        item_index=index,
                    )
                )
                continue
            seen.add(item.platform_item_id)
            normalized.append(
                NormalizedCandidate(
                    platform_item_id=item.platform_item_id,
                    title=item.title,
                    author_id=item.author_id,
                    author_name=item.author_name,
                    platform=platform,
                    category=f"关键词/{keyword}",
                    published_at=item.published_at,
                    source_url=item.source_url,
                    source_type=DataSource.LICENSED_PROVIDER,
                    metrics=item.metrics,
                    matched_by=[keyword],
                    cohort_key=f"{provider}:{platform.value}:keyword:{keyword.casefold()}",
                    eligibility_status=EligibilityStatus.AUTO_MATCHED,
                    evidence=item.evidence,
                )
            )
        return normalized, errors

    def _save_matches_and_checkpoints(
        self,
        *,
        discovery: DiscoveryResult,
        normalized: list[NormalizedCandidate],
        platform: Platform,
        provider: str,
        keyword: str,
        published_window_days: int,
        rank_by_item: dict[str, int],
    ) -> None:
        candidate_ids = {
            (item.platform, item.platform_item_id): item.video_id
            for item in self.repository.list_candidates()
        }
        keyword_key = keyword.casefold()
        existing_checkpoints = self.repository.list_sampling_checkpoints(keyword_key)
        for fallback_rank, item in enumerate(normalized, start=1):
            video_id = candidate_ids.get((platform, item.platform_item_id))
            if not video_id:
                continue
            self.repository.save_candidate_match(
                CandidateMatch(
                    request_id=discovery.request_id,
                    video_id=video_id,
                    keyword=keyword_key,
                    cohort_key=f"{provider}:{platform.value}:keyword:{keyword_key}",
                    platform=platform,
                    provider_name=provider,
                    platform_rank=rank_by_item.get(
                        item.platform_item_id, fallback_rank
                    ),
                    observed_at=item.metrics.sampled_at,
                    publish_time=published_window_days,
                    sort_type=0,
                    evidence=item.evidence,
                )
            )
            has_plan = any(
                checkpoint.candidate_id == video_id
                and checkpoint.platform == platform
                and checkpoint.provider_name == provider
                for checkpoint in existing_checkpoints
            )
            if has_plan:
                continue
            for offset in (2, 6, 24):
                checkpoint = SamplingCheckpoint(
                    checkpoint_id=(
                        f"sample-{hashlib.sha256(f'{discovery.request_id}|{video_id}|{offset}'.encode()).hexdigest()[:12]}"
                    ),
                    keyword=keyword_key,
                    candidate_id=video_id,
                    request_id=discovery.request_id,
                    platform=platform,
                    provider_name=provider,
                    published_window_days=published_window_days,
                    offset_hours=offset,
                    due_at=item.metrics.sampled_at + timedelta(hours=offset),
                )
                self.repository.save_sampling_checkpoint(checkpoint)

    def _cached_run(
        self,
        *,
        provider: str,
        platform: Platform,
        keyword: str,
        published_window_days: int,
        count: int,
        now: datetime,
    ) -> PlatformSearchRun | None:
        return self.repository.find_cached_platform_search_run(
            provider=provider,
            platform=platform,
            keyword=keyword,
            published_window_days=published_window_days,
            requested_count=count,
            since=now - timedelta(minutes=CACHE_TTL_MINUTES),
        )

    def _finish_run(self, run: PlatformSearchRun, **updates) -> PlatformSearchRun:
        finished = run.model_copy(update={"finished_at": self.clock(), **updates})
        self.repository.save_platform_search_run(finished)
        return finished

    @staticmethod
    def _validate_request(keyword: str, published_window_days: int, count: int) -> str:
        normalized = keyword.strip()
        if not 2 <= len(normalized) <= 50:
            raise ValueError("关键词长度必须为 2 到 50 个字符。")
        if published_window_days not in {1, 7}:
            raise ValueError("召回时间范围只支持近 24 小时或近 7 天。")
        if not 1 <= count <= 10:
            raise ValueError("每个平台获取数量必须为 1 到 10 条。")
        return normalized

    @staticmethod
    def _fingerprint(
        provider: str,
        platform: Platform,
        keyword: str,
        published_window_days: int,
        count: int,
    ) -> str:
        payload = (
            f"{provider}|{platform.value}|{keyword.casefold()}|"
            f"{published_window_days}|{count}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _url_matches_platform(url: str, platform: Platform) -> bool:
        host_markers = {
            Platform.DOUYIN: ("douyin.com",),
            Platform.XIAOHONGSHU: ("xiaohongshu.com", "xhslink.com"),
            Platform.WECHAT_CHANNELS: (
                "channels.weixin.qq.com",
                "weixin.qq.com",
                "wechat.com",
            ),
        }
        return any(marker in url.casefold() for marker in host_markers[platform])
