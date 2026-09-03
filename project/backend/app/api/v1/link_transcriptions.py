"""Single-link local transcription for authorized public platform shares."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.api.v1.transcriptions import _to_response
from project.backend.app.core.deps import (
    get_douyin_link_transcription_service,
    get_repository,
)
from project.backend.app.schemas.responses import TranscriptionResponse
from src.adapters.douyin_parser import DouyinParserError
from src.models import Platform

router = APIRouter(prefix="/api/v1/crawler/link-transcriptions", tags=["crawler"])


def _candidate_xiaohongshu_url(candidate) -> str | None:
    """Build a canonical note URL for legacy candidates without source_url."""
    if candidate.platform != Platform.XIAOHONGSHU:
        return None
    raw_id = str(candidate.platform_item_id or candidate.video_id or "").strip()
    if raw_id.casefold().startswith("xiaohongshu-"):
        raw_id = raw_id[len("xiaohongshu-"):]
    if not raw_id or not all(char.isalnum() or char in "-_" for char in raw_id):
        return None
    item_id = quote(raw_id, safe="")
    return f"https://www.xiaohongshu.com/explore/{item_id}" if item_id else None


def _candidate_xiaohongshu_search_keyword(candidate) -> str | None:
    """Return a precise bounded keyword for recovering a legacy bare note URL."""
    if candidate.platform != Platform.XIAOHONGSHU:
        return None
    values: list[str] = []
    title = " ".join(str(getattr(candidate, "title", "") or "").split())
    if title:
        # 小红书卡片标题尾部常拼接作者、发布时间和互动数；优先取分隔线
        # 前的内容，避免用分类词搜索时因平台排序变化找不到旧作品。
        title_keyword = title.split("｜", 1)[0].split("|", 1)[0].strip()
        # 去掉标题开头的装饰 emoji/标点，保留真正的可检索词。
        while title_keyword and not title_keyword[0].isalnum():
            title_keyword = title_keyword[1:].lstrip()
        if len(title_keyword) > 80:
            title_keyword = title_keyword[:80].rstrip()
        if len(title_keyword) >= 2:
            values.append(title_keyword)
    values.extend(
        str(value).strip()
        for value in getattr(candidate, "matched_by", [])
        if str(value).strip()
    )
    category = str(getattr(candidate, "category", "") or "").strip()
    if category.startswith("关键词/"):
        values.append(category.split("/", 1)[1].strip())
    for value in values:
        if 2 <= len(value) <= 80:
            return value
    return None


def _has_usable_xiaohongshu_share_url(source_url: object) -> bool:
    """Return whether a saved Xiaohongshu note URL can enter the logged browser.

    The connected, user-authorized browser confirms whether the note is reachable.
    A direct note URL may still require an active session or manual verification;
    the resolver reports that failure instead of silently switching providers.
    """
    if not isinstance(source_url, str):
        return False
    try:
        parsed = urlparse(source_url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").casefold()
    if host == "xhslink.com" or host.endswith(".xhslink.com"):
        return True
    is_xiaohongshu = (
        host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com")
    )
    is_note_path = "/explore/" in parsed.path or "/discovery/item/" in parsed.path
    return is_xiaohongshu and is_note_path


class LinkPreviewRequest(BaseModel):
    share_text: str = Field(..., min_length=8, max_length=2000)


class LinkCreateRequest(LinkPreviewRequest):
    rights_confirmed: bool = False
    rights_holder: str = Field(..., min_length=1, max_length=80)
    model_name: str = "large-v3-turbo"


class CandidateLinkCreateRequest(BaseModel):
    rights_confirmed: bool = False
    rights_holder: str = Field(..., min_length=1, max_length=80)
    model_name: str = "large-v3-turbo"


class LinkFallbackRequest(LinkCreateRequest):
    work_id: str = Field(..., pattern=r"^\d+$")
    confirmed: bool = False


class LinkPreviewResponse(BaseModel):
    platform: str
    platform_label: str
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
        "supported_platforms": ["douyin", "xiaohongshu", "kuaishou", "bilibili"],
        "supported_platform_labels": ["抖音", "小红书", "快手", "B站"],
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
        platform = getattr(exc, "platform", Platform.DOUYIN)
        fallback_price = service._fallback_price(platform)
        if exc.work_id and fallback_price is not None:
            return LinkCreateResponse(
                status="fallback_required",
                message=exc.user_message,
                work_id=exc.work_id,
                oneapi_estimated_cost_cny=fallback_price,
            )
        raise HTTPException(status_code=400, detail=exc.user_message) from exc
    return LinkCreateResponse(
        status="succeeded",
        message="已创建转写任务，请先校对并确认成稿。",
        transcription=_to_response(task, service.transcription_service),
    )


@router.post("/candidates/{candidate_id}", response_model=LinkCreateResponse)
def create_from_candidate(
    candidate_id: str,
    body: CandidateLinkCreateRequest,
    service=Depends(get_douyin_link_transcription_service),
    repo=Depends(get_repository),
):
    """Create a free local transcription from the candidate's saved public link."""
    if not body.rights_confirmed:
        raise HTTPException(status_code=400, detail="请先确认拥有该内容的处理权。")
    candidate = repo.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="候选不存在。")
    supported_platforms = {
        Platform.DOUYIN,
        Platform.XIAOHONGSHU,
        Platform.KUAISHOU,
        Platform.BILIBILI,
    }
    if candidate.platform not in supported_platforms:
        raise HTTPException(
            status_code=400,
            detail="当前平台候选暂不支持链接转写，请上传已获授权的视频文件。",
        )
    source_url = str(candidate.source_url) if candidate.source_url else None
    source_url = source_url or _candidate_xiaohongshu_url(candidate)
    if not source_url:
        raise HTTPException(status_code=400, detail="该候选没有可用的原视频链接。")
    try:
        task = service.transcribe_experimental(
            share_text=source_url,
            rights_holder=body.rights_holder,
            rights_confirmed=body.rights_confirmed,
            model_name=body.model_name,
            candidate_id=candidate.video_id,
            search_keyword=_candidate_xiaohongshu_search_keyword(candidate),
        )
    except DouyinParserError as exc:
        platform = getattr(exc, "platform", candidate.platform)
        fallback_price = service._fallback_price(platform)
        if exc.work_id and fallback_price is not None:
            return LinkCreateResponse(
                status="fallback_required",
                message=exc.user_message,
                work_id=exc.work_id,
                oneapi_estimated_cost_cny=fallback_price,
            )
        raise HTTPException(status_code=400, detail=exc.user_message) from exc
    return LinkCreateResponse(
        status="succeeded",
        message="已创建免费本机转写任务，请先校对并确认成稿。",
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
