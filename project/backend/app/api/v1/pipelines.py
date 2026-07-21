"""流水线任务 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_pipeline_service
from project.backend.app.schemas.requests import PipelineCreateRequest
from project.backend.app.schemas.responses import PipelineResponse

router = APIRouter(prefix="/api/v1/pipelines", tags=["pipelines"])


class PipelineFromCandidateRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1)
    rights_confirmed: bool = Field(False, description="确认拥有媒体处理权")
    rights_holder: str = Field(..., min_length=1, max_length=80)
    model_name: str = Field("large-v3-turbo")
    hotwords: str = Field("", max_length=500)
    target_length: int = Field(300, ge=50, le=800)
    tone: str = Field("casual", max_length=40)
    target_audience: str = Field("", max_length=200)
    style_prompt: str = Field("", max_length=500)
    variant_count: int = Field(2, ge=1, le=3)


def _to_response(run) -> PipelineResponse:
    stages = [
        {
            "stage": s.stage.value,
            "status": s.status.value,
            "task_id": s.task_id,
            "error_message": s.error_message,
            "outputs": s.outputs,
        }
        for s in run.stages
    ]
    return PipelineResponse(
        run_id=run.run_id,
        keyword=run.keyword,
        status=run.status.value,
        current_stage=run.current_stage.value if run.current_stage else None,
        stages=stages,
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


@router.post("", response_model=PipelineResponse)
def create_pipeline(
    body: PipelineCreateRequest,
    service=Depends(get_pipeline_service),
):
    """创建流水线任务。"""
    try:
        run = service.create_run(keyword=body.keyword, config=body.config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_response(run)


@router.post("/from-candidate", response_model=PipelineResponse)
def create_pipeline_from_candidate(
    body: PipelineFromCandidateRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8),
    service=Depends(get_pipeline_service),
):
    """从爬虫候选创建真实生产流水线：补媒体、转写、改写并等待人工审核。"""
    try:
        run = service.execute_candidate_script_pipeline(
            candidate_id=body.candidate_id,
            rights_confirmed=body.rights_confirmed,
            rights_holder=body.rights_holder,
            idempotency_key=idempotency_key,
            model_name=body.model_name,
            hotwords=body.hotwords or None,
            target_length=body.target_length,
            tone=body.tone,
            target_audience=body.target_audience,
            style_prompt=body.style_prompt,
            variant_count=body.variant_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(run)


@router.get("/{run_id}", response_model=PipelineResponse)
def get_pipeline(
    run_id: str,
    service=Depends(get_pipeline_service),
):
    """获取流水线任务详情。"""
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="流水线不存在。")
    return _to_response(run)


@router.get("", response_model=list[PipelineResponse])
def list_pipelines(
    limit: int = 20,
    service=Depends(get_pipeline_service),
):
    """列出 SQLite 中的流水线记录。"""
    safe_limit = max(1, min(limit, 100))
    return [_to_response(run) for run in service.list_runs(limit=safe_limit)]
