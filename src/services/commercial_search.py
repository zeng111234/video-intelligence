from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import unquote

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
SMART_FALLBACK_CACHE_TTL_MINUTES = 24 * 60
DUPLICATE_GUARD_SECONDS = 60
MONTHLY_WARNING_QUERIES = 80
MONTHLY_HARD_LIMIT_QUERIES = 100
# 兼容旧引用（新代码统一走 _monthly_cost_limit_cny，管理员可在定价设置中调整）
MONTHLY_HARD_LIMIT_COST_CNY = 10.0


def _monthly_cost_limit_cny() -> float:
    """供应商侧月度预算保护，不属于客户收费项目。"""
    return MONTHLY_HARD_LIMIT_COST_CNY


RANKING_MODE = "keyword_hot"
# 不限发布时间时使用综合排序，后续爆发判断完全由本地真实快照决定。
KEYWORD_HOT_SORT_TYPE = 0
# 规则版本同时是公共搜索缓存键的一部分。B 站放宽为“直接命中或单一行业/对象
# 维度相关”后，旧的严格结果不能继续作为本次搜索结果复用。
RELEVANCE_RULE_VERSION = "platform_search_final_eligible_v5"
# B 站结果分为高相关、相关待确认和明显无关。相关待确认项会进入候选列表，
# 但由 API/UI 标记为 reserve，不能被自动创作路径静默使用。
# 一页最多展示目标数量（上限 30），避免把数百条页面卡片全部灌入结果区。
BILIBILI_REFERENCE_FALLBACK_LIMIT = 30
_NON_CACHEABLE_PUBLIC_SEARCH_CODES = frozenset(
    {
        "public_search_multi_column_unavailable",
        "public_search_multi_column_unconfirmed",
        "public_search_time_filter_unavailable",
        "public_search_time_filter_unconfirmed",
    }
)
_BUSINESS_INTENT_SUFFIXES = (
    "获客",
    "引流",
    "招生",
    "招聘",
    "带货",
    "营销",
    "运营",
)
_BILIBILI_HIGH_RELEVANCE = "high"
_BILIBILI_REVIEW_RELEVANCE = "review"
_BILIBILI_IRRELEVANT_RELEVANCE = "irrelevant"

# 只用于拆分中文复合关键词，不会放宽其它平台的筛选。复合关键词完整命中
# 仍是高相关；只命中一个明确行业维度时进入“相关待确认”，避免 B 站
# 因平台标题措辞差异把可用素材全部挡掉。
_BILIBILI_INDUSTRY_GROUPS = {
    "美业": ("美业", "美容", "美发", "美妆", "美甲", "化妆品", "医美"),
    "餐饮": ("餐饮", "餐馆", "饭店", "茶饮", "咖啡"),
    "服装": ("服装", "女装", "男装", "童装", "鞋服"),
    "教育": ("教育", "教培", "培训", "课程", "招生"),
    "家居": ("家居", "家具", "装修", "家装"),
    "母婴": ("母婴", "孕婴", "育儿"),
    "健身": ("健身", "瑜伽", "运动"),
    "汽车": ("汽车", "汽修", "洗车", "二手车"),
}
_BILIBILI_OBJECT_GROUPS = {
    "工厂": ("工厂", "供应链", "源头", "oem", "生产", "加工", "代工"),
    "获客": ("获客", "引流", "营销", "运营", "招生", "带货"),
    "门店": ("门店", "店铺", "门店经营"),
    "IP": ("ip", "人设", "个人品牌"),
}
_BILIBILI_GENERIC_TERMS = ("智能", "老板", "案例", "短视频", "直播", "批发", "零售")
# 新批次采用自适应三点采样：首次 2 小时后复搜；第二个间隔按真实互动
# 变化缩短为 4 小时或延长为 12 小时。保留窗口映射仅供历史入口兼容。
ADAPTIVE_FIRST_RECRAWL_HOURS = 2
ADAPTIVE_FAST_RECRAWL_HOURS = 4
ADAPTIVE_SLOW_RECRAWL_HOURS = 12
RECRAWL_OFFSETS_BY_WINDOW = {
    0: (ADAPTIVE_FIRST_RECRAWL_HOURS,),
    1: (ADAPTIVE_FIRST_RECRAWL_HOURS,),
    3: (ADAPTIVE_FIRST_RECRAWL_HOURS,),
    7: (ADAPTIVE_FIRST_RECRAWL_HOURS,),
    30: (ADAPTIVE_FIRST_RECRAWL_HOURS,),
    180: (ADAPTIVE_FIRST_RECRAWL_HOURS,),
    300: (ADAPTIVE_FIRST_RECRAWL_HOURS,),
}
RECRAWL_MISS_GRACE_MINUTES = 30


def normalized_keyword_text(value: str) -> str:
    """Normalize a human-entered keyword/title for deterministic strict matching."""
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value).casefold()
        if not character.isspace()
        and not unicodedata.category(character).startswith(("P", "Z"))
    )


def _bilibili_match_text_from_evidence(evidence: str | None) -> str:
    """Recover visible title/topic/description text attached by the B 站 adapter."""
    if not evidence:
        return ""
    for part in evidence.split(";"):
        if part.startswith("bilibili_match_text="):
            return unquote(part.split("=", 1)[1])
    return ""


def _bilibili_matching_groups(
    text: str, groups: dict[str, tuple[str, ...]]
) -> set[str]:
    normalized = normalized_keyword_text(text)
    return {
        name
        for name, aliases in groups.items()
        if any(normalized_keyword_text(alias) in normalized for alias in aliases)
    }


def _bilibili_query_terms(keyword: str) -> tuple[set[str], set[str], set[str]]:
    normalized = normalized_keyword_text(keyword)
    industries = _bilibili_matching_groups(normalized, _BILIBILI_INDUSTRY_GROUPS)
    objects = _bilibili_matching_groups(normalized, _BILIBILI_OBJECT_GROUPS)
    generic = {
        term
        for term in _BILIBILI_GENERIC_TERMS
        if normalized_keyword_text(term) in normalized
    }
    return industries, objects, generic


def bilibili_relevance_tier(
    *, title: str, keyword: str, evidence: str | None = None
) -> str:
    """Classify one B 站 card as high, reviewable, or unrelated."""
    normalized_keyword = normalized_keyword_text(keyword)
    if not normalized_keyword:
        return _BILIBILI_IRRELEVANT_RELEVANCE
    match_text = " ".join(
        value for value in (title, _bilibili_match_text_from_evidence(evidence)) if value
    )
    normalized_text = normalized_keyword_text(match_text)
    if normalized_keyword in normalized_text:
        return _BILIBILI_HIGH_RELEVANCE
    if evidence and "direct_match=1" in evidence:
        return _BILIBILI_HIGH_RELEVANCE

    query_industries, query_objects, query_generic = _bilibili_query_terms(keyword)
    matched_industries = _bilibili_matching_groups(
        normalized_text,
        {group: _BILIBILI_INDUSTRY_GROUPS[group] for group in query_industries},
    )
    matched_objects = _bilibili_matching_groups(
        normalized_text,
        {group: _BILIBILI_OBJECT_GROUPS[group] for group in query_objects},
    )
    matched_generic = {
        term
        for term in query_generic
        if normalized_keyword_text(term) in normalized_text
    }

    if query_industries:
        if matched_industries and matched_objects:
            return _BILIBILI_HIGH_RELEVANCE
        # B 站允许行业单维度相关，但保持待确认标记；这不是把所有
        # 搜索卡片放进来，完全没有行业维度命中时仍然排除。
        if matched_industries:
            return _BILIBILI_REVIEW_RELEVANCE
        return _BILIBILI_IRRELEVANT_RELEVANCE

    # 无已知行业维度的关键词保留旧的精确匹配习惯，同时允许拆开的多概念命中。
    if query_objects and matched_objects and (not query_generic or matched_generic):
        return _BILIBILI_HIGH_RELEVANCE
    if matched_objects or matched_generic:
        return _BILIBILI_REVIEW_RELEVANCE
    return _BILIBILI_IRRELEVANT_RELEVANCE


def title_matches_keyword(
    *, title: str, keyword: str, require_intent: bool = True
) -> bool:
    """Match only the provider title/description text (including inline hashtags).

    ``require_intent=True``(默认)要求标题同时含关键词词根与业务意图词
    (获客/引流/营销/运营等);``False`` 时只要求词根命中,相关性由平台
    搜索排序兜底,适合抖音这类每页返回量受限的渠道。
    """
    normalized_keyword = normalized_keyword_text(keyword)
    normalized_title = normalized_keyword_text(title)
    if not normalized_keyword:
        return False
    if normalized_keyword in normalized_title:
        return True
    for suffix in _BUSINESS_INTENT_SUFFIXES:
        if not normalized_keyword.endswith(suffix):
            continue
        subject = normalized_keyword[: -len(suffix)]
        return (
            len(subject) >= 2
            and subject in normalized_title
            and (
                not require_intent
                or any(
                    intent in normalized_title for intent in _BUSINESS_INTENT_SUFFIXES
                )
            )
        )
    return False


def item_matches_keyword(
    *, title: str, keyword: str, evidence: str | None = None
) -> bool:
    """Accept an exact Hotspot topic match when a video title omits the topic.

    Topic-detail cards belong to an exact topic selected from the visible topic
    board.  Their titles can be intentionally short, so title-only filtering
    would discard valid candidates such as a video inside “餐饮获客”.
    """
    if (evidence or "").startswith("bilibili:"):
        return (
            bilibili_relevance_tier(title=title, keyword=keyword, evidence=evidence)
            == _BILIBILI_HIGH_RELEVANCE
        )
    return (
        title_matches_keyword(title=title, keyword=keyword)
        or "严格话题=1" in (evidence or "")
        or "关键词联合命中=1" in (evidence or "")
        or (
            (evidence or "").startswith("bilibili:")
            and "direct_match=1" in (evidence or "")
        )
    )


def keyword_match_reason(keyword: str) -> str:
    return f"标题/话题包含“{keyword.strip()}”"


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
        # The legacy commercial default remains the three established
        # platforms.  A dedicated adapter can opt into an additional platform
        # without silently adding it to every legacy/sandbox batch.
        adapter_platforms = set(provider.capabilities().supported_platforms)
        unsupported = (
            set(self.active_platforms) - set(SUPPORTED_PLATFORMS) - adapter_platforms
        )
        if unsupported:
            names = "、".join(sorted(item.value for item in unsupported))
            raise ValueError(f"不支持的关键词搜索平台：{names}。")
        self.clock = clock or (lambda: datetime.now().astimezone())

    def preview(
        self,
        *,
        keyword: str,
        published_window_days: int = 0,
        hotspot_window_hours: int | None = None,
        count: int = 10,
        force_refresh: bool = False,
        platforms: tuple[Platform, ...] | None = None,
        cache_ttl_minutes: int = CACHE_TTL_MINUTES,
        include_monitoring: bool = True,
        kuaishou_sort: str = "platform",
        kuaishou_duration_bucket: str = "all",
    ) -> list[PlatformSearchPreview]:
        keyword = self._validate_request(
            keyword, published_window_days, count, hotspot_window_hours
        )
        kuaishou_sort, kuaishou_duration_bucket = self._validate_kuaishou_filters(
            kuaishou_sort,
            kuaishou_duration_bucket,
        )
        selected_platforms = self._selected_platforms(platforms)
        safe_cache_ttl_minutes = max(1, int(cache_ttl_minutes))
        capability = self.provider.capabilities()
        now = self.clock()
        monthly_queries = self.monthly_query_count(now)
        monthly_cost = self.monthly_query_cost(now)
        prices = self._endpoint_prices()
        previews: list[PlatformSearchPreview] = []
        pending_calls = 0
        pending_cost = 0.0
        for platform in selected_platforms:
            cached = (
                None
                if force_refresh
                else self._cached_run(
                    provider=capability.provider_name,
                    platform=platform,
                    keyword=keyword,
                    published_window_days=published_window_days,
                    hotspot_window_hours=hotspot_window_hours,
                    count=count,
                    now=now,
                    cache_ttl_minutes=safe_cache_ttl_minutes,
                    kuaishou_sort=kuaishou_sort,
                    kuaishou_duration_bucket=kuaishou_duration_bucket,
                )
            )
            # 首次 + 两次自适应复搜；用户未开启趋势跟踪时只预估首次调用。
            estimated_calls = (
                0
                if cached
                or capability.mode in {ProviderMode.SANDBOX, ProviderMode.LOCAL_BROWSER}
                else (3 if include_monitoring else 1)
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
                > _monthly_cost_limit_cny()
            ):
                blocked_reason = "已达到本月素材检索的系统保护上限"
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
        published_window_days: int = 0,
        hotspot_window_hours: int | None = None,
        count: int = 10,
        force_refresh: bool = False,
        platforms: tuple[Platform, ...] | None = None,
        cache_ttl_minutes: int = CACHE_TTL_MINUTES,
        schedule_recrawls: bool = True,
        tracking_parent_batch_id: str | None = None,
        kuaishou_sort: str = "platform",
        kuaishou_duration_bucket: str = "all",
        progress_callback: Callable[[dict[str, Any], SearchBatch], None] | None = None,
    ) -> SearchBatch:
        keyword = self._validate_request(
            keyword, published_window_days, count, hotspot_window_hours
        )
        kuaishou_sort, kuaishou_duration_bucket = self._validate_kuaishou_filters(
            kuaishou_sort,
            kuaishou_duration_bucket,
        )
        selected_platforms = self._selected_platforms(platforms)
        safe_cache_ttl_minutes = max(1, int(cache_ttl_minutes))
        capability = self.provider.capabilities()
        if not capability.enabled:
            raise ValueError("商业数据接口尚未配置或验收，未发起任何真实请求。")
        batch = SearchBatch(
            keyword=keyword,
            published_window_days=published_window_days,
            hotspot_window_hours=hotspot_window_hours,
            requested_count_per_platform=count,
            kuaishou_sort=kuaishou_sort,
            kuaishou_duration_bucket=kuaishou_duration_bucket,
            provider=capability.provider_name,
            mode=capability.mode,
            platforms=list(selected_platforms),
            force_refresh=force_refresh,
            tracking_authorized=bool(tracking_parent_batch_id),
            tracking_parent_batch_id=tracking_parent_batch_id,
            monitoring_policy=(
                "adaptive_three_sample_v1"
                if schedule_recrawls
                else "manual_tracking_v1"
            ),
            sampling_offsets_hours=(
                [0, ADAPTIVE_FIRST_RECRAWL_HOURS] if schedule_recrawls else [0]
            ),
        )
        self.repository.save_search_batch(batch)
        batch = batch.model_copy(update={"status": SearchBatchStatus.RUNNING})
        self.repository.save_search_batch(batch)

        runs: list[PlatformSearchRun] = []
        for platform in selected_platforms:
            runs.append(
                self._execute_platform(
                    batch=batch,
                    platform=platform,
                    keyword=keyword,
                    published_window_days=published_window_days,
                    hotspot_window_hours=hotspot_window_hours,
                    count=count,
                    force_refresh=force_refresh,
                    cache_ttl_minutes=safe_cache_ttl_minutes,
                    schedule_recrawls=schedule_recrawls,
                    tracking_parent_batch_id=tracking_parent_batch_id,
                    kuaishou_sort=kuaishou_sort,
                    kuaishou_duration_bucket=kuaishou_duration_bucket,
                    progress_callback=progress_callback,
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

    def start_batch_tracking(
        self, batch_id: str
    ) -> tuple[SearchBatch, int, datetime | None]:
        """Authorize exactly the next two real snapshots for a completed batch.

        The first search never creates a billable follow-up on its own.  This
        method is the only place that marks checkpoints as user-authorized.
        """
        batch = self.repository.get_search_batch(batch_id)
        if batch is None:
            raise ValueError("搜索批次不存在。")
        if batch.status not in {SearchBatchStatus.SUCCEEDED, SearchBatchStatus.PARTIAL}:
            raise ValueError("仅成功或部分成功的批次可以开启走势追踪。")
        if batch.provider == "douyin_local_browser":
            raise ValueError(
                "热点宝近7天五榜为单次新增播放量排序，不支持复爬走势追踪。"
            )
        now = self.clock()
        candidates = []
        for run_id in batch.platform_run_ids:
            for match in self.repository.list_candidate_matches(run_id):
                candidate = self.repository.get_candidate(match.video_id)
                if candidate is None:
                    continue
                metrics = candidate.metrics
                heat = (
                    float(metrics.likes or 0)
                    + float(metrics.comments or 0) * 3
                    + float(metrics.shares or 0) * 4
                    + float(metrics.favorites or 0) * 4
                )
                if heat >= 100:
                    candidates.append(candidate)
        unique_candidates = {item.video_id: item for item in candidates}.values()
        if not unique_candidates:
            raise ValueError("本批次没有达到互动热度门槛的候选，无法开启走势追踪。")
        existing = self.repository.list_sampling_checkpoints(batch.keyword.casefold())
        due_at = now + timedelta(hours=ADAPTIVE_FIRST_RECRAWL_HOURS)
        created = 0
        for candidate in unique_candidates:
            if any(
                checkpoint.tracking_batch_id == batch.batch_id
                and checkpoint.candidate_id == candidate.video_id
                and checkpoint.status == SamplingStatus.PENDING
                for checkpoint in existing
            ):
                continue
            checkpoint = SamplingCheckpoint(
                checkpoint_id=f"sample-{hashlib.sha256(f'{batch.batch_id}|{candidate.video_id}|{due_at.isoformat()}'.encode()).hexdigest()[:12]}",
                keyword=batch.keyword.casefold(),
                candidate_id=candidate.video_id,
                request_id=batch.platform_run_ids[0]
                if batch.platform_run_ids
                else batch.batch_id,
                platform=candidate.platform,
                provider_name=batch.provider,
                published_window_days=batch.published_window_days,
                offset_hours=ADAPTIVE_FIRST_RECRAWL_HOURS,
                due_at=due_at,
                tracking_batch_id=batch.batch_id,
                billing_authorized=True,
            )
            self.repository.save_sampling_checkpoint(checkpoint)
            created += 1
        batch = batch.model_copy(
            update={
                "tracking_authorized": True,
                "monitoring_policy": "adaptive_three_sample_v1",
                "sampling_offsets_hours": [0, ADAPTIVE_FIRST_RECRAWL_HOURS],
            }
        )
        self.repository.save_search_batch(batch)
        return batch, created, due_at if created else None

    def cancel_batch_tracking(self, batch_id: str) -> int:
        cancelled = 0
        for checkpoint in self.repository.list_sampling_checkpoints():
            if (
                checkpoint.tracking_batch_id == batch_id
                and checkpoint.status == SamplingStatus.PENDING
            ):
                self.repository.save_sampling_checkpoint(
                    checkpoint.model_copy(update={"status": SamplingStatus.CANCELLED})
                )
                cancelled += 1
        return cancelled

    def execute_due_recrawls(
        self,
        *,
        max_groups: int = 5,
        published_window_days: int | None = None,
        authorized_only: bool = False,
    ) -> list[SearchBatch]:
        capability = self.provider.capabilities()
        now = self.clock()
        grouped: dict[
            tuple[str, Platform, str, int, str | None], list[SamplingCheckpoint]
        ] = {}
        for checkpoint in self.repository.list_sampling_checkpoints():
            if checkpoint.status != SamplingStatus.PENDING:
                continue
            if authorized_only and not checkpoint.billing_authorized:
                continue
            if checkpoint.due_at > now:
                continue
            if checkpoint.platform not in self.active_platforms:
                continue
            if checkpoint.provider_name != capability.provider_name:
                continue
            if (
                published_window_days is not None
                and checkpoint.published_window_days != published_window_days
            ):
                continue
            key = (
                checkpoint.keyword.casefold(),
                checkpoint.platform,
                checkpoint.provider_name,
                checkpoint.published_window_days,
                checkpoint.tracking_batch_id,
            )
            grouped.setdefault(key, []).append(checkpoint)

        batches: list[SearchBatch] = []
        ordered_groups = sorted(
            grouped.items(),
            key=lambda item: min(checkpoint.due_at for checkpoint in item[1]),
        )
        for (
            keyword,
            _platform,
            _provider_name,
            window_days,
            tracking_batch_id,
        ), _checkpoints in ordered_groups[: max(1, max_groups)]:
            batches.append(
                self.execute(
                    keyword=keyword,
                    published_window_days=window_days,
                    count=10,
                    force_refresh=True,
                    tracking_parent_batch_id=tracking_batch_id,
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
        hotspot_window_hours: int | None,
        count: int,
        force_refresh: bool,
        cache_ttl_minutes: int,
        schedule_recrawls: bool,
        tracking_parent_batch_id: str | None,
        kuaishou_sort: str,
        kuaishou_duration_bucket: str,
        progress_callback: Callable[[dict[str, Any], SearchBatch], None] | None = None,
    ) -> PlatformSearchRun:
        capability = self.provider.capabilities()
        started_at = self.clock()
        fingerprint = self._fingerprint(
            capability.provider_name,
            platform,
            keyword,
            published_window_days,
            hotspot_window_hours,
            count,
            kuaishou_sort,
            kuaishou_duration_bucket,
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
                hotspot_window_hours=hotspot_window_hours,
                count=count,
                kuaishou_sort=kuaishou_sort,
                kuaishou_duration_bucket=kuaishou_duration_bucket,
                now=started_at,
                cache_ttl_minutes=cache_ttl_minutes,
            )
        )
        if cached:
            return self._finish_run(
                run,
                status=PlatformRunStatus.CACHED,
                returned_count=cached.returned_count,
                raw_item_count=cached.raw_item_count,
                parsed_item_count=cached.parsed_item_count,
                raw_discovered_count=cached.raw_discovered_count,
                deduped_item_count=cached.deduped_item_count,
                direct_match_count=cached.direct_match_count,
                out_of_window_count=cached.out_of_window_count,
                invalid_count=cached.invalid_count,
                duplicate_count=cached.duplicate_count,
                irrelevant_count=cached.irrelevant_count,
                duration_filtered_count=cached.duration_filtered_count,
                incremental_play_filtered_count=cached.incremental_play_filtered_count,
                low_incremental_items=cached.low_incremental_items,
                reference_items=cached.reference_items,
                relevance_rule_version=cached.relevance_rule_version,
                result_state=cached.result_state,
                payload_diagnostic=cached.payload_diagnostic,
                crawl_stop_reason=cached.crawl_stop_reason,
                crawl_stop_message=cached.crawl_stop_message,
                stage_timings_ms=cached.stage_timings_ms,
                adapter_rule_version=cached.adapter_rule_version,
                browser_reused=cached.browser_reused,
                session_recovered=cached.session_recovered,
                cached_from_run_id=cached.run_id,
                cache_hit=True,
            )

        # 原子占位：60 秒防重复 + 月上限检查 + 写入占位一次完成，
        # 并发请求下也只有一个能通过（防止重复扣费、超上限）。
        unit_price = self._endpoint_prices().get(platform)
        claim_result = self.repository.try_claim_platform_search_request(
            fingerprint=fingerprint,
            run_id=run.run_id,
            claimed_at=started_at,
            ttl_seconds=DUPLICATE_GUARD_SECONDS,
            unit_price=float(unit_price or 0.0),
            enforce_limits=capability.mode == ProviderMode.PRODUCTION,
            monthly_queries_limit=MONTHLY_HARD_LIMIT_QUERIES,
            monthly_cost_limit_cny=_monthly_cost_limit_cny(),
        )
        if claim_result == "duplicate" and capability.mode != ProviderMode.PRODUCTION:
            # 本机浏览器搜索不计费，且调用方已通过平台浏览器租约避免同一
            # 平台并发。上次请求已经结束时，60 秒通用防重锁不应把用户挡在
            # 真正打开搜索页之前；清锁后允许本机重新验证登录/页面状态。
            self.repository.resolve_platform_search_request(fingerprint)
            claim_result = self.repository.try_claim_platform_search_request(
                fingerprint=fingerprint,
                run_id=run.run_id,
                claimed_at=started_at,
                ttl_seconds=DUPLICATE_GUARD_SECONDS,
                unit_price=float(unit_price or 0.0),
                enforce_limits=False,
                monthly_queries_limit=MONTHLY_HARD_LIMIT_QUERIES,
                monthly_cost_limit_cny=_monthly_cost_limit_cny(),
            )
        if claim_result == "duplicate":
            return self._finish_run(
                run,
                status=PlatformRunStatus.BLOCKED,
                error="相同请求刚刚执行过，请等待 60 秒，防止重复计费。",
            )
        if claim_result == "count_limit":
            return self._finish_run(
                run,
                status=PlatformRunStatus.BLOCKED,
                error="已达到本月 100 次平台查询上限，未发起请求。",
            )
        if claim_result == "cost_limit":
            return self._finish_run(
                run,
                status=PlatformRunStatus.BLOCKED,
                error="已达到本月素材检索的系统保护上限，未发起请求。",
            )
        if claim_result == "unresolved":
            if capability.mode == ProviderMode.PRODUCTION:
                return self._finish_run(
                    run,
                    status=PlatformRunStatus.BLOCKED,
                    error="上次请求费用状态待核对，请联系管理员确认后再试。",
                )
            # 免费的本机浏览器不会产生供应商费用。上次程序异常不应留下
            # 永久付费锁，否则修复代码后同一关键词也无法重新验证。
            self.repository.resolve_platform_search_request(fingerprint)
            claim_result = self.repository.try_claim_platform_search_request(
                fingerprint=fingerprint,
                run_id=run.run_id,
                claimed_at=started_at,
                ttl_seconds=DUPLICATE_GUARD_SECONDS,
                unit_price=float(unit_price or 0.0),
                enforce_limits=capability.mode == ProviderMode.PRODUCTION,
                monthly_queries_limit=MONTHLY_HARD_LIMIT_QUERIES,
                monthly_cost_limit_cny=_monthly_cost_limit_cny(),
            )
            if claim_result != "ok":
                return self._finish_run(
                    run,
                    status=PlatformRunStatus.BLOCKED,
                    error="相同请求刚刚执行过，请等待 60 秒，防止重复计费。",
                )

        # 找素材不消耗客户积分；供应商侧仍受前面的防重复、缓存及月预算限制。
        run = run.model_copy(update={"status": PlatformRunStatus.RUNNING})
        self.repository.save_platform_search_run(run)

        def emit_progress(event: dict[str, Any]) -> None:
            """Persist the live funnel before forwarding it to the queue."""
            updates = {
                "returned_count": max(
                    run.returned_count,
                    int(event.get("retained_count") or event.get("direct_match_count") or 0),
                ),
                "raw_item_count": max(
                    run.raw_item_count,
                    int(event.get("scanned_count") or 0),
                ),
                "parsed_item_count": max(
                    run.parsed_item_count,
                    int(event.get("parsed_count") or 0),
                ),
                "raw_discovered_count": max(
                    run.raw_discovered_count,
                    int(event.get("scanned_count") or 0),
                ),
                "deduped_item_count": max(
                    run.deduped_item_count,
                    int(event.get("deduped_count") or 0),
                ),
                "direct_match_count": max(
                    run.direct_match_count,
                    int(event.get("direct_match_count") or 0),
                ),
                "out_of_window_count": max(
                    run.out_of_window_count,
                    int(event.get("out_of_window_count") or 0),
                ),
                "invalid_count": max(
                    run.invalid_count,
                    int(event.get("invalid_count") or 0),
                ),
                "duplicate_count": max(
                    run.duplicate_count,
                    int(event.get("duplicate_count") or 0),
                ),
                "irrelevant_count": max(
                    run.irrelevant_count,
                    int(event.get("irrelevant_count") or 0),
                ),
            }
            self.repository.save_platform_search_run(run.model_copy(update=updates))
            if progress_callback is not None:
                try:
                    progress_callback(event, batch)
                except Exception:
                    # A UI/queue observer must never turn a successful crawl into
                    # a provider failure.
                    pass

        emit_progress(
            {
                "stage": "scanning",
                "platform": platform.value,
                "message": "已打开搜索页面，正在扫描结果。",
            }
        )
        published_after = (
            None
            if published_window_days == 0
            else started_at - timedelta(days=published_window_days)
        )
        try:
            search_kwargs = {
                "platform": platform,
                "keyword": keyword,
                "published_after": published_after,
                "limit": count,
                "idempotency_key": idempotency_key,
            }
            if hotspot_window_hours is not None:
                search_kwargs["hotspot_window_hours"] = hotspot_window_hours
            if platform == Platform.KUAISHOU and (
                kuaishou_sort != "platform" or kuaishou_duration_bucket != "all"
            ):
                search_kwargs["search_filters"] = {
                    "kuaishou_sort": kuaishou_sort,
                    "kuaishou_duration_bucket": kuaishou_duration_bucket,
                }
            if progress_callback is not None and (
                capability.provider_name == "douyin_public_browser_v2"
                or capability.provider_name.endswith("_local_browser")
            ):
                search_kwargs["progress_callback"] = emit_progress
            page = self._search_with_retry(
                **search_kwargs,
            )
            normalized, validation_errors, validation_counts = self._normalize_page(
                page,
                platform=platform,
                keyword=keyword,
                provider=capability.provider_name,
                source_type=getattr(
                    self.provider,
                    "source_type",
                    DataSource.LICENSED_PROVIDER,
                ),
                published_after=published_after,
                limit=count,
            )
            reference_items = validation_counts.pop("reference_items", [])
            if platform != Platform.BILIBILI:
                reference_items = []
            all_import_items = [*normalized, *reference_items]
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
            source_page = SourcePage(items=all_import_items, errors=import_errors)
            report = (
                self.source_service.import_page(source_page) if all_import_items else None
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
                raw_discovered_count=(
                    page.raw_discovered_count or effective_raw_item_count
                ),
                deduped_item_count=(
                    page.deduped_item_count or len(page.items)
                ),
                direct_match_count=(
                    sum(
                        bilibili_relevance_tier(
                            title=item.title,
                            keyword=keyword,
                            evidence=item.evidence,
                        )
                        == _BILIBILI_HIGH_RELEVANCE
                        for item in page.items
                    )
                    if platform == Platform.BILIBILI
                    else page.direct_match_count
                    or sum(
                        item_matches_keyword(
                            title=item.title,
                            keyword=keyword,
                            evidence=item.evidence,
                        )
                        for item in page.items
                    )
                ),
                out_of_window_count=validation_counts["out_of_window_count"],
                invalid_count=validation_counts["invalid_count"],
                irrelevant_count=validation_counts["irrelevant_count"],
                duration_filtered_count=validation_counts["duration_filtered_count"],
                incremental_play_filtered_count=validation_counts[
                    "incremental_play_filtered_count"
                ],
                low_incremental_items=page.low_incremental_items,
                relevance_rule_version=RELEVANCE_RULE_VERSION,
                exhausted=not page.has_more,
                # 热点宝五榜是“筛选后的榜单”，结果少于上限并不等同于供应商缺页。
                partial=bool(provider_errors)
                or (
                    capability.provider_name != "douyin_local_browser"
                    and len(normalized) < count
                ),
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
                crawl_stop_reason=page.crawl_stop_reason,
                crawl_stop_message=page.crawl_stop_message,
                stage_timings_ms=page.stage_timings_ms,
                adapter_rule_version=page.adapter_rule_version,
                browser_reused=page.browser_reused,
                session_recovered=page.session_recovered,
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
                    for item in page.items
                    if item.platform == platform
                },
                schedule_recrawls=schedule_recrawls,
                tracking_parent_batch_id=tracking_parent_batch_id,
            )
            self._save_reference_matches(
                discovery=discovery,
                normalized=reference_items,
                platform=platform,
                provider=capability.provider_name,
                keyword=keyword,
                published_window_days=published_window_days,
                rank_by_item={
                    item.platform_item_id: item.provider_rank
                    for item in page.items
                    if item.platform == platform
                },
            )
            trends = (
                []
                if capability.provider_name == "douyin_local_browser"
                and not schedule_recrawls
                else self.trend_service.recompute(
                    keyword,
                    platform=platform,
                    provider_name=capability.provider_name,
                )
            )
            run_status = (
                PlatformRunStatus.PARTIAL
                if provider_errors
                or (
                    capability.provider_name != "douyin_local_browser"
                    and len(normalized) < count
                )
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
                raw_discovered_count=(
                    page.raw_discovered_count or effective_raw_item_count
                ),
                deduped_item_count=(page.deduped_item_count or len(page.items)),
                direct_match_count=(
                    sum(
                        bilibili_relevance_tier(
                            title=item.title,
                            keyword=keyword,
                            evidence=item.evidence,
                        )
                        == _BILIBILI_HIGH_RELEVANCE
                        for item in page.items
                    )
                    if platform == Platform.BILIBILI
                    else page.direct_match_count
                    or sum(
                        item_matches_keyword(
                            title=item.title,
                            keyword=keyword,
                            evidence=item.evidence,
                        )
                        for item in page.items
                    )
                ),
                out_of_window_count=validation_counts["out_of_window_count"],
                invalid_count=validation_counts["invalid_count"],
                duplicate_count=validation_counts["duplicate_count"],
                irrelevant_count=validation_counts["irrelevant_count"],
                duration_filtered_count=validation_counts["duration_filtered_count"],
                incremental_play_filtered_count=validation_counts[
                    "incremental_play_filtered_count"
                ],
                relevance_rule_version=RELEVANCE_RULE_VERSION,
                result_state=result_state,
                crawl_stop_reason=page.crawl_stop_reason,
                crawl_stop_message=page.crawl_stop_message,
                payload_diagnostic=page.payload_diagnostic,
                stage_timings_ms=page.stage_timings_ms,
                adapter_rule_version=page.adapter_rule_version,
                browser_reused=page.browser_reused,
                session_recovered=page.session_recovered,
                reference_items=reference_items,
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
        # 连接问题最多自动重试一次；登录、验证码、频繁访问和校验错误不重试。
        for attempt in range(2):
            try:
                return self.provider.search(**kwargs)
            except LicensedProviderError as exc:
                if (
                    exc.kind == ProviderErrorKind.CONNECTION
                    and exc.retryable
                    and attempt == 0
                ):
                    continue
                raise
            except (ConnectionError, TimeoutError) as exc:
                if attempt == 0:
                    continue
                raise LicensedProviderError(
                    f"连接商业数据接口失败：{exc}",
                    kind=ProviderErrorKind.CONNECTION,
                    retryable=False,
                    outcome_unknown=True,
                ) from exc
            except OSError as exc:
                if attempt == 0:
                    continue
                raise LicensedProviderError(
                    f"商业数据接口连接异常：{exc}",
                    kind=ProviderErrorKind.CONNECTION,
                    retryable=False,
                    outcome_unknown=True,
                ) from exc
        raise AssertionError("unreachable connection retry state")

    @staticmethod
    def _normalize_page(
        page: ProviderSearchPage,
        *,
        platform: Platform,
        keyword: str,
        provider: str,
        source_type: DataSource,
        published_after: datetime | None,
        limit: int,
    ) -> tuple[list[NormalizedCandidate], list[ProviderSearchError], dict[str, object]]:
        errors: list[ProviderSearchError] = []
        counts = {
            "out_of_window_count": page.out_of_window_count,
            "invalid_count": page.invalid_count,
            "duplicate_count": page.duplicate_count,
            "irrelevant_count": page.relevance_filtered_count,
            "duration_filtered_count": page.duration_filtered_count,
            "incremental_play_filtered_count": page.incremental_play_filtered_count,
        }
        if page.platform != platform or page.provider != provider:
            raise LicensedProviderError(
                "供应商响应的平台或供应商标识与请求不一致。",
                kind=ProviderErrorKind.VALIDATION,
            )
        normalized: list[NormalizedCandidate] = []
        reference_items: list[NormalizedCandidate] = []
        bilibili_review_fallback_items: list[ProviderSearchItem] = []
        seen: set[str] = set()

        def build_candidate(
            item: ProviderSearchItem,
            *,
            is_bilibili_related: bool,
            pending_review: bool = False,
            extra_warning: str | None = None,
        ) -> NormalizedCandidate:
            warnings = list(item.data_quality_warnings)
            if is_bilibili_related:
                warnings.append(
                    f"B站按行业/对象相关性放宽匹配“{keyword}”；请人工确认后再使用。"
                )
            if extra_warning:
                warnings.append(extra_warning)
            return NormalizedCandidate(
                platform_item_id=item.platform_item_id,
                title=item.title,
                author_id=item.author_id,
                author_name=item.author_name,
                platform=platform,
                category=(
                    f"关键词相关/{keyword}"
                    if is_bilibili_related
                    else f"关键词/{keyword}"
                ),
                published_at=item.published_at,
                duration_seconds=item.duration_seconds,
                source_url=item.source_url,
                source_type=source_type,
                metrics=item.metrics,
                matched_by=[keyword],
                cohort_key=f"{provider}:{platform.value}:keyword:{keyword.casefold()}",
                eligibility_status=(
                    EligibilityStatus.PENDING_REVIEW
                    if is_bilibili_related or pending_review
                    else EligibilityStatus.AUTO_MATCHED
                ),
                evidence=item.evidence,
                official_hot=(
                    provider == "douyin_local_browser"
                    and (item.evidence or "").startswith("hotspot:")
                ),
                official_rank=(
                    item.provider_rank
                    if provider == "douyin_local_browser"
                    and (item.evidence or "").startswith("hotspot:")
                    else None
                ),
                official_hot_value=(
                    float(item.metrics.plays)
                    if provider == "douyin_local_browser"
                    and (item.evidence or "").startswith("hotspot:")
                    and item.metrics.plays is not None
                    else None
                ),
                data_quality_warnings=warnings,
            )

        for index, item in enumerate(page.items):
            reason = None
            if item.platform != platform:
                reason = "作品平台与当前子任务不一致。"
                counts["invalid_count"] += 1
            elif item.platform_item_id in seen:
                reason = "供应商返回了重复作品ID。"
                counts["duplicate_count"] += 1
            elif published_after is not None and (
                "time=search_order_fallback" in (item.evidence or "")
                or any(
                    "发布时间" in warning
                    and ("未取得有效" in warning or "未返回可靠" in warning)
                    for warning in item.data_quality_warnings
                )
            ):
                reason = "作品没有可靠发布时间，不能确认属于本次时间范围。"
                counts["out_of_window_count"] += 1
            elif published_after is not None and item.published_at < published_after:
                reason = "作品发布时间超出本次查询范围。"
                counts["out_of_window_count"] += 1
            elif (
                item.source_url is not None
                and not CommercialSearchService._url_matches_platform(
                    str(item.source_url),
                    platform,
                )
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
            if platform == Platform.BILIBILI:
                relevance_tier = bilibili_relevance_tier(
                    title=item.title,
                    keyword=keyword,
                    evidence=item.evidence,
                )
                direct_keyword_match = relevance_tier == _BILIBILI_HIGH_RELEVANCE
            else:
                relevance_tier = _BILIBILI_HIGH_RELEVANCE
                direct_keyword_match = item_matches_keyword(
                    title=item.title,
                    keyword=keyword,
                    evidence=item.evidence,
                )
            seen.add(item.platform_item_id)
            if "关键词待确认=1" in (item.evidence or ""):
                reference_items.append(
                    build_candidate(
                        item,
                        is_bilibili_related=False,
                        pending_review=True,
                        extra_warning=(
                            f"标题未直接命中“{keyword}”；已保留为待确认素材，"
                            "避免本次搜索直接变成 0 条。"
                        ),
                    )
                )
                continue
            # B 站整页 DOM 容易混入推荐位或弹幕等非搜索卡片。完全无查询
            # 维度命中仍丢弃；单一行业/对象命中进入候选列表，但保留待确认
            # 状态，供人工挑选而不是自动送入智能创作。
            if (
                platform == Platform.BILIBILI
                and relevance_tier == _BILIBILI_IRRELEVANT_RELEVANCE
            ):
                counts["irrelevant_count"] += 1
                if provider.endswith("_local_browser"):
                    bilibili_review_fallback_items.append(item)
                continue
            is_bilibili_related = (
                platform == Platform.BILIBILI
                and relevance_tier == _BILIBILI_REVIEW_RELEVANCE
            )
            normalized.append(
                build_candidate(
                    item,
                    is_bilibili_related=is_bilibili_related,
                    extra_warning=(
                        None
                        if is_bilibili_related or direct_keyword_match
                        else f"标题未直接命中“{keyword}”；这是平台搜索返回的候选，请人工判断相关性。"
                    ),
                )
            )
            if len(normalized) >= limit:
                break
        if (
            platform == Platform.BILIBILI
            and provider.endswith("_local_browser")
            and not normalized
            and bilibili_review_fallback_items
        ):
            fallback_items = bilibili_review_fallback_items[
                :BILIBILI_REFERENCE_FALLBACK_LIMIT
            ]
            reference_items.extend(
                build_candidate(
                    item,
                    is_bilibili_related=True,
                    extra_warning="未命中完整关键词，已保留为待确认素材，避免本次搜索直接变成 0 条。",
                )
                for item in fallback_items
            )
            counts["irrelevant_count"] = max(
                0, counts["irrelevant_count"] - len(fallback_items)
            )
        counts["reference_items"] = reference_items
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
                "provider_payload_invalid" if payload_diagnostic else "provider_empty"
            )
        if not normalized:
            if validation_counts["out_of_window_count"]:
                return "all_out_of_window"
            if validation_counts["irrelevant_count"]:
                return "all_irrelevant"
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
        schedule_recrawls: bool = True,
        tracking_parent_batch_id: str | None = None,
    ) -> None:
        keyword_key = keyword.casefold()
        all_checkpoints = self.repository.list_sampling_checkpoints(keyword_key)
        observed_ids: set[str] = set()
        for fallback_rank, item in enumerate(normalized, start=1):
            video_id = self.repository.resolve_candidate_id(
                platform.value, item.platform_item_id
            )
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
                and checkpoint.tracking_batch_id == tracking_parent_batch_id
            ]
            if schedule_recrawls:
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
            if not schedule_recrawls:
                continue
            matching_checkpoints = [
                checkpoint
                for checkpoint in all_checkpoints
                if checkpoint.candidate_id == video_id
                and checkpoint.platform == platform
                and checkpoint.provider_name == provider
                and checkpoint.published_window_days == published_window_days
                and checkpoint.tracking_batch_id == tracking_parent_batch_id
            ]
            pending = [
                checkpoint
                for checkpoint in matching_checkpoints
                if checkpoint.status == SamplingStatus.PENDING
                and item.metrics.sampled_at < checkpoint.due_at
            ]
            observed_count = sum(
                checkpoint.status == SamplingStatus.OBSERVED
                for checkpoint in matching_checkpoints
            )
            # 刚在本轮命中的到期采样也要计入，才能安排第三个点。
            observed_count += sum(
                checkpoint.status == SamplingStatus.PENDING
                and item.metrics.sampled_at >= checkpoint.due_at
                for checkpoint in matching_checkpoints
            )
            if pending or observed_count >= 2:
                continue
            interval = (
                ADAPTIVE_FIRST_RECRAWL_HOURS
                if not matching_checkpoints
                else self._adaptive_next_interval(video_id)
            )
            checkpoint = SamplingCheckpoint(
                checkpoint_id=(
                    f"sample-{hashlib.sha256(f'{discovery.request_id}|{video_id}|{interval}|{observed_count}'.encode()).hexdigest()[:12]}"
                ),
                keyword=keyword_key,
                candidate_id=video_id,
                request_id=discovery.request_id,
                platform=platform,
                provider_name=provider,
                published_window_days=published_window_days,
                offset_hours=interval,
                due_at=item.metrics.sampled_at + timedelta(hours=interval),
                tracking_batch_id=tracking_parent_batch_id,
                billing_authorized=bool(tracking_parent_batch_id),
            )
            self.repository.save_sampling_checkpoint(checkpoint)
            all_checkpoints.append(checkpoint)

        if schedule_recrawls:
            miss_cutoff = discovery.finished_at - timedelta(
                minutes=RECRAWL_MISS_GRACE_MINUTES
            )
            for checkpoint in all_checkpoints:
                if (
                    checkpoint.status == SamplingStatus.PENDING
                    and checkpoint.platform == platform
                    and checkpoint.provider_name == provider
                    and checkpoint.published_window_days == published_window_days
                    and checkpoint.tracking_batch_id == tracking_parent_batch_id
                    and checkpoint.due_at < miss_cutoff
                    and checkpoint.candidate_id not in observed_ids
                ):
                    self.repository.save_sampling_checkpoint(
                        checkpoint.model_copy(update={"status": SamplingStatus.MISSED})
                    )

    def _save_reference_matches(
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
        """Persist bounded reference cards without scheduling recrawls."""
        keyword_key = keyword.casefold()
        for fallback_rank, item in enumerate(normalized, start=1):
            video_id = self.repository.resolve_candidate_id(
                platform.value, item.platform_item_id
            )
            if not video_id:
                continue
            self.repository.save_candidate_match(
                CandidateMatch(
                    request_id=discovery.request_id,
                    video_id=video_id,
                    keyword=keyword_key,
                    cohort_key=f"{provider}:{platform.value}:keyword-reference:{keyword_key}",
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

    def _cached_run(
        self,
        *,
        provider: str,
        platform: Platform,
        keyword: str,
        published_window_days: int,
        hotspot_window_hours: int | None,
        count: int,
        now: datetime,
        cache_ttl_minutes: int = CACHE_TTL_MINUTES,
        kuaishou_sort: str = "platform",
        kuaishou_duration_bucket: str = "all",
    ) -> PlatformSearchRun | None:
        fingerprint = self._fingerprint(
            provider,
            platform,
            keyword,
            published_window_days,
            hotspot_window_hours,
            count,
            kuaishou_sort,
            kuaishou_duration_bucket,
        )
        cached = self.repository.find_cached_platform_search_run(
            provider=provider,
            platform=platform,
            keyword=keyword,
            published_window_days=published_window_days,
            hotspot_window_hours=hotspot_window_hours,
            requested_count=count,
            since=now - timedelta(minutes=max(1, cache_ttl_minutes)),
            request_fingerprint=fingerprint,
        )
        if (
            cached
            and cached.request_fingerprint == fingerprint
            and not (
                cached.status == PlatformRunStatus.PARTIAL
                and cached.returned_count == 0
            )
            and not any(
                error.code in _NON_CACHEABLE_PUBLIC_SEARCH_CODES
                for error in cached.errors
            )
        ):
            return cached
        return None

    def _adaptive_next_interval(self, video_id: str) -> int:
        """Use the latest actual interaction delta to choose the final sample gap."""
        snapshots = self.repository.list_snapshots(video_id)
        if len(snapshots) < 2:
            return ADAPTIVE_SLOW_RECRAWL_HOURS
        previous, current = snapshots[-2:]
        previous_value = sum(
            weight * (value or 0)
            for weight, value in zip(
                (1, 3, 4, 4),
                (
                    previous.likes,
                    previous.comments,
                    previous.shares,
                    previous.favorites,
                ),
                strict=True,
            )
        )
        current_value = sum(
            weight * (value or 0)
            for weight, value in zip(
                (1, 3, 4, 4),
                (current.likes, current.comments, current.shares, current.favorites),
                strict=True,
            )
        )
        return (
            ADAPTIVE_FAST_RECRAWL_HOURS
            if current_value > previous_value
            else ADAPTIVE_SLOW_RECRAWL_HOURS
        )

    def _selected_platforms(
        self, platforms: tuple[Platform, ...] | None
    ) -> tuple[Platform, ...]:
        selected = (
            self.active_platforms
            if platforms is None
            else tuple(dict.fromkeys(platforms))
        )
        if not selected:
            raise ValueError("至少需要选择一个关键词搜索平台。")
        unsupported = set(selected) - set(self.active_platforms)
        if unsupported:
            names = "、".join(sorted(item.value for item in unsupported))
            raise ValueError(f"当前未启用的平台不能参与本次搜索：{names}。")
        return selected

    def _finish_run(self, run: PlatformSearchRun, **updates) -> PlatformSearchRun:
        finished = run.model_copy(update={"finished_at": self.clock(), **updates})
        self.repository.save_platform_search_run(finished)
        return finished

    @staticmethod
    def _validate_request(
        keyword: str,
        published_window_days: int,
        count: int,
        hotspot_window_hours: int | None = None,
    ) -> str:
        normalized = keyword.strip()
        if not 1 <= len(normalized) <= 50:
            raise ValueError("关键词长度必须为 1 到 50 个字符。")
        if published_window_days not in RECRAWL_OFFSETS_BY_WINDOW:
            raise ValueError(
                "召回时间范围只支持不限、近 24 小时、近 3 天、近 7 天、近 30 天、近半年或近 10 个月。"
            )
        if hotspot_window_hours not in {None, 1, 24, 72, 168}:
            raise ValueError(
                "热点宝榜单周期只支持近 1 小时、近 1 天、近 3 天或近 7 天。"
            )
        if not 1 <= count <= 100:
            raise ValueError("每个平台获取数量必须为 1 到 100 条。")
        return normalized

    @staticmethod
    def _fingerprint(
        provider: str,
        platform: Platform,
        keyword: str,
        published_window_days: int,
        hotspot_window_hours: int | None,
        count: int,
        kuaishou_sort: str = "platform",
        kuaishou_duration_bucket: str = "all",
    ) -> str:
        payload = (
            f"{provider}|{platform.value}|{RANKING_MODE}|{RELEVANCE_RULE_VERSION}|{keyword.casefold()}|"
            f"{published_window_days}|{hotspot_window_hours or '-'}|{count}|"
            f"{kuaishou_sort}|{kuaishou_duration_bucket}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_kuaishou_filters(
        sort: str,
        duration_bucket: str,
    ) -> tuple[str, str]:
        if sort not in {"platform", "newest", "likes"}:
            raise ValueError("快手排序只支持综合、最新发布或最多点赞。")
        if duration_bucket not in {"all", "under_60", "between_60_300", "over_300"}:
            raise ValueError("快手时长只支持不限、1分钟以下、1到5分钟或5分钟以上。")
        return sort, duration_bucket

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
            Platform.BILIBILI: ("bilibili.com", "b23.tv"),
            Platform.XIAOHONGSHU: ("xiaohongshu.com", "xhslink.com"),
            Platform.KUAISHOU: ("kuaishou.com", "gifshow.com"),
            Platform.WECHAT_CHANNELS: (
                "channels.weixin.qq.com",
                "weixin.qq.com",
                "wechat.com",
            ),
        }
        return any(marker in url.casefold() for marker in host_markers[platform])
