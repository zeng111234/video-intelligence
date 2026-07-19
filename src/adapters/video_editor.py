"""FFmpeg 视频编辑适配器。

基于 FFmpeg 实现裁剪、字幕、水印、速度调整、拼接等功能。
不依赖额外 Python 包，仅通过 subprocess 调用 FFmpeg。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from src.models import VideoEditConfig, VideoEditStep, VideoEditStepKind


class VideoEditorError(RuntimeError):
    """视频编辑失败。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class FFmpegVideoEditor:
    """基于 FFmpeg 的视频编辑器实现。"""

    def __init__(
        self,
        *,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
        temp_dir: Path | None = None,
        command_runner=subprocess.run,
    ) -> None:
        self.ffmpeg = ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe = ffprobe_path or shutil.which("ffprobe") or "ffprobe"
        self.temp_dir = temp_dir
        self.command_runner = command_runner

    def capabilities(self) -> dict[str, str | bool]:
        ffmpeg_available = shutil.which(self.ffmpeg) is not None
        return {
            "provider_name": "ffmpeg_local",
            "display_name": "FFmpeg 本地剪辑",
            "enabled": ffmpeg_available,
            "supports_trim": True,
            "supports_subtitle": True,
            "supports_watermark": True,
            "supports_speed": True,
            "supports_resize": True,
            "supports_filter": True,
            "supports_concat": True,
            "supports_transition": False,
            "supports_background_music": True,
        }

    def apply_edit(
        self,
        source_video_path: str,
        config: VideoEditConfig,
        *,
        output_path: str | None = None,
    ) -> str:
        """按配置依次执行剪辑步骤，返回最终输出路径。"""
        source = Path(source_video_path)
        if not source.exists():
            raise VideoEditorError("源视频文件不存在。")
        self._validate_video(source)

        if output_path:
            output = Path(output_path)
        else:
            output = source.parent / f"{source.stem}_edited.{config.output_format}"

        # 按 order 排序步骤
        sorted_steps = sorted(config.steps, key=lambda s: s.order)

        # 逐步骤链式处理
        current_input = source
        for i, step in enumerate(sorted_steps):
            is_last = i == len(sorted_steps) - 1
            if is_last:
                step_output = output
            else:
                step_output = source.parent / f"_temp_step_{i}.{config.output_format}"
            self._apply_step(current_input, step, step_output, config)
            if current_input != source and current_input.exists():
                current_input.unlink(missing_ok=True)
            current_input = step_output

        # 如果没有步骤，直接复制
        if not sorted_steps:
            self._copy_with_reencode(source, output, config)

        if not output.exists():
            raise VideoEditorError("剪辑处理完成但未生成输出文件。")
        return str(output)

    def add_subtitles(
        self,
        video_path: str,
        srt_path: str,
        *,
        style: str = "default",
        output_path: str | None = None,
    ) -> str:
        """为视频添加字幕。"""
        video = Path(video_path)
        srt = Path(srt_path)
        if not video.exists():
            raise VideoEditorError("视频文件不存在。")
        if not srt.exists():
            raise VideoEditorError("字幕文件不存在。")
        output = Path(output_path) if output_path else video.parent / f"{video.stem}_subtitled.mp4"
        subtitle_filter = self._build_subtitle_filter(srt, style)
        cmd = [
            self.ffmpeg,
            "-nostdin",
            "-v", "error",
            "-i", str(video),
            "-vf", subtitle_filter,
            "-c:a", "copy",
            "-y",
            str(output),
        ]
        self._run(cmd, "添加字幕失败。")
        return str(output)

    def add_watermark(
        self,
        video_path: str,
        watermark_path: str,
        *,
        position: str = "bottom_right",
        opacity: float = 0.5,
        output_path: str | None = None,
    ) -> str:
        """为视频添加水印。"""
        video = Path(video_path)
        wm = Path(watermark_path)
        if not video.exists():
            raise VideoEditorError("视频文件不存在。")
        if not wm.exists():
            raise VideoEditorError("水印文件不存在。")
        output = Path(output_path) if output_path else video.parent / f"{video.stem}_watermarked.mp4"
        pos_filter = self._build_watermark_position(position)
        filter_complex = (
            f"[1:v]format=rgba,colorchannelmixer=aa={opacity}[wm];"
            f"[0:v][wm]overlay={pos_filter}[out]"
        )
        cmd = [
            self.ffmpeg,
            "-nostdin",
            "-v", "error",
            "-i", str(video),
            "-i", str(wm),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
            "-c:a", "copy",
            "-y",
            str(output),
        ]
        self._run(cmd, "添加水印失败。")
        return str(output)

    # -- 私有方法 --

    def _apply_step(
        self,
        input_path: Path,
        step: VideoEditStep,
        output_path: Path,
        config: VideoEditConfig,
    ) -> None:
        kind = step.kind
        params = step.params
        if kind == VideoEditStepKind.TRIM:
            self._apply_trim(input_path, output_path, params)
        elif kind == VideoEditStepKind.SUBTITLE:
            style = params.get("style", "default")
            srt_path = params.get("srt_path", "")
            if not srt_path:
                raise VideoEditorError("字幕步骤缺少 srt_path 参数。")
            self.add_subtitles(str(input_path), srt_path, style=style, output_path=str(output_path))
        elif kind == VideoEditStepKind.WATERMARK:
            wm_path = params.get("watermark_path", "")
            position = params.get("position", "bottom_right")
            opacity = float(params.get("opacity", 0.5))
            if not wm_path:
                raise VideoEditorError("水印步骤缺少 watermark_path 参数。")
            self.add_watermark(str(input_path), wm_path, position=position, opacity=opacity, output_path=str(output_path))
        elif kind == VideoEditStepKind.SPEED:
            self._apply_speed(input_path, output_path, params, config)
        elif kind == VideoEditStepKind.RESIZE:
            self._apply_resize(input_path, output_path, params, config)
        elif kind == VideoEditStepKind.FILTER:
            self._apply_filter(input_path, output_path, params)
        elif kind == VideoEditStepKind.BACKGROUND_MUSIC:
            self._apply_bgm(input_path, output_path, params)
        else:
            # 不支持的步骤类型直接复制
            self._copy_with_reencode(input_path, output_path, config)

    def _apply_trim(
        self, input_path: Path, output_path: Path, params: dict[str, Any]
    ) -> None:
        start = params.get("start", 0)
        duration = params.get("duration")
        end = params.get("end")
        cmd = [self.ffmpeg, "-nostdin", "-v", "error", "-ss", str(start)]
        if duration is not None:
            cmd += ["-t", str(duration)]
        elif end is not None:
            cmd += ["-to", str(end)]
        cmd += ["-i", str(input_path), "-c", "copy", "-y", str(output_path)]
        self._run(cmd, "裁剪失败。")

    def _apply_speed(
        self,
        input_path: Path,
        output_path: Path,
        params: dict[str, Any],
        config: VideoEditConfig,
    ) -> None:
        speed = float(params.get("speed", 1.0))
        speed = max(0.25, min(4.0, speed))
        video_filter = f"setpts={1.0 / speed}*PTS"
        audio_filter = f"atempo={speed}"
        # atempo 只支持 0.5-2.0，超出需链式
        if speed > 2.0:
            chain = []
            remaining = speed
            while remaining > 2.0:
                chain.append("atempo=2.0")
                remaining /= 2.0
            chain.append(f"atempo={remaining}")
            audio_filter = ",".join(chain)
        elif speed < 0.5:
            chain = []
            remaining = speed
            while remaining < 0.5:
                chain.append("atempo=0.5")
                remaining /= 0.5
            chain.append(f"atempo={remaining}")
            audio_filter = ",".join(chain)
        cmd = [
            self.ffmpeg, "-nostdin", "-v", "error",
            "-i", str(input_path),
            "-filter_complex", f"[0:v]{video_filter}[v];[0:a]{audio_filter}[a]",
            "-map", "[v]", "-map", "[a]",
            "-y", str(output_path),
        ]
        self._run(cmd, "速度调整失败。")

    def _apply_resize(
        self,
        input_path: Path,
        output_path: Path,
        params: dict[str, Any],
        config: VideoEditConfig,
    ) -> None:
        resolution = params.get("resolution", config.output_resolution)
        w, h = resolution.split("x")
        cmd = [
            self.ffmpeg, "-nostdin", "-v", "error",
            "-i", str(input_path),
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
            "-c:a", "copy",
            "-y", str(output_path),
        ]
        self._run(cmd, "分辨率调整失败。")

    def _apply_filter(
        self, input_path: Path, output_path: Path, params: dict[str, Any]
    ) -> None:
        vf = params.get("vf", "")
        af = params.get("af", "")
        if not vf and not af:
            self._copy_with_reencode(input_path, output_path, VideoEditConfig())
            return
        cmd = [self.ffmpeg, "-nostdin", "-v", "error", "-i", str(input_path)]
        if vf:
            cmd += ["-vf", vf]
        if af:
            cmd += ["-af", af]
        cmd += ["-y", str(output_path)]
        self._run(cmd, "滤镜应用失败。")

    def _apply_bgm(
        self, input_path: Path, output_path: Path, params: dict[str, Any]
    ) -> None:
        bgm_path = params.get("bgm_path", "")
        bgm_volume = float(params.get("bgm_volume", 0.3))
        video_volume = float(params.get("video_volume", 1.0))
        if not bgm_path:
            raise VideoEditorError("背景音乐步骤缺少 bgm_path 参数。")
        bgm = Path(bgm_path)
        if not bgm.exists():
            raise VideoEditorError("背景音乐文件不存在。")
        filter_complex = (
            f"[0:a]volume={video_volume}[a0];"
            f"[1:a]volume={bgm_volume},aloop=loop=-1:size=2e+09[a1];"
            f"[a0][a1]amix=inputs=2:duration=first[aout]"
        )
        cmd = [
            self.ffmpeg, "-nostdin", "-v", "error",
            "-i", str(input_path),
            "-i", str(bgm),
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-shortest",
            "-y", str(output_path),
        ]
        self._run(cmd, "添加背景音乐失败。")

    def _copy_with_reencode(
        self, input_path: Path, output_path: Path, config: VideoEditConfig
    ) -> None:
        cmd = [
            self.ffmpeg, "-nostdin", "-v", "error",
            "-i", str(input_path),
            "-c:v", "libx264", "-preset", "fast",
            "-b:v", config.output_bitrate,
            "-r", str(config.output_fps),
            "-c:a", "aac",
            "-y", str(output_path),
        ]
        self._run(cmd, "视频转码失败。")

    def _build_subtitle_filter(self, srt_path: Path, style: str) -> str:
        srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
        if style == "highlight":
            return (
                f"subtitles='{srt_escaped}':force_style="
                f"'FontSize=24,PrimaryColour=&H00FFFF,OutlineColour=&H000000,"
                f"Outline=2,Shadow=1,Alignment=2,MarginV=30'"
            )
        # 默认样式
        return (
            f"subtitles='{srt_escaped}':force_style="
            f"'FontSize=20,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,"
            f"Outline=2,Shadow=1,Alignment=2,MarginV=30'"
        )

    def _build_watermark_position(self, position: str) -> str:
        mapping = {
            "top_left": "10:10",
            "top_right": "main_w-overlay_w-10:10",
            "bottom_left": "10:main_h-overlay_h-10",
            "bottom_right": "main_w-overlay_w-10:main_h-overlay_h-10",
            "center": "(main_w-overlay_w)/2:(main_h-overlay_h)/2",
        }
        return mapping.get(position, mapping["bottom_right"])

    def _validate_video(self, path: Path) -> None:
        cmd = [
            self.ffprobe, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type",
            "-of", "default=nw=1:nk=1",
            str(path),
        ]
        result = self.command_runner(cmd, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0 or "video" not in result.stdout:
            raise VideoEditorError("输入文件不是有效的视频。")

    def _run(self, cmd: list[str], error_msg: str) -> None:
        result = self.command_runner(cmd, capture_output=True, text=True, timeout=600, check=False)
        if result.returncode != 0:
            detail = (result.stderr or "")[:200]
            raise VideoEditorError(f"{error_msg} {detail}")
