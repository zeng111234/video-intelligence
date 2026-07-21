"""API 响应 Schema —— Pydantic v2 显式模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class CandidateItem(BaseModel):
    video_id: str
    title: str
    platform: str
    author_name: str = ""
    category: str = ""
    heat_score: float = 0.0
    heat_level: str = ""
    source_url: str | None = None
    published_at: datetime | None = None


class CandidateListResponse(BaseModel):
    items: list[CandidateItem]
    total: int


class TranscriptionResponse(BaseModel):
    task_id: str
    title: str
    status: str
    progress: int
    stage: str = ""
    media_name: str = ""
    model_name: str | None = None
    duration_seconds: float | None = None
    approved_revision_id: str | None = None
    low_confidence_count: int = 0
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
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskItem(BaseModel):
    task_id: str
    kind: str
    title: str
    status: str
    progress: int
    created_at: datetime | None = None


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
