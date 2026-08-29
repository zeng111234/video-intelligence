"""关键词爬虫 API —— FastAPI 直接复用商业搜索服务与 SQLite 持久化。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import csv
import io
import logging
import subprocess
import sys
import os
import shutil
import re
import hashlib
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from project.backend.app.core.deps import (
    get_bilibili_browser_provider,
    get_bilibili_browser_search_service,
    get_bilibili_public_metrics_provider,
    get_commercial_search_service,
    get_candidate_copy_probe_service,
    get_discovery_search_provider,
    get_douyin_public_browser_provider,
    get_douyin_public_search_service,
    get_kuaishou_browser_provider,
    get_kuaishou_browser_search_service,
    get_doubao_browser_service,
    get_doubao_mobile_service,
    get_media_resolution_service,
    get_official_hot_billboard_adapter,
    get_official_hot_pool_service,
    get_official_hot_words_adapter,
    get_copywriting_service,
    get_repository,
    get_source_service,
    get_transcription_service,
    get_xiaohongshu_browser_provider,
    get_xiaohongshu_login_browser_provider,
    get_xiaohongshu_browser_search_service,
)
from project.backend.app.core import config as backend_config
from project.backend.app.core.config import ASRMode
from project.backend.app.schemas.responses import TranscriptionResponse
from src.models import (
    CopySource,
    CandidateCopyProbe,
    CrawlerKeywordQueue,
    CrawlerKeywordQueueItem,
    KeywordQueueItemStatus,
    KeywordQueueStatus,
    DataSource,
    NormalizedCandidate,
    Platform,
    PlatformRunStatus,
    ProviderMode,
    SamplingStatus,
    SearchBatch,
    SearchBatchStatus,
    SourcePage,
    TaskStatus,
    TranscriptionTask,
    VideoMetricSnapshot,
)
from src.adapters.licensed import LicensedProviderError
from src.adapters.official import OfficialAdapterDisabledError, OfficialApiError
from src.services.doubao_browser import DoubaoBrowserAutomationError
from src.services.candidate_copy_probe import COPY_PROBE_VERSION
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
    item_matches_keyword,
    keyword_match_reason,
    normalized_keyword_text,
)
from src.services.transcription import TranscriptionError

router = APIRouter(prefix="/api/v1/crawler", tags=["crawler"])
logger = logging.getLogger(__name__)

_CRAWLER_QUEUE_MAX_ITEMS = 20
_CRAWLER_QUEUE_EXECUTOR = ThreadPoolExecutor(max_workers=1)
_CRAWLER_QUEUE_LOCK = threading.Lock()
_CRAWLER_QUEUE_FUTURES: dict[str, Any] = {}

PLATFORM_LABELS: dict[str, str] = {
    "douyin": "抖音",
    "bilibili": "B站",
    "xiaohongshu": "小红书",
    "kuaishou": "快手",
    "wechat_channels": "视频号",
}
SMART_FREE_CANDIDATE_THRESHOLD = 3
# 兼容历史批次和文案检测优先级的统计线；不再决定搜索结果是否可见。
INTERACTION_HEAT_FLOOR = 100.0
BILIBILI_PLAY_HEAT_FLOOR = 100
KUAISHOU_PUBLISHED_WINDOW_DAYS = 30
BILIBILI_PUBLISHED_WINDOW_DAYS = 7
# 常规“找素材”复用抖音专用登录浏览器，不再启动或补足热点宝。
DOUYIN_PUBLIC_SEARCH_PUBLISHED_WINDOW_DAYS = 0
DOUYIN_PUBLIC_SEARCH_RESULT_LIMIT = 100
HOTSPOT_CACHE_TTL_MINUTES = 60
HOTSPOT_COOLDOWN_MIN_SECONDS = 60 * 60
# 质量池只收“真实检测到文案”的候选：8 条主素材 + 4 条备用。
# 探测仅判断每条候选开头的前 10 秒，单条下载上限由 CandidateCopyProbeService 固定为 4 MiB。
COPY_PROBE_MAX_ATTEMPTS = 36
COPY_PROBE_LEGACY_VERSION = "copy_probe_v1"
COPY_PROBE_SUPPORTED_VERSIONS = frozenset(
    {COPY_PROBE_LEGACY_VERSION, COPY_PROBE_VERSION}
)
COPY_POOL_PRIMARY_TARGET = 8
COPY_POOL_RESERVE_TARGET = 4
COPY_PROBE_TARGET_DETECTIONS = COPY_POOL_PRIMARY_TARGET + COPY_POOL_RESERVE_TARGET
COPY_SEARCH_MATRIX_LIMIT = 8
COPY_SEARCH_MAX_VARIANTS_PER_PLATFORM = 3
COPY_SEARCH_SUFFIXES = ("讲解", "怎么选", "使用方法", "常见问题", "应用案例")
HOTSPOT_COOLDOWN_MAX_SECONDS = 60 * 60
HOTSPOT_SAFETY_PAUSE_SECONDS = 24 * 60 * 60
HOTSPOT_LEASE_SECONDS = 10 * 60
HOTSPOT_ROLLING_WINDOW_SECONDS = 24 * 60 * 60
# 安全优先：缓存始终优先；异常时暂停真实浏览器采集。
HOTSPOT_MAX_REAL_RUNS_PER_WINDOW = 4
HOTSPOT_PROVIDER_KEY = "douyin_hotspot_browser"
BROWSER_CACHE_TTL_MINUTES = 10
# 普通客户不额外等待；同平台互斥和验证码/访问异常暂停仍生效。
BROWSER_COOLDOWN_SECONDS = 0
BROWSER_SAFETY_PAUSE_SECONDS = 24 * 60 * 60
def _optional_positive_env_int(name: str) -> int | None:
    """Read an optional administrator guard; empty/zero means disabled."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


# 普通客户默认不设置 24 小时次数上限；管理员显式配置正整数后才启用。
# 历史 SQLite 计数仍保留用于诊断，但不会在默认关闭时阻断新任务。
BROWSER_MAX_REAL_RUNS_PER_WINDOW: int | None = _optional_positive_env_int(
    "BROWSER_MAX_REAL_RUNS_PER_WINDOW"
)


def _browser_safety_policy_message() -> str:
    if BROWSER_MAX_REAL_RUNS_PER_WINDOW is not None:
        return (
            f"同平台不额外冷却（同平台仍一次只运行一个任务）；管理员设置滚动24小时最多"
            f"{BROWSER_MAX_REAL_RUNS_PER_WINDOW}次；遇到验证码或访问异常会自动暂停。"
        )
    return "同平台不额外冷却（同平台仍一次只运行一个任务）；遇到验证码或访问异常会自动暂停。"
BROWSER_LEASE_SECONDS = 15 * 60
BROWSER_RESULT_LIMIT = 15
# B站公开详情单次最多读取 10 条；按批次补全本次最多 100 条候选，避免
# 后排结果因固定的首批上限全部显示为“未返回”。任一批失败即停止后续批次。
BILIBILI_PUBLIC_METRIC_REFRESH_BATCH_SIZE = 10
BILIBILI_PUBLIC_METRIC_REFRESH_LIMIT = 100
HOTSPOT_WINDOW_LABELS = {
    1: "近1小时",
    24: "近1天",
    72: "近3天",
    168: "近7天",
}


def _hotspot_window_label(hours: int | None) -> str:
    return HOTSPOT_WINDOW_LABELS.get(hours or 168, f"近{hours}小时")


def _next_hotspot_cooldown_seconds() -> int:
    """Keep a fixed, explainable spacing instead of behavior simulation."""
    return HOTSPOT_COOLDOWN_MIN_SECONDS


def _require_specific_search_keyword(keyword: str) -> None:
    """Retain the call site for old clients; broad platform search accepts any text."""
    del keyword


def _selected_free_platforms(body: "CrawlerSearchRequest") -> tuple[Platform, ...]:
    selected = tuple(dict.fromkeys(Platform(value) for value in body.platforms))
    if not selected:
        raise HTTPException(status_code=400, detail="至少选择一个搜索平台。")
    return selected


def _free_requested_count(body: "CrawlerSearchRequest") -> int:
    """Prefer the new count field while keeping old callers that sent only target_main_count."""
    if body.count_per_platform == 30 and body.target_main_count != 30:
        return body.target_main_count
    return body.count_per_platform


class CrawlerSearchRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=50, description="搜索关键词")
    platforms: list[Literal["douyin", "xiaohongshu", "kuaishou", "bilibili"]] = Field(
        default_factory=lambda: ["douyin", "xiaohongshu", "kuaishou", "bilibili"],
        min_length=1,
        description="本次要搜索的平台；小红书需先在独立登录浏览器中完成登录。",
    )
    # 热点宝的统计周期独立；这里仅是公开素材的发布时间筛选。
    published_window_days: int = Field(
        0,
        description="0=不限；1=一天内；7=一周内；180=半年内；历史批次兼容旧的 3/30/300 天。",
    )
    hotspot_window_hours: Literal[1, 24, 72, 168] = Field(
        168,
        description="热点宝榜单统计周期：1/24/72/168 小时；不限制视频发布时间。",
    )
    count_per_platform: int = Field(
        30, ge=1, le=100, description="每个平台最多保留数量"
    )
    kuaishou_sort: Literal["platform", "newest", "likes"] = Field(
        "platform",
        description="快手排序：平台综合、最新发布或最多点赞。",
    )
    kuaishou_duration_bucket: Literal[
        "all", "under_60", "between_60_300", "over_300"
    ] = Field(
        "all",
        description="快手时长：不限、1分钟以下、1到5分钟或5分钟以上。",
    )
    hotspot_result_limit: int = Field(
        100,
        ge=1,
        le=100,
        description="热点宝所选周期五榜合并、筛选后的最多保留数量；不影响 OneAPI 的 10 条上限。",
    )
    force_refresh: bool = Field(False, description="是否绕过缓存强制刷新")
    mode: str | None = Field(
        default=None,
        description="批次模式；smart=免费多平台候选；official_hot=仅官方热榜池",
    )
    related_terms: list[str] = Field(default_factory=list, max_length=5)
    allow_related_fallback: bool = Field(
        False,
        description="严格相关候选不足时，允许对一个用户明确给出的关联词额外搜索一次",
    )
    track_trend: Literal[False] = Field(
        False,
        description="复采已关闭，只执行本次搜索",
    )
    target_main_count: int = Field(
        30, ge=1, le=100, description="历史兼容的候选目标数量"
    )
    max_paid_calls: int = Field(0, ge=0, le=0, description="免费模式不允许付费调用")
    allow_paid_fallback: bool = Field(
        False,
        description="免费模式固定关闭，不允许付费补充。",
    )


class CrawlerKeywordQueueRequest(BaseModel):
    keywords: str = Field(..., min_length=1, max_length=2000)
    platforms: list[Literal["douyin", "xiaohongshu", "kuaishou", "bilibili"]] = Field(
        default_factory=lambda: ["douyin", "xiaohongshu", "kuaishou", "bilibili"],
        min_length=1,
    )
    published_window_days: int = Field(0)
    count_per_platform: int = Field(30, ge=1, le=100)
    force_refresh: bool = False


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

    platform: str = "douyin"
    profile: str = "safe"
    state: str = "ready"
    cache_ttl_minutes: int = HOTSPOT_CACHE_TTL_MINUTES
    estimated_duration_seconds: int = 150
    cooldown_remaining_seconds: int = 0
    next_available_at: datetime | None = None
    real_runs_in_window: int = 0
    real_run_limit: int | None = HOTSPOT_MAX_REAL_RUNS_PER_WINDOW
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
    crawler_safety_policy: str = _browser_safety_policy_message()
    usage: dict[str, Any] | None = None
    official_hot_billboard: CrawlerOfficialHotCapability | None = None
    official_hot_words: CrawlerOfficialHotCapability | None = None
    hotspot_browser: CrawlerBrowserDiscoveryCapabilities | None = None
    platform_browsers: list[CrawlerBrowserDiscoveryCapabilities] = Field(
        default_factory=list
    )


class CrawlerBrowserDiscoveryCapabilities(BaseModel):
    platform: str = "douyin"
    platform_label: str = "抖音"
    enabled: bool
    running: bool
    login_required: bool
    login_reset_available: bool = False
    missing_configuration: list[str] = Field(default_factory=list)
    browser_channel: str = "chrome"
    ready_to_crawl: bool = False
    phase: str = "unknown"
    adapter_version: str = "unknown"
    provider_name: str
    message: str


class CrawlerBrowserDiscoveryStartResponse(CrawlerBrowserDiscoveryCapabilities):
    started: bool


class CrawlerBrowserDiscoveryResetRequest(BaseModel):
    confirmed: bool = False


class CrawlerBrowserDiscoveryResetResponse(BaseModel):
    platform: str
    platform_label: str
    reset: bool
    manual_login_required: bool
    message: str


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
    published_at_reliable: bool = False
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
    heat_score: float | None = None
    new_likes: int | None = None
    likes_per_day: float | None = None
    quality_source: str | None = None
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
    spoken_material_status: str = "topic_only"
    spoken_material_message: str = "仅有标题和互动数据，只能用于选题参考。"
    spoken_seed_score: int = 0
    spoken_seed_status: str = "low_information"
    spoken_seed_message: str = "公开文字不足以支撑原创文案。"
    audio_status: str = "unknown"
    audio_message: str = "尚未检测文案。"
    copy_pool_status: Literal["primary", "reserve", "excluded"] | None = None
    copy_rejection_reason: str | None = None
    # 参考候选只能人工确认后进入后续流程；严格命中项保持默认空值。
    selection_tier: Literal["priority", "reserve"] | None = None
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
    # 新漏斗字段：旧批次/旧接口缺失时均安全回退为 0 或现有计数。
    raw_discovered: int = 0
    parsed: int = 0
    deduped: int = 0
    direct_match: int = 0
    relevance_filtered: int = 0
    duration_filtered: int = 0
    invalid_fields: int = 0
    retained: int = 0
    out_of_window_count: int = 0
    invalid_count: int = 0
    duplicate_count: int = 0
    relevant_count: int = 0
    strict_relevant_count: int = 0
    below_heat_floor_count: int = 0
    low_spoken_value_count: int = 0
    irrelevant_count: int = 0
    duration_filtered_count: int = 0
    incremental_play_filtered_count: int = 0
    relevance_rule_version: str | None = None
    result_state: str = "historical_unknown"
    crawl_stop_reason: str | None = None
    crawl_stop_message: str | None = None
    payload_diagnostic: str | None = None
    stage_timings_ms: dict[str, int] = Field(default_factory=dict)
    rule_version: str | None = None
    browser_reused: bool | None = None
    session_recovered: bool = False
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
    # 严格关键词未命中时的少量公开搜索参考项；需要人工确认，不自动进入流水线。
    reference_count: int = 0
    reference_candidates: list[CrawlerCandidateResult] = Field(default_factory=list)
    # 仅在热点宝主榜为空时返回：严格相关但新增播放量不超过 1,000 的参考视频。
    low_incremental_candidates: list[CrawlerCandidateResult] = Field(
        default_factory=list
    )


class CrawlerBatchResponse(BaseModel):
    batch_id: str
    keyword: str
    platforms: list[str] = Field(default_factory=list)
    published_window_days: int
    hotspot_window_hours: int | None = None
    count_per_platform: int
    kuaishou_sort: Literal["platform", "newest", "likes"] = "platform"
    kuaishou_duration_bucket: Literal[
        "all", "under_60", "between_60_300", "over_300"
    ] = "all"
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
    copy_detected_count: int = 0
    copy_primary_count: int = 0
    copy_reserve_count: int = 0
    copy_probe_attempt_count: int = 0
    copy_probe_recheckable_count: int = 0
    copy_queries_executed: int = 0
    copy_matrix_exhausted: bool = False


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


class CrawlerKeywordQueueItemResponse(BaseModel):
    item_id: str
    keyword: str
    status: str
    batch_id: str | None = None
    partial_batch_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    progress_stage: str = "queued"
    progress_platform: str | None = None
    progress_message: str | None = None
    scanned_count: int = 0
    parsed_count: int = 0
    retained_count: int = 0
    progress_candidates: list[CrawlerCandidateResult] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CrawlerKeywordQueueResponse(BaseModel):
    queue_id: str
    status: str
    platforms: list[str]
    published_window_days: int
    count_per_platform: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    total: int
    completed: int
    queued: int
    running: int
    failed: int
    worker_active: bool = False
    items: list[CrawlerKeywordQueueItemResponse]
    message: str | None = None


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


def _browser_capability_payload(
    provider,
    *,
    platform: Platform,
) -> CrawlerBrowserDiscoveryCapabilities:
    capability = provider.capabilities()
    status = getattr(provider, "session_status", lambda: None)()
    return CrawlerBrowserDiscoveryCapabilities(
        platform=platform.value,
        platform_label=_platform_label(platform.value),
        enabled=capability.enabled,
        running=bool(status and status.running),
        login_required=bool(status and status.login_required),
        login_reset_available=bool(getattr(provider, "login_reset_available", False)),
        missing_configuration=getattr(capability, "missing_configuration", []),
        browser_channel=getattr(provider, "browser_channel", "chrome"),
        ready_to_crawl=bool(status and status.ready_to_crawl),
        phase=(status.phase if status is not None else "unavailable"),
        adapter_version=getattr(provider, "adapter_version", "unknown"),
        provider_name=capability.provider_name,
        message=(
            status.message
            if status is not None
            else f"{_platform_label(platform.value)}本机浏览器不可用。"
        ),
    )


def _browser_capability_payloads(
    providers: list[tuple[object, Platform]],
) -> list[CrawlerBrowserDiscoveryCapabilities]:
    """Probe independent loopback browser sessions concurrently.

    A closed or proxy-delayed debug port must not multiply the page-open delay
    by the number of supported platforms.
    """
    with ThreadPoolExecutor(max_workers=len(providers)) as executor:
        futures = [
            executor.submit(_browser_capability_payload, provider, platform=platform)
            for provider, platform in providers
        ]
        return [future.result() for future in futures]


def _capability_payload(
    service,
    provider,
    *,
    billboard_adapter=None,
    hot_words_adapter=None,
    hotspot_provider=None,
    douyin_public_provider=None,
    xiaohongshu_provider=None,
    xiaohongshu_login_provider=None,
    kuaishou_provider=None,
    bilibili_provider=None,
) -> CrawlerCapabilitiesResponse:
    capability = provider.capabilities()
    active_platforms = list(service.active_platforms)
    paused_platforms = [
        item for item in capability.supported_platforms if item not in active_platforms
    ]
    # Capability discovery must not call a remote supplier.  This page no
    # longer exposes paid fallback controls, and a usage probe can otherwise
    # delay every page open while also creating unnecessary external traffic.
    usage = None
    hotspot_capability = (
        hotspot_provider.capabilities() if hotspot_provider is not None else None
    )
    # 小红书找素材与连接状态共用用户主动登录的独立资料目录。
    xiaohongshu_connection_provider = xiaohongshu_login_provider or xiaohongshu_provider
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
        active_platform_labels=[
            _platform_label(item.value) for item in active_platforms
        ],
        paused_platforms=[item.value for item in paused_platforms],
        paused_platform_labels=[
            _platform_label(item.value) for item in paused_platforms
        ],
        missing_configuration=capability.missing_configuration,
        permission_status=capability.permission_status,
        monthly_query_count=service.monthly_query_count(),
        monthly_estimated_cost_cny=service.monthly_query_cost(),
        monthly_warning_queries=MONTHLY_WARNING_QUERIES,
        monthly_hard_limit_queries=MONTHLY_HARD_LIMIT_QUERIES,
        monthly_hard_limit_cost_cny=MONTHLY_HARD_LIMIT_COST_CNY,
        cache_ttl_minutes=CACHE_TTL_MINUTES,
        crawler_safety_policy=_browser_safety_policy_message(),
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
            _browser_capability_payload(
                hotspot_provider,
                platform=Platform.DOUYIN,
            )
            if hotspot_capability is not None
            else None
        ),
        platform_browsers=_browser_capability_payloads(
            [
                (douyin_public_provider, Platform.DOUYIN),
                (xiaohongshu_connection_provider, Platform.XIAOHONGSHU),
                (kuaishou_provider, Platform.KUAISHOU),
                (bilibili_provider, Platform.BILIBILI),
            ]
        )
        if (
            douyin_public_provider is not None
            and xiaohongshu_connection_provider is not None
            and kuaishou_provider is not None
            and bilibili_provider is not None
        )
        else [],
    )


@router.get("/capabilities", response_model=CrawlerCapabilitiesResponse)
def get_capabilities(
    service=Depends(get_commercial_search_service),
    provider=Depends(get_discovery_search_provider),
    billboard_adapter=Depends(get_official_hot_billboard_adapter),
    hot_words_adapter=Depends(get_official_hot_words_adapter),
    douyin_public_provider=Depends(get_douyin_public_browser_provider),
    xiaohongshu_provider=Depends(get_xiaohongshu_browser_provider),
    xiaohongshu_login_provider=Depends(get_xiaohongshu_login_browser_provider),
    kuaishou_provider=Depends(get_kuaishou_browser_provider),
    bilibili_provider=Depends(get_bilibili_browser_provider),
):
    """返回供应商模式、支持平台、缺失配置、缓存和额度状态。"""
    return _capability_payload(
        service,
        provider,
        billboard_adapter=billboard_adapter,
        hot_words_adapter=hot_words_adapter,
        douyin_public_provider=douyin_public_provider,
        xiaohongshu_provider=xiaohongshu_provider,
        xiaohongshu_login_provider=xiaohongshu_login_provider,
        kuaishou_provider=kuaishou_provider,
        bilibili_provider=bilibili_provider,
    )


@router.get(
    "/browser-discovery/capabilities",
    response_model=CrawlerBrowserDiscoveryCapabilities,
)
def get_browser_discovery_capabilities(
    provider=Depends(get_douyin_public_browser_provider),
):
    """兼容旧客户端：返回抖音官网浏览器状态，不再指向热点宝。"""
    return _browser_capability_payload(
        provider,
        platform=Platform.DOUYIN,
    )


def _start_visible_browser_login(provider):
    """Open only a visible local login window and convert launch failures to API errors."""
    start = getattr(provider, "open_login_browser", None) or getattr(
        provider, "start_login_browser", None
    )
    if start is None:
        raise HTTPException(status_code=409, detail="当前来源不支持打开本机登录窗口。")
    previous_status = None
    try:
        previous_status = getattr(provider, "session_status", lambda: None)()
        return previous_status, start()
    except LicensedProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Chrome may finish opening while its local CDP endpoint is being
        # checked.  When the dedicated browser is demonstrably running, keep
        # the usable session instead of reporting a false launch failure.
        try:
            current_status = getattr(provider, "session_status", lambda: None)()
        except Exception:
            current_status = None
        if current_status is not None and bool(current_status.running):
            return previous_status, current_status
        # Browser launch is local-only.  Do not let an adapter/process error
        # become an opaque fetch failure in the page.
        raise HTTPException(
            status_code=503,
            detail="无法打开本机登录浏览器，请关闭该专用浏览器后重试；仍失败请重启后端。",
        ) from exc


@router.post(
    "/browser-discovery/start",
    response_model=CrawlerBrowserDiscoveryStartResponse,
)
def start_browser_discovery_login(
    provider=Depends(get_douyin_public_browser_provider),
):
    """兼容旧客户端：打开抖音官网登录窗口，不再指向热点宝。"""
    previous_status, status = _start_visible_browser_login(provider)
    capability = provider.capabilities()
    return CrawlerBrowserDiscoveryStartResponse(
        platform=Platform.DOUYIN.value,
        platform_label=_platform_label(Platform.DOUYIN.value),
        enabled=status.enabled,
        running=status.running,
        login_required=status.login_required,
        login_reset_available=bool(getattr(provider, "login_reset_available", False)),
        missing_configuration=getattr(capability, "missing_configuration", []),
        browser_channel=getattr(provider, "browser_channel", "chrome"),
        ready_to_crawl=status.ready_to_crawl,
        phase=status.phase,
        adapter_version=getattr(provider, "adapter_version", "unknown"),
        provider_name=capability.provider_name,
        message=status.message,
        started=not bool(previous_status and previous_status.running),
    )


def _select_platform_browser_provider(
    platform: Literal["douyin", "xiaohongshu", "kuaishou", "bilibili"],
    douyin_public_provider,
    xiaohongshu_login_provider,
    kuaishou_provider,
    bilibili_provider,
):
    return {
        Platform.DOUYIN.value: douyin_public_provider,
        Platform.XIAOHONGSHU.value: xiaohongshu_login_provider,
        Platform.KUAISHOU.value: kuaishou_provider,
        Platform.BILIBILI.value: bilibili_provider,
    }[platform]


@router.get(
    "/browser-discovery/{platform}/capabilities",
    response_model=CrawlerBrowserDiscoveryCapabilities,
)
def get_platform_browser_discovery_capabilities(
    platform: Literal["douyin", "xiaohongshu", "kuaishou", "bilibili"],
    douyin_public_provider=Depends(get_douyin_public_browser_provider),
    xiaohongshu_login_provider=Depends(get_xiaohongshu_login_browser_provider),
    kuaishou_provider=Depends(get_kuaishou_browser_provider),
    bilibili_provider=Depends(get_bilibili_browser_provider),
):
    """返回平台独立 Chrome 的连接状态；小红书需先完成人工登录。"""
    provider = _select_platform_browser_provider(
        platform,
        douyin_public_provider,
        xiaohongshu_login_provider,
        kuaishou_provider,
        bilibili_provider,
    )
    return _browser_capability_payload(
        provider,
        platform=Platform(platform),
    )


@router.post(
    "/browser-discovery/{platform}/start",
    response_model=CrawlerBrowserDiscoveryStartResponse,
)
def start_platform_browser_discovery_login(
    platform: Literal["douyin", "xiaohongshu", "kuaishou", "bilibili"],
    douyin_public_provider=Depends(get_douyin_public_browser_provider),
    xiaohongshu_login_provider=Depends(get_xiaohongshu_login_browser_provider),
    kuaishou_provider=Depends(get_kuaishou_browser_provider),
    bilibili_provider=Depends(get_bilibili_browser_provider),
):
    """打开平台独立 Chrome；小红书由用户在窗口中完成登录。"""
    provider = _select_platform_browser_provider(
        platform,
        douyin_public_provider,
        xiaohongshu_login_provider,
        kuaishou_provider,
        bilibili_provider,
    )
    previous_status, status = _start_visible_browser_login(provider)
    capability = provider.capabilities()
    return CrawlerBrowserDiscoveryStartResponse(
        platform=platform,
        platform_label=_platform_label(platform),
        enabled=capability.enabled,
        running=status.running,
        login_required=status.login_required,
        login_reset_available=bool(getattr(provider, "login_reset_available", False)),
        missing_configuration=capability.missing_configuration,
        browser_channel=getattr(provider, "browser_channel", "chrome"),
        ready_to_crawl=status.ready_to_crawl,
        phase=status.phase,
        adapter_version=getattr(provider, "adapter_version", "unknown"),
        provider_name=capability.provider_name,
        message=status.message,
        started=not bool(previous_status and previous_status.running),
    )


@router.post(
    "/browser-discovery/{platform}/reset-login",
    response_model=CrawlerBrowserDiscoveryResetResponse,
)
def reset_platform_browser_login_state(
    platform: Literal["douyin", "xiaohongshu", "kuaishou", "bilibili"],
    body: CrawlerBrowserDiscoveryResetRequest,
    douyin_public_provider=Depends(get_douyin_public_browser_provider),
    xiaohongshu_login_provider=Depends(get_xiaohongshu_login_browser_provider),
    kuaishou_provider=Depends(get_kuaishou_browser_provider),
    bilibili_provider=Depends(get_bilibili_browser_provider),
):
    """Explicit advanced action: reset only one platform's login state."""
    provider = _select_platform_browser_provider(
        platform,
        douyin_public_provider,
        xiaohongshu_login_provider,
        kuaishou_provider,
        bilibili_provider,
    )
    reset = getattr(provider, "reset_login_state", None)
    if reset is None:
        raise HTTPException(status_code=400, detail="该平台暂不支持单独重置登录状态。")
    try:
        status = reset(confirmed=body.confirmed)
    except LicensedProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CrawlerBrowserDiscoveryResetResponse(
        platform=platform,
        platform_label=_platform_label(platform),
        reset=True,
        manual_login_required=True,
        message=(
            f"已重置{_platform_label(platform)}登录状态；请在专用浏览器中人工重新登录后继续。"
            if status.login_required
            else f"已清理{_platform_label(platform)}域名登录状态；请在专用浏览器中确认并重新登录后继续。"
        ),
    )


class XiaohongshuManualMaterial(BaseModel):
    """Information the operator has already seen; this endpoint never opens the URL."""

    title: str = Field(min_length=1, max_length=200)
    source_url: str = Field(min_length=8, max_length=2000)
    visible_copy: str = Field(default="", max_length=3000)
    author_name: str = Field(default="人工观察", max_length=80)
    observed_at: datetime | None = None


class XiaohongshuManualMaterialRequest(BaseModel):
    items: list[XiaohongshuManualMaterial] = Field(min_length=1, max_length=50)


@router.post("/manual-materials/xiaohongshu")
def import_xiaohongshu_manual_materials(
    body: XiaohongshuManualMaterialRequest,
    source_service=Depends(get_source_service),
):
    """Store manually observed XHS material without any browser, network, or media access."""
    pages = []
    errors: list[str] = []
    for index, item in enumerate(body.items, start=1):
        observed_at = item.observed_at or datetime.now().astimezone()
        reference = f"{item.source_url.strip()}|{item.title.strip()}"
        item_id = f"manual-{hashlib.sha256(reference.encode('utf-8')).hexdigest()[:16]}"
        try:
            pages.append(
                NormalizedCandidate(
                    platform_item_id=item_id,
                    title=item.title,
                    author_id="",
                    author_name=item.author_name or "人工观察",
                    platform=Platform.XIAOHONGSHU,
                    published_at=observed_at,
                    source_url=item.source_url,
                    source_type=DataSource.MANUAL,
                    metrics=VideoMetricSnapshot(
                        item_id=item_id, sampled_at=observed_at, confidence=0.7
                    ),
                    evidence=f"人工素材箱；操作者观察时间={observed_at.isoformat()}；可见文案={item.visible_copy.strip()}",
                )
            )
        except Exception as exc:
            errors.append(f"第{index}条：{exc}")
    report = source_service.import_page(SourcePage(items=pages))
    return {
        "added": report.added_candidates,
        "updated": report.updated_candidates,
        "duplicates": report.duplicates,
        "errors": errors,
        "message": "已保存人工观察素材；系统没有打开链接、抓取页面或访问小红书账号。",
    }


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
    """执行一次官方热榜同步并按关键词匹配，不安排复采。"""
    keyword = (body.keyword if body else None) or ""
    keyword = keyword.strip()
    try:
        executed_recrawls = []
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
                    f"未输入关键词，已完成全量热榜同步{hot_words_note}；不会生成关键词复爬计划。"
                ),
                executed_recrawls=len(executed_recrawls),
                candidates=[
                    _candidate_to_response(candidate, repo=repo, platform_rank=index)
                    for index, candidate in enumerate(pool, start=1)
                ],
            )
        service.sync_billboard(limit=50)
        result = service.search_hot_pool(
            keyword=keyword,
            limit=10,
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
                f"官方热榜匹配 {len(result.matched)} 条候选；本次为单次搜索，不安排后续复采。"
            )
        ),
        executed_recrawls=len(executed_recrawls),
        next_recrawl_at=None,
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
        reference_text=_visible_copy_from_evidence(candidate.evidence),
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
    douyin_public_service=Depends(get_douyin_public_search_service),
    douyin_public_provider=Depends(get_douyin_public_browser_provider),
    bilibili_service=Depends(get_bilibili_browser_search_service),
    bilibili_provider=Depends(get_bilibili_browser_provider),
    xiaohongshu_service=Depends(get_xiaohongshu_browser_search_service),
    xiaohongshu_provider=Depends(get_xiaohongshu_browser_provider),
    kuaishou_service=Depends(get_kuaishou_browser_search_service),
    kuaishou_provider=Depends(get_kuaishou_browser_provider),
):
    """提交前预览当前启用平台的执行计划。"""
    _require_specific_search_keyword(body.keyword)
    if body.mode in (None, "", "smart"):
        return _preview_free_multi_platform_batch(
            body,
            bilibili_service,
            bilibili_provider,
            douyin_public_service,
            douyin_public_provider,
            xiaohongshu_service,
            xiaohongshu_provider,
            kuaishou_service,
            kuaishou_provider,
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
        monitoring_policy=(
            "adaptive_three_sample_v1" if body.track_trend else "manual_tracking_v1"
        ),
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
    douyin_public_service=Depends(get_douyin_public_search_service),
    douyin_public_provider=Depends(get_douyin_public_browser_provider),
    bilibili_service=Depends(get_bilibili_browser_search_service),
    bilibili_provider=Depends(get_bilibili_browser_provider),
    bilibili_metrics_provider=Depends(get_bilibili_public_metrics_provider),
    xiaohongshu_service=Depends(get_xiaohongshu_browser_search_service),
    xiaohongshu_provider=Depends(get_xiaohongshu_browser_provider),
    kuaishou_service=Depends(get_kuaishou_browser_search_service),
    kuaishou_provider=Depends(get_kuaishou_browser_provider),
):
    """执行当前启用平台的关键词榜单批次并持久化；official_hot 模式走官方热榜池。"""
    _require_specific_search_keyword(body.keyword)
    if body.mode == "official_hot":
        return _execute_official_hot_batch(body, hot_pool, repo)
    if body.mode in (None, "", "smart"):
        return _execute_free_multi_platform_batch(
            body,
            bilibili_service,
            bilibili_provider,
            bilibili_metrics_provider,
            douyin_public_service,
            douyin_public_provider,
            xiaohongshu_service,
            xiaohongshu_provider,
            kuaishou_service,
            kuaishou_provider,
            repo,
        )
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
):
    """复采功能已关闭；保留路由用于向旧客户端返回明确结果。"""
    raise HTTPException(
        status_code=410,
        detail=f"批次 {batch_id} 不再支持复采；请直接使用本次搜索结果。",
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


def _hotspot_safety_status(
    body: CrawlerSearchRequest, hotspot_service, repo
) -> CrawlerSafetyStatus:
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
            message=(
                state.blocked_reason
                or "热点宝出现安全验证或访问频繁提示，已暂停真实采集。"
            ),
            **common,
        )
    if (
        state
        and state.active_run_id
        and state.lease_expires_at
        and state.lease_expires_at > now
    ):
        remaining = max(1, int((state.lease_expires_at - now).total_seconds()))
        return CrawlerSafetyStatus(
            state="running",
            cooldown_remaining_seconds=remaining,
            next_available_at=state.lease_expires_at,
            message="已有热点宝采集任务在顺序执行；请等待它结束。",
            **common,
        )
    if real_runs >= HOTSPOT_MAX_REAL_RUNS_PER_WINDOW:
        remaining = (
            max(1, int((window_ends_at - now).total_seconds())) if window_ends_at else 1
        )
        return CrawlerSafetyStatus(
            state="daily_limit",
            cooldown_remaining_seconds=remaining,
            next_available_at=window_ends_at,
            message=(
                "为降低账号风险，滚动 24 小时的真实热点宝采集已达到 48 次上限；缓存结果仍可立即查看。"
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
            "next_available_at": status.next_available_at.isoformat()
            if status.next_available_at
            else None,
        },
        headers={"Retry-After": str(retry_after)},
    )


def _browser_provider_key(platform: Platform) -> str:
    return f"{platform.value}_browser_search"


def _claim_browser_lease(platform: Platform, repo, now: datetime) -> str | None:
    """为平台的真实浏览器采集抢占租约（并发互斥 + 可选滚动次数上限）。

    返回租约 id 表示成功；返回 None 表示被限（冷却/运行中/管理员次数上限）。
    调用方在 finally 中必须用同一租约 id 释放。
    """
    lease_id = f"browser-{uuid4().hex}"
    if repo.claim_provider_safety_lease(
        provider=_browser_provider_key(platform),
        run_id=lease_id,
        now=now,
        lease_seconds=BROWSER_LEASE_SECONDS,
        max_runs_in_window=BROWSER_MAX_REAL_RUNS_PER_WINDOW,
        rolling_window_seconds=24 * 60 * 60,
    ):
        return lease_id
    return None


def _browser_safety_status(platform: Platform, repo) -> CrawlerSafetyStatus:
    """Read persisted pacing state without opening a platform page."""
    now = datetime.now().astimezone()
    state = repo.get_provider_safety_state(_browser_provider_key(platform))
    real_runs, window_ends_at = _hotspot_rolling_usage(state, now)
    common = {
        "platform": platform.value,
        "profile": "low_frequency",
        "cache_ttl_minutes": BROWSER_CACHE_TTL_MINUTES,
        "estimated_duration_seconds": 90,
        "real_runs_in_window": real_runs,
        "real_run_limit": BROWSER_MAX_REAL_RUNS_PER_WINDOW,
        "rolling_window_ends_at": window_ends_at,
    }
    if state and state.blocked_until and state.blocked_until > now:
        remaining = max(1, int((state.blocked_until - now).total_seconds()))
        return CrawlerSafetyStatus(
            state="safety_pause",
            cooldown_remaining_seconds=remaining,
            next_available_at=state.blocked_until,
            message=state.blocked_reason
            or f"{_platform_label(platform.value)}已暂停真实采集。",
            **common,
        )
    if (
        state
        and state.active_run_id
        and state.lease_expires_at
        and state.lease_expires_at > now
    ):
        remaining = max(1, int((state.lease_expires_at - now).total_seconds()))
        return CrawlerSafetyStatus(
            state="running",
            cooldown_remaining_seconds=remaining,
            next_available_at=state.lease_expires_at,
            message=f"{_platform_label(platform.value)}正在顺序采集，请等待结束。",
            **common,
        )
    if (
        BROWSER_MAX_REAL_RUNS_PER_WINDOW is not None
        and real_runs >= BROWSER_MAX_REAL_RUNS_PER_WINDOW
    ):
        remaining = (
            max(1, int((window_ends_at - now).total_seconds())) if window_ends_at else 1
        )
        return CrawlerSafetyStatus(
            state="daily_limit",
            cooldown_remaining_seconds=remaining,
            next_available_at=window_ends_at,
            message=f"{_platform_label(platform.value)}滚动24小时已达到 {BROWSER_MAX_REAL_RUNS_PER_WINDOW} 次真实采集上限。",
            **common,
        )
    if state and state.next_allowed_at and state.next_allowed_at > now:
        remaining = max(1, int((state.next_allowed_at - now).total_seconds()))
        cooldown_minutes = max(1, (remaining + 59) // 60)
        return CrawlerSafetyStatus(
            state="cooldown",
            cooldown_remaining_seconds=remaining,
            next_available_at=state.next_allowed_at,
            message=(
                f"{_platform_label(platform.value)}刚完成一次真实搜索；"
                f"同平台约 {cooldown_minutes} 分钟后可再搜。"
            ),
            **common,
        )
    return CrawlerSafetyStatus(
        state="ready",
        message=(
            f"{_platform_label(platform.value)}安全模式已就绪：最多15条；"
            f"{_browser_safety_policy_message()}"
        ),
        **common,
    )


def _browser_safety_event(error: str | None) -> bool:
    text = str(error or "")
    return any(
        marker in text
        for marker in (
            "安全验证",
            "验证码",
            "访问频繁",
            "操作频繁",
            "请求过于频繁",
            "账号异常",
            "返回 403",
            "返回 412",
            "返回 429",
        )
    )


def _preview_free_multi_platform_batch(
    body: CrawlerSearchRequest,
    bilibili_service,
    bilibili_provider,
    douyin_public_service,
    douyin_public_provider,
    xiaohongshu_service,
    xiaohongshu_provider,
    kuaishou_service,
    kuaishou_provider,
) -> CrawlerPreviewResponse:
    """Describe the free, no-OneAPI path before it runs."""
    selected_platforms = _selected_free_platforms(body)
    requested_count = _free_requested_count(body)
    platform_items: list[CrawlerPlatformPreview] = []

    def browser_preview(
        service,
        provider,
        platform: Platform,
        published_window_days: int,
        result_limit: int | None = None,
    ):
        status = getattr(provider, "session_status", lambda: None)()
        ready = bool(
            provider.capabilities().enabled and status and status.ready_to_crawl
        )
        search_options = (
            {
                "kuaishou_sort": body.kuaishou_sort,
                "kuaishou_duration_bucket": body.kuaishou_duration_bucket,
            }
            if platform == Platform.KUAISHOU
            else {}
        )
        effective_published_window_days = (
            0 if platform == Platform.KUAISHOU else published_window_days
        )
        preview = service.preview(
            keyword=body.keyword,
            published_window_days=effective_published_window_days,
            count=result_limit if result_limit is not None else requested_count,
            force_refresh=body.force_refresh,
            platforms=(platform,),
            cache_ttl_minutes=BROWSER_CACHE_TTL_MINUTES,
            include_monitoring=False,
            **search_options,
        )[0]
        return preview, status, ready

    if Platform.DOUYIN in selected_platforms:
        public_preview, public_status, public_ready = browser_preview(
            douyin_public_service,
            douyin_public_provider,
            Platform.DOUYIN,
            body.published_window_days,
        )
        platform_items.append(
            CrawlerPlatformPreview(
                platform=Platform.DOUYIN.value,
                platform_label="抖音登录搜索（最多30条）",
                cache_hit=public_preview.cache_hit,
                estimated_api_calls=0,
                platform_unit_price_cny=0.0,
                estimated_cost_cny=0.0,
                blocked_reason=(
                    None
                    if public_ready
                    else (
                        public_preview.blocked_reason
                        or (public_status.message if public_status else None)
                        or "抖音登录搜索浏览器未连接。"
                    )
                ),
            )
        )

    if Platform.XIAOHONGSHU in selected_platforms:
        xiaohongshu_preview, xiaohongshu_status, xiaohongshu_ready = browser_preview(
            xiaohongshu_service,
            xiaohongshu_provider,
            Platform.XIAOHONGSHU,
            body.published_window_days,
            requested_count,
        )
        platform_items.append(
            CrawlerPlatformPreview(
                platform=Platform.XIAOHONGSHU.value,
                platform_label="小红书登录搜索",
                cache_hit=xiaohongshu_preview.cache_hit,
                estimated_api_calls=0,
                platform_unit_price_cny=0.0,
                estimated_cost_cny=0.0,
                blocked_reason=(
                    xiaohongshu_preview.blocked_reason
                    or (
                        xiaohongshu_status.message
                        if xiaohongshu_status is not None and not xiaohongshu_ready
                        else None
                    )
                ),
            )
        )

    for platform, service, provider in (
        (Platform.KUAISHOU, kuaishou_service, kuaishou_provider),
        (Platform.BILIBILI, bilibili_service, bilibili_provider),
    ):
        if platform not in selected_platforms:
            continue
        preview, status, ready = browser_preview(
            service,
            provider,
            platform,
            body.published_window_days,
        )
        platform_items.append(
            CrawlerPlatformPreview(
                platform=platform.value,
                platform_label=f"{_platform_label(platform.value)}浏览器搜索（平台默认综合排序）",
                cache_hit=preview.cache_hit,
                estimated_api_calls=0,
                platform_unit_price_cny=0.0,
                estimated_cost_cny=0.0,
                blocked_reason=(
                    preview.blocked_reason
                    or (status.message if status is not None and not ready else None)
                ),
            )
        )
    return CrawlerPreviewResponse(
        keyword=body.keyword.strip(),
        published_window_days=body.published_window_days,
        hotspot_window_hours=None,
        count_per_platform=requested_count,
        force_refresh=body.force_refresh,
        mode="smart",
        provider_mode=ProviderMode.LOCAL_BROWSER.value,
        provider_name="抖音登录搜索 + 小红书登录搜索 + 快手/B站浏览器",
        ranking_mode="platform_default_search_then_table_sort",
        monthly_query_count=0,
        monthly_estimated_cost_cny=0.0,
        monthly_warning_queries=0,
        monthly_hard_limit_queries=0,
        monthly_hard_limit_cost_cny=0.0,
        cache_ttl_minutes=BROWSER_CACHE_TTL_MINUTES,
        platforms=platform_items,
        estimated_total_cost_cny=0.0,
        monitoring_policy="free_single_snapshot_v1",
        sampling_offsets_hours=[0],
        max_api_calls_per_platform=0,
        blocked=all(item.blocked_reason for item in platform_items),
        free_pool_status="ready",
        free_pool_message=(
            "只使用抖音登录搜索、小红书登录搜索、快手和B站浏览器搜索结果；"
            "按本次发布时间条件保留可核验内容，遇到登录或安全验证立即停止；不调用热点宝或 OneAPI。"
        ),
        paid_fallback_required=False,
        paid_fallback_blocked_reason="本次固定使用免费来源，不会调用热点宝或 OneAPI。",
        trend_tracking_enabled=False,
        hotspot_ready=False,
        hotspot_message="本次只使用抖音登录搜索，不使用热点宝。",
        hotspot_time_strategy="douyin_official_search_only",
        target_main_count=requested_count,
        paid_call_cap=0,
        hotspot_result_limit=0,
        hotspot_list_types=[],
    )


def _start_browser_for_search(provider, *, public_only: bool = False):
    """Start a browser on demand without upgrading a public-only source to login."""
    capability = provider.capabilities()
    status = getattr(provider, "session_status", lambda: None)()
    if not capability.enabled or (status is not None and status.running):
        return status
    start = getattr(
        provider,
        "start_public_browser" if public_only else "start_login_browser",
        None,
    )
    if start is None:
        return status
    return start()


def _copy_search_matrix(keyword: str, related_terms: list[str]) -> list[str]:
    """Build bounded intent variants without widening into unrelated topics."""
    base = keyword.strip()
    queries: list[str] = []
    seen: set[str] = set()
    for value in (
        base,
        *_related_terms(related_terms),
        *(f"{base}{suffix}" for suffix in COPY_SEARCH_SUFFIXES),
    ):
        query = str(value or "").strip()
        normalized = normalized_keyword_text(query)
        if not normalized or len(query) > 50 or normalized in seen:
            continue
        seen.add(normalized)
        queries.append(query)
        if len(queries) >= COPY_SEARCH_MATRIX_LIMIT:
            break
    return queries


def _direct_match_keyword(batch: SearchBatch, match, candidate) -> str | None:
    """Return the search term that directly appears in a title/topic, if any.

    The broader result table keeps all readable platform-search cards.  This
    helper is deliberately retained for the narrower copy-probe queue, where
    an explicit title/topic signal is still required before downloading media.
    """
    evidence = match.evidence or candidate.evidence
    matched_query = str(getattr(match, "keyword", "") or batch.keyword).strip()
    for query in dict.fromkeys((matched_query, batch.keyword.strip())):
        if query and item_matches_keyword(
            title=candidate.title,
            keyword=query,
            evidence=evidence,
        ):
            return query
    return None


def _copy_match_is_relevant(batch: SearchBatch, match, candidate) -> bool:
    """Keep strict relevance for the optional local copy-probe queue."""
    return _direct_match_keyword(batch, match, candidate) is not None


def _is_direct_public_douyin_copy_reference(match, candidate) -> bool:
    """Allow only a narrow, ASR-gated public-search path below the hot floor.

    The title/hashtag match is still only a discovery hint.  Such a candidate
    cannot become visible or count toward the copy pool until the separate
    local copy probe detects speech.
    """
    evidence = str(getattr(match, "evidence", None) or candidate.evidence or "")
    if (
        candidate.platform != Platform.DOUYIN
        or str(getattr(match, "provider_name", "")) != "douyin_public_browser_v2"
        or not evidence.startswith("douyin_public_search:")
        or candidate.source_url is None
    ):
        return False
    seed = _spoken_seed_quality(
        title=candidate.title,
        keyword=str(getattr(match, "keyword", "") or candidate.title),
        evidence=evidence,
    )
    return seed["status"] == "writeable"


def _is_copy_probe_eligible(match, candidate) -> bool:
    """Keep the hot main board strict while letting one verified content lane probe."""
    return _passes_main_board_heat_floor(
        candidate
    ) or _is_direct_public_douyin_copy_reference(match, candidate)


def _has_detected_copy_probe(repo, candidate_id: str) -> bool:
    probe = repo.get_candidate_copy_probe(candidate_id)
    return _is_detected_copy_probe(probe)


def _is_supported_copy_probe(probe: CandidateCopyProbe | None) -> bool:
    return bool(probe is not None and probe.version in COPY_PROBE_SUPPORTED_VERSIONS)


def _is_detected_copy_probe(probe: CandidateCopyProbe | None) -> bool:
    return bool(_is_supported_copy_probe(probe) and probe.status == "detected")


def _should_widen_legacy_no_text_probe(probe: CandidateCopyProbe | None) -> bool:
    """Recheck only the old three-second miss once; detected results stay reused."""
    return bool(
        probe is not None
        and probe.version == COPY_PROBE_LEGACY_VERSION
        and probe.status == "no_text"
    )


def _has_legacy_no_text_probe(batch: SearchBatch, repo) -> bool:
    """Keep an old batch's wider audio recheck free of additional page searches."""
    return any(
        _should_widen_legacy_no_text_probe(
            repo.get_candidate_copy_probe(candidate.video_id)
        )
        for _, candidate in _copy_probe_candidates(batch, repo)
    )


def _recheck_legacy_no_text_probes(
    batch: SearchBatch,
    repo,
    service,
) -> int:
    """Widen only saved three-second misses; this path never searches pages."""
    attempts = 0
    blocked_platforms: set[Platform] = set()
    for _, candidate in _copy_probe_candidates(batch, repo):
        if (
            attempts >= COPY_PROBE_MAX_ATTEMPTS
            or candidate.platform in blocked_platforms
        ):
            break
        existing = repo.get_candidate_copy_probe(candidate.video_id)
        if not _should_widen_legacy_no_text_probe(existing):
            continue
        attempts += 1
        probe = service.probe(candidate)
        repo.save_candidate_copy_probe(probe)
        if probe.status in {"failed", "unsupported"} and _browser_safety_event(
            probe.message
        ):
            blocked_platforms.add(candidate.platform)
    return attempts


def _copy_probe_candidates(
    batch: SearchBatch,
    repo,
    *,
    blocked_platforms: set[Platform] | None = None,
) -> list[tuple[Any, Any]]:
    """Return a de-duplicated global queue; title heuristics only control probe order."""
    blocked = blocked_platforms or set()
    best: dict[str, tuple[tuple[Any, ...], Any, Any]] = {}
    for run in repo.list_platform_search_runs(batch.batch_id):
        if run.platform == Platform.XIAOHONGSHU or run.platform in blocked:
            continue
        match_run_id = run.cached_from_run_id or run.run_id
        for match in repo.list_candidate_matches(match_run_id):
            candidate = repo.get_candidate(match.video_id)
            if (
                candidate is None
                or candidate.platform in blocked
                or not _copy_match_is_relevant(batch, match, candidate)
                or not _is_copy_probe_eligible(match, candidate)
            ):
                continue
            seed = _spoken_seed_quality(
                title=candidate.title,
                keyword=str(getattr(match, "keyword", "") or batch.keyword),
                evidence=match.evidence or candidate.evidence,
            )
            sort_key = (
                candidate.source_url is None,
                not _passes_main_board_heat_floor(candidate),
                -int(seed["score"]),
                -(candidate.metrics.plays or 0),
                -_interaction_heat(candidate),
                match.platform_rank,
                candidate.video_id,
            )
            previous = best.get(candidate.video_id)
            if previous is None or sort_key < previous[0]:
                best[candidate.video_id] = (sort_key, match, candidate)
    return [
        (match, candidate)
        for _, match, candidate in sorted(best.values(), key=lambda item: item[0])
    ]


def _probe_copy_candidates(
    batch: SearchBatch,
    repo,
    service,
    *,
    attempts: int,
    blocked_platforms: set[Platform],
) -> tuple[int, int, set[Platform]]:
    """Probe only unclassified candidates and stop at the fixed no-cost budget."""
    detected_ids = {
        candidate.video_id
        for _, candidate in _copy_probe_candidates(batch, repo)
        if (
            (existing := repo.get_candidate_copy_probe(candidate.video_id)) is not None
            and _is_detected_copy_probe(existing)
        )
    }
    for _, candidate in _copy_probe_candidates(
        batch, repo, blocked_platforms=blocked_platforms
    ):
        existing = repo.get_candidate_copy_probe(candidate.video_id)
        if existing is not None:
            if _is_detected_copy_probe(existing):
                detected_ids.add(candidate.video_id)
                continue
            if not _should_widen_legacy_no_text_probe(existing):
                continue
        if (
            attempts >= COPY_PROBE_MAX_ATTEMPTS
            or len(detected_ids) >= COPY_PROBE_TARGET_DETECTIONS
        ):
            break
        attempts += 1
        probe = service.probe(candidate)
        repo.save_candidate_copy_probe(probe)
        if probe.status == "detected":
            detected_ids.add(candidate.video_id)
        if probe.status in {"failed", "unsupported"} and _browser_safety_event(
            probe.message
        ):
            blocked_platforms.add(candidate.platform)
    return attempts, len(detected_ids), blocked_platforms


def _append_copy_search_variant(
    batch: SearchBatch,
    query: str,
    *,
    repo,
    douyin_public_service,
    douyin_public_provider,
    kuaishou_service,
    kuaishou_provider,
    bilibili_service,
    bilibili_provider,
    blocked_platforms: set[Platform],
) -> tuple[SearchBatch, list[str]]:
    """Search one bounded intent variant on the already-open public browser sessions."""
    errors: list[str] = []
    supplemental_batches: list[SearchBatch] = []
    for service, provider, platform, window_days, result_limit in (
        (
            douyin_public_service,
            douyin_public_provider,
            Platform.DOUYIN,
            DOUYIN_PUBLIC_SEARCH_PUBLISHED_WINDOW_DAYS,
            DOUYIN_PUBLIC_SEARCH_RESULT_LIMIT,
        ),
        (
            kuaishou_service,
            kuaishou_provider,
            Platform.KUAISHOU,
            KUAISHOU_PUBLISHED_WINDOW_DAYS,
            batch.requested_count_per_platform,
        ),
        (
            bilibili_service,
            bilibili_provider,
            Platform.BILIBILI,
            BILIBILI_PUBLISHED_WINDOW_DAYS,
            batch.requested_count_per_platform,
        ),
    ):
        if platform in blocked_platforms:
            continue
        status = getattr(provider, "session_status", lambda: None)()
        ready = bool(
            provider.capabilities().enabled and status and status.ready_to_crawl
        )
        if not ready:
            errors.append(
                status.message
                if status is not None
                else f"{_platform_label(platform.value)}浏览器未连接。"
            )
            continue
        try:
            supplemental = service.execute(
                keyword=query,
                published_window_days=window_days,
                count=result_limit,
                # 扩展词只复用正常缓存，不强制刷新。抖音只访问一页官网
                # 搜索结果，避免为补充素材重复打开热点宝或放大平台访问。
                force_refresh=False,
                platforms=(platform,),
                cache_ttl_minutes=10,
                schedule_recrawls=False,
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        supplemental_batches.append(supplemental)
        if supplemental.error:
            errors.append(supplemental.error)
            if _browser_safety_event(supplemental.error):
                blocked_platforms.add(platform)

    moved_runs = repo.list_platform_search_runs(batch.batch_id)
    for supplemental in supplemental_batches:
        if supplemental.batch_id == batch.batch_id:
            continue
        for run in repo.list_platform_search_runs(supplemental.batch_id):
            moved = run.model_copy(update={"batch_id": batch.batch_id})
            repo.save_platform_search_run(moved)
            moved_runs.append(moved)
        repo.delete_search_batch(supplemental.batch_id)

    executed = [*batch.copy_search_queries]
    if supplemental_batches:
        executed.append(query)
    updated = batch.model_copy(
        update={
            "platform_run_ids": [run.run_id for run in moved_runs],
            "copy_search_queries": executed,
            "finished_at": datetime.now().astimezone(),
        }
    )
    repo.save_search_batch(updated)
    return updated, errors


def _execute_free_multi_platform_batch(
    body: CrawlerSearchRequest,
    bilibili_service,
    bilibili_provider,
    bilibili_metrics_provider,
    douyin_public_service,
    douyin_public_provider,
    xiaohongshu_service,
    xiaohongshu_provider,
    kuaishou_service,
    kuaishou_provider,
    repo,
    progress_callback=None,
) -> CrawlerBatchResponse:
    """Collect a bounded, broad-recall set from selected public platforms.

    ``progress_callback`` is internal-only and used by the persistent queue.
    It reports completed platform batches; the synchronous HTTP contract remains
    unchanged for older callers.
    """
    selected_platforms = _selected_free_platforms(body)
    requested_count = _free_requested_count(body)
    batches: list[SearchBatch] = []
    errors: list[str] = []
    browser_statuses: dict[Platform, Any] = {}
    browser_sources = (
        (Platform.DOUYIN, douyin_public_service),
        (Platform.XIAOHONGSHU, xiaohongshu_service),
        (Platform.KUAISHOU, kuaishou_service),
        (Platform.BILIBILI, bilibili_service),
    )
    browser_cache_hits: dict[Platform, bool] = {}

    def report_progress(
        stage: str,
        message: str,
        current_batch=None,
        candidate: CrawlerCandidateResult | None = None,
        platform: str | None = None,
    ) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(stage, message, current_batch, candidate, platform)
        except Exception:
            # Diagnostics must never turn a successful platform search into a failure.
            return

    def report_platform_progress(event, current_batch) -> None:
        if progress_callback is None:
            return
        progress_callback(
            str(event.get("stage") or "scanning"),
            str(event.get("message") or "正在扫描平台结果。"),
            current_batch,
            event.get("candidate"),
            str(event.get("platform") or "") or None,
        )

    for platform, service in browser_sources:
        if platform not in selected_platforms:
            continue
        result_count = (
            min(requested_count, DOUYIN_PUBLIC_SEARCH_RESULT_LIMIT)
            if platform == Platform.DOUYIN
            else requested_count
        )
        search_options = (
            {
                "kuaishou_sort": body.kuaishou_sort,
                "kuaishou_duration_bucket": body.kuaishou_duration_bucket,
            }
            if platform == Platform.KUAISHOU
            else {}
        )
        effective_published_window_days = (
            0 if platform == Platform.KUAISHOU else body.published_window_days
        )
        browser_cache_hits[platform] = service.preview(
            keyword=body.keyword,
            published_window_days=effective_published_window_days,
            count=result_count,
            force_refresh=body.force_refresh,
            platforms=(platform,),
            cache_ttl_minutes=BROWSER_CACHE_TTL_MINUTES,
            include_monitoring=False,
            **search_options,
        )[0].cache_hit
    for platform, provider in (
        (Platform.XIAOHONGSHU, xiaohongshu_provider),
        (Platform.KUAISHOU, kuaishou_provider),
        (Platform.BILIBILI, bilibili_provider),
    ):
        if platform not in selected_platforms:
            continue
        if browser_cache_hits.get(platform, False):
            continue
        try:
            browser_statuses[platform] = _start_browser_for_search(
                provider,
                public_only=False,
            )
        except LicensedProviderError as exc:
            browser_statuses[platform] = None
            errors.append(f"{_platform_label(platform.value)}：{exc}")

    public_status = None
    if Platform.DOUYIN in selected_platforms and not browser_cache_hits.get(
        Platform.DOUYIN, False
    ):
        try:
            public_status = _start_browser_for_search(douyin_public_provider)
        except LicensedProviderError as exc:
            errors.append(f"抖音登录搜索：{exc}")

    def execute_browser_source(
        service,
        provider,
        platform: Platform,
        published_window_days: int,
        *,
        status=None,
        count: int | None = None,
        cache_hit: bool = False,
    ) -> SearchBatch | None:
        status = browser_statuses.get(platform) if status is None else status
        ready = bool(
            provider.capabilities().enabled and status and status.ready_to_crawl
        )
        if not ready and not cache_hit:
            message = status.message if status is not None else f"{_platform_label(platform.value)}浏览器未连接。"
            report_progress("paused", message, platform=platform.value)
            errors.append(message)
            return None
        report_progress(
            "opening_search",
            f"正在打开{_platform_label(platform.value)}搜索页面。",
            platform=platform.value,
        )
        # 真实浏览器采集前抢占平台租约（并发互斥 + 可选 24h 次数上限）；
        # 缓存命中不占用配额。被限时跳过该平台并提示，不影响其他平台。
        lease_id: str | None = None
        if not cache_hit:
            lease_id = _claim_browser_lease(platform, repo, datetime.now().astimezone())
            if lease_id is None:
                errors.append(_browser_safety_status(platform, repo).message)
                return None
        batch: SearchBatch | None = None
        try:
            # 快手公开搜索页没有可验证的发布时间筛选。不要把通用时间条件
            # 伪装成平台筛选，否则会因卡片时间缺失而错误丢弃候选。
            effective_published_window_days = (
                0 if platform == Platform.KUAISHOU else published_window_days
            )
            search_options = (
                {
                    "kuaishou_sort": body.kuaishou_sort,
                    "kuaishou_duration_bucket": body.kuaishou_duration_bucket,
                }
                if platform == Platform.KUAISHOU
                else {}
            )
            execute_options = {
                "keyword": body.keyword,
                "published_window_days": effective_published_window_days,
                "count": count or requested_count,
                "force_refresh": body.force_refresh,
                "platforms": (platform,),
                "cache_ttl_minutes": BROWSER_CACHE_TTL_MINUTES,
                "schedule_recrawls": False,
                **search_options,
            }
            if platform == Platform.DOUYIN and progress_callback is not None:
                execute_options["progress_callback"] = report_platform_progress
            batch = service.execute(**execute_options)
        except ValueError as exc:
            errors.append(str(exc))
            return None
        finally:
            if lease_id is not None:
                error_text = batch.error if batch is not None else ""
                is_safety_event = _browser_safety_event(error_text)
                repo.release_provider_safety_lease(
                    provider=_browser_provider_key(platform),
                    run_id=lease_id,
                    now=datetime.now().astimezone(),
                    cooldown_seconds=BROWSER_COOLDOWN_SECONDS,
                    safety_pause_seconds=(
                        BROWSER_SAFETY_PAUSE_SECONDS if is_safety_event else 0
                    ),
                    safety_reason=(
                        f"{_platform_label(platform.value)}出现安全验证或访问频繁提示，已自动暂停真实采集 24 小时。"
                        if is_safety_event
                        else None
                    ),
                )
        batches.append(batch)
        if batch.error:
            errors.append(batch.error)
        runs = repo.list_platform_search_runs(batch.batch_id)
        latest_run = runs[-1] if runs else None
        if latest_run is not None:
            snapshot = _batch_to_response(batch, repo)
            response_run = next(
                (run for run in snapshot.platform_runs if run.run_id == latest_run.run_id),
                None,
            )
            if response_run is not None:
                seen_candidate_ids: set[str] = set()
                for candidate in [
                    *response_run.candidates,
                    *response_run.low_incremental_candidates,
                ]:
                    if candidate.video_id in seen_candidate_ids:
                        continue
                    seen_candidate_ids.add(candidate.video_id)
                    report_progress(
                        "candidate_found",
                        (
                            f"{_platform_label(platform.value)}已保留 "
                            f"{len(seen_candidate_ids)} 条，正在继续整理。"
                        ),
                        batch,
                        candidate,
                        platform.value,
                    )
            report_progress(
                "platform_complete",
                (
                    f"{_platform_label(platform.value)}已扫描 {latest_run.raw_item_count} 条，"
                    f"解析 {latest_run.parsed_item_count} 条，保留 {latest_run.returned_count} 条。"
                ),
                batch,
                platform=platform.value,
            )
        return batch

    if Platform.DOUYIN in selected_platforms:
        execute_browser_source(
            douyin_public_service,
            douyin_public_provider,
            Platform.DOUYIN,
            body.published_window_days,
            status=public_status,
            count=min(requested_count, DOUYIN_PUBLIC_SEARCH_RESULT_LIMIT),
            cache_hit=browser_cache_hits.get(Platform.DOUYIN, False),
        )
    for platform, service, provider in (
        (Platform.XIAOHONGSHU, xiaohongshu_service, xiaohongshu_provider),
        (Platform.KUAISHOU, kuaishou_service, kuaishou_provider),
        (Platform.BILIBILI, bilibili_service, bilibili_provider),
    ):
        if platform in selected_platforms:
            execute_browser_source(
                service,
                provider,
                platform,
                body.published_window_days,
                count=None,
                cache_hit=(browser_cache_hits.get(platform, False)),
            )

    persisted = [batch for batch in batches if repo.get_search_batch(batch.batch_id)]
    if not persisted:
        failed = SearchBatch(
            keyword=body.keyword.strip(),
            published_window_days=body.published_window_days,
            hotspot_window_hours=None,
            requested_count_per_platform=requested_count,
            kuaishou_sort=body.kuaishou_sort,
            kuaishou_duration_bucket=body.kuaishou_duration_bucket,
            provider="free_multi_platform",
            mode=ProviderMode.LOCAL_BROWSER,
            platforms=list(selected_platforms),
            status=SearchBatchStatus.FAILED,
            error="；".join(errors) or "免费来源暂时没有返回候选。",
            monitoring_policy="free_single_snapshot_v1",
            sampling_offsets_hours=[0],
            copy_search_queries=[body.keyword.strip()],
            copy_related_terms=(
                _related_terms(body.related_terms)
                if body.allow_related_fallback
                else []
            ),
        )
        repo.save_search_batch(failed)
        return _batch_to_response(failed, repo).model_copy(
            update={
                "free_candidate_count": 0,
                "paid_fallback_blocked_reason": "本次固定不调用热点宝或 OneAPI。",
            }
        )

    primary = persisted[0]
    all_runs = repo.list_platform_search_runs(primary.batch_id)
    for supplemental in persisted[1:]:
        supplemental_runs = repo.list_platform_search_runs(supplemental.batch_id)
        for run in supplemental_runs:
            moved = run.model_copy(update={"batch_id": primary.batch_id})
            repo.save_platform_search_run(moved)
            all_runs.append(moved)
        repo.delete_search_batch(supplemental.batch_id)

    # 搜索页已经先保留所有结果；B站公开详情再分批补全一个有界候选池。
    # 详情接口未返回的字段保持空，不把弹幕等相近字段伪装成评论。
    _enrich_bilibili_public_metrics(
        runs=all_runs,
        repo=repo,
        refresh_provider=bilibili_metrics_provider,
        source_service=getattr(bilibili_service, "source_service", None),
        batch_id=primary.batch_id,
    )

    total_candidates = sum(
        run.returned_count
        for run in all_runs
        if run.status
        in {
            PlatformRunStatus.SUCCEEDED,
            PlatformRunStatus.PARTIAL,
            PlatformRunStatus.CACHED,
        }
    )
    successful_platforms = {
        run.platform
        for run in all_runs
        if run.returned_count > 0
        and run.status
        in {
            PlatformRunStatus.SUCCEEDED,
            PlatformRunStatus.PARTIAL,
            PlatformRunStatus.CACHED,
        }
    }
    status = (
        SearchBatchStatus.SUCCEEDED
        if successful_platforms == set(selected_platforms)
        else SearchBatchStatus.PARTIAL
        if successful_platforms
        else SearchBatchStatus.FAILED
    )
    combined = primary.model_copy(
        update={
            "published_window_days": body.published_window_days,
            "hotspot_window_hours": None,
            "requested_count_per_platform": requested_count,
            "kuaishou_sort": body.kuaishou_sort,
            "kuaishou_duration_bucket": body.kuaishou_duration_bucket,
            "provider": "free_multi_platform",
            "mode": ProviderMode.LOCAL_BROWSER,
            "status": status,
            "platforms": list(selected_platforms),
            "platform_run_ids": [run.run_id for run in all_runs],
            "finished_at": datetime.now().astimezone(),
            "error": "；".join(dict.fromkeys(errors)) or None,
            "monitoring_policy": "free_single_snapshot_v1",
            "sampling_offsets_hours": [0],
            "tracking_authorized": False,
            "copy_search_queries": [body.keyword.strip()],
            "copy_related_terms": (
                _related_terms(body.related_terms)
                if body.allow_related_fallback
                else []
            ),
            "copy_matrix_exhausted": False,
        }
    )
    repo.save_search_batch(combined)
    return _batch_to_response(combined, repo).model_copy(
        update={
            "free_candidate_count": total_candidates,
            "paid_fallback_used": False,
            "paid_fallback_blocked_reason": "本次固定只使用抖音登录搜索、小红书登录搜索、快手和B站浏览器搜索；不调用热点宝或 OneAPI。",
            "trend_tracking_enabled": False,
        }
    )


def _enrich_bilibili_public_metrics(
    *,
    runs: list[Any],
    repo,
    refresh_provider,
    source_service,
    batch_id: str,
) -> None:
    """Safely enrich a bounded fresh Bilibili result set in batches of ten.

    A cached search deliberately does not issue fresh detail lookups.  That keeps
    normal cache reuse quiet while a force-refreshed search can improve its metric
    coverage.  Failures are recorded on the platform run but never discard the
    already collected search results.
    """
    capability = refresh_provider.capabilities()
    if not capability.enabled or not capability.supports_metric_refresh:
        return

    eligible_statuses = {
        PlatformRunStatus.SUCCEEDED,
        PlatformRunStatus.PARTIAL,
    }
    bilibili_runs = [
        run
        for run in runs
        if run.platform == Platform.BILIBILI
        and run.status in eligible_statuses
        and not run.cache_hit
        and run.returned_count > 0
    ]
    if not bilibili_runs:
        return

    ranked_candidates: list[tuple[int, Any]] = []
    seen_candidate_ids: set[str] = set()
    for run in bilibili_runs:
        for match in repo.list_candidate_matches(run.run_id):
            if (
                match.platform != Platform.BILIBILI
                or match.video_id in seen_candidate_ids
            ):
                continue
            candidate = repo.get_candidate(match.video_id)
            if candidate is None:
                continue
            platform_item_id = (candidate.platform_item_id or "").strip()
            if not platform_item_id:
                continue
            seen_candidate_ids.add(candidate.video_id)
            ranked_candidates.append((match.platform_rank, candidate))

    ranked_candidates.sort(key=lambda item: (item[0], item[1].video_id))
    candidates = [
        candidate
        for _rank, candidate in ranked_candidates[:BILIBILI_PUBLIC_METRIC_REFRESH_LIMIT]
    ]
    if not candidates:
        return

    updated_count = 0
    incomplete_count = 0
    stopped_early = False
    for offset in range(0, len(candidates), BILIBILI_PUBLIC_METRIC_REFRESH_BATCH_SIZE):
        candidate_batch = candidates[
            offset : offset + BILIBILI_PUBLIC_METRIC_REFRESH_BATCH_SIZE
        ]
        try:
            detail_page = refresh_provider.refresh_metrics(
                Platform.BILIBILI,
                [
                    candidate.platform_item_id or candidate.video_id
                    for candidate in candidate_batch
                ],
                hashlib.sha256(
                    f"{batch_id}|bilibili-public-detail|{offset}".encode("utf-8")
                ).hexdigest(),
            )
        except Exception:
            stopped_early = True
            break

        detail_by_item_id = {
            item.platform_item_id: item for item in detail_page.items
        }
        incomplete_count += len(detail_page.errors)
        for candidate in candidate_batch:
            detail = detail_by_item_id.get(
                candidate.platform_item_id or candidate.video_id
            )
            if detail is None:
                continue
            repo.save_candidate(_merge_bilibili_public_detail(candidate, detail))
            updated_count += 1

    recompute = getattr(source_service, "recompute_all", None)
    if updated_count and callable(recompute):
        recompute()

    message = f"B站公开详情已补全前 {len(candidates)} 条：成功 {updated_count} 条"
    if incomplete_count:
        message += f"；{incomplete_count} 条未返回完整指标"
    if stopped_early:
        message += "；后续批次未完成，已保留搜索结果和已有字段"
    _record_bilibili_metric_refresh_diagnostic(
        bilibili_runs,
        repo=repo,
        message=message,
    )


def _merge_bilibili_public_detail(candidate, detail):
    """Merge a detail response without letting an absent detail field erase search data."""
    previous = candidate.metrics
    refreshed = detail.metrics
    metrics = refreshed.model_copy(
        update={
            "item_id": candidate.video_id,
            "plays": refreshed.plays if refreshed.plays is not None else previous.plays,
            "likes": refreshed.likes if refreshed.likes is not None else previous.likes,
            "comments": (
                refreshed.comments
                if refreshed.comments is not None
                else previous.comments
            ),
            "shares": refreshed.shares
            if refreshed.shares is not None
            else previous.shares,
            "favorites": (
                refreshed.favorites
                if refreshed.favorites is not None
                else previous.favorites
            ),
            "followers": (
                refreshed.followers
                if refreshed.followers is not None
                else previous.followers
            ),
            "confidence": max(previous.confidence, refreshed.confidence),
        }
    )
    retained_warnings = [
        warning
        for warning in candidate.data_quality_warnings
        if not warning.startswith("B站公开搜索未返回")
    ]
    warnings = list(dict.fromkeys([*retained_warnings, *detail.data_quality_warnings]))
    return candidate.model_copy(
        update={
            "title": detail.title or candidate.title,
            "author_id": detail.author_id or candidate.author_id,
            "author_name": detail.author_name or candidate.author_name,
            "published_at": detail.published_at or candidate.published_at,
            "duration_seconds": detail.duration_seconds or candidate.duration_seconds,
            "source_url": detail.source_url or candidate.source_url,
            "evidence": detail.evidence or candidate.evidence,
            "data_quality_warnings": warnings,
            "share_count": metrics.shares,
            "collect_count": metrics.favorites,
            "metrics": metrics,
        }
    )


def _record_bilibili_metric_refresh_diagnostic(runs, *, repo, message: str) -> None:
    for run in runs:
        diagnostics = [part for part in (run.payload_diagnostic, message) if part]
        repo.save_platform_search_run(
            run.model_copy(update={"payload_diagnostic": "；".join(diagnostics)})
        )


def _preview_smart_batch(
    body: CrawlerSearchRequest,
    service,
    hot_pool,
    hotspot_provider,
    hotspot_service,
    repo,
):
    """Preview Hotspot-first discovery; paid fallback always needs opt-in."""
    discovery_capability = service.provider.capabilities()
    hotspot_capability = hotspot_provider.capabilities()
    hotspot_status = getattr(hotspot_provider, "session_status", lambda: None)()
    hotspot_ready = bool(
        hotspot_capability.enabled and hotspot_status and hotspot_status.ready_to_crawl
    )
    if hotspot_ready:
        hotspot_window_label = _hotspot_window_label(body.hotspot_window_hours)
        crawl_safety = _hotspot_safety_status(body, hotspot_service, repo)
        blocked = crawl_safety.state in {
            "safety_pause",
            "cooldown",
            "running",
            "daily_limit",
        }
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
            hotspot_list_types=[
                "视频总榜",
                "低粉爆款",
                "高完播率",
                "高涨粉率",
                "高点赞率",
            ],
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
            blocked_reason=None
            if hotspot_ready
            else (
                hotspot_status.message
                if hotspot_status
                else "请先连接热点宝专用浏览器。"
            ),
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
                    if not item.cache_hit
                    else 0.0
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
        paid_fallback_required=paid_fallback_requested
        and discovery_capability.mode.value == "production",
        paid_fallback_cache_ttl_minutes=(
            SMART_FALLBACK_CACHE_TTL_MINUTES
            if paid_fallback_requested
            and discovery_capability.mode.value == "production"
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
            else (
                hotspot_status.message
                if hotspot_status
                else "请先连接热点宝专用浏览器。"
            )
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


def _deduplicate_smart_runs(
    runs: list[CrawlerPlatformRunResponse],
) -> list[CrawlerPlatformRunResponse]:
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
    *,
    published_window_days: int = 0,
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
            published_window_days=published_window_days,
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
            published_window_days=published_window_days,
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
                        for run in (
                            response.platform_runs if "response" in locals() else []
                        )
                    ),
                ]
                if part
            )
            is_safety_event = any(
                marker in error_text
                for marker in (
                    "安全验证",
                    "访问频繁",
                    "操作频繁",
                    "请求过于频繁",
                    "返回 403",
                    "返回 429",
                )
            )
            released = repo.release_provider_safety_lease(
                provider=HOTSPOT_PROVIDER_KEY,
                run_id=lease_id,
                now=datetime.now().astimezone(),
                cooldown_seconds=cooldown_seconds,
                safety_pause_seconds=HOTSPOT_SAFETY_PAUSE_SECONDS
                if is_safety_event
                else 0,
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
                    else max(
                        1,
                        int(
                            (
                                window_ends_at - datetime.now().astimezone()
                            ).total_seconds()
                        ),
                    )
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
                        f"本次真实采集已完成；下次真实采集约在 {(cooldown_seconds + 59) // 60} 分钟后开放。"
                    )
                )
                or "热点宝安全状态已更新。",
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


def _execute_smart_batch(
    body: CrawlerSearchRequest,
    service,
    hotspot_service,
    hotspot_provider,
    hot_pool,
    repo,
):
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
    if (
        hotspot_provider.capabilities().enabled
        and hotspot_status
        and hotspot_status.running
    ):
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
        hotspot_error = (
            hotspot_status.message
            if hotspot_status is not None
            else "热点宝专用浏览器未连接。"
        )

    hotspot_count = (
        sum(run.relevant_count for run in hotspot_response.platform_runs)
        if hotspot_response is not None
        else 0
    )
    if hotspot_response is not None and (
        free_count + hotspot_count >= body.target_main_count
        or not body.allow_paid_fallback
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
            status="succeeded"
            if candidate_count >= body.target_main_count
            else "partial",
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
            batch_id=(
                free_result.request_id
                if free_result
                else f"hotspot-{int(now.timestamp())}"
            ),
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
            batch_id=(
                free_result.request_id
                if free_result
                else f"smart-{int(now.timestamp())}"
            ),
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


def _parse_keyword_queue(text: str) -> list[str]:
    values = re.split(r"[\r\n,，;；、]+", text)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = value.strip()
        key = keyword.casefold()
        if not keyword or key in seen:
            continue
        if len(keyword) > 50:
            raise HTTPException(status_code=400, detail=f"关键词“{keyword[:20]}…”超过 50 个字符。")
        seen.add(key)
        result.append(keyword)
    if not result:
        raise HTTPException(status_code=400, detail="请至少输入一个关键词。")
    if len(result) > _CRAWLER_QUEUE_MAX_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多处理 {_CRAWLER_QUEUE_MAX_ITEMS} 个关键词，请分批提交。",
        )
    return result


def _crawler_queue_worker_active(queue_id: str) -> bool:
    with _CRAWLER_QUEUE_LOCK:
        future = _CRAWLER_QUEUE_FUTURES.get(queue_id)
        return future is not None and not future.done()


def _valid_crawler_queue_batch_ids(repo, queue: CrawlerKeywordQueue, item: CrawlerKeywordQueueItem) -> list[str]:
    """Keep only saved batches belonging to this queue's keyword/platforms.

    Older queue payloads do not have ``crawler_queue_id`` on their batches, so
    keyword/platform checks remain the compatibility fallback. New progressive
    batches are explicitly bound before their id is exposed to the queue.
    """
    ordered_ids = [item.batch_id, *item.partial_batch_ids]
    valid: list[str] = []
    seen: set[str] = set()
    for batch_id in ordered_ids:
        if not batch_id or batch_id in seen:
            continue
        seen.add(batch_id)
        batch = repo.get_search_batch(batch_id)
        if batch is None:
            continue
        owner = getattr(batch, "crawler_queue_id", None)
        if owner and owner != queue.queue_id:
            continue
        if batch.keyword.casefold() != item.keyword.casefold():
            continue
        if batch.platforms and not set(batch.platforms).intersection(queue.platforms):
            continue
        valid.append(batch_id)
    return valid


def _sanitize_crawler_queue(repo, queue: CrawlerKeywordQueue) -> CrawlerKeywordQueue:
    changed = False
    items: list[CrawlerKeywordQueueItem] = []
    for item in queue.items:
        valid_ids = _valid_crawler_queue_batch_ids(repo, queue, item)
        valid_primary = valid_ids[0] if valid_ids else None
        next_item = item.model_copy(
            update={
                "batch_id": valid_primary,
                "partial_batch_ids": valid_ids,
            }
        )
        if next_item != item:
            changed = True
            logger.warning(
                "crawler_queue_orphan_batch_ignored queue_id=%s item_id=%s keyword=%s",
                queue.queue_id,
                item.item_id,
                item.keyword,
            )
        items.append(next_item)
    if not changed:
        return queue
    sanitized = queue.model_copy(
        update={"items": items, "updated_at": datetime.now().astimezone()}
    )
    repo.save_crawler_keyword_queue(sanitized)
    return sanitized


def _bind_crawler_batch_to_queue(
    repo,
    batch_id: str | None,
    queue_id: str,
    keyword: str,
    platforms: list[Platform],
) -> str | None:
    """Bind and re-read a saved final batch before exposing its id to a queue."""
    if not batch_id:
        return None
    batch = repo.get_search_batch(batch_id)
    if batch is None or batch.keyword.casefold() != keyword.casefold():
        return None
    if batch.platforms and not set(batch.platforms).intersection(platforms):
        return None
    bound = batch.model_copy(update={"crawler_queue_id": queue_id})
    repo.save_search_batch(bound)
    verified = repo.get_search_batch(batch_id)
    if verified is None or getattr(verified, "crawler_queue_id", None) != queue_id:
        return None
    return batch_id


def _crawler_batch_failure_message(result: CrawlerBatchResponse) -> str:
    """Return a user-actionable error even when a provider lost its detail.

    A failed queue item must never be persisted with both ``error`` and
    ``progress_message`` empty. Provider adapters may only set a structured
    platform error, and older result objects may omit even that; preserve all
    available detail and finish with a safe fallback.
    """
    messages: list[str] = []
    for run in result.platform_runs:
        run_messages: list[str] = []
        if run.error:
            run_messages.append(str(run.error).strip())
        for error in run.errors or []:
            if isinstance(error, dict):
                message = error.get("message") or error.get("detail") or error.get("error")
            else:
                message = str(error)
            if message:
                run_messages.append(str(message).strip())
        if run_messages:
            label = run.platform_label or run.platform
            messages.append(f"{label}：{'；'.join(run_messages)}")
        elif run.status in {"failed", "blocked", "paused"}:
            messages.append(f"{run.platform_label or run.platform}暂未完成搜索。")
    if result.error:
        messages.insert(0, str(result.error).strip())
    unique_messages = list(dict.fromkeys(message for message in messages if message))
    return "；".join(unique_messages) or "本次找素材未完成，请检查平台状态后再试。"


def _progress_candidates(item: CrawlerKeywordQueueItem) -> list[CrawlerCandidateResult]:
    candidates: list[CrawlerCandidateResult] = []
    seen: set[str] = set()
    for payload in item.progress_candidates:
        try:
            candidate = CrawlerCandidateResult.model_validate(payload)
        except Exception:
            continue
        if candidate.video_id in seen:
            continue
        seen.add(candidate.video_id)
        candidates.append(candidate)
    return candidates


def _progress_candidate_identity(payload: dict) -> str:
    """Return a stable identity across provisional and completed candidates."""
    video_id = str(payload.get("video_id") or "").strip()
    platform = str(payload.get("platform") or "").strip().casefold()
    prefix = f"{platform}-" if platform else ""
    if prefix and video_id.casefold().startswith(prefix):
        return video_id[len(prefix) :]
    return video_id


def _crawler_queue_response(
    queue: CrawlerKeywordQueue,
    repo=None,
) -> CrawlerKeywordQueueResponse:
    if repo is not None:
        queue = _sanitize_crawler_queue(repo, queue)
    completed_statuses = {
        KeywordQueueItemStatus.SUCCEEDED,
        KeywordQueueItemStatus.PARTIAL,
    }
    completed = sum(item.status in completed_statuses for item in queue.items)
    return CrawlerKeywordQueueResponse(
        queue_id=queue.queue_id,
        status=queue.status.value,
        platforms=[item.value for item in queue.platforms],
        published_window_days=queue.published_window_days,
        count_per_platform=queue.requested_count_per_platform,
        created_at=queue.created_at,
        updated_at=queue.updated_at,
        finished_at=queue.finished_at,
        total=len(queue.items),
        completed=completed,
        queued=sum(item.status == KeywordQueueItemStatus.QUEUED for item in queue.items),
        running=sum(item.status == KeywordQueueItemStatus.RUNNING for item in queue.items),
        failed=sum(item.status == KeywordQueueItemStatus.FAILED for item in queue.items),
        worker_active=_crawler_queue_worker_active(queue.queue_id),
        items=[
            CrawlerKeywordQueueItemResponse(
                item_id=item.item_id,
                keyword=item.keyword,
                status=item.status.value,
                batch_id=item.batch_id,
                partial_batch_ids=item.partial_batch_ids,
                error=item.error,
                progress_stage=item.progress_stage,
                progress_platform=(
                    item.progress_platform.value
                    if isinstance(item.progress_platform, Platform)
                    else item.progress_platform
                ),
                progress_message=item.progress_message,
                scanned_count=item.scanned_count,
                parsed_count=item.parsed_count,
                retained_count=item.retained_count,
                progress_candidates=_progress_candidates(item),
                started_at=item.started_at,
                finished_at=item.finished_at,
            )
            for item in queue.items
        ],
        message=queue.error,
    )


def _save_crawler_queue(repo, queue: CrawlerKeywordQueue, **updates) -> CrawlerKeywordQueue:
    updated = queue.model_copy(
        update={"updated_at": datetime.now().astimezone(), **updates}
    )
    repo.save_crawler_keyword_queue(updated)
    return updated


def _save_crawler_queue_item_result(
    repo,
    queue_id: str,
    fallback_queue: CrawlerKeywordQueue,
    item_update: CrawlerKeywordQueueItem,
) -> CrawlerKeywordQueue:
    # Pause/cancel can be clicked while the browser search is still running.
    # Reload the queue before merging the result so the worker cannot overwrite
    # the user's newer control state with its stale RUNNING snapshot.
    latest_queue = repo.get_crawler_keyword_queue(queue_id) or fallback_queue
    updated_items = [
        item_update if value.item_id == item_update.item_id else value
        for value in latest_queue.items
    ]
    return _save_crawler_queue(repo, latest_queue, items=updated_items)


def _save_crawler_queue_item_progress(
    repo,
    queue_id: str,
    item_id: str,
    **updates,
) -> CrawlerKeywordQueue | None:
    """Persist progress without overwriting a user's pause/cancel decision."""
    latest_queue = repo.get_crawler_keyword_queue(queue_id)
    if latest_queue is None:
        return None
    current_item = next(
        (item for item in latest_queue.items if item.item_id == item_id), None
    )
    if current_item is None:
        return latest_queue
    return _save_crawler_queue_item_result(
        repo,
        queue_id,
        latest_queue,
        current_item.model_copy(update=updates),
    )


def _run_crawler_keyword_queue(queue_id: str) -> None:
    repo = get_repository()
    try:
        dependencies = {
            "douyin_public_service": get_douyin_public_search_service(),
            "douyin_public_provider": get_douyin_public_browser_provider(),
            "bilibili_service": get_bilibili_browser_search_service(),
            "bilibili_provider": get_bilibili_browser_provider(),
            "bilibili_metrics_provider": get_bilibili_public_metrics_provider(),
            "xiaohongshu_service": get_xiaohongshu_browser_search_service(),
            "xiaohongshu_provider": get_xiaohongshu_browser_provider(),
            "kuaishou_service": get_kuaishou_browser_search_service(),
            "kuaishou_provider": get_kuaishou_browser_provider(),
        }
        while True:
            queue = repo.get_crawler_keyword_queue(queue_id)
            if queue is None or queue.status in {
                KeywordQueueStatus.PAUSED,
                KeywordQueueStatus.CANCELLED,
            }:
                return
            queue = _sanitize_crawler_queue(repo, queue)
            item = next(
                (
                    value
                    for value in queue.items
                    if value.status == KeywordQueueItemStatus.QUEUED
                ),
                None,
            )
            if item is None:
                failed = any(value.status == KeywordQueueItemStatus.FAILED for value in queue.items)
                partial = any(value.status == KeywordQueueItemStatus.PARTIAL for value in queue.items)
                final_status = (
                    KeywordQueueStatus.PARTIAL
                    if failed or partial
                    else KeywordQueueStatus.SUCCEEDED
                )
                _save_crawler_queue(
                    repo,
                    queue,
                    status=final_status,
                    finished_at=datetime.now().astimezone(),
                )
                return

            started_at = datetime.now().astimezone()
            running_item = item.model_copy(
                update={"status": KeywordQueueItemStatus.RUNNING, "started_at": started_at}
            )
            running_items = [
                running_item if value.item_id == item.item_id else value
                for value in queue.items
            ]
            queue = _save_crawler_queue(
                repo,
                queue,
                items=running_items,
                status=KeywordQueueStatus.RUNNING,
                finished_at=None,
            )
            _save_crawler_queue_item_progress(
                repo,
                queue_id,
                item.item_id,
                progress_stage="preparing",
                progress_message="任务已保存，正在准备浏览器。",
            )
            body = CrawlerSearchRequest(
                keyword=item.keyword,
                platforms=[value.value for value in queue.platforms],
                published_window_days=queue.published_window_days,
                count_per_platform=queue.requested_count_per_platform,
                target_main_count=queue.requested_count_per_platform,
                force_refresh=queue.force_refresh,
                mode="smart",
                track_trend=False,
                allow_paid_fallback=False,
            )

            def report_progress(
                stage: str,
                message: str,
                current_batch=None,
                candidate: CrawlerCandidateResult | None = None,
                platform: str | None = None,
            ) -> None:
                latest_queue = repo.get_crawler_keyword_queue(queue_id)
                latest_item = next(
                    (
                        value
                        for value in (latest_queue.items if latest_queue else [])
                        if value.item_id == item.item_id
                    ),
                    None,
                )
                scanned = latest_item.scanned_count if latest_item else 0
                parsed = latest_item.parsed_count if latest_item else 0
                retained = latest_item.retained_count if latest_item else 0
                partial_batch_ids = list(latest_item.partial_batch_ids) if latest_item else []
                progress_candidates = (
                    list(latest_item.progress_candidates) if latest_item else []
                )
                if candidate is not None:
                    if not isinstance(candidate, CrawlerCandidateResult):
                        try:
                            candidate = _provider_item_to_crawler_response(
                                candidate,
                                keyword=item.keyword,
                            )
                        except Exception:
                            candidate = None
                if candidate is not None:
                    payload = candidate.model_dump(mode="json")
                    candidate_id = _progress_candidate_identity(payload)
                    replaced = False
                    for index, existing in enumerate(progress_candidates):
                        if _progress_candidate_identity(existing) == candidate_id:
                            # Keep the provisional ID as the React/queue key while
                            # replacing its fields with the completed candidate.
                            # This prevents a platform prefix change from creating
                            # a second visible item.
                            if existing.get("video_id"):
                                payload["video_id"] = existing["video_id"]
                            progress_candidates[index] = payload
                            replaced = True
                            break
                    if not replaced:
                        progress_candidates.append(payload)
                if current_batch is not None:
                    # Bind the durable batch as soon as the service has saved it.
                    # Provisional callback snapshots are ignored by the binder,
                    # so an unsaved batch can never become a queue-owned record.
                    _bind_crawler_batch_to_queue(
                        repo,
                        current_batch.batch_id,
                        queue_id,
                        item.keyword,
                        queue.platforms,
                    )
                    runs = repo.list_platform_search_runs(current_batch.batch_id)
                    if platform:
                        runs = [run for run in runs if run.platform.value == platform]
                    if runs:
                        run = runs[-1]
                        scanned = max(scanned, run.raw_item_count)
                        parsed = max(parsed, run.parsed_item_count)
                        retained = max(retained, run.returned_count)
                _save_crawler_queue_item_progress(
                    repo,
                    queue_id,
                    item.item_id,
                    progress_stage=stage,
                    progress_platform=platform or (
                        latest_item.progress_platform if latest_item else None
                    ),
                    progress_message=message,
                    partial_batch_ids=partial_batch_ids,
                    scanned_count=scanned,
                    parsed_count=parsed,
                    retained_count=retained,
                    progress_candidates=progress_candidates,
                )

            def current_item_snapshot() -> CrawlerKeywordQueueItem:
                latest_queue = repo.get_crawler_keyword_queue(queue_id)
                latest_item = next(
                    (
                        value
                        for value in (latest_queue.items if latest_queue else [])
                        if value.item_id == item.item_id
                    ),
                    None,
                )
                return latest_item or running_item

            def report_platform_progress(event, current_batch) -> None:
                report_progress(
                    str(event.get("stage") or "scanning"),
                    str(event.get("message") or "正在扫描平台结果。"),
                    current_batch,
                    event.get("candidate"),
                    platform=str(event.get("platform") or "") or None,
                )

            try:
                result = _execute_free_multi_platform_batch(
                    body,
                    repo=repo,
                    progress_callback=report_progress,
                    **dependencies,
                )
                canonical_batch_id = _bind_crawler_batch_to_queue(
                    repo,
                    result.batch_id,
                    queue_id,
                    item.keyword,
                    queue.platforms,
                )
                item_status = (
                    KeywordQueueItemStatus.PARTIAL
                    if result.status == "partial"
                    else KeywordQueueItemStatus.SUCCEEDED
                    if result.status in {"succeeded", "cached"}
                    else KeywordQueueItemStatus.FAILED
                )
                item_error = (
                    _crawler_batch_failure_message(result)
                    if item_status == KeywordQueueItemStatus.FAILED
                    else None
                )
                item_update = current_item_snapshot().model_copy(
                    update={
                        "status": item_status,
                        "batch_id": canonical_batch_id,
                        "partial_batch_ids": (
                            [canonical_batch_id] if canonical_batch_id else []
                        ),
                        "error": item_error,
                        "progress_stage": "completed" if item_status != KeywordQueueItemStatus.FAILED else "failed",
                        "progress_message": (
                            "已整理最终结果。"
                            if item_status != KeywordQueueItemStatus.FAILED
                            else item_error
                        ),
                        "scanned_count": max(
                            running_item.scanned_count,
                            max(
                                (run.raw_item_count for run in result.platform_runs),
                                default=0,
                            ),
                        ),
                        "parsed_count": max(
                            running_item.parsed_count,
                            max(
                                (run.parsed_item_count for run in result.platform_runs),
                                default=0,
                            ),
                        ),
                        "retained_count": max(
                            running_item.retained_count,
                            result.total_candidates,
                        ),
                        "finished_at": datetime.now().astimezone(),
                    }
                )
            except HTTPException as exc:
                item_update = current_item_snapshot().model_copy(
                    update={
                        "status": KeywordQueueItemStatus.FAILED,
                        "error": str(exc.detail),
                        "progress_stage": "failed",
                        "progress_message": str(exc.detail),
                        "finished_at": datetime.now().astimezone(),
                    }
                )
            except Exception as exc:
                item_update = current_item_snapshot().model_copy(
                    update={
                        "status": KeywordQueueItemStatus.FAILED,
                        "error": f"找素材失败：{str(exc)[:240] or '暂时无法完成'}",
                        "progress_stage": "failed",
                        "progress_message": f"找素材失败：{str(exc)[:240] or '暂时无法完成'}",
                        "finished_at": datetime.now().astimezone(),
                    }
                )
            _save_crawler_queue_item_result(repo, queue_id, queue, item_update)
    finally:
        should_restart = False
        with _CRAWLER_QUEUE_LOCK:
            _CRAWLER_QUEUE_FUTURES.pop(queue_id, None)
            latest = repo.get_crawler_keyword_queue(queue_id)
            should_restart = bool(
                latest
                and latest.status == KeywordQueueStatus.QUEUED
                and any(
                    item.status == KeywordQueueItemStatus.QUEUED
                    for item in latest.items
                )
            )
        # A pause/resume request can arrive while this worker is unwinding.
        # Re-check after removing the finished future so resume cannot leave a
        # queued task stranded behind a just-finished worker.
        if should_restart:
            _start_crawler_keyword_queue(queue_id)


def _start_crawler_keyword_queue(queue_id: str) -> None:
    with _CRAWLER_QUEUE_LOCK:
        future = _CRAWLER_QUEUE_FUTURES.get(queue_id)
        if future is not None and not future.done():
            return
        _CRAWLER_QUEUE_FUTURES[queue_id] = _CRAWLER_QUEUE_EXECUTOR.submit(
            _run_crawler_keyword_queue, queue_id
        )


@router.post("/keyword-queues", response_model=CrawlerKeywordQueueResponse)
def create_crawler_keyword_queue(
    body: CrawlerKeywordQueueRequest,
    repo=Depends(get_repository),
):
    keywords = _parse_keyword_queue(body.keywords)
    queue = CrawlerKeywordQueue(
        items=[CrawlerKeywordQueueItem(keyword=value) for value in keywords],
        platforms=[Platform(value) for value in dict.fromkeys(body.platforms)],
        published_window_days=body.published_window_days,
        requested_count_per_platform=body.count_per_platform,
        force_refresh=body.force_refresh,
    )
    repo.save_crawler_keyword_queue(queue)
    _start_crawler_keyword_queue(queue.queue_id)
    return _crawler_queue_response(queue, repo)


@router.get("/keyword-queues", response_model=list[CrawlerKeywordQueueResponse])
def list_crawler_keyword_queues(
    limit: int = 20,
    repo=Depends(get_repository),
):
    safe_limit = max(1, min(limit, 50))
    return [_crawler_queue_response(item, repo) for item in repo.list_crawler_keyword_queues(safe_limit)]


@router.get("/keyword-queues/{queue_id}", response_model=CrawlerKeywordQueueResponse)
def get_crawler_keyword_queue(queue_id: str, repo=Depends(get_repository)):
    queue = repo.get_crawler_keyword_queue(queue_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="批量找素材任务不存在。")
    return _crawler_queue_response(queue, repo)


@router.post("/keyword-queues/{queue_id}/pause", response_model=CrawlerKeywordQueueResponse)
def pause_crawler_keyword_queue(queue_id: str, repo=Depends(get_repository)):
    queue = repo.get_crawler_keyword_queue(queue_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="批量找素材任务不存在。")
    if queue.status in {KeywordQueueStatus.SUCCEEDED, KeywordQueueStatus.CANCELLED}:
        return _crawler_queue_response(queue, repo)
    return _crawler_queue_response(_save_crawler_queue(repo, queue, status=KeywordQueueStatus.PAUSED), repo)


@router.post("/keyword-queues/{queue_id}/resume", response_model=CrawlerKeywordQueueResponse)
def resume_crawler_keyword_queue(queue_id: str, repo=Depends(get_repository)):
    queue = repo.get_crawler_keyword_queue(queue_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="批量找素材任务不存在。")
    if queue.status == KeywordQueueStatus.CANCELLED:
        raise HTTPException(status_code=409, detail="已取消的批量任务不能继续；请重新粘贴关键词。")
    with _CRAWLER_QUEUE_LOCK:
        active = (
            (future := _CRAWLER_QUEUE_FUTURES.get(queue_id)) is not None
            and not future.done()
        )
    # If the current keyword is still running, let it finish once and keep its
    # RUNNING state. Resetting it here would cause a duplicate batch after a
    # quick pause/resume click. A worker that was lost after a process restart
    # has no active future, so its RUNNING item is safely re-queued.
    reset_items = [
        item.model_copy(
            update={
                "status": KeywordQueueItemStatus.QUEUED,
                "batch_id": None,
                "error": None,
                "started_at": None,
                "finished_at": None,
            }
        )
        if item.status == KeywordQueueItemStatus.FAILED
        or (item.status == KeywordQueueItemStatus.RUNNING and not active)
        else item
        for item in queue.items
    ]
    queue = _save_crawler_queue(
        repo,
        queue,
        items=reset_items,
        status=KeywordQueueStatus.QUEUED,
        error=None,
        finished_at=None,
    )
    _start_crawler_keyword_queue(queue.queue_id)
    return _crawler_queue_response(queue, repo)


@router.post("/keyword-queues/{queue_id}/cancel", response_model=CrawlerKeywordQueueResponse)
def cancel_crawler_keyword_queue(queue_id: str, repo=Depends(get_repository)):
    queue = repo.get_crawler_keyword_queue(queue_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="批量找素材任务不存在。")
    cancelled_items = [
        item.model_copy(update={"status": KeywordQueueItemStatus.CANCELLED})
        if item.status == KeywordQueueItemStatus.QUEUED
        else item
        for item in queue.items
    ]
    queue = _save_crawler_queue(
        repo,
        queue,
        items=cancelled_items,
        status=KeywordQueueStatus.CANCELLED,
        finished_at=datetime.now().astimezone(),
    )
    return _crawler_queue_response(queue, repo)


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
def execute_due_recrawls():
    """复采执行入口已关闭。"""
    raise HTTPException(
        status_code=410,
        detail="复采功能已关闭；系统只执行用户当次发起的搜索。",
    )


@router.get("/batches/{batch_id}/selection", response_model=CrawlerBatchResponse)
def get_crawler_batch_for_selection(
    batch_id: str,
    repo=Depends(get_repository),
):
    """快速恢复素材选择，只返回挑选所需字段，不逐条加载转写与趋势明细。"""
    batch = repo.get_search_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="搜索批次不存在。")
    return _batch_to_response(batch, repo, include_runtime_details=False)


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


@router.get("/batches/{batch_id}/export.csv")
def export_crawler_batch_csv(
    batch_id: str,
    repo=Depends(get_repository),
):
    """导出当前批次可见候选的固定字段 CSV，不包含浏览器或认证信息。"""
    batch = repo.get_search_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="搜索批次不存在。")
    response = _batch_to_response(batch, repo)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        ["关键词", "平台", "标题", "作者", "链接", "时间", "互动", "热度", "相关性", "质量说明"]
    )
    for run in response.platform_runs:
        for candidate in [*run.candidates, *run.reference_candidates]:
            quality = "；".join(
                dict.fromkeys(
                    [
                        *candidate.data_quality_warnings,
                        candidate.spoken_material_message,
                    ]
                )
            )
            writer.writerow(
                [
                    response.keyword,
                    candidate.platform_label or candidate.platform,
                    candidate.title,
                    candidate.author_name,
                    candidate.source_url or "",
                    candidate.published_at.isoformat() if candidate.published_at else "",
                    candidate.effective_interactions
                    if candidate.effective_interactions is not None
                    else "",
                    candidate.heat_score if candidate.heat_score is not None else "",
                    candidate.relevance_reason or candidate.relevance_basis or "",
                    quality,
                ]
            )
    content = output.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="crawler-{batch_id}.csv"'
        },
    )


@router.post("/batches/{batch_id}/copy-probes", response_model=CrawlerBatchResponse)
def probe_crawler_batch_copy(
    batch_id: str,
    repo=Depends(get_repository),
    service=Depends(get_candidate_copy_probe_service),
    douyin_public_service=Depends(get_douyin_public_search_service),
    douyin_public_provider=Depends(get_douyin_public_browser_provider),
    kuaishou_service=Depends(get_kuaishou_browser_search_service),
    kuaishou_provider=Depends(get_kuaishou_browser_provider),
    bilibili_service=Depends(get_bilibili_browser_search_service),
    bilibili_provider=Depends(get_bilibili_browser_provider),
):
    """Find a bounded pool with real copy detections; never treat title text as a pass."""
    batch = repo.get_search_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="搜索批次不存在。")

    matrix = _copy_search_matrix(batch.keyword, batch.copy_related_terms)
    executed = batch.copy_search_queries or [batch.keyword.strip()]
    executed_keys = {normalized_keyword_text(query) for query in executed}
    if not batch.copy_search_queries:
        batch = batch.model_copy(update={"copy_search_queries": executed})
        repo.save_search_batch(batch)

    attempts = 0
    blocked_platforms: set[Platform] = set()
    attempts, detected, blocked_platforms = _probe_copy_candidates(
        batch,
        repo,
        service,
        attempts=attempts,
        blocked_platforms=blocked_platforms,
    )

    should_append_copy_search_variants = (
        batch.monitoring_policy == "free_single_snapshot_v1"
        or batch.provider == "free_multi_platform"
    )
    if should_append_copy_search_variants:
        # The original keyword has already run.  At most three additional query
        # rounds are allowed for each browser in this batch, so improving recall
        # cannot turn into repeated platform browsing.
        used_variant_rounds = max(0, len(executed_keys) - 1)
        remaining_rounds = max(
            0, COPY_SEARCH_MAX_VARIANTS_PER_PLATFORM - used_variant_rounds
        )
        for query in matrix:
            if (
                normalized_keyword_text(query) in executed_keys
                or remaining_rounds <= 0
                or attempts >= COPY_PROBE_MAX_ATTEMPTS
                or detected >= COPY_PROBE_TARGET_DETECTIONS
            ):
                continue
            batch, _ = _append_copy_search_variant(
                batch,
                query,
                repo=repo,
                douyin_public_service=douyin_public_service,
                douyin_public_provider=douyin_public_provider,
                kuaishou_service=kuaishou_service,
                kuaishou_provider=kuaishou_provider,
                bilibili_service=bilibili_service,
                bilibili_provider=bilibili_provider,
                blocked_platforms=blocked_platforms,
            )
            if normalized_keyword_text(query) not in {
                normalized_keyword_text(value) for value in batch.copy_search_queries
            }:
                continue
            executed_keys.add(normalized_keyword_text(query))
            remaining_rounds -= 1
            attempts, detected, blocked_platforms = _probe_copy_candidates(
                batch,
                repo,
                service,
                attempts=attempts,
                blocked_platforms=blocked_platforms,
            )

    matrix_exhausted = bool(matrix) and all(
        normalized_keyword_text(query) in executed_keys for query in matrix
    )
    if batch.copy_matrix_exhausted != matrix_exhausted:
        batch = batch.model_copy(update={"copy_matrix_exhausted": matrix_exhausted})
        repo.save_search_batch(batch)
    return _batch_to_response(batch, repo)


@router.post(
    "/batches/{batch_id}/copy-probes/recheck-v1-no-text",
    response_model=CrawlerBatchResponse,
)
def recheck_crawler_batch_legacy_no_text_copy(
    batch_id: str,
    repo=Depends(get_repository),
    service=Depends(get_candidate_copy_probe_service),
):
    """Recheck saved short-window misses without invoking a browser search."""
    batch = repo.get_search_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="搜索批次不存在。")
    _recheck_legacy_no_text_probes(batch, repo, service)
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
            raise HTTPException(
                status_code=exc.status_code, detail=exc.user_message
            ) from exc
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
        raise HTTPException(
            status_code=exc.status_code, detail=exc.user_message
        ) from exc
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
        raise HTTPException(
            status_code=exc.status_code, detail=exc.user_message
        ) from exc
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
        raise HTTPException(
            status_code=exc.status_code, detail=exc.user_message
        ) from exc
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
    log_dir = backend_config.RUNTIME_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "doubao-browser-worker.log"
    node_executable = (
        os.getenv("VIDEOINSIGHT_NODE_EXECUTABLE", "node").strip() or "node"
    )
    backend_origin = os.getenv(
        "VIDEOINSIGHT_BACKEND_ORIGIN", "http://127.0.0.1:2001"
    ).rstrip("/")
    command = [
        node_executable,
        str(script),
        "--api",
        f"{backend_origin}/api/v1/crawler",
    ]
    worker_environment = os.environ.copy()
    if os.getenv("VIDEOINSIGHT_NODE_AS_ELECTRON", "").casefold() == "true":
        worker_environment["ELECTRON_RUN_AS_NODE"] = "1"
    try:
        with log_path.open("ab") as log_file:
            subprocess.Popen(
                command,
                cwd=str(backend_config.RUNTIME_ROOT),
                env=worker_environment,
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
        raise HTTPException(
            status_code=500, detail="未找到 Node.js，无法启动本地执行器。"
        ) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"执行器启动失败：{exc}") from exc
    return CrawlerDoubaoWorkerStartResponse(
        started=True,
        command=command,
        log_path=str(log_path),
        message="已启动专用浏览器执行器；如遇登录、验证码或豆包限制，任务会停在失败状态并显示原因。",
    )


def _check_doubao_mobile_prerequisites() -> tuple[
    CrawlerDoubaoMobilePrerequisites, list[str], str, str | None
]:
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
            raise HTTPException(
                status_code=exc.status_code, detail=exc.user_message
            ) from exc
        if missing and job.status == TaskStatus.QUEUED:
            job = service.fail_job(
                job.task_id,
                error_message=("手机豆包执行器缺少前置条件：" + "；".join(missing)),
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
        raise HTTPException(
            status_code=exc.status_code, detail=exc.user_message
        ) from exc
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
        raise HTTPException(
            status_code=exc.status_code, detail=exc.user_message
        ) from exc
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
        raise HTTPException(
            status_code=exc.status_code, detail=exc.user_message
        ) from exc
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
    log_dir = backend_config.RUNTIME_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "doubao-mobile-worker.log"
    node_executable = (
        os.getenv("VIDEOINSIGHT_NODE_EXECUTABLE", "node").strip() or "node"
    )
    backend_origin = os.getenv(
        "VIDEOINSIGHT_BACKEND_ORIGIN", "http://127.0.0.1:2001"
    ).rstrip("/")
    command = [
        node_executable,
        str(script),
        "--api",
        f"{backend_origin}/api/v1/crawler",
    ]
    worker_environment = os.environ.copy()
    if os.getenv("VIDEOINSIGHT_NODE_AS_ELECTRON", "").casefold() == "true":
        worker_environment["ELECTRON_RUN_AS_NODE"] = "1"
    try:
        with log_path.open("ab") as log_file:
            subprocess.Popen(
                command,
                cwd=str(backend_config.RUNTIME_ROOT),
                env=worker_environment,
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
        raise HTTPException(
            status_code=500, detail="未找到 Node.js，无法启动安卓执行器。"
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"安卓执行器启动失败：{exc}"
        ) from exc
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


def _latest_candidate_transcription(
    repo, candidate_id: str, candidate_tasks: list[TranscriptionTask] | None = None
) -> TranscriptionTask | None:
    """Return the latest authorized media task associated with this candidate."""
    try:
        tasks = [
            task
            for task in (candidate_tasks if candidate_tasks is not None else repo.list_tasks())
            if isinstance(task, TranscriptionTask)
            and task.candidate_id == candidate_id
            and task.rights_confirmed
        ]
        return max(tasks, key=lambda task: task.created_at, default=None)
    except Exception:
        return None


def _candidate_copy_fields(
    repo,
    candidate,
    media_task: TranscriptionTask | None,
    candidate_tasks: list[TranscriptionTask] | None = None,
) -> dict:
    """推导候选的三档文案来源字段。

    优先级：手机豆包任务 > 授权 ASR 媒体任务 > 无（前端再决定给原创脚本）。
    """
    doubao_mobile = None
    try:
        for task in (candidate_tasks if candidate_tasks is not None else repo.list_tasks()):
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
            and any(
                segment.needs_review and not segment.reviewed
                for segment in media_task.segments
            ),
        }
    return {
        "copy_source": None,
        "is_original_transcript": None,
        "needs_manual_review": None,
    }


def _visible_copy_from_evidence(evidence: str | None) -> str:
    marker = "可见文案="
    if marker not in (evidence or ""):
        return ""
    return (evidence or "").split(marker, 1)[1].strip()


def _spoken_material_fields(
    *, evidence: str | None, copy_fields: dict
) -> dict[str, str]:
    """Never label a title-only video as extractable copy."""
    if copy_fields.get("is_original_transcript") is True:
        return {
            "status": "transcript_ready",
            "message": "已有授权转写稿，可先复核再改写。",
        }
    visible_copy = _visible_copy_from_evidence(evidence)
    if len(visible_copy) >= 30:
        return {
            "status": "text_reference",
            "message": "已保存人工可见文案，可作为改写参考；请先核对原意。",
        }
    return {
        "status": "topic_only",
        "message": "仅有标题和互动数据，只能用于选题参考，不能提取原视频文案。",
    }


_SPOKEN_PROBLEM_TERMS = (
    "故障",
    "问题",
    "原因",
    "导致",
    "不准",
    "掉标",
    "带胶",
    "困难",
    "异常",
    "卡标",
    "起皱",
    "漏标",
    "偏移",
    "怎么",
    "为什么",
    "怎么办",
)
_SPOKEN_SOLUTION_TERMS = (
    "解决",
    "方法",
    "技巧",
    "步骤",
    "调试",
    "设置",
    "选型",
    "选择",
    "避坑",
    "注意",
    "区别",
    "对比",
    "如何",
    "一机多用",
    "应用",
    "方案",
    "案例",
    "教程",
)
_SPOKEN_CONTEXT_TERMS = (
    "瓶",
    "饮料",
    "食品",
    "医药",
    "化工",
    "水厂",
    "产线",
    "行业",
    "场景",
    "包装",
    "不干胶",
    "异形",
)
_SPOKEN_PROMOTION_TERMS = (
    "源头厂家",
    "厂家直销",
    "欢迎咨询",
    "专业生产",
    "工厂实拍",
)


def _spoken_seed_quality(
    *,
    title: str,
    keyword: str,
    evidence: str | None = None,
) -> dict[str, str | int]:
    """Score whether public text can support an original spoken script."""
    visible_copy = _visible_copy_from_evidence(evidence)
    if len(visible_copy) >= 30:
        return {
            "score": 4,
            "status": "writeable",
            "message": "人工可见文案包含足够信息，可先核对原意再改写。",
        }

    score = 0
    reasons: list[str] = []
    normalized_title = normalized_keyword_text(title)
    normalized_keyword = normalized_keyword_text(keyword)
    title_without_tags = re.sub(r"#[^#\s]+", "", title).strip()

    if any(term in title for term in _SPOKEN_PROBLEM_TERMS):
        score += 2
        reasons.append("包含具体问题")
    if any(term in title for term in _SPOKEN_SOLUTION_TERMS):
        score += 2
        reasons.append("包含方法或应用")
    if any(term in title for term in _SPOKEN_CONTEXT_TERMS):
        score += 1
        reasons.append("包含使用场景")
    if re.search(
        r"\d+(?:\.\d+)?(?:瓶|件|个|台|米|毫米|mm|秒|分钟|万|元|%|％|/)",
        title,
        re.IGNORECASE,
    ):
        score += 1
        reasons.append("包含具体参数")
    if len(normalized_keyword_text(title_without_tags)) >= max(
        18, len(normalized_keyword) + 10
    ):
        score += 1
        reasons.append("描述较具体")
    if any(term in title for term in _SPOKEN_PROMOTION_TERMS):
        score -= 2
        reasons.append("偏广告展示")
    if (
        normalized_title == normalized_keyword
        or len(normalized_keyword_text(title_without_tags))
        <= len(normalized_keyword) + 2
    ):
        score -= 2
        reasons.append("标题信息过少")

    if score >= 3:
        status = "writeable"
        message = "标题信息较完整：" + "、".join(reasons) + "。"
    elif score >= 1:
        status = "reference_only"
        message = "信息量一般，只建议作为选题参考：" + "、".join(reasons) + "。"
    else:
        status = "low_information"
        message = (
            "标题信息不足以支撑原创文案"
            + ("：" + "、".join(reasons) if reasons else "")
            + "。"
        )
    return {"score": score, "status": status, "message": message}


def _candidate_audio_fields(
    task: TranscriptionTask | None,
    probe: CandidateCopyProbe | None = None,
) -> dict[str, str]:
    """Prefer an authorized transcript, otherwise use the ephemeral copy probe."""
    if task is None or not task.media_type.startswith(("video/", "audio/")):
        if probe is not None:
            if probe.status == "detected":
                return {"status": "speech_detected", "message": probe.message}
            if probe.status == "no_text":
                return {"status": "no_clear_speech", "message": probe.message}
            return {"status": "check_failed", "message": probe.message}
        return {
            "status": "unknown",
            "message": "尚未检测文案。",
        }
    if task.status in {TaskStatus.QUEUED, TaskStatus.SUBMITTED, TaskStatus.RUNNING}:
        return {"status": "checking", "message": "已提交授权视频，正在检测文案。"}
    if task.status == TaskStatus.FAILED:
        if "没有可识别的音轨" in (task.error_message or ""):
            return {"status": "no_audio", "message": "本地视频没有可识别音轨。"}
        return {
            "status": "check_failed",
            "message": "文案检测未完成，请保留文件后重试。",
        }
    transcript_text = "".join(segment.text.strip() for segment in task.segments)
    if transcript_text:
        return {"status": "speech_detected", "message": "检测到文案，已生成转写稿。"}
    return {
        "status": "no_clear_speech",
        "message": "视频有音轨，但没有识别出清晰文案。",
    }


def _spoken_material_message(platform: Platform, *, status: str, message: str) -> str:
    """Explain the login-dependent XHS path without hiding the action."""
    if platform == Platform.XIAOHONGSHU and status == "topic_only":
        return (
            "小红书候选会先尝试使用已登录浏览器打开并转写；若该笔记当前无法浏览，"
            "可重新复制分享链接，或上传已获授权的视频。"
        )
    return message


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
    selection_tier: Literal["priority", "reserve"] | None = None,
    candidate_tasks: list[TranscriptionTask] | None = None,
    include_runtime_details: bool = True,
) -> CrawlerCandidateResult:
    resolved_evidence = evidence or candidate.evidence
    hotspot_lists, evidence_duration_seconds, hotspot_window_hours = (
        _hotspot_evidence_details(resolved_evidence)
    )
    duration_seconds = (
        getattr(candidate, "duration_seconds", None) or evidence_duration_seconds
    )
    is_incremental_hotspot = "来源=video_board" in (resolved_evidence or "")
    likes_per_day, quality_source = _hotspot_quality_details(resolved_evidence)
    if include_runtime_details:
        media_resolution = repo.find_latest_media_resolution_for_candidate(
            candidate.video_id
        )
        resolved_task = (
            repo.get_task(media_resolution.task_id)
            if media_resolution and media_resolution.task_id
            else None
        )
        resolved_task = (
            resolved_task if isinstance(resolved_task, TranscriptionTask) else None
        )
        local_link_task = _latest_candidate_transcription(
            repo,
            candidate.video_id,
            candidate_tasks,
        )
        media_task = max(
            (task for task in (resolved_task, local_link_task) if task is not None),
            key=lambda task: task.created_at,
            default=None,
        )
        media_task_id = media_task.task_id if media_task else None
        media_status = (
            media_resolution.status.value
            if media_task is not None and media_task is resolved_task and media_resolution
            else media_task.status.value
            if media_task
            else None
        )
        copy_fields = _candidate_copy_fields(
            repo,
            candidate,
            media_task,
            candidate_tasks,
        )
        audio = _candidate_audio_fields(
            media_task,
            repo.get_candidate_copy_probe(candidate.video_id),
        )
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
                    growth_per_hour = round((interactions - previous_interactions) / elapsed_hours, 4)
            trend_points.append(CrawlerTrendPoint(
                sampled_at=snapshot.sampled_at,
                effective_interactions=interactions,
                growth_per_hour=growth_per_hour,
            ))
            previous_interactions = interactions
            previous_at = snapshot.sampled_at
    else:
        media_task_id = None
        media_status = None
        copy_fields = {"copy_source": None, "is_original_transcript": None, "needs_manual_review": None}
        audio = {"status": "unknown", "message": "选中素材后再检查授权转写状态。"}
        trend_points = []
    spoken_material = _spoken_material_fields(
        evidence=resolved_evidence,
        copy_fields=copy_fields,
    )
    spoken_material["message"] = _spoken_material_message(
        candidate.platform,
        status=str(spoken_material["status"]),
        message=str(spoken_material["message"]),
    )
    spoken_seed = _spoken_seed_quality(
        title=candidate.title,
        keyword=keyword or candidate.title,
        evidence=resolved_evidence,
    )
    return CrawlerCandidateResult(
        video_id=candidate.video_id,
        title=candidate.title,
        author_name=candidate.author_name,
        platform=candidate.platform.value,
        platform_label=_platform_label(candidate.platform.value),
        source_url=str(candidate.source_url) if candidate.source_url else None,
        published_at=candidate.published_at,
        published_at_reliable=bool(getattr(candidate, "published_at_reliable", False)),
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
        missed_checkpoint_count=(trend.missed_checkpoint_count if trend else None),
        sampling_span_hours=trend.sampling_span_hours if trend else None,
        anomaly_status=trend.anomaly_status.value if trend else None,
        platform_rank=platform_rank,
        provider_hot_rank=provider_hot_rank or platform_rank,
        system_rank=system_rank,
        plays=candidate.metrics.plays,
        new_plays=candidate.metrics.plays if is_incremental_hotspot else None,
        likes=candidate.metrics.likes,
        heat_score=_interaction_heat_or_none(candidate),
        new_likes=candidate.metrics.likes if is_incremental_hotspot else None,
        likes_per_day=likes_per_day,
        quality_source=quality_source,
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
        needs_manual_review=(
            True if selection_tier == "reserve" else copy_fields["needs_manual_review"]
        ),
        spoken_material_status=spoken_material["status"],
        spoken_material_message=spoken_material["message"],
        spoken_seed_score=spoken_seed["score"],
        spoken_seed_status=spoken_seed["status"],
        spoken_seed_message=spoken_seed["message"],
        audio_status=audio["status"],
        audio_message=audio["message"],
        share_count=candidate.share_count,
        collect_count=candidate.collect_count,
        relevance_basis=relevance_basis or ("title_or_hashtag" if keyword else None),
        relevance_reason=relevance_reason
        or (keyword_match_reason(keyword) if keyword else None),
        selection_tier=selection_tier,
        trend_points=trend_points,
    )


def _provider_item_to_crawler_response(item, *, keyword: str) -> CrawlerCandidateResult:
    hotspot_lists, evidence_duration_seconds, hotspot_window_hours = (
        _hotspot_evidence_details(item.evidence)
    )
    duration_seconds = (
        getattr(item, "duration_seconds", None) or evidence_duration_seconds
    )
    likes_per_day, quality_source = _hotspot_quality_details(item.evidence)
    spoken_seed = _spoken_seed_quality(
        title=item.title,
        keyword=keyword,
        evidence=item.evidence,
    )
    direct_match = item_matches_keyword(
        title=item.title,
        keyword=keyword,
        evidence=item.evidence,
    )
    spoken_material_message = _spoken_material_message(
        item.platform,
        status="topic_only",
        message="这条结果只验证了标题和互动数据；可据此生成原创口播，但不能提取原视频文案。",
    )
    return CrawlerCandidateResult(
        video_id=item.platform_item_id,
        title=item.title,
        author_name=item.author_name,
        platform=item.platform.value,
        platform_label=_platform_label(item.platform.value),
        source_url=str(item.source_url) if item.source_url else None,
        published_at=item.published_at,
        published_at_reliable=bool(getattr(item, "published_at_reliable", False)),
        confidence=item.metrics.confidence,
        provider_hot_rank=item.provider_rank,
        plays=item.metrics.plays,
        # 这个转换器只用于 low_incremental_items；这些项目本身就是视频榜
        # 的新增量参考，不能因旧证据字符串缺少“来源=video_board”而丢掉数值。
        new_plays=item.metrics.plays,
        likes=item.metrics.likes,
        heat_score=_interaction_heat_or_none(item),
        new_likes=item.metrics.likes,
        likes_per_day=likes_per_day,
        quality_source=quality_source,
        duration_seconds=duration_seconds,
        hotspot_window_hours=hotspot_window_hours,
        hotspot_list_labels=hotspot_lists,
        comments=item.metrics.comments,
        shares=item.metrics.shares,
        favorites=item.metrics.favorites,
        data_quality_warnings=item.data_quality_warnings,
        evidence=item.evidence,
        spoken_material_status="topic_only",
        spoken_material_message=spoken_material_message,
        spoken_seed_score=spoken_seed["score"],
        spoken_seed_status=spoken_seed["status"],
        spoken_seed_message=spoken_seed["message"],
        relevance_basis="title_or_hashtag" if direct_match else "platform_search",
        relevance_reason=(
            keyword_match_reason(keyword)
            if direct_match
            else f"平台搜索结果，标题/话题未直接命中“{keyword.strip()}”。"
        ),
    )


def _hotspot_evidence_details(
    evidence: str | None,
) -> tuple[list[str], int | None, int | None]:
    if not evidence:
        return [], None, None
    if evidence.startswith("douyin_public_search:"):
        duration_match = re.search(r"时长秒=(\d+)", evidence)
        return (
            ["抖音登录搜索"],
            int(duration_match.group(1)) if duration_match else None,
            None,
        )
    if not evidence.startswith("hotspot:"):
        duration_match = re.search(r"时长秒=(\d+)", evidence)
        return [], int(duration_match.group(1)) if duration_match else None, None
    header, *_ = evidence.split(";", 1)
    header_parts = header.split(":")
    labels = [
        label
        for label in (header_parts[1].split("|") if len(header_parts) > 1 else [])
        if label
    ]
    window_match = re.search(r":(\d+)h:", header)
    duration_match = re.search(r"时长秒=(\d+)", evidence)
    return (
        labels,
        int(duration_match.group(1)) if duration_match else None,
        int(window_match.group(1)) if window_match else None,
    )


def _hotspot_quality_details(evidence: str | None) -> tuple[float | None, str | None]:
    if not evidence or not evidence.startswith("hotspot:"):
        return None, None
    likes_per_day_match = re.search(r"日均点赞=([0-9]+(?:\.[0-9]+)?)", evidence)
    quality_source_match = re.search(r"质量口径=([^;]+)", evidence)
    return (
        float(likes_per_day_match.group(1)) if likes_per_day_match else None,
        quality_source_match.group(1) if quality_source_match else None,
    )


def _batch_to_response(
    batch: SearchBatch,
    repo,
    *,
    include_candidates: bool = True,
    include_runtime_details: bool = True,
) -> CrawlerBatchResponse:
    runs = repo.list_platform_search_runs(batch.batch_id)
    # 详情页可能一次返回上百条候选。逐条调用 repo.list_tasks() 会重复扫描
    # 整张任务表，导致“继续挑选”长期停在加载状态；本批只读取一次后按候选复用。
    candidate_tasks_by_id: dict[str, list[TranscriptionTask]] | None = None
    if include_candidates and include_runtime_details:
        candidate_tasks_by_id = {}
        # 收集本 batch 所有 candidate_id, 只查相关 task (避免全表扫描 1227+ 行)
        batch_candidate_ids: set[str] = set()
        # 一次查所有 run 的 candidate_matches, 避免 N+1
        match_run_ids = [run.cached_from_run_id or run.run_id for run in runs]
        all_matches_by_run_id = repo.list_candidate_matches_by_run_ids(match_run_ids)
        for run in runs:
            for match in all_matches_by_run_id.get(run.cached_from_run_id or run.run_id, []):
                batch_candidate_ids.add(match.video_id)
        for task in repo.list_tasks(candidate_ids=list(batch_candidate_ids)):
            if isinstance(task, TranscriptionTask) and task.candidate_id:
                candidate_tasks_by_id.setdefault(task.candidate_id, []).append(task)
    # 把 matches 按 run_id group 传给 _run_to_response, 避免 _run_to_response 内部 N+1
    matches_by_run_id: dict[str, list] = {}
    if include_candidates and include_runtime_details:
        for run in runs:
            rid = run.cached_from_run_id if run.cached_from_run_id else run.run_id
            matches_by_run_id[rid] = all_matches_by_run_id.get(rid, [])
    run_items = [
        _run_to_response(
            batch,
            run,
            repo,
            include_candidates=include_candidates,
            candidate_tasks_by_id=candidate_tasks_by_id,
            include_runtime_details=include_runtime_details,
            preloaded_matches=matches_by_run_id.get(
                run.cached_from_run_id if run.cached_from_run_id else run.run_id
            ),
        )
        for run in runs
    ]
    tracking_checkpoints = repo.list_sampling_checkpoints(
        tracking_batch_id=batch.batch_id
    )
    pending_tracking = [
        item for item in tracking_checkpoints if item.status == SamplingStatus.PENDING
    ]
    if not batch.tracking_authorized:
        tracking_status = "not_started"
    elif pending_tracking:
        tracking_status = "scheduled"
    elif tracking_checkpoints and all(
        item.status == SamplingStatus.OBSERVED for item in tracking_checkpoints
    ):
        tracking_status = "complete"
    elif tracking_checkpoints and all(
        item.status == SamplingStatus.CANCELLED for item in tracking_checkpoints
    ):
        tracking_status = "cancelled"
    else:
        tracking_status = "partial"
    response = CrawlerBatchResponse(
        batch_id=batch.batch_id,
        keyword=batch.keyword,
        platforms=[platform.value for platform in batch.platforms],
        published_window_days=batch.published_window_days,
        hotspot_window_hours=batch.hotspot_window_hours,
        count_per_platform=batch.requested_count_per_platform,
        kuaishou_sort=batch.kuaishou_sort,
        kuaishou_duration_bucket=batch.kuaishou_duration_bucket,
        provider=batch.provider,
        mode=batch.mode.value,
        status=batch.status.value,
        force_refresh=batch.force_refresh,
        created_at=batch.created_at,
        finished_at=batch.finished_at,
        error=batch.error,
        platform_runs=run_items,
        total_api_calls=sum(item.api_call_count for item in run_items),
        # 可查看数量包含严格命中和明确标记的参考候选；参考候选仍由
        # platform_run.reference_count 单独说明，不把严格命中口径混在一起。
        total_candidates=sum(
            item.relevant_count + item.reference_count for item in run_items
        ),
        total_estimated_cost_cny=round(
            sum(item.billable_units or 0.0 for item in run_items),
            4,
        ),
        monitoring_policy=batch.monitoring_policy,
        sampling_offsets_hours=batch.sampling_offsets_hours,
        free_candidate_count=sum(
            item.relevant_count + item.reference_count for item in run_items
        ),
        trend_tracking_enabled=batch.monitoring_policy == "adaptive_three_sample_v1",
        tracking_status=tracking_status,
        next_tracking_at=min((item.due_at for item in pending_tracking), default=None),
    )
    if not include_candidates:
        return response.model_copy(
            update={
                "copy_queries_executed": (
                    len(batch.copy_search_queries) or int(bool(response.platform_runs))
                ),
                "copy_matrix_exhausted": batch.copy_matrix_exhausted,
            }
        )
    return (
        _with_copy_pool_metadata(response, batch=batch, repo=repo)
        if include_runtime_details
        else response
    )


def _copy_pool_sort_key(candidate: CrawlerCandidateResult) -> tuple[Any, ...]:
    return (
        -candidate.spoken_seed_score,
        -(candidate.plays or 0),
        -(candidate.likes or 0),
        candidate.platform_rank if candidate.platform_rank is not None else 10_000,
        candidate.video_id,
    )


def _copy_exclusion_reason(
    candidate: CrawlerCandidateResult,
    *,
    probe_attempt_count: int,
    matrix_exhausted: bool,
) -> str:
    if candidate.audio_status == "no_audio":
        return "没有可识别的音轨。"
    if candidate.audio_status == "no_clear_speech":
        message = candidate.audio_message or ""
        if "未识别" in message or "没有" in message:
            return message
        return "抽样范围内未识别到清晰文案。"
    if candidate.audio_status == "check_failed":
        return candidate.audio_message or "文案检测失败，需手动确认。"
    if candidate.audio_status == "checking":
        return "授权转写仍在进行，暂不能作为可用文案素材。"
    if candidate.audio_status == "speech_detected":
        return "已收满8条优先素材和4条备用素材。"
    if probe_attempt_count >= COPY_PROBE_MAX_ATTEMPTS:
        return f"本次文案检测最多检查{COPY_PROBE_MAX_ATTEMPTS}条，尚未检测到此条。"
    if matrix_exhausted:
        return "本次检索词已用完，尚未检测到此条。"
    return "本轮优先检测已结束，尚未检测到此条。"


def _with_copy_pool_metadata(
    response: CrawlerBatchResponse,
    *,
    batch: SearchBatch,
    repo,
) -> CrawlerBatchResponse:
    """Decorate an existing response; persistence remains the probe result itself."""
    best_by_id: dict[str, CrawlerCandidateResult] = {}
    for run in response.platform_runs:
        for candidate in run.candidates:
            current = best_by_id.get(candidate.video_id)
            if current is None or _copy_pool_sort_key(candidate) < _copy_pool_sort_key(
                current
            ):
                best_by_id[candidate.video_id] = candidate

    # 批量预加载 copy probes, 一次 SQL 替代每个 candidate 都查一次 (原 N 次降为 1 次).
    probes_by_id = repo.get_candidate_copy_probes_by_ids(list(best_by_id.keys()))
    probe_attempt_count = sum(
        1
        for candidate_id in best_by_id
        if (
            (probe := probes_by_id.get(candidate_id)) is not None
            and _is_supported_copy_probe(probe)
        )
    )
    recheckable_count = sum(
        1
        for candidate_id in best_by_id
        if _should_widen_legacy_no_text_probe(
            probes_by_id.get(candidate_id)
        )
    )
    detected = sorted(
        (
            candidate
            for candidate in best_by_id.values()
            if candidate.audio_status == "speech_detected"
        ),
        key=_copy_pool_sort_key,
    )
    primary_ids = {
        candidate.video_id for candidate in detected[:COPY_POOL_PRIMARY_TARGET]
    }
    reserve_ids = {
        candidate.video_id
        for candidate in detected[COPY_POOL_PRIMARY_TARGET:COPY_PROBE_TARGET_DETECTIONS]
    }

    runs: list[CrawlerPlatformRunResponse] = []
    for run in response.platform_runs:
        candidates: list[CrawlerCandidateResult] = []
        for candidate in run.candidates:
            if candidate.video_id in primary_ids:
                candidates.append(
                    candidate.model_copy(update={"copy_pool_status": "primary"})
                )
                continue
            if candidate.video_id in reserve_ids:
                candidates.append(
                    candidate.model_copy(update={"copy_pool_status": "reserve"})
                )
                continue
            candidates.append(
                candidate.model_copy(
                    update={
                        "copy_pool_status": "excluded",
                        "copy_rejection_reason": _copy_exclusion_reason(
                            candidate,
                            probe_attempt_count=probe_attempt_count,
                            matrix_exhausted=batch.copy_matrix_exhausted,
                        ),
                    }
                )
            )
        runs.append(run.model_copy(update={"candidates": candidates}))

    return response.model_copy(
        update={
            "platform_runs": runs,
            "copy_detected_count": len(detected),
            "copy_primary_count": len(primary_ids),
            "copy_reserve_count": len(reserve_ids),
            "copy_probe_attempt_count": probe_attempt_count,
            "copy_probe_recheckable_count": recheckable_count,
            "copy_queries_executed": (
                len(batch.copy_search_queries) or int(bool(response.platform_runs))
            ),
            "copy_matrix_exhausted": batch.copy_matrix_exhausted,
        }
    )


def _interaction_heat(candidate) -> float:
    metrics = candidate.metrics
    return (
        float(metrics.likes or 0)
        + float(metrics.comments or 0) * 3
        + float(metrics.shares or 0) * 4
        + float(metrics.favorites or 0) * 4
    )


def _interaction_heat_or_none(candidate) -> float | None:
    """Expose a heat score only when the platform returned an interaction field."""
    metrics = candidate.metrics
    values = (metrics.likes, metrics.comments, metrics.shares, metrics.favorites)
    return (
        _interaction_heat(candidate)
        if any(value is not None for value in values)
        else None
    )


def _is_official_hot_candidate(candidate) -> bool:
    return (
        bool(getattr(candidate, "official_hot", False))
        or "official" in str(candidate.evidence or "").casefold()
    )


def _passes_main_board_heat_floor(candidate) -> bool:
    return (
        candidate.platform == Platform.XIAOHONGSHU
        or _interaction_heat(candidate) >= INTERACTION_HEAT_FLOOR
        or _is_official_hot_candidate(candidate)
        or (
            candidate.platform == Platform.BILIBILI
            and (candidate.metrics.plays or 0) >= BILIBILI_PLAY_HEAT_FLOOR
        )
    )


def _run_to_response(
    batch: SearchBatch,
    run,
    repo,
    *,
    include_candidates: bool,
    candidate_tasks_by_id: dict[str, list[TranscriptionTask]] | None = None,
    include_runtime_details: bool = True,
    preloaded_matches: list | None = None,
) -> CrawlerPlatformRunResponse:
    candidates: list[CrawlerCandidateResult] = []
    reference_candidates: list[CrawlerCandidateResult] = []
    low_incremental_candidates: list[CrawlerCandidateResult] = []
    visible_matches: list[tuple[Any, Any]] = []
    reference_item_ids = {
        item.platform_item_id for item in getattr(run, "reference_items", [])
    }

    def is_bilibili_related_candidate(candidate) -> bool:
        """Detect the B 站 one-dimension related tier persisted by normalization."""
        return (
            candidate.platform == Platform.BILIBILI
            and str(getattr(candidate, "category", "") or "").startswith("关键词相关/")
        )
    if run.status in {
        PlatformRunStatus.SUCCEEDED,
        PlatformRunStatus.PARTIAL,
        PlatformRunStatus.CACHED,
    }:
        match_run_id = run.cached_from_run_id if run.cached_from_run_id else run.run_id
        matches = preloaded_matches if preloaded_matches is not None else repo.list_candidate_matches(match_run_id)
        # 批量预加载 candidates, 一次 SQL 替代每个 match 都触发
        # candidates+metric_snapshots+heat_results 三次单条查询 (原 N*3 降为 3 次).
        candidate_by_video_id = {
            c.video_id: c
            for c in repo.get_candidates_by_ids([m.video_id for m in matches])
        }
        for match in sorted(matches, key=lambda item: item.platform_rank):
            candidate = candidate_by_video_id.get(match.video_id)
            if candidate is None:
                continue
            visible_matches.append((match, candidate))

    strict_visible_matches = [
        (match, candidate)
        for match, candidate in visible_matches
        if candidate.platform_item_id not in reference_item_ids
    ]
    reference_visible_matches = [
        (match, candidate)
        for match, candidate in visible_matches
        if candidate.platform_item_id in reference_item_ids
    ]
    strict_relevant_count = sum(
        _direct_match_keyword(batch, match, candidate) is not None
        for match, candidate in strict_visible_matches
    )
    below_heat_floor_count = sum(
        not _passes_main_board_heat_floor(candidate)
        for match, candidate in strict_visible_matches
    )
    low_spoken_value_count = sum(
        _spoken_seed_quality(
            title=candidate.title,
            keyword=str(getattr(match, "keyword", "") or batch.keyword),
            evidence=match.evidence or candidate.evidence,
        )["status"]
        != "writeable"
        for match, candidate in strict_visible_matches
    )
    reference_count = len(reference_visible_matches)
    # returned/retained 保持主结果口径；待确认素材单独由 reference_count 返回，
    # 避免把人工确认项混进“高相关素材”数量。
    visible_count = len(strict_visible_matches)
    relevant_count = len(strict_visible_matches)
    irrelevant_count = run.irrelevant_count
    result_state = run.result_state
    if reference_visible_matches and not strict_visible_matches:
        result_state = "reference_only"
    elif relevant_count and result_state.startswith("all_"):
        result_state = "has_results"

    if include_candidates and visible_matches:
        is_hotspot_run = run.provider == "douyin_local_browser"

        def default_table_sort_key(item: tuple[Any, Any]) -> tuple[Any, ...]:
            match, candidate = item
            heat_score = _interaction_heat_or_none(candidate)
            return (
                heat_score is None,
                -(heat_score or 0.0),
                -(candidate.metrics.plays or 0),
                match.platform_rank,
                candidate.video_id,
            )

        visible_matches.sort(key=default_table_sort_key)
        trends = (
            []
            if is_hotspot_run or not include_runtime_details
            else repo.list_keyword_trend_results(
                batch.keyword,
                limit=batch.requested_count_per_platform,
                platform=run.platform,
            )
        )
        trend_by_candidate = {item.candidate_id: item for item in trends}
        visible_ids = {candidate.video_id for _, candidate in visible_matches}
        system_rank_by_candidate = {
            item.candidate_id: index
            for index, item in enumerate(
                [item for item in trends if item.candidate_id in visible_ids], start=1
            )
        }
        for match, candidate in visible_matches:
            is_reference = candidate.platform_item_id in reference_item_ids
            is_related_candidate = is_reference or is_bilibili_related_candidate(candidate)
            direct_match_keyword = _direct_match_keyword(batch, match, candidate)
            matched_keyword = str(
                getattr(match, "keyword", "") or batch.keyword
            ).strip()
            trend = trend_by_candidate.get(candidate.video_id)
            response_candidate = _candidate_to_response(
                candidate,
                repo=repo,
                trend=trend,
                platform_rank=match.platform_rank,
                provider_hot_rank=match.platform_rank,
                system_rank=system_rank_by_candidate.get(candidate.video_id),
                evidence=match.evidence or candidate.evidence,
                keyword=matched_keyword,
                relevance_basis=(
                    "title_or_hashtag"
                    if direct_match_keyword
                    else "related_concept"
                    if is_related_candidate
                    else "platform_search"
                ),
                relevance_reason=(
                    keyword_match_reason(direct_match_keyword)
                    if direct_match_keyword
                    else (
                        "B站按行业/对象相关性放宽匹配，请人工确认是否适合当前关键词。"
                        if is_related_candidate
                        else (
                            "平台搜索参考，请人工确认与当前关键词的相关性。"
                            if is_reference
                            else f"平台搜索结果，标题/话题未直接命中“{batch.keyword.strip()}”。"
                        )
                    )
                ),
                selection_tier="reserve" if is_related_candidate else None,
                candidate_tasks=(candidate_tasks_by_id or {}).get(candidate.video_id),
                include_runtime_details=include_runtime_details,
            )
            (reference_candidates if is_reference else candidates).append(
                response_candidate
            )
    if (
        include_candidates
        and run.provider == "douyin_local_browser"
        and not candidates
        and run.low_incremental_items
    ):
        low_incremental_candidates = [
            _provider_item_to_crawler_response(item, keyword=batch.keyword)
            for item in run.low_incremental_items
            if item_matches_keyword(
                title=item.title,
                keyword=batch.keyword,
                evidence=item.evidence,
            )
        ]
    return CrawlerPlatformRunResponse(
        run_id=run.run_id,
        platform=run.platform.value,
        platform_label=_platform_label(run.platform.value),
        provider=run.provider,
        mode=run.mode.value,
        status=run.status.value,
        requested_count=run.requested_count,
        returned_count=visible_count,
        raw_item_count=run.raw_item_count,
        parsed_item_count=run.parsed_item_count,
        raw_discovered=(run.raw_discovered_count or run.raw_item_count),
        parsed=(run.parsed_item_count or run.raw_item_count),
        deduped=(run.deduped_item_count or run.parsed_item_count),
        direct_match=(run.direct_match_count or strict_relevant_count),
        relevance_filtered=run.irrelevant_count,
        duration_filtered=run.duration_filtered_count,
        invalid_fields=run.invalid_count,
        retained=visible_count,
        out_of_window_count=run.out_of_window_count,
        invalid_count=run.invalid_count,
        duplicate_count=run.duplicate_count,
        relevant_count=relevant_count,
        strict_relevant_count=strict_relevant_count,
        below_heat_floor_count=below_heat_floor_count,
        low_spoken_value_count=low_spoken_value_count,
        irrelevant_count=irrelevant_count,
        duration_filtered_count=run.duration_filtered_count,
        incremental_play_filtered_count=run.incremental_play_filtered_count,
        relevance_rule_version=(run.relevance_rule_version or RELEVANCE_RULE_VERSION),
        result_state=result_state,
        crawl_stop_reason=run.crawl_stop_reason,
        crawl_stop_message=run.crawl_stop_message,
        payload_diagnostic=run.payload_diagnostic,
        stage_timings_ms=run.stage_timings_ms,
        rule_version=(run.adapter_rule_version or run.relevance_rule_version),
        browser_reused=run.browser_reused,
        session_recovered=run.session_recovered,
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
        reference_count=reference_count,
        reference_candidates=reference_candidates,
        low_incremental_candidates=low_incremental_candidates,
    )
