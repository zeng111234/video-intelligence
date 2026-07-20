"""流水线任务 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from project.backend.app.core.deps import get_pipeline_service
from project.backend.app.schemas.requests import PipelineCreateRequest
from project.backend.app.schemas.responses import PipelineResponse

router = APIRouter(prefix="/api/v1/pipelines", tags=["pipelines"])


def _to_response(run) -> PipelineResponse:
    stages = [
        {
            "stage": s.stage.value,
            "status": s.status.value,
            "task_id": s.task_id,
            "error_message": s.error_message,
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
