"""Fail-closed checks for the unpacked Windows customer application."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit


SENSITIVE_ENVIRONMENT_KEYS = (
    "DASHSCOPE_API_KEY",
    "ALIYUN_MODEL_STUDIO_WORKSPACE_ID",
    "ALIBABA_CLOUD_ACCESS_KEY_ID",
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    "ALIYUN_ACCESS_KEY_ID",
    "ALIYUN_ACCESS_KEY_SECRET",
    "ALIYUN_ASR_ACCESS_KEY_ID",
    "ALIYUN_ASR_ACCESS_KEY_SECRET",
    "ALIYUN_ASR_APP_KEY",
    "ONEAPI_API_KEY",
    "DOUYIN_CLIENT_KEY",
    "DOUYIN_CLIENT_SECRET",
    "COPYWRITING_API_KEY",
    "OPENAI_API_KEY",
    "AVATAR_API_KEY",
    "SHUYING_AVATAR_API_CODE",
    "AVATAR_SERVICE_TOKEN",
    "BAIDU_XILING_APP_ID",
    "BAIDU_XILING_APP_KEY",
    "PUBLISH_DOUYIN_CLIENT_KEY",
    "PUBLISH_DOUYIN_CLIENT_SECRET",
    "PUBLISH_DOUYIN_ACCESS_TOKEN",
    "PUBLISH_DOUYIN_REFRESH_TOKEN",
    "PUBLISH_KUAISHOU_CLIENT_KEY",
    "PUBLISH_KUAISHOU_CLIENT_SECRET",
    "PUBLISH_KUAISHOU_ACCESS_TOKEN",
    "PUBLISH_WECHAT_CHANNELS_CLIENT_KEY",
    "PUBLISH_WECHAT_CHANNELS_CLIENT_SECRET",
    "PUBLISH_WECHAT_CHANNELS_ACCESS_TOKEN",
    "PUBLISH_XIAOHONGSHU_CLIENT_KEY",
    "PUBLISH_XIAOHONGSHU_CLIENT_SECRET",
    "PUBLISH_XIAOHONGSHU_ACCESS_TOKEN",
)
FORBIDDEN_DIRECTORY_NAMES = {
    ".git",
    "backups",
    "browser_profiles",
    "data",
    "logs",
    "outputs",
    "uploads",
}
FORBIDDEN_EXTENSIONS = {
    ".db",
    ".key",
    ".log",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".p12",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".wav",
    ".webm",
}


class ReleasePayloadError(ValueError):
    pass


def _production_origin(value: str) -> str:
    origin = value.strip().rstrip("/")
    parsed = urlsplit(origin)
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or hostname == "localhost"
        or hostname in {"127.0.0.1", "::1"}
        or hostname in {"example.com", "example.net", "example.org"}
        or any(
            hostname.endswith(suffix)
            for suffix in (
                ".localhost",
                ".test",
                ".invalid",
                ".example",
                ".example.com",
                ".example.net",
                ".example.org",
            )
        )
    ):
        raise ReleasePayloadError("最终客户包必须使用正式的非本机 HTTPS 公司服务地址。")
    return origin


def _read_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleasePayloadError(f"无法读取发布配置：{path}") from exc
    if not isinstance(payload, dict):
        raise ReleasePayloadError(f"发布配置必须是 JSON 对象：{path}")
    return payload


def _configured_secrets(environment: Mapping[str, str]) -> dict[str, bytes]:
    patterns: dict[str, bytes] = {}
    ignored = {"change-me", "changeme", "placeholder", "replace-me"}
    for key in SENSITIVE_ENVIRONMENT_KEYS:
        value = str(environment.get(key) or "").strip()
        if len(value) < 8 or value.casefold() in ignored:
            continue
        patterns[key] = value.encode("utf-8")
    return patterns


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        return values
    for raw_line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().upper()
        value = value.strip()
        if len(value) >= 2 and value[:1] == value[-1:] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _scan_files(
    files: list[Path], patterns: Mapping[str, tuple[bytes, ...]]
) -> dict[str, Path]:
    remaining = dict(patterns)
    matches: dict[str, Path] = {}
    if not remaining:
        return matches
    max_pattern_length = max(
        len(value) for values in remaining.values() for value in values
    )
    overlap = max_pattern_length - 1
    for path in files:
        tail = b""
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                window = tail + chunk
                found = [
                    name
                    for name, values in remaining.items()
                    if any(value in window for value in values)
                ]
                for name in found:
                    matches[name] = path
                    remaining.pop(name)
                if not remaining:
                    return matches
                tail = window[-overlap:]
    return matches


def verify_release_payload(
    package_root: Path,
    *,
    control_plane_url: str,
    version: str,
    environment: Mapping[str, str] | None = None,
    additional_secret_sources: Mapping[str, Mapping[str, str]] | None = None,
) -> list[str]:
    root = package_root.resolve()
    origin = _production_origin(control_plane_url)
    required_files = {
        "桌面程序": root / "VideoInsight.exe",
        "本地服务": root / "resources" / "backend" / "VideoInsightBackend.exe",
        "桌面发布配置": root / "resources" / "config" / "release.json",
        "本地服务配置": (
            root
            / "resources"
            / "backend"
            / "_internal"
            / "config"
            / "desktop-control-plane.json"
        ),
    }
    missing = [name for name, path in required_files.items() if not path.is_file()]
    if missing:
        raise ReleasePayloadError(f"Windows 客户包缺少必要文件：{', '.join(missing)}")

    release_config = _read_object(required_files["桌面发布配置"])
    backend_config = _read_object(required_files["本地服务配置"])
    if (
        str(release_config.get("current_version") or "") != version
        or str(release_config.get("control_plane_url") or "").rstrip("/") != origin
    ):
        raise ReleasePayloadError("桌面更新配置与本次正式版本不一致。")
    if (
        backend_config.get("enabled") is not True
        or str(backend_config.get("control_plane_url") or "").rstrip("/") != origin
    ):
        raise ReleasePayloadError("本地服务未被锁定到本次正式公司服务。")

    files = [path for path in root.rglob("*") if path.is_file()]
    forbidden: list[str] = []
    for path in files:
        relative = path.relative_to(root)
        lowered_parts = {part.casefold() for part in relative.parts[:-1]}
        name = path.name.casefold()
        if (
            lowered_parts & FORBIDDEN_DIRECTORY_NAMES
            or name == ".env"
            or name.startswith(".env.")
            or path.suffix.casefold() in FORBIDDEN_EXTENSIONS
        ):
            forbidden.append(relative.as_posix())
    if forbidden:
        raise ReleasePayloadError(
            "Windows 客户包包含不允许的密钥、运行数据或媒体文件："
            + ", ".join(forbidden[:10])
        )

    configured = _configured_secrets(environment or os.environ)
    for source, values in (additional_secret_sources or {}).items():
        for key, value in _configured_secrets(values).items():
            configured[f"{key}@{source}"] = value
    scan_patterns: dict[str, tuple[bytes, ...]] = {
        "PRIVATE_KEY": (
            b"-----BEGIN PRIVATE KEY-----",
            b"-----BEGIN RSA PRIVATE KEY-----",
            b"-----BEGIN EC PRIVATE KEY-----",
            b"-----BEGIN OPENSSH PRIVATE KEY-----",
        )
    }
    for key, utf8_value in configured.items():
        scan_patterns[key] = (
            utf8_value,
            utf8_value.decode("utf-8").encode("utf-16-le"),
            utf8_value.decode("utf-8").encode("utf-16-be"),
        )
    matches = _scan_files(files, scan_patterns)
    leaked = [
        f"{key}:{path.relative_to(root).as_posix()}"
        for key, path in matches.items()
    ]
    if leaked:
        raise ReleasePayloadError(
            "Windows 客户包包含构建机供应商密钥，已停止：" + ", ".join(leaked)
        )

    return [
        f"公司服务={origin}",
        f"版本={version}",
        f"文件数={len(files)}",
        f"已核对构建机密钥={len(configured)}",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 Windows 客户包安全边界")
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--control-plane-url", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--secret-env-file", action="append", type=Path, default=[])
    args = parser.parse_args()
    secret_sources = {
        f"{path.parent.name}-{path.name}": _parse_env_file(path.resolve())
        for path in args.secret_env_file
        if path.is_file()
    }
    try:
        evidence = verify_release_payload(
            args.package_root,
            control_plane_url=args.control_plane_url,
            version=args.version,
            additional_secret_sources=secret_sources,
        )
    except ReleasePayloadError as exc:
        print(f"FAIL {exc}")
        return 1
    for item in evidence:
        print(f"PASS {item}")
    print("PASS Windows 客户包未包含运行数据、媒体文件或构建机供应商密钥。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
