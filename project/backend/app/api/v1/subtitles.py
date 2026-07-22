"""字幕功能状态 API。

实际识别、校对与导出复用 ``/api/v1/transcriptions``，避免维护两套
faster-whisper 调用链而产生不一致的字幕结果。
"""

from __future__ import annotations

import importlib.metadata
import shutil

from fastapi import APIRouter
from pydantic import BaseModel, Field

from project.backend.app.core.config import ASRMode, ASR_MODE
from src.adapters.subtitle_generator import SubtitleGenerator

router = APIRouter(prefix="/subtitles", tags=["subtitles"])


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class SubtitleStatusResponse(BaseModel):
    """本地字幕工作流可用性。"""

    whisper_available: bool
    version: str | None = None
    supported_formats: list[str] = Field(default_factory=lambda: ["srt", "ass"])
    install_command: str | None = None
    provider_name: str = "faster-whisper"
    ffmpeg_available: bool = False
    asr_mode: str
    supported_models: list[str] = Field(
        default_factory=lambda: ["large-v3-turbo", "base"]
    )
    default_model: str = "large-v3-turbo"
    reason: str | None = None


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("/status", response_model=SubtitleStatusResponse)
def subtitle_status():
    """检查真实本地字幕工作流的依赖状态。"""
    engine_available = SubtitleGenerator.whisper_available()
    ffmpeg_available = shutil.which("ffmpeg") is not None
    local_mode = ASR_MODE == ASRMode.LOCAL
    whisper_available = local_mode and engine_available and ffmpeg_available
    version: str | None = None
    if engine_available:
        try:
            version = importlib.metadata.version("faster-whisper")
        except importlib.metadata.PackageNotFoundError:
            version = None

    reason = None
    install_command = None
    if not local_mode:
        reason = f"当前 ASR_MODE={ASR_MODE.value}，字幕页只提供真实本地识别。"
    elif not engine_available:
        reason = "未安装 faster-whisper。"
        install_command = "pip install faster-whisper"
    elif not ffmpeg_available:
        reason = "未检测到 FFmpeg，无法处理视频文件。"

    return SubtitleStatusResponse(
        whisper_available=whisper_available,
        version=version,
        supported_formats=["srt", "ass"],
        install_command=install_command,
        ffmpeg_available=ffmpeg_available,
        asr_mode=ASR_MODE.value,
        reason=reason,
    )
