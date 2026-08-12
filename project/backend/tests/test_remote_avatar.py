from __future__ import annotations

import httpx

from project.backend.app.services import remote_avatar


def test_remote_avatar_uses_admin_session_and_bypasses_environment_proxy(monkeypatch):
    captured: list[dict[str, object]] = []
    request_headers: list[dict[str, str]] = []

    class FakeClient:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _url, *, headers):
            request_headers.append(headers)
            return httpx.Response(200, json=[])

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setattr(remote_avatar, "active_upstream_customer_session", lambda: None)
    monkeypatch.setattr(
        remote_avatar, "active_upstream_admin_session", lambda: "admin-token"
    )
    monkeypatch.setattr(
        remote_avatar, "control_plane_base_url", lambda: "https://xmt.example"
    )
    monkeypatch.setattr(remote_avatar.httpx, "Client", FakeClient)

    assets = remote_avatar.RemoteAvatarProvider().list_assets()

    assert assets == []
    assert captured == [
        {
            "timeout": 60.0,
            "verify": True,
            "follow_redirects": False,
            "trust_env": False,
        }
    ]
    assert request_headers == [
        {"X-Admin-Token": "admin-token", "Accept": "application/json"}
    ]
