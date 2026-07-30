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
    def __init__(
        self,
        *,
        outcome_unknown: bool = False,
        service_error: bool = False,
    ) -> None:
        self.outcome_unknown = outcome_unknown
        self.service_error = service_error
        self.get_job_calls = 0
        self.submitted_requests: list[AvatarSubmitRequest] = []
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
        self.submitted_requests.append(request)
        if self.outcome_unknown:
            raise AvatarProviderError(
                "提交响应丢失",
                kind=ProviderErrorKind.CONNECTION,
                outcome_unknown=True,
            )
        if self.service_error:
            raise AvatarProviderError(
                "系统繁忙，请联系平台运营商！",
                kind=ProviderErrorKind.SERVICE,
            )
        return self.snapshot.model_copy(
            update={"idempotency_key": request.idempotency_key}
        )

    def get_job(self, job_id: str) -> AvatarJobSnapshot:
        self.get_job_calls += 1
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


class RecoverableVoiceProvider(FakeAvatarProvider):
    def __init__(self) -> None:
        super().__init__()
        self.resume_calls = 0

    def submit(self, request: AvatarSubmitRequest) -> AvatarJobSnapshot:
        self.submitted_requests.append(request)
        return AvatarJobSnapshot(
            job_id="voice-tts:tts-task-101",
            idempotency_key=request.idempotency_key,
            status=AvatarProviderStatus.RUNNING,
            progress=10,
            stage="克隆声音合成中",
        )

    def resume_submit(
        self,
        request: AvatarSubmitRequest,
        pending_job_id: str,
    ) -> AvatarJobSnapshot:
        self.resume_calls += 1
        assert request.video_name == "数字人视频1"
        assert pending_job_id == "voice-tts:tts-task-101"
        return AvatarJobSnapshot(
            job_id="provider-video-101",
            idempotency_key=request.idempotency_key,
            status=AvatarProviderStatus.QUEUED,
            progress=5,
            stage="公司云端排队中",
            provider_job_id="provider-video-101",
        )


class BusyThenQueuedVideoProvider(RecoverableVoiceProvider):
    def resume_submit(
        self,
        request: AvatarSubmitRequest,
        pending_job_id: str,
    ) -> AvatarJobSnapshot:
        self.resume_calls += 1
        if self.resume_calls == 1:
            return AvatarJobSnapshot(
                job_id=pending_job_id,
                idempotency_key=request.idempotency_key,
                status=AvatarProviderStatus.FAILED,
                progress=100,
                stage="视频提交失败",
                error_kind=ProviderErrorKind.SERVICE,
                error_message="系统繁忙，请联系平台运营商！",
            )
        return AvatarJobSnapshot(
            job_id="provider-video-retry-101",
            idempotency_key=request.idempotency_key,
            status=AvatarProviderStatus.QUEUED,
            progress=5,
            stage="公司云端排队中",
            provider_job_id="provider-video-retry-101",
        )


class BusyThenUnknownVideoProvider(RecoverableVoiceProvider):
    def resume_submit(
        self,
        request: AvatarSubmitRequest,
        pending_job_id: str,
    ) -> AvatarJobSnapshot:
        self.resume_calls += 1
        if self.resume_calls == 1:
            return AvatarJobSnapshot(
                job_id=pending_job_id,
                idempotency_key=request.idempotency_key,
                status=AvatarProviderStatus.FAILED,
                progress=100,
                stage="视频提交失败",
                error_kind=ProviderErrorKind.SERVICE,
                error_message="系统繁忙，请联系平台运营商！",
            )
        if self.resume_calls == 2:
            return AvatarJobSnapshot(
                job_id=pending_job_id,
                idempotency_key=request.idempotency_key,
                status=AvatarProviderStatus.OUTCOME_UNKNOWN,
                progress=0,
                stage="视频提交结果待核对",
                error_kind=ProviderErrorKind.OUTCOME_UNKNOWN,
                error_message="视频提交连接中断，供应商是否接单暂不确定。",
            )
        raise AssertionError("结果不确定后不得再次提交视频")


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


def test_avatar_service_assigns_video_names_and_passes_final_name_to_provider() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FakeAvatarProvider()
    service = AvatarService(repository, provider)

    first = service.submit(
        _request("avatar-name-0001").model_copy(update={"keyword": "AI获客"}),
        avatar_name="授权形象",
        voice_name="授权音色",
    )
    second = service.submit(
        _request("avatar-name-0002").model_copy(update={"keyword": "AI获客"}),
        avatar_name="授权形象",
        voice_name="授权音色",
    )
    custom = service.submit(
        _request("avatar-name-0003").model_copy(update={"video_name": "产品介绍"}),
        avatar_name="授权形象",
        voice_name="授权音色",
    )
    duplicate_custom = service.submit(
        _request("avatar-name-0004").model_copy(update={"video_name": "产品介绍"}),
        avatar_name="授权形象",
        voice_name="授权音色",
    )
    fallback = service.submit(
        _request("avatar-name-0005"),
        avatar_name="授权形象",
        voice_name="授权音色",
    )

    assert [task.title for task in (first, second, custom, duplicate_custom, fallback)] == [
        "AI获客1",
        "AI获客2",
        "产品介绍",
        "产品介绍2",
        "数字人视频1",
    ]
    assert [item.video_name for item in provider.submitted_requests] == [
        "AI获客1",
        "AI获客2",
        "产品介绍",
        "产品介绍2",
        "数字人视频1",
    ]


def test_avatar_service_rejects_profile_when_provider_has_no_profiles() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FakeAvatarProvider()
    service = AvatarService(repository, provider)
    request = _request("avatar-profile-mismatch").model_copy(
        update={"profile_id": "local_fast"}
    )

    with pytest.raises(ValueError, match="不支持所选数字人生成方案"):
        service.submit(request, avatar_name="授权形象", voice_name="授权音色")


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


def test_rejected_submit_keeps_original_error_without_querying_a_missing_job() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = FakeAvatarProvider(service_error=True)
    service = AvatarService(repository, provider)

    failed = service.submit(
        _request(), avatar_name="授权形象", voice_name="授权音色"
    )
    refreshed = service.refresh_task(failed.task_id)

    assert refreshed.status == TaskStatus.FAILED
    assert refreshed.provider_status == AvatarProviderStatus.FAILED
    assert refreshed.error_message == "系统繁忙，请联系平台运营商！"
    assert refreshed.backend_job_id is None
    assert provider.get_job_calls == 0


def test_pending_cloned_voice_resumes_without_repeating_voice_submission() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = RecoverableVoiceProvider()
    service = AvatarService(repository, provider)

    pending = service.submit(
        _request(), avatar_name="授权形象", voice_name="授权音色"
    )
    resumed = service.refresh_task(pending.task_id)

    assert pending.status == TaskStatus.RUNNING
    assert pending.backend_job_id == "voice-tts:tts-task-101"
    assert resumed.status == TaskStatus.QUEUED
    assert resumed.backend_job_id == "provider-video-101"
    assert resumed.provider_job_id == "provider-video-101"
    assert provider.resume_calls == 1
    assert provider.get_job_calls == 0
    assert len(provider.submitted_requests) == 1


def test_busy_video_submission_can_be_retried_once_without_recreating_voice() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = BusyThenQueuedVideoProvider()
    service = AvatarService(repository, provider)

    pending = service.submit(
        _request(), avatar_name="授权形象", voice_name="授权音色"
    )
    failed = service.refresh_task(pending.task_id)
    retried = service.retry_failed_video(failed.task_id)

    assert service.can_retry_video_submit(failed) is True
    assert retried.status == TaskStatus.QUEUED
    assert retried.backend_job_id == "provider-video-retry-101"
    assert retried.provider_job_id == "provider-video-retry-101"
    assert retried.retry_count == 1
    assert service.can_retry_video_submit(retried) is False
    assert provider.resume_calls == 2
    assert len(provider.submitted_requests) == 1

    with pytest.raises(ValueError, match="不能安全重试"):
        service.retry_failed_video(retried.task_id)


def test_unknown_video_retry_is_never_reposted_during_refresh() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    provider = BusyThenUnknownVideoProvider()
    service = AvatarService(repository, provider)

    pending = service.submit(
        _request(), avatar_name="授权形象", voice_name="授权音色"
    )
    failed = service.refresh_task(pending.task_id)
    unknown = service.retry_failed_video(failed.task_id)
    refreshed = service.refresh_task(unknown.task_id)

    assert unknown.status == TaskStatus.OUTCOME_UNKNOWN
    assert refreshed.status == TaskStatus.OUTCOME_UNKNOWN
    assert refreshed.error_message == "视频提交连接中断，供应商是否接单暂不确定。"
    assert refreshed.retry_count == 1
    assert provider.resume_calls == 2
    assert len(provider.submitted_requests) == 1


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
