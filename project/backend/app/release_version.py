"""Read the immutable release version embedded in a server bundle."""

from __future__ import annotations

import re
from pathlib import Path


_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){2}(?:-[0-9A-Za-z.-]+)?$")
_RELEASE_VERSION_PATH = Path(__file__).resolve().parents[3] / "release_version.txt"


def get_release_version() -> str:
    """Return the bundled release version, or ``development`` outside a bundle."""

    try:
        value = _RELEASE_VERSION_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return "development"
    return value if _VERSION_PATTERN.fullmatch(value) else "development"
