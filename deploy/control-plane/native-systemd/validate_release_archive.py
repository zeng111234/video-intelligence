"""Validate and extract one immutable control-plane source archive."""

from __future__ import annotations

import json
import re
import shutil
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath


REQUIRED_FILES = {
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
    "deploy/control-plane/native-systemd/README.md",
    "deploy/control-plane/native-systemd/common.sh",
    "deploy/control-plane/native-systemd/install_unit.sh",
    "deploy/control-plane/native-systemd/legacy_rollback_descriptor.py",
    "deploy/control-plane/native-systemd/preflight.sh",
    "deploy/control-plane/native-systemd/rollback.sh",
    "deploy/control-plane/native-systemd/upgrade.sh",
    "deploy/control-plane/native-systemd/validate_release_archive.py",
    "deploy/control-plane/native-systemd/verify.sh",
    "deploy/control-plane/native-systemd/videoinsight-control-plane.service",
    "deploy/control-plane/native-systemd/wheelhouse.sha256",
}
FORBIDDEN_DIRECTORIES = {
    "data",
    "logs",
    "backups",
    "updates",
    "caddy-data",
    "caddy-config",
}
FORBIDDEN_EXTENSIONS = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".mp3",
    ".wav",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".der",
    ".p8",
    ".ppk",
}
PRIVATE_KEY_MARKERS = tuple(
    b"-----BEGIN " + key_kind + b"PRIVATE KEY-----"
    for key_kind in (b"", b"ENCRYPTED ", b"RSA ", b"DSA ", b"EC ", b"OPENSSH ")
) + (b"PuTTY-" + b"User-Key-File:",)
SECRET_SCAN_CHUNK_BYTES = 1024 * 1024
SECRET_SCAN_OVERLAP_BYTES = max(map(len, PRIVATE_KEY_MARKERS)) - 1
MAX_FILES = 5000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?$")
REQUIREMENT_PATTERN = re.compile(
    r"^([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([^ ]+)$"
)
LOCK_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)==([^ ]+) --hash=sha256:([0-9a-f]{64})$")
WHEEL_MANIFEST_PATTERN = re.compile(
    r"^([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._+-]*\.whl)$"
)


def _validate_expected_version(expected_version: str) -> bytes:
    if not VERSION_PATTERN.fullmatch(expected_version):
        raise ValueError("预期版本号格式无效。")
    return expected_version.encode("utf-8")


def _normalized_project_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _decoded_lines(archive: zipfile.ZipFile, name: str) -> list[str]:
    try:
        payload = archive.read(name).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("离线依赖清单必须使用 UTF-8。") from exc
    lines = payload.splitlines()
    if not lines or any(not line for line in lines):
        raise ValueError("离线依赖清单不能为空或包含空行。")
    return lines


def _validate_dependency_lock(archive: zipfile.ZipFile) -> None:
    requirements: dict[str, str] = {}
    for line in _decoded_lines(archive, "deploy/control-plane/requirements.txt"):
        match = REQUIREMENT_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError("控制层 requirements 必须逐项使用精确 == 版本。")
        name, version = match.groups()
        normalized = _normalized_project_name(name)
        if normalized in requirements:
            raise ValueError("控制层 requirements 包含重复依赖。")
        requirements[normalized] = version

    locked: dict[str, tuple[str, str]] = {}
    for line in _decoded_lines(archive, "deploy/control-plane/requirements.lock"):
        match = LOCK_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError("控制层 requirements.lock 必须逐项固定版本和 SHA256。")
        name, version, digest = match.groups()
        normalized = _normalized_project_name(name)
        if normalized in locked:
            raise ValueError("控制层 requirements.lock 包含重复依赖。")
        locked[normalized] = (version, digest)
    for name, version in requirements.items():
        if name not in locked or locked[name][0] != version:
            raise ValueError("控制层 requirements 与哈希锁版本不一致。")

    manifest_hashes: list[str] = []
    manifest_names: set[str] = set()
    for line in _decoded_lines(
        archive, "deploy/control-plane/native-systemd/wheelhouse.sha256"
    ):
        match = WHEEL_MANIFEST_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError("wheelhouse SHA256 清单格式无效。")
        digest, filename = match.groups()
        if filename in manifest_names:
            raise ValueError("wheelhouse SHA256 清单包含重复文件。")
        manifest_names.add(filename)
        manifest_hashes.append(digest)
    lock_hashes = [digest for _, digest in locked.values()]
    if sorted(lock_hashes) != sorted(manifest_hashes):
        raise ValueError("requirements.lock 与 wheelhouse SHA256 清单不一致。")


def _contains_local_path(value: object) -> bool:
    if isinstance(value, dict):
        if any(str(key).casefold() == "sample_path" for key in value):
            return True
        return any(_contains_local_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_local_path(item) for item in value)
    if not isinstance(value, str):
        return False
    return bool(
        value.startswith(("/", "\\\\", "file://"))
        or re.match(r"^[A-Za-z]:[\\/]", value)
    )


def _validate_bootstrap_manifest(archive: zipfile.ZipFile) -> None:
    name = "deploy/control-plane/bootstrap/avatar_assets/shuying_cloud.json"
    try:
        payload = json.loads(archive.read(name))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("数字人 bootstrap 清单不是有效 JSON。") from exc
    if not isinstance(payload, dict) or set(payload) != {"assets"}:
        raise ValueError("数字人 bootstrap 清单字段无效。")
    assets = payload.get("assets")
    if not isinstance(assets, list) or len(assets) != 2:
        raise ValueError("数字人 bootstrap 必须恰好包含一组形象和声音。")
    expected = {
        "avatar": ("shuying-avatar-21920", "21920", "custom"),
        "voice": ("shuying-voice-7869", "7869", "custom_clone"),
    }
    seen: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("数字人 bootstrap 资产记录无效。")
        kind = asset.get("kind")
        if kind not in expected or kind in seen:
            raise ValueError("数字人 bootstrap 资产类型重复或未知。")
        seen.add(kind)
        asset_id, provider_asset_id, source_type = expected[kind]
        if (
            asset.get("asset_id") != asset_id
            or asset.get("provider_asset_id") != provider_asset_id
            or asset.get("source_type") != source_type
            or asset.get("name") != "大树1"
            or asset.get("authorized") is not True
            or asset.get("shared") is not True
            or asset.get("status") != "ready"
        ):
            raise ValueError("数字人 bootstrap 未绑定已授权的大树1资产。")
    if seen != set(expected) or _contains_local_path(payload):
        raise ValueError("数字人 bootstrap 包含本机路径或缺少目标资产。")


def _scan_member_content(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    *,
    reject_carriage_returns: bool,
) -> None:
    overlap = b""
    with archive.open(member) as source:
        while chunk := source.read(SECRET_SCAN_CHUNK_BYTES):
            window = overlap + chunk
            if reject_carriage_returns and b"\r" in window:
                raise ValueError("部署 ZIP 的 shell 脚本必须使用 LF 换行。")
            if any(marker in window for marker in PRIVATE_KEY_MARKERS):
                raise ValueError("部署 ZIP 包含私钥内容。")
            overlap = window[-SECRET_SCAN_OVERLAP_BYTES:]


def _validated_members(
    archive: zipfile.ZipFile, expected_version: str
) -> list[zipfile.ZipInfo]:
    expected_version_bytes = _validate_expected_version(expected_version)
    members = archive.infolist()
    if not 20 <= len(members) <= MAX_FILES:
        raise ValueError("部署 ZIP 文件数量异常。")

    seen: set[str] = set()
    total = 0
    for member in members:
        name = member.filename
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or any(ord(character) < 32 for character in name)
            or path.is_absolute()
            or ".." in path.parts
            or name in seen
            or member.flag_bits & 0x1
        ):
            raise ValueError("部署 ZIP 包含不安全路径、重复项或加密项。")
        seen.add(name)
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError("部署 ZIP 不能包含符号链接。")
        total += member.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("部署 ZIP 解压后超过 2GB。")
        if member.is_dir():
            continue
        leaf = path.name.casefold()
        if (
            FORBIDDEN_DIRECTORIES.intersection(part.casefold() for part in path.parts)
            or path.suffix.casefold() in FORBIDDEN_EXTENSIONS
            or leaf == ".env"
            or (leaf.startswith(".env.") and leaf != ".env.example")
        ):
            raise ValueError("部署 ZIP 包含运行数据、密钥文件或媒体。")
        _scan_member_content(
            archive,
            member,
            reject_carriage_returns=path.suffix.casefold() == ".sh",
        )

    if not REQUIRED_FILES.issubset(seen):
        raise ValueError("部署 ZIP 缺少控制层必要文件。")
    version_member = archive.getinfo("release_version.txt")
    if archive.read(version_member) != expected_version_bytes:
        raise ValueError("部署 ZIP 内版本标识与预期版本不一致。")
    _validate_dependency_lock(archive)
    _validate_bootstrap_manifest(archive)
    return members


def extract_validated_archive(
    archive_path: Path, target_root: Path, expected_version: str
) -> None:
    """Extract regular files only after every archive member has passed validation."""

    resolved_target = target_root.resolve()
    if not resolved_target.is_dir() or target_root.is_symlink():
        raise ValueError("部署暂存目录不存在或是符号链接。")
    if any(resolved_target.iterdir()):
        raise ValueError("部署暂存目录必须为空。")

    with zipfile.ZipFile(archive_path) as archive:
        members = _validated_members(archive, expected_version)
        for member in members:
            if member.is_dir():
                continue
            path = PurePosixPath(member.filename)
            destination = (resolved_target / Path(*path.parts)).resolve()
            if resolved_target not in destination.parents:
                raise ValueError("部署 ZIP 解压目标越界。")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    if (
        resolved_target / "release_version.txt"
    ).read_bytes() != _validate_expected_version(expected_version):
        raise ValueError("解压后的版本标识与预期版本不一致。")


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "用法：validate_release_archive.py <部署 ZIP> <空暂存目录> <预期版本>",
            file=sys.stderr,
        )
        return 2
    try:
        extract_validated_archive(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
