"""VideoEditingService 独立测试。

覆盖：正向路径（沙箱模式）、字幕嵌入、批量编辑、SRT 生成、异常路径。
不依赖 FFmpeg，使用 SandboxVideoEditor。
"""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from src.adapters.video_editor import SandboxVideoEditor
from src.models import (
    TaskKind,
    TaskStatus,
    VideoEditConfig,
    VideoEditStep,
    VideoEditStepKind,
    VideoEditTask,
)
from src.repositories.mock import MockRepository
from src.services.video_editor import VideoEditingService


class TestVideoEditingServiceSandbox:
    """使用 SandboxVideoEditor 测试 VideoEditingService。"""

    def setup_method(self):
        self.repo = MockRepository()
        self.editor = SandboxVideoEditor()
        self.svc = VideoEditingService(self.repo, self.editor)
        # 创建临时视频文件
        self._temp_dir = tempfile.mkdtemp()
        self._temp_video = Path(self._temp_dir) / "test_video.mp4"
        self._temp_video.write_bytes(b"\x00" * 100)

    def teardown_method(self):
        self._temp_video.unlink(missing_ok=True)
        Path(self._temp_dir).rmdir()

    def test_capabilities_delegates_to_editor(self):
        cap = self.svc.capabilities()
        assert cap["enabled"] is True
        assert cap["mode"] == "sandbox"
        assert "supports_trim" in cap

    def test_edit_video_success(self):
        """沙箱模式下成功剪辑视频。"""
        task = self.svc.edit_video(source_video_path=str(self._temp_video))
        assert isinstance(task, VideoEditTask)
        assert task.status == TaskStatus.SUCCEEDED
        assert task.progress == 100
        assert task.result_path is not None
        assert Path(task.result_path).exists()

    def test_edit_video_nonexistent_file_raises(self):
        """源视频不存在应抛出 ValueError。"""
        with pytest.raises(ValueError, match="源视频文件不存在"):
            self.svc.edit_video(source_video_path="/nonexistent/video.mp4")

    def test_edit_video_with_config(self):
        """带 VideoEditConfig 的剪辑。"""
        config = VideoEditConfig(
            steps=[
                VideoEditStep(
                    kind=VideoEditStepKind.TRIM,
                    params={"start": 0, "duration": 10},
                    order=0,
                )
            ],
            output_format="mp4",
        )
        task = self.svc.edit_video(
            source_video_path=str(self._temp_video),
            edit_config=config,
        )
        assert task.status == TaskStatus.SUCCEEDED

    def test_edit_video_with_subtitle_text(self):
        """带字幕文本的剪辑应生成 SRT 并嵌入。"""
        task = self.svc.edit_video(
            source_video_path=str(self._temp_video),
            subtitle_text="这是一段字幕文本。\n这是第二行。",
        )
        assert task.status == TaskStatus.SUCCEEDED
        # SRT 临时文件应被清理
        srt_candidates = list(Path(self._temp_dir).glob("*.srt"))
        assert len(srt_candidates) == 0

    def test_edit_video_with_source_ids(self):
        """source_task_id 和 source_avatar_task_id 应正确存储。"""
        task = self.svc.edit_video(
            source_video_path=str(self._temp_video),
            source_task_id="transcript-123",
            source_avatar_task_id="avatar-456",
        )
        assert task.source_task_id == "transcript-123"
        assert task.source_avatar_task_id == "avatar-456"

    def test_edit_video_on_progress_called(self):
        """on_progress 回调应被多次调用。"""
        calls: list[VideoEditTask] = []
        self.svc.edit_video(
            source_video_path=str(self._temp_video),
            on_progress=lambda t: calls.append(t),
        )
        assert len(calls) >= 3  # RUNNING, 处理中, 剪辑完成

    def test_edit_video_on_progress_exception_swallowed(self):
        """on_progress 异常不应影响主流程。"""

        def bad_callback(t):
            raise RuntimeError("回调异常")

        task = self.svc.edit_video(
            source_video_path=str(self._temp_video),
            on_progress=bad_callback,
        )
        assert task.status == TaskStatus.SUCCEEDED

    def test_batch_edit(self):
        """批量剪辑应返回多个任务。"""
        video2 = Path(self._temp_dir) / "test_video2.mp4"
        video2.write_bytes(b"\x00" * 100)
        try:
            tasks = self.svc.batch_edit(
                source_video_paths=[str(self._temp_video), str(video2)]
            )
            assert len(tasks) == 2
            assert all(t.status == TaskStatus.SUCCEEDED for t in tasks)
        finally:
            video2.unlink(missing_ok=True)

    def test_batch_edit_partial_failure(self):
        """批量剪辑中不存在的文件会抛出 ValueError（batch_edit 不捕获异常）。"""
        with pytest.raises(ValueError, match="源视频文件不存在"):
            self.svc.batch_edit(
                source_video_paths=[str(self._temp_video), "/nonexistent.mp4"]
            )

    def test_list_tasks(self):
        self.svc.edit_video(source_video_path=str(self._temp_video))
        tasks = self.svc.list_tasks()
        assert len(tasks) >= 1
        assert all(isinstance(t, VideoEditTask) for t in tasks)

    def test_get_task_returns_correct_type(self):
        task = self.svc.edit_video(source_video_path=str(self._temp_video))
        fetched = self.svc.get_task(task.task_id)
        assert fetched is not None
        assert fetched.task_id == task.task_id

    def test_get_nonexistent_task_returns_none(self):
        assert self.svc.get_task("nonexistent") is None

    def test_task_kind_is_video_editing(self):
        task = self.svc.edit_video(source_video_path=str(self._temp_video))
        assert task.kind == TaskKind.VIDEO_EDITING

    def test_result_size_bytes_is_positive(self):
        task = self.svc.edit_video(source_video_path=str(self._temp_video))
        assert task.result_size_bytes is not None
        assert task.result_size_bytes > 0


class TestTextToSrt:
    """测试 _text_to_srt 静态方法。"""

    def test_single_line(self):
        srt = VideoEditingService._text_to_srt("这是一句话")
        assert "1\n" in srt
        assert "00:00:00,000 --> 00:00:03,000" in srt
        assert "这是一句话" in srt

    def test_multiple_sentences(self):
        srt = VideoEditingService._text_to_srt("第一句。第二句！第三句？")
        assert "1\n" in srt
        assert "2\n" in srt
        assert "3\n" in srt

    def test_empty_text(self):
        """空文本应产生至少一个字幕块。"""
        srt = VideoEditingService._text_to_srt("")
        assert "1\n" in srt

    def test_multiline_text(self):
        srt = VideoEditingService._text_to_srt("第一行\n第二行")
        assert "第一行" in srt
        assert "第二行" in srt

    def test_srt_format_valid(self):
        """SRT 输出应包含标准时间戳格式。"""
        srt = VideoEditingService._text_to_srt("测试内容")
        assert "-->" in srt
        assert "," in srt  # 毫秒分隔符

    def test_whitespace_only_text(self):
        """仅空白文本应产生至少一个块。"""
        srt = VideoEditingService._text_to_srt("   \n  \n  ")
        assert "1\n" in srt
