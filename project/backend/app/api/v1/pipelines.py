"""流水线任务 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_pipeline_service, get_repository
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


class PipelineReviewRequest(BaseModel):
    approved: bool
    reviewer: str = Field(..., min_length=1, max_length=80)
    note: str = Field("", max_length=500)
    approved_text: str = Field("", max_length=2000)


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
        candidate_video_id=run.candidate_video_id,
        copywriting_task_id=run.copywriting_task_id,
        avatar_task_id=run.avatar_task_id,
        edit_task_id=run.edit_task_id,
        publish_task_ids=run.publish_task_ids,
        config=run.config,
        events=[event.model_dump(mode="json") for event in getattr(run, "events", [])],
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
        finished_at=run.finished_at,
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


@router.post("/{run_id}/review", response_model=PipelineResponse)
def review_pipeline_script(
    run_id: str,
    body: PipelineReviewRequest,
    service=Depends(get_pipeline_service),
):
    """保存人工文案审核结果；批准后仅进入数字人配置等待，不自动生成或发布。"""
    try:
        run = service.review_candidate_script(
            run_id=run_id,
            approved=body.approved,
            reviewer=body.reviewer,
            note=body.note,
            approved_text=body.approved_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(run)


@router.post("/{run_id}/retry", response_model=PipelineResponse)
def retry_pipeline(
    run_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8),
    service=Depends(get_pipeline_service),
):
    """使用原运行 ID 重试候选的媒体、转写和文案链路。"""
    try:
        run = service.retry_candidate_script_pipeline(
            run_id=run_id,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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


@router.delete("/{run_id}")
def delete_pipeline(
    run_id: str,
    repo=Depends(get_repository),
):
    if not repo.delete_pipeline_run(run_id):
        raise HTTPException(status_code=404, detail="流水线不存在或已删除。")
    return {"run_id": run_id, "deleted": True}


@router.get("", response_model=list[PipelineResponse])
def list_pipelines(
    limit: int = 20,
    candidate_id: str | None = None,
    service=Depends(get_pipeline_service),
):
    """列出流水线记录；可按候选筛选，避免工作台混入无关任务。"""
    safe_limit = max(1, min(limit, 100))
    runs = service.list_runs(limit=safe_limit)
    if candidate_id:
        runs = [
            run
            for run in runs
            if run.candidate_video_id == candidate_id
            or str(run.config.get("candidate_id", "")) == candidate_id
        ]
    return [_to_response(run) for run in runs]
