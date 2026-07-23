"""Migration 004: preserve and remove orphaned sampling checkpoints."""

from __future__ import annotations

import sqlite3

VERSION = 4
DESCRIPTION = "隔离缺失候选的复采检查点并恢复外键完整性"


def upgrade(conn: sqlite3.Connection) -> None:
    """Move legacy orphan checkpoints into a recoverable quarantine table."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS orphaned_sampling_checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            keyword TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            due_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            quarantined_at TEXT NOT NULL,
            reason TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_orphaned_sampling_candidate
        ON orphaned_sampling_checkpoints(candidate_id, quarantined_at DESC);

        INSERT OR IGNORE INTO orphaned_sampling_checkpoints(
            checkpoint_id, keyword, candidate_id, due_at, payload_json,
            quarantined_at, reason
        )
        SELECT checkpoint_id, keyword, candidate_id, due_at, payload_json,
               strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'missing_candidate'
        FROM sampling_checkpoints AS checkpoint
        WHERE NOT EXISTS (
            SELECT 1
            FROM candidates
            WHERE candidates.video_id = checkpoint.candidate_id
        );

        DELETE FROM sampling_checkpoints
        WHERE NOT EXISTS (
            SELECT 1
            FROM candidates
            WHERE candidates.video_id = sampling_checkpoints.candidate_id
        );
        """
    )


def downgrade(conn: sqlite3.Connection) -> None:
    """Restore quarantined rows only when their candidate exists again.

    The quarantine table is intentionally retained so a downgrade never discards
    historical checkpoint payloads whose parent candidate is still absent.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO sampling_checkpoints(
            checkpoint_id, keyword, candidate_id, due_at, payload_json
        )
        SELECT checkpoint_id, keyword, candidate_id, due_at, payload_json
        FROM orphaned_sampling_checkpoints AS checkpoint
        WHERE EXISTS (
            SELECT 1
            FROM candidates
            WHERE candidates.video_id = checkpoint.candidate_id
        )
        """
    )
