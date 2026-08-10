from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "control-plane"
    / "verify_control_plane.py"
)
spec = spec_from_file_location("verify_control_plane", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
verify = module_from_spec(spec)
sys.modules[spec.name] = verify
spec.loader.exec_module(verify)


def _result(status: int, payload=None, headers=None):
    return verify.HttpResult(
        status=status,
        payload=payload,
        headers=headers or {},
        text="",
    )


def test_live_requester_disables_environment_proxies(monkeypatch):
    captured_handlers = []

    class _Opener:
        pass

    def _capture_opener(*handlers):
        captured_handlers.extend(handlers)
        return _Opener()

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.invalid:8080")
    monkeypatch.setattr(verify, "build_opener", _capture_opener)

    requester = verify.LiveRequester("https://video-api.company.com")

    assert isinstance(requester.opener, _Opener)
    proxy_handlers = [
        handler
        for handler in captured_handlers
        if isinstance(handler, verify.ProxyHandler)
    ]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}
    assert any(isinstance(handler, verify._NoRedirect) for handler in captured_handlers)


class _HappyRequester:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, **kwargs):
        self.calls.append((method, path))
        if kwargs.get("insecure_http"):
            return _result(308)
        if path == "/health":
            return _result(
                200,
                {"status": "ok", "release_version": "0.2.7"},
                {
                    "x-content-type-options": "nosniff",
                    "x-frame-options": "DENY",
                    "referrer-policy": "no-referrer",
                    "strict-transport-security": "max-age=31536000; includeSubDomains",
                },
            )
        if path == "/ready":
            return _result(200, {"status": "ready"})
        if path == "/docs" or path == "/desktop-updates/latest.json":
            return _result(404)
        if path == "/api/v1/auth/customer-login":
            code = kwargs["body"]["code"]
            if code == "TEST-ACTIVATION":
                return _result(200, {"token": "customer-session"})
            return _result(401)
        if path == "/api/v1/auth/admin-login":
            if kwargs["body"]["password"] == "correct-admin-password":
                return _result(200, {"token": "admin-session"})
            return _result(401)
        if path == "/api/v1/credits" and kwargs.get("headers"):
            return _result(200, {"balance": "10"})
        if path == "/api/v1/admin/server-status" and kwargs.get("headers"):
            return _result(
                200,
                {
                    "crawler": {
                        "location": "customer_desktop",
                        "billable": False,
                        "server_provider_disabled": True,
                    },
                    "copywriting": {"mode": "production", "enabled": True},
                    "transcription": {
                        "provider_mode": "aliyun",
                        "provider_name": "aliyun_fun_asr",
                        "enabled": True,
                        "live_ready": True,
                        "is_mock": False,
                        "billing_authorized": True,
                        "missing_configuration": [],
                    },
                    "video_editor": {
                        "provider_mode": "aliyun",
                        "provider_name": "aliyun_cloud_editor",
                        "enabled": True,
                        "live_ready": True,
                        "is_mock": False,
                        "missing_configuration": [],
                    },
                    "avatar": {
                        "mode": "production",
                        "provider_name": "shuying_legacy_cloud",
                        "enabled": True,
                        "missing_configuration": [],
                        "required_shared_assets": {
                            "avatar": {
                                "asset_id": "shuying-avatar-21920",
                                "provider_asset_id": "21920",
                                "ready": True,
                            },
                            "voice": {
                                "asset_id": "shuying-voice-7869",
                                "provider_asset_id": "7869",
                                "ready": True,
                            },
                        },
                    },
                },
            )
        if path.startswith("/api/v1/"):
            return _result(401)
        raise AssertionError(f"unexpected request {method} {path}")


def test_live_verifier_public_checks_are_no_charge_and_pass():
    requester = _HappyRequester()
    checks = verify.run_checks(
        "https://video-api.company.com",
        requester=requester,
    )
    assert all(check.passed for check in checks)
    assert not any("copywriting" in path for _, path in requester.calls)
    assert not any(
        method in {"PUT", "PATCH", "DELETE"} for method, _ in requester.calls
    )


def test_live_verifier_can_check_dedicated_customer_and_admin_without_charge():
    checks = verify.run_checks(
        "https://video-api.company.com",
        activation_code="TEST-ACTIVATION",
        admin_username="admin",
        admin_password="correct-admin-password",
        requester=_HappyRequester(),
    )
    assert all(check.passed for check in checks)
    assert any(check.name == "客户积分只读查询" for check in checks)
    assert any(check.name == "服务器状态与免费本地爬虫边界" for check in checks)


def test_live_verifier_strict_mode_requires_credentials_and_production_config():
    missing = verify.run_checks(
        "https://video-api.company.com",
        require_authenticated=True,
        require_production_configuration=True,
        requester=_HappyRequester(),
    )
    assert any(
        check.name == "正式验收凭据已提供" and not check.passed for check in missing
    )
    assert any(
        check.name == "正式供应商生产配置" and not check.passed for check in missing
    )

    strict = verify.run_checks(
        "https://video-api.company.com",
        expected_version="0.2.7",
        activation_code="TEST-ACTIVATION",
        admin_username="admin",
        admin_password="correct-admin-password",
        require_authenticated=True,
        require_production_configuration=True,
        requester=_HappyRequester(),
    )
    assert all(check.passed for check in strict)
    assert any(check.name == "正式供应商生产配置" for check in strict)


def test_live_verifier_rejects_wrong_release_and_missing_required_avatar_asset():
    class _WrongReleaseRequester(_HappyRequester):
        def __call__(self, method: str, path: str, **kwargs):
            result = super().__call__(method, path, **kwargs)
            if path == "/health" and isinstance(result.payload, dict):
                result.payload["release_version"] = "0.2.6"
            if path == "/api/v1/admin/server-status" and isinstance(
                result.payload, dict
            ):
                result.payload["avatar"]["required_shared_assets"]["voice"]["ready"] = (
                    False
                )
            return result

    checks = verify.run_checks(
        "https://video-api.company.com",
        expected_version="0.2.7",
        activation_code="TEST-ACTIVATION",
        admin_username="admin",
        admin_password="correct-admin-password",
        require_authenticated=True,
        require_production_configuration=True,
        requester=_WrongReleaseRequester(),
    )

    assert any(check.name == "控制层发布版本" and not check.passed for check in checks)
    assert any(
        check.name == "正式供应商生产配置" and not check.passed for check in checks
    )


@pytest.mark.parametrize(
    "value",
    [
        "http://video-api.company.example",
        "https://video-api.company.example/nested",
        "https://user:password@video-api.company.example",
        "https://localhost",
        "https://127.0.0.1",
        "https://video-api.example.com",
        "https://video-api.invalid",
    ],
)
def test_live_verifier_rejects_non_origin_or_insecure_url(value: str):
    with pytest.raises(ValueError):
        verify.validate_origin(value)


def test_update_manifest_requires_matching_name_hash_and_size():
    valid, _ = verify._check_update_manifest(
        _result(
            200,
            {
                "version": "0.2.1",
                "installer": "VideoInsight-0.2.1-Setup.exe",
                "sha256": "a" * 64,
                "size_bytes": 123,
            },
        )
    )
    assert valid is True

    invalid, _ = verify._check_update_manifest(
        _result(
            200,
            {
                "version": "0.2.1",
                "installer": "other.exe",
                "sha256": "a" * 64,
                "size_bytes": 123,
            },
        )
    )
    assert invalid is False
