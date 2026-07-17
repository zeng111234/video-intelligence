from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


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
    evidence: str | None = None
    official_hot: bool = False
    official_rank: int | None = Field(default=None, ge=1)
    official_hot_value: float | None = Field(default=None, ge=0)
    metrics: VideoMetricSnapshot
    heat: HeatResult


class SourceRequest(BaseModel):
    source: DataSource
    category: str = "B2B/AI企业服务获客数字人口播"
    keywords: list[str] = Field(default_factory=list)
    urls: list[HttpUrl] = Field(default_factory=list)
    cursor: str | None = None
    snapshot_policy_hours: list[int] = Field(default_factory=lambda: [0, 2, 6, 24])


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
    text: str
    confidence: float = Field(ge=0, le=1)
    needs_review: bool = False


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
    candidate_id: str | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
