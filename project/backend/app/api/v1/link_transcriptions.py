"""Single-link, locally hosted experimental Douyin transcription entrypoint."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.api.v1.transcriptions import _to_response
from project.backend.app.core.deps import get_douyin_link_transcription_service
from project.backend.app.schemas.responses import TranscriptionResponse
from src.adapters.douyin_parser import DouyinParserError

router = APIRouter(prefix="/api/v1/crawler/link-transcriptions", tags=["crawler"])


class LinkPreviewRequest(BaseModel):
    share_text: str = Field(..., min_length=8, max_length=2000)


class LinkCreateRequest(LinkPreviewRequest):
    rights_confirmed: bool = False
    rights_holder: str = Field(..., min_length=1, max_length=80)
    model_name: str = "large-v3-turbo"


class LinkFallbackRequest(LinkCreateRequest):
    work_id: str = Field(..., pattern=r"^\d+$")
    confirmed: bool = False


class LinkPreviewResponse(BaseModel):
    share_url: str
    work_id: str | None
    parser_enabled: bool
    parser_message: str | None
    oneapi_fallback_available: bool
    oneapi_estimated_cost_cny: float | None
    is_experimental: bool = True


class LinkCreateResponse(BaseModel):
    status: Literal["succeeded", "fallback_required"]
    message: str
    work_id: str | None = None
    oneapi_estimated_cost_cny: float | None = None
    transcription: TranscriptionResponse | None = None


@router.get("/capabilities")
def capabilities(service=Depends(get_douyin_link_transcription_service)):
    enabled, message = service.parser.capabilities()
    return {
        "experimental": True,
        "parser_enabled": enabled,
        "parser_message": message,
        "oneapi_estimated_cost_cny": service._fallback_price(),
    }


@router.post("/preview", response_model=LinkPreviewResponse)
def preview(body: LinkPreviewRequest, service=Depends(get_douyin_link_transcription_service)):
    try:
        item = service.preview(body.share_text)
    except DouyinParserError as exc:
        raise HTTPException(status_code=400, detail=exc.user_message) from exc
    return LinkPreviewResponse(**item.__dict__)


@router.post("", response_model=LinkCreateResponse)
def create(body: LinkCreateRequest, service=Depends(get_douyin_link_transcription_service)):
    if not body.rights_confirmed:
        raise HTTPException(status_code=400, detail="请先确认拥有该内容的处理权。")
    try:
        task = service.transcribe_experimental(**body.model_dump())
    except DouyinParserError as exc:
        if exc.work_id:
            return LinkCreateResponse(
                status="fallback_required",
                message=exc.user_message,
                work_id=exc.work_id,
                oneapi_estimated_cost_cny=service._fallback_price(),
            )
        raise HTTPException(status_code=400, detail=exc.user_message) from exc
    return LinkCreateResponse(
        status="succeeded",
        message="已创建转写任务，请先校对并确认成稿。",
        transcription=_to_response(task, service.transcription_service),
    )


@router.post("/fallback", response_model=LinkCreateResponse)
def fallback(
    body: LinkFallbackRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8),
    service=Depends(get_douyin_link_transcription_service),
):
    if not body.rights_confirmed:
        raise HTTPException(status_code=400, detail="请先确认拥有该内容的处理权。")
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="OneAPI 回退需要在页面明确确认后才会执行。")
    try:
        task = service.transcribe_oneapi_fallback(
            share_text=body.share_text,
            work_id=body.work_id,
            rights_holder=body.rights_holder,
            rights_confirmed=body.rights_confirmed,
            model_name=body.model_name,
            idempotency_key=idempotency_key,
        )
    except DouyinParserError as exc:
        raise HTTPException(status_code=400, detail=exc.user_message) from exc
    return LinkCreateResponse(
        status="succeeded",
        message="已通过 OneAPI 回退创建转写任务，请先校对并确认成稿。",
        transcription=_to_response(task, service.transcription_service),
    )
