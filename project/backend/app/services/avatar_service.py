"""数字人服务集成层

支持：
1. Duix-Avatar 本地部署（需要 GPU）
2. Duix-Avatar 远程 API
3. Mock 模式（演示用）
"""

from __future__ import annotations

import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import Any

import httpx


class AvatarMode(StrEnum):
    """数字人运行模式"""
    MOCK = "mock"           # 演示模式
    LOCAL = "local"         # 本地 Duix-Avatar
    REMOTE = "remote"       # 远程 API


class AvatarTask:
    """数字人任务"""
    def __init__(
        self,
        task_id: str,
        status: str = "pending",
        progress: int = 0,
        avatar_type: str = "image",
        audio_type: str = "tts",
        video_url: str | None = None,
        error_message: str | None = None,
    ):
        self.task_id = task_id
        self.status = status
        self.progress = progress
        self.avatar_type = avatar_type
        self.audio_type = audio_type
        self.video_url = video_url
        self.error_message = error_message
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "progress": self.progress,
            "avatar_type": self.avatar_type,
            "audio_type": self.audio_type,
            "video_url": self.video_url,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class BaseAvatarProvider(ABC):
    """数字人提供者基类"""

    @abstractmethod
    async def generate_video(
        self,
        avatar_image: str | bytes,
        audio: str | bytes,
        text: str = "",
        voice: str = "sweet_female",
        speech_rate: float = 1.0,
    ) -> AvatarTask:
        """生成数字人视频"""
        pass

    @abstractmethod
    async def get_task_status(self, task_id: str) -> AvatarTask:
        """获取任务状态"""
        pass

    @abstractmethod
    async def list_voices(self) -> list[dict[str, str]]:
        """获取可用音色列表"""
        pass


class MockAvatarProvider(BaseAvatarProvider):
    """Mock 数字人提供者"""

    def __init__(self):
        self.tasks: dict[str, AvatarTask] = {}

    async def generate_video(
        self,
        avatar_image: str | bytes,
        audio: str | bytes,
        text: str = "",
        voice: str = "sweet_female",
        speech_rate: float = 1.0,
    ) -> AvatarTask:
        task_id = f"avatar-{uuid.uuid4().hex[:10]}"
        task = AvatarTask(
            task_id=task_id,
            status="running",
            progress=0,
        )
        self.tasks[task_id] = task

        # 模拟进度
        import asyncio

        async def simulate():
            for i in range(10, 101, 10):
                await asyncio.sleep(0.5)
                if task_id in self.tasks:
                    self.tasks[task_id].progress = i
                    if i == 100:
                        self.tasks[task_id].status = "succeeded"
                        self.tasks[task_id].video_url = f"/api/v1/avatar/download/{task_id}"

        asyncio.create_task(simulate())
        return task

    async def get_task_status(self, task_id: str) -> AvatarTask:
        if task_id not in self.tasks:
            raise ValueError(f"任务不存在: {task_id}")
        return self.tasks[task_id]

    async def list_voices(self) -> list[dict[str, str]]:
        return [
            {"id": "sweet_female", "name": "甜美女声", "gender": "female"},
            {"id": "magnetic_male", "name": "磁性男声", "gender": "male"},
            {"id": "youth", "name": "活力青年", "gender": "neutral"},
            {"id": "broadcast", "name": "专业播音", "gender": "neutral"},
            {"id": "customer_service", "name": "亲切客服", "gender": "female"},
        ]


class DuixAvatarProvider(BaseAvatarProvider):
    """Duix-Avatar 提供者

    集成 Duix-Avatar 的 API：
    - 音频合成: http://127.0.0.1:18180/v1/invoke
    - 视频合成: http://127.0.0.1:8383/easy/submit
    - 进度查询: http://127.0.0.1:8383/easy/query
    """

    def __init__(
        self,
        tts_url: str = "http://127.0.0.1:18180",
        video_url: str = "http://127.0.0.1:8383",
    ):
        self.tts_url = tts_url
        self.video_url = video_url
        self.tasks: dict[str, AvatarTask] = {}

    async def generate_video(
        self,
        avatar_image: str | bytes,
        audio: str | bytes,
        text: str = "",
        voice: str = "sweet_female",
        speech_rate: float = 1.0,
    ) -> AvatarTask:
        task_id = f"avatar-{uuid.uuid4().hex[:10]}"

        try:
            # Step 1: TTS 合成音频
            if text:
                audio_path = await self._synthesize_speech(text, voice, speech_rate)
            else:
                audio_path = audio if isinstance(audio, str) else None

            # Step 2: 视频合成
            if audio_path:
                video_result = await self._synthesize_video(
                    audio_path,
                    avatar_image if isinstance(avatar_image, str) else "",
                    task_id,
                )

                task = AvatarTask(
                    task_id=task_id,
                    status="running",
                    progress=10,
                )
                self.tasks[task_id] = task
                return task

        except Exception as e:
            task = AvatarTask(
                task_id=task_id,
                status="failed",
                error_message=str(e),
            )
            self.tasks[task_id] = task
            return task

        # Fallback
        task = AvatarTask(task_id=task_id, status="pending")
        self.tasks[task_id] = task
        return task

    async def _synthesize_speech(self, text: str, voice: str, rate: float) -> str:
        """调用 TTS 服务合成音频"""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.tts_url}/v1/invoke",
                json={
                    "speaker": str(uuid.uuid4()),
                    "text": text,
                    "format": "wav",
                    "topP": 0.7,
                    "max_new_tokens": 1024,
                    "chunk_length": 100,
                    "repetition_penalty": 1.2,
                    "temperature": 0.7,
                    "need_asr": False,
                    "streaming": False,
                    "is_fixed_seed": 0,
                    "is_norm": 0,
                },
                timeout=60,
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("audio_path", "")

    async def _synthesize_video(
        self, audio_path: str, video_path: str, code: str
    ) -> dict[str, Any]:
        """调用视频合成服务"""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.video_url}/easy/submit",
                json={
                    "audio_url": audio_path,
                    "video_url": video_path,
                    "code": code,
                    "chaofen": 0,
                    "watermark_switch": 0,
                    "pn": 1,
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_task_status(self, task_id: str) -> AvatarTask:
        if task_id not in self.tasks:
            raise ValueError(f"任务不存在: {task_id}")

        task = self.tasks[task_id]

        # 查询 Duix-Avatar 进度
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.video_url}/easy/query",
                    params={"code": task_id},
                    timeout=10,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    task.progress = result.get("progress", task.progress)
                    if result.get("status") == "completed":
                        task.status = "succeeded"
                        task.video_url = result.get("video_url")
                    elif result.get("status") == "failed":
                        task.status = "failed"
                        task.error_message = result.get("error", "合成失败")
        except Exception:
            pass  # 使用本地状态

        task.updated_at = datetime.now().isoformat()
        return task

    async def list_voices(self) -> list[dict[str, str]]:
        return [
            {"id": "sweet_female", "name": "甜美女声", "gender": "female"},
            {"id": "magnetic_male", "name": "磁性男声", "gender": "male"},
            {"id": "youth", "name": "活力青年", "gender": "neutral"},
            {"id": "broadcast", "name": "专业播音", "gender": "neutral"},
            {"id": "customer_service", "name": "亲切客服", "gender": "female"},
        ]


def get_avatar_provider() -> BaseAvatarProvider:
    """获取数字人提供者"""
    mode = os.getenv("AVATAR_MODE", "mock").lower()

    if mode == "local":
        tts_url = os.getenv("DUIX_TTS_URL", "http://127.0.0.1:18180")
        video_url = os.getenv("DUIX_VIDEO_URL", "http://127.0.0.1:8383")
        return DuixAvatarProvider(tts_url, video_url)
    elif mode == "remote":
        tts_url = os.getenv("DUIX_TTS_URL", "http://127.0.0.1:18180")
        video_url = os.getenv("DUIX_VIDEO_URL", "http://127.0.0.1:8383")
        return DuixAvatarProvider(tts_url, video_url)
    else:
        return MockAvatarProvider()
