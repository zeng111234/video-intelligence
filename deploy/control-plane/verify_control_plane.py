"""No-charge external acceptance checks for the company control plane."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:-[0-9A-Za-z.-]+)?$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: dict[str, str]
    payload: Any
    text: str


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    evidence: str


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def validate_origin(value: str) -> str:
    origin = value.strip().rstrip("/")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("公司服务地址必须是纯 HTTPS 域名，不能包含账号、参数或子路径。")
    hostname = parsed.hostname.casefold()
    reserved_names = {"example.com", "example.net", "example.org"}
    reserved_suffixes = (".localhost", ".test", ".invalid", ".example")
    if hostname == "localhost" or hostname in reserved_names or any(
        hostname.endswith(f".{name}") for name in reserved_names
    ) or hostname.endswith(reserved_suffixes):
        raise ValueError("公司服务地址不能使用本机或示例域名。")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (address.is_loopback or address.is_unspecified):
        raise ValueError("公司服务地址不能使用本机地址。")
    return origin


class LiveRequester:
    def __init__(self, origin: str, timeout_seconds: float = 10.0) -> None:
        self.origin = validate_origin(origin)
        self.timeout_seconds = timeout_seconds
        self.opener = build_opener(_NoRedirect())

    def __call__(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        insecure_http: bool = False,
    ) -> HttpResult:
        base = self.origin.replace("https://", "http://", 1) if insecure_http else self.origin
        url = f"{base}{path}"
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "VideoInsight-Control-Plane-Verifier/0.2.0",
            **(headers or {}),
        }
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=request_headers, method=method.upper())

        last_error: BaseException | None = None
        for attempt in range(2):
            try:
                with self.opener.open(request, timeout=self.timeout_seconds) as response:
                    return self._result(response.status, response.headers, response.read(1024 * 1024))
            except HTTPError as exc:
                return self._result(exc.code, exc.headers, exc.read(1024 * 1024))
            except (URLError, OSError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(1)
        raise RuntimeError(f"无法连接公司服务：{last_error}") from last_error

    @staticmethod
    def _result(status: int, headers, raw: bytes) -> HttpResult:  # noqa: ANN001
        text = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(text) if text else None
        except json.JSONDecodeError:
            payload = None
        return HttpResult(
            status=status,
            headers={str(key).casefold(): str(value) for key, value in headers.items()},
            payload=payload,
            text=text[:1000],
        )


def _json_status(result: HttpResult, status: int, key: str, value: Any) -> bool:
    return (
        result.status == status
        and isinstance(result.payload, dict)
        and result.payload.get(key) == value
    )


def _check_update_manifest(result: HttpResult) -> tuple[bool, str]:
    if result.status == 404:
        return True, "尚未发布桌面更新（允许）"
    payload = result.payload
    if result.status != 200 or not isinstance(payload, dict):
        return False, f"HTTP {result.status}"
    version = str(payload.get("version") or "")
    installer = str(payload.get("installer") or "")
    digest = str(payload.get("sha256") or "").casefold()
    size = payload.get("size_bytes")
    valid = bool(
        _VERSION_PATTERN.fullmatch(version)
        and installer == f"VideoInsight-{version}-Setup.exe"
        and _SHA256_PATTERN.fullmatch(digest)
        and isinstance(size, int)
        and 0 < size <= 1024 * 1024 * 1024
    )
    return valid, f"HTTP 200; version={version or 'missing'}"


def run_checks(
    origin: str,
    *,
    activation_code: str = "",
    admin_username: str = "",
    admin_password: str = "",
    requester: Callable[..., HttpResult] | None = None,
) -> list[Check]:
    request = requester or LiveRequester(origin)
    checks: list[Check] = []

    redirect = request("GET", "/health", insecure_http=True)
    checks.append(
        Check(
            "HTTP 自动跳转 HTTPS",
            redirect.status in {301, 302, 307, 308},
            f"HTTP {redirect.status}",
        )
    )

    health = request("GET", "/health")
    checks.append(Check("服务健康", _json_status(health, 200, "status", "ok"), f"HTTP {health.status}"))
    ready = request("GET", "/ready")
    checks.append(Check("数据库就绪", _json_status(ready, 200, "status", "ready"), f"HTTP {ready.status}"))

    required_headers = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "no-referrer",
    }
    headers_valid = all(health.headers.get(key) == value for key, value in required_headers.items())
    hsts = health.headers.get("strict-transport-security", "")
    headers_valid = headers_valid and "max-age=" in hsts and "server" not in health.headers
    checks.append(
        Check(
            "HTTPS 安全响应头",
            headers_valid,
            "关键安全头齐全且隐藏服务器标识" if headers_valid else "安全头缺失或暴露服务器标识",
        )
    )

    docs = request("GET", "/docs")
    checks.append(Check("生产接口文档关闭", docs.status == 404, f"HTTP {docs.status}"))

    invalid_customer = request(
        "POST",
        "/api/v1/auth/customer-login",
        body={"code": "VI-LIVE-CHECK-INVALID-DO-NOT-CREATE"},
    )
    checks.append(Check("无效激活码被拒绝", invalid_customer.status == 401, f"HTTP {invalid_customer.status}"))
    invalid_admin = request(
        "POST",
        "/api/v1/auth/admin-login",
        body={"username": "admin", "password": "invalid-live-check-password"},
    )
    checks.append(Check("无效管理员登录被拒绝", invalid_admin.status == 401, f"HTTP {invalid_admin.status}"))

    for name, path in (
        ("积分接口要求登录", "/api/v1/credits"),
        ("管理员接口要求登录", "/api/v1/admin/server-status"),
        ("收费供应商接口要求登录", "/api/v1/provider/asr/capabilities"),
    ):
        result = request("GET", path)
        checks.append(Check(name, result.status == 401, f"HTTP {result.status}"))

    updates = request("GET", "/desktop-updates/latest.json")
    update_valid, update_evidence = _check_update_manifest(updates)
    checks.append(Check("桌面更新清单", update_valid, update_evidence))

    if activation_code:
        customer_login = request(
            "POST",
            "/api/v1/auth/customer-login",
            body={"code": activation_code},
        )
        token = customer_login.payload.get("token", "") if isinstance(customer_login.payload, dict) else ""
        customer_ok = customer_login.status == 200 and bool(token)
        checks.append(Check("指定激活码登录", customer_ok, f"HTTP {customer_login.status}"))
        if customer_ok:
            credits = request("GET", "/api/v1/credits", headers={"X-Customer-Token": token})
            checks.append(Check("客户积分只读查询", credits.status == 200, f"HTTP {credits.status}"))

    if admin_username and admin_password:
        admin_login = request(
            "POST",
            "/api/v1/auth/admin-login",
            body={"username": admin_username, "password": admin_password},
        )
        token = admin_login.payload.get("token", "") if isinstance(admin_login.payload, dict) else ""
        admin_ok = admin_login.status == 200 and bool(token)
        checks.append(Check("指定管理员登录", admin_ok, f"HTTP {admin_login.status}"))
        if admin_ok:
            status = request(
                "GET",
                "/api/v1/admin/server-status",
                headers={"X-Admin-Token": token},
            )
            crawler = status.payload.get("crawler", {}) if isinstance(status.payload, dict) else {}
            crawler_safe = (
                status.status == 200
                and crawler.get("location") == "customer_desktop"
                and crawler.get("billable") is False
                and crawler.get("server_provider_disabled") is True
            )
            checks.append(Check("服务器状态与免费本地爬虫边界", crawler_safe, f"HTTP {status.status}"))

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="VideoInsight 公司控制层无付费在线验收")
    parser.add_argument("--base-url", required=True, help="例如 https://video-api.company.com")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()
    try:
        origin = validate_origin(args.base_url)
        activation_code = os.getenv("VIDEOINSIGHT_VERIFY_ACTIVATION_CODE", "").strip()
        admin_username = os.getenv("VIDEOINSIGHT_VERIFY_ADMIN_USERNAME", "").strip()
        admin_password = os.getenv("VIDEOINSIGHT_VERIFY_ADMIN_PASSWORD", "").strip()
        checks = run_checks(
            origin,
            activation_code=activation_code,
            admin_username=admin_username,
            admin_password=admin_password,
            requester=LiveRequester(origin, timeout_seconds=args.timeout_seconds),
        )
    except (ValueError, RuntimeError) as exc:
        print(f"FAIL {exc}")
        return 1

    for check in checks:
        print(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.evidence}")
    failed = [check for check in checks if not check.passed]
    print(f"结果：{len(checks) - len(failed)}/{len(checks)} 通过；未发起任何收费调用。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
