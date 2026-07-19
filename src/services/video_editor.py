"""视频剪辑服务。

协调 VideoEditor 适配器与 TaskRepository，
支持单次剪辑、SRT 字幕嵌入、批量剪辑。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from src.contracts import TaskRepository, VideoEditor
from src.models import (
    TaskStatus,
    VideoEditConfig,
    VideoEditTask,
    VideoEditStep,
    VideoEditStepKind,
)


class VideoEditingService:
    def __init__(
        self,
        repository: TaskRepository,
        editor: VideoEditor,
        *,
        output_directory: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.editor = editor
        self.output_directory = Path(output_directory or "data/video_edits")

    def capabilities(self) -> dict[str, str | bool]:
        return self.editor.capabilities()

    def edit_video(
        self,
        *,
        source_video_path: str,
        edit_config: VideoEditConfig | None = None,
        subtitle_text: str | None = None,
        subtitle_style: str = "default",
        source_task_id: str | None = None,
        source_avatar_task_id: str | None = None,
        on_progress: Callable[[VideoEditTask], None] | None = None,
    ) -> VideoEditTask:
        """创建并执行视频剪辑任务。"""
        source = Path(source_video_path)
        if not source.exists():
            raise ValueError("源视频文件不存在。")

        if edit_config is None:
            edit_config = VideoEditConfig()

        now = datetime.now().astimezone()
        task = VideoEditTask(
            task_id=f"edit-{uuid4().hex[:10]}",
            title=f"视频剪辑 · {source.name}",
            status=TaskStatus.RUNNING,
            progress=10,
            created_at=now,
            updated_at=now,
            source_video_path=str(source),
            subtitle_text=subtitle_text,
            subtitle_style=subtitle_style,
            edit_config=edit_config,
            source_task_id=source_task_id,
            source_avatar_task_id=source_avatar_task_id,
            stage="正在剪辑",
            is_mock=False,
        )
        self._save(task, on_progress)
        try:
            self.output_directory.mkdir(parents=True, exist_ok=True)
            output_name = f"{task.task_id}.{edit_config.output_format}"
            output_path = self.output_directory / output_name

            # 如果有字幕文本，先生成 SRT 并加入剪辑步骤
            srt_path = None
            if subtitle_text and subtitle_text.strip():
                srt_path = source.parent / f"{task.task_id}.srt"
                srt_content = self._text_to_srt(subtitle_text)
                srt_path.write_text(srt_content, encoding="utf-8")
                sub_step = VideoEditStep(
                    kind=VideoEditStepKind.SUBTITLE,
                    params={"srt_path": str(srt_path), "style": subtitle_style},
                    order=len(edit_config.steps),
                )
                edit_config = edit_config.model_copy(
                    update={"steps": list(edit_config.steps) + [sub_step]}
                )
                task = task.model_copy(update={"edit_config": edit_config})

            task = self._update(
                task, stage="处理中", progress=40, on_progress=on_progress
            )

            result_path = self.editor.apply_edit(
                str(source), edit_config, output_path=str(output_path)
            )

            # 清理临时 SRT
            if srt_path and srt_path.exists():
                srt_path.unlink(missing_ok=True)

            result_stat = Path(result_path).stat()
            task = task.model_copy(
                update={
                    "status": TaskStatus.SUCCEEDED,
                    "progress": 100,
                    "stage": "剪辑完成",
                    "updated_at": datetime.now().astimezone(),
                    "result_path": result_path,
                    "result_mime": "video/mp4",
                    "result_size_bytes": result_stat.st_size,
                    "outputs": {"video": result_path},
                }
            )
            self._save(task, on_progress)
            return task

        except Exception as exc:
            task = task.model_copy(
                update={
                    "status": TaskStatus.FAILED,
                    "stage": "剪辑失败",
                    "updated_at": datetime.now().astimezone(),
                    "error_message": str(exc),
                }
            )
            self._save(task, on_progress)
            return task

    def batch_edit(
        self,
        *,
        source_video_paths: list[str],
        edit_config: VideoEditConfig | None = None,
    ) -> list[VideoEditTask]:
        """批量视频剪辑。"""
        tasks: list[VideoEditTask] = []
        for path in source_video_paths:
            task = self.edit_video(
                source_video_path=path,
                edit_config=edit_config,
            )
            tasks.append(task)
        return tasks

    def list_tasks(self) -> list[VideoEditTask]:
        return [t for t in self.repository.list_tasks() if isinstance(t, VideoEditTask)]

    def get_task(self, task_id: str) -> VideoEditTask | None:
        task = self.repository.get_task(task_id)
        return task if isinstance(task, VideoEditTask) else None

    def _update(
        self,
        task: VideoEditTask,
        *,
        stage: str,
        progress: int,
        on_progress: Callable[[VideoEditTask], None] | None,
    ) -> VideoEditTask:
        updated = task.model_copy(
            update={
                "stage": stage,
                "progress": progress,
                "updated_at": datetime.now().astimezone(),
            }
        )
        self._save(updated, on_progress)
        return updated

    def _save(
        self,
        task: VideoEditTask,
        on_progress: Callable[[VideoEditTask], None] | None,
    ) -> None:
        self.repository.save_task(task)
        if on_progress is not None:
            try:
                on_progress(task)
            except Exception:
                pass

    @staticmethod
    def _text_to_srt(text: str) -> str:
        """将纯文本转换为简单 SRT 字幕。

        每句话约 15 字，每段持续 3 秒。
        """
        lines = text.strip().split("\n")
        segments: list[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 按标点分句
            for sep in ["。", "！", "？", "；", ".", "!", "?", ";"]:
                line = line.replace(sep, sep + "\n")
            for seg in line.split("\n"):
                seg = seg.strip()
                if seg:
                    segments.append(seg)

        if not segments:
            segments = [text[:50]]

        srt_blocks: list[str] = []
        time_per_seg = 3.0
        for i, seg in enumerate(segments):
            start = i * time_per_seg
            end = start + time_per_seg
            srt_blocks.append(f"{i + 1}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{seg}")
        return "\n\n".join(srt_blocks) + "\n"


def _srt_ts(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
