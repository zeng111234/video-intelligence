from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class Platform(StrEnum):
    DOUYIN = "douyin"
    BILIBILI = "bilibili"
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


class GrowthStage(StrEnum):
    """候选增长阶段标签（供前端直接展示）。"""

    OBSERVING_SAMPLE = "观察样本"
    CONFIRMING = "增长确认中"
    HOT_CANDIDATE = "热门候选"
    EXPLODING_CANDIDATE = "爆发候选"


class CopySource(StrEnum):
    """三档文案来源。"""

    METADATA_ORIGINAL = "metadata_original"
    DOUBAO_MOBILE_TRANSCRIPT = "doubao_mobile_transcript"
    AUTHORIZED_ASR_TRANSCRIPT = "authorized_asr_transcript"


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
    PAUSED = "paused"


class TranscriptStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class SamplingStatus(StrEnum):
    PENDING = "pending"
    OBSERVED = "observed"
    MISSED = "missed"
    CANCELLED = "cancelled"


class ProviderMode(StrEnum):
    SANDBOX = "sandbox"
    LOCAL_BROWSER = "local_browser"
    PUBLIC_WEB = "public_web"
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
    PRODUCT_SHOWCASE = "product_showcase"
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
    BILIBILI = "bilibili"


class PublishStatus(StrEnum):
    PENDING = "pending"
    MANUAL_READY = "manual_ready"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    ACTION_REQUIRED = "action_required"


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


class ProductionBatchStatus(StrEnum):
    """批次控制台的聚合状态；单条的真实执行状态保留在 PipelineRun。"""

    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_REVIEW = "awaiting_review"
    AWAITING_PUBLISH = "awaiting_publish"
    PARTIAL = "partial"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProductionBatchItemStatus(StrEnum):
    """批次项的调度状态，避免将预检受阻误报成流水线失败。"""

    PLANNED = "planned"
    BLOCKED = "blocked"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    AWAITING_PUBLISH = "awaiting_publish"
    READY_TO_PUBLISH = "ready_to_publish"
    SKIPPED = "skipped"
    SUCCEEDED = "succeeded"
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
    # 分享/收藏在数据源未返回时保持 null，前端显示“未返回”，不当作 0
    share_count: int | None = Field(default=None, ge=0)
    collect_count: int | None = Field(default=None, ge=0)
    metrics: VideoMetricSnapshot
    heat: HeatResult


class CandidateCopyProbe(BaseModel):
    """Ephemeral three-second ASR result; the recognised text is never stored."""

    candidate_id: str
    status: str
    message: str
    checked_at: datetime
    version: str = "copy_probe_v1"


class HotWordRecord(BaseModel):
    """官方实时热点词持久化记录（供前端搜索建议读取）。"""

    model_config = ConfigDict(frozen=True)

    word: str = Field(min_length=1)
    hot_value: int | None = Field(default=None, ge=0)
    fetched_at: datetime
    source: str = "douyin_hot_words"


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
    max_page_size: int = Field(default=10, ge=1, le=100)
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
    provider_rank: int = Field(ge=1, le=100)
    metrics: VideoMetricSnapshot
    evidence: str | None = None
    data_quality_warnings: list[str] = Field(default_factory=list)


class ProviderSearchPage(BaseModel):
    platform: Platform
    provider: str
    items: list[ProviderSearchItem] = Field(default_factory=list)
    # 仅热点宝使用：标题相关、时长有效，但新增播放量未超过主榜阈值的候选。
    low_incremental_items: list[ProviderSearchItem] = Field(default_factory=list)
    observed_at: datetime
    request_id: str
    api_call_count: int = Field(default=0, ge=0, le=1)
    quota_remaining: int | None = Field(default=None, ge=0)
    billable_units: float | None = Field(default=None, ge=0)
    has_more: bool = False
    raw_item_count: int = Field(default=0, ge=0)
    parsed_item_count: int = Field(default=0, ge=0)
    duration_filtered_count: int = Field(default=0, ge=0)
    incremental_play_filtered_count: int = Field(default=0, ge=0)
    relevance_filtered_count: int = Field(default=0, ge=0)
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
    # 素材录入能力独立于供应商名称，前端不应通过 provider_name 猜测。
    supports_cloud_avatar_training: bool = False
    supports_voice_cloning: bool = False
    supports_voice_sample_upload: bool = False


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
    # image/video 决定前端的媒体元素；不能再通过 URL 后缀猜测。
    preview_type: str = "image"
    # ready 之外的素材可展示训练进度，但不可用于提交生成任务。
    status: str = "ready"
    status_message: str | None = None
    source_type: str = "built_in"


class AvatarSubmitRequest(BaseModel):
    script_text: str = Field(min_length=1)
    video_name: str | None = Field(default=None, max_length=100)
    keyword: str | None = Field(default=None, max_length=100)
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
    platform_rank: int = Field(default=10, ge=1, le=100)
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
    irrelevant_count: int = Field(default=0, ge=0)
    duration_filtered_count: int = Field(default=0, ge=0)
    incremental_play_filtered_count: int = Field(default=0, ge=0)
    relevance_rule_version: str | None = None
    result_state: str = "historical_unknown"
    payload_diagnostic: str | None = None
    user_notice: str | None = None
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
    # 0 表示不限发布时间；30 天供快手近期召回，历史批次仍兼容 180/300 天。
    published_window_days: int = Field(default=0)
    # 热点宝的榜单统计周期，和发布时间筛选分开保存。
    hotspot_window_hours: int | None = None
    monitoring_policy: str = "low_cost_three_point_v1"
    sampling_offsets_hours: list[int] = Field(default_factory=lambda: [0, 6, 24])
    requested_count_per_platform: int = Field(default=10, ge=1, le=100)
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
    # 文案质量补检的搜索轨迹。保存在批次 JSON 中，历史批次缺省时仍可读取，
    # 不增加数据库表，也不会把识别出的文字保存下来。
    copy_search_queries: list[str] = Field(default_factory=list)
    copy_related_terms: list[str] = Field(default_factory=list)
    copy_matrix_exhausted: bool = False
    force_refresh: bool = False
    # 只有用户在批次详情中明确确认后，才允许定时任务发起后续付费采样。
    tracking_authorized: bool = False
    tracking_parent_batch_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    finished_at: datetime | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_window(self):
        if self.published_window_days not in {0, 1, 3, 7, 30, 180, 300}:
            raise ValueError(
                "发布时间范围只支持不限、近 1 天、近 3 天、近 7 天、近 30 天、近半年或近 10 个月。"
            )
        if self.hotspot_window_hours not in {None, 1, 24, 72, 168}:
            raise ValueError("热点宝榜单周期只支持近 1 小时、近 1 天、近 3 天或近 7 天。")
        return self


class ProviderSafetyState(BaseModel):
    """Persistent, provider-wide pacing state for user-authorized collection."""

    provider: str
    active_run_id: str | None = None
    lease_expires_at: datetime | None = None
    next_allowed_at: datetime | None = None
    blocked_until: datetime | None = None
    blocked_reason: str | None = None
    rolling_window_started_at: datetime | None = None
    real_runs_in_window: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


class PlatformSearchRun(BaseModel):
    run_id: str = Field(default_factory=lambda: f"platform-{uuid4().hex[:12]}")
    batch_id: str
    platform: Platform
    provider: str
    mode: ProviderMode
    status: PlatformRunStatus = PlatformRunStatus.QUEUED
    requested_count: int = Field(default=10, ge=1, le=100)
    returned_count: int = Field(default=0, ge=0, le=100)
    raw_item_count: int = Field(default=0, ge=0)
    parsed_item_count: int = Field(default=0, ge=0)
    out_of_window_count: int = Field(default=0, ge=0)
    invalid_count: int = Field(default=0, ge=0)
    duplicate_count: int = Field(default=0, ge=0)
    irrelevant_count: int = Field(default=0, ge=0, le=100)
    duration_filtered_count: int = Field(default=0, ge=0)
    incremental_play_filtered_count: int = Field(default=0, ge=0)
    low_incremental_items: list[ProviderSearchItem] = Field(default_factory=list)
    relevance_rule_version: str | None = None
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
    platform_rank: int = Field(ge=1, le=100)
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
    growth_stage: GrowthStage = GrowthStage.OBSERVING_SAMPLE
    snapshot_count: int = Field(default=1, ge=0)
    next_recrawl_at: datetime | None = None
    share_count: int | None = Field(default=None, ge=0)
    collect_count: int | None = Field(default=None, ge=0)
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
    # AI 自动质检结果；保留旧的 needs_review/reviewed 以兼容历史人工校对版本。
    quality_status: str = "pending"
    quality_source: str = "primary_asr"
    quality_note: str | None = None
    alternatives: list[str] = Field(default_factory=list)

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
    approval_mode: str = "manual"
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
    published_window_days: int = Field(default=0)
    offset_hours: int = Field(gt=0)
    due_at: datetime
    status: SamplingStatus = SamplingStatus.PENDING
    observed_at: datetime | None = None
    # 旧检查点默认不具备付费复搜授权，避免升级后意外产生调用。
    tracking_batch_id: str | None = None
    billing_authorized: bool = False


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
    provider_name: str | None = None
    provider_job_id: str | None = None
    provider_status: str | None = None
    provider_object_key: str | None = None
    estimated_cost_cny: float | None = Field(default=None, ge=0)
    pricing_version: str | None = None
    billing_authorized: bool = False
    asr_hotwords: str | None = None
    language: str | None = None
    duration_seconds: float | None = Field(default=None, gt=0, le=15 * 60)
    source_kind: str = "asr"
    source_url: str | None = None
    timing_available: bool = True
    approved_revision_id: str | None = None
    auto_reviewed: bool = False
    uncertain_segment_count: int = Field(default=0, ge=0)
    secondary_asr_count: int = Field(default=0, ge=0)
    llm_review_count: int = Field(default=0, ge=0)
    auto_review_error: str | None = None


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
    # 疑似属于其他企业、品牌、机构或人物的原文词，供前端在结果中高亮。
    attention_terms: list[str] = Field(default_factory=list)
    # 文案风险表达优化的结果。旧任务缺省为未检测，保证历史记录可继续读取。
    compliance_status: str = "not_checked"
    compliance_notes: list[str] = Field(default_factory=list)
    compliance_rewritten: bool = False
    compliance_retry_used: bool = False
    source_task_id: str | None = None
    source_revision_id: str | None = None
    # 三档文案来源标记（见 CopySource），默认空表示历史改写任务
    copy_source: str | None = None
    is_original_transcript: bool = False
    needs_manual_review: bool = False
    estimated_cost_cny: float | None = Field(default=None, ge=0)


class CopyResult(BaseModel):
    """三档文案来源的统一结果载荷。"""

    model_config = ConfigDict(frozen=True)

    copy_id: str = Field(default_factory=lambda: f"copyres-{uuid4().hex[:12]}")
    candidate_id: str | None = None
    copy_source: CopySource
    text: str = ""
    is_original_transcript: bool
    needs_manual_review: bool
    estimated_cost_cny: float | None = Field(default=None, ge=0)
    source_basis: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


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


class VideoEditorBatchItem(BaseModel):
    """智能剪辑自动批次中的单个素材状态。"""

    model_config = ConfigDict(frozen=True)

    item_id: str = Field(default_factory=lambda: f"edit-item-{uuid4().hex[:12]}")
    source_id: str
    title: str = ""
    status: str = "queued"
    analysis_id: str | None = None
    subtitle_task_id: str | None = None
    edit_task_id: str | None = None
    title_candidates: list[str] = Field(default_factory=list)
    selected_title: str | None = None
    selected_bgm_id: str | None = None
    bgm_reason: str | None = None
    provider_stage: str | None = None
    provider_job_ids: dict[str, str] = Field(default_factory=dict)
    provider_payload: dict[str, Any] = Field(default_factory=dict)
    actual_usage: dict[str, Any] = Field(default_factory=dict)
    edit_plan: dict[str, Any] = Field(default_factory=dict)
    enabled_plan_step_ids: list[str] = Field(default_factory=list)
    subtitle_segments: list[dict[str, Any]] = Field(default_factory=list)
    review_snapshot: dict[str, Any] = Field(default_factory=dict)
    review_confirmed_at: datetime | None = None
    result_media_url: str | None = None
    is_mock: bool = False
    publish_allowed: bool = True
    error_message: str | None = None
    confirmed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


class VideoEditorBatch(BaseModel):
    """可恢复的自动剪辑批次。"""

    model_config = ConfigDict(frozen=True)

    batch_id: str = Field(default_factory=lambda: f"edit-batch-{uuid4().hex[:12]}")
    target_platform: str = "douyin"
    subtitle_enabled: bool = True
    subtitle_model: str = "large-v3-turbo"
    bgm_enabled: bool = True
    bgm_id: str | None = None
    bgm_volume: float = Field(default=0.24, ge=0, le=1)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    output_format: str = "mp4"
    output_resolution: str = "1080x1920"
    output_fps: int = Field(default=30, ge=15, le=60)
    output_bitrate: str = "4M"
    provider_mode: str = "legacy"
    output_profile: str | None = None
    quote_id: str | None = None
    cost_quote: dict[str, Any] = Field(default_factory=dict)
    actual_usage: dict[str, Any] = Field(default_factory=dict)
    billing_confirmation: dict[str, Any] = Field(default_factory=dict)
    billing_confirmed_at: datetime | None = None
    idempotency_key: str | None = None
    is_mock: bool = False
    items: list[VideoEditorBatchItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    updated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


# ---------------------------------------------------------------------------
# 发布
# ---------------------------------------------------------------------------


class PublishTarget(BaseModel):
    """发布目标平台配置。"""

    model_config = ConfigDict(frozen=True)

    platform: PublishPlatform
    account_id: str | None = None
    title: str = Field(min_length=1, max_length=100)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    cover_image_path: str | None = None
    scheduled_at: datetime | None = None
    visibility: str = "public"
    auto_publish_authorized: bool = False
    use_prepared_page: bool = False
    native_music_mode: str = Field(
        default="off",
        pattern="^(off|auto_recommended)$",
    )
    native_music_hint: str = Field(default="", max_length=80)
    selected_music_title: str | None = Field(default=None, max_length=100)


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
    action_required: str | None = None
    final_publish_started_at: datetime | None = None
    outcome_evidence: str | None = None


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


class PipelineEvent(BaseModel):
    """持久化的流水线状态变更事件，用于恢复、审计和人工排障。"""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: f"pipeline-event-{uuid4().hex[:12]}")
    action: str = Field(min_length=1)
    status: PipelineRunStatus
    stage: PipelineStage | None = None
    message: str = ""
    details: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


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
    events: list[PipelineEvent] = Field(default_factory=list)
    error_message: str | None = None


# ---------------------------------------------------------------------------
# 可复用 IP 资产与生产批次
# ---------------------------------------------------------------------------


class ProductionProfile(BaseModel):
    """可复用的内容 IP 配方；只保存非敏感的创作与资产引用。"""

    model_config = ConfigDict(frozen=True)

    profile_id: str = Field(default_factory=lambda: f"ip-{uuid4().hex[:12]}")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    target_audience: str = Field(default="", max_length=200)
    platform: str = Field(default="douyin", max_length=40)
    script_style: str = Field(default="", max_length=500)
    avatar_id: str | None = None
    voice_id: str | None = None
    edit_template_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    updated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


class ProductionWorkspaceConfiguration(BaseModel):
    """单客户工作台的一次性基础设置与授权声明。"""

    rights_holder: str = Field(min_length=1, max_length=80)
    agreement_version: str = Field(default="workspace-rights-v1", min_length=1)
    agreement_accepted_at: datetime = Field(
        default_factory=lambda: datetime.now().astimezone()
    )
    default_profile_id: str | None = None
    default_publish_platforms: list[str] = Field(default_factory=lambda: ["douyin"])
    bundled_compute: bool = True
    copywriting_estimated_cost_cny: float | None = Field(default=None, ge=0)
    avatar_estimated_cost_cny: float | None = Field(default=None, ge=0)


class ProductionBatchItem(BaseModel):
    """批次中的单条候选计划与调度状态。"""

    model_config = ConfigDict(frozen=True)

    # 历史批次只有 candidate_id；新批次允许从链接、选题或已写成稿开始。
    candidate_id: str = ""
    run_id: str = Field(min_length=1)
    source_type: str = "candidate"
    source_value: str = ""
    display_title: str = ""
    candidate_role: str = "primary"
    spoken_material_status: str = "pending"
    spoken_material_message: str = ""
    profile_overrides: dict[str, str] = Field(default_factory=dict)
    status: ProductionBatchItemStatus = ProductionBatchItemStatus.PLANNED
    blocked_reasons: list[str] = Field(default_factory=list)
    current_stage: PipelineStage | None = None
    error_message: str | None = None
    video_path: str | None = None
    publish_mode: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())


class ProductionBatch(BaseModel):
    """可暂停、可恢复的批量生产控制记录。"""

    model_config = ConfigDict(frozen=True)

    batch_id: str = Field(default_factory=lambda: f"production-batch-{uuid4().hex[:12]}")
    name: str = Field(min_length=1, max_length=100)
    profile_id: str = Field(min_length=1)
    profile_name: str = Field(min_length=1)
    items: list[ProductionBatchItem] = Field(default_factory=list)
    status: ProductionBatchStatus = ProductionBatchStatus.PLANNED
    is_paused: bool = False
    execution_config: dict[str, Any] = Field(default_factory=dict)
    estimated_cost_cny: float = Field(default=0.0, ge=0)
    monthly_budget_used_cny: float = Field(default=0.0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    updated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PublishFeedback(BaseModel):
    """仅记录人工确认已发布作品的真实反馈数据。"""

    model_config = ConfigDict(frozen=True)

    feedback_id: str = Field(default_factory=lambda: f"feedback-{uuid4().hex[:12]}")
    publish_task_id: str = Field(min_length=1)
    pipeline_run_id: str | None = None
    platform: str = Field(min_length=1)
    views: int = Field(ge=0)
    likes: int = Field(default=0, ge=0)
    comments: int = Field(default=0, ge=0)
    leads: int = Field(default=0, ge=0)
    recorded_by: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=500)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
