"""流水线任务 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from project.backend.app.core.deps import (
    get_avatar_service,
    get_douyin_link_transcription_service,
    get_media_resolution_service,
    get_pipeline_service,
    get_production_service,
    get_publish_service,
    get_repository,
    get_template_service,
)
from project.backend.app.schemas.requests import PipelineCreateRequest
from project.backend.app.schemas.responses import PipelineResponse
from src.adapters.douyin_parser import DouyinParserError
from src.models import CopywritingTask, PipelineStage, Platform, TranscriptionTask

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


class GuidedPipelineRequest(BaseModel):
    source_type: Literal["share_link", "candidate"]
    candidate_id: str | None = Field(default=None, min_length=1)
    share_text: str = Field(default="", max_length=2000)
    profile_id: str = Field(..., min_length=1)
    rights_confirmed: bool = False
    rights_holder: str = Field(..., min_length=1, max_length=80)
    publish_enabled: bool = False
    publish_platforms: list[str] = Field(default_factory=list, max_length=4)
    paid_fallback_confirmed: bool = False


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
        result_media_url=(f"/api/v1/pipelines/{run.run_id}/media" if run.edit_task_id else None),
    )


def _guided_preflight(
    body: GuidedPipelineRequest,
    *,
    production_service,
    repository,
    media_resolution_service,
    link_transcription_service,
    avatar_service,
    template_service,
    publish_service,
) -> dict:
    """校验单条傻瓜式生产所需的真实能力，不创建外部任务。"""
    missing: list[str] = []
    warnings: list[str] = []
    source: dict = {"type": body.source_type}
    profile = production_service.get_profile(body.profile_id)
    if profile is None:
        missing.append("IP 配方不存在")
    else:
        if not profile.avatar_id:
            missing.append("IP 配方未绑定数字人形象")
        if not profile.voice_id:
            missing.append("IP 配方未绑定音色")
        if not profile.edit_template_id:
            missing.append("IP 配方未绑定剪辑模板")
        if profile.avatar_id and profile.voice_id:
            assets = {item.asset_id: item for item in avatar_service.list_assets()}
            for asset_id, label in ((profile.avatar_id, "数字人形象"), (profile.voice_id, "音色")):
                asset = assets.get(asset_id)
                if asset is None:
                    missing.append(f"IP 配方绑定的{label}不存在")
                elif not asset.authorized:
                    missing.append(f"IP 配方绑定的{label}未确认授权")
        if profile.edit_template_id and template_service.get_template(profile.edit_template_id) is None:
            missing.append("IP 配方绑定的剪辑模板不存在")
    if not body.rights_confirmed or not body.rights_holder.strip():
        missing.append("请填写授权主体并确认拥有媒体、文案、肖像和声音处理权")

    if body.source_type == "candidate":
        if not body.candidate_id:
            missing.append("请选择一条候选视频")
        else:
            candidate = repository.get_candidate(body.candidate_id)
            if candidate is None:
                missing.append("所选候选不存在")
            elif candidate.platform != Platform.DOUYIN:
                missing.append("当前单条生产仅支持抖音候选")
            else:
                preview = media_resolution_service.preview(candidate)
                source = {
                    "type": "candidate",
                    "title": candidate.title,
                    "candidate_id": candidate.video_id,
                    "resolvable": preview.resolvable,
                    "estimated_cost_cny": preview.estimated_cost_cny,
                    "message": preview.block_reason,
                }
                if not preview.resolvable:
                    missing.append(preview.block_reason or "该候选当前不能解析媒体")
    else:
        if not body.share_text.strip():
            missing.append("请输入抖音分享链接")
        else:
            try:
                preview = link_transcription_service.preview(body.share_text)
                source = {
                    "type": "share_link",
                    "share_url": preview.share_url,
                    "work_id": preview.work_id,
                    "parser_enabled": preview.parser_enabled,
                    "parser_message": preview.parser_message,
                    "estimated_cost_cny": preview.oneapi_estimated_cost_cny if not preview.parser_enabled else 0,
                    "uses_paid_fallback": not preview.parser_enabled,
                }
                if not preview.parser_enabled:
                    if preview.oneapi_fallback_available:
                        if not body.paid_fallback_confirmed:
                            missing.append("本机解析不可用；请明确确认 OneAPI 付费回退")
                            source["requires_paid_fallback_confirmation"] = True
                    else:
                        missing.append(preview.parser_message or "当前无法解析该抖音分享链接")
            except DouyinParserError as exc:
                missing.append(exc.user_message)

    platforms: list[dict] = []
    if body.publish_enabled:
        if not body.publish_platforms:
            missing.append("打开多平台发布后，请至少选择一个平台")
        available = {item["platform"]: item for item in publish_service.available_platforms()}
        for name in body.publish_platforms:
            capability = available.get(name)
            if capability is None:
                missing.append(f"不支持的发布平台：{name}")
                continue
            platforms.append(capability)
            if not (capability["enabled"] or capability["manual_fallback"]):
                missing.append(f"{capability['display_name']} 未启用且没有人工发布兜底")
            elif capability["manual_only"]:
                warnings.append(f"{capability['display_name']} 将生成人工发布任务，需由客户回填结果")
    return {
        "ready": not missing,
        "missing": missing,
        "warnings": warnings,
        "source": source,
        "profile_name": profile.name if profile else None,
        "platforms": platforms,
    }


@router.post("/guided/preflight")
def guided_preflight(
    body: GuidedPipelineRequest,
    production_service=Depends(get_production_service),
    repository=Depends(get_repository),
    media_resolution_service=Depends(get_media_resolution_service),
    link_transcription_service=Depends(get_douyin_link_transcription_service),
    avatar_service=Depends(get_avatar_service),
    template_service=Depends(get_template_service),
    publish_service=Depends(get_publish_service),
):
    return _guided_preflight(
        body,
        production_service=production_service,
        repository=repository,
        media_resolution_service=media_resolution_service,
        link_transcription_service=link_transcription_service,
        avatar_service=avatar_service,
        template_service=template_service,
        publish_service=publish_service,
    )


@router.post("/guided", response_model=PipelineResponse, status_code=202)
def create_guided_pipeline(
    body: GuidedPipelineRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8),
    service=Depends(get_pipeline_service),
    production_service=Depends(get_production_service),
    repository=Depends(get_repository),
    media_resolution_service=Depends(get_media_resolution_service),
    link_transcription_service=Depends(get_douyin_link_transcription_service),
    avatar_service=Depends(get_avatar_service),
    template_service=Depends(get_template_service),
    publish_service=Depends(get_publish_service),
):
    preflight = _guided_preflight(
        body,
        production_service=production_service,
        repository=repository,
        media_resolution_service=media_resolution_service,
        link_transcription_service=link_transcription_service,
        avatar_service=avatar_service,
        template_service=template_service,
        publish_service=publish_service,
    )
    if not preflight["ready"]:
        raise HTTPException(status_code=400, detail="；".join(preflight["missing"]))
    profile = production_service.get_profile(body.profile_id)
    assert profile is not None
    candidate = repository.get_candidate(body.candidate_id) if body.candidate_id else None
    keyword = candidate.title if candidate is not None else str(preflight["source"].get("share_url") or "抖音分享视频")
    run = service.start_guided_run(
        source_type=body.source_type,
        keyword=keyword,
        profile=profile.model_dump(mode="json"),
        rights_holder=body.rights_holder,
        publish_enabled=body.publish_enabled,
        publish_platforms=body.publish_platforms,
        candidate_id=body.candidate_id,
        share_text=body.share_text,
        use_paid_fallback=bool(preflight["source"].get("uses_paid_fallback")),
        idempotency_key=idempotency_key,
    )
    run = service._event(run, action="idempotency_accepted", message="生产请求已接受。", details={"idempotency_key": idempotency_key})
    service.repository.save_pipeline_run(run)
    return _to_response(run)


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
    if body.approved and not body.approved_text.strip():
        # 保持旧接口的兼容性：只有新引导式任务强制提交最终稿。
        getter = getattr(service, "get_run", None)
        if getter is not None:
            run = getter(run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="流水线不存在。")
            if run.config.get("workflow") in {"guided_candidate", "guided_share_link"}:
                raise HTTPException(status_code=400, detail="请确认或编辑最终口播文案后再生成数字人。")
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


@router.get("/{run_id}/review-draft")
def get_pipeline_review_draft(
    run_id: str,
    service=Depends(get_pipeline_service),
):
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="流水线不存在。")
    copy_task = service.repository.get_task(run.copywriting_task_id or "")
    if not isinstance(copy_task, CopywritingTask):
        raise HTTPException(status_code=404, detail="当前流水线还没有可确认的 AI 文案。")
    transcription_id = next(
        (item.task_id for item in run.stages if item.stage == PipelineStage.TRANSCRIPTION and item.task_id),
        None,
    )
    transcription = service.repository.get_task(transcription_id or "")
    transcript_text = ""
    low_confidence_count = 0
    if isinstance(transcription, TranscriptionTask):
        transcript_text = "\n".join(segment.text for segment in transcription.segments).strip()
        low_confidence_count = sum(
            1 for segment in transcription.segments if (segment.confidence or 0) < 0.6
        )
    variants = list(copy_task.result_variants)
    if copy_task.result_text and copy_task.result_text not in variants:
        variants.insert(0, copy_task.result_text)
    return {
        "run_id": run.run_id,
        "transcription_task_id": transcription_id,
        "transcript_text": transcript_text,
        "low_confidence_count": low_confidence_count,
        "variants": variants,
        "default_text": copy_task.result_text or (variants[0] if variants else ""),
    }


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


@router.get("/{run_id}/media")
def get_pipeline_media(
    run_id: str,
    service=Depends(get_pipeline_service),
):
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="流水线不存在。")
    task = service.repository.get_task(run.edit_task_id or "")
    path = Path(str(getattr(task, "result_path", "") or ""))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="数字人口播成片尚未生成。")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.delete("/{run_id}")
def delete_pipeline(
    run_id: str,
    repo=Depends(get_repository),
):
    if not repo.delete_pipeline_run(run_id):
        raise HTTPException(status_code=404, detail="流水线不存在或已删除。")
    return {"run_id": run_id, "deleted": True}


@router.delete("")
def delete_all_pipelines(
    repo=Depends(get_repository),
):
    """删除全部流水线记录；不会删除候选、素材或数字人成片。"""
    return {"deleted_count": repo.delete_all_pipeline_runs()}


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
