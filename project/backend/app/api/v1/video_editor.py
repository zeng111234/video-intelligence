"""视频剪辑 API。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_video_editing_service

router = APIRouter(prefix="/api/v1/video-editor", tags=["video-editor"])


class VideoEditRequest(BaseModel):
    source_video_path: str = Field(..., description="源视频路径")
    subtitle_text: str | None = Field(None, description="字幕文本")


class VideoEditResponse(BaseModel):
    task_id: str
    status: str
    result_path: str | None = None
    result_size_bytes: int | None = None
    error_message: str | None = None


@router.post("/edit", response_model=VideoEditResponse)
def edit_video(
    body: VideoEditRequest,
    service=Depends(get_video_editing_service),
):
    """视频剪辑。"""
    if not Path(body.source_video_path).exists():
        raise HTTPException(status_code=400, detail="源视频文件不存在。")
    try:
        task = service.edit_video(
            source_video_path=body.source_video_path,
            subtitle_text=body.subtitle_text,
        )
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
):
    """获取视频编辑器能力。"""
    return service.capabilities()
