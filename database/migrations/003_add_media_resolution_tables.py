"""Migration 003: media resolution attempts and guards."""

from __future__ import annotations

import sqlite3

VERSION = 3
DESCRIPTION = "媒体解析尝试与幂等守卫表"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS media_resolution_attempts (
            resolution_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            candidate_id TEXT NOT NULL REFERENCES candidates(video_id) ON DELETE CASCADE,
            platform TEXT NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            api_call_count INTEGER NOT NULL DEFAULT 0,
            billable_units REAL,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_media_resolution_candidate
        ON media_resolution_attempts(candidate_id, updated_at DESC);

        CREATE INDEX IF NOT EXISTS idx_media_resolution_usage
        ON media_resolution_attempts(created_at, api_call_count);

        CREATE TABLE IF NOT EXISTS media_resolution_guards (
            idempotency_key TEXT PRIMARY KEY,
            resolution_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            status TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_media_resolution_guards_candidate
        ON media_resolution_guards(candidate_id, status);
        """
    )


def downgrade(conn: sqlite3.Connection) -> None:
    conn.execute("DROP INDEX IF EXISTS idx_media_resolution_guards_candidate")
    conn.execute("DROP INDEX IF EXISTS idx_media_resolution_usage")
    conn.execute("DROP INDEX IF EXISTS idx_media_resolution_candidate")
    conn.execute("DROP TABLE IF EXISTS media_resolution_guards")
    conn.execute("DROP TABLE IF EXISTS media_resolution_attempts")
