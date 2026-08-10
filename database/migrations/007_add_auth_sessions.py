"""Migration 007: persist revocable server-side login sessions.

Only a SHA-256 digest of each opaque session token is stored.  The plaintext
token exists only on the client and is never written to the database.
"""

from __future__ import annotations

import sqlite3

VERSION = 7
DESCRIPTION = "添加可撤销的持久登录会话（auth_sessions）"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token_hash TEXT PRIMARY KEY,
            role TEXT NOT NULL CHECK (role IN ('admin', 'customer')),
            subject TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_auth_sessions_subject
        ON auth_sessions(role, subject, expires_at);

        CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry
        ON auth_sessions(expires_at);
        """
    )


def downgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS auth_sessions;
        """
    )
