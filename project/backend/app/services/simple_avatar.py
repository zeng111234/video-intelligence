"""简化数字人服务

使用 edge-tts 生成语音，配合图片生成口播视频
无需复杂依赖，适合快速验证和本地测试
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import edge_tts

# 音色映射
VOICE_MAP = {
    "sweet_female": "zh-CN-XiaoxiaoNeural",
    "magnetic_male": "zh-CN-YunxiNeural",
    "youth": "zh-CN-YunyangNeural",
    "broadcast": "zh-CN-XiaoyiNeural",
    "customer_service": "zh-CN-XiaoxiaoNeural",
}

# 输出目录
OUTPUT_DIR = Path("data/avatar_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class SimpleAvatarService:
    """简化数字人服务"""

    def __init__(self):
        self.tasks: dict[str, dict[str, Any]] = {}

    async def generate_speech(
        self,
        text: str,
        voice: str = "sweet_female",
        rate: float = 1.0,
    ) -> str:
        """生成语音文件"""
        voice_name = VOICE_MAP.get(voice, VOICE_MAP["sweet_female"])
        output_file = OUTPUT_DIR / f"speech_{uuid.uuid4().hex[:8]}.mp3"

        # 调整语速
        rate_str = f"+{int((rate - 1) * 100)}%" if rate >= 1 else f"{int((rate - 1) * 100)}%"

        communicate = edge_tts.Communicate(text, voice_name, rate=rate_str)
        await communicate.save(str(output_file))

        return str(output_file)

    async def generate_video(
        self,
        text: str,
        voice: str = "sweet_female",
        rate: float = 1.0,
        avatar_image: str | None = None,
    ) -> dict[str, Any]:
        """生成数字人视频（简化版：图片+音频）"""
        task_id = f"avatar-{uuid.uuid4().hex[:10]}"

        # 创建任务记录
        self.tasks[task_id] = {
            "task_id": task_id,
            "status": "running",
            "progress": 0,
            "created_at": datetime.now().isoformat(),
            "text": text,
            "voice": voice,
        }

        try:
            # Step 1: 生成语音
            self.tasks[task_id]["progress"] = 30
            self.tasks[task_id]["stage"] = "生成语音中..."
            audio_path = await self.generate_speech(text, voice, rate)

            # Step 2: 生成视频（简化版：使用默认背景+音频）
            self.tasks[task_id]["progress"] = 70
            self.tasks[task_id]["stage"] = "合成视频中..."

            # 如果有图片，使用图片作为封面；否则使用默认
            if avatar_image and os.path.exists(avatar_image):
                video_path = await self._create_video_with_image(
                    avatar_image, audio_path, task_id
                )
            else:
                video_path = await self._create_default_video(audio_path, task_id)

            # 完成
            self.tasks[task_id].update({
                "status": "succeeded",
                "progress": 100,
                "stage": "完成",
                "video_path": video_path,
                "audio_path": audio_path,
            })

        except Exception as e:
            self.tasks[task_id].update({
                "status": "failed",
                "error_message": str(e),
            })

        return self.tasks[task_id]

    async def _create_video_with_image(
        self, image_path: str, audio_path: str, task_id: str
    ) -> str:
        """使用图片和音频创建视频"""
        try:
            output_path = str(OUTPUT_DIR / f"video_{task_id}.mp4")

            # 使用 ffmpeg 合成视频
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", image_path,
                "-i", audio_path,
                "-c:v", "libx264",
                "-tune", "stillimage",
                "-c:a", "aac",
                "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-shortest",
                output_path,
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()

            if os.path.exists(output_path):
                return output_path
            else:
                raise Exception("视频生成失败")

        except Exception as e:
            raise Exception(f"创建视频失败: {str(e)}")

    async def _create_default_video(self, audio_path: str, task_id: str) -> str:
        """创建默认视频（纯色背景+音频）"""
        try:
            import numpy as np
            import cv2

            output_path = str(OUTPUT_DIR / f"video_{task_id}.mp4")

            # 创建简单的渐变背景视频
            fps = 30
            width, height = 720, 1280  # 竖屏

            # 获取音频时长
            duration = await self._get_audio_duration(audio_path)
            total_frames = int(duration * fps)

            # 创建视频写入器
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

            for i in range(total_frames):
                # 创建渐变背景
                frame = np.zeros((height, width, 3), dtype=np.uint8)

                # 渐变颜色
                progress = i / total_frames
                r = int(100 + 50 * progress)
                g = int(50 + 100 * progress)
                b = int(150 + 50 * progress)

                frame[:, :] = (b, g, r)  # BGR

                # 添加文字提示
                text = "数字人视频生成中..."
                font = cv2.FONT_HERSHEY_SIMPLEX
                text_size = cv2.getTextSize(text, font, 1, 2)[0]
                text_x = (width - text_size[0]) // 2
                text_y = height // 2
                cv2.putText(frame, text, (text_x, text_y), font, 1, (255, 255, 255), 2)

                writer.write(frame)

            writer.release()

            # 合并音频
            final_path = str(OUTPUT_DIR / f"final_{task_id}.mp4")
            cmd = [
                "ffmpeg", "-y",
                "-i", output_path,
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                final_path,
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()

            # 清理临时文件
            if os.path.exists(output_path):
                os.remove(output_path)

            return final_path if os.path.exists(final_path) else output_path

        except Exception as e:
            raise Exception(f"创建默认视频失败: {str(e)}")

    async def _get_audio_duration(self, audio_path: str) -> float:
        """获取音频时长"""
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()

            return float(stdout.decode().strip())
        except Exception:
            return 10.0  # 默认 10 秒

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """获取任务状态"""
        return self.tasks.get(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        """列出所有任务"""
        return list(self.tasks.values())

    def get_voices(self) -> list[dict[str, str]]:
        """获取可用音色"""
        return [
            {"id": "sweet_female", "name": "甜美女声", "gender": "female"},
            {"id": "magnetic_male", "name": "磁性男声", "gender": "male"},
            {"id": "youth", "name": "活力青年", "gender": "neutral"},
            {"id": "broadcast", "name": "专业播音", "gender": "neutral"},
            {"id": "customer_service", "name": "亲切客服", "gender": "female"},
        ]


# 全局实例
simple_avatar_service = SimpleAvatarService()
