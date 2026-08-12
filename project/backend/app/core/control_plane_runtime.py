"""Fail-closed production invariants for the company control plane.

The shell deployment helper performs a friendly preflight, but operators can
still invoke Docker Compose directly.  These checks therefore run inside the
server process as a second safety boundary.  They intentionally validate only
security-critical invariants; provider completeness is still reported through
the normal capability endpoints while sandbox modes remain safe to deploy.
"""

from __future__ import annotations

import ipaddress
import os


_RESERVED_EXACT_HOSTS = {
    "localhost",
    "testserver",
    "example.com",
    "example.net",
    "example.org",
}
_RESERVED_SUFFIXES = (".localhost", ".test", ".invalid", ".example")
_RESERVED_EXAMPLE_SUFFIXES = (".example.com", ".example.net", ".example.org")


def _normalized_host(value: str) -> str:
    return value.strip().casefold().rstrip(".")


def _unsafe_production_host(value: str) -> bool:
    host = _normalized_host(value)
    if not host or "*" in host or "://" in host or "/" in host or ":" in host:
        return True
    if host in _RESERVED_EXACT_HOSTS:
        return True
    if host.endswith(_RESERVED_SUFFIXES + _RESERVED_EXAMPLE_SUFFIXES):
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return "." not in host
    return True


def validate_control_plane_runtime() -> None:
    """Reject unsafe production startup even when deploy.sh was bypassed."""

    if os.getenv("APP_ENV", "development").strip().casefold() != "production":
        return

    problems: list[str] = []
    if os.getenv("ENABLE_DOCS", "false").strip().casefold() != "false":
        problems.append("ENABLE_DOCS 必须为 false")
    if os.getenv("AUTH_SESSION_STORE", "memory").strip().casefold() != "sqlite":
        problems.append("AUTH_SESSION_STORE 必须为 sqlite")

    domain = _normalized_host(os.getenv("CONTROL_PLANE_DOMAIN", ""))
    if _unsafe_production_host(domain):
        problems.append("CONTROL_PLANE_DOMAIN 必须是真实公网域名")

    allowed_hosts = [
        _normalized_host(item)
        for item in os.getenv("CONTROL_PLANE_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    ]
    if not allowed_hosts or any(
        _unsafe_production_host(item) for item in allowed_hosts
    ):
        problems.append("CONTROL_PLANE_ALLOWED_HOSTS 不能包含通配符、本机或示例地址")
    elif domain not in allowed_hosts:
        problems.append("CONTROL_PLANE_ALLOWED_HOSTS 必须包含正式域名")

    asr_mode = os.getenv("ASR_MODE", "sandbox").strip().casefold()
    if asr_mode not in {"sandbox", "cloud"}:
        problems.append("服务器 ASR_MODE 仅支持 sandbox 或 cloud")

    crawler_mode = os.getenv("CRAWLER_PROVIDER_MODE", "sandbox").strip().casefold()
    if crawler_mode != "sandbox":
        problems.append("素材发现必须在客户电脑本地运行，服务器端只能保持关闭状态")

    if problems:
        raise RuntimeError("正式控制层配置不安全：" + "；".join(problems))
