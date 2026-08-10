"""Migration 008: persist control-plane idempotency outcomes."""

from __future__ import annotations

import sqlite3

VERSION = 8
DESCRIPTION = "添加公司控制层幂等操作记录（control_plane_operations）"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS control_plane_operations (
            owner TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('pending', 'completed', 'unknown')),
            response_status INTEGER,
            response_body BLOB,
            response_content_type TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner, operation_type, idempotency_key)
        );

        CREATE INDEX IF NOT EXISTS idx_control_plane_operations_updated
        ON control_plane_operations(updated_at);
        """
    )


def downgrade(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS control_plane_operations")
