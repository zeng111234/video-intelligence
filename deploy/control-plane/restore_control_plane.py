"""Validate and restore a control-plane backup while the service is stopped."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


DATA_ROOT = Path("/app/runtime/data").resolve()
BACKUP_ROOT = Path("/app/backups").resolve()
DATABASE_ARCHIVE_PATH = "data/video_intelligence.db"
MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024 * 1024


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    total = 0
    names = {item.filename for item in members}
    if DATABASE_ARCHIVE_PATH not in names or "manifest.json" not in names:
        raise ValueError("备份缺少数据库或清单。")
    for item in members:
        path = PurePosixPath(item.filename)
        if path.is_absolute() or ".." in path.parts:
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


def _clear_data_root() -> None:
    """Remove only the validated control-plane data mount contents."""

    for path in DATA_ROOT.iterdir():
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def main() -> int:
    if len(sys.argv) != 2:
        print("用法：restore_control_plane.py <备份文件名.zip>", file=sys.stderr)
        return 2
    backup = (BACKUP_ROOT / Path(sys.argv[1]).name).resolve()
    if backup.parent != BACKUP_ROOT or not backup.is_file():
        print("只允许恢复 /app/backups 内的备份。", file=sys.stderr)
        return 2

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(backup) as archive:
            members = _safe_members(archive)
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "videoinsight-control-plane-backup-v1":
                raise ValueError("备份格式不受支持。")
            with tempfile.TemporaryDirectory(prefix="videoinsight-restore-") as temp:
                staging = Path(temp)
                archive.extractall(staging, members=members)
                staged_db = staging / DATABASE_ARCHIVE_PATH
                integrity = sqlite3.connect(str(staged_db))
                try:
                    if integrity.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise ValueError("备份数据库完整性检查失败。")
                    has_sessions = integrity.execute(
                        """
                        SELECT 1 FROM sqlite_master
                        WHERE type = 'table' AND name = 'auth_sessions'
                        """
                    ).fetchone()
                    if has_sessions:
                        integrity.execute("DELETE FROM auth_sessions")
                        integrity.commit()
                finally:
                    integrity.close()

                data_staging = staging / "data"
                _clear_data_root()
                for source in sorted(data_staging.rglob("*")):
                    if not source.is_file() or source.name == "video_intelligence.db":
                        continue
                    relative = source.relative_to(data_staging)
                    destination = DATA_ROOT / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(f".{destination.name}.restore")
                    shutil.copy2(source, temporary)
                    os.replace(temporary, destination)

                temporary_db = DATA_ROOT / ".video_intelligence.db.restore"
                shutil.copy2(staged_db, temporary_db)
                os.replace(temporary_db, DATA_ROOT / "video_intelligence.db")
                for suffix in ("-wal", "-shm"):
                    (DATA_ROOT / f"video_intelligence.db{suffix}").unlink(missing_ok=True)
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"恢复失败：{exc}", file=sys.stderr)
        return 1

    chown = getattr(os, "chown", None)
    for path in DATA_ROOT.rglob("*"):
        try:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        except OSError:
            pass
    try:
        os.chmod(DATA_ROOT, 0o700)
    except OSError:
        pass
    if callable(chown):
        for path in DATA_ROOT.rglob("*"):
            try:
                chown(path, 10001, 10001)
            except OSError:
                pass
        chown(DATA_ROOT, 10001, 10001)
    print("恢复完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
