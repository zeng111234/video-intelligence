"""数字人视频生成 API"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/avatar", tags=["avatar"])


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
    """生成数字人视频

    当前为演示模式，返回模拟任务。
    真实模式需要集成：
    - Duix-Avatar: https://github.com/duixcom/Duix-Avatar
    - Linly-Talker: https://github.com/Kedreamix/Linly-Talker
    - 或其他数字人服务
    """
    task_id = f"avatar-{uuid.uuid4().hex[:10]}"

    # 验证输入
    if body.audio_type == "tts" and not body.tts_text.strip():
        raise HTTPException(status_code=400, detail="TTS 模式需要输入文案")

    # 创建模拟任务
    task = AvatarTask(
        task_id=task_id,
        status="running",
        progress=0,
        created_at=datetime.now().isoformat(),
        avatar_type=body.avatar_type,
        audio_type=body.audio_type,
    )
    MOCK_TASKS[task_id] = task

    # 模拟进度更新（实际应由后台任务处理）
    import asyncio

    async def simulate_progress():
        for i in range(10, 101, 10):
            await asyncio.sleep(0.5)
            if task_id in MOCK_TASKS:
                MOCK_TASKS[task_id].progress = i
                if i == 100:
                    MOCK_TASKS[task_id].status = "succeeded"
                    MOCK_TASKS[task_id].video_url = f"/api/v1/avatar/download/{task_id}"

    asyncio.create_task(simulate_progress())

    return task


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


@router.get("/tasks/{task_id}", response_model=AvatarTask)
async def get_task_status(task_id: str) -> AvatarTask:
    """获取任务状态"""
    if task_id not in MOCK_TASKS:
        raise HTTPException(status_code=404, detail="任务不存在")
    return MOCK_TASKS[task_id]


@router.get("/tasks")
async def list_tasks() -> list[AvatarTask]:
    """获取所有任务"""
    return list(MOCK_TASKS.values())


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
    return [
        {"id": "sweet_female", "name": "甜美女声", "gender": "female", "style": "sweet"},
        {"id": "magnetic_male", "name": "磁性男声", "gender": "male", "style": "magnetic"},
        {"id": "youth", "name": "活力青年", "gender": "neutral", "style": "energetic"},
        {"id": "broadcast", "name": "专业播音", "gender": "neutral", "style": "professional"},
        {"id": "customer_service", "name": "亲切客服", "gender": "female", "style": "friendly"},
    ]
