"""IP 配方与批量生产计划 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_pipeline_service, get_production_service
from src.services.production import IdempotencyConflictError

router = APIRouter(prefix="/api/v1/production", tags=["production"])


class ProfileCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field("", max_length=300)
    target_audience: str = Field("", max_length=200)
    platform: str = Field("douyin", max_length=40)
    script_style: str = Field("", max_length=500)
    avatar_id: str | None = None
    voice_id: str | None = None
    edit_template_id: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)


class BatchCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    profile_id: str = Field(..., min_length=1)
    candidate_ids: list[str] = Field(default_factory=list, max_length=50)
    items: list["BatchSourceItem"] = Field(default_factory=list, max_length=50)


class BatchSourceItem(BaseModel):
    source_type: str = Field(..., pattern="^(candidate|share_link|brief|script)$")
    source_value: str = Field(..., min_length=1, max_length=5000)
    display_title: str = Field("", max_length=120)
    profile_overrides: dict[str, str] = Field(default_factory=dict)


class KeywordAutoRunRequest(BaseModel):
    keyword: str = Field(..., min_length=2, max_length=50)
    candidate_count: int = Field(1, ge=1, le=10)
    profile_id: str = Field(..., min_length=1)
    rights_holder: str = Field(..., min_length=1, max_length=80)
    rights_confirmed: bool = False
    publish_platforms: list[str] = Field(default_factory=lambda: ["douyin"], min_length=1)


class BatchExecutionRequest(BaseModel):
    rights_holder: str = Field("", max_length=80)
    rights_confirmed: bool = False
    publish_platforms: list[str] = Field(default_factory=lambda: ["douyin"], min_length=1)
    concurrency: int = Field(1, ge=1, le=5)
    max_total_cost_cny: float | None = Field(default=None, ge=0)
    paid_actions_confirmed: bool = False


class WorkspaceConfigurationRequest(BaseModel):
    rights_holder: str = Field(..., min_length=1, max_length=80)
    agreement_accepted: bool = False
    default_profile_id: str | None = None
    default_publish_platforms: list[str] = Field(
        default_factory=lambda: ["douyin"], min_length=1, max_length=10
    )
    copywriting_estimated_cost_cny: float | None = Field(default=None, ge=0)
    avatar_estimated_cost_cny: float | None = Field(default=None, ge=0)
    bundled_compute: bool = True


class PublishTargetRequest(BaseModel):
    platform: str = Field(..., min_length=1, max_length=40)
    account_id: str | None = Field(default=None, max_length=80)
    use_manual_fallback: bool = True


class BatchPublishRequest(BaseModel):
    run_ids: list[str] = Field(default_factory=list, max_length=50)
    publish_platforms: list[str] = Field(default_factory=lambda: ["douyin"], min_length=1)
    targets: list[PublishTargetRequest] = Field(default_factory=list, max_length=20)
    confirmation_accepted: bool = False


class BatchReviewItem(BaseModel):
    run_id: str = Field(..., min_length=1)
    approved_text: str = Field("", max_length=10000)
    note: str = Field("", max_length=500)


class BatchReviewRequest(BaseModel):
    stage: str = Field(..., pattern="^(transcript|script|output)$")
    reviewer: str = Field(..., min_length=1, max_length=80)
    items: list[BatchReviewItem] = Field(..., min_length=1, max_length=50)


def _profile_response(profile) -> dict[str, Any]:
    return profile.model_dump(mode="json")


def _batch_response(batch, service) -> dict[str, Any]:
    batch = service.sync_batch(batch.batch_id) or batch
    payload = batch.model_dump(mode="json")
    payload["execution_config"] = {
        key: value
        for key, value in payload.get("execution_config", {}).items()
        if not key.startswith("_")
    }
    items = []
    for item in batch.items:
        run = service.repository.get_pipeline_run(item.run_id)
        items.append(
            {
                "candidate_id": item.candidate_id,
                "run_id": item.run_id,
                "source_type": item.source_type,
                "source_value": item.source_value,
                "display_title": item.display_title,
                "profile_overrides": item.profile_overrides,
                "status": "pending" if item.status.value == "planned" else item.status.value,
                "current_stage": item.current_stage.value if item.current_stage else (run.current_stage.value if run and run.current_stage else None),
                "review_stage": str(run.config.get("review_stage") or "") if run else "",
                "blocked_reasons": item.blocked_reasons,
                "error_message": item.error_message,
                "video_path": item.video_path,
                "publish_mode": item.publish_mode,
            }
        )
    payload["items"] = items
    payload["progress"] = service.batch_progress(batch)
    return payload


@router.get("/profiles")
def list_profiles(service=Depends(get_production_service)):
    return {"items": [_profile_response(item) for item in service.list_profiles()]}


@router.post("/profiles", status_code=201)
def create_profile(body: ProfileCreateRequest, service=Depends(get_production_service)):
    try:
        profile = service.create_profile(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _profile_response(profile)


@router.get("/workspace/configuration")
def get_workspace_configuration(service=Depends(get_production_service)):
    configuration = service.get_workspace_configuration()
    if configuration is None:
        return {"configured": False}
    return {"configured": True, **configuration.model_dump(mode="json")}


@router.put("/workspace/configuration")
def configure_workspace(
    body: WorkspaceConfigurationRequest,
    service=Depends(get_production_service),
):
    try:
        configuration = service.configure_workspace(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"configured": True, **configuration.model_dump(mode="json")}


@router.get("/batches")
def list_batches(service=Depends(get_production_service)):
    return {"items": [_batch_response(item, service) for item in service.list_batches()]}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, service=Depends(get_production_service)):
    batch = service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="生产批次不存在。")
    return _batch_response(batch, service)


@router.get("/batches/{batch_id}/workspace")
def get_batch_workspace(batch_id: str, service=Depends(get_production_service)):
    try:
        return service.workspace(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/batches", status_code=201)
def create_batch(
    body: BatchCreateRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8),
    service=Depends(get_production_service),
    pipeline_service=Depends(get_pipeline_service),
):
    request_hash = service.request_hash(body.model_dump(mode="json"))
    try:
        batch = service.create_batch(
            name=body.name,
            profile_id=body.profile_id,
            candidate_ids=body.candidate_ids,
            source_items=[item.model_dump() for item in body.items],
            pipeline_service=pipeline_service,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _batch_response(batch, service)


@router.post("/batches/{batch_id}/reviews")
def review_batch_items(
    batch_id: str,
    body: BatchReviewRequest,
    service=Depends(get_production_service),
    pipeline_service=Depends(get_pipeline_service),
):
    batch = service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="生产批次不存在。")
    owned = {item.run_id for item in batch.items}
    if body.stage in {"transcript", "script"} and any(
        not item.approved_text.strip() for item in body.items
    ):
        raise HTTPException(
            status_code=400,
            detail=f"{body.stage} 审核必须提交非空的最终文本。",
        )
    results: list[dict[str, Any]] = []
    for item in body.items:
        if item.run_id not in owned:
            results.append({"run_id": item.run_id, "ok": False, "error": "该任务不属于当前批次。"})
            continue
        try:
            if body.stage == "transcript":
                reviewed = pipeline_service.review_transcript(
                    run_id=item.run_id,
                    reviewer=body.reviewer,
                    note=item.note,
                    approved_text=item.approved_text,
                )
                entered_script_review = (
                    reviewed.status.value == "paused"
                    and reviewed.current_stage is not None
                    and reviewed.current_stage.value == "human_review"
                    and reviewed.config.get("review_stage") == "script"
                    and bool(reviewed.copywriting_task_id)
                )
                if not entered_script_review:
                    results.append(
                        {
                            "run_id": item.run_id,
                            "ok": False,
                            "error": reviewed.error_message
                            or "改写稿未成功进入文案确认阶段。",
                        }
                    )
                    continue
            elif body.stage == "script":
                pipeline_service.review_candidate_script(
                    run_id=item.run_id,
                    approved=True,
                    reviewer=body.reviewer,
                    note=item.note,
                    approved_text=item.approved_text,
                )
            else:
                pipeline_service.confirm_output_review(
                    run_id=item.run_id,
                    reviewer=body.reviewer,
                    note=item.note,
                )
            results.append({"run_id": item.run_id, "ok": True})
        except ValueError as exc:
            results.append({"run_id": item.run_id, "ok": False, "error": str(exc)})
    refreshed = service.sync_batch(batch_id) or batch
    return {"batch": _batch_response(refreshed, service), "results": results}


@router.post("/batches/{batch_id}/preflight")
def preflight_batch(batch_id: str, body: BatchExecutionRequest, service=Depends(get_production_service)):
    try:
        return service.preflight_batch(
            batch_id,
            **service.resolve_workspace_execution_options(body.model_dump()),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batches/{batch_id}/start")
def start_batch(
    batch_id: str,
    body: BatchExecutionRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8),
    service=Depends(get_production_service),
    pipeline_service=Depends(get_pipeline_service),
):
    payload = service.resolve_workspace_execution_options(body.model_dump(mode="json"))
    try:
        batch = service.start_batch(
            batch_id,
            options=payload,
            pipeline_service=pipeline_service,
            idempotency_key=idempotency_key,
            request_hash=service.request_hash(payload),
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = _batch_response(batch, service)
    response["idempotency_key"] = idempotency_key
    return response


@router.post("/batches/{batch_id}/pause")
def pause_batch(batch_id: str, service=Depends(get_production_service)):
    try:
        return _batch_response(service.pause_batch(batch_id), service)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/batches/{batch_id}/resume")
def resume_batch(batch_id: str, service=Depends(get_production_service)):
    try:
        return _batch_response(service.resume_batch(batch_id), service)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batches/{batch_id}/retry-failed")
def retry_failed_batch(batch_id: str, service=Depends(get_production_service), pipeline_service=Depends(get_pipeline_service)):
    try:
        return _batch_response(service.retry_failed(batch_id, pipeline_service=pipeline_service), service)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batches/{batch_id}/publish/preflight")
def preflight_batch_publish(batch_id: str, body: BatchPublishRequest, service=Depends(get_production_service), pipeline_service=Depends(get_pipeline_service)):
    try:
        return service.publish_preflight(
            batch_id,
            run_ids=body.run_ids,
            targets=[item.model_dump() for item in body.targets],
            publish_platforms=body.publish_platforms,
            pipeline_service=pipeline_service,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batches/{batch_id}/publish")
def confirm_batch_publish(
    batch_id: str,
    body: BatchPublishRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8),
    service=Depends(get_production_service),
    pipeline_service=Depends(get_pipeline_service),
):
    if not body.confirmation_accepted:
        raise HTTPException(status_code=400, detail="请先完成发布预检并确认发布。")
    payload = body.model_dump(mode="json")
    try:
        batch = service.confirm_publish(
            batch_id,
            run_ids=body.run_ids,
            targets=[item.model_dump() for item in body.targets],
            publish_platforms=body.publish_platforms,
            pipeline_service=pipeline_service,
            idempotency_key=idempotency_key,
            request_hash=service.request_hash(payload),
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = _batch_response(batch, service)
    response["idempotency_key"] = idempotency_key
    return response


@router.post("/keyword-runs/preflight")
def keyword_run_preflight(body: KeywordAutoRunRequest, service=Depends(get_production_service)):
    profile = next((item for item in service.list_profiles() if item.profile_id == body.profile_id), None)
    missing = []
    if profile is None:
        missing.append("IP 配方不存在")
    else:
        if not profile.avatar_id:
            missing.append("IP 配方未绑定数字人形象")
        if not profile.voice_id:
            missing.append("IP 配方未绑定音色")
        if not profile.edit_template_id:
            missing.append("IP 配方未绑定剪辑模板")
    if not body.rights_confirmed:
        missing.append("未确认媒体处理授权")
    return {
        "ready": not missing,
        "missing": missing,
        "candidate_count": body.candidate_count,
        "message": "预检只校验本地配置；实际媒体解析费用以运行时供应商返回和本月预算上限为准。",
    }


@router.post("/keyword-runs", status_code=202)
def start_keyword_auto_run(
    body: KeywordAutoRunRequest,
    service=Depends(get_production_service),
    pipeline_service=Depends(get_pipeline_service),
):
    if not body.rights_confirmed:
        raise HTTPException(status_code=400, detail="必须确认拥有媒体处理权。")
    profile = next((item for item in service.list_profiles() if item.profile_id == body.profile_id), None)
    if profile is None:
        raise HTTPException(status_code=400, detail="IP 配方不存在。")
    if not all([profile.avatar_id, profile.voice_id, profile.edit_template_id]):
        raise HTTPException(status_code=400, detail="IP 配方必须绑定形象、音色和剪辑模板。")
    run = pipeline_service.start_keyword_auto_run(
        keyword=body.keyword,
        candidate_count=body.candidate_count,
        profile=profile.model_dump(mode="json"),
        rights_holder=body.rights_holder,
        publish_platforms=body.publish_platforms,
    )
    return {"run_id": run.run_id, "status": run.status.value, "message": "已入队，后台将从关键词检索开始执行。"}
