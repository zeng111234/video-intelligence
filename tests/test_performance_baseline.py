"""性能基线测试 - 测量关键操作的基准性能

运行方式：
    pytest tests/test_performance_baseline.py -v --benchmark-json=baseline.json
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.models import (
    DataSource,
    EligibilityStatus,
    Platform,
    ProviderCapability,
    ProviderMode,
    ProviderSearchItem,
    ProviderSearchPage,
    ProviderUsage,
    VideoCandidate,
    VideoMetricSnapshot,
)
from src.repositories import SQLiteRepository
from src.services import HeatService, SourceService, KeywordTrendService
from src.services.commercial_search import CommercialSearchService


class BenchmarkProvider:
    """用于性能测试的模拟供应商"""

    def __init__(self, now: datetime, num_items: int = 10) -> None:
        self.now = now
        self.num_items = num_items

    def capabilities(self) -> ProviderCapability:
        return ProviderCapability(
            provider_name="benchmark_vendor",
            display_name="性能测试供应商",
            mode=ProviderMode.PRODUCTION,
            enabled=True,
            supported_platforms=[Platform.DOUYIN, Platform.XIAOHONGSHU, Platform.WECHAT_CHANNELS],
            permission_status="verified_fixture",
            credential_alias="benchmark-alias",
        )

    def search(self, platform, keyword, published_after, limit, idempotency_key):
        items = []
        for i in range(min(limit, self.num_items)):
            item_id = f"{platform.value}-item-{i}"
            items.append(
                ProviderSearchItem(
                    platform=platform,
                    platform_item_id=item_id,
                    title=f"{keyword}-{platform.value}-{i}",
                    author_id=f"author-{platform.value}-{i}",
                    author_name=f"作者-{i}",
                    published_at=self.now - timedelta(hours=i + 1),
                    source_url=f"https://example.com/{platform.value}/{item_id}",
                    provider_rank=i + 1,
                    metrics=VideoMetricSnapshot(
                        item_id=item_id,
                        sampled_at=self.now,
                        likes=1000 * (i + 1),
                        comments=100 * (i + 1),
                        shares=50 * (i + 1),
                        confidence=0.9,
                    ),
                )
            )
        return ProviderSearchPage(
            platform=platform,
            provider="benchmark_vendor",
            items=items,
            observed_at=self.now,
            request_id=f"bench-{platform.value}",
            api_call_count=1,
            billable_units=1,
            has_more=False,
        )

    def refresh_metrics(self, platform, platform_item_ids, idempotency_key):
        raise NotImplementedError

    def usage(self) -> ProviderUsage | None:
        return None


def _make_candidate(now: datetime, index: int, heat: HeatService) -> VideoCandidate:
    platforms = [Platform.DOUYIN, Platform.XIAOHONGSHU, Platform.KUAISHOU, Platform.WECHAT_CHANNELS]
    platform = platforms[index % len(platforms)]
    metric = VideoMetricSnapshot(
        item_id=f"v-{index:05d}",
        sampled_at=now,
        plays=1000 * (index + 1),
        likes=100 * (index + 1),
        comments=50 * (index + 1),
        shares=25 * (index + 1),
        confidence=0.9,
    )
    return VideoCandidate(
        video_id=f"v-{index:05d}",
        platform_item_id=f"item-{index:05d}",
        title=f"测试视频 {index}",
        author_id=f"author-{index}",
        author_name=f"作者 {index}",
        platform=platform,
        category="测试",
        published_at=now - timedelta(hours=index),
        duration_seconds=60 + index,
        source_url=f"https://example.com/v/{index}",
        source_type=DataSource.LICENSED_PROVIDER,
        rights_status="authorized",
        matched_by=["benchmark"],
        cohort_key=f"bench:{platform.value}:kw",
        eligibility_status=EligibilityStatus.AUTO_MATCHED,
        evidence="benchmark",
        metrics=metric,
        heat=heat.analyze(metric),
    )


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SQLiteRepository(Path(tmpdir) / "bench.db")
        yield db
        db.connection.close()


@pytest.fixture
def now():
    return datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def heat():
    return HeatService()


@pytest.fixture
def provider(now):
    return BenchmarkProvider(now)


# ---------------------------------------------------------------------------
# list_candidates() 基准
# ---------------------------------------------------------------------------
class TestListCandidatesBenchmark:

    def test_empty(self, benchmark, repo):
        benchmark(repo.list_candidates)

    def test_100_rows(self, benchmark, repo, now, heat):
        for i in range(100):
            repo.save_candidate(_make_candidate(now, i, heat))
        benchmark(repo.list_candidates)

    def test_500_rows(self, benchmark, repo, now, heat):
        for i in range(500):
            repo.save_candidate(_make_candidate(now, i, heat))
        benchmark(repo.list_candidates)


# ---------------------------------------------------------------------------
# resolve_candidate_id() 基准
# ---------------------------------------------------------------------------
class TestResolveCandidateIdBenchmark:

    def test_hit(self, benchmark, repo, now, heat):
        for i in range(1000):
            repo.save_candidate(_make_candidate(now, i, heat))
        benchmark(repo.resolve_candidate_id, "douyin", "item-00500")

    def test_miss(self, benchmark, repo, now, heat):
        for i in range(1000):
            repo.save_candidate(_make_candidate(now, i, heat))
        benchmark(repo.resolve_candidate_id, "douyin", "no-such-item")


# ---------------------------------------------------------------------------
# save_candidate() 基准
# ---------------------------------------------------------------------------
class TestSaveCandidateBenchmark:

    def test_insert(self, benchmark, repo, now, heat):
        c = _make_candidate(now, 0, heat)
        benchmark(repo.save_candidate, c)

    def test_upsert(self, benchmark, repo, now, heat):
        c = _make_candidate(now, 0, heat)
        repo.save_candidate(c)
        benchmark(repo.save_candidate, c)


# ---------------------------------------------------------------------------
# CommercialSearchService.execute() 基准
# ---------------------------------------------------------------------------
class TestCommercialSearchBenchmark:

    def test_single_platform(self, benchmark, repo, now, provider, heat):
        svc = CommercialSearchService(
            repository=repo,
            source_service=SourceService(repo, heat),
            trend_service=KeywordTrendService(repo),
            provider=provider,
        )
        benchmark(
            svc.execute,
            keyword="测试关键词",
            published_window_days=1,
            count=10,
            platforms=(Platform.DOUYIN,),
        )

    def test_two_platforms(self, benchmark, repo, now, provider, heat):
        svc = CommercialSearchService(
            repository=repo,
            source_service=SourceService(repo, heat),
            trend_service=KeywordTrendService(repo),
            provider=provider,
        )
        benchmark(
            svc.execute,
            keyword="测试关键词",
            published_window_days=1,
            count=10,
            platforms=(Platform.DOUYIN, Platform.XIAOHONGSHU),
        )


# ---------------------------------------------------------------------------
# 计算基准（参考锚点）
# ---------------------------------------------------------------------------
def test_compute_anchor(benchmark):
    benchmark(sum, range(100_000))
