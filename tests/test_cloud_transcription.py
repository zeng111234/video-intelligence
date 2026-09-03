from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Event
from time import sleep
from types import SimpleNamespace

import pytest

from src.adapters.video_editor_cloud import CloudProviderError
from src.models import TaskStatus, TranscriptionTask
from src.repositories import MockRepository
from src.services.cloud_transcription import (
    ASRAuthorization,
    ASRAuthorizationStore,
    ASR_PRICE_VERSION,
    AliyunFunASRRuntime,
)
from src.services.transcription import TranscriptionError, TranscriptionService
from src.services.video_editor_cloud import (
    CloudEditorConfiguration,
    CloudProviderMode,
    CloudTranscript,
    ProviderJobSnapshot,
    ProviderJobStatus,
    TranscriptSegment,
)

VIDEO_BYTES = b"\x00\x00\x00\x18ftypisom-authorized-video"


def fake_probe(args, **kwargs):
    assert args[0] == "ffprobe"
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps(
            {
                "streams": [{"codec_type": "audio"}],
                "format": {"duration": "8.5"},
            }
        ),
    )


class FakeCloudRuntime:
    def __init__(
        self,
        confidence: float | None = None,
        words: list[dict[str, object]] | None = None,
    ) -> None:
        self.submissions = 0
        self.confidence = confidence
        self.words = words or []

    def ensure_authorized(self, duration_seconds: float) -> Decimal:
        assert duration_seconds == 8.5
        return Decimal("0.0019")

    def capability(self):
        return {"price_version": ASR_PRICE_VERSION}

    def upload(self, path, *, object_key: str, media_type: str):
        assert Path(path).is_file()
        assert object_key.startswith("asr-input/transcript-")
        assert media_type == "video/mp4"
        return SimpleNamespace()

    def submit(self, asset, *, language: str) -> ProviderJobSnapshot:
        self.submissions += 1
        assert language == "zh"
        return ProviderJobSnapshot(
            provider_name="aliyun_fun_asr",
            provider_job_id="aliyun-job-1",
            provider_stage="transcription_complete",
            status=ProviderJobStatus.SUCCEEDED,
        )

    def query(self, provider_job_id: str) -> ProviderJobSnapshot:
        assert provider_job_id == "aliyun-job-1"
        return ProviderJobSnapshot(
            provider_name="aliyun_fun_asr",
            provider_job_id=provider_job_id,
            provider_stage="transcription_complete",
            status=ProviderJobStatus.SUCCEEDED,
        )

    def fetch_result(self, snapshot: ProviderJobSnapshot) -> CloudTranscript:
        return CloudTranscript(
            provider_name="aliyun_fun_asr",
            transcript="公司云端识别结果。",
            segments=[
                TranscriptSegment(
                    start=0,
                    end=1.2,
                    text="公司云端识别结果。",
                    words=self.words,
                    confidence=self.confidence,
                )
            ],
            duration_seconds=8.5,
            language="zh",
        )


def test_cloud_task_uses_one_provider_submission_and_never_loads_local_model(
    tmp_path: Path,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    runtime = FakeCloudRuntime()
    service = TranscriptionService(
        repository,
        model_loader=lambda _name: pytest.fail("本地模型不得加载"),
        command_runner=fake_probe,
        cloud_runtime=runtime,
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )

    queued = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="fun-asr",
        async_processing=True,
    )
    assert queued.status == TaskStatus.QUEUED
    assert queued.provider_name == "aliyun_fun_asr"
    assert queued.estimated_cost_cny == 0.0019

    completed = service.process_cloud_task(queued.task_id)
    assert completed.status == TaskStatus.SUCCEEDED
    assert completed.stage == "识别完成"
    assert completed.provider_job_id == "aliyun-job-1"
    assert runtime.submissions == 1
    assert completed.secondary_asr_count == 0
    assert completed.llm_review_count == 0
    assert completed.segments[0].confidence is None
    assert completed.segments[0].needs_review is False
    assert completed.segments[0].quality_status == "completed"
    assert completed.uncertain_segment_count == 0
    assert not Path(completed.outputs["source_media_path"]).exists()


def test_cloud_task_preserves_provider_word_timing_for_video_editor(
    tmp_path: Path,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    runtime = FakeCloudRuntime(
        words=[
            {"start": 0.0, "end": 0.42, "text": "公司"},
            {"start": 0.48, "end": 1.1, "text": "云端识别结果"},
        ]
    )
    service = TranscriptionService(
        repository,
        model_loader=lambda _name: pytest.fail("本地模型不得加载"),
        command_runner=fake_probe,
        cloud_runtime=runtime,
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )

    queued = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="fun-asr",
        include_word_timestamps=True,
        async_processing=True,
    )
    completed = service.process_cloud_task(queued.task_id)

    assert completed.word_timestamps_available is True
    assert completed.segments[0].words == runtime.words
    assert completed.segments[0].quality_note == "阿里云识别完成，已保留逐词时间。"


def test_same_cloud_task_is_processed_once_when_worker_and_request_race(
    tmp_path: Path,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    runtime = FakeCloudRuntime()
    upload_started = Event()
    allow_upload = Event()
    original_upload = runtime.upload

    def blocking_upload(*args, **kwargs):
        upload_started.set()
        assert allow_upload.wait(timeout=2)
        return original_upload(*args, **kwargs)

    runtime.upload = blocking_upload
    service = TranscriptionService(
        repository,
        command_runner=fake_probe,
        cloud_runtime=runtime,
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )
    queued = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="fun-asr",
        async_processing=True,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.process_cloud_task, queued.task_id)
        assert upload_started.wait(timeout=2)
        second = executor.submit(service.process_cloud_task, queued.task_id)
        sleep(0.05)
        assert not second.done()
        allow_upload.set()
        results = [first.result(timeout=2), second.result(timeout=2)]

    assert [item.status for item in results] == [
        TaskStatus.SUCCEEDED,
        TaskStatus.SUCCEEDED,
    ]
    assert runtime.submissions == 1


def test_cloud_task_only_marks_explicit_low_confidence_segments(tmp_path: Path) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    runtime = FakeCloudRuntime(confidence=0.62)
    service = TranscriptionService(
        repository,
        command_runner=fake_probe,
        cloud_runtime=runtime,
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )
    queued = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="fun-asr",
        async_processing=True,
    )

    completed = service.process_cloud_task(queued.task_id)

    assert completed.stage == "有 1 段待确认"
    assert completed.uncertain_segment_count == 1
    assert completed.segments[0].confidence == 0.62
    assert completed.segments[0].needs_review is True
    assert completed.segments[0].quality_status == "pending"


def test_cloud_low_confidence_segments_are_ai_reviewed_before_user_confirmation(
    tmp_path: Path,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    runtime = FakeCloudRuntime(confidence=0.62)
    seen: dict[str, object] = {}

    def batch_reviewer(**kwargs):
        seen.update(kwargs)
        return {
            "corrections": [
                {
                    "index": 0,
                    "corrected_text": "公司云端识别的结果。",
                    "note": "已修正断句。",
                    "requires_human_review": False,
                }
            ]
        }

    service = TranscriptionService(
        repository,
        command_runner=fake_probe,
        transcript_batch_reviewer=batch_reviewer,
        cloud_runtime=runtime,
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )
    queued = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="fun-asr",
        async_processing=True,
    )

    completed = service.process_cloud_task(queued.task_id)

    assert completed.stage == "AI 校对完成"
    assert completed.auto_reviewed is True
    assert completed.llm_review_count == 1
    assert completed.uncertain_segment_count == 0
    assert completed.segments[0].text == "公司云端识别的结果。"
    assert completed.segments[0].needs_review is False
    assert completed.segments[0].quality_status == "llm_rewritten"
    assert completed.segments[0].alternatives == ["公司云端识别结果。"]
    assert seen["segments"][0]["needs_review"] is True


def test_legacy_cloud_task_with_no_uncertain_segments_can_finish_ai_review() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    service = TranscriptionService(
        repository,
        transcript_batch_reviewer=lambda **_kwargs: pytest.fail(
            "无待确认片段时不应调用 AI"
        ),
    )
    now = datetime.now().astimezone()
    legacy = TranscriptionTask(
        task_id="transcript-legacy-no-review-needed",
        title="legacy.mp4",
        status=TaskStatus.SUCCEEDED,
        progress=100,
        created_at=now,
        updated_at=now,
        media_name="legacy.mp4",
        media_type="video/mp4",
        rights_confirmed=True,
        rights_holder="测试公司",
        provider_name="aliyun_fun_asr",
        stage="识别完成",
        segments=[
            {
                "start": 0,
                "end": 1,
                "text": "高置信旧转写。",
                "needs_review": False,
                "quality_status": "completed",
            }
        ],
        auto_reviewed=False,
        uncertain_segment_count=0,
    )
    repository.save_task(legacy)

    reviewed = service.review_completed_cloud_task(legacy.task_id)

    assert reviewed.auto_reviewed is True
    assert reviewed.stage == "AI 校对完成"
    assert reviewed.llm_review_count == 0
    assert reviewed.uncertain_segment_count == 0
    assert repository.get_task(legacy.task_id).auto_reviewed is True


def test_cloud_ai_review_keeps_changed_amount_for_human_confirmation(
    tmp_path: Path,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    runtime = FakeCloudRuntime(confidence=0.62)
    runtime.fetch_result = lambda _snapshot: CloudTranscript(
        provider_name="aliyun_fun_asr",
        transcript="优惠9块。",
        segments=[TranscriptSegment(start=0, end=1.2, text="优惠9块。", confidence=0.62)],
        duration_seconds=8.5,
        language="zh",
    )
    service = TranscriptionService(
        repository,
        command_runner=fake_probe,
        transcript_batch_reviewer=lambda **_kwargs: {
            "corrections": [
                {
                    "index": 0,
                    "corrected_text": "优惠90块。",
                    "note": "按上下文修正。",
                    "requires_human_review": False,
                }
            ]
        },
        cloud_runtime=runtime,
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )
    queued = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="fun-asr",
        async_processing=True,
    )

    completed = service.process_cloud_task(queued.task_id)

    assert completed.segments[0].text == "优惠9块。"
    assert completed.segments[0].needs_review is True
    assert completed.segments[0].quality_status == "uncertain"
    assert completed.uncertain_segment_count == 1
    assert "已保留原文" in (completed.segments[0].quality_note or "")


def test_cloud_task_with_provider_job_id_only_queries_existing_job(
    tmp_path: Path,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    runtime = FakeCloudRuntime()
    service = TranscriptionService(
        repository,
        command_runner=fake_probe,
        cloud_runtime=runtime,
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )
    queued = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="fun-asr",
        async_processing=True,
    )
    repository.save_task(
        queued.model_copy(
            update={
                "status": TaskStatus.SUBMITTED,
                "provider_job_id": "aliyun-job-1",
            }
        )
    )

    completed = service.process_cloud_task(queued.task_id)
    assert completed.status == TaskStatus.SUCCEEDED
    assert runtime.submissions == 0


def test_cloud_task_explains_when_provider_finds_no_spoken_words(
    tmp_path: Path,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    runtime = FakeCloudRuntime()

    def fail_without_words(_asset, *, language: str) -> ProviderJobSnapshot:
        assert language == "zh"
        return ProviderJobSnapshot(
            provider_name="aliyun_fun_asr",
            provider_job_id="aliyun-no-words",
            provider_stage="transcription_failed",
            status=ProviderJobStatus.FAILED,
            detail={"code": "ASR_RESPONSE_HAVE_NO_WORDS"},
        )

    runtime.submit = fail_without_words
    service = TranscriptionService(
        repository,
        command_runner=fake_probe,
        cloud_runtime=runtime,
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )
    queued = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="fun-asr",
        async_processing=True,
    )

    with pytest.raises(TranscriptionError, match="没有识别到人声"):
        service.process_cloud_task(queued.task_id)

    failed = repository.get_task(queued.task_id)
    assert failed is not None
    assert "没有识别到人声" in (failed.error_message or "")


def test_cloud_task_upload_failure_is_retryable_before_provider_submission(
    tmp_path: Path,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    runtime = FakeCloudRuntime()

    def fail_upload(*_args, **_kwargs):
        raise CloudProviderError(
            "OSS 上传连接失败，结果未确认。",
            kind="connection",
            outcome_unknown=True,
        )

    runtime.upload = fail_upload
    service = TranscriptionService(
        repository,
        command_runner=fake_probe,
        cloud_runtime=runtime,
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )
    queued = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="fun-asr",
        async_processing=True,
    )

    with pytest.raises(TranscriptionError):
        service.process_cloud_task(queued.task_id)

    failed = repository.get_task(queued.task_id)
    assert failed is not None
    assert failed.status == TaskStatus.FAILED
    assert failed.provider_status == "failed"
    assert failed.provider_job_id is None
    assert failed.stage == "素材上传失败"
    assert "尚未创建云端识别任务" in (failed.error_message or "")
    assert Path(failed.outputs["source_media_path"]).is_file()
    assert runtime.submissions == 0


def test_cloud_query_connection_failure_preserves_task_for_reconnect(
    tmp_path: Path,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    runtime = FakeCloudRuntime()
    service = TranscriptionService(
        repository,
        command_runner=fake_probe,
        cloud_runtime=runtime,
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )
    queued = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="fun-asr",
        async_processing=True,
    )
    repository.save_task(
        queued.model_copy(
            update={
                "status": TaskStatus.SUBMITTED,
                "provider_job_id": "aliyun-job-1",
                "provider_status": "running",
            }
        )
    )

    def fail_query(_provider_job_id: str):
        raise CloudProviderError(
            "任务状态查询连接失败，已自动重试一次；当前结果未知。",
            kind="connection",
            outcome_unknown=True,
        )

    runtime.query = fail_query

    with pytest.raises(TranscriptionError) as caught:
        service.process_cloud_task(queued.task_id)

    assert caught.value.code == "cloud_asr_outcome_unknown"
    assert caught.value.task_id == queued.task_id
    assert "重新连接查询" in caught.value.user_message
    preserved = repository.get_task(queued.task_id)
    assert preserved is not None
    assert preserved.status == TaskStatus.OUTCOME_UNKNOWN
    assert preserved.provider_job_id == "aliyun-job-1"
    assert "任务编号已保留" in (preserved.error_message or "")

    runtime.query = FakeCloudRuntime().query
    completed = service.process_cloud_task(queued.task_id)
    assert completed.status == TaskStatus.SUCCEEDED
    assert completed.error_message is None


def test_lost_submit_response_reconnects_same_task_with_idempotent_replay(
    tmp_path: Path,
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    runtime = FakeCloudRuntime()
    original_submit = runtime.submit

    def lose_first_submit_response(asset, *, language: str):
        runtime.submit = original_submit
        raise CloudProviderError(
            "云端提交回执丢失。",
            kind="connection",
            outcome_unknown=True,
        )

    runtime.submit = lose_first_submit_response
    service = TranscriptionService(
        repository,
        command_runner=fake_probe,
        cloud_runtime=runtime,
        cloud_storage_directory=tmp_path,
        cloud_poll_interval_seconds=0,
    )
    queued = service.create_task(
        media_name="owned.mp4",
        media_type="video/mp4",
        media_bytes=VIDEO_BYTES,
        rights_confirmed=True,
        rights_holder="测试公司",
        model_name="fun-asr",
        async_processing=True,
    )

    with pytest.raises(TranscriptionError) as caught:
        service.process_cloud_task(queued.task_id)

    preserved = repository.get_task(queued.task_id)
    assert caught.value.task_id == queued.task_id
    assert preserved is not None
    assert preserved.status == TaskStatus.OUTCOME_UNKNOWN
    assert preserved.provider_job_id is None
    assert preserved.provider_object_key
    assert "提交回执" in (preserved.error_message or "")

    completed = service.reconnect_cloud_task(queued.task_id)

    assert completed.task_id == queued.task_id
    assert completed.status == TaskStatus.SUCCEEDED
    assert runtime.submissions == 1
    assert len(repository.list_tasks()) == 1


def test_authorization_is_versioned_and_caps_each_task(tmp_path: Path) -> None:
    store = ASRAuthorizationStore(tmp_path / "authorization.json")
    config = CloudEditorConfiguration(
        provider_mode=CloudProviderMode.ALIYUN,
        workspace_id="workspace",
        dashscope_api_key="secret",
        oss_bucket="bucket",
        access_key_id="access",
        access_key_secret="secret",
    )
    runtime = AliyunFunASRRuntime(
        authorization_store=store,
        configuration=config,
        providers=SimpleNamespace(),
    )

    assert runtime.missing_configuration == []
    with pytest.raises(RuntimeError, match="费用尚未授权"):
        runtime.ensure_authorized(60)

    store.save(
        ASRAuthorization(
            confirmed=True,
            confirmed_at=datetime.now().astimezone(),
        )
    )
    assert runtime.ensure_authorized(900) == Decimal("0.1980")
    with pytest.raises(RuntimeError, match="超过管理员设置"):
        runtime.ensure_authorized(1000)


def test_cloud_hotwords_are_rejected_without_a_paid_submission(
    tmp_path: Path,
) -> None:
    runtime = FakeCloudRuntime()
    service = TranscriptionService(
        MockRepository(candidates=[], tasks=[]),
        command_runner=fake_probe,
        cloud_runtime=runtime,
        cloud_storage_directory=tmp_path,
    )
    with pytest.raises(TranscriptionError, match="暂未接入专有词表"):
        service.create_task(
            media_name="owned.mp4",
            media_type="video/mp4",
            media_bytes=VIDEO_BYTES,
            rights_confirmed=True,
            rights_holder="测试公司",
            model_name="fun-asr",
            hotwords="品牌词",
        )
    assert runtime.submissions == 0
