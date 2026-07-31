"""字幕功能状态 API。

实际识别、校对与导出复用 ``/api/v1/transcriptions``，避免维护两套
faster-whisper 调用链而产生不一致的字幕结果。
"""

from __future__ import annotations

import importlib.metadata
import shutil

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from project.backend.app.core.config import ASRMode, ASR_MODE
from project.backend.app.core.deps import get_transcription_service
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
def subtitle_status(service=Depends(get_transcription_service)):
    """检查字幕工作流当前选择的真实识别能力。"""
    if ASR_MODE == ASRMode.CLOUD and service.cloud_runtime is not None:
        capability = service.cloud_runtime.capability()
        enabled = bool(capability["enabled"])
        reason = None
        if not capability["live_ready"]:
            reason = "公司云识别配置不完整，请联系管理员。"
        elif not capability["billing_authorized"]:
            reason = "公司云识别费用尚未授权，请管理员到系统设置确认。"
        return SubtitleStatusResponse(
            whisper_available=enabled,
            version=None,
            supported_formats=["srt", "ass"],
            install_command=None,
            provider_name="阿里云 Fun-ASR",
            ffmpeg_available=True,
            asr_mode=ASR_MODE.value,
            supported_models=["fun-asr"],
            default_model="fun-asr",
            reason=reason,
        )

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
