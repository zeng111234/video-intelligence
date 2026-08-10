from __future__ import annotations

from database.migrations.runner import MigrationRunner
from project.backend.app.core import security


def test_sqlite_auth_session_survives_memory_reset_and_can_be_revoked(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "control-plane.db"
    MigrationRunner(database_path).upgrade()
    monkeypatch.setenv("AUTH_SESSION_STORE", "sqlite")
    monkeypatch.setenv("AUTH_SESSION_DATABASE_PATH", str(database_path))

    token = security.issue_auth_token("customer", "CUSTOMER-01")
    security._auth_tokens.clear()

    assert security.verify_auth_token(token) == {
        "role": "customer",
        "subject": "CUSTOMER-01",
    }

    security.revoke_auth_token(token)
    assert security.verify_auth_token(token) is None


def test_sqlite_auth_session_can_revoke_every_session_for_subject(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "control-plane.db"
    MigrationRunner(database_path).upgrade()
    monkeypatch.setenv("AUTH_SESSION_STORE", "sqlite")
    monkeypatch.setenv("AUTH_SESSION_DATABASE_PATH", str(database_path))

    first = security.issue_auth_token("admin", "operator")
    second = security.issue_auth_token("admin", "operator")
    other = security.issue_auth_token("admin", "other")

    security.revoke_auth_tokens("admin", "operator")

    assert security.verify_auth_token(first) is None
    assert security.verify_auth_token(second) is None
    assert security.verify_auth_token(other) == {
        "role": "admin",
        "subject": "other",
    }
