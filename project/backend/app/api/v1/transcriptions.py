"""转写任务 API —— 支持演示模式、文件上传和链接转写。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel

from project.backend.app.core.deps import get_transcription_service
from project.backend.app.core.config import ASRMode, ASR_MODE
from project.backend.app.schemas.requests import (
    TranscriptionCreateRequest,
    TranscriptionRevisionRequest,
)
from project.backend.app.schemas.responses import TranscriptionResponse
from src.models import TaskKind, TranscriptSegment, TranscriptionTask
from src.services.transcription import TranscriptionError

router = APIRouter(prefix="/api/v1/transcriptions", tags=["transcriptions"])


class TranscriptionUrlRequest(BaseModel):
    """链接转写请求"""
    url: str
    rights_confirmed: bool = True


def _to_response(task) -> TranscriptionResponse:
    segments = [
        {
            "start": s.start,
            "end": s.end,
            "text": s.text,
            "confidence": s.confidence,
            "needs_review": s.needs_review,
        }
        for s in (task.segments or [])
    ]
    return TranscriptionResponse(
        task_id=task.task_id,
        title=task.title,
        status=task.status.value,
        progress=task.progress,
        stage=task.stage,
        media_name=task.media_name,
        segments=segments,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@router.post("", response_model=TranscriptionResponse)
def create_transcription(
    body: TranscriptionCreateRequest,
    service=Depends(get_transcription_service),
):
    """创建演示转写任务（不上传真实文件）。"""
    try:
        task = service.create_mock_task(
            media_name=body.media_name,
            media_type=body.media_type,
            rights_confirmed=body.rights_confirmed,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_response(task)


@router.post("/upload", response_model=TranscriptionResponse)
async def upload_and_transcribe(
    file: UploadFile = File(..., description="视频文件（MP4 / MOV）"),
    rights_confirmed: bool = Form(True, description="是否确认拥有媒体处理权"),
    rights_holder: str = Form("", description="权利主体"),
    model_name: str = Form("base", description="识别模型名称"),
    hotwords: str = Form("", description="专有词提示"),
    service=Depends(get_transcription_service),
) -> TranscriptionResponse:
    """上传视频文件并执行真实转写。

    根据 ASR_MODE 配置，可能调用：
    - sandbox：返回演示数据（忽略文件内容）
    - local：本地 faster-whisper 模型
    - cloud：云端 ASR 供应商（阿里云等）
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空。")

    try:
        media_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"文件读取失败: {exc}")

    if not rights_holder.strip():
        rights_holder = "匿名用户"

    try:
        task = service.create_task(
            media_name=file.filename,
            media_type=file.content_type or "video/mp4",
            media_bytes=media_bytes,
            rights_confirmed=rights_confirmed,
            rights_holder=rights_holder,
            model_name=model_name,
            hotwords=hotwords or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return _to_response(task)


@router.post("/url", response_model=TranscriptionResponse)
async def create_transcription_by_url(
    body: TranscriptionUrlRequest,
    service=Depends(get_transcription_service),
):
    """通过视频直链创建转写任务。

    支持 MP4/MOV 格式的 HTTPS 直链。
    抖音、快手等平台分享链接需要先获取视频直链。
    """
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="链接不能为空。")

    try:
        from src.services.video_source import fetch_authorized_video

        # 下载视频
        video = fetch_authorized_video(body.url)

        # 创建真实转写任务
        task = service.create_task(
            media_name=video.name,
            media_type=video.media_type,
            media_bytes=video.content,
            rights_confirmed=body.rights_confirmed,
            rights_holder="API用户",
            model_name="base",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_response(task)


@router.get("/config", response_model=dict[str, Any])
def get_asr_config() -> dict[str, Any]:
    """返回当前 ASR 配置信息，供前端展示。"""
    return {
        "asr_mode": ASR_MODE.value,
        "supports_upload": ASR_MODE in {ASRMode.LOCAL, ASRMode.CLOUD},
        "description": {
            ASRMode.SANDBOX: "演示模式 —— 返回模拟数据",
            ASRMode.LOCAL: "本地模型 —— 使用 faster-whisper 识别",
            ASRMode.CLOUD: "云端识别 —— 调用阿里云等供应商 API",
        }.get(ASR_MODE, "未知模式"),
    }


@router.get("", response_model=list[TranscriptionResponse])
def list_transcriptions(
    limit: int = 50,
    service=Depends(get_transcription_service),
):
    """从 SQLite 加载转写历史。"""
    safe_limit = max(1, min(limit, 100))
    tasks = [
        task
        for task in service.repository.list_tasks()
        if getattr(task, "kind", None) == TaskKind.TRANSCRIPTION
    ][:safe_limit]
    return [_to_response(task) for task in tasks]


@router.get("/{task_id}", response_model=TranscriptionResponse)
def get_transcription(
    task_id: str,
    service=Depends(get_transcription_service),
):
    """获取转写任务详情。"""
    task = service.repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="转写任务不存在。")
    return _to_response(task)


@router.post("/{task_id}/revisions", response_model=dict[str, Any])
def save_transcription_revision(
    task_id: str,
    body: TranscriptionRevisionRequest,
    service=Depends(get_transcription_service),
):
    """保存校对版本；approve=true 时确认成稿。"""
    segments = [
        TranscriptSegment(
            start=item.start,
            end=item.end,
            text=item.text,
            confidence=item.confidence,
            needs_review=item.needs_review,
            reviewed=item.reviewed,
        )
        for item in body.segments
    ]
    try:
        revision = service.save_revision(
            task_id,
            segments,
            reviewer=body.reviewer,
            approve=body.approve,
        )
    except TranscriptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return revision.model_dump(mode="json")


@router.get("/{task_id}/export")
def export_transcription(
    task_id: str,
    format: str = "txt",
    service=Depends(get_transcription_service),
):
    """导出 TXT/JSON/SRT。真实转写必须先确认成稿，演示任务可直接导出。"""
    if format not in {"txt", "json", "srt"}:
        raise HTTPException(status_code=400, detail="导出格式只支持 txt/json/srt。")
    task = service.repository.get_task(task_id)
    if not isinstance(task, TranscriptionTask):
        raise HTTPException(status_code=404, detail="转写任务不存在。")
    if task.is_mock:
        segments = task.segments
    else:
        revision = service.get_approved_revision(task_id)
        if revision is None:
            raise HTTPException(status_code=400, detail="真实转写需确认成稿后才能导出。")
        segments = revision.corrected_segments
    exporter = {
        "txt": service.export_txt,
        "json": service.export_json,
        "srt": service.export_srt,
    }[format]
    content = exporter(segments)
    media_type = "application/json" if format == "json" else "text/plain"
    filename = f"{task_id}.{format}"
    return Response(
        content=content,
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
