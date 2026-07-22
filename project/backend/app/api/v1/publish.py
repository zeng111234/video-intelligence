"""发布 API。"""

from __future__ import annotations

import os
from pathlib import Path
from shutil import copyfileobj
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from project.backend.app.core import config as backend_config
from project.backend.app.core import deps as backend_deps
from project.backend.app.core.config import PROJECT_ROOT
from project.backend.app.core.deps import get_publish_service

router = APIRouter(prefix="/api/v1/publish", tags=["publish"])

PUBLISH_ASSET_DIR = PROJECT_ROOT / "data" / "publish_assets"

PUBLISH_CONFIG_FIELDS: dict[str, dict[str, str]] = {
    "douyin": {
        "mode": "PUBLISH_DOUYIN_MODE",
        "access_token": "PUBLISH_DOUYIN_ACCESS_TOKEN",
        "open_id": "PUBLISH_DOUYIN_OPEN_ID",
        "client_key": "PUBLISH_DOUYIN_CLIENT_KEY",
        "client_secret": "PUBLISH_DOUYIN_CLIENT_SECRET",
    },
    "kuaishou": {
        "mode": "PUBLISH_KUAISHOU_MODE",
        "access_token": "PUBLISH_KUAISHOU_ACCESS_TOKEN",
        "open_id": "PUBLISH_KUAISHOU_OPEN_ID",
        "client_key": "PUBLISH_KUAISHOU_CLIENT_KEY",
        "client_secret": "PUBLISH_KUAISHOU_CLIENT_SECRET",
    },
    "xiaohongshu": {
        "mode": "PUBLISH_XIAOHONGSHU_MODE",
        "access_token": "PUBLISH_XIAOHONGSHU_ACCESS_TOKEN",
        "open_id": "PUBLISH_XIAOHONGSHU_OPEN_ID",
        "client_key": "PUBLISH_XIAOHONGSHU_CLIENT_KEY",
        "client_secret": "PUBLISH_XIAOHONGSHU_CLIENT_SECRET",
    },
    "wechat_channels": {
        "mode": "PUBLISH_WECHAT_CHANNELS_MODE",
        "access_token": "PUBLISH_WECHAT_CHANNELS_ACCESS_TOKEN",
        "open_id": "PUBLISH_WECHAT_CHANNELS_OPEN_ID",
        "client_key": "PUBLISH_WECHAT_CHANNELS_CLIENT_KEY",
        "client_secret": "PUBLISH_WECHAT_CHANNELS_CLIENT_SECRET",
    },
}

SECRET_CONFIG_FIELDS = {"access_token", "client_secret"}


class PublishRequest(BaseModel):
    video_path: str = Field(..., description="视频路径")
    platform: str = Field(..., description="平台标识")
    title: str = Field(..., min_length=1, description="标题")
    description: str = Field("", description="描述")
    tags: list[str] = Field(default_factory=list, description="标签")


class PublishPreflightRequest(BaseModel):
    video_path: str = Field(..., description="视频路径")
    platforms: list[str] = Field(..., min_length=1, description="平台标识列表")
    title: str = Field(..., min_length=1, description="标题")
    description: str = Field("", description="描述")
    tags: list[str] = Field(default_factory=list, description="标签")


class PublishBatchRequest(PublishPreflightRequest):
    confirmation_accepted: bool = Field(False, description="是否已确认预检结果")
    source_pipeline_run_id: str | None = None


class ManualPublishResultRequest(BaseModel):
    succeeded: bool | None = Field(
        ..., description="true=已发布，false=失败，null=结果不确定"
    )
    platform_url: str | None = None
    platform_video_id: str | None = None
    note: str = ""


class PublishVariableStatus(BaseModel):
    field: str
    key: str
    configured: bool
    masked_value: str
    secret: bool


class PublishPlatformConfig(BaseModel):
    platform: str
    display_name: str
    mode: str
    env_path: str
    variables: list[PublishVariableStatus]


class PublishConfigResponse(BaseModel):
    env_path: str
    platforms: list[PublishPlatformConfig]


class PublishPlatformConfigUpdate(BaseModel):
    mode: str = Field("manual", pattern="^(manual|official)$")
    access_token: str | None = None
    open_id: str | None = None
    client_key: str | None = None
    client_secret: str | None = None


class PublishResponse(BaseModel):
    task_id: str
    batch_id: str | None = None
    status: str
    publish_status: str
    platform: str
    title: str
    stage: str
    provider_name: str
    platform_video_id: str | None = None
    platform_url: str | None = None
    is_mock: bool
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class PublishBatchResponse(BaseModel):
    batch_id: str
    status: str
    total: int
    succeeded: int
    failed: int
    outcome_unknown: int
    pending: int
    created_at: str
    updated_at: str
    tasks: list[PublishResponse]


def _build_targets(body: PublishPreflightRequest):
    from src.models import PublishPlatform, PublishTarget

    targets = []
    for platform_key in body.platforms:
        try:
            platform = PublishPlatform(platform_key)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"不支持的平台: {platform_key}")
        targets.append(
            PublishTarget(
                platform=platform,
                title=body.title,
                description=body.description,
                tags=body.tags,
            )
        )
    return targets


def _task_response(task) -> PublishResponse:
    return PublishResponse(
        task_id=task.task_id,
        batch_id=task.batch_id,
        status=task.status.value,
        publish_status=task.publish_status.value,
        platform=task.target.platform.value,
        title=task.target.title,
        stage=task.stage,
        provider_name=task.provider_name,
        platform_video_id=task.platform_video_id,
        platform_url=task.platform_url,
        is_mock=task.is_mock,
        error_message=task.error_message,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


def _batch_response(summary: dict[str, Any]) -> PublishBatchResponse:
    return PublishBatchResponse(
        batch_id=summary["batch_id"],
        status=summary["status"],
        total=summary["total"],
        succeeded=summary["succeeded"],
        failed=summary["failed"],
        outcome_unknown=summary["outcome_unknown"],
        pending=summary["pending"],
        created_at=summary["created_at"],
        updated_at=summary["updated_at"],
        tasks=[_task_response(task) for task in summary["tasks"]],
    )


def _mask_value(value: str, *, secret: bool) -> str:
    if not value:
        return ""
    if not secret and len(value) <= 16:
        return value
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def _env_value(key: str) -> str:
    return os.getenv(key, backend_config._parse_env_file(backend_config.ENV_PATH).get(key, "")).strip()


def _platform_config(platform: str) -> PublishPlatformConfig:
    fields = PUBLISH_CONFIG_FIELDS[platform]
    mode = _env_value(fields["mode"]) or "manual"
    variables = []
    for field, key in fields.items():
        value = _env_value(key)
        is_secret = field in SECRET_CONFIG_FIELDS
        variables.append(
            PublishVariableStatus(
                field=field,
                key=key,
                configured=bool(value),
                masked_value=_mask_value(value, secret=is_secret),
                secret=is_secret,
            )
        )
    return PublishPlatformConfig(
        platform=platform,
        display_name={
            "douyin": "抖音",
            "kuaishou": "快手",
            "xiaohongshu": "小红书",
            "wechat_channels": "视频号",
        }[platform],
        mode=mode if mode in {"manual", "official"} else "manual",
        env_path=str(backend_config.ENV_PATH),
        variables=variables,
    )


def _format_env_line(key: str, value: str) -> str:
    normalized = value.strip()
    if any(ch.isspace() for ch in normalized) or "#" in normalized:
        normalized = '"' + normalized.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{key}={normalized}"


def _update_env_file(updates: dict[str, str]) -> None:
    env_path = backend_config.ENV_PATH
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = (
        env_path.read_text(encoding="utf-8-sig").splitlines()
        if env_path.exists()
        else []
    )
    remaining = dict(updates)
    output_lines: list[str] = []
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            output_lines.append(raw_line)
            continue
        key = raw_line.split("=", 1)[0].strip()
        if key in remaining:
            output_lines.append(_format_env_line(key, remaining.pop(key)))
        else:
            output_lines.append(raw_line)
    if remaining:
        if output_lines and output_lines[-1].strip():
            output_lines.append("")
        output_lines.append("# Publish platform configuration")
        for key, value in remaining.items():
            output_lines.append(_format_env_line(key, value))
    env_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
    for key, value in updates.items():
        os.environ[key] = value
    backend_deps.get_publishers.cache_clear()
    backend_deps.get_publish_service.cache_clear()


def _config_updates(platform: str, body: PublishPlatformConfigUpdate) -> dict[str, str]:
    if platform not in PUBLISH_CONFIG_FIELDS:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")
    fields = PUBLISH_CONFIG_FIELDS[platform]
    updates = {fields["mode"]: body.mode}
    for field in ("access_token", "open_id", "client_key", "client_secret"):
        value = getattr(body, field)
        if value is not None and value.strip():
            updates[fields[field]] = value.strip()
    return updates


@router.get("/config", response_model=PublishConfigResponse)
def get_publish_config():
    """读取发布配置状态，不回显完整密钥。"""
    return PublishConfigResponse(
        env_path=str(backend_config.ENV_PATH),
        platforms=[
            _platform_config(platform)
            for platform in (
                "douyin",
                "kuaishou",
                "wechat_channels",
                "xiaohongshu",
            )
        ],
    )


@router.put("/config/{platform}", response_model=PublishPlatformConfig)
def update_publish_config(
    platform: str,
    body: PublishPlatformConfigUpdate,
):
    """保存单个平台发布配置到根目录 .env。"""
    updates = _config_updates(platform, body)
    _update_env_file(updates)
    return _platform_config(platform)


@router.get("/platforms")
def list_platforms(
    service=Depends(get_publish_service),
):
    """列出可用发布平台。"""
    return {"platforms": service.available_platforms()}


@router.get("/assets")
def list_assets():
    """列出已上传到本机的待发布成片。"""
    PUBLISH_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(PUBLISH_ASSET_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "size_bytes": stat.st_size,
                "updated_at": stat.st_mtime,
            }
        )
    return {"items": items, "total": len(items)}


@router.post("/assets/upload")
def upload_asset(
    file: UploadFile = File(...),
    rights_confirmed: bool = Form(True),
):
    """上传本机成片，用于人工或官方发布任务。"""
    if not rights_confirmed:
        raise HTTPException(status_code=400, detail="请先确认拥有该成片的发布权。")
    filename = Path(file.filename or "video.mp4").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mp4", ".mov", ".m4v"}:
        raise HTTPException(status_code=400, detail="仅支持 mp4、mov、m4v 成片文件。")

    PUBLISH_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    target = PUBLISH_ASSET_DIR / filename
    if target.exists():
        target = PUBLISH_ASSET_DIR / f"{target.stem}-{len(list(PUBLISH_ASSET_DIR.glob(target.stem + '*')))}{target.suffix}"
    try:
        with target.open("wb") as out_file:
            copyfileobj(file.file, out_file)
    finally:
        file.file.close()
    return {
        "name": target.name,
        "path": str(target),
        "size_bytes": target.stat().st_size,
    }


@router.post("/preflight")
def preflight_publish(
    body: PublishPreflightRequest,
    service=Depends(get_publish_service),
):
    """发布前检查，不创建任务。"""
    targets = _build_targets(body)
    return service.preflight(video_path=body.video_path, targets=targets)


@router.post("/batches", response_model=PublishBatchResponse)
def create_publish_batch(
    body: PublishBatchRequest,
    service=Depends(get_publish_service),
):
    """创建多平台发布批次。"""
    targets = _build_targets(body)
    preflight = service.preflight(video_path=body.video_path, targets=targets)
    if preflight["blocked"]:
        raise HTTPException(status_code=400, detail={"message": "发布预检未通过", **preflight})
    if not body.confirmation_accepted:
        raise HTTPException(status_code=400, detail="请先完成发布预检并确认。")
    summary = service.create_batch(
        video_path=body.video_path,
        targets=targets,
        source_pipeline_run_id=body.source_pipeline_run_id,
    )
    return _batch_response(summary)


@router.get("/batches")
def list_publish_batches(
    service=Depends(get_publish_service),
):
    """列出发布批次。"""
    batches = [_batch_response(summary) for summary in service.list_batches()]
    return {"items": batches, "total": len(batches)}


@router.get("/batches/{batch_id}", response_model=PublishBatchResponse)
def get_publish_batch(
    batch_id: str,
    service=Depends(get_publish_service),
):
    summary = service.get_batch(batch_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="发布批次不存在。")
    return _batch_response(summary)


@router.post("/tasks/{task_id}/manual-result", response_model=PublishResponse)
def record_manual_result(
    task_id: str,
    body: ManualPublishResultRequest,
    service=Depends(get_publish_service),
):
    try:
        task = service.record_manual_result(
            task_id=task_id,
            succeeded=body.succeeded,
            platform_url=body.platform_url,
            platform_video_id=body.platform_video_id,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _task_response(task)


@router.post("/tasks/{task_id}/retry", response_model=PublishResponse)
def retry_publish_task(
    task_id: str,
    service=Depends(get_publish_service),
):
    try:
        task = service.retry_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _task_response(task)


@router.post("", response_model=PublishResponse)
def publish_video(
    body: PublishRequest,
    service=Depends(get_publish_service),
):
    """兼容旧单平台发布接口。"""
    from src.models import PublishPlatform, PublishTarget

    try:
        platform = PublishPlatform(body.platform)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {body.platform}")

    target = PublishTarget(
        platform=platform,
        title=body.title,
        description=body.description,
        tags=body.tags,
    )
    try:
        task = service.publish(video_path=body.video_path, target=target)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _task_response(task)
