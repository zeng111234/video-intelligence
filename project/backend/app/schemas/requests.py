"""API 请求 Schema —— Pydantic v2 显式模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CandidateSearchRequest(BaseModel):
    keyword: str = Field("", max_length=200, description="搜索关键词")
    limit: int = Field(10, ge=1, le=100, description="返回数量")
    platforms: list[str] = Field(default_factory=list, description="平台筛选")
    category: str | None = Field(None, description="分类筛选")


class TranscriptionCreateRequest(BaseModel):
    media_name: str = Field(..., min_length=1, description="媒体文件名")
    media_type: str = Field("video/mp4", description="媒体类型")
    rights_confirmed: bool = Field(False, description="是否确认权利")
    rights_holder: str = Field("", description="权利主体")
    model_name: str = Field("base", description="识别模型")
    hotwords: str | None = Field(None, description="热词")


class TranscriptSegmentRequest(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(..., min_length=1)
    confidence: float = Field(ge=0, le=1)
    needs_review: bool = False
    reviewed: bool = False


class TranscriptionRevisionRequest(BaseModel):
    segments: list[TranscriptSegmentRequest] = Field(..., min_length=1)
    reviewer: str = Field(..., min_length=1, max_length=80)
    approve: bool = False


class PipelineCreateRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=200, description="关键词")
    config: dict = Field(default_factory=dict, description="流水线配置")
