"""文案生成 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_copywriting_service

router = APIRouter(prefix="/api/v1/copywriting", tags=["copywriting"])


class CopywritingGenerateRequest(BaseModel):
    content_brief: str = Field(..., min_length=1, description="内容概要")
    platform: str = Field("douyin", description="目标平台")
    target_audience: str = Field("", description="目标受众")
    selling_points: str = Field("", description="核心卖点")
    call_to_action: str = Field("", description="行动号召")
    style_prompt: str = Field("", description="风格提示")
    target_length: int = Field(300, ge=50, le=800, description="目标长度")
    tone: str = Field("professional", description="语调")
    variant_count: int = Field(1, ge=1, le=5, description="变体数量")


class CopywritingRewriteRequest(BaseModel):
    source_text: str = Field(..., min_length=1, description="源文案")
    platform: str = Field("douyin", description="目标平台")
    target_audience: str = Field("", description="目标受众")
    style_prompt: str = Field("", description="风格提示")
    target_length: int = Field(300, ge=50, le=800, description="目标长度")
    tone: str = Field("professional", description="语调")
    variant_count: int = Field(1, ge=1, le=5, description="变体数量")


class CopywritingResponse(BaseModel):
    task_id: str
    status: str
    provider_name: str
    model_name: str
    is_mock: bool
    token_usage: dict[str, int] = {}
    result_text: str | None = None
    result_variants: list[str] = []
    error_message: str | None = None


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


@router.post("/generate", response_model=CopywritingResponse)
def generate(
    body: CopywritingGenerateRequest,
    service=Depends(get_copywriting_service),
):
    """从需求生成文案。"""
    try:
        task = service.generate(
            content_brief=body.content_brief,
            platform=body.platform,
            target_audience=body.target_audience,
            selling_points=body.selling_points,
            call_to_action=body.call_to_action,
            style_prompt=body.style_prompt,
            target_length=body.target_length,
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
            platform=body.platform,
            target_audience=body.target_audience,
            style_prompt=body.style_prompt,
            target_length=body.target_length,
            tone=body.tone,
            variant_count=body.variant_count,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(task)


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
        error_message=task.error_message,
    )
