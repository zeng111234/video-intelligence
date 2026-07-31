from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.models import TaskStatus
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
    def __init__(self) -> None:
        self.submissions = 0

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
    assert completed.stage == "待人工复核"
    assert completed.provider_job_id == "aliyun-job-1"
    assert runtime.submissions == 1
    assert completed.secondary_asr_count == 0
    assert completed.llm_review_count == 0
    assert completed.segments[0].needs_review is True
    assert not Path(completed.outputs["source_media_path"]).exists()


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
