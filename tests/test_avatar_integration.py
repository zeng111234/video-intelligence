from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.adapters.avatar import AvatarProviderError, InternalAvatarProvider
from src.models import (
    AvatarAsset,
    AvatarAssetKind,
    AvatarCapability,
    AvatarJobSnapshot,
    AvatarProviderStatus,
    AvatarSubmitRequest,
    AvatarTask,
    ProviderErrorKind,
    ProviderMode,
    TaskStatus,
)
from src.repositories import MockRepository
from src.services.avatar import AvatarService


def _request(key: str = "avatar-test-123") -> AvatarSubmitRequest:
    return AvatarSubmitRequest(
        script_text="这是一段已授权的测试文案。",
        source_task_id="transcription-1",
        source_revision_id="revision-2",
        avatar_id="avatar-1",
        voice_id="voice-1",
        speech_rate=1.0,
        aspect_ratio="9:16",
        resolution="1080x1920",
        background="brand",
        rights_holder="测试公司",
        script_rights_confirmed=True,
        avatar_rights_confirmed=True,
        voice_rights_confirmed=True,
        idempotency_key=key,
    )


class FakeAvatarProvider:
    def __init__(self, *, outcome_unknown: bool = False) -> None:
        self.outcome_unknown = outcome_unknown
        self.snapshot = AvatarJobSnapshot(
            job_id="provider-job-1",
            idempotency_key="avatar-test-123",
            status=AvatarProviderStatus.QUEUED,
            progress=5,
            stage="供应商排队中",
            provider_job_id="provider-job-1",
            estimated_cost_cny=1.5,
            estimated_seconds=90,
        )

    def capabilities(self) -> AvatarCapability:
        return AvatarCapability(
            provider_name="fake-avatar",
            display_name="测试数字人",
            mode=ProviderMode.PRODUCTION,
            enabled=True,
            permission_status="authorized",
            max_script_chars=240,
            estimated_cost_cny=1.5,
            estimated_seconds=90,
        )

    def list_assets(self) -> list[AvatarAsset]:
        return [
            AvatarAsset(
                asset_id="avatar-1",
                kind=AvatarAssetKind.AVATAR,
                name="授权形象",
                authorized=True,
            ),
            AvatarAsset(
                asset_id="voice-1",
                kind=AvatarAssetKind.VOICE,
                name="授权音色",
                authorized=True,
            ),
        ]

    def submit(self, request: AvatarSubmitRequest) -> AvatarJobSnapshot:
        if self.outcome_unknown:
            raise AvatarProviderError(
                "提交响应丢失",
                kind=ProviderErrorKind.CONNECTION,
                outcome_unknown=True,
            )
        return self.snapshot.model_copy(
            update={"idempotency_key": request.idempotency_key}
        )

    def get_job(self, job_id: str) -> AvatarJobSnapshot:
        return self.snapshot.model_copy(
            update={
                "job_id": job_id,
                "status": AvatarProviderStatus.SUCCEEDED,
                "progress": 100,
                "stage": "生成成功",
                "result_mime": "video/mp4",
            }
        )

    def find_job(self, idempotency_key: str) -> AvatarJobSnapshot | None:
        return self.snapshot.model_copy(update={"idempotency_key": idempotency_key})

    def download_result(self, job_id: str) -> tuple[bytes, str]:
        return b"\x00\x00\x00\x18ftypmp42test-video", "video/mp4"


def test_internal_provider_retries_get_once_but_never_repeats_submit() -> None:
    get_calls = 0

    def get_transport(method, url, headers, body, timeout):
        nonlocal get_calls
        get_calls += 1
        if get_calls == 1:
            raise AvatarProviderError("temporary", kind=ProviderErrorKind.CONNECTION)
        payload = {
            "code": 1,
            "msg": "ok",
            "data": {
                "provider_name": "company",
                "display_name": "公司服务",
                "mode": "sandbox",
                "enabled": True,
                "permission_status": "authorized",
            },
        }
        return json.dumps(payload).encode(), "application/json"

    provider = InternalAvatarProvider(
        "http://127.0.0.1:8080",
        "secret",
        enabled=True,
        transport=get_transport,
    )
    assert provider.capabilities().enabled is True
    assert get_calls == 2

    post_calls = 0

    def post_transport(method, url, headers, body, timeout):
        nonlocal post_calls
        post_calls += 1
        raise AvatarProviderError(
            "response lost",
            kind=ProviderErrorKind.CONNECTION,
            outcome_unknown=True,
        )

    provider.transport = post_transport
    with pytest.raises(AvatarProviderError, match="response lost"):
        provider.submit(_request())
    assert post_calls == 1


def test_avatar_service_persists_real_task_and_refreshes_status() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FakeAvatarProvider()
    service = AvatarService(repository, provider)

    task = service.submit(_request(), avatar_name="授权形象", voice_name="授权音色")

    stored = repository.get_task(task.task_id)
    assert isinstance(stored, AvatarTask)
    assert stored.script_text == "这是一段已授权的测试文案。"
    assert stored.source_revision_id == "revision-2"
    assert stored.status == TaskStatus.QUEUED
    assert stored.is_mock is False

    refreshed = service.refresh_task(task.task_id)
    assert refreshed.status == TaskStatus.SUCCEEDED
    assert refreshed.provider_status == AvatarProviderStatus.SUCCEEDED


def test_unknown_submit_is_saved_and_reconciled_by_idempotency_key() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FakeAvatarProvider(outcome_unknown=True)
    service = AvatarService(repository, provider)

    task = service.submit(_request(), avatar_name="授权形象", voice_name="授权音色")

    assert task.status == TaskStatus.FAILED
    assert task.provider_status == AvatarProviderStatus.OUTCOME_UNKNOWN
    assert task.retry_count == 1

    reconciled = service.refresh_task(task.task_id)
    assert reconciled.status == TaskStatus.QUEUED
    assert reconciled.provider_job_id == "provider-job-1"


def test_result_is_saved_only_after_mp4_and_ffprobe_validation(
    tmp_path: Path, monkeypatch
) -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FakeAvatarProvider()
    service = AvatarService(repository, provider, result_directory=tmp_path)
    task = service.submit(_request(), avatar_name="授权形象", voice_name="授权音色")
    task = service.refresh_task(task.task_id)
    monkeypatch.setattr(service, "_validate_with_ffprobe", lambda path: None)

    completed = service.download_result(task.task_id)

    assert completed.result_path is not None
    assert Path(completed.result_path).is_file()
    assert completed.outputs["video"] == completed.result_path
