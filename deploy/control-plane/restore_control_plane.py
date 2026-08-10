"""Validate and restore a control-plane backup while the service is stopped."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


RUNTIME_ROOT = Path(os.getenv("VIDEOINSIGHT_RUNTIME_ROOT", "/app/runtime")).resolve()
DATA_ROOT = RUNTIME_ROOT / "data"
BACKUP_ROOT = Path(os.getenv("VIDEOINSIGHT_BACKUP_ROOT", "/app/backups")).resolve()
SERVICE_UID = int(os.getenv("VIDEOINSIGHT_SERVICE_UID", "10001"))
SERVICE_GID = int(os.getenv("VIDEOINSIGHT_SERVICE_GID", "10001"))
DATABASE_ARCHIVE_PATH = "data/video_intelligence.db"
MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024 * 1024
MOUNT_ESCAPE_PATTERN = re.compile(r"\\([0-7]{3})")


def _mounted_paths() -> set[str]:
    if os.name != "posix":
        return set()
    mounted: set[str] = set()
    with Path("/proc/self/mountinfo").open(
        encoding="utf-8", errors="surrogateescape"
    ) as source:
        for line in source:
            fields = line.split()
            if len(fields) < 5:
                raise ValueError("系统挂载信息格式无效。")
            mounted.add(
                MOUNT_ESCAPE_PATTERN.sub(
                    lambda match: chr(int(match.group(1), 8)), fields[4]
                )
            )
    return mounted


def _is_mount_point(path: Path, mounted: set[str]) -> bool:
    return os.path.ismount(path) or str(path.resolve(strict=True)) in mounted


def _validate_same_filesystem_tree(root: Path) -> None:
    if root.is_symlink():
        raise ValueError("数据目录不能是符号链接。")
    root_metadata = os.lstat(root)
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError("数据路径不是普通目录。")
    mounted = _mounted_paths()
    for current, directories, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        current_path = Path(current)
        current_metadata = os.lstat(current_path)
        if (
            not stat.S_ISDIR(current_metadata.st_mode)
            or current_metadata.st_dev != root_metadata.st_dev
            or (current_path != root and _is_mount_point(current_path, mounted))
        ):
            raise ValueError("数据目录包含子挂载或跨文件系统目录。")
        for name in directories:
            path = current_path / name
            metadata = os.lstat(path)
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_dev != root_metadata.st_dev
                or _is_mount_point(path, mounted)
            ):
                raise ValueError("数据目录包含符号链接、子挂载或特殊目录。")
        for name in filenames:
            metadata = os.lstat(current_path / name)
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_dev != root_metadata.st_dev
            ):
                raise ValueError("数据目录包含符号链接或非普通文件。")
    final_mounts = _mounted_paths()
    root_value = str(root.resolve(strict=True))
    if any(
        mount != root_value and os.path.commonpath((root_value, mount)) == root_value
        for mount in final_mounts
    ):
        raise ValueError("数据目录在校验期间出现子挂载。")


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    total = 0
    names: set[str] = set()
    for item in members:
        if item.filename in names:
            raise ValueError("备份中包含重复路径。")
        names.add(item.filename)
    if DATABASE_ARCHIVE_PATH not in names or "manifest.json" not in names:
        raise ValueError("备份缺少数据库或清单。")
    for item in members:
        path = PurePosixPath(item.filename)
        if (
            not item.filename
            or "\\" in item.filename
            or any(ord(character) < 32 for character in item.filename)
            or path.is_absolute()
            or ".." in path.parts
            or item.flag_bits & 0x1
        ):
            raise ValueError("备份中包含不安全路径。")
        if item.filename != "manifest.json" and (
            not path.parts or path.parts[0] != "data"
        ):
            raise ValueError("备份中包含未知目录。")
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError("备份中不能包含符号链接。")
        total += item.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("备份解压后超过 20GB，已停止恢复。")
    return members


def _validate_manifest(
    manifest: object,
    members: list[zipfile.ZipInfo],
    expected_source_version: str | None,
) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("备份清单格式无效。")
    if manifest.get("format") != "videoinsight-control-plane-backup-v1":
        raise ValueError("备份格式不受支持。")
    data_files = [
        item
        for item in members
        if not item.is_dir() and item.filename.startswith("data/")
    ]
    additional_file_count = manifest.get("additional_file_count")
    if (
        not isinstance(additional_file_count, int)
        or isinstance(additional_file_count, bool)
        or additional_file_count < 0
        or additional_file_count != len(data_files) - 1
    ):
        raise ValueError("备份清单文件数量与归档不一致。")
    database_members = [
        item for item in members if item.filename == DATABASE_ARCHIVE_PATH
    ]
    if len(database_members) != 1 or database_members[0].is_dir():
        raise ValueError("备份数据库不是普通归档文件。")
    source_version = manifest.get("source_release_version")
    if source_version is not None and (
        not isinstance(source_version, str)
        or not source_version
        or any(ord(character) < 32 for character in source_version)
    ):
        raise ValueError("备份清单源版本无效。")
    if (
        expected_source_version is not None
        and source_version != expected_source_version
    ):
        raise ValueError("备份源版本与目标版本不一致。")


def _extract_candidate_data(
    archive: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    candidate: Path,
) -> None:
    for member in members:
        if member.is_dir() or not member.filename.startswith("data/"):
            continue
        relative = PurePosixPath(member.filename).relative_to("data")
        destination = candidate.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, destination.open("xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def _validate_and_revoke_sessions(candidate: Path) -> None:
    database = candidate / "video_intelligence.db"
    connection = sqlite3.connect(str(database))
    try:
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("备份数据库完整性检查失败。")
        has_sessions = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'auth_sessions'
            """
        ).fetchone()
        if has_sessions:
            connection.execute("DELETE FROM auth_sessions")
            connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("注销会话后的数据库完整性检查失败。")
    finally:
        connection.close()


def _portable_prepare_candidate(candidate: Path) -> None:
    """Best-effort fallback for non-POSIX development and test hosts."""
    paths = [candidate, *sorted(candidate.rglob("*"))]
    chown = getattr(os, "chown", None)
    for path in paths:
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("恢复候选目录包含非普通文件。")
        mode = 0o700 if path.is_dir() else 0o600
        os.chmod(path, mode)
        if callable(chown) and SERVICE_UID >= 0 and SERVICE_GID >= 0:
            chown(path, SERVICE_UID, SERVICE_GID)
    if os.name == "posix":
        for path in paths:
            expected_mode = 0o700 if path.is_dir() else 0o600
            metadata = path.stat()
            if stat.S_IMODE(metadata.st_mode) != expected_mode:
                raise ValueError("恢复候选目录权限校验失败。")
            if SERVICE_UID >= 0 and metadata.st_uid != SERVICE_UID:
                raise ValueError("恢复候选目录 UID 校验失败。")
            if SERVICE_GID >= 0 and metadata.st_gid != SERVICE_GID:
                raise ValueError("恢复候选目录 GID 校验失败。")

    _fsync_candidate_portable(candidate)


def _verify_prepared_descriptor(
    descriptor: int, *, directory: bool, root_device: int, expected_mode: int
) -> os.stat_result:
    metadata = os.fstat(descriptor)
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(metadata.st_mode) or metadata.st_dev != root_device:
        raise ValueError("恢复候选目录包含跨文件系统或非普通数据项。")
    if stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise ValueError("恢复候选目录权限校验失败。")
    if SERVICE_UID >= 0 and metadata.st_uid != SERVICE_UID:
        raise ValueError("恢复候选目录 UID 校验失败。")
    if SERVICE_GID >= 0 and metadata.st_gid != SERVICE_GID:
        raise ValueError("恢复候选目录 GID 校验失败。")
    return metadata


def _prepare_descriptor(descriptor: int, *, directory: bool, root_device: int) -> None:
    mode = 0o700 if directory else 0o600
    os.fchmod(descriptor, mode)
    if SERVICE_UID >= 0 and SERVICE_GID >= 0:
        os.fchown(descriptor, SERVICE_UID, SERVICE_GID)
    _verify_prepared_descriptor(
        descriptor,
        directory=directory,
        root_device=root_device,
        expected_mode=mode,
    )
    os.fsync(descriptor)


def _open_verified_child(
    parent_descriptor: int,
    name: str,
    before: os.stat_result,
    *,
    directory: bool,
    root_device: int,
) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise OSError("当前 POSIX 平台不支持 O_NOFOLLOW，拒绝恢复。")
    flags = os.O_RDONLY | no_follow
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    else:
        flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    try:
        after = os.fstat(descriptor)
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        if (
            not expected_type(after.st_mode)
            or after.st_dev != root_device
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
        ):
            raise ValueError("恢复候选数据项在安全打开期间发生变化。")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _prepare_directory_descriptor(
    directory_descriptor: int, *, root_device: int
) -> None:
    for name in sorted(os.listdir(directory_descriptor)):
        before = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if before.st_dev != root_device:
            raise ValueError("恢复候选目录包含跨文件系统数据项。")
        if stat.S_ISDIR(before.st_mode):
            child = _open_verified_child(
                directory_descriptor,
                name,
                before,
                directory=True,
                root_device=root_device,
            )
            try:
                _prepare_directory_descriptor(child, root_device=root_device)
                _prepare_descriptor(child, directory=True, root_device=root_device)
            finally:
                os.close(child)
        elif stat.S_ISREG(before.st_mode):
            child = _open_verified_child(
                directory_descriptor,
                name,
                before,
                directory=False,
                root_device=root_device,
            )
            try:
                _prepare_descriptor(child, directory=False, root_device=root_device)
            finally:
                os.close(child)
        else:
            raise ValueError("恢复候选目录包含符号链接或非普通文件。")


def _prepare_candidate_for_commit(candidate: Path) -> None:
    """Prepare via pinned descriptors, transferring the root ownership last."""
    if os.name != "posix":
        _portable_prepare_candidate(candidate)
        return
    if (SERVICE_UID >= 0) != (SERVICE_GID >= 0):
        raise ValueError("恢复服务 UID/GID 必须同时提供。")

    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise OSError("当前 POSIX 平台不支持 O_NOFOLLOW，拒绝恢复。")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    descriptor = os.open(candidate, flags)
    try:
        root_metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(root_metadata.st_mode):
            raise ValueError("恢复候选根不是普通目录。")
        if SERVICE_UID >= 0 and (
            root_metadata.st_uid != 0 or root_metadata.st_gid != 0
        ):
            raise ValueError("恢复候选根在提交准备前必须属于 root:root。")
        _prepare_directory_descriptor(descriptor, root_device=root_metadata.st_dev)
        # The root is deliberately transferred last. After this fchown only
        # descriptor-local verification/fsync and the same-filesystem rename run.
        _prepare_descriptor(
            descriptor, directory=True, root_device=root_metadata.st_dev
        )
    finally:
        os.close(descriptor)


def _fsync_file(path: Path) -> None:
    if os.name != "posix":
        return
    with path.open("rb") as source:
        os.fsync(source.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_candidate_portable(candidate: Path) -> None:
    files = sorted(path for path in candidate.rglob("*") if path.is_file())
    directories = sorted(
        (path for path in candidate.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in files:
        _fsync_file(path)
    for path in directories:
        _fsync_directory(path)
    _fsync_directory(candidate)


def _validate_switch_preconditions(candidate: Path) -> tuple[Path, bool]:
    previous = candidate.with_name(f"{candidate.name}-previous")
    had_original = DATA_ROOT.exists()
    if DATA_ROOT.is_symlink() or (had_original and not DATA_ROOT.is_dir()):
        raise ValueError("现有 data 不是普通目录。")
    if had_original:
        _validate_same_filesystem_tree(DATA_ROOT)
    if previous.exists() or previous.is_symlink():
        raise ValueError("恢复事务旧数据暂存路径已存在。")
    return previous, had_original


def _switch_candidate(
    candidate: Path, switch_state: tuple[Path, bool] | None = None
) -> None:
    previous, had_original = (
        switch_state
        if switch_state is not None
        else _validate_switch_preconditions(candidate)
    )
    runtime_root = DATA_ROOT.parent
    moved_original = False
    installed_candidate = False
    try:
        _fsync_directory(runtime_root)
        if had_original:
            os.replace(DATA_ROOT, previous)
            moved_original = True
            _fsync_directory(runtime_root)
        os.replace(candidate, DATA_ROOT)
        installed_candidate = True
        _fsync_directory(runtime_root)
    except BaseException as primary_error:
        recovery_error: BaseException | None = None
        try:
            if installed_candidate:
                os.replace(DATA_ROOT, candidate)
            if moved_original:
                os.replace(previous, DATA_ROOT)
        except BaseException as exc:
            recovery_error = exc
        try:
            _fsync_directory(runtime_root)
        except BaseException as exc:
            if recovery_error is None:
                recovery_error = exc
        if recovery_error is not None:
            raise OSError(
                "恢复目录切换失败，且原数据目录自动复位未完整落盘。"
            ) from recovery_error
        raise primary_error
    if had_original:
        try:
            _validate_same_filesystem_tree(previous)
            shutil.rmtree(previous)
            _fsync_directory(runtime_root)
        except (OSError, ValueError) as exc:
            print(
                "WARNING: 新数据目录已提交，但旧数据清理或最终目录落盘未完成："
                f"{previous.name}（{exc}）",
                file=sys.stderr,
            )


def _remove_directory_contents_descriptor(
    directory_descriptor: int, *, root_device: int
) -> None:
    for name in sorted(os.listdir(directory_descriptor)):
        before = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if stat.S_ISDIR(before.st_mode):
            child = _open_verified_child(
                directory_descriptor,
                name,
                before,
                directory=True,
                root_device=root_device,
            )
            try:
                _remove_directory_contents_descriptor(child, root_device=root_device)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=directory_descriptor)
        else:
            # unlinkat-style removal never follows a raced symlink or special file.
            os.unlink(name, dir_fd=directory_descriptor)


def _remove_candidate(candidate: Path) -> None:
    if os.name != "posix":
        if candidate.exists() and candidate.is_dir() and not candidate.is_symlink():
            _validate_same_filesystem_tree(candidate)
            shutil.rmtree(candidate)
        return

    before = os.lstat(candidate)
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError("恢复候选清理目标不是普通目录。")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise OSError("当前 POSIX 平台不支持 O_NOFOLLOW，拒绝清理。")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    descriptor = os.open(candidate, flags)
    try:
        after = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(after.st_mode)
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
        ):
            raise ValueError("恢复候选清理目标在安全打开期间发生变化。")
        _remove_directory_contents_descriptor(descriptor, root_device=after.st_dev)
    finally:
        os.close(descriptor)
    os.rmdir(candidate)


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        print(
            "用法：restore_control_plane.py <备份文件名.zip> [预期源版本]",
            file=sys.stderr,
        )
        return 2
    backup_name = sys.argv[1]
    if backup_name != Path(backup_name).name:
        print("只允许恢复已配置备份目录内的备份。", file=sys.stderr)
        return 2
    expected_source_version = sys.argv[2] if len(sys.argv) == 3 else None
    backup_path = BACKUP_ROOT / backup_name
    backup = backup_path.resolve()
    if (
        backup.parent != BACKUP_ROOT
        or not backup_path.is_file()
        or backup_path.is_symlink()
    ):
        print("只允许恢复已配置备份目录内的备份。", file=sys.stderr)
        return 2

    runtime_root = DATA_ROOT.parent
    candidate: Path | None = None
    try:
        runtime_root.mkdir(parents=True, exist_ok=True)
        if runtime_root.is_symlink() or not runtime_root.is_dir():
            raise ValueError("运行目录不存在或是符号链接。")
        if os.name == "posix" and SERVICE_UID >= 0:
            runtime_metadata = os.lstat(runtime_root)
            if (
                runtime_metadata.st_uid != 0
                or runtime_metadata.st_gid != 0
                or stat.S_IMODE(runtime_metadata.st_mode) & 0o022
            ):
                raise ValueError(
                    "运行目录必须属于 root:root 且不能由组或其他用户写入。"
                )
        if DATA_ROOT.is_symlink() or (DATA_ROOT.exists() and not DATA_ROOT.is_dir()):
            raise ValueError("现有 data 不是普通目录。")
        candidate = Path(
            tempfile.mkdtemp(prefix=".videoinsight-data-restore-", dir=runtime_root)
        )
        with zipfile.ZipFile(backup) as archive:
            members = _safe_members(archive)
            manifest = json.loads(archive.read("manifest.json"))
            _validate_manifest(manifest, members, expected_source_version)
            _extract_candidate_data(archive, members, candidate)
        _validate_and_revoke_sessions(candidate)
        _validate_same_filesystem_tree(candidate)
        switch_state = _validate_switch_preconditions(candidate)
        _prepare_candidate_for_commit(candidate)
        _switch_candidate(candidate, switch_state)
        candidate = None
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"恢复失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if candidate is not None:
            try:
                _remove_candidate(candidate)
            except (OSError, ValueError):
                pass
    print("恢复完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
