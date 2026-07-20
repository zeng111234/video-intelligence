"""文案改写 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_copywriting_service

router = APIRouter(prefix="/api/v1/copywriting", tags=["copywriting"])


class CopywritingRequest(BaseModel):
    source_text: str = Field(..., min_length=1, description="源文案")
    style_prompt: str = Field("", description="风格提示")
    target_length: int = Field(300, ge=50, le=5000, description="目标长度")
    tone: str = Field("professional", description="语调")
    variant_count: int = Field(1, ge=1, le=5, description="变体数量")


class CopywritingResponse(BaseModel):
    task_id: str
    status: str
    result_text: str | None = None
    result_variants: list[str] = []
    error_message: str | None = None


@router.post("/rewrite", response_model=CopywritingResponse)
def rewrite(
    body: CopywritingRequest,
    service=Depends(get_copywriting_service),
):
    """文案改写。"""
    try:
        task = service.rewrite(
            source_text=body.source_text,
            style_prompt=body.style_prompt,
            target_length=body.target_length,
            tone=body.tone,
            variant_count=body.variant_count,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return CopywritingResponse(
        task_id=task.task_id,
        status=task.status.value,
        result_text=task.result_text,
        result_variants=task.result_variants,
        error_message=task.error_message,
    )
