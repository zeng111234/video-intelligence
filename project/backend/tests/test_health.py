"""健康检查端点单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

# 确保项目根目录在 Python 路径中
_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from project.backend.app.main import app  # noqa: E402


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_non_sensitive_desktop_mode(monkeypatch):
    monkeypatch.setenv("VIDEOINSIGHT_DESKTOP_CLIENT", "true")
    monkeypatch.setenv("VIDEOINSIGHT_DESKTOP_DEMO", "true")
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_ENABLED", "false")
    response = TestClient(app).get("/health")
    payload = response.json()
    assert payload["desktop_client"] is True
    assert payload["desktop_demo"] is True
    assert payload["control_plane_enabled"] is False
    assert "token" not in payload
    assert "owner" not in payload
