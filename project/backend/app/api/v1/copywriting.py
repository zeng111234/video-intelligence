"""文案生成 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_copywriting_service

router = APIRouter(prefix="/api/v1/copywriting", tags=["copywriting"])


class CopywritingGenerateRequest(BaseModel):
    content_brief: str = Field(..., min_length=1, description="内容概要")
    # 兼容旧客户端；新页面不再以平台作为文案生成条件。
    platform: str | None = Field(None, description="已废弃的目标平台")
    target_audience: str = Field("", description="目标受众")
    selling_points: str = Field("", description="核心卖点")
    call_to_action: str = Field("", description="行动号召")
    style_prompt: str = Field("", description="风格提示")
    target_length: int | None = Field(None, description="已废弃的目标长度")
    tone: str = Field("professional", description="语调")
    variant_count: int = Field(1, ge=1, le=5, description="变体数量")


class CopywritingRewriteRequest(BaseModel):
    source_text: str = Field(..., min_length=1, description="源文案")
    platform: str | None = Field(None, description="已废弃的目标平台")
    target_audience: str = Field("", description="目标受众")
    style_prompt: str = Field("", description="风格提示")
    target_length: int | None = Field(None, description="已废弃的目标长度")
    tone: str = Field("professional", description="语调")
    variant_count: int = Field(1, ge=1, le=5, description="变体数量")


class CopywritingResponse(BaseModel):
    task_id: str
    status: str
    provider_name: str
    model_name: str
    is_mock: bool
    token_usage: dict[str, int] = Field(default_factory=dict)
    result_text: str | None = None
    result_variants: list[str] = Field(default_factory=list)
    compliance_status: str = "not_checked"
    compliance_notes: list[str] = Field(default_factory=list)
    compliance_rewritten: bool = False
    compliance_retry_used: bool = False
    error_message: str | None = None


class CopywritingSummaryResponse(CopywritingResponse):
    title: str
    creation_mode: str
    platform: str
    target_audience: str
    target_length: int
    tone: str
    created_at: str | None = None
    updated_at: str | None = None


class CopywritingDetailResponse(CopywritingSummaryResponse):
    source_text: str = ""
    content_brief: str = ""
    selling_points: str = ""
    call_to_action: str = ""
    style_prompt: str = ""


class CopywritingCapabilitiesResponse(BaseModel):
    provider_name: str
    display_name: str
    mode: str
    enabled: bool
    model_name: str
    max_input_chars: int
    max_output_chars: int
    supports_variants: bool
    max_variants: int
    supported_platforms: list[str]
    missing_configuration: list[str]


class CopywritingHistoryDeleteResponse(BaseModel):
    """独立 AI 文案历史的批量删除结果。"""

    deleted_count: int


class PublishMetadataRequest(BaseModel):
    source_text: str = Field(..., min_length=1, max_length=12000)
    platforms: list[str] = Field(default_factory=list, max_length=5)
    source_task_id: str | None = None


class PublishMetadataResponse(BaseModel):
    task_id: str
    provider_name: str
    model_name: str
    is_mock: bool
    title: str
    description: str
    tags: list[str]


@router.get("/capabilities", response_model=CopywritingCapabilitiesResponse)
def capabilities(service=Depends(get_copywriting_service)):
    """返回文案模型能力，不包含任何密钥。"""
    cap = service.capabilities()
    return CopywritingCapabilitiesResponse(
        provider_name=str(cap.get("provider_name", "unknown")),
        display_name=str(cap.get("display_name", "AI 文案生成")),
        mode=str(cap.get("mode", "disabled")),
        enabled=bool(cap.get("enabled", False)),
        model_name=str(cap.get("model", "")),
        max_input_chars=int(cap.get("max_input_chars", 12000)),
        max_output_chars=int(cap.get("max_output_chars", 4000)),
        supports_variants=bool(cap.get("supports_variants", True)),
        max_variants=int(cap.get("max_variants", 5)),
        supported_platforms=["douyin", "xiaohongshu", "wechat_channels"],
        missing_configuration=[
            str(item) for item in cap.get("missing_configuration", [])  # type: ignore[arg-type]
        ],
    )


@router.get("", response_model=list[CopywritingSummaryResponse])
def list_copywriting(
    limit: int = 50,
    service=Depends(get_copywriting_service),
):
    """返回独立 AI 文案任务历史，排除转写生成的口播稿。"""
    safe_limit = max(1, min(limit, 100))
    tasks = [
        task
        for task in service.list_tasks()
        if getattr(task, "source_task_id", None) is None
    ][:safe_limit]
    return [_to_summary(task) for task in tasks]


@router.delete("/history", response_model=CopywritingHistoryDeleteResponse)
def clear_copywriting_history(service=Depends(get_copywriting_service)):
    """删除历史抽屉中的全部独立文案任务，保留转写生成的口播稿。"""
    task_ids = [task.task_id for task in service.list_tasks() if task.source_task_id is None]
    for task_id in task_ids:
        service.repository.delete_task(task_id)
    return CopywritingHistoryDeleteResponse(deleted_count=len(task_ids))


@router.get("/{task_id}", response_model=CopywritingDetailResponse)
def get_copywriting(
    task_id: str,
    service=Depends(get_copywriting_service),
):
    """返回可恢复输入和结果的文案任务详情。"""
    task = service.get_task(task_id)
    if task is None or task.source_task_id is not None:
        raise HTTPException(status_code=404, detail="文案任务不存在。")
    return _to_detail(task)


@router.post("/generate", response_model=CopywritingResponse)
def generate(
    body: CopywritingGenerateRequest,
    service=Depends(get_copywriting_service),
):
    """从需求生成文案。"""
    try:
        task = service.generate(
            content_brief=body.content_brief,
            # 旧字段仅为兼容旧客户端保留；新文案使用通用短视频口播规则。
            platform="douyin",
            target_audience=body.target_audience,
            selling_points=body.selling_points,
            call_to_action=body.call_to_action,
            style_prompt=body.style_prompt,
            target_length=300,
            tone=body.tone,
            variant_count=body.variant_count,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(task)


@router.post("/rewrite", response_model=CopywritingResponse)
def rewrite(
    body: CopywritingRewriteRequest,
    service=Depends(get_copywriting_service),
):
    """改写已有文案。"""
    try:
        task = service.rewrite(
            source_text=body.source_text,
            platform="douyin",
            target_audience=body.target_audience,
            style_prompt=body.style_prompt,
            target_length=300,
            tone=body.tone,
            variant_count=body.variant_count,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(task)


@router.post("/publish-metadata", response_model=PublishMetadataResponse)
def generate_publish_metadata(
    body: PublishMetadataRequest,
    service=Depends(get_copywriting_service),
):
    """基于已生成或已确认的文案生成发布标题、描述和话题。"""
    try:
        task, metadata = service.generate_publish_metadata(
            source_text=body.source_text,
            platforms=body.platforms,
            source_task_id=body.source_task_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if metadata is None:
        raise HTTPException(status_code=400, detail=task.error_message or "发布信息生成失败。")
    return PublishMetadataResponse(
        task_id=task.task_id,
        provider_name=task.provider_name,
        model_name=task.model_name,
        is_mock=task.is_mock,
        title=str(metadata["title"]),
        description=str(metadata["description"]),
        tags=[str(item) for item in metadata["tags"]],
    )


def _to_response(task) -> CopywritingResponse:
    return CopywritingResponse(
        task_id=task.task_id,
        status=task.status.value,
        provider_name=task.provider_name,
        model_name=task.model_name,
        is_mock=task.is_mock,
        token_usage=task.token_usage,
        result_text=task.result_text,
        result_variants=task.result_variants,
        compliance_status=task.compliance_status,
        compliance_notes=task.compliance_notes,
        compliance_rewritten=task.compliance_rewritten,
        compliance_retry_used=task.compliance_retry_used,
        error_message=task.error_message,
    )


def _to_summary(task) -> CopywritingSummaryResponse:
    return CopywritingSummaryResponse(
        **_to_response(task).model_dump(),
        title=task.title,
        creation_mode=task.creation_mode,
        platform=task.platform.value,
        target_audience=task.target_audience,
        target_length=task.target_length,
        tone=task.tone,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


def _to_detail(task) -> CopywritingDetailResponse:
    return CopywritingDetailResponse(
        **_to_summary(task).model_dump(),
        source_text=task.source_text,
        content_brief=task.content_brief,
        selling_points=task.selling_points,
        call_to_action=task.call_to_action,
        style_prompt=task.style_prompt,
    )
