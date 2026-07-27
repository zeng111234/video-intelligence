"""FFmpeg 视频编辑适配器。

基于 FFmpeg 实现裁剪、字幕、水印、速度调整、拼接等功能。
支持 AI 智能剪辑步骤：自动字幕、音量标准化、画面增强、静音裁剪。
不依赖额外 Python 包，仅通过 subprocess 调用 FFmpeg。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

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
        subtitle_available = False
        if ffmpeg_available:
            try:
                from src.adapters.subtitle_generator import SubtitleGenerator

                subtitle_available = (
                    SubtitleGenerator.whisper_available()
                    or shutil.which("whisper") is not None
                )
            except Exception:
                subtitle_available = False
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
            # AI 智能剪辑
            "supports_ai_subtitle": subtitle_available,
            "supports_ai_volume_norm": ffmpeg_available,
            "supports_ai_enhance": ffmpeg_available,
            "supports_ai_silence_trim": ffmpeg_available,
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
        output = (
            Path(output_path)
            if output_path
            else video.parent / f"{video.stem}_subtitled.mp4"
        )
        subtitle_filter = self._build_subtitle_filter(srt, style)
        cmd = [
            self.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            subtitle_filter,
            "-c:a",
            "copy",
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
        output = (
            Path(output_path)
            if output_path
            else video.parent / f"{video.stem}_watermarked.mp4"
        )
        pos_filter = self._build_watermark_position(position)
        filter_complex = (
            f"[1:v]format=rgba,colorchannelmixer=aa={opacity}[wm];"
            f"[0:v][wm]overlay={pos_filter}[out]"
        )
        cmd = [
            self.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(video),
            "-i",
            str(wm),
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-map",
            "0:a?",
            "-c:a",
            "copy",
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
            self.add_subtitles(
                str(input_path), srt_path, style=style, output_path=str(output_path)
            )
        elif kind == VideoEditStepKind.WATERMARK:
            wm_path = params.get("watermark_path", "")
            position = params.get("position", "bottom_right")
            opacity = float(params.get("opacity", 0.5))
            if not wm_path:
                raise VideoEditorError("水印步骤缺少 watermark_path 参数。")
            self.add_watermark(
                str(input_path),
                wm_path,
                position=position,
                opacity=opacity,
                output_path=str(output_path),
            )
        elif kind == VideoEditStepKind.SPEED:
            self._apply_speed(input_path, output_path, params, config)
        elif kind == VideoEditStepKind.RESIZE:
            self._apply_resize(input_path, output_path, params, config)
        elif kind == VideoEditStepKind.FILTER:
            self._apply_filter(input_path, output_path, params)
        elif kind == VideoEditStepKind.BACKGROUND_MUSIC:
            self._apply_bgm(input_path, output_path, params)
        # AI 智能剪辑步骤
        elif kind == VideoEditStepKind.AI_SUBTITLE:
            self._apply_ai_subtitle(input_path, output_path, params)
        elif kind == VideoEditStepKind.AI_VOLUME_NORM:
            self._apply_ai_volume_norm(input_path, output_path, params)
        elif kind == VideoEditStepKind.AI_ENHANCE:
            self._apply_ai_enhance(input_path, output_path, params)
        elif kind == VideoEditStepKind.AI_SILENCE_TRIM:
            self._apply_ai_silence_trim(input_path, output_path, params, config)
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
            self.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-filter_complex",
            f"[0:v]{video_filter}[v];[0:a]{audio_filter}[a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-y",
            str(output_path),
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
            self.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-vf",
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
            "-c:a",
            "copy",
            "-y",
            str(output_path),
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
        bgm_path = params.get("bgm_path") or params.get("audio_path", "")
        bgm_volume = float(params.get("bgm_volume", params.get("volume", 0.3)))
        video_volume = float(params.get("video_volume", 1.0))
        ducking = bool(params.get("ducking", True))
        if not bgm_path:
            raise VideoEditorError("背景音乐步骤缺少 bgm_path 参数。")
        bgm = Path(bgm_path)
        if not bgm.exists():
            raise VideoEditorError("背景音乐文件不存在。")
        has_voice = self._has_audio_stream(input_path)
        duration = self._probe_duration_seconds(input_path)
        fade_out_start = max(duration - 0.8, 0)
        music_filters = (
            f"loudnorm=I=-23:TP=-2:LRA=7,"
            f"afade=t=in:st=0:d=0.6,"
            f"afade=t=out:st={fade_out_start:.3f}:d={min(0.8, duration):.3f},"
            f"volume={bgm_volume}"
        )
        if not has_voice:
            filter_complex = f"[1:a]{music_filters}[aout]"
        elif ducking:
            filter_complex = (
                f"[0:a]asplit=2[voice][side];"
                f"[1:a]{music_filters}[music];"
                f"[music][side]sidechaincompress=threshold=0.035:ratio=8:"
                f"attack=20:release=500[ducked];"
                f"[voice]volume={video_volume}[voicelevel];"
                f"[voicelevel][ducked]amix=inputs=2:duration=first[aout]"
            )
        else:
            filter_complex = (
                f"[0:a]volume={video_volume}[voice];"
                f"[1:a]{music_filters}[music];"
                f"[voice][music]amix=inputs=2:duration=first[aout]"
            )
        cmd = [
            self.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-stream_loop",
            "-1",
            "-i",
            str(bgm),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-y",
            str(output_path),
        ]
        self._run(cmd, "添加背景音乐失败。")

    def _has_audio_stream(self, path: Path) -> bool:
        result = self.command_runner(
            [
                self.ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0 and bool((result.stdout or "").strip())

    def _probe_duration_seconds(self, path: Path) -> float:
        result = self.command_runner(
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        try:
            duration = float((result.stdout or "").strip())
        except (TypeError, ValueError):
            duration = 0
        if result.returncode != 0 or duration <= 0:
            raise VideoEditorError("无法读取视频时长，不能自动调整背景音乐。")
        return duration

    # -- AI 智能剪辑方法 --

    def _apply_ai_subtitle(
        self, input_path: Path, output_path: Path, params: dict[str, Any]
    ) -> None:
        """基于 Whisper 的自动字幕生成。

        优先使用 faster-whisper Python API（SubtitleGenerator），
        如果不可用则回退到 whisper CLI，
        两者都不可用时抛出明确错误。

        params:
            model: Whisper 模型名，默认 base
            language: 语言代码，默认 zh
            style: 字幕样式，默认 default
        """
        model = params.get("model", "base")
        language = params.get("language", "zh")
        style = params.get("style", "default")

        srt_content = self._try_generate_subtitle_with_api(
            input_path, model=model, language=language
        )

        if srt_content is None:
            # API 不可用，回退到 CLI
            srt_content = self._try_generate_subtitle_with_cli(
                input_path, model=model, language=language
            )

        if srt_content is None:
            raise VideoEditorError(
                "字幕生成失败：faster-whisper 和 whisper CLI 均不可用。"
                "请运行 pip install faster-whisper 或 pip install openai-whisper。",
                retryable=False,
            )

        with tempfile.TemporaryDirectory(prefix="crow5_ai_sub_") as tmp:
            srt_path = Path(tmp) / "subtitle.srt"
            srt_path.write_text(srt_content, encoding="utf-8")

            # 烧入字幕
            subtitle_filter = self._build_subtitle_filter(srt_path, style)
            cmd = [
                self.ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(input_path),
                "-vf",
                subtitle_filter,
                "-c:a",
                "copy",
                "-y",
                str(output_path),
            ]
            self._run(cmd, "AI 字幕烧入失败。")

    def _try_generate_subtitle_with_api(
        self,
        input_path: Path,
        *,
        model: str = "base",
        language: str = "zh",
    ) -> str | None:
        """尝试使用 faster-whisper Python API 生成 SRT 内容。

        Returns:
            SRT 内容字符串，如果 API 不可用则返回 None
        """
        try:
            from src.adapters.subtitle_generator import SubtitleGenerator
        except ImportError:
            return None

        if not SubtitleGenerator.whisper_available():
            return None

        try:
            generator = SubtitleGenerator(model_name=model)
            segments = generator.generate_segments(str(input_path), language=language)
            # 内联生成 SRT 内容
            lines: list[str] = []
            for i, seg in enumerate(segments, start=1):
                start_ts = SubtitleGenerator._format_srt_time(seg.start)
                end_ts = SubtitleGenerator._format_srt_time(seg.end)
                lines.append(f"{i}")
                lines.append(f"{start_ts} --> {end_ts}")
                lines.append(seg.text)
                lines.append("")
            return "\n".join(lines)
        except Exception:
            # API 调用失败，返回 None 以便回退到 CLI
            return None

    def _try_generate_subtitle_with_cli(
        self,
        input_path: Path,
        *,
        model: str = "base",
        language: str = "zh",
    ) -> str | None:
        """尝试使用 whisper CLI 生成 SRT 内容。

        Returns:
            SRT 内容字符串，如果 CLI 不可用则返回 None
        """
        whisper_bin = shutil.which("whisper")
        if not whisper_bin:
            return None

        with tempfile.TemporaryDirectory(prefix="crow5_ai_sub_cli_") as tmp:
            # 提取音频
            audio_path = Path(tmp) / "audio.wav"
            extract_cmd = [
                self.ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(input_path),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-y",
                str(audio_path),
            ]
            result = self.command_runner(
                extract_cmd, capture_output=True, text=True, timeout=300, check=False
            )
            if result.returncode != 0 or not audio_path.exists():
                return None

            # Whisper 生成 SRT
            whisper_cmd = [
                whisper_bin,
                str(audio_path),
                "--model",
                model,
                "--language",
                language,
                "--output_format",
                "srt",
                "--output_dir",
                tmp,
            ]
            result = self.command_runner(
                whisper_cmd,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            if result.returncode != 0:
                return None

            srt_path = Path(tmp) / "audio.srt"
            if not srt_path.exists():
                alt_srt = Path(tmp) / f"{audio_path.stem}.srt"
                if alt_srt.exists():
                    srt_path = alt_srt
                else:
                    return None

            return srt_path.read_text(encoding="utf-8")

    def _apply_ai_volume_norm(
        self, input_path: Path, output_path: Path, params: dict[str, Any]
    ) -> None:
        """音量标准化（EBU R128 loudnorm）。

        使用 FFmpeg loudnorm 滤镜实现两遍标准化：
        第一遍分析响度，第二遍应用校正。

        params:
            target_i: 目标响度 LUFS，默认 -16（适合短视频）
            target_lra: 目标响度范围，默认 11
            target_tp: 目标真峰值 dBTP，默认 -1.5
        """
        target_i = float(params.get("target_i", -16))
        target_lra = float(params.get("target_lra", 11))
        target_tp = float(params.get("target_tp", -1.5))

        # 第一遍：分析
        analyze_cmd = [
            self.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-af",
            (
                f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}:print_format=json"
            ),
            "-f",
            "null",
            "-",
        ]
        result = self.command_runner(
            analyze_cmd, capture_output=True, text=True, timeout=600, check=False
        )
        if result.returncode != 0:
            detail = (result.stderr or "")[:200]
            raise VideoEditorError(f"音量分析失败。 {detail}")

        # 解析 loudnorm JSON 输出
        stderr = result.stderr or ""
        measured_i = target_i
        measured_lra = target_lra
        measured_tp = target_tp
        measured_thresh = -24.0
        offset = 0.0

        try:
            # loudnorm 输出在 stderr 最后一个 JSON 块
            json_matches = re.findall(r'\{[^{}]*"input_i"[^{}]*\}', stderr)
            if json_matches:
                stats = json.loads(json_matches[-1])
                measured_i = float(stats.get("input_i", target_i))
                measured_lra = float(stats.get("input_lra", target_lra))
                measured_tp = float(stats.get("input_tp", target_tp))
                measured_thresh = float(stats.get("input_thresh", -24.0))
                offset = float(stats.get("target_offset", 0.0))
        except (json.JSONDecodeError, ValueError, KeyError):
            pass  # 降级使用默认值

        # 第二遍：应用校正
        norm_filter = (
            f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}"
            f":measured_I={measured_i}:measured_LRA={measured_lra}"
            f":measured_TP={measured_tp}:measured_thresh={measured_thresh}"
            f":offset={offset}:linear=true:print_format=summary"
        )
        normalize_cmd = [
            self.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-af",
            norm_filter,
            "-c:v",
            "copy",
            "-y",
            str(output_path),
        ]
        self._run(normalize_cmd, "音量标准化失败。")

    def _apply_ai_enhance(
        self, input_path: Path, output_path: Path, params: dict[str, Any]
    ) -> None:
        """画面增强（亮度/对比度/锐化/降噪）。

        使用 FFmpeg 滤镜组合实现自动画质提升：
        - eq: 亮度 (brightness) + 对比度 (contrast) + 饱和度 (saturation)
        - unsharp: 锐化
        - hqdn3d: 时空降噪

        params:
            brightness: 亮度调整，默认 0.06（范围 -1~1）
            contrast: 对比度，默认 1.1（范围 0~2）
            saturation: 饱和度，默认 1.15（范围 0~3）
            sharpen: 锐化强度，默认 1.5（范围 0~5）
            denoise: 降噪强度，默认 3（范围 0~10）
        """
        brightness = float(params.get("brightness", 0.06))
        contrast = float(params.get("contrast", 1.1))
        saturation = float(params.get("saturation", 1.15))
        sharpen = float(params.get("sharpen", 1.5))
        denoise = int(params.get("denoise", 3))

        filters: list[str] = []

        # 亮度 / 对比度 / 饱和度
        filters.append(
            f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}"
        )

        # 锐化 (unsharp)
        if sharpen > 0:
            filters.append(f"unsharp=5:5:{sharpen}:5:5:{sharpen * 0.5}")

        # 降噪 (hqdn3d)
        if denoise > 0:
            filters.append(f"hqdn3d={denoise}:{denoise}:{denoise}:{denoise}")

        vf = ",".join(filters)

        cmd = [
            self.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-vf",
            vf,
            "-c:a",
            "copy",
            "-y",
            str(output_path),
        ]
        self._run(cmd, "画面增强失败。")

    def _apply_ai_silence_trim(
        self,
        input_path: Path,
        output_path: Path,
        params: dict[str, Any],
        config: VideoEditConfig,
    ) -> None:
        """智能静音裁剪。

        流程：
        1. 用 silencedetect 检测静音段
        2. 计算非静音段时间范围
        3. 提取非静音片段并拼接

        params:
            noise_threshold: 静音阈值 dB，默认 -30
            min_duration: 最短静音时长秒，默认 0.5
            keep_padding: 静音段两端保留的秒数，默认 0.1
        """
        noise_db = float(params.get("noise_threshold", -30))
        min_duration = float(params.get("min_duration", 0.5))
        keep_padding = float(params.get("keep_padding", 0.1))

        # 1. 获取视频时长
        duration = self._get_video_duration(input_path)

        # 2. 检测静音段
        detect_cmd = [
            self.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_duration}",
            "-f",
            "null",
            "-",
        ]
        result = self.command_runner(
            detect_cmd, capture_output=True, text=True, timeout=600, check=False
        )
        if result.returncode != 0:
            detail = (result.stderr or "")[:200]
            raise VideoEditorError(f"静音检测失败。 {detail}")

        # 3. 解析静音段
        silence_starts: list[float] = []
        silence_ends: list[float] = []
        for match in re.finditer(r"silence_start:\s*([\d.]+)", result.stderr or ""):
            silence_starts.append(float(match.group(1)))
        for match in re.finditer(r"silence_end:\s*([\d.]+)", result.stderr or ""):
            silence_ends.append(float(match.group(1)))

        # 配对 start/end
        silence_ranges: list[tuple[float, float]] = []
        for i, start in enumerate(silence_starts):
            end = silence_ends[i] if i < len(silence_ends) else duration
            silence_ranges.append(
                (max(0, start + keep_padding), min(duration, end - keep_padding))
            )

        # 4. 计算非静音段
        speech_segments: list[tuple[float, float]] = []
        cursor = 0.0
        for s_start, s_end in silence_ranges:
            if s_start > cursor:
                speech_segments.append((cursor, s_start))
            cursor = max(cursor, s_end)
        if cursor < duration:
            speech_segments.append((cursor, duration))

        # 过滤太短的片段（<0.3s）
        speech_segments = [(s, e) for s, e in speech_segments if e - s >= 0.3]

        if not speech_segments:
            # 全是静音，直接复制
            self._copy_with_reencode(input_path, output_path, config)
            return

        if (
            len(speech_segments) == 1
            and speech_segments[0][0] <= 0.1
            and speech_segments[0][1] >= duration - 0.1
        ):
            # 无实质静音，直接复制
            self._copy_with_reencode(input_path, output_path, config)
            return

        # 5. 提取片段并拼接
        with tempfile.TemporaryDirectory(prefix="crow5_ai_trim_") as tmp:
            segment_files: list[Path] = []
            for idx, (seg_start, seg_end) in enumerate(speech_segments):
                seg_file = Path(tmp) / f"seg_{idx:04d}.{config.output_format}"
                seg_cmd = [
                    self.ffmpeg,
                    "-nostdin",
                    "-v",
                    "error",
                    "-ss",
                    str(seg_start),
                    "-to",
                    str(seg_end),
                    "-i",
                    str(input_path),
                    "-c",
                    "copy",
                    "-y",
                    str(seg_file),
                ]
                self._run(seg_cmd, f"提取静音裁剪片段 {idx} 失败。")
                segment_files.append(seg_file)

            # 拼接
            concat_list = Path(tmp) / "concat.txt"
            concat_list.write_text(
                "\n".join(f"file '{f}'" for f in segment_files),
                encoding="utf-8",
            )
            concat_cmd = [
                self.ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                "-y",
                str(output_path),
            ]
            self._run(concat_cmd, "静音裁剪拼接失败。")

    def _get_video_duration(self, path: Path) -> float:
        """获取视频时长（秒）。"""
        cmd = [
            self.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
        result = self.command_runner(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode != 0:
            raise VideoEditorError("无法获取视频时长。")
        try:
            info = json.loads(result.stdout)
            return float(info["format"]["duration"])
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise VideoEditorError(f"解析视频时长失败: {exc}") from exc

    def _copy_with_reencode(
        self, input_path: Path, output_path: Path, config: VideoEditConfig
    ) -> None:
        cmd = [
            self.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-b:v",
            config.output_bitrate,
            "-r",
            str(config.output_fps),
            "-c:a",
            "aac",
            "-y",
            str(output_path),
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
            self.ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ]
        result = self.command_runner(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode != 0 or "video" not in result.stdout:
            raise VideoEditorError("输入文件不是有效的视频。")

    def _run(self, cmd: list[str], error_msg: str) -> None:
        result = self.command_runner(
            cmd, capture_output=True, text=True, timeout=600, check=False
        )
        if result.returncode != 0:
            detail = (result.stderr or "")[:200]
            raise VideoEditorError(f"{error_msg} {detail}")


class SandboxVideoEditor:
    """离线沙箱视频编辑器——不调用 FFmpeg，仅生成占位文件用于演示。"""

    def __init__(self, *, output_directory: str | Path | None = None) -> None:
        self.output_directory = Path(output_directory or "data/video_edits")

    def capabilities(self) -> dict[str, str | bool]:
        return {
            "provider_name": "sandbox_video_editor",
            "display_name": "视频剪辑（演示）",
            "enabled": True,
            "mode": "sandbox",
            "supports_trim": True,
            "supports_subtitle": True,
            "supports_watermark": True,
            "supports_speed": True,
            "supports_resize": True,
            "supports_filter": True,
            # AI 智能剪辑
            "supports_ai_subtitle": False,
            "supports_ai_volume_norm": False,
            "supports_ai_enhance": False,
            "supports_ai_silence_trim": False,
        }

    def apply_edit(
        self,
        source_video_path: str,
        config: VideoEditConfig,
        *,
        output_path: str | None = None,
    ) -> str:
        """生成一个占位 MP4 文件，用于演示流水线流程。"""
        self.output_directory.mkdir(parents=True, exist_ok=True)
        if output_path:
            output = Path(output_path)
        else:
            output = self.output_directory / f"sandbox_{uuid4().hex[:8]}.mp4"
        # 写入最小合法 MP4 占位文件（不会实际播放，仅用于流水线流转）
        output.write_bytes(b"\x00" * 64)
        return str(output)

    def add_subtitles(
        self,
        video_path: str,
        srt_path: str,
        *,
        style: str = "default",
        output_path: str | None = None,
    ) -> str:
        return self.apply_edit(video_path, VideoEditConfig(), output_path=output_path)

    def add_watermark(
        self,
        video_path: str,
        watermark_path: str,
        *,
        position: str = "bottom_right",
        opacity: float = 0.5,
        output_path: str | None = None,
    ) -> str:
        return self.apply_edit(video_path, VideoEditConfig(), output_path=output_path)
