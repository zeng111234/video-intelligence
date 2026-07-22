"""SubtitleGenerator 测试。

覆盖：静态方法、SRT/ASS 导出、格式时间戳、whisper_available 检测。
不依赖真实 faster-whisper，使用 mock 模型加载器。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.adapters.subtitle_generator import (
    SubtitleGenerator,
    SubtitleGeneratorError,
    SUBTITLE_SUPPORTED_EXTENSIONS,
)
from src.models import TranscriptSegment


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _make_segments() -> list[TranscriptSegment]:
    """创建测试用转写片段。"""
    return [
        TranscriptSegment(start=0.0, end=5.0, text="第一段文字", confidence=0.9),
        TranscriptSegment(start=5.0, end=10.0, text="第二段文字", confidence=0.85),
        TranscriptSegment(start=10.0, end=15.5, text="第三段文字", confidence=0.95),
    ]


# ---------------------------------------------------------------------------
# 测试类
# ---------------------------------------------------------------------------


class TestSubtitleGeneratorStatic:
    """测试 SubtitleGenerator 静态方法。"""

    def test_format_srt_time_zero(self):
        assert SubtitleGenerator._format_srt_time(0.0) == "00:00:00,000"

    def test_format_srt_time_simple(self):
        assert SubtitleGenerator._format_srt_time(5.0) == "00:00:05,000"

    def test_format_srt_time_with_ms(self):
        assert SubtitleGenerator._format_srt_time(1.234) == "00:00:01,234"

    def test_format_srt_time_minutes(self):
        assert SubtitleGenerator._format_srt_time(65.5) == "00:01:05,500"

    def test_format_srt_time_hours(self):
        assert SubtitleGenerator._format_srt_time(3661.1) == "01:01:01,100"

    def test_format_ass_time_zero(self):
        assert SubtitleGenerator._format_ass_time(0.0) == "0:00:00.00"

    def test_format_ass_time_simple(self):
        assert SubtitleGenerator._format_ass_time(5.0) == "0:00:05.00"

    def test_format_ass_time_with_cs(self):
        assert SubtitleGenerator._format_ass_time(1.23) == "0:00:01.23"

    def test_format_ass_time_minutes(self):
        assert SubtitleGenerator._format_ass_time(65.5) == "0:01:05.50"

    def test_format_ass_time_hours(self):
        assert SubtitleGenerator._format_ass_time(3661.1) == "1:01:01.10"


class TestExportSrt:
    """测试 SRT 导出。"""

    def test_export_srt_basic(self):
        segments = _make_segments()
        with tempfile.TemporaryDirectory() as tmp:
            output_path = str(Path(tmp) / "test.srt")
            result = SubtitleGenerator.export_srt(segments, output_path)

            assert result == output_path
            content = Path(output_path).read_text(encoding="utf-8")

            # 验证内容
            assert "1" in content
            assert "00:00:00,000 --> 00:00:05,000" in content
            assert "第一段文字" in content
            assert "2" in content
            assert "00:00:05,000 --> 00:00:10,000" in content
            assert "第二段文字" in content
            assert "3" in content
            assert "00:00:10,000 --> 00:00:15,500" in content
            assert "第三段文字" in content

    def test_export_srt_single_segment(self):
        segments = [
            TranscriptSegment(start=0.0, end=3.0, text="测试内容", confidence=0.9),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output_path = str(Path(tmp) / "single.srt")
            SubtitleGenerator.export_srt(segments, output_path)
            content = Path(output_path).read_text(encoding="utf-8")

            assert "1\n" in content
            assert "00:00:00,000 --> 00:00:03,000" in content
            assert "测试内容" in content

    def test_export_srt_empty_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = str(Path(tmp) / "empty.srt")
            SubtitleGenerator.export_srt([], output_path)
            content = Path(output_path).read_text(encoding="utf-8")
            assert content.strip() == ""


class TestExportAss:
    """测试 ASS 导出。"""

    def test_export_ass_basic(self):
        segments = _make_segments()
        with tempfile.TemporaryDirectory() as tmp:
            output_path = str(Path(tmp) / "test.ass")
            result = SubtitleGenerator.export_ass(segments, output_path)

            assert result == output_path
            content = Path(output_path).read_text(encoding="utf-8-sig")

            # 验证文件头
            assert "[Script Info]" in content
            assert "[V4+ Styles]" in content
            assert "[Events]" in content

            # 验证样式
            assert "Style: Default" in content

            # 验证对话内容
            assert "Dialogue:" in content
            assert "第一段文字" in content
            assert "第二段文字" in content
            assert "第三段文字" in content

    def test_export_ass_custom_style(self):
        segments = [
            TranscriptSegment(start=0.0, end=3.0, text="测试", confidence=0.9),
        ]
        style = {
            "font_name": "SimHei",
            "font_size": 24,
            "primary_color": "&H0000FFFF",
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_path = str(Path(tmp) / "styled.ass")
            SubtitleGenerator.export_ass(segments, output_path, style=style)
            content = Path(output_path).read_text(encoding="utf-8-sig")

            assert "SimHei" in content
            assert "24" in content
            assert "&H0000FFFF" in content

    def test_export_ass_has_proper_header(self):
        """ASS 文件头应包含 ScriptType 和 PlayRes。"""
        segments = [
            TranscriptSegment(start=0.0, end=3.0, text="测试", confidence=0.9),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output_path = str(Path(tmp) / "header.ass")
            SubtitleGenerator.export_ass(segments, output_path)
            content = Path(output_path).read_text(encoding="utf-8-sig")

            assert "ScriptType: v4.00+" in content
            assert "PlayResX: 1920" in content
            assert "PlayResY: 1080" in content


class TestWhisperAvailable:
    """测试 whisper_available 静态方法。"""

    def test_whisper_available_returns_bool(self):
        result = SubtitleGenerator.whisper_available()
        assert isinstance(result, bool)


class TestSubtitleGeneratorInit:
    """测试 SubtitleGenerator 初始化。"""

    def test_default_init(self):
        gen = SubtitleGenerator()
        assert gen.model_name == "base"
        assert gen.device == "cpu"
        assert gen.compute_type == "int8"

    def test_custom_init(self):
        gen = SubtitleGenerator(model_name="medium", device="cuda", compute_type="float16")
        assert gen.model_name == "medium"
        assert gen.device == "cuda"
        assert gen.compute_type == "float16"


class TestSupportedExtensions:
    """测试支持的格式常量。"""

    def test_video_formats_included(self):
        assert ".mp4" in SUBTITLE_SUPPORTED_EXTENSIONS
        assert ".mov" in SUBTITLE_SUPPORTED_EXTENSIONS

    def test_audio_formats_included(self):
        assert ".wav" in SUBTITLE_SUPPORTED_EXTENSIONS
        assert ".mp3" in SUBTITLE_SUPPORTED_EXTENSIONS
        assert ".aac" in SUBTITLE_SUPPORTED_EXTENSIONS
        assert ".flac" in SUBTITLE_SUPPORTED_EXTENSIONS
        assert ".ogg" in SUBTITLE_SUPPORTED_EXTENSIONS
        assert ".m4a" in SUBTITLE_SUPPORTED_EXTENSIONS

    def test_total_extensions_count(self):
        assert len(SUBTITLE_SUPPORTED_EXTENSIONS) == 8


class TestGenerateSegmentsValidation:
    """测试 generate_segments 输入验证。"""

    def test_nonexistent_file_raises(self):
        gen = SubtitleGenerator()
        with pytest.raises(SubtitleGeneratorError, match="媒体文件不存在"):
            gen.generate_segments("/nonexistent/file.mp4")

    def test_unsupported_format_raises(self):
        gen = SubtitleGenerator()
        with tempfile.NamedTemporaryFile(suffix=".avi", delete=False) as f:
            f.write(b"\x00" * 100)
            temp_path = f.name
        try:
            with pytest.raises(SubtitleGeneratorError, match="不支持的媒体格式"):
                gen.generate_segments(temp_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)


class TestGenerateSegmentsWithMockLoader:
    """测试 generate_segments 使用 mock 模型加载器。"""

    def test_generate_segments_success(self):
        """使用 mock 模型加载器测试成功路径。"""

        class MockSegment:
            def __init__(self, start, end, text, avg_logprob):
                self.start = start
                self.end = end
                self.text = text
                self.avg_logprob = avg_logprob

        class MockInfo:
            language = "zh"

        class MockModel:
            def transcribe(self, path, **kwargs):
                return [
                    MockSegment(0.0, 5.0, "你好世界", -0.1),
                    MockSegment(5.0, 10.0, "测试内容", -0.2),
                ], MockInfo()

        gen = SubtitleGenerator(model_loader=lambda name: MockModel())

        # 创建临时 WAV 文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"\x00" * 100)
            temp_path = f.name

        try:
            segments = gen.generate_segments(temp_path, language="zh")
            assert len(segments) == 2
            assert segments[0].text == "你好世界"
            assert segments[0].start == 0.0
            assert segments[0].end == 5.0
            assert segments[1].text == "测试内容"
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_generate_segments_empty_result_raises(self):
        """空转写结果应抛出错误。"""

        class MockInfo:
            language = "zh"

        class MockModel:
            def transcribe(self, path, **kwargs):
                return [], MockInfo()

        gen = SubtitleGenerator(model_loader=lambda name: MockModel())

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"\x00" * 100)
            temp_path = f.name

        try:
            with pytest.raises(SubtitleGeneratorError, match="没有识别到有效语音内容"):
                gen.generate_segments(temp_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_generate_segments_with_language(self):
        """指定语言参数应传递给模型。"""

        class MockSegment:
            def __init__(self, start, end, text, avg_logprob):
                self.start = start
                self.end = end
                self.text = text
                self.avg_logprob = avg_logprob

        class MockInfo:
            language = "en"

        captured_kwargs = {}

        class MockModel:
            def transcribe(self, path, **kwargs):
                captured_kwargs.update(kwargs)
                return [
                    MockSegment(0.0, 3.0, "Hello", -0.1),
                ], MockInfo()

        gen = SubtitleGenerator(model_loader=lambda name: MockModel())

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"\x00" * 100)
            temp_path = f.name

        try:
            segments = gen.generate_segments(temp_path, language="en")
            assert len(segments) == 1
            assert segments[0].text == "Hello"
            assert captured_kwargs.get("language") == "en"
        finally:
            Path(temp_path).unlink(missing_ok=True)
