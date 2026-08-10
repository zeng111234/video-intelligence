from __future__ import annotations

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
    evidence = verify.verify_release_payload(
        root,
        control_plane_url="https://video.company.com",
        version="0.2.1",
        environment={},
    )
    assert "版本=0.2.1" in evidence


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


def test_release_payload_rejects_private_key_content(tmp_path):
    root = _payload(tmp_path)
    (root / "resources" / "unexpected.pem").write_text(
        "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
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
