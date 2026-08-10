"""Server-side media validation used before authoritative billing."""

from __future__ import annotations

import subprocess
from pathlib import Path


def probe_media_duration(path: str | Path) -> float:
    source = Path(path)
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        duration = float(completed.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ValueError("无法读取素材时长，请确认文件完整。") from exc
    if not 0 < duration <= 6 * 60 * 60:
        raise ValueError("素材时长无效或超过 6 小时限制。")
    return duration


def get_media_duration_probe():
    return probe_media_duration
