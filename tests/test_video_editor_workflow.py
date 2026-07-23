"""智能剪辑工作流的系统素材与字幕复核门禁测试。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.models import AvatarTask, AvatarProviderStatus, TaskStatus, VideoEditTask
from src.repositories.mock import MockRepository
from src.services.video_editor_workflow import (
    VideoEditorWorkflowError,
    VideoEditorWorkflowService,
)


class _TranscriptionStub:
    def __init__(self, approved=None):
        self.approved = approved

    def get_approved_revision(self, task_id: str):
        return self.approved


def _avatar_task(task_id: str, result_path: Path | None, *, is_mock: bool = False) -> AvatarTask:
    now = datetime.now().astimezone()
    return AvatarTask(
        task_id=task_id,
        title="系统数字人成片",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        script_text="测试口播",
        avatar_id="avatar-1",
        avatar_name="测试数字人",
        voice_id="voice-1",
        voice_name="测试声音",
        rights_holder="测试用户",
        rights_confirmed_at=now,
        idempotency_key=f"key-{task_id}",
        provider_name="local",
        provider_status=AvatarProviderStatus.SUCCEEDED,
        result_path=str(result_path) if result_path else None,
        is_mock=is_mock,
    )


def _analysis_task(source_path: Path) -> VideoEditTask:
    now = datetime.now().astimezone()
    return VideoEditTask(
        task_id="analysis-test",
        title="智能分析",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        source_video_path=str(source_path),
        is_mock=False,
        outputs={
            "workflow": "analysis",
            "source_id": "avatar:avatar-real",
            "transcription_task_id": "transcript-test",
        },
    )


def test_list_sources_only_exposes_real_existing_system_media(tmp_path: Path):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    repo.save_task(_avatar_task("avatar-real", video))
    repo.save_task(_avatar_task("avatar-mock", video, is_mock=True))
    repo.save_task(_avatar_task("avatar-missing", tmp_path / "missing.mp4"))
    service = VideoEditorWorkflowService(repo, None, _TranscriptionStub(), None)

    sources = service.list_sources()

    assert [item["source_id"] for item in sources] == ["avatar:avatar-real"]
    assert sources[0]["source_type"] == "avatar"
    assert sources[0]["media_url"].endswith("/avatar:avatar-real/media")


def test_subtitle_enabled_job_requires_an_approved_revision(tmp_path: Path):
    video = tmp_path / "avatar.mp4"
    video.write_bytes(b"video")
    repo = MockRepository(tasks=[])
    repo.save_task(_avatar_task("avatar-real", video))
    repo.save_task(_analysis_task(video))
    service = VideoEditorWorkflowService(repo, None, _TranscriptionStub(approved=None), None)

    with pytest.raises(VideoEditorWorkflowError, match="字幕尚未完成复核确认"):
        service.create_edit_job(analysis_id="analysis-test", steps=[], subtitle_enabled=True)
