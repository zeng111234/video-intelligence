"""API 请求 Schema —— Pydantic v2 显式模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CandidateSearchRequest(BaseModel):
    keyword: str = Field("", max_length=200, description="搜索关键词")
    limit: int = Field(10, ge=1, le=100, description="返回数量")


class TranscriptionCreateRequest(BaseModel):
    media_name: str = Field(..., min_length=1, description="媒体文件名")
    media_type: str = Field("video/mp4", description="媒体类型")
    rights_confirmed: bool = Field(False, description="是否确认权利")
    rights_holder: str = Field("", description="权利主体")
    model_name: str = Field("base", description="识别模型")
    hotwords: str | None = Field(None, description="热词")


class PipelineCreateRequest(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=200, description="关键词")
    config: dict = Field(default_factory=dict, description="流水线配置")
