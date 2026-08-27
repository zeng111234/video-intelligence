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
        "低置信片段AI口播修订中",
        "AI口播成稿",
        "AI自动成稿",
    ]
    revisions = repository.list_transcript_revisions(task.task_id)
    assert len(revisions) == 1
    assert revisions[0].status == TranscriptStatus.APPROVED
    assert revisions[0].approval_mode == "ai_auto"
    assert task.approved_revision_id == revisions[0].revision_id
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


def test_word_timestamps_are_opt_in_and_preserved_from_provider() -> None:
    seen_options: dict[str, object] = {}

    class WordModel:
        def transcribe(self, path: str, **options):
            seen_options.update(options)
            return (
                [
                    SimpleNamespace(
                        start=0.0,
                        end=1.2,
                        text=" 客户数据库 ",
                        avg_logprob=-0.1,
                        words=[
                            SimpleNamespace(
                                start=0.0,
                                end=0.32,
                                word="客户",
                                probability=0.98,
                            ),
                            SimpleNamespace(
                                start=0.34,
                                end=0.72,
                                word="数据库",
                                probability=0.97,
                            ),
                        ],
                    )
                ],
                SimpleNamespace(language="zh"),
            )

    service = TranscriptionService(
        MockRepository(candidates=[], tasks=[]),
        model_loader=lambda _name: WordModel(),
        command_runner=fake_media_runner([]),
    )
    task = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        include_word_timestamps=True,
    )

    assert seen_options["word_timestamps"] is True
    assert task.word_timestamps_available is True
    assert task.segments[0].words == [
        {"start": 0.0, "end": 0.32, "text": "客户", "probability": 0.98},
        {"start": 0.34, "end": 0.72, "text": "数据库", "probability": 0.97},
    ]


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


def test_missing_ffprobe_reports_user_safe_media_component_error() -> None:
    def missing_tool(_args, **_kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    service = TranscriptionService(
        MockRepository(candidates=[], tasks=[]),
        command_runner=missing_tool,
    )

    with pytest.raises(MediaValidationError) as captured:
        service.create_task(
            media_name="owned.mp4",
            media_type="video/mp4",
            media_bytes=VIDEO_BYTES,
            rights_confirmed=True,
            rights_holder="测试公司",
        )

    assert captured.value.code == "media_tools_unavailable"
    assert "视频检查组件暂不可用" in captured.value.user_message
    assert "WinError" not in captured.value.user_message


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
    assert callback_attempts == 6


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
    with pytest.raises(RuntimeError, match="已自动重试"):
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


def test_low_confidence_segment_is_rewritten_for_voiceover_even_when_rerecognition_matches() -> (
    None
):
    class LowConfidenceModel:
        def transcribe(self, path: str, **options):
            if Path(path).name == "audio.wav":
                assert options["beam_size"] == 5
                return (
                    [
                        SimpleNamespace(
                            start=0.0, end=1.0, text="原始文本", avg_logprob=-0.7
                        )
                    ],
                    SimpleNamespace(language="zh"),
                )
            assert options["beam_size"] == 8
            return (
                [
                    SimpleNamespace(
                        start=0.0, end=1.0, text="原始文本", avg_logprob=-0.1
                    )
                ],
                SimpleNamespace(language="zh"),
            )

    repository = MockRepository(candidates=[], tasks=[])
    reviewer_called = False

    def reviewer(**kwargs):
        nonlocal reviewer_called
        reviewer_called = True
        return {"corrected_text": "这是更自然的原始文本。", "note": "已按上下文修订。"}

    task = TranscriptionService(
        repository,
        model_loader=lambda _name: LowConfidenceModel(),
        command_runner=fake_media_runner([]),
        transcript_reviewer=reviewer,
    ).create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
    )

    revision = repository.get_transcript_revision(task.approved_revision_id or "")
    assert revision is not None
    assert revision.corrected_segments[0].text == "这是更自然的原始文本。"
    assert revision.corrected_segments[0].quality_status == "llm_rewritten"
    assert revision.corrected_segments[0].alternatives == ["原始文本"]
    assert task.secondary_asr_count == 1
    assert task.llm_review_count == 1
    assert task.uncertain_segment_count == 0
    assert reviewer_called is True


def test_conflicting_rerecognition_uses_llm_voiceover_rewrite_and_preserves_candidates() -> (
    None
):
    class ConflictingModel:
        def transcribe(self, path: str, **options):
            if Path(path).name == "audio.wav":
                return (
                    [
                        SimpleNamespace(
                            start=0.0, end=1.0, text="今天优惠八十元", avg_logprob=-0.7
                        )
                    ],
                    SimpleNamespace(language="zh"),
                )
            return (
                [
                    SimpleNamespace(
                        start=0.0, end=1.0, text="今天优惠八十块", avg_logprob=-0.1
                    )
                ],
                SimpleNamespace(language="zh"),
            )

    seen: dict[str, object] = {}

    def reviewer(**kwargs):
        seen.update(kwargs)
        return {
            "corrected_text": "今天的优惠是八十块。",
            "note": "已采用最佳口播判断。",
        }

    repository = MockRepository(candidates=[], tasks=[])
    task = TranscriptionService(
        repository,
        model_loader=lambda _name: ConflictingModel(),
        command_runner=fake_media_runner([]),
        transcript_reviewer=reviewer,
    ).create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
    )

    revision = repository.get_transcript_revision(task.approved_revision_id or "")
    assert revision is not None
    segment = revision.corrected_segments[0]
    assert segment.text == "今天的优惠是八十块。"
    assert segment.quality_status == "llm_rewritten"
    assert segment.alternatives == ["今天优惠八十元", "今天优惠八十块"]
    assert task.llm_review_count == 1
    assert task.uncertain_segment_count == 0
    assert seen["candidates"] == ["今天优惠八十元", "今天优惠八十块"]


def test_failed_llm_review_retries_once_and_auto_completes_as_uncertain() -> None:
    class ConflictingModel:
        def transcribe(self, path: str, **options):
            text = "第一候选" if Path(path).name == "audio.wav" else "第二候选"
            return (
                [SimpleNamespace(start=0.0, end=1.0, text=text, avg_logprob=-0.7)],
                SimpleNamespace(language="zh"),
            )

    attempts = 0

    def reviewer(**kwargs):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("network unavailable")

    repository = MockRepository(candidates=[], tasks=[])
    task = TranscriptionService(
        repository,
        model_loader=lambda _name: ConflictingModel(),
        command_runner=fake_media_runner([]),
        transcript_reviewer=reviewer,
    ).create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
    )

    revision = repository.get_transcript_revision(task.approved_revision_id or "")
    assert revision is not None
    assert revision.corrected_segments[0].quality_status == "uncertain"
    assert task.uncertain_segment_count == 1
    assert task.auto_review_error
    assert attempts == 2


def test_manual_text_import_creates_untimed_reviewable_task() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    service = TranscriptionService(repository)

    task = service.import_manual_text(
        text="第一段文案。\n\n第二段文案。",
        rights_confirmed=True,
        rights_holder="测试公司",
        media_name="豆包回填",
        candidate_id="douyin-1",
        source_url="https://v.douyin.com/example/",
    )

    assert task.source_kind == "manual_text"
    assert task.timing_available is False
    assert [segment.start for segment in task.segments] == [None, None]
    assert [segment.end for segment in task.segments] == [None, None]
    assert all(segment.needs_review for segment in task.segments)
    assert service.export_txt(task.segments) == "第一段文案。\n第二段文案。".encode(
        "utf-8"
    )

    with pytest.raises(TranscriptionError) as caught:
        service.save_revision(
            task.task_id,
            task.segments,
            reviewer="校对员",
            approve=True,
        )
    assert caught.value.code == "review_required"

    approved = service.save_revision(
        task.task_id,
        [segment.model_copy(update={"reviewed": True}) for segment in task.segments],
        reviewer="校对员",
        approve=True,
    )
    assert approved.status == TranscriptStatus.APPROVED


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

    assert service.get_approved_revision(task.task_id) is not None
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
