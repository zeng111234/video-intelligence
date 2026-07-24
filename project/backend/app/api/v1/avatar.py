"""数字人视频生成 API。

客户版只暴露真实后端状态：能力、资产、任务、媒体文件。开发沙箱可以创建
演示任务，但不会返回假视频或假下载地址。
"""

from __future__ import annotations

from datetime import datetime
import json
import mimetypes
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from project.backend.app.core.deps import get_avatar_service
from src.adapters.avatar import PROJECT_ROOT
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

AVATAR_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VOICE_UPLOAD_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm"}
MAX_AVATAR_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_VOICE_UPLOAD_BYTES = 50 * 1024 * 1024


class AvatarJobCreate(BaseModel):
    """客户版数字人任务请求。"""

    industry_config_id: str | None = None
    template_version_id: str | None = None
    source_task_id: str | None = None
    source_revision_id: str | None = None
    script_text: str = Field(..., min_length=1)
    avatar_id: str = Field(..., min_length=1)
    voice_id: str = Field(..., min_length=1)
    profile_id: str = "default"
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
    profile_id: str = "default"
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


@router.post("/assets/upload", response_model=AvatarAsset)
def upload_asset(
    kind: AvatarAssetKind = Form(...),
    file: UploadFile = File(...),
    name: str = Form(""),
    rights_confirmed: bool = Form(False),
    rights_holder: str = Form(""),
    service: AvatarService = Depends(get_avatar_service),
):
    capability = service.capabilities()
    if capability.provider_name != "local_avatar":
        raise HTTPException(status_code=400, detail="只有本地数字人模式支持上传素材。")
    if not rights_confirmed:
        raise HTTPException(status_code=400, detail="必须确认拥有形象或声音授权。")

    suffix = Path(file.filename or "").suffix.lower()
    allowed = AVATAR_UPLOAD_EXTENSIONS if kind == AvatarAssetKind.AVATAR else VOICE_UPLOAD_EXTENSIONS
    max_bytes = MAX_AVATAR_UPLOAD_BYTES if kind == AvatarAssetKind.AVATAR else MAX_VOICE_UPLOAD_BYTES
    if suffix not in allowed:
        allowed_text = "、".join(sorted(allowed))
        raise HTTPException(status_code=400, detail=f"素材格式不支持，请上传 {allowed_text}。")

    payload = file.file.read(max_bytes + 1)
    if not payload:
        raise HTTPException(status_code=400, detail="上传文件为空。")
    if len(payload) > max_bytes:
        raise HTTPException(status_code=400, detail="上传文件超过大小限制。")

    manifest_path = _local_assets_manifest_path()
    uploads_directory = manifest_path.parent / "uploads"
    uploads_directory.mkdir(parents=True, exist_ok=True)
    asset_id = f"local-{kind.value}-{uuid4().hex[:12]}"
    stored_name = f"{asset_id}{suffix}"
    stored_path = (uploads_directory / stored_name).resolve()
    expected_root = uploads_directory.resolve()
    if expected_root not in stored_path.parents:
        raise HTTPException(status_code=400, detail="上传路径越界。")
    stored_path.write_bytes(payload)

    manifest = _read_assets_manifest(manifest_path)
    relative_path = stored_path.relative_to(manifest_path.parent).as_posix()
    display_name = name.strip() or Path(file.filename or asset_id).stem or asset_id
    record: dict[str, Any] = {
        "asset_id": asset_id,
        "kind": kind.value,
        "name": display_name,
        "path": relative_path,
        "authorized": True,
        "rights_holder": rights_holder.strip() or "本人/公司已授权",
        "uploaded_at": datetime.now().astimezone().isoformat(),
    }
    if kind == AvatarAssetKind.AVATAR:
        record["preview_url"] = f"/api/v1/avatar/assets/{asset_id}/media"
    manifest["assets"] = [
        item for item in manifest["assets"] if item.get("asset_id") != asset_id
    ] + [record]
    _write_assets_manifest(manifest_path, manifest)

    return AvatarAsset(
        asset_id=asset_id,
        kind=kind,
        name=display_name,
        preview_url=record.get("preview_url"),
        authorized=True,
    )


@router.get("/assets/{asset_id}/media")
def get_asset_media(asset_id: str):
    manifest_path = _local_assets_manifest_path()
    record = _find_asset_record(manifest_path, asset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="素材不存在。")
    path = _asset_record_path(manifest_path, record)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="素材文件不存在。")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


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
        profile_id=body.profile_id,
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
def list_jobs(
    include_sandbox: bool = Query(
        False,
        description="是否包含历史 Sandbox 演示任务；生产页面默认只显示真实任务。",
    ),
    service: AvatarService = Depends(get_avatar_service),
):
    tasks = [
        item for item in service.repository.list_tasks() if isinstance(item, AvatarTask)
    ]
    if not include_sandbox:
        tasks = [task for task in tasks if not task.is_mock]
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
        profile_id="default",
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
        "supports_upload": capability.provider_name == "local_avatar",
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


def _local_assets_manifest_path() -> Path:
    raw_path = os.getenv("LOCAL_AVATAR_ASSETS_MANIFEST", "").strip()
    if not raw_path:
        raise HTTPException(status_code=503, detail="本地素材清单未配置。")
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _read_assets_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"assets": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="本地素材清单无法读取。") from exc
    items = payload.get("assets", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise HTTPException(status_code=500, detail="本地素材清单格式无效。")
    return {"assets": [item for item in items if isinstance(item, dict)]}


def _write_assets_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    shutil.move(str(temporary_path), str(path))


def _find_asset_record(path: Path, asset_id: str) -> dict[str, Any] | None:
    manifest = _read_assets_manifest(path)
    for item in manifest["assets"]:
        if item.get("asset_id") == asset_id and item.get("authorized"):
            return item
    return None


def _asset_record_path(manifest_path: Path, record: dict[str, Any]) -> Path:
    raw_path = str(record.get("path", ""))
    path = Path(raw_path)
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path.resolve()


def _job_response(task: AvatarTask) -> AvatarJobResponse:
    result_url = (
        f"/api/v1/avatar/jobs/{task.task_id}/media"
        if task.result_path or (task.status.value == "succeeded" and not task.is_mock)
        else None
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
        profile_id=task.profile_id,
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
