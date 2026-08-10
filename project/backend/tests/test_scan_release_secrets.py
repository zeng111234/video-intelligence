from __future__ import annotations

import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "scan_release_secrets.py"
)
spec = spec_from_file_location("scan_release_secrets", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
scanner = module_from_spec(spec)
sys.modules[spec.name] = scanner
spec.loader.exec_module(scanner)


def _git(tmp_path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )


def test_scan_compares_ignored_env_value_without_printing_it(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "COPYWRITING_API_KEY=local-secret-value-123\n", encoding="utf-8"
    )
    (tmp_path / "safe.py").write_text("print('safe')\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore", "safe.py")
    monkeypatch.delenv("COPYWRITING_API_KEY", raising=False)

    configured_count, matches = scanner.scan(tmp_path)
    assert configured_count >= 1
    assert matches == []

    (tmp_path / "safe.py").write_text(
        "value = 'local-secret-value-123'\n", encoding="utf-8"
    )
    _, matches = scanner.scan(tmp_path)
    assert matches == ["COPYWRITING_API_KEY@root-env:safe.py"]
    assert all("local-secret-value-123" not in match for match in matches)


def test_scan_ignores_only_known_public_placeholder_values(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "APP_SECRET_KEY=your-secret-key-change-this\n", encoding="utf-8"
    )
    (tmp_path / ".env.example").write_text(
        "APP_SECRET_KEY=your-secret-key-change-this\n", encoding="utf-8"
    )
    _git(tmp_path, "add", ".gitignore", ".env.example")
    for key in scanner.SENSITIVE_ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)

    configured_count, matches = scanner.scan(tmp_path)

    assert configured_count == 0
    assert matches == []


def test_scan_does_not_skip_secret_after_32_mib(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    secret = "large-file-secret-value-123"
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text(f"COPYWRITING_API_KEY={secret}\n", encoding="utf-8")
    candidate = tmp_path / "large.bin"
    with candidate.open("wb") as stream:
        stream.seek(33 * 1024 * 1024)
        stream.write(secret.encode())
    _git(tmp_path, "add", ".gitignore", "large.bin")
    monkeypatch.delenv("COPYWRITING_API_KEY", raising=False)

    _, matches = scanner.scan(tmp_path)

    assert matches == ["COPYWRITING_API_KEY@root-env:large.bin"]


def test_scan_fails_closed_when_env_input_exceeds_limit(tmp_path):
    _git(tmp_path, "init", "-q")
    env_file = tmp_path / ".env"
    with env_file.open("wb") as stream:
        stream.seek(1024 * 1024)
        stream.write(b"x")

    with pytest.raises(OSError, match="exceeds 1 MiB"):
        scanner.scan(tmp_path)


def test_scan_includes_ignored_control_plane_credentials(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    secret = "formal-admin-password-123"
    (tmp_path / ".gitignore").write_text(
        "deploy/control-plane/.env\n", encoding="utf-8"
    )
    control_plane_env = tmp_path / "deploy" / "control-plane" / ".env"
    control_plane_env.parent.mkdir(parents=True)
    control_plane_env.write_text(f"ADMIN_PASSWORD={secret}\n", encoding="utf-8")
    candidate = tmp_path / "ordinary.txt"
    candidate.write_text(f"prefix-{secret}-suffix\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore", "ordinary.txt")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    _, matches = scanner.scan(tmp_path)

    assert matches == ["ADMIN_PASSWORD@control-plane-env:ordinary.txt"]


def test_scan_includes_legacy_streamlit_credentials(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    secret = "legacy-oneapi-secret-123"
    (tmp_path / ".gitignore").write_text(".streamlit/secrets.toml\n", encoding="utf-8")
    secrets_file = tmp_path / ".streamlit" / "secrets.toml"
    secrets_file.parent.mkdir(parents=True)
    secrets_file.write_text(f'ONEAPI_API_KEY = "{secret}"\n', encoding="utf-8")
    candidate = tmp_path / "ordinary.txt"
    candidate.write_text(f"prefix-{secret}-suffix\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore", "ordinary.txt")
    monkeypatch.delenv("ONEAPI_API_KEY", raising=False)

    _, matches = scanner.scan(tmp_path)

    assert matches == ["ONEAPI_API_KEY@legacy-streamlit:ordinary.txt"]
