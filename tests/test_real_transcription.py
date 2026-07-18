from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.models import (
    TaskStatus,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptStatus,
)
from src.repositories import MockRepository, SQLiteRepository
from src.services.transcription import (
    MediaValidationError,
    TranscriptionError,
    TranscriptionService,
)

VIDEO_BYTES = b"\x00\x00\x00\x18ftypisom-authorized-video"


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

    progress_updates = []
    task = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        candidate_id="douyin-1",
        on_progress=progress_updates.append,
    )

    assert task.status == TaskStatus.SUCCEEDED
    assert task.is_mock is False
    assert task.segments[0].text == "这是当前上传媒体的内容。"
    assert task.media_sha256
    assert task.duration_seconds == 8.5
    assert [update.stage for update in progress_updates] == [
        "视频检查",
        "音频提取",
        "语音识别",
        "待校对",
    ]
    revisions = repository.list_transcript_revisions(task.task_id)
    assert len(revisions) == 1
    assert revisions[0].status == TranscriptStatus.DRAFT
    assert all(not path.exists() for path in seen_paths)


def test_accuracy_model_and_hotwords_are_forwarded_without_rewriting() -> None:
    seen_options: dict[str, object] = {}

    class CapturingModel:
        def transcribe(self, path: str, **options):
            seen_options.update(options)
            return (
                [
                    SimpleNamespace(
                        start=0.0,
                        end=1.0,
                        text=" 速腾二手车。 ",
                        avg_logprob=-0.1,
                    )
                ],
                SimpleNamespace(language="zh"),
            )

    service = TranscriptionService(
        MockRepository(candidates=[], tasks=[]),
        model_loader=lambda model_name: (
            CapturingModel()
            if model_name == "large-v3-turbo"
            else pytest.fail("unexpected model")
        ),
        command_runner=fake_media_runner([]),
    )
    task = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="large-v3-turbo",
        hotwords=" 速腾、懂车帝   车况 ",
    )

    assert task.model_name == "large-v3-turbo"
    assert task.asr_hotwords == "速腾、懂车帝 车况"
    assert seen_options == {
        "language": "zh",
        "vad_filter": True,
        "beam_size": 5,
        "hotwords": "速腾、懂车帝 车况",
    }


def test_unknown_asr_model_is_rejected_before_processing() -> None:
    service = TranscriptionService(MockRepository(candidates=[], tasks=[]))

    with pytest.raises(TranscriptionError, match="不支持所选识别模型"):
        service.create_task(
            media_name="owned.mp4",
            media_type="video/mp4",
            media_bytes=VIDEO_BYTES,
            rights_confirmed=True,
            rights_holder="测试公司",
            model_name="untrusted-model",
        )


def test_progress_callback_failure_does_not_change_task_lifecycle() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    callback_attempts = 0

    def failing_callback(_task) -> None:
        nonlocal callback_attempts
        callback_attempts += 1
        raise RuntimeError("UI observer failed")

    service = TranscriptionService(
        repository,
        model_loader=lambda _name: FakeModel(),
        command_runner=fake_media_runner([]),
    )
    task = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        on_progress=failing_callback,
    )

    saved = repository.get_task(task.task_id)
    assert task.status == TaskStatus.SUCCEEDED
    assert saved is not None
    assert saved.status == TaskStatus.SUCCEEDED
    assert callback_attempts == 4


def test_correction_approval_persists_after_sqlite_restart(tmp_path: Path) -> None:
    database = tmp_path / "transcripts.sqlite3"
    repository = SQLiteRepository(database)
    service = TranscriptionService(
        repository,
        model_loader=lambda _name: FakeModel(),
        command_runner=fake_media_runner([]),
    )
    task = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
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
    with pytest.raises(MediaValidationError, match="仅支持 MP4、MOV"):
        service.create_task(
            media_name="audio.mp3",
            media_type="audio/mpeg",
            media_bytes=b"ID3authorized-audio",
            rights_confirmed=True,
            rights_holder="测试公司",
        )

    with pytest.raises(MediaValidationError, match="扩展名不匹配"):
        service.create_task(
            media_name="fake.mp4",
            media_type="video/mp4",
            media_bytes=b"not-an-mp4",
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
            media_name="silent.mp4",
            media_type="video/mp4",
            media_bytes=VIDEO_BYTES,
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
            media_name="owned.mp4",
            media_type="video/mp4",
            media_bytes=VIDEO_BYTES,
            rights_confirmed=True,
            rights_holder="测试公司",
        )
    assert attempts == 2


@pytest.mark.parametrize("duration", ["0", "-1", "NaN", "Infinity", "901"])
def test_invalid_or_excessive_duration_is_rejected(duration: str) -> None:
    def invalid_duration(args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [{"codec_type": "audio"}],
                    "format": {"duration": duration},
                }
            ),
        )

    service = TranscriptionService(
        MockRepository(candidates=[], tasks=[]), command_runner=invalid_duration
    )
    with pytest.raises(MediaValidationError):
        service.create_task(
            media_name="owned.mp4",
            media_type="video/mp4",
            media_bytes=VIDEO_BYTES,
            rights_confirmed=True,
            rights_holder="测试公司",
        )


def test_failure_callback_and_error_are_user_safe() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    updates = []

    class BrokenModel(FakeModel):
        def transcribe(self, path: str, **options):
            raise RuntimeError(f"secret path: {path}")

    service = TranscriptionService(
        repository,
        model_loader=lambda _name: BrokenModel(),
        command_runner=fake_media_runner([]),
    )
    with pytest.raises(TranscriptionError) as caught:
        service.create_task(
            media_name="owned.mp4",
            media_type="video/mp4",
            media_bytes=VIDEO_BYTES,
            rights_confirmed=True,
            rights_holder="测试公司",
            on_progress=updates.append,
        )

    assert caught.value.code == "asr_failed"
    assert caught.value.task_id == updates[0].task_id
    assert "secret path" not in caught.value.user_message
    assert updates[-1].status == TaskStatus.FAILED
    assert updates[-1].error_message == caught.value.user_message


def test_low_confidence_segments_require_explicit_review_before_new_approval() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    service = TranscriptionService(
        repository,
        model_loader=lambda _name: FakeModel(),
        command_runner=fake_media_runner([]),
    )
    task = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
    )
    low_confidence = [
        TranscriptSegment(
            start=0,
            end=1,
            text="需要人工复核。",
            confidence=0.5,
            needs_review=False,
        )
    ]

    draft = service.save_revision(
        task.task_id, low_confidence, reviewer="校对员", approve=False
    )
    assert draft.status == TranscriptStatus.DRAFT
    with pytest.raises(TranscriptionError) as caught:
        service.save_revision(
            task.task_id, low_confidence, reviewer="校对员", approve=True
        )
    assert caught.value.code == "review_required"

    approved = service.save_revision(
        task.task_id,
        [low_confidence[0].model_copy(update={"reviewed": True})],
        reviewer="校对员",
        approve=True,
    )
    assert approved.status == TranscriptStatus.APPROVED
    assert approved.corrected_segments[0].reviewed is True


def test_export_revision_lookup_strictly_follows_task_pointer() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    service = TranscriptionService(
        repository,
        model_loader=lambda _name: FakeModel(),
        command_runner=fake_media_runner([]),
    )
    task = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
    )
    draft = repository.list_transcript_revisions(task.task_id)[0]
    approved = TranscriptRevision(
        **draft.model_dump(
            exclude={"revision_id", "revision_number", "status", "reviewer"}
        ),
        revision_id="approved-but-not-selected",
        revision_number=2,
        status=TranscriptStatus.APPROVED,
        reviewer="历史校对员",
    )
    repository.save_transcript_revision(approved)

    assert service.get_approved_revision(task.task_id) is None
    repository.save_task(
        task.model_copy(update={"approved_revision_id": approved.revision_id})
    )
    assert service.get_approved_revision(task.task_id) == approved


def test_sqlite_revision_insert_rejects_version_overwrite(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "append-only.sqlite3")
    task = TranscriptionService(
        repository,
        model_loader=lambda _name: FakeModel(),
        command_runner=fake_media_runner([]),
    ).create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
    )
    original = repository.list_transcript_revisions(task.task_id)[0]
    conflicting = TranscriptRevision(
        **original.model_dump(exclude={"revision_id", "corrected_segments"}),
        revision_id="different-id",
        corrected_segments=[
            original.corrected_segments[0].model_copy(update={"text": "覆盖内容"})
        ],
    )

    with pytest.raises(ValueError, match="版本号已经存在"):
        repository.save_transcript_revision(conflicting)
    saved = repository.get_transcript_revision(original.revision_id)
    assert saved is not None
    assert saved.corrected_segments[0].text != "覆盖内容"
