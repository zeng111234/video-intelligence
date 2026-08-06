from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from src.adapters.official import DouyinKeywordAdapter, OfficialApiError
from src.models import (
    DataSource,
    DiscoveryResult,
    EligibilityStatus,
    HeatLevel,
    HeatResult,
    KeywordTrendLevel,
    NormalizedCandidate,
    Platform,
    SourceCapability,
    SourcePage,
    SourceRequest,
    VideoCandidate,
    VideoMetricSnapshot,
)
from src.repositories import MockRepository, SQLiteRepository
from src.services import (
    HeatService,
    KeywordDiscoveryService,
    KeywordTrendService,
    SourceService,
)


def _official_video(item_id: str, *, likes: int = 12) -> dict[str, object]:
    return {
        "item_id": item_id,
        "title": f"二手车讲解 {item_id}",
        "create_time": 1784246400,
        "nickname": "测试车商",
        "link": f"https://www.douyin.com/video/{item_id}",
        "statistics": {"digg_count": likes},
    }


def test_official_keyword_adapter_maps_documented_metadata_only() -> None:
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if method == "POST":
            return {
                "data": {
                    "access_token": "test-token",
                    "expires_in": 7200,
                    "error_code": 0,
                }
            }
        return {
            "err_no": 0,
            "log_id": "log-1",
            "data": {
                "data": {
                    "cursor": 10,
                    "has_more": True,
                    "search_id": "search-1",
                    "video_list": [_official_video("123", likes=88)],
                }
            },
        }

    adapter = DouyinKeywordAdapter("key", "secret", transport=transport)
    page = adapter.sync(
        SourceRequest(
            source=DataSource.OFFICIAL,
            keywords=["二手车"],
            request_id="request-1",
            page_size=10,
        )
    )

    assert len(calls) == 2
    assert calls[0][0] == "POST"
    assert calls[1][2]["access-token"] == "test-token"
    query = parse_qs(urlparse(calls[1][1]).query)
    assert query["keyword"] == ["二手车"]
    assert query["count"] == ["10"]
    assert query["publish_time"] == ["1"]
    assert query["sort_type"] == ["0"]
    assert page.cursor == "10"
    assert page.search_id == "search-1"
    item = page.items[0]
    assert item.metrics.likes == 88
    assert item.metrics.plays is None
    assert item.metrics.comments is None
    assert item.eligibility_status == EligibilityStatus.AUTO_MATCHED
    assert item.cohort_key == "douyin:keyword:二手车"


def test_official_keyword_adapter_retries_connection_only_once() -> None:
    search_attempts = 0

    def transport(method, url, headers, body):
        nonlocal search_attempts
        if method == "POST":
            return {"data": {"access_token": "token", "expires_in": 7200}}
        search_attempts += 1
        raise ConnectionError("offline")

    adapter = DouyinKeywordAdapter("key", "secret", transport=transport)
    with pytest.raises(OfficialApiError, match="已自动重试一次"):
        adapter.sync(SourceRequest(source=DataSource.OFFICIAL, keywords=["二手车"]))

    assert search_attempts == 2


def test_official_keyword_permission_error_is_not_retried() -> None:
    search_attempts = 0

    def transport(method, url, headers, body):
        nonlocal search_attempts
        if method == "POST":
            return {"data": {"access_token": "token", "expires_in": 7200}}
        search_attempts += 1
        return {"err_no": 2190004, "err_msg": "scope missing"}

    adapter = DouyinKeywordAdapter("key", "secret", transport=transport)
    with pytest.raises(OfficialApiError, match="scope missing"):
        adapter.sync(SourceRequest(source=DataSource.OFFICIAL, keywords=["二手车"]))

    assert search_attempts == 1


def test_official_keyword_404_retryable_propagation() -> None:
    """HTTP 404 时 transport 抛出的 OfficialApiError.retryable 应传递到上层。"""
    search_attempts = 0

    def transport(method, url, headers, body):
        nonlocal search_attempts
        if method == "POST":
            return {"data": {"access_token": "token", "expires_in": 7200}}
        search_attempts += 1
        raise OfficialApiError("404: Not Found", code=404, retryable=False)

    adapter = DouyinKeywordAdapter("key", "secret", transport=transport)
    with pytest.raises(OfficialApiError) as exc_info:
        adapter.sync(SourceRequest(source=DataSource.OFFICIAL, keywords=["二手车"]))

    # retryable=False 时不应重试
    assert exc_info.value.retryable is False
    assert search_attempts == 1


def test_official_keyword_404_retryable_endpoint_change() -> None:
    """当 transport 标记 retryable=True 时，adapter 应重试一次。"""
    search_attempts = 0

    def transport(method, url, headers, body):
        nonlocal search_attempts
        if method == "POST":
            return {"data": {"access_token": "token", "expires_in": 7200}}
        search_attempts += 1
        raise OfficialApiError("404: endpoint changed", code=404, retryable=True)

    adapter = DouyinKeywordAdapter("key", "secret", transport=transport)
    with pytest.raises(OfficialApiError) as exc_info:
        adapter.sync(SourceRequest(source=DataSource.OFFICIAL, keywords=["二手车"]))

    assert exc_info.value.retryable is True
    assert search_attempts == 2  # 应重试一次


class _PagedAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[SourceRequest] = []

    def capabilities(self) -> SourceCapability:
        return SourceCapability(
            provider_name="fixture",
            enabled=True,
            supports_keyword_search=True,
            permission_status="fixture",
            max_page_size=10,
        )

    def sync(self, request: SourceRequest) -> SourcePage:
        self.calls += 1
        self.requests.append(request)
        start = int(request.cursor or 0)
        items = [_normalized(str(index)) for index in range(start, start + 10)]
        return SourcePage(
            items=items,
            cursor=str(start + 10),
            search_id="fixture-search",
            has_more=start + 10 < 100,
        )


def _normalized(item_id: str) -> NormalizedCandidate:
    now = datetime.now(timezone.utc)
    return NormalizedCandidate(
        platform_item_id=item_id,
        title=f"二手车视频 {item_id}",
        author_id=f"author-{item_id}",
        author_name="测试作者",
        platform=Platform.DOUYIN,
        category="关键词/二手车",
        published_at=now,
        source_url=f"https://www.douyin.com/video/{item_id}",
        source_type=DataSource.OFFICIAL,
        metrics=VideoMetricSnapshot(
            item_id=item_id,
            sampled_at=now,
            likes=int(item_id),
            confidence=1.0,
        ),
        matched_by=["二手车"],
        cohort_key="douyin:keyword:二手车",
        eligibility_status=EligibilityStatus.AUTO_MATCHED,
    )


def test_discovery_fetches_one_page_and_persists_keyword_matches(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "discovery.db")
    adapter = _PagedAdapter()
    service = KeywordDiscoveryService(
        repository,
        SourceService(repository, HeatService()),
    )

    result = service.discover(keyword="二手车", adapter=adapter)

    assert adapter.calls == 1
    assert adapter.requests[0].limit == 10
    assert adapter.requests[0].page_size == 10
    assert adapter.requests[0].publish_time == 1
    assert adapter.requests[0].sort_type == 0
    assert result.api_call_count == 1
    assert result.unique_count == 10
    assert result.partial is False
    assert len(repository.list_candidates()) == 10
    matches = repository.list_candidate_matches(result.request_id)
    assert len(matches) == 10
    assert [match.platform_rank for match in matches] == list(range(1, 11))
    assert all(match.publish_time == 1 and match.sort_type == 0 for match in matches)
    assert repository.list_discovery_results()[0].request_id == result.request_id
    assert all(
        candidate.eligibility_status == EligibilityStatus.AUTO_MATCHED
        for candidate in repository.list_candidates()
    )
    assert not repository.list_reviews()
    trends = KeywordTrendService(repository).recompute("二手车")
    assert len(trends) == 10
    assert repository.list_keyword_trend_results("二手车") == trends


def test_discovery_accepts_seven_day_window_without_pagination(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "seven-days.db")
    adapter = _PagedAdapter()
    service = KeywordDiscoveryService(
        repository,
        SourceService(repository, HeatService()),
    )

    result = service.discover(keyword="二手车", adapter=adapter, publish_time=7)

    assert adapter.calls == 1
    assert adapter.requests[0].publish_time == 7
    assert result.publish_time == 7
    assert result.api_call_count == 1


def test_discovery_uses_selected_count_in_one_request(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "selected-count.db")
    adapter = _PagedAdapter()
    service = KeywordDiscoveryService(
        repository,
        SourceService(repository, HeatService()),
    )

    result = service.discover(keyword="二手车", adapter=adapter, count=4)

    assert adapter.calls == 1
    assert adapter.requests[0].limit == 4
    assert adapter.requests[0].page_size == 4
    assert result.requested_count == 4
    assert result.unique_count == 4
    assert len(repository.list_candidate_matches(result.request_id)) == 4

    with pytest.raises(ValueError, match="1 到 10"):
        service.discover(keyword="二手车", adapter=adapter, count=11)


def test_discovery_without_credentials_makes_no_transport_call() -> None:
    transport_calls = 0

    def transport(method, url, headers, body):
        nonlocal transport_calls
        transport_calls += 1
        return {}

    repository = MockRepository(candidates=[], tasks=[])
    service = KeywordDiscoveryService(
        repository,
        SourceService(repository, HeatService()),
    )
    result = service.discover(
        keyword="二手车",
        adapter=DouyinKeywordAdapter(transport=transport),
    )

    assert result.unique_count == 0
    assert result.api_call_count == 0
    assert result.permission_status == "credentials_missing"
    assert transport_calls == 0


def _trend_candidate(
    item_id: str,
    *,
    now: datetime,
    likes: int,
    age_hours: float = 4,
    platform: Platform = Platform.DOUYIN,
) -> VideoCandidate:
    metrics = VideoMetricSnapshot(
        item_id=item_id,
        sampled_at=now - timedelta(hours=2),
        likes=likes,
        confidence=1.0,
    )
    return VideoCandidate(
        video_id=item_id,
        platform_item_id=item_id,
        title=f"趋势视频 {item_id}",
        author_id=f"author-{item_id}",
        author_name="趋势作者",
        platform=platform,
        category="关键词/二手车",
        published_at=now - timedelta(hours=age_hours),
        source_url=(
            f"https://www.douyin.com/video/{item_id}"
            if platform == Platform.DOUYIN
            else f"https://www.xiaohongshu.com/explore/{item_id}"
        ),
        source_type=DataSource.OFFICIAL,
        eligibility_status=EligibilityStatus.AUTO_MATCHED,
        metrics=metrics,
        heat=HeatResult(
            score=0,
            level=HeatLevel.INSUFFICIENT,
            confidence=1.0,
        ),
    )


def _save_match(
    repository: MockRepository,
    *,
    candidate_id: str,
    request_id: str,
    observed_at: datetime,
    rank: int,
    keyword: str = "二手车",
    platform: Platform = Platform.DOUYIN,
) -> None:
    from src.models import CandidateMatch

    repository.save_candidate_match(
        CandidateMatch(
            request_id=request_id,
            video_id=candidate_id,
            keyword=keyword,
            cohort_key=f"douyin:keyword:{keyword}",
            platform=platform,
            platform_rank=rank,
            observed_at=observed_at,
        )
    )


def test_first_keyword_snapshot_only_observes_and_ranks_top_ten() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    candidates = [
        _trend_candidate(str(index), now=now, likes=100 + index) for index in range(12)
    ]
    repository = MockRepository(candidates=candidates, tasks=[])
    for index, candidate in enumerate(candidates, start=1):
        _save_match(
            repository,
            candidate_id=candidate.video_id,
            request_id="run-1",
            observed_at=now,
            rank=min(index, 10),
        )

    results = KeywordTrendService(repository).recompute("二手车", now=now)

    assert len(results) == 10
    assert all(result.level == KeywordTrendLevel.OBSERVING for result in results)
    assert all(result.confidence == 0.65 for result in results)
    assert all(result.display_tier == "observing" for result in results)
    assert all(result.recrawl_count == 0 for result in results)
    assert all(result.pool_size == 12 for result in results)


def test_keyword_trend_uses_seven_day_window_and_isolates_keywords() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    candidate = _trend_candidate("shared", now=now, likes=500)
    old = _trend_candidate("old", now=now, likes=900)
    repository = MockRepository(candidates=[candidate, old], tasks=[])
    _save_match(
        repository,
        candidate_id="shared",
        request_id="car-run",
        observed_at=now,
        rank=1,
    )
    _save_match(
        repository,
        candidate_id="shared",
        request_id="ai-run",
        observed_at=now,
        rank=2,
        keyword="AI工具",
    )
    _save_match(
        repository,
        candidate_id="old",
        request_id="old-run",
        observed_at=now - timedelta(days=8),
        rank=1,
    )

    car_results = KeywordTrendService(repository).recompute("二手车", now=now)
    ai_results = KeywordTrendService(repository).recompute("AI工具", now=now)

    assert [item.candidate_id for item in car_results] == ["shared"]
    assert [item.candidate_id for item in ai_results] == ["shared"]
    assert car_results[0].platform_rank == 1
    assert ai_results[0].platform_rank == 2

    expired = KeywordTrendService(repository).recompute(
        "二手车", now=now + timedelta(days=8)
    )
    assert expired == []
    assert repository.list_keyword_trend_results("二手车") == []


def test_keyword_trend_isolates_same_keyword_by_platform(tmp_path) -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    douyin = _trend_candidate("dy", now=now, likes=100, platform=Platform.DOUYIN)
    xhs = _trend_candidate("xhs", now=now, likes=10_000, platform=Platform.XIAOHONGSHU)
    repository = MockRepository(candidates=[douyin, xhs], tasks=[])
    _save_match(
        repository,
        candidate_id=douyin.video_id,
        request_id="dy-run",
        observed_at=now,
        rank=2,
        platform=Platform.DOUYIN,
    )
    _save_match(
        repository,
        candidate_id=xhs.video_id,
        request_id="xhs-run",
        observed_at=now,
        rank=1,
        platform=Platform.XIAOHONGSHU,
    )

    douyin_results = KeywordTrendService(repository).recompute(
        "二手车", platform=Platform.DOUYIN, now=now
    )
    xhs_results = KeywordTrendService(repository).recompute(
        "二手车", platform=Platform.XIAOHONGSHU, now=now
    )

    assert [item.candidate_id for item in douyin_results] == ["dy"]
    assert [item.candidate_id for item in xhs_results] == ["xhs"]
    assert (
        repository.list_keyword_trend_results("二手车", platform=Platform.DOUYIN)
        == douyin_results
    )
    assert (
        repository.list_keyword_trend_results("二手车", platform=Platform.XIAOHONGSHU)
        == xhs_results
    )

    sqlite_repository = SQLiteRepository(tmp_path / "platform-trends.db")
    sqlite_repository.save_candidate(douyin)
    sqlite_repository.save_candidate(xhs)
    sqlite_repository.save_keyword_trend_results(douyin_results)
    sqlite_repository.save_keyword_trend_results(xhs_results)
    assert (
        sqlite_repository.list_keyword_trend_results("二手车", platform=Platform.DOUYIN)
        == douyin_results
    )
    assert (
        sqlite_repository.list_keyword_trend_results(
            "二手车", platform=Platform.XIAOHONGSHU
        )
        == xhs_results
    )


def test_keyword_growth_waits_for_three_recrawls_before_formal_level() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    candidates = [
        _trend_candidate(str(index), now=now, likes=100 + index) for index in range(30)
    ]
    repository = MockRepository(candidates=candidates, tasks=[])
    for index, candidate in enumerate(candidates):
        repository.append_snapshot(
            candidate.metrics.model_copy(
                update={
                    "sampled_at": now,
                    "likes": (5000 if index == 29 else 120 + index),
                }
            )
        )
        _save_match(
            repository,
            candidate_id=candidate.video_id,
            request_id="growth-run",
            observed_at=now,
            rank=1 if index == 29 else min(10, index % 10 + 1),
        )

    results = KeywordTrendService(repository).recompute("二手车", now=now)
    target = next(item for item in results if item.candidate_id == "29")

    assert target.percentiles["growth"] >= 80
    assert target.confidence >= 0.60
    assert target.level == KeywordTrendLevel.OBSERVING
    assert target.display_tier == "observing"
    assert target.recrawl_count == 1
    assert target.provisional is True


def test_keyword_growth_can_promote_after_three_recrawls() -> None:
    first_seen_at = datetime(2026, 7, 17, 0, tzinfo=timezone.utc)
    computed_at = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    candidates = [
        _trend_candidate(
            str(index),
            now=first_seen_at + timedelta(hours=2),
            likes=100 + index,
            age_hours=3,
        )
        for index in range(30)
    ]
    repository = MockRepository(candidates=candidates, tasks=[])
    for index, candidate in enumerate(candidates):
        values = (
            [100 + index, 102 + index, 106 + index, 112 + index]
            if index != 29
            else [100, 300, 1100, 4300]
        )
        for request_index, (sampled_at, likes) in enumerate(
            zip(
                [
                    first_seen_at,
                    first_seen_at + timedelta(hours=2),
                    first_seen_at + timedelta(hours=6),
                    first_seen_at + timedelta(hours=12),
                ],
                values,
                strict=True,
            )
        ):
            if request_index > 0:
                repository.append_snapshot(
                    candidate.metrics.model_copy(
                        update={"sampled_at": sampled_at, "likes": likes}
                    )
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
    assert target.recall_count == 4
    assert target.engagement_growth_per_hour is not None
    assert target.percentiles["growth"] >= 90
    assert target.level in {
            KeywordTrendLevel.S,
            KeywordTrendLevel.A,
            KeywordTrendLevel.B,
        }
    assert target.display_tier in {"exploding", "hot", "potential"}


def test_suspicious_like_structure_is_penalized_not_removed() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    candidates = [
        _trend_candidate(str(index), now=now, likes=100 + index) for index in range(10)
    ]
    candidates[-1] = _trend_candidate("9", now=now, likes=100_000)
    repository = MockRepository(candidates=candidates, tasks=[])
    for candidate in candidates:
        rank = 9 if candidate.video_id == "9" else 1
        _save_match(
            repository,
            candidate_id=candidate.video_id,
            request_id="run-1",
            observed_at=now - timedelta(hours=3),
            rank=rank,
        )
        _save_match(
            repository,
            candidate_id=candidate.video_id,
            request_id="run-2",
            observed_at=now,
            rank=rank,
        )

    results = KeywordTrendService(repository).recompute("二手车", now=now)
    suspicious = next(item for item in results if item.candidate_id == "9")

    assert suspicious.anomaly_status.value == "suspected"
    assert suspicious.anomaly_penalty == 0.75
    assert suspicious in results
    assert any("疑似结构异常" in reason for reason in suspicious.reasons)


def test_missing_likes_stays_null_and_negative_growth_is_flagged() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    missing = _trend_candidate("missing", now=now, likes=10)
    missing = missing.model_copy(
        update={
            "metrics": missing.metrics.model_copy(update={"likes": None}),
        }
    )
    falling = _trend_candidate("falling", now=now, likes=200)
    repository = MockRepository(candidates=[missing, falling], tasks=[])
    repository.append_snapshot(
        falling.metrics.model_copy(update={"sampled_at": now, "likes": 100})
    )
    for candidate in (missing, falling):
        _save_match(
            repository,
            candidate_id=candidate.video_id,
            request_id="run-1",
            observed_at=now,
            rank=1,
        )

    results = KeywordTrendService(repository).recompute("二手车", now=now)
    missing_result = next(item for item in results if item.candidate_id == "missing")
    falling_result = next(item for item in results if item.candidate_id == "falling")

    assert missing_result.likes_per_hour is None
    assert missing_result.component_scores["age_adjusted_likes"] is None
    assert falling_result.anomaly_status.value == "suspected"
    assert any("点赞计数出现倒退" in reason for reason in falling_result.reasons)


def test_keyword_level_thresholds_are_gated() -> None:
    service = KeywordTrendService(MockRepository(candidates=[], tasks=[]))

    assert (
        service._level(
            score=90,
            confidence=0.85,
            pool_size=100,
            growth_percentile=96,
            acceleration_percentile=90,
            recrawl_count=3,
            recall_count=4,
            age_hours=10,
        )
        == KeywordTrendLevel.S
    )
    assert (
        service._level(
            score=80,
            confidence=0.75,
            pool_size=100,
            growth_percentile=92,
            acceleration_percentile=50,
            recrawl_count=3,
            recall_count=4,
            age_hours=10,
        )
        == KeywordTrendLevel.A
    )
    assert (
        service._level(
            score=70,
            confidence=0.65,
            pool_size=30,
            growth_percentile=85,
            acceleration_percentile=None,
            recrawl_count=3,
            recall_count=3,
            age_hours=10,
        )
        == KeywordTrendLevel.B
    )
    # 门槛 MIN_CONFIDENT_POOL_SIZE 曾为 30,现为 5;pool 不足
    # MIN_COMPARABLE_POOL_SIZE(3)时保持 OBSERVING。
    assert (
        service._level(
            score=90,
            confidence=0.90,
            pool_size=2,
            growth_percentile=99,
            acceleration_percentile=90,
            recrawl_count=3,
            recall_count=4,
            age_hours=1,
        )
        == KeywordTrendLevel.OBSERVING
    )


def test_sqlite_migrates_existing_candidate_matches(tmp_path) -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    database_path = tmp_path / "migration.db"
    repository = SQLiteRepository(database_path)
    candidate = _trend_candidate("legacy", now=now, likes=100)
    repository.save_candidate(candidate)
    repository.save_discovery_result(
        DiscoveryResult(
            request_id="legacy-run",
            keyword="二手车",
            provider_name="legacy",
            requested_count=10,
            permission_status="legacy",
            started_at=now - timedelta(minutes=1),
            finished_at=now,
        )
    )
    repository.close()

    connection = sqlite3.connect(database_path)
    connection.execute("DROP TABLE candidate_matches")
    connection.execute(
        """
        CREATE TABLE candidate_matches (
            request_id TEXT NOT NULL REFERENCES discovery_runs(request_id),
            video_id TEXT NOT NULL REFERENCES candidates(video_id),
            keyword TEXT NOT NULL,
            cohort_key TEXT NOT NULL,
            evidence TEXT,
            PRIMARY KEY(request_id, video_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO candidate_matches(
            request_id, video_id, keyword, cohort_key, evidence
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("legacy-run", "legacy", "二手车", "douyin:keyword:二手车", None),
    )
    connection.commit()
    connection.close()

    migrated = SQLiteRepository(database_path)
    match = migrated.list_candidate_matches("legacy-run")[0]

    assert match.platform_rank == 10
    assert match.observed_at == now
    assert match.publish_time == 1
    assert match.sort_type == 0


def test_sqlite_request_guard_is_shared_across_connections(tmp_path) -> None:
    database = tmp_path / "shared-guard.sqlite3"
    first = SQLiteRepository(database)
    second = SQLiteRepository(database)
    now = datetime.now().astimezone()

    assert first.claim_discovery_request("same", "request-1", now) is True
    assert second.claim_discovery_request("same", "request-2", now) is False
