from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class Platform(StrEnum):
    DOUYIN = "douyin"
    KUAISHOU = "kuaishou"
    XIAOHONGSHU = "xiaohongshu"
    WECHAT_CHANNELS = "wechat_channels"


class DataSource(StrEnum):
    OFFICIAL = "official_authorized_api"
    LICENSED_PROVIDER = "licensed_commercial_provider"
    OFFICIAL_HOT_BILLBOARD = "douyin_hot_billboard"
    PUBLIC_RESEARCH = "public_metadata_research"
    MANUAL = "manual"
    CSV = "csv"
    MOCK = "mock"


class HeatLevel(StrEnum):
    S = "S"
    A = "A"
    B = "B"
    STATIC_HIGH = "静态高热"
    ANOMALOUS = "异常"
    NORMAL = "普通"
    INSUFFICIENT = "数据不足"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class EligibilityStatus(StrEnum):
    AUTO_MATCHED = "auto_matched"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class KeywordTrendLevel(StrEnum):
    S = "S"
    A = "A"
    B = "B"
    OBSERVING = "观察中"
    NORMAL = "普通"


class MomentumState(StrEnum):
    VIRAL = "viral"
    POTENTIAL = "potential"
    STATIC_HIGH = "static_high"
    NORMAL = "normal"
    INSUFFICIENT = "insufficient"


class AnomalyStatus(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    NORMAL = "normal"
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"


class TaskKind(StrEnum):
    SEARCH = "search"
    TRANSCRIPTION = "transcription"
    AVATAR = "avatar"
    COPYWRITING = "copywriting"
    VIDEO_EDITING = "video_editing"
    PUBLISHING = "publishing"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    SUBMITTED = "submitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"


class TranscriptStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class SamplingStatus(StrEnum):
    PENDING = "pending"
    OBSERVED = "observed"
    MISSED = "missed"


class ProviderMode(StrEnum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


class SearchBatchStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class PlatformRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    CACHED = "cached"
    FAILED = "failed"
    BLOCKED = "blocked"
    OUTCOME_UNKNOWN = "outcome_unknown"


class MediaResolutionStatus(StrEnum):
    PREVIEW = "preview"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    OUTCOME_UNKNOWN = "outcome_unknown"


class ProviderErrorKind(StrEnum):
    AUTHORIZATION = "authorization"
    RATE_LIMIT = "rate_limit"
    VALIDATION = "validation"
    CONNECTION = "connection"
    SERVICE = "service"
    OUTCOME_UNKNOWN = "outcome_unknown"


class VideoEditStepKind(StrEnum):
    TRIM = "trim"
    SUBTITLE = "subtitle"
    WATERMARK = "watermark"
    TRANSITION = "transition"
    BACKGROUND_MUSIC = "background_music"
    SPEED = "speed"
    RESIZE = "resize"
    FILTER = "filter"
    CONCAT = "concat"
    # AI 智能剪辑步骤
    AI_SUBTITLE = "ai_subtitle"        # 基于 Whisper 的自动字幕生成
    AI_VOLUME_NORM = "ai_volume_norm"  # 音量标准化（EBU R128）
    AI_ENHANCE = "ai_enhance"          # 画面增强（亮度/对比度/锐化/降噪）
    AI_SILENCE_TRIM = "ai_silence_trim"  # 智能静音裁剪


class PublishPlatform(StrEnum):
    DOUYIN = "douyin"
    KUAISHOU = "kuaishou"
    WECHAT_CHANNELS = "wechat_channels"
    XIAOHONGSHU = "xiaohongshu"


class PublishStatus(StrEnum):
    PENDING = "pending"
    MANUAL_READY = "manual_ready"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class PipelineStage(StrEnum):
    KEYWORD_SEARCH = "keyword_search"
    MEDIA_RESOLUTION = "media_resolution"
    TRANSCRIPTION = "transcription"
    COPYWRITING = "copywriting"
    HUMAN_REVIEW = "human_review"
    AVATAR_GENERATION = "avatar_generation"
    VIDEO_EDITING = "video_editing"
    PUBLISHING = "publishing"


class PipelineRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class AvatarAssetKind(StrEnum):
    AVATAR = "avatar"
    VOICE = "voice"


class AvatarProviderStatus(StrEnum):
    QUEUED = "queued"
    SUBMITTED = "submitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"


class VideoMetricSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str
    sampled_at: datetime
    plays: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    favorites: int | None = Field(default=None, ge=0)
    followers: int | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=1)


class HeatResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: float = Field(ge=0, le=100)
    level: HeatLevel
    confidence: float = Field(ge=0, le=1)
    provisional: bool = True
    reasons: list[str] = Field(default_factory=list)
    model_version: str = "rule-v1"
    component_scores: dict[str, float | None] = Field(default_factory=dict)
    percentiles: dict[str, float | None] = Field(default_factory=dict)
    bucket_definition: str | None = None
    bucket_sample_size: int = Field(default=0, ge=0)
    baseline_window: str = "30d"
    snapshot_count: int = Field(default=1, ge=0)
    growth_window_hours: float | None = Field(default=None, ge=0)
    missing_fields: list[str] = Field(default_factory=list)
    unavailable_components: list[str] = Field(default_factory=list)
    threshold_checks: dict[str, bool | None] = Field(default_factory=dict)
    official_hot: bool = False
    official_rank: int | None = Field(default=None, ge=1)
    official_hot_value: float | None = Field(default=None, ge=0)
    momentum_state: MomentumState = MomentumState.INSUFFICIENT
    anomaly_status: AnomalyStatus = AnomalyStatus.NOT_EVALUATED
    next_sample_at: datetime | None = None


class VideoCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    video_id: str
    platform_item_id: str | None = None
    title: str = Field(min_length=1)
    author_id: str
    author_name: str
    platform: Platform
    category: str
    published_at: datetime
    source_url: HttpUrl | None = None
    source_type: DataSource
    rights_status: str = "metadata_only"
    matched_by: list[str] = Field(default_factory=list)
    cohort_key: str | None = None
    eligibility_status: EligibilityStatus = EligibilityStatus.PENDING_REVIEW
    evidence: str | None = None
    feed_id: str | None = None
    finder_user_name: str | None = None
    official_hot: bool = False
    official_rank: int | None = Field(default=None, ge=1)
    official_hot_value: float | None = Field(default=None, ge=0)
    data_quality_warnings: list[str] = Field(default_factory=list)
    metrics: VideoMetricSnapshot
    heat: HeatResult


class SourceRequest(BaseModel):
    source: DataSource
    platform: Platform = Platform.DOUYIN
    request_id: str = Field(default_factory=lambda: f"discover-{uuid4().hex[:12]}")
    category: str = "B2B/AI企业服务获客数字人口播"
    keywords: list[str] = Field(default_factory=list)
    urls: list[HttpUrl] = Field(default_factory=list)
    cursor: str | None = None
    search_id: str | None = None
    limit: int = Field(default=10, ge=1, le=100)
    page_size: int = Field(default=10, ge=1, le=100)
    publish_time: int = Field(default=1)
    sort_type: int = Field(default=0)
    snapshot_policy_hours: list[int] = Field(default_factory=lambda: [0, 2, 6, 24])


class SourceCapability(BaseModel):
    provider_name: str
    enabled: bool
    supports_keyword_search: bool = False
    metadata_only: bool = True
    permission_status: str
    max_page_size: int = Field(default=10, ge=1)
    missing_configuration: list[str] = Field(default_factory=list)


class ProviderCapability(BaseModel):
    provider_name: str
    display_name: str
    mode: ProviderMode
    enabled: bool
    supported_platforms: list[Platform] = Field(default_factory=list)
    max_page_size: int = Field(default=10, ge=1, le=10)
    supports_published_after: bool = True
    supports_metric_refresh: bool = False
    supports_usage: bool = False
    permission_status: str
    credential_alias: str | None = None
    missing_configuration: list[str] = Field(default_factory=list)


class ProviderSearchError(BaseModel):
    kind: ProviderErrorKind
    message: str
    code: str | None = None
    item_index: int | None = Field(default=None, ge=0)
    retryable: bool = False


class ProviderSearchItem(BaseModel):
    platform: Platform
    platform_item_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author_id: str = Field(min_length=1)
    author_name: str = Field(min_length=1)
    published_at: datetime
    source_url: HttpUrl | None = None
    provider_rank: int = Field(ge=1, le=10)
    metrics: VideoMetricSnapshot
    evidence: str | None = None
    data_quality_warnings: list[str] = Field(default_factory=list)


class ProviderSearchPage(BaseModel):
    platform: Platform
    provider: str
    items: list[ProviderSearchItem] = Field(default_factory=list)
    observed_at: datetime
    request_id: str
    api_call_count: int = Field(default=0, ge=0, le=1)
    quota_remaining: int | None = Field(default=None, ge=0)
    billable_units: float | None = Field(default=None, ge=0)
    has_more: bool = False
    raw_item_count: int = Field(default=0, ge=0)
    parsed_item_count: int = Field(default=0, ge=0)
    payload_diagnostic: str | None = None
    errors: list[ProviderSearchError] = Field(default_factory=list)


class ProviderUsage(BaseModel):
    provider: str
    period_started_at: datetime
    period_ends_at: datetime
    platform_queries: int = Field(default=0, ge=0)
    quota_remaining: int | None = Field(default=None, ge=0)
    billable_units: float | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    currency: str = "CNY"


class ProviderMediaResult(BaseModel):
    platform: Platform
    provider: str
    platform_item_id: str
    media_url: HttpUrl
    observed_at: datetime
    request_id: str
    api_call_count: int = Field(default=1, ge=0, le=1)
    billable_units: float = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


class MediaResolutionAttempt(BaseModel):
    resolution_id: str = Field(default_factory=lambda: f"media-{uuid4().hex[:12]}")
    idempotency_key: str = Field(min_length=8)
    candidate_id: str
    platform: Platform
    platform_item_id: str
    provider: str
    status: MediaResolutionStatus = MediaResolutionStatus.RUNNING
    estimated_cost_cny: float | None = Field(default=None, ge=0)
    billable_units: float | None = Field(default=None, ge=0)
    api_call_count: int = Field(default=0, ge=0, le=1)
    provider_request_id: str | None = None
    task_id: str | None = None
    error_kind: ProviderErrorKind | None = None
    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    updated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


class AvatarCapability(BaseModel):
    provider_name: str
    display_name: str
    mode: ProviderMode = ProviderMode.SANDBOX
    enabled: bool = False
    permission_status: str
    max_script_chars: int = Field(default=240, ge=1)
    supported_aspect_ratios: list[str] = Field(default_factory=lambda: ["9:16"])
    estimated_cost_cny: float | None = Field(default=None, ge=0)
    estimated_seconds: int | None = Field(default=None, ge=0)
    missing_configuration: list[str] = Field(default_factory=list)
    profiles: list["AvatarProfile"] = Field(default_factory=list)


class AvatarProfile(BaseModel):
    """面向客户展示的数字人生成档位，而非底层模型名称。"""

    profile_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str = ""
    enabled: bool = False
    estimated_cost_cny: float | None = Field(default=None, ge=0)
    estimated_seconds: int | None = Field(default=None, ge=0)
    required_vram_gb: int | None = Field(default=None, ge=0)
    missing_configuration: list[str] = Field(default_factory=list)


class AvatarAsset(BaseModel):
    asset_id: str = Field(min_length=1)
    kind: AvatarAssetKind
    name: str = Field(min_length=1)
    preview_url: str | None = None
    authorized: bool = False


class AvatarSubmitRequest(BaseModel):
    script_text: str = Field(min_length=1)
    source_task_id: str | None = None
    source_revision_id: str | None = None
    avatar_id: str = Field(min_length=1)
    voice_id: str = Field(min_length=1)
    profile_id: str = "default"
    speech_rate: float = Field(default=1.0, ge=0.8, le=1.2)
    aspect_ratio: str = "9:16"
    resolution: str = "1080x1920"
    background: str = "transparent"
    rights_holder: str = Field(min_length=1)
    script_rights_confirmed: bool
    avatar_rights_confirmed: bool
    voice_rights_confirmed: bool
    idempotency_key: str = Field(min_length=8)

    @model_validator(mode="after")
    def validate_rights(self):
        if not all(
            [
                self.script_rights_confirmed,
                self.avatar_rights_confirmed,
                self.voice_rights_confirmed,
            ]
        ):
            raise ValueError("必须确认文案、肖像和声音授权。")
        return self


class AvatarJobSnapshot(BaseModel):
    job_id: str
    idempotency_key: str
    status: AvatarProviderStatus
    progress: int = Field(default=0, ge=0, le=100)
    stage: str = "等待处理"
    provider_job_id: str | None = None
    estimated_cost_cny: float | None = Field(default=None, ge=0)
    estimated_seconds: int | None = Field(default=None, ge=0)
    result_mime: str | None = None
    result_size_bytes: int | None = Field(default=None, ge=0)
    error_kind: ProviderErrorKind | None = None
    error_message: str | None = None


class PlatformCapability(BaseModel):
    platform: Platform
    label: str
    status_label: str
    description: str
    supports_manual: bool = True
    supports_csv: bool = True
    supports_automatic_search: bool = False
    automatic_search_enabled: bool = False


class NormalizedCandidate(BaseModel):
    platform_item_id: str
    title: str = Field(min_length=1)
    author_id: str
    author_name: str
    platform: Platform = Platform.DOUYIN
    category: str = "B2B/AI企业服务获客数字人口播"
    published_at: datetime
    source_url: HttpUrl | None = None
    source_type: DataSource
    metrics: VideoMetricSnapshot
    rights_status: str = "metadata_only"
    matched_by: list[str] = Field(default_factory=list)
    cohort_key: str | None = None
    eligibility_status: EligibilityStatus = EligibilityStatus.PENDING_REVIEW
    evidence: str | None = None
    feed_id: str | None = None
    finder_user_name: str | None = None
    official_hot: bool = False
    official_rank: int | None = Field(default=None, ge=1)
    official_hot_value: float | None = Field(default=None, ge=0)
    data_quality_warnings: list[str] = Field(default_factory=list)


class ImportErrorDetail(BaseModel):
    row: int = Field(ge=1)
    field: str | None = None
    message: str


class SourcePage(BaseModel):
    items: list[NormalizedCandidate] = Field(default_factory=list)
    cursor: str | None = None
    search_id: str | None = None
    has_more: bool = False
    errors: list[ImportErrorDetail] = Field(default_factory=list)


class SyncReport(BaseModel):
    run_id: str
    source: DataSource
    started_at: datetime
    finished_at: datetime
    added_candidates: int = Field(ge=0)
    updated_candidates: int = Field(ge=0)
    added_snapshots: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    missing_fields: dict[str, int] = Field(default_factory=dict)
    errors: list[ImportErrorDetail] = Field(default_factory=list)
    next_suggested_sync_at: datetime | None = None
    permission_status: str = "not_required"


class CandidateMatch(BaseModel):
    request_id: str
    video_id: str
    keyword: str
    cohort_key: str
    platform: Platform = Platform.DOUYIN
    provider_name: str = "legacy"
    platform_rank: int = Field(default=10, ge=1, le=10)
    observed_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    publish_time: int = Field(default=1)
    sort_type: int = Field(default=0)
    evidence: str | None = None


class DiscoveryResult(BaseModel):
    request_id: str
    keyword: str
    provider_name: str
    platform: Platform = Platform.DOUYIN
    batch_id: str | None = None
    requested_count: int = Field(ge=1, le=100)
    fetched_count: int = Field(default=0, ge=0)
    unique_count: int = Field(default=0, ge=0)
    duplicate_count: int = Field(default=0, ge=0)
    raw_item_count: int = Field(default=0, ge=0)
    parsed_item_count: int = Field(default=0, ge=0)
    out_of_window_count: int = Field(default=0, ge=0)
    invalid_count: int = Field(default=0, ge=0)
    result_state: str = "historical_unknown"
    payload_diagnostic: str | None = None
    exhausted: bool = False
    partial: bool = False
    permission_status: str
    publish_time: int = Field(default=1)
    sort_type: int = Field(default=0)
    api_call_count: int = Field(default=0, ge=0, le=1)
    started_at: datetime
    finished_at: datetime
    import_report: SyncReport | None = None
    errors: list[ImportErrorDetail] = Field(default_factory=list)
    request_fingerprint: str | None = None
    provider_request_id: str | None = None
    billable_units: float | None = Field(default=None, ge=0)
    cache_hit: bool = False


class SearchBatch(BaseModel):
    batch_id: str = Field(default_factory=lambda: f"batch-{uuid4().hex[:12]}")
    keyword: str = Field(min_length=2, max_length=50)
    published_window_days: int = Field(default=7)
    requested_count_per_platform: int = Field(default=10, ge=1, le=10)
    provider: str
    mode: ProviderMode
    status: SearchBatchStatus = SearchBatchStatus.PENDING
    platforms: list[Platform] = Field(
        default_factory=lambda: [
            Platform.DOUYIN,
            Platform.XIAOHONGSHU,
            Platform.WECHAT_CHANNELS,
        ]
    )
    platform_run_ids: list[str] = Field(default_factory=list)
    force_refresh: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    finished_at: datetime | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_window(self):
        if self.published_window_days not in {1, 7}:
            raise ValueError("发布时间范围只支持近 1 天或近 7 天。")
        return self


class PlatformSearchRun(BaseModel):
    run_id: str = Field(default_factory=lambda: f"platform-{uuid4().hex[:12]}")
    batch_id: str
    platform: Platform
    provider: str
    mode: ProviderMode
    status: PlatformRunStatus = PlatformRunStatus.QUEUED
    requested_count: int = Field(default=10, ge=1, le=10)
    returned_count: int = Field(default=0, ge=0, le=10)
    raw_item_count: int = Field(default=0, ge=0)
    parsed_item_count: int = Field(default=0, ge=0)
    out_of_window_count: int = Field(default=0, ge=0)
    invalid_count: int = Field(default=0, ge=0)
    duplicate_count: int = Field(default=0, ge=0)
    result_state: str = "historical_unknown"
    payload_diagnostic: str | None = None
    api_call_count: int = Field(default=0, ge=0, le=1)
    billable_units: float | None = Field(default=None, ge=0)
    quota_remaining: int | None = Field(default=None, ge=0)
    cache_hit: bool = False
    cached_from_run_id: str | None = None
    idempotency_key: str
    request_fingerprint: str
    provider_request_id: str | None = None
    credential_alias: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    finished_at: datetime | None = None
    error: str | None = None
    errors: list[ProviderSearchError] = Field(default_factory=list)


class KeywordTrendResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    keyword: str
    platform: Platform = Platform.DOUYIN
    candidate_id: str
    computed_at: datetime
    score: float = Field(ge=0, le=100)
    level: KeywordTrendLevel
    confidence: float = Field(ge=0, le=1)
    provisional: bool = True
    platform_rank: int = Field(ge=1, le=10)
    likes_per_hour: float | None = None
    engagement_per_hour: float | None = None
    like_growth_per_hour: float | None = None
    engagement_growth_per_hour: float | None = None
    acceleration_ratio: float | None = None
    valid_snapshot_count: int = Field(default=1, ge=0)
    recrawl_count: int = Field(default=0, ge=0)
    recall_count: int = Field(default=1, ge=0)
    missed_checkpoint_count: int = Field(default=0, ge=0)
    sampling_span_hours: float | None = None
    appearance_count: int = Field(ge=1)
    pool_size: int = Field(ge=1)
    component_scores: dict[str, float | None] = Field(default_factory=dict)
    percentiles: dict[str, float | None] = Field(default_factory=dict)
    anomaly_status: AnomalyStatus = AnomalyStatus.NOT_EVALUATED
    anomaly_penalty: float = Field(default=1.0, gt=0, le=1)
    display_tier: str = "ordinary"
    effective_interactions: float = Field(default=0, ge=0)
    tier_reasons: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    model_version: str = "keyword-trend-v1"


class RelevanceReview(BaseModel):
    candidate_id: str
    is_digital_human: bool | None = None
    is_target_vertical: bool | None = None
    has_marketing_cta: bool | None = None
    marketing_goal: str = "lead_generation"
    status: ReviewStatus = ReviewStatus.PENDING
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    evidence: str | None = None
    exclusion_reason: str | None = None


class TranscriptSegment(BaseModel):
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, gt=0)
    text: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    needs_review: bool = False
    reviewed: bool = False

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.start is None and self.end is None:
            return self
        if self.start is None or self.end is None:
            raise ValueError("片段时间必须同时提供开始和结束时间。")
        if self.end <= self.start:
            raise ValueError("片段结束时间必须晚于开始时间。")
        return self


class TranscriptRevision(BaseModel):
    revision_id: str
    task_id: str
    revision_number: int = Field(ge=1)
    status: TranscriptStatus = TranscriptStatus.DRAFT
    created_at: datetime
    updated_at: datetime
    reviewer: str | None = None
    language: str = "zh"
    model_name: str = "base"
    media_sha256: str
    original_segments: list[TranscriptSegment]
    corrected_segments: list[TranscriptSegment]


class SamplingCheckpoint(BaseModel):
    checkpoint_id: str
    keyword: str
    candidate_id: str
    request_id: str
    platform: Platform = Platform.DOUYIN
    provider_name: str = "legacy"
    published_window_days: int = Field(default=1)
    offset_hours: int = Field(gt=0)
    due_at: datetime
    status: SamplingStatus = SamplingStatus.PENDING
    observed_at: datetime | None = None


class TaskRecord(BaseModel):
    task_id: str
    kind: TaskKind
    title: str
    status: TaskStatus
    progress: int = Field(ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    elapsed_seconds: float | None = Field(default=None, ge=0)
    error_message: str | None = None
    retry_count: int = Field(default=0, ge=0, le=1)
    outputs: dict[str, str] = Field(default_factory=dict)
    is_mock: bool = True
    stage: str = "等待处理"


class TranscriptionTask(TaskRecord):
    kind: TaskKind = TaskKind.TRANSCRIPTION
    media_name: str
    media_type: str
    rights_confirmed: bool
    rights_holder: str | None = None
    rights_confirmed_at: datetime | None = None
    rights_purpose: str = "仅用于本次私有文案转写"
    candidate_id: str | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
    stage: str = "等待处理"
    media_sha256: str | None = None
    model_name: str | None = None
    asr_hotwords: str | None = None
    language: str | None = None
    duration_seconds: float | None = Field(default=None, gt=0, le=15 * 60)
    source_kind: str = "asr"
    source_url: str | None = None
    timing_available: bool = True
    approved_revision_id: str | None = None


class AvatarTask(TaskRecord):
    kind: TaskKind = TaskKind.AVATAR
    script_text: str
    source_task_id: str | None = None
    source_revision_id: str | None = None
    avatar_id: str
    avatar_name: str
    voice_id: str
    voice_name: str
    profile_id: str = "default"
    speech_rate: float = Field(default=1.0, ge=0.8, le=1.2)
    aspect_ratio: str = "9:16"
    resolution: str = "1080x1920"
    background: str = "transparent"
    rights_holder: str
    rights_confirmed_at: datetime
    idempotency_key: str
    provider_name: str
    backend_job_id: str | None = None
    provider_job_id: str | None = None
    provider_status: AvatarProviderStatus = AvatarProviderStatus.QUEUED
    stage: str = "等待提交"
    estimated_cost_cny: float | None = Field(default=None, ge=0)
    estimated_seconds: int | None = Field(default=None, ge=0)
    result_path: str | None = None
    result_mime: str | None = None
    result_size_bytes: int | None = Field(default=None, ge=0)
    error_kind: ProviderErrorKind | None = None


# ---------------------------------------------------------------------------
# 文案改写
# ---------------------------------------------------------------------------


class CopywritingTask(TaskRecord):
    """基于 LLM 的文案改写 / 复刻任务。"""

    kind: TaskKind = TaskKind.COPYWRITING
    creation_mode: str = "rewrite"
    source_text: str = ""
    content_brief: str = ""
    platform: Platform = Platform.DOUYIN
    target_audience: str = ""
    selling_points: str = ""
    call_to_action: str = ""
    style_prompt: str = ""
    target_length: int = Field(default=300, ge=50, le=2000)
    tone: str = "professional"
    rewrite_goal: str = ""
    provider_name: str = "local_llm"
    model_name: str = ""
    token_usage: dict[str, int] = Field(default_factory=dict)
    result_text: str | None = None
    result_variants: list[str] = Field(default_factory=list)
    source_task_id: str | None = None
    source_revision_id: str | None = None


# ---------------------------------------------------------------------------
# 视频剪辑
# ---------------------------------------------------------------------------


class VideoEditStep(BaseModel):
    """单个剪辑步骤。"""

    model_config = ConfigDict(frozen=True)

    step_id: str = Field(default_factory=lambda: f"step-{uuid4().hex[:8]}")
    kind: VideoEditStepKind
    params: dict[str, Any] = Field(default_factory=dict)
    order: int = Field(default=0, ge=0)


class VideoEditConfig(BaseModel):
    """剪辑配置：由多个有序步骤组成。"""

    model_config = ConfigDict(frozen=True)

    steps: list[VideoEditStep] = Field(default_factory=list)
    output_format: str = "mp4"
    output_resolution: str = "1080x1920"
    output_fps: int = Field(default=30, ge=15, le=60)
    output_bitrate: str = "4M"


class VideoEditTask(TaskRecord):
    """视频剪辑任务。"""

    kind: TaskKind = TaskKind.VIDEO_EDITING
    source_video_path: str
    subtitle_text: str | None = None
    subtitle_style: str = "default"
    edit_config: VideoEditConfig = Field(default_factory=VideoEditConfig)
    result_path: str | None = None
    result_mime: str | None = None
    result_size_bytes: int | None = Field(default=None, ge=0)
    source_task_id: str | None = None
    source_avatar_task_id: str | None = None


# ---------------------------------------------------------------------------
# 发布
# ---------------------------------------------------------------------------


class PublishTarget(BaseModel):
    """发布目标平台配置。"""

    model_config = ConfigDict(frozen=True)

    platform: PublishPlatform
    title: str = Field(min_length=1, max_length=100)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    cover_image_path: str | None = None
    scheduled_at: datetime | None = None
    visibility: str = "public"


class PublishTask(TaskRecord):
    """发布任务。"""

    kind: TaskKind = TaskKind.PUBLISHING
    batch_id: str | None = None
    video_path: str
    target: PublishTarget
    publish_status: PublishStatus = PublishStatus.PENDING
    platform_video_id: str | None = None
    platform_url: str | None = None
    provider_name: str = ""
    source_pipeline_run_id: str | None = None
    stage: str = "等待发布"


# ---------------------------------------------------------------------------
# 端到端流水线
# ---------------------------------------------------------------------------


class PipelineStepResult(BaseModel):
    """流水线单步骤结果。"""

    model_config = ConfigDict(frozen=True)

    stage: PipelineStage
    status: TaskStatus
    task_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
    outputs: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 转写支持格式
# ---------------------------------------------------------------------------

# 转写服务允许的媒体格式（视频 + 音频）
ALLOWED_TRANSCRIPTION_EXTENSIONS: set[str] = {
    ".mp4", ".mov",           # 视频
    ".wav", ".mp3", ".aac",   # 音频
    ".flac", ".ogg", ".m4a",  # 音频
}


# ---------------------------------------------------------------------------
# 编辑模板
# ---------------------------------------------------------------------------


class TemplateStepDef(BaseModel):
    """模板步骤定义"""

    model_config = ConfigDict(frozen=True)

    kind: str  # VideoEditStepKind 的值
    params: dict[str, Any] = Field(default_factory=dict)
    label: str = ""


class EditTemplate(BaseModel):
    """编辑模板"""

    model_config = ConfigDict(frozen=True)

    template_id: str
    name: str
    description: str = ""
    category: str = "custom"  # optimization | subtitle | social_media | podcast | custom
    icon: str = "RocketOutlined"
    steps: list[TemplateStepDef] = Field(default_factory=list)
    output_format: str = "mp4"
    output_resolution: str = "1080x1920"
    output_fps: int = 30
    output_bitrate: str = "4M"
    is_builtin: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# 端到端流水线
# ---------------------------------------------------------------------------


class PipelineRun(BaseModel):
    """端到端流水线执行记录。"""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(default_factory=lambda: f"pipeline-{uuid4().hex[:12]}")
    keyword: str
    status: PipelineRunStatus = PipelineRunStatus.PENDING
    stages: list[PipelineStepResult] = Field(default_factory=list)
    current_stage: PipelineStage | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    updated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    finished_at: datetime | None = None
    candidate_video_id: str | None = None
    copywriting_task_id: str | None = None
    avatar_task_id: str | None = None
    edit_task_id: str | None = None
    publish_task_ids: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
