from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.models import Platform, PlatformRunStatus, PlatformSearchRun, ProviderMode, SearchBatch
from src.repositories import MockRepository, SQLiteRepository


def test_sqlite_crawler_history_indexes_are_created(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "crawler-history.db")

    batch_indexes = {
        row[1] for row in repository.connection.execute("PRAGMA index_list('search_batches')")
    }
    guard_indexes = {
        row[1] for row in repository.connection.execute("PRAGMA index_list('provider_request_guards')")
    }

    assert "idx_search_batches_created" in batch_indexes
    assert "idx_provider_request_guards_run" in guard_indexes


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


@pytest.mark.parametrize("repository_factory", [MockRepository, SQLiteRepository])
def test_hotspot_cache_isolated_by_statistical_window(repository_factory, tmp_path) -> None:
    repository = (
        repository_factory()
        if repository_factory is MockRepository
        else repository_factory(tmp_path / "hotspot-window.db")
    )
    now = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)
    runs_by_window = {}
    for window_hours in (1, 168):
        batch = SearchBatch(
            keyword="二手车",
            published_window_days=0,
            hotspot_window_hours=window_hours,
            requested_count_per_platform=100,
            provider="douyin_local_browser",
            mode=ProviderMode.LOCAL_BROWSER,
            platforms=[Platform.DOUYIN],
        )
        repository.save_search_batch(batch)
        run = PlatformSearchRun(
            batch_id=batch.batch_id,
            platform=Platform.DOUYIN,
            provider="douyin_local_browser",
            mode=ProviderMode.LOCAL_BROWSER,
            status=PlatformRunStatus.SUCCEEDED,
            requested_count=100,
            idempotency_key=f"window-{window_hours}",
            request_fingerprint=f"window-{window_hours}",
            started_at=now,
            finished_at=now,
        )
        repository.save_platform_search_run(run)
        runs_by_window[window_hours] = run

    assert repository.find_cached_platform_search_run(
        provider="douyin_local_browser",
        platform=Platform.DOUYIN,
        keyword="二手车",
        published_window_days=0,
        hotspot_window_hours=1,
        requested_count=100,
        since=now - timedelta(minutes=30),
    ).run_id == runs_by_window[1].run_id
    assert repository.find_cached_platform_search_run(
        provider="douyin_local_browser",
        platform=Platform.DOUYIN,
        keyword="二手车",
        published_window_days=0,
        hotspot_window_hours=24,
        requested_count=100,
        since=now - timedelta(minutes=30),
    ) is None


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


@pytest.mark.parametrize("repository_factory", [MockRepository, SQLiteRepository])
def test_provider_safety_zero_limit_is_disabled_even_with_historical_count(
    repository_factory, tmp_path
) -> None:
    repository = (
        repository_factory()
        if repository_factory is MockRepository
        else repository_factory(tmp_path / "provider-disabled-limit.db")
    )
    now = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)
    for index in range(8):
        run_at = now + timedelta(minutes=index * 2)
        assert repository.claim_provider_safety_lease(
            provider="bilibili_browser_search",
            run_id=f"historical-{index}",
            now=run_at,
            lease_seconds=30,
            max_runs_in_window=0,
            rolling_window_seconds=24 * 60 * 60,
        )
        repository.release_provider_safety_lease(
            provider="bilibili_browser_search",
            run_id=f"historical-{index}",
            now=run_at + timedelta(seconds=1),
            cooldown_seconds=1,
        )

    assert repository.claim_provider_safety_lease(
        provider="bilibili_browser_search",
        run_id="after-default-change",
        now=now + timedelta(minutes=20),
        lease_seconds=30,
        max_runs_in_window=0,
        rolling_window_seconds=24 * 60 * 60,
    )


# ---------------------------------------------------------------------------
# 0.2.52：租约过期回收与心跳
# ---------------------------------------------------------------------------


def test_browser_lease_expires_within_90_seconds_after_a_crashed_backend(tmp_path) -> None:
    """后端崩溃时 finally 不会执行，用户不应被卡满 15 分钟。"""
    repository = SQLiteRepository(tmp_path / "lease-expiry.db")
    now = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)
    provider = "douyin_browser_search"

    assert repository.claim_provider_safety_lease(
        provider=provider,
        run_id="crashed",
        now=now,
        lease_seconds=90,
        backend_instance_id="backend-dead",
    )
    # 租约仍在有效期内：互斥照常生效。
    assert not repository.claim_provider_safety_lease(
        provider=provider,
        run_id="next",
        now=now + timedelta(seconds=89),
        lease_seconds=90,
    )
    # 超过 90 秒后必须自动失效。
    assert repository.claim_provider_safety_lease(
        provider=provider,
        run_id="next",
        now=now + timedelta(seconds=91),
        lease_seconds=90,
    )


def test_heartbeat_renewal_keeps_a_long_collection_alive(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "lease-heartbeat.db")
    now = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)
    provider = "douyin_browser_search"

    assert repository.claim_provider_safety_lease(
        provider=provider,
        run_id="long-run",
        now=now,
        lease_seconds=90,
        backend_instance_id="backend-a",
    )
    # 采集进行到第 30 秒时续租。
    assert repository.renew_provider_safety_lease(
        provider=provider,
        run_id="long-run",
        now=now + timedelta(seconds=30),
        lease_seconds=90,
    )
    # 原本 90 秒到期；续租后到 120 秒仍然有效。
    assert not repository.claim_provider_safety_lease(
        provider=provider,
        run_id="intruder",
        now=now + timedelta(seconds=100),
        lease_seconds=90,
    )
    assert repository.claim_provider_safety_lease(
        provider=provider,
        run_id="intruder",
        now=now + timedelta(seconds=121),
        lease_seconds=90,
    )


def test_heartbeat_only_renews_for_the_run_that_owns_the_lease(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "lease-owner.db")
    now = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)
    provider = "douyin_browser_search"
    repository.claim_provider_safety_lease(
        provider=provider,
        run_id="owner",
        now=now,
        lease_seconds=90,
        backend_instance_id="backend-a",
    )

    assert not repository.renew_provider_safety_lease(
        provider=provider,
        run_id="someone-else",
        now=now + timedelta(seconds=30),
        lease_seconds=90,
    )
    state = repository.get_provider_safety_state(provider)
    assert state is not None
    # 被拒绝的续租不得延长过期时间。
    assert state.lease_expires_at == now + timedelta(seconds=90)


def test_reaping_recovers_an_interrupted_run_but_keeps_the_safety_pause(tmp_path) -> None:
    """只清进程中断留下的运行租约，绝不删除 24 小时风控暂停。"""
    repository = SQLiteRepository(tmp_path / "lease-reap.db")
    now = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)
    provider = "douyin_browser_search"

    repository.claim_provider_safety_lease(
        provider=provider,
        run_id="crashed",
        now=now,
        lease_seconds=90,
        backend_instance_id="backend-dead",
    )
    # 风控暂停由另一个 run 写入：active_run_id 不变，blocked_until 被设置。
    repository.release_provider_safety_lease(
        provider=provider,
        run_id="someone-else",
        now=now + timedelta(seconds=10),
        cooldown_seconds=0,
        safety_pause_seconds=24 * 60 * 60,
        safety_reason="出现安全验证",
    )
    before = repository.get_provider_safety_state(provider)
    assert before is not None
    assert before.blocked_until == now + timedelta(seconds=10) + timedelta(hours=24)
    assert before.active_run_id == "crashed"

    reaped = repository.reap_stale_provider_leases(
        now=now + timedelta(seconds=120),
        live_backend_instance_ids={"backend-alive"},
    )
    assert reaped == [provider]

    after = repository.get_provider_safety_state(provider)
    assert after is not None
    assert after.active_run_id is None
    assert after.lease_expires_at is None
    assert after.heartbeat_at is None
    assert after.browser_pid is None
    assert after.backend_instance_id is None
    # 关键：风控暂停必须原样保留。
    assert after.blocked_until == before.blocked_until
    assert after.blocked_reason == "出现安全验证"


def test_reaping_leaves_a_live_lease_alone(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "lease-reap-live.db")
    now = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)
    provider = "douyin_browser_search"
    repository.claim_provider_safety_lease(
        provider=provider,
        run_id="running",
        now=now,
        lease_seconds=90,
        backend_instance_id="backend-alive",
    )

    # 心跳仍在：不得回收。
    assert repository.reap_stale_provider_leases(
        now=now + timedelta(seconds=30),
        live_backend_instance_ids={"backend-alive"},
    ) == []
    # 心跳已停但租约未过期，且该实例仍在存活集合里：同样不得回收。
    assert repository.reap_stale_provider_leases(
        now=now + timedelta(seconds=80),
        live_backend_instance_ids={"backend-alive"},
    ) == []
    state = repository.get_provider_safety_state(provider)
    assert state is not None
    assert state.active_run_id == "running"


def test_reaping_keeps_an_unexpired_lease_whose_owner_is_unknown(tmp_path) -> None:
    """旧版本写入的租约没有 backend_instance_id，无从判断生死时必须保守保留。"""
    repository = SQLiteRepository(tmp_path / "lease-reap-legacy.db")
    now = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)
    provider = "douyin_browser_search"
    repository.claim_provider_safety_lease(
        provider=provider,
        run_id="legacy",
        now=now,
        lease_seconds=90,
    )

    assert repository.reap_stale_provider_leases(
        now=now + timedelta(seconds=30),
        live_backend_instance_ids={"backend-alive"},
    ) == []
