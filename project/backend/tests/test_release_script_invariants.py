from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not executable:
        pytest.skip("PowerShell is required for Windows release script checks")
    return executable


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell compatibility")
def test_all_shipped_powershell_scripts_parse_in_windows_powershell_5() -> None:
    """Catch UTF-8-without-BOM scripts that pwsh accepts but Windows PowerShell 5 corrupts."""
    executable = shutil.which("powershell.exe")
    if not executable:
        pytest.skip("Windows PowerShell 5 is not available")

    excluded_parts = {"node_modules", "build", "dist", "release", "venv", ".venv"}
    scripts = [
        path
        for path in REPOSITORY_ROOT.rglob("*.ps1")
        if not excluded_parts.intersection(path.parts)
    ]
    assert scripts, "No PowerShell scripts found"

    failures: list[str] = []
    for script in scripts:
        escaped_path = str(script).replace("'", "''")
        command = (
            "$tokens = $null; $errors = $null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile('{escaped_path}', "
            "[ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count -gt 0) { $errors | ForEach-Object { $_.Message }; exit 1 }"
        )
        result = subprocess.run(
            [executable, "-NoProfile", "-Command", command],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            failures.append(
                f"{script.relative_to(REPOSITORY_ROOT)}: "
                f"{(result.stdout + result.stderr).strip()}"
            )

    assert not failures, "\n".join(failures)


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_published_updates_are_immutable_and_strictly_increasing(tmp_path: Path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "publish_windows_update.ps1"
    shutil.copy2(REPOSITORY_ROOT / "scripts" / script.name, script)

    def publish(version: str) -> subprocess.CompletedProcess[str]:
        installer = tmp_path / f"VideoInsight-{version}-Setup.exe"
        installer.write_bytes(f"installer-{version}".encode())
        return subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Version",
                version,
                "-InstallerPath",
                str(installer),
                "-Notes",
                "release test",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    first = publish("0.2.1")
    assert first.returncode == 0, first.stderr
    manifest_path = tmp_path / "deploy" / "control-plane" / "updates" / "latest.json"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["version"] == "0.2.1"

    duplicate = publish("0.2.1")
    assert duplicate.returncode != 0
    rollback = publish("0.2.0")
    assert rollback.returncode != 0
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["version"] == "0.2.1"

    newer = publish("0.2.2")
    assert newer.returncode == 0, newer.stderr
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["version"] == "0.2.2"


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_final_release_requires_a_new_explicit_stable_version():
    final_script = (REPOSITORY_ROOT / "scripts" / "build_final_windows_release.ps1").read_text(
        encoding="utf-8"
    )
    offline_script = (
        REPOSITORY_ROOT / "scripts" / "build_offline_windows_installer.ps1"
    ).read_text(encoding="utf-8")
    control_plane_script = (
        REPOSITORY_ROOT / "scripts" / "build_control_plane_bundle.ps1"
    ).read_text(encoding="utf-8")

    assert "[Parameter(Mandatory = $true)][string]$Version" in final_script
    assert '[version]$Version -le [version]"0.2.0"' in final_script
    assert '[string]$Version = "0.2.0"' not in final_script
    assert "[Parameter(Mandatory = $true)][string]$Version" in offline_script
    assert "同版本安装包已经存在，禁止覆盖" in offline_script
    assert "[Parameter(Mandatory = $true)]" in control_plane_script
    assert "[string]$Version = '0.2.0'" not in control_plane_script

    rejected = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "scripts" / "build_final_windows_release.ps1"),
            "-ControlPlaneUrl",
            "https://video.company.com",
            "-Version",
            "0.2.0",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert rejected.returncode != 0


def test_control_plane_compose_protects_a_shared_small_server():
    compose = (
        REPOSITORY_ROOT / "deploy" / "control-plane" / "docker-compose.yml"
    ).read_text(encoding="utf-8")
    env_example = (
        REPOSITORY_ROOT / "deploy" / "control-plane" / ".env.example"
    ).read_text(encoding="utf-8")

    for variable in (
        "CONTROL_PLANE_CPU_LIMIT",
        "CONTROL_PLANE_MEMORY_LIMIT",
        "CADDY_CPU_LIMIT",
        "CADDY_MEMORY_LIMIT",
        "CONTAINER_LOG_MAX_SIZE",
        "CONTAINER_LOG_MAX_FILES",
    ):
        assert f"${{{variable}:-" in compose
        assert f"{variable}=" in env_example

    assert compose.count("pids_limit:") == 3
    assert "x-default-logging: &default-logging" in compose
    assert compose.count("logging: *default-logging") == 3


def test_installer_verifies_before_deleting_backup_and_can_restore_it():
    install_script = (
        REPOSITORY_ROOT / "scripts" / "install_windows_desktop.ps1"
    ).read_text(encoding="utf-8")
    bootstrap_source = (
        REPOSITORY_ROOT / "scripts" / "offline_installer_bootstrap.cs"
    ).read_text(encoding="utf-8")

    verification = install_script.index('$phase = "自动验收新版本"')
    backup_cleanup = install_script.index(
        "if (Test-Path -LiteralPath $backupRoot)", verification
    )
    assert verification < backup_cleanup
    assert "自动验收未全部通过，已停止启用新版本" in install_script
    assert "Restore-UninstallRegistration" in install_script
    assert "Move-Item -LiteralPath $backupRoot -Destination $installRoot" in install_script
    assert "RunVerification(" not in bootstrap_source
    assert '" -VerifierPath \\"" + verifierPath' in bootstrap_source
