from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_SCRIPT = REPOSITORY_ROOT / "scripts" / "build_control_plane_bundle.ps1"
RELEASE_CHECK_SCRIPT = REPOSITORY_ROOT / "scripts" / "check_release.ps1"


def _run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )


def _write(path: Path, value: str = "tracked\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _registry() -> dict[str, object]:
    return {
        "schema_version": 2,
        "used_versions": [f"0.2.{index}" for index in range(7)],
        "current_candidate": "0.2.7",
        "release_in_progress": None,
        "completed_releases": [
            {
                "version": "0.2.6",
                "source_commit": "1" * 40,
                "server_state": "ready",
                "control_plane_sha256": "2" * 64,
                "windows_state": "complete",
                "installer_sha256": "3" * 64,
            }
        ],
    }


def _repository(tmp_path: Path, *, forbidden_marker: str | None = None) -> Path:
    root = tmp_path / "repo"
    _write(root / ".dockerignore")
    _write(root / "PRODUCTION_RELEASE_CHECKLIST.md")
    _write(root / "project" / "__init__.py", "")
    _write(root / "project" / "backend" / "__init__.py", "")
    for index in range(10):
        _write(root / "project" / "backend" / "app" / f"module_{index}.py")
    _write(root / "project" / "backend" / "app" / ".ENV.production", "secret\n")
    for index in range(6):
        _write(root / "src" / f"source_{index}.py")
    for index in range(5):
        _write(root / "database" / f"migration_{index}.sql")
    _write(root / "deploy" / "control-plane" / "README.md")
    validator_source = (
        REPOSITORY_ROOT
        / "deploy"
        / "control-plane"
        / "native-systemd"
        / "validate_release_archive.py"
    )
    validator_target = (
        root
        / "deploy"
        / "control-plane"
        / "native-systemd"
        / "validate_release_archive.py"
    )
    validator_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(validator_source, validator_target)
    if forbidden_marker:
        _write(
            root / "deploy" / "control-plane" / "unsafe.txt",
            forbidden_marker + "\n",
        )
    manifest = {
        "assets": [
            {
                "asset_id": "shuying-avatar-21920",
                "kind": "avatar",
                "name": "大树1",
                "authorized": True,
                "shared": True,
                "status": "ready",
                "provider_asset_id": "21920",
            },
            {
                "asset_id": "shuying-voice-7869",
                "kind": "voice",
                "name": "大树1",
                "authorized": True,
                "shared": True,
                "status": "ready",
                "provider_asset_id": "7869",
            },
        ]
    }
    _write(
        root
        / "deploy"
        / "control-plane"
        / "bootstrap"
        / "avatar_assets"
        / "shuying_cloud.json",
        json.dumps(manifest, ensure_ascii=False),
    )
    _write(
        root / "deploy" / "control-plane" / "release_versions.json",
        json.dumps(_registry(), ensure_ascii=False, indent=2) + "\n",
    )
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BUNDLE_SCRIPT, scripts / BUNDLE_SCRIPT.name)
    _write(scripts / "check_release.ps1", "& cmd.exe /c exit 0\n")
    _run("git", "init", "-q", cwd=root)
    _run("git", "config", "user.email", "release-test@example.invalid", cwd=root)
    _run("git", "config", "user.name", "Release Test", cwd=root)
    _run("git", "add", ".", cwd=root)
    _run("git", "commit", "-qm", "fixture", cwd=root)
    return root


def _bundle(root: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return _run(
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(root / "scripts" / BUNDLE_SCRIPT.name),
        "-Version",
        "0.2.7",
        "-OutputDirectory",
        str(output),
        cwd=root,
        check=False,
    )


def _inject_and_commit(root: Path, marker: str, replacement: str) -> None:
    script_path = root / "scripts" / BUNDLE_SCRIPT.name
    source = script_path.read_text(encoding="utf-8-sig")
    assert source.count(marker) == 1
    script_path.write_text(source.replace(marker, replacement), encoding="utf-8-sig")
    _run("git", "add", str(script_path), cwd=root)
    _run("git", "commit", "-qm", "inject release race", cwd=root)


def test_release_check_rejects_env_names_case_insensitively() -> None:
    source = RELEASE_CHECK_SCRIPT.read_text(encoding="utf-8-sig")
    assert "git -c core.quotepath=false ls-files" in source
    assert "$trackedFileName = [System.IO.Path]::GetFileName" in source
    assert (
        '$trackedFileName.StartsWith(".env.", '
        "[System.StringComparison]::OrdinalIgnoreCase)" in source
    )
    assert (
        '$trackedFileName.Equals(".env.example", '
        "[System.StringComparison]::OrdinalIgnoreCase)" in source
    )


def test_release_check_handles_unicode_tracked_paths_and_rejects_env(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(RELEASE_CHECK_SCRIPT, scripts / RELEASE_CHECK_SCRIPT.name)
    _write(root / "中文目录" / ".ENV.production", "secret\n")
    _run("git", "init", "-q", cwd=root)
    _run("git", "config", "user.email", "release-test@example.invalid", cwd=root)
    _run("git", "config", "user.name", "Release Test", cwd=root)
    _run("git", "add", ".", cwd=root)
    _run("git", "commit", "-qm", "fixture", cwd=root)

    result = _run(
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(scripts / RELEASE_CHECK_SCRIPT.name),
        cwd=root,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "[BLOCKED] Local secrets" in output
    assert "Illegal characters in path" not in output


def test_bundle_burns_version_and_binds_zip_to_commit_and_hash(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = tmp_path / "delivery"
    source_commit = _run("git", "rev-parse", "HEAD", cwd=root).stdout.strip()
    original_history = _registry()["completed_releases"]

    result = _bundle(root, output)

    assert result.returncode == 0, result.stdout + result.stderr
    archive = output / "VideoInsight-control-plane-0.2.7.zip"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    registry = json.loads(
        (root / "deploy" / "control-plane" / "release_versions.json").read_text(
            encoding="utf-8"
        )
    )
    assert registry["current_candidate"] is None
    assert registry["used_versions"].count("0.2.7") == 1
    assert registry["completed_releases"] == original_history
    assert registry["release_in_progress"] == {
        "version": "0.2.7",
        "source_commit": source_commit,
        "server_state": "ready",
        "control_plane_sha256": digest,
        "windows_state": "pending",
        "installer_sha256": None,
    }
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        assert "release_version.txt" in names
        assert package.read("release_version.txt") == b"0.2.7"
        assert package.read(".dockerignore") == b"tracked\n"
        assert "project/backend/app/.ENV.production" not in names
        assert "deploy/control-plane/release_versions.json" not in names
        assert len(names) == len(set(names))
    assert not (
        root / "deploy" / "control-plane" / "release_versions.json.lock"
    ).exists()

    retry = _bundle(root, tmp_path / "retry")
    assert retry.returncode != 0
    assert not (tmp_path / "retry" / "VideoInsight-control-plane-0.2.7.zip").exists()


@pytest.mark.parametrize(
    "marker",
    [
        "-----BEGIN ENCRYPTED PRIVATE KEY-----",
        "-----BEGIN DSA PRIVATE KEY-----",
        "PuTTY-User-Key-File:",
    ],
)
def test_bundle_failure_after_reservation_burns_version_without_partial_zip(
    tmp_path: Path,
    marker: str,
) -> None:
    root = _repository(tmp_path, forbidden_marker=marker)
    output = tmp_path / "delivery"

    result = _bundle(root, output)

    assert result.returncode != 0
    registry = json.loads(
        (root / "deploy" / "control-plane" / "release_versions.json").read_text(
            encoding="utf-8"
        )
    )
    assert registry["current_candidate"] is None
    assert "0.2.7" in registry["used_versions"]
    assert registry["release_in_progress"]["server_state"] == "failed"
    assert registry["release_in_progress"]["control_plane_sha256"] is None
    if output.exists():
        assert not list(output.glob("*"))


@pytest.mark.parametrize(
    "malformation",
    [
        "missing_completed",
        "extra_top_level",
        "extra_completed_field",
        "wrong_case_top_level",
    ],
)
def test_bundle_rejects_malformed_registry_before_creating_artifact(
    tmp_path: Path,
    malformation: str,
) -> None:
    root = _repository(tmp_path)
    registry_path = root / "deploy" / "control-plane" / "release_versions.json"
    malformed = _registry()
    if malformation == "missing_completed":
        malformed.pop("completed_releases")
    elif malformation == "extra_top_level":
        malformed["unreviewed_state"] = "unexpected"
    elif malformation == "extra_completed_field":
        malformed["completed_releases"][0]["unreviewed_state"] = "unexpected"
    else:
        malformed["Schema_Version"] = malformed.pop("schema_version")
    registry_path.write_text(json.dumps(malformed), encoding="utf-8")
    _run("git", "add", str(registry_path), cwd=root)
    _run("git", "commit", "-qm", "malformed registry", cwd=root)

    output = tmp_path / "delivery"
    result = _bundle(root, output)

    assert result.returncode != 0
    assert not output.exists()


def test_bundle_does_not_remove_a_lock_it_did_not_acquire(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    lock = root / "deploy" / "control-plane" / "release_versions.json.lock"
    lock.write_text("owned by another process", encoding="utf-8")

    result = _bundle(root, tmp_path / "delivery")

    assert result.returncode != 0
    assert lock.read_text(encoding="utf-8") == "owned by another process"
    assert (
        json.loads(
            (root / "deploy" / "control-plane" / "release_versions.json").read_text(
                encoding="utf-8"
            )
        )["current_candidate"]
        == "0.2.7"
    )


def test_bundle_does_not_remove_concurrently_created_final_archive(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    move_statement = "[System.IO.File]::Move($partialArchivePath, $archivePath)"
    _inject_and_commit(
        root,
        move_statement,
        (
            "[System.IO.File]::WriteAllText("
            "$archivePath, 'foreign-sentinel', "
            "[System.Text.UTF8Encoding]::new($false))\n"
            f"{move_statement}"
        ),
    )
    output = tmp_path / "delivery"

    result = _bundle(root, output)

    assert result.returncode != 0
    final_archive = output / "VideoInsight-control-plane-0.2.7.zip"
    assert final_archive.read_text(encoding="utf-8") == "foreign-sentinel"
    registry = json.loads(
        (root / "deploy" / "control-plane" / "release_versions.json").read_text(
            encoding="utf-8"
        )
    )
    assert registry["release_in_progress"]["server_state"] == "failed"


def test_bundle_keeps_committed_registry_state_when_backup_cleanup_fails(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    cleanup_statement = "Remove-Item -LiteralPath $cleanupPath -Force"
    _inject_and_commit(
        root,
        cleanup_statement,
        cleanup_statement + "\nthrow 'simulated post-commit cleanup failure'",
    )
    output = tmp_path / "delivery"

    result = _bundle(root, output)

    assert result.returncode == 0, result.stdout + result.stderr
    archive = output / "VideoInsight-control-plane-0.2.7.zip"
    assert archive.is_file()
    registry = json.loads(
        (root / "deploy" / "control-plane" / "release_versions.json").read_text(
            encoding="utf-8"
        )
    )
    assert registry["release_in_progress"]["server_state"] == "ready"
    assert registry["release_in_progress"]["control_plane_sha256"] == (
        hashlib.sha256(archive.read_bytes()).hexdigest()
    )


def test_bundle_burns_version_if_head_changes_during_packaging(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    marker = "$finalDirtyState = @(& git -C $repositoryRoot status"
    _inject_and_commit(
        root,
        marker,
        (
            "& git -C $repositoryRoot commit --allow-empty -qm "
            "'concurrent release commit'\n"
            "if ($LASTEXITCODE -ne 0) { throw 'failed to inject HEAD change' }\n"
            + marker
        ),
    )
    output = tmp_path / "delivery"

    result = _bundle(root, output)

    assert result.returncode != 0
    assert not (output / "VideoInsight-control-plane-0.2.7.zip").exists()
    registry = json.loads(
        (root / "deploy" / "control-plane" / "release_versions.json").read_text(
            encoding="utf-8"
        )
    )
    assert registry["release_in_progress"]["server_state"] == "failed"


def test_bundle_rejects_tampered_release_version_entry(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    marker = "$partialVerificationStream = [System.IO.FileStream]::new("
    tamper = (
        """$tamperArchive = [System.IO.Compression.ZipFile]::Open(
    $partialArchivePath,
    [System.IO.Compression.ZipArchiveMode]::Update
)
try {
    $tamperArchive.GetEntry('release_version.txt').Delete()
    $tamperEntry = $tamperArchive.CreateEntry('release_version.txt')
    $tamperWriter = [System.IO.StreamWriter]::new(
        $tamperEntry.Open(),
        [System.Text.UTF8Encoding]::new($false)
    )
    try { $tamperWriter.Write('9.9.9') } finally { $tamperWriter.Dispose() }
}
finally { $tamperArchive.Dispose() }
"""
        + marker
    )
    _inject_and_commit(root, marker, tamper)
    output = tmp_path / "delivery"

    result = _bundle(root, output)

    assert result.returncode != 0
    assert not (output / "VideoInsight-control-plane-0.2.7.zip").exists()
    registry = json.loads(
        (root / "deploy" / "control-plane" / "release_versions.json").read_text(
            encoding="utf-8"
        )
    )
    assert registry["release_in_progress"]["server_state"] == "failed"


def test_bundle_revalidates_final_after_verified_partial_is_swapped(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    move_statement = "[System.IO.File]::Move($partialArchivePath, $archivePath)"
    _inject_and_commit(
        root,
        move_statement,
        (
            "[System.IO.File]::WriteAllText("
            "$partialArchivePath, 'swapped-after-verification', "
            "[System.Text.UTF8Encoding]::new($false))\n"
            f"{move_statement}"
        ),
    )
    output = tmp_path / "delivery"

    result = _bundle(root, output)

    assert result.returncode != 0
    final_archive = output / "VideoInsight-control-plane-0.2.7.zip"
    assert final_archive.read_text(encoding="utf-8") == "swapped-after-verification"
    registry = json.loads(
        (root / "deploy" / "control-plane" / "release_versions.json").read_text(
            encoding="utf-8"
        )
    )
    assert registry["release_in_progress"]["server_state"] == "failed"
