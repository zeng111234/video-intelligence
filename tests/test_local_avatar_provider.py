from __future__ import annotations

import json
from pathlib import Path

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
