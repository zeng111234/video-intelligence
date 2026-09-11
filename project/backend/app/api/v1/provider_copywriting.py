"""Authenticated raw copywriting engine gateway for desktop workflows."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from project.backend.app.core.copywriting import get_copywriting_service

router = APIRouter(
    prefix="/api/v1/provider/copywriting",
    tags=["provider-copywriting"],
)


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateRequest(_StrictRequest):
    content_brief: str = Field(min_length=1, max_length=12000)
    platform: str = Field(default="douyin", max_length=40)
    target_audience: str = Field(default="", max_length=1000)
    selling_points: str = Field(default="", max_length=4000)
    call_to_action: str = Field(default="", max_length=1000)
    style_prompt: str = Field(default="", max_length=4000)
    skill_prompt: str = Field(default="", max_length=8000)
    target_length: int = Field(default=300, ge=50, le=800)
    tone: str = Field(default="professional", max_length=80)
    variant_count: int = Field(default=1, ge=1, le=5)


class RewriteRequest(_StrictRequest):
    source_text: str = Field(min_length=1, max_length=12000)
    platform: str = Field(default="douyin", max_length=40)
    target_audience: str = Field(default="", max_length=1000)
    style_prompt: str = Field(default="", max_length=4000)
    skill_prompt: str = Field(default="", max_length=8000)
    target_length: int = Field(default=300, ge=50, le=800)
    tone: str = Field(default="professional", max_length=80)
    rewrite_goal: str = Field(default="", max_length=1000)
    variant_count: int = Field(default=1, ge=1, le=5)


class PublishMetadataRequest(_StrictRequest):
    source_text: str = Field(min_length=1, max_length=12000)
    platforms: list[str] = Field(default_factory=list, max_length=5)


class TranscriptCandidatesRequest(_StrictRequest):
    previous_text: str = Field(default="", max_length=1000)
    next_text: str = Field(default="", max_length=1000)
    candidates: list[str] = Field(min_length=1, max_length=10)


class TranscriptBatchRequest(_StrictRequest):
    segments: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    context_hint: str = Field(default="", max_length=1000)


class SelectScriptRequest(_StrictRequest):
    candidates: list[dict[str, str]] = Field(min_length=1, max_length=20)
    target_audience: str = Field(default="", max_length=1000)
    style_prompt: str = Field(default="", max_length=4000)


class ReviewScriptRequest(_StrictRequest):
    script_text: str = Field(min_length=1, max_length=12000)
    target_audience: str = Field(default="", max_length=1000)
    style_prompt: str = Field(default="", max_length=4000)


class EngineResponse(BaseModel):
    result: list[str] | dict[str, Any]
    token_usage: dict[str, int] = Field(default_factory=dict)
    attention_terms: list[str] = Field(default_factory=list)
    charged_credits: float = 0.0
    is_mock: bool = False


def _execute(request: Request, service, operation: str, payload: dict[str, Any]):
    capability = service.engine.capabilities()
    if not bool(capability.get("enabled")):
        raise HTTPException(status_code=503, detail="AI 文案服务尚未配置。")
    service._ensure_minimum_credits(capability=capability)
    method = getattr(service.engine, operation, None)
    if not callable(method):
        raise HTTPException(status_code=400, detail="当前 AI 文案服务不支持该操作。")
    try:
        result = method(**payload)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="AI 服务结果暂时无法确认，系统不会自动重复提交或重复扣费。",
        ) from exc
    if not isinstance(result, (list, dict)):
        raise HTTPException(status_code=502, detail="AI 服务返回了无效结果。")
    token_usage = service._last_usage()
    charged = service._charge_token_usage(
        capability=capability,
        task_id=request.headers["Idempotency-Key"],
        token_usage=token_usage,
    )
    raw_terms = getattr(service.engine, "last_attention_terms", [])
    attention_terms = (
        [str(item) for item in raw_terms[:50]] if isinstance(raw_terms, list) else []
    )
    return EngineResponse(
        result=result,
        token_usage=token_usage,
        attention_terms=attention_terms,
        charged_credits=charged,
        is_mock=capability.get("mode") == "sandbox",
    )


@router.post("/generate", response_model=EngineResponse)
def generate(body: GenerateRequest, request: Request, service=Depends(get_copywriting_service)):
    return _execute(request, service, "generate", body.model_dump())


@router.post("/rewrite", response_model=EngineResponse)
def rewrite(body: RewriteRequest, request: Request, service=Depends(get_copywriting_service)):
    return _execute(request, service, "rewrite", body.model_dump())


@router.post("/publish-metadata", response_model=EngineResponse)
def publish_metadata(
    body: PublishMetadataRequest,
    request: Request,
    service=Depends(get_copywriting_service),
):
    return _execute(
        request,
        service,
        "generate_publish_metadata",
        body.model_dump(),
    )


@router.post("/review-transcript-candidates", response_model=EngineResponse)
def review_transcript_candidates(
    body: TranscriptCandidatesRequest,
    request: Request,
    service=Depends(get_copywriting_service),
):
    return _execute(
        request,
        service,
        "review_transcript_candidates",
        body.model_dump(),
    )


@router.post("/review-transcript-batch", response_model=EngineResponse)
def review_transcript_batch(
    body: TranscriptBatchRequest,
    request: Request,
    service=Depends(get_copywriting_service),
):
    return _execute(
        request,
        service,
        "review_transcript_batch",
        body.model_dump(),
    )


@router.post("/select-best-spoken-script", response_model=EngineResponse)
def select_best_spoken_script(
    body: SelectScriptRequest,
    request: Request,
    service=Depends(get_copywriting_service),
):
    return _execute(
        request,
        service,
        "select_best_spoken_script",
        body.model_dump(),
    )


@router.post("/review-spoken-script", response_model=EngineResponse)
def review_spoken_script(
    body: ReviewScriptRequest,
    request: Request,
    service=Depends(get_copywriting_service),
):
    return _execute(
        request,
        service,
        "review_spoken_script",
        body.model_dump(),
    )
