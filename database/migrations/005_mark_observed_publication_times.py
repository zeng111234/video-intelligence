"""Migration 005: identify legacy Hotspot rows whose publication time is a sample fallback."""

from __future__ import annotations

import sqlite3

VERSION = 5
DESCRIPTION = "标注热点宝候选的采样时间回退，避免误作发布时间"

_WARNING = "未取得有效发布时间，页面展示为采样时间。"


def upgrade(conn: sqlite3.Connection) -> None:
    """Annotate only rows proven to have copied their local observation time."""
    _ensure_warnings_column(conn)
    rows = conn.execute(
        """
        SELECT DISTINCT candidate.video_id, candidate.data_quality_warnings_json
        FROM candidates AS candidate
        JOIN candidate_matches AS match ON match.video_id = candidate.video_id
        WHERE candidate.source_type = 'licensed_commercial_provider'
          AND match.provider_name = 'douyin_local_browser'
          AND candidate.published_at = match.observed_at
        """
    ).fetchall()
    for video_id, warnings_json in rows:
        warnings = _load_warnings(warnings_json)
        if _WARNING not in warnings:
            warnings.append(_WARNING)
            conn.execute(
                "UPDATE candidates SET data_quality_warnings_json = ? WHERE video_id = ?",
                (_dump_warnings(warnings), video_id),
            )


def downgrade(conn: sqlite3.Connection) -> None:
    """Remove only the warning introduced by this migration."""
    _ensure_warnings_column(conn)
    rows = conn.execute(
        "SELECT video_id, data_quality_warnings_json FROM candidates"
    ).fetchall()
    for video_id, warnings_json in rows:
        warnings = _load_warnings(warnings_json)
        if _WARNING in warnings:
            conn.execute(
                "UPDATE candidates SET data_quality_warnings_json = ? WHERE video_id = ?",
                (_dump_warnings([warning for warning in warnings if warning != _WARNING]), video_id),
            )


def _load_warnings(value: str | None) -> list[str]:
    import json

    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _dump_warnings(warnings: list[str]) -> str:
    import json

    return json.dumps(warnings, ensure_ascii=False)


def _ensure_warnings_column(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(candidates)")}
    if "data_quality_warnings_json" not in columns:
        conn.execute(
            "ALTER TABLE candidates ADD COLUMN data_quality_warnings_json TEXT NOT NULL DEFAULT '[]'"
        )
