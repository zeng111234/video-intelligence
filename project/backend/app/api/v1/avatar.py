"""数字人视频生成 API。

客户版只暴露真实后端状态：能力、资产、任务、媒体文件。开发沙箱可以创建
演示任务，但不会返回假视频或假下载地址。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from project.backend.app.core.deps import get_avatar_service
from src.models import (
    AvatarAsset,
    AvatarAssetKind,
    AvatarCapability,
    AvatarProviderStatus,
    AvatarSubmitRequest,
    AvatarTask,
)
from src.services.avatar import AvatarService

router = APIRouter(prefix="/api/v1/avatar", tags=["avatar"])


class AvatarJobCreate(BaseModel):
    """客户版数字人任务请求。"""

    industry_config_id: str | None = None
    template_version_id: str | None = None
    source_task_id: str | None = None
    source_revision_id: str | None = None
    script_text: str = Field(..., min_length=1)
    avatar_id: str = Field(..., min_length=1)
    voice_id: str = Field(..., min_length=1)
    target_seconds: int = Field(45, ge=15, le=60)
    speech_rate: float = Field(1.0, ge=0.8, le=1.2)
    aspect_ratio: str = "9:16"
    resolution: str = "1080x1920"
    publish_mode: str = "manual"
    target_platforms: list[str] = Field(default_factory=list)
    idempotency_key: str = Field(default_factory=lambda: f"avatar-{uuid4().hex[:12]}")


class LegacyAvatarGenerateRequest(BaseModel):
    """旧 /generate 兼容请求，接受 text/voice 与 tts_text/tts_voice。"""

    model_config = ConfigDict(extra="allow")

    text: str | None = None
    voice: str | None = None
    tts_text: str | None = None
    tts_voice: str | None = None
    speech_rate: float = Field(1.0, ge=0.5, le=2.0)
    avatar_type: str = "public"
    audio_type: str = "tts"


class AvatarJobResponse(BaseModel):
    task_id: str
    status: str
    progress: int
    stage: str
    title: str
    script_text: str
    avatar_id: str
    avatar_name: str
    voice_id: str
    voice_name: str
    speech_rate: float
    aspect_ratio: str
    resolution: str
    provider_name: str
    provider_job_id: str | None = None
    estimated_cost_cny: float | None = None
    estimated_seconds: int | None = None
    actual_seconds: float | None = None
    result_url: str | None = None
    error_kind: str | None = None
    error_message: str | None = None
    is_mock: bool
    created_at: datetime
    updated_at: datetime


class LegacyAvatarTaskResponse(BaseModel):
    task_id: str
    status: str
    progress: int
    created_at: str
    stage: str = ""
    avatar_type: str = "public"
    audio_type: str = "tts"
    video_url: str | None = None
    error_message: str | None = None


@router.get("/capabilities", response_model=AvatarCapability)
def get_capabilities(service: AvatarService = Depends(get_avatar_service)):
    return service.capabilities()


@router.get("/assets", response_model=list[AvatarAsset])
def list_assets(service: AvatarService = Depends(get_avatar_service)):
    return service.list_assets()


@router.post("/jobs", response_model=AvatarJobResponse)
def create_job(
    body: AvatarJobCreate,
    service: AvatarService = Depends(get_avatar_service),
):
    avatar_name, voice_name = _asset_names(service, body.avatar_id, body.voice_id)
    request = AvatarSubmitRequest(
        script_text=body.script_text,
        source_task_id=body.source_task_id or body.industry_config_id,
        source_revision_id=body.source_revision_id or body.template_version_id,
        avatar_id=body.avatar_id,
        voice_id=body.voice_id,
        speech_rate=body.speech_rate,
        aspect_ratio=body.aspect_ratio,
        resolution=body.resolution,
        background="solid",
        rights_holder="current_tenant",
        script_rights_confirmed=True,
        avatar_rights_confirmed=True,
        voice_rights_confirmed=True,
        idempotency_key=body.idempotency_key,
    )
    try:
        task = service.submit(request, avatar_name=avatar_name, voice_name=voice_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _job_response(task)


@router.get("/jobs", response_model=list[AvatarJobResponse])
def list_jobs(service: AvatarService = Depends(get_avatar_service)):
    tasks = [
        item for item in service.repository.list_tasks() if isinstance(item, AvatarTask)
    ]
    return [_job_response(task) for task in tasks]


@router.get("/jobs/{task_id}", response_model=AvatarJobResponse)
def get_job(task_id: str, service: AvatarService = Depends(get_avatar_service)):
    try:
        task = service.refresh_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _job_response(task)


@router.get("/jobs/{task_id}/media")
def get_job_media(task_id: str, service: AvatarService = Depends(get_avatar_service)):
    try:
        task = service.download_result(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path = Path(task.result_path or "")
    if not path.exists():
        raise HTTPException(status_code=404, detail="视频文件不存在。")
    return FileResponse(
        path, media_type=task.result_mime or "video/mp4", filename=f"{task_id}.mp4"
    )


@router.post("/generate", response_model=LegacyAvatarTaskResponse)
def generate_legacy(
    body: LegacyAvatarGenerateRequest,
    service: AvatarService = Depends(get_avatar_service),
):
    script_text = (body.text or body.tts_text or "").strip()
    if not script_text:
        raise HTTPException(status_code=400, detail="请输入文案内容")
    assets = service.list_assets()
    avatar = next(
        (item for item in assets if item.kind == AvatarAssetKind.AVATAR), None
    )
    voice = next(
        (
            item
            for item in assets
            if item.kind == AvatarAssetKind.VOICE
            and item.asset_id == (body.voice or body.tts_voice)
        ),
        None,
    ) or next((item for item in assets if item.kind == AvatarAssetKind.VOICE), None)
    if avatar is None or voice is None:
        raise HTTPException(status_code=503, detail="数字人服务缺少可用公共形象或音色")
    request = AvatarSubmitRequest(
        script_text=script_text,
        avatar_id=avatar.asset_id,
        voice_id=voice.asset_id,
        speech_rate=min(1.2, max(0.8, body.speech_rate)),
        aspect_ratio="9:16",
        resolution="1080x1920",
        background="solid",
        rights_holder="current_tenant",
        script_rights_confirmed=True,
        avatar_rights_confirmed=True,
        voice_rights_confirmed=True,
        idempotency_key=f"legacy-{uuid4().hex[:12]}",
    )
    try:
        task = service.submit(request, avatar_name=avatar.name, voice_name=voice.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _legacy_response(
        task, avatar_type=body.avatar_type, audio_type=body.audio_type
    )


@router.get("/tasks", response_model=list[LegacyAvatarTaskResponse])
def list_legacy_tasks(service: AvatarService = Depends(get_avatar_service)):
    tasks = [
        item for item in service.repository.list_tasks() if isinstance(item, AvatarTask)
    ]
    return [_legacy_response(task) for task in tasks]


@router.get("/tasks/{task_id}", response_model=LegacyAvatarTaskResponse)
def get_legacy_task(task_id: str, service: AvatarService = Depends(get_avatar_service)):
    try:
        task = service.refresh_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _legacy_response(task)


@router.get("/voices")
def list_voices(
    service: AvatarService = Depends(get_avatar_service),
) -> list[dict[str, str]]:
    return [
        {"id": item.asset_id, "name": item.name, "gender": "unknown"}
        for item in service.list_assets()
        if item.kind == AvatarAssetKind.VOICE
    ]


@router.get("/config")
def get_config(service: AvatarService = Depends(get_avatar_service)) -> dict[str, Any]:
    capability = service.capabilities()
    return {
        "mode": capability.mode.value,
        "provider_name": capability.provider_name,
        "enabled": capability.enabled,
        "supports_upload": False,
        "supports_download": capability.enabled and capability.mode.value != "sandbox",
        "missing_configuration": capability.missing_configuration,
        "description": capability.display_name,
    }


def _asset_names(
    service: AvatarService, avatar_id: str, voice_id: str
) -> tuple[str, str]:
    assets = service.list_assets()
    avatars = {
        item.asset_id: item.name
        for item in assets
        if item.kind == AvatarAssetKind.AVATAR
    }
    voices = {
        item.asset_id: item.name
        for item in assets
        if item.kind == AvatarAssetKind.VOICE
    }
    return avatars.get(avatar_id, avatar_id), voices.get(voice_id, voice_id)


def _job_response(task: AvatarTask) -> AvatarJobResponse:
    result_url = (
        f"/api/v1/avatar/jobs/{task.task_id}/media" if task.result_path else None
    )
    return AvatarJobResponse(
        task_id=task.task_id,
        status=task.status.value,
        progress=task.progress,
        stage=task.stage,
        title=task.title,
        script_text=task.script_text,
        avatar_id=task.avatar_id,
        avatar_name=task.avatar_name,
        voice_id=task.voice_id,
        voice_name=task.voice_name,
        speech_rate=task.speech_rate,
        aspect_ratio=task.aspect_ratio,
        resolution=task.resolution,
        provider_name=task.provider_name,
        provider_job_id=task.provider_job_id,
        estimated_cost_cny=task.estimated_cost_cny,
        estimated_seconds=task.estimated_seconds,
        actual_seconds=task.elapsed_seconds,
        result_url=result_url,
        error_kind=task.error_kind.value if task.error_kind else None,
        error_message=task.error_message,
        is_mock=task.is_mock,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _legacy_response(
    task: AvatarTask,
    *,
    avatar_type: str = "public",
    audio_type: str = "tts",
) -> LegacyAvatarTaskResponse:
    status = (
        "running"
        if task.provider_status
        in {
            AvatarProviderStatus.QUEUED,
            AvatarProviderStatus.SUBMITTED,
            AvatarProviderStatus.RUNNING,
        }
        else task.status.value
    )
    return LegacyAvatarTaskResponse(
        task_id=task.task_id,
        status=status,
        progress=task.progress,
        created_at=task.created_at.isoformat(),
        stage=task.stage,
        avatar_type=avatar_type,
        audio_type=audio_type,
        video_url=f"/api/v1/avatar/jobs/{task.task_id}/media"
        if task.result_path
        else None,
        error_message=task.error_message,
    )
