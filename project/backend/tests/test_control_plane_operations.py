from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from database.migrations.runner import MigrationRunner
from project.backend.app.core import control_plane_operations as operations


def _operation_row(database_path, key: str) -> tuple[str, str]:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT state, updated_at
            FROM control_plane_operations
            WHERE owner = 'customer:TEST'
              AND operation_type = '/api/v1/provider/test'
              AND idempotency_key = ?
            """,
            (key,),
        ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


def test_restart_recovery_marks_pending_operations_unknown(tmp_path, monkeypatch):
    database_path = tmp_path / "operations.db"
    MigrationRunner(database_path).upgrade()
    monkeypatch.setattr(operations, "DATABASE_PATH", database_path)
    claimed, _ = operations.claim_operation(
        owner="customer:TEST",
        operation_type="/api/v1/provider/test",
        idempotency_key="restart-operation",
        request_hash="a" * 64,
    )
    assert claimed is True

    assert operations.recover_pending_operations() == 1
    assert _operation_row(database_path, "restart-operation")[0] == "unknown"

    claimed_again, record = operations.claim_operation(
        owner="customer:TEST",
        operation_type="/api/v1/provider/test",
        idempotency_key="restart-operation",
        request_hash="a" * 64,
    )
    assert claimed_again is False
    assert record is not None and record.state == "unknown"


def test_stale_pending_operation_becomes_unknown_without_resubmission(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "stale-operation.db"
    MigrationRunner(database_path).upgrade()
    monkeypatch.setattr(operations, "DATABASE_PATH", database_path)
    monkeypatch.setenv("CONTROL_PLANE_OPERATION_PENDING_TIMEOUT_SECONDS", "60")
    operations.claim_operation(
        owner="customer:TEST",
        operation_type="/api/v1/provider/test",
        idempotency_key="stale-operation",
        request_hash="b" * 64,
    )
    old = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            UPDATE control_plane_operations SET updated_at = ?
            WHERE idempotency_key = 'stale-operation'
            """,
            (old,),
        )

    claimed, record = operations.claim_operation(
        owner="customer:TEST",
        operation_type="/api/v1/provider/test",
        idempotency_key="stale-operation",
        request_hash="b" * 64,
    )
    assert claimed is False
    assert record is not None and record.state == "unknown"


def test_recent_pending_operation_remains_pending(tmp_path, monkeypatch):
    database_path = tmp_path / "recent-operation.db"
    MigrationRunner(database_path).upgrade()
    monkeypatch.setattr(operations, "DATABASE_PATH", database_path)
    monkeypatch.setenv("CONTROL_PLANE_OPERATION_PENDING_TIMEOUT_SECONDS", "900")
    operations.claim_operation(
        owner="customer:TEST",
        operation_type="/api/v1/provider/test",
        idempotency_key="recent-operation",
        request_hash="c" * 64,
    )

    claimed, record = operations.claim_operation(
        owner="customer:TEST",
        operation_type="/api/v1/provider/test",
        idempotency_key="recent-operation",
        request_hash="c" * 64,
    )
    assert claimed is False
    assert record is not None and record.state == "pending"
