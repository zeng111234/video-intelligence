from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _powershell() -> str:
    if os.name == "nt":
        return _windows_powershell()
    executable = shutil.which("pwsh")
    if not executable:
        pytest.skip("PowerShell is required for release script checks")
    return executable


def _windows_powershell() -> str:
    executable = shutil.which("powershell.exe")
    if not executable:
        pytest.skip("Windows PowerShell 5.1 is required for release behavior checks")
    return executable


def _powershell_function_loader(
    script_path: Path, function_names: tuple[str, ...]
) -> str:
    escaped_path = str(script_path).replace("'", "''")
    names = ",".join(
        f"'{name.replace(chr(39), chr(39) * 2)}'" for name in function_names
    )
    return (
        "$tokens = $null; $errors = $null; "
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{escaped_path}', "
        "[ref]$tokens, [ref]$errors); "
        "if ($errors.Count -gt 0) { throw ($errors[0].Message) }; "
        f"foreach ($functionName in @({names})) {{ "
        "$definition = $ast.Find({ param($node) "
        "$node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -eq $functionName }, $true); "
        'if (-not $definition) { throw "Missing function: $functionName" }; '
        "Invoke-Expression ([string]$definition.Extent.Text) "
        "}; "
    )


def _find_windows_stable_versioned_executable() -> tuple[Path, str]:
    candidates = {
        Path(sys.executable),
        Path(shutil.which("powershell.exe") or ""),
        Path(shutil.which("node.exe") or ""),
        Path(shutil.which("curl.exe") or ""),
    }
    for candidate in candidates:
        if not candidate.is_file():
            continue
        escaped = str(candidate).replace("'", "''")
        command = (
            f"$info = [Diagnostics.FileVersionInfo]::GetVersionInfo('{escaped}'); "
            "[pscustomobject]@{ file = $info.FileVersion; product = $info.ProductVersion } "
            "| ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            [_windows_powershell(), "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            continue
        metadata = json.loads(result.stdout.strip())
        versions: list[str] = []
        for value in (metadata["file"], metadata["product"]):
            match = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)(?:\.0)?\s*$", value or "")
            if not match:
                break
            versions.append(".".join(match.groups()))
        if len(versions) == 2 and versions[0] == versions[1]:
            return candidate, versions[0]
    pytest.skip(
        "A Windows executable with matching stable File/ProductVersion is required"
    )


def _commit_release_fixture(tmp_path: Path) -> None:
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is required for release registry checks")
    for arguments in (
        ("config", "user.email", "release-fixture@example.invalid"),
        ("config", "user.name", "Release Fixture"),
        ("config", "core.autocrlf", "false"),
        ("add", "-A"),
        ("commit", "-q", "--allow-empty", "-m", "release fixture"),
    ):
        subprocess.run(
            [git, *arguments],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )


def _refresh_release_source_commit(tmp_path: Path) -> None:
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is required for release registry checks")
    source_commit = subprocess.run(
        [git, "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    registry_path = tmp_path / "deploy" / "control-plane" / "release_versions.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["release_in_progress"]["source_commit"] = source_commit
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    subprocess.run(
        [git, "add", "--", "deploy/control-plane/release_versions.json"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [git, "commit", "-q", "-m", "refresh release source"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )


def _prepare_final_release_fixture(
    tmp_path: Path,
    *,
    latest_version: str | None = None,
    track_registry: bool = True,
    used_versions: list[str] | None = None,
    current_candidate: str | None = None,
    windows_state: str = "attempted",
    server_state: str = "ready",
) -> tuple[Path, Path]:
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is required for release registry checks")

    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True)
    final_script = scripts / "build_final_windows_release.ps1"
    shutil.copy2(REPOSITORY_ROOT / "scripts" / final_script.name, final_script)
    paid_runner = scripts / "run_paid_release_acceptance.py"
    paid_runner.write_text("raise SystemExit(0)\n", encoding="utf-8")

    registry = tmp_path / "deploy" / "control-plane" / "release_versions.json"
    registry.parent.mkdir(parents=True)
    verifier = registry.parent / "verify_control_plane.py"
    verifier.write_text("raise SystemExit(0)\n", encoding="utf-8")
    historical_versions = (
        used_versions
        if used_versions is not None
        else [
            "0.2.0",
            "0.2.1",
            "0.2.2",
            "0.2.3",
            "0.2.4",
            "0.2.5",
            "0.2.6",
            "0.2.7",
        ]
    )
    registry.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "used_versions": historical_versions[:-1],
                "current_candidate": "0.2.7",
                "completed_releases": [],
                "release_in_progress": None,
            }
        ),
        encoding="utf-8",
    )
    if latest_version is not None:
        manifest = registry.parent / "updates" / "latest.json"
        manifest.parent.mkdir()
        manifest.write_text(json.dumps({"version": latest_version}), encoding="utf-8")

    release_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    release_python.parent.mkdir(parents=True)
    release_python.write_bytes(b"")
    paid_report = tmp_path / "paid-acceptance.json"
    paid_report.write_text("{}", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        ".venv/\nbuild/\ndeploy/control-plane/updates/\n",
        encoding="utf-8",
    )

    subprocess.run(
        [git, "init", "-q"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    for key, value in (
        ("user.email", "release-fixture@example.invalid"),
        ("user.name", "Release Fixture"),
        ("core.autocrlf", "false"),
    ):
        subprocess.run(
            [git, "config", key, value],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
    tracked_paths = [
        ".gitignore",
        "paid-acceptance.json",
        "scripts/build_final_windows_release.ps1",
        "scripts/run_paid_release_acceptance.py",
        "deploy/control-plane/verify_control_plane.py",
    ]
    if track_registry:
        tracked_paths.append("deploy/control-plane/release_versions.json")
    subprocess.run(
        [git, "add", "--", *tracked_paths],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [git, "commit", "-q", "-m", "release source"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    source_commit = subprocess.run(
        [git, "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    registry.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "used_versions": historical_versions,
                "current_candidate": current_candidate,
                "completed_releases": [],
                "release_in_progress": {
                    "version": "0.2.7",
                    "source_commit": source_commit,
                    "server_state": server_state,
                    "control_plane_sha256": "a" * 64,
                    "windows_state": windows_state,
                    "installer_sha256": None,
                },
            }
        ),
        encoding="utf-8",
    )
    if track_registry:
        subprocess.run(
            [git, "add", "--", "deploy/control-plane/release_versions.json"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            [git, "commit", "-q", "-m", "reserve release"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
    return final_script, paid_report


def _run_final_release_preflight(
    tmp_path: Path,
    version: str,
    *,
    latest_version: str | None = None,
    track_registry: bool = True,
    environment_overrides: dict[str, str] | None = None,
    used_versions: list[str] | None = None,
    current_candidate: str | None = None,
    windows_state: str = "attempted",
    server_state: str = "ready",
) -> subprocess.CompletedProcess[str]:
    final_script, paid_report = _prepare_final_release_fixture(
        tmp_path,
        latest_version=latest_version,
        track_registry=track_registry,
        used_versions=used_versions,
        current_candidate=current_candidate,
        windows_state=windows_state,
        server_state=server_state,
    )
    environment = os.environ.copy()
    for name in (
        "VIDEOINSIGHT_VERIFY_ACTIVATION_CODE",
        "VIDEOINSIGHT_VERIFY_ADMIN_USERNAME",
        "VIDEOINSIGHT_VERIFY_ADMIN_PASSWORD",
        "VIDEOINSIGHT_ACCEPTANCE_ACTIVATION_CODE",
    ):
        environment.pop(name, None)
    environment.update(environment_overrides or {})
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(final_script),
            "-ControlPlaneUrl",
            "https://video.company.test",
            "-Version",
            version,
            "-PaidAcceptanceReport",
            str(paid_report),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


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
                f"{script.relative_to(REPOSITORY_ROOT)}: {(result.stdout + result.stderr).strip()}"
            )

    assert not failures, "\n".join(failures)


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_published_updates_are_immutable_and_strictly_increasing(tmp_path: Path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "publish_windows_update.ps1"
    shutil.copy2(REPOSITORY_ROOT / "scripts" / script.name, script)

    def embedded_version(executable: Path) -> tuple[tuple[int, int, int], str] | None:
        escaped = str(executable).replace("'", "''")
        command = (
            f"$info = [Diagnostics.FileVersionInfo]::GetVersionInfo('{escaped}'); "
            "[pscustomobject]@{ file = $info.FileVersion; product = $info.ProductVersion } "
            "| ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            [_powershell(), "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return None
        metadata = json.loads(result.stdout.strip())
        prefixes = []
        for value in (metadata["file"], metadata["product"]):
            match = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)(?:\.0)?\s*$", value or "")
            if not match:
                return None
            prefixes.append(tuple(int(part) for part in match.groups()))
        if prefixes[0] != prefixes[1]:
            return None
        version_tuple = prefixes[0]
        return version_tuple, ".".join(str(part) for part in version_tuple)

    executable_candidates = {
        Path(sys.executable),
        Path(shutil.which("powershell.exe") or ""),
        Path(_powershell()),
        Path(shutil.which("node.exe") or ""),
        Path(shutil.which("curl.exe") or ""),
    }
    versioned_executables = []
    for executable in executable_candidates:
        if not executable.is_file():
            continue
        embedded = embedded_version(executable)
        if embedded is not None:
            versioned_executables.append((*embedded, executable))
    versioned_executables.sort(key=lambda item: item[0])
    distinct_versions = []
    for item in versioned_executables:
        if not distinct_versions or item[0] != distinct_versions[-1][0]:
            distinct_versions.append(item)
    if len(distinct_versions) < 2:
        pytest.skip(
            "Two Windows executables with distinct embedded versions are required"
        )
    (_, first_version, first_source), (_, newer_version, newer_source) = (
        distinct_versions[0],
        distinct_versions[-1],
    )

    registry_path = tmp_path / "deploy" / "control-plane" / "release_versions.json"
    registry_path.parent.mkdir(parents=True)

    def write_registry(
        used_versions: list[str],
        version: str,
        source: Path,
        *,
        completed_releases: list[dict[str, object]] | None = None,
    ) -> None:
        installer_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        registry_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "used_versions": used_versions,
                    "current_candidate": None,
                    "completed_releases": completed_releases or [],
                    "release_in_progress": {
                        "version": version,
                        "source_commit": "a" * 40,
                        "server_state": "ready",
                        "control_plane_sha256": "b" * 64,
                        "windows_state": "built",
                        "installer_sha256": installer_sha256,
                    },
                }
            ),
            encoding="utf-8",
        )

    def next_patch(version: str) -> str:
        major, minor, patch = (int(part) for part in version.split("."))
        return f"{major}.{minor}.{patch + 1}"

    write_registry([first_version], first_version, first_source)
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is required for release registry checks")
    subprocess.run([git, "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        [git, "add", "--", "deploy/control-plane/release_versions.json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    def publish(version: str, source: Path) -> subprocess.CompletedProcess[str]:
        installer = tmp_path / f"VideoInsight-{version}-Setup.exe"
        shutil.copy2(source, installer)
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

    first = publish(first_version, first_source)
    assert first.returncode == 0, first.stderr
    manifest_path = tmp_path / "deploy" / "control-plane" / "updates" / "latest.json"
    first_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first_installer = tmp_path / f"VideoInsight-{first_version}-Setup.exe"
    first_published = manifest_path.parent / first_manifest["installer"]
    assert first_manifest["version"] == first_version
    assert first_published.read_bytes() == first_installer.read_bytes()
    assert first_manifest["size_bytes"] == first_installer.stat().st_size
    consumed_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert first_version in consumed_registry["used_versions"]
    assert consumed_registry["current_candidate"] == next_patch(first_version)
    assert consumed_registry["release_in_progress"] is None
    assert consumed_registry["completed_releases"][0]["version"] == first_version
    assert consumed_registry["completed_releases"][0]["windows_state"] == "complete"

    duplicate = publish(first_version, first_source)
    assert duplicate.returncode != 0
    rollback = publish("0.0.0", first_source)
    assert rollback.returncode != 0
    assert (
        json.loads(manifest_path.read_text(encoding="utf-8"))["version"]
        == first_version
    )

    write_registry(
        [first_version, newer_version],
        newer_version,
        newer_source,
        completed_releases=consumed_registry["completed_releases"],
    )
    embedded_mismatch = publish(newer_version, first_source)
    assert embedded_mismatch.returncode != 0
    mismatch_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert mismatch_registry["used_versions"] == [first_version, newer_version]
    assert mismatch_registry["current_candidate"] is None
    assert mismatch_registry["release_in_progress"]["windows_state"] == "built"
    newer = publish(newer_version, newer_source)
    assert newer.returncode == 0, newer.stderr
    newer_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    newer_installer = tmp_path / f"VideoInsight-{newer_version}-Setup.exe"
    newer_published = manifest_path.parent / newer_manifest["installer"]
    assert newer_manifest["version"] == newer_version
    assert newer_published.read_bytes() == newer_installer.read_bytes()
    assert newer_manifest["size_bytes"] == newer_installer.stat().st_size
    consumed_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert newer_version in consumed_registry["used_versions"]
    assert consumed_registry["current_candidate"] == next_patch(newer_version)
    assert [entry["version"] for entry in consumed_registry["completed_releases"]] == [
        first_version,
        newer_version,
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_publish_lock_blocks_a_second_process_before_any_artifact_write(
    tmp_path: Path,
):
    source_executable, version = _find_windows_stable_versioned_executable()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    publish_script = scripts / "publish_windows_update.ps1"
    shutil.copy2(REPOSITORY_ROOT / "scripts" / publish_script.name, publish_script)
    registry_path = tmp_path / "deploy" / "control-plane" / "release_versions.json"
    registry_path.parent.mkdir(parents=True)
    installer = tmp_path / f"VideoInsight-{version}-Setup.exe"
    shutil.copy2(source_executable, installer)
    installer_hash = hashlib.sha256(installer.read_bytes()).hexdigest()
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "used_versions": [version],
                "current_candidate": None,
                "completed_releases": [],
                "release_in_progress": {
                    "version": version,
                    "source_commit": "a" * 40,
                    "server_state": "ready",
                    "control_plane_sha256": "b" * 64,
                    "windows_state": "built",
                    "installer_sha256": installer_hash,
                },
            }
        ),
        encoding="utf-8",
    )
    git = shutil.which("git")
    assert git
    subprocess.run([git, "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        [git, "add", "--", "deploy/control-plane/release_versions.json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    lock_path = Path(f"{registry_path}.lock")
    escaped_lock = str(lock_path).replace("'", "''")
    holder_command = (
        f"$stream = New-Object IO.FileStream('{escaped_lock}', "
        "[IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None); "
        "[Console]::Out.WriteLine('LOCKED'); [Console]::Out.Flush(); "
        "try { [Console]::In.ReadLine() | Out-Null } finally { "
        "$stream.Dispose(); Remove-Item -LiteralPath '" + escaped_lock + "' -Force }"
    )
    holder = subprocess.Popen(
        [_windows_powershell(), "-NoProfile", "-Command", holder_command],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "LOCKED"
        second = subprocess.run(
            [
                _windows_powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(publish_script),
                "-Version",
                version,
                "-InstallerPath",
                str(installer),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        assert second.returncode != 0
        assert lock_path.exists(), "The second process must not delete the first lock"
        assert not (registry_path.parent / "updates").exists()
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        assert registry["release_in_progress"]["windows_state"] == "built"
    finally:
        if holder.stdin is not None:
            holder.stdin.write("release\n")
            holder.stdin.flush()
        holder.communicate(timeout=30)


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
@pytest.mark.parametrize(
    ("fault_marker", "fault_statement"),
    [
        (
            "$publishStateStarted = $true",
            "if ($env:VIDEOINSIGHT_TEST_FAULT) { throw 'after publishing state' }",
        ),
        (
            "[System.IO.File]::Move($temporaryInstaller, $publishedInstaller)",
            "if ($env:VIDEOINSIGHT_TEST_FAULT) { throw 'after installer commit' }",
        ),
        (
            "-ExpectedExistingSha256 $expectedExistingHash\n        $temporaryManifestCreated = $false",
            "if ($env:VIDEOINSIGHT_TEST_FAULT) { throw 'after manifest commit' }",
        ),
    ],
)
def test_publish_can_resume_exact_artifact_at_each_commit_boundary(
    tmp_path: Path,
    fault_marker: str,
    fault_statement: str,
):
    source_executable, version = _find_windows_stable_versioned_executable()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    publish_script = scripts / "publish_windows_update.ps1"
    original_script = (REPOSITORY_ROOT / "scripts" / publish_script.name).read_text(
        encoding="utf-8-sig"
    )
    assert original_script.count(fault_marker) == 1
    publish_script.write_text(
        original_script.replace(
            fault_marker, fault_marker + "\n    " + fault_statement
        ),
        encoding="utf-8-sig",
    )
    registry_path = tmp_path / "deploy" / "control-plane" / "release_versions.json"
    registry_path.parent.mkdir(parents=True)
    installer = tmp_path / f"VideoInsight-{version}-Setup.exe"
    shutil.copy2(source_executable, installer)
    installer_hash = hashlib.sha256(installer.read_bytes()).hexdigest()
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "used_versions": [version],
                "current_candidate": None,
                "completed_releases": [],
                "release_in_progress": {
                    "version": version,
                    "source_commit": "a" * 40,
                    "server_state": "ready",
                    "control_plane_sha256": "b" * 64,
                    "windows_state": "built",
                    "installer_sha256": installer_hash,
                },
            }
        ),
        encoding="utf-8",
    )
    git = shutil.which("git")
    assert git
    subprocess.run([git, "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        [git, "add", "--", "deploy/control-plane/release_versions.json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    def publish(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                _windows_powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(publish_script),
                "-Version",
                version,
                "-InstallerPath",
                str(installer),
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )

    fault_environment = os.environ.copy()
    fault_environment["VIDEOINSIGHT_TEST_FAULT"] = "1"
    interrupted = publish(fault_environment)
    assert interrupted.returncode != 0
    interrupted_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert interrupted_registry["release_in_progress"]["windows_state"] == "publishing"
    clean_environment = os.environ.copy()
    clean_environment.pop("VIDEOINSIGHT_TEST_FAULT", None)
    resumed = publish(clean_environment)
    assert resumed.returncode == 0, resumed.stderr
    completed_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert completed_registry["release_in_progress"] is None
    assert completed_registry["completed_releases"][0]["version"] == version
    latest = json.loads(
        (registry_path.parent / "updates" / "latest.json").read_text(encoding="utf-8")
    )
    assert latest["sha256"] == installer_hash


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
@pytest.mark.parametrize("collision_target", ["installer", "manifest"])
def test_publish_never_overwrites_a_noncooperative_sentinel_after_precheck(
    tmp_path: Path,
    collision_target: str,
):
    source_executable, version = _find_windows_stable_versioned_executable()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    publish_script = scripts / "publish_windows_update.ps1"
    original_script = (REPOSITORY_ROOT / "scripts" / publish_script.name).read_text(
        encoding="utf-8-sig"
    )
    if collision_target == "installer":
        marker = "[System.IO.File]::Move($temporaryInstaller, $publishedInstaller)"
        injected = (
            "[System.IO.File]::WriteAllText($publishedInstaller, 'sentinel');\n    "
            + marker
        )
    else:
        marker = "$manifestBackupPath = Commit-LatestManifestAtomically `"
        injected = (
            "[System.IO.File]::WriteAllText($manifestPath, 'sentinel');\n    " + marker
        )
    assert original_script.count(marker) == 1
    publish_script.write_text(
        original_script.replace(marker, injected), encoding="utf-8-sig"
    )
    registry_path = tmp_path / "deploy" / "control-plane" / "release_versions.json"
    registry_path.parent.mkdir(parents=True)
    installer = tmp_path / f"VideoInsight-{version}-Setup.exe"
    shutil.copy2(source_executable, installer)
    installer_hash = hashlib.sha256(installer.read_bytes()).hexdigest()
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "used_versions": [version],
                "current_candidate": None,
                "completed_releases": [],
                "release_in_progress": {
                    "version": version,
                    "source_commit": "a" * 40,
                    "server_state": "ready",
                    "control_plane_sha256": "b" * 64,
                    "windows_state": "built",
                    "installer_sha256": installer_hash,
                },
            }
        ),
        encoding="utf-8",
    )
    git = shutil.which("git")
    assert git
    subprocess.run([git, "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        [git, "add", "--", "deploy/control-plane/release_versions.json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [
            _windows_powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(publish_script),
            "-Version",
            version,
            "-InstallerPath",
            str(installer),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    updates = registry_path.parent / "updates"
    sentinel = (
        updates / f"VideoInsight-{version}-Setup.exe"
        if collision_target == "installer"
        else updates / "latest.json"
    )
    assert sentinel.read_text(encoding="utf-8-sig") == "sentinel"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["release_in_progress"]["windows_state"] == "publishing"
    assert registry["release_in_progress"]["installer_sha256"] == installer_hash


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
@pytest.mark.parametrize(
    ("script_name", "writer_name", "path_parameter", "assertion_name"),
    [
        (
            "build_final_windows_release.ps1",
            "Write-ReleaseRegistryAtomically",
            "LiteralPath",
            "Assert-NoReparsePointsForReleasePath",
        ),
        (
            "publish_windows_update.ps1",
            "Write-ReleaseRegistryAtomically",
            "RegistryPath",
            "Assert-NoReparsePointsForPublishPath",
        ),
        (
            "build_offline_windows_installer.ps1",
            "Write-OfflineReleaseRegistryAtomically",
            "RegistryPath",
            "Assert-NoReparsePointsForOfflinePath",
        ),
    ],
)
def test_registry_commit_survives_post_replace_backup_cleanup_failure(
    tmp_path: Path,
    script_name: str,
    writer_name: str,
    path_parameter: str,
    assertion_name: str,
):
    script_path = REPOSITORY_ROOT / "scripts" / script_name
    helper_names = [writer_name]
    if "Final" in script_name or script_name.startswith("build_final"):
        helper_names[:0] = ["Get-ExistingPathAttributesForRelease", assertion_name]
    elif script_name.startswith("publish"):
        helper_names[:0] = ["Get-ExistingPathAttributesForPublish", assertion_name]
    else:
        helper_names[:0] = ["Get-ExistingPathAttributesForOfflineBuild", assertion_name]
    loader = _powershell_function_loader(script_path, tuple(helper_names))
    repository = tmp_path / script_path.stem
    registry_path = repository / "deploy" / "control-plane" / "release_versions.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text('{"value":"old"}', encoding="utf-8")
    escaped_repository = str(repository).replace("'", "''")
    escaped_registry = str(registry_path).replace("'", "''")
    command = loader + (
        f"$repositoryRoot = '{escaped_repository}'; $repoRoot = $repositoryRoot; "
        "function Remove-Item { param([string]$LiteralPath, [switch]$Force); "
        "if ($LiteralPath -like '*.bak') { throw 'injected backup cleanup failure' }; "
        "Microsoft.PowerShell.Management\\Remove-Item @PSBoundParameters }; "
        "$registry = [pscustomobject]@{ value = 'new' }; "
        f"{writer_name} -{path_parameter} '{escaped_registry}' -Registry $registry; "
        "Write-Output 'COMMITTED'"
    )
    result = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "COMMITTED" in result.stdout
    assert json.loads(registry_path.read_text(encoding="utf-8"))["value"] == "new"


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_final_release_requires_a_new_explicit_stable_version(tmp_path: Path):
    final_script = (
        REPOSITORY_ROOT / "scripts" / "build_final_windows_release.ps1"
    ).read_text(encoding="utf-8")
    offline_script = (
        REPOSITORY_ROOT / "scripts" / "build_offline_windows_installer.ps1"
    ).read_text(encoding="utf-8")
    publish_script = (
        REPOSITORY_ROOT / "scripts" / "publish_windows_update.ps1"
    ).read_text(encoding="utf-8")
    control_plane_script = (
        REPOSITORY_ROOT / "scripts" / "build_control_plane_bundle.ps1"
    ).read_text(encoding="utf-8")

    assert "[Parameter(Mandatory = $true)][string]$Version" in final_script
    assert "release_versions.json" in final_script
    assert "[int]$Registry.schema_version -ne 2" in final_script
    assert (
        "@($usedVersions | Where-Object { $_ -eq $ExpectedVersion }).Count -ne 1"
        in final_script
    )
    assert "[version]$ExpectedVersion -ne $highestUsedVersion" in final_script
    assert '[string]$releaseInProgress.server_state -ne "ready"' in final_script
    assert (
        '$windowsState -notin @("pending", "building", "attempted", "built")'
        in final_script
    )
    assert '[string]$Version = "0.2.0"' not in final_script
    assert "[Parameter(Mandatory = $true)][string]$Version" in offline_script
    assert "Assert-OfflineBuildLifecycle" in offline_script
    assert '[string]$release.windows_state -ne "attempted"' in offline_script
    assert "脚本不会清理或覆盖已有交付文件" in offline_script
    assert '$startingWindowsState -notin @("built", "publishing")' in publish_script
    assert "installer_sha256 -ne $sourceHash" in publish_script
    assert "[Parameter(Mandatory = $true)]" in control_plane_script
    assert "[string]$Version = '0.2.0'" not in control_plane_script
    assert "$candidateBaseVersion" in control_plane_script
    assert "$usedBaseVersions" in control_plane_script

    rejected = _run_final_release_preflight(tmp_path, "0.2.0")
    assert rejected.returncode != 0
    output = rejected.stdout + rejected.stderr
    assert "0.2.0" in output
    assert "PaidAcceptanceReport" not in output
    assert "[1/7]" not in output


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_final_release_requires_the_tracked_version_registry(tmp_path: Path):
    rejected = _run_final_release_preflight(
        tmp_path,
        "0.2.7",
        track_registry=False,
    )

    assert rejected.returncode != 0
    output = rejected.stdout + rejected.stderr
    assert "release_versions.json" in output
    assert "[1/7]" not in output


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
@pytest.mark.parametrize("latest_version", ["0.2.7", "0.2.8"])
def test_final_release_rejects_equal_or_older_than_latest_before_online_checks(
    tmp_path: Path,
    latest_version: str,
):
    rejected = _run_final_release_preflight(
        tmp_path,
        "0.2.7",
        latest_version=latest_version,
    )

    assert rejected.returncode != 0
    output = rejected.stdout + rejected.stderr
    assert latest_version in output
    assert "[1/7]" not in output


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_current_candidate_requires_paid_activation_before_online_checks(
    tmp_path: Path,
):
    rejected = _run_final_release_preflight(
        tmp_path,
        "0.2.7",
        environment_overrides={
            "VIDEOINSIGHT_VERIFY_ACTIVATION_CODE": "test-only",
            "VIDEOINSIGHT_VERIFY_ADMIN_USERNAME": "test-only",
            "VIDEOINSIGHT_VERIFY_ADMIN_PASSWORD": "test-only",
        },
    )

    assert rejected.returncode != 0
    output = rejected.stdout + rejected.stderr
    assert "VIDEOINSIGHT_ACCEPTANCE_ACTIVATION_CODE" in output
    assert "[1/7]" not in output


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_final_release_rejects_version_that_is_not_highest_burned_version(
    tmp_path: Path,
):
    rejected = _run_final_release_preflight(
        tmp_path,
        "0.2.7",
        used_versions=["0.2.6", "0.2.7", "0.2.8"],
        current_candidate=None,
    )

    assert rejected.returncode != 0
    output = rejected.stdout + rejected.stderr
    assert "0.2.7" in output
    assert "[1/7]" not in output


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point behavior")
def test_final_release_rejects_build_output_junction_before_online_checks(
    tmp_path: Path,
):
    final_script, paid_report = _prepare_final_release_fixture(tmp_path)
    frontend = tmp_path / "project" / "frontend"
    frontend.mkdir(parents=True)
    external = tmp_path / "external-dist"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    junction = frontend / "dist"
    escaped_junction = str(junction).replace("'", "''")
    escaped_external = str(external).replace("'", "''")
    created = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-Command",
            f"New-Item -ItemType Junction -Path '{escaped_junction}' -Target '{escaped_external}' | Out-Null",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"Junction creation is unavailable: {created.stderr}")

    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(final_script),
            "-ControlPlaneUrl",
            "https://video.company.test",
            "-Version",
            "0.2.7",
            "-PaidAcceptanceReport",
            str(paid_report),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "[1/7]" not in result.stdout + result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
@pytest.mark.parametrize(
    ("failure_mode", "version", "track_registry", "expected_error"),
    [
        ("missing_report", "0.2.7", True, "missing-paid-acceptance.json"),
        ("used_version", "0.2.0", True, "0.2.0"),
        ("untracked_registry", "0.2.7", False, "release_versions.json"),
    ],
)
def test_final_release_clears_all_release_credentials_after_preflight_failure(
    tmp_path: Path,
    failure_mode: str,
    version: str,
    track_registry: bool,
    expected_error: str,
):
    final_script, paid_report = _prepare_final_release_fixture(
        tmp_path,
        track_registry=track_registry,
    )
    missing_report = tmp_path / "missing-paid-acceptance.json"
    report = missing_report if failure_mode == "missing_report" else paid_report
    escaped_script = str(final_script).replace("'", "''")
    escaped_report = str(report).replace("'", "''")
    names = (
        "VIDEOINSIGHT_VERIFY_ACTIVATION_CODE",
        "VIDEOINSIGHT_VERIFY_ADMIN_USERNAME",
        "VIDEOINSIGHT_VERIFY_ADMIN_PASSWORD",
        "VIDEOINSIGHT_ACCEPTANCE_ACTIVATION_CODE",
    )
    environment = os.environ.copy()
    for name in names:
        environment[name] = "secret-value-must-not-be-printed"
    powershell_names = ",".join(f"'{name}'" for name in names)
    command = (
        f"$names = @({powershell_names}); "
        "$caught = ''; "
        "try { "
        f"& '{escaped_script}' -ControlPlaneUrl 'https://video.company.test' "
        f"-Version '{version}' -PaidAcceptanceReport '{escaped_report}' "
        "} catch { $caught = $_.Exception.Message }; "
        "$remaining = @($names | Where-Object { "
        "-not [string]::IsNullOrWhiteSpace("
        "[Environment]::GetEnvironmentVariable($_, 'Process')) }); "
        "if ($remaining.Count -gt 0) { "
        "[Console]::Error.WriteLine('NOT_CLEARED:' + ($remaining -join ',')); exit 24 }; "
        "Write-Output ('CAUGHT:' + $caught); Write-Output 'CLEARED'"
    )
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-Command", command],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "CLEARED" in result.stdout
    assert expected_error in result.stdout
    assert "secret-value-must-not-be-printed" not in result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_authoritative_release_gate_retries_transport_failure_only_once():
    script_text = (
        REPOSITORY_ROOT / "scripts" / "build_final_windows_release.ps1"
    ).read_text(encoding="utf-8")
    function_start = script_text.index("function Invoke-ReleaseJsonRequest {")
    function_end = script_text.index(
        "function Invoke-ControlPlaneAuthoritativeGate {", function_start
    )
    request_function = script_text[function_start:function_end]

    assert "for ($attempt = 0; $attempt -lt 2; $attempt += 1)" in request_function
    assert "if ($attempt -eq 0)" in request_function
    assert "Start-Sleep -Milliseconds 750" in request_function
    assert request_function.count("$client.SendAsync($request)") == 1
    assert request_function.index("$client.SendAsync($request)") < request_function.index(
        "if ($attempt -eq 0)"
    )
    assert request_function.index("$statusCode = [int]$response.StatusCode") > (
        request_function.index("if ($attempt -eq 0)")
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_ignored_python_cannot_receive_release_secrets_or_replace_authoritative_gate(
    tmp_path: Path,
):
    final_script, paid_report = _prepare_final_release_fixture(tmp_path)
    sentinel = tmp_path / "ignored-python-executed.txt"
    sitecustomize = tmp_path / ".venv" / "Scripts" / "sitecustomize.py"
    sitecustomize.write_text(
        "import os\n"
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text(repr(dict(os.environ)), encoding='utf-8')\n",
        encoding="utf-8",
    )
    verification_stub = (
        tmp_path / "deploy" / "control-plane" / "verify_control_plane.py"
    )
    verification_stub.write_text(
        "print('FAKE_PYTHON_PASS_MUST_NOT_AUTHORIZE_RELEASE')\n",
        encoding="utf-8",
    )
    _commit_release_fixture(tmp_path)
    _refresh_release_source_commit(tmp_path)

    environment = os.environ.copy()
    for name in (
        "VIDEOINSIGHT_VERIFY_ACTIVATION_CODE",
        "VIDEOINSIGHT_VERIFY_ADMIN_USERNAME",
        "VIDEOINSIGHT_VERIFY_ADMIN_PASSWORD",
        "VIDEOINSIGHT_ACCEPTANCE_ACTIVATION_CODE",
    ):
        environment[name] = "secret-value-must-not-be-printed"
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(final_script),
            "-ControlPlaneUrl",
            "https://video.company.test",
            "-Version",
            "0.2.7",
            "-PaidAcceptanceReport",
            str(paid_report),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert not sentinel.exists()
    assert "FAKE_PYTHON_PASS_MUST_NOT_AUTHORIZE_RELEASE" not in output
    assert "secret-value-must-not-be-printed" not in output
    script_text = final_script.read_text(encoding="utf-8")
    first_stage = script_text.index('Write-Output "[1/7]')
    authoritative_gate = script_text.index(
        "Invoke-ControlPlaneAuthoritativeGate `", first_stage
    )
    assert authoritative_gate > first_stage
    assert "& $releasePython" not in script_text
    assert "--secret-env-file" not in script_text


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
@pytest.mark.parametrize("verifier_state", ["dirty", "untracked"])
def test_dirty_or_untracked_verifier_never_receives_release_credentials(
    tmp_path: Path,
    verifier_state: str,
):
    final_script, paid_report = _prepare_final_release_fixture(tmp_path)
    verification_stub = (
        tmp_path / "deploy" / "control-plane" / "verify_control_plane.py"
    )
    paid_stub = tmp_path / "scripts" / "run_paid_release_acceptance.py"
    paid_stub.write_text("raise SystemExit(0)\n", encoding="utf-8")
    if verifier_state == "dirty":
        verification_stub.write_text("raise SystemExit(0)\n", encoding="utf-8")
    _commit_release_fixture(tmp_path)

    sentinel = tmp_path / "verifier-executed.txt"
    verification_stub.write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('secret process executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    names = (
        "VIDEOINSIGHT_VERIFY_ACTIVATION_CODE",
        "VIDEOINSIGHT_VERIFY_ADMIN_USERNAME",
        "VIDEOINSIGHT_VERIFY_ADMIN_PASSWORD",
        "VIDEOINSIGHT_ACCEPTANCE_ACTIVATION_CODE",
    )
    environment = os.environ.copy()
    for name in names:
        environment[name] = "secret-value-must-not-be-printed"
    escaped_script = str(final_script).replace("'", "''")
    escaped_report = str(paid_report).replace("'", "''")
    powershell_names = ",".join(f"'{name}'" for name in names)
    command = (
        f"$names = @({powershell_names}); $caught = ''; "
        "try { "
        f"& '{escaped_script}' -ControlPlaneUrl 'https://video.company.test' "
        f"-Version '0.2.7' -PaidAcceptanceReport '{escaped_report}' "
        "} catch { $caught = $_.Exception.Message }; "
        "$remaining = @($names | Where-Object { "
        "-not [string]::IsNullOrWhiteSpace("
        "[Environment]::GetEnvironmentVariable($_, 'Process')) }); "
        "if ($remaining.Count -gt 0) { "
        "[Console]::Error.WriteLine('NOT_CLEARED:' + ($remaining -join ',')); exit 24 }; "
        "Write-Output ('CAUGHT:' + $caught); Write-Output 'CLEARED'"
    )
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-Command", command],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "CLEARED" in output
    assert "[1/7]" not in output
    assert "secret-value-must-not-be-printed" not in output
    assert not sentinel.exists()


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


def test_control_plane_bundle_keeps_release_registry_and_secret_file_gates():
    script = (REPOSITORY_ROOT / "scripts" / "build_control_plane_bundle.ps1").read_text(
        encoding="utf-8"
    )

    version_registry = script.index("release_versions.json")
    used_version_gate = script.index("$used -contains $ExpectedVersion")
    candidate_gate = script.index("$candidateProperty.Value -ne $ExpectedVersion")
    lifecycle_gate = script.index(
        "Assert-FreshReleaseCandidate -Registry $releaseRegistry"
    )
    release_check = script.index("check_release.ps1")
    archive_creation = script.index(
        "$archive = [System.IO.Compression.ZipArchive]::new("
    )
    assert version_registry < lifecycle_gate < release_check < archive_creation
    assert used_version_gate < lifecycle_gate
    assert candidate_gate < lifecycle_gate < release_check
    assert "-AllowDirty" not in script
    assert "build\\control-plane-ready-$Version" in script
    assert "$candidateBaseVersion" in script
    assert "$usedBaseVersions" in script
    output_reparse_gate = script.index(
        "Assert-NoReparsePointInExistingPath -LiteralPath $resolvedOutputDirectory"
    )
    output_creation = script.index(
        "[System.IO.Directory]::CreateDirectory($resolvedOutputDirectory)"
    )
    assert output_reparse_gate < output_creation < archive_creation
    committed_tree_gate = script.index("ls-tree -r $sourceCommit")
    committed_blob_read = script.index("cat-file blob $BlobSha")
    archive_blob_write = script.index(
        "$committedBlobBytes[$entry.Entry]", archive_creation
    )
    assert (
        committed_blob_read
        < committed_tree_gate
        < archive_creation
        < archive_blob_write
    )
    assert "$record.Mode -notin @('100644', '100755')" in script
    assert "release_versions.json'" in script
    assert "data/avatar_assets" not in script
    assert "data\\avatar_assets" not in script
    bootstrap_manifest = (
        "deploy/control-plane/bootstrap/avatar_assets/shuying_cloud.json"
    )
    assert bootstrap_manifest in script
    bootstrap_gate = script.index(
        "$committedBlobBytes.ContainsKey($avatarManifestRelativePath)"
    )
    bootstrap_read = script.index("$avatarManifest =", bootstrap_gate)
    assert candidate_gate < bootstrap_gate < bootstrap_read < archive_creation

    for extension in (
        ".key",
        ".pem",
        ".p12",
        ".pfx",
        ".jks",
        ".keystore",
        ".der",
        ".p8",
        ".ppk",
    ):
        assert f"'{extension}'" in script
    for marker in (
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----",
        "-----BEGIN DSA PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "PuTTY-User-Key-File:",
    ):
        assert marker in script

    manifest_path = (
        REPOSITORY_ROOT
        / "deploy"
        / "control-plane"
        / "bootstrap"
        / "avatar_assets"
        / "shuying_cloud.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "$committedBlobBytes.ContainsKey($avatarManifestRelativePath)" in script
    assets = manifest["assets"]
    assert len(assets) == 2
    assert {asset["provider_asset_id"] for asset in assets} == {"21920", "7869"}
    assert all(
        asset["authorized"] is True
        and asset["shared"] is True
        and asset["status"] == "ready"
        for asset in assets
    )
    serialized_manifest = json.dumps(manifest, ensure_ascii=False)
    assert "sample_path" not in serialized_manifest
    assert not re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", serialized_manifest)


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point behavior")
def test_release_bundle_and_publish_reject_junction_ancestors(tmp_path: Path):
    def create_junction(path: Path, target: Path) -> None:
        escaped_path = str(path).replace("'", "''")
        escaped_target = str(target).replace("'", "''")
        result = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-Command",
                f"New-Item -ItemType Junction -Path '{escaped_path}' -Target '{escaped_target}' | Out-Null",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(f"Junction creation is unavailable: {result.stderr}")

    external_bundle = tmp_path / "external-bundle"
    external_bundle.mkdir()
    bundle_sentinel = external_bundle / "sentinel.txt"
    bundle_sentinel.write_text("keep", encoding="utf-8")
    bundle_junction = tmp_path / "bundle-output-link"
    create_junction(bundle_junction, external_bundle)
    bundle_script = REPOSITORY_ROOT / "scripts" / "build_control_plane_bundle.ps1"
    escaped_bundle_script = str(bundle_script).replace("'", "''")
    escaped_bundle_output = str(bundle_junction / "release").replace("'", "''")
    bundle_command = (
        "$tokens = $null; $errors = $null; "
        f"$ast = [Management.Automation.Language.Parser]::ParseFile('{escaped_bundle_script}', "
        "[ref]$tokens, [ref]$errors); "
        "foreach ($name in @('Assert-NoReparsePoint', "
        "'Assert-NoReparsePointInExistingPath')) { "
        "$definition = $ast.Find({ param($node) "
        "$node -is [Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -eq $name }, $true); "
        "Invoke-Expression ([string]$definition.Extent.Text) }; "
        f"Assert-NoReparsePointInExistingPath -LiteralPath '{escaped_bundle_output}' "
        "-Label 'test output'"
    )
    bundle_result = subprocess.run(
        [_powershell(), "-NoProfile", "-Command", bundle_command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert bundle_result.returncode != 0
    assert bundle_sentinel.read_text(encoding="utf-8") == "keep"

    publish_root = tmp_path / "publish-fixture"
    publish_scripts = publish_root / "scripts"
    publish_scripts.mkdir(parents=True)
    publish_script = publish_scripts / "publish_windows_update.ps1"
    shutil.copy2(
        REPOSITORY_ROOT / "scripts" / "publish_windows_update.ps1",
        publish_script,
    )
    external_deploy = tmp_path / "external-deploy"
    external_deploy.mkdir()
    publish_sentinel = external_deploy / "sentinel.txt"
    publish_sentinel.write_text("keep", encoding="utf-8")
    create_junction(publish_root / "deploy", external_deploy)
    installer = publish_root / "VideoInsight-0.2.7-Setup.exe"
    installer.write_bytes(b"not executed")
    publish_result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(publish_script),
            "-Version",
            "0.2.7",
            "-InstallerPath",
            str(installer),
        ],
        cwd=publish_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert publish_result.returncode != 0
    assert publish_sentinel.read_text(encoding="utf-8") == "keep"


def test_installer_verifies_before_deleting_backup_and_can_restore_it():
    install_script = (
        REPOSITORY_ROOT / "scripts" / "install_windows_desktop.ps1"
    ).read_text(encoding="utf-8")
    bootstrap_source = (
        REPOSITORY_ROOT / "scripts" / "offline_installer_bootstrap.cs"
    ).read_text(encoding="utf-8")
    electron_main = (
        REPOSITORY_ROOT / "project" / "frontend" / "electron" / "main.cjs"
    ).read_text(encoding="utf-8")

    verification = install_script.index('$phase = "自动验收新版本"')
    committed = install_script.index("$installationCommitted = $true", verification)
    backup_cleanup = install_script.index(
        "$oldBackupRemoved = Remove-PostCommitBackupSafely", committed
    )
    rollback_guard = install_script.index(
        "Test-InstallationRollbackRequired -InstallationCommitted $installationCommitted",
        backup_cleanup,
    )
    assert verification < committed < backup_cleanup < rollback_guard
    assert "自动验收未全部通过，已停止启用新版本" in install_script
    assert "Restore-UninstallRegistration" in install_script
    assert (
        "Remove-Item -LiteralPath $RegistryPath -Recurse -Force -ErrorAction Stop"
        in install_script
    )
    assert "Remove-DirectoryTreeWithoutFollowingReparse" in install_script
    assert (
        "Move-Item -LiteralPath $backupRoot -Destination $existingInstallRoot"
        in install_script
    )
    assert "RunVerification(" not in bootstrap_source
    assert '" -VerifierPath \\"" + verifierPath' in bootstrap_source
    assert '" -InstallRoot \\"" + installationPaths.InstallRoot' in bootstrap_source
    assert '" -RuntimeRoot \\"" + installationPaths.RuntimeRoot' in bootstrap_source
    assert "new FolderBrowserDialog()" in bootstrap_source
    assert '@"Local\\VideoInsight-Installer"' in bootstrap_source
    assert "installerMutex.WaitOne(0, false)" in bootstrap_source
    assert "installerMutex.ReleaseMutex()" in bootstrap_source
    assert "不要重复启动" in bootstrap_source
    assert "卸载 VideoInsight" in bootstrap_source
    assert "drive.DriveType != DriveType.Fixed" in bootstrap_source
    assert "Path.GetDirectoryName(installationPaths.InstallRoot)" in bootstrap_source
    assert "CreatePowerShellStartInfo(temporaryRoot)" in bootstrap_source
    assert "startInfo.WorkingDirectory = workingDirectory" in bootstrap_source
    assert "cwd: path.dirname(destination)" in electron_main
    assert (
        "New-ItemProperty -Path $uninstallKey -Name RuntimeLocation" in install_script
    )
    assert 'Join-Path $installRoot "runtime-location.json"' in install_script


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_installer_migrates_customer_data_with_content_verification(tmp_path: Path):
    install_script = REPOSITORY_ROOT / "scripts" / "install_windows_desktop.ps1"
    loader = _powershell_function_loader(
        install_script,
        (
            "Get-ExistingPathAttributesForInstall",
            "Assert-NoReparsePoint",
            "Assert-NoReparsePointsInTree",
            "Get-FileSha256ForMigration",
            "Copy-DirectoryTreeForMigration",
        ),
    )
    source = tmp_path / "old-data"
    destination = tmp_path / "new-data"
    (source / "data" / "assets").mkdir(parents=True)
    (source / "data" / "video_intelligence.db").write_bytes(b"sqlite-state")
    (source / "data" / "assets" / "material.mp4").write_bytes(b"customer-material")
    escaped_source = str(source).replace("'", "''")
    escaped_destination = str(destination).replace("'", "''")
    command = loader + (
        f"Copy-DirectoryTreeForMigration -SourceRoot '{escaped_source}' "
        f"-DestinationRoot '{escaped_destination}'; Write-Output 'MIGRATED'"
    )
    result = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "MIGRATED" in result.stdout
    assert (
        destination / "data" / "video_intelligence.db"
    ).read_bytes() == b"sqlite-state"
    assert (
        destination / "data" / "assets" / "material.mp4"
    ).read_bytes() == b"customer-material"


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_installer_post_commit_cleanup_failure_cannot_roll_back_new_install(
    tmp_path: Path,
):
    install_script = REPOSITORY_ROOT / "scripts" / "install_windows_desktop.ps1"
    loader = _powershell_function_loader(
        install_script,
        ("Test-InstallationRollbackRequired", "Remove-PostCommitBackupSafely"),
    )
    application = tmp_path / "installed" / "VideoInsight.exe"
    registration = tmp_path / "uninstall-registration.txt"
    backup = tmp_path / "old-backup"
    application.parent.mkdir()
    backup.mkdir()
    application.write_text("new-app", encoding="utf-8")
    registration.write_text("new-registry", encoding="utf-8")
    escaped_application = str(application).replace("'", "''")
    escaped_registration = str(registration).replace("'", "''")
    escaped_backup = str(backup).replace("'", "''")
    command = loader + (
        "function Assert-NoReparsePointsInTree { throw 'injected cleanup failure' }; "
        f"$removed = Remove-PostCommitBackupSafely -Path '{escaped_backup}' -Label 'backup'; "
        "if ($removed) { throw 'Cleanup fault was not injected.' }; "
        "if (Test-InstallationRollbackRequired -InstallationCommitted $true) { "
        f"Remove-Item -LiteralPath '{escaped_application}' -Force; "
        f"Remove-Item -LiteralPath '{escaped_registration}' -Force "
        "}; Write-Output 'COMMITTED'"
    )
    result = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "COMMITTED" in result.stdout
    assert application.read_text(encoding="utf-8") == "new-app"
    assert registration.read_text(encoding="utf-8") == "new-registry"


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_first_install_rollback_continues_when_uninstall_key_never_existed(
    tmp_path: Path,
):
    install_script = REPOSITORY_ROOT / "scripts" / "install_windows_desktop.ps1"
    loader = _powershell_function_loader(
        install_script,
        ("Restore-UninstallRegistration",),
    )
    touched_shortcut = tmp_path / "VideoInsight.lnk"
    touched_shortcut.write_text("new shortcut", encoding="utf-8")
    escaped_shortcut = str(touched_shortcut).replace("'", "''")
    missing_registry_key = (
        "HKCU:\\Software\\VideoInsight-Rollback-Test-" + os.urandom(8).hex()
    )
    command = loader + (
        f"Restore-UninstallRegistration -RegistryPath '{missing_registry_key}' "
        "-PreviousValues $null; "
        f"Remove-Item -LiteralPath '{escaped_shortcut}' -Force; Write-Output 'CONTINUED'"
    )
    result = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "CONTINUED" in result.stdout
    assert not touched_shortcut.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_installer_rollback_recreates_shortcut_parent_before_restore(tmp_path: Path):
    install_script = REPOSITORY_ROOT / "scripts" / "install_windows_desktop.ps1"
    loader = _powershell_function_loader(
        install_script,
        (
            "Get-ExistingPathAttributesForInstall",
            "Assert-NoReparsePoint",
            "Assert-NoReparsePointsInAncestors",
            "Restore-ShortcutBackupSafely",
        ),
    )
    backup = tmp_path / "backup.lnk"
    shortcut = (
        tmp_path / "Start Menu" / "Programs" / "VideoInsight" / "VideoInsight.lnk"
    )
    backup.write_text("original shortcut", encoding="utf-8")
    escaped_backup = str(backup).replace("'", "''")
    escaped_shortcut = str(shortcut).replace("'", "''")
    command = loader + (
        f"Restore-ShortcutBackupSafely -BackupPath '{escaped_backup}' "
        f"-ShortcutPath '{escaped_shortcut}'; Write-Output 'RESTORED'"
    )
    result = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "RESTORED" in result.stdout
    assert shortcut.read_text(encoding="utf-8") == "original shortcut"


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_installer_version_guard_rejects_reinstall_and_downgrade_in_powershell():
    install_script = REPOSITORY_ROOT / "scripts" / "install_windows_desktop.ps1"
    script_text = install_script.read_text(encoding="utf-8")
    registry_read = script_text.index("Get-ItemProperty -LiteralPath $uninstallKey")
    existing_install_probe = script_text.index(
        "$hasExistingInstall = Test-Path -LiteralPath $existingInstallRoot",
        registry_read,
    )
    guard_call = script_text.index("Assert-NewerInstallerVersion", registry_read)
    first_install_write = script_text.index(
        "New-Item -ItemType Directory -Path $programsRoot", guard_call
    )
    assert registry_read < existing_install_probe < guard_call < first_install_write
    assert "([string]$previousUninstall.DisplayVersion)" in script_text
    assert (
        "-RegisteredVersion ([string]$previousUninstall.DisplayVersion)" in script_text
    )
    assert "-HasExistingInstall $hasExistingInstall" in script_text

    escaped_script = str(install_script).replace("'", "''")

    def invoke(
        new_version: str,
        installed_version: str,
        registered_version: str,
        has_existing_install: bool,
    ) -> subprocess.CompletedProcess[str]:
        escaped_new = new_version.replace("'", "''")
        escaped_installed = installed_version.replace("'", "''")
        escaped_registered = registered_version.replace("'", "''")
        existing_install_literal = "$true" if has_existing_install else "$false"
        command = (
            "$tokens = $null; $errors = $null; "
            f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{escaped_script}', "
            "[ref]$tokens, [ref]$errors); "
            "$definition = $ast.Find({ param($node) "
            "$node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and "
            "$node.Name -eq 'Assert-NewerInstallerVersion' }, $true); "
            "if (-not $definition) { throw 'Version guard function is missing.' }; "
            "Invoke-Expression ([string]$definition.Extent.Text); "
            "try { "
            f"Assert-NewerInstallerVersion -NewVersion '{escaped_new}' "
            f"-InstalledVersion '{escaped_installed}' "
            f"-RegisteredVersion '{escaped_registered}' "
            f"-HasExistingInstall {existing_install_literal}; "
            "Write-Output 'ALLOWED'; exit 0 "
            "} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 23 }"
        )
        return subprocess.run(
            [_powershell(), "-NoProfile", "-Command", command],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    for new_version, installed_version, registered_version, has_existing_install in (
        ("0.2.7", "", "", False),
        ("0.2.8", "", "0.2.7", False),
        ("0.2.8", "0.2.7", "0.2.7", True),
        ("0.2.28", "0.2.25", "0.2.27", True),
    ):
        allowed = invoke(
            new_version, installed_version, registered_version, has_existing_install
        )
        assert allowed.returncode == 0, allowed.stderr
        assert "ALLOWED" in allowed.stdout

    for new_version, installed_version, registered_version, has_existing_install in (
        ("0.2.7", "", "0.2.6", True),
        ("0.2.7", "unknown", "0.2.6", True),
        ("0.2.7", "0.2.7", "0.2.7", True),
        ("0.2.6", "0.2.7", "0.2.7", True),
        ("0.2.27", "0.2.25", "0.2.27", True),
        ("0.2.7", "", "0.2.8", False),
        ("0.2.7", "", "unknown", False),
    ):
        rejected = invoke(
            new_version, installed_version, registered_version, has_existing_install
        )
        assert rejected.returncode != 0
        assert "ALLOWED" not in rejected.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_installer_reads_actual_executable_version_independently_of_registry(
    tmp_path: Path,
):
    source_executable, actual_version = _find_windows_stable_versioned_executable()
    install_root = tmp_path / "Programs" / "VideoInsight"
    install_root.mkdir(parents=True)
    installed_executable = install_root / "VideoInsight.exe"
    shutil.copy2(source_executable, installed_executable)
    install_script = REPOSITORY_ROOT / "scripts" / "install_windows_desktop.ps1"
    loader = _powershell_function_loader(
        install_script,
        (
            "Get-ExistingPathAttributesForInstall",
            "Assert-NoReparsePoint",
            "Assert-NoReparsePointsInTree",
            "Get-ValidatedExecutableStableVersion",
            "Get-ValidatedInstalledApplicationVersion",
        ),
    )
    escaped_root = str(install_root).replace("'", "''")

    def invoke() -> subprocess.CompletedProcess[str]:
        command = loader + (
            f"$actual = Get-ValidatedInstalledApplicationVersion -InstallRoot '{escaped_root}' "
            '; Write-Output "VALIDATED:$actual"'
        )
        return subprocess.run(
            [_windows_powershell(), "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    exact = invoke()
    assert exact.returncode == 0, exact.stderr
    assert f"VALIDATED:{actual_version}" in exact.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_installer_only_targets_processes_inside_install_root(tmp_path: Path):
    install_script = REPOSITORY_ROOT / "scripts" / "install_windows_desktop.ps1"
    script_text = install_script.read_text(encoding="utf-8")
    assert "$process.Path" in script_text
    assert "Stop-VideoInsightProcesses -ExpectedInstallRoot $installRoot" in script_text
    assert "$processes | Stop-Process -Force" not in script_text
    assert "$shortcutBackups[$shortcutPath]" in script_text
    assert "Restore-ShortcutBackupSafely" in script_text
    assert 'Join-Path $startMenuDir "卸载 VideoInsight.lnk"' in script_text
    assert "DisplayIcon" in script_text
    assert "EstimatedSize" in script_text
    assert "QuietUninstallString" in script_text
    assert "$startMenuDirCreated" in script_text
    assert "Remove-Item -LiteralPath $startMenuDir -Recurse" not in script_text
    assert "Get-ChildItem -LiteralPath $startMenuDir -Force" in script_text

    escaped_script = str(install_script).replace("'", "''")
    install_root = tmp_path / "Programs" / "VideoInsight"
    inside = install_root / "VideoInsight.exe"
    outside = tmp_path / "Other" / "VideoInsight.exe"

    def invoke(process_path: str) -> subprocess.CompletedProcess[str]:
        escaped_root = str(install_root).replace("'", "''")
        escaped_process = process_path.replace("'", "''")
        command = (
            "$tokens = $null; $errors = $null; "
            f"$ast = [Management.Automation.Language.Parser]::ParseFile('{escaped_script}', "
            "[ref]$tokens, [ref]$errors); "
            "$definition = $ast.Find({ param($node) "
            "$node -is [Management.Automation.Language.FunctionDefinitionAst] -and "
            "$node.Name -eq 'Test-ProcessPathWithinInstallRoot' }, $true); "
            "Invoke-Expression ([string]$definition.Extent.Text); "
            f"Test-ProcessPathWithinInstallRoot -ProcessPath '{escaped_process}' "
            f"-ExpectedInstallRoot '{escaped_root}'"
        )
        return subprocess.run(
            [_powershell(), "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    allowed = invoke(str(inside))
    assert allowed.returncode == 0
    assert "True" in allowed.stdout
    external = invoke(str(outside))
    assert external.returncode == 0
    assert "False" in external.stdout
    unreadable = invoke("")
    assert unreadable.returncode != 0


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_uninstaller_preserves_external_processes_and_user_shortcuts(tmp_path: Path):
    uninstall_script = REPOSITORY_ROOT / "scripts" / "uninstall_windows_desktop.ps1"
    script_text = uninstall_script.read_text(encoding="utf-8")
    assert '$cleanupPowerShell = Join-Path $PSHOME "powershell.exe"' in script_text
    assert "Join-Path `$target 'Uninstall-VideoInsight.ps1'" in script_text
    assert "Join-Path `$target 'uninstall_windows_desktop.ps1'" not in script_text
    assert (
        "Remove-Item -LiteralPath '$escapedUninstallKey' -Recurse -Force -ErrorAction Stop"
        in script_text
    )
    assert "Remove-Item -LiteralPath $installRoot -Recurse" not in script_text
    assert "Remove-Item -LiteralPath $startMenuDir -Recurse" not in script_text
    directory_delete = script_text.index(
        "[System.IO.Directory]::Delete(`$target, `$false)"
    )
    registry_delete = script_text.index(
        "Remove-Item -LiteralPath '$escapedUninstallKey' -Recurse -Force -ErrorAction Stop"
    )
    assert directory_delete < registry_delete

    install_root = tmp_path / "Programs" / "VideoInsight"
    external_root = tmp_path / "Programs" / "VideoInsightEvil"
    install_root.mkdir(parents=True)
    external_root.mkdir(parents=True)
    owned_target = install_root / "VideoInsight.exe"
    external_target = external_root / "VideoInsight.exe"
    owned_target.write_text("owned", encoding="utf-8")
    external_target.write_text("external", encoding="utf-8")
    owned_shortcut = tmp_path / "owned.lnk"
    external_shortcut = tmp_path / "external.lnk"
    uninstall_loader = _powershell_function_loader(
        uninstall_script,
        (
            "Get-ExistingPathAttributesForUninstall",
            "Assert-NoReparsePoint",
            "Test-ProcessPathWithinInstallRoot",
            "Remove-OwnedShortcutSafely",
        ),
    )
    escaped_root = str(install_root).replace("'", "''")
    escaped_owned_target = str(owned_target).replace("'", "''")
    escaped_external_target = str(external_target).replace("'", "''")
    escaped_owned_shortcut = str(owned_shortcut).replace("'", "''")
    escaped_external_shortcut = str(external_shortcut).replace("'", "''")
    command = uninstall_loader + (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$owned = $shell.CreateShortcut('{escaped_owned_shortcut}'); "
        f"$owned.TargetPath = '{escaped_owned_target}'; $owned.Save(); "
        f"$external = $shell.CreateShortcut('{escaped_external_shortcut}'); "
        f"$external.TargetPath = '{escaped_external_target}'; $external.Save(); "
        f"if (-not (Test-ProcessPathWithinInstallRoot -ProcessPath '{escaped_owned_target}' "
        f"-ExpectedInstallRoot '{escaped_root}')) {{ throw 'Owned path was rejected.' }}; "
        f"if (Test-ProcessPathWithinInstallRoot -ProcessPath '{escaped_external_target}' "
        f"-ExpectedInstallRoot '{escaped_root}') {{ throw 'Sibling-prefix process was accepted.' }}; "
        f"Remove-OwnedShortcutSafely -ShortcutPath '{escaped_external_shortcut}' "
        f"-ExpectedTarget '{escaped_owned_target}'; "
        f"Remove-OwnedShortcutSafely -ShortcutPath '{escaped_owned_shortcut}' "
        f"-ExpectedTarget '{escaped_owned_target}'"
    )
    result = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert external_shortcut.exists()
    assert not owned_shortcut.exists()
    assert external_target.read_text(encoding="utf-8") == "external"


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_uninstaller_delayed_cleanup_is_valid_windows_powershell_syntax():
    uninstall_script = REPOSITORY_ROOT / "scripts" / "uninstall_windows_desktop.ps1"
    escaped_script = str(uninstall_script).replace("'", "''")
    command = (
        "$tokens = $null; $errors = $null; "
        f"$ast = [Management.Automation.Language.Parser]::ParseFile('{escaped_script}', "
        "[ref]$tokens, [ref]$errors); "
        "$assignment = $ast.Find({ param($node) "
        "$node -is [Management.Automation.Language.AssignmentStatementAst] -and "
        "$node.Left.Extent.Text -eq '$cleanup' }, $true); "
        "if (-not $assignment) { throw 'Cleanup assignment missing.' }; "
        "$escapedInstallRoot='C:\\Users\\fixture\\Programs\\VideoInsight'; "
        "$escapedExpectedInstallRoot=$escapedInstallRoot; "
        "$escapedProgramsRoot='C:\\Users\\fixture\\Programs'; "
        "$escapedUninstallKey='HKCU:\\Software\\Fixture'; "
        "$escapedCleanupFailureLog='C:\\Users\\fixture\\cleanup.log'; "
        "Invoke-Expression ([string]$assignment.Extent.Text); "
        "$innerTokens=$null; $innerErrors=$null; "
        "[Management.Automation.Language.Parser]::ParseInput($cleanup, [ref]$innerTokens, "
        "[ref]$innerErrors) | Out-Null; "
        "if ($innerErrors.Count -gt 0) { throw ($innerErrors[0].Message) }"
    )
    result = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_final_release_uses_one_versioned_customer_delivery_installer():
    final_script = (
        REPOSITORY_ROOT / "scripts" / "build_final_windows_release.ps1"
    ).read_text(encoding="utf-8")
    offline_script = (
        REPOSITORY_ROOT / "scripts" / "build_offline_windows_installer.ps1"
    ).read_text(encoding="utf-8")
    publish_script = (
        REPOSITORY_ROOT / "scripts" / "publish_windows_update.ps1"
    ).read_text(encoding="utf-8")

    assert "build\\final-windows-release-$Version" in final_script
    assert "-OutputDirectory $deliveryDirectory" in final_script
    assert "$deliveryExecutables.Count -ne 1" in final_script
    assert "$customerInstaller.Length -ne $serviceInstaller.Length" in final_script
    assert "$customerHash -ne $serviceHash" in final_script
    assert "客户唯一发送路径" in final_script
    for protected_output in (
        "build\\pyinstaller",
        "project\\frontend\\dist",
        "project\\frontend\\desktop\\backend",
        "project\\frontend\\desktop\\release-config.json",
        "project\\frontend\\desktop\\icon.ico",
        "project\\frontend\\desktop\\icon.png",
        "project\\frontend\\release",
    ):
        assert protected_output in final_script
    output_reparse_gate = final_script.index("Assert-NoReparsePointsForReleasePath `")

    assert '[string]$OutputDirectory = ""' in offline_script
    assert "final-windows-release-$Version" in offline_script
    assert "$resolvedOutputDirectory -eq $resolvedBuildRoot" in offline_script
    assert "输出目录必须为空" in offline_script
    assert "$deliveryExecutables.Count -ne 1" in offline_script
    assert "AssemblyFileVersion" in offline_script
    assert "AssemblyInformationalVersion" in offline_script
    reparse_scan = offline_script.index(
        "Assert-NoReparsePointsInTree -RootPath $unpacked"
    )
    payload_creation = offline_script.index("CreateFromDirectory")
    assert reparse_scan < payload_creation
    assert "build 到交付目录之间不能包含链接或联接点" in offline_script
    assert "Assert-OfflineBuildLifecycle" in offline_script
    assert "FileMode]::CreateNew" in offline_script
    assert "[System.IO.File]::Move($partialOutput, $output)" in offline_script
    assert "Remove-Item -LiteralPath $workspace -Recurse" not in offline_script
    offline_lock = offline_script.index(
        "$offlineLifecycleLease = Assert-OfflineBuildLifecycle"
    )
    one_time_compile_gate = offline_script.index(
        "$offlineStartedStream.Flush($true)",
    )
    embedded_version_gate = offline_script.index(
        "$partialFileVersion -ne $Version", offline_lock
    )
    output_commit = offline_script.index(
        "[System.IO.File]::Move($partialOutput, $output)", embedded_version_gate
    )
    built_commit = offline_script.index(
        "Complete-OfflineArtifactRegistry `", output_commit
    )
    lock_release = offline_script.index(
        "$offlineLifecycleLease.Stream.Dispose()", built_commit
    )
    failed_burn = offline_script.index("Set-OfflineArtifactFailed `", built_commit)
    assert one_time_compile_gate < offline_lock < embedded_version_gate
    assert (
        embedded_version_gate
        < output_commit
        < built_commit
        < failed_burn
        < lock_release
    )

    assert "release_versions.json" in publish_script
    assert "$null -ne $releaseRegistry.current_candidate" in publish_script
    assert "[version]$Version -ne $highestUsedVersion" in publish_script
    assert "FileVersionInfo" in publish_script
    assert "$embeddedFileVersion -ne $Version" in publish_script
    registry_lock = publish_script.index(
        "$registryLock = New-Object System.IO.FileStream("
    )
    registry_read = publish_script.index(
        "$releaseRegistry = Get-Content", registry_lock
    )
    publishing_state = publish_script.index('$inProgress.windows_state = "publishing"')
    update_directory_write = publish_script.index(
        "[System.IO.Directory]::CreateDirectory($updatesRoot)",
        publishing_state,
    )
    installer_partial = publish_script.index(
        "Copy-InstallerToPartialCreateNew",
        update_directory_write,
    )
    latest_commit = publish_script.index(
        "Commit-LatestManifestAtomically", installer_partial
    )
    completed_ledger = publish_script.index(
        "$releaseRegistry.completed_releases =", latest_commit
    )
    assert registry_lock < registry_read < publishing_state
    assert publishing_state < update_directory_write < installer_partial
    assert installer_partial < latest_commit < completed_ledger
    assert "[System.IO.File]::Replace" in publish_script
    assert "FileMode]::CreateNew" in publish_script
    assert "$outputStream.Flush($true)" in publish_script
    assert (
        "[System.IO.File]::Move($temporaryInstaller, $publishedInstaller)"
        in publish_script
    )
    assert ".latest.{0}.tmp" in publish_script
    assert "更新清单路径已被目录占用" in publish_script
    registry_reparse_gate = publish_script.index(
        "Assert-NoReparsePointsForPublishPath `"
    )
    publish_path_reparse_gate = publish_script.index("foreach ($publishPathCheck in @(")
    assert registry_reparse_gate < registry_read
    assert publish_path_reparse_gate < publishing_state
    for protected_publish_path in (
        "$releaseRegistryPath",
        "$updatesRoot",
        "$publishedInstaller",
        "$temporaryInstaller",
        "$manifestPath",
        "$temporaryManifest",
    ):
        assert protected_publish_path in publish_script

    registry_gate = final_script.index(
        "$preflightRegistry = Read-ReleaseRegistry -LiteralPath $releaseRegistryPath"
    )
    manifest_gate = final_script.index("$existingManifest =")
    clean_gate = final_script.index("Assert-TrackedCleanReleaseInputs")
    online_verification = final_script.index('Write-Output "[1/7]')
    windows_build = final_script.index('Write-Output "[4/7]')
    assert (
        output_reparse_gate
        < clean_gate
        < registry_gate
        < online_verification
        < windows_build
    )
    assert manifest_gate < online_verification < windows_build


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_offline_installer_rejects_output_outside_build_before_packaging(
    tmp_path: Path,
):
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "scripts" / "build_offline_windows_installer.ps1"),
            "-Version",
            "0.2.7",
            "-OutputDirectory",
            str(tmp_path / "outside-build"),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "outside-build" in output
    assert "win-unpacked" not in output


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point behavior")
def test_offline_installer_rejects_nested_and_build_root_junctions(tmp_path: Path):
    source_script = REPOSITORY_ROOT / "scripts" / "build_offline_windows_installer.ps1"

    def create_junction(path: Path, target: Path) -> None:
        escaped_path = str(path).replace("'", "''")
        escaped_target = str(target).replace("'", "''")
        result = subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-Command",
                f"New-Item -ItemType Junction -Path '{escaped_path}' -Target '{escaped_target}' | Out-Null",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(f"Junction creation is unavailable: {result.stderr}")

    unpacked = tmp_path / "unpacked"
    external = tmp_path / "external"
    unpacked.mkdir()
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    create_junction(unpacked / "linked", external)

    escaped_script = str(source_script).replace("'", "''")
    escaped_unpacked = str(unpacked).replace("'", "''")
    tree_command = (
        "$tokens = $null; $errors = $null; "
        f"$ast = [Management.Automation.Language.Parser]::ParseFile('{escaped_script}', "
        "[ref]$tokens, [ref]$errors); "
        "$definition = $ast.Find({ param($node) "
        "$node -is [Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -eq 'Assert-NoReparsePointsInTree' }, $true); "
        "Invoke-Expression ([string]$definition.Extent.Text); "
        f"Assert-NoReparsePointsInTree -RootPath '{escaped_unpacked}' -Label 'test'"
    )
    nested = subprocess.run(
        [_powershell(), "-NoProfile", "-Command", tree_command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert nested.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "keep"

    fixture_root = tmp_path / "fixture"
    fixture_scripts = fixture_root / "scripts"
    fixture_scripts.mkdir(parents=True)
    fixture_script = fixture_scripts / source_script.name
    shutil.copy2(source_script, fixture_script)
    build_target = tmp_path / "external-build"
    build_target.mkdir()
    build_sentinel = build_target / "sentinel.txt"
    build_sentinel.write_text("keep", encoding="utf-8")
    create_junction(fixture_root / "build", build_target)
    rejected = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(fixture_script),
            "-Version",
            "0.2.7",
        ],
        cwd=fixture_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert rejected.returncode != 0
    assert build_sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_offline_installer_recovers_exact_committed_artifact_before_rebuild(
    tmp_path: Path,
):
    source_executable, version = _find_windows_stable_versioned_executable()
    repository = tmp_path / "fixture"
    scripts = repository / "scripts"
    output = repository / "build" / f"final-windows-release-{version}"
    registry_path = repository / "deploy" / "control-plane" / "release_versions.json"
    scripts.mkdir(parents=True)
    output.mkdir(parents=True)
    registry_path.parent.mkdir(parents=True)
    trusted_files = {
        "verify_windows_install.ps1": REPOSITORY_ROOT
        / "scripts"
        / "verify_windows_install.ps1",
        "CLEAN_PC_ACCEPTANCE.md": REPOSITORY_ROOT / "CLEAN_PC_ACCEPTANCE.md",
        "PRODUCTION_RELEASE_CHECKLIST.md": REPOSITORY_ROOT
        / "PRODUCTION_RELEASE_CHECKLIST.md",
    }
    for name, source in trusted_files.items():
        shutil.copy2(
            source, scripts / name if name.endswith(".ps1") else repository / name
        )
        shutil.copy2(source, output / name)
    installer_name = f"VideoInsight-{version}-Setup.exe"
    installer = output / installer_name
    shutil.copy2(source_executable, installer)

    def write_registry() -> None:
        registry_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "used_versions": [version],
                    "current_candidate": None,
                    "completed_releases": [],
                    "release_in_progress": {
                        "version": version,
                        "source_commit": "a" * 40,
                        "server_state": "ready",
                        "control_plane_sha256": "b" * 64,
                        "windows_state": "attempted",
                        "installer_sha256": None,
                    },
                }
            ),
            encoding="utf-8",
        )

    def write_checksums() -> None:
        checksum_names = [installer_name, *trusted_files]
        lines = [
            f"{hashlib.sha256((output / name).read_bytes()).hexdigest()}  {name}"
            for name in checksum_names
        ]
        (output / "SHA256SUMS.txt").write_text(
            "\n".join(lines) + "\n", encoding="ascii"
        )

    write_registry()
    write_checksums()
    offline_script = REPOSITORY_ROOT / "scripts" / "build_offline_windows_installer.ps1"
    loader = _powershell_function_loader(
        offline_script,
        (
            "Get-Sha256Hex",
            "Get-EmbeddedStableVersionForOfflineBuild",
            "Get-ExistingPathAttributesForOfflineBuild",
            "Assert-NoReparsePointsForOfflinePath",
            "Write-OfflineReleaseRegistryAtomically",
            "Complete-OfflineArtifactRegistry",
            "Try-RecoverCommittedOfflineArtifact",
        ),
    )
    escaped_repository = str(repository).replace("'", "''")
    escaped_output = str(output).replace("'", "''")
    escaped_installer = str(installer).replace("'", "''")
    escaped_registry = str(registry_path).replace("'", "''")
    delivery_paths = [
        installer,
        *(output / name for name in trusted_files),
        output / "SHA256SUMS.txt",
    ]
    delivery_literal = ",".join(
        f"'{str(path).replace(chr(39), chr(39) * 2)}'" for path in delivery_paths
    )

    def recover() -> subprocess.CompletedProcess[str]:
        command = loader + (
            f"$repoRoot = '{escaped_repository}'; $deliveryPaths = @({delivery_literal}); "
            f"$recovered = Try-RecoverCommittedOfflineArtifact -OutputDirectory '{escaped_output}' "
            f"-DeliveryPaths $deliveryPaths -InstallerPath '{escaped_installer}' "
            f"-ExpectedInstallerName '{installer_name}' -ExpectedVersion '{version}' "
            f"-RegistryPath '{escaped_registry}'; Write-Output \"RECOVERED:$recovered\""
        )
        return subprocess.run(
            [_windows_powershell(), "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    recovered = recover()
    assert recovered.returncode == 0, recovered.stderr
    assert "RECOVERED:True" in recovered.stdout
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["release_in_progress"]["windows_state"] == "built"
    assert (
        registry["release_in_progress"]["installer_sha256"]
        == hashlib.sha256(installer.read_bytes()).hexdigest()
    )

    write_registry()
    (output / "verify_windows_install.ps1").write_text("tampered", encoding="utf-8")
    write_checksums()
    tampered = recover()
    assert tampered.returncode != 0
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["release_in_progress"]["windows_state"] == "attempted"
    assert registry["release_in_progress"]["installer_sha256"] is None


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_offline_started_marker_consumes_compile_right_before_any_artifact(
    tmp_path: Path,
):
    repository = tmp_path / "fixture"
    registry_path = repository / "deploy" / "control-plane" / "release_versions.json"
    registry_path.parent.mkdir(parents=True)
    (repository / ".gitignore").write_text("build/\n", encoding="utf-8")
    version = "0.2.7"
    registry = {
        "schema_version": 2,
        "used_versions": [version],
        "current_candidate": None,
        "completed_releases": [],
        "release_in_progress": {
            "version": version,
            "source_commit": "0" * 40,
            "server_state": "ready",
            "control_plane_sha256": "b" * 64,
            "windows_state": "attempted",
            "installer_sha256": None,
        },
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    git = shutil.which("git")
    assert git
    subprocess.run([git, "init", "-q"], cwd=repository, check=True, capture_output=True)
    _commit_release_fixture(repository)
    source_commit = subprocess.run(
        [git, "rev-parse", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    registry["release_in_progress"]["source_commit"] = source_commit
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    subprocess.run(
        [git, "add", "--", "deploy/control-plane/release_versions.json"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [git, "commit", "-q", "-m", "attempt windows artifact"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    build = repository / "build"
    build.mkdir()
    attempt_marker = build / f".windows-release-{version}.attempted"
    started_marker = build / f".windows-release-{version}.offline-started"
    attempt_marker.write_text(
        f"version={version}\nsource_commit={source_commit}\n", encoding="utf-8"
    )
    offline_script = REPOSITORY_ROOT / "scripts" / "build_offline_windows_installer.ps1"
    loader = _powershell_function_loader(
        offline_script,
        (
            "Get-ExistingPathAttributesForOfflineBuild",
            "Assert-NoReparsePointsForOfflinePath",
            "Assert-OfflineBuildLifecycle",
            "Write-OfflineReleaseRegistryAtomically",
            "Set-OfflineArtifactFailed",
        ),
    )
    escaped_repository = str(repository).replace("'", "''")
    escaped_registry = str(registry_path).replace("'", "''")
    escaped_attempt = str(attempt_marker).replace("'", "''")
    escaped_started = str(started_marker).replace("'", "''")

    def lifecycle(*, fail_after_gate: bool = False) -> subprocess.CompletedProcess[str]:
        failure_statement = (
            f"Set-OfflineArtifactFailed -RegistryPath '{escaped_registry}' -ExpectedVersion '{version}' | Out-Null; "
            if fail_after_gate
            else ""
        )
        command = loader + (
            f"$repoRoot = '{escaped_repository}'; "
            f"$lease = Assert-OfflineBuildLifecycle -RepositoryRoot '{escaped_repository}' "
            f"-ExpectedVersion '{version}' -RegistryPath '{escaped_registry}' "
            f"-AttemptMarkerPath '{escaped_attempt}' "
            f"-OfflineStartedMarkerPath '{escaped_started}'; "
            'try { Write-Output "RECOVERY_ONLY:$($lease.RecoveryOnly)"; '
            + failure_statement
            + "} finally { $lease.Stream.Dispose(); "
            "Remove-Item -LiteralPath $lease.Path -Force }"
        )
        return subprocess.run(
            [_windows_powershell(), "-NoProfile", "-Command", command],
            cwd=repository,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    first = lifecycle()
    assert first.returncode == 0, first.stderr
    assert "RECOVERY_ONLY:False" in first.stdout
    assert started_marker.read_text(encoding="utf-8-sig").replace("\r\n", "\n") == (
        f"version={version}\nsource_commit={source_commit}\n"
    )
    second = lifecycle(fail_after_gate=True)
    assert second.returncode == 0, second.stderr
    assert "RECOVERY_ONLY:True" in second.stdout
    burned = json.loads(registry_path.read_text(encoding="utf-8"))
    assert burned["release_in_progress"]["windows_state"] == "failed"
    subprocess.run(
        [git, "add", "--", "deploy/control-plane/release_versions.json"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [git, "commit", "-q", "-m", "burn failed windows artifact"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    third = lifecycle()
    assert third.returncode != 0
    assert "RECOVERY_ONLY:" not in third.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_offline_installer_ignores_poisoned_systemroot_for_csharp_compiler(
    tmp_path: Path,
):
    fake_system_root = tmp_path / "poisoned-windows"
    fake_compiler = (
        fake_system_root / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    )
    fake_compiler.parent.mkdir(parents=True)
    fake_compiler.write_text("sentinel", encoding="utf-8")
    offline_script = REPOSITORY_ROOT / "scripts" / "build_offline_windows_installer.ps1"
    loader = _powershell_function_loader(
        offline_script,
        (
            "Get-ExistingPathAttributesForOfflineBuild",
            "Assert-NoReparsePointsInAbsoluteOfflinePath",
            "Get-TrustedCSharpCompilerPath",
        ),
    )
    escaped_fake_root = str(fake_system_root).replace("'", "''")
    command = loader + (
        f"$env:SystemRoot = '{escaped_fake_root}'; "
        "$compiler = Get-TrustedCSharpCompilerPath; "
        f"if ($compiler.StartsWith('{escaped_fake_root}', [StringComparison]::OrdinalIgnoreCase)) {{ "
        "throw 'Poisoned SystemRoot compiler was selected.' }; Write-Output $compiler"
    )
    powershells = [_windows_powershell()]
    modern_powershell = shutil.which("pwsh.exe")
    if modern_powershell:
        powershells.append(modern_powershell)
    for powershell in dict.fromkeys(powershells):
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert str(fake_compiler).lower() not in result.stdout.lower()
        assert "Microsoft.NET" in result.stdout
    assert fake_compiler.read_text(encoding="utf-8") == "sentinel"


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
@pytest.mark.parametrize(
    "secret_key",
    [
        "APP_SECRET_KEY",
        "API_KEY",
        "ADMIN_PASSWORD",
        "POSTGRES_PASSWORD",
        "VIDEOINSIGHT_WORKER_TOKEN",
    ],
)
def test_authoritative_windows_payload_scan_rejects_exact_env_secret_values(
    tmp_path: Path,
    secret_key: str,
):
    final_script = REPOSITORY_ROOT / "scripts" / "build_final_windows_release.ps1"
    script_text = final_script.read_text(encoding="utf-8")
    for required_key in (
        "APP_SECRET_KEY",
        "API_KEY",
        "ADMIN_PASSWORD",
        "POSTGRES_PASSWORD",
        "VIDEOINSIGHT_WORKER_TOKEN",
    ):
        assert f'"{required_key}"' in script_text
    assert 'Join-Path $repositoryRoot "deploy\\control-plane\\.env"' in script_text
    repository = tmp_path / "fixture"
    package = repository / "project" / "frontend" / "release" / "win-unpacked"
    backend = package / "resources" / "backend"
    desktop_config = package / "resources" / "config" / "release.json"
    backend_config = backend / "_internal" / "config" / "desktop-control-plane.json"
    desktop_config.parent.mkdir(parents=True)
    backend_config.parent.mkdir(parents=True)
    media = backend / "_internal" / "media"
    media.mkdir(parents=True)
    (package / "VideoInsight.exe").write_bytes(b"desktop")
    (backend / "VideoInsightBackend.exe").write_bytes(b"backend")
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
    origin = "https://release.fixture.invalid"
    version = "0.2.7"
    desktop_config.write_text(
        json.dumps({"current_version": version, "control_plane_url": origin}),
        encoding="utf-8",
    )
    backend_config.write_text(
        json.dumps({"enabled": True, "control_plane_url": origin}),
        encoding="utf-8",
    )
    secret_value = f"exact-{secret_key.lower()}-sentinel"
    (package / "resources" / "app.bin").write_bytes(
        b"prefix\x00" + secret_value.encode() + b"\x00suffix"
    )
    secret_file = repository / ".env"
    secret_file.write_text(f"{secret_key}={secret_value}\n", encoding="utf-8")
    loader = _powershell_function_loader(
        final_script,
        (
            "Get-Sha256Hex",
            "Get-ExistingPathAttributesForRelease",
            "Assert-NoReparsePointsForReleasePath",
            "Test-ByteSequenceInArray",
            "Test-FileContainsBytePattern",
            "Assert-WindowsReleasePayloadAuthoritative",
        ),
    )
    escaped_repository = str(repository).replace("'", "''")
    escaped_package = str(package).replace("'", "''")
    escaped_secret_file = str(secret_file).replace("'", "''")
    command = loader + (
        f"$repositoryRoot = '{escaped_repository}'; "
        f"Assert-WindowsReleasePayloadAuthoritative -PackageRoot '{escaped_package}' "
        f"-Origin '{origin}' -ExpectedVersion '{version}' "
        f"-SecretEnvironmentFiles @('{escaped_secret_file}')"
    )
    result = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert secret_value not in (result.stdout + result.stderr)
    for public_placeholder in (
        "your-secret-key-change-this",
        "change_me_at_least_16_characters",
        "postgres",
    ):
        (package / "resources" / "app.bin").write_text(
            public_placeholder, encoding="utf-8"
        )
        secret_file.write_text(f"{secret_key}={public_placeholder}\n", encoding="utf-8")
        allowed = subprocess.run(
            [_windows_powershell(), "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        assert allowed.returncode == 0, allowed.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_authoritative_windows_payload_scan_allows_only_certifi_public_ca_pem(
    tmp_path: Path,
):
    final_script = REPOSITORY_ROOT / "scripts" / "build_final_windows_release.ps1"
    repository = tmp_path / "fixture"
    package = repository / "project" / "frontend" / "release" / "win-unpacked"
    backend = package / "resources" / "backend"
    desktop_config = package / "resources" / "config" / "release.json"
    backend_config = backend / "_internal" / "config" / "desktop-control-plane.json"
    trusted_ca = backend / "_internal" / "certifi" / "cacert.pem"
    trusted_builtin = backend / "_internal" / "data" / "templates" / "builtin.json"
    desktop_config.parent.mkdir(parents=True)
    backend_config.parent.mkdir(parents=True)
    trusted_ca.parent.mkdir(parents=True)
    trusted_builtin.parent.mkdir(parents=True)
    media = backend / "_internal" / "media"
    media.mkdir(parents=True)
    (package / "VideoInsight.exe").write_bytes(b"desktop")
    (backend / "VideoInsightBackend.exe").write_bytes(b"backend")
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
    origin = "https://release.fixture.invalid"
    version = "0.2.7"
    desktop_config.write_text(
        json.dumps({"current_version": version, "control_plane_url": origin}),
        encoding="utf-8",
    )
    backend_config.write_text(
        json.dumps({"enabled": True, "control_plane_url": origin}),
        encoding="utf-8",
    )
    trusted_ca.write_text(
        "-----BEGIN CERTIFICATE-----\npublic-root-ca\n-----END CERTIFICATE-----\n",
        encoding="ascii",
    )
    trusted_builtin.write_text('{"templates": []}', encoding="utf-8")
    loader = _powershell_function_loader(
        final_script,
        (
            "Get-Sha256Hex",
            "Get-ExistingPathAttributesForRelease",
            "Assert-NoReparsePointsForReleasePath",
            "Test-ByteSequenceInArray",
            "Test-FileContainsBytePattern",
            "Assert-WindowsReleasePayloadAuthoritative",
        ),
    )
    escaped_repository = str(repository).replace("'", "''")
    escaped_package = str(package).replace("'", "''")
    command = loader + (
        f"$repositoryRoot = '{escaped_repository}'; "
        f"Assert-WindowsReleasePayloadAuthoritative -PackageRoot '{escaped_package}' "
        f"-Origin '{origin}' -ExpectedVersion '{version}' -SecretEnvironmentFiles @()"
    )
    allowed = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert allowed.returncode == 0, allowed.stderr

    untrusted_runtime_data = trusted_builtin.parent / "customer.json"
    untrusted_runtime_data.write_text("{}", encoding="utf-8")
    rejected_runtime_data = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert rejected_runtime_data.returncode != 0
    untrusted_runtime_data.unlink()

    untrusted_pem = package / "resources" / "other.pem"
    untrusted_pem.write_text(trusted_ca.read_text(encoding="ascii"), encoding="ascii")
    rejected_path = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert rejected_path.returncode != 0

    untrusted_pem.unlink()
    trusted_ca.write_text(
        "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
        encoding="ascii",
    )
    rejected_marker = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert rejected_marker.returncode != 0


def test_final_release_requires_authenticated_production_configuration():
    final_script = (
        REPOSITORY_ROOT / "scripts" / "build_final_windows_release.ps1"
    ).read_text(encoding="utf-8")

    assert "function Invoke-ControlPlaneAuthoritativeGate" in final_script
    assert "$handler.AllowAutoRedirect = $false" in final_script
    assert "$handler.UseCookies = $false" in final_script
    assert "$handler.UseProxy = $false" in final_script
    for endpoint in (
        '"$Origin/health"',
        '"$Origin/ready"',
        '"$Origin/api/v1/auth/customer-login"',
        '"$Origin/api/v1/auth/admin-login"',
        '"$Origin/api/v1/admin/server-status"',
    ):
        assert endpoint in final_script
    assert (
        "[string]$health.Payload.release_version -ne $ExpectedVersion" in final_script
    )
    verification = final_script.index("Invoke-ControlPlaneAuthoritativeGate `")
    paid_gate = final_script.index(
        'Write-Output "[2/7] 验证最近 24 小时内的最低成本真实付费闭环"',
        verification,
    )
    release_gate = final_script.index('Write-Output "[3/7]', paid_gate)
    assert verification < paid_gate < release_gate
    for variable in (
        "VIDEOINSIGHT_VERIFY_ACTIVATION_CODE",
        "VIDEOINSIGHT_VERIFY_ADMIN_USERNAME",
        "VIDEOINSIGHT_VERIFY_ADMIN_PASSWORD",
    ):
        assert variable in final_script
    verification_variable_block = final_script[
        final_script.index("$verificationVariables = @(") : final_script.index(
            "$paidAcceptanceVariable ="
        )
    ]
    assert "VIDEOINSIGHT_ACCEPTANCE_ACTIVATION_CODE" not in verification_variable_block
    release_secret_definition = final_script.index("$releaseSecretVariables =")
    outer_release_try = final_script.index("try {", release_secret_definition)
    credential_input_gate = final_script.index("$credentialBearingInputs = @(")
    clean_worktree_gate = final_script.index(
        "Assert-TrackedCleanReleaseInputs -RelativePaths $credentialBearingInputs",
        credential_input_gate,
    )
    early_registry_gate = final_script.index(
        "$preflightRegistry = Read-ReleaseRegistry"
    )
    paid_activation_requirement = final_script.index(
        'GetEnvironmentVariable($paidAcceptanceVariable, "Process")'
    )
    paid_report_resolution = final_script.index(
        "Resolve-Path -LiteralPath $PaidAcceptanceReport"
    )
    online_verification = final_script.index('Write-Output "[1/7]')
    environment_cleanup = final_script.index(
        'SetEnvironmentVariable($name, $null, "Process")', paid_activation_requirement
    )
    paid_report_verification = final_script.index(
        "Assert-PaidAcceptanceAuthoritativeProof `", paid_gate
    )
    paid_local_cleanup = final_script.index(
        "$paidAcceptanceValue = $null", paid_report_verification
    )
    final_secret_cleanup = final_script.rindex(
        'SetEnvironmentVariable($name, $null, "Process")'
    )
    assert release_secret_definition < outer_release_try < credential_input_gate
    assert (
        credential_input_gate
        < clean_worktree_gate
        < early_registry_gate
        < paid_activation_requirement
        < environment_cleanup
        < paid_report_resolution
        < online_verification
    )
    assert (
        online_verification < paid_gate < paid_report_verification < paid_local_cleanup
    )
    assert paid_local_cleanup < release_gate
    assert paid_gate < final_secret_cleanup
    assert "脚本不会读取、打印或写入该值" in final_script
    assert "& $releasePython" not in final_script
    assert "--secret-env-file" not in final_script
    for tracked_input in (
        "deploy/control-plane/release_versions.json",
        "deploy/control-plane/verify_control_plane.py",
        "scripts/run_paid_release_acceptance.py",
        "scripts/build_final_windows_release.ps1",
    ):
        assert tracked_input in final_script

    control_plane_script = (
        REPOSITORY_ROOT / "scripts" / "build_control_plane_bundle.ps1"
    ).read_text(encoding="utf-8")
    assert "$releaseVersionEntry = 'release_version.txt'" in control_plane_script
    assert "$versionWriter.Write($Version)" in control_plane_script


def test_final_release_requires_current_paid_acceptance_report():
    final_script = (
        REPOSITORY_ROOT / "scripts" / "build_final_windows_release.ps1"
    ).read_text(encoding="utf-8")

    assert "[Parameter(Mandatory = $true)][string]$PaidAcceptanceReport" in final_script
    assert '[string]$ControlPlaneVersion = ""' in final_script
    assert "-ExpectedVersion $ControlPlaneVersion" in final_script
    assert final_script.count("-ExpectedVersion $ControlPlaneVersion") == 2
    assert "Resolve-Path -LiteralPath $PaidAcceptanceReport" in final_script
    assert "function Assert-PaidAcceptanceAuthoritativeProof" in final_script
    assert '"$Origin/api/v1/provider/release-acceptance/proof/$runId' in final_script
    for binding in (
        "release_version",
        "run_id",
        "evidence_manifest_sha256",
        "result_sha256",
        "duration_seconds",
        "billed_seconds",
        "transaction_ids",
        "digest_sha256",
        "expires_at",
    ):
        assert binding in final_script
    assert (
        '$expectedCapabilityNames = "asr,avatar,copywriting,video_editor"'
        in final_script
    )
    assert "function ConvertTo-CanonicalReleaseProofJson" in final_script
    assert "ConvertTo-CanonicalReleaseProofJson $report.server_proof" in final_script
    assert "ConvertTo-CanonicalReleaseProofJson $online" in final_script
    assert "$report.server_proof | ConvertTo-Json" not in final_script
    assert "& $releasePython" not in final_script


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_paid_proof_comparison_is_property_order_independent() -> None:
    script_path = REPOSITORY_ROOT / "scripts" / "build_final_windows_release.ps1"
    loader = _powershell_function_loader(
        script_path,
        (
            "ConvertTo-CanonicalReleaseProofObject",
            "ConvertTo-CanonicalReleaseProofJson",
        ),
    )
    command = (
        loader
        + r"""
$stored = [pscustomobject][ordered]@{
    avatar_id = 'shuying-avatar-21920'
    capabilities = [pscustomobject][ordered]@{
        avatar = [pscustomobject][ordered]@{ result_sha256 = ('a' * 64); status = 'passed' }
        asr = [pscustomobject][ordered]@{ result_sha256 = ('b' * 64); status = 'passed' }
    }
    ledger = [pscustomobject][ordered]@{ transaction_ids = @(7, 9); digest_sha256 = ('c' * 64) }
    schema_version = 1
}
$online = [pscustomobject][ordered]@{
    schema_version = 1
    ledger = [pscustomobject][ordered]@{ digest_sha256 = ('c' * 64); transaction_ids = @(7, 9) }
    capabilities = [pscustomobject][ordered]@{
        asr = [pscustomobject][ordered]@{ status = 'passed'; result_sha256 = ('b' * 64) }
        avatar = [pscustomobject][ordered]@{ status = 'passed'; result_sha256 = ('a' * 64) }
    }
    avatar_id = 'shuying-avatar-21920'
}
$storedJson = ConvertTo-CanonicalReleaseProofJson $stored
$onlineJson = ConvertTo-CanonicalReleaseProofJson $online
if ($storedJson -cne $onlineJson) { throw 'canonical proof mismatch' }
Write-Output $storedJson
"""
    )
    result = subprocess.run(
        [_windows_powershell(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    parsed = json.loads(result.stdout.strip())
    assert list(parsed) == ["avatar_id", "capabilities", "ledger", "schema_version"]


@pytest.mark.skipif(os.name != "nt", reason="Windows release script")
def test_installer_allows_developer_tools_but_manual_clean_pc_check_stays_strict(
    tmp_path: Path,
):
    install_script = (
        REPOSITORY_ROOT / "scripts" / "install_windows_desktop.ps1"
    ).read_text(encoding="utf-8")
    verifier = REPOSITORY_ROOT / "scripts" / "verify_windows_install.ps1"
    verifier_script = verifier.read_text(encoding="utf-8")

    assert "-RequireNoDeveloperTools" not in install_script
    assert "[switch]$RequireNoDeveloperTools" in verifier_script

    developer_tools = tmp_path / "developer-tools"
    developer_tools.mkdir()
    (developer_tools / "python.exe").write_bytes(b"")
    (developer_tools / "node.exe").write_bytes(b"")
    environment = os.environ.copy()
    environment["PATH"] = (
        str(developer_tools) + os.pathsep + environment.get("PATH", "")
    )

    def run_verifier(*extra_arguments: str) -> str:
        report = (
            tmp_path / f"acceptance-{len(list(tmp_path.glob('acceptance-*.txt')))}.txt"
        )
        subprocess.run(
            [
                _powershell(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(verifier),
                "-ReportPath",
                str(report),
                *extra_arguments,
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        assert report.exists()
        return report.read_text(encoding="utf-8-sig")

    automatic_report = run_verifier()
    assert "[PASS] Developer tools inventory (not enforced)" in automatic_report

    strict_report = run_verifier("-RequireNoDeveloperTools")
    assert "[FAIL] Python and Node unavailable to verification runtime" in strict_report


def test_windows_backend_packages_builtin_video_templates():
    script = (REPOSITORY_ROOT / "scripts" / "build_windows_installer.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "'data/templates');data/templates" in script
    assert "config\\windows-media-tools.sha256" in script
    assert "--add-binary \"$($mediaSources['ffmpeg.exe']);media\"" in script
    assert "--add-binary \"$($mediaSources['ffprobe.exe']);media\"" in script
    assert "Get-FileHash -LiteralPath $entry.Value -Algorithm SHA256" in script
    final_script = (
        REPOSITORY_ROOT / "scripts" / "build_final_windows_release.ps1"
    ).read_text(encoding="utf-8")
    for required_media_file in (
        "media\\ffmpeg.exe",
        "media\\ffprobe.exe",
        "media\\LICENSE",
        "media\\README.txt",
        "media\\windows-media-tools.sha256",
    ):
        assert required_media_file in final_script
