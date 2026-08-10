"""Migration 009: bind supplier jobs to the authenticated customer."""

from __future__ import annotations

import sqlite3

VERSION = 9
DESCRIPTION = "添加公司控制层供应商任务归属（control_plane_provider_jobs）"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS control_plane_provider_jobs (
            provider_job_id TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            provider_kind TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_control_plane_provider_jobs_owner
        ON control_plane_provider_jobs(owner, provider_kind, created_at);
        """
    )


def downgrade(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS control_plane_provider_jobs")
