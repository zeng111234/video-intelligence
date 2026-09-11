from __future__ import annotations

import json
import os
import re
from io import BytesIO
from pathlib import Path

import pytest

from scripts import desktop_launcher
from scripts import verify_windows_release_payload

_ENVIRONMENT_KEYS = (
    "PATH",
    "APP_ENV",
    "ENABLE_DOCS",
    "VIDEOINSIGHT_CONTROL_PLANE_ENABLED",
    "VIDEOINSIGHT_CONTROL_PLANE_URL",
    "VIDEOINSIGHT_DESKTOP_CLIENT",
    "VIDEOINSIGHT_DESKTOP_DEMO",
    "VIDEOINSIGHT_DEMO_OWNER",
    "VIDEOINSIGHT_WORKER_TOKEN",
    "VIDEOINSIGHT_RUNTIME_ROOT",
    "VIDEOINSIGHT_DESKTOP_PORT",
    "VIDEOINSIGHT_FRONTEND_DIST",
    "VIDEOINSIGHT_BACKEND_ORIGIN",
    "ASR_MODE",
    "VIDEO_EDITOR_PROVIDER_MODE",
    "CRAWLER_PROVIDER_MODE",
    "COPYWRITING_MODE",
    "AVATAR_PROVIDER_MODE",
    "CRAWLER_ONEAPI_AUTO_ENABLED",
    "DOUYIN_OFFICIAL_HOT_ENABLED",
    "DOUYIN_HOT_WORDS_ENABLED",
    "DEFAULT_CREDIT_BALANCE",
    "openai_api_key",
    "avatar_api_key",
    *desktop_launcher.DESKTOP_BLOCKED_SECRET_KEYS,
)


def _restore_environment(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class _HealthResponse(BytesIO):
    def __init__(self, payload: dict[str, str], status: int = 200) -> None:
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def test_desktop_health_requires_current_service_identity(monkeypatch):
    monkeypatch.setenv("VIDEOINSIGHT_CONTROL_PLANE_ENABLED", "false")
    monkeypatch.setattr(
        desktop_launcher.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HealthResponse(
            {
                "status": "ok",
                "service": "videoinsight-desktop-api",
                "desktop_protocol": "2",
                "desktop_client": True,
                "desktop_demo": True,
                "control_plane_enabled": False,
            }
        ),
    )
    assert desktop_launcher._health_ready() is True

    monkeypatch.setattr(
        desktop_launcher.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HealthResponse({"status": "ok"}),
    )
    assert desktop_launcher._health_ready() is False


def test_source_startup_script_validates_mode_and_can_restart_backend_only():
    script = (Path(__file__).resolve().parents[3] / "scripts" / "start_all_services.ps1").read_text(
        encoding="utf-8"
    )
    assert "[switch]$BackendOnly" in script
    assert "-RequireDesktopMode" in script
    assert "desktop_client" in script
    assert "ExpectedControlPlane" in script


def test_restart_script_preserves_desktop_mode_and_does_not_probe_auth_only_capability():
    root = Path(__file__).resolve().parents[3]
    restart_script = (root / "scripts" / "restart_backend.ps1").read_text(encoding="utf-8")
    launch_script = (root / "scripts" / "launch_app.ps1").read_text(encoding="utf-8")

    assert '$env:VIDEOINSIGHT_DESKTOP_CLIENT = "true"' in restart_script
    assert '$env:VIDEOINSIGHT_DESKTOP_DEMO = "true"' in restart_script
    assert '$env:VIDEOINSIGHT_CONTROL_PLANE_ENABLED = "false"' in restart_script
    assert 'desktop_client' in restart_script
    assert '"/api/v1/crawler/browser-discovery/capabilities"' not in launch_script
    assert 'backendHealthUrl = "http://127.0.0.1:2001/health"' in launch_script


def test_desktop_control_plane_config_defaults_to_demo(tmp_path):
    runtime = tmp_path / "runtime"
    original = os.getcwd()
    previous = {key: os.environ.get(key) for key in _ENVIRONMENT_KEYS}
    try:
        assert desktop_launcher._configure_desktop_environment(tmp_path, runtime) is False
        assert desktop_launcher.os.environ["VIDEOINSIGHT_CONTROL_PLANE_ENABLED"] == "false"
        assert desktop_launcher.os.environ["VIDEOINSIGHT_DESKTOP_DEMO"] == "true"
    finally:
        os.chdir(original)
        _restore_environment(previous)


def test_desktop_port_can_be_selected_dynamically(monkeypatch, tmp_path):
    monkeypatch.delenv("VIDEOINSIGHT_DESKTOP_PORT", raising=False)
    port = desktop_launcher._resolve_desktop_port()
    assert 1024 <= port <= 65535
    desktop_launcher._configure_local_urls(port)
    desktop_launcher._write_runtime_state(tmp_path, port)
    state = json.loads((tmp_path / "data" / "desktop-runtime.json").read_text(encoding="utf-8"))
    assert state["port"] == port
    assert state["origin"] == f"http://127.0.0.1:{port}"
    assert state["started_at"]


def test_desktop_launcher_removes_only_a_stale_runtime_record(monkeypatch, tmp_path):
    desktop_launcher._write_runtime_state(tmp_path, 16543)
    runtime_file = tmp_path / "data" / "desktop-runtime.json"
    payload = json.loads(runtime_file.read_text(encoding="utf-8"))
    payload["pid"] = 123456
    runtime_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(desktop_launcher, "_runtime_pid_is_alive", lambda _pid: False)

    assert desktop_launcher._clear_stale_runtime_state(tmp_path) is True
    assert not runtime_file.exists()


def test_desktop_launcher_keeps_a_live_runtime_record(monkeypatch, tmp_path):
    desktop_launcher._write_runtime_state(tmp_path, 16543)
    runtime_file = tmp_path / "data" / "desktop-runtime.json"
    monkeypatch.setattr(desktop_launcher, "_runtime_pid_is_alive", lambda _pid: True)

    assert desktop_launcher._clear_stale_runtime_state(tmp_path) is False
    assert runtime_file.exists()


def test_desktop_launcher_removes_runtime_record_with_invalid_windows_handle(monkeypatch, tmp_path):
    desktop_launcher._write_runtime_state(tmp_path, 16543)
    runtime_file = tmp_path / "data" / "desktop-runtime.json"
    payload = json.loads(runtime_file.read_text(encoding="utf-8"))
    payload["pid"] = 123456
    runtime_file.write_text(json.dumps(payload), encoding="utf-8")

    class _InvalidWindowsHandle(OSError):
        winerror = 6

    def _raise_invalid_handle(_pid, _signal):
        raise _InvalidWindowsHandle("invalid handle")

    monkeypatch.setattr(desktop_launcher.os, "kill", _raise_invalid_handle)

    assert desktop_launcher._clear_stale_runtime_state(tmp_path) is True
    assert not runtime_file.exists()


@pytest.mark.parametrize("value", ["nope", "80", "70000"])
def test_desktop_port_rejects_invalid_environment(monkeypatch, value):
    monkeypatch.setenv("VIDEOINSIGHT_DESKTOP_PORT", value)
    with pytest.raises(ValueError, match="端口配置无效"):
        desktop_launcher._resolve_desktop_port()


def test_packaged_desktop_never_allows_local_demo(monkeypatch):
    monkeypatch.setattr(desktop_launcher.sys, "frozen", True, raising=False)
    assert desktop_launcher._allow_local_demo() is False


def test_source_checkout_can_use_explicit_local_demo(monkeypatch):
    monkeypatch.delattr(desktop_launcher.sys, "frozen", raising=False)
    assert desktop_launcher._allow_local_demo() is True


def test_packaged_main_fails_closed_before_creating_demo(monkeypatch, tmp_path):
    messages: list[str] = []
    monkeypatch.setattr(desktop_launcher.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(desktop_launcher, "_application_root", lambda: tmp_path)
    monkeypatch.setattr(desktop_launcher, "_runtime_root", lambda _root: tmp_path)
    monkeypatch.setattr(
        desktop_launcher,
        "_configure_desktop_environment",
        lambda _root, _runtime, _port=None: False,
    )
    monkeypatch.setattr(desktop_launcher, "_configure_logging", lambda _root: None)
    monkeypatch.setattr(desktop_launcher, "_health_ready", lambda: False)
    monkeypatch.setattr(desktop_launcher, "_port_in_use", lambda _port: False)
    monkeypatch.setattr(desktop_launcher, "_allow_local_demo", lambda: False)
    monkeypatch.setattr(desktop_launcher, "_show_error", messages.append)
    monkeypatch.setattr(
        desktop_launcher,
        "_ensure_demo_customer",
        lambda: (_ for _ in ()).throw(AssertionError("demo must not be created")),
    )

    assert desktop_launcher.main() == 1
    assert messages == ["公司服务配置缺失或无效，请联系服务人员重新安装正式版本。"]


def test_desktop_control_plane_config_enables_remote_authority(tmp_path):
    config_directory = tmp_path / "config"
    config_directory.mkdir()
    (config_directory / "desktop-control-plane.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "control_plane_url": "https://video-api.company.cn",
            }
        ),
        encoding="utf-8",
    )

    original = os.getcwd()
    previous = {key: os.environ.get(key) for key in _ENVIRONMENT_KEYS}
    try:
        os.environ["COPYWRITING_API_KEY"] = "must-not-survive"
        os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"] = "must-not-survive"
        os.environ["openai_api_key"] = "case-insensitive-secret"
        os.environ["avatar_api_key"] = "legacy-secret"
        assert desktop_launcher._configure_desktop_environment(
            tmp_path, tmp_path / "runtime"
        ) is True
        assert desktop_launcher.os.environ["ASR_MODE"] == "cloud"
        assert desktop_launcher.os.environ["VIDEO_EDITOR_PROVIDER_MODE"] == "aliyun"
        assert desktop_launcher.os.environ["COPYWRITING_MODE"] == "production"
        # Crawler and avatar still use their explicit customer-desktop/remote
        # service boundaries; they must not inherit a supplier credential.
        assert desktop_launcher.os.environ["CRAWLER_PROVIDER_MODE"] == "sandbox"
        assert desktop_launcher.os.environ["AVATAR_PROVIDER_MODE"] == "sandbox"
        assert (
            desktop_launcher.os.environ["VIDEOINSIGHT_CONTROL_PLANE_URL"]
            == "https://video-api.company.cn"
        )
        assert desktop_launcher.os.environ["VIDEOINSIGHT_DESKTOP_DEMO"] == "false"
        assert "COPYWRITING_API_KEY" not in desktop_launcher.os.environ
        assert "ALIBABA_CLOUD_ACCESS_KEY_SECRET" not in desktop_launcher.os.environ
        assert not any(
            key.casefold() == "openai_api_key" for key in desktop_launcher.os.environ
        )
        assert not any(
            key.casefold() == "avatar_api_key" for key in desktop_launcher.os.environ
        )
    finally:
        os.chdir(original)
        _restore_environment(previous)


def test_electron_and_python_launchers_block_the_same_supplier_secrets():
    electron_environment = (
        Path(__file__).resolve().parents[2] / "frontend" / "electron" / "environment.cjs"
    ).read_text(encoding="utf-8")
    list_body = electron_environment.split(
        "const DESKTOP_BLOCKED_SECRET_KEYS = Object.freeze([", 1
    )[1].split("]);", 1)[0]
    electron_keys = set(re.findall(r'"([A-Z0-9_]+)"', list_body))
    assert electron_keys == set(desktop_launcher.DESKTOP_BLOCKED_SECRET_KEYS)
    assert set(verify_windows_release_payload.SENSITIVE_ENVIRONMENT_KEYS) == set(
        desktop_launcher.DESKTOP_BLOCKED_SECRET_KEYS
    )


def test_desktop_config_rejects_plain_http_remote_host(tmp_path):
    config_directory = tmp_path / "config"
    config_directory.mkdir()
    (config_directory / "desktop-control-plane.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "control_plane_url": "http://video-api.example.com",
            }
        ),
        encoding="utf-8",
    )

    assert desktop_launcher._load_control_plane_config(tmp_path) == {
        "enabled": False,
        "control_plane_url": "",
    }


def test_desktop_config_rejects_reserved_example_origin(tmp_path):
    config_directory = tmp_path / "config"
    config_directory.mkdir()
    (config_directory / "desktop-control-plane.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "control_plane_url": "https://video-api.example.com",
            }
        ),
        encoding="utf-8",
    )

    assert desktop_launcher._load_control_plane_config(tmp_path) == {
        "enabled": False,
        "control_plane_url": "",
    }


def test_desktop_config_rejects_control_plane_subpath(tmp_path):
    config_directory = tmp_path / "config"
    config_directory.mkdir()
    (config_directory / "desktop-control-plane.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "control_plane_url": "https://video-api.company.cn/nested",
            }
        ),
        encoding="utf-8",
    )

    assert desktop_launcher._load_control_plane_config(tmp_path) == {
        "enabled": False,
        "control_plane_url": "",
    }
