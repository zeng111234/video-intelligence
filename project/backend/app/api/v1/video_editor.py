"""视频剪辑 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from project.backend.app.core.deps import (
    get_copywriting_service,
    get_repository,
    get_transcription_service,
    get_video_editing_service,
)
from src.services.video_editor_workflow import (
    VideoEditorWorkflowError,
    VideoEditorWorkflowService,
)

router = APIRouter(prefix="/api/v1/video-editor", tags=["video-editor"])


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class VideoEditStepRequest(BaseModel):
    """单个剪辑步骤。"""

    step_id: str | None = Field(None, description="步骤唯一标识（留空自动生成）")
    kind: str = Field(
        ...,
        description=(
            "步骤类型：trim | subtitle | watermark | transition | "
            "background_music | speed | resize | filter | concat | "
            "ai_subtitle | ai_volume_norm | ai_enhance | ai_silence_trim"
        ),
    )
    params: dict[str, Any] = Field(default_factory=dict, description="步骤参数")
    order: int = Field(0, ge=0, description="执行顺序")


class VideoEditConfigRequest(BaseModel):
    """完整剪辑配置（含 AI 步骤）。"""

    steps: list[VideoEditStepRequest] = Field(
        default_factory=list, description="剪辑步骤列表"
    )
    output_format: str = Field("mp4", description="输出格式")
    output_resolution: str = Field("1080x1920", description="输出分辨率")
    output_fps: int = Field(30, ge=15, le=60, description="输出帧率")
    output_bitrate: str = Field("4M", description="输出码率")


class VideoEditRequest(BaseModel):
    """视频剪辑请求。

    兼容模式：仅传 ``source_video_path`` + ``subtitle_text`` 时行为与旧版一致。
    完整模式：传 ``edit_config`` 可配置 AI 步骤和输出参数。
    """

    source_video_path: str = Field(..., description="源视频路径")
    subtitle_text: str | None = Field(None, description="字幕文本（兼容字段）")
    subtitle_style: str = Field("default", description="字幕样式")
    edit_config: VideoEditConfigRequest | None = Field(
        None,
        description="完整剪辑配置（含 AI 步骤）。不传时使用默认配置。",
    )


class VideoEditResponse(BaseModel):
    task_id: str
    status: str
    result_path: str | None = None
    result_size_bytes: int | None = None
    error_message: str | None = None


class AnalysisCreateRequest(BaseModel):
    source_id: str = Field(..., min_length=3)
    target_platform: str = Field("douyin", pattern="^(douyin|kuaishou|wechat_channels|xiaohongshu)$")
    subtitle_enabled: bool = True
    subtitle_model: str = Field("large-v3-turbo", pattern="^(large-v3-turbo|base)$")
    language: str = Field("zh", pattern="^(auto|zh|en|ja|ko)$")


class WorkflowStepRequest(BaseModel):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class EditJobCreateRequest(BaseModel):
    analysis_id: str = Field(..., min_length=3)
    steps: list[WorkflowStepRequest] = Field(default_factory=list)
    output_format: str = Field("mp4", pattern="^(mp4|webm|avi|mov)$")
    output_resolution: str = Field("1080x1920", pattern="^\\d{2,5}x\\d{2,5}$")
    output_fps: int = Field(30, ge=15, le=60)
    output_bitrate: str = Field("4M", pattern="^\\d+(?:\\.\\d+)?M$")
    subtitle_enabled: bool = True
    publish_title: str | None = Field(None, max_length=100)


class ProductShowcaseCreateRequest(BaseModel):
    source_id: str = Field(min_length=3)
    product_asset_id: str = Field(min_length=3)
    background_asset_id: str | None = None
    layout: str = Field(
        "avatar_left_product_right",
        pattern="^(avatar_left_product_right|product_canvas_avatar_pip)$",
    )


class BillingConfirmationRequest(BaseModel):
    confirmed: bool = False
    max_cost_cny: float = Field(ge=0)


class BatchCreateRequest(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=10)
    target_platform: str = Field("douyin", pattern="^(douyin|kuaishou|wechat_channels|xiaohongshu)$")
    subtitle_enabled: bool = True
    subtitle_model: str = Field("large-v3-turbo", pattern="^(large-v3-turbo|base)$")
    steps: list[WorkflowStepRequest] = Field(default_factory=list)
    output_format: str = Field("mp4", pattern="^(mp4|webm|avi|mov)$")
    output_resolution: str = Field("1080x1920", pattern="^\\d{2,5}x\\d{2,5}$")
    output_fps: int = Field(30, ge=15, le=60)
    output_bitrate: str = Field("4M", pattern="^\\d+(?:\\.\\d+)?M$")
    bgm_enabled: bool = False
    bgm_id: str | None = None
    bgm_volume: float = Field(0.24, ge=0, le=1)
    output_profile: str | None = Field(
        None,
        pattern="^(720p|1080p)$",
        description="新云工作台唯一权威输出档位；旧调用可不传。",
    )
    quote_id: str | None = None
    billing_confirmation: BillingConfirmationRequest | None = None


class CloudPreflightRequest(BaseModel):
    source_id: str = Field(min_length=3)
    output_profile: str = Field(pattern="^(720p|1080p)$")
    target_platform: str = Field(
        "douyin",
        pattern="^(douyin|kuaishou|wechat_channels|xiaohongshu)$",
    )


class SubtitleSegmentReviewRequest(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(default="", max_length=2000)


class CloudBatchReviewRequest(BaseModel):
    subtitle_segments: list[SubtitleSegmentReviewRequest] = Field(
        default_factory=list,
        max_length=5000,
    )
    enabled_plan_step_ids: list[str] = Field(default_factory=list, max_length=10)
    selected_title: str = Field(min_length=1, max_length=100)
    selected_bgm_id: str | None = None
    confirmed: bool = False


class BatchItemIdsRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=10)


class BatchItemTitleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _build_edit_config(req_config: VideoEditConfigRequest | None):
    """将 API 请求模型转换为 src.models.VideoEditConfig。"""
    if req_config is None:
        return None

    from src.models import VideoEditConfig, VideoEditStep, VideoEditStepKind

    valid_kinds = {k.value for k in VideoEditStepKind}
    steps = []
    for s in req_config.steps:
        if s.kind not in valid_kinds:
            raise HTTPException(
                status_code=422,
                detail=f"不支持的步骤类型: {s.kind}。支持的类型: {', '.join(sorted(valid_kinds))}",
            )
        steps.append(
            VideoEditStep(
                step_id=s.step_id or f"step-{__import__('uuid').uuid4().hex[:8]}",
                kind=VideoEditStepKind(s.kind),
                params=s.params,
                order=s.order,
            )
        )

    return VideoEditConfig(
        steps=steps,
        output_format=req_config.output_format,
        output_resolution=req_config.output_resolution,
        output_fps=req_config.output_fps,
        output_bitrate=req_config.output_bitrate,
    )


def get_workflow_service(
    repository=Depends(get_repository),
    video_editing_service=Depends(get_video_editing_service),
    transcription_service=Depends(get_transcription_service),
    copywriting_service=Depends(get_copywriting_service),
) -> VideoEditorWorkflowService:
    return VideoEditorWorkflowService(
        repository,
        video_editing_service,
        transcription_service,
        copywriting_service,
    )


def _workflow_error(exc: VideoEditorWorkflowError) -> HTTPException:
    message = str(exc)
    status = 404 if "不存在" in message or "已不存在" in message else 400
    return HTTPException(status_code=status, detail=message)


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("/sources")
def list_sources(workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    """列出可被智能剪辑使用的真实系统成片。"""
    items = workflow.list_sources()
    for item in items:
        item.pop("_path", None)
    return {"items": items, "total": len(items)}


@router.post("/uploads")
async def upload_sources(
    files: list[UploadFile] = File(..., description="已授权的 MP4 / MOV 素材"),
    rights_confirmed: bool = Form(False),
    rights_holder: str = Form(""),
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="单次最多上传 10 条素材。")
    items = []
    try:
        for file in files:
            if not file.filename:
                raise VideoEditorWorkflowError("上传文件名不能为空。")
            items.append(
                workflow.upload_source(
                    file_name=file.filename,
                    media_type=file.content_type or "video/mp4",
                    media_bytes=await file.read(),
                    rights_confirmed=rights_confirmed,
                    rights_holder=rights_holder,
                )
            )
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc
    return {"items": items, "total": len(items)}


@router.post("/visual-assets")
async def upload_visual_asset(
    kind: str = Form(...),
    file: UploadFile = File(..., description="已授权的商品主图或背景图"),
    rights_confirmed: bool = Form(False),
    rights_holder: str = Form(""),
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    try:
        if not file.filename:
            raise VideoEditorWorkflowError("上传文件名不能为空。")
        asset = workflow.upload_visual_asset(
            kind=kind,
            file_name=file.filename,
            media_type=file.content_type or "image/png",
            media_bytes=await file.read(),
            rights_confirmed=rights_confirmed,
            rights_holder=rights_holder,
        )
        asset.pop("_path", None)
        return asset
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc


@router.get("/visual-assets")
def list_visual_assets(
    kind: str | None = None,
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    try:
        items = workflow.list_visual_assets(kind)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc
    for item in items:
        item.pop("_path", None)
    return {"items": items, "total": len(items)}


@router.get("/visual-assets/{asset_id}/media")
def get_visual_asset_media(
    asset_id: str,
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    try:
        asset = workflow.resolve_visual_asset(asset_id)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc
    path = Path(asset["_path"])
    return FileResponse(path, media_type=asset["media_type"], filename=path.name)


@router.get("/bgm")
def list_bgm(workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    items = workflow.list_bgm_assets()
    return {"items": items, "total": len(items)}


@router.post("/bgm")
async def upload_bgm(
    file: UploadFile = File(..., description="已授权的背景音乐"),
    mood: str = Form("通用"),
    rights_confirmed: bool = Form(False),
    rights_holder: str = Form(""),
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="背景音乐文件名不能为空。")
    try:
        return workflow.upload_bgm(
            file_name=file.filename,
            media_type=file.content_type or "audio/mpeg",
            media_bytes=await file.read(),
            mood=mood,
            rights_confirmed=rights_confirmed,
            rights_holder=rights_holder,
        )
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc


@router.get("/bgm/{asset_id}/media")
def get_bgm_media(
    asset_id: str,
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    try:
        asset = workflow.resolve_bgm_asset(asset_id)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc
    return FileResponse(
        asset["_path"],
        media_type=asset["media_type"],
        filename=asset["original_name"],
    )


@router.get("/sources/{source_id}/media")
def get_source_media(source_id: str, workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    try:
        source = workflow.resolve_source(source_id)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc
    path = source["_path"]
    return FileResponse(path, media_type=source["media_type"], filename=source["file_name"])


@router.post("/analyses")
def create_analysis(
    body: AnalysisCreateRequest,
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    try:
        task = workflow.create_analysis(**body.model_dump())
        return workflow.get_analysis(task.task_id)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc


@router.post("/preflight")
def preflight_cloud_editor(
    body: CloudPreflightRequest,
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    """只读取本地媒体信息并报价；不会调用任何付费云接口。"""
    try:
        return workflow.create_cloud_preflight(**body.model_dump())
    except (VideoEditorWorkflowError, ValueError) as exc:
        raise _workflow_error(VideoEditorWorkflowError(str(exc))) from exc


@router.post("/batches")
def create_batch(
    body: BatchCreateRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    try:
        if body.output_profile is not None or body.quote_id is not None:
            if body.output_profile is None or not body.quote_id:
                raise VideoEditorWorkflowError(
                    "云端剪辑必须同时提供 output_profile 和 quote_id。"
                )
            return workflow.create_cloud_batch(
                source_ids=body.source_ids,
                target_platform=body.target_platform,
                output_profile=body.output_profile,
                quote_id=body.quote_id,
                billing_confirmation=(
                    body.billing_confirmation.model_dump()
                    if body.billing_confirmation
                    else {}
                ),
                idempotency_key=idempotency_key or "",
                bgm_enabled=body.bgm_enabled,
                bgm_id=body.bgm_id,
                bgm_volume=body.bgm_volume,
            )
        return workflow.create_batch(
            source_ids=body.source_ids,
            target_platform=body.target_platform,
            subtitle_enabled=body.subtitle_enabled,
            subtitle_model=body.subtitle_model,
            steps=[item.model_dump() for item in body.steps],
            output_format=body.output_format,
            output_resolution=body.output_resolution,
            output_fps=body.output_fps,
            output_bitrate=body.output_bitrate,
            bgm_enabled=body.bgm_enabled,
            bgm_id=body.bgm_id,
            bgm_volume=body.bgm_volume,
        )
    except (VideoEditorWorkflowError, ValueError) as exc:
        raise _workflow_error(VideoEditorWorkflowError(str(exc))) from exc


@router.get("/batches")
def list_batches(limit: int = 20, workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    limit = max(1, min(limit, 100))
    items = workflow.list_batches(limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    try:
        return workflow.get_batch(batch_id)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc


@router.post("/batches/{batch_id}/items/{item_id}/continue")
def continue_batch_item(batch_id: str, item_id: str, workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    try:
        return workflow.continue_batch_item(batch_id, item_id)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc


@router.post("/batches/{batch_id}/items/{item_id}/review")
def review_cloud_batch_item(
    batch_id: str,
    item_id: str,
    body: CloudBatchReviewRequest,
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    """原子保存人工字幕/方案复核，然后最多提交一次正式渲染。"""
    try:
        return workflow.review_cloud_batch_item(
            batch_id,
            item_id,
            subtitle_segments=[
                segment.model_dump() for segment in body.subtitle_segments
            ],
            enabled_plan_step_ids=body.enabled_plan_step_ids,
            selected_title=body.selected_title,
            selected_bgm_id=body.selected_bgm_id,
            confirmed=body.confirmed,
        )
    except (VideoEditorWorkflowError, ValueError) as exc:
        raise _workflow_error(VideoEditorWorkflowError(str(exc))) from exc


@router.put("/batches/{batch_id}/items/{item_id}/title")
def select_batch_item_title(
    batch_id: str,
    item_id: str,
    body: BatchItemTitleRequest,
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    try:
        return workflow.select_batch_item_title(batch_id, item_id, body.title)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc


@router.post("/batches/{batch_id}/items/{item_id}/retry")
def retry_batch_item(batch_id: str, item_id: str, workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    try:
        return workflow.retry_batch_item(batch_id, item_id)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc


@router.post("/batches/{batch_id}/confirm-results")
def confirm_batch_results(
    batch_id: str,
    body: BatchItemIdsRequest,
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    try:
        return workflow.confirm_batch_results(batch_id, body.item_ids)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: str, workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    try:
        return workflow.get_analysis(analysis_id)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc


@router.post("/analyses/{analysis_id}/content-advice")
def create_content_advice(analysis_id: str, workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    try:
        return workflow.generate_content_advice(analysis_id)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc


@router.post("/jobs")
def create_edit_job(
    body: EditJobCreateRequest,
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    try:
        task = workflow.create_edit_job(
            analysis_id=body.analysis_id,
            steps=[item.model_dump() for item in body.steps],
            output_format=body.output_format,
            output_resolution=body.output_resolution,
            output_fps=body.output_fps,
            output_bitrate=body.output_bitrate,
            subtitle_enabled=body.subtitle_enabled,
            publish_title=body.publish_title,
        )
        return workflow.get_job(task.task_id)
    except (VideoEditorWorkflowError, ValueError) as exc:
        raise _workflow_error(VideoEditorWorkflowError(str(exc))) from exc


@router.post("/product-showcase/jobs")
def create_product_showcase_job(
    body: ProductShowcaseCreateRequest,
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    try:
        task = workflow.create_product_showcase_job(**body.model_dump())
        return workflow.get_job(task.task_id)
    except (VideoEditorWorkflowError, ValueError) as exc:
        raise _workflow_error(VideoEditorWorkflowError(str(exc))) from exc


@router.get("/jobs")
def list_jobs(limit: int = 20, workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    limit = max(1, min(limit, 100))
    items = workflow.list_jobs(limit)
    return {"items": items, "total": len(items)}


@router.get("/jobs/{task_id}")
def get_job(task_id: str, workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    try:
        return workflow.get_job(task_id)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc


@router.get("/jobs/{task_id}/media")
def get_job_media(task_id: str, workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    try:
        task = workflow.get_edit_task(task_id)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc
    if not task.result_path:
        raise HTTPException(status_code=400, detail="成片尚未生成。")
    from pathlib import Path
    path = Path(task.result_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="成片文件不存在。")
    return FileResponse(path, media_type=task.result_mime or "video/mp4", filename=path.name)


@router.get("/jobs/{task_id}/download")
def download_job_media(task_id: str, workflow: VideoEditorWorkflowService = Depends(get_workflow_service)):
    try:
        task = workflow.get_edit_task(task_id)
    except VideoEditorWorkflowError as exc:
        raise _workflow_error(exc) from exc
    if not task.result_path:
        raise HTTPException(status_code=400, detail="成片尚未生成。")
    from pathlib import Path
    path = Path(task.result_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="成片文件不存在。")
    return FileResponse(path, media_type=task.result_mime or "video/mp4", filename=f"{task_id}{path.suffix}")


@router.post("/edit", response_model=VideoEditResponse)
def edit_video(
    body: VideoEditRequest,
    service=Depends(get_video_editing_service),
):
    """视频剪辑。

    - 仅传 source_video_path + subtitle_text 时为兼容模式。
    - 传 edit_config 可使用完整 AI 智能剪辑步骤。
    """
    if not Path(body.source_video_path).exists():
        raise HTTPException(status_code=400, detail="源视频文件不存在。")
    try:
        edit_config = _build_edit_config(body.edit_config)
        task = service.edit_video(
            source_video_path=body.source_video_path,
            subtitle_text=body.subtitle_text,
            subtitle_style=body.subtitle_style,
            edit_config=edit_config,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return VideoEditResponse(
        task_id=task.task_id,
        status=task.status.value,
        result_path=task.result_path,
        result_size_bytes=task.result_size_bytes,
        error_message=task.error_message,
    )


@router.get("/capabilities")
def capabilities(
    service=Depends(get_video_editing_service),
    workflow: VideoEditorWorkflowService = Depends(get_workflow_service),
):
    """获取新云工作台能力，并保留旧本地编辑器诊断信息。"""
    local = service.capabilities()
    cloud = workflow.cloud_capabilities()
    return {
        **local,
        **cloud,
        "display_name": (
            "阿里云轻量智能剪辑"
            if cloud["provider_mode"] == "aliyun"
            else "云端轻量智能剪辑（沙箱）"
        ),
        "legacy_local_capabilities": local,
    }


@router.get("/models")
def local_models():
    from src.resources import list_asr_model_statuses

    items = list_asr_model_statuses()
    return {"items": items, "total": len(items)}


@router.post("/models/{model_name}/prepare")
def prepare_local_model(model_name: str):
    from src.resources import prepare_asr_model

    try:
        return prepare_asr_model(model_name)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/step-kinds")
def step_kinds():
    """返回所有支持的步骤类型及其参数说明。"""
    kinds = {
        "trim": {
            "label": "裁剪",
            "category": "basic",
            "params": {
                "start": {"type": "number", "label": "起始秒", "default": 0},
                "end": {"type": "number", "label": "结束秒（可选）"},
                "duration": {"type": "number", "label": "持续秒（可选）"},
            },
        },
        "subtitle": {
            "label": "字幕",
            "category": "basic",
            "params": {
                "srt_path": {
                    "type": "string",
                    "label": "SRT 文件路径",
                    "required": True,
                },
                "style": {"type": "string", "label": "样式", "default": "default"},
            },
        },
        "watermark": {
            "label": "水印",
            "category": "basic",
            "params": {
                "watermark_path": {
                    "type": "string",
                    "label": "水印图片路径",
                    "required": True,
                },
                "position": {
                    "type": "select",
                    "label": "位置",
                    "options": [
                        "top_left",
                        "top_right",
                        "bottom_left",
                        "bottom_right",
                        "center",
                    ],
                    "default": "bottom_right",
                },
                "opacity": {
                    "type": "number",
                    "label": "透明度",
                    "default": 0.5,
                    "min": 0,
                    "max": 1,
                },
            },
        },
        "speed": {
            "label": "速度调整",
            "category": "basic",
            "params": {
                "speed": {
                    "type": "number",
                    "label": "倍速",
                    "default": 1.0,
                    "min": 0.25,
                    "max": 4.0,
                },
            },
        },
        "resize": {
            "label": "分辨率调整",
            "category": "basic",
            "params": {
                "resolution": {
                    "type": "string",
                    "label": "目标分辨率",
                    "default": "1080x1920",
                },
            },
        },
        "filter": {
            "label": "滤镜",
            "category": "basic",
            "params": {
                "filter_string": {
                    "type": "string",
                    "label": "FFmpeg 滤镜",
                    "required": True,
                },
            },
        },
        "background_music": {
            "label": "背景音乐",
            "category": "basic",
            "params": {
                "audio_path": {"type": "string", "label": "音频路径", "required": True},
                "volume": {
                    "type": "number",
                    "label": "音量",
                    "default": 0.3,
                    "min": 0,
                    "max": 1,
                },
            },
        },
        "concat": {
            "label": "拼接",
            "category": "basic",
            "params": {
                "video_paths": {
                    "type": "array",
                    "label": "视频路径列表",
                    "required": True,
                },
            },
        },
        "ai_subtitle": {
            "label": "AI 自动字幕",
            "category": "ai",
            "requires": "faster-whisper 或 whisper CLI",
            "params": {
                "model": {
                    "type": "select",
                    "label": "Whisper 模型",
                    "options": [
                        "tiny",
                        "base",
                        "small",
                        "medium",
                        "large",
                        "large-v2",
                        "large-v3",
                    ],
                    "default": "base",
                },
                "language": {
                    "type": "select",
                    "label": "语言",
                    "options": ["zh", "en", "ja", "ko"],
                    "default": "zh",
                },
                "style": {"type": "string", "label": "字幕样式", "default": "default"},
            },
        },
        "ai_volume_norm": {
            "label": "AI 音量标准化",
            "category": "ai",
            "requires": "ffmpeg",
            "params": {
                "target_i": {
                    "type": "number",
                    "label": "目标响度 (LUFS)",
                    "default": -16,
                    "min": -70,
                    "max": -5,
                },
                "target_lra": {
                    "type": "number",
                    "label": "响度范围",
                    "default": 11,
                    "min": 1,
                    "max": 50,
                },
                "target_tp": {
                    "type": "number",
                    "label": "真峰值 (dBTP)",
                    "default": -1.5,
                    "min": -9,
                    "max": 0,
                },
            },
        },
        "ai_enhance": {
            "label": "AI 画面增强",
            "category": "ai",
            "requires": "ffmpeg",
            "params": {
                "brightness": {
                    "type": "number",
                    "label": "亮度",
                    "default": 0.06,
                    "min": -1,
                    "max": 1,
                },
                "contrast": {
                    "type": "number",
                    "label": "对比度",
                    "default": 1.1,
                    "min": 0,
                    "max": 2,
                },
                "saturation": {
                    "type": "number",
                    "label": "饱和度",
                    "default": 1.15,
                    "min": 0,
                    "max": 3,
                },
                "sharpen": {
                    "type": "number",
                    "label": "锐化",
                    "default": 1.5,
                    "min": 0,
                    "max": 5,
                },
                "denoise": {
                    "type": "number",
                    "label": "降噪",
                    "default": 3,
                    "min": 0,
                    "max": 10,
                },
            },
        },
        "ai_silence_trim": {
            "label": "AI 静音裁剪",
            "category": "ai",
            "requires": "ffmpeg",
            "params": {
                "noise_threshold": {
                    "type": "number",
                    "label": "噪声阈值 (dB)",
                    "default": -30,
                    "min": -80,
                    "max": 0,
                },
                "min_duration": {
                    "type": "number",
                    "label": "最短静音时长 (秒)",
                    "default": 0.5,
                    "min": 0.1,
                    "max": 10,
                },
                "keep_padding": {
                    "type": "number",
                    "label": "保留间距 (秒)",
                    "default": 0.1,
                    "min": 0,
                    "max": 2,
                },
            },
        },
    }
    return kinds
