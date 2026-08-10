"""Create and validate a v2 rollback record bound to strict legacy adoption."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


FORMAT = "videoinsight-native-legacy-rollback-v2"
ADOPTION_FORMAT = "videoinsight-native-legacy-adoption-v1"
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SNAPSHOT_PATTERN = re.compile(r"^[A-Za-z0-9._-]+\.zip$")
UNIT_BACKUP_PATTERN = re.compile(
    r"^videoinsight-control-plane\.service\.pre-[A-Za-z0-9._-]+$"
)
ADOPTION_NAME = "legacy-adoption.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DESCRIPTOR_KEYS = {
    "format",
    "created_at",
    "source_version",
    "upgraded_to_version",
    "interpreter_version",
    "original_current_link",
    "bridge_unit_backup_name",
    "snapshot_name",
    "snapshot_sha256",
    "bridge_unit_backup_sha256",
    "bridge_unit_sha256",
    "adoption_descriptor_name",
    "adoption_descriptor_sha256",
}


def _require_version(value: object) -> str:
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise ValueError("legacy 回滚描述中的稳定版本号无效。")
    return value


def _require_safe_name(value: object, pattern: re.Pattern[str], label: str) -> str:
    if (
        not isinstance(value, str)
        or Path(value).name != value
        or not pattern.fullmatch(value)
    ):
        raise ValueError(f"legacy 回滚描述中的{label}无效。")
    return value


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"legacy 回滚描述中的{label} SHA256 无效。")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_file(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"legacy 回滚{label}不是普通文件。")


def create_descriptor(
    descriptor: Path,
    *,
    root: Path,
    source_version: str,
    upgraded_to_version: str,
    interpreter_version: str,
    original_current_link: str,
    bridge_unit_backup_name: str,
    snapshot_name: str,
    snapshot_sha256: str,
    bridge_unit_backup_sha256: str,
    bridge_unit_sha256: str,
    adoption_descriptor_name: str,
    adoption_descriptor_sha256: str,
) -> None:
    source_version = _require_version(source_version)
    _require_version(upgraded_to_version)
    _require_version(interpreter_version)
    _require_safe_name(snapshot_name, SNAPSHOT_PATTERN, "快照文件名")
    _require_safe_name(
        bridge_unit_backup_name, UNIT_BACKUP_PATTERN, "bridge unit 备份文件名"
    )
    if adoption_descriptor_name != ADOPTION_NAME:
        raise ValueError("legacy 回滚只能绑定固定 adoption 描述。")
    snapshot_sha256 = _require_sha256(snapshot_sha256, "快照")
    bridge_unit_backup_sha256 = _require_sha256(
        bridge_unit_backup_sha256, "bridge unit 备份"
    )
    bridge_unit_sha256 = _require_sha256(bridge_unit_sha256, "bridge unit")
    adoption_descriptor_sha256 = _require_sha256(
        adoption_descriptor_sha256, "adoption 描述"
    )
    if bridge_unit_backup_sha256 != bridge_unit_sha256:
        raise ValueError(
            "legacy 回滚 bridge unit 备份不是 adoption 绑定的 strict bridge。"
        )
    expected_current = str(
        root.resolve(strict=True) / "releases" / source_version / "app"
    )
    if original_current_link != expected_current:
        raise ValueError("legacy 回滚原 current 未精确绑定 adoption application。")
    resolved_root = root.resolve(strict=True)
    snapshot = resolved_root / "backups" / snapshot_name
    unit_backup = resolved_root / "state" / "unit-backups" / bridge_unit_backup_name
    adoption = resolved_root / "state" / adoption_descriptor_name
    _require_regular_file(snapshot, "快照")
    _require_regular_file(unit_backup, "bridge unit 备份")
    _require_regular_file(adoption, "adoption 描述")
    if _sha256(snapshot) != snapshot_sha256:
        raise ValueError("legacy 回滚快照哈希在描述创建前发生变化。")
    if _sha256(unit_backup) != bridge_unit_backup_sha256:
        raise ValueError("legacy 回滚 bridge unit 在描述创建前发生变化。")
    if _sha256(adoption) != adoption_descriptor_sha256:
        raise ValueError("legacy 回滚 adoption 描述在创建前发生变化。")
    adoption_payload = json.loads(adoption.read_text(encoding="utf-8"))
    expected_adoption = {
        "format": ADOPTION_FORMAT,
        "status": "active",
        "application_version": source_version,
        "interpreter_version": interpreter_version,
        "original_current_link": original_current_link,
        "bridge_unit_sha256": bridge_unit_sha256,
    }
    if not isinstance(adoption_payload, dict) or any(
        adoption_payload.get(key) != value for key, value in expected_adoption.items()
    ):
        raise ValueError("legacy 回滚创建参数与 active adoption 组合不一致。")
    payload = {
        "format": FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_version": source_version,
        "upgraded_to_version": upgraded_to_version,
        "interpreter_version": interpreter_version,
        "original_current_link": original_current_link,
        "bridge_unit_backup_name": bridge_unit_backup_name,
        "snapshot_name": snapshot_name,
        "snapshot_sha256": snapshot_sha256,
        "bridge_unit_backup_sha256": bridge_unit_backup_sha256,
        "bridge_unit_sha256": bridge_unit_sha256,
        "adoption_descriptor_name": adoption_descriptor_name,
        "adoption_descriptor_sha256": adoption_descriptor_sha256,
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
) -> tuple[str, str, str, str, str, str]:
    _require_regular_file(descriptor, "描述文件")
    expected_source_version = _require_version(expected_source_version)
    expected_upgraded_to_version = _require_version(expected_upgraded_to_version)
    _require_safe_name(expected_snapshot_name, SNAPSHOT_PATTERN, "快照文件名")
    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != DESCRIPTOR_KEYS:
        raise ValueError("legacy 回滚描述字段不完整或包含额外字段。")
    if payload.get("format") != FORMAT:
        raise ValueError("legacy 回滚描述格式不是 adoption v2。")
    if payload.get("source_version") != expected_source_version:
        raise ValueError("legacy 回滚描述与目标 application 版本不匹配。")
    if payload.get("upgraded_to_version") != expected_upgraded_to_version:
        raise ValueError("legacy 回滚描述与当前新版本不匹配。")
    if payload.get("snapshot_name") != expected_snapshot_name:
        raise ValueError("legacy 回滚描述与目标快照不匹配。")

    interpreter_version = _require_version(payload.get("interpreter_version"))
    current_link = payload.get("original_current_link")
    if not isinstance(current_link, str) or any(
        ord(character) < 32 for character in current_link
    ):
        raise ValueError("legacy 回滚 current 类型无效。")
    expected_current = str(
        root.resolve(strict=True) / "releases" / expected_source_version / "app"
    )
    if current_link != expected_current:
        raise ValueError("legacy 回滚 current 未精确绑定 adoption application。")
    unit_name = _require_safe_name(
        payload.get("bridge_unit_backup_name"),
        UNIT_BACKUP_PATTERN,
        "bridge unit 备份文件名",
    )
    snapshot_sha = _require_sha256(payload.get("snapshot_sha256"), "快照")
    unit_sha = _require_sha256(
        payload.get("bridge_unit_backup_sha256"), "bridge unit 备份"
    )
    bridge_sha = _require_sha256(payload.get("bridge_unit_sha256"), "bridge unit")
    adoption_name = payload.get("adoption_descriptor_name")
    if adoption_name != ADOPTION_NAME:
        raise ValueError("legacy 回滚 adoption 描述文件名不固定。")
    adoption_sha = _require_sha256(
        payload.get("adoption_descriptor_sha256"), "adoption 描述"
    )
    if unit_sha != bridge_sha:
        raise ValueError("legacy 回滚 unit 备份不是 adoption strict bridge。")

    resolved_root = root.resolve(strict=True)
    snapshot = backup_root.resolve(strict=True) / expected_snapshot_name
    unit_backup = resolved_root / "state" / "unit-backups" / unit_name
    adoption = resolved_root / "state" / adoption_name
    _require_regular_file(snapshot, "快照")
    _require_regular_file(unit_backup, "bridge unit 备份")
    _require_regular_file(adoption, "adoption 描述")
    if _sha256(snapshot) != snapshot_sha:
        raise ValueError("legacy 回滚快照哈希不匹配。")
    if _sha256(unit_backup) != unit_sha:
        raise ValueError("legacy 回滚 bridge unit 备份哈希不匹配。")
    if _sha256(adoption) != adoption_sha:
        raise ValueError("legacy 回滚 adoption 描述哈希不匹配。")
    adoption_payload = json.loads(adoption.read_text(encoding="utf-8"))
    expected_adoption = {
        "format": ADOPTION_FORMAT,
        "status": "active",
        "application_version": expected_source_version,
        "interpreter_version": interpreter_version,
        "original_current_link": current_link,
        "bridge_unit_sha256": bridge_sha,
    }
    if not isinstance(adoption_payload, dict) or any(
        adoption_payload.get(key) != value for key, value in expected_adoption.items()
    ):
        raise ValueError("legacy 回滚描述与 active adoption 组合不一致。")
    return (
        current_link,
        unit_name,
        adoption_name,
        expected_source_version,
        interpreter_version,
        bridge_sha,
    )


def main() -> int:
    try:
        if len(sys.argv) == 15 and sys.argv[1] == "create":
            create_descriptor(
                Path(sys.argv[2]),
                root=Path(sys.argv[3]),
                source_version=sys.argv[4],
                upgraded_to_version=sys.argv[5],
                original_current_link=sys.argv[6],
                bridge_unit_backup_name=sys.argv[7],
                snapshot_name=sys.argv[8],
                snapshot_sha256=sys.argv[9],
                bridge_unit_backup_sha256=sys.argv[10],
                adoption_descriptor_name=sys.argv[11],
                adoption_descriptor_sha256=sys.argv[12],
                interpreter_version=sys.argv[13],
                bridge_unit_sha256=sys.argv[14],
            )
        elif len(sys.argv) == 8 and sys.argv[1] == "validate":
            fields = validate_descriptor(
                Path(sys.argv[2]),
                root=Path(sys.argv[3]),
                backup_root=Path(sys.argv[4]),
                expected_source_version=sys.argv[5],
                expected_upgraded_to_version=sys.argv[6],
                expected_snapshot_name=sys.argv[7],
            )
            print(*fields, sep="\n")
        else:
            print("legacy 回滚描述工具参数无效。", file=sys.stderr)
            return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
