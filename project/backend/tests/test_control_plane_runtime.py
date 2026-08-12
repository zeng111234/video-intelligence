from __future__ import annotations

import pytest

from project.backend.app.core.control_plane_runtime import (
    validate_control_plane_runtime,
)


def _safe_production(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ENABLE_DOCS", "false")
    monkeypatch.setenv("AUTH_SESSION_STORE", "sqlite")
    monkeypatch.setenv("CONTROL_PLANE_DOMAIN", "video-api.company.cn")
    monkeypatch.setenv("CONTROL_PLANE_ALLOWED_HOSTS", "video-api.company.cn")
    monkeypatch.setenv("ASR_MODE", "sandbox")
    monkeypatch.setenv("CRAWLER_PROVIDER_MODE", "sandbox")


def test_safe_production_control_plane_runtime_is_accepted(monkeypatch):
    _safe_production(monkeypatch)
    validate_control_plane_runtime()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("AUTH_SESSION_STORE", "memory", "AUTH_SESSION_STORE"),
        ("CONTROL_PLANE_DOMAIN", "video-api.example.com", "公网域名"),
        ("CONTROL_PLANE_ALLOWED_HOSTS", "video-api.company.cn,*", "通配符"),
        ("ASR_MODE", "local", "ASR_MODE"),
        ("CRAWLER_PROVIDER_MODE", "production", "素材发现"),
    ],
)
def test_unsafe_production_control_plane_runtime_is_rejected(
    monkeypatch, key, value, message
):
    _safe_production(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match=message):
        validate_control_plane_runtime()


def test_development_runtime_keeps_local_test_defaults(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_SESSION_STORE", "memory")
    monkeypatch.setenv("CONTROL_PLANE_ALLOWED_HOSTS", "testserver")
    validate_control_plane_runtime()
