"""发布 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_publish_service

router = APIRouter(prefix="/api/v1/publish", tags=["publish"])


class PublishRequest(BaseModel):
    video_path: str = Field(..., description="视频路径")
    platform: str = Field(..., description="平台标识")
    title: str = Field(..., min_length=1, description="标题")
    description: str = Field("", description="描述")
    tags: list[str] = Field(default_factory=list, description="标签")


class PublishResponse(BaseModel):
    task_id: str
    status: str
    platform: str
    error_message: str | None = None


@router.post("", response_model=PublishResponse)
def publish_video(
    body: PublishRequest,
    service=Depends(get_publish_service),
):
    """发布视频到指定平台。"""
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
    return PublishResponse(
        task_id=task.task_id,
        status=task.status.value,
        platform=task.platform.value,
        error_message=task.error_message,
    )


@router.get("/platforms")
def list_platforms(
    service=Depends(get_publish_service),
):
    """列出可用发布平台。"""
    return {"platforms": service.available_platforms()}
