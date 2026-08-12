from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from project.backend.app.desktop import DesktopOperationLogMiddleware


def _test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(DesktopOperationLogMiddleware)

    @app.post("/api/v1/transcriptions/url")
    def create_transcription():
        return {"ok": True}

    @app.get("/api/v1/transcriptions/task-1")
    def get_transcription():
        return {"ok": True}

    return app


def test_desktop_mutation_is_logged_without_query_or_body(caplog) -> None:
    caplog.set_level(logging.INFO, logger="project.backend.app.desktop")
    secret = "must-not-appear"

    response = TestClient(_test_app()).post(
        f"/api/v1/transcriptions/url?activation_code={secret}",
        json={"url": f"https://example.invalid/video?token={secret}"},
    )

    assert response.status_code == 200
    assert "method=POST" in caplog.text
    assert "path=/api/v1/transcriptions/url" in caplog.text
    assert "status=200" in caplog.text
    assert secret not in caplog.text


def test_desktop_polling_get_does_not_flood_operation_log(caplog) -> None:
    caplog.set_level(logging.INFO, logger="project.backend.app.desktop")

    response = TestClient(_test_app()).get("/api/v1/transcriptions/task-1")

    assert response.status_code == 200
    assert "桌面操作完成" not in caplog.text
