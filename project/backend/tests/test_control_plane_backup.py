from __future__ import annotations

import sqlite3
import os
import shutil
import subprocess
import sys
import zipfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


DEPLOY_ROOT = Path(__file__).resolve().parents[3] / "deploy" / "control-plane"


def _load_script(name: str):
    spec = spec_from_file_location(name, DEPLOY_ROOT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup_control_plane = _load_script("backup_control_plane")
restore_control_plane = _load_script("restore_control_plane")


def _validated_environment(tmp_path: Path) -> Path:
    example = (DEPLOY_ROOT / ".env.example").read_text(encoding="utf-8")
    configured = example.replace(
        "video-api.example.com", "video-api.company.cn"
    ).replace(
        "CHANGE_ME_AT_LEAST_16_CHARACTERS",
        "correct-horse-battery-staple-2026",
    )
    target = tmp_path / "control-plane.env"
    target.write_text(configured, encoding="utf-8", newline="\n")
    return target


def _run_validator(path: Path) -> subprocess.CompletedProcess[str]:
    if shutil.which("sh") is None:
        raise RuntimeError("测试环境缺少 sh。")
    environment = os.environ.copy()
    environment["CONTROL_PLANE_ENV_FILE"] = str(path)
    return subprocess.run(
        ["sh", str(DEPLOY_ROOT / "validate_env.sh")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        check=False,
    )


def _create_database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample(value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample(value) VALUES (?)", (value,))


def test_complete_backup_and_restore_include_non_database_state(tmp_path, monkeypatch):
    source = tmp_path / "source-data"
    backups = tmp_path / "backups"
    restored = tmp_path / "restored-data"
    stale = restored / "control_plane" / "stale-authorization.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("must be removed", encoding="utf-8")
    backups.mkdir()
    _create_database(source / "video_intelligence.db", "original")
    authorization = source / "control_plane" / "asr_authorization.json"
    authorization.parent.mkdir(parents=True)
    authorization.write_text('{"confirmed": true}', encoding="utf-8")
    asset = source / "avatar_assets" / "sample.bin"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"private server asset")

    monkeypatch.setattr(backup_control_plane, "DATA_ROOT", source.resolve())
    monkeypatch.setattr(backup_control_plane, "BACKUP_ROOT", backups.resolve())
    monkeypatch.setattr(sys, "argv", ["backup_control_plane.py", "backup.zip"])
    assert backup_control_plane.main() == 0

    archive = backups / "backup.zip"
    assert archive.is_file()
    with zipfile.ZipFile(archive) as bundle:
        assert "data/video_intelligence.db" in bundle.namelist()
        assert "data/control_plane/asr_authorization.json" in bundle.namelist()
        assert "data/avatar_assets/sample.bin" in bundle.namelist()

    monkeypatch.setattr(restore_control_plane, "DATA_ROOT", restored.resolve())
    monkeypatch.setattr(restore_control_plane, "BACKUP_ROOT", backups.resolve())
    monkeypatch.setattr(sys, "argv", ["restore_control_plane.py", "backup.zip"])
    assert restore_control_plane.main() == 0
    with sqlite3.connect(restored / "video_intelligence.db") as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == (
            "original",
        )
    assert (
        restored / "control_plane" / "asr_authorization.json"
    ).read_text(encoding="utf-8") == '{"confirmed": true}'
    assert (restored / "avatar_assets" / "sample.bin").read_bytes() == (
        b"private server asset"
    )
    assert not stale.exists()


def test_restore_rejects_archive_path_traversal(tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    restored = tmp_path / "restored-data"
    backups.mkdir()
    archive = backups / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", '{"format":"videoinsight-control-plane-backup-v1"}')
        bundle.writestr("data/video_intelligence.db", b"not-used")
        bundle.writestr("../escaped.txt", "unsafe")

    monkeypatch.setattr(restore_control_plane, "DATA_ROOT", restored.resolve())
    monkeypatch.setattr(restore_control_plane, "BACKUP_ROOT", backups.resolve())
    monkeypatch.setattr(sys, "argv", ["restore_control_plane.py", "unsafe.zip"])
    assert restore_control_plane.main() == 1
    assert not (tmp_path / "escaped.txt").exists()


def test_restore_revokes_sessions_from_the_backup(tmp_path, monkeypatch):
    source = tmp_path / "session-source"
    backups = tmp_path / "backups"
    restored = tmp_path / "session-restored"
    backups.mkdir()
    database = source / "video_intelligence.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE auth_sessions (
                token_hash TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                subject TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO auth_sessions(token_hash, role, subject, created_at, expires_at)
            VALUES ('old-token-hash', 'admin', 'admin', 1, 9999999999)
            """
        )

    monkeypatch.setattr(backup_control_plane, "DATA_ROOT", source.resolve())
    monkeypatch.setattr(backup_control_plane, "BACKUP_ROOT", backups.resolve())
    monkeypatch.setattr(sys, "argv", ["backup_control_plane.py", "sessions.zip"])
    assert backup_control_plane.main() == 0

    monkeypatch.setattr(restore_control_plane, "DATA_ROOT", restored.resolve())
    monkeypatch.setattr(restore_control_plane, "BACKUP_ROOT", backups.resolve())
    monkeypatch.setattr(sys, "argv", ["restore_control_plane.py", "sessions.zip"])
    assert restore_control_plane.main() == 0
    with sqlite3.connect(restored / "video_intelligence.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone() == (0,)


def test_deployment_environment_validator_accepts_safe_sandbox(tmp_path):
    result = _run_validator(_validated_environment(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "未调用任何真实供应商" in result.stdout


def test_deployment_environment_validator_rejects_domain_not_in_allowed_hosts(
    tmp_path,
):
    path = _validated_environment(tmp_path)
    content = path.read_text(encoding="utf-8").replace(
        "CONTROL_PLANE_ALLOWED_HOSTS=video-api.company.cn",
        "CONTROL_PLANE_ALLOWED_HOSTS=other.company.cn",
    )
    path.write_text(content, encoding="utf-8", newline="\n")
    result = _run_validator(path)
    assert result.returncode == 1
    assert "必须包含" in result.stdout


def test_deployment_environment_validator_rejects_example_domain(tmp_path):
    path = _validated_environment(tmp_path)
    content = path.read_text(encoding="utf-8").replace(
        "video-api.company.cn",
        "video-api.example.com",
    )
    path.write_text(content, encoding="utf-8", newline="\n")
    result = _run_validator(path)
    assert result.returncode == 1
    assert "示例域名" in result.stdout


def test_deployment_environment_validator_rejects_incomplete_cloud_asr(tmp_path):
    path = _validated_environment(tmp_path)
    content = path.read_text(encoding="utf-8").replace(
        "ASR_MODE=sandbox",
        "ASR_MODE=cloud",
    )
    path.write_text(content, encoding="utf-8", newline="\n")
    result = _run_validator(path)
    assert result.returncode == 1
    assert "DASHSCOPE_API_KEY" in result.stdout
