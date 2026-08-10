"""Migration 010: persist digital-avatar reservations and final settlements."""

from __future__ import annotations

import sqlite3

VERSION = 10
DESCRIPTION = "添加数字人成片费用预留与最终整秒结算记录"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS avatar_billing_reservations (
            owner TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            provider_job_id TEXT,
            price_per_minute_cny TEXT NOT NULL,
            billing_unit_seconds INTEGER NOT NULL DEFAULT 1,
            reserved_seconds INTEGER NOT NULL,
            reserved_credits TEXT NOT NULL,
            final_seconds INTEGER,
            final_credits TEXT,
            state TEXT NOT NULL CHECK (state IN ('reserved', 'settled', 'released')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner, idempotency_key)
        );

        CREATE INDEX IF NOT EXISTS idx_avatar_billing_provider_job
        ON avatar_billing_reservations(owner, provider_job_id);
        """
    )


def downgrade(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS avatar_billing_reservations")
