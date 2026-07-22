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
    SamplingStatus,
)
from src.platforms import SUPPORTED_PLATFORMS
from src.services.keyword_trend import KeywordTrendService
from src.services.source import SourceService

CACHE_TTL_MINUTES = 360
DUPLICATE_GUARD_SECONDS = 60
MONTHLY_WARNING_QUERIES = 80
MONTHLY_HARD_LIMIT_QUERIES = 100
MONTHLY_HARD_LIMIT_COST_CNY = 10.0
RANKING_MODE = "keyword_hot"
KEYWORD_HOT_SORT_TYPE = 1
RECRAWL_OFFSETS_BY_WINDOW = {
    1: (2, 6, 12),
    7: (6, 24, 48),
}
RECRAWL_MISS_GRACE_MINUTES = 30


@dataclass(frozen=True)
class PlatformSearchPreview:
    platform: Platform
    cache_hit: bool
    estimated_api_calls: int
    estimated_cost_cny: float | None = None
    platform_unit_price_cny: float | None = None
    blocked_reason: str | None = None


class CommercialSearchService:
    def __init__(
        self,
        repository: CandidateRepository,
        source_service: SourceService,
        trend_service: KeywordTrendService,
        provider: LicensedSearchProvider,
        *,
        active_platforms: tuple[Platform, ...] | None = None,
        clock=None,
    ) -> None:
        self.repository = repository
        self.source_service = source_service
        self.trend_service = trend_service
        self.provider = provider
        selected_platforms = (
            SUPPORTED_PLATFORMS if active_platforms is None else active_platforms
        )
        self.active_platforms = tuple(dict.fromkeys(selected_platforms))
        if not self.active_platforms:
            raise ValueError("至少需要启用一个关键词搜索平台。")
        unsupported = set(self.active_platforms) - set(SUPPORTED_PLATFORMS)
        if unsupported:
            names = "、".join(sorted(item.value for item in unsupported))
            raise ValueError(f"不支持的关键词搜索平台：{names}。")
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
        monthly_cost = self.monthly_query_cost(now)
        prices = self._endpoint_prices()
        previews: list[PlatformSearchPreview] = []
        pending_calls = 0
        pending_cost = 0.0
        for platform in self.active_platforms:
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
            unit_price = prices.get(platform)
            estimated_cost = (
                round(estimated_calls * unit_price, 4)
                if unit_price is not None
                else None
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
                blocked_reason = "已达到本月 100 次平台查询上限"
            elif (
                estimated_cost is not None
                and monthly_cost + pending_cost + estimated_cost
                > MONTHLY_HARD_LIMIT_COST_CNY
            ):
                blocked_reason = "已达到本地本月 ¥10 爬虫预算上限"
            if not blocked_reason:
                pending_calls += estimated_calls
                pending_cost += estimated_cost or 0.0
            previews.append(
                PlatformSearchPreview(
                    platform=platform,
                    cache_hit=cached is not None,
                    estimated_api_calls=estimated_calls,
                    estimated_cost_cny=estimated_cost,
                    platform_unit_price_cny=unit_price,
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
            platforms=list(self.active_platforms),
            force_refresh=force_refresh,
        )
        self.repository.save_search_batch(batch)
        batch = batch.model_copy(update={"status": SearchBatchStatus.RUNNING})
        self.repository.save_search_batch(batch)

        runs: list[PlatformSearchRun] = []
        for platform in self.active_platforms:
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
            run.status
            in {
                PlatformRunStatus.SUCCEEDED,
                PlatformRunStatus.PARTIAL,
                PlatformRunStatus.CACHED,
            }
            for run in runs
        )
        if all(
            run.status in {PlatformRunStatus.SUCCEEDED, PlatformRunStatus.CACHED}
            for run in runs
        ):
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

    def execute_due_recrawls(self, *, max_groups: int = 5) -> list[SearchBatch]:
        capability = self.provider.capabilities()
        now = self.clock()
        grouped: dict[tuple[str, Platform, str, int], list[SamplingCheckpoint]] = {}
        for checkpoint in self.repository.list_sampling_checkpoints():
            if checkpoint.status != SamplingStatus.PENDING:
                continue
            if checkpoint.due_at > now:
                continue
            if checkpoint.platform not in self.active_platforms:
                continue
            if checkpoint.provider_name != capability.provider_name:
                continue
            key = (
                checkpoint.keyword.casefold(),
                checkpoint.platform,
                checkpoint.provider_name,
                checkpoint.published_window_days,
            )
            grouped.setdefault(key, []).append(checkpoint)

        batches: list[SearchBatch] = []
        ordered_groups = sorted(
            grouped.items(),
            key=lambda item: min(checkpoint.due_at for checkpoint in item[1]),
        )
        for (keyword, _platform, _provider_name, window_days), _checkpoints in ordered_groups[
            : max(1, max_groups)
        ]:
            batches.append(
                self.execute(
                    keyword=keyword,
                    published_window_days=window_days,
                    count=10,
                    force_refresh=True,
                )
            )
        return batches

    def monthly_query_count(self, now: datetime | None = None) -> int:
        current = now or self.clock()
        month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self.repository.monthly_platform_query_count(month_start)

    def monthly_query_cost(self, now: datetime | None = None) -> float:
        current = now or self.clock()
        month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self.repository.monthly_platform_query_cost(month_start)

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
                raw_item_count=cached.raw_item_count,
                parsed_item_count=cached.parsed_item_count,
                out_of_window_count=cached.out_of_window_count,
                invalid_count=cached.invalid_count,
                duplicate_count=cached.duplicate_count,
                result_state=cached.result_state,
                payload_diagnostic=cached.payload_diagnostic,
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
                error="已达到本月 100 次平台查询上限，未发起请求。",
            )
        unit_price = self._endpoint_prices().get(platform)
        if (
            capability.mode == ProviderMode.PRODUCTION
            and unit_price is not None
            and self.monthly_query_cost(started_at) + unit_price
            > MONTHLY_HARD_LIMIT_COST_CNY
        ):
            return self._finish_run(
                run,
                status=PlatformRunStatus.BLOCKED,
                error="已达到本地本月 ¥10 爬虫预算上限，未发起请求。",
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
            normalized, validation_errors, validation_counts = self._normalize_page(
                page,
                platform=platform,
                keyword=keyword,
                provider=capability.provider_name,
                published_after=published_after,
                limit=count,
            )
            provider_errors = [*page.errors, *validation_errors]
            effective_raw_item_count = (
                page.raw_item_count if page.raw_item_count else len(page.items)
            )
            effective_parsed_item_count = (
                page.parsed_item_count if page.parsed_item_count else len(page.items)
            )
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
                duplicate_count=validation_counts["duplicate_count"],
                raw_item_count=effective_raw_item_count,
                parsed_item_count=effective_parsed_item_count,
                out_of_window_count=validation_counts["out_of_window_count"],
                invalid_count=validation_counts["invalid_count"],
                exhausted=not page.has_more,
                partial=bool(provider_errors) or len(normalized) < count,
                permission_status=capability.permission_status,
                publish_time=published_window_days,
                sort_type=KEYWORD_HOT_SORT_TYPE,
                api_call_count=page.api_call_count,
                started_at=started_at,
                finished_at=self.clock(),
                import_report=report,
                errors=import_errors,
                request_fingerprint=fingerprint,
                provider_request_id=page.request_id,
                billable_units=page.billable_units,
                payload_diagnostic=page.payload_diagnostic,
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
            trends = self.trend_service.recompute(
                keyword,
                platform=platform,
                provider_name=capability.provider_name,
            )
            run_status = (
                PlatformRunStatus.PARTIAL
                if provider_errors or len(normalized) < count
                else PlatformRunStatus.SUCCEEDED
            )
            result_state = self._result_state(
                raw_item_count=effective_raw_item_count,
                payload_diagnostic=page.payload_diagnostic,
                normalized=normalized,
                validation_counts=validation_counts,
                trends=trends,
            )
            finished = self._finish_run(
                run,
                status=run_status,
                returned_count=len(normalized),
                api_call_count=page.api_call_count,
                billable_units=page.billable_units,
                quota_remaining=page.quota_remaining,
                provider_request_id=page.request_id,
                errors=provider_errors,
                raw_item_count=effective_raw_item_count,
                parsed_item_count=effective_parsed_item_count,
                out_of_window_count=validation_counts["out_of_window_count"],
                invalid_count=validation_counts["invalid_count"],
                duplicate_count=validation_counts["duplicate_count"],
                result_state=result_state,
                payload_diagnostic=page.payload_diagnostic,
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
        try:
            return self.provider.search(**kwargs)
        except LicensedProviderError:
            raise
        except (ConnectionError, TimeoutError) as exc:
            raise LicensedProviderError(
                f"连接商业数据接口失败：{exc}",
                kind=ProviderErrorKind.CONNECTION,
                retryable=False,
                outcome_unknown=True,
            ) from exc
        except OSError as exc:
            raise LicensedProviderError(
                f"商业数据接口连接异常：{exc}",
                kind=ProviderErrorKind.CONNECTION,
                retryable=False,
                outcome_unknown=True,
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
    ) -> tuple[list[NormalizedCandidate], list[ProviderSearchError], dict[str, int]]:
        errors: list[ProviderSearchError] = []
        counts = {
            "out_of_window_count": 0,
            "invalid_count": 0,
            "duplicate_count": 0,
        }
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
                counts["invalid_count"] += 1
            elif item.platform_item_id in seen:
                reason = "供应商返回了重复作品ID。"
                counts["duplicate_count"] += 1
            elif item.published_at < published_after:
                reason = "作品发布时间超出本次查询范围。"
                counts["out_of_window_count"] += 1
            elif item.source_url is not None and not CommercialSearchService._url_matches_platform(
                str(item.source_url),
                platform,
            ):
                reason = "作品链接与平台不匹配。"
                counts["invalid_count"] += 1
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
                    data_quality_warnings=item.data_quality_warnings,
                )
            )
        return normalized, errors, counts

    @staticmethod
    def _result_state(
        *,
        raw_item_count: int,
        payload_diagnostic: str | None,
        normalized: list[NormalizedCandidate],
        validation_counts: dict[str, int],
        trends,
    ) -> str:
        """Describe why a run is empty without spending on another page."""
        if raw_item_count == 0:
            return (
                "provider_payload_invalid"
                if payload_diagnostic
                else "provider_empty"
            )
        if not normalized:
            if validation_counts["out_of_window_count"]:
                return "all_out_of_window"
            return "all_invalid"
        if trends and all(
            item.display_tier in {"ordinary", "observing"} for item in trends
        ):
            return "no_hot"
        return "has_results"

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
        all_checkpoints = self.repository.list_sampling_checkpoints(keyword_key)
        observed_ids: set[str] = set()
        for fallback_rank, item in enumerate(normalized, start=1):
            video_id = candidate_ids.get((platform, item.platform_item_id))
            if not video_id:
                continue
            observed_ids.add(video_id)
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
                    sort_type=KEYWORD_HOT_SORT_TYPE,
                    evidence=item.evidence,
                )
            )
            existing_checkpoints = [
                checkpoint
                for checkpoint in all_checkpoints
                if checkpoint.candidate_id == video_id
                and checkpoint.platform == platform
                and checkpoint.provider_name == provider
                and checkpoint.published_window_days == published_window_days
            ]
            for checkpoint in existing_checkpoints:
                if (
                    checkpoint.status == SamplingStatus.PENDING
                    and item.metrics.sampled_at >= checkpoint.due_at
                ):
                    updated = checkpoint.model_copy(
                        update={
                            "status": SamplingStatus.OBSERVED,
                            "observed_at": item.metrics.sampled_at,
                        }
                    )
                    self.repository.save_sampling_checkpoint(updated)
            has_plan = any(
                checkpoint.candidate_id == video_id
                and checkpoint.platform == platform
                and checkpoint.provider_name == provider
                and checkpoint.published_window_days == published_window_days
                for checkpoint in all_checkpoints
            )
            if has_plan:
                continue
            for offset in RECRAWL_OFFSETS_BY_WINDOW[published_window_days]:
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

        miss_cutoff = discovery.finished_at - timedelta(
            minutes=RECRAWL_MISS_GRACE_MINUTES
        )
        for checkpoint in all_checkpoints:
            if (
                checkpoint.status == SamplingStatus.PENDING
                and checkpoint.platform == platform
                and checkpoint.provider_name == provider
                and checkpoint.published_window_days == published_window_days
                and checkpoint.due_at < miss_cutoff
                and checkpoint.candidate_id not in observed_ids
            ):
                self.repository.save_sampling_checkpoint(
                    checkpoint.model_copy(update={"status": SamplingStatus.MISSED})
                )

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
        fingerprint = self._fingerprint(
            provider,
            platform,
            keyword,
            published_window_days,
            count,
        )
        cached = self.repository.find_cached_platform_search_run(
            provider=provider,
            platform=platform,
            keyword=keyword,
            published_window_days=published_window_days,
            requested_count=count,
            since=now - timedelta(minutes=CACHE_TTL_MINUTES),
        )
        if cached and cached.request_fingerprint == fingerprint:
            return cached
        return None

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
            f"{provider}|{platform.value}|{RANKING_MODE}|{keyword.casefold()}|"
            f"{published_window_days}|{count}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _endpoint_prices(self) -> dict[Platform, float]:
        raw_prices = getattr(self.provider, "endpoint_prices_cny", {})
        return {
            platform: float(price)
            for platform, price in raw_prices.items()
            if isinstance(platform, Platform) and price is not None
        }

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
