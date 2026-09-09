"""API 响应 Schema —— Pydantic v2 显式模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class CandidateItem(BaseModel):
    video_id: str
    platform_item_id: str | None = None
    title: str
    platform: str
    author_name: str = ""
    category: str = ""
    heat_score: float = 0.0
    heat_level: str = ""
    source_url: str | None = None
    published_at: datetime | None = None
    observed_at: datetime | None = None
    publication_time_state: str = "platform"
    official_hot: bool = False
    official_rank: int | None = None
    snapshot_count: int = 0
    growth_window_hours: float | None = None
    heat_reasons: list[str] = Field(default_factory=list)


class CandidateCategoryOption(BaseModel):
    value: str
    count: int


class CandidateListResponse(BaseModel):
    items: list[CandidateItem]
    total: int
    category_options: list[CandidateCategoryOption] = Field(default_factory=list)


class TranscriptionResponse(BaseModel):
    task_id: str
    title: str
    status: str
    progress: int
    stage: str = ""
    media_name: str = ""
    model_name: str | None = None
    provider_name: str | None = None
    provider_job_id: str | None = None
    provider_status: str | None = None
    estimated_cost_cny: float | None = None
    pricing_version: str | None = None
    billing_authorized: bool = False
    source_kind: str = "asr"
    timing_available: bool = True
    duration_seconds: float | None = None
    approved_revision_id: str | None = None
    low_confidence_count: int = 0
    is_mock: bool = False
    auto_reviewed: bool = False
    uncertain_segment_count: int = 0
    secondary_asr_count: int = 0
    llm_review_count: int = 0
    auto_review_error: str | None = None
    segments: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PipelineResponse(BaseModel):
    run_id: str
    keyword: str
    status: str
    current_stage: str | None = None
    stages: list[dict[str, Any]] = Field(default_factory=list)
    candidate_video_id: str | None = None
    copywriting_task_id: str | None = None
    avatar_task_id: str | None = None
    edit_task_id: str | None = None
    publish_task_ids: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None
    result_media_url: str | None = None


class TaskItem(BaseModel):
    task_id: str
    kind: str
    title: str
    status: str
    progress: int
    error_message: str | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None


class TaskListResponse(BaseModel):
    items: list[TaskItem]
    total: int


class AdminStatusResponse(BaseModel):
    status: str
    repository_type: str
    database_path: str | None = None
    candidate_count: int = 0
    task_count: int = 0
    pipeline_count: int = 0
    migration_version: int | None = None
    migration_details: dict[str, Any] | None = None
    python_version: str | None = None
    platform_info: str | None = None
    version: str = "0.1.0"
