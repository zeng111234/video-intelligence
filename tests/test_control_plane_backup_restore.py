from __future__ import annotations

import errno
import json
import os
import sqlite3
import stat
import subprocess
import sys
import zipfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = REPOSITORY_ROOT / "deploy/control-plane/backup_control_plane.py"
RESTORE_SCRIPT = REPOSITORY_ROOT / "deploy/control-plane/restore_control_plane.py"


def _run_script(
    script: Path, argument: str, env: dict[str, str], *extra_arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), argument, *extra_arguments],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _load_restore_module():
    spec = spec_from_file_location(
        "fault_injected_restore_control_plane", RESTORE_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _load_backup_module():
    spec = spec_from_file_location("fault_injected_backup_control_plane", BACKUP_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _create_database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records(value) VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _tree_contents(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): None if path.is_dir() else path.read_bytes()
        for path in sorted(root.rglob("*"))
    }


def _backup_fixture(tmp_path: Path) -> tuple[Path, Path]:
    source_runtime = tmp_path / "source-runtime"
    source_data = source_runtime / "data"
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    _create_database(source_data / "video_intelligence.db", "snapshot")
    asset = source_data / "avatar_assets" / "shared.json"
    asset.parent.mkdir()
    asset.write_text('{"status":"snapshot"}', encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "VIDEOINSIGHT_RUNTIME_ROOT": str(source_runtime),
            "VIDEOINSIGHT_BACKUP_ROOT": str(backup_root),
        }
    )
    result = _run_script(BACKUP_SCRIPT, "fault-test.zip", env, "0.2.6")
    assert result.returncode == 0, result.stderr
    return backup_root, backup_root / "fault-test.zip"


def _current_data(tmp_path: Path) -> Path:
    data_root = tmp_path / "runtime" / "data"
    _create_database(data_root / "video_intelligence.db", "current")
    keep = data_root / "must-stay.txt"
    keep.write_bytes(b"current-data")
    return data_root


def _run_loaded_restore(module, monkeypatch, data_root: Path, backup_root: Path) -> int:
    monkeypatch.setattr(module, "DATA_ROOT", data_root)
    monkeypatch.setattr(module, "BACKUP_ROOT", backup_root.resolve())
    monkeypatch.setattr(module, "SERVICE_UID", -1)
    monkeypatch.setattr(module, "SERVICE_GID", -1)
    monkeypatch.setattr(
        sys,
        "argv",
        ["restore_control_plane.py", "fault-test.zip", "0.2.6"],
    )
    return module.main()


def test_backup_and_restore_use_configured_native_paths(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    data_root = runtime_root / "data"
    backup_root = tmp_path / "backups"
    data_root.mkdir(parents=True)
    backup_root.mkdir()

    database = data_root / "video_intelligence.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE records (value TEXT NOT NULL);
        INSERT INTO records (value) VALUES ('original');
        CREATE TABLE auth_sessions (token TEXT NOT NULL);
        INSERT INTO auth_sessions (token) VALUES ('must-be-revoked');
        """
    )
    connection.commit()
    connection.close()
    asset = data_root / "avatar_assets" / "shared.json"
    asset.parent.mkdir()
    asset.write_text('{"status":"ready"}', encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "VIDEOINSIGHT_RUNTIME_ROOT": str(runtime_root),
            "VIDEOINSIGHT_BACKUP_ROOT": str(backup_root),
            "VIDEOINSIGHT_SERVICE_UID": "-1",
            "VIDEOINSIGHT_SERVICE_GID": "-1",
        }
    )
    backup_name = "native-paths.zip"
    backup = _run_script(BACKUP_SCRIPT, backup_name, env, "0.2.6")
    assert backup.returncode == 0, backup.stderr

    backup_path = backup_root / backup_name
    with zipfile.ZipFile(backup_path) as archive:
        assert "data/video_intelligence.db" in archive.namelist()
        assert "data/avatar_assets/shared.json" in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "videoinsight-control-plane-backup-v1"
        assert manifest["source_release_version"] == "0.2.6"

    connection = sqlite3.connect(database)
    connection.execute("UPDATE records SET value = 'changed'")
    connection.commit()
    connection.close()
    asset.write_text('{"status":"changed"}', encoding="utf-8")

    restore = _run_script(RESTORE_SCRIPT, backup_name, env, "0.2.6")
    assert restore.returncode == 0, restore.stderr

    connection = sqlite3.connect(database)
    assert connection.execute("SELECT value FROM records").fetchone() == ("original",)
    assert connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone() == (0,)
    connection.close()
    assert asset.read_text(encoding="utf-8") == '{"status":"ready"}'
    assert not any(
        path.name.startswith(".videoinsight-data-restore-")
        for path in runtime_root.iterdir()
    )


def test_restore_full_disk_copy_failure_keeps_original_data(
    tmp_path: Path, monkeypatch
) -> None:
    backup_root, _ = _backup_fixture(tmp_path)
    data_root = _current_data(tmp_path)
    original = _tree_contents(data_root)
    module = _load_restore_module()

    def fail_copy(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "fault injected full disk")

    monkeypatch.setattr(module.shutil, "copyfileobj", fail_copy)

    assert _run_loaded_restore(module, monkeypatch, data_root, backup_root) == 1
    assert _tree_contents(data_root) == original
    assert not any(
        path.name.startswith(".videoinsight-data-restore-")
        for path in data_root.parent.iterdir()
    )


def test_restore_permission_failure_keeps_original_data(
    tmp_path: Path, monkeypatch
) -> None:
    backup_root, _ = _backup_fixture(tmp_path)
    data_root = _current_data(tmp_path)
    original = _tree_contents(data_root)
    module = _load_restore_module()
    if module.os.name == "posix":
        calls = 0

        def fail_candidate_fchmod(_descriptor, _mode):
            nonlocal calls
            calls += 1
            raise PermissionError("fault injected fchmod")

        monkeypatch.setattr(module.os, "fchmod", fail_candidate_fchmod)
    else:
        real_chmod = module.os.chmod

        def fail_candidate_chmod(path, mode):
            if Path(path).name.startswith(".videoinsight-data-restore-"):
                raise PermissionError("fault injected chmod")
            return real_chmod(path, mode)

        monkeypatch.setattr(module.os, "chmod", fail_candidate_chmod)

    assert _run_loaded_restore(module, monkeypatch, data_root, backup_root) == 1
    assert _tree_contents(data_root) == original


def test_restore_fsync_failure_keeps_original_data(tmp_path: Path, monkeypatch) -> None:
    backup_root, _ = _backup_fixture(tmp_path)
    data_root = _current_data(tmp_path)
    original = _tree_contents(data_root)
    module = _load_restore_module()
    fsync_calls: list[Path | int] = []

    if module.os.name == "posix":

        def fail_descriptor_fsync(descriptor: int) -> None:
            fsync_calls.append(descriptor)
            raise OSError(errno.ENOSPC, "fault injected fsync")

        monkeypatch.setattr(module.os, "fsync", fail_descriptor_fsync)
    else:

        def fail_fsync(path: Path) -> None:
            fsync_calls.append(path)
            raise OSError(errno.ENOSPC, "fault injected fsync")

        monkeypatch.setattr(module, "_fsync_file", fail_fsync)

    assert _run_loaded_restore(module, monkeypatch, data_root, backup_root) == 1
    assert fsync_calls
    assert _tree_contents(data_root) == original


@pytest.mark.skipif(
    os.name != "posix",
    reason="requires POSIX dirfd and fchown semantics",
)
def test_restore_transfers_candidate_root_last_without_following_raced_symlink(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_restore_module()
    candidate = tmp_path / ".videoinsight-data-restore-race"
    nested = candidate / "nested"
    nested.mkdir(parents=True)
    victim = nested / "victim.txt"
    outside = tmp_path / "outside.txt"
    victim.write_text("candidate", encoding="utf-8")
    outside.write_text("outside", encoding="utf-8")
    outside.chmod(0o640)
    outside_before = outside.stat()
    root_inode = candidate.stat().st_ino
    real_fstat = module.os.fstat
    ownership_order: list[int] = []
    root_transferred = False
    raced = False

    def simulate_initial_root_ownership(descriptor: int):
        metadata = real_fstat(descriptor)
        if metadata.st_ino != root_inode or root_transferred:
            return metadata
        values = list(metadata)
        values[4] = 0
        values[5] = 0
        return os.stat_result(values)

    def race_after_root_transfer(_descriptor: int, _uid: int, _gid: int) -> None:
        nonlocal raced, root_transferred
        inode = real_fstat(_descriptor).st_ino
        ownership_order.append(inode)
        if inode == root_inode:
            root_transferred = True
            victim.unlink()
            victim.symlink_to(outside)
            raced = True

    monkeypatch.setattr(module, "SERVICE_UID", os.getuid())
    monkeypatch.setattr(module, "SERVICE_GID", os.getgid())
    monkeypatch.setattr(module.os, "fstat", simulate_initial_root_ownership)
    monkeypatch.setattr(module.os, "fchown", race_after_root_transfer)

    module._prepare_candidate_for_commit(candidate)

    assert raced
    assert ownership_order[-1] == root_inode
    assert victim.is_symlink()
    outside_after = outside.stat()
    assert stat.S_IMODE(outside_after.st_mode) == stat.S_IMODE(outside_before.st_mode)
    assert outside_after.st_uid == outside_before.st_uid
    assert outside.read_text(encoding="utf-8") == "outside"


def test_backup_native_sqlite_source_uses_pinned_data_directory_for_wal() -> None:
    source = BACKUP_SCRIPT.read_text(encoding="utf-8")

    assert (
        'pinned_path = f"/proc/self/fd/{data_root_descriptor}/{DATABASE_NAME}"'
        in source
    )
    assert 'f"file:{pinned_path}?mode=ro", uri=True' in source
    assert "dir_fd=data_root_descriptor, follow_symlinks=False" in source
    assert "descriptors_before_connect = _active_process_descriptors()" in source
    assert "_validate_sqlite_connection_inode(" in source
    assert "sqlite3.connect(str(source_db)" not in source


def test_restore_switch_failure_renames_original_data_back(
    tmp_path: Path, monkeypatch
) -> None:
    backup_root, _ = _backup_fixture(tmp_path)
    data_root = _current_data(tmp_path)
    original = _tree_contents(data_root)
    module = _load_restore_module()
    real_replace = module.os.replace
    failed = False

    def fail_candidate_switch(source, destination):
        nonlocal failed
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not failed
            and source_path.name.startswith(".videoinsight-data-restore-")
            and not source_path.name.endswith("-previous")
            and destination_path == data_root
        ):
            failed = True
            raise OSError(errno.ENOSPC, "fault injected directory switch")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_candidate_switch)

    assert _run_loaded_restore(module, monkeypatch, data_root, backup_root) == 1
    assert failed
    assert _tree_contents(data_root) == original


def test_restore_post_switch_fsync_failure_restores_original_data(
    tmp_path: Path, monkeypatch
) -> None:
    backup_root, _ = _backup_fixture(tmp_path)
    data_root = _current_data(tmp_path)
    original = _tree_contents(data_root)
    module = _load_restore_module()
    directory_fsync_calls = 0
    failed = False

    def fail_after_candidate_switch(path: Path) -> None:
        nonlocal directory_fsync_calls, failed
        directory_fsync_calls += 1
        previous_exists = any(
            item.name.endswith("-previous") for item in data_root.parent.iterdir()
        )
        if (
            not failed
            and Path(path) == data_root.parent
            and data_root.exists()
            and previous_exists
        ):
            failed = True
            raise OSError(errno.ENOSPC, "fault injected post-switch fsync")

    monkeypatch.setattr(module, "_fsync_directory", fail_after_candidate_switch)

    assert _run_loaded_restore(module, monkeypatch, data_root, backup_root) == 1
    assert failed
    assert directory_fsync_calls >= 5
    assert _tree_contents(data_root) == original
    assert not any(
        path.name.startswith(".videoinsight-data-restore-")
        for path in data_root.parent.iterdir()
    )


def test_restore_old_tree_cleanup_failure_is_committed_with_warning(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    backup_root, _ = _backup_fixture(tmp_path)
    data_root = _current_data(tmp_path)
    module = _load_restore_module()
    real_rmtree = module.shutil.rmtree

    def fail_old_tree_cleanup(path, *args, **kwargs):
        if Path(path).name.endswith("-previous"):
            raise OSError(errno.EACCES, "fault injected old-tree cleanup")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "rmtree", fail_old_tree_cleanup)

    assert _run_loaded_restore(module, monkeypatch, data_root, backup_root) == 0
    with sqlite3.connect(data_root / "video_intelligence.db") as connection:
        assert connection.execute("SELECT value FROM records").fetchone() == (
            "snapshot",
        )
    assert not (data_root / "must-stay.txt").exists()
    assert any(path.name.endswith("-previous") for path in data_root.parent.iterdir())
    assert "WARNING:" in capsys.readouterr().err


def test_restore_final_parent_fsync_failure_is_committed_with_warning(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    backup_root, _ = _backup_fixture(tmp_path)
    data_root = _current_data(tmp_path)
    module = _load_restore_module()
    real_rmtree = module.shutil.rmtree
    cleanup_finished = False
    final_fsync_failed = False

    def track_old_tree_cleanup(path, *args, **kwargs):
        nonlocal cleanup_finished
        result = real_rmtree(path, *args, **kwargs)
        if Path(path).name.endswith("-previous"):
            cleanup_finished = True
        return result

    def fail_final_fsync(path: Path) -> None:
        nonlocal final_fsync_failed
        if (
            cleanup_finished
            and not final_fsync_failed
            and Path(path) == data_root.parent
        ):
            final_fsync_failed = True
            raise OSError(errno.EIO, "fault injected final parent fsync")

    monkeypatch.setattr(module.shutil, "rmtree", track_old_tree_cleanup)
    monkeypatch.setattr(module, "_fsync_directory", fail_final_fsync)

    assert _run_loaded_restore(module, monkeypatch, data_root, backup_root) == 0
    assert final_fsync_failed
    with sqlite3.connect(data_root / "video_intelligence.db") as connection:
        assert connection.execute("SELECT value FROM records").fetchone() == (
            "snapshot",
        )
    assert not any(
        path.name.endswith("-previous") for path in data_root.parent.iterdir()
    )
    assert "WARNING:" in capsys.readouterr().err


def test_restore_rejects_snapshot_for_another_release_without_touching_data(
    tmp_path: Path, monkeypatch
) -> None:
    backup_root, _ = _backup_fixture(tmp_path)
    data_root = _current_data(tmp_path)
    original = _tree_contents(data_root)
    module = _load_restore_module()
    monkeypatch.setattr(module, "DATA_ROOT", data_root)
    monkeypatch.setattr(module, "BACKUP_ROOT", backup_root.resolve())
    monkeypatch.setattr(
        sys, "argv", ["restore_control_plane.py", "fault-test.zip", "0.2.5"]
    )

    assert module.main() == 1
    assert _tree_contents(data_root) == original


def test_backup_fsyncs_final_archive_and_backup_directory(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_backup_module()
    data_root = tmp_path / "runtime" / "data"
    backup_root = tmp_path / "backups"
    _create_database(data_root / "video_intelligence.db", "source")
    backup_root.mkdir()
    file_calls: list[Path] = []
    directory_calls: list[Path] = []
    monkeypatch.setattr(module, "DATA_ROOT", data_root)
    monkeypatch.setattr(module, "BACKUP_ROOT", backup_root.resolve())
    monkeypatch.setattr(module, "_fsync_file", lambda path: file_calls.append(path))
    monkeypatch.setattr(
        module, "_fsync_directory", lambda path: directory_calls.append(path)
    )
    monkeypatch.setattr(
        sys, "argv", ["backup_control_plane.py", "durable.zip", "0.2.6"]
    )

    assert module.main() == 0
    assert file_calls[-1] == backup_root / "durable.zip"
    assert directory_calls == [backup_root]


def test_backup_rejects_existing_target_and_path_components(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    data_root = runtime_root / "data"
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    _create_database(data_root / "video_intelligence.db", "source")
    env = os.environ.copy()
    env.update(
        {
            "VIDEOINSIGHT_RUNTIME_ROOT": str(runtime_root),
            "VIDEOINSIGHT_BACKUP_ROOT": str(backup_root),
        }
    )
    existing = backup_root / "existing.zip"
    existing.write_bytes(b"must-not-change")

    overwrite = _run_script(BACKUP_SCRIPT, "existing.zip", env, "0.2.6")
    traversal = _run_script(BACKUP_SCRIPT, "../escaped.zip", env, "0.2.6")

    assert overwrite.returncode == 2
    assert existing.read_bytes() == b"must-not-change"
    assert traversal.returncode == 2
    assert not (tmp_path / "escaped.zip").exists()


def test_backup_atomic_no_clobber_rejects_concurrent_target_creation(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_backup_module()
    data_root = tmp_path / "runtime" / "data"
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    _create_database(data_root / "video_intelligence.db", "source")
    target = backup_root / "concurrent.zip"
    real_link = module.os.link

    def create_target_before_link(source, destination):
        Path(destination).write_bytes(b"concurrent-writer")
        return real_link(source, destination)

    monkeypatch.setattr(module, "DATA_ROOT", data_root)
    monkeypatch.setattr(module, "BACKUP_ROOT", backup_root.resolve())
    monkeypatch.setattr(module.os, "link", create_target_before_link)
    monkeypatch.setattr(
        sys, "argv", ["backup_control_plane.py", "concurrent.zip", "0.2.6"]
    )

    assert module.main() == 2
    assert target.read_bytes() == b"concurrent-writer"


def test_backup_secure_open_rejects_file_changed_to_symlink_after_scan(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_backup_module()
    data_root = tmp_path / "runtime" / "data"
    asset = data_root / "avatar_assets" / "shared.json"
    outside = tmp_path / "outside-secret.txt"
    asset.parent.mkdir(parents=True)
    asset.write_text("safe", encoding="utf-8")
    outside.write_text("must-not-be-read", encoding="utf-8")
    monkeypatch.setattr(module, "DATA_ROOT", data_root)
    files = module._regular_files()
    assert files == [asset]
    asset.unlink()
    try:
        asset.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"test environment cannot create a symlink: {exc}")

    with pytest.raises((OSError, ValueError)):
        with module._open_regular_data_file(Path("avatar_assets/shared.json")):
            pass


@pytest.mark.skipif(
    os.name != "posix", reason="native pinned SQLite source uses /proc/self/fd"
)
def test_backup_sqlite_online_backup_rejects_path_swap_before_sqlite_open(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_backup_module()
    data_root = tmp_path / "runtime" / "data"
    source = data_root / "video_intelligence.db"
    replacement = tmp_path / "replacement.db"
    moved_original = tmp_path / "pinned-original.db"
    _create_database(source, "pinned-original")
    _create_database(replacement, "replacement")
    monkeypatch.setattr(module, "DATA_ROOT", data_root)

    with module._open_pinned_database_file() as (
        source_handle,
        source_metadata,
        data_root_descriptor,
        data_root_metadata,
    ):
        os.replace(source, moved_original)
        os.replace(replacement, source)
        with pytest.raises(ValueError, match="路径与固定 inode"):
            with module._open_pinned_sqlite_source(
                source_handle,
                source_metadata,
                data_root_descriptor,
                data_root_metadata,
            ):
                pass

    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT value FROM records").fetchone() == (
            "replacement",
        )


@pytest.mark.skipif(
    os.name != "posix", reason="native pinned SQLite source uses /proc/self/fd"
)
def test_backup_sqlite_online_backup_rejects_path_swap_after_sqlite_open(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_backup_module()
    data_root = tmp_path / "runtime" / "data"
    source = data_root / "video_intelligence.db"
    replacement = tmp_path / "replacement.db"
    moved_original = tmp_path / "pinned-original.db"
    snapshot = tmp_path / "snapshot.db"
    _create_database(source, "pinned-original")
    _create_database(replacement, "replacement")
    monkeypatch.setattr(module, "DATA_ROOT", data_root)

    with module._open_pinned_database_file() as (
        source_handle,
        source_metadata,
        data_root_descriptor,
        data_root_metadata,
    ):
        destination = sqlite3.connect(snapshot)
        try:
            with pytest.raises(ValueError, match="路径与固定 inode"):
                with module._open_pinned_sqlite_source(
                    source_handle,
                    source_metadata,
                    data_root_descriptor,
                    data_root_metadata,
                ) as pinned_connection:
                    os.replace(source, moved_original)
                    os.replace(replacement, source)
                    pinned_connection.backup(destination)
        finally:
            destination.close()


@pytest.mark.skipif(
    os.name != "posix", reason="native pinned SQLite source uses /proc/self/fd"
)
def test_backup_sqlite_online_backup_rejects_transient_swap_during_connect(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_backup_module()
    data_root = tmp_path / "runtime" / "data"
    source = data_root / "video_intelligence.db"
    replacement = tmp_path / "replacement.db"
    moved_original = tmp_path / "pinned-original.db"
    _create_database(source, "pinned-original")
    _create_database(replacement, "replacement")
    monkeypatch.setattr(module, "DATA_ROOT", data_root)
    real_connect = module.sqlite3.connect

    def connect_through_transient_replacement(database, *args, **kwargs):
        if str(database).startswith("file:/proc/self/fd/"):
            os.replace(source, moved_original)
            os.replace(replacement, source)
            try:
                connection = real_connect(database, *args, **kwargs)
                connection.execute("PRAGMA schema_version").fetchone()
            finally:
                os.replace(source, replacement)
                os.replace(moved_original, source)
            return connection
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(
        module.sqlite3, "connect", connect_through_transient_replacement
    )
    with module._open_pinned_database_file() as (
        source_handle,
        source_metadata,
        data_root_descriptor,
        data_root_metadata,
    ):
        with pytest.raises(ValueError, match="SQLite 连接未持有固定"):
            with module._open_pinned_sqlite_source(
                source_handle,
                source_metadata,
                data_root_descriptor,
                data_root_metadata,
            ):
                pass

    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT value FROM records").fetchone() == (
            "pinned-original",
        )


@pytest.mark.skipif(
    os.name != "posix", reason="native pinned SQLite source uses /proc/self/fd"
)
def test_backup_sqlite_online_backup_includes_uncheckpointed_wal(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_backup_module()
    data_root = tmp_path / "runtime" / "data"
    backup_root = tmp_path / "backups"
    source = data_root / "video_intelligence.db"
    snapshot = tmp_path / "snapshot.db"
    data_root.mkdir(parents=True)
    backup_root.mkdir()
    monkeypatch.setattr(module, "DATA_ROOT", data_root)
    monkeypatch.setattr(module, "BACKUP_ROOT", backup_root.resolve())
    monkeypatch.setattr(sys, "argv", ["backup_control_plane.py", "wal.zip", "0.2.6"])

    writer = sqlite3.connect(source)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE records (value TEXT NOT NULL)")
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("INSERT INTO records(value) VALUES ('wal-only')")
        writer.commit()
        assert source.with_name(f"{source.name}-wal").stat().st_size > 0

        immutable = sqlite3.connect(f"file:{source}?immutable=1", uri=True)
        try:
            assert immutable.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)
        finally:
            immutable.close()

        assert module.main() == 0
        with zipfile.ZipFile(backup_root / "wal.zip") as archive:
            snapshot.write_bytes(archive.read("data/video_intelligence.db"))
    finally:
        writer.close()

    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT value FROM records").fetchone() == (
            "wal-only",
        )


def test_backup_walk_rejects_simulated_nested_mount(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_backup_module()
    data_root = tmp_path / "runtime" / "data"
    mounted = data_root / "mounted"
    mounted.mkdir(parents=True)
    (mounted / "external.txt").write_text("external", encoding="utf-8")
    monkeypatch.setattr(module, "DATA_ROOT", data_root)
    monkeypatch.setattr(
        module,
        "_is_mount_point",
        lambda path, _mounted: Path(path) == mounted,
    )

    with pytest.raises(ValueError, match="子挂载"):
        module._regular_files()


def test_restore_rejects_simulated_nested_mount_before_switch(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_restore_module()
    data_root = _current_data(tmp_path)
    mounted = data_root / "mounted"
    mounted.mkdir()
    (mounted / "external.txt").write_text("external", encoding="utf-8")
    candidate = data_root.parent / ".videoinsight-data-restore-candidate"
    _create_database(candidate / "video_intelligence.db", "snapshot")
    original = _tree_contents(data_root)
    monkeypatch.setattr(module, "DATA_ROOT", data_root)
    monkeypatch.setattr(
        module,
        "_is_mount_point",
        lambda path, _mounted: Path(path) == mounted,
    )

    with pytest.raises(ValueError, match="子挂载"):
        module._switch_candidate(candidate)

    assert _tree_contents(data_root) == original
    assert candidate.is_dir()


def test_backup_rejects_database_symlink_outside_data(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_backup_module()
    data_root = tmp_path / "runtime" / "data"
    backup_root = tmp_path / "backups"
    outside = tmp_path / "outside.db"
    data_root.mkdir(parents=True)
    backup_root.mkdir()
    _create_database(outside, "outside")
    try:
        (data_root / "video_intelligence.db").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"test environment cannot create a symlink: {exc}")
    monkeypatch.setattr(module, "DATA_ROOT", data_root)
    monkeypatch.setattr(module, "BACKUP_ROOT", backup_root.resolve())
    monkeypatch.setattr(sys, "argv", ["backup_control_plane.py", "unsafe.zip", "0.2.6"])

    assert module.main() == 1
    assert not (backup_root / "unsafe.zip").exists()
