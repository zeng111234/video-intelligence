"""并发原子性测试：60 秒防重复锁 + 月上限检查。

验证 try_claim_platform_search_request 在并发下只有一个请求能占位成功，
且月上限（次数/金额）不会被并发突破。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

from src.repositories.sqlite import SQLiteRepository


def _repo(tmp_path):
    return SQLiteRepository(tmp_path / "test_guard.db")


def test_concurrent_duplicate_guard_only_one_wins(tmp_path):
    """同一关键词 20 个并发请求，只有一个能通过防重复锁。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    results: list[str] = []
    lock = threading.Lock()

    def worker(i: int):
        result = repo.try_claim_platform_search_request(
            fingerprint="kw-同款关键词",
            run_id=f"run-{i}",
            claimed_at=now,
            ttl_seconds=60,
            enforce_limits=False,
        )
        with lock:
            results.append(result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 1
    assert results.count("duplicate") == 19


def test_concurrent_duplicate_guard_outside_ttl_allows_retry(tmp_path):
    """防重复锁超过 60 秒窗口后允许再次占位。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    assert (
        repo.try_claim_platform_search_request(
            fingerprint="f1",
            run_id="run-1",
            claimed_at=now,
            enforce_limits=False,
        )
        == "ok"
    )
    # 59 秒内仍被挡
    assert (
        repo.try_claim_platform_search_request(
            fingerprint="f1",
            run_id="run-2",
            claimed_at=now + timedelta(seconds=30),
            enforce_limits=False,
        )
        == "duplicate"
    )
    # 超过 60 秒后可重试
    later = now + timedelta(seconds=61)
    assert (
        repo.try_claim_platform_search_request(
            fingerprint="f1",
            run_id="run-3",
            claimed_at=later,
            enforce_limits=False,
        )
        == "ok"
    )


def test_concurrent_monthly_count_limit_not_exceeded(tmp_path):
    """并发 30 个请求、上限 10 次：只允许 10 个通过，不超上限。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    results: list[str] = []
    lock = threading.Lock()

    def worker(i: int):
        result = repo.try_claim_platform_search_request(
            fingerprint=f"kw-{i}",
            run_id=f"run-{i}",
            claimed_at=now,
            enforce_limits=True,
            monthly_queries_limit=10,
            monthly_cost_limit_cny=None,
        )
        with lock:
            results.append(result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 10
    assert results.count("count_limit") == 20


def test_concurrent_monthly_cost_limit_not_exceeded(tmp_path):
    """并发 30 个请求、预算 10 元、单价 1 元：通过的请求成本合计不超过 10 元。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    results: list[str] = []
    lock = threading.Lock()

    def worker(i: int):
        result = repo.try_claim_platform_search_request(
            fingerprint=f"kw-{i}",
            run_id=f"run-{i}",
            claimed_at=now,
            unit_price=1.0,
            enforce_limits=True,
            monthly_queries_limit=None,
            monthly_cost_limit_cny=10.0,
        )
        with lock:
            results.append(result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 10
    assert results.count("cost_limit") == 20
    # 已占位的成本合计不得超过预算
    pending_cost = sum(
        float(r["cost"])
        for r in repo.connection.execute(
            """
            SELECT cost FROM provider_request_guards
            WHERE status = 'claimed'
            """
        ).fetchall()
    )
    assert pending_cost <= 10.0


def test_unresolved_status_blocks_claim(tmp_path):
    """outcome_unknown 状态阻止新占位，返回 unresolved。"""
    repo = _repo(tmp_path)
    now = datetime.now().astimezone()
    assert (
        repo.try_claim_platform_search_request(
            fingerprint="f1", run_id="run-1", claimed_at=now, enforce_limits=False
        )
        == "ok"
    )
    repo.mark_platform_search_request("f1", "outcome_unknown", now)
    assert (
        repo.try_claim_platform_search_request(
            fingerprint="f1", run_id="run-2", claimed_at=now, enforce_limits=False
        )
        == "unresolved"
    )
    # 清理后可重新占位
    repo.resolve_platform_search_request("f1")
    assert (
        repo.try_claim_platform_search_request(
            fingerprint="f1", run_id="run-3", claimed_at=now, enforce_limits=False
        )
        == "ok"
    )


def test_guard_table_migration_adds_cost_column(tmp_path):
    """旧库（无 cost 列）打开后自动迁移，原子占位正常。"""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE provider_request_guards (
            fingerprint TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            status TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    repo = SQLiteRepository(db_path)
    now = datetime.now().astimezone()
    assert (
        repo.try_claim_platform_search_request(
            fingerprint="f1", run_id="run-1", claimed_at=now, enforce_limits=False
        )
        == "ok"
    )
