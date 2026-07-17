from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class Platform(StrEnum):
    DOUYIN = "douyin"
    KUAISHOU = "kuaishou"
    XIAOHONGSHU = "xiaohongshu"


class DataSource(StrEnum):
    OFFICIAL = "official_authorized_api"
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


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TranscriptStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class SamplingStatus(StrEnum):
    PENDING = "pending"
    OBSERVED = "observed"
    MISSED = "missed"


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
    source_url: HttpUrl
    source_type: DataSource
    rights_status: str = "metadata_only"
    matched_by: list[str] = Field(default_factory=list)
    cohort_key: str | None = None
    eligibility_status: EligibilityStatus = EligibilityStatus.PENDING_REVIEW
    evidence: str | None = None
    official_hot: bool = False
    official_rank: int | None = Field(default=None, ge=1)
    official_hot_value: float | None = Field(default=None, ge=0)
    metrics: VideoMetricSnapshot
    heat: HeatResult


class SourceRequest(BaseModel):
    source: DataSource
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


class NormalizedCandidate(BaseModel):
    platform_item_id: str
    title: str = Field(min_length=1)
    author_id: str
    author_name: str
    platform: Platform = Platform.DOUYIN
    category: str = "B2B/AI企业服务获客数字人口播"
    published_at: datetime
    source_url: HttpUrl
    source_type: DataSource
    metrics: VideoMetricSnapshot
    rights_status: str = "metadata_only"
    matched_by: list[str] = Field(default_factory=list)
    cohort_key: str | None = None
    eligibility_status: EligibilityStatus = EligibilityStatus.PENDING_REVIEW
    evidence: str | None = None
    official_hot: bool = False
    official_rank: int | None = Field(default=None, ge=1)
    official_hot_value: float | None = Field(default=None, ge=0)


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
    platform_rank: int = Field(default=10, ge=1, le=10)
    observed_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    publish_time: int = Field(default=1)
    sort_type: int = Field(default=0)
    evidence: str | None = None


class DiscoveryResult(BaseModel):
    request_id: str
    keyword: str
    provider_name: str
    requested_count: int = Field(ge=1, le=100)
    fetched_count: int = Field(default=0, ge=0)
    unique_count: int = Field(default=0, ge=0)
    duplicate_count: int = Field(default=0, ge=0)
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


class KeywordTrendResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    keyword: str
    candidate_id: str
    computed_at: datetime
    score: float = Field(ge=0, le=100)
    level: KeywordTrendLevel
    confidence: float = Field(ge=0, le=1)
    provisional: bool = True
    platform_rank: int = Field(ge=1, le=10)
    likes_per_hour: float | None = None
    like_growth_per_hour: float | None = None
    appearance_count: int = Field(ge=1)
    pool_size: int = Field(ge=1)
    component_scores: dict[str, float | None] = Field(default_factory=dict)
    percentiles: dict[str, float | None] = Field(default_factory=dict)
    anomaly_status: AnomalyStatus = AnomalyStatus.NOT_EVALUATED
    anomaly_penalty: float = Field(default=1.0, gt=0, le=1)
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
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    needs_review: bool = False

    @model_validator(mode="after")
    def validate_time_range(self):
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
    language: str | None = None
    approved_revision_id: str | None = None
