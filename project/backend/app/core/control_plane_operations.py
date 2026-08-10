"""Durable idempotency records for paid control-plane mutations."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from project.backend.app.core.config import DATABASE_PATH

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
DEFAULT_PENDING_TIMEOUT_SECONDS = 15 * 60


@dataclass(frozen=True)
class OperationRecord:
    state: str
    request_hash: str
    response_status: int | None = None
    response_body: bytes | None = None
    response_content_type: str | None = None


def valid_idempotency_key(value: str) -> bool:
    return bool(_KEY_PATTERN.fullmatch(value))


def request_fingerprint(operation_type: str, body: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(operation_type.encode("utf-8"))
    digest.update(b"\0")
    digest.update(body)
    return digest.hexdigest()


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(str(DATABASE_PATH), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _pending_timeout_seconds() -> int:
    try:
        configured = int(
            os.getenv(
                "CONTROL_PLANE_OPERATION_PENDING_TIMEOUT_SECONDS",
                str(DEFAULT_PENDING_TIMEOUT_SECONDS),
            )
        )
    except ValueError:
        return DEFAULT_PENDING_TIMEOUT_SECONDS
    return max(60, min(configured, 24 * 60 * 60))


def recover_pending_operations() -> int:
    """Mark requests interrupted by a server restart as outcome-unknown.

    The state is deliberately not reset to retryable: a paid supplier may have
    accepted the request before the process stopped, so automatic resubmission
    could duplicate both the job and its cost.
    """

    now = datetime.now().astimezone().isoformat()
    with _connection() as connection:
        cursor = connection.execute(
            """
            UPDATE control_plane_operations
            SET state = 'unknown', updated_at = ?
            WHERE state = 'pending'
            """,
            (now,),
        )
        return max(0, cursor.rowcount)


def claim_operation(
    *,
    owner: str,
    operation_type: str,
    idempotency_key: str,
    request_hash: str,
) -> tuple[bool, OperationRecord | None]:
    now_value = datetime.now().astimezone()
    now = now_value.isoformat()
    stale_before = (
        now_value - timedelta(seconds=_pending_timeout_seconds())
    ).isoformat()
    with _connection() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO control_plane_operations (
                owner, operation_type, idempotency_key, request_hash, state,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (owner, operation_type, idempotency_key, request_hash, now, now),
        )
        if cursor.rowcount == 1:
            return True, None
        connection.execute(
            """
            UPDATE control_plane_operations
            SET state = 'unknown', updated_at = ?
            WHERE owner = ? AND operation_type = ? AND idempotency_key = ?
              AND state = 'pending'
              AND julianday(updated_at) <= julianday(?)
            """,
            (now, owner, operation_type, idempotency_key, stale_before),
        )
        row = connection.execute(
            """
            SELECT request_hash, state, response_status, response_body,
                   response_content_type
            FROM control_plane_operations
            WHERE owner = ? AND operation_type = ? AND idempotency_key = ?
            """,
            (owner, operation_type, idempotency_key),
        ).fetchone()
    if row is None:
        return False, None
    return False, OperationRecord(
        state=str(row["state"]),
        request_hash=str(row["request_hash"]),
        response_status=(
            int(row["response_status"])
            if row["response_status"] is not None
            else None
        ),
        response_body=(
            bytes(row["response_body"]) if row["response_body"] is not None else None
        ),
        response_content_type=(
            str(row["response_content_type"])
            if row["response_content_type"] is not None
            else None
        ),
    )


def complete_operation(
    *,
    owner: str,
    operation_type: str,
    idempotency_key: str,
    response_status: int,
    response_body: bytes,
    response_content_type: str | None,
) -> None:
    now = datetime.now().astimezone().isoformat()
    with _connection() as connection:
        connection.execute(
            """
            UPDATE control_plane_operations
            SET state = 'completed', response_status = ?, response_body = ?,
                response_content_type = ?, updated_at = ?
            WHERE owner = ? AND operation_type = ? AND idempotency_key = ?
              AND state = 'pending'
            """,
            (
                response_status,
                response_body,
                response_content_type,
                now,
                owner,
                operation_type,
                idempotency_key,
            ),
        )


def mark_operation_unknown(
    *, owner: str, operation_type: str, idempotency_key: str
) -> None:
    now = datetime.now().astimezone().isoformat()
    with _connection() as connection:
        connection.execute(
            """
            UPDATE control_plane_operations
            SET state = 'unknown', updated_at = ?
            WHERE owner = ? AND operation_type = ? AND idempotency_key = ?
              AND state = 'pending'
            """,
            (now, owner, operation_type, idempotency_key),
        )


def operation_completed(
    *, owner: str, operation_type: str, idempotency_key: str
) -> bool:
    with _connection() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM control_plane_operations
            WHERE owner = ? AND operation_type = ? AND idempotency_key = ?
              AND state = 'completed' AND response_status BETWEEN 200 AND 299
            """,
            (owner, operation_type, idempotency_key),
        ).fetchone()
    return row is not None


def completed_operation_record(
    *, owner: str, operation_type: str, idempotency_key: str
) -> OperationRecord | None:
    with _connection() as connection:
        row = connection.execute(
            """
            SELECT request_hash, state, response_status, response_body,
                   response_content_type
            FROM control_plane_operations
            WHERE owner = ? AND operation_type = ? AND idempotency_key = ?
              AND state = 'completed' AND response_status BETWEEN 200 AND 299
            """,
            (owner, operation_type, idempotency_key),
        ).fetchone()
    if row is None:
        return None
    return OperationRecord(
        state=str(row["state"]),
        request_hash=str(row["request_hash"]),
        response_status=int(row["response_status"]),
        response_body=(
            bytes(row["response_body"]) if row["response_body"] is not None else b""
        ),
        response_content_type=(
            str(row["response_content_type"])
            if row["response_content_type"] is not None
            else None
        ),
    )
