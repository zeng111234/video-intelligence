from __future__ import annotations

import hashlib
import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "verify_windows_release_payload.py"
)
spec = spec_from_file_location("verify_windows_release_payload", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
verify = module_from_spec(spec)
sys.modules[spec.name] = verify
spec.loader.exec_module(verify)


def _payload(tmp_path: Path, url: str = "https://video.company.com") -> Path:
    root = tmp_path / "win-unpacked"
    backend = root / "resources" / "backend"
    backend_config = backend / "_internal" / "config"
    release_config = root / "resources" / "config"
    backend_config.mkdir(parents=True)
    release_config.mkdir(parents=True)
    (root / "VideoInsight.exe").write_bytes(b"desktop")
    (backend / "VideoInsightBackend.exe").write_bytes(b"backend")
    media = backend / "_internal" / "media"
    media.mkdir(parents=True)
    (media / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (media / "ffprobe.exe").write_bytes(b"ffprobe")
    (media / "LICENSE").write_text("GPLv3 test fixture", encoding="utf-8")
    (media / "README.txt").write_text("test fixture", encoding="utf-8")
    (media / "windows-media-tools.sha256").write_text(
        "\n".join(
            f"{hashlib.sha256((media / name).read_bytes()).hexdigest()}  {name}"
            for name in ("LICENSE", "README.txt", "ffmpeg.exe", "ffprobe.exe")
        )
        + "\n",
        encoding="utf-8",
    )
    (release_config / "release.json").write_text(
        json.dumps({"current_version": "0.2.1", "control_plane_url": url}),
        encoding="utf-8",
    )
    (backend_config / "desktop-control-plane.json").write_text(
        json.dumps({"enabled": True, "control_plane_url": url}),
        encoding="utf-8",
    )
    return root


def test_release_payload_accepts_matching_production_configuration(tmp_path):
    root = _payload(tmp_path)
    public_ca = root / "resources" / "backend" / "_internal" / "certifi"
    public_ca.mkdir(parents=True)
    (public_ca / "cacert.pem").write_text(
        "-----BEGIN CERTIFICATE-----\npublic-ca-only\n-----END CERTIFICATE-----\n",
        encoding="ascii",
    )
    builtin_template = (
        root
        / "resources"
        / "backend"
        / "_internal"
        / "data"
        / "templates"
        / "builtin.json"
    )
    builtin_template.parent.mkdir(parents=True)
    builtin_template.write_text('{"templates": []}', encoding="utf-8")
    evidence = verify.verify_release_payload(
        root,
        control_plane_url="https://video.company.com",
        version="0.2.1",
        environment={},
    )
    assert "版本=0.2.1" in evidence

    (builtin_template.parent / "customer.json").write_text("{}", encoding="utf-8")
    with pytest.raises(verify.ReleasePayloadError, match="运行数据"):
        verify.verify_release_payload(
            root,
            control_plane_url="https://video.company.com",
            version="0.2.1",
            environment={},
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://video.company.com",
        "https://127.0.0.1",
        "https://video-api.example.com",
    ],
)
def test_release_payload_rejects_nonproduction_control_plane(tmp_path, url):
    with pytest.raises(verify.ReleasePayloadError):
        verify.verify_release_payload(
            _payload(tmp_path, url),
            control_plane_url=url,
            version="0.2.1",
            environment={},
        )


def test_release_payload_rejects_runtime_data_and_configured_secret(tmp_path):
    root = _payload(tmp_path)
    runtime_data = root / "resources" / "backend" / "data"
    runtime_data.mkdir()
    (runtime_data / "customer.sqlite").write_bytes(b"database")
    with pytest.raises(verify.ReleasePayloadError, match="运行数据"):
        verify.verify_release_payload(
            root,
            control_plane_url="https://video.company.com",
            version="0.2.1",
            environment={},
        )

    (runtime_data / "customer.sqlite").unlink()
    runtime_data.rmdir()
    (root / "resources" / "app.asar").write_bytes(b"prefix-real-secret-value-suffix")
    with pytest.raises(verify.ReleasePayloadError, match="构建机供应商密钥"):
        verify.verify_release_payload(
            root,
            control_plane_url="https://video.company.com",
            version="0.2.1",
            environment={"COPYWRITING_API_KEY": "real-secret-value"},
        )


def test_release_payload_rejects_tampered_media_tool(tmp_path):
    root = _payload(tmp_path)
    ffprobe = root / "resources" / "backend" / "_internal" / "media" / "ffprobe.exe"
    ffprobe.write_bytes(b"tampered")

    with pytest.raises(verify.ReleasePayloadError, match="ffprobe.exe"):
        verify.verify_release_payload(
            root,
            control_plane_url="https://video.company.com",
            version="0.2.1",
            environment={},
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("APP_SECRET_KEY", "app-secret-value"),
        ("API_KEY", "generic-api-secret"),
        ("ADMIN_PASSWORD", "admin-password-secret"),
        ("POSTGRES_PASSWORD", "postgres-password-secret"),
        ("VIDEOINSIGHT_WORKER_TOKEN", "worker-token-secret"),
    ],
)
def test_release_payload_scans_application_and_admin_secrets(tmp_path, key, value):
    root = _payload(tmp_path)
    (root / "resources" / "app.asar").write_bytes(
        b"ordinary-prefix-" + value.encode("utf-8") + b"-ordinary-suffix"
    )

    with pytest.raises(verify.ReleasePayloadError, match=key):
        verify.verify_release_payload(
            root,
            control_plane_url="https://video.company.com",
            version="0.2.1",
            environment={key: value},
        )


@pytest.mark.parametrize(
    "marker",
    [
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN DSA PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "PuTTY-User-Key-File:",
    ],
)
def test_release_payload_rejects_private_key_content(tmp_path, marker):
    root = _payload(tmp_path)
    (root / "resources" / "unexpected.txt").write_text(
        f"{marker}\nnot-a-real-key\n",
        encoding="ascii",
    )
    with pytest.raises(verify.ReleasePayloadError, match="PRIVATE_KEY"):
        verify.verify_release_payload(
            root,
            control_plane_url="https://video.company.com",
            version="0.2.1",
            environment={},
        )


def test_release_payload_compares_additional_ignored_env_secrets(tmp_path):
    root = _payload(tmp_path)
    (root / "resources" / "app.asar").write_bytes(b"bundled-local-env-secret")
    with pytest.raises(verify.ReleasePayloadError, match="root-env"):
        verify.verify_release_payload(
            root,
            control_plane_url="https://video.company.com",
            version="0.2.1",
            environment={},
            additional_secret_sources={
                "root-env": {"COPYWRITING_API_KEY": "local-env-secret"}
            },
        )


def test_env_secret_input_over_limit_fails_closed(tmp_path):
    env_file = tmp_path / ".env"
    with env_file.open("wb") as stream:
        stream.seek(1024 * 1024)
        stream.write(b"x")

    with pytest.raises(verify.ReleasePayloadError, match="超过 1 MiB"):
        verify._parse_env_file(env_file)


def test_explicit_missing_or_nonregular_secret_input_fails_closed(tmp_path):
    with pytest.raises(verify.ReleasePayloadError, match="无法读取"):
        verify._parse_env_file(tmp_path / "missing.env")

    directory = tmp_path / "env-directory"
    directory.mkdir()
    with pytest.raises(verify.ReleasePayloadError, match="不是普通文件"):
        verify._parse_env_file(directory)
