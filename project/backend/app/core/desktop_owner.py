"""Bind one desktop runtime to the first authenticated customer workspace.

Customer business data stays on the Windows computer while authentication and
billing live on the company control plane.  A desktop runtime must therefore
not let a second activation code inherit the first customer's local database
and media.  The binding stores only a domain-separated digest, never the
activation code itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path

from project.backend.app.core.config import RUNTIME_ROOT

_BINDING_VERSION = 1
_DIGEST_PATTERN = re.compile(r"[a-f0-9]{64}")
_BINDING_LOCK = threading.Lock()


def _binding_path() -> Path:
    runtime_root = Path(
        os.getenv("VIDEOINSIGHT_RUNTIME_ROOT", str(RUNTIME_ROOT))
    ).expanduser()
    return (runtime_root / "data" / "desktop-owner.json").resolve()


def _owner_digest(subject: str) -> str:
    normalized = subject.strip()
    if not normalized:
        return ""
    payload = f"videoinsight-desktop-owner-v1\0{normalized}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_binding() -> str:
    path = _binding_path()
    try:
        if not path.is_file() or path.stat().st_size > 4096:
            return ""
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict) or payload.get("version") != _BINDING_VERSION:
        return ""
    digest = str(payload.get("owner_sha256") or "").strip().casefold()
    return digest if _DIGEST_PATTERN.fullmatch(digest) else ""


def desktop_owner_matches(subject: str) -> bool:
    """Return true only when an existing binding belongs to ``subject``."""

    expected = _owner_digest(subject)
    return bool(expected) and _read_binding() == expected


def ensure_desktop_owner(subject: str) -> bool:
    """Create the first binding atomically, or verify the existing owner."""

    expected = _owner_digest(subject)
    if not expected:
        return False
    with _BINDING_LOCK:
        current = _read_binding()
        if current:
            return current == expected

        path = _binding_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": _BINDING_VERSION, "owner_sha256": expected},
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return _read_binding() == expected
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return True
