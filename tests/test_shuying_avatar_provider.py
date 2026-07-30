from __future__ import annotations

import json

import pytest

from src.adapters.avatar import (
    AvatarProviderError,
    ShuyingLegacyAvatarProvider,
    build_avatar_provider,
)
from src.models import AvatarProviderStatus, AvatarSubmitRequest, ProviderMode


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
                    {"code": 1, "data": {"ossurl": "https://media.example.com/voice.mp3"}}
                ).encode(),
                "application/json",
            )
        video_bodies.append(body)
        return (
            json.dumps({"code": 1, "data": {"videoId": "video-job-short-name"}}).encode(),
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
    assert "SHUYING_AVATAR_AUDIO_ALLOWED_HOSTS" in capability.missing_configuration


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


def test_status_maps_success_and_download_checks_allowed_host():
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
    assert payload[4:8] == b"ftyp"
    assert mime_type == "video/mp4"


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
            return json.dumps({"code": 1, "data": {"id": 10079}}).encode(), "application/json"
        assert url.endswith("/modelDetail")
        return json.dumps({"code": 1, "data": {"status": 1}}).encode(), "application/json"

    provider = _provider(
        transport=transport,
        assets_manifest_path=str(tmp_path / "assets.json"),
        model_upload_url="https://upload.example.com/system/basic/test",
        model_upload_allowed_hosts="upload.example.com,media.example.com",
        file_upload_transport=lambda url, filename, mime_type, path, timeout: (
            json.dumps({"code": 1, "path": "https://media.example.com/training.mp4"}).encode(),
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
        return json.dumps({"code": 1, "data": {"id": 10080}}).encode(), "application/json"

    def upload_transport(url, filename, mime_type, path, timeout):
        upload_urls.append(url)
        return (
            json.dumps({"code": 1, "path": "https://media.example.com/training.mp4"}).encode(),
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

    assert upload_urls == [
        "https://upload.example.com/system/basic/test?is_video=1"
    ]
    assert asset.asset_id == "shuying-avatar-10080"


def test_cloud_avatar_training_is_hidden_for_an_explicit_unapproved_upload_host():
    provider = _provider(
        model_upload_url="https://upload.example.com/system/basic/test",
        model_upload_allowed_hosts="media.example.com",
    )

    assert provider.capabilities().supports_cloud_avatar_training is False


def test_voice_cloning_is_hidden_until_a_dedicated_primary_route_is_configured():
    provider = _provider(
        audio_upload_url="https://upload.example.com/system/basic/test",
        audio_allowed_hosts="media.example.com",
    )

    assert provider.capabilities().supports_voice_cloning is False


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
    assert (tmp_path / "voice_samples" / f"{asset.asset_id}.mp3").read_bytes() == b"authorised-sample"
