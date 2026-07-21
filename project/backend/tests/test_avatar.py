"""数字人生成 API 测试。"""

from fastapi.testclient import TestClient

from project.backend.app.main import app


def test_capabilities_are_explicit():
    with TestClient(app) as client:
        resp = client.get("/api/v1/avatar/capabilities")

    assert resp.status_code == 200
    data = resp.json()
    assert data["provider_name"] == "sandbox_avatar"
    assert data["mode"] == "sandbox"
    assert data["enabled"] is True
    assert data["missing_configuration"] == []


def test_assets_only_return_authorized_public_assets():
    with TestClient(app) as client:
        resp = client.get("/api/v1/avatar/assets")

    assert resp.status_code == 200
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

        get_resp = client.get(f"/api/v1/avatar/jobs/{created['task_id']}")
        assert get_resp.status_code == 200
        assert get_resp.json()["task_id"] == created["task_id"]


def test_jobs_list_uses_real_history_not_hardcoded_samples():
    with TestClient(app) as client:
        resp = client.get("/api/v1/avatar/jobs")

    assert resp.status_code == 200
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

    assert resp.status_code == 200
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
