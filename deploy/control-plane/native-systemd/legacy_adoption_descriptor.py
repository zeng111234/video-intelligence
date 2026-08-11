"""Create and validate the one-time mixed-layout legacy adoption record."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple


FORMAT = "videoinsight-native-legacy-adoption-v1"
PREPARED_STATUS = "prepared"
ACTIVE_STATUS = "active"
VALID_STATUSES = {PREPARED_STATUS, ACTIVE_STATUS}
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BACKUP_PATTERN = re.compile(
    r"^videoinsight-control-plane\.service\.legacy-evidence-[0-9a-f]{64}$"
)
MOUNT_ESCAPE_PATTERN = re.compile(r"\\([0-7]{3})")
UNSAFE_RECORD_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
EXPECTED_INTERPRETER_LINKS = {
    "bin/python": "/opt/videoinsight-control-plane/python/3.12.13/bin/python3",
    "bin/python3": "python",
    "bin/python3.12": "python",
    "lib64": "lib",
}
AUDITED_ROOT_ONLY_INTERPRETER_FILES = frozenset({".lock"})
DESCRIPTOR_KEYS = {
    "format",
    "status",
    "created_at",
    "application_version",
    "interpreter_version",
    "original_current_link",
    "original_unit_sha256",
    "original_unit_backup_name",
    "bridge_unit_sha256",
    "offline_python_sha256",
    "application_file_count",
    "application_tree_sha256",
    "interpreter_tree_entry_count",
    "interpreter_tree_sha256",
}


class AdoptionRecord(NamedTuple):
    application_version: str
    interpreter_version: str
    original_current_link: str
    original_unit_sha256: str
    original_unit_backup_name: str
    bridge_unit_sha256: str
    offline_python_sha256: str
    application_file_count: int
    application_tree_sha256: str
    interpreter_tree_entry_count: int
    interpreter_tree_sha256: str


def _sha256_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _open_regular_file(
    path: Path, flags: int = os.O_RDONLY
) -> tuple[int, os.stat_result]:
    descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"不是普通文件：{path}")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, metadata


def _sha256(path: Path) -> str:
    descriptor, _ = _open_regular_file(path)
    try:
        return _sha256_descriptor(descriptor)
    finally:
        os.close(descriptor)


def _read_regular_bytes(path: Path) -> bytes:
    descriptor, metadata = _open_regular_file(path)
    try:
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
            total += len(chunk)
        final_metadata = os.fstat(descriptor)
        if (
            total != metadata.st_size
            or final_metadata.st_dev != metadata.st_dev
            or final_metadata.st_ino != metadata.st_ino
            or final_metadata.st_size != metadata.st_size
            or final_metadata.st_mtime_ns != metadata.st_mtime_ns
            or final_metadata.st_ctime_ns != metadata.st_ctime_ns
        ):
            raise ValueError(f"普通文件读取期间发生变化：{path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_regular_file(path: Path, label: str) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise ValueError(f"{label}不是普通文件。")


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


def _service_permission_mask(
    metadata: os.stat_result, service_gid: int, bit: int
) -> int:
    return bit << (3 if metadata.st_gid == service_gid else 0)


def _validate_record_text(value: str, label: str) -> None:
    if UNSAFE_RECORD_CHARACTER_PATTERN.search(value):
        raise ValueError(f"{label}包含控制字符。")


def _validate_root_policy(
    root: Path, *, control_root: Path, service_gid: int, mounted: set[str]
) -> os.stat_result:
    if root.is_symlink():
        raise ValueError("legacy 树根目录不能是符号链接。")
    metadata = root.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or root.resolve(strict=True) != root:
        raise ValueError("legacy 树根目录不是规范普通目录。")
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ValueError("legacy 树根目录必须由 root 持有且组和其他用户不可写。")
    execute_mask = _service_permission_mask(metadata, service_gid, stat.S_IXOTH)
    if not metadata.st_mode & execute_mask:
        raise ValueError("固定服务身份无法遍历 legacy 树根目录。")
    root_text = str(root)
    if any(
        mount == root_text or mount.startswith(root_text + os.sep) for mount in mounted
    ):
        raise ValueError("legacy 树根目录或后代不能是独立挂载点。")
    resolved_control = control_root.resolve(strict=True)
    if control_root.is_symlink() or not resolved_control.is_dir():
        raise ValueError("固定控制层根目录无效。")
    if os.path.commonpath((root_text, str(resolved_control))) != str(resolved_control):
        raise ValueError("legacy 树越过固定控制层根目录。")
    current = root
    while True:
        current_metadata = current.lstat()
        if (
            current.is_symlink()
            or not stat.S_ISDIR(current_metadata.st_mode)
            or current_metadata.st_uid != 0
            or stat.S_IMODE(current_metadata.st_mode) & 0o022
            or current_metadata.st_dev != metadata.st_dev
            or str(current) in mounted
        ):
            raise ValueError(
                "legacy 树祖先必须同设备、root 持有且不可被组或其他用户修改。"
            )
        execute_mask = _service_permission_mask(
            current_metadata, service_gid, stat.S_IXOTH
        )
        if not current_metadata.st_mode & execute_mask:
            raise ValueError("固定服务身份无法遍历 legacy 树祖先。")
        if current == resolved_control:
            break
        if current.parent == current:
            raise ValueError("legacy 树祖先未到达固定控制层根目录。")
        current = current.parent
    return metadata


def _validate_entry_policy(
    entry: Path,
    metadata: os.stat_result,
    *,
    relative: str,
    root_device: int,
    service_gid: int,
    mounted: set[str],
    audited_root_only_files: frozenset[str] = frozenset(),
) -> None:
    _validate_record_text(relative, "legacy 树相对路径")
    if metadata.st_dev != root_device:
        raise ValueError("legacy 树跨越了文件系统。")
    if metadata.st_uid != 0:
        raise ValueError("legacy 树条目必须由 root 持有。")
    if not stat.S_ISLNK(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ValueError("legacy 树包含可由组或其他用户修改的条目。")
    if stat.S_ISDIR(metadata.st_mode):
        if str(entry.resolve(strict=True)) in mounted:
            raise ValueError("legacy 树包含子挂载。")
        execute_mask = _service_permission_mask(metadata, service_gid, stat.S_IXOTH)
        if not metadata.st_mode & execute_mask:
            raise ValueError("固定服务身份无法遍历 legacy 树目录。")
    elif stat.S_ISREG(metadata.st_mode):
        read_mask = _service_permission_mask(metadata, service_gid, stat.S_IROTH)
        is_audited_root_only_file = (
            relative in audited_root_only_files
            and metadata.st_gid == 0
            and stat.S_IMODE(metadata.st_mode) == 0o600
            and metadata.st_size == 0
        )
        if not metadata.st_mode & read_mask and not is_audited_root_only_file:
            raise ValueError("固定服务身份无法读取 legacy 树文件。")


def _tree_entries(root: Path) -> list[Path]:
    return sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())


def _require_version(value: object, label: str) -> str:
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise ValueError(f"{label}版本号无效。")
    return value


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{label} SHA256 无效。")
    return value


def _require_count(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label}数量无效。")
    return value


def application_tree_summary(
    root: Path, *, control_root: Path, service_uid: int, service_gid: int
) -> tuple[int, str]:
    """Hash the root and every descendant after enforcing the audited policy."""
    if service_uid <= 0 or service_gid <= 0:
        raise ValueError("固定服务 UID/GID 无效。")
    mounted = _mounted_paths()
    root_metadata = _validate_root_policy(
        root, control_root=control_root, service_gid=service_gid, mounted=mounted
    )
    digest = hashlib.sha256()
    digest.update(
        (
            f"d\t.\t{stat.S_IMODE(root_metadata.st_mode):04o}\t"
            f"{root_metadata.st_uid}\t{root_metadata.st_gid}\t\n"
        ).encode("utf-8")
    )
    count = 0
    for entry in _tree_entries(root):
        metadata = entry.lstat()
        relative = entry.relative_to(root).as_posix()
        _validate_entry_policy(
            entry,
            metadata,
            relative=relative,
            root_device=root_metadata.st_dev,
            service_gid=service_gid,
            mounted=mounted,
        )
        if stat.S_ISREG(metadata.st_mode):
            kind = "f"
            value = _sha256(entry)
            count += 1
        elif stat.S_ISDIR(metadata.st_mode):
            kind = "d"
            value = ""
        else:
            raise ValueError("legacy application 树只能包含普通文件和目录。")
        digest.update(
            (
                f"{kind}\t{relative}\t{stat.S_IMODE(metadata.st_mode):04o}\t"
                f"{metadata.st_uid}\t{metadata.st_gid}\t{value}\n"
            ).encode("utf-8")
        )
    if count == 0:
        raise ValueError("legacy application 树为空。")
    return count, digest.hexdigest()


def interpreter_tree_summary(
    root: Path,
    *,
    control_root: Path,
    service_uid: int,
    service_gid: int,
    offline_python: Path,
) -> tuple[int, str]:
    """Hash the interpreter tree and enforce its exact symlink allowlist."""
    if service_uid <= 0 or service_gid <= 0:
        raise ValueError("固定服务 UID/GID 无效。")
    mounted = _mounted_paths()
    root_metadata = _validate_root_policy(
        root, control_root=control_root, service_gid=service_gid, mounted=mounted
    )
    digest = hashlib.sha256()
    digest.update(
        (
            f"d\t.\t{stat.S_IMODE(root_metadata.st_mode):04o}\t"
            f"{root_metadata.st_uid}\t{root_metadata.st_gid}\t\n"
        ).encode("utf-8")
    )
    count = 0
    seen_links: dict[str, str] = {}
    for entry in _tree_entries(root):
        metadata = entry.lstat()
        relative = entry.relative_to(root).as_posix()
        _validate_entry_policy(
            entry,
            metadata,
            relative=relative,
            root_device=root_metadata.st_dev,
            service_gid=service_gid,
            mounted=mounted,
            audited_root_only_files=AUDITED_ROOT_ONLY_INTERPRETER_FILES,
        )
        if stat.S_ISREG(metadata.st_mode):
            kind = "f"
            value = _sha256(entry)
        elif stat.S_ISDIR(metadata.st_mode):
            kind = "d"
            value = ""
        elif stat.S_ISLNK(metadata.st_mode):
            kind = "l"
            value = os.readlink(entry)
            _validate_record_text(value, "legacy interpreter 符号链接目标")
            if EXPECTED_INTERPRETER_LINKS.get(relative) != value:
                raise ValueError("legacy interpreter 包含未审计的符号链接。")
            resolved = entry.resolve(strict=True)
            if relative == "lib64":
                if resolved != (root / "lib").resolve(strict=True):
                    raise ValueError("legacy interpreter lib64 未解析到固定 lib。")
            elif resolved != offline_python.resolve(strict=True):
                raise ValueError(
                    "legacy interpreter Python 链接未解析到固定离线 Python。"
                )
            seen_links[relative] = value
        else:
            raise ValueError("legacy interpreter 树包含未允许的特殊文件。")
        record = (
            f"{kind}\t{relative}\t{stat.S_IMODE(metadata.st_mode):04o}\t"
            f"{metadata.st_uid}\t{metadata.st_gid}\t{value}\n"
        )
        digest.update(record.encode("utf-8"))
        count += 1
    if count == 0:
        raise ValueError("legacy interpreter 树为空。")
    if seen_links != EXPECTED_INTERPRETER_LINKS:
        raise ValueError("legacy interpreter 缺少精确的符号链接集合。")
    return count, digest.hexdigest()


def _parse_record(
    payload: object, expected_status: str | None = ACTIVE_STATUS
) -> tuple[str, AdoptionRecord]:
    if not isinstance(payload, dict) or set(payload) != DESCRIPTOR_KEYS:
        raise ValueError("legacy adoption 描述字段不完整或包含额外字段。")
    status_value = payload.get("status")
    if (
        payload.get("format") != FORMAT
        or not isinstance(status_value, str)
        or status_value not in VALID_STATUSES
    ):
        raise ValueError("legacy adoption 描述不是 prepared/active 固定格式。")
    if expected_status is not None and status_value != expected_status:
        raise ValueError(f"legacy adoption 描述不是 {expected_status} 状态。")
    created_at = payload.get("created_at")
    if not isinstance(created_at, str):
        raise ValueError("legacy adoption 创建时间无效。")
    try:
        created_time = datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise ValueError("legacy adoption 创建时间无效。") from exc
    if (
        created_time.tzinfo is None
        or created_time.utcoffset() != timezone.utc.utcoffset(None)
    ):
        raise ValueError("legacy adoption 创建时间必须是 UTC。")
    backup_name = payload.get("original_unit_backup_name")
    if not isinstance(backup_name, str) or not BACKUP_PATTERN.fullmatch(backup_name):
        raise ValueError("legacy adoption 原 unit 证据文件名无效。")
    current_link = payload.get("original_current_link")
    if not isinstance(current_link, str):
        raise ValueError("legacy adoption 原 current 链接无效。")
    _validate_record_text(current_link, "legacy adoption 原 current 链接")
    record = AdoptionRecord(
        application_version=_require_version(
            payload.get("application_version"), "application"
        ),
        interpreter_version=_require_version(
            payload.get("interpreter_version"), "interpreter"
        ),
        original_current_link=current_link,
        original_unit_sha256=_require_sha256(
            payload.get("original_unit_sha256"), "原 unit"
        ),
        original_unit_backup_name=backup_name,
        bridge_unit_sha256=_require_sha256(
            payload.get("bridge_unit_sha256"), "bridge unit"
        ),
        offline_python_sha256=_require_sha256(
            payload.get("offline_python_sha256"), "离线 Python"
        ),
        application_file_count=_require_count(
            payload.get("application_file_count"), "application 文件"
        ),
        application_tree_sha256=_require_sha256(
            payload.get("application_tree_sha256"), "application 树"
        ),
        interpreter_tree_entry_count=_require_count(
            payload.get("interpreter_tree_entry_count"), "interpreter 树条目"
        ),
        interpreter_tree_sha256=_require_sha256(
            payload.get("interpreter_tree_sha256"), "interpreter 树"
        ),
    )
    return status_value, record


def _load_record(
    descriptor: Path, expected_status: str | None = ACTIVE_STATUS
) -> tuple[str, AdoptionRecord]:
    try:
        payload = json.loads(_read_regular_bytes(descriptor).decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("legacy adoption 描述不是 UTF-8。") from exc
    return _parse_record(payload, expected_status)


def _validate_record_paths(
    record: AdoptionRecord,
    *,
    root: Path,
    unit_path: Path,
    offline_python: Path,
    service_uid: int,
    service_gid: int,
    require_active_runtime: bool,
) -> None:
    resolved_root = root.resolve(strict=True)
    app = resolved_root / "releases" / record.application_version / "app"
    interpreter_root = resolved_root / "releases" / record.interpreter_version / "venv"
    expected_current_link = str(app)
    if record.original_current_link != expected_current_link:
        raise ValueError("legacy adoption current 未精确绑定 application 版本。")
    if app.is_symlink() or app.resolve(strict=True) != app:
        raise ValueError("legacy adoption application 路径不规范。")
    if (
        interpreter_root.is_symlink()
        or interpreter_root.resolve(strict=True) != interpreter_root
    ):
        raise ValueError("legacy adoption interpreter 路径不规范。")

    _require_regular_file(offline_python, "离线 Python")
    if _sha256(offline_python) != record.offline_python_sha256:
        raise ValueError("legacy adoption 离线 Python 哈希不匹配。")
    interpreter = interpreter_root / "bin" / "python"
    if not interpreter.is_symlink() or interpreter.resolve(
        strict=True
    ) != offline_python.resolve(strict=True):
        raise ValueError("legacy adoption interpreter 未解析到固定离线 Python。")

    app_count, app_digest = application_tree_summary(
        app,
        control_root=resolved_root,
        service_uid=service_uid,
        service_gid=service_gid,
    )
    if (app_count, app_digest) != (
        record.application_file_count,
        record.application_tree_sha256,
    ):
        raise ValueError("legacy adoption application 树摘要不匹配。")
    venv_count, venv_digest = interpreter_tree_summary(
        interpreter_root,
        control_root=resolved_root,
        service_uid=service_uid,
        service_gid=service_gid,
        offline_python=offline_python,
    )
    if (venv_count, venv_digest) != (
        record.interpreter_tree_entry_count,
        record.interpreter_tree_sha256,
    ):
        raise ValueError("legacy adoption interpreter 树摘要不匹配。")

    evidence = (
        resolved_root / "state" / "legacy-adoption" / record.original_unit_backup_name
    )
    _require_regular_file(evidence, "legacy adoption 原 unit 证据")
    if _sha256(evidence) != record.original_unit_sha256:
        raise ValueError("legacy adoption 原 unit 证据哈希不匹配。")

    if require_active_runtime:
        current = resolved_root / "current"
        if (
            not current.is_symlink()
            or os.readlink(current) != record.original_current_link
        ):
            raise ValueError("legacy adoption active current 已改变。")
        if current.resolve(strict=True) != app:
            raise ValueError("legacy adoption active current 未解析到 application。")
        _require_regular_file(unit_path, "legacy adoption bridge unit")
        if _sha256(unit_path) != record.bridge_unit_sha256:
            raise ValueError("legacy adoption bridge unit 哈希不匹配。")


def create_descriptor(
    descriptor: Path,
    *,
    root: Path,
    application_version: str,
    interpreter_version: str,
    original_current_link: str,
    original_unit_sha256: str,
    original_unit_backup_name: str,
    bridge_unit_sha256: str,
    offline_python_sha256: str,
    application_file_count: int,
    application_tree_sha256: str,
    interpreter_tree_entry_count: int,
    interpreter_tree_sha256: str,
) -> None:
    record = AdoptionRecord(
        application_version=_require_version(application_version, "application"),
        interpreter_version=_require_version(interpreter_version, "interpreter"),
        original_current_link=original_current_link,
        original_unit_sha256=_require_sha256(original_unit_sha256, "原 unit"),
        original_unit_backup_name=original_unit_backup_name,
        bridge_unit_sha256=_require_sha256(bridge_unit_sha256, "bridge unit"),
        offline_python_sha256=_require_sha256(offline_python_sha256, "离线 Python"),
        application_file_count=_require_count(
            application_file_count, "application 文件"
        ),
        application_tree_sha256=_require_sha256(
            application_tree_sha256, "application 树"
        ),
        interpreter_tree_entry_count=_require_count(
            interpreter_tree_entry_count, "interpreter 树条目"
        ),
        interpreter_tree_sha256=_require_sha256(
            interpreter_tree_sha256, "interpreter 树"
        ),
    )
    if not BACKUP_PATTERN.fullmatch(record.original_unit_backup_name):
        raise ValueError("legacy adoption 原 unit 证据文件名无效。")
    expected_current = str(
        root.resolve(strict=True) / "releases" / application_version / "app"
    )
    if original_current_link != expected_current:
        raise ValueError("legacy adoption 原 current 参数不是精确绝对路径。")
    payload = {
        "format": FORMAT,
        "status": PREPARED_STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **record._asdict(),
    }
    with descriptor.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    _fsync_directory(descriptor.parent)


def promote_descriptor(
    source: Path,
    destination: Path,
    *,
    root: Path,
    unit_path: Path,
    offline_python: Path,
    service_uid: int,
    service_gid: int,
) -> None:
    try:
        payload = json.loads(_read_regular_bytes(source).decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("legacy adoption prepared 描述不是 UTF-8。") from exc
    _, record = _parse_record(payload, PREPARED_STATUS)
    _validate_record_paths(
        record,
        root=root,
        unit_path=unit_path,
        offline_python=offline_python,
        service_uid=service_uid,
        service_gid=service_gid,
        require_active_runtime=True,
    )
    if not isinstance(payload, dict):
        raise ValueError("legacy adoption prepared 描述在晋升前发生字段漂移。")
    payload["status"] = ACTIVE_STATUS
    with destination.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    _fsync_directory(destination.parent)


def copy_evidence(source: Path, destination: Path, expected_sha256: str) -> None:
    _require_sha256(expected_sha256, "原 unit")
    source_descriptor, source_before = _open_regular_file(source)
    destination_descriptor = -1
    destination_created = False
    try:
        destination_descriptor = os.open(
            destination,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        destination_created = True
        digest = hashlib.sha256()
        copied = 0
        while chunk := os.read(source_descriptor, 1024 * 1024):
            digest.update(chunk)
            copied += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    raise OSError("原 unit 证据写入未取得进展。")
                view = view[written:]
        source_after = os.fstat(source_descriptor)
        if (
            digest.hexdigest() != expected_sha256
            or copied != source_before.st_size
            or source_after.st_dev != source_before.st_dev
            or source_after.st_ino != source_before.st_ino
            or source_after.st_size != source_before.st_size
            or source_after.st_mtime_ns != source_before.st_mtime_ns
            or source_after.st_ctime_ns != source_before.st_ctime_ns
        ):
            raise ValueError("原 unit SHA256 与显式证据不一致或复制期间发生变化。")
        os.fsync(destination_descriptor)
        os.lseek(destination_descriptor, 0, os.SEEK_SET)
        if _sha256_descriptor(destination_descriptor) != expected_sha256:
            raise ValueError("原 unit 取证目标哈希不一致。")
    except BaseException:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
            destination_descriptor = -1
        if destination_created:
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        os.close(source_descriptor)
    os.close(destination_descriptor)
    _fsync_directory(destination.parent)


def validate_descriptor(
    descriptor: Path,
    *,
    root: Path,
    unit_path: Path,
    offline_python: Path,
    service_uid: int,
    service_gid: int,
    require_active_runtime: bool,
    expected_status: str = ACTIVE_STATUS,
) -> AdoptionRecord:
    _, record = _load_record(descriptor, expected_status)
    _validate_record_paths(
        record,
        root=root,
        unit_path=unit_path,
        offline_python=offline_python,
        service_uid=service_uid,
        service_gid=service_gid,
        require_active_runtime=require_active_runtime,
    )
    return record


def _print_record(record: AdoptionRecord, descriptor: Path) -> None:
    for value in (
        record.application_version,
        record.interpreter_version,
        record.original_current_link,
        record.original_unit_backup_name,
        record.original_unit_sha256,
        record.bridge_unit_sha256,
        _sha256(descriptor),
        str(record.application_file_count),
        record.application_tree_sha256,
        str(record.interpreter_tree_entry_count),
        record.interpreter_tree_sha256,
    ):
        print(value)


def main() -> int:
    try:
        command = sys.argv[1] if len(sys.argv) > 1 else ""
        if command == "summary" and len(sys.argv) == 7:
            root = Path(sys.argv[2])
            app = root / "releases" / sys.argv[3] / "app"
            venv = root / "releases" / sys.argv[4] / "venv"
            service_uid = int(sys.argv[5])
            service_gid = int(sys.argv[6])
            app_count, app_digest = application_tree_summary(
                app,
                control_root=root,
                service_uid=service_uid,
                service_gid=service_gid,
            )
            venv_count, venv_digest = interpreter_tree_summary(
                venv,
                control_root=root,
                service_uid=service_uid,
                service_gid=service_gid,
                offline_python=root / "python" / "3.12.13" / "bin" / "python3.12",
            )
            print(app_count)
            print(app_digest)
            print(venv_count)
            print(venv_digest)
        elif command == "copy-evidence" and len(sys.argv) == 5:
            copy_evidence(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
        elif command == "create-prepared" and len(sys.argv) == 15:
            create_descriptor(
                Path(sys.argv[2]),
                root=Path(sys.argv[3]),
                application_version=sys.argv[4],
                interpreter_version=sys.argv[5],
                original_current_link=sys.argv[6],
                original_unit_sha256=sys.argv[7],
                original_unit_backup_name=sys.argv[8],
                bridge_unit_sha256=sys.argv[9],
                offline_python_sha256=sys.argv[10],
                application_file_count=int(sys.argv[11]),
                application_tree_sha256=sys.argv[12],
                interpreter_tree_entry_count=int(sys.argv[13]),
                interpreter_tree_sha256=sys.argv[14],
            )
        elif command == "promote" and len(sys.argv) == 9:
            promote_descriptor(
                Path(sys.argv[2]),
                Path(sys.argv[3]),
                root=Path(sys.argv[4]),
                unit_path=Path(sys.argv[5]),
                offline_python=Path(sys.argv[6]),
                service_uid=int(sys.argv[7]),
                service_gid=int(sys.argv[8]),
            )
        elif command == "status" and len(sys.argv) == 3:
            status_value, _ = _load_record(Path(sys.argv[2]), None)
            print(status_value)
        elif (
            command
            in {
                "validate-prepared",
                "validate-record",
                "validate-active",
            }
            and len(sys.argv) == 8
        ):
            descriptor = Path(sys.argv[2])
            expected_status = (
                PREPARED_STATUS if command == "validate-prepared" else ACTIVE_STATUS
            )
            record = validate_descriptor(
                descriptor,
                root=Path(sys.argv[3]),
                unit_path=Path(sys.argv[4]),
                offline_python=Path(sys.argv[5]),
                service_uid=int(sys.argv[6]),
                service_gid=int(sys.argv[7]),
                expected_status=expected_status,
                require_active_runtime=command == "validate-active",
            )
            _print_record(record, descriptor)
        else:
            print("legacy adoption 描述工具参数无效。", file=sys.stderr)
            return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
