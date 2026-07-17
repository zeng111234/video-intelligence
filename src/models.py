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
    MANUAL = "manual"
    CSV = "csv"
    MOCK = "mock"


class HeatLevel(StrEnum):
    S = "S"
    A = "A"
    B = "B"
    NORMAL = "普通"
    INSUFFICIENT = "数据不足"


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
    model_version: str = "mock-rule-v1"


class VideoCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    video_id: str
    title: str = Field(min_length=1)
    author_id: str
    author_name: str
    platform: Platform
    category: str
    published_at: datetime
    source_url: HttpUrl
    source_type: DataSource
    rights_status: str = "metadata_only"
    metrics: VideoMetricSnapshot
    heat: HeatResult


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
