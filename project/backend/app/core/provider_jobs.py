"""Ownership checks for opaque third-party job identifiers."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from project.backend.app.core.config import DATABASE_PATH


def claim_provider_job(*, provider_job_id: str, owner: str, kind: str) -> bool:
    """Atomically reserve a new opaque identifier.

    Unlike ``register_provider_job``, an existing identifier owned by the same
    customer is not treated as a fresh claim.  This is used for one-time
    billing markers where replaying the side effect (for example a refund)
    would be unsafe.
    """

    now = datetime.now().astimezone().isoformat()
    with sqlite3.connect(str(DATABASE_PATH), timeout=10) as connection:
        cursor = connection.execute(
            """
            INSERT INTO control_plane_provider_jobs(
                provider_job_id, owner, provider_kind, created_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(provider_job_id) DO NOTHING
            """,
            (provider_job_id, owner, kind, now),
        )
        return cursor.rowcount == 1


def register_provider_job(*, provider_job_id: str, owner: str, kind: str) -> bool:
    now = datetime.now().astimezone().isoformat()
    with sqlite3.connect(str(DATABASE_PATH), timeout=10) as connection:
        connection.execute(
            """
            INSERT INTO control_plane_provider_jobs(
                provider_job_id, owner, provider_kind, created_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(provider_job_id) DO UPDATE SET
                owner = excluded.owner,
                provider_kind = excluded.provider_kind
            WHERE control_plane_provider_jobs.owner = excluded.owner
              AND control_plane_provider_jobs.provider_kind = excluded.provider_kind
            """,
            (provider_job_id, owner, kind, now),
        )
    return provider_job_owned(
        provider_job_id=provider_job_id,
        owner=owner,
        kind=kind,
    )


def provider_job_owned(*, provider_job_id: str, owner: str, kind: str) -> bool:
    with sqlite3.connect(str(DATABASE_PATH), timeout=10) as connection:
        row = connection.execute(
            """
            SELECT 1 FROM control_plane_provider_jobs
            WHERE provider_job_id = ? AND owner = ? AND provider_kind = ?
            """,
            (provider_job_id, owner, kind),
        ).fetchone()
    return row is not None
