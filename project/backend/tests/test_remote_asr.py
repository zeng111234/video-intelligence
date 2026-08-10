from __future__ import annotations

from decimal import Decimal

from project.backend.app.services import control_plane_client
from project.backend.app.services.remote_asr import RemoteAliyunASRRuntime


class _Response:
    status_code = 200
    is_success = True

    @staticmethod
    def json():
        return {"enabled": True, "billing_authorized": True}


def test_remote_asr_admin_authorization_uses_memory_token(monkeypatch):
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, content):
            captured.update(url=url, headers=headers, content=content)
            return _Response()

    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_URL", "https://api.example.com")
    monkeypatch.setattr(
        "project.backend.app.services.remote_asr.httpx.Client", FakeClient
    )
    control_plane_client.clear_upstream_sessions()
    control_plane_client.register_upstream_session(
        "local-admin", "remote-admin", role="admin"
    )
    try:
        result = RemoteAliyunASRRuntime().authorize_provider(
            confirmed=True,
            per_task_cap_cny=Decimal("0.20"),
        )
        assert result["billing_authorized"] is True
        headers = captured["headers"]
        assert isinstance(headers, dict)
        assert headers["X-Admin-Token"] == "remote-admin"
        assert str(headers["Idempotency-Key"]).startswith(
            "asr-admin-authorization-"
        )
    finally:
        control_plane_client.clear_upstream_sessions()
