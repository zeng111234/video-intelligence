from __future__ import annotations

import json

import anyio
import httpx
from starlette.requests import Request

from project.backend.app.services import control_plane_client as client


def test_control_plane_url_requires_https_except_loopback(monkeypatch):
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_ENABLED", "true")

    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_URL", "http://api.example.com")
    assert client.control_plane_base_url() == ""
    assert client.control_plane_enabled() is False

    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_URL", "https://api.example.com")
    assert client.control_plane_base_url() == "https://api.example.com"
    assert client.control_plane_enabled() is True

    monkeypatch.setenv(
        "VIDEOINSIGHT_CONTROL_PLANE_URL", "https://api.example.com/nested"
    )
    assert client.control_plane_base_url() == ""

    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_URL", "http://127.0.0.1:8080")
    assert client.control_plane_base_url() == "http://127.0.0.1:8080"


def test_company_client_bypasses_environment_proxy_and_reuses_connection(monkeypatch):
    created: list[dict[str, object]] = []
    requests: list[str] = []

    class FakeClient:
        is_closed = False

        def __init__(self, **kwargs):
            created.append(kwargs)

        async def request(self, method, url, *, headers, content):
            requests.append(url)
            return httpx.Response(200, json={"ok": True})

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setattr(client.httpx, "AsyncClient", FakeClient)
    client._http_client = None

    async def exercise():
        await client._send_request(
            "GET", "https://api.example.com/one", headers={}, content=b""
        )
        await client._send_request(
            "GET", "https://api.example.com/two", headers={}, content=b""
        )
        await client.close_control_plane_http_client()

    anyio.run(exercise)

    assert len(created) == 1
    assert created[0]["trust_env"] is False
    assert created[0]["follow_redirects"] is False
    assert requests == [
        "https://api.example.com/one",
        "https://api.example.com/two",
    ]
    assert client._http_client is None


def test_only_authoritative_routes_are_proxied(monkeypatch):
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_ENABLED", "true")
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_URL", "https://api.example.com")

    assert client.should_proxy_to_control_plane("/api/v1/auth/customer-login")
    assert client.should_proxy_to_control_plane("/api/v1/credits")
    assert client.should_proxy_to_control_plane("/api/v1/copywriting/generate")
    assert client.should_proxy_to_control_plane("/api/v1/admin/codes")
    assert client.should_proxy_to_control_plane("/api/v1/admin/server-status")
    assert not client.should_proxy_to_control_plane("/api/v1/admin/status")
    assert not client.should_proxy_to_control_plane("/api/v1/crawler/search")


def test_upstream_tokens_are_memory_only_and_revocable():
    client.clear_upstream_sessions()
    client.register_upstream_session(
        "local", "remote", role="customer", subject="CUSTOMER-1"
    )
    client.register_upstream_session(
        "local-admin", "remote-admin", role="admin", subject="admin"
    )
    assert client.upstream_session("local") == "remote"
    assert client.active_upstream_customer_session() == "remote"
    assert client.active_upstream_customer_subject() == "CUSTOMER-1"
    assert client.active_upstream_admin_session() == "remote-admin"
    client.clear_upstream_session("local")
    assert client.upstream_session("local") is None
    assert client.active_upstream_customer_session() is None
    assert client.active_upstream_customer_subject() is None
    client.clear_upstream_session("local-admin")
    assert client.active_upstream_admin_session() is None


def test_proxy_uses_customer_for_business_and_admin_only_for_management():
    client.clear_upstream_sessions()
    client.register_upstream_session(
        "local-customer", "remote-customer", role="customer", subject="CUSTOMER-1"
    )
    client.register_upstream_session(
        "local-admin", "remote-admin", role="admin", subject="admin"
    )

    def translated(path: str):
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "headers": [
                    (b"x-customer-token", b"local-customer"),
                    (b"x-admin-token", b"local-admin"),
                ],
                "client": ("127.0.0.1", 50000),
                "server": ("127.0.0.1", 1001),
            }
        )
        return client._translated_headers(request)

    business_headers, business_token = translated("/api/v1/copywriting/generate")
    assert business_token == "local-customer"
    assert business_headers["X-Customer-Token"] == "remote-customer"
    assert "X-Admin-Token" not in business_headers

    admin_headers, admin_token = translated("/api/v1/admin/server-status")
    assert admin_token == "local-admin"
    assert admin_headers["X-Admin-Token"] == "remote-admin"
    assert "X-Customer-Token" not in admin_headers

    recharge_headers, recharge_token = translated("/api/v1/credits/recharge-requests")
    assert recharge_token == "local-admin"
    assert recharge_headers["X-Admin-Token"] == "remote-admin"

    mine_headers, mine_token = translated("/api/v1/credits/recharge-requests/mine")
    assert mine_token == "local-customer"
    assert mine_headers["X-Customer-Token"] == "remote-customer"
    client.clear_upstream_sessions()


def test_desktop_background_workers_require_bound_active_customer(
    monkeypatch, tmp_path
):
    from project.backend.app.core.deps import _desktop_background_work_authorized
    from project.backend.app.core.desktop_owner import ensure_desktop_owner

    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_ENABLED", "true")
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_URL", "https://api.example.com")
    monkeypatch.setenv("VIDEOINSIGHT_RUNTIME_ROOT", str(tmp_path))
    client.clear_upstream_sessions()
    assert _desktop_background_work_authorized() is False

    assert ensure_desktop_owner("CUSTOMER-1") is True
    client.register_upstream_session(
        "local", "remote", role="customer", subject="CUSTOMER-1"
    )
    assert _desktop_background_work_authorized() is True

    client.clear_upstream_sessions()
    client.register_upstream_session(
        "local-other", "remote-other", role="customer", subject="CUSTOMER-2"
    )
    assert _desktop_background_work_authorized() is False
    client.clear_upstream_sessions()


def test_proxied_login_sets_local_media_cookie(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_ENABLED", "true")
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_URL", "https://api.example.com")
    monkeypatch.setenv("VIDEOINSIGHT_RUNTIME_ROOT", str(tmp_path))
    client.clear_upstream_sessions()

    async def fake_send_request(method, url, *, headers, content):
        assert method == "POST"
        assert url.endswith("/api/v1/auth/customer-login")
        assert json.loads(content) == {"code": "CUSTOMER-1"}
        return httpx.Response(
            200,
            json={
                "token": "upstream-secret-token",
                "role": "customer",
                "code": "CUSTOMER-1",
                "name": "测试客户",
                "balance": "100",
            },
        )

    monkeypatch.setattr(client, "_send_request", fake_send_request)
    body = json.dumps({"code": "CUSTOMER-1"}).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/auth/customer-login",
            "raw_path": b"/api/v1/auth/customer-login",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 1001),
        },
        receive,
    )

    response = anyio.run(client.proxy_control_plane_request, request)
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["token"] != "upstream-secret-token"
    assert client.upstream_session(payload["token"]) == "upstream-secret-token"
    cookie = "\n".join(response.headers.getlist("set-cookie"))
    assert "vi_customer_media_token=" in cookie
    assert 'vi_admin_media_token=""' in cookie
    assert "Max-Age=0" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/api/v1/" in cookie
    assert "upstream-secret-token" not in cookie
    client.clear_upstream_sessions()


def test_proxied_login_rejects_role_confusion(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_ENABLED", "true")
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_URL", "https://api.example.com")
    monkeypatch.setenv("VIDEOINSIGHT_RUNTIME_ROOT", str(tmp_path))
    client.clear_upstream_sessions()

    async def fake_send_request(method, url, *, headers, content):
        return httpx.Response(
            200,
            json={
                "token": "upstream-admin-token",
                "role": "admin",
                "username": "admin",
            },
        )

    monkeypatch.setattr(client, "_send_request", fake_send_request)
    body = json.dumps({"code": "CUSTOMER-1"}).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/auth/customer-login",
            "raw_path": b"/api/v1/auth/customer-login",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 1001),
        },
        receive,
    )

    response = anyio.run(client.proxy_control_plane_request, request)
    assert response.status_code == 502
    assert client.active_upstream_admin_session() is None
    assert client.active_upstream_customer_session() is None


def test_proxied_login_rejects_a_second_customer_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_ENABLED", "true")
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_URL", "https://api.example.com")
    monkeypatch.setenv("VIDEOINSIGHT_RUNTIME_ROOT", str(tmp_path))
    client.clear_upstream_sessions()

    from project.backend.app.core.desktop_owner import ensure_desktop_owner

    assert ensure_desktop_owner("CUSTOMER-1") is True
    calls: list[str] = []

    async def fake_send_request(method, url, *, headers, content):
        calls.append(url)
        if url.endswith("/api/v1/auth/logout"):
            assert headers["X-Customer-Token"] == "unused-upstream-token"
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(
            200,
            json={
                "token": "unused-upstream-token",
                "role": "customer",
                "code": "CUSTOMER-2",
                "name": "另一个客户",
                "balance": "100",
            },
        )

    monkeypatch.setattr(client, "_send_request", fake_send_request)
    body = json.dumps({"code": "CUSTOMER-2"}).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/auth/customer-login",
            "raw_path": b"/api/v1/auth/customer-login",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 1001),
        },
        receive,
    )

    response = anyio.run(client.proxy_control_plane_request, request)
    assert response.status_code == 409
    assert "绑定其他客户" in json.loads(response.body)["message"]
    assert calls[-1].endswith("/api/v1/auth/logout")
    assert client.active_upstream_customer_session() is None
    client.clear_upstream_sessions()
