"""关键词爬虫 API —— FastAPI 直接复用商业搜索服务与 SQLite 持久化。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import (
    get_commercial_search_service,
    get_licensed_search_provider,
    get_media_resolution_service,
    get_repository,
    get_transcription_service,
)
from project.backend.app.core import config as backend_config
from project.backend.app.core.config import ASRMode
from project.backend.app.schemas.responses import TranscriptionResponse
from src.models import PlatformRunStatus, SearchBatch
from src.adapters.licensed import LicensedProviderError
from src.services.media_resolution import MediaResolutionError
from src.services.transcription import MAX_PROVIDER_MEDIA_BYTES
from src.services.commercial_search import (
    CACHE_TTL_MINUTES,
    MONTHLY_HARD_LIMIT_COST_CNY,
    MONTHLY_HARD_LIMIT_QUERIES,
    MONTHLY_WARNING_QUERIES,
    RANKING_MODE,
)
from src.services.transcription import TranscriptionError

router = APIRouter(prefix="/api/v1/crawler", tags=["crawler"])

PLATFORM_LABELS: dict[str, str] = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "wechat_channels": "视频号",
}


class CrawlerSearchRequest(BaseModel):
    keyword: str = Field(..., min_length=2, max_length=50, description="搜索关键词")
    published_window_days: int = Field(7, description="1=近24小时，7=近7天")
    count_per_platform: int = Field(10, ge=1, le=10, description="当前启用平台返回数量")
    force_refresh: bool = Field(False, description="是否绕过缓存强制刷新")


class CrawlerPlatformPreview(BaseModel):
    platform: str
    platform_label: str
    cache_hit: bool
    estimated_api_calls: int
    platform_unit_price_cny: float | None = None
    estimated_cost_cny: float | None = None
    blocked_reason: str | None = None


class CrawlerPreviewResponse(BaseModel):
    keyword: str
    published_window_days: int
    count_per_platform: int
    force_refresh: bool
    provider_mode: str
    provider_name: str
    ranking_mode: str
    monthly_query_count: int
    monthly_estimated_cost_cny: float
    monthly_warning_queries: int
    monthly_hard_limit_queries: int
    monthly_hard_limit_cost_cny: float
    cache_ttl_minutes: int
    platforms: list[CrawlerPlatformPreview]
    estimated_total_cost_cny: float
    blocked: bool


class CrawlerCapabilitiesResponse(BaseModel):
    provider_name: str
    display_name: str
    mode: str
    enabled: bool
    supported_platforms: list[str]
    supported_platform_labels: list[str]
    active_platforms: list[str]
    active_platform_labels: list[str]
    paused_platforms: list[str]
    paused_platform_labels: list[str]
    missing_configuration: list[str]
    permission_status: str
    monthly_query_count: int
    monthly_estimated_cost_cny: float
    monthly_warning_queries: int
    monthly_hard_limit_queries: int
    monthly_hard_limit_cost_cny: float
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
    provider_hot_rank: int | None = None
    system_rank: int | None = None
    plays: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    favorites: int | None = None
    component_scores: dict[str, float | None] = Field(default_factory=dict)
    data_quality_warnings: list[str] = Field(default_factory=list)
    model_version: str | None = None
    evidence: str | None = None
    reasons: list[str] = Field(default_factory=list)
    media_resolution_status: str | None = None
    media_transcription_task_id: str | None = None


class CrawlerCandidateMediaPreviewResponse(BaseModel):
    candidate_id: str
    resolvable: bool
    mode: str
    provider: str
    platform: str
    platform_label: str
    platform_item_id: str | None = None
    estimated_cost_cny: float | None = None
    monthly_budget_used_cny: float
    monthly_budget_limit_cny: float
    existing_task_id: str | None = None
    last_resolution_status: str | None = None
    block_reason: str | None = None
    source: str


class CrawlerCandidateTranscriptionRequest(BaseModel):
    rights_confirmed: bool = Field(False, description="确认拥有媒体处理权")
    rights_holder: str = Field(..., min_length=1, max_length=80)
    model_name: str = Field("large-v3-turbo", description="转写模型")
    hotwords: str = Field("", max_length=500)


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
    total_estimated_cost_cny: float = 0.0


class CrawlerBatchListResponse(BaseModel):
    items: list[CrawlerBatchResponse]
    total: int


def _platform_label(value: str) -> str:
    return PLATFORM_LABELS.get(value, value)


def _capability_payload(service, provider) -> CrawlerCapabilitiesResponse:
    capability = provider.capabilities()
    active_platforms = list(service.active_platforms)
    paused_platforms = [
        item
        for item in capability.supported_platforms
        if item not in active_platforms
    ]
    try:
        usage = provider.usage()
    except LicensedProviderError:
        usage = None
    return CrawlerCapabilitiesResponse(
        provider_name=capability.provider_name,
        display_name=capability.display_name,
        mode=capability.mode.value,
        enabled=capability.enabled,
        supported_platforms=[item.value for item in capability.supported_platforms],
        supported_platform_labels=[
            _platform_label(item.value) for item in capability.supported_platforms
        ],
        active_platforms=[item.value for item in active_platforms],
        active_platform_labels=[_platform_label(item.value) for item in active_platforms],
        paused_platforms=[item.value for item in paused_platforms],
        paused_platform_labels=[_platform_label(item.value) for item in paused_platforms],
        missing_configuration=capability.missing_configuration,
        permission_status=capability.permission_status,
        monthly_query_count=service.monthly_query_count(),
        monthly_estimated_cost_cny=service.monthly_query_cost(),
        monthly_warning_queries=MONTHLY_WARNING_QUERIES,
        monthly_hard_limit_queries=MONTHLY_HARD_LIMIT_QUERIES,
        monthly_hard_limit_cost_cny=MONTHLY_HARD_LIMIT_COST_CNY,
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
    """提交前预览当前启用平台的执行计划。"""
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
            platform_unit_price_cny=item.platform_unit_price_cny,
            estimated_cost_cny=item.estimated_cost_cny,
            blocked_reason=item.blocked_reason,
        )
        for item in previews
    ]
    estimated_total_cost = round(
        sum(item.estimated_cost_cny or 0.0 for item in platform_items),
        4,
    )
    return CrawlerPreviewResponse(
        keyword=body.keyword.strip(),
        published_window_days=body.published_window_days,
        count_per_platform=body.count_per_platform,
        force_refresh=body.force_refresh,
        provider_mode=capability.mode.value,
        provider_name=capability.provider_name,
        ranking_mode=RANKING_MODE,
        monthly_query_count=service.monthly_query_count(),
        monthly_estimated_cost_cny=service.monthly_query_cost(),
        monthly_warning_queries=MONTHLY_WARNING_QUERIES,
        monthly_hard_limit_queries=MONTHLY_HARD_LIMIT_QUERIES,
        monthly_hard_limit_cost_cny=MONTHLY_HARD_LIMIT_COST_CNY,
        cache_ttl_minutes=CACHE_TTL_MINUTES,
        platforms=platform_items,
        estimated_total_cost_cny=estimated_total_cost,
        blocked=all(item.blocked_reason for item in platform_items),
    )


@router.post("/batches", response_model=CrawlerBatchResponse)
def create_crawler_batch(
    body: CrawlerSearchRequest,
    service=Depends(get_commercial_search_service),
    repo=Depends(get_repository),
):
    """执行当前启用平台的关键词榜单批次并持久化。"""
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
        items=[
            _batch_to_response(batch, repo, include_candidates=False)
            for batch in batches
        ],
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


@router.get(
    "/candidates/{candidate_id}/media-preview",
    response_model=CrawlerCandidateMediaPreviewResponse,
)
def preview_candidate_media_resolution(
    candidate_id: str,
    repo=Depends(get_repository),
    service=Depends(get_media_resolution_service),
):
    """预览单条候选补媒体直链的成本、预算和阻断原因。"""
    candidate = repo.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="候选不存在。")
    preview = service.preview(candidate)
    return CrawlerCandidateMediaPreviewResponse(
        candidate_id=preview.candidate_id,
        resolvable=preview.resolvable,
        mode=preview.mode,
        provider=preview.provider,
        platform=preview.platform.value,
        platform_label=_platform_label(preview.platform.value),
        platform_item_id=preview.platform_item_id,
        estimated_cost_cny=preview.estimated_cost_cny,
        monthly_budget_used_cny=preview.monthly_budget_used_cny,
        monthly_budget_limit_cny=preview.monthly_budget_limit_cny,
        existing_task_id=preview.existing_task_id,
        last_resolution_status=(
            preview.last_resolution_status.value
            if preview.last_resolution_status
            else None
        ),
        block_reason=preview.block_reason,
        source=preview.source,
    )


@router.post(
    "/candidates/{candidate_id}/transcriptions",
    response_model=TranscriptionResponse,
)
def transcribe_candidate_media(
    candidate_id: str,
    body: CrawlerCandidateTranscriptionRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8),
    repo=Depends(get_repository),
    media_service=Depends(get_media_resolution_service),
    transcription_service=Depends(get_transcription_service),
):
    """授权后按单条候选补媒体并创建真实转写任务。"""
    if backend_config.ASR_MODE == ASRMode.SANDBOX:
        raise HTTPException(
            status_code=400,
            detail="当前转写仍是 Sandbox 模式，未发起 OneAPI 媒体解析；请先启用真实 ASR。",
        )
    if not body.rights_confirmed:
        raise HTTPException(status_code=400, detail="必须确认拥有媒体处理权。")
    candidate = repo.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="候选不存在。")

    previous = repo.find_media_resolution_by_idempotency_key(idempotency_key)
    if previous and previous.task_id:
        task = repo.get_task(previous.task_id)
        if task is not None:
            return _transcription_to_response(task)

    try:
        resolved = media_service.resolve_video(
            candidate,
            idempotency_key=idempotency_key,
        )
        task = transcription_service.create_task(
            media_name=resolved.video.name,
            media_type=resolved.video.media_type,
            media_bytes=resolved.video.content,
            rights_confirmed=body.rights_confirmed,
            rights_holder=body.rights_holder,
            candidate_id=candidate.video_id,
            model_name=body.model_name,
            hotwords=body.hotwords or None,
            max_media_bytes=MAX_PROVIDER_MEDIA_BYTES,
        )
        media_service.attach_task(resolved.attempt, task)
    except MediaResolutionError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.user_message
        ) from exc
    except TranscriptionError as exc:
        raise HTTPException(status_code=400, detail=exc.user_message) from exc
    return _transcription_to_response(task)


def _transcription_to_response(task) -> TranscriptionResponse:
    segments = [
        {
            "start": s.start,
            "end": s.end,
            "text": s.text,
            "confidence": s.confidence,
            "needs_review": s.needs_review,
            "reviewed": s.reviewed,
        }
        for s in (task.segments or [])
    ]
    return TranscriptionResponse(
        task_id=task.task_id,
        title=task.title,
        status=task.status.value,
        progress=task.progress,
        stage=task.stage,
        media_name=task.media_name,
        model_name=task.model_name,
        duration_seconds=task.duration_seconds,
        approved_revision_id=task.approved_revision_id,
        low_confidence_count=sum(
            1
            for segment in (task.segments or [])
            if segment.needs_review and not segment.reviewed
        ),
        segments=segments,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


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
        total_estimated_cost_cny=round(
            sum(item.billable_units or 0.0 for item in run_items),
            4,
        ),
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
        PlatformRunStatus.PARTIAL,
        PlatformRunStatus.CACHED,
    }:
        match_run_id = run.cached_from_run_id if run.cached_from_run_id else run.run_id
        matches = repo.list_candidate_matches(match_run_id)
        trends = repo.list_keyword_trend_results(
            batch.keyword,
            limit=batch.requested_count_per_platform,
            platform=run.platform,
        )
        trend_by_candidate = {item.candidate_id: item for item in trends}
        system_rank_by_candidate = {
            item.candidate_id: index for index, item in enumerate(trends, start=1)
        }
        for match in sorted(matches, key=lambda item: item.platform_rank):
            candidate = repo.get_candidate(match.video_id)
            if candidate is None:
                continue
            trend = trend_by_candidate.get(candidate.video_id)
            media_resolution = repo.find_latest_media_resolution_for_candidate(
                candidate.video_id
            )
            candidates.append(
                CrawlerCandidateResult(
                    video_id=candidate.video_id,
                    title=candidate.title,
                    author_name=candidate.author_name,
                    platform=candidate.platform.value,
                    platform_label=_platform_label(candidate.platform.value),
                    source_url=str(candidate.source_url)
                    if candidate.source_url
                    else None,
                    published_at=candidate.published_at,
                    trend_score=trend.score if trend else None,
                    trend_level=trend.level.value if trend else None,
                    confidence=trend.confidence if trend else None,
                    pool_size=trend.pool_size if trend else None,
                    like_growth_per_hour=trend.like_growth_per_hour if trend else None,
                    anomaly_status=trend.anomaly_status.value if trend else None,
                    platform_rank=match.platform_rank,
                    provider_hot_rank=match.platform_rank,
                    system_rank=system_rank_by_candidate.get(candidate.video_id),
                    plays=candidate.metrics.plays,
                    likes=candidate.metrics.likes,
                    comments=candidate.metrics.comments,
                    shares=candidate.metrics.shares,
                    favorites=candidate.metrics.favorites,
                    component_scores=trend.component_scores if trend else {},
                    data_quality_warnings=candidate.data_quality_warnings,
                    model_version=trend.model_version if trend else None,
                    evidence=match.evidence or candidate.evidence,
                    reasons=trend.reasons if trend else [],
                    media_resolution_status=(
                        media_resolution.status.value if media_resolution else None
                    ),
                    media_transcription_task_id=(
                        media_resolution.task_id if media_resolution else None
                    ),
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
