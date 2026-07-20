"""关键词爬虫 API —— FastAPI 直接复用商业搜索服务与 SQLite 持久化。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import (
    get_commercial_search_service,
    get_licensed_search_provider,
    get_repository,
)
from src.models import PlatformRunStatus, SearchBatch
from src.services.commercial_search import (
    CACHE_TTL_MINUTES,
    MONTHLY_HARD_LIMIT_QUERIES,
    MONTHLY_WARNING_QUERIES,
)

router = APIRouter(prefix="/api/v1/crawler", tags=["crawler"])

PLATFORM_LABELS: dict[str, str] = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "wechat_channels": "视频号",
}


class CrawlerSearchRequest(BaseModel):
    keyword: str = Field(..., min_length=2, max_length=50, description="搜索关键词")
    published_window_days: int = Field(7, description="1=近24小时，7=近7天")
    count_per_platform: int = Field(10, ge=1, le=10, description="每平台返回数量")
    force_refresh: bool = Field(False, description="是否绕过缓存强制刷新")


class CrawlerPlatformPreview(BaseModel):
    platform: str
    platform_label: str
    cache_hit: bool
    estimated_api_calls: int
    blocked_reason: str | None = None


class CrawlerPreviewResponse(BaseModel):
    keyword: str
    published_window_days: int
    count_per_platform: int
    force_refresh: bool
    provider_mode: str
    provider_name: str
    monthly_query_count: int
    monthly_warning_queries: int
    monthly_hard_limit_queries: int
    cache_ttl_minutes: int
    platforms: list[CrawlerPlatformPreview]
    blocked: bool


class CrawlerCapabilitiesResponse(BaseModel):
    provider_name: str
    display_name: str
    mode: str
    enabled: bool
    supported_platforms: list[str]
    supported_platform_labels: list[str]
    missing_configuration: list[str]
    permission_status: str
    monthly_query_count: int
    monthly_warning_queries: int
    monthly_hard_limit_queries: int
    cache_ttl_minutes: int
    supports_usage: bool
    usage: dict[str, Any] | None = None


class CrawlerCandidateResult(BaseModel):
    video_id: str
    title: str
    author_name: str
    platform: str
    platform_label: str
    source_url: str | None = None
    published_at: datetime | None = None
    trend_score: float | None = None
    trend_level: str | None = None
    confidence: float | None = None
    pool_size: int | None = None
    like_growth_per_hour: float | None = None
    anomaly_status: str | None = None
    platform_rank: int | None = None
    evidence: str | None = None
    reasons: list[str] = Field(default_factory=list)


class CrawlerPlatformRunResponse(BaseModel):
    run_id: str
    platform: str
    platform_label: str
    provider: str
    mode: str
    status: str
    requested_count: int
    returned_count: int
    cache_hit: bool
    cached_from_run_id: str | None = None
    api_call_count: int
    billable_units: float | None = None
    quota_remaining: int | None = None
    error: str | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    candidates: list[CrawlerCandidateResult] = Field(default_factory=list)


class CrawlerBatchResponse(BaseModel):
    batch_id: str
    keyword: str
    published_window_days: int
    count_per_platform: int
    provider: str
    mode: str
    status: str
    force_refresh: bool
    created_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    platform_runs: list[CrawlerPlatformRunResponse] = Field(default_factory=list)
    total_api_calls: int = 0
    total_candidates: int = 0


class CrawlerBatchListResponse(BaseModel):
    items: list[CrawlerBatchResponse]
    total: int


def _platform_label(value: str) -> str:
    return PLATFORM_LABELS.get(value, value)


def _capability_payload(service, provider) -> CrawlerCapabilitiesResponse:
    capability = provider.capabilities()
    usage = provider.usage()
    return CrawlerCapabilitiesResponse(
        provider_name=capability.provider_name,
        display_name=capability.display_name,
        mode=capability.mode.value,
        enabled=capability.enabled,
        supported_platforms=[item.value for item in capability.supported_platforms],
        supported_platform_labels=[
            _platform_label(item.value) for item in capability.supported_platforms
        ],
        missing_configuration=capability.missing_configuration,
        permission_status=capability.permission_status,
        monthly_query_count=service.monthly_query_count(),
        monthly_warning_queries=MONTHLY_WARNING_QUERIES,
        monthly_hard_limit_queries=MONTHLY_HARD_LIMIT_QUERIES,
        cache_ttl_minutes=CACHE_TTL_MINUTES,
        supports_usage=capability.supports_usage,
        usage=usage.model_dump(mode="json") if usage else None,
    )


@router.get("/capabilities", response_model=CrawlerCapabilitiesResponse)
def get_capabilities(
    service=Depends(get_commercial_search_service),
    provider=Depends(get_licensed_search_provider),
):
    """返回供应商模式、支持平台、缺失配置、缓存和额度状态。"""
    return _capability_payload(service, provider)


@router.post("/preview", response_model=CrawlerPreviewResponse)
def preview_crawler_batch(
    body: CrawlerSearchRequest,
    service=Depends(get_commercial_search_service),
    provider=Depends(get_licensed_search_provider),
):
    """提交前预览三平台执行计划，包含缓存命中、预计调用和阻断原因。"""
    try:
        previews = service.preview(
            keyword=body.keyword,
            published_window_days=body.published_window_days,
            count=body.count_per_platform,
            force_refresh=body.force_refresh,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    capability = provider.capabilities()
    platform_items = [
        CrawlerPlatformPreview(
            platform=item.platform.value,
            platform_label=_platform_label(item.platform.value),
            cache_hit=item.cache_hit,
            estimated_api_calls=item.estimated_api_calls,
            blocked_reason=item.blocked_reason,
        )
        for item in previews
    ]
    return CrawlerPreviewResponse(
        keyword=body.keyword.strip(),
        published_window_days=body.published_window_days,
        count_per_platform=body.count_per_platform,
        force_refresh=body.force_refresh,
        provider_mode=capability.mode.value,
        provider_name=capability.provider_name,
        monthly_query_count=service.monthly_query_count(),
        monthly_warning_queries=MONTHLY_WARNING_QUERIES,
        monthly_hard_limit_queries=MONTHLY_HARD_LIMIT_QUERIES,
        cache_ttl_minutes=CACHE_TTL_MINUTES,
        platforms=platform_items,
        blocked=all(item.blocked_reason for item in platform_items),
    )


@router.post("/batches", response_model=CrawlerBatchResponse)
def create_crawler_batch(
    body: CrawlerSearchRequest,
    service=Depends(get_commercial_search_service),
    repo=Depends(get_repository),
):
    """执行三平台关键词榜单批次，并持久化到 SQLite。"""
    try:
        batch = service.execute(
            keyword=body.keyword,
            published_window_days=body.published_window_days,
            count=body.count_per_platform,
            force_refresh=body.force_refresh,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _batch_to_response(batch, repo)


@router.get("/batches", response_model=CrawlerBatchListResponse)
def list_crawler_batches(
    limit: int = 20,
    repo=Depends(get_repository),
):
    """列出 SQLite 中持久化的历史搜索批次。"""
    safe_limit = max(1, min(limit, 100))
    batches = repo.list_search_batches(limit=safe_limit)
    return CrawlerBatchListResponse(
        items=[_batch_to_response(batch, repo, include_candidates=False) for batch in batches],
        total=len(batches),
    )


@router.get("/batches/{batch_id}", response_model=CrawlerBatchResponse)
def get_crawler_batch(
    batch_id: str,
    repo=Depends(get_repository),
):
    """获取搜索批次详情，包括平台运行记录、候选与趋势依据。"""
    batch = repo.get_search_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="搜索批次不存在。")
    return _batch_to_response(batch, repo)


def _batch_to_response(
    batch: SearchBatch,
    repo,
    *,
    include_candidates: bool = True,
) -> CrawlerBatchResponse:
    runs = repo.list_platform_search_runs(batch.batch_id)
    run_items = [
        _run_to_response(batch, run, repo, include_candidates=include_candidates)
        for run in runs
    ]
    return CrawlerBatchResponse(
        batch_id=batch.batch_id,
        keyword=batch.keyword,
        published_window_days=batch.published_window_days,
        count_per_platform=batch.requested_count_per_platform,
        provider=batch.provider,
        mode=batch.mode.value,
        status=batch.status.value,
        force_refresh=batch.force_refresh,
        created_at=batch.created_at,
        finished_at=batch.finished_at,
        error=batch.error,
        platform_runs=run_items,
        total_api_calls=sum(item.api_call_count for item in run_items),
        total_candidates=sum(item.returned_count for item in run_items),
    )


def _run_to_response(
    batch: SearchBatch,
    run,
    repo,
    *,
    include_candidates: bool,
) -> CrawlerPlatformRunResponse:
    candidates: list[CrawlerCandidateResult] = []
    if include_candidates and run.status in {
        PlatformRunStatus.SUCCEEDED,
        PlatformRunStatus.CACHED,
    }:
        match_run_id = run.cached_from_run_id if run.cached_from_run_id else run.run_id
        matches = repo.list_candidate_matches(match_run_id)
        trend_by_candidate = {
            item.candidate_id: item
            for item in repo.list_keyword_trend_results(
                batch.keyword,
                limit=batch.requested_count_per_platform,
                platform=run.platform,
            )
        }
        for match in sorted(matches, key=lambda item: item.platform_rank):
            candidate = repo.get_candidate(match.video_id)
            if candidate is None:
                continue
            trend = trend_by_candidate.get(candidate.video_id)
            candidates.append(
                CrawlerCandidateResult(
                    video_id=candidate.video_id,
                    title=candidate.title,
                    author_name=candidate.author_name,
                    platform=candidate.platform.value,
                    platform_label=_platform_label(candidate.platform.value),
                    source_url=str(candidate.source_url) if candidate.source_url else None,
                    published_at=candidate.published_at,
                    trend_score=trend.score if trend else None,
                    trend_level=trend.level.value if trend else None,
                    confidence=trend.confidence if trend else None,
                    pool_size=trend.pool_size if trend else None,
                    like_growth_per_hour=trend.like_growth_per_hour if trend else None,
                    anomaly_status=trend.anomaly_status.value if trend else None,
                    platform_rank=match.platform_rank,
                    evidence=match.evidence or candidate.evidence,
                    reasons=trend.reasons if trend else [],
                )
            )
    return CrawlerPlatformRunResponse(
        run_id=run.run_id,
        platform=run.platform.value,
        platform_label=_platform_label(run.platform.value),
        provider=run.provider,
        mode=run.mode.value,
        status=run.status.value,
        requested_count=run.requested_count,
        returned_count=run.returned_count,
        cache_hit=run.cache_hit,
        cached_from_run_id=run.cached_from_run_id,
        api_call_count=run.api_call_count,
        billable_units=run.billable_units,
        quota_remaining=run.quota_remaining,
        error=run.error,
        errors=[error.model_dump(mode="json") for error in run.errors],
        started_at=run.started_at,
        finished_at=run.finished_at,
        candidates=candidates,
    )
