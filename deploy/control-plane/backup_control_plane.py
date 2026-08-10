"""Create a consistent control-plane backup without copying a live SQLite file."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


DATA_ROOT = Path("/app/runtime/data").resolve()
BACKUP_ROOT = Path("/app/backups").resolve()
DATABASE_NAME = "video_intelligence.db"


def _regular_files() -> list[Path]:
    files: list[Path] = []
    for path in DATA_ROOT.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(DATA_ROOT)
        if relative.as_posix() in {
            DATABASE_NAME,
            f"{DATABASE_NAME}-wal",
            f"{DATABASE_NAME}-shm",
        }:
            continue
        files.append(path)
    return files


def main() -> int:
    if len(sys.argv) != 2:
        print("用法：backup_control_plane.py <备份文件名.zip>", file=sys.stderr)
        return 2
    target = (BACKUP_ROOT / Path(sys.argv[1]).name).resolve()
    if target.parent != BACKUP_ROOT or target.suffix.casefold() != ".zip":
        print("备份目标必须是 /app/backups 下的 .zip 文件。", file=sys.stderr)
        return 2
    source_db = DATA_ROOT / DATABASE_NAME
    if not source_db.is_file():
        print("控制层数据库不存在，无法备份。", file=sys.stderr)
        return 1

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="videoinsight-backup-", dir=BACKUP_ROOT) as temp:
        temp_root = Path(temp)
        snapshot = temp_root / DATABASE_NAME
        source = sqlite3.connect(str(source_db), timeout=30)
        destination = sqlite3.connect(str(snapshot))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        integrity = sqlite3.connect(str(snapshot))
        try:
            if integrity.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                print("数据库快照完整性检查失败。", file=sys.stderr)
                return 1
        finally:
            integrity.close()

        temporary_zip = temp_root / "backup.zip"
        files = _regular_files()
        with zipfile.ZipFile(
            temporary_zip,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.write(snapshot, f"data/{DATABASE_NAME}")
            for path in files:
                archive.write(path, f"data/{path.relative_to(DATA_ROOT).as_posix()}")
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "format": "videoinsight-control-plane-backup-v1",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "additional_file_count": len(files),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        os.replace(temporary_zip, target)
        os.chmod(target, 0o600)
    print(target.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
