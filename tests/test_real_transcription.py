from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.models import TaskStatus, TranscriptSegment, TranscriptStatus
from src.repositories import MockRepository, SQLiteRepository
from src.services.transcription import MediaValidationError, TranscriptionService


class FakeModel:
    def transcribe(self, path: str, **options):
        assert Path(path).read_bytes() == b"fake-wav"
        assert options == {"language": "zh", "vad_filter": True, "beam_size": 5}
        return (
            [
                SimpleNamespace(
                    start=0.0,
                    end=1.25,
                    text=" 这是当前上传媒体的内容。 ",
                    avg_logprob=-0.1,
                )
            ],
            SimpleNamespace(language="zh"),
        )


def fake_media_runner(seen_paths: list[Path]):
    def run(args, **kwargs):
        if args[0] == "ffprobe":
            seen_paths.append(Path(args[-1]))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "streams": [{"codec_type": "audio"}],
                        "format": {"duration": "8.5"},
                    }
                ),
            )
        output = Path(args[-1])
        output.write_bytes(b"fake-wav")
        seen_paths.append(output)
        return SimpleNamespace(returncode=0)

    return run


def test_uploaded_media_is_processed_and_temp_files_are_removed() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    seen_paths: list[Path] = []
    service = TranscriptionService(
        repository,
        model_loader=lambda _name: FakeModel(),
        command_runner=fake_media_runner(seen_paths),
    )

    task = service.create_task(
        media_name="owned.mp3",
        media_type="audio/mpeg",
        media_bytes=b"ID3\x04\x00\x00authorized-media",
        rights_confirmed=True,
        rights_holder="测试公司",
        candidate_id="douyin-1",
    )

    assert task.status == TaskStatus.SUCCEEDED
    assert task.is_mock is False
    assert task.segments[0].text == "这是当前上传媒体的内容。"
    assert task.media_sha256
    revisions = repository.list_transcript_revisions(task.task_id)
    assert len(revisions) == 1
    assert revisions[0].status == TranscriptStatus.DRAFT
    assert all(not path.exists() for path in seen_paths)


def test_correction_approval_persists_after_sqlite_restart(tmp_path: Path) -> None:
    database = tmp_path / "transcripts.sqlite3"
    repository = SQLiteRepository(database)
    service = TranscriptionService(
        repository,
        model_loader=lambda _name: FakeModel(),
        command_runner=fake_media_runner([]),
    )
    task = service.create_task(
        media_name="owned.mp3",
        media_type="audio/mpeg",
        media_bytes=b"ID3\x04\x00\x00authorized-media",
        rights_confirmed=True,
        rights_holder="测试公司",
    )
    corrected = [
        TranscriptSegment(
            start=0,
            end=1.25,
            text="这是已校对的最终文案。",
            confidence=0.9,
        )
    ]
    approved = service.save_revision(
        task.task_id, corrected, reviewer="老板", approve=True
    )

    reopened = SQLiteRepository(database)
    saved = reopened.get_transcript_revision(approved.revision_id)
    saved_task = reopened.get_task(task.task_id)
    assert saved is not None
    assert saved.status == TranscriptStatus.APPROVED
    assert saved.corrected_segments[0].text == "这是已校对的最终文案。"
    assert saved_task is not None
    assert saved_task.approved_revision_id == approved.revision_id


def test_media_signature_and_audio_track_are_validated() -> None:
    service = TranscriptionService(MockRepository(candidates=[], tasks=[]))
    with pytest.raises(MediaValidationError, match="扩展名不匹配"):
        service.create_task(
            media_name="fake.mp3",
            media_type="audio/mpeg",
            media_bytes=b"not-an-mp3",
            rights_confirmed=True,
            rights_holder="测试公司",
        )

    def no_audio(args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"streams": [], "format": {"duration": "1"}}),
        )

    no_audio_service = TranscriptionService(
        MockRepository(candidates=[], tasks=[]), command_runner=no_audio
    )
    with pytest.raises(MediaValidationError, match="没有可识别的音轨"):
        no_audio_service.create_task(
            media_name="silent.mp3",
            media_type="audio/mpeg",
            media_bytes=b"ID3silent",
            rights_confirmed=True,
            rights_holder="测试公司",
        )


def test_model_loading_retries_once_then_reports_failure() -> None:
    attempts = 0

    def unavailable_model(_name: str):
        nonlocal attempts
        attempts += 1
        raise ConnectionError("model host unavailable")

    service = TranscriptionService(
        MockRepository(candidates=[], tasks=[]),
        model_loader=unavailable_model,
        command_runner=fake_media_runner([]),
    )
    with pytest.raises(RuntimeError, match="已自动重试一次"):
        service.create_task(
            media_name="owned.mp3",
            media_type="audio/mpeg",
            media_bytes=b"ID3authorized-media",
            rights_confirmed=True,
            rights_holder="测试公司",
        )
    assert attempts == 2
