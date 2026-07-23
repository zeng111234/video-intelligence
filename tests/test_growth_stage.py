"""增长阶段标签与 null 增长计算测试。

覆盖：
- 单快照只能「观察样本」；复爬中「增长确认中」；3 次复爬后才可进入热门/爆发候选。
- 分享/收藏缺失为 null 且不进入 0 值增长计算。
- 增长倒退、异常暴增后失速继续降权。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.models import (
    CandidateMatch,
    DataSource,
    EligibilityStatus,
    GrowthStage,
    HeatLevel,
    HeatResult,
    Platform,
    SamplingCheckpoint,
    VideoCandidate,
    VideoMetricSnapshot,
)
from src.repositories import MockRepository
from src.services.keyword_trend import KeywordTrendService, _weighted_delta


def _snapshot(
    item_id: str,
    sampled_at: datetime,
    *,
    likes: int | None = None,
    comments: int | None = None,
    shares: int | None = None,
    favorites: int | None = None,
) -> VideoMetricSnapshot:
    return VideoMetricSnapshot(
        item_id=item_id,
        sampled_at=sampled_at,
        likes=likes,
        comments=comments,
        shares=shares,
        favorites=favorites,
        confidence=1.0,
    )


def _candidate(
    item_id: str,
    now: datetime,
    likes: int = 100,
    metrics_at: datetime | None = None,
) -> VideoCandidate:
    return VideoCandidate(
        video_id=item_id,
        platform_item_id=item_id,
        title=f"趋势视频 {item_id}",
        author_id=f"author-{item_id}",
        author_name="趋势作者",
        platform=Platform.DOUYIN,
        category="关键词/二手车",
        published_at=now - timedelta(hours=4),
        source_url=f"https://www.douyin.com/video/{item_id}",
        source_type=DataSource.OFFICIAL,
        eligibility_status=EligibilityStatus.AUTO_MATCHED,
        metrics=_snapshot(item_id, metrics_at or now - timedelta(hours=2), likes=likes),
        heat=HeatResult(score=0, level=HeatLevel.INSUFFICIENT, confidence=1.0),
    )


def _save_match(
    repository: MockRepository,
    *,
    candidate_id: str,
    request_id: str,
    observed_at: datetime,
    rank: int = 1,
    keyword: str = "二手车",
) -> None:
    repository.save_candidate_match(
        CandidateMatch(
            request_id=request_id,
            video_id=candidate_id,
            keyword=keyword,
            cohort_key=f"douyin:keyword:{keyword}",
            platform=Platform.DOUYIN,
            platform_rank=rank,
            observed_at=observed_at,
        )
    )


def test_single_snapshot_is_observing_sample_only() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    candidate = _candidate("c1", now)
    repository = MockRepository(candidates=[candidate], tasks=[])
    _save_match(
        repository, candidate_id="c1", request_id="run-1", observed_at=now
    )

    result = KeywordTrendService(repository).recompute("二手车", now=now)[0]

    assert result.snapshot_count == 1
    assert result.growth_stage == GrowthStage.OBSERVING_SAMPLE
    assert result.next_recrawl_at is None


def test_one_recrawl_is_confirming_not_hot() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    candidate = _candidate("c1", now)
    repository = MockRepository(candidates=[candidate], tasks=[])
    repository.append_snapshot(
        _snapshot("c1", now, likes=500, comments=10, shares=5, favorites=5)
    )
    for request_id, observed_at in (
        ("run-1", now - timedelta(hours=2)),
        ("run-2", now),
    ):
        _save_match(
            repository, candidate_id="c1", request_id=request_id, observed_at=observed_at
        )

    result = KeywordTrendService(repository).recompute("二手车", now=now)[0]

    assert result.snapshot_count == 2
    assert result.recrawl_count == 1
    assert result.growth_stage == GrowthStage.CONFIRMING
    assert result.growth_stage not in {
        GrowthStage.HOT_CANDIDATE,
        GrowthStage.EXPLODING_CANDIDATE,
    }


def test_three_recrawls_required_before_hot_or_exploding() -> None:
    first_seen = datetime(2026, 7, 17, 0, tzinfo=timezone.utc)
    computed_at = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    candidates = [
        _candidate(str(index), first_seen + timedelta(hours=2), likes=100 + index)
        for index in range(30)
    ]
    repository = MockRepository(candidates=candidates, tasks=[])
    for index, candidate in enumerate(candidates):
        values = (
            [100, 300, 1100, 4300]
            if index == 29
            else [100 + index, 102 + index, 106 + index, 112 + index]
        )
        for request_index, (sampled_at, likes) in enumerate(
            zip(
                [
                    first_seen,
                    first_seen + timedelta(hours=2),
                    first_seen + timedelta(hours=6),
                    first_seen + timedelta(hours=12),
                ],
                values,
                strict=True,
            )
        ):
            if request_index > 0:
                repository.append_snapshot(
                    _snapshot(candidate.video_id, sampled_at, likes=likes)
                )
            _save_match(
                repository,
                candidate_id=candidate.video_id,
                request_id=f"run-{request_index}",
                observed_at=sampled_at,
                rank=1 if index == 29 else min(10, index % 10 + 1),
            )

    results = KeywordTrendService(repository).recompute("二手车", now=computed_at)
    target = next(item for item in results if item.candidate_id == "29")

    assert target.recrawl_count == 3
    assert target.snapshot_count == 4
    assert target.growth_stage in {
        GrowthStage.HOT_CANDIDATE,
        GrowthStage.EXPLODING_CANDIDATE,
    }
    # 未达 3 次复爬的普通候选只能停在观察/确认阶段
    for item in results:
        if item.recrawl_count < 3:
            assert item.growth_stage in {
                GrowthStage.OBSERVING_SAMPLE,
                GrowthStage.CONFIRMING,
            }


def test_pending_checkpoint_exposes_next_recrawl_at() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    candidate = _candidate("c1", now)
    repository = MockRepository(candidates=[candidate], tasks=[])
    _save_match(repository, candidate_id="c1", request_id="run-1", observed_at=now)
    due = now + timedelta(hours=6)
    repository.save_sampling_checkpoint(
        SamplingCheckpoint(
            checkpoint_id="sample-x1",
            keyword="二手车",
            candidate_id="c1",
            request_id="run-1",
            offset_hours=6,
            due_at=due,
        )
    )

    result = KeywordTrendService(repository).recompute("二手车", now=now)[0]

    assert result.next_recrawl_at == due


def test_missing_shares_favorites_stay_null_and_skip_growth() -> None:
    previous = _snapshot(
        "v1",
        datetime(2026, 7, 17, 0, tzinfo=timezone.utc),
        likes=10,
        comments=1,
        shares=None,
        favorites=None,
    )
    current = _snapshot(
        "v1",
        datetime(2026, 7, 17, 6, tzinfo=timezone.utc),
        likes=20,
        comments=2,
        shares=None,
        favorites=None,
    )
    # 分享/收藏两侧缺失：完全不参与，赞 1*10 + 评论 3*1 = 13
    assert _weighted_delta(previous, current) == 13.0

    current_with_share = current.model_copy(update={"shares": 10})
    # 上一快照分享缺失 → 分享分量跳过，不按 0→10 计入 40
    assert _weighted_delta(previous, current_with_share) == 13.0

    both_present_previous = previous.model_copy(update={"shares": 2, "favorites": 3})
    both_present_current = current.model_copy(update={"shares": 10, "favorites": 8})
    # 13 + 4*(10-2) + 4*(8-3) = 13 + 32 + 20 = 65
    assert _weighted_delta(both_present_previous, both_present_current) == 65.0

    # 全部缺失时不产生增长值
    empty_previous = _snapshot(
        "v1", datetime(2026, 7, 17, 0, tzinfo=timezone.utc)
    )
    empty_current = _snapshot(
        "v1", datetime(2026, 7, 17, 6, tzinfo=timezone.utc)
    )
    assert _weighted_delta(empty_previous, empty_current) is None


def test_null_share_collect_exposed_in_trend_result() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    candidate = _candidate("c1", now)
    repository = MockRepository(candidates=[candidate], tasks=[])
    repository.append_snapshot(
        _snapshot("c1", now, likes=300, comments=4, shares=None, favorites=None)
    )
    _save_match(repository, candidate_id="c1", request_id="run-1", observed_at=now)

    result = KeywordTrendService(repository).recompute("二手车", now=now)[0]

    assert result.share_count is None
    assert result.collect_count is None
    # 增长只按赞/评论计算，不因缺失字段按 0 值放大或缩小
    assert result.engagement_growth_per_hour is not None


def test_negative_growth_and_spike_then_stall_stay_penalized() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    start = now - timedelta(hours=12)
    falling = _candidate("falling", now, likes=100, metrics_at=start)
    spiky = _candidate("spiky", now, likes=100, metrics_at=start)
    normal = _candidate("normal", now, likes=100, metrics_at=start)
    repository = MockRepository(candidates=[falling, spiky, normal], tasks=[])
    series = {
        # 增长倒退
        "falling": [100, 400, 250, 260],
        # 异常暴增后失速（中位正增长 5 倍以上暴增 → 跌回 20% 以下）
        "spiky": [100, 110, 120, 2010],
        "normal": [100, 105, 112, 120],
    }
    for candidate_id, values in series.items():
        for index, likes in enumerate(values):
            sampled_at = start + timedelta(hours=4 * index)
            if index > 0:
                repository.append_snapshot(
                    _snapshot(candidate_id, sampled_at, likes=likes)
                )
            _save_match(
                repository,
                candidate_id=candidate_id,
                request_id=f"run-{index}",
                observed_at=sampled_at,
                rank=1 if candidate_id == "normal" else 3,
            )
    # spiky 在 +9h 处出现一次 5 倍以上的异常暴增，随后快速失速
    repository.append_snapshot(
        _snapshot("spiky", start + timedelta(hours=9), likes=2000)
    )

    results = KeywordTrendService(repository).recompute("二手车", now=now)
    by_id = {item.candidate_id: item for item in results}

    falling_result = by_id["falling"]
    assert falling_result.anomaly_status.value == "suspected"
    assert falling_result.anomaly_penalty == 0.75
    assert any("倒退" in reason for reason in falling_result.reasons)
    assert falling_result.display_tier == "ordinary"
    assert falling_result.growth_stage in {
        GrowthStage.OBSERVING_SAMPLE,
        GrowthStage.CONFIRMING,
    }

    spiky_result = by_id["spiky"]
    assert spiky_result.anomaly_status.value == "suspected"
    assert spiky_result.anomaly_penalty == 0.75
    assert any("失速" in reason for reason in spiky_result.reasons)
    assert spiky_result.growth_stage not in {
        GrowthStage.HOT_CANDIDATE,
        GrowthStage.EXPLODING_CANDIDATE,
    }
