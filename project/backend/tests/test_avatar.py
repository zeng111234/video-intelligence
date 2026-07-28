"""数字人生成 API 测试。"""

from fastapi.testclient import TestClient
import pytest

from project.backend.app.core.deps import get_avatar_service
from project.backend.app.main import app
from src.adapters.avatar import (
    LocalCommandAvatarProvider,
    SandboxAvatarProvider,
    ShuyingLegacyAvatarProvider,
)
from src.repositories.mock import MockRepository
from src.services.avatar import AvatarService


@pytest.fixture(autouse=True)
def sandbox_avatar_service():
    """Keep API tests independent from the developer's configured provider and DB."""
    repository = MockRepository()
    app.dependency_overrides[get_avatar_service] = lambda: AvatarService(
        repository,
        SandboxAvatarProvider(),
    )
    try:
        yield
    finally:
        app.dependency_overrides.clear()


def test_capabilities_are_explicit():
    with TestClient(app) as client:
        resp = client.get("/api/v1/avatar/capabilities")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["provider_name"] == "sandbox_avatar"
    assert data["mode"] == "sandbox"
    assert data["enabled"] is True
    assert data["missing_configuration"] == []


def test_assets_only_return_authorized_public_assets():
    with TestClient(app) as client:
        resp = client.get("/api/v1/avatar/assets")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert {item["kind"] for item in data} == {"avatar", "voice"}
    assert all(item["authorized"] for item in data)


def test_create_and_get_job():
    with TestClient(app) as client:
        assets = client.get("/api/v1/avatar/assets").json()
        avatar = next(item for item in assets if item["kind"] == "avatar")
        voice = next(item for item in assets if item["kind"] == "voice")

        create_resp = client.post(
            "/api/v1/avatar/jobs",
            json={
                "script_text": "大家好，欢迎观看今天的视频。",
                "avatar_id": avatar["asset_id"],
                "voice_id": voice["asset_id"],
                "target_seconds": 45,
                "speech_rate": 1.0,
                "aspect_ratio": "9:16",
                "resolution": "1080x1920",
                "publish_mode": "manual",
                "target_platforms": ["douyin"],
                "idempotency_key": "test-avatar-job-0001",
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["status"] == "running"
        assert created["is_mock"] is True
        assert created["result_url"] is None
        assert created["video_name"] == "数字人视频1"

        get_resp = client.get(f"/api/v1/avatar/jobs/{created['task_id']}")
        assert get_resp.status_code == 200
        assert get_resp.json()["task_id"] == created["task_id"]


def test_create_job_uses_keyword_and_auto_increments_name():
    with TestClient(app) as client:
        assets = client.get("/api/v1/avatar/assets").json()
        avatar = next(item for item in assets if item["kind"] == "avatar")
        voice = next(item for item in assets if item["kind"] == "voice")
        payload = {
            "script_text": "关键词命名测试。",
            "avatar_id": avatar["asset_id"],
            "voice_id": voice["asset_id"],
            "keyword": "企业获客",
        }
        first = client.post(
            "/api/v1/avatar/jobs",
            json={**payload, "idempotency_key": "avatar-keyword-name-0001"},
        )
        second = client.post(
            "/api/v1/avatar/jobs",
            json={**payload, "idempotency_key": "avatar-keyword-name-0002"},
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["video_name"] == "企业获客1"
    assert second.json()["video_name"] == "企业获客2"


def test_jobs_list_uses_real_history_not_hardcoded_samples():
    with TestClient(app) as client:
        resp = client.get("/api/v1/avatar/jobs")

    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


def test_missing_job_is_404():
    with TestClient(app) as client:
        resp = client.get("/api/v1/avatar/jobs/nonexistent")

    assert resp.status_code == 404


def test_media_is_not_available_for_sandbox_job():
    with TestClient(app) as client:
        assets = client.get("/api/v1/avatar/assets").json()
        avatar = next(item for item in assets if item["kind"] == "avatar")
        voice = next(item for item in assets if item["kind"] == "voice")
        created = client.post(
            "/api/v1/avatar/jobs",
            json={
                "script_text": "这是一条演示任务。",
                "avatar_id": avatar["asset_id"],
                "voice_id": voice["asset_id"],
                "idempotency_key": "test-avatar-job-media",
            },
        ).json()

        resp = client.get(f"/api/v1/avatar/jobs/{created['task_id']}/media")

    assert resp.status_code == 400


def test_legacy_generate_accepts_old_tts_fields():
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/avatar/generate",
            json={
                "avatar_type": "image",
                "audio_type": "tts",
                "tts_text": "旧页面提交的文案。",
                "tts_voice": "sandbox-voice-cn-female-01",
                "speech_rate": 1.0,
            },
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["task_id"].startswith("avatar-")
    assert data["status"] == "running"
    assert data["video_url"] is None


def test_config_reports_upload_disabled():
    with TestClient(app) as client:
        resp = client.get("/api/v1/avatar/config")

    assert resp.status_code == 200
    data = resp.json()
    assert data["supports_upload"] is False


def test_local_asset_upload_updates_manifest(monkeypatch, tmp_path):
    manifest = tmp_path / "assets.json"
    monkeypatch.setenv("LOCAL_AVATAR_ASSETS_MANIFEST", str(manifest))
    provider = LocalCommandAvatarProvider(
        assets_manifest=str(manifest),
        natural_command="python sadtalker.py",
    )
    app.dependency_overrides[get_avatar_service] = lambda: AvatarService(
        MockRepository(), provider
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/avatar/assets/upload",
                data={
                    "kind": "avatar",
                    "name": "本人形象",
                    "rights_confirmed": "true",
                    "rights_holder": "测试公司",
                },
                files={"file": ("me.png", b"image-bytes", "image/png")},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["kind"] == "avatar"
    assert data["name"] == "本人形象"
    assert data["authorized"] is True
    assert data["preview_url"].startswith("/api/v1/avatar/assets/")
    assert manifest.exists()


def test_local_asset_upload_accepts_browser_recording_webm(monkeypatch, tmp_path):
    """浏览器 MediaRecorder 的默认 WebM 录音可作为本地口播素材上传。"""
    manifest = tmp_path / "assets.json"
    monkeypatch.setenv("LOCAL_AVATAR_ASSETS_MANIFEST", str(manifest))
    provider = LocalCommandAvatarProvider(
        assets_manifest=str(manifest),
        natural_command="python sadtalker.py",
    )
    app.dependency_overrides[get_avatar_service] = lambda: AvatarService(
        MockRepository(), provider
    )

    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/avatar/assets/upload",
                data={
                    "kind": "voice",
                    "name": "浏览器录音",
                    "rights_confirmed": "true",
                    "rights_holder": "测试公司",
                },
                files={"file": ("recording.webm", b"webm-bytes", "audio/webm")},
            )
            media_resp = client.get(resp.json()["preview_url"])
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "voice"
    assert resp.json()["name"] == "浏览器录音"
    assert resp.json()["preview_type"] == "audio"
    assert media_resp.status_code == 200
    assert media_resp.headers["content-type"] == "audio/webm"
    assert media_resp.content == b"webm-bytes"


def test_pending_cloud_voice_sample_can_be_previewed(tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"authorised-sample")
    provider = ShuyingLegacyAvatarProvider(
        base_url="https://avatar-gateway.example.com",
        api_code="test-api-code",
        avatars_json='[{"asset_id":"avatar-1","name":"测试形象"}]',
        voices_json='[{"asset_id":"voice-1","name":"通用女声"}]',
        result_allowed_hosts="media.example.com",
        assets_manifest_path=str(tmp_path / "assets.json"),
        enabled=True,
    )
    asset = provider.store_pending_voice_sample(
        name="本人声音",
        sample_path=sample,
        filename="sample.mp3",
    )
    app.dependency_overrides[get_avatar_service] = lambda: AvatarService(
        MockRepository(), provider
    )

    try:
        with TestClient(app) as client:
            resp = client.get(asset.preview_url)
    finally:
        app.dependency_overrides.clear()

    assert asset.preview_type == "audio"
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.content == b"authorised-sample"
