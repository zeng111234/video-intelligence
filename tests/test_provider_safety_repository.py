from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.repositories import MockRepository, SQLiteRepository


@pytest.mark.parametrize("repository_factory", [MockRepository, SQLiteRepository])
def test_provider_safety_lease_blocks_parallel_runs_and_persists_cooldown(
    repository_factory, tmp_path
) -> None:
    repository = (
        repository_factory()
        if repository_factory is MockRepository
        else repository_factory(tmp_path / "provider-safety.db")
    )
    now = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)

    assert repository.claim_provider_safety_lease(
        provider="douyin_hotspot_browser", run_id="first", now=now, lease_seconds=600
    )
    assert not repository.claim_provider_safety_lease(
        provider="douyin_hotspot_browser", run_id="second", now=now, lease_seconds=600
    )

    released = repository.release_provider_safety_lease(
        provider="douyin_hotspot_browser",
        run_id="first",
        now=now + timedelta(seconds=1),
        cooldown_seconds=600,
    )
    assert released.active_run_id is None
    assert released.next_allowed_at == now + timedelta(seconds=601)
    assert not repository.claim_provider_safety_lease(
        provider="douyin_hotspot_browser",
        run_id="second",
        now=now + timedelta(minutes=5),
        lease_seconds=600,
    )
    assert repository.claim_provider_safety_lease(
        provider="douyin_hotspot_browser",
        run_id="second",
        now=now + timedelta(minutes=11),
        lease_seconds=600,
    )


def test_sqlite_provider_safety_pause_survives_reopen(tmp_path) -> None:
    path = tmp_path / "provider-safety.db"
    now = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)
    repository = SQLiteRepository(path)
    repository.release_provider_safety_lease(
        provider="douyin_hotspot_browser",
        run_id="missing",
        now=now,
        cooldown_seconds=600,
        safety_pause_seconds=3600,
        safety_reason="访问频繁",
    )

    reloaded = SQLiteRepository(path).get_provider_safety_state("douyin_hotspot_browser")
    assert reloaded is not None
    assert reloaded.blocked_reason == "访问频繁"
    assert reloaded.blocked_until == now + timedelta(hours=1)


@pytest.mark.parametrize("repository_factory", [MockRepository, SQLiteRepository])
def test_provider_safety_real_run_limit_uses_rolling_window(
    repository_factory, tmp_path
) -> None:
    repository = (
        repository_factory()
        if repository_factory is MockRepository
        else repository_factory(tmp_path / "provider-window.db")
    )
    now = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)

    for index in range(2):
        run_at = now + timedelta(minutes=index * 2)
        assert repository.claim_provider_safety_lease(
            provider="douyin_hotspot_browser",
            run_id=f"run-{index}",
            now=run_at,
            lease_seconds=30,
            max_runs_in_window=2,
            rolling_window_seconds=3600,
        )
        repository.release_provider_safety_lease(
            provider="douyin_hotspot_browser",
            run_id=f"run-{index}",
            now=run_at + timedelta(seconds=1),
            cooldown_seconds=1,
        )

    assert not repository.claim_provider_safety_lease(
        provider="douyin_hotspot_browser",
        run_id="limited",
        now=now + timedelta(minutes=5),
        lease_seconds=30,
        max_runs_in_window=2,
        rolling_window_seconds=3600,
    )
    assert repository.claim_provider_safety_lease(
        provider="douyin_hotspot_browser",
        run_id="new-window",
        now=now + timedelta(hours=1, seconds=1),
        lease_seconds=30,
        max_runs_in_window=2,
        rolling_window_seconds=3600,
    )
