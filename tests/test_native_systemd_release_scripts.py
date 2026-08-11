from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path, PurePosixPath

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NATIVE_ROOT = REPOSITORY_ROOT / "deploy" / "control-plane" / "native-systemd"
MEDIA_TOOLS_MANIFEST = NATIVE_ROOT / "media-tools.sha256"
SHELL_SCRIPTS = (
    "common.sh",
    "normalize_offline_python_runtime.sh",
    "normalize_legacy_unit.sh",
    "preflight.sh",
    "install_unit.sh",
    "upgrade.sh",
    "verify.sh",
    "rollback.sh",
)


def _archive_validator():
    path = NATIVE_ROOT / "validate_release_archive.py"
    spec = spec_from_file_location("native_release_archive_validator", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _legacy_descriptor():
    path = NATIVE_ROOT / "legacy_rollback_descriptor.py"
    spec = spec_from_file_location("native_legacy_rollback_descriptor", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _legacy_adoption_descriptor():
    path = NATIVE_ROOT / "legacy_adoption_descriptor.py"
    spec = spec_from_file_location("native_legacy_adoption_descriptor", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _read(name: str) -> str:
    return (NATIVE_ROOT / name).read_text(encoding="utf-8")


def _bash() -> str:
    candidates = (
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    executable = shutil.which("bash")
    if not executable:
        pytest.skip("Bash is required for native systemd script syntax checks")
    return executable


def _executable_fixture() -> Path:
    candidates = (
        Path(r"C:\Program Files\Git\usr\bin\true.exe"),
        Path(_bash()),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise AssertionError("No executable fixture is available")


def _posix_path(path: Path) -> str:
    result = subprocess.run(
        [_bash(), "-c", 'cygpath -u "$1"', "native-test", str(path)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def _run_common_function(
    function_call: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return _run_common_file_function(
        NATIVE_ROOT / "common.sh", function_call, *arguments
    )


def _run_common_file_function(
    common_path: Path, function_call: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _bash(),
            "-c",
            (
                "PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH; readonly PATH; "
                f'source "$1"; {function_call}'
            ),
            "native-test",
            str(common_path),
            *arguments,
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def test_native_systemd_scripts_parse_as_bash() -> None:
    for name in SHELL_SCRIPTS:
        result = subprocess.run(
            [_bash(), "-n", str(NATIVE_ROOT / name)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, f"{name}: {result.stdout}{result.stderr}"


def test_native_shells_pin_interpreter_and_path_before_external_commands(
    tmp_path: Path,
) -> None:
    entry_scripts = tuple(name for name in SHELL_SCRIPTS if name != "common.sh")
    for name in SHELL_SCRIPTS:
        script = _read(name)
        assert script.startswith("#!/bin/bash\nset -Eeuo pipefail\n")
        assert "#!/usr/bin/env bash" not in script
        if name == "common.sh":
            assert '"${PATH:-}" != "$VIDEOINSIGHT_SYSTEM_PATH"' in script
            assert "( PATH=/videoinsight-untrusted-path )" in script
            continue
        path_assignment = script.index("PATH=/usr/sbin:/usr/bin:/sbin:/bin")
        assert path_assignment < script.index("SCRIPT_DIR=")
        assert script.index("export PATH", path_assignment) < script.index(
            "readonly PATH", path_assignment
        )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    marker = tmp_path / "executed.txt"
    for command in ("bash", "dirname", "awk"):
        fake = fake_bin / command
        fake.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$0" >> "$VIDEOINSIGHT_TEST_MARKER"\nexit 99\n',
            encoding="utf-8",
            newline="\n",
        )
        fake.chmod(0o755)
    polluted_environment = os.environ.copy()
    polluted_environment["PATH"] = _posix_path(fake_bin)
    polluted_environment["VIDEOINSIGHT_TEST_MARKER"] = _posix_path(marker)
    for name in entry_scripts:
        result = subprocess.run(
            [_bash(), str(NATIVE_ROOT / name)],
            cwd=REPOSITORY_ROOT,
            env=polluted_environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        assert result.returncode == 2, name + result.stdout + result.stderr

    fixed_gate = subprocess.run(
        [
            _bash(),
            "-c",
            (
                'PATH="$2"; export PATH; '
                "PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH; readonly PATH; "
                'source "$1"; bash -c :; dirname / >/dev/null; '
                "awk 'BEGIN { exit 0 }'"
            ),
            "native-test",
            str(NATIVE_ROOT / "common.sh"),
            _posix_path(fake_bin),
        ],
        cwd=REPOSITORY_ROOT,
        env=polluted_environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert fixed_gate.returncode == 0, fixed_gate.stdout + fixed_gate.stderr
    assert not marker.exists()


def test_linux_release_files_are_pinned_to_lf_in_git() -> None:
    attributes = (REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert "*.sh text eol=lf" in attributes
    assert "*.service text eol=lf" in attributes
    assert "deploy/control-plane/native-systemd/*.sha256 text eol=lf" in attributes
    manifests = (
        "wheelhouse.sha256",
        "media-tools.sha256",
        "offline-python-archive.sha256",
        "offline-python-legacy-drift-tree.sha256",
        "offline-python-tree.sha256",
    )
    for name in manifests:
        assert b"\r" not in (NATIVE_ROOT / name).read_bytes()
    relative_paths = [
        (NATIVE_ROOT / name).relative_to(REPOSITORY_ROOT).as_posix()
        for name in manifests
    ]
    result = subprocess.run(
        ["git", "check-attr", "text", "eol", "--", *relative_paths],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for relative in relative_paths:
        assert f"{relative}: text: set" in result.stdout
        assert f"{relative}: eol: lf" in result.stdout
    assert "*.service text eol=lf" in attributes
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".gitattributes"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0, ".gitattributes 必须被 Git 跟踪后才能发布"


def test_unit_uses_one_version_root_and_cent_os_7_compatible_limits() -> None:
    unit = _read("videoinsight-control-plane.service")

    assert "User=videoinsight" in unit
    assert "Group=videoinsight" in unit
    assert "WorkingDirectory=/opt/videoinsight-control-plane/current/app" in unit
    assert (
        "ExecStart=/opt/videoinsight-control-plane/current/venv/bin/python "
        "-m uvicorn project.backend.app.control_plane:app "
        "--host 127.0.0.1 --port 18080 --workers 1 "
        "--proxy-headers --forwarded-allow-ips 127.0.0.1"
    ) in unit
    assert (
        "EnvironmentFile=/opt/videoinsight-control-plane/config/control-plane.env"
        in unit
    )
    assert "--reload" not in unit
    assert "/releases/0." not in unit
    assert "CPUQuota=100%" in unit
    assert "MemoryLimit=1G" in unit
    assert "MemoryMax=" not in unit
    assert (
        "Environment=PATH=/opt/videoinsight-control-plane/tools/media/bin:"
        "/usr/local/bin:/usr/bin:/bin"
    ) in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=full" in unit
    assert "CapabilityBoundingSet=" in unit


def test_scripts_are_hard_scoped_to_one_root_and_one_service() -> None:
    common = _read("common.sh")
    assert 'readonly VIDEOINSIGHT_ROOT="/opt/videoinsight-control-plane"' in common
    assert (
        'readonly VIDEOINSIGHT_SERVICE="videoinsight-control-plane.service"' in common
    )
    assert (
        'readonly VIDEOINSIGHT_UNIT_PATH="/etc/systemd/system/'
        'videoinsight-control-plane.service"'
    ) in common

    combined = "\n".join(_read(name) for name in SHELL_SCRIPTS).casefold()
    for line in combined.splitlines():
        stripped = line.strip()
        assert not stripped.startswith(("docker ", "caddy ", "nginx "))
        assert "docker compose" not in stripped
    assert "systemctl --all" not in combined
    assert "systemctl list-units" not in combined
    assert "systemctl restart" not in combined

    for name in SHELL_SCRIPTS:
        for line in _read(name).splitlines():
            stripped = line.strip()
            if re.search(r"(?:^|[!&|(;]\s*)systemctl\s", stripped) is None:
                continue
            assert (
                '"$VIDEOINSIGHT_SERVICE"' in stripped
                or "systemctl daemon-reload" in stripped
            ), f"unscoped systemctl call in {name}: {stripped}"


def test_preflight_supports_legacy_current_but_rejects_scope_escape() -> None:
    common = _read("common.sh")
    preflight = _read("preflight.sh")

    assert 'if [[ $(basename -- "$target") == "app" ]]' in common
    assert 'real_path_under "$resolved" "$VIDEOINSIGHT_RELEASES_ROOT"' in common
    assert "validate_existing_unit_scope" in preflight
    assert "validate_active_legacy_adoption" in preflight
    assert "validate_service_identity" in preflight
    assert 'real_path_under "$SCRIPT_DIR" "$VIDEOINSIGHT_ROOT"' in preflight
    assert 'validate_secure_file "$VIDEOINSIGHT_ENV_FILE" 0' in preflight
    assert 'env_gid" == "$SERVICE_GID' in preflight
    assert "8#$env_mode & 0037" in preflight
    assert "配置不得覆盖固定的 systemd 服务 PATH" in preflight
    assert "VIDEOINSIGHT_MEDIA_ROOT|VIDEOINSIGHT_SERVICE_PATH" in preflight
    assert '"$VIDEOINSIGHT_MEDIA_ROOT"' in preflight
    assert 'validate_secure_file "$VIDEOINSIGHT_OFFLINE_PYTHON" 0' in preflight
    assert 'backup_gid" == "0' in preflight
    assert "8#$backup_mode & 0077" in preflight
    assert "! -user root" in preflight
    assert "-perm -0022" in preflight
    assert 'find "$VIDEOINSIGHT_ROOT/python" -xdev' in preflight
    assert (
        'find "$VIDEOINSIGHT_WHEELHOUSE" -mindepth 1 -maxdepth 1 ! -type f' in preflight
    )
    assert 'validate_root_file "$VIDEOINSIGHT_WHEELHOUSE_MANIFEST"' in preflight
    assert "sha256sum --check --status" in preflight
    assert "actual_wheel_files" in preflight
    assert "expected_wheel_files" in preflight
    assert "prepare_secure_state" in common
    assert "open_native_release_lock" in common
    assert "validate_unit_file_safety" in common
    for forbidden in (
        "ExecStartPre",
        "ExecStartPost",
        "ExecStop",
        "ExecStopPost",
        "PartOf",
        "BindsTo",
        "Conflicts",
        "Requires",
        "Requisite",
        "Upholds",
        "PropagatesStopTo",
        "PropagatesReloadTo",
        "PYTHONPATH",
    ):
        assert forbidden in common
    assert '"$VIDEOINSIGHT_RUNTIME_ROOT"' in preflight
    assert 'validate_root_directory "$root_owned_path"' in preflight
    assert (
        'validate_service_tree "$VIDEOINSIGHT_RUNTIME_ROOT/data" '
        '"$SERVICE_UID" "$SERVICE_GID"'
    ) in preflight
    assert 'find "$root" -xdev -type l' in common
    assert "! -type d ! -type f" in common
    assert "/proc/self/mountinfo" in common
    assert "root_device" in common
    assert "8#$mode & 0077" in common
    assert "__pycache__" in preflight
    assert "__pycache__" in common
    assert "systemctl is-active --quiet" in preflight
    assert "validate_trusted_media_tool ffprobe" in preflight
    assert "validate_trusted_media_tool ffmpeg" in preflight
    assert "validate_trusted_media_tool_execution ffprobe" in preflight
    assert "validate_trusted_media_tool_execution ffmpeg" in preflight
    assert "validate_trusted_execution_dependencies" in preflight
    assert 'require_command "$fixed_command"' in preflight
    assert (
        'readonly VIDEOINSIGHT_MEDIA_ROOT="$VIDEOINSIGHT_ROOT/tools/media/bin"'
        in common
    )
    assert (
        'readonly VIDEOINSIGHT_SERVICE_PATH="$VIDEOINSIGHT_MEDIA_ROOT:'
        '/usr/local/bin:/usr/bin:/bin"' in common
    )
    assert "resolve_command_in_explicit_path" in common
    assert 'Environment=PATH="$VIDEOINSIGHT_SERVICE_PATH"' in common
    assert '[[ "$candidate" == "$expected" ]]' in common
    assert "validate_media_tools_manifest" in common
    assert 'sha256sum -- "$candidate"' in common
    assert "固定媒体工具目录必须精确包含" in common
    assert "--value" not in common


def test_upgrade_is_immutable_offline_and_transactional() -> None:
    script = _read("upgrade.sh")
    validator = _read("validate_release_archive.py")

    assert 'readonly ARCHIVE="$VIDEOINSIGHT_ROOT/incoming/' in script
    assert '[[ ! -e "$RELEASE_DIR" && ! -L "$RELEASE_DIR" ]]' in script
    assert 'actual_sha256=$(sha256sum -- "$ARCHIVE"' in script
    assert "${actual_sha256,,}" in script
    assert '"$SCRIPT_DIR/validate_release_archive.py"' in script
    assert "PurePosixPath" in validator
    assert 'or "\\\\" in name' in validator
    assert "stat.S_ISLNK" in validator
    assert "member.flag_bits & 0x1" in validator
    assert "MAX_UNCOMPRESSED_BYTES" in validator
    assert 'run_trusted_offline_python_in_tmp "$STAGING_TMP" -m venv --copies' in script
    assert "run_trusted_staging_python" in script
    assert "--no-index" in script
    assert "--only-binary=:all:" in script
    assert '--find-links "$VIDEOINSIGHT_WHEELHOUSE"' in script
    assert "pip check" in script
    assert "open_native_release_lock" in script
    assert '"$ARCHIVE" "$STAGING_APP" "$VERSION"' in script
    assert (
        'printf \'%s\\n\' "$VERSION" > "$STAGING_APP/release_version.txt"' not in script
    )
    assert "validate_tools_match_release" in script
    assert 'run_trusted_staging_python "$STAGING_DIR/venv/bin/python"' in script
    common = _read("common.sh")
    assert "PIP_CONFIG_FILE=/dev/null" in common
    assert "PIP_NO_CACHE_DIR=1" in common
    assert '"$python_path" -B -I -X utf8' in common
    assert "--require-hashes" in script
    assert "requirements.lock" in script
    assert 'fsync_tree "$STAGING_DIR"' in script
    assert 'durable_rename "$STAGING_DIR" "$RELEASE_DIR"' in script
    assert 'fsync_path "$PREVIOUS_UNIT_BACKUP"' in script
    assert (
        'durable_rename "$LEGACY_DESCRIPTOR_TEMP" "$LEGACY_DESCRIPTOR_FINAL"' in script
    )
    assert 'durable_rename "$UNIT_TEMP" "$VIDEOINSIGHT_UNIT_PATH"' in script
    assert '"$RELEASE_APP/deploy/control-plane/backup_control_plane.py"' in script
    assert '"$RELEASE_APP/deploy/control-plane/restore_control_plane.py"' in script
    assert "$PREVIOUS_APP" not in script
    assert "require_stable_version" in script
    comparison = script.index(
        'version_is_strictly_greater "$VERSION" "$PREVIOUS_VERSION"'
    )
    legacy_binding = script.index("validate_active_legacy_adoption")
    extraction = script.index('"$SCRIPT_DIR/validate_release_archive.py"', comparison)
    stop_service = script.index(
        'stop_control_plane_fail_closed "升级切换前', extraction
    )
    assert legacy_binding < comparison < extraction < stop_service
    assert "run_trusted_offline_python" in script
    assert 'PYTHONDONTWRITEBYTECODE=1 "$VIDEOINSIGHT_OFFLINE_PYTHON"' not in script

    prepare = script.index('durable_rename "$STAGING_DIR" "$RELEASE_DIR"')
    stop = script.index('stop_control_plane_fail_closed "升级切换前', prepare)
    snapshot = script.index("backup_control_plane.py", stop)
    install_unit = script.index(
        'install -o root -g root -m 0644 -- "$UNIT_TEMPLATE"', snapshot
    )
    switch = script.index('atomic_switch_current "$RELEASE_DIR"', install_unit)
    start = script.index('systemctl start "$VIDEOINSIGHT_SERVICE"', switch)
    assert prepare < stop < snapshot < install_unit < switch < start

    assert "restore_snapshot" in script
    assert "atomic_restore_current_target" in script
    assert 'durable_rename "$UNIT_TEMP" "$VIDEOINSIGHT_UNIT_PATH"' in script
    assert "systemctl daemon-reload" in script
    assert "rollback_transaction" in script
    assert script.count("validate_active_legacy_adoption") >= 2
    assert "strict bridge 与 active adoption 恢复校验失败" in script
    assert "CRITICAL:" in script
    assert 'case "$STAGING_DIR" in' in script
    assert 'rm -rf -- "$STAGING_DIR"' in script


def test_health_has_one_retry_local_host_header_and_version_check() -> None:
    common = _read("common.sh")

    assert "for attempt in 1 2; do" in common
    assert 'readonly VIDEOINSIGHT_LOCAL_URL="http://127.0.0.1:18080"' in common
    assert '--header "Host: $domain"' in common
    assert "curl --disable --noproxy '*'" in common
    assert "domain=$(sed -n 's/^CONTROL_PLANE_DOMAIN=//p'" in common
    assert 'health.get("release_version") == expected' in common
    assert "http://localhost" not in common
    assert "https://" not in common
    assert "PYTHONDONTWRITEBYTECODE=1" in common


@pytest.mark.parametrize(
    ("candidate", "baseline", "allowed"),
    (
        ("0.2.7", "0.2.6", True),
        ("0.2.6", "0.2.6", False),
        ("0.2.5", "0.2.6", False),
    ),
)
def test_stable_version_comparison_is_strictly_forward_only(
    candidate: str, baseline: str, allowed: bool
) -> None:
    result = _run_common_function(
        'version_is_strictly_greater "$2" "$3"', candidate, baseline
    )
    assert (result.returncode == 0) is allowed, result.stdout + result.stderr


def test_durable_rename_surfaces_post_rename_fsync_failure(tmp_path: Path) -> None:
    source = tmp_path / "pending"
    destination = tmp_path / "committed"
    source.write_bytes(b"durable-metadata")
    function_call = r"""
fsync_calls=0
fsync_path() {
  fsync_calls=$((fsync_calls + 1))
  (( fsync_calls != 2 ))
}
durable_rename "$2" "$3" "$4"
"""

    result = _run_common_function(
        function_call, str(source), str(destination), str(tmp_path)
    )

    assert result.returncode != 0
    assert not source.exists()
    assert destination.read_bytes() == b"durable-metadata"


@pytest.mark.parametrize(
    "malicious_line",
    (
        "Conflicts=nginx.service",
        "Requires=other.service",
        "RequiresMountsFor=/srv/other-service",
        "After=nginx.service",
        "Wants=other.service",
    ),
)
def test_unit_directive_audit_rejects_cross_service_dependencies(
    tmp_path: Path, malicious_line: str
) -> None:
    unit = tmp_path / "malicious.service"
    template = _read("videoinsight-control-plane.service")
    unit.write_text(
        template.replace("[Unit]\n", f"[Unit]\n{malicious_line}\n"),
        encoding="utf-8",
        newline="\n",
    )

    result = _run_common_function('validate_unit_file_directives "$2"', str(unit))

    assert result.returncode != 0


def test_unit_directive_audit_accepts_only_the_fixed_template() -> None:
    unit = NATIVE_ROOT / "videoinsight-control-plane.service"

    result = _run_common_function('validate_unit_file_directives "$2"', str(unit))

    assert result.returncode == 0, result.stdout + result.stderr


def test_unit_directive_audit_requires_fixed_service_path(tmp_path: Path) -> None:
    unit = tmp_path / "missing-path.service"
    unit.write_text(
        _read("videoinsight-control-plane.service").replace(
            (
                "Environment=PATH=/opt/videoinsight-control-plane/tools/media/bin:"
                "/usr/local/bin:/usr/bin:/bin\n"
            ),
            "",
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = _run_common_function('validate_unit_file_directives "$2"', str(unit))

    assert result.returncode != 0
    assert "PATH" in result.stderr


@pytest.mark.parametrize(
    "malicious_line",
    (
        "AmbientCapabilities=CAP_SYS_ADMIN",
        "SupplementaryGroups=nginx",
        "LoadCredential=token:/etc/shadow",
        "BindPaths=/etc:/srv/etc",
        "RootDirectory=/",
    ),
)
def test_unit_directive_audit_rejects_service_scope_expansion(
    tmp_path: Path, malicious_line: str
) -> None:
    unit = tmp_path / "malicious-service.service"
    template = _read("videoinsight-control-plane.service")
    unit.write_text(
        template.replace("[Service]\n", f"[Service]\n{malicious_line}\n"),
        encoding="utf-8",
        newline="\n",
    )

    result = _run_common_function('validate_unit_file_directives "$2"', str(unit))

    assert result.returncode != 0


def test_unit_directive_audit_rejects_shell_exec_start(tmp_path: Path) -> None:
    unit = tmp_path / "shell.service"
    template = _read("videoinsight-control-plane.service")
    unit.write_text(
        template.replace(
            " --forwarded-allow-ips 127.0.0.1\n",
            " --forwarded-allow-ips 127.0.0.1 ; /bin/sh -c evil\n",
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = _run_common_function('validate_unit_file_directives "$2"', str(unit))

    assert result.returncode != 0


def test_legacy_bridge_is_exact_strict_and_rejects_interpreter_tampering(
    tmp_path: Path,
) -> None:
    unit = tmp_path / "legacy-bridge.service"
    function_call = r"""
unit=$(cygpath -u "$2")
rm -f -- "$unit"
write_legacy_bridge_unit "$unit" 0.2.6 0.2.4
sha256sum -- "$unit"
"""
    generated = _run_common_function(function_call, str(unit))
    assert generated.returncode == 0, generated.stdout + generated.stderr
    digest = generated.stdout.split()[0]
    assert digest == "839ad0602fd054d3d3d2eb5574c6184d4e6ceafa2d381d06f7a7a8dffe6aaf84"
    content = unit.read_text(encoding="utf-8")
    assert "WorkingDirectory=/opt/videoinsight-control-plane/current\n" in content
    assert (
        "ExecStart=/opt/videoinsight-control-plane/releases/0.2.4/venv/bin/python "
        "-m uvicorn project.backend.app.control_plane:app"
    ) in content
    assert "CPUQuota=100%" in content
    assert "MemoryLimit=1G" in content
    assert (
        "Environment=PATH=/opt/videoinsight-control-plane/tools/media/bin:" in content
    )

    validator_call = r"""
unit=$(cygpath -u "$2")
stat() {
  if [[ "$2" == '%a %u' ]]; then
    printf '644 0\n'
  elif [[ "$2" == '%g' ]]; then
    printf '0\n'
  else
    printf '644 0 0\n'
  fi
}
validate_legacy_bridge_unit_file "$unit" 0.2.6 0.2.4 "$3" 0
"""
    accepted = _run_common_function(validator_call, str(unit), digest)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    unit.write_text(
        content.replace(
            "/releases/0.2.4/venv/bin/python", "/releases/0.2.3/venv/bin/python"
        ),
        encoding="utf-8",
        newline="\n",
    )
    tampered_digest = hashlib.sha256(unit.read_bytes()).hexdigest()
    rejected = _run_common_function(validator_call, str(unit), tampered_digest)
    assert rejected.returncode != 0
    assert "ExecStart" in rejected.stderr


def test_effective_exec_start_parser_accepts_systemd_219_and_rejects_ambiguity() -> (
    None
):
    expected_path = "/opt/videoinsight-control-plane/releases/0.2.4/venv/bin/uvicorn"
    expected_argv = (
        f"{expected_path} project.backend.app.control_plane:app --host 127.0.0.1 "
        "--port 18080 --workers 1 --proxy-headers --forwarded-allow-ips "
        "127.0.0.1"
    )
    active = (
        f"{{ path={expected_path} ; argv[]={expected_argv} ; ignore_errors=no ; "
        "start_time=[Mon 2026-08-10 20:36:14 CST] ; stop_time=[n/a] ; "
        "pid=3498 ; code=(null) ; status=0/0 }"
    )
    inactive = (
        f"{{ path={expected_path} ; argv[]={expected_argv} ; ignore_errors=no ; "
        "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }"
    )
    call = 'validate_effective_exec_start_record "$2" "$3" "$4"'
    for fixture in (active, inactive):
        accepted = _run_common_function(call, fixture, expected_path, expected_argv)
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    invalid = (
        active.replace(
            "127.0.0.1 ; ignore_errors", "127.0.0.1 --extra ; ignore_errors"
        ),
        active + " " + inactive,
        active.replace(f"path={expected_path}", f"path={expected_path}-other", 1),
        "prefix " + active,
        active + " suffix",
        active.replace(" ; ignore_errors=no", " ; argv[]=second ; ignore_errors=no"),
        active.replace(
            " ; ignore_errors=no", " --ignore_errors=no ; ignore_errors=yes"
        ),
        active + "\n",
    )
    for fixture in invalid:
        rejected = _run_common_function(call, fixture, expected_path, expected_argv)
        assert rejected.returncode != 0, fixture


def test_fail_closed_stop_kills_only_fixed_service_and_requires_inactive() -> None:
    fallback_success = _run_common_function(
        r"""
state=active
systemctl() {
  case "$1" in
    stop) return 1 ;;
    is-active)
      printf '%s\n' "$state"
      [[ "$state" == inactive ]] && return 3
      return 0
      ;;
    show) printf 'MainPID=0\n'; return 0 ;;
    kill)
      [[ "$4" == "$VIDEOINSIGHT_SERVICE" ]] || return 98
      state=inactive
      return 0
      ;;
    *) return 97 ;;
  esac
}
stop_control_plane_fail_closed "测试停止"
"""
    )
    assert fallback_success.returncode == 0, (
        fallback_success.stdout + fallback_success.stderr
    )

    stop_failed_but_inactive = _run_common_function(
        r"""
systemctl() {
  case "$1" in
    stop) return 1 ;;
    is-active) printf 'inactive\n'; return 3 ;;
    show) printf 'MainPID=0\n'; return 0 ;;
    kill) return 99 ;;
    *) return 97 ;;
  esac
}
stop_control_plane_fail_closed "测试停止"
"""
    )
    assert stop_failed_but_inactive.returncode == 0
    assert "已复核" in stop_failed_but_inactive.stderr

    stubborn = _run_common_function(
        r"""
systemctl() {
  case "$1" in
    stop) return 1 ;;
    is-active) printf 'active\n'; return 0 ;;
    show) printf 'MainPID=3498\n'; return 0 ;;
    kill)
      [[ "$4" == "$VIDEOINSIGHT_SERVICE" ]] || return 98
      return 1
      ;;
    *) return 97 ;;
  esac
}
stop_control_plane_fail_closed "测试停止"
"""
    )
    assert stubborn.returncode != 0
    assert "CRITICAL" in stubborn.stderr
    assert "未确认停止" in stubborn.stderr

    deactivating = _run_common_function(
        r"""
systemctl() {
  case "$1" in
    stop) return 1 ;;
    is-active) printf 'deactivating\n'; return 3 ;;
    show) printf 'MainPID=3498\n'; return 0 ;;
    kill) return 1 ;;
    *) return 97 ;;
  esac
}
stop_control_plane_fail_closed "测试停止"
"""
    )
    assert deactivating.returncode != 0
    assert "state=deactivating" in deactivating.stderr


def test_all_transaction_recovery_paths_use_verified_fail_closed_stop() -> None:
    for name in ("normalize_legacy_unit.sh", "upgrade.sh", "rollback.sh"):
        script = _read(name)
        assert "stop_control_plane_fail_closed" in script
        assert 'systemctl stop "$VIDEOINSIGHT_SERVICE"' not in script
        assert "恢复健康失败后的停止" in script


@pytest.mark.parametrize(
    "bad_metadata",
    (
        "600 2002 1002 111",
        "644 1001 1002 111",
        "600 1001 1002 222",
    ),
)
def test_runtime_tree_rejects_nested_wrong_owner_or_world_readable(
    tmp_path: Path, bad_metadata: str
) -> None:
    data_root = tmp_path / "data"
    nested = data_root / "nested"
    nested.mkdir(parents=True)
    (nested / "bad.txt").write_bytes(b"runtime")
    function_call = r"""
data_root="$2"
bad_metadata="$3"
stat() {
  local path="${!#}"
  if [[ "$1" == "-c" && "$2" == "%d" ]]; then
    printf '111\n'
    return
  fi
  if [[ "$path" == "$data_root/nested/bad.txt" ]]; then
    printf '%s\n' "$bad_metadata"
  elif [[ -d "$path" ]]; then
    printf '700 1001 1002 111\n'
  else
    printf '600 1001 1002 111\n'
  fi
}
validate_service_tree "$data_root" 1001 1002
"""

    result = _run_common_function(function_call, str(data_root), bad_metadata)

    assert result.returncode != 0


def test_verify_requires_new_layout_unit_database_identity_and_permissions() -> None:
    script = _read("verify.sh")

    assert "validate_unit_effective_config" in script
    assert '[[ "$CURRENT_TARGET" == "$CURRENT_RELEASE" ]]' in script
    assert 'validate_release_python "$CURRENT_RELEASE"' in script
    assert (
        'validate_service_tree "$VIDEOINSIGHT_RUNTIME_ROOT/data" '
        '"$SERVICE_UID" "$SERVICE_GID"'
    ) in script
    assert 'health_check "$EXPECTED_VERSION"' in script
    assert "video_intelligence.db" in script
    assert "PRAGMA quick_check" in script
    assert 'database_uid" == "$SERVICE_UID' in script
    assert "8#$database_mode & 0077" in script
    assert "validate_trusted_media_tool ffprobe" in script
    assert "validate_trusted_media_tool ffmpeg" in script
    assert "validate_trusted_media_tool_execution ffprobe" in script
    assert "validate_trusted_media_tool_execution ffmpeg" in script
    assert "validate_trusted_execution_dependencies" in script
    assert "未访问外网、未调用供应商" in script


def test_media_tool_execution_gate_clears_environment_and_fixes_identity() -> None:
    common = _read("common.sh")

    assert 'readonly VIDEOINSIGHT_SYSTEM_ENV="/usr/bin/env"' in common
    assert 'readonly VIDEOINSIGHT_SYSTEM_TIMEOUT="/usr/bin/timeout"' in common
    assert 'readonly VIDEOINSIGHT_SYSTEM_RUNUSER="/usr/sbin/runuser"' in common
    assert 'validate_trusted_executable_path "$candidate" /' in common
    assert '"$VIDEOINSIGHT_SYSTEM_ENV" -i HOME=/nonexistent' in common
    assert 'PATH="/usr/bin:/usr/sbin:/bin"' in common
    assert '"$VIDEOINSIGHT_SYSTEM_TIMEOUT" --signal=KILL 10' in common
    assert (
        '"$VIDEOINSIGHT_SYSTEM_RUNUSER" --user "$VIDEOINSIGHT_SERVICE_USER"' in common
    )
    assert '--group "$VIDEOINSIGHT_SERVICE_GROUP" --' in common
    assert '"$VIDEOINSIGHT_SYSTEM_ENV" -i HOME=/nonexistent' in common
    assert 'PATH="$VIDEOINSIGHT_SERVICE_PATH"' in common
    assert '"$candidate" -hide_banner -version' in common
    assert "--preserve-environment" not in common


def test_media_tools_manifest_is_tracked_exact_and_bound_to_reviewed_binaries() -> None:
    lines = MEDIA_TOOLS_MANIFEST.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(r"^([0-9a-f]{64})  (ffmpeg|ffprobe)$")
    parsed: dict[str, str] = {}
    for line in lines:
        match = pattern.fullmatch(line)
        assert match is not None
        digest, name = match.groups()
        assert name not in parsed
        parsed[name] = digest

    assert list(parsed) == ["ffmpeg", "ffprobe"]
    assert parsed == {
        "ffmpeg": "e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99",
        "ffprobe": "4f231a1960d83e403d08f7971e271707bec278a9ae18e21b8b5b03186668450d",
    }
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--error-unmatch",
            str(MEDIA_TOOLS_MANIFEST.relative_to(REPOSITORY_ROOT)),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0, "media-tools.sha256 必须被 Git 跟踪后才能发布"


def test_media_tools_manifest_parser_accepts_the_reviewed_exact_pair() -> None:
    function_call = r"""
stat() {
  if [[ "$2" == '%g' ]]; then
    printf '0\n'
  else
    printf '644 0\n'
  fi
}
validate_media_tools_manifest
"""

    result = _run_common_function(function_call)

    assert result.returncode == 0, result.stdout + result.stderr


def test_media_tool_gate_accepts_exact_files_with_matching_hashes(
    tmp_path: Path,
) -> None:
    control_root = tmp_path / "control"
    media_root = control_root / "tools" / "media" / "bin"
    media_root.mkdir(parents=True)
    ffmpeg = media_root / "ffmpeg"
    ffprobe = media_root / "ffprobe"
    shutil.copy2(_executable_fixture(), ffmpeg)
    shutil.copy2(_executable_fixture(), ffprobe)
    native_root = tmp_path / "native-systemd"
    native_root.mkdir()
    common = native_root / "common.sh"
    source = _read("common.sh").replace(
        'readonly VIDEOINSIGHT_ROOT="/opt/videoinsight-control-plane"',
        f'readonly VIDEOINSIGHT_ROOT="{_posix_path(control_root)}"',
    )
    source = source.replace(
        'readonly VIDEOINSIGHT_SYSTEM_RUNUSER="/usr/sbin/runuser"',
        'readonly VIDEOINSIGHT_SYSTEM_RUNUSER="/usr/bin/true"',
    )
    common.write_text(source, encoding="utf-8", newline="\n")
    (native_root / "media-tools.sha256").write_text(
        f"{hashlib.sha256(ffmpeg.read_bytes()).hexdigest()}  ffmpeg\n"
        f"{hashlib.sha256(ffprobe.read_bytes()).hexdigest()}  ffprobe\n",
        encoding="utf-8",
        newline="\n",
    )
    function_call = r"""
stat() {
  case "$2" in
    '%a %u') printf '755 0\n' ;;
    '%g') printf '0\n' ;;
    *) printf '755 0 0\n' ;;
  esac
}
getent() {
  printf 'videoinsight:x:994:\n'
}
validate_trusted_media_tool ffmpeg
validate_trusted_media_tool ffprobe
validate_trusted_media_tool_execution ffmpeg "$VIDEOINSIGHT_MEDIA_ROOT/ffmpeg"
validate_trusted_media_tool_execution ffprobe "$VIDEOINSIGHT_MEDIA_ROOT/ffprobe"
"""

    result = _run_common_file_function(common, function_call)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        _posix_path(ffmpeg),
        _posix_path(ffprobe),
    ]


@pytest.mark.parametrize("failure", ("hash", "extra", "execution"))
def test_media_tool_gate_rejects_hash_mismatch_extra_entry_or_execution_failure(
    tmp_path: Path, failure: str
) -> None:
    control_root = tmp_path / "control"
    media_root = control_root / "tools" / "media" / "bin"
    media_root.mkdir(parents=True)
    ffmpeg = media_root / "ffmpeg"
    ffprobe = media_root / "ffprobe"
    shutil.copy2(_executable_fixture(), ffmpeg)
    shutil.copy2(_executable_fixture(), ffprobe)
    if failure == "extra":
        (media_root / "unexpected").write_bytes(b"unexpected")
    native_root = tmp_path / "native-systemd"
    native_root.mkdir()
    common = native_root / "common.sh"
    source = _read("common.sh").replace(
        'readonly VIDEOINSIGHT_ROOT="/opt/videoinsight-control-plane"',
        f'readonly VIDEOINSIGHT_ROOT="{_posix_path(control_root)}"',
    )
    execution_fixture = "/usr/bin/false" if failure == "execution" else "/usr/bin/true"
    source = source.replace(
        'readonly VIDEOINSIGHT_SYSTEM_RUNUSER="/usr/sbin/runuser"',
        f'readonly VIDEOINSIGHT_SYSTEM_RUNUSER="{execution_fixture}"',
    )
    common.write_text(source, encoding="utf-8", newline="\n")
    ffmpeg_hash = hashlib.sha256(ffmpeg.read_bytes()).hexdigest()
    if failure == "hash":
        ffmpeg_hash = "0" * 64
    (native_root / "media-tools.sha256").write_text(
        f"{ffmpeg_hash}  ffmpeg\n"
        f"{hashlib.sha256(ffprobe.read_bytes()).hexdigest()}  ffprobe\n",
        encoding="utf-8",
        newline="\n",
    )
    function_call = r"""
stat() {
  case "$2" in
    '%a %u') printf '755 0\n' ;;
    '%g') printf '0\n' ;;
    *) printf '755 0 0\n' ;;
  esac
}
getent() {
  printf 'videoinsight:x:994:\n'
}
candidate=$(validate_trusted_media_tool ffmpeg)
if [[ "$2" == 'execution' ]]; then
  validate_trusted_media_tool_execution ffmpeg "$candidate"
fi
"""

    result = _run_common_file_function(common, function_call, failure)

    assert result.returncode != 0


@pytest.mark.parametrize(
    "manifest",
    (
        "0" * 64 + "  ffmpeg\n",
        "0" * 64 + "  ffmpeg\n" + "1" * 64 + "  ffmpeg\n",
        "0" * 64 + "  ffmpeg\n" + "1" * 64 + "  unexpected\n",
        "0" * 63 + "  ffmpeg\n" + "1" * 64 + "  ffprobe\n",
    ),
)
def test_media_tools_manifest_parser_rejects_incomplete_duplicate_or_malformed_entries(
    tmp_path: Path, manifest: str
) -> None:
    native_root = tmp_path / "native-systemd"
    native_root.mkdir()
    common = native_root / "common.sh"
    shutil.copy2(NATIVE_ROOT / "common.sh", common)
    (native_root / "media-tools.sha256").write_text(
        manifest, encoding="utf-8", newline="\n"
    )
    function_call = r"""
stat() {
  if [[ "$2" == '%g' ]]; then
    printf '0\n'
  else
    printf '644 0\n'
  fi
}
validate_media_tools_manifest
"""

    result = _run_common_file_function(common, function_call)

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("bad_path_kind", "bad_mode"),
    (
        ("tool", "775"),
        ("ancestor", "777"),
        ("ancestor", "750"),
    ),
)
def test_trusted_media_tool_rejects_mutable_or_service_inaccessible_paths(
    bad_path_kind: str, bad_mode: str
) -> None:
    tool = Path(_bash())
    function_call = r"""
tool=$(readlink -f -- "$(cygpath -u "$2")")
bad_path_kind="$3"
bad_mode="$4"
bad_ancestor=$(dirname -- "$tool")
stat() {
  local path="${!#}"
  local mode=755
  if [[ "$bad_path_kind" == "tool" && "$path" == "$tool" ]]; then
    mode="$bad_mode"
  elif [[ "$bad_path_kind" == "ancestor" && "$path" == "$bad_ancestor" ]]; then
    mode="$bad_mode"
  fi
  printf '%s 0 0\n' "$mode"
}
getent() {
  printf 'videoinsight:x:994:\n'
}
validate_trusted_executable_path "$tool"
"""

    result = _run_common_function(function_call, str(tool), bad_path_kind, bad_mode)

    assert result.returncode != 0


def test_trusted_media_tool_accepts_root_owned_nonwritable_path() -> None:
    tool = Path(_bash())
    function_call = r"""
stat() {
  printf '755 0 0\n'
}
getent() {
  printf 'videoinsight:x:994:\n'
}
tool=$(readlink -f -- "$(cygpath -u "$2")")
validate_trusted_executable_path "$tool"
"""

    result = _run_common_function(function_call, str(tool))

    assert result.returncode == 0, result.stdout + result.stderr


def test_trusted_media_tool_accepts_service_group_0750_ancestor() -> None:
    tool = Path(_bash())
    function_call = r"""
tool=$(readlink -f -- "$(cygpath -u "$2")")
group_ancestor=$(dirname -- "$tool")
stat() {
  local path="${!#}"
  if [[ "$path" == "$group_ancestor" ]]; then
    printf '750 0 994\n'
  else
    printf '755 0 0\n'
  fi
}
getent() {
  printf 'videoinsight:x:994:\n'
}
validate_trusted_executable_path "$tool"
"""

    result = _run_common_function(function_call, str(tool))

    assert result.returncode == 0, result.stdout + result.stderr


def test_trusted_media_tool_rejects_root_group_0750_ancestor() -> None:
    tool = Path(_bash())
    function_call = r"""
tool=$(readlink -f -- "$(cygpath -u "$2")")
blocked_ancestor=$(dirname -- "$tool")
stat() {
  local path="${!#}"
  if [[ "$path" == "$blocked_ancestor" ]]; then
    printf '750 0 0\n'
  else
    printf '755 0 0\n'
  fi
}
getent() {
  printf 'videoinsight:x:994:\n'
}
validate_trusted_executable_path "$tool"
"""

    result = _run_common_function(function_call, str(tool))

    assert result.returncode != 0


def test_manual_rollback_has_safety_snapshot_and_restores_failed_rollback() -> None:
    script = _read("rollback.sh")

    assert (
        '[[ "$TARGET_SNAPSHOT_NAME" == "$(basename -- "$TARGET_SNAPSHOT_NAME")" ]]'
        in script
    )
    assert 'validate_root_file "$TARGET_SNAPSHOT"' in script
    assert "videoinsight-control-plane-pre-rollback" in script
    assert "backup_control_plane.py" in script
    assert 'restore_with "$TARGET_SNAPSHOT_NAME" "$TARGET_VERSION"' in script
    assert 'atomic_switch_current "$TARGET_RELEASE"' in script
    assert "rollback_failed_rollback" in script
    assert 'restore_with "$SAFETY_SNAPSHOT_NAME" "$ORIGINAL_VERSION"' in script
    assert '"$ORIGINAL_APP/deploy/control-plane/restore_control_plane.py"' in script
    assert '"$TARGET_APP/deploy/control-plane/restore_control_plane.py"' not in script
    assert (
        'validate_tools_match_release "$TARGET_APP/deploy/control-plane/native-systemd"'
    ) in script
    assert script.index(
        'validate_tools_match_release "$TARGET_APP/deploy/control-plane/native-systemd"'
    ) < script.index("TRANSACTION_STARTED=1")
    assert "legacy_rollback_descriptor.py" in script
    assert "validate_legacy_adoption_record record" in script
    assert "validate_legacy_bridge_unit_file" in script
    assert "validate_active_legacy_adoption" in script
    assert "原宽松 unit 仅作取证、不会恢复" in script
    assert 'health_check ""' in script
    assert "CURRENT_UNIT_SAFETY" in script
    assert "open_native_release_lock" in script


def test_release_tools_comparison_rejects_tampered_target(tmp_path: Path) -> None:
    target = tmp_path / "native-systemd"
    shutil.copytree(
        NATIVE_ROOT,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    (target / "common.sh").write_text("tampered\n", encoding="utf-8")

    result = _run_common_function('validate_tools_match_release "$2"', str(target))

    assert result.returncode != 0


def test_unit_install_is_recoverable_and_refuses_legacy_layout() -> None:
    script = _read("install_unit.sh")

    assert "尚未迁移到 releases/<版本>/{app,venv}" in script
    assert "state/unit-backups" in script
    assert "restore_unit" in script
    assert "CRITICAL:" in script
    assert "validate_existing_unit_scope" in script
    assert 'durable_rename "$UNIT_TEMP" "$VIDEOINSIGHT_UNIT_PATH"' in script
    assert "新 unit 写入或落盘失败" in script
    assert "open_native_release_lock" in script
    assert 'validate_release_python "$CURRENT_RELEASE"' in script
    assert "validate_tools_match_release" in script
    assert 'durable_rename "$UNIT_TEMP" "$VIDEOINSIGHT_UNIT_PATH"' in script
    assert "systemctl daemon-reload" in script
    assert "systemctl start" not in script
    assert "systemctl stop" not in script


def test_scripts_do_not_source_server_secrets_or_publish_them() -> None:
    combined = "\n".join(_read(name) for name in SHELL_SCRIPTS)
    assert 'source "$VIDEOINSIGHT_ENV_FILE"' not in combined
    assert '. "$VIDEOINSIGHT_ENV_FILE"' not in combined
    assert 'cat "$VIDEOINSIGHT_ENV_FILE"' not in combined
    assert "COPYWRITING_API_KEY" not in combined
    assert "ALIBABA_CLOUD_ACCESS_KEY_SECRET" not in combined
    assert "SHUYING_AVATAR_API_CODE" not in combined


def test_bundle_builder_will_include_native_systemd_directory() -> None:
    builder = (
        REPOSITORY_ROOT / "scripts" / "build_control_plane_bundle.ps1"
    ).read_text(encoding="utf-8")
    assert "'deploy/control-plane'" in builder
    assert "ls-tree -r $sourceCommit" in builder
    assert "$candidatePaths.Add([string]$record.Entry)" in builder
    assert "Get-CommittedGitBlobBytes" in builder


def test_readme_bootstraps_tools_outside_legacy_current() -> None:
    readme = _read("README.md")
    assert "/opt/videoinsight-control-plane/tools/native-systemd" in readme
    assert "旧版 `current/app` 尚无这些工具" in readme
    assert "current/app/deploy/control-plane/native-systemd" not in readme


def test_offline_requirement_lock_and_wheelhouse_manifest_are_exact() -> None:
    requirements = (
        (REPOSITORY_ROOT / "deploy" / "control-plane" / "requirements.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    lock_lines = (
        (REPOSITORY_ROOT / "deploy" / "control-plane" / "requirements.lock")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    manifest_lines = _read("wheelhouse.sha256").splitlines()

    assert requirements and all("==" in line for line in requirements)
    assert all(
        not any(operator in line for operator in (">=", "<=", "<", ">", "~="))
        for line in requirements
    )
    lock_pattern = re.compile(
        r"^([A-Za-z0-9_.-]+)==([^ ]+) --hash=sha256:([0-9a-f]{64})$"
    )
    locked: dict[str, tuple[str, str]] = {}
    for line in lock_lines:
        match = lock_pattern.fullmatch(line)
        assert match is not None, f"invalid lock line: {line}"
        name, version, digest = match.groups()
        normalized = name.casefold().replace("_", "-")
        assert normalized not in locked
        locked[normalized] = (version, digest)

    for requirement in requirements:
        name, version = requirement.split("==", 1)
        normalized = name.split("[", 1)[0].casefold().replace("_", "-")
        assert locked[normalized][0] == version

    manifest_pattern = re.compile(
        r"^([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._+-]*\.whl)$"
    )
    manifest: dict[str, str] = {}
    for line in manifest_lines:
        match = manifest_pattern.fullmatch(line)
        assert match is not None, f"invalid wheelhouse manifest line: {line}"
        digest, filename = match.groups()
        assert filename not in manifest
        manifest[filename] = digest
    assert set(manifest.values()) == {digest for _, digest in locked.values()}

    local_wheelhouse = REPOSITORY_ROOT / "work" / "server-deploy-xmt" / "wheelhouse"
    if local_wheelhouse.is_dir():
        actual = {
            wheel.name: hashlib.sha256(wheel.read_bytes()).hexdigest()
            for wheel in local_wheelhouse.iterdir()
            if wheel.is_file()
        }
        assert actual == manifest


def test_legacy_descriptor_v2_binds_adoption_snapshot_and_strict_bridge_hashes(
    tmp_path: Path,
) -> None:
    descriptor_module = _legacy_descriptor()
    root = tmp_path / "control-plane"
    backup_root = root / "backups"
    app = root / "releases" / "0.2.6" / "app"
    unit_backups = root / "state" / "unit-backups"
    descriptor_root = root / "state" / "legacy-rollbacks"
    adoption = root / "state" / "legacy-adoption.json"
    app.mkdir(parents=True)
    backup_root.mkdir()
    unit_backups.mkdir(parents=True)
    descriptor_root.mkdir()
    snapshot_name = "videoinsight-control-plane-pre-upgrade-0.2.7-test.zip"
    unit_name = "videoinsight-control-plane.service.pre-0.2.7.test"
    snapshot = backup_root / snapshot_name
    unit = unit_backups / unit_name
    snapshot.write_bytes(b"snapshot")
    unit.write_bytes(b"unit")
    bridge_sha = hashlib.sha256(unit.read_bytes()).hexdigest()
    adoption.write_text(
        json.dumps(
            {
                "format": "videoinsight-native-legacy-adoption-v1",
                "status": "active",
                "application_version": "0.2.6",
                "interpreter_version": "0.2.4",
                "original_current_link": str(app),
                "bridge_unit_sha256": bridge_sha,
            }
        ),
        encoding="utf-8",
    )
    descriptor = descriptor_root / f"{snapshot_name}.json"

    descriptor_module.create_descriptor(
        descriptor,
        root=root,
        source_version="0.2.6",
        upgraded_to_version="0.2.7",
        interpreter_version="0.2.4",
        original_current_link=str(app),
        bridge_unit_backup_name=unit_name,
        snapshot_name=snapshot_name,
        snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        bridge_unit_backup_sha256=bridge_sha,
        bridge_unit_sha256=bridge_sha,
        adoption_descriptor_name="legacy-adoption.json",
        adoption_descriptor_sha256=hashlib.sha256(adoption.read_bytes()).hexdigest(),
    )

    assert descriptor_module.validate_descriptor(
        descriptor,
        root=root,
        backup_root=backup_root,
        expected_source_version="0.2.6",
        expected_upgraded_to_version="0.2.7",
        expected_snapshot_name=snapshot_name,
    ) == (str(app), unit_name, "legacy-adoption.json", "0.2.6", "0.2.4", bridge_sha)

    unit.write_bytes(b"tampered bridge")
    with pytest.raises(ValueError, match="bridge unit 备份哈希"):
        descriptor_module.validate_descriptor(
            descriptor,
            root=root,
            backup_root=backup_root,
            expected_source_version="0.2.6",
            expected_upgraded_to_version="0.2.7",
            expected_snapshot_name=snapshot_name,
        )
    unit.write_bytes(b"unit")
    adoption.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="adoption 描述哈希"):
        descriptor_module.validate_descriptor(
            descriptor,
            root=root,
            backup_root=backup_root,
            expected_source_version="0.2.6",
            expected_upgraded_to_version="0.2.7",
            expected_snapshot_name=snapshot_name,
        )


def test_legacy_adoption_summaries_use_exact_algorithms_and_detect_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _legacy_adoption_descriptor()
    assert module.EXPECTED_INTERPRETER_LINKS == {
        "bin/python": "/opt/videoinsight-control-plane/python/3.12.13/bin/python3",
        "bin/python3": "python",
        "bin/python3.12": "python",
        "lib64": "lib",
    }
    control_root = tmp_path / "control"
    app = control_root / "releases" / "0.2.6" / "app"
    venv = control_root / "releases" / "0.2.4" / "venv"
    (app / "nested").mkdir(parents=True)
    (venv / "bin").mkdir(parents=True)
    (app / "b.txt").write_bytes(b"b")
    (app / "nested" / "a.txt").write_bytes(b"a")
    (venv / "bin" / "python").write_bytes(b"python")
    monkeypatch.setattr(module, "_mounted_paths", lambda: set())
    monkeypatch.setattr(
        module,
        "_validate_root_policy",
        lambda root, **_kwargs: root.lstat(),
    )
    monkeypatch.setattr(
        module, "_validate_entry_policy", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(module, "EXPECTED_INTERPRETER_LINKS", {})

    app_count, app_digest = module.application_tree_summary(
        app, control_root=control_root, service_uid=996, service_gid=994
    )
    expected_app = hashlib.sha256()
    root_metadata = app.lstat()
    expected_app.update(
        (
            f"d\t.\t{stat.S_IMODE(root_metadata.st_mode):04o}\t"
            f"{root_metadata.st_uid}\t{root_metadata.st_gid}\t\n"
        ).encode()
    )
    for path in sorted(
        app.rglob("*"),
        key=lambda item: item.relative_to(app).as_posix(),
    ):
        metadata = path.lstat()
        kind = "d" if path.is_dir() else "f"
        value = "" if kind == "d" else hashlib.sha256(path.read_bytes()).hexdigest()
        expected_app.update(
            (
                f"{kind}\t{path.relative_to(app).as_posix()}\t"
                f"{stat.S_IMODE(metadata.st_mode):04o}\t{metadata.st_uid}\t"
                f"{metadata.st_gid}\t{value}\n"
            ).encode()
        )
    assert (app_count, app_digest) == (2, expected_app.hexdigest())

    venv_count, venv_digest = module.interpreter_tree_summary(
        venv,
        control_root=control_root,
        service_uid=996,
        service_gid=994,
        offline_python=venv / "bin" / "python",
    )
    expected_venv = hashlib.sha256()
    venv_metadata = venv.lstat()
    expected_venv.update(
        (
            f"d\t.\t{stat.S_IMODE(venv_metadata.st_mode):04o}\t"
            f"{venv_metadata.st_uid}\t{venv_metadata.st_gid}\t\n"
        ).encode()
    )
    for path in sorted(
        venv.rglob("*"), key=lambda item: item.relative_to(venv).as_posix()
    ):
        metadata = path.lstat()
        kind = "d" if path.is_dir() else "f"
        value = "" if kind == "d" else hashlib.sha256(path.read_bytes()).hexdigest()
        expected_venv.update(
            (
                f"{kind}\t{path.relative_to(venv).as_posix()}\t"
                f"{stat.S_IMODE(metadata.st_mode):04o}\t{metadata.st_uid}\t"
                f"{metadata.st_gid}\t{value}\n"
            ).encode()
        )
    assert (venv_count, venv_digest) == (2, expected_venv.hexdigest())

    (app / "b.txt").write_bytes(b"changed")
    assert (
        module.application_tree_summary(
            app, control_root=control_root, service_uid=996, service_gid=994
        )[1]
        != app_digest
    )


def test_legacy_tree_rejects_writable_version_parent_and_mount_before_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _legacy_adoption_descriptor()
    control_root = tmp_path / "control"
    app = control_root / "releases" / "0.2.6" / "app"
    app.mkdir(parents=True)
    original_lstat = Path.lstat
    writable_parent = app.parent

    def controlled_lstat(path: Path) -> os.stat_result:
        metadata = original_lstat(path)
        permissions = 0o777 if path == writable_parent else 0o755
        values = list(metadata)
        values[0] = stat.S_IFMT(metadata.st_mode) | permissions
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", controlled_lstat)
    with pytest.raises(ValueError, match="祖先"):
        module._validate_root_policy(
            app, control_root=control_root, service_gid=994, mounted=set()
        )

    walked = False

    def unexpected_walk(_self: Path, _pattern: str):
        nonlocal walked
        walked = True
        return iter(())

    monkeypatch.setattr(Path, "rglob", unexpected_walk)
    monkeypatch.setattr(module, "_mounted_paths", lambda: {str(app / "nested")})
    with pytest.raises(ValueError, match="挂载"):
        module.application_tree_summary(
            app,
            control_root=control_root,
            service_uid=996,
            service_gid=994,
        )
    assert not walked


def test_interpreter_tree_allows_only_the_audited_empty_root_lock(
    tmp_path: Path,
) -> None:
    module = _legacy_adoption_descriptor()

    def root_only_metadata(path: Path) -> os.stat_result:
        current = path.lstat()
        return os.stat_result(
            (
                stat.S_IFREG | 0o600,
                current.st_ino,
                current.st_dev,
                current.st_nlink,
                0,
                0,
                current.st_size,
                current.st_atime,
                current.st_mtime,
                current.st_ctime,
            )
        )

    lock = tmp_path / ".lock"
    lock.write_bytes(b"")
    metadata = root_only_metadata(lock)

    with pytest.raises(ValueError, match="无法读取"):
        module._validate_entry_policy(
            lock,
            metadata,
            relative=".lock",
            root_device=metadata.st_dev,
            service_gid=994,
            mounted=set(),
        )

    module._validate_entry_policy(
        lock,
        metadata,
        relative=".lock",
        root_device=metadata.st_dev,
        service_gid=994,
        mounted=set(),
        audited_root_only_files=module.AUDITED_ROOT_ONLY_INTERPRETER_FILES,
    )

    lock.write_bytes(b"not-empty")
    metadata = root_only_metadata(lock)
    with pytest.raises(ValueError, match="无法读取"):
        module._validate_entry_policy(
            lock,
            metadata,
            relative=".lock",
            root_device=metadata.st_dev,
            service_gid=994,
            mounted=set(),
            audited_root_only_files=module.AUDITED_ROOT_ONLY_INTERPRETER_FILES,
        )

    other = tmp_path / "secret"
    other.write_bytes(b"")
    other_metadata = root_only_metadata(other)
    with pytest.raises(ValueError, match="无法读取"):
        module._validate_entry_policy(
            other,
            other_metadata,
            relative="secret",
            root_device=other_metadata.st_dev,
            service_gid=994,
            mounted=set(),
            audited_root_only_files=module.AUDITED_ROOT_ONLY_INTERPRETER_FILES,
        )


def test_normalize_legacy_unit_is_one_time_exact_and_failure_closed() -> None:
    script = _read("normalize_legacy_unit.sh")
    assert 'readonly AUDITED_APPLICATION_VERSION="0.2.6"' in script
    assert 'readonly AUDITED_INTERPRETER_VERSION="0.2.4"' in script
    assert "98e7841399dcb1cb5654225bbfde65735fe0dc8cc3b56c0bd2336ad6f116a994" in script
    assert "839ad0602fd054d3d3d2eb5574c6184d4e6ceafa2d381d06f7a7a8dffe6aaf84" in script
    assert "81c846d367b74d087fd845372f673a78011cdd0f48f2952c91a98f7e22ab2dc6" in script
    assert "bfd8ba051af78d812c9b39c1679b7843c14196cea77ee7457b8599e8797368da" in script
    assert "copy-evidence" in script
    assert "O_EXCL" in _read("legacy_adoption_descriptor.py")
    assert "restore_original_unit" in script
    assert "服务保持停止" in script
    assert "validate_active_legacy_adoption" in script
    assert 'expected_exec_path="$INTERPRETER_ROOT/bin/uvicorn"' in script
    assert "validate_effective_exec_start_record" in script
    assert '"$working_directory" == "$VIDEOINSIGHT_CURRENT"' in script
    assert '"#!$INTERPRETER_PYTHON"' in script
    assert '/bin/bash "$SCRIPT_DIR/preflight.sh"' in script
    assert "systemctl restart" not in script


def test_normalize_persists_prepared_before_bridge_and_recovers_all_states() -> None:
    script = _read("normalize_legacy_unit.sh")
    prepared_create = script.index("create-prepared")
    prepared_publish = script.index(
        'durable_rename "$DESCRIPTOR_TEMP" "$VIDEOINSIGHT_LEGACY_ADOPTION_DESCRIPTOR"'
    )
    transaction = script.index("TRANSACTION_STARTED=1")
    bridge_publish = script.index(
        'durable_rename "$UNIT_TEMP" "$VIDEOINSIGHT_UNIT_PATH"', transaction
    )
    promotion = script.index("promote_adoption_descriptor", bridge_publish)
    assert prepared_create < prepared_publish < transaction < bridge_publish < promotion
    assert 'if [[ "$installed_unit_sha256" == "$ORIGINAL_UNIT_SHA256" ]]' in script
    assert 'elif [[ "$installed_unit_sha256" == "$BRIDGE_UNIT_SHA256" ]]' in script
    assert "prepared adoption 遇到原 unit/strict bridge 之外的混合状态" in script
    assert "一次性 legacy adoption 已完整处于 active 状态" in script
    assert "stop_control_plane_fail_closed" in script
    assert "--legacy-prepared" in script
    assert (
        '"$exit_status" == "active" && "$installed_sha" == "$BRIDGE_UNIT_SHA256"'
        in script
    )
    assert "active adoption 已持久化，退出恢复保持 strict bridge 不变" in script


def test_offline_python_gate_is_manifest_bound_isolated_and_before_lock(
    tmp_path: Path,
) -> None:
    common = _read("common.sh")
    manifest = _read("offline-python-tree.sha256").splitlines()
    assert manifest == [
        "f4446ac8e57f0a85d2bd0851fc05acb0cd5e8137f6752df428116ddc8e4fab01  "
        "videoinsight-offline-python-tree-v1",
        "4728  descendants",
        "3485  regular-files",
        "1048  symlinks",
    ]
    lock_body = common.split("open_native_release_lock() {", 1)[1].split("}", 1)[0]
    assert lock_body.index("validate_offline_python_runtime") < lock_body.index(
        "open_native_release_lock_without_runtime_validation"
    )
    unvalidated_lock = common.split(
        "open_native_release_lock_without_runtime_validation() {", 1
    )[1].split("}", 1)[0]
    assert "prepare_secure_state" in unvalidated_lock
    assert "/proc/self/mountinfo" in common
    assert "NF < 5 { exit 2 }" in common
    assert "gsub(/\\\\040/" in common
    assert '"$leaf" == "sitecustomize.py"' in common
    assert '"$leaf" == "usercustomize.py"' in common
    assert '"$uid" == "0" && "$device" == "$root_device"' in common
    digest_body = common.split("actual_digest=$(", 1)[1].split(
        '[[ "$actual_digest" == "$expected_digest" ]]', 1
    )[0]
    assert "case " not in digest_body
    assert "esac" not in digest_body
    assert '"$VIDEOINSIGHT_SYSTEM_ENV" -i' in common
    assert '"$VIDEOINSIGHT_OFFLINE_PYTHON" -B -I -S -X utf8' in common
    assert "PYTHONPATH=" not in common
    assert _read("preflight.sh").count("validate_offline_python_runtime") == 1
    assert _read("verify.sh").count("validate_offline_python_runtime") == 1

    fake_common = tmp_path / "common.sh"
    python_path = _posix_path(Path(sys.executable))
    fake_common.write_text(
        common.replace(
            'readonly VIDEOINSIGHT_OFFLINE_PYTHON="$VIDEOINSIGHT_ROOT/python/3.12.13/bin/python3.12"',
            f'readonly VIDEOINSIGHT_OFFLINE_PYTHON="{python_path}"',
        ),
        encoding="utf-8",
        newline="\n",
    )
    result = _run_common_file_function(
        fake_common,
        """
VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=1
export PYTHONPATH=/tmp/poison
run_trusted_offline_python -c '
import os, sys
assert "PYTHONPATH" not in os.environ
assert sys.flags.isolated == 1
assert sys.flags.no_site == 1
assert sys.flags.dont_write_bytecode == 1
assert sys.flags.utf8_mode == 1
'
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_offline_python_archive_provenance_rebuilds_clean_tree_manifest() -> None:
    archive_manifest = _read("offline-python-archive.sha256").splitlines()
    expected_archive_sha = (
        "506191be3ee7bd190a8834dcdc1b3bc70aab50608deccc711935aa007239cabd"
    )
    archive_name = (
        "cpython-3.12.13+20260807-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
    )
    historical_url = (
        "https://releases.astral.sh/github/python-build-standalone/releases/"
        "download/20260807/cpython-3.12.13%2B20260807-x86_64-unknown-linux-"
        "gnu-install_only_stripped.tar.gz"
    )
    assert archive_manifest == [
        f"{expected_archive_sha}  {archive_name}",
        "34163738  bytes",
        f"{historical_url}  source",
    ]
    common = _read("common.sh")
    repair = _read("normalize_offline_python_runtime.sh")
    readme = _read("README.md")
    for evidence in (archive_name, expected_archive_sha, historical_url, "34163738"):
        assert evidence in common + repair
        assert evidence in readme
    assert "astral-sh/python-build-standalone" in readme
    assert "GitHub tag `20260807`" in readme

    source_archive = REPOSITORY_ROOT / "work" / "server-deploy-xmt" / archive_name
    if not source_archive.is_file():
        return
    assert source_archive.stat().st_size == 34163738
    archive_digest = hashlib.sha256()
    with source_archive.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            archive_digest.update(chunk)
    assert archive_digest.hexdigest() == expected_archive_sha

    records: dict[str, tuple[str, int, str]] = {}
    directories: set[str] = set()
    link_destinations: list[str] = []
    with tarfile.open(source_archive, "r:gz") as archive:
        members = archive.getmembers()
        assert len(members) == 4533
        for member in members:
            path = PurePosixPath(member.name)
            assert path.parts[0] == "python"
            relative = PurePosixPath(*path.parts[1:])
            assert relative.parts and ".." not in relative.parts
            relative_text = relative.as_posix()
            assert relative_text not in records
            for parent in relative.parents:
                if parent == PurePosixPath("."):
                    break
                directories.add(parent.as_posix())
            if member.isreg():
                payload = archive.extractfile(member)
                assert payload is not None
                digest = hashlib.sha256()
                while chunk := payload.read(1024 * 1024):
                    digest.update(chunk)
                records[relative_text] = (
                    "f",
                    member.mode & ~0o022,
                    digest.hexdigest(),
                )
            elif member.issym():
                assert not posixpath.isabs(member.linkname)
                destination = posixpath.normpath(
                    posixpath.join(relative.parent.as_posix(), member.linkname)
                )
                assert destination != ".." and not destination.startswith("../")
                link_destinations.append(destination)
                records[relative_text] = ("l", 0o777, member.linkname)
            else:
                raise AssertionError(f"unexpected archive member: {member.name}")
    for directory in directories:
        assert directory not in records
        records[directory] = ("d", 0o755, "")
    assert all(destination in records for destination in link_destinations)

    aggregate = hashlib.sha256(b"d\t.\t0755\t0\t0\t\n")
    for path in sorted(records, key=lambda value: value.encode("utf-8")):
        kind, mode, value = records[path]
        aggregate.update(f"{kind}\t{path}\t{mode:04o}\t0\t0\t{value}\n".encode("utf-8"))
    assert len(records) == 4728
    assert sum(kind == "f" for kind, _, _ in records.values()) == 3485
    assert sum(kind == "l" for kind, _, _ in records.values()) == 1048
    assert aggregate.hexdigest() == (
        "f4446ac8e57f0a85d2bd0851fc05acb0cd5e8137f6752df428116ddc8e4fab01"
    )


def test_offline_python_repair_transaction_is_fail_closed_and_reproducible() -> None:
    common = _read("common.sh")
    repair = _read("normalize_offline_python_runtime.sh")
    legacy_manifest = _read("offline-python-legacy-drift-tree.sha256").splitlines()
    assert legacy_manifest == [
        "3f3407b97c487aaf0dedd632590fd7731486675cf77000827918274bab07b8b0  "
        "videoinsight-offline-python-legacy-drift-tree-v1",
        "4949  descendants",
        "3683  regular-files",
        "1048  symlinks",
    ]
    assert "必须先执行 normalize_offline_python_runtime.sh" in common
    assert "open_native_release_lock_without_runtime_validation" in repair
    assert "open_native_release_lock\n" not in repair
    assert "VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=1" not in repair
    assert repair.count("VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=0") >= 2
    assert (
        'readonly DESCRIPTOR_TEMP="$VIDEOINSIGHT_ROOT/state/'
        '.offline-python-runtime-repair.new"' in repair
    )
    assert 'readonly SYSTEM_TAR="/usr/bin/tar"' in repair
    assert "--strip-components=1" in repair
    assert "--no-same-owner --no-same-permissions" in repair
    assert "reject_mount_at_or_below" in repair
    assert 'find "$EXTRACT_ROOT" -xdev -type d -exec chmod 0755' in repair
    assert 'find "$EXTRACT_ROOT" -xdev -type f -exec chmod go-w' in repair
    main = repair[repair.index("main()") :]
    assert main.index("discard_uncommitted_descriptor_temp") < main.index(
        "descriptor_state=$(repair_descriptor_state)"
    )
    assert main.index("validate_official_archive") < main.index("build_clean_stage")
    prepared = main.index("write_repair_descriptor prepared")
    prepare_branch = main.index('if [[ "$action" == "prepare" ]]')
    transaction = main.index("TRANSACTION_STARTED=1", prepare_branch)
    old_to_backup = main.index(
        'durable_rename "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT" "$LEGACY_BACKUP"'
    )
    clean_to_live = main.index(
        'durable_rename "$CLEAN_STAGE" "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT"'
    )
    clean_gate = main.index("validate_repair_service_start_binding", clean_to_live)
    first_new_start = main.index('systemctl start "$VIDEOINSIGHT_SERVICE"', clean_gate)
    active = main.index("write_repair_descriptor active", first_new_start)
    first_stop = main.index(
        'stop_repair_service_fail_closed "离线 Python 原子替换前停止控制层"'
    )
    post_health_clean_gate = main.index(
        "revalidate_clean_runtime_after_service_health", first_new_start
    )
    assert (
        transaction < prepared < first_stop < old_to_backup < clean_to_live < clean_gate
    )
    assert clean_gate < first_new_start < post_health_clean_gate < active

    restore = repair.split("restore_legacy_runtime() {", 1)[1].split(
        "\n}\n\nfinish()", 1
    )[0]
    assert (
        'durable_rename "$LEGACY_BACKUP" "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT"' in restore
    )
    assert "systemctl start" not in restore
    assert "repair_health_check_without_python" not in restore
    assert "validate_offline_python_runtime" not in restore
    assert "固定服务保持停止" in restore
    assert "restore_legacy_runtime || true" in repair
    assert "active clean runtime 退出健康失败后的停止" in repair


def test_offline_python_repair_binds_unit_and_trees_around_every_service_action() -> (
    None
):
    common = _read("common.sh")
    repair = _read("normalize_offline_python_runtime.sh")
    main = repair[repair.index("main()") :]
    original_sha = "98e7841399dcb1cb5654225bbfde65735fe0dc8cc3b56c0bd2336ad6f116a994"
    bridge_sha = "839ad0602fd054d3d3d2eb5574c6184d4e6ceafa2d381d06f7a7a8dffe6aaf84"
    for evidence in (
        original_sha,
        bridge_sha,
        "NeedDaemonReload",
        "PYTHONDONTWRITEBYTECODE=1",
        "81c846d367b74d087fd845372f673a78011cdd0f48f2952c91a98f7e22ab2dc6",
        "bfd8ba051af78d812c9b39c1679b7843c14196cea77ee7457b8599e8797368da",
    ):
        assert evidence in repair + common
    assert '[[ "$need_daemon_reload" == "no" ]]' in repair
    assert '[[ "$need_daemon_reload" == "no" ]]' in common
    assert '[[ "$effective_environment" == "$expected_environment" ]]' in repair
    assert '[[ "$effective_environment" == "$expected_environment" ]]' in common

    lock = main.index("open_native_release_lock_without_runtime_validation")
    first_binding = main.index("validate_repair_service_unit_for_control", lock)
    temp_cleanup = main.index("discard_uncommitted_descriptor_temp")
    first_state_read = main.index("descriptor_state=$(repair_descriptor_state)")
    assert lock < first_binding < temp_cleanup < first_state_read

    # The repair script has exactly one path to the common stop primitive. Every
    # normal, recovery, mixed-state, and EXIT stop call must pass through the
    # exact-unit revalidation wrapper first.
    assert repair.count("stop_control_plane_fail_closed") == 1
    assert 'stop_control_plane_fail_closed "$reason"' in repair
    assert not re.search(r"^[ \t]*systemctl[ \t]+stop\b", repair, re.MULTILINE)
    wrapper = repair.split("stop_repair_service_fail_closed() {", 1)[1].split(
        "\n}\n", 1
    )[0]
    assert wrapper.index("validate_repair_service_unit_for_control") < wrapper.index(
        "stop_control_plane_fail_closed"
    )

    for start in re.finditer(
        r'^[ \t]*systemctl start "\$VIDEOINSIGHT_SERVICE"', main, re.MULTILINE
    ):
        preceding = main[: start.start()]
        assert preceding.rfind(
            "validate_repair_service_start_binding"
        ) > preceding.rfind("write_repair_descriptor active")

    first_new_start = main.index(
        'systemctl start "$VIDEOINSIGHT_SERVICE"',
        main.index('durable_rename "$CLEAN_STAGE" "$VIDEOINSIGHT_OFFLINE_PYTHON_ROOT"'),
    )
    health = main.index('health_check ""', first_new_start)
    post_health_gate = main.index(
        "revalidate_clean_runtime_after_service_health", health
    )
    active = main.index("write_repair_descriptor active", post_health_gate)
    assert first_new_start < health < post_health_gate < active


@pytest.mark.parametrize(
    ("scenario", "expected_rc"),
    [
        ("ok", 0),
        ("unit-sha", 1),
        ("drop-in", 1),
        ("daemon-reload", 1),
        ("environment", 1),
        ("exec-start", 1),
    ],
)
def test_offline_python_repair_original_unit_binding_rejects_effective_drift(
    scenario: str, expected_rc: int
) -> None:
    script_path = NATIVE_ROOT / "normalize_offline_python_runtime.sh"
    shell = r"""
source "$1"
scenario="$2"
validate_root_file() { :; }
validate_audited_original_current_binding() { :; }
sha256sum() {
  if [[ "$scenario" == "unit-sha" ]]; then
    printf '%064d  mocked\n' 0
  else
    printf '%s  mocked\n' "$AUDITED_ORIGINAL_UNIT_SHA256"
  fi
}
readlink() {
  if [[ "$1" == "--" ]]; then
    printf '%s\n' "$AUDITED_ORIGINAL_CURRENT_LINK"
  else
    printf '%s\n' "$AUDITED_APPLICATION_ROOT"
  fi
}
grep() { printf '1\n'; }
sed() {
  if [[ "$#" -eq 3 && "$3" == "$VIDEOINSIGHT_UNIT_PATH" ]]; then
    printf '%s\n' "$AUDITED_INTERPRETER_ROOT/bin/uvicorn project.backend.app.control_plane:app --host 127.0.0.1 --port 18080 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1"
    return 0
  fi
  while IFS= read -r line; do printf '%s\n' "${line#*=}"; done
}
systemctl() {
  case "${2#--property=}" in
    FragmentPath) printf 'FragmentPath=%s\n' "$VIDEOINSIGHT_UNIT_PATH" ;;
    DropInPaths)
      if [[ "$scenario" == "drop-in" ]]; then
        printf 'DropInPaths=/etc/systemd/system/videoinsight-control-plane.service.d/evil.conf\n'
      else
        printf 'DropInPaths=\n'
      fi
      ;;
    NeedDaemonReload)
      [[ "$scenario" == "daemon-reload" ]] && value=yes || value=no
      printf 'NeedDaemonReload=%s\n' "$value"
      ;;
    WorkingDirectory) printf 'WorkingDirectory=%s\n' "$VIDEOINSIGHT_CURRENT" ;;
    User) printf 'User=%s\n' "$VIDEOINSIGHT_SERVICE_USER" ;;
    Group) printf 'Group=%s\n' "$VIDEOINSIGHT_SERVICE_GROUP" ;;
    Environment)
      if [[ "$scenario" == "environment" ]]; then
        printf 'Environment=PYTHONDONTWRITEBYTECODE=0\n'
      else
        printf 'Environment=PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VIDEOINSIGHT_RUNTIME_ROOT=%s AUTH_SESSION_DATABASE_PATH=%s/data/video_intelligence.db\n' "$VIDEOINSIGHT_RUNTIME_ROOT" "$VIDEOINSIGHT_RUNTIME_ROOT"
      fi
      ;;
    ExecStart)
      if [[ "$scenario" == "exec-start" ]]; then
        path=/bin/false
        argv=/bin/false
      else
        path="$AUDITED_INTERPRETER_ROOT/bin/uvicorn"
        argv="$path project.backend.app.control_plane:app --host 127.0.0.1 --port 18080 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1"
      fi
      printf 'ExecStart={ path=%s ; argv[]=%s ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }\n' "$path" "$argv"
      ;;
  esac
}
validate_audited_original_service_unit_binding
"""
    result = subprocess.run(
        [_bash(), "-c", shell, "repair-binding-test", str(script_path), scenario],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if expected_rc == 0:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("binding_kind", "expected_marker"),
    [("original", "original-trees"), ("strict-bridge", "active-adoption")],
)
def test_offline_python_repair_start_binding_accepts_only_audited_positive_paths(
    binding_kind: str, expected_marker: str
) -> None:
    script_path = NATIVE_ROOT / "normalize_offline_python_runtime.sh"
    shell = r"""
source "$1"
kind="$2"
validate_offline_python_runtime() {
  [[ "$VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED" -eq 0 ]] || return 9
  VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=1
}
validate_repair_service_unit_for_control() {
  REPAIR_SERVICE_BINDING_KIND="$kind"
}
validate_audited_legacy_trees_for_restart() { MARKER=original-trees; }
validate_active_legacy_adoption() { MARKER=active-adoption; }
VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=1
validate_repair_service_start_binding
printf '%s\n' "$MARKER"
"""
    result = subprocess.run(
        [
            _bash(),
            "-c",
            shell,
            "repair-start-binding-test",
            str(script_path),
            binding_kind,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == expected_marker


def test_offline_python_repair_unknown_unit_never_reaches_stop_primitive() -> None:
    script_path = NATIVE_ROOT / "normalize_offline_python_runtime.sh"
    shell = r"""
source "$1"
validate_repair_service_unit_for_control() { return 17; }
stop_control_plane_fail_closed() { printf 'STOP-CALLED\n'; return 0; }
stop_repair_service_fail_closed test-reason
"""
    result = subprocess.run(
        [_bash(), "-c", shell, "repair-stop-binding-test", str(script_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode != 0
    assert "STOP-CALLED" not in result.stdout + result.stderr
    assert "systemctl stop" in result.stderr


@pytest.mark.parametrize(("tree_drift", "expected_rc"), [("0", 0), ("1", 1)])
def test_offline_python_repair_recomputes_exact_legacy_trees_before_restart(
    tree_drift: str, expected_rc: int
) -> None:
    script_path = NATIVE_ROOT / "normalize_offline_python_runtime.sh"
    shell = r"""
source "$1"
tree_drift="$2"
SERVICE_UID=123
SERVICE_GID=456
validate_root_file() { :; }
run_trusted_offline_python() {
  printf '175\n'
  if [[ "$tree_drift" == "1" ]]; then
    printf '%064d\n' 0
  else
    printf '%s\n' "$AUDITED_APPLICATION_TREE_SHA256"
  fi
  printf '1594\n%s\n' "$AUDITED_INTERPRETER_TREE_SHA256"
}
validate_audited_legacy_trees_for_restart
"""
    result = subprocess.run(
        [
            _bash(),
            "-c",
            shell,
            "repair-tree-binding-test",
            str(script_path),
            tree_drift,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if expected_rc == 0:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr


@pytest.mark.parametrize(("polluted", "expected_rc"), [("0", 0), ("1", 1)])
def test_offline_python_repair_post_health_gate_clears_cached_validation(
    polluted: str, expected_rc: int
) -> None:
    script_path = NATIVE_ROOT / "normalize_offline_python_runtime.sh"
    shell = r"""
source "$1"
polluted="$2"
validate_offline_python_runtime() {
  printf 'observed-flag=%s\n' "$VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED"
  [[ "$VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED" -eq 0 ]] || return 8
  [[ "$polluted" == "0" ]] || return 7
  VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=1
}
VIDEOINSIGHT_OFFLINE_PYTHON_VALIDATED=1
revalidate_clean_runtime_after_service_health
"""
    result = subprocess.run(
        [_bash(), "-c", shell, "repair-post-health-test", str(script_path), polluted],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "observed-flag=0" in result.stdout
    if expected_rc == 0:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr


def test_offline_python_repair_crash_states_are_behaviorally_classified() -> None:
    script_path = NATIVE_ROOT / "normalize_offline_python_runtime.sh"
    cases = (
        (("missing", "legacy", "missing", "missing"), "prepare", 0),
        (("missing", "legacy", "missing", "clean"), "prepare", 0),
        (("prepared", "legacy", "missing", "clean"), "replace", 0),
        (("prepared", "missing", "legacy", "clean"), "install-clean", 0),
        (("prepared", "clean", "legacy", "missing"), "finalize", 0),
        (("active", "clean", "legacy", "missing"), "done", 0),
        (("prepared", "legacy", "legacy", "clean"), "invalid", 1),
        (("active", "legacy", "missing", "clean"), "invalid", 1),
    )
    for arguments, expected, expected_rc in cases:
        result = subprocess.run(
            [
                _bash(),
                "-c",
                'source "$1"; shift; classify_repair_state "$@"',
                "repair-state-test",
                str(script_path),
                *arguments,
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        assert result.returncode == expected_rc, result.stdout + result.stderr
        assert result.stdout.strip() == expected


def test_readme_requires_target_bash_42_parse_gate_before_execution() -> None:
    readme = _read("README.md")
    assert "cd /opt/videoinsight-control-plane/tools/native-systemd || exit 1" in readme
    assert "TARGET_VERSION='<正式构建生成的版本>'" in readme
    parse_gate = readme.index("for script in common.sh install_unit.sh")
    runtime_repair = readme.index(
        "/bin/bash normalize_offline_python_runtime.sh", parse_gate
    )
    second_parse_gate = readme.index(
        "for script in common.sh install_unit.sh", runtime_repair
    )
    normalize = readme.index("/bin/bash normalize_legacy_unit.sh", parse_gate)
    assert parse_gate < runtime_repair < second_parse_gate < normalize
    assert '/bin/bash -n "$script" || exit 1' in readme[parse_gate:runtime_repair]
    assert '/bin/bash -n "$script" || exit 1' in readme[second_parse_gate:normalize]
    for name in SHELL_SCRIPTS:
        assert name in readme[parse_gate:normalize]
    assert "Bash 4.2" in readme
    assert '/bin/bash upgrade.sh "$TARGET_VERSION"' in readme
    assert '/bin/bash verify.sh "$TARGET_VERSION"' in readme
    assert not re.search(
        r"/bin/bash (?:upgrade|verify)\.sh [0-9]+\.[0-9]+\.[0-9]+", readme
    )


def test_shells_never_execute_offline_python_outside_trusted_wrappers() -> None:
    direct_pattern = re.compile(r'"\$VIDEOINSIGHT_OFFLINE_PYTHON"\s+-[A-Za-z]')
    occurrences: list[tuple[str, str]] = []
    for name in SHELL_SCRIPTS:
        for line in _read(name).splitlines():
            if direct_pattern.search(line):
                occurrences.append((name, line.strip()))
    assert occurrences == [
        (
            "common.sh",
            '"$VIDEOINSIGHT_OFFLINE_PYTHON" -B -I -S -X utf8 "$@"',
        ),
        (
            "common.sh",
            '"$VIDEOINSIGHT_OFFLINE_PYTHON" -B -I -S -X utf8 "$@"',
        ),
        (
            "common.sh",
            '"$VIDEOINSIGHT_OFFLINE_PYTHON" -B -I -S -X utf8 "$@"',
        ),
    ]


def test_adoption_descriptor_rejects_extra_fields_and_inactive_status(
    tmp_path: Path,
) -> None:
    module = _legacy_adoption_descriptor()
    root = tmp_path / "control"
    root.mkdir()
    descriptor = tmp_path / "adoption.json"
    module.create_descriptor(
        descriptor,
        root=root,
        application_version="0.2.6",
        interpreter_version="0.2.4",
        original_current_link=str(root.resolve() / "releases" / "0.2.6" / "app"),
        original_unit_sha256="1" * 64,
        original_unit_backup_name="videoinsight-control-plane.service.legacy-evidence-"
        + "1" * 64,
        bridge_unit_sha256="2" * 64,
        offline_python_sha256="3" * 64,
        application_file_count=175,
        application_tree_sha256="4" * 64,
        interpreter_tree_entry_count=1594,
        interpreter_tree_sha256="5" * 64,
    )
    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    payload["status"] = "inactive"
    descriptor.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="prepared/active"):
        module._load_record(descriptor)
    payload["status"] = "active"
    payload["unexpected"] = True
    descriptor.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="额外字段"):
        module._load_record(descriptor)


def test_adoption_prepared_promotion_is_no_clobber_and_changes_only_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _legacy_adoption_descriptor()
    root = tmp_path / "control"
    root.mkdir()
    prepared = tmp_path / "prepared.json"
    active = tmp_path / "active.json"
    module.create_descriptor(
        prepared,
        root=root,
        application_version="0.2.6",
        interpreter_version="0.2.4",
        original_current_link=str(root.resolve() / "releases" / "0.2.6" / "app"),
        original_unit_sha256="1" * 64,
        original_unit_backup_name="videoinsight-control-plane.service.legacy-evidence-"
        + "1" * 64,
        bridge_unit_sha256="2" * 64,
        offline_python_sha256="3" * 64,
        application_file_count=175,
        application_tree_sha256="4" * 64,
        interpreter_tree_entry_count=1594,
        interpreter_tree_sha256="5" * 64,
    )
    monkeypatch.setattr(
        module, "_validate_record_paths", lambda *_args, **_kwargs: None
    )
    module.promote_descriptor(
        prepared,
        active,
        root=root,
        unit_path=tmp_path / "unit",
        offline_python=tmp_path / "python",
        service_uid=996,
        service_gid=994,
    )
    prepared_payload = json.loads(prepared.read_text(encoding="utf-8"))
    active_payload = json.loads(active.read_text(encoding="utf-8"))
    assert prepared_payload["status"] == "prepared"
    assert active_payload.pop("status") == "active"
    prepared_payload.pop("status")
    assert active_payload == prepared_payload

    active.write_text("sentinel", encoding="utf-8")
    with pytest.raises(FileExistsError):
        module.promote_descriptor(
            prepared,
            active,
            root=root,
            unit_path=tmp_path / "unit",
            offline_python=tmp_path / "python",
            service_uid=996,
            service_gid=994,
        )
    assert active.read_text(encoding="utf-8") == "sentinel"


def test_adoption_evidence_copy_uses_one_source_fd_and_never_clobbers(
    tmp_path: Path,
) -> None:
    module = _legacy_adoption_descriptor()
    source = tmp_path / "unit"
    destination = tmp_path / "evidence"
    source.write_bytes(b"fixed original unit")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    module.copy_evidence(source, destination, expected)
    assert destination.read_bytes() == source.read_bytes()

    destination.write_bytes(b"sentinel")
    with pytest.raises(FileExistsError):
        module.copy_evidence(source, destination, expected)
    assert destination.read_bytes() == b"sentinel"

    destination.unlink()
    with pytest.raises(ValueError, match="SHA256"):
        module.copy_evidence(source, destination, "0" * 64)
    assert not destination.exists()


def _write_valid_archive(
    path: Path,
    *,
    version: str = "0.2.7",
    extra_name: str | None = None,
    extra_content: bytes | str = "unsafe",
    crlf_shell: bool = False,
    requirements_content: str | None = None,
    lock_content: str | None = None,
    bootstrap_content: str | None = None,
    omit_name: str | None = None,
) -> None:
    required = {
        "release_version.txt",
        "project/backend/app/control_plane.py",
        "project/backend/app/api/v1/provider_release_acceptance.py",
        "project/backend/app/release_version.py",
        "deploy/control-plane/bootstrap/avatar_assets/shuying_cloud.json",
        "deploy/control-plane/requirements.lock",
        "deploy/control-plane/requirements.txt",
        "deploy/control-plane/validate_env.sh",
        "deploy/control-plane/backup_control_plane.py",
        "deploy/control-plane/restore_control_plane.py",
        *{
            f"deploy/control-plane/native-systemd/{item.name}"
            for item in NATIVE_ROOT.iterdir()
            if item.is_file()
        },
    }
    if omit_name is not None:
        required.discard(omit_name)
    with zipfile.ZipFile(path, "w") as archive:
        for name in sorted(required):
            if name == "release_version.txt":
                content = version
            elif name == "deploy/control-plane/requirements.txt":
                content = requirements_content or (REPOSITORY_ROOT / name).read_text(
                    encoding="utf-8"
                )
            elif name == "deploy/control-plane/requirements.lock":
                content = (REPOSITORY_ROOT / name).read_text(encoding="utf-8")
                if lock_content is not None:
                    content = lock_content
            elif name == "deploy/control-plane/native-systemd/wheelhouse.sha256":
                content = _read("wheelhouse.sha256")
            elif (
                name
                == "deploy/control-plane/bootstrap/avatar_assets/shuying_cloud.json"
            ):
                content = bootstrap_content or (REPOSITORY_ROOT / name).read_text(
                    encoding="utf-8"
                )
            else:
                content = (
                    "#!/bin/sh\r\n" if crlf_shell and name.endswith(".sh") else "safe"
                )
            archive.writestr(name, content)
        for index in range(15):
            archive.writestr(f"src/safe_{index}.py", "safe")
        if extra_name:
            archive.writestr(extra_name, extra_content)


def test_archive_validator_extracts_a_safe_bundle(tmp_path: Path) -> None:
    validator = _archive_validator()
    archive = tmp_path / "release.zip"
    target = tmp_path / "target"
    target.mkdir()
    _write_valid_archive(archive)

    validator.extract_validated_archive(archive, target, "0.2.7")

    assert (target / "project/backend/app/control_plane.py").read_text() == "safe"
    assert (target / "deploy/control-plane/requirements.txt").is_file()
    assert (target / "release_version.txt").read_bytes() == b"0.2.7"


@pytest.mark.parametrize(
    "required_native_file",
    (
        "legacy_adoption_descriptor.py",
        "normalize_offline_python_runtime.sh",
        "normalize_legacy_unit.sh",
        "media-tools.sha256",
        "offline-python-archive.sha256",
        "offline-python-legacy-drift-tree.sha256",
        "offline-python-tree.sha256",
    ),
)
def test_archive_validator_requires_all_adoption_and_runtime_gate_files(
    tmp_path: Path, required_native_file: str
) -> None:
    validator = _archive_validator()
    archive = tmp_path / "release.zip"
    target = tmp_path / "target"
    target.mkdir()
    _write_valid_archive(
        archive,
        omit_name=f"deploy/control-plane/native-systemd/{required_native_file}",
    )
    with pytest.raises(ValueError, match="缺少控制层必要文件"):
        validator.extract_validated_archive(archive, target, "0.2.7")
    assert not any(target.iterdir())


@pytest.mark.parametrize(
    "unsafe_name",
    (
        "../outside.py",
        "/absolute.py",
        "deploy/control-plane/.env",
        "data/video_intelligence.db",
        "outputs/leak.mp4",
    ),
)
def test_archive_validator_rejects_unsafe_members_before_extracting(
    tmp_path: Path, unsafe_name: str
) -> None:
    validator = _archive_validator()
    archive = tmp_path / "release.zip"
    target = tmp_path / "target"
    target.mkdir()
    _write_valid_archive(archive, extra_name=unsafe_name)

    with pytest.raises(ValueError):
        validator.extract_validated_archive(archive, target, "0.2.7")

    assert not any(target.iterdir())


def test_archive_validator_rejects_symlinks_before_extracting(tmp_path: Path) -> None:
    validator = _archive_validator()
    archive = tmp_path / "release.zip"
    target = tmp_path / "target"
    target.mkdir()
    _write_valid_archive(archive)
    with zipfile.ZipFile(archive, "a") as bundle:
        symlink = zipfile.ZipInfo("src/link.py")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(symlink, "target.py")

    with pytest.raises(ValueError, match="符号链接"):
        validator.extract_validated_archive(archive, target, "0.2.7")

    assert not any(target.iterdir())


def test_archive_validator_rejects_crlf_shell_scripts(tmp_path: Path) -> None:
    validator = _archive_validator()
    archive = tmp_path / "release.zip"
    target = tmp_path / "target"
    target.mkdir()
    _write_valid_archive(archive, crlf_shell=True)

    with pytest.raises(ValueError, match="LF 换行"):
        validator.extract_validated_archive(archive, target, "0.2.7")

    assert not any(target.iterdir())


def test_archive_validator_rejects_renamed_old_release_before_extracting(
    tmp_path: Path,
) -> None:
    validator = _archive_validator()
    archive = tmp_path / "VideoInsight-control-plane-0.2.8.zip"
    target = tmp_path / "target"
    target.mkdir()
    _write_valid_archive(archive, version="0.2.7")

    with pytest.raises(ValueError, match="版本标识"):
        validator.extract_validated_archive(archive, target, "0.2.8")

    assert not any(target.iterdir())


def test_archive_validator_rejects_unpinned_or_rehashed_dependencies(
    tmp_path: Path,
) -> None:
    validator = _archive_validator()
    archive = tmp_path / "release.zip"
    target = tmp_path / "target"
    target.mkdir()
    _write_valid_archive(
        archive,
        requirements_content="fastapi>=0.139\n",
    )

    with pytest.raises(ValueError, match="精确 =="):
        validator.extract_validated_archive(archive, target, "0.2.7")
    assert not any(target.iterdir())

    archive.unlink()
    original_lock = (
        REPOSITORY_ROOT / "deploy" / "control-plane" / "requirements.lock"
    ).read_text(encoding="utf-8")
    _write_valid_archive(
        archive,
        lock_content=original_lock.replace(
            "9243213661e29250eb41368e5daa826fc017156c3b8a11440826b2e3ed376472",
            "0" * 64,
            1,
        ),
    )
    with pytest.raises(ValueError, match="wheelhouse SHA256"):
        validator.extract_validated_archive(archive, target, "0.2.7")
    assert not any(target.iterdir())


@pytest.mark.parametrize(
    "secret_name",
    (
        "config/server.key",
        "config/server.pem",
        "config/server.p12",
        "config/server.pfx",
        "config/server.jks",
        "config/server.keystore",
        "config/server.der",
        "config/server.p8",
        "config/server.ppk",
    ),
)
def test_archive_validator_rejects_private_key_extensions(
    tmp_path: Path, secret_name: str
) -> None:
    validator = _archive_validator()
    archive = tmp_path / "release.zip"
    target = tmp_path / "target"
    target.mkdir()
    _write_valid_archive(archive, extra_name=secret_name)

    with pytest.raises(ValueError, match="运行数据、密钥文件或媒体"):
        validator.extract_validated_archive(archive, target, "0.2.7")

    assert not any(target.iterdir())


@pytest.mark.parametrize(
    "marker",
    (
        b"-----BEGIN PRIVATE KEY-----",
        b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
        b"-----BEGIN RSA PRIVATE KEY-----",
        b"-----BEGIN DSA PRIVATE KEY-----",
        b"-----BEGIN EC PRIVATE KEY-----",
        b"-----BEGIN OPENSSH PRIVATE KEY-----",
        b"PuTTY-User-Key-File:",
    ),
)
def test_archive_validator_rejects_private_key_markers(
    tmp_path: Path, marker: bytes
) -> None:
    validator = _archive_validator()
    archive = tmp_path / "release.zip"
    target = tmp_path / "target"
    target.mkdir()
    _write_valid_archive(
        archive, extra_name="config/innocent.txt", extra_content=b"prefix\n" + marker
    )

    with pytest.raises(ValueError, match="私钥内容"):
        validator.extract_validated_archive(archive, target, "0.2.7")

    assert not any(target.iterdir())


def test_archive_validator_streams_large_member_and_cross_chunk_key_marker(
    tmp_path: Path,
) -> None:
    validator = _archive_validator()
    archive = tmp_path / "release.zip"
    target = tmp_path / "target"
    target.mkdir()
    marker = b"-----BEGIN OPENSSH PRIVATE KEY-----"
    old_skipped_limit = 32 * 1024 * 1024
    prefix_size = (
        old_skipped_limit + validator.SECRET_SCAN_CHUNK_BYTES - len(marker) // 2
    )
    _write_valid_archive(
        archive,
        extra_name="config/large-innocent-name.bin",
        extra_content=b"A" * prefix_size + marker + b"tail",
    )

    with pytest.raises(ValueError, match="私钥内容"):
        validator.extract_validated_archive(archive, target, "0.2.7")

    assert not any(target.iterdir())


def test_archive_validator_requires_release_acceptance_and_avatar_assets(
    tmp_path: Path,
) -> None:
    validator = _archive_validator()
    archive = tmp_path / "release.zip"
    target = tmp_path / "target"
    target.mkdir()
    _write_valid_archive(
        archive,
        omit_name="deploy/control-plane/bootstrap/avatar_assets/shuying_cloud.json",
    )

    with pytest.raises(ValueError, match="缺少控制层必要文件"):
        validator.extract_validated_archive(archive, target, "0.2.7")
    assert not any(target.iterdir())


@pytest.mark.parametrize("mutation", ("provider", "sample_path"))
def test_archive_validator_rejects_tampered_avatar_bootstrap(
    tmp_path: Path, mutation: str
) -> None:
    validator = _archive_validator()
    archive = tmp_path / "release.zip"
    target = tmp_path / "target"
    target.mkdir()
    manifest_path = (
        REPOSITORY_ROOT
        / "deploy"
        / "control-plane"
        / "bootstrap"
        / "avatar_assets"
        / "shuying_cloud.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "provider":
        payload["assets"][0]["provider_asset_id"] = "99999"
    else:
        payload["assets"][1]["sample_path"] = r"C:\private\voice.m4a"
    _write_valid_archive(
        archive,
        bootstrap_content=json.dumps(payload, ensure_ascii=False),
    )

    with pytest.raises(ValueError, match="bootstrap"):
        validator.extract_validated_archive(archive, target, "0.2.7")
    assert not any(target.iterdir())
