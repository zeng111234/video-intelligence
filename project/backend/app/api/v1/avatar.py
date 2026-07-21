"""数字人视频生成 API

支持三种模式：
1. mock - 演示模式，返回模拟数据
2. simple - 简化模式，使用 edge-tts + ffmpeg（推荐本地测试）
3. cloud - 云端 API 模式（推荐生产环境）
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/avatar", tags=["avatar"])

# 获取运行模式
AVATAR_MODE = os.getenv("AVATAR_MODE", "simple").lower()


class AvatarGenerateRequest(BaseModel):
    """数字人生成请求"""
    text: str
    voice: str = "sweet_female"
    speech_rate: float = 1.0
    avatar_type: str = "image"


class AvatarTask(BaseModel):
    """数字人任务"""
    task_id: str
    status: str
    progress: int
    created_at: str
    stage: str = ""
    video_url: str | None = None
    error_message: str | None = None


# 根据模式选择服务
avatar_service = None
if AVATAR_MODE == "simple":
    try:
        from project.backend.app.services.simple_avatar import simple_avatar_service
        avatar_service = simple_avatar_service
    except Exception as e:
        print(f"Warning: Simple avatar service not available: {e}")
elif AVATAR_MODE == "cloud":
    try:
        from project.backend.app.services.avatar_service import get_avatar_provider
        avatar_service = get_avatar_provider()
    except Exception as e:
        print(f"Warning: Cloud avatar service not available: {e}")


@router.post("/generate", response_model=AvatarTask)
async def generate_avatar_video(body: AvatarGenerateRequest) -> AvatarTask:
    """生成数字人视频"""
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="请输入文案内容")

    if AVATAR_MODE == "mock" or avatar_service is None:
        # 演示模式
        task_id = f"avatar-{uuid.uuid4().hex[:10]}"
        return AvatarTask(
            task_id=task_id,
            status="succeeded",
            progress=100,
            created_at=datetime.now().isoformat(),
            stage="演示完成",
            video_url=None,
        )

    elif AVATAR_MODE == "simple":
        # 简化模式
        try:
            result = await avatar_service.generate_video(
                text=body.text,
                voice=body.voice,
                rate=body.speech_rate,
            )
            return AvatarTask(
                task_id=result["task_id"],
                status=result["status"],
                progress=result.get("progress", 0),
                created_at=result.get("created_at", datetime.now().isoformat()),
                stage=result.get("stage", ""),
                video_url=f"/api/v1/avatar/download/{result['task_id']}" if result["status"] == "succeeded" else None,
                error_message=result.get("error_message"),
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    else:
        # 云端模式
        try:
            task = await avatar_service.generate_video(
                avatar_image="",
                audio="",
                text=body.text,
                voice=body.voice,
                speech_rate=body.speech_rate,
            )
            return AvatarTask(**task.to_dict())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/{task_id}", response_model=AvatarTask)
async def get_task_status(task_id: str) -> AvatarTask:
    """获取任务状态"""
    if AVATAR_MODE == "mock" or avatar_service is None:
        return AvatarTask(
            task_id=task_id,
            status="succeeded",
            progress=100,
            created_at=datetime.now().isoformat(),
            stage="演示完成",
        )

    try:
        if AVATAR_MODE == "simple":
            result = avatar_service.get_task(task_id)
            if not result:
                raise HTTPException(status_code=404, detail="任务不存在")
            return AvatarTask(
                task_id=result["task_id"],
                status=result["status"],
                progress=result.get("progress", 0),
                created_at=result.get("created_at", datetime.now().isoformat()),
                stage=result.get("stage", ""),
                video_url=f"/api/v1/avatar/download/{task_id}" if result["status"] == "succeeded" else None,
                error_message=result.get("error_message"),
            )
        else:
            task = await avatar_service.get_task_status(task_id)
            return AvatarTask(**task.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download/{task_id}")
async def download_video(task_id: str):
    """下载生成的视频"""
    if AVATAR_MODE != "simple" or avatar_service is None:
        raise HTTPException(status_code=501, detail="当前模式不支持下载")

    result = avatar_service.get_task(task_id)
    if not result:
        raise HTTPException(status_code=404, detail="任务不存在")
    if result["status"] != "succeeded":
        raise HTTPException(status_code=400, detail="视频尚未生成完成")

    video_path = result.get("video_path")
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="视频文件不存在")

    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename=f"avatar_{task_id}.mp4",
    )


@router.get("/voices")
async def list_voices() -> list[dict[str, str]]:
    """获取可用音色列表"""
    if AVATAR_MODE == "simple" and avatar_service:
        return avatar_service.get_voices()
    else:
        return [
            {"id": "sweet_female", "name": "甜美女声", "gender": "female"},
            {"id": "magnetic_male", "name": "磁性男声", "gender": "male"},
            {"id": "youth", "name": "活力青年", "gender": "neutral"},
            {"id": "broadcast", "name": "专业播音", "gender": "neutral"},
            {"id": "customer_service", "name": "亲切客服", "gender": "female"},
        ]


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """获取数字人服务配置"""
    return {
        "mode": AVATAR_MODE,
        "supports_upload": AVATAR_MODE != "mock",
        "supports_download": AVATAR_MODE == "simple",
        "description": {
            "mock": "演示模式 —— 返回模拟数据",
            "simple": "简化模式 —— edge-tts + ffmpeg，适合本地测试",
            "cloud": "云端 API —— 需要配置 API Key",
        }.get(AVATAR_MODE, "未知模式"),
    }
