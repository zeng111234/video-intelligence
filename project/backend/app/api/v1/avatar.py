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
import re
import shutil
import subprocess
import tempfile
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from project.backend.app.core.deps import get_avatar_service
from src.adapters.avatar import AvatarProviderError, PROJECT_ROOT, ShuyingLegacyAvatarProvider
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
CLOUD_AVATAR_UPLOAD_EXTENSIONS = {".mp4", ".mov"}
CLOUD_VOICE_UPLOAD_EXTENSIONS = {".wav", ".mp3", ".m4a"}
MAX_CLOUD_AVATAR_UPLOAD_BYTES = 500 * 1024 * 1024
MAX_CLOUD_VOICE_UPLOAD_BYTES = 20 * 1024 * 1024


class AvatarJobCreate(BaseModel):
    """客户版数字人任务请求。"""

    industry_config_id: str | None = None
    template_version_id: str | None = None
    source_task_id: str | None = None
    source_revision_id: str | None = None
    video_name: str | None = Field(default=None, max_length=100)
    keyword: str | None = Field(default=None, max_length=100)
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
    video_name: str
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
    retry_count: int
    can_retry_video_submit: bool
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
        "preview_url": f"/api/v1/avatar/assets/{asset_id}/media",
        "preview_type": "image" if kind == AvatarAssetKind.AVATAR else "audio",
    }
    manifest["assets"] = [
        item for item in manifest["assets"] if item.get("asset_id") != asset_id
    ] + [record]
    _write_assets_manifest(manifest_path, manifest)

    return AvatarAsset(
        asset_id=asset_id,
        kind=kind,
        name=display_name,
        preview_url=record.get("preview_url"),
        preview_type=record["preview_type"],
        authorized=True,
    )


@router.post("/assets/cloud-avatar", response_model=AvatarAsset)
def train_cloud_avatar(
    name: str = Form(...),
    file: UploadFile = File(...),
    rights_confirmed: bool = Form(False),
    rights_holder: str = Form(""),
    service: AvatarService = Depends(get_avatar_service),
):
    capability = service.capabilities()
    if not capability.supports_cloud_avatar_training:
        raise HTTPException(status_code=503, detail="公司云形象训练线路尚未配置。")
    if not rights_confirmed:
        raise HTTPException(status_code=400, detail="必须确认拥有训练视频中的肖像授权。")
    display_name = name.strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="请输入形象名称。")
    path = _stage_cloud_upload(
        file,
        allowed_extensions=CLOUD_AVATAR_UPLOAD_EXTENSIONS,
        max_bytes=MAX_CLOUD_AVATAR_UPLOAD_BYTES,
        label="训练视频",
    )
    try:
        _validate_cloud_avatar_video(path)
        provider = service.provider
        if not isinstance(provider, ShuyingLegacyAvatarProvider):
            raise HTTPException(status_code=503, detail="当前数字人供应商不支持云形象训练。")
        return provider.create_cloud_avatar(
            name=display_name, training_video_path=path, filename=file.filename or "training.mp4"
        )
    except AvatarProviderError as exc:
        status_code = 503 if exc.kind.value == "authorization" else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)


@router.post("/assets/cloud-voice", response_model=AvatarAsset)
def train_cloud_voice(
    name: str = Form(...),
    file: UploadFile = File(...),
    rights_confirmed: bool = Form(False),
    rights_holder: str = Form(""),
    service: AvatarService = Depends(get_avatar_service),
):
    capability = service.capabilities()
    if not capability.supports_voice_sample_upload:
        raise HTTPException(status_code=503, detail="公司云数字人服务尚未配置。")
    if not rights_confirmed:
        raise HTTPException(status_code=400, detail="必须确认拥有声音样本的使用授权。")
    display_name = name.strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="请输入声音名称。")
    path = _stage_cloud_upload(
        file,
        allowed_extensions=CLOUD_VOICE_UPLOAD_EXTENSIONS,
        max_bytes=MAX_CLOUD_VOICE_UPLOAD_BYTES,
        label="声音样本",
    )
    try:
        _validate_cloud_voice_sample(path)
        provider = service.provider
        if not isinstance(provider, ShuyingLegacyAvatarProvider):
            raise HTTPException(status_code=503, detail="当前数字人供应商不支持声音克隆。")
        mime_type = mimetypes.guess_type(file.filename or "")[0] or "audio/mpeg"
        if capability.supports_voice_cloning:
            return provider.create_voice_clone(
                name=display_name,
                sample_path=path,
                filename=file.filename or "voice-sample.mp3",
                mime_type=mime_type,
            )
        return provider.store_pending_voice_sample(
            name=display_name,
            sample_path=path,
            filename=file.filename or "voice-sample.mp3",
        )
    except AvatarProviderError as exc:
        status_code = 503 if exc.kind.value == "authorization" else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)


@router.post("/assets/{asset_id}/resume-voice-clone", response_model=AvatarAsset)
def resume_voice_clone(
    asset_id: str,
    service: AvatarService = Depends(get_avatar_service),
):
    """Submit a legacy saved voice sample exactly once after the clone route is fixed."""
    provider = service.provider
    if not isinstance(provider, ShuyingLegacyAvatarProvider):
        raise HTTPException(status_code=503, detail="当前数字人供应商不支持声音克隆。")
    try:
        return provider.resume_pending_voice_clone(asset_id)
    except AvatarProviderError as exc:
        status_code = 503 if exc.kind.value == "authorization" else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/assets/{asset_id}/media")
def get_asset_media(
    asset_id: str,
    service: AvatarService = Depends(get_avatar_service),
):
    provider = service.provider
    if isinstance(provider, ShuyingLegacyAvatarProvider):
        manifest_path = provider.assets_manifest_path
        record = _find_asset_record(manifest_path, asset_id)
        if record is None or record.get("kind") != AvatarAssetKind.VOICE.value:
            raise HTTPException(status_code=404, detail="声音样本不存在。")
        path = _asset_record_path(
            manifest_path,
            record,
            path_field="sample_path",
        )
        samples_root = (manifest_path.parent / "voice_samples").resolve()
        if samples_root not in path.parents:
            raise HTTPException(status_code=404, detail="声音样本不存在。")
    elif service.capabilities().provider_name == "local_avatar":
        manifest_path = _local_assets_manifest_path()
        record = _find_asset_record(manifest_path, asset_id)
        if record is None:
            raise HTTPException(status_code=404, detail="素材不存在。")
        path = _asset_record_path(manifest_path, record)
    else:
        raise HTTPException(status_code=404, detail="素材不存在。")

    if not path.is_file():
        raise HTTPException(status_code=404, detail="素材文件不存在。")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if (
        record.get("kind") == AvatarAssetKind.VOICE.value
        and path.suffix.lower() == ".webm"
    ):
        media_type = "audio/webm"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/assets/{asset_id}/voice-preview")
def get_voice_preview(
    asset_id: str,
    service: AvatarService = Depends(get_avatar_service),
):
    provider = service.provider
    if not isinstance(provider, ShuyingLegacyAvatarProvider):
        raise HTTPException(status_code=404, detail="当前声音没有可用的试听样本。")
    try:
        audio = provider.render_voice_preview(asset_id)
    except AvatarProviderError as exc:
        status_code = 404 if exc.kind.value == "validation" else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/jobs", response_model=AvatarJobResponse)
def create_job(
    body: AvatarJobCreate,
    service: AvatarService = Depends(get_avatar_service),
):
    avatar_name, voice_name = _asset_names(service, body.avatar_id, body.voice_id)
    request = AvatarSubmitRequest(
        script_text=body.script_text,
        video_name=body.video_name,
        keyword=body.keyword,
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


@router.post("/jobs/{task_id}/retry-video", response_model=AvatarJobResponse)
def retry_avatar_video(
    task_id: str,
    service: AvatarService = Depends(get_avatar_service),
):
    try:
        task = service.retry_failed_video(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        path,
        media_type=task.result_mime or "video/mp4",
        filename=f"{_safe_download_name(task.title)}.mp4",
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
        "supports_cloud_avatar_training": capability.supports_cloud_avatar_training,
        "supports_voice_cloning": capability.supports_voice_cloning,
        "supports_voice_sample_upload": capability.supports_voice_sample_upload,
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


def _stage_cloud_upload(
    file: UploadFile,
    *,
    allowed_extensions: set[str],
    max_bytes: int,
    label: str,
) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"{label}格式不支持，请上传 {'、'.join(sorted(allowed_extensions))}。",
        )
    upload_root = (PROJECT_ROOT / "data" / "avatar_uploads").resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="cloud-training-", suffix=suffix, dir=upload_root, delete=False
    ) as temporary:
        shutil.copyfileobj(file.file, temporary, length=1024 * 1024)
        path = Path(temporary.name).resolve()
    if upload_root not in path.parents:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="上传路径越界。")
    if not path.stat().st_size:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"{label}为空。")
    if path.stat().st_size > max_bytes:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"{label}超过大小限制。")
    return path


def _ffprobe_json(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise HTTPException(status_code=503, detail="服务器未安装 FFprobe，不能安全校验训练素材。")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise HTTPException(status_code=400, detail="无法读取训练素材，请确认文件没有损坏。")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="训练素材元数据无效。") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="训练素材元数据无效。")
    return payload


def _duration_seconds(payload: dict[str, Any]) -> float:
    values: list[Any] = [payload.get("format", {}).get("duration")]
    values.extend(
        stream.get("duration")
        for stream in payload.get("streams", [])
        if isinstance(stream, dict)
    )
    for value in values:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration
    raise HTTPException(status_code=400, detail="训练素材缺少有效时长。")


def _validate_cloud_avatar_video(path: Path) -> None:
    payload = _ffprobe_json(path)
    duration = _duration_seconds(payload)
    streams = [
        item
        for item in payload.get("streams", [])
        if isinstance(item, dict) and item.get("codec_type") == "video"
    ]
    if not streams:
        raise HTTPException(status_code=400, detail="训练视频未检测到视频轨。")
    video = streams[0]
    try:
        width, height = int(video.get("width")), int(video.get("height"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="训练视频缺少有效分辨率。") from exc
    if not 30 <= duration <= 30 * 60:
        raise HTTPException(status_code=400, detail="训练视频时长必须在 30 秒至 30 分钟之间。")
    if min(width, height) < 360 or max(width, height) > 4096:
        raise HTTPException(status_code=400, detail="训练视频分辨率必须在 360p 至 4K 之间。")


def _validate_cloud_voice_sample(path: Path) -> None:
    payload = _ffprobe_json(path)
    if not any(
        isinstance(item, dict) and item.get("codec_type") == "audio"
        for item in payload.get("streams", [])
    ):
        raise HTTPException(status_code=400, detail="声音样本未检测到音频轨。")
    if _duration_seconds(payload) > 30:
        raise HTTPException(status_code=400, detail="声音样本必须在 30 秒以内。")


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


def _asset_record_path(
    manifest_path: Path,
    record: dict[str, Any],
    *,
    path_field: str = "path",
) -> Path:
    raw_path = str(record.get(path_field, ""))
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
        video_name=task.title,
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
        retry_count=task.retry_count,
        can_retry_video_submit=AvatarService.can_retry_video_submit(task),
        is_mock=task.is_mock,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _safe_download_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(". ")
    return cleaned[:100] or "数字人视频"


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
