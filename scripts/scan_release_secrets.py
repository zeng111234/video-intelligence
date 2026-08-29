"""Compare local configured supplier secrets against release source files.

Only secret names and matching file paths are reported; values are never
printed. Git-ignored local configuration files are read only as comparison
inputs and are never added to the candidate release file list.
"""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import tomllib
from pathlib import Path

try:
    from scripts.verify_windows_release_payload import SENSITIVE_ENVIRONMENT_KEYS
except ModuleNotFoundError:  # Direct execution puts scripts/ on sys.path.
    from verify_windows_release_payload import SENSITIVE_ENVIRONMENT_KEYS


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return values
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"secret input is not a regular file: {path.name}")
    if metadata.st_size > 1024 * 1024:
        raise OSError(f"secret input exceeds 1 MiB: {path.name}")
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


def _parse_legacy_streamlit_secrets(path: Path) -> dict[str, str]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {}
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"secret input is not a regular file: {path.name}")
    if metadata.st_size > 1024 * 1024:
        raise OSError(f"secret input exceeds 1 MiB: {path.name}")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise OSError(f"secret input cannot be parsed: {path.name}") from exc
    return {str(key).upper(): str(value) for key, value in payload.items()}


def _configured_values(repository_root: Path) -> dict[str, bytes]:
    sources = [
        ("process", {key.upper(): value for key, value in os.environ.items()}),
        ("root-env", _parse_env_file(repository_root / ".env")),
        ("production-env", _parse_env_file(repository_root / ".env.production")),
        (
            "backend-env",
            _parse_env_file(repository_root / "project" / "backend" / ".env"),
        ),
        (
            "control-plane-env",
            _parse_env_file(repository_root / "deploy" / "control-plane" / ".env"),
        ),
        (
            "legacy-streamlit",
            _parse_legacy_streamlit_secrets(
                repository_root / ".streamlit" / "secrets.toml"
            ),
        ),
    ]
    ignored = {
        "change-me",
        "changeme",
        "placeholder",
        "replace-me",
        "your-secret-key-change-this",
        "change_me_at_least_16_characters",
        "postgres",
    }
    configured: dict[str, bytes] = {}
    for source, values in sources:
        for key in SENSITIVE_ENVIRONMENT_KEYS:
            value = str(values.get(key) or "").strip()
            if len(value) < 8 or value.casefold() in ignored:
                continue
            configured[f"{key}@{source}"] = value.encode("utf-8")
    return configured


def _release_candidates(repository_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=repository_root,
        check=True,
        capture_output=True,
    )
    candidates: list[Path] = []
    excluded_prefixes = (
        "work/",
        "node_modules/",
    )
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        if relative.replace("\\", "/").startswith(excluded_prefixes):
            continue
        path = repository_root / relative
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise OSError(f"release candidate is not a regular file: {relative}")
        candidates.append(path)
    return candidates


def _matching_secret_labels(
    path: Path,
    configured: dict[str, bytes],
    *,
    chunk_size: int = 1024 * 1024,
) -> list[str]:
    if not configured:
        return []
    remaining = dict(configured)
    matched: list[str] = []
    overlap = max(len(value) for value in configured.values()) - 1
    tail = b""
    with path.open("rb") as stream:
        while remaining:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            window = tail + chunk
            for label, value in list(remaining.items()):
                if value in window:
                    matched.append(label)
                    del remaining[label]
            tail = window[-overlap:] if overlap > 0 else b""
    return matched


def scan(repository_root: Path) -> tuple[int, list[str]]:
    configured = _configured_values(repository_root)
    if not configured:
        return 0, []
    matches: list[str] = []
    for path in _release_candidates(repository_root):
        for label in _matching_secret_labels(path, configured):
            matches.append(f"{label}:{path.relative_to(repository_root).as_posix()}")
    return len(configured), matches


def main() -> int:
    parser = argparse.ArgumentParser(description="发布源文件本机密钥比对")
    parser.add_argument("--repository-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    try:
        configured_count, matches = scan(root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"FAIL 无法完成发布源文件密钥比对：{type(exc).__name__}")
        return 1
    if matches:
        print("FAIL 发布源文件包含本机供应商密钥：")
        for match in matches:
            print(f"  {match}")
        return 1
    print(f"PASS 已比对 {configured_count} 个本机供应商密钥值；发布源文件命中 0。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
