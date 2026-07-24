from __future__ import annotations

import json
import os
from pathlib import Path
import time

from src.adapters.avatar import LocalCommandAvatarProvider
from src.models import AvatarAssetKind, AvatarProviderStatus, AvatarSubmitRequest


def _request(profile_id: str) -> AvatarSubmitRequest:
    return AvatarSubmitRequest(
        script_text="这是一段本地数字人测试文案。",
        avatar_id="avatar-local-01",
        voice_id="voice-local-01",
        profile_id=profile_id,
        rights_holder="测试公司",
        script_rights_confirmed=True,
        avatar_rights_confirmed=True,
        voice_rights_confirmed=True,
        idempotency_key=f"local-provider-{profile_id}-001",
    )


def test_local_provider_reports_each_profile_as_disabled_without_commands(tmp_path: Path):
    provider = LocalCommandAvatarProvider(assets_manifest=str(tmp_path / "assets.json"))

    capability = provider.capabilities()

    assert capability.enabled is False
    assert {profile.profile_id for profile in capability.profiles} == {
        "local_fast",
        "local_natural",
        "local_recorded_natural",
    }
    assert all(not profile.enabled for profile in capability.profiles)


def test_local_provider_only_exposes_existing_authorized_assets(tmp_path: Path):
    avatar_path = tmp_path / "avatar.jpg"
    voice_path = tmp_path / "voice.wav"
    avatar_path.write_bytes(b"image")
    voice_path.write_bytes(b"audio")
    manifest = tmp_path / "assets.json"
    manifest.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "asset_id": "avatar-local-01",
                        "kind": "avatar",
                        "name": "本人形象",
                        "path": str(avatar_path),
                        "authorized": True,
                    },
                    {
                        "asset_id": "voice-local-01",
                        "kind": "voice",
                        "name": "本人声音",
                        "path": str(voice_path),
                        "authorized": True,
                    },
                    {
                        "asset_id": "not-authorized",
                        "kind": "avatar",
                        "name": "未授权",
                        "path": str(avatar_path),
                        "authorized": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    provider = LocalCommandAvatarProvider(assets_manifest=str(manifest))

    assets = provider.list_assets()

    assert {(asset.asset_id, asset.kind) for asset in assets} == {
        ("avatar-local-01", AvatarAssetKind.AVATAR),
        ("voice-local-01", AvatarAssetKind.VOICE),
    }
    assert provider.capabilities().profiles[0].enabled is False


def test_local_provider_rejects_submit_until_selected_profile_is_configured(tmp_path: Path):
    provider = LocalCommandAvatarProvider(assets_manifest=str(tmp_path / "assets.json"))

    try:
        provider.submit(_request("local_fast"))
    except Exception as exc:  # AvatarProviderError is intentionally an adapter detail.
        assert "尚未就绪" in str(exc)
    else:
        raise AssertionError("未配置本地模型时不应接受任务")

    assert provider.find_job("local-provider-local_fast-001") is None


def test_local_provider_keeps_profile_disabled_when_required_paths_are_missing(tmp_path: Path):
    avatar_path = tmp_path / "avatar.jpg"
    voice_path = tmp_path / "voice.wav"
    avatar_path.write_bytes(b"image")
    voice_path.write_bytes(b"audio")
    manifest = tmp_path / "assets.json"
    manifest.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "asset_id": "avatar-local-01",
                        "kind": "avatar",
                        "name": "本人形象",
                        "path": "avatar.jpg",
                        "authorized": True,
                    },
                    {
                        "asset_id": "voice-local-01",
                        "kind": "voice",
                        "name": "本人声音",
                        "path": "voice.wav",
                        "authorized": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    provider = LocalCommandAvatarProvider(
        assets_manifest=str(manifest),
        working_directory=str(tmp_path),
        tts_command="python tts.py",
        natural_command="python sadtalker.py",
        natural_required_paths=["missing-checkpoint.safetensors"],
    )

    capability = provider.capabilities()
    natural = next(profile for profile in capability.profiles if profile.profile_id == "local_natural")

    assert capability.enabled is False
    assert natural.enabled is False
    assert any("missing-checkpoint.safetensors" in item for item in natural.missing_configuration)


def test_recorded_audio_profile_does_not_require_tts_command(tmp_path: Path):
    avatar_path = tmp_path / "avatar.jpg"
    voice_path = tmp_path / "voice.wav"
    avatar_path.write_bytes(b"image")
    voice_path.write_bytes(b"audio")
    manifest = tmp_path / "assets.json"
    manifest.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "asset_id": "avatar-local-01",
                        "kind": "avatar",
                        "name": "本人形象",
                        "path": "avatar.jpg",
                        "authorized": True,
                    },
                    {
                        "asset_id": "voice-local-01",
                        "kind": "voice",
                        "name": "本人录音",
                        "path": "voice.wav",
                        "authorized": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    provider = LocalCommandAvatarProvider(
        assets_manifest=str(manifest),
        working_directory=str(tmp_path),
        natural_command="python sadtalker.py",
    )

    capability = provider.capabilities()
    recorded = next(
        profile for profile in capability.profiles if profile.profile_id == "local_recorded_natural"
    )

    assert capability.enabled is True
    assert recorded.enabled is True
    assert "LOCAL_AVATAR_TTS_COMMAND" not in recorded.missing_configuration


def test_local_provider_recovers_completed_result_after_restart(tmp_path: Path):
    output_directory = tmp_path / "jobs"
    result_path = output_directory / "local-avatar-recovered001" / "result.mp4"
    result_path.parent.mkdir(parents=True)
    payload = b"\x00\x00\x00\x18ftypisom" + b"video-data"
    result_path.write_bytes(payload)
    provider = LocalCommandAvatarProvider(output_directory=str(output_directory))

    snapshot = provider.get_job("local-avatar-recovered001")
    downloaded, mime_type = provider.download_result("local-avatar-recovered001")

    assert snapshot.status == AvatarProviderStatus.SUCCEEDED
    assert snapshot.progress == 100
    assert snapshot.result_size_bytes == len(payload)
    assert downloaded == payload
    assert mime_type == "video/mp4"


def test_local_provider_marks_stale_interrupted_job_failed(tmp_path: Path):
    output_directory = tmp_path / "jobs"
    job_directory = output_directory / "local-avatar-stale001"
    job_directory.mkdir(parents=True)
    intermediate = job_directory / "speech.wav"
    intermediate.write_bytes(b"audio")
    old_timestamp = time.time() - LocalCommandAvatarProvider.RECOVERY_GRACE_SECONDS - 60
    os.utime(intermediate, (old_timestamp, old_timestamp))
    os.utime(job_directory, (old_timestamp, old_timestamp))
    provider = LocalCommandAvatarProvider(output_directory=str(output_directory))

    snapshot = provider.get_job("local-avatar-stale001")

    assert snapshot.status == AvatarProviderStatus.FAILED
    assert snapshot.progress == 100
    assert snapshot.stage == "本地生成已中断"
    assert "后端重启" in (snapshot.error_message or "")


def test_local_provider_rechecks_recent_recovered_job_for_result(tmp_path: Path):
    output_directory = tmp_path / "jobs"
    job_directory = output_directory / "local-avatar-recent001"
    job_directory.mkdir(parents=True)
    (job_directory / "speech.wav").write_bytes(b"audio")
    provider = LocalCommandAvatarProvider(output_directory=str(output_directory))

    pending = provider.get_job("local-avatar-recent001")
    payload = b"\x00\x00\x00\x18ftypisom" + b"completed-video"
    (job_directory / "result.mp4").write_bytes(payload)
    recovered = provider.get_job("local-avatar-recent001")

    assert pending.status == AvatarProviderStatus.OUTCOME_UNKNOWN
    assert recovered.status == AvatarProviderStatus.SUCCEEDED
    assert provider.download_result("local-avatar-recent001") == (payload, "video/mp4")
