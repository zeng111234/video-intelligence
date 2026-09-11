"""启动时恢复被中断的抓取队列（0.2.52）。

覆盖：running 项退回 queued 并自动重启一次；已经恢复过一次又中断则标记 failed
但保留已找到的素材；本进程自己的队列不动；终态队列不动。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from project.backend.app.api.v1 import crawler as crawler_module
from src.models import (
    CrawlerKeywordQueue,
    CrawlerKeywordQueueItem,
    KeywordQueueItemStatus,
    KeywordQueueStatus,
    Platform,
)
from src.repositories import SQLiteRepository


@pytest.fixture()
def repo(tmp_path):
    return SQLiteRepository(tmp_path / "queue-recovery.db")


@pytest.fixture(autouse=True)
def started_workers(monkeypatch):
    """拦截真实 worker：恢复逻辑只负责"该不该重启"，不该在测试里跑采集。"""
    started: list[str] = []
    monkeypatch.setattr(
        crawler_module,
        "_start_crawler_keyword_queue",
        lambda queue_id: started.append(queue_id),
    )
    return started


def _queue(
    *,
    status: KeywordQueueStatus,
    item_statuses: list[KeywordQueueItemStatus],
    backend_instance_id: str | None,
    recovery_attempts: int = 0,
    partial_batch_ids: list[str] | None = None,
    progress_candidates: list[dict] | None = None,
) -> CrawlerKeywordQueue:
    items = [
        CrawlerKeywordQueueItem(
            keyword=f"关键词{index}",
            status=item_status,
            partial_batch_ids=list(partial_batch_ids or []),
            progress_candidates=list(progress_candidates or []),
        )
        for index, item_status in enumerate(item_statuses)
    ]
    return CrawlerKeywordQueue(
        items=items,
        platforms=[Platform.DOUYIN],
        status=status,
        backend_instance_id=backend_instance_id,
        recovery_attempts=recovery_attempts,
    )


def test_interrupted_running_queue_is_requeued_and_restarted(repo, started_workers):
    queue = _queue(
        status=KeywordQueueStatus.RUNNING,
        item_statuses=[KeywordQueueItemStatus.RUNNING, KeywordQueueItemStatus.QUEUED],
        backend_instance_id="backend-dead",
    )
    repo.save_crawler_keyword_queue(queue)

    result = crawler_module.recover_interrupted_crawler_queues(repo)

    assert result["recovered"] == [queue.queue_id]
    assert result["exhausted"] == []
    assert started_workers == [queue.queue_id]

    reloaded = repo.get_crawler_keyword_queue(queue.queue_id)
    assert reloaded is not None
    assert reloaded.status == KeywordQueueStatus.QUEUED
    assert reloaded.recovery_attempts == 1
    assert reloaded.backend_instance_id == crawler_module.BACKEND_INSTANCE_ID
    # running 项退回 queued，并给出人话提示。
    assert reloaded.items[0].status == KeywordQueueItemStatus.QUEUED
    assert reloaded.items[0].progress_message == crawler_module.CRAWLER_RECOVERY_MESSAGE
    assert reloaded.items[0].started_at is None
    # 原本 queued 的项原样保留，顺序不变。
    assert reloaded.items[1].status == KeywordQueueItemStatus.QUEUED
    assert [item.keyword for item in reloaded.items] == ["关键词0", "关键词1"]


def test_second_interruption_is_reported_as_failed_with_material_kept(
    repo, started_workers
):
    """同一任务最多自动恢复一次；第二次失败要保留已找到的素材。"""
    queue = _queue(
        status=KeywordQueueStatus.RUNNING,
        item_statuses=[KeywordQueueItemStatus.RUNNING],
        backend_instance_id="backend-dead-again",
        recovery_attempts=1,
        partial_batch_ids=["batch-1", "batch-2"],
        progress_candidates=[{"title": "已找到的素材"}],
    )
    repo.save_crawler_keyword_queue(queue)

    result = crawler_module.recover_interrupted_crawler_queues(repo)

    assert result["recovered"] == []
    assert result["exhausted"] == [queue.queue_id]
    assert started_workers == []

    reloaded = repo.get_crawler_keyword_queue(queue.queue_id)
    assert reloaded is not None
    assert reloaded.status == KeywordQueueStatus.FAILED
    assert reloaded.items[0].status == KeywordQueueItemStatus.FAILED
    assert reloaded.items[0].error == (
        crawler_module.CRAWLER_RECOVERY_EXHAUSTED_MESSAGE
    )
    # 关键：已经找到的素材必须还在，页面才能显示并提供"重新抓取"。
    assert reloaded.items[0].partial_batch_ids == ["batch-1", "batch-2"]
    assert reloaded.items[0].progress_candidates == [{"title": "已找到的素材"}]


def test_queue_owned_by_this_process_is_left_alone(repo, started_workers):
    queue = _queue(
        status=KeywordQueueStatus.RUNNING,
        item_statuses=[KeywordQueueItemStatus.RUNNING],
        backend_instance_id=crawler_module.BACKEND_INSTANCE_ID,
    )
    repo.save_crawler_keyword_queue(queue)

    result = crawler_module.recover_interrupted_crawler_queues(repo)

    assert result["recovered"] == []
    assert result["exhausted"] == []
    assert started_workers == []
    reloaded = repo.get_crawler_keyword_queue(queue.queue_id)
    assert reloaded is not None
    assert reloaded.status == KeywordQueueStatus.RUNNING


def test_terminal_queues_are_not_touched(repo, started_workers):
    for status in (
        KeywordQueueStatus.SUCCEEDED,
        KeywordQueueStatus.CANCELLED,
        KeywordQueueStatus.PAUSED,
    ):
        queue = _queue(
            status=status,
            item_statuses=[KeywordQueueItemStatus.QUEUED],
            backend_instance_id="backend-dead",
        )
        repo.save_crawler_keyword_queue(queue)

    result = crawler_module.recover_interrupted_crawler_queues(repo)

    assert result["recovered"] == []
    assert result["exhausted"] == []
    assert started_workers == []


def test_recovery_reaps_a_stale_lease_so_the_restarted_queue_is_not_blocked(repo):
    now = datetime(2026, 7, 24, 9, tzinfo=timezone.utc)
    provider = crawler_module._browser_provider_key(Platform.DOUYIN)
    assert repo.claim_provider_safety_lease(
        provider=provider,
        run_id="crashed-run",
        now=now,
        lease_seconds=90,
        backend_instance_id="backend-dead",
    )

    queue = _queue(
        status=KeywordQueueStatus.RUNNING,
        item_statuses=[KeywordQueueItemStatus.RUNNING],
        backend_instance_id="backend-dead",
    )
    repo.save_crawler_keyword_queue(queue)

    result = crawler_module.recover_interrupted_crawler_queues(repo)

    # 租约已过期 → 必须被回收，否则恢复起来的队列会立刻被自己的旧租约挡住。
    assert provider in result["reaped_providers"]
    state = repo.get_provider_safety_state(provider)
    assert state is not None
    assert state.active_run_id is None
