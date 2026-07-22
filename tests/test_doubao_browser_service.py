from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.models import (
    DataSource,
    HeatLevel,
    HeatResult,
    Platform,
    TaskStatus,
    VideoCandidate,
    VideoMetricSnapshot,
)
from src.repositories import MockRepository
from src.services.doubao_browser import (
    DoubaoBrowserAutomationService,
    DoubaoMobileAutomationService,
)


NOW = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)


def _candidate(
    video_id: str = "douyin-1",
    *,
    platform: Platform = Platform.DOUYIN,
    source_url: str | None = "https://www.douyin.com/video/7663098861069536550",
) -> VideoCandidate:
    return VideoCandidate(
        video_id=video_id,
        platform_item_id=video_id,
        title="7月开始风向彻底变了普通人的机会来了",
        author_id="author-1",
        author_name="琦姐",
        platform=platform,
        category="商业趋势",
        published_at=NOW - timedelta(days=1),
        source_url=source_url,
        source_type=DataSource.LICENSED_PROVIDER,
        metrics=VideoMetricSnapshot(
            item_id=video_id,
            sampled_at=NOW,
            likes=2894,
            comments=71,
            shares=1241,
            favorites=1515,
            confidence=0.8,
        ),
        heat=HeatResult(score=70, level=HeatLevel.NORMAL, confidence=0.8),
    )


def test_doubao_browser_job_lifecycle_creates_zero_cost_review_task() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    candidate = _candidate()
    repository.save_candidate(candidate)
    service = DoubaoBrowserAutomationService(repository)

    queued = service.create_job(candidate)
    claimed = service.claim_next_job("worker-1")
    assert claimed is not None
    assert claimed.task_id == queued.task_id
    assert claimed.status == TaskStatus.RUNNING
    assert claimed.outputs["fee_cny"] == "0"

    completed = service.complete_job(
        claimed.task_id,
        transcript_text="第一段原文。\n\n第二段原文。",
        short_url="https://v.douyin.com/5PkWSQr4BCY/",
        doubao_conversation_url="https://www.doubao.com/chat/1",
        doubao_message_id="message-1",
    )

    assert completed.status == TaskStatus.SUCCEEDED
    assert completed.source_kind == "doubao_browser"
    assert completed.timing_available is False
    assert completed.source_url == "https://v.douyin.com/5PkWSQr4BCY/"
    assert [segment.text for segment in completed.segments] == [
        "第一段原文。",
        "第二段原文。",
    ]
    assert all(segment.needs_review for segment in completed.segments)
    assert repository.monthly_platform_query_cost(NOW.replace(day=1)) == 0
    revisions = repository.list_transcript_revisions(completed.task_id)
    assert len(revisions) == 1
    assert revisions[0].model_name == "doubao_browser"


def test_doubao_browser_job_reuses_existing_active_candidate_job() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    candidate = _candidate()
    repository.save_candidate(candidate)
    service = DoubaoBrowserAutomationService(repository)

    first = service.create_job(candidate)
    second = service.create_job(candidate)

    assert second.task_id == first.task_id


def test_doubao_browser_job_blocks_non_douyin_candidate() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    candidate = _candidate(platform=Platform.XIAOHONGSHU)
    repository.save_candidate(candidate)
    service = DoubaoBrowserAutomationService(repository)

    with pytest.raises(RuntimeError, match="只支持抖音"):
        service.create_job(candidate)


def test_doubao_mobile_jobs_are_separate_from_browser_jobs() -> None:
    repository = MockRepository(candidates=[], tasks=[])
    candidate = _candidate()
    repository.save_candidate(candidate)
    browser_service = DoubaoBrowserAutomationService(repository)
    mobile_service = DoubaoMobileAutomationService(repository)

    browser_job = browser_service.create_job(candidate)
    mobile_job = mobile_service.create_job(candidate)

    assert browser_job.task_id != mobile_job.task_id
    assert browser_job.source_kind == "doubao_browser"
    assert mobile_job.source_kind == "doubao_mobile"
    assert mobile_job.model_name == "doubao_mobile"
    assert mobile_service.claim_next_job("android-worker") is not None
    assert browser_service.list_jobs(candidate_id=candidate.video_id) == [browser_job]
