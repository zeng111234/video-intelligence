"""Create a consistent control-plane backup without copying a live SQLite file."""

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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


RUNTIME_ROOT = Path(os.getenv("VIDEOINSIGHT_RUNTIME_ROOT", "/app/runtime")).resolve()
DATA_ROOT = RUNTIME_ROOT / "data"
BACKUP_ROOT = Path(os.getenv("VIDEOINSIGHT_BACKUP_ROOT", "/app/backups")).resolve()
DATABASE_NAME = "video_intelligence.db"
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?$")
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


def _regular_files() -> list[Path]:
    files: list[Path] = []
    root_metadata = os.lstat(DATA_ROOT)
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError("运行数据目录不是普通目录。")
    mounted = _mounted_paths()
    for current, directories, filenames in os.walk(
        DATA_ROOT, topdown=True, followlinks=False
    ):
        current_path = Path(current)
        current_metadata = os.lstat(current_path)
        if (
            not stat.S_ISDIR(current_metadata.st_mode)
            or current_metadata.st_dev != root_metadata.st_dev
            or (current_path != DATA_ROOT and _is_mount_point(current_path, mounted))
        ):
            raise ValueError("运行数据目录包含子挂载或跨文件系统目录。")
        for name in directories:
            path = current_path / name
            metadata = os.lstat(path)
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_dev != root_metadata.st_dev
                or _is_mount_point(path, mounted)
            ):
                raise ValueError("运行数据目录包含符号链接、子挂载或特殊目录。")
        for name in filenames:
            path = current_path / name
            metadata = os.lstat(path)
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_dev != root_metadata.st_dev
            ):
                raise ValueError("运行数据目录包含符号链接或非普通文件。")
            relative = path.relative_to(DATA_ROOT)
            if relative.as_posix() in {
                DATABASE_NAME,
                f"{DATABASE_NAME}-wal",
                f"{DATABASE_NAME}-shm",
            }:
                continue
            files.append(path)
    final_mounts = _mounted_paths()
    if any(
        mount != str(DATA_ROOT)
        and os.path.commonpath((str(DATA_ROOT), mount)) == str(DATA_ROOT)
        for mount in final_mounts
    ):
        raise ValueError("运行数据目录在备份遍历期间出现子挂载。")
    return sorted(files)


@contextmanager
def _open_regular_data_file(relative: Path):
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("运行数据文件路径越界。")
    if os.name != "posix":
        path = DATA_ROOT / relative
        if path.is_symlink():
            raise ValueError("运行数据文件在备份期间变成了符号链接。")
        with path.open("rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("运行数据文件在备份期间不再是普通文件。")
            yield source, metadata
        return

    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise OSError("当前 POSIX 平台不支持 O_NOFOLLOW，拒绝备份。")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    file_flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | no_follow
    directory_descriptors: list[int] = []
    file_descriptor: int | None = None
    try:
        directory_descriptors.append(os.open(DATA_ROOT, directory_flags))
        root_metadata = os.fstat(directory_descriptors[0])
        for part in relative.parts[:-1]:
            descriptor = os.open(
                part, directory_flags, dir_fd=directory_descriptors[-1]
            )
            if os.fstat(descriptor).st_dev != root_metadata.st_dev:
                os.close(descriptor)
                raise ValueError("运行数据目录在备份期间跨入其他文件系统。")
            directory_descriptors.append(descriptor)
        file_descriptor = os.open(
            relative.parts[-1], file_flags, dir_fd=directory_descriptors[-1]
        )
        metadata = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_dev != root_metadata.st_dev
        ):
            raise ValueError("运行数据文件在备份期间不再是普通文件。")
        with os.fdopen(file_descriptor, "rb", closefd=True) as source:
            file_descriptor = None
            yield source, metadata
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for descriptor in reversed(directory_descriptors):
            os.close(descriptor)


def _same_open_file(metadata: os.stat_result, expected: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_dev == expected.st_dev
        and metadata.st_ino == expected.st_ino
    )


@contextmanager
def _open_pinned_database_file():
    """Pin the data directory and database inode for the SQLite snapshot."""
    if os.name != "posix":
        with _open_regular_data_file(Path(DATABASE_NAME)) as (source, metadata):
            yield source, metadata, None, None
        return

    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise OSError("当前 POSIX 平台不支持 O_NOFOLLOW，拒绝备份。")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    file_flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | no_follow
    data_root_descriptor: int | None = None
    database_descriptor: int | None = None
    try:
        data_root_descriptor = os.open(DATA_ROOT, directory_flags)
        data_root_metadata = os.fstat(data_root_descriptor)
        if not stat.S_ISDIR(data_root_metadata.st_mode):
            raise ValueError("运行数据目录在备份期间不再是普通目录。")
        database_descriptor = os.open(
            DATABASE_NAME, file_flags, dir_fd=data_root_descriptor
        )
        database_metadata = os.fstat(database_descriptor)
        if (
            not stat.S_ISREG(database_metadata.st_mode)
            or database_metadata.st_dev != data_root_metadata.st_dev
        ):
            raise ValueError("控制层数据库在备份期间不再是同文件系统普通文件。")
        with os.fdopen(database_descriptor, "rb", closefd=True) as source:
            database_descriptor = None
            yield source, database_metadata, data_root_descriptor, data_root_metadata
    finally:
        if database_descriptor is not None:
            os.close(database_descriptor)
        if data_root_descriptor is not None:
            os.close(data_root_descriptor)


def _validate_pinned_sqlite_source(
    source_handle,
    source_metadata: os.stat_result,
    data_root_descriptor: int,
    data_root_metadata: os.stat_result,
) -> None:
    current_root = os.fstat(data_root_descriptor)
    if (
        not stat.S_ISDIR(current_root.st_mode)
        or current_root.st_dev != data_root_metadata.st_dev
        or current_root.st_ino != data_root_metadata.st_ino
    ):
        raise ValueError("运行数据目录固定 inode 在备份期间发生变化。")
    current_handle = os.fstat(source_handle.fileno())
    try:
        current_path = os.stat(
            DATABASE_NAME, dir_fd=data_root_descriptor, follow_symlinks=False
        )
    except OSError as exc:
        raise ValueError("控制层数据库路径在备份期间不可用。") from exc
    if not _same_open_file(current_handle, source_metadata) or not _same_open_file(
        current_path, source_metadata
    ):
        raise ValueError("控制层数据库路径与固定 inode 在备份期间不一致。")


def _active_process_descriptors() -> set[int]:
    """Return descriptors that remained open after enumerating /proc/self/fd."""
    try:
        names = os.listdir("/proc/self/fd")
    except OSError as exc:
        raise ValueError("无法审计 SQLite 连接的文件描述符。") from exc
    descriptors: set[int] = set()
    for name in names:
        try:
            descriptor = int(name)
            os.fstat(descriptor)
        except (OSError, ValueError):
            # /proc/self/fd may include the short-lived descriptor used to list
            # the directory itself. It is already closed by the time we get here.
            continue
        descriptors.add(descriptor)
    return descriptors


def _validate_sqlite_connection_inode(
    descriptors_before_connect: set[int], source_metadata: os.stat_result
) -> None:
    """Prove the new SQLite connection itself holds the pinned database inode."""
    for descriptor in _active_process_descriptors() - descriptors_before_connect:
        try:
            metadata = os.fstat(descriptor)
        except OSError:
            continue
        if _same_open_file(metadata, source_metadata):
            return
    raise ValueError("SQLite 连接未持有固定的控制层数据库 inode。")


@contextmanager
def _open_pinned_sqlite_source(
    source_handle,
    source_metadata: os.stat_result,
    data_root_descriptor: int | None,
    data_root_metadata: os.stat_result | None,
):
    """Open SQLite through a pinned data directory so WAL sidecars stay visible."""
    if os.name != "posix":
        # The native release path is Linux-only. This fallback keeps local Windows
        # bundle tests usable without claiming an inode pin on that platform.
        connection = sqlite3.connect(str(DATA_ROOT / DATABASE_NAME), timeout=30)
        try:
            yield connection
        finally:
            connection.close()
        return

    if data_root_descriptor is None or data_root_metadata is None:
        raise ValueError("控制层数据库缺少固定的数据目录描述符。")
    _validate_pinned_sqlite_source(
        source_handle,
        source_metadata,
        data_root_descriptor,
        data_root_metadata,
    )
    pinned_path = f"/proc/self/fd/{data_root_descriptor}/{DATABASE_NAME}"
    descriptors_before_connect = _active_process_descriptors()
    connection = sqlite3.connect(f"file:{pinned_path}?mode=ro", uri=True, timeout=30)
    try:
        # Force SQLite to open the main database before auditing its kernel FD.
        connection.execute("PRAGMA schema_version").fetchone()
        _validate_sqlite_connection_inode(
            descriptors_before_connect,
            source_metadata,
        )
        _validate_pinned_sqlite_source(
            source_handle,
            source_metadata,
            data_root_descriptor,
            data_root_metadata,
        )
        yield connection
        _validate_pinned_sqlite_source(
            source_handle,
            source_metadata,
            data_root_descriptor,
            data_root_metadata,
        )
    finally:
        connection.close()


def _write_open_data_file(
    archive: zipfile.ZipFile, relative: Path, source, metadata: os.stat_result
) -> None:
    member = zipfile.ZipInfo(f"data/{relative.as_posix()}")
    member.compress_type = zipfile.ZIP_DEFLATED
    member.external_attr = (stat.S_IFREG | 0o600) << 16
    with archive.open(member, mode="w", force_zip64=True) as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)
    after = os.fstat(source.fileno())
    if (
        after.st_dev != metadata.st_dev
        or after.st_ino != metadata.st_ino
        or after.st_size != metadata.st_size
        or after.st_mtime_ns != metadata.st_mtime_ns
    ):
        raise ValueError("运行数据文件在备份读取期间发生变化。")


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


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        print(
            "用法：backup_control_plane.py <备份文件名.zip> [源版本]",
            file=sys.stderr,
        )
        return 2
    source_release_version = (
        sys.argv[2]
        if len(sys.argv) == 3
        else os.getenv("VIDEOINSIGHT_SOURCE_RELEASE_VERSION", "legacy-unversioned")
    )
    if len(sys.argv) == 3 and not VERSION_PATTERN.fullmatch(source_release_version):
        print("源版本格式无效。", file=sys.stderr)
        return 2
    target_name = sys.argv[1]
    if target_name != Path(target_name).name:
        print("备份目标必须位于已配置的备份目录下。", file=sys.stderr)
        return 2
    target_path = BACKUP_ROOT / target_name
    target = target_path.resolve()
    if (
        target.parent != BACKUP_ROOT
        or target.suffix.casefold() != ".zip"
        or target_path.exists()
        or target_path.is_symlink()
    ):
        print("备份目标必须是备份目录内尚不存在的新 ZIP 文件。", file=sys.stderr)
        return 2
    source_db = DATA_ROOT / DATABASE_NAME
    if DATA_ROOT.is_symlink() or not DATA_ROOT.is_dir():
        print("运行数据目录不存在或是符号链接。", file=sys.stderr)
        return 1
    if not source_db.is_file() or source_db.is_symlink():
        print("控制层数据库不存在、不是普通文件或是符号链接。", file=sys.stderr)
        return 1
    try:
        resolved_db = source_db.resolve(strict=True)
        resolved_data = DATA_ROOT.resolve(strict=True)
    except OSError as exc:
        print(f"控制层数据库路径无法解析：{exc}", file=sys.stderr)
        return 1
    if resolved_data not in resolved_db.parents:
        print("控制层数据库越过运行数据目录。", file=sys.stderr)
        return 1
    try:
        files = _regular_files()
    except (OSError, ValueError) as exc:
        print(f"无法备份运行数据：{exc}", file=sys.stderr)
        return 1

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="videoinsight-backup-", dir=BACKUP_ROOT
    ) as temp:
        temp_root = Path(temp)
        snapshot = temp_root / DATABASE_NAME
        try:
            with _open_pinned_database_file() as (
                source_handle,
                source_metadata,
                data_root_descriptor,
                data_root_metadata,
            ):
                current_metadata = os.stat(source_db, follow_symlinks=False)
                if (
                    not stat.S_ISREG(current_metadata.st_mode)
                    or current_metadata.st_dev != source_metadata.st_dev
                    or current_metadata.st_ino != source_metadata.st_ino
                ):
                    print("控制层数据库在备份前发生路径变化。", file=sys.stderr)
                    return 1
                destination = sqlite3.connect(str(snapshot))
                try:
                    with _open_pinned_sqlite_source(
                        source_handle,
                        source_metadata,
                        data_root_descriptor,
                        data_root_metadata,
                    ) as source:
                        source.backup(destination)
                finally:
                    destination.close()
                current_metadata = os.stat(source_db, follow_symlinks=False)
                if not _same_open_file(
                    current_metadata, source_metadata
                ) or not _same_open_file(
                    os.fstat(source_handle.fileno()), source_metadata
                ):
                    print("控制层数据库在备份期间发生路径变化。", file=sys.stderr)
                    return 1
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(f"控制层数据库快照失败：{exc}", file=sys.stderr)
            return 1
        integrity = sqlite3.connect(str(snapshot))
        try:
            if integrity.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                print("数据库快照完整性检查失败。", file=sys.stderr)
                return 1
        finally:
            integrity.close()

        temporary_zip = temp_root / "backup.zip"
        with zipfile.ZipFile(
            temporary_zip,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.write(snapshot, f"data/{DATABASE_NAME}")
            for path in files:
                relative = path.relative_to(DATA_ROOT)
                with _open_regular_data_file(relative) as (source, metadata):
                    _write_open_data_file(archive, relative, source, metadata)
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "format": "videoinsight-control-plane-backup-v1",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "source_release_version": source_release_version,
                        "additional_file_count": len(files),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        os.chmod(temporary_zip, 0o600)
        _fsync_file(temporary_zip)
        try:
            os.link(temporary_zip, target)
        except FileExistsError:
            print("备份目标已存在，拒绝覆盖。", file=sys.stderr)
            return 2
        try:
            _fsync_file(target)
            _fsync_directory(BACKUP_ROOT)
        except OSError:
            try:
                target.unlink()
                _fsync_directory(BACKUP_ROOT)
            except OSError:
                pass
            raise
    print(target.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
