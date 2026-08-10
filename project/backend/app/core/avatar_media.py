"""Lightweight server validation for avatar training media and voice samples."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _ffprobe_json(path: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,width,height,duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        payload = json.loads(completed.stdout)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        raise ValueError("训练素材无法读取，请确认文件完整。") from exc
    if not isinstance(payload, dict):
        raise ValueError("训练素材元数据无效。")
    return payload


def _duration_seconds(payload: dict[str, Any]) -> float:
    values: list[Any] = [payload.get("format", {}).get("duration")]
    values.extend(
        stream.get("duration")
        for stream in payload.get("streams", [])
        if isinstance(stream, dict)
    )
    for value in values:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration
    raise ValueError("训练素材缺少有效时长。")


def validate_avatar_training_video(path: Path) -> None:
    payload = _ffprobe_json(path)
    duration = _duration_seconds(payload)
    videos = [
        item
        for item in payload.get("streams", [])
        if isinstance(item, dict) and item.get("codec_type") == "video"
    ]
    if not videos:
        raise ValueError("训练视频未检测到视频轨。")
    try:
        width, height = int(videos[0].get("width")), int(videos[0].get("height"))
    except (TypeError, ValueError) as exc:
        raise ValueError("训练视频缺少有效分辨率。") from exc
    if not 30 <= duration <= 30 * 60:
        raise ValueError("训练视频时长必须在 30 秒至 30 分钟之间。")
    if min(width, height) < 360 or max(width, height) > 4096:
        raise ValueError("训练视频分辨率必须在 360p 至 4K 之间。")


def validate_voice_training_sample(path: Path) -> None:
    payload = _ffprobe_json(path)
    if not any(
        isinstance(item, dict) and item.get("codec_type") == "audio"
        for item in payload.get("streams", [])
    ):
        raise ValueError("声音样本未检测到音频轨。")
    if _duration_seconds(payload) > 30:
        raise ValueError("声音样本必须在 30 秒以内。")


def custom_voice_sample_path(manifest_path: Path, asset_id: str) -> Path | None:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    items = payload.get("assets", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return None
    record = next(
        (
            item
            for item in items
            if isinstance(item, dict) and str(item.get("asset_id") or "") == asset_id
        ),
        None,
    )
    if record is None:
        return None
    raw_path = str(record.get("sample_path") or "").strip()
    if not raw_path:
        return None
    candidate = Path(raw_path)
    resolved = (
        candidate if candidate.is_absolute() else manifest_path.parent / candidate
    ).resolve()
    allowed_root = (manifest_path.parent / "voice_samples").resolve()
    if allowed_root not in resolved.parents or not resolved.is_file():
        return None
    return resolved
