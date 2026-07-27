"""关键词爬虫 API —— FastAPI 直接复用商业搜索服务与 SQLite 持久化。"""

from __future__ import annotations

import subprocess
import sys
import os
import shutil
import re
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import (
    get_commercial_search_service,
    get_discovery_search_provider,
    get_hotspot_browser_provider,
    get_hotspot_search_service,
    get_doubao_browser_service,
    get_doubao_mobile_service,
    get_media_resolution_service,
    get_official_hot_billboard_adapter,
    get_official_hot_pool_service,
    get_official_hot_words_adapter,
    get_copywriting_service,
    get_repository,
    get_transcription_service,
)
from project.backend.app.core import config as backend_config
from project.backend.app.core.config import ASRMode
from project.backend.app.schemas.responses import TranscriptionResponse
from src.models import (
    CopySource,
    Platform,
    PlatformRunStatus,
    SamplingStatus,
    SearchBatch,
    TaskStatus,
    TranscriptionTask,
)
from src.adapters.licensed import LicensedProviderError
from src.adapters.official import OfficialAdapterDisabledError, OfficialApiError
from src.services.doubao_browser import DoubaoBrowserAutomationError
from src.services.hot_pool import _candidate_hot_words
from src.services.media_resolution import MediaResolutionError
from src.services.transcription import MAX_PROVIDER_MEDIA_BYTES
from src.services.commercial_search import (
    CACHE_TTL_MINUTES,
    MONTHLY_HARD_LIMIT_COST_CNY,
    MONTHLY_HARD_LIMIT_QUERIES,
    MONTHLY_WARNING_QUERIES,
    RANKING_MODE,
    RELEVANCE_RULE_VERSION,
    SMART_FALLBACK_CACHE_TTL_MINUTES,
    keyword_match_reason,
    title_matches_keyword,
)
from src.services.transcription import TranscriptionError

router = APIRouter(prefix="/api/v1/crawler", tags=["crawler"])

PLATFORM_LABELS: dict[str, str] = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "wechat_channels": "视频号",
}
SMART_FREE_CANDIDATE_THRESHOLD = 3
# 主榜只展示有足够互动基础的严格相关内容；官方热榜候选不受此门槛限制。
INTERACTION_HEAT_FLOOR = 100.0
HOTSPOT_CACHE_TTL_MINUTES = 10
HOTSPOT_COOLDOWN_MIN_SECONDS = 8 * 60
HOTSPOT_COOLDOWN_MAX_SECONDS = 12 * 60
HOTSPOT_SAFETY_PAUSE_SECONDS = 60 * 60
HOTSPOT_LEASE_SECONDS = 10 * 60
HOTSPOT_ROLLING_WINDOW_SECONDS = 24 * 60 * 60
# The 8–12 minute randomized cooldown still spaces browser work; the rolling
# cap is sized for several active customers rather than a single operator.
HOTSPOT_MAX_REAL_RUNS_PER_WINDOW = 48
HOTSPOT_PROVIDER_KEY = "douyin_hotspot_browser"
HOTSPOT_WINDOW_LABELS = {
    1: "近1小时",
    24: "近1天",
    72: "近3天",
    168: "近7天",
}


def _hotspot_window_label(hours: int | None) -> str:
    return HOTSPOT_WINDOW_LABELS.get(hours or 168, f"近{hours}小时")


def _next_hotspot_cooldown_seconds() -> int:
    """Choose one cooldown per real run; the stored deadline remains authoritative."""
    return random.SystemRandom().randint(
        HOTSPOT_COOLDOWN_MIN_SECONDS,
        HOTSPOT_COOLDOWN_MAX_SECONDS,
    )


class CrawlerSearchRequest(BaseModel):
    keyword: str = Field(..., min_length=2, max_length=50, description="搜索关键词")
    # OneAPI/官方热榜兼容字段；热点宝不使用它限制视频发布时间。
    published_window_days: int = Field(0, description="0=不限；1/7 兼容其他发现模式")
    hotspot_window_hours: Literal[1, 24, 72, 168] = Field(
        168,
        description="热点宝榜单统计周期：1/24/72/168 小时；不限制视频发布时间。",
    )
    count_per_platform: int = Field(10, ge=1, le=10, description="当前启用平台返回数量")
    hotspot_result_limit: int = Field(
        100,
        ge=1,
        le=100,
        description="热点宝所选周期五榜合并、筛选后的最多保留数量；不影响 OneAPI 的 10 条上限。",
    )
    force_refresh: bool = Field(False, description="是否绕过缓存强制刷新")
    mode: str | None = Field(
        default=None,
        description="批次模式；smart=先免费池后低价兜底；official_hot=仅官方热榜池",
    )
    related_terms: list[str] = Field(default_factory=list, max_length=5)
    allow_related_fallback: bool = Field(
        False,
        description="严格相关候选不足时，允许对一个用户明确给出的关联词额外搜索一次",
    )
    track_trend: bool = Field(
        False,
        description="确认后安排两次自适应复搜以形成真实趋势曲线",
    )
    target_main_count: int = Field(10, ge=1, le=10, description="爆款主榜目标数量")
    max_paid_calls: int = Field(3, ge=1, le=3, description="热点宝不足时的 OneAPI 费用上限")
    allow_paid_fallback: bool = Field(
        False,
        description="仅在用户二次确认后，才允许 OneAPI 作为付费补充。",
    )


class CrawlerPlatformPreview(BaseModel):
    platform: str
    platform_label: str
    cache_hit: bool
    estimated_api_calls: int
    platform_unit_price_cny: float | None = None
    estimated_cost_cny: float | None = None
    blocked_reason: str | None = None


class CrawlerSafetyStatus(BaseModel):
    """User-visible pacing state for the local, authorized Hotspot browser."""

    profile: str = "safe"
    state: str = "ready"
    cache_ttl_minutes: int = HOTSPOT_CACHE_TTL_MINUTES
    estimated_duration_seconds: int = 150
    cooldown_remaining_seconds: int = 0
    next_available_at: datetime | None = None
    real_runs_in_window: int = 0
    real_run_limit: int = HOTSPOT_MAX_REAL_RUNS_PER_WINDOW
    rolling_window_ends_at: datetime | None = None
    message: str


class CrawlerPreviewResponse(BaseModel):
    keyword: str
    published_window_days: int
    hotspot_window_hours: int | None = None
    count_per_platform: int
    force_refresh: bool
    mode: str | None = None
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
    monitoring_policy: str = "manual_tracking_v1"
    sampling_offsets_hours: list[int] = Field(default_factory=lambda: [0])
    max_api_calls_per_platform: int = 3
    blocked: bool
    free_candidate_count: int = 0
    free_pool_status: str = "unavailable"
    free_pool_message: str | None = None
    paid_fallback_required: bool = False
    paid_fallback_cache_ttl_minutes: int | None = None
    paid_fallback_blocked_reason: str | None = None
    related_fallback_possible: bool = False
    related_fallback_term: str | None = None
    trend_tracking_enabled: bool = False
    hotspot_ready: bool = False
    hotspot_message: str | None = None
    hotspot_time_strategy: str = "1h_to_7d"
    target_main_count: int = 10
    paid_call_cap: int = 3
    hotspot_result_limit: int = 100
    hotspot_list_types: list[str] = Field(default_factory=list)
    crawl_safety: CrawlerSafetyStatus | None = None


class CrawlerOfficialHotCapability(BaseModel):
    enabled: bool
    missing_configuration: list[str]
    provider_name: str


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
    official_hot_billboard: CrawlerOfficialHotCapability | None = None
    official_hot_words: CrawlerOfficialHotCapability | None = None
    hotspot_browser: CrawlerBrowserDiscoveryCapabilities | None = None


class CrawlerBrowserDiscoveryCapabilities(BaseModel):
    enabled: bool
    running: bool
    login_required: bool
    missing_configuration: list[str] = Field(default_factory=list)
    browser_channel: str = "chrome"
    ready_to_crawl: bool = False
    phase: str = "unknown"
    adapter_version: str = "unknown"
    provider_name: str
    message: str


class CrawlerBrowserDiscoveryStartResponse(CrawlerBrowserDiscoveryCapabilities):
    started: bool


class CrawlerHotWordItem(BaseModel):
    word: str
    hot_value: int | None = None
    fetched_at: datetime


class CrawlerHotWordsResponse(BaseModel):
    words: list[CrawlerHotWordItem] = Field(default_factory=list)
    error: str | None = None


class CrawlerOfficialHotMonitorRequest(BaseModel):
    keyword: str | None = Field(default=None, min_length=2, max_length=50)
    related_terms: list[str] = Field(default_factory=list, max_length=5)


class CrawlerOfficialHotMonitorResponse(BaseModel):
    matched_count: int
    result_state: str
    result_message: str
    executed_recrawls: int
    next_recrawl_at: datetime | None = None
    candidates: list["CrawlerCandidateResult"] = Field(default_factory=list)


class CrawlerOriginalScriptResponse(BaseModel):
    copy_source: str = CopySource.METADATA_ORIGINAL.value
    is_original_transcript: bool
    needs_manual_review: bool
    script: str


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
    display_tier: str = "ordinary"
    effective_interactions: float | None = None
    confidence: float | None = None
    pool_size: int | None = None
    like_growth_per_hour: float | None = None
    engagement_growth_per_hour: float | None = None
    acceleration_ratio: float | None = None
    valid_snapshot_count: int | None = None
    recrawl_count: int | None = None
    recall_count: int | None = None
    missed_checkpoint_count: int | None = None
    sampling_span_hours: float | None = None
    anomaly_status: str | None = None
    platform_rank: int | None = None
    provider_hot_rank: int | None = None
    system_rank: int | None = None
    plays: int | None = None
    new_plays: int | None = None
    likes: int | None = None
    new_likes: int | None = None
    duration_seconds: int | None = None
    hotspot_window_hours: int | None = None
    hotspot_list_labels: list[str] = Field(default_factory=list)
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
    growth_stage: str | None = None
    snapshot_count: int | None = None
    next_recrawl_at: datetime | None = None
    copy_source: str | None = None
    is_original_transcript: bool | None = None
    needs_manual_review: bool | None = None
    share_count: int | None = None
    collect_count: int | None = None
    relevance_basis: str | None = None
    relevance_reason: str | None = None
    trend_points: list["CrawlerTrendPoint"] = Field(default_factory=list)


class CrawlerTrendPoint(BaseModel):
    sampled_at: datetime
    effective_interactions: float
    growth_per_hour: float | None = None


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


class CrawlerDoubaoJobCreateRequest(BaseModel):
    candidate_ids: list[str] = Field(..., min_length=1, max_length=10)


class CrawlerDoubaoJobStageRequest(BaseModel):
    stage: str = Field(..., min_length=1, max_length=120)
    progress: int | None = Field(default=None, ge=0, le=99)
    outputs: dict[str, str] = Field(default_factory=dict)


class CrawlerDoubaoJobCompleteRequest(BaseModel):
    transcript_text: str = Field(..., min_length=1, max_length=80_000)
    short_url: str = Field(..., min_length=1, max_length=500)
    doubao_conversation_url: str | None = Field(default=None, max_length=1000)
    doubao_message_id: str | None = Field(default=None, max_length=200)


class CrawlerDoubaoJobFailRequest(BaseModel):
    error_message: str = Field(..., min_length=1, max_length=500)
    stage: str = Field("浏览器自动化失败", min_length=1, max_length=120)
    retryable: bool = True


class CrawlerDoubaoJobResponse(TranscriptionResponse):
    candidate_id: str | None = None
    source_url: str | None = None
    douyin_short_url: str | None = None
    doubao_conversation_url: str | None = None
    fee_cny: float = 0.0
    review_required: bool = True
    prompt_version: str | None = None
    worker_id: str | None = None


class CrawlerDoubaoJobListResponse(BaseModel):
    items: list[CrawlerDoubaoJobResponse]
    total: int


class CrawlerDoubaoJobClaimResponse(BaseModel):
    job: CrawlerDoubaoJobResponse | None = None


class CrawlerDoubaoWorkerStartResponse(BaseModel):
    started: bool
    command: list[str]
    log_path: str
    message: str


class CrawlerDoubaoMobilePrerequisites(BaseModel):
    """手机豆包链路本机前置条件逐项状态。"""

    adb: bool
    appium_url: bool
    package: bool
    device_ready: bool


class CrawlerDoubaoMobileCapabilitiesResponse(BaseModel):
    enabled: bool
    worker_mode: str
    appium_server_url: str
    android_package: str | None = None
    missing_configuration: list[str]
    requirements: list[str]
    message: str
    prerequisites: CrawlerDoubaoMobilePrerequisites | None = None
    estimated_cost_cny: float = 0.0


class CrawlerPlatformRunResponse(BaseModel):
    run_id: str
    platform: str
    platform_label: str
    provider: str
    mode: str
    status: str
    requested_count: int
    returned_count: int
    raw_item_count: int = 0
    parsed_item_count: int = 0
    out_of_window_count: int = 0
    invalid_count: int = 0
    duplicate_count: int = 0
    relevant_count: int = 0
    strict_relevant_count: int = 0
    below_heat_floor_count: int = 0
    irrelevant_count: int = 0
    duration_filtered_count: int = 0
    incremental_play_filtered_count: int = 0
    relevance_rule_version: str | None = None
    result_state: str = "historical_unknown"
    payload_diagnostic: str | None = None
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
    # 仅在热点宝主榜为空时返回：严格相关但新增播放量不超过 1,000 的参考视频。
    low_incremental_candidates: list[CrawlerCandidateResult] = Field(default_factory=list)


class CrawlerBatchResponse(BaseModel):
    batch_id: str
    keyword: str
    published_window_days: int
    hotspot_window_hours: int | None = None
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
    monitoring_policy: str = "manual_tracking_v1"
    sampling_offsets_hours: list[int] = Field(default_factory=lambda: [0])
    free_candidate_count: int = 0
    paid_fallback_used: bool = False
    paid_fallback_blocked_reason: str | None = None
    related_terms: list[str] = Field(default_factory=list)
    related_fallback_used: bool = False
    trend_tracking_enabled: bool = False
    tracking_status: str = "not_started"
    next_tracking_at: datetime | None = None
    crawl_safety: CrawlerSafetyStatus | None = None


class CrawlerTrackingResponse(BaseModel):
    batch: CrawlerBatchResponse
    scheduled_candidates: int
    additional_api_calls: int = 2
    estimated_additional_cost_cny: float | None = None
    next_tracking_at: datetime | None = None
    message: str


class CrawlerBatchListResponse(BaseModel):
    items: list[CrawlerBatchResponse]
    total: int


class CrawlerBatchDeleteResponse(BaseModel):
    batch_id: str
    deleted: bool


class CrawlerDueRecrawlResponse(BaseModel):
    executed_batches: list[CrawlerBatchResponse]
    total: int


def _platform_label(value: str) -> str:
    return PLATFORM_LABELS.get(value, value)


def _official_capability_payload(adapter) -> CrawlerOfficialHotCapability:
    capability = adapter.capabilities()
    return CrawlerOfficialHotCapability(
        enabled=capability.enabled,
        missing_configuration=capability.missing_configuration,
        provider_name=capability.provider_name,
    )


def _official_capability_fallback(
    provider_name: str, flag_name: str
) -> CrawlerOfficialHotCapability:
    """功能开关为 false 时：报 enabled=False，不构造适配器、不调用官方接口。"""
    return CrawlerOfficialHotCapability(
        enabled=False,
        missing_configuration=[flag_name],
        provider_name=provider_name,
    )


def _capability_payload(
    service,
    provider,
    *,
    billboard_adapter=None,
    hot_words_adapter=None,
    hotspot_provider=None,
) -> CrawlerCapabilitiesResponse:
    capability = provider.capabilities()
    active_platforms = list(service.active_platforms)
    paused_platforms = [
        item
        for item in capability.supported_platforms
        if item not in active_platforms
    ]
    # Capability discovery must not call a remote supplier.  This page no
    # longer exposes paid fallback controls, and a usage probe can otherwise
    # delay every page open while also creating unnecessary external traffic.
    usage = None
    hotspot_status = getattr(hotspot_provider, "session_status", lambda: None)()
    hotspot_capability = hotspot_provider.capabilities() if hotspot_provider is not None else None
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
        official_hot_billboard=(
            _official_capability_payload(billboard_adapter)
            if billboard_adapter is not None
            else _official_capability_fallback(
                "douyin_hot_billboard", "DOUYIN_OFFICIAL_HOT_ENABLED"
            )
        ),
        official_hot_words=(
            _official_capability_payload(hot_words_adapter)
            if hot_words_adapter is not None
            else _official_capability_fallback(
                "douyin_hot_words", "DOUYIN_HOT_WORDS_ENABLED"
            )
        ),
        hotspot_browser=(
            CrawlerBrowserDiscoveryCapabilities(
                enabled=hotspot_capability.enabled,
                running=bool(hotspot_status and hotspot_status.running),
                login_required=bool(hotspot_status and hotspot_status.login_required),
                missing_configuration=getattr(hotspot_capability, "missing_configuration", []),
                browser_channel=getattr(hotspot_provider, "browser_channel", "chrome"),
                ready_to_crawl=bool(hotspot_status and hotspot_status.ready_to_crawl),
                phase=(hotspot_status.phase if hotspot_status is not None else "unavailable"),
                adapter_version=getattr(hotspot_provider, "adapter_version", "hotspot_fiber_v2"),
                provider_name=hotspot_capability.provider_name,
                message=hotspot_status.message if hotspot_status is not None else "热点宝本机浏览器不可用。",
            )
            if hotspot_capability is not None
            else None
        ),
    )


@router.get("/capabilities", response_model=CrawlerCapabilitiesResponse)
def get_capabilities(
    service=Depends(get_commercial_search_service),
    provider=Depends(get_discovery_search_provider),
    billboard_adapter=Depends(get_official_hot_billboard_adapter),
    hot_words_adapter=Depends(get_official_hot_words_adapter),
    hotspot_provider=Depends(get_hotspot_browser_provider),
):
    """返回供应商模式、支持平台、缺失配置、缓存和额度状态。"""
    return _capability_payload(
        service,
        provider,
        billboard_adapter=billboard_adapter,
        hot_words_adapter=hot_words_adapter,
        hotspot_provider=hotspot_provider,
    )


@router.get(
    "/browser-discovery/capabilities",
    response_model=CrawlerBrowserDiscoveryCapabilities,
)
def get_browser_discovery_capabilities(
    provider=Depends(get_hotspot_browser_provider),
):
    """返回专用本机 Chrome 的连接/登录状态，不读取任何 Cookie 内容。"""
    capability = provider.capabilities()
    status = getattr(provider, "session_status", lambda: None)()
    return CrawlerBrowserDiscoveryCapabilities(
        enabled=capability.enabled,
        running=bool(status and status.running),
        login_required=bool(status and status.login_required),
        missing_configuration=getattr(capability, "missing_configuration", []),
        browser_channel=getattr(provider, "browser_channel", "chrome"),
        ready_to_crawl=bool(status and status.ready_to_crawl),
        phase=(status.phase if status is not None else "unavailable"),
        adapter_version=getattr(provider, "adapter_version", "hotspot_fiber_v2"),
        provider_name=capability.provider_name,
        message=(
            status.message
            if status is not None
            else "当前发现源不是本机 Chrome；请关闭浏览器发现开关后使用已确认的数据源。"
        ),
    )


@router.post(
    "/browser-discovery/start",
    response_model=CrawlerBrowserDiscoveryStartResponse,
)
def start_browser_discovery_login(
    provider=Depends(get_hotspot_browser_provider),
):
    """打开独立 Chrome 资料目录，让操作者手工登录或处理平台验证。"""
    start = getattr(provider, "start_login_browser", None)
    if start is None:
        raise HTTPException(status_code=409, detail="当前发现源不是本机 Chrome。")
    try:
        previous_status = getattr(provider, "session_status", lambda: None)()
        status = start()
    except LicensedProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CrawlerBrowserDiscoveryStartResponse(
        enabled=status.enabled,
        running=status.running,
        login_required=status.login_required,
        missing_configuration=getattr(provider.capabilities(), "missing_configuration", []),
        browser_channel=getattr(provider, "browser_channel", "chrome"),
        ready_to_crawl=status.ready_to_crawl,
        phase=status.phase,
        adapter_version=getattr(provider, "adapter_version", "hotspot_fiber_v2"),
        provider_name=provider.capabilities().provider_name,
        message=status.message,
        started=not bool(previous_status and previous_status.running),
    )


@router.get("/hotwords", response_model=CrawlerHotWordsResponse)
def get_hot_words(
    service=Depends(get_official_hot_pool_service),
):
    """读取官方实时热点词建议；首次无缓存时尝试同步一次。"""
    words = service.hot_word_suggestions()
    if not words:
        try:
            result = service.sync_hot_words()
        except ValueError as exc:
            # 未配置热点词适配器（开关关闭）：返回空列表与原因，不报错
            return CrawlerHotWordsResponse(words=[], error=str(exc))
        words = result.words
        sync_error = result.error
    else:
        sync_error = None
    return CrawlerHotWordsResponse(
        words=[
            CrawlerHotWordItem(
                word=item.word,
                hot_value=item.hot_value,
                fetched_at=item.fetched_at,
            )
            for item in words
        ],
        error=sync_error,
    )


@router.post(
    "/official-hot/monitor",
    response_model=CrawlerOfficialHotMonitorResponse,
)
def monitor_official_hot_pool(
    body: CrawlerOfficialHotMonitorRequest | None = None,
    service=Depends(get_official_hot_pool_service),
    repo=Depends(get_repository),
):
    """执行官方热榜监测：先跑到期复爬，再同步热榜并按关键词匹配。"""
    keyword = (body.keyword if body else None) or ""
    keyword = keyword.strip()
    try:
        executed_recrawls = service.execute_due_recrawls()
        if not keyword:
            # 无关键词：全量热榜同步 + 热点词同步
            service.sync_billboard(limit=50)
            try:
                hot_words_result = service.sync_hot_words()
                hot_words_note = (
                    f"；同步实时热点词 {hot_words_result.saved_count} 个"
                    if hot_words_result.error is None
                    else f"；热点词同步失败：{hot_words_result.error}"
                )
            except ValueError:
                hot_words_note = "；热点词适配器未配置，已跳过热点词同步"
            pool = repo.list_official_hot_pool(Platform.DOUYIN)[:10]
            return CrawlerOfficialHotMonitorResponse(
                matched_count=len(pool),
                result_state="官方热榜候选",
                result_message=(
                    "未输入关键词，已完成全量热榜同步"
                    f"{hot_words_note}；不会生成关键词复爬计划。"
                ),
                executed_recrawls=len(executed_recrawls),
                candidates=[
                    _candidate_to_response(candidate, repo=repo, platform_rank=index)
                    for index, candidate in enumerate(pool, start=1)
                ],
            )
        result = service.monitor(
            keyword=keyword,
            limit=10,
            publish_time=1,
            related_terms=(body.related_terms if body else None),
        )
    except (ValueError, OfficialAdapterDisabledError, OfficialApiError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trend_by_candidate = {trend.candidate_id: trend for trend in result.trends}
    ordered_ids = [trend.candidate_id for trend in result.trends]
    return CrawlerOfficialHotMonitorResponse(
        matched_count=len(result.matched),
        result_state=result.result_state,
        result_message=(
            result.user_notice
            or (
                f"官方热榜匹配 {len(result.matched)} 条候选；"
                "已保存快照并安排后续复爬。"
            )
        ),
        executed_recrawls=len(executed_recrawls),
        next_recrawl_at=result.next_recrawl_at,
        candidates=[
            _candidate_to_response(
                candidate,
                trend=trend_by_candidate.get(candidate.video_id),
                repo=repo,
                platform_rank=candidate.official_rank or index,
                system_rank=(
                    ordered_ids.index(candidate.video_id) + 1
                    if candidate.video_id in ordered_ids
                    else None
                ),
            )
            for index, candidate in enumerate(result.matched, start=1)
        ],
    )


@router.post(
    "/candidates/{candidate_id}/original-script",
    response_model=CrawlerOriginalScriptResponse,
)
def generate_candidate_original_script(
    candidate_id: str,
    repo=Depends(get_repository),
    service=Depends(get_copywriting_service),
):
    """基于标题/热点词/互动数据生成数字人口播文案。"""
    candidate = repo.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="候选不存在。")
    # 热点词优先取 evidence 中的官方 hot_words，辅以历史匹配关键词
    hot_words = list(
        dict.fromkeys([*_candidate_hot_words(candidate), *candidate.matched_by])
    )
    task = service.generate_metadata_original(
        title=candidate.title,
        hot_words=hot_words or None,
        metrics=candidate.metrics,
        platform=candidate.platform.value,
        target_length=180,
    )
    if task.status == TaskStatus.FAILED:
        raise HTTPException(
            status_code=400,
            detail=task.error_message or "生成文案失败。",
        )
    return CrawlerOriginalScriptResponse(
        is_original_transcript=False,
        needs_manual_review=True,
        script=task.result_text or "",
    )


@router.post("/preview", response_model=CrawlerPreviewResponse)
def preview_crawler_batch(
    body: CrawlerSearchRequest,
    service=Depends(get_commercial_search_service),
    repo=Depends(get_repository),
    provider=Depends(get_discovery_search_provider),
    hot_pool=Depends(get_official_hot_pool_service),
    hotspot_service=Depends(get_hotspot_search_service),
    hotspot_provider=Depends(get_hotspot_browser_provider),
):
    """提交前预览当前启用平台的执行计划。"""
    if body.mode == "smart":
        return _preview_smart_batch(
            body, service, hot_pool, hotspot_provider, hotspot_service, repo
        )
    if body.mode not in (None, "", "official_hot"):
        raise HTTPException(status_code=400, detail=f"不支持的批次模式：{body.mode}")
    try:
        previews = service.preview(
            keyword=body.keyword,
            published_window_days=body.published_window_days,
            count=body.count_per_platform,
            force_refresh=body.force_refresh,
            include_monitoring=body.track_trend,
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
        mode=body.mode,
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
        monitoring_policy=("adaptive_three_sample_v1" if body.track_trend else "manual_tracking_v1"),
        sampling_offsets_hours=[0, 2] if body.track_trend else [0],
        max_api_calls_per_platform=3 if body.track_trend else 1,
        trend_tracking_enabled=body.track_trend,
        blocked=all(item.blocked_reason for item in platform_items),
    )


@router.post("/batches", response_model=CrawlerBatchResponse)
def create_crawler_batch(
    body: CrawlerSearchRequest,
    service=Depends(get_commercial_search_service),
    repo=Depends(get_repository),
    hot_pool=Depends(get_official_hot_pool_service),
    hotspot_service=Depends(get_hotspot_search_service),
    hotspot_provider=Depends(get_hotspot_browser_provider),
):
    """执行当前启用平台的关键词榜单批次并持久化；official_hot 模式走官方热榜池。"""
    if body.mode == "official_hot":
        return _execute_official_hot_batch(body, hot_pool, repo)
    if body.mode == "smart":
        return _execute_smart_batch(body, service, hotspot_service, hotspot_provider, hot_pool, repo)
    if body.mode not in (None, ""):
        raise HTTPException(status_code=400, detail=f"不支持的批次模式：{body.mode}")
    try:
        batch = service.execute(
            keyword=body.keyword,
            published_window_days=body.published_window_days,
            count=body.count_per_platform,
            force_refresh=body.force_refresh,
            schedule_recrawls=body.track_trend,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _batch_to_response(batch, repo)


@router.post("/batches/{batch_id}/tracking", response_model=CrawlerTrackingResponse)
def start_crawler_batch_tracking(
    batch_id: str,
    service=Depends(get_commercial_search_service),
    repo=Depends(get_repository),
):
    """用户明确确认后，才为本批次安排两次真实后续采样。"""
    try:
        batch, scheduled, due_at = service.start_batch_tracking(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    price = service._endpoint_prices().get(Platform.DOUYIN)
    return CrawlerTrackingResponse(
        batch=_batch_to_response(batch, repo),
        scheduled_candidates=scheduled,
        additional_api_calls=2,
        estimated_additional_cost_cny=(round(price * 2, 4) if price is not None else None),
        next_tracking_at=due_at,
        message=(
            f"已为 {scheduled} 条候选安排真实走势追踪；首次将在约 2 小时后采样，"
            "再根据真实变化安排最后一次采样。"
        ),
    )


@router.delete("/batches/{batch_id}/tracking", response_model=CrawlerTrackingResponse)
def cancel_crawler_batch_tracking(
    batch_id: str,
    service=Depends(get_commercial_search_service),
    repo=Depends(get_repository),
):
    batch = repo.get_search_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="搜索批次不存在。")
    cancelled = service.cancel_batch_tracking(batch_id)
    return CrawlerTrackingResponse(
        batch=_batch_to_response(batch, repo),
        scheduled_candidates=0,
        additional_api_calls=0,
        estimated_additional_cost_cny=0.0,
        message=f"已取消 {cancelled} 个尚未执行的走势采样。",
    )


def _related_terms(values: list[str]) -> list[str]:
    """Trim and de-duplicate explicit recall terms; never infer them automatically."""
    terms: list[str] = []
    for value in values:
        term = str(value or "").strip()
        if len(term) < 2 or term in terms:
            continue
        terms.append(term)
        if len(terms) >= 5:
            break
    return terms


def _hotspot_rolling_usage(state, now: datetime) -> tuple[int, datetime | None]:
    if state is None or state.rolling_window_started_at is None:
        return 0, None
    window_ends_at = state.rolling_window_started_at + timedelta(
        seconds=HOTSPOT_ROLLING_WINDOW_SECONDS
    )
    if window_ends_at <= now:
        return 0, None
    return state.real_runs_in_window, window_ends_at


def _hotspot_safety_status(body: CrawlerSearchRequest, hotspot_service, repo) -> CrawlerSafetyStatus:
    """Report cache/cooldown state without opening the browser or changing it."""
    now = datetime.now().astimezone()
    cache_hit = False
    if not body.force_refresh:
        try:
            previews = hotspot_service.preview(
                keyword=body.keyword,
                published_window_days=0,
                hotspot_window_hours=body.hotspot_window_hours,
                count=body.hotspot_result_limit,
                force_refresh=False,
                platforms=(Platform.DOUYIN,),
                cache_ttl_minutes=HOTSPOT_CACHE_TTL_MINUTES,
                include_monitoring=False,
            )
            cache_hit = bool(previews and previews[0].cache_hit)
        except ValueError:
            # The regular capability message is shown by the caller.  Do not make
            # a preview error look like a pacing denial.
            cache_hit = False
    state = repo.get_provider_safety_state(HOTSPOT_PROVIDER_KEY)
    real_runs, window_ends_at = _hotspot_rolling_usage(state, now)
    common = {
        "real_runs_in_window": real_runs,
        "rolling_window_ends_at": window_ends_at,
    }
    if cache_hit:
        return CrawlerSafetyStatus(
            state="cached",
            message="命中 10 分钟本地缓存，可立即查看，不会打开热点宝页面。",
            **common,
        )

    if state and state.blocked_until and state.blocked_until > now:
        remaining = max(1, int((state.blocked_until - now).total_seconds()))
        return CrawlerSafetyStatus(
            state="safety_pause",
            cooldown_remaining_seconds=remaining,
            next_available_at=state.blocked_until,
            message=(state.blocked_reason or "热点宝出现安全验证或访问频繁提示，已暂停真实采集。"),
            **common,
        )
    if state and state.active_run_id and state.lease_expires_at and state.lease_expires_at > now:
        remaining = max(1, int((state.lease_expires_at - now).total_seconds()))
        return CrawlerSafetyStatus(
            state="running",
            cooldown_remaining_seconds=remaining,
            next_available_at=state.lease_expires_at,
            message="已有热点宝采集任务在顺序执行；请等待它结束。",
            **common,
        )
    if real_runs >= HOTSPOT_MAX_REAL_RUNS_PER_WINDOW:
        remaining = max(1, int((window_ends_at - now).total_seconds())) if window_ends_at else 1
        return CrawlerSafetyStatus(
            state="daily_limit",
            cooldown_remaining_seconds=remaining,
            next_available_at=window_ends_at,
            message=(
                "为降低账号风险，滚动 24 小时的真实热点宝采集已达到 48 次上限；"
                "缓存结果仍可立即查看。"
            ),
            **common,
        )
    if state and state.next_allowed_at and state.next_allowed_at > now:
        remaining = max(1, int((state.next_allowed_at - now).total_seconds()))
        return CrawlerSafetyStatus(
            state="cooldown",
            cooldown_remaining_seconds=remaining,
            next_available_at=state.next_allowed_at,
            message="真实采集完成后会随机冷却 8–12 分钟；本次按已生成的截止时间倒计时，缓存结果不受影响。",
            **common,
        )
    return CrawlerSafetyStatus(
        state="ready",
        message=(
            "安全优先：预计约 2–3 分钟，五个榜单顺序采集；同关键词 10 分钟缓存，"
            "完成后随机冷却 8–12 分钟，滚动 24 小时最多 48 次真实采集。"
        ),
        **common,
    )


def _hotspot_safety_exception(status: CrawlerSafetyStatus) -> HTTPException:
    retry_after = max(1, status.cooldown_remaining_seconds)
    return HTTPException(
        status_code=429,
        detail={
            "code": f"hotspot_{status.state}",
            "message": status.message,
            "retry_after_seconds": retry_after,
            "next_available_at": status.next_available_at.isoformat() if status.next_available_at else None,
        },
        headers={"Retry-After": str(retry_after)},
    )


def _preview_smart_batch(body: CrawlerSearchRequest, service, hot_pool, hotspot_provider, hotspot_service, repo):
    """Preview Hotspot-first discovery; paid fallback always needs opt-in."""
    discovery_capability = service.provider.capabilities()
    hotspot_capability = hotspot_provider.capabilities()
    hotspot_status = getattr(hotspot_provider, "session_status", lambda: None)()
    hotspot_ready = bool(
        hotspot_capability.enabled
        and hotspot_status
        and hotspot_status.ready_to_crawl
    )
    if hotspot_ready:
        hotspot_window_label = _hotspot_window_label(body.hotspot_window_hours)
        crawl_safety = _hotspot_safety_status(body, hotspot_service, repo)
        blocked = crawl_safety.state in {"safety_pause", "cooldown", "running", "daily_limit"}
        return CrawlerPreviewResponse(
            keyword=body.keyword.strip(),
            published_window_days=0,
            hotspot_window_hours=body.hotspot_window_hours,
            count_per_platform=body.hotspot_result_limit,
            force_refresh=body.force_refresh,
            mode="smart",
            provider_mode="local_browser",
            provider_name=hotspot_capability.provider_name,
            ranking_mode="hotspot_new_plays_desc",
            monthly_query_count=service.monthly_query_count(),
            monthly_estimated_cost_cny=service.monthly_query_cost(),
            monthly_warning_queries=MONTHLY_WARNING_QUERIES,
            monthly_hard_limit_queries=MONTHLY_HARD_LIMIT_QUERIES,
            monthly_hard_limit_cost_cny=MONTHLY_HARD_LIMIT_COST_CNY,
            cache_ttl_minutes=HOTSPOT_CACHE_TTL_MINUTES,
            platforms=[
                CrawlerPlatformPreview(
                    platform="douyin_hotspot",
                    platform_label=f"热点宝{hotspot_window_label}五类爆款榜（本机授权）",
                    cache_hit=crawl_safety.state == "cached",
                    estimated_api_calls=0,
                    platform_unit_price_cny=0.0,
                    estimated_cost_cny=0.0,
                    blocked_reason=crawl_safety.message if blocked else None,
                )
            ],
            estimated_total_cost_cny=0.0,
            monitoring_policy="hotspot_single_snapshot_v1",
            sampling_offsets_hours=[0],
            max_api_calls_per_platform=0,
            blocked=blocked,
            free_candidate_count=0,
            free_pool_status="not_used",
            free_pool_message=f"热点宝已授权：本次扫描{hotspot_window_label}的五类榜单。",
            paid_fallback_required=False,
            paid_fallback_blocked_reason="本次热点宝采集不自动调用 OneAPI，也不安排复爬。",
            trend_tracking_enabled=False,
            hotspot_ready=True,
            hotspot_message=(
                f"将扫描{hotspot_window_label}的视频总榜、低粉爆款、高完播率、高涨粉率和高点赞率，"
                "按所选周期新增播放量排序。"
            ),
            hotspot_time_strategy="selectable_1h_24h_72h_168h",
            target_main_count=body.hotspot_result_limit,
            paid_call_cap=0,
            hotspot_result_limit=body.hotspot_result_limit,
            hotspot_list_types=["视频总榜", "低粉爆款", "高完播率", "高涨粉率", "高点赞率"],
            crawl_safety=crawl_safety,
        )
    free_count = 0
    billboard = getattr(hot_pool, "billboard_adapter", None)
    billboard_capability = billboard.capabilities() if billboard is not None else None
    free_pool_ready = bool(billboard_capability and billboard_capability.enabled)
    free_pool_status = "ready" if free_pool_ready else "unavailable"
    free_pool_message = (
        "官方热榜已配置，将先在免费池匹配。"
        if free_pool_ready
        else "官方热榜未配置；将优先使用本机已授权的热点宝。"
    )
    if free_pool_ready:
        try:
            free_result = hot_pool.search_hot_pool(
                keyword=body.keyword,
                limit=body.count_per_platform,
                record=False,
                related_terms=[],
            )
            free_count = len(free_result.matched)
            if free_count == 0:
                free_pool_status = "empty"
                free_pool_message = "官方热榜已配置，但当前没有匹配候选。"
        except (ValueError, OfficialAdapterDisabledError, OfficialApiError):
            free_pool_status = "unavailable"
            free_pool_message = "官方热榜暂不可用；将优先使用本机已授权的热点宝。"

    fallback_required = free_count < body.target_main_count
    paid_fallback_requested = fallback_required and body.allow_paid_fallback
    previews = []
    if paid_fallback_requested:
        try:
            previews = service.preview(
                keyword=body.keyword,
                published_window_days=body.published_window_days,
                count=body.count_per_platform,
                force_refresh=False,
                platforms=(Platform.DOUYIN,),
                cache_ttl_minutes=SMART_FALLBACK_CACHE_TTL_MINUTES,
                include_monitoring=body.track_trend,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    platform_items = [
        CrawlerPlatformPreview(
            platform="douyin_hotspot",
            platform_label="热点宝爆款榜（本机授权）",
            cache_hit=False,
            estimated_api_calls=0,
            platform_unit_price_cny=0.0,
            estimated_cost_cny=0.0,
            blocked_reason=None if hotspot_ready else (hotspot_status.message if hotspot_status else "请先连接热点宝专用浏览器。"),
        ),
        *[
        CrawlerPlatformPreview(
            platform=item.platform.value,
            platform_label=_platform_label(item.platform.value),
            cache_hit=item.cache_hit,
            estimated_api_calls=(body.max_paid_calls if not item.cache_hit else 0),
            platform_unit_price_cny=item.platform_unit_price_cny,
            estimated_cost_cny=(
                round((item.estimated_cost_cny or 0) * body.max_paid_calls, 4)
                if not item.cache_hit else 0.0
            ),
            blocked_reason=item.blocked_reason,
        )
        for item in previews
        ],
        *(
            [
                CrawlerPlatformPreview(
                    platform="douyin_paid_fallback",
                    platform_label="OneAPI 付费补充（默认关闭）",
                    cache_hit=False,
                    estimated_api_calls=0,
                    platform_unit_price_cny=0.0,
                    estimated_cost_cny=0.0,
                    blocked_reason="热点宝不足 10 条时将如实展示；如需补充，请点击“付费补充”并再次确认。",
                )
            ]
            if fallback_required and not body.allow_paid_fallback
            else []
        ),
    ]
    fallback_blocked = next(
        (item.blocked_reason for item in platform_items if item.blocked_reason),
        None,
    )
    estimated_total_cost = round(
        sum(item.estimated_cost_cny or 0.0 for item in platform_items), 4
    )
    return CrawlerPreviewResponse(
        keyword=body.keyword.strip(),
        published_window_days=body.published_window_days,
        count_per_platform=body.count_per_platform,
        force_refresh=body.force_refresh,
        mode="smart",
        provider_mode=discovery_capability.mode.value,
        provider_name=f"热点宝 + official_hot_pool + {discovery_capability.provider_name}",
        ranking_mode=RANKING_MODE,
        monthly_query_count=service.monthly_query_count(),
        monthly_estimated_cost_cny=service.monthly_query_cost(),
        monthly_warning_queries=MONTHLY_WARNING_QUERIES,
        monthly_hard_limit_queries=MONTHLY_HARD_LIMIT_QUERIES,
        monthly_hard_limit_cost_cny=MONTHLY_HARD_LIMIT_COST_CNY,
        cache_ttl_minutes=SMART_FALLBACK_CACHE_TTL_MINUTES,
        platforms=platform_items,
        estimated_total_cost_cny=estimated_total_cost,
        monitoring_policy=(
            "official_pool_first_then_adaptive_three_sample_v1"
            if body.track_trend
            else "official_pool_first_manual_tracking_v1"
        ),
        sampling_offsets_hours=[0, 2] if body.track_trend else [0],
        max_api_calls_per_platform=body.max_paid_calls,
        blocked=bool(not hotspot_ready and fallback_required and free_count == 0),
        free_candidate_count=free_count,
        free_pool_status=free_pool_status,
        free_pool_message=free_pool_message,
        paid_fallback_required=paid_fallback_requested and discovery_capability.mode.value == "production",
        paid_fallback_cache_ttl_minutes=(
            SMART_FALLBACK_CACHE_TTL_MINUTES
            if paid_fallback_requested and discovery_capability.mode.value == "production"
            else None
        ),
        paid_fallback_blocked_reason=(
            fallback_blocked
            if paid_fallback_requested
            else (
                "默认不自动调用 OneAPI；热点宝不足时会如实显示结果数量。"
                if fallback_required
                else None
            )
        ),
        trend_tracking_enabled=body.track_trend,
        hotspot_ready=hotspot_ready,
        hotspot_message=(
            f"热点宝已连接：将读取{_hotspot_window_label(body.hotspot_window_hours)}并采集五类爆款榜。"
            if hotspot_ready
            else (hotspot_status.message if hotspot_status else "请先连接热点宝专用浏览器。")
        ),
        target_main_count=body.target_main_count,
        paid_call_cap=body.max_paid_calls,
    )


def _execute_official_hot_batch(
    body: CrawlerSearchRequest,
    hot_pool,
    repo,
) -> CrawlerBatchResponse:
    """官方热榜模式批量：同步热榜 → 本地匹配 → 排复爬 → 返回合成批次载荷。"""
    publish_time = (
        body.published_window_days if body.published_window_days in (1, 7) else 1
    )
    try:
        result = hot_pool.monitor(
            keyword=body.keyword,
            limit=body.count_per_platform,
            publish_time=publish_time,
            related_terms=_related_terms(body.related_terms),
        )
    except (ValueError, OfficialAdapterDisabledError, OfficialApiError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trend_by_candidate = {trend.candidate_id: trend for trend in result.trends}
    ordered_ids = [trend.candidate_id for trend in result.trends]
    candidates = [
        _candidate_to_response(
            candidate,
            repo=repo,
            trend=trend_by_candidate.get(candidate.video_id),
            platform_rank=candidate.official_rank or index,
            provider_hot_rank=candidate.official_rank or index,
            system_rank=(
                ordered_ids.index(candidate.video_id) + 1
                if candidate.video_id in ordered_ids
                else None
            ),
            relevance_basis=(
                "related_term"
                if result.match_reasons.get(candidate.video_id, "").startswith("相关")
                else "primary_keyword"
            ),
            relevance_reason=result.match_reasons.get(candidate.video_id),
        )
        for index, candidate in enumerate(result.matched, start=1)
    ]
    now = datetime.now().astimezone()
    run = CrawlerPlatformRunResponse(
        run_id=result.request_id,
        platform="douyin",
        platform_label=_platform_label("douyin"),
        provider=result.provider_name,
        mode="official_hot",
        status="succeeded",
        requested_count=body.count_per_platform,
        returned_count=len(result.matched),
        raw_item_count=result.pool_size,
        parsed_item_count=result.pool_size,
        relevant_count=len(result.matched),
        relevance_rule_version="official_hot_pool_title_or_hot_word",
        result_state=result.result_state,
        payload_diagnostic=result.user_notice,
        cache_hit=False,
        cached_from_run_id=None,
        api_call_count=0,
        started_at=now,
        finished_at=now,
        candidates=candidates,
    )
    return CrawlerBatchResponse(
        batch_id=result.request_id,
        keyword=result.keyword,
        published_window_days=body.published_window_days,
        count_per_platform=body.count_per_platform,
        provider=result.provider_name,
        mode="official_hot",
        status="succeeded",
        force_refresh=body.force_refresh,
        created_at=now,
        finished_at=now,
        platform_runs=[run],
        total_api_calls=0,
        total_candidates=len(result.matched),
        total_estimated_cost_cny=0.0,
        free_candidate_count=len(result.matched),
        related_terms=_related_terms(body.related_terms),
    )


def _deduplicate_smart_runs(runs: list[CrawlerPlatformRunResponse]) -> list[CrawlerPlatformRunResponse]:
    """Keep the first source's richer evidence for each Douyin work."""
    seen_ids: set[str] = set()
    deduplicated_runs: list[CrawlerPlatformRunResponse] = []
    for run in runs:
        candidates = [
            candidate
            for candidate in run.candidates
            if not (candidate.video_id in seen_ids or seen_ids.add(candidate.video_id))
        ]
        deduplicated_runs.append(
            run.model_copy(
                update={
                    "candidates": candidates,
                    "returned_count": len(candidates),
                    "relevant_count": len(candidates),
                }
            )
        )
    return deduplicated_runs


def _execute_hotspot_single_snapshot_batch(
    body: CrawlerSearchRequest,
    hotspot_service,
    repo,
) -> CrawlerBatchResponse:
    """Run one selected-period, five-list Hotspot flow without trend checkpoints."""
    now = datetime.now().astimezone()
    safety_before = _hotspot_safety_status(body, hotspot_service, repo)
    if safety_before.state in {"safety_pause", "cooldown", "running", "daily_limit"}:
        raise _hotspot_safety_exception(safety_before)

    lease_id: str | None = None
    if safety_before.state != "cached":
        lease_id = f"hotspot-{uuid4().hex}"
        if not repo.claim_provider_safety_lease(
            provider=HOTSPOT_PROVIDER_KEY,
            run_id=lease_id,
            now=now,
            lease_seconds=HOTSPOT_LEASE_SECONDS,
            max_runs_in_window=HOTSPOT_MAX_REAL_RUNS_PER_WINDOW,
            rolling_window_seconds=HOTSPOT_ROLLING_WINDOW_SECONDS,
        ):
            raise _hotspot_safety_exception(
                _hotspot_safety_status(body, hotspot_service, repo)
            )

    response: CrawlerBatchResponse
    safety_after: CrawlerSafetyStatus | None = None
    cooldown_seconds: int | None = None
    try:
        batch = hotspot_service.execute(
            keyword=body.keyword,
            published_window_days=0,
            hotspot_window_hours=body.hotspot_window_hours,
            count=body.hotspot_result_limit,
            force_refresh=body.force_refresh,
            platforms=(Platform.DOUYIN,),
            cache_ttl_minutes=HOTSPOT_CACHE_TTL_MINUTES,
            schedule_recrawls=False,
        )
    except ValueError as exc:
        response = CrawlerBatchResponse(
            batch_id=f"hotspot-{int(now.timestamp())}",
            keyword=body.keyword.strip(),
            published_window_days=0,
            hotspot_window_hours=body.hotspot_window_hours,
            count_per_platform=body.hotspot_result_limit,
            provider="douyin_local_browser",
            mode="smart",
            status="failed",
            force_refresh=body.force_refresh,
            created_at=now,
            finished_at=now,
            error=str(exc),
            monitoring_policy="hotspot_single_snapshot_v1",
        )
    else:
        batch = batch.model_copy(
            update={
                "monitoring_policy": "hotspot_single_snapshot_v1",
                "sampling_offsets_hours": [0],
                "tracking_authorized": False,
            }
        )
        repo.save_search_batch(batch)
        response = _batch_to_response(batch, repo)
    finally:
        if lease_id is not None:
            cooldown_seconds = _next_hotspot_cooldown_seconds()
            error_text = " ".join(
                part
                for part in [
                    response.error if "response" in locals() else "",
                    *(
                        run.error or ""
                        for run in (response.platform_runs if "response" in locals() else [])
                    ),
                ]
                if part
            )
            is_safety_event = any(
                marker in error_text
                for marker in ("安全验证", "访问频繁", "操作频繁", "请求过于频繁", "返回 403", "返回 429")
            )
            released = repo.release_provider_safety_lease(
                provider=HOTSPOT_PROVIDER_KEY,
                run_id=lease_id,
                now=datetime.now().astimezone(),
                cooldown_seconds=cooldown_seconds,
                safety_pause_seconds=HOTSPOT_SAFETY_PAUSE_SECONDS if is_safety_event else 0,
                safety_reason=(
                    "热点宝出现安全验证或访问频繁提示，已自动暂停真实采集 60 分钟。"
                    if is_safety_event
                    else None
                ),
            )
            real_runs, window_ends_at = _hotspot_rolling_usage(
                released, datetime.now().astimezone()
            )
            daily_limit_reached = real_runs >= HOTSPOT_MAX_REAL_RUNS_PER_WINDOW
            safety_after = CrawlerSafetyStatus(
                state=(
                    "safety_pause"
                    if is_safety_event
                    else "daily_limit"
                    if daily_limit_reached
                    else "cooldown"
                ),
                cooldown_remaining_seconds=(
                    HOTSPOT_SAFETY_PAUSE_SECONDS
                    if is_safety_event
                    else max(1, int((window_ends_at - datetime.now().astimezone()).total_seconds()))
                    if daily_limit_reached and window_ends_at
                    else cooldown_seconds
                ),
                next_available_at=(
                    released.blocked_until
                    if is_safety_event
                    else window_ends_at
                    if daily_limit_reached
                    else released.next_allowed_at
                ),
                real_runs_in_window=real_runs,
                rolling_window_ends_at=window_ends_at,
                message=(
                    released.blocked_reason
                    if is_safety_event
                    else "滚动 24 小时的真实热点宝采集已达到 48 次上限；缓存结果仍可立即查看。"
                    if daily_limit_reached
                    else (
                        "本次真实采集已完成；"
                        f"下次真实采集约在 {(cooldown_seconds + 59) // 60} 分钟后开放。"
                    )
                ) or "热点宝安全状态已更新。",
            )
        else:
            current_state = repo.get_provider_safety_state(HOTSPOT_PROVIDER_KEY)
            real_runs, window_ends_at = _hotspot_rolling_usage(
                current_state, datetime.now().astimezone()
            )
            safety_after = CrawlerSafetyStatus(
                state="cached",
                message="本次返回 10 分钟本地缓存，未打开热点宝页面，也不会进入冷却。",
                real_runs_in_window=real_runs,
                rolling_window_ends_at=window_ends_at,
            )
    return response.model_copy(
        update={
            "monitoring_policy": "hotspot_single_snapshot_v1",
            "trend_tracking_enabled": False,
            "free_candidate_count": response.total_candidates,
            "hotspot_window_hours": body.hotspot_window_hours,
            "paid_fallback_blocked_reason": (
                f"热点宝{_hotspot_window_label(body.hotspot_window_hours)}五榜单次采集，"
                "不自动调用 OneAPI，也不安排复爬。"
            ),
            "crawl_safety": safety_after,
        }
    )


def _execute_smart_batch(body: CrawlerSearchRequest, service, hotspot_service, hotspot_provider, hot_pool, repo):
    """Hotspot-first discovery; OneAPI is only a capped fallback after free sources."""
    hotspot_status = getattr(hotspot_provider, "session_status", lambda: None)()
    if (
        hotspot_provider.capabilities().enabled
        and hotspot_status
        and hotspot_status.ready_to_crawl
    ):
        return _execute_hotspot_single_snapshot_batch(body, hotspot_service, repo)
    terms = _related_terms(body.related_terms)
    publish_time = body.published_window_days
    free_result = None
    free_error: str | None = None
    try:
        free_result = hot_pool.monitor(
            keyword=body.keyword,
            limit=body.count_per_platform,
            publish_time=publish_time,
            related_terms=terms,
        )
    except (ValueError, OfficialAdapterDisabledError, OfficialApiError) as exc:
        free_error = str(exc)

    now = datetime.now().astimezone()
    free_run = (
        _official_result_to_run(body, free_result, repo, now=now)
        if free_result is not None
        else None
    )
    free_count = len(free_result.matched) if free_result is not None else 0
    if free_count >= body.target_main_count:
        return CrawlerBatchResponse(
            batch_id=free_result.request_id,
            keyword=free_result.keyword,
            published_window_days=body.published_window_days,
            count_per_platform=body.count_per_platform,
            provider=free_result.provider_name,
            mode="smart",
            status="succeeded",
            force_refresh=body.force_refresh,
            created_at=now,
            finished_at=now,
            platform_runs=[free_run],
            total_api_calls=0,
            total_candidates=free_count,
            total_estimated_cost_cny=0.0,
            monitoring_policy=(
                "official_pool_first_then_adaptive_three_sample_v1"
                if body.track_trend
                else "official_pool_first_manual_tracking_v1"
            ),
            free_candidate_count=free_count,
            related_terms=terms,
            trend_tracking_enabled=body.track_trend,
        )

    hotspot_response: CrawlerBatchResponse | None = None
    hotspot_error: str | None = None
    hotspot_status = getattr(hotspot_provider, "session_status", lambda: None)()
    if hotspot_provider.capabilities().enabled and hotspot_status and hotspot_status.running:
        try:
            hotspot_batch = hotspot_service.execute(
                keyword=body.keyword,
                published_window_days=0,
                count=body.target_main_count,
                force_refresh=body.force_refresh,
                platforms=(Platform.DOUYIN,),
                cache_ttl_minutes=15,
                schedule_recrawls=body.track_trend,
            )
            hotspot_response = _batch_to_response(hotspot_batch, repo)
        except ValueError as exc:
            hotspot_error = str(exc)
    else:
        hotspot_error = hotspot_status.message if hotspot_status is not None else "热点宝专用浏览器未连接。"

    hotspot_count = (
        sum(run.relevant_count for run in hotspot_response.platform_runs)
        if hotspot_response is not None
        else 0
    )
    if hotspot_response is not None and (
        free_count + hotspot_count >= body.target_main_count or not body.allow_paid_fallback
    ):
        runs = [run for run in [free_run] if run is not None]
        runs.extend(hotspot_response.platform_runs)
        deduplicated = _deduplicate_smart_runs(runs)
        candidate_count = sum(run.relevant_count for run in deduplicated)
        return CrawlerBatchResponse(
            batch_id=hotspot_response.batch_id,
            keyword=body.keyword.strip(),
            published_window_days=0,
            count_per_platform=body.target_main_count,
            provider="douyin_local_browser",
            mode="smart",
            status="succeeded" if candidate_count >= body.target_main_count else "partial",
            force_refresh=body.force_refresh,
            created_at=hotspot_response.created_at,
            finished_at=hotspot_response.finished_at,
            platform_runs=deduplicated,
            total_api_calls=0,
            total_candidates=candidate_count,
            total_estimated_cost_cny=0.0,
            monitoring_policy="hotspot_first_manual_tracking_v1",
            free_candidate_count=sum(run.relevant_count for run in deduplicated),
            paid_fallback_blocked_reason=(
                None
                if candidate_count >= body.target_main_count
                else "热点宝严格相关结果不足 10 条，已停止；未自动调用 OneAPI。"
            ),
            related_terms=terms,
            trend_tracking_enabled=body.track_trend,
        )

    if not body.allow_paid_fallback:
        runs = [run for run in [free_run] if run is not None]
        return CrawlerBatchResponse(
            batch_id=(free_result.request_id if free_result else f"hotspot-{int(now.timestamp())}"),
            keyword=body.keyword.strip(),
            published_window_days=0,
            count_per_platform=body.target_main_count,
            provider="douyin_local_browser",
            mode="smart",
            status="partial" if runs else "failed",
            force_refresh=body.force_refresh,
            created_at=now,
            finished_at=now,
            error=hotspot_error or free_error,
            platform_runs=runs,
            total_api_calls=0,
            total_candidates=sum(run.relevant_count for run in runs),
            total_estimated_cost_cny=0.0,
            monitoring_policy="hotspot_first_manual_tracking_v1",
            free_candidate_count=sum(run.relevant_count for run in runs),
            paid_fallback_blocked_reason="未自动调用 OneAPI；请先连接并授权热点宝。",
            related_terms=terms,
            trend_tracking_enabled=body.track_trend,
        )

    try:
        paid_batch = service.execute(
            keyword=body.keyword,
            published_window_days=body.published_window_days,
            count=body.count_per_platform,
            force_refresh=False,
            platforms=(Platform.DOUYIN,),
            cache_ttl_minutes=SMART_FALLBACK_CACHE_TTL_MINUTES,
            schedule_recrawls=body.track_trend,
        )
    except ValueError as exc:
        runs = [free_run] if free_run else []
        return CrawlerBatchResponse(
            batch_id=(free_result.request_id if free_result else f"smart-{int(now.timestamp())}"),
            keyword=body.keyword.strip(),
            published_window_days=body.published_window_days,
            count_per_platform=body.count_per_platform,
            provider=(free_result.provider_name if free_result else "smart_discovery"),
            mode="smart",
            status="partial" if runs else "failed",
            force_refresh=body.force_refresh,
            created_at=now,
            finished_at=now,
            error=str(exc),
            platform_runs=runs,
            total_api_calls=0,
            total_candidates=free_count,
            total_estimated_cost_cny=0.0,
            monitoring_policy=(
                "official_pool_first_then_adaptive_three_sample_v1"
                if body.track_trend
                else "official_pool_first_manual_tracking_v1"
            ),
            free_candidate_count=free_count,
            paid_fallback_blocked_reason=str(exc),
            related_terms=terms,
            trend_tracking_enabled=body.track_trend,
        )

    paid_response = _batch_to_response(paid_batch, repo)
    related_response: CrawlerBatchResponse | None = None
    primary_relevant_count = sum(
        run.relevant_count for run in paid_response.platform_runs
    )
    if (
        primary_relevant_count + free_count + hotspot_count < body.target_main_count
        and body.allow_related_fallback
        and terms
    ):
        try:
            related_batch = service.execute(
                keyword=terms[0],
                published_window_days=body.published_window_days,
                count=body.count_per_platform,
                force_refresh=False,
                platforms=(Platform.DOUYIN,),
                cache_ttl_minutes=SMART_FALLBACK_CACHE_TTL_MINUTES,
                # 关联词只用于扩充候选，趋势跟踪由主关键词批次承担。
                schedule_recrawls=False,
            )
            related_response = _batch_to_response(related_batch, repo)
            related_response = related_response.model_copy(
                update={
                    "platform_runs": [
                        run.model_copy(
                            update={
                                "platform_label": f"{run.platform_label}（关联词：{terms[0]}）",
                                "candidates": [
                                    candidate.model_copy(
                                        update={
                                            "relevance_basis": "related_term",
                                            "relevance_reason": f"关联词“{terms[0]}”命中",
                                        }
                                    )
                                    for candidate in run.candidates
                                ],
                            }
                        )
                        for run in related_response.platform_runs
                    ]
                }
            )
        except ValueError:
            # 主关键词结果仍然可用；扩充检索失败不应抹掉已确认的候选。
            related_response = None
    runs = [free_run] if free_run else []
    if hotspot_response is not None:
        runs.extend(hotspot_response.platform_runs)
    runs.extend(paid_response.platform_runs)
    if related_response is not None:
        runs.extend(related_response.platform_runs)
    deduplicated_runs = _deduplicate_smart_runs(runs)
    return CrawlerBatchResponse(
        batch_id=paid_response.batch_id,
        keyword=body.keyword.strip(),
        published_window_days=body.published_window_days,
        count_per_platform=body.count_per_platform,
        provider=paid_response.provider,
        mode="smart",
        status=paid_response.status,
        force_refresh=body.force_refresh,
        created_at=paid_response.created_at,
        finished_at=paid_response.finished_at,
        error=paid_response.error or hotspot_error or free_error,
        platform_runs=deduplicated_runs,
        total_api_calls=sum(run.api_call_count for run in deduplicated_runs),
        total_candidates=sum(run.relevant_count for run in deduplicated_runs),
        total_estimated_cost_cny=round(
            sum(run.billable_units or 0.0 for run in deduplicated_runs), 4
        ),
        monitoring_policy=(
            "official_pool_first_then_adaptive_three_sample_v1"
            if body.track_trend
            else "official_pool_first_manual_tracking_v1"
        ),
        free_candidate_count=free_count + hotspot_count,
        paid_fallback_used=paid_response.total_estimated_cost_cny > 0,
        related_terms=terms,
        related_fallback_used=related_response is not None,
        trend_tracking_enabled=body.track_trend,
    )


def _official_result_to_run(body: CrawlerSearchRequest, result, repo, *, now):
    trend_by_candidate = {trend.candidate_id: trend for trend in result.trends}
    ordered_ids = [trend.candidate_id for trend in result.trends]
    candidates = [
        _candidate_to_response(
            candidate,
            repo=repo,
            trend=trend_by_candidate.get(candidate.video_id),
            platform_rank=candidate.official_rank or index,
            provider_hot_rank=candidate.official_rank or index,
            system_rank=(
                ordered_ids.index(candidate.video_id) + 1
                if candidate.video_id in ordered_ids
                else None
            ),
            relevance_basis=(
                "related_term"
                if result.match_reasons.get(candidate.video_id, "").startswith("相关")
                else "primary_keyword"
            ),
            relevance_reason=result.match_reasons.get(candidate.video_id),
        )
        for index, candidate in enumerate(result.matched, start=1)
    ]
    return CrawlerPlatformRunResponse(
        run_id=result.request_id,
        platform="douyin",
        platform_label=_platform_label("douyin"),
        provider=result.provider_name,
        mode="official_hot",
        status="succeeded",
        requested_count=body.count_per_platform,
        returned_count=len(candidates),
        raw_item_count=result.pool_size,
        parsed_item_count=result.pool_size,
        relevant_count=len(candidates),
        relevance_rule_version="official_hot_pool_title_hot_word_or_related_term",
        result_state=result.result_state,
        payload_diagnostic=result.user_notice,
        cache_hit=False,
        cached_from_run_id=None,
        api_call_count=0,
        started_at=now,
        finished_at=now,
        candidates=candidates,
    )


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


@router.delete("/batches/{batch_id}", response_model=CrawlerBatchDeleteResponse)
def delete_crawler_batch(
    batch_id: str,
    repo=Depends(get_repository),
):
    """删除历史搜索批次及其关联的运行记录，保留可复用的候选数据。"""
    if not repo.delete_search_batch(batch_id):
        raise HTTPException(status_code=404, detail="搜索批次不存在或已删除。")
    return CrawlerBatchDeleteResponse(batch_id=batch_id, deleted=True)


@router.post("/recrawls/due", response_model=CrawlerDueRecrawlResponse)
def execute_due_recrawls(
    limit: int = 5,
    service=Depends(get_commercial_search_service),
    repo=Depends(get_repository),
):
    """执行不限发布时间关键词的到期采样，仍走预算、缓存和重复请求保护。"""
    try:
        batches = service.execute_due_recrawls(
            max_groups=max(1, min(limit, 10)),
            published_window_days=0,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CrawlerDueRecrawlResponse(
        executed_batches=[_batch_to_response(batch, repo) for batch in batches],
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


@router.post(
    "/doubao-browser/jobs",
    response_model=CrawlerDoubaoJobListResponse,
)
def create_doubao_browser_jobs(
    body: CrawlerDoubaoJobCreateRequest,
    repo=Depends(get_repository),
    service=Depends(get_doubao_browser_service),
):
    """为候选创建零成本豆包浏览器文案提取任务，不调用付费媒体解析。"""
    jobs = []
    seen: set[str] = set()
    for candidate_id in body.candidate_ids:
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        candidate = repo.get_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail=f"候选不存在：{candidate_id}")
        try:
            jobs.append(service.create_job(candidate))
        except DoubaoBrowserAutomationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.user_message) from exc
    return CrawlerDoubaoJobListResponse(
        items=[_doubao_job_to_response(job) for job in jobs],
        total=len(jobs),
    )


@router.get(
    "/doubao-browser/jobs",
    response_model=CrawlerDoubaoJobListResponse,
)
def list_doubao_browser_jobs(
    candidate_id: str | None = None,
    limit: int = 50,
    service=Depends(get_doubao_browser_service),
):
    jobs = service.list_jobs(candidate_id=candidate_id, limit=limit)
    return CrawlerDoubaoJobListResponse(
        items=[_doubao_job_to_response(job) for job in jobs],
        total=len(jobs),
    )


@router.post(
    "/doubao-browser/jobs/claim",
    response_model=CrawlerDoubaoJobClaimResponse,
)
def claim_doubao_browser_job(
    worker_id: str = "local-browser-worker",
    service=Depends(get_doubao_browser_service),
):
    job = service.claim_next_job(worker_id)
    return CrawlerDoubaoJobClaimResponse(
        job=_doubao_job_to_response(job) if job else None
    )


@router.post(
    "/doubao-browser/jobs/{task_id}/stage",
    response_model=CrawlerDoubaoJobResponse,
)
def update_doubao_browser_job_stage(
    task_id: str,
    body: CrawlerDoubaoJobStageRequest,
    service=Depends(get_doubao_browser_service),
):
    try:
        job = service.mark_stage(
            task_id,
            stage=body.stage,
            progress=body.progress,
            outputs=body.outputs,
        )
    except DoubaoBrowserAutomationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.user_message) from exc
    return _doubao_job_to_response(job)


@router.post(
    "/doubao-browser/jobs/{task_id}/complete",
    response_model=CrawlerDoubaoJobResponse,
)
def complete_doubao_browser_job(
    task_id: str,
    body: CrawlerDoubaoJobCompleteRequest,
    service=Depends(get_doubao_browser_service),
):
    try:
        job = service.complete_job(
            task_id,
            transcript_text=body.transcript_text,
            short_url=body.short_url,
            doubao_conversation_url=body.doubao_conversation_url,
            doubao_message_id=body.doubao_message_id,
        )
    except (DoubaoBrowserAutomationError, TranscriptionError) as exc:
        status_code = getattr(exc, "status_code", 400)
        detail = getattr(exc, "user_message", str(exc))
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return _doubao_job_to_response(job)


@router.post(
    "/doubao-browser/jobs/{task_id}/fail",
    response_model=CrawlerDoubaoJobResponse,
)
def fail_doubao_browser_job(
    task_id: str,
    body: CrawlerDoubaoJobFailRequest,
    service=Depends(get_doubao_browser_service),
):
    try:
        job = service.fail_job(
            task_id,
            error_message=body.error_message,
            stage=body.stage,
            retryable=body.retryable,
        )
    except DoubaoBrowserAutomationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.user_message) from exc
    return _doubao_job_to_response(job)


@router.post(
    "/doubao-browser/jobs/{task_id}/retry",
    response_model=CrawlerDoubaoJobResponse,
)
def retry_doubao_browser_job(
    task_id: str,
    service=Depends(get_doubao_browser_service),
):
    try:
        job = service.requeue_job(task_id)
    except DoubaoBrowserAutomationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.user_message) from exc
    return _doubao_job_to_response(job)


@router.post(
    "/doubao-browser/worker/start",
    response_model=CrawlerDoubaoWorkerStartResponse,
)
def start_doubao_browser_worker():
    """启动本机专用 Chrome profile 执行器。用户点击该接口即授权打开浏览器。"""
    project_root = Path(__file__).resolve().parents[5]
    script = project_root / "scripts" / "doubao_browser_worker.mjs"
    if not script.exists():
        raise HTTPException(status_code=500, detail="本地豆包执行器脚本不存在。")
    log_dir = project_root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "doubao-browser-worker.log"
    command = [
        "node",
        str(script),
        "--api",
        "http://127.0.0.1:2001/api/v1/crawler",
    ]
    try:
        with log_path.open("ab") as log_file:
            subprocess.Popen(
                command,
                cwd=str(project_root),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    if sys.platform.startswith("win")
                    else 0
                ),
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="未找到 Node.js，无法启动本地执行器。") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"执行器启动失败：{exc}") from exc
    return CrawlerDoubaoWorkerStartResponse(
        started=True,
        command=command,
        log_path=str(log_path),
        message="已启动专用浏览器执行器；如遇登录、验证码或豆包限制，任务会停在失败状态并显示原因。",
    )


def _check_doubao_mobile_prerequisites() -> (
    tuple[CrawlerDoubaoMobilePrerequisites, list[str], str, str | None]
):
    """逐项检查手机豆包链路本机前置条件（只读检查，不改动本机状态）。"""
    appium_server_url = os.getenv("DOUBAO_MOBILE_APPIUM_URL", "http://127.0.0.1:4723")
    android_package = os.getenv("DOUBAO_ANDROID_PACKAGE", "").strip() or None
    adb_path = os.getenv("ADB_PATH", "").strip() or shutil.which("adb")
    appium_ok = True
    device_ready = False
    missing: list[str] = []
    if not adb_path:
        missing.append("ADB（配置 ADB_PATH 或将 adb 加入 PATH）")
    else:
        try:
            adb_resp = subprocess.run(
                [adb_path, "devices"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            device_ready = any(
                line.strip().endswith("\tdevice")
                for line in adb_resp.stdout.splitlines()
            )
        except (OSError, subprocess.TimeoutExpired):
            device_ready = False
        if not device_ready:
            missing.append("已连接并授权的安卓设备（adb devices 无在线设备）")
    if not android_package:
        missing.append("豆包 App 包名（DOUBAO_ANDROID_PACKAGE）")
    try:
        httpx.get(f"{appium_server_url.rstrip('/')}/status", timeout=1.0)
    except httpx.HTTPError:
        appium_ok = False
        missing.append(f"Appium Server 不可达（{appium_server_url}）")
    prerequisites = CrawlerDoubaoMobilePrerequisites(
        adb=bool(adb_path),
        appium_url=appium_ok,
        package=bool(android_package),
        device_ready=device_ready,
    )
    return prerequisites, missing, appium_server_url, android_package


@router.get(
    "/doubao-mobile/capabilities",
    response_model=CrawlerDoubaoMobileCapabilitiesResponse,
)
def get_doubao_mobile_capabilities():
    """返回安卓手机豆包执行器的本机依赖状态（逐项前置条件 + 费用恒 0）。"""
    prerequisites, missing, appium_server_url, android_package = (
        _check_doubao_mobile_prerequisites()
    )
    return CrawlerDoubaoMobileCapabilitiesResponse(
        enabled=not missing,
        worker_mode="android_appium_uiautomator2",
        appium_server_url=appium_server_url,
        android_package=android_package,
        missing_configuration=missing,
        requirements=[
            "一台已登录抖音和豆包的安卓测试机",
            "Android USB 调试已开启，adb devices 可看到设备",
            "Appium Server 已启动，并安装 UiAutomator2 driver",
            "DOUBAO_ANDROID_PACKAGE 指向豆包 App 包名",
        ],
        message=(
            "手机执行器可启动；实际成功率取决于当前 App UI、登录状态和授权弹窗。"
            if not missing
            else "手机执行器未配置完整；创建的任务会直接失败并写明缺失项。"
        ),
        prerequisites=prerequisites,
        estimated_cost_cny=0.0,
    )


@router.post(
    "/doubao-mobile/jobs",
    response_model=CrawlerDoubaoJobListResponse,
)
def create_doubao_mobile_jobs(
    body: CrawlerDoubaoJobCreateRequest,
    repo=Depends(get_repository),
    service=Depends(get_doubao_mobile_service),
):
    """为候选创建安卓手机豆包文案提取任务（费用恒 0）。

    缺少 ADB/Appium/包名/设备时，新建任务直接置为失败并写明具体缺失项，
    不调用付费媒体解析。
    """
    _prerequisites, missing, _appium_url, _package = (
        _check_doubao_mobile_prerequisites()
    )
    jobs = []
    seen: set[str] = set()
    for candidate_id in body.candidate_ids:
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        candidate = repo.get_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail=f"候选不存在：{candidate_id}")
        try:
            job = service.create_job(candidate)
        except DoubaoBrowserAutomationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.user_message) from exc
        if missing and job.status == TaskStatus.QUEUED:
            job = service.fail_job(
                job.task_id,
                error_message=(
                    "手机豆包执行器缺少前置条件：" + "；".join(missing)
                ),
                stage="前置条件检查未通过",
                retryable=True,
            )
        jobs.append(job)
    return CrawlerDoubaoJobListResponse(
        items=[_doubao_job_to_response(job) for job in jobs],
        total=len(jobs),
    )


@router.get(
    "/doubao-mobile/jobs",
    response_model=CrawlerDoubaoJobListResponse,
)
def list_doubao_mobile_jobs(
    candidate_id: str | None = None,
    limit: int = 50,
    service=Depends(get_doubao_mobile_service),
):
    jobs = service.list_jobs(candidate_id=candidate_id, limit=limit)
    return CrawlerDoubaoJobListResponse(
        items=[_doubao_job_to_response(job) for job in jobs],
        total=len(jobs),
    )


@router.post(
    "/doubao-mobile/jobs/claim",
    response_model=CrawlerDoubaoJobClaimResponse,
)
def claim_doubao_mobile_job(
    worker_id: str = "local-android-doubao-worker",
    service=Depends(get_doubao_mobile_service),
):
    job = service.claim_next_job(worker_id)
    return CrawlerDoubaoJobClaimResponse(
        job=_doubao_job_to_response(job) if job else None
    )


@router.post(
    "/doubao-mobile/jobs/{task_id}/stage",
    response_model=CrawlerDoubaoJobResponse,
)
def update_doubao_mobile_job_stage(
    task_id: str,
    body: CrawlerDoubaoJobStageRequest,
    service=Depends(get_doubao_mobile_service),
):
    try:
        job = service.mark_stage(
            task_id,
            stage=body.stage,
            progress=body.progress,
            outputs=body.outputs,
        )
    except DoubaoBrowserAutomationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.user_message) from exc
    return _doubao_job_to_response(job)


@router.post(
    "/doubao-mobile/jobs/{task_id}/complete",
    response_model=CrawlerDoubaoJobResponse,
)
def complete_doubao_mobile_job(
    task_id: str,
    body: CrawlerDoubaoJobCompleteRequest,
    service=Depends(get_doubao_mobile_service),
):
    try:
        job = service.complete_job(
            task_id,
            transcript_text=body.transcript_text,
            short_url=body.short_url,
            doubao_conversation_url=body.doubao_conversation_url,
            doubao_message_id=body.doubao_message_id,
        )
    except (DoubaoBrowserAutomationError, TranscriptionError) as exc:
        status_code = getattr(exc, "status_code", 400)
        detail = getattr(exc, "user_message", str(exc))
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return _doubao_job_to_response(job)


@router.post(
    "/doubao-mobile/jobs/{task_id}/fail",
    response_model=CrawlerDoubaoJobResponse,
)
def fail_doubao_mobile_job(
    task_id: str,
    body: CrawlerDoubaoJobFailRequest,
    service=Depends(get_doubao_mobile_service),
):
    try:
        job = service.fail_job(
            task_id,
            error_message=body.error_message,
            stage=body.stage,
            retryable=body.retryable,
        )
    except DoubaoBrowserAutomationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.user_message) from exc
    return _doubao_job_to_response(job)


@router.post(
    "/doubao-mobile/jobs/{task_id}/retry",
    response_model=CrawlerDoubaoJobResponse,
)
def retry_doubao_mobile_job(
    task_id: str,
    service=Depends(get_doubao_mobile_service),
):
    try:
        job = service.requeue_job(task_id)
    except DoubaoBrowserAutomationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.user_message) from exc
    return _doubao_job_to_response(job)


@router.post(
    "/doubao-mobile/worker/start",
    response_model=CrawlerDoubaoWorkerStartResponse,
)
def start_doubao_mobile_worker():
    """启动本机安卓手机 Appium 执行器。"""
    project_root = Path(__file__).resolve().parents[5]
    script = project_root / "scripts" / "doubao_mobile_worker.mjs"
    if not script.exists():
        raise HTTPException(status_code=500, detail="本地安卓豆包执行器脚本不存在。")
    log_dir = project_root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "doubao-mobile-worker.log"
    command = [
        "node",
        str(script),
        "--api",
        "http://127.0.0.1:2001/api/v1/crawler",
    ]
    try:
        with log_path.open("ab") as log_file:
            subprocess.Popen(
                command,
                cwd=str(project_root),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    if sys.platform.startswith("win")
                    else 0
                ),
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="未找到 Node.js，无法启动安卓执行器。") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"安卓执行器启动失败：{exc}") from exc
    return CrawlerDoubaoWorkerStartResponse(
        started=True,
        command=command,
        log_path=str(log_path),
        message="已启动安卓手机执行器；如缺少 ADB、Appium、豆包包名或需要登录，任务会失败并显示原因。",
    )


def _transcription_to_response(task) -> TranscriptionResponse:
    segments = [
        {
            "start": s.start,
            "end": s.end,
            "text": s.text,
            "confidence": s.confidence,
            "needs_review": s.needs_review,
            "reviewed": s.reviewed,
            "quality_status": s.quality_status,
            "quality_source": s.quality_source,
            "quality_note": s.quality_note,
            "alternatives": s.alternatives,
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
        source_kind=task.source_kind,
        timing_available=task.timing_available,
        duration_seconds=task.duration_seconds,
        approved_revision_id=task.approved_revision_id,
        low_confidence_count=(
            task.uncertain_segment_count
            if task.auto_reviewed
            else sum(
                1
                for segment in (task.segments or [])
                if segment.needs_review and not segment.reviewed
            )
        ),
        is_mock=task.is_mock,
        auto_reviewed=task.auto_reviewed,
        uncertain_segment_count=task.uncertain_segment_count,
        secondary_asr_count=task.secondary_asr_count,
        llm_review_count=task.llm_review_count,
        auto_review_error=task.auto_review_error,
        segments=segments,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _doubao_job_to_response(task) -> CrawlerDoubaoJobResponse:
    outputs = task.outputs or {}
    base = _transcription_to_response(task).model_dump()
    return CrawlerDoubaoJobResponse(
        **base,
        candidate_id=task.candidate_id,
        source_url=task.source_url,
        douyin_short_url=outputs.get("douyin_short_url"),
        doubao_conversation_url=outputs.get("doubao_conversation_url"),
        fee_cny=0.0,
        review_required=outputs.get("review_required") == "true",
        prompt_version=outputs.get("prompt_version"),
        worker_id=outputs.get("worker_id"),
    )


def _latest_local_link_transcription(repo, candidate_id: str) -> TranscriptionTask | None:
    """Return the latest zero-cost local link transcription for this candidate."""
    try:
        for task in repo.list_tasks():
            if (
                isinstance(task, TranscriptionTask)
                and task.candidate_id == candidate_id
                and task.source_kind == "douyin_local_browser"
            ):
                return task
    except Exception:
        return None
    return None


def _candidate_copy_fields(repo, candidate, media_task: TranscriptionTask | None) -> dict:
    """推导候选的三档文案来源字段。

    优先级：手机豆包任务 > 授权 ASR 媒体任务 > 无（前端再决定给原创脚本）。
    """
    doubao_mobile = None
    try:
        for task in repo.list_tasks():
            if (
                isinstance(task, TranscriptionTask)
                and task.candidate_id == candidate.video_id
                and task.source_kind == "doubao_mobile"
            ):
                doubao_mobile = task
                break
    except Exception:
        doubao_mobile = None
    if doubao_mobile is not None:
        succeeded = doubao_mobile.status == TaskStatus.SUCCEEDED
        return {
            "copy_source": CopySource.DOUBAO_MOBILE_TRANSCRIPT.value,
            "is_original_transcript": succeeded,
            "needs_manual_review": (
                succeeded
                and (doubao_mobile.outputs or {}).get("review_required") == "true"
            ),
        }
    if media_task is not None:
        succeeded = media_task.status == TaskStatus.SUCCEEDED
        return {
            "copy_source": CopySource.AUTHORIZED_ASR_TRANSCRIPT.value,
            "is_original_transcript": succeeded,
            "needs_manual_review": succeeded
            and any(segment.needs_review and not segment.reviewed for segment in media_task.segments),
        }
    return {
        "copy_source": None,
        "is_original_transcript": None,
        "needs_manual_review": None,
    }


def _candidate_to_response(
    candidate,
    *,
    repo,
    trend=None,
    platform_rank: int | None = None,
    provider_hot_rank: int | None = None,
    system_rank: int | None = None,
    evidence: str | None = None,
    keyword: str | None = None,
    relevance_basis: str | None = None,
    relevance_reason: str | None = None,
) -> CrawlerCandidateResult:
    resolved_evidence = evidence or candidate.evidence
    hotspot_lists, duration_seconds, hotspot_window_hours = _hotspot_evidence_details(resolved_evidence)
    is_hotspot = bool(resolved_evidence and resolved_evidence.startswith("hotspot:"))
    media_resolution = repo.find_latest_media_resolution_for_candidate(candidate.video_id)
    resolved_task = (
        repo.get_task(media_resolution.task_id)
        if media_resolution and media_resolution.task_id
        else None
    )
    resolved_task = resolved_task if isinstance(resolved_task, TranscriptionTask) else None
    local_link_task = _latest_local_link_transcription(repo, candidate.video_id)
    media_task = max(
        (task for task in (resolved_task, local_link_task) if task is not None),
        key=lambda task: task.created_at,
        default=None,
    )
    media_task_id = media_task.task_id if media_task else None
    media_status = (
        media_resolution.status.value
        if media_task is not None and media_task is resolved_task and media_resolution
        else media_task.status.value if media_task else None
    )
    copy_fields = _candidate_copy_fields(repo, candidate, media_task)
    trend_points: list[CrawlerTrendPoint] = []
    previous_interactions: float | None = None
    previous_at: datetime | None = None
    for snapshot in repo.list_snapshots(candidate.video_id)[-3:]:
        interactions = float(
            (snapshot.likes or 0)
            + 3 * (snapshot.comments or 0)
            + 4 * (snapshot.shares or 0)
            + 4 * (snapshot.favorites or 0)
        )
        growth_per_hour = None
        if previous_interactions is not None and previous_at is not None:
            elapsed_hours = (snapshot.sampled_at - previous_at).total_seconds() / 3600
            if elapsed_hours > 0:
                growth_per_hour = round(
                    (interactions - previous_interactions) / elapsed_hours,
                    4,
                )
        trend_points.append(
            CrawlerTrendPoint(
                sampled_at=snapshot.sampled_at,
                effective_interactions=interactions,
                growth_per_hour=growth_per_hour,
            )
        )
        previous_interactions = interactions
        previous_at = snapshot.sampled_at
    return CrawlerCandidateResult(
        video_id=candidate.video_id,
        title=candidate.title,
        author_name=candidate.author_name,
        platform=candidate.platform.value,
        platform_label=_platform_label(candidate.platform.value),
        source_url=str(candidate.source_url) if candidate.source_url else None,
        published_at=candidate.published_at,
        trend_score=trend.score if trend else None,
        trend_level=trend.level.value if trend else None,
        display_tier=trend.display_tier if trend else "ordinary",
        effective_interactions=trend.effective_interactions if trend else None,
        confidence=trend.confidence if trend else None,
        pool_size=trend.pool_size if trend else None,
        like_growth_per_hour=trend.like_growth_per_hour if trend else None,
        engagement_growth_per_hour=(
            trend.engagement_growth_per_hour if trend else None
        ),
        acceleration_ratio=trend.acceleration_ratio if trend else None,
        valid_snapshot_count=trend.valid_snapshot_count if trend else None,
        recrawl_count=trend.recrawl_count if trend else None,
        recall_count=trend.recall_count if trend else None,
        missed_checkpoint_count=(
            trend.missed_checkpoint_count if trend else None
        ),
        sampling_span_hours=trend.sampling_span_hours if trend else None,
        anomaly_status=trend.anomaly_status.value if trend else None,
        platform_rank=platform_rank,
        provider_hot_rank=provider_hot_rank or platform_rank,
        system_rank=system_rank,
        plays=candidate.metrics.plays,
        new_plays=candidate.metrics.plays if is_hotspot else None,
        likes=candidate.metrics.likes,
        new_likes=candidate.metrics.likes if is_hotspot else None,
        duration_seconds=duration_seconds,
        hotspot_window_hours=hotspot_window_hours,
        hotspot_list_labels=hotspot_lists,
        comments=candidate.metrics.comments,
        shares=candidate.metrics.shares,
        favorites=candidate.metrics.favorites,
        component_scores=trend.component_scores if trend else {},
        data_quality_warnings=candidate.data_quality_warnings,
        model_version=trend.model_version if trend else None,
        evidence=resolved_evidence,
        reasons=trend.reasons if trend else [],
        media_resolution_status=media_status,
        media_transcription_task_id=media_task_id,
        growth_stage=trend.growth_stage.value if trend else None,
        snapshot_count=trend.snapshot_count if trend else None,
        next_recrawl_at=trend.next_recrawl_at if trend else None,
        copy_source=copy_fields["copy_source"],
        is_original_transcript=copy_fields["is_original_transcript"],
        needs_manual_review=copy_fields["needs_manual_review"],
        share_count=candidate.share_count,
        collect_count=candidate.collect_count,
        relevance_basis=relevance_basis or ("title_or_hashtag" if keyword else None),
        relevance_reason=relevance_reason or (
            keyword_match_reason(keyword) if keyword else None
        ),
        trend_points=trend_points,
    )


def _provider_item_to_crawler_response(item) -> CrawlerCandidateResult:
    hotspot_lists, duration_seconds, hotspot_window_hours = _hotspot_evidence_details(
        item.evidence
    )
    return CrawlerCandidateResult(
        video_id=item.platform_item_id,
        title=item.title,
        author_name=item.author_name,
        platform=item.platform.value,
        platform_label=_platform_label(item.platform.value),
        source_url=str(item.source_url) if item.source_url else None,
        published_at=item.published_at,
        confidence=item.metrics.confidence,
        provider_hot_rank=item.provider_rank,
        plays=item.metrics.plays,
        new_plays=item.metrics.plays,
        likes=item.metrics.likes,
        new_likes=item.metrics.likes,
        duration_seconds=duration_seconds,
        hotspot_window_hours=hotspot_window_hours,
        hotspot_list_labels=hotspot_lists,
        comments=item.metrics.comments,
        shares=item.metrics.shares,
        favorites=item.metrics.favorites,
        data_quality_warnings=item.data_quality_warnings,
        evidence=item.evidence,
    )


def _hotspot_evidence_details(evidence: str | None) -> tuple[list[str], int | None, int | None]:
    if not evidence or not evidence.startswith("hotspot:"):
        return [], None, None
    header, *_ = evidence.split(";", 1)
    header_parts = header.split(":")
    labels = [label for label in (header_parts[1].split("|") if len(header_parts) > 1 else []) if label]
    window_match = re.search(r":(\d+)h:", header)
    duration_match = re.search(r"时长秒=(\d+)", evidence)
    return (
        labels,
        int(duration_match.group(1)) if duration_match else None,
        int(window_match.group(1)) if window_match else None,
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
    tracking_checkpoints = [
        item for item in repo.list_sampling_checkpoints()
        if item.tracking_batch_id == batch.batch_id
    ]
    pending_tracking = [
        item for item in tracking_checkpoints if item.status == SamplingStatus.PENDING
    ]
    if not batch.tracking_authorized:
        tracking_status = "not_started"
    elif pending_tracking:
        tracking_status = "scheduled"
    elif tracking_checkpoints and all(item.status == SamplingStatus.OBSERVED for item in tracking_checkpoints):
        tracking_status = "complete"
    elif tracking_checkpoints and all(item.status == SamplingStatus.CANCELLED for item in tracking_checkpoints):
        tracking_status = "cancelled"
    else:
        tracking_status = "partial"
    return CrawlerBatchResponse(
        batch_id=batch.batch_id,
        keyword=batch.keyword,
        published_window_days=batch.published_window_days,
        hotspot_window_hours=batch.hotspot_window_hours,
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
        total_candidates=sum(item.relevant_count for item in run_items),
        total_estimated_cost_cny=round(
            sum(item.billable_units or 0.0 for item in run_items),
            4,
        ),
        monitoring_policy=batch.monitoring_policy,
        sampling_offsets_hours=batch.sampling_offsets_hours,
        trend_tracking_enabled=batch.monitoring_policy == "adaptive_three_sample_v1",
        tracking_status=tracking_status,
        next_tracking_at=min((item.due_at for item in pending_tracking), default=None),
    )


def _interaction_heat(candidate) -> float:
    metrics = candidate.metrics
    return (
        float(metrics.likes or 0)
        + float(metrics.comments or 0) * 3
        + float(metrics.shares or 0) * 4
        + float(metrics.favorites or 0) * 4
    )


def _is_official_hot_candidate(candidate) -> bool:
    return bool(getattr(candidate, "official_hot", False)) or "official" in str(
        candidate.evidence or ""
    ).casefold()


def _run_to_response(
    batch: SearchBatch,
    run,
    repo,
    *,
    include_candidates: bool,
) -> CrawlerPlatformRunResponse:
    candidates: list[CrawlerCandidateResult] = []
    low_incremental_candidates: list[CrawlerCandidateResult] = []
    visible_matches: list[tuple[Any, Any]] = []
    historical_irrelevant_count = 0
    if run.status in {
        PlatformRunStatus.SUCCEEDED,
        PlatformRunStatus.PARTIAL,
        PlatformRunStatus.CACHED,
    }:
        match_run_id = run.cached_from_run_id if run.cached_from_run_id else run.run_id
        matches = repo.list_candidate_matches(match_run_id)
        for match in sorted(matches, key=lambda item: item.platform_rank):
            candidate = repo.get_candidate(match.video_id)
            if candidate is None:
                continue
            if not title_matches_keyword(title=candidate.title, keyword=batch.keyword):
                historical_irrelevant_count += 1
                continue
            visible_matches.append((match, candidate))

    strict_relevant_count = len(visible_matches)
    qualified_matches = [
        (match, candidate)
        for match, candidate in visible_matches
        if _interaction_heat(candidate) >= INTERACTION_HEAT_FLOOR
        or _is_official_hot_candidate(candidate)
    ]
    below_heat_floor_count = strict_relevant_count - len(qualified_matches)
    relevant_count = len(qualified_matches)
    irrelevant_count = max(run.irrelevant_count, historical_irrelevant_count)
    result_state = run.result_state
    if relevant_count == 0 and irrelevant_count:
        result_state = "all_irrelevant"
    elif strict_relevant_count and not relevant_count:
        result_state = "all_below_heat_floor"

    if include_candidates and qualified_matches:
        is_hotspot_run = run.provider == "douyin_local_browser"
        if is_hotspot_run:
            qualified_matches.sort(
                key=lambda item: (
                    -(item[1].metrics.plays or 0),
                    item[0].platform_rank,
                    item[1].video_id,
                )
            )
        trends = [] if is_hotspot_run else repo.list_keyword_trend_results(
            batch.keyword,
            limit=batch.requested_count_per_platform,
            platform=run.platform,
        )
        trend_by_candidate = {item.candidate_id: item for item in trends}
        visible_ids = {candidate.video_id for _, candidate in qualified_matches}
        system_rank_by_candidate = {
            item.candidate_id: index
            for index, item in enumerate(
                [item for item in trends if item.candidate_id in visible_ids], start=1
            )
        }
        for match, candidate in qualified_matches:
            trend = trend_by_candidate.get(candidate.video_id)
            candidates.append(
                _candidate_to_response(
                    candidate,
                    repo=repo,
                    trend=trend,
                    platform_rank=match.platform_rank,
                    provider_hot_rank=match.platform_rank,
                    system_rank=system_rank_by_candidate.get(candidate.video_id),
                    evidence=match.evidence or candidate.evidence,
                    keyword=batch.keyword,
                )
            )
    if (
        include_candidates
        and run.provider == "douyin_local_browser"
        and not candidates
        and run.low_incremental_items
    ):
        low_incremental_candidates = [
            _provider_item_to_crawler_response(item)
            for item in run.low_incremental_items
            if title_matches_keyword(title=item.title, keyword=batch.keyword)
        ]
    return CrawlerPlatformRunResponse(
        run_id=run.run_id,
        platform=run.platform.value,
        platform_label=_platform_label(run.platform.value),
        provider=run.provider,
        mode=run.mode.value,
        status=run.status.value,
        requested_count=run.requested_count,
        returned_count=relevant_count,
        raw_item_count=run.raw_item_count,
        parsed_item_count=run.parsed_item_count,
        out_of_window_count=run.out_of_window_count,
        invalid_count=run.invalid_count,
        duplicate_count=run.duplicate_count,
        relevant_count=relevant_count,
        strict_relevant_count=strict_relevant_count,
        below_heat_floor_count=below_heat_floor_count,
        irrelevant_count=irrelevant_count,
        duration_filtered_count=run.duration_filtered_count,
        incremental_play_filtered_count=run.incremental_play_filtered_count,
        relevance_rule_version=(
            run.relevance_rule_version or RELEVANCE_RULE_VERSION
        ),
        result_state=result_state,
        payload_diagnostic=run.payload_diagnostic,
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
        low_incremental_candidates=low_incremental_candidates,
    )
