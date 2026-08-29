from __future__ import annotations

import httpx

from project.backend.app.services import remote_avatar
from src.models import ProviderErrorKind


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


def test_remote_avatar_hides_only_the_legacy_builtin_avatar(monkeypatch):
    payload = [
        {
            "asset_id": "10078",
            "kind": "avatar",
            "name": "11",
            "preview_url": "https://media.example.com/legacy.mp4",
            "authorized": True,
            "status": "ready",
            "source_type": "built_in",
        },
        {
            "asset_id": "customer-avatar-11",
            "kind": "avatar",
            "name": "11",
            "preview_url": "https://media.example.com/customer.mp4",
            "authorized": True,
            "status": "ready",
            "source_type": "custom",
        },
        {
            "asset_id": "shuying-avatar-21920",
            "kind": "avatar",
            "name": "大树1",
            "preview_url": "https://media.example.com/dashu.mp4",
            "authorized": True,
            "status": "ready",
            "source_type": "custom",
        },
    ]

    class FakeResponse:
        @staticmethod
        def json():
            return payload

    provider = remote_avatar.RemoteAvatarProvider()
    monkeypatch.setattr(provider, "_get", lambda _path: FakeResponse())

    assets = provider.list_assets()

    assert [asset.asset_id for asset in assets] == [
        "customer-avatar-11",
        "shuying-avatar-21920",
    ]


def test_remote_avatar_capability_preserves_safe_authorization_reason(monkeypatch):
    provider = remote_avatar.RemoteAvatarProvider()

    def unavailable(_path):
        raise remote_avatar.AvatarProviderError(
            "请先登录客户账号或管理员账号，再使用数字人。",
            kind=ProviderErrorKind.AUTHORIZATION,
        )

    monkeypatch.setattr(provider, "_get", unavailable)

    capability = provider.capabilities()

    assert not capability.enabled
    assert capability.permission_status == "unavailable_authorization"
    assert capability.missing_configuration == [
        "请先登录客户账号或管理员账号，再使用数字人。"
    ]
