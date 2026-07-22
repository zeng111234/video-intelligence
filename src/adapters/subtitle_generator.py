"""字幕生成适配器。

基于 faster-whisper Python API 实现语音识别，支持 SRT/ASS 字幕导出。
支持视频和音频格式：.mp4, .mov, .wav, .mp3, .aac, .flac, .ogg, .m4a
"""

from __future__ import annotations

import importlib.util
import math
import tempfile
from pathlib import Path
from typing import Any

from src.models import TranscriptSegment


# 支持的媒体格式
SUBTITLE_SUPPORTED_EXTENSIONS: set[str] = {
    ".mp4",
    ".mov",  # 视频
    ".wav",
    ".mp3",
    ".aac",  # 音频
    ".flac",
    ".ogg",
    ".m4a",  # 音频
}


class SubtitleGeneratorError(RuntimeError):
    """字幕生成失败。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class SubtitleGenerator:
    """基于 faster-whisper 的字幕生成器。

    使用 faster-whisper Python API 进行语音识别，
    支持 SRT 和 ASS 两种字幕格式导出。
    """

    def __init__(
        self,
        *,
        model_name: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        model_loader: Any = None,
        ffmpeg_path: str | None = None,
    ) -> None:
        """初始化字幕生成器。

        Args:
            model_name: Whisper 模型名称，默认 base
            device: 计算设备，默认 cpu
            compute_type: 计算精度类型，默认 int8
            model_loader: 自定义模型加载器（用于测试）
            ffmpeg_path: FFmpeg 路径（可选）
        """
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model_loader = model_loader
        self._model: Any = None
        self._ffmpeg_path = ffmpeg_path

    @staticmethod
    def whisper_available() -> bool:
        """检测 faster-whisper 是否已安装。

        Returns:
            True 如果 faster-whisper 可用
        """
        return importlib.util.find_spec("faster_whisper") is not None

    def _get_model(self) -> Any:
        """懒加载 Whisper 模型。"""
        if self._model is not None:
            return self._model

        if self._model_loader is not None:
            self._model = self._model_loader(self.model_name)
            return self._model

        if not self.whisper_available():
            raise SubtitleGeneratorError(
                "faster-whisper 未安装。请运行 pip install faster-whisper。",
                retryable=False,
            )

        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
        )
        return self._model

    def generate_segments(
        self,
        audio_path: str,
        language: str | None = None,
    ) -> list[TranscriptSegment]:
        """生成转写片段。

        Args:
            audio_path: 音频或视频文件路径
            language: 语言代码（可选，None 表示自动检测）

        Returns:
            转写片段列表

        Raises:
            SubtitleGeneratorError: 文件不存在、格式不支持或识别失败
        """
        path = Path(audio_path)
        if not path.exists():
            raise SubtitleGeneratorError("媒体文件不存在。")

        ext = path.suffix.lower()
        if ext not in SUBTITLE_SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUBTITLE_SUPPORTED_EXTENSIONS))
            raise SubtitleGeneratorError(
                f"不支持的媒体格式 '{ext}'。支持的格式：{supported}"
            )

        # 视频格式需要先提取音频
        actual_audio_path = str(path)
        video_extensions = {".mp4", ".mov"}
        temp_dir = None

        if ext in video_extensions:
            temp_dir = tempfile.TemporaryDirectory(prefix="crow5_sub_")
            actual_audio_path = self._extract_audio(path, Path(temp_dir.name))

        try:
            model = self._get_model()

            transcribe_opts: dict[str, Any] = {
                "vad_filter": True,
                "beam_size": 5,
            }
            if language is not None:
                transcribe_opts["language"] = language

            raw_segments, info = model.transcribe(actual_audio_path, **transcribe_opts)

            segments: list[TranscriptSegment] = []
            for item in raw_segments:
                text = str(item.text).strip()
                if not text:
                    continue
                confidence = max(0.0, min(1.0, math.exp(float(item.avg_logprob))))
                segments.append(
                    TranscriptSegment(
                        start=float(item.start),
                        end=float(item.end),
                        text=text,
                        confidence=confidence,
                        needs_review=confidence < 0.75,
                    )
                )

            if not segments:
                raise SubtitleGeneratorError("没有识别到有效语音内容。")

            return segments

        except SubtitleGeneratorError:
            raise
        except Exception as exc:
            raise SubtitleGeneratorError(f"语音识别失败：{exc}") from exc
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    def _extract_audio(self, video_path: Path, temp_dir: Path) -> str:
        """从视频中提取音频为 WAV 格式。"""
        import shutil
        import subprocess

        ffmpeg = self._ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        wav_path = temp_dir / "audio.wav"

        cmd = [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-y",
            str(wav_path),
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, check=False
        )
        if result.returncode != 0 or not wav_path.exists():
            detail = (result.stderr or "")[:200]
            raise SubtitleGeneratorError(f"音频提取失败。 {detail}")

        return str(wav_path)

    @staticmethod
    def export_srt(
        segments: list[TranscriptSegment],
        output_path: str,
    ) -> str:
        """导出 SRT 字幕文件。

        Args:
            segments: 转写片段列表
            output_path: 输出文件路径

        Returns:
            输出文件路径
        """
        lines: list[str] = []
        for i, seg in enumerate(segments, start=1):
            start_ts = SubtitleGenerator._format_srt_time(seg.start)
            end_ts = SubtitleGenerator._format_srt_time(seg.end)
            lines.append(f"{i}")
            lines.append(f"{start_ts} --> {end_ts}")
            lines.append(seg.text)
            lines.append("")

        output = Path(output_path)
        output.write_text("\n".join(lines), encoding="utf-8")
        return str(output)

    @staticmethod
    def export_ass(
        segments: list[TranscriptSegment],
        output_path: str,
        style: dict[str, str | int | float] | None = None,
    ) -> str:
        """导出 ASS 字幕文件。

        Args:
            segments: 转写片段列表
            output_path: 输出文件路径
            style: 样式配置（可选）

        Returns:
            输出文件路径
        """
        # 默认样式
        default_style: dict[str, str | int | float] = {
            "font_name": "Microsoft YaHei",
            "font_size": 20,
            "primary_color": "&H00FFFFFF",  # 白色
            "outline_color": "&H00000000",  # 黑色
            "outline_width": 2,
            "shadow_depth": 1,
            "alignment": 2,  # 底部居中
            "margin_v": 30,
        }
        if style:
            default_style.update(style)

        # 构建 ASS 文件内容
        header = _build_ass_header(default_style)

        dialogue_lines: list[str] = []
        for seg in segments:
            start_ts = SubtitleGenerator._format_ass_time(seg.start)
            end_ts = SubtitleGenerator._format_ass_time(seg.end)
            dialogue_lines.append(
                f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{seg.text}"
            )

        content = header + "\n".join(dialogue_lines) + "\n"

        output = Path(output_path)
        output.write_text(content, encoding="utf-8-sig")
        return str(output)

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        """格式化 SRT 时间戳。"""
        ms = round(seconds * 1000)
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1_000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def _format_ass_time(seconds: float) -> str:
        """格式化 ASS 时间戳（H:MM:SS.CC）。"""
        cs = round(seconds * 100)
        h, rem = divmod(cs, 360_000)
        m, rem = divmod(rem, 6_000)
        s, cs = divmod(rem, 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _build_ass_header(style: dict[str, str | int | float]) -> str:
    """构建 ASS 文件头。"""
    font_name = style.get("font_name", "Microsoft YaHei")
    font_size = int(style.get("font_size", 20))
    primary_color = str(style.get("primary_color", "&H00FFFFFF"))
    outline_color = str(style.get("outline_color", "&H00000000"))
    outline_width = int(style.get("outline_width", 2))
    shadow_depth = int(style.get("shadow_depth", 1))
    alignment = int(style.get("alignment", 2))
    margin_v = int(style.get("margin_v", 30))

    return f"""[Script Info]
; Script generated by Crow5 SubtitleGenerator
Title: Crow5 Auto Subtitle
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary_color},&H000000FF,{outline_color},&H80000000,0,0,0,0,100,100,0,0,1,{outline_width},{shadow_depth},{alignment},10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
