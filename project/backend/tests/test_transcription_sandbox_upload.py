"""Sandbox upload must stay free and must not invoke a local/cloud ASR model."""

from fastapi.testclient import TestClient

from project.backend.app.api.v1 import transcriptions as transcriptions_api
from project.backend.app.core.config import ASRMode
from project.backend.app.core.deps import get_transcription_service
from project.backend.app.main import app
from src.repositories.mock import MockRepository
from src.services.transcription import TranscriptionService


def test_sandbox_upload_returns_demo_without_loading_asr(monkeypatch):
    model_loads: list[str] = []
    service = TranscriptionService(
        MockRepository(),
        model_loader=lambda model: model_loads.append(model),
    )
    monkeypatch.setattr(transcriptions_api, "ASR_MODE", ASRMode.SANDBOX)
    app.dependency_overrides[get_transcription_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/transcriptions/upload",
                files={"file": ("demo.mp4", b"not-a-real-video", "video/mp4")},
                data={
                    "rights_confirmed": "true",
                    "rights_holder": "测试客户",
                    "model_name": "fun-asr",
                    "language": "zh",
                },
            )
    finally:
        app.dependency_overrides.pop(get_transcription_service, None)

    assert response.status_code == 202
    assert response.json()["status"] == "succeeded"
    assert response.json()["stage"] == "演示完成"
    assert response.json()["is_mock"] is True
    assert model_loads == []
