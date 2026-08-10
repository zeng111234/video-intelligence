from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NATIVE_ROOT = REPOSITORY_ROOT / "deploy" / "control-plane" / "native-systemd"
SHELL_SCRIPTS = (
    "common.sh",
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


def _run_common_function(
    function_call: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _bash(),
            "-c",
            f'source "$1"; {function_call}',
            "native-test",
            str(NATIVE_ROOT / "common.sh"),
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


def test_linux_release_files_are_pinned_to_lf_in_git() -> None:
    attributes = (REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert "*.sh text eol=lf" in attributes
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
    assert "Environment=PATH=/usr/local/bin:/usr/bin:/bin" in unit
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
    assert "validate_service_identity" in preflight
    assert 'real_path_under "$SCRIPT_DIR" "$VIDEOINSIGHT_ROOT"' in preflight
    assert 'validate_secure_file "$VIDEOINSIGHT_ENV_FILE" 0' in preflight
    assert 'env_gid" == "$SERVICE_GID' in preflight
    assert "8#$env_mode & 0037" in preflight
    assert "配置不得覆盖固定的 systemd 服务 PATH" in preflight
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
    assert 'readonly VIDEOINSIGHT_SERVICE_PATH="/usr/local/bin:/usr/bin:/bin"' in common
    assert 'PATH="$VIDEOINSIGHT_SERVICE_PATH" command -v "$name"' in common
    assert 'Environment=PATH="$VIDEOINSIGHT_SERVICE_PATH"' in common
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
    assert '"$VIDEOINSIGHT_OFFLINE_PYTHON" -m venv --copies' in script
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
    assert 'TMPDIR="$STAGING_TMP"' in script
    assert "PIP_CONFIG_FILE=/dev/null" in script
    assert "PIP_NO_CACHE_DIR=1" in script
    assert "PYTHONNOUSERSITE=1" in script
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
    legacy_binding = script.index(
        'validate_legacy_unit_binding "$PREVIOUS_RELEASE_ROOT"'
    )
    extraction = script.index('"$SCRIPT_DIR/validate_release_archive.py"', comparison)
    stop_service = script.index('systemctl stop "$VIDEOINSIGHT_SERVICE"', extraction)
    assert legacy_binding < comparison < extraction < stop_service
    assert 'PYTHONDONTWRITEBYTECODE=1 "$VIDEOINSIGHT_OFFLINE_PYTHON"' in script

    prepare = script.index('durable_rename "$STAGING_DIR" "$RELEASE_DIR"')
    stop = script.index('systemctl stop "$VIDEOINSIGHT_SERVICE"', prepare)
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
            "Environment=PATH=/usr/local/bin:/usr/bin:/bin\n", ""
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


def test_legacy_unit_binding_rejects_current_and_interpreter_version_mismatch(
    tmp_path: Path,
) -> None:
    expected_release = "/opt/videoinsight-control-plane/releases/0.2.4"
    unit = tmp_path / "legacy-mismatch.service"
    unit.write_text(
        _read("videoinsight-control-plane.service")
        .replace(
            "WorkingDirectory=/opt/videoinsight-control-plane/current/app",
            f"WorkingDirectory={expected_release}/app",
        )
        .replace(
            "ExecStart=/opt/videoinsight-control-plane/current/venv/bin/python",
            "ExecStart=/opt/videoinsight-control-plane/releases/0.2.3/venv/bin/python",
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = _run_common_function(
        'validate_legacy_unit_file_binding "$2" "$3"',
        str(unit),
        expected_release,
    )

    assert result.returncode != 0
    assert "ExecStart" in result.stderr

    unit.write_text(
        unit.read_text(encoding="utf-8")
        .replace(
            f"WorkingDirectory={expected_release}/app",
            "WorkingDirectory=/opt/videoinsight-control-plane/current",
        )
        .replace(
            "/releases/0.2.3/venv/bin/python",
            "/releases/0.2.4/venv/bin/python",
        ),
        encoding="utf-8",
        newline="\n",
    )
    accepted = _run_common_function(
        'validate_legacy_unit_file_binding "$2" "$3"',
        str(unit),
        expected_release,
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    invalid_current_app = unit.read_text(encoding="utf-8").replace(
        "WorkingDirectory=/opt/videoinsight-control-plane/current",
        "WorkingDirectory=/opt/videoinsight-control-plane/current/app",
    )
    unit.write_text(invalid_current_app, encoding="utf-8", newline="\n")
    rejected_current_app = _run_common_function(
        'validate_legacy_unit_file_binding "$2" "$3"',
        str(unit),
        expected_release,
    )
    assert rejected_current_app.returncode != 0
    assert "WorkingDirectory" in rejected_current_app.stderr


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
    assert "未访问外网、未调用供应商" in script


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
tool=$(readlink -f -- "$(cygpath -u "$2")")
validate_trusted_executable_path "$tool"
"""

    result = _run_common_function(function_call, str(tool))

    assert result.returncode == 0, result.stdout + result.stderr


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


def test_legacy_descriptor_binds_versions_link_snapshot_and_unit_hashes(
    tmp_path: Path,
) -> None:
    descriptor_module = _legacy_descriptor()
    root = tmp_path / "control-plane"
    backup_root = root / "backups"
    app = root / "releases" / "0.2.6" / "app"
    unit_backups = root / "state" / "unit-backups"
    descriptor_root = root / "state" / "legacy-rollbacks"
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
    descriptor = descriptor_root / f"{snapshot_name}.json"

    descriptor_module.create_descriptor(
        descriptor,
        root=root,
        source_version="0.2.6",
        upgraded_to_version="0.2.7",
        original_current_link="releases/0.2.6/app",
        unit_backup_name=unit_name,
        snapshot_name=snapshot_name,
        snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        unit_backup_sha256=hashlib.sha256(unit.read_bytes()).hexdigest(),
    )

    assert descriptor_module.validate_descriptor(
        descriptor,
        root=root,
        backup_root=backup_root,
        expected_source_version="0.2.6",
        expected_upgraded_to_version="0.2.7",
        expected_snapshot_name=snapshot_name,
    ) == ("releases/0.2.6/app", unit_name)

    snapshot.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="快照哈希"):
        descriptor_module.validate_descriptor(
            descriptor,
            root=root,
            backup_root=backup_root,
            expected_source_version="0.2.6",
            expected_upgraded_to_version="0.2.7",
            expected_snapshot_name=snapshot_name,
        )


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
