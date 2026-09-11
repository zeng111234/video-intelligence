from __future__ import annotations

from http.client import RemoteDisconnected
import json

import pytest

from src.adapters.avatar import (
    AvatarProviderError,
    ShuyingLegacyAvatarProvider,
    _default_transport,
    build_avatar_provider,
)
from src.models import (
    AvatarAsset,
    AvatarAssetKind,
    AvatarProviderStatus,
    AvatarSubmitRequest,
    ProviderErrorKind,
    ProviderMode,
)


def _request() -> AvatarSubmitRequest:
    return AvatarSubmitRequest(
        script_text="这是一条公司云数字人口播测试。",
        avatar_id="robot-company-01",
        voice_id="voice-company-01",
        speech_rate=1.1,
        rights_holder="测试公司",
        script_rights_confirmed=True,
        avatar_rights_confirmed=True,
        voice_rights_confirmed=True,
        idempotency_key="shuying-test-request-0001",
    )


def _provider(**overrides) -> ShuyingLegacyAvatarProvider:
    values = {
        "base_url": "https://avatar-gateway.example.com",
        "api_code": "test-api-code",
        "avatars_json": json.dumps(
            [{"asset_id": "robot-company-01", "name": "公司数字人"}]
        ),
        "voices_json": json.dumps(
            [{"asset_id": "voice-company-01", "name": "公司音色"}]
        ),
        "result_allowed_hosts": "media.example.com",
        "enabled": True,
    }
    values.update(overrides)
    return ShuyingLegacyAvatarProvider(**values)


def test_shuying_capability_fails_closed_when_gateway_is_unknown():
    provider = ShuyingLegacyAvatarProvider(
        base_url="",
        api_code="configured-secret",
        enabled=True,
    )

    capability = provider.capabilities()

    assert capability.mode == ProviderMode.PRODUCTION
    assert capability.enabled is False
    assert "SHUYING_AVATAR_BASE_URL" in capability.missing_configuration
    assert "SHUYING_AVATAR_API_CODE" not in capability.missing_configuration


def test_shuying_capability_rejects_insecure_gateway_url():
    provider = _provider(base_url="http://avatar-gateway.example.com")

    capability = provider.capabilities()

    assert capability.enabled is False
    assert "SHUYING_AVATAR_BASE_URL(必须为HTTPS)" in capability.missing_configuration


def test_factory_selects_shuying_cloud(monkeypatch):
    monkeypatch.setenv("AVATAR_PROVIDER_MODE", "shuying_cloud")
    monkeypatch.setenv("SHUYING_AVATAR_ENABLED", "true")
    monkeypatch.setenv("SHUYING_AVATAR_API_CODE", "test-api-code")

    provider = build_avatar_provider()

    assert isinstance(provider, ShuyingLegacyAvatarProvider)


def test_submit_uses_single_key_legacy_form_protocol_without_retry():
    calls: list[tuple[str, str, dict[str, str], bytes | None, float]] = []
    responses = [
        {
            "code": 1,
            "data": {"ossurl": "https://media.example.com/voice.mp3"},
        },
        {"code": 1, "data": {"videoId": "video-job-100"}},
    ]

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, headers, body, timeout))
        return json.dumps(responses[len(calls) - 1]).encode(), "application/json"

    provider = _provider(transport=transport)
    request = _request().model_copy(update={"video_name": "接口验证1"})
    snapshot = provider.submit(request)
    duplicate = provider.submit(request)

    assert snapshot.status == AvatarProviderStatus.QUEUED
    assert snapshot.job_id == "video-job-100"
    assert duplicate == snapshot
    assert len(calls) == 2
    assert [call[1] for call in calls] == [
        "https://avatar-gateway.example.com/voice",
        "https://avatar-gateway.example.com/video",
    ]
    assert all(call[0] == "POST" for call in calls)
    assert all("multipart/form-data" in call[2]["Content-Type"] for call in calls)
    assert b'name="api_code"' in calls[0][3]
    assert b"test-api-code" in calls[0][3]
    assert b'name="audioUrl"' in calls[1][3]
    assert b"\xe6\x8e\xa5\xe5\x8f\xa3\xe9\xaa\x8c\xe8\xaf\x811" in calls[1][3]
    assert b'name="aspect_ratio"' not in calls[1][3]
    assert b'name="resolution"' not in calls[1][3]
    assert b'name="background"' not in calls[1][3]


def test_submit_maps_custom_avatar_to_its_provider_model_id(tmp_path):
    calls: list[tuple[str, str, dict[str, str], bytes | None, float]] = []
    responses = [
        {"code": 1, "data": {"ossurl": "https://media.example.com/voice.mp3"}},
        {"code": 1, "data": {"videoId": "video-job-custom-avatar"}},
    ]

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, headers, body, timeout))
        return json.dumps(responses[len(calls) - 1]).encode(), "application/json"

    provider = _provider(
        assets_manifest_path=str(tmp_path / "assets.json"), transport=transport
    )
    provider._upsert_custom_asset(
        AvatarAsset(
            asset_id="shuying-avatar-21920",
            kind=AvatarAssetKind.AVATAR,
            name="大树1",
            authorized=True,
            status="ready",
            source_type="custom",
        ),
        provider_asset_id="21920",
    )

    snapshot = provider.submit(
        _request().model_copy(
            update={"avatar_id": "shuying-avatar-21920", "video_name": "自定义形象"}
        )
    )

    assert snapshot.job_id == "video-job-custom-avatar"
    assert b'name="modeid"' in calls[1][3]
    assert b"\r\n21920\r\n" in calls[1][3]
    assert b"shuying-avatar-21920" not in calls[1][3]


def test_shared_ready_manifest_assets_enable_existing_paid_pair_without_retraining(
    tmp_path, monkeypatch
):
    calls: list[str] = []

    def transport(method, url, headers, body, timeout):
        calls.append(url)
        if url.endswith("/voice_2"):
            return json.dumps(
                {"code": 1, "data": "tts-existing-1"}
            ).encode(), "application/json"
        if url.endswith("/voice_tts_info"):
            return (
                json.dumps(
                    {
                        "code": 1,
                        "data": {"ossurl": "https://media.example.com/existing.mp3"},
                    }
                ).encode(),
                "application/json",
            )
        if url.endswith("/video"):
            return json.dumps(
                {"code": 1, "data": {"videoId": "video-existing-1"}}
            ).encode(), "application/json"
        raise AssertionError(f"unexpected URL: {url}")

    runtime_root = tmp_path / "runtime"
    manifest = runtime_root / "data" / "avatar_assets" / "shuying_cloud.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "asset_id": "shuying-avatar-21920",
                        "kind": "avatar",
                        "name": "大树1",
                        "authorized": True,
                        "status": "ready",
                        "source_type": "custom",
                        "shared": True,
                        "provider_asset_id": "21920",
                    },
                    {
                        "asset_id": "shuying-voice-7869",
                        "kind": "voice",
                        "name": "大树1",
                        "authorized": True,
                        "status": "ready",
                        "source_type": "custom_clone",
                        "shared": True,
                        "provider_asset_id": "7869",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIDEOINSIGHT_RUNTIME_ROOT", str(runtime_root))
    provider = ShuyingLegacyAvatarProvider(
        base_url="https://avatar-gateway.example.com/aif",
        api_code="test-api-code",
        avatars_json="[]",
        voices_json="[]",
        result_allowed_hosts="media.example.com",
        assets_manifest_path="data/avatar_assets/shuying_cloud.json",
        enabled=True,
        transport=transport,
    )

    capability = provider.capabilities()
    assets = provider.list_assets()

    assert provider.assets_manifest_path == manifest.resolve()
    assert capability.enabled is True
    assert capability.supports_voice_cloning is False
    assert provider._can_use_cloned_voice() is True
    assert {item.asset_id for item in assets} == {
        "shuying-avatar-21920",
        "shuying-voice-7869",
    }
    assert all(item.shared for item in assets)
    assert provider.is_shared_asset("shuying-voice-7869") is True
    assert provider.has_ready_shared_asset(
        asset_id="shuying-avatar-21920",
        kind=AvatarAssetKind.AVATAR,
        provider_asset_id="21920",
    )
    assert provider.has_ready_shared_asset(
        asset_id="shuying-voice-7869",
        kind=AvatarAssetKind.VOICE,
        provider_asset_id="7869",
    )
    assert not provider.has_ready_shared_asset(
        asset_id="shuying-voice-7869",
        kind=AvatarAssetKind.VOICE,
        provider_asset_id="wrong-provider-id",
    )

    snapshot = provider.submit(
        _request().model_copy(
            update={
                "avatar_id": "shuying-avatar-21920",
                "voice_id": "shuying-voice-7869",
                "idempotency_key": "reuse-paid-assets-0001",
            }
        )
    )
    assert snapshot.job_id == "video-existing-1"
    assert [url.rsplit("/", 1)[-1] for url in calls] == [
        "voice_2",
        "voice_tts_info",
        "video",
    ]
    assert not any("voice_clone" in url or url.endswith("/model") for url in calls)


def test_submit_can_render_upload_audio_before_video():
    calls: list[tuple[str, str, dict[str, str], bytes | None, float]] = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, headers, body, timeout))
        if url.endswith("/system/basic/test"):
            return (
                json.dumps(
                    {
                        "code": 1,
                        "path": "http://audio.example.com/speech.mp3",
                    }
                ).encode(),
                "application/json",
            )
        return (
            json.dumps({"code": 1, "data": {"videoId": "video-job-101"}}).encode(),
            "application/json",
        )

    provider = _provider(
        audio_mode="edge_tts_upload",
        audio_upload_url="https://upload.example.com/system/basic/test",
        audio_allowed_hosts="audio.example.com",
        audio_renderer=lambda text, rate, voice: b"fake-mp3",
        transport=transport,
    )

    snapshot = provider.submit(_request())

    assert snapshot.job_id == "video-job-101"
    assert [call[1] for call in calls] == [
        "https://upload.example.com/system/basic/test",
        "https://avatar-gateway.example.com/video",
    ]
    assert b'name="file"; filename="speech.mp3"' in calls[0][3]
    assert b"fake-mp3" in calls[0][3]
    assert b"https://audio.example.com/speech.mp3" in calls[1][3]


def test_video_accepts_a_structurally_successful_zero_code_response():
    responses = [
        {"code": 1, "data": {"ossurl": "https://media.example.com/voice.mp3"}},
        {"code": 0, "data": {"videoId": "video-job-zero-code"}},
    ]
    calls = 0

    def transport(method, url, headers, body, timeout):
        nonlocal calls
        response = responses[calls]
        calls += 1
        return json.dumps(response).encode(), "application/json"

    snapshot = _provider(transport=transport).submit(_request())

    assert snapshot.job_id == "video-job-zero-code"


def test_gateway_error_uses_the_supplier_message_field():
    responses = [
        {"code": 1, "data": {"ossurl": "https://media.example.com/voice.mp3"}},
        {"code": 0, "message": "当前形象不可用"},
    ]
    calls = 0

    def transport(method, url, headers, body, timeout):
        nonlocal calls
        response = responses[calls]
        calls += 1
        return json.dumps(response).encode(), "application/json"

    with pytest.raises(AvatarProviderError, match="当前形象不可用"):
        _provider(transport=transport).submit(_request())


def test_video_name_is_trimmed_to_the_legacy_gateway_limit():
    video_bodies = []

    def transport(method, url, headers, body, timeout):
        if url.endswith("/voice"):
            return (
                json.dumps(
                    {
                        "code": 1,
                        "data": {"ossurl": "https://media.example.com/voice.mp3"},
                    }
                ).encode(),
                "application/json",
            )
        video_bodies.append(body)
        return (
            json.dumps(
                {"code": 1, "data": {"videoId": "video-job-short-name"}}
            ).encode(),
            "application/json",
        )

    long_name = "餐" * 50 + "不能发送"
    request = _request().model_copy(update={"video_name": long_name})

    _provider(transport=transport).submit(request)

    assert ("餐" * 50).encode() in video_bodies[0]
    assert "不能发送".encode() not in video_bodies[0]


def test_edge_audio_mode_requires_safe_upload_configuration():
    provider = _provider(
        audio_mode="edge_tts_upload",
        audio_upload_url="http://upload.example.com/system/basic/test",
        audio_allowed_hosts="",
    )

    capability = provider.capabilities()

    assert capability.enabled is False
    assert (
        "SHUYING_AVATAR_AUDIO_UPLOAD_URL(必须为HTTPS)"
        in capability.missing_configuration
    )


def test_submit_connection_failure_is_outcome_unknown_and_not_retried():
    call_count = 0

    def transport(method, url, headers, body, timeout):
        nonlocal call_count
        call_count += 1
        raise AvatarProviderError(
            "连接中断",
            outcome_unknown=True,
        )

    provider = _provider(transport=transport)

    with pytest.raises(AvatarProviderError) as caught:
        provider.submit(_request())

    assert caught.value.outcome_unknown is True
    assert call_count == 1


@pytest.mark.parametrize(
    ("provider_duration", "expected_seconds"),
    [("1.07", 2), ("1.067", 2), (999, 999), (1200, 1200)],
)
def test_status_maps_success_and_download_checks_allowed_host(
    provider_duration,
    expected_seconds,
):
    def transport(method, url, headers, body, timeout):
        assert url.endswith("/videoDetail")
        return (
            json.dumps(
                {
                    "code": 1,
                    "data": {
                        "synthesisStatus": 3,
                        "videoUrl": "https://media.example.com/result.mp4",
                        "videoSize": 1234,
                        "duration": provider_duration,
                    },
                }
            ).encode(),
            "application/json",
        )

    def download_transport(method, url, headers, body, timeout):
        assert method == "GET"
        assert url == "https://media.example.com/result.mp4"
        return b"\x00\x00\x00\x18ftypmp42video", "video/mp4"

    provider = _provider(
        transport=transport,
        download_transport=download_transport,
    )

    snapshot = provider.get_job("video-job-100")
    payload, mime_type = provider.download_result("video-job-100")

    assert snapshot.status == AvatarProviderStatus.SUCCEEDED
    assert snapshot.result_mime == "video/mp4"
    assert snapshot.result_size_bytes == 1234
    assert snapshot.estimated_seconds == expected_seconds
    assert payload[4:8] == b"ftyp"
    assert mime_type == "video/mp4"


@pytest.mark.parametrize(
    "provider_duration",
    [None, "", 0, -1, True, {}, "not-a-duration", float("nan"), float("inf")],
)
def test_status_keeps_invalid_provider_duration_unknown(provider_duration):
    def transport(method, url, headers, body, timeout):
        assert url.endswith("/videoDetail")
        return (
            json.dumps(
                {
                    "code": 1,
                    "data": {
                        "synthesisStatus": 3,
                        "videoUrl": "https://media.example.com/result.mp4",
                        "duration": provider_duration,
                    },
                }
            ).encode(),
            "application/json",
        )

    snapshot = _provider(transport=transport).get_job("video-job-duration-unknown")

    assert snapshot.status == AvatarProviderStatus.SUCCEEDED
    assert snapshot.estimated_seconds is None


@pytest.mark.parametrize(
    ("provider_status", "expected_status", "error_field"),
    [
        (4, AvatarProviderStatus.FAILED, {"err_msg": "服务异常：视频合成失败"}),
        (5, AvatarProviderStatus.FAILED, {"failreason": "任务已归档"}),
        (6, AvatarProviderStatus.CANCELLED, {"message": "用户取消任务"}),
        (7, AvatarProviderStatus.FAILED, {"msg": "供应商任务失败"}),
    ],
)
def test_status_maps_legacy_terminal_states(
    provider_status: int,
    expected_status: AvatarProviderStatus,
    error_field: dict[str, str],
):
    def transport(method, url, headers, body, timeout):
        assert url.endswith("/videoDetail")
        return (
            json.dumps(
                {
                    "code": 1,
                    "data": {"synthesisStatus": provider_status, **error_field},
                },
                ensure_ascii=False,
            ).encode(),
            "application/json",
        )

    snapshot = _provider(transport=transport).get_job("video-job-terminal")

    assert snapshot.status == expected_status
    assert snapshot.progress == 100
    assert snapshot.error_message == next(iter(error_field.values()))


def test_download_rejects_unapproved_result_host():
    provider = _provider()
    provider.result_urls["video-job-100"] = "https://attacker.example/result.mp4"

    with pytest.raises(AvatarProviderError) as caught:
        provider.download_result("video-job-100")

    assert "允许的供应商域名" in str(caught.value)


def test_cloud_avatar_training_persists_a_pending_video_asset(tmp_path):
    training_video = tmp_path / "training.mp4"
    training_video.write_bytes(b"not-used-by-fake-upload")

    def transport(method, url, headers, body, timeout):
        if url.endswith("/model"):
            return json.dumps(
                {"code": 1, "data": {"id": 10079}}
            ).encode(), "application/json"
        assert url.endswith("/modelDetail")
        return json.dumps(
            {"code": 1, "data": {"status": 1}}
        ).encode(), "application/json"

    provider = _provider(
        transport=transport,
        assets_manifest_path=str(tmp_path / "assets.json"),
        model_upload_url="https://upload.example.com/system/basic/test",
        model_upload_allowed_hosts="upload.example.com,media.example.com",
        file_upload_transport=lambda url, filename, mime_type, path, timeout: (
            json.dumps(
                {"code": 1, "path": "https://media.example.com/training.mp4"}
            ).encode(),
            "application/json",
        ),
    )

    asset = provider.create_cloud_avatar(
        name="新形象", training_video_path=training_video, filename="training.mp4"
    )

    assert provider.capabilities().supports_cloud_avatar_training is True
    assert asset.asset_id == "shuying-avatar-10079"
    assert asset.preview_type == "video"
    assert asset.status == "training"
    assert provider.list_assets()[-1].asset_id == asset.asset_id


def test_cloud_avatar_training_reuses_the_configured_audio_upload_entry(tmp_path):
    training_video = tmp_path / "training.mp4"
    training_video.write_bytes(b"not-used-by-fake-upload")
    upload_urls = []

    def transport(method, url, headers, body, timeout):
        assert url.endswith("/model")
        return json.dumps(
            {"code": 1, "data": {"id": 10080}}
        ).encode(), "application/json"

    def upload_transport(url, filename, mime_type, path, timeout):
        upload_urls.append(url)
        return (
            json.dumps(
                {"code": 1, "path": "https://media.example.com/training.mp4"}
            ).encode(),
            "application/json",
        )

    provider = _provider(
        transport=transport,
        assets_manifest_path=str(tmp_path / "assets.json"),
        audio_upload_url="https://upload.example.com/system/basic/test",
        audio_allowed_hosts="media.example.com",
        file_upload_transport=upload_transport,
    )

    assert provider.capabilities().supports_cloud_avatar_training is True

    asset = provider.create_cloud_avatar(
        name="复用入口的新形象",
        training_video_path=training_video,
        filename="training.mp4",
    )

    assert upload_urls == ["https://upload.example.com/system/basic/test?is_video=1"]
    assert asset.asset_id == "shuying-avatar-10080"


def test_cloud_avatar_training_is_hidden_for_an_explicit_unapproved_upload_host():
    provider = _provider(
        model_upload_url="https://upload.example.com/system/basic/test",
        model_upload_allowed_hosts="media.example.com",
    )

    assert provider.capabilities().supports_cloud_avatar_training is False


def test_voice_cloning_reuses_the_configured_gateway_route_and_key(tmp_path):
    calls: list[tuple[str, str, dict[str, str], bytes | None, float]] = []
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"authorised-sample")

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, headers, body, timeout))
        assert url == "https://avatar-gateway.example.com/apiai/ai/voice_clone"
        return json.dumps(
            {"code": 0, "data": {"task_id": "voice-task-101"}}
        ).encode(), "application/json"

    provider = _provider(
        base_url="https://avatar-gateway.example.com/apiai/aif",
        audio_upload_url="https://upload.example.com/system/basic/test",
        audio_allowed_hosts="media.example.com",
        assets_manifest_path=str(tmp_path / "assets.json"),
        transport=transport,
        file_upload_transport=lambda url, filename, mime_type, path, timeout: (
            json.dumps(
                {"code": 1, "path": "https://media.example.com/sample.mp3"}
            ).encode(),
            "application/json",
        ),
    )
    assert provider.voice_base_url == "https://avatar-gateway.example.com/apiai/ai"

    asset = provider.create_voice_clone(
        name="新声音", sample_path=sample, filename="sample.mp3", mime_type="audio/mpeg"
    )

    assert provider.capabilities().supports_voice_cloning is True
    assert asset.asset_id == "shuying-voice-voice-task-101"
    assert asset.status == "training"
    assert b'name="api_code"' in calls[0][3]
    assert b"test-api-code" in calls[0][3]


def test_cloned_voice_waits_as_a_resumable_job_before_submitting_video(tmp_path):
    calls: list[str] = []
    voice_status_calls = 0

    def transport(method, url, headers, body, timeout):
        nonlocal voice_status_calls
        calls.append(url)
        if url.endswith("/voice_2"):
            return json.dumps(
                {"code": 1, "data": "tts-task-101"}
            ).encode(), "application/json"
        if url.endswith("/voice_tts_info"):
            voice_status_calls += 1
            data = (
                {"status": 1}
                if voice_status_calls == 1
                else {"ossurl": "https://media.example.com/cloned-voice.mp3"}
            )
            return json.dumps({"code": 1, "data": data}).encode(), "application/json"
        if url.endswith("/video"):
            return json.dumps(
                {"code": 1, "data": {"videoId": "video-job-voice"}}
            ).encode(), "application/json"
        raise AssertionError(f"unexpected URL: {url}")

    provider = _provider(
        assets_manifest_path=str(tmp_path / "assets.json"),
        audio_upload_url="https://upload.example.com/system/basic/test",
        audio_allowed_hosts="media.example.com",
        transport=transport,
    )
    provider._upsert_custom_asset(
        AvatarAsset(
            asset_id="shuying-voice-7869",
            kind=AvatarAssetKind.VOICE,
            name="大树1",
            authorized=True,
            status="ready",
            source_type="custom_clone",
        ),
        provider_asset_id="7869",
    )
    request = _request().model_copy(
        update={
            "voice_id": "shuying-voice-7869",
            "video_name": "异步声音测试",
        }
    )

    pending = provider.submit(request)
    resumed = provider.resume_submit(request, pending.job_id)

    assert pending.status == AvatarProviderStatus.RUNNING
    assert pending.job_id == "voice-tts:tts-task-101"
    assert pending.stage == "克隆声音合成中"
    assert resumed.status == AvatarProviderStatus.QUEUED
    assert resumed.job_id == "video-job-voice"
    assert sum(url.endswith("/voice_2") for url in calls) == 1
    assert sum(url.endswith("/video") for url in calls) == 1


def test_default_transport_wraps_remote_disconnect_as_safe_provider_error(monkeypatch):
    def disconnect(*args, **kwargs):
        raise RemoteDisconnected("remote closed")

    monkeypatch.setattr("src.adapters.avatar.urlopen", disconnect)

    with pytest.raises(AvatarProviderError) as error:
        _default_transport(
            "POST",
            "https://avatar-gateway.example.com/voice_tts_info",
            {},
            b"tts_task_id=tts-task-101",
            1.0,
        )

    assert error.value.kind.value == "connection"
    assert error.value.outcome_unknown is True
    assert "远端中断" in str(error.value)


def test_cloned_voice_query_disconnect_keeps_tts_task_resumable(tmp_path):
    calls: list[str] = []
    voice_status_calls = 0

    def transport(method, url, headers, body, timeout):
        nonlocal voice_status_calls
        calls.append(url)
        if url.endswith("/voice_2"):
            return json.dumps({"code": 1, "data": "tts-task-disconnect"}).encode(), "application/json"
        if url.endswith("/voice_tts_info"):
            voice_status_calls += 1
            if voice_status_calls == 1:
                raise AvatarProviderError(
                    "数字人服务连接被远端中断，请稍后重试。",
                    kind=ProviderErrorKind.CONNECTION,
                )
            return json.dumps(
                {
                    "code": 1,
                    "data": {"ossurl": "https://media.example.com/cloned-voice.mp3"},
                }
            ).encode(), "application/json"
        if url.endswith("/video"):
            return json.dumps(
                {"code": 1, "data": {"videoId": "video-job-voice"}}
            ).encode(), "application/json"
        raise AssertionError(f"unexpected URL: {url}")

    provider = _provider(
        assets_manifest_path=str(tmp_path / "assets.json"),
        audio_allowed_hosts="media.example.com",
        transport=transport,
    )
    provider._upsert_custom_asset(
        AvatarAsset(
            asset_id="shuying-voice-7869",
            kind=AvatarAssetKind.VOICE,
            name="大树1",
            authorized=True,
            status="ready",
            source_type="custom_clone",
        ),
        provider_asset_id="7869",
    )
    request = _request().model_copy(update={"voice_id": "shuying-voice-7869"})

    pending = provider.submit(request)
    resumed = provider.resume_submit(request, pending.job_id)

    assert pending.status == AvatarProviderStatus.RUNNING
    assert pending.job_id == "voice-tts:tts-task-disconnect"
    assert resumed.status == AvatarProviderStatus.QUEUED
    assert resumed.job_id == "video-job-voice"
    assert sum(url.endswith("/voice_2") for url in calls) == 1
    assert sum(url.endswith("/video") for url in calls) == 1


def test_pending_voice_sample_is_saved_without_submitting_a_clone(tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"authorised-sample")
    provider = _provider(assets_manifest_path=str(tmp_path / "assets.json"))

    asset = provider.store_pending_voice_sample(
        name="待训练声音", sample_path=sample, filename="sample.mp3"
    )

    assert asset.status == "pending_configuration"
    assert asset.source_type == "pending_clone"
    assert asset.preview_type == "audio"
    assert asset.preview_url == f"/api/v1/avatar/assets/{asset.asset_id}/media"
    assert provider.list_assets()[-1].status == "pending_configuration"
    assert (
        tmp_path / "voice_samples" / f"{asset.asset_id}.mp3"
    ).read_bytes() == b"authorised-sample"


def test_pending_voice_sample_can_be_submitted_after_the_clone_route_is_restored(
    tmp_path,
):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"authorised-sample")

    def transport(method, url, headers, body, timeout):
        if url.endswith("/voice_clone_status_2"):
            return json.dumps(
                {"code": 1, "data": {"status": 1}}
            ).encode(), "application/json"
        assert url == "https://avatar-gateway.example.com/voice_clone"
        return json.dumps(
            {"code": 0, "data": {"task_id": "voice-task-resumed"}}
        ).encode(), "application/json"

    provider = _provider(
        assets_manifest_path=str(tmp_path / "assets.json"),
        audio_upload_url="https://upload.example.com/system/basic/test",
        audio_allowed_hosts="media.example.com",
        transport=transport,
        file_upload_transport=lambda url, filename, mime_type, path, timeout: (
            json.dumps(
                {"code": 1, "path": "https://media.example.com/sample.mp3"}
            ).encode(),
            "application/json",
        ),
    )
    pending = provider.store_pending_voice_sample(
        name="待训练声音", sample_path=sample, filename="sample.mp3"
    )

    resumed = provider.resume_pending_voice_clone(pending.asset_id)

    assert resumed.asset_id == "shuying-voice-voice-task-resumed"
    assert resumed.status == "training"
    assert all(item.asset_id != pending.asset_id for item in provider.list_assets())
