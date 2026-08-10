from __future__ import annotations

from pathlib import Path

import pytest

from scripts import local_acceptance_backend


def test_runtime_root_must_be_disposable(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="VideoInsight-local-acceptance"):
        local_acceptance_backend._safe_runtime_root(tmp_path / "real-data")


def test_runtime_root_accepts_only_named_acceptance_directory(tmp_path: Path) -> None:
    expected = tmp_path / "VideoInsight-local-acceptance-test"
    assert local_acceptance_backend._safe_runtime_root(expected) == expected.resolve()


def test_environment_forces_sandbox_and_removes_supplier_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COPYWRITING_API_KEY", "must-not-survive")
    runtime_root = tmp_path / "VideoInsight-local-acceptance-env"

    local_acceptance_backend._configure_environment(runtime_root)

    assert local_acceptance_backend.os.environ["COPYWRITING_MODE"] == "sandbox"
    assert local_acceptance_backend.os.environ["ASR_MODE"] == "sandbox"
    assert (
        local_acceptance_backend.os.environ["VIDEO_EDITOR_PROVIDER_MODE"] == "sandbox"
    )
    assert local_acceptance_backend.os.environ["AVATAR_PROVIDER_MODE"] == "sandbox"
    assert "COPYWRITING_API_KEY" not in local_acceptance_backend.os.environ


def test_disposable_accounts_are_seeded_after_database_migrations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from project.backend.app.core import config
    from src.repositories.sqlite import SQLiteRepository

    database = tmp_path / "fresh-acceptance.db"
    monkeypatch.setattr(config, "DATABASE_PATH", database)

    local_acceptance_backend._seed_disposable_accounts()

    repository = SQLiteRepository(database)
    customer = repository.get_customer_code(local_acceptance_backend.QA_CUSTOMER_CODE)
    assert customer is not None
    assert customer.valid_days is None
