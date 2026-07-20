"""数字人视频生成 API"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from project.backend.app.services.avatar_service import get_avatar_provider

router = APIRouter(prefix="/api/v1/avatar", tags=["avatar"])

# 获取数字人提供者
avatar_provider = get_avatar_provider()


class AvatarGenerateRequest(BaseModel):
    """数字人生成请求"""
    avatar_type: str = "image"  # image 或 video
    audio_type: str = "tts"  # tts, record, upload
    tts_text: str = ""
    tts_voice: str = "sweet_female"
    speech_rate: float = 1.0


class AvatarTask(BaseModel):
    """数字人任务"""
    task_id: str
    status: str
    progress: int
    created_at: str
    avatar_type: str
    audio_type: str
    video_url: str | None = None
    error_message: str | None = None


# Mock 任务存储
MOCK_TASKS: dict[str, AvatarTask] = {}


@router.post("/generate", response_model=AvatarTask)
async def generate_avatar_video(body: AvatarGenerateRequest) -> AvatarTask:
    """生成数字人视频"""
    # 验证输入
    if body.audio_type == "tts" and not body.tts_text.strip():
        raise HTTPException(status_code=400, detail="TTS 模式需要输入文案")

    try:
        task = await avatar_provider.generate_video(
            avatar_image="",  # 需要先上传形象
            audio="",  # 需要先上传音频或使用 TTS
            text=body.tts_text,
            voice=body.tts_voice,
            speech_rate=body.speech_rate,
        )
        return AvatarTask(**task.to_dict())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload-avatar")
async def upload_avatar_image(file: UploadFile = File(...)) -> dict[str, str]:
    """上传数字人形象图片"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    # 验证文件类型
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="仅支持 JPG/PNG/WebP 格式")

    # 保存文件（实际应保存到对象存储）
    file_id = f"avatar-{uuid.uuid4().hex[:10]}"
    return {"file_id": file_id, "filename": file.filename, "status": "uploaded"}


@router.post("/upload-audio")
async def upload_audio_file(file: UploadFile = File(...)) -> dict[str, str]:
    """上传音频文件"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    # 验证文件类型
    allowed_types = {"audio/mpeg", "audio/wav", "audio/mp4", "audio/x-m4a"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="仅支持 MP3/WAV/M4A 格式")

    # 保存文件
    file_id = f"audio-{uuid.uuid4().hex[:10]}"
    return {"file_id": file_id, "filename": file.filename, "status": "uploaded"}


@router.get("/tasks", response_model=list[AvatarTask])
async def list_avatar_tasks() -> list[AvatarTask]:
    """获取数字人任务列表。"""
    tasks = getattr(avatar_provider, "tasks", {})
    return [AvatarTask(**task.to_dict()) for task in tasks.values()]


@router.get("/tasks/{task_id}", response_model=AvatarTask)
async def get_task_status(task_id: str) -> AvatarTask:
    """获取任务状态"""
    try:
        task = await avatar_provider.get_task_status(task_id)
        return AvatarTask(**task.to_dict())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download/{task_id}")
async def download_video(task_id: str):
    """下载生成的视频"""
    if task_id not in MOCK_TASKS:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = MOCK_TASKS[task_id]
    if task.status != "succeeded":
        raise HTTPException(status_code=400, detail="视频尚未生成完成")

    # 实际应返回视频文件
    return {"message": "视频下载（演示模式）", "task_id": task_id}


@router.get("/voices")
async def list_voices() -> list[dict[str, str]]:
    """获取可用音色列表"""
    try:
        return await avatar_provider.list_voices()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
