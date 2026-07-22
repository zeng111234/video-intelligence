"""视频剪辑 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_video_editing_service

router = APIRouter(prefix="/api/v1/video-editor", tags=["video-editor"])


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class VideoEditStepRequest(BaseModel):
    """单个剪辑步骤。"""

    step_id: str | None = Field(None, description="步骤唯一标识（留空自动生成）")
    kind: str = Field(
        ...,
        description=(
            "步骤类型：trim | subtitle | watermark | transition | "
            "background_music | speed | resize | filter | concat | "
            "ai_subtitle | ai_volume_norm | ai_enhance | ai_silence_trim"
        ),
    )
    params: dict[str, Any] = Field(default_factory=dict, description="步骤参数")
    order: int = Field(0, ge=0, description="执行顺序")


class VideoEditConfigRequest(BaseModel):
    """完整剪辑配置（含 AI 步骤）。"""

    steps: list[VideoEditStepRequest] = Field(
        default_factory=list, description="剪辑步骤列表"
    )
    output_format: str = Field("mp4", description="输出格式")
    output_resolution: str = Field("1080x1920", description="输出分辨率")
    output_fps: int = Field(30, ge=15, le=60, description="输出帧率")
    output_bitrate: str = Field("4M", description="输出码率")


class VideoEditRequest(BaseModel):
    """视频剪辑请求。

    兼容模式：仅传 ``source_video_path`` + ``subtitle_text`` 时行为与旧版一致。
    完整模式：传 ``edit_config`` 可配置 AI 步骤和输出参数。
    """

    source_video_path: str = Field(..., description="源视频路径")
    subtitle_text: str | None = Field(None, description="字幕文本（兼容字段）")
    subtitle_style: str = Field("default", description="字幕样式")
    edit_config: VideoEditConfigRequest | None = Field(
        None,
        description="完整剪辑配置（含 AI 步骤）。不传时使用默认配置。",
    )


class VideoEditResponse(BaseModel):
    task_id: str
    status: str
    result_path: str | None = None
    result_size_bytes: int | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _build_edit_config(req_config: VideoEditConfigRequest | None):
    """将 API 请求模型转换为 src.models.VideoEditConfig。"""
    if req_config is None:
        return None

    from src.models import VideoEditConfig, VideoEditStep, VideoEditStepKind

    valid_kinds = {k.value for k in VideoEditStepKind}
    steps = []
    for s in req_config.steps:
        if s.kind not in valid_kinds:
            raise HTTPException(
                status_code=422,
                detail=f"不支持的步骤类型: {s.kind}。支持的类型: {', '.join(sorted(valid_kinds))}",
            )
        steps.append(
            VideoEditStep(
                step_id=s.step_id or f"step-{__import__('uuid').uuid4().hex[:8]}",
                kind=VideoEditStepKind(s.kind),
                params=s.params,
                order=s.order,
            )
        )

    return VideoEditConfig(
        steps=steps,
        output_format=req_config.output_format,
        output_resolution=req_config.output_resolution,
        output_fps=req_config.output_fps,
        output_bitrate=req_config.output_bitrate,
    )


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.post("/edit", response_model=VideoEditResponse)
def edit_video(
    body: VideoEditRequest,
    service=Depends(get_video_editing_service),
):
    """视频剪辑。

    - 仅传 source_video_path + subtitle_text 时为兼容模式。
    - 传 edit_config 可使用完整 AI 智能剪辑步骤。
    """
    if not Path(body.source_video_path).exists():
        raise HTTPException(status_code=400, detail="源视频文件不存在。")
    try:
        edit_config = _build_edit_config(body.edit_config)
        task = service.edit_video(
            source_video_path=body.source_video_path,
            subtitle_text=body.subtitle_text,
            subtitle_style=body.subtitle_style,
            edit_config=edit_config,
        )
    except HTTPException:
        raise
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
    """获取视频编辑器能力（含 AI 步骤支持状态）。"""
    return service.capabilities()


@router.get("/step-kinds")
def step_kinds():
    """返回所有支持的步骤类型及其参数说明。"""
    kinds = {
        "trim": {
            "label": "裁剪",
            "category": "basic",
            "params": {
                "start": {"type": "number", "label": "起始秒", "default": 0},
                "end": {"type": "number", "label": "结束秒（可选）"},
                "duration": {"type": "number", "label": "持续秒（可选）"},
            },
        },
        "subtitle": {
            "label": "字幕",
            "category": "basic",
            "params": {
                "srt_path": {
                    "type": "string",
                    "label": "SRT 文件路径",
                    "required": True,
                },
                "style": {"type": "string", "label": "样式", "default": "default"},
            },
        },
        "watermark": {
            "label": "水印",
            "category": "basic",
            "params": {
                "watermark_path": {
                    "type": "string",
                    "label": "水印图片路径",
                    "required": True,
                },
                "position": {
                    "type": "select",
                    "label": "位置",
                    "options": [
                        "top_left",
                        "top_right",
                        "bottom_left",
                        "bottom_right",
                        "center",
                    ],
                    "default": "bottom_right",
                },
                "opacity": {
                    "type": "number",
                    "label": "透明度",
                    "default": 0.5,
                    "min": 0,
                    "max": 1,
                },
            },
        },
        "speed": {
            "label": "速度调整",
            "category": "basic",
            "params": {
                "speed": {
                    "type": "number",
                    "label": "倍速",
                    "default": 1.0,
                    "min": 0.25,
                    "max": 4.0,
                },
            },
        },
        "resize": {
            "label": "分辨率调整",
            "category": "basic",
            "params": {
                "resolution": {
                    "type": "string",
                    "label": "目标分辨率",
                    "default": "1080x1920",
                },
            },
        },
        "filter": {
            "label": "滤镜",
            "category": "basic",
            "params": {
                "filter_string": {
                    "type": "string",
                    "label": "FFmpeg 滤镜",
                    "required": True,
                },
            },
        },
        "background_music": {
            "label": "背景音乐",
            "category": "basic",
            "params": {
                "audio_path": {"type": "string", "label": "音频路径", "required": True},
                "volume": {
                    "type": "number",
                    "label": "音量",
                    "default": 0.3,
                    "min": 0,
                    "max": 1,
                },
            },
        },
        "concat": {
            "label": "拼接",
            "category": "basic",
            "params": {
                "video_paths": {
                    "type": "array",
                    "label": "视频路径列表",
                    "required": True,
                },
            },
        },
        "ai_subtitle": {
            "label": "AI 自动字幕",
            "category": "ai",
            "requires": "faster-whisper 或 whisper CLI",
            "params": {
                "model": {
                    "type": "select",
                    "label": "Whisper 模型",
                    "options": [
                        "tiny",
                        "base",
                        "small",
                        "medium",
                        "large",
                        "large-v2",
                        "large-v3",
                    ],
                    "default": "base",
                },
                "language": {
                    "type": "select",
                    "label": "语言",
                    "options": ["zh", "en", "ja", "ko"],
                    "default": "zh",
                },
                "style": {"type": "string", "label": "字幕样式", "default": "default"},
            },
        },
        "ai_volume_norm": {
            "label": "AI 音量标准化",
            "category": "ai",
            "requires": "ffmpeg",
            "params": {
                "target_i": {
                    "type": "number",
                    "label": "目标响度 (LUFS)",
                    "default": -16,
                    "min": -70,
                    "max": -5,
                },
                "target_lra": {
                    "type": "number",
                    "label": "响度范围",
                    "default": 11,
                    "min": 1,
                    "max": 50,
                },
                "target_tp": {
                    "type": "number",
                    "label": "真峰值 (dBTP)",
                    "default": -1.5,
                    "min": -9,
                    "max": 0,
                },
            },
        },
        "ai_enhance": {
            "label": "AI 画面增强",
            "category": "ai",
            "requires": "ffmpeg",
            "params": {
                "brightness": {
                    "type": "number",
                    "label": "亮度",
                    "default": 0.06,
                    "min": -1,
                    "max": 1,
                },
                "contrast": {
                    "type": "number",
                    "label": "对比度",
                    "default": 1.1,
                    "min": 0,
                    "max": 2,
                },
                "saturation": {
                    "type": "number",
                    "label": "饱和度",
                    "default": 1.15,
                    "min": 0,
                    "max": 3,
                },
                "sharpen": {
                    "type": "number",
                    "label": "锐化",
                    "default": 1.5,
                    "min": 0,
                    "max": 5,
                },
                "denoise": {
                    "type": "number",
                    "label": "降噪",
                    "default": 3,
                    "min": 0,
                    "max": 10,
                },
            },
        },
        "ai_silence_trim": {
            "label": "AI 静音裁剪",
            "category": "ai",
            "requires": "ffmpeg",
            "params": {
                "noise_threshold": {
                    "type": "number",
                    "label": "噪声阈值 (dB)",
                    "default": -30,
                    "min": -80,
                    "max": 0,
                },
                "min_duration": {
                    "type": "number",
                    "label": "最短静音时长 (秒)",
                    "default": 0.5,
                    "min": 0.1,
                    "max": 10,
                },
                "keep_padding": {
                    "type": "number",
                    "label": "保留间距 (秒)",
                    "default": 0.1,
                    "min": 0,
                    "max": 2,
                },
            },
        },
    }
    return kinds
