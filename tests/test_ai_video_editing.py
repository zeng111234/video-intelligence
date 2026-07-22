"""AI 智能剪辑方法单元测试。

覆盖 FFmpegVideoEditor 的 4 个 AI 方法：
- _apply_ai_subtitle
- _apply_ai_volume_norm
- _apply_ai_enhance
- _apply_ai_silence_trim

使用 mock command_runner 避免真实调用 FFmpeg。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.video_editor import FFmpegVideoEditor, VideoEditorError
from src.models import VideoEditConfig, VideoEditStep, VideoEditStepKind


class FakeCompletedProcess:
    """模拟 subprocess.CompletedProcess。"""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_editor(command_runner: MagicMock | None = None) -> FFmpegVideoEditor:
    """创建测试用编辑器实例。"""
    return FFmpegVideoEditor(
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe",
        command_runner=command_runner or MagicMock(return_value=FakeCompletedProcess()),
    )


def fake_video(tmp_path: Path) -> Path:
    """创建一个假视频文件。"""
    v = tmp_path / "test_video.mp4"
    v.write_bytes(b"\x00" * 100)
    return v


# ---------------------------------------------------------------------------
# _apply_ai_subtitle
# ---------------------------------------------------------------------------


class TestAiSubtitle:
    """测试 AI 自动字幕生成。"""

    def test_whisper_not_installed_raises(self, tmp_path: Path) -> None:
        """whisper 不可用时应抛出 VideoEditorError。"""
        editor = make_editor()
        # 同时禁用 SubtitleGenerator API 和 whisper CLI
        import shutil

        original = shutil.which
        shutil.which = lambda x: None if x == "whisper" else original(x)
        try:
            with pytest.raises(VideoEditorError, match="字幕生成失败"):
                with patch(
                    "src.adapters.subtitle_generator.SubtitleGenerator.whisper_available",
                    return_value=False,
                ):
                    editor._apply_ai_subtitle(
                        fake_video(tmp_path),
                        tmp_path / "out.mp4",
                        {},
                    )
        finally:
            shutil.which = original

    def test_whisper_failure_raises(self, tmp_path: Path) -> None:
        """whisper 失败时应抛出 VideoEditorError。"""
        call_count = 0
        responses = [
            # extract audio -> success
            FakeCompletedProcess(0, "", ""),
            # whisper -> failure
            FakeCompletedProcess(1, "", "model not found"),
        ]

        def runner(cmd, **kwargs):
            nonlocal call_count
            r = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return r

        editor = make_editor(command_runner=runner)

        import shutil

        original = shutil.which
        shutil.which = lambda x: "/usr/bin/whisper" if x == "whisper" else original(x)
        try:
            with pytest.raises(VideoEditorError, match="字幕生成失败"):
                with patch(
                    "src.adapters.subtitle_generator.SubtitleGenerator.whisper_available",
                    return_value=False,
                ):
                    editor._apply_ai_subtitle(
                        fake_video(tmp_path),
                        tmp_path / "out.mp4",
                        {"model": "base", "language": "zh"},
                    )
        finally:
            shutil.which = original

    def test_whisper_success_generates_srt_and_burns(self, tmp_path: Path) -> None:
        """whisper 成功后应生成 SRT 并烧入字幕。"""
        srt_content = "1\n00:00:00,000 --> 00:00:03,000\n测试字幕\n"
        call_count = 0
        srt_written = False

        def runner(cmd, **kwargs):
            nonlocal call_count, srt_written
            call_count += 1
            # extract audio
            if call_count == 1:
                return FakeCompletedProcess(0, "", "")
            # whisper -> write SRT file
            if call_count == 2:
                # Find output dir from command
                output_dir_idx = (
                    cmd.index("--output_dir") + 1 if "--output_dir" in cmd else -1
                )
                if output_dir_idx > 0 and output_dir_idx < len(cmd):
                    out_dir = Path(cmd[output_dir_idx])
                    srt_file = out_dir / "audio.srt"
                    srt_file.write_text(srt_content, encoding="utf-8")
                    srt_written = True
                return FakeCompletedProcess(0, "", "")
            # burn subtitles
            return FakeCompletedProcess(0, "", "")

        editor = make_editor(command_runner=runner)

        import shutil

        original = shutil.which
        shutil.which = lambda x: "/usr/bin/whisper" if x == "whisper" else original(x)
        try:
            # Mock SubtitleGenerator API 返回 SRT 内容
            with (
                patch(
                    "src.adapters.subtitle_generator.SubtitleGenerator.whisper_available",
                    return_value=True,
                ),
                patch(
                    "src.adapters.subtitle_generator.SubtitleGenerator.__init__",
                    return_value=None,
                ),
                patch(
                    "src.adapters.subtitle_generator.SubtitleGenerator.generate_segments",
                    return_value=[],
                ),
                patch(
                    "src.adapters.video_editor.FFmpegVideoEditor._try_generate_subtitle_with_api",
                    return_value=srt_content,
                ),
            ):
                editor._apply_ai_subtitle(
                    fake_video(tmp_path),
                    tmp_path / "out.mp4",
                    {"model": "base", "language": "zh", "style": "default"},
                )
                # API 返回了 SRT 内容，所以不需要 CLI
                # 只验证字幕烧入（FFmpeg 命令）被调用了
                assert call_count == 1  # 只有 burn subtitles 一步
        finally:
            shutil.which = original


# ---------------------------------------------------------------------------
# _apply_ai_volume_norm
# ---------------------------------------------------------------------------


class TestAiVolumeNorm:
    """测试音量标准化。"""

    def test_two_pass_loudnorm(self, tmp_path: Path) -> None:
        """应执行两遍 loudnorm 并解析 JSON。"""
        call_count = 0
        loudnorm_json = json.dumps(
            {
                "input_i": "-12.5",
                "input_tp": "-1.0",
                "input_lra": "8.5",
                "input_thresh": "-22.0",
                "target_offset": "3.5",
            }
        )

        def runner(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # analyze pass
                return FakeCompletedProcess(0, "", f"some output\n{loudnorm_json}\n")
            # normalize pass
            return FakeCompletedProcess(0, "", "")

        editor = make_editor(command_runner=runner)
        editor._apply_ai_volume_norm(
            fake_video(tmp_path),
            tmp_path / "out.mp4",
            {"target_i": -16, "target_lra": 11, "target_tp": -1.5},
        )
        assert call_count == 2

    def test_analysis_failure_raises(self, tmp_path: Path) -> None:
        """分析阶段失败应抛出异常。"""

        def runner(cmd, **kwargs):
            return FakeCompletedProcess(1, "", "codec error")

        editor = make_editor(command_runner=runner)
        with pytest.raises(VideoEditorError, match="音量分析失败"):
            editor._apply_ai_volume_norm(
                fake_video(tmp_path),
                tmp_path / "out.mp4",
                {},
            )

    def test_graceful_fallback_on_bad_json(self, tmp_path: Path) -> None:
        """JSON 解析失败时应降级使用默认值。"""
        call_count = 0

        def runner(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return FakeCompletedProcess(0, "", "no json here")
            return FakeCompletedProcess(0, "", "")

        editor = make_editor(command_runner=runner)
        # 不应抛出异常
        editor._apply_ai_volume_norm(
            fake_video(tmp_path),
            tmp_path / "out.mp4",
            {},
        )
        assert call_count == 2


# ---------------------------------------------------------------------------
# _apply_ai_enhance
# ---------------------------------------------------------------------------


class TestAiEnhance:
    """测试画面增强。"""

    def test_default_params_produces_filter(self, tmp_path: Path) -> None:
        """默认参数应生成包含 eq + unsharp + hqdn3d 的滤镜。"""
        captured_cmd: list[str] = []

        def runner(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return FakeCompletedProcess(0, "", "")

        editor = make_editor(command_runner=runner)
        editor._apply_ai_enhance(
            fake_video(tmp_path),
            tmp_path / "out.mp4",
            {},
        )

        # 找到 -vf 参数
        assert "-vf" in captured_cmd
        vf_idx = captured_cmd.index("-vf") + 1
        vf = captured_cmd[vf_idx]
        assert "eq=" in vf
        assert "unsharp=" in vf
        assert "hqdn3d=" in vf

    def test_custom_params(self, tmp_path: Path) -> None:
        """自定义参数应正确传递。"""
        captured_cmd: list[str] = []

        def runner(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return FakeCompletedProcess(0, "", "")

        editor = make_editor(command_runner=runner)
        editor._apply_ai_enhance(
            fake_video(tmp_path),
            tmp_path / "out.mp4",
            {"brightness": 0.1, "contrast": 1.2, "sharpen": 2.0, "denoise": 5},
        )

        assert "-vf" in captured_cmd
        vf_idx = captured_cmd.index("-vf") + 1
        vf = captured_cmd[vf_idx]
        assert "brightness=0.1" in vf
        assert "contrast=1.2" in vf

    def test_ffmpeg_failure_raises(self, tmp_path: Path) -> None:
        """FFmpeg 失败应抛出异常。"""

        def runner(cmd, **kwargs):
            return FakeCompletedProcess(1, "", "filter error")

        editor = make_editor(command_runner=runner)
        with pytest.raises(VideoEditorError, match="画面增强失败"):
            editor._apply_ai_enhance(
                fake_video(tmp_path),
                tmp_path / "out.mp4",
                {},
            )


# ---------------------------------------------------------------------------
# _apply_ai_silence_trim
# ---------------------------------------------------------------------------


class TestAiSilenceTrim:
    """测试智能静音裁剪。"""

    def test_no_silence_detected_copies_directly(self, tmp_path: Path) -> None:
        """无静音段时应直接复制。"""
        call_count = 0

        def runner(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            # duration probe
            if "ffprobe" in cmd[0]:
                return FakeCompletedProcess(
                    0,
                    json.dumps({"format": {"duration": "10.0"}}),
                    "",
                )
            # silence detect - no silence found
            if "silencedetect" in " ".join(cmd):
                return FakeCompletedProcess(0, "", "")
            # direct copy
            return FakeCompletedProcess(0, "", "")

        editor = make_editor(command_runner=runner)
        editor._apply_ai_silence_trim(
            fake_video(tmp_path),
            tmp_path / "out.mp4",
            {},
            VideoEditConfig(),
        )

    def test_silence_in_middle_segments(self, tmp_path: Path) -> None:
        """中间有静音时应分割并拼接。"""
        call_count = 0
        silence_stderr = (
            "[silencedetect @ 0x0] silence_start: 3.0\n"
            "[silencedetect @ 0x0] silence_end: 5.0 | silence_duration: 2.0\n"
        )

        def runner(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            # duration probe
            if "ffprobe" in cmd[0]:
                return FakeCompletedProcess(
                    0,
                    json.dumps({"format": {"duration": "10.0"}}),
                    "",
                )
            # silence detect
            if "silencedetect" in " ".join(cmd):
                return FakeCompletedProcess(0, "", silence_stderr)
            # segment extraction or concat
            return FakeCompletedProcess(0, "", "")

        editor = make_editor(command_runner=runner)
        editor._apply_ai_silence_trim(
            fake_video(tmp_path),
            tmp_path / "out.mp4",
            {"noise_threshold": -30, "min_duration": 0.5},
            VideoEditConfig(),
        )
        # 应该调用了: ffprobe + silencedetect + seg_0 + seg_1 + concat = 5
        assert call_count == 5

    def test_all_silence_copies_directly(self, tmp_path: Path) -> None:
        """全是静音时应直接复制（不产生空输出）。"""
        call_count = 0

        def runner(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if "ffprobe" in cmd[0]:
                return FakeCompletedProcess(
                    0,
                    json.dumps({"format": {"duration": "10.0"}}),
                    "",
                )
            if "silencedetect" in " ".join(cmd):
                return FakeCompletedProcess(
                    0,
                    "",
                    "[silencedetect @ 0x0] silence_start: 0.0\n"
                    "[silencedetect @ 0x0] silence_end: 10.0 | silence_duration: 10.0\n",
                )
            return FakeCompletedProcess(0, "", "")

        editor = make_editor(command_runner=runner)
        editor._apply_ai_silence_trim(
            fake_video(tmp_path),
            tmp_path / "out.mp4",
            {},
            VideoEditConfig(),
        )

    def test_detect_failure_raises(self, tmp_path: Path) -> None:
        """静音检测失败应抛出异常。"""

        def runner(cmd, **kwargs):
            if "ffprobe" in cmd[0]:
                return FakeCompletedProcess(
                    0,
                    json.dumps({"format": {"duration": "10.0"}}),
                    "",
                )
            return FakeCompletedProcess(1, "", "filter error")

        editor = make_editor(command_runner=runner)
        with pytest.raises(VideoEditorError, match="静音检测失败"):
            editor._apply_ai_silence_trim(
                fake_video(tmp_path),
                tmp_path / "out.mp4",
                {},
                VideoEditConfig(),
            )


# ---------------------------------------------------------------------------
# _get_video_duration
# ---------------------------------------------------------------------------


class TestGetVideoDuration:
    """测试视频时长获取。"""

    def test_valid_duration(self, tmp_path: Path) -> None:
        def runner(cmd, **kwargs):
            return FakeCompletedProcess(
                0,
                json.dumps({"format": {"duration": "123.45"}}),
                "",
            )

        editor = make_editor(command_runner=runner)
        assert editor._get_video_duration(fake_video(tmp_path)) == 123.45

    def test_ffprobe_failure_raises(self, tmp_path: Path) -> None:
        def runner(cmd, **kwargs):
            return FakeCompletedProcess(1, "", "probe error")

        editor = make_editor(command_runner=runner)
        with pytest.raises(VideoEditorError, match="无法获取视频时长"):
            editor._get_video_duration(fake_video(tmp_path))

    def test_bad_json_raises(self, tmp_path: Path) -> None:
        def runner(cmd, **kwargs):
            return FakeCompletedProcess(0, "not json", "")

        editor = make_editor(command_runner=runner)
        with pytest.raises(VideoEditorError, match="解析视频时长失败"):
            editor._get_video_duration(fake_video(tmp_path))


# ---------------------------------------------------------------------------
# AI 步骤路由集成测试
# ---------------------------------------------------------------------------


class TestAiStepRouting:
    """测试 AI 步骤在 _apply_step 中的路由。"""

    def test_ai_subtitle_step_routed(self, tmp_path: Path) -> None:
        """AI_SUBTITLE 步骤应路由到 _apply_ai_subtitle。"""
        editor = make_editor()
        editor._apply_ai_subtitle = MagicMock()

        step = VideoEditStep(
            kind=VideoEditStepKind.AI_SUBTITLE,
            params={"model": "base"},
            order=0,
        )
        editor._apply_step(
            fake_video(tmp_path),
            step,
            tmp_path / "out.mp4",
            VideoEditConfig(),
        )
        editor._apply_ai_subtitle.assert_called_once()

    def test_ai_volume_norm_step_routed(self, tmp_path: Path) -> None:
        """AI_VOLUME_NORM 步骤应路由到 _apply_ai_volume_norm。"""
        editor = make_editor()
        editor._apply_ai_volume_norm = MagicMock()

        step = VideoEditStep(
            kind=VideoEditStepKind.AI_VOLUME_NORM,
            params={},
            order=0,
        )
        editor._apply_step(
            fake_video(tmp_path),
            step,
            tmp_path / "out.mp4",
            VideoEditConfig(),
        )
        editor._apply_ai_volume_norm.assert_called_once()

    def test_ai_enhance_step_routed(self, tmp_path: Path) -> None:
        """AI_ENHANCE 步骤应路由到 _apply_ai_enhance。"""
        editor = make_editor()
        editor._apply_ai_enhance = MagicMock()

        step = VideoEditStep(
            kind=VideoEditStepKind.AI_ENHANCE,
            params={},
            order=0,
        )
        editor._apply_step(
            fake_video(tmp_path),
            step,
            tmp_path / "out.mp4",
            VideoEditConfig(),
        )
        editor._apply_ai_enhance.assert_called_once()

    def test_ai_silence_trim_step_routed(self, tmp_path: Path) -> None:
        """AI_SILENCE_TRIM 步骤应路由到 _apply_ai_silence_trim。"""
        editor = make_editor()
        editor._apply_ai_silence_trim = MagicMock()

        step = VideoEditStep(
            kind=VideoEditStepKind.AI_SILENCE_TRIM,
            params={},
            order=0,
        )
        config = VideoEditConfig()
        editor._apply_step(
            fake_video(tmp_path),
            step,
            tmp_path / "out.mp4",
            config,
        )
        editor._apply_ai_silence_trim.assert_called_once()


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    """测试 AI 能力声明。"""

    def test_ffmpeg_editor_declares_ai_capabilities(self) -> None:
        editor = make_editor()
        caps = editor.capabilities()
        assert caps["supports_ai_subtitle"] is True
        assert caps["supports_ai_volume_norm"] is True
        assert caps["supports_ai_enhance"] is True
        assert caps["supports_ai_silence_trim"] is True

    def test_sandbox_editor_declares_ai_capabilities(self) -> None:
        from src.adapters.video_editor import SandboxVideoEditor

        editor = SandboxVideoEditor()
        caps = editor.capabilities()
        assert caps["supports_ai_subtitle"] is False
        assert caps["supports_ai_volume_norm"] is False
        assert caps["supports_ai_enhance"] is False
        assert caps["supports_ai_silence_trim"] is False
