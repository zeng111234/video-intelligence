"""Migration 011: add first-use access periods to customer activation codes."""

from __future__ import annotations

import sqlite3

VERSION = 11
DESCRIPTION = "为客户激活码添加首次激活起算的使用期限"


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(customer_codes)").fetchall()
    }


def upgrade(conn: sqlite3.Connection) -> None:
    columns = _columns(conn)
    if not columns:
        return
    additions = {
        "valid_days": "INTEGER",
        "package_price_credits": "TEXT NOT NULL DEFAULT '0'",
        "activated_at": "TEXT",
        "access_expires_at": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE customer_codes ADD COLUMN {name} {definition}")


def downgrade(conn: sqlite3.Connection) -> None:
    columns = _columns(conn)
    for name in (
        "access_expires_at",
        "activated_at",
        "package_price_credits",
        "valid_days",
    ):
        if name in columns:
            conn.execute(f"ALTER TABLE customer_codes DROP COLUMN {name}")
