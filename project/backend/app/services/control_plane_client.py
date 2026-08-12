"""Narrow desktop-to-control-plane reverse proxy.

The browser continues to talk only to localhost.  This module forwards the
small authoritative surface (activation, credits, account administration and
paid text generation) to the company server.  Supplier secrets are never
returned to the desktop.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from starlette.responses import JSONResponse, Response

from project.backend.app.core.desktop_owner import ensure_desktop_owner
from project.backend.app.core.security import issue_auth_token, revoke_auth_token

MAX_CONTROL_PLANE_BODY_BYTES = 64 * 1024
_session_tokens: dict[str, str] = {}
_session_roles: dict[str, str] = {}
_session_subjects: dict[str, str] = {}
_active_customer_local_token: str | None = None
_active_admin_local_token: str | None = None
_session_tokens_lock = threading.Lock()
_http_client: httpx.AsyncClient | None = None


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().casefold()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def control_plane_base_url() -> str:
    value = os.getenv("VIDEOINSIGHT_CONTROL_PLANE_URL", "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlsplit(value)
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    valid_scheme = parsed.scheme == "https" or (parsed.scheme == "http" and loopback)
    if (
        not valid_scheme
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return ""
    return value


def control_plane_enabled() -> bool:
    return _flag("VIDEOINSIGHT_CONTROL_PLANE_ENABLED") and bool(
        control_plane_base_url()
    )


def should_proxy_to_control_plane(path: str) -> bool:
    if not control_plane_enabled():
        return False
    if path.startswith("/api/v1/auth/") or path.startswith("/api/v1/credits"):
        return True
    if path.startswith("/api/v1/copywriting"):
        return True
    return any(
        path.startswith(prefix)
        for prefix in (
            "/api/v1/admin/codes",
            "/api/v1/admin/accounts",
            "/api/v1/admin/pricing",
            "/api/v1/admin/server-status",
        )
    )


def register_upstream_session(
    local_token: str,
    upstream_token: str,
    *,
    role: str = "",
    subject: str = "",
) -> None:
    global _active_admin_local_token, _active_customer_local_token
    with _session_tokens_lock:
        _session_tokens[local_token] = upstream_token
        _session_roles[local_token] = role
        _session_subjects[local_token] = subject
        if role == "customer":
            _active_customer_local_token = local_token
        elif role == "admin":
            _active_admin_local_token = local_token


def upstream_session(local_token: str) -> str | None:
    with _session_tokens_lock:
        return _session_tokens.get(local_token)


def clear_upstream_session(local_token: str) -> None:
    global _active_admin_local_token, _active_customer_local_token
    with _session_tokens_lock:
        _session_tokens.pop(local_token, None)
        _session_roles.pop(local_token, None)
        _session_subjects.pop(local_token, None)
        if _active_customer_local_token == local_token:
            _active_customer_local_token = None
        if _active_admin_local_token == local_token:
            _active_admin_local_token = None


def clear_upstream_sessions() -> None:
    """Test/startup helper; session mappings intentionally never persist."""

    global _active_admin_local_token, _active_customer_local_token
    with _session_tokens_lock:
        _session_tokens.clear()
        _session_roles.clear()
        _session_subjects.clear()
        _active_customer_local_token = None
        _active_admin_local_token = None


def active_upstream_customer_session() -> str | None:
    """Return the current desktop customer's remote token from process memory."""

    with _session_tokens_lock:
        if not _active_customer_local_token:
            return None
        return _session_tokens.get(_active_customer_local_token)


def active_upstream_admin_session() -> str | None:
    """Return the current desktop administrator's remote token from memory."""

    with _session_tokens_lock:
        if not _active_admin_local_token:
            return None
        return _session_tokens.get(_active_admin_local_token)


def active_upstream_customer_subject() -> str | None:
    """Return the authenticated customer bound to local worker requests."""

    with _session_tokens_lock:
        if not _active_customer_local_token:
            return None
        return _session_subjects.get(_active_customer_local_token) or None


async def _revoke_unused_upstream_login(role: str, token: str) -> None:
    """Best-effort cleanup when this computer belongs to another customer."""

    header = "X-Admin-Token" if role == "admin" else "X-Customer-Token"
    try:
        await _send_request(
            "POST",
            f"{control_plane_base_url()}/api/v1/auth/logout",
            headers={"Accept": "application/json", header: token},
            content=b"",
        )
    except (httpx.TimeoutException, httpx.NetworkError, ValueError):
        return


def _admin_only_proxy_path(path: str) -> bool:
    if path.startswith("/api/v1/admin"):
        return True
    if path == "/api/v1/credits/adjust":
        return True
    return path.startswith("/api/v1/credits/recharge-requests") and not path.endswith(
        "/mine"
    )


def _translated_headers(request: Request) -> tuple[dict[str, str], str]:
    headers = {"Accept": "application/json", "User-Agent": "VideoInsight-Desktop/0.2"}
    content_type = request.headers.get("content-type", "").strip()
    if content_type:
        headers["Content-Type"] = content_type
    idempotency_key = request.headers.get("idempotency-key", "").strip()
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    admin_token = request.headers.get("X-Admin-Token", "").strip()
    customer_token = request.headers.get("X-Customer-Token", "").strip()
    prefer_admin = _admin_only_proxy_path(request.url.path)
    local_token = (
        (admin_token or customer_token)
        if prefer_admin
        else (customer_token or admin_token)
    )
    if local_token:
        remote_token = upstream_session(local_token)
        if remote_token:
            header = (
                "X-Admin-Token" if local_token == admin_token else "X-Customer-Token"
            )
            headers[header] = remote_token
    return headers, local_token


def _verify_setting() -> bool | str:
    configured = os.getenv("CONTROL_PLANE_CA_BUNDLE", "").strip()
    if not configured:
        return True
    path = Path(configured).expanduser().resolve()
    if not path.is_file():
        raise ValueError("公司服务 CA 证书文件不存在。")
    return str(path)


def _shared_http_client() -> httpx.AsyncClient:
    """Return the direct, reusable client for the company control plane only."""

    global _http_client
    if _http_client is None or _http_client.is_closed:
        timeout = max(
            2.0,
            min(float(os.getenv("CONTROL_PLANE_TIMEOUT_SECONDS", "15")), 60.0),
        )
        _http_client = httpx.AsyncClient(
            timeout=timeout,
            verify=_verify_setting(),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


async def close_control_plane_http_client() -> None:
    """Close the shared company client during application shutdown/tests."""

    global _http_client
    client = _http_client
    _http_client = None
    if client is not None and not client.is_closed:
        await client.aclose()


async def _send_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    content: bytes,
) -> httpx.Response:
    return await _shared_http_client().request(
        method, url, headers=headers, content=content
    )


async def proxy_control_plane_request(request: Request) -> Response:
    body = await request.body()
    if len(body) > MAX_CONTROL_PLANE_BODY_BYTES:
        return JSONResponse(status_code=413, content={"message": "请求内容过大。"})

    headers, local_token = _translated_headers(request)
    if local_token and not any(
        name in headers for name in ("X-Admin-Token", "X-Customer-Token")
    ):
        return JSONResponse(
            status_code=401,
            content={"message": "登录已过期，请重新登录。"},
        )
    method = request.method.upper()
    url = f"{control_plane_base_url()}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    attempts = 2 if method in {"GET", "HEAD"} else 1
    remote_response: httpx.Response | None = None
    try:
        for attempt in range(attempts):
            remote_response = await _send_request(
                method, url, headers=headers, content=body
            )
            if remote_response.status_code not in {502, 503} or attempt + 1 >= attempts:
                break
    except (httpx.TimeoutException, httpx.NetworkError, ValueError):
        return JSONResponse(
            status_code=503,
            content={"message": "暂时无法连接公司服务，本地内容已保留，请稍后再试。"},
        )
    assert remote_response is not None

    response_body = remote_response.content
    local_media_cookie: tuple[str, str, int] | None = None
    if remote_response.is_success and request.url.path.endswith(
        ("customer-login", "admin-login")
    ):
        try:
            payload = remote_response.json()
            upstream_token = str(payload.pop("token")).strip()
            role = str(payload.get("role") or "").strip()
            subject = str(payload.get("code") or payload.get("username") or "").strip()
            expected_role = (
                "admin" if request.url.path.endswith("admin-login") else "customer"
            )
            if not upstream_token or not subject or role != expected_role:
                raise ValueError("invalid upstream login identity")
            if role == "customer" and not ensure_desktop_owner(subject):
                await _revoke_unused_upstream_login(role, upstream_token)
                return JSONResponse(
                    status_code=409,
                    content={
                        "message": (
                            "这台电脑已绑定其他客户账号。为保护本机素材，请使用首次激活的账号；"
                            "如需更换，请联系服务人员迁移或清理本机工作区。"
                        )
                    },
                )
            local_session = issue_auth_token(role, subject)
            register_upstream_session(
                local_session,
                upstream_token,
                role=role,
                subject=subject,
            )
            payload["token"] = local_session
            cookie_name = (
                "vi_admin_media_token" if role == "admin" else "vi_customer_media_token"
            )
            try:
                max_age = max(300, int(payload.get("expires_in_seconds", 12 * 60 * 60)))
            except (TypeError, ValueError):
                max_age = 12 * 60 * 60
            local_media_cookie = (cookie_name, local_session, max_age)
            response_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return JSONResponse(
                status_code=502,
                content={"message": "公司服务返回了无效的登录结果。"},
            )

    if request.url.path.endswith("/logout") and local_token:
        clear_upstream_session(local_token)
        revoke_auth_token(local_token)

    response_headers = {"Cache-Control": "no-store"}
    content_type = remote_response.headers.get("content-type")
    if content_type:
        response_headers["Content-Type"] = content_type
    response = Response(
        content=response_body,
        status_code=remote_response.status_code,
        headers=response_headers,
    )
    if local_media_cookie is not None:
        name, value, max_age = local_media_cookie
        other_name = (
            "vi_admin_media_token"
            if name == "vi_customer_media_token"
            else "vi_customer_media_token"
        )
        response.delete_cookie(other_name, path="/api/v1/")
        response.set_cookie(
            key=name,
            value=value,
            max_age=max_age,
            httponly=True,
            samesite="lax",
            secure=False,
            path="/api/v1/",
        )
    if request.url.path.endswith("/logout"):
        for name in ("vi_admin_media_token", "vi_customer_media_token"):
            response.delete_cookie(name, path="/api/v1/")
    return response
