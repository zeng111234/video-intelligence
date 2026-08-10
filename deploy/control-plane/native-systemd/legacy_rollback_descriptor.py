"""Create and validate a fixed-scope legacy rollback descriptor."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


FORMAT = "videoinsight-native-legacy-rollback-v1"
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?$")
SNAPSHOT_PATTERN = re.compile(r"^[A-Za-z0-9._-]+\.zip$")
UNIT_BACKUP_PATTERN = re.compile(
    r"^videoinsight-control-plane\.service\.pre-[A-Za-z0-9._-]+$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DESCRIPTOR_KEYS = {
    "format",
    "created_at",
    "source_version",
    "upgraded_to_version",
    "original_current_link",
    "unit_backup_name",
    "snapshot_name",
    "snapshot_sha256",
    "unit_backup_sha256",
}


def _require_version(value: str) -> None:
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError("legacy 回滚描述中的版本号无效。")


def _require_safe_name(value: str, pattern: re.Pattern[str], label: str) -> None:
    if Path(value).name != value or not pattern.fullmatch(value):
        raise ValueError(f"legacy 回滚描述中的{label}无效。")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_file(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"legacy 回滚{label}不是普通文件。")


def _validate_original_link(root: Path, source_version: str, link: str) -> None:
    if not link or any(ord(character) < 32 for character in link):
        raise ValueError("legacy 原 current 链接内容无效。")
    expected = root / "releases" / source_version / "app"
    if not expected.is_dir() or expected.is_symlink():
        raise ValueError("legacy 目标 app 不存在或是符号链接。")
    candidate = Path(link) if Path(link).is_absolute() else root / link
    if candidate.resolve(strict=True) != expected.resolve(strict=True):
        raise ValueError("legacy 原 current 链接未绑定目标旧版本。")


def create_descriptor(
    descriptor: Path,
    *,
    root: Path,
    source_version: str,
    upgraded_to_version: str,
    original_current_link: str,
    unit_backup_name: str,
    snapshot_name: str,
    snapshot_sha256: str,
    unit_backup_sha256: str,
) -> None:
    _require_version(source_version)
    _require_version(upgraded_to_version)
    _require_safe_name(snapshot_name, SNAPSHOT_PATTERN, "快照文件名")
    _require_safe_name(unit_backup_name, UNIT_BACKUP_PATTERN, "unit 备份文件名")
    if not SHA256_PATTERN.fullmatch(snapshot_sha256) or not SHA256_PATTERN.fullmatch(
        unit_backup_sha256
    ):
        raise ValueError("legacy 回滚描述中的 SHA256 无效。")
    _validate_original_link(root.resolve(), source_version, original_current_link)
    payload = {
        "format": FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_version": source_version,
        "upgraded_to_version": upgraded_to_version,
        "original_current_link": original_current_link,
        "unit_backup_name": unit_backup_name,
        "snapshot_name": snapshot_name,
        "snapshot_sha256": snapshot_sha256,
        "unit_backup_sha256": unit_backup_sha256,
    }
    with descriptor.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()


def validate_descriptor(
    descriptor: Path,
    *,
    root: Path,
    backup_root: Path,
    expected_source_version: str,
    expected_upgraded_to_version: str,
    expected_snapshot_name: str,
) -> tuple[str, str]:
    _require_regular_file(descriptor, "描述文件")
    _require_version(expected_source_version)
    _require_version(expected_upgraded_to_version)
    _require_safe_name(expected_snapshot_name, SNAPSHOT_PATTERN, "快照文件名")
    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != DESCRIPTOR_KEYS:
        raise ValueError("legacy 回滚描述字段不完整或包含额外字段。")
    if payload.get("format") != FORMAT:
        raise ValueError("legacy 回滚描述格式不受支持。")
    if payload.get("source_version") != expected_source_version:
        raise ValueError("legacy 回滚描述与目标旧版本不匹配。")
    if payload.get("upgraded_to_version") != expected_upgraded_to_version:
        raise ValueError("legacy 回滚描述与当前新版本不匹配。")
    if payload.get("snapshot_name") != expected_snapshot_name:
        raise ValueError("legacy 回滚描述与目标快照不匹配。")

    original_current_link = payload.get("original_current_link")
    unit_backup_name = payload.get("unit_backup_name")
    snapshot_sha256 = payload.get("snapshot_sha256")
    unit_backup_sha256 = payload.get("unit_backup_sha256")
    if not isinstance(original_current_link, str) or not isinstance(
        unit_backup_name, str
    ):
        raise ValueError("legacy 回滚描述内容类型无效。")
    if not isinstance(snapshot_sha256, str) or not isinstance(unit_backup_sha256, str):
        raise ValueError("legacy 回滚描述哈希类型无效。")
    _require_safe_name(unit_backup_name, UNIT_BACKUP_PATTERN, "unit 备份文件名")
    if not SHA256_PATTERN.fullmatch(snapshot_sha256) or not SHA256_PATTERN.fullmatch(
        unit_backup_sha256
    ):
        raise ValueError("legacy 回滚描述中的 SHA256 无效。")

    resolved_root = root.resolve()
    _validate_original_link(
        resolved_root, expected_source_version, original_current_link
    )
    snapshot = backup_root.resolve() / expected_snapshot_name
    unit_backup = resolved_root / "state" / "unit-backups" / unit_backup_name
    _require_regular_file(snapshot, "快照")
    _require_regular_file(unit_backup, "unit 备份")
    if _sha256(snapshot) != snapshot_sha256:
        raise ValueError("legacy 回滚快照哈希不匹配。")
    if _sha256(unit_backup) != unit_backup_sha256:
        raise ValueError("legacy 回滚 unit 备份哈希不匹配。")
    return original_current_link, unit_backup_name


def main() -> int:
    try:
        if len(sys.argv) == 11 and sys.argv[1] == "create":
            create_descriptor(
                Path(sys.argv[2]),
                root=Path(sys.argv[3]),
                source_version=sys.argv[4],
                upgraded_to_version=sys.argv[5],
                original_current_link=sys.argv[6],
                unit_backup_name=sys.argv[7],
                snapshot_name=sys.argv[8],
                snapshot_sha256=sys.argv[9],
                unit_backup_sha256=sys.argv[10],
            )
        elif len(sys.argv) == 8 and sys.argv[1] == "validate":
            current_link, unit_backup = validate_descriptor(
                Path(sys.argv[2]),
                root=Path(sys.argv[3]),
                backup_root=Path(sys.argv[4]),
                expected_source_version=sys.argv[5],
                expected_upgraded_to_version=sys.argv[6],
                expected_snapshot_name=sys.argv[7],
            )
            print(current_link)
            print(unit_backup)
        else:
            print("legacy 回滚描述工具参数无效。", file=sys.stderr)
            return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
