from __future__ import annotations

from fastapi.testclient import TestClient

from project.backend.app.api.v1 import notifications
from project.backend.app.main import app


def test_storage_locations_report_the_active_runtime_root(tmp_path, monkeypatch):
    monkeypatch.setattr(notifications.backend_config, "RUNTIME_ROOT", tmp_path)

    response = TestClient(app).get("/api/v1/user/storage-locations")

    assert response.status_code == 200
    assert response.json() == {
        "data_directory": str(tmp_path / "data"),
        "log_directory": str(tmp_path / "data" / "logs"),
        "primary_log_path": str(tmp_path / "data" / "logs" / "desktop.log"),
    }


def test_open_storage_location_only_opens_a_known_directory(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(notifications.backend_config, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(notifications, "_open_local_directory", opened.append)
    client = TestClient(app)

    response = client.post(
        "/api/v1/user/storage-locations/open",
        json={"target": "logs"},
    )

    expected = tmp_path / "data" / "logs"
    assert response.status_code == 200
    assert response.json() == {
        "opened": True,
        "target": "logs",
        "path": str(expected),
    }
    assert expected.is_dir()
    assert opened == [expected]

    invalid = client.post(
        "/api/v1/user/storage-locations/open",
        json={"target": "C:\\Windows"},
    )
    assert invalid.status_code == 422
