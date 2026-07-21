from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.adapters.licensed import LicensedProviderError, SandboxLicensedSearchProvider
from src.models import (
    Platform,
    PlatformRunStatus,
    ProviderCapability,
    ProviderErrorKind,
    ProviderMode,
    ProviderSearchItem,
    ProviderSearchPage,
    ProviderUsage,
    SearchBatch,
)
from src.repositories import MockRepository, SQLiteRepository
from src.services import HeatService, KeywordTrendService, SourceService
from src.services.commercial_search import CommercialSearchService


class FixtureProvider:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.search_calls: list[Platform] = []
        self.error: LicensedProviderError | None = None
        self.fail_first_connection = False
        self._attempts: dict[Platform, int] = {}

    def capabilities(self) -> ProviderCapability:
        return ProviderCapability(
            provider_name="fixture_vendor",
            display_name="固定响应供应商",
            mode=ProviderMode.PRODUCTION,
            enabled=True,
            supported_platforms=[
                Platform.DOUYIN,
                Platform.XIAOHONGSHU,
                Platform.WECHAT_CHANNELS,
            ],
            permission_status="verified_fixture",
            credential_alias="fixture-secret-alias",
        )

    def search(
        self,
        platform: Platform,
        keyword: str,
        published_after: datetime,
        limit: int,
        idempotency_key: str,
    ) -> ProviderSearchPage:
        self.search_calls.append(platform)
        self._attempts[platform] = self._attempts.get(platform, 0) + 1
        if self.error:
            raise self.error
        if self.fail_first_connection and self._attempts[platform] == 1:
            raise LicensedProviderError(
                "临时连接失败",
                kind=ProviderErrorKind.CONNECTION,
                retryable=True,
            )
        item_id = "same-id"
        return ProviderSearchPage(
            platform=platform,
            provider="fixture_vendor",
            items=[
                ProviderSearchItem(
                    platform=platform,
                    platform_item_id=item_id,
                    title=f"{keyword}-{platform.value}",
                    author_id=f"author-{platform.value}",
                    author_name=f"作者-{platform.value}",
                    published_at=self.now - timedelta(hours=2),
                    source_url=_url(platform, item_id),
                    provider_rank=7,
                    metrics={
                        "item_id": item_id,
                        "sampled_at": self.now,
                        "likes": 1000,
                        "comments": None,
                        "shares": 20,
                        "favorites": None,
                        "confidence": 0.9,
                    },
                )
            ],
            observed_at=self.now,
            request_id=f"provider-{platform.value}",
            api_call_count=1,
            billable_units=1,
            has_more=True,
        )

    def refresh_metrics(
        self,
        platform: Platform,
        platform_item_ids: list[str],
        idempotency_key: str,
    ) -> ProviderSearchPage:
        raise NotImplementedError

    def usage(self) -> ProviderUsage | None:
        return None


def _url(platform: Platform, item_id: str) -> str:
    if platform == Platform.DOUYIN:
        return f"https://www.douyin.com/video/{item_id}"
    if platform == Platform.XIAOHONGSHU:
        return f"https://www.xiaohongshu.com/explore/{item_id}"
    return f"https://channels.weixin.qq.com/platform/post/{item_id}"


def _service(repository, provider, now: datetime) -> CommercialSearchService:
    source = SourceService(repository, HeatService())
    return CommercialSearchService(
        repository,
        source,
        KeywordTrendService(repository),
        provider,
        clock=lambda: now,
    )


def _douyin_only_service(
    repository,
    provider,
    now: datetime,
) -> CommercialSearchService:
    source = SourceService(repository, HeatService())
    return CommercialSearchService(
        repository,
        source,
        KeywordTrendService(repository),
        provider,
        active_platforms=(Platform.DOUYIN,),
        clock=lambda: now,
    )


def test_douyin_only_service_never_calls_other_platforms() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.endpoint_prices_cny = {
        Platform.DOUYIN: 0.03,
        Platform.XIAOHONGSHU: 0.12,
        Platform.WECHAT_CHANNELS: 0.15,
    }
    service = _douyin_only_service(repository, provider, now)

    preview = service.preview(keyword="二手车")
    batch = service.execute(keyword="二手车")
    runs = repository.list_platform_search_runs(batch.batch_id)

    assert [item.platform for item in preview] == [Platform.DOUYIN]
    assert preview[0].estimated_cost_cny == 0.03
    assert batch.platforms == [Platform.DOUYIN]
    assert [run.platform for run in runs] == [Platform.DOUYIN]
    assert provider.search_calls == [Platform.DOUYIN]


def test_sandbox_executes_three_platforms_without_external_calls() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = SandboxLicensedSearchProvider(clock=lambda: now)
    batch = _service(repository, provider, now).execute(keyword="二手车")

    runs = repository.list_platform_search_runs(batch.batch_id)
    assert len(runs) == 3
    assert all(run.status == PlatformRunStatus.SUCCEEDED for run in runs)
    assert all(run.returned_count == 10 for run in runs)
    assert sum(run.api_call_count for run in runs) == 0
    assert len(repository.list_candidates()) == 30
    for platform in (
        Platform.DOUYIN,
        Platform.XIAOHONGSHU,
        Platform.WECHAT_CHANNELS,
    ):
        trends = repository.list_keyword_trend_results("二手车", platform=platform)
        assert len(trends) == 10
        assert all(item.platform == platform for item in trends)


def test_same_item_id_is_isolated_by_platform_and_null_metrics_stay_null() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    _service(repository, provider, now).execute(keyword="二手车")

    candidates = repository.list_candidates()
    assert len(candidates) == 3
    assert {item.platform for item in candidates} == {
        Platform.DOUYIN,
        Platform.XIAOHONGSHU,
        Platform.WECHAT_CHANNELS,
    }
    assert all(item.metrics.comments is None for item in candidates)
    assert all(item.metrics.favorites is None for item in candidates)
    assert all(
        repository.list_keyword_trend_results("二手车", platform=item.platform)[
            0
        ].platform_rank
        == 7
        for item in candidates
    )


def test_ten_minute_cache_creates_zero_new_calls_and_no_new_matches() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    service = _service(repository, provider, now)
    first = service.execute(keyword="二手车")
    second = service.execute(keyword="二手车")

    first_runs = repository.list_platform_search_runs(first.batch_id)
    second_runs = repository.list_platform_search_runs(second.batch_id)
    assert all(run.status == PlatformRunStatus.PARTIAL for run in first_runs)
    assert all(run.status == PlatformRunStatus.CACHED for run in second_runs)
    assert sum(run.api_call_count for run in second_runs) == 0
    assert len(provider.search_calls) == 3
    assert all(repository.list_candidate_matches(run.run_id) for run in first_runs)
    assert all(not repository.list_candidate_matches(run.run_id) for run in second_runs)


def test_force_refresh_within_sixty_seconds_is_blocked_across_batches() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    service = _service(repository, provider, now)
    service.execute(keyword="二手车")
    second = service.execute(keyword="二手车", force_refresh=True)

    runs = repository.list_platform_search_runs(second.batch_id)
    assert all(run.status == PlatformRunStatus.BLOCKED for run in runs)
    assert len(provider.search_calls) == 3


def test_connection_failure_is_not_retried_for_paid_post() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    retry_repository = MockRepository(candidates=[], tasks=[])
    retry_provider = FixtureProvider(now)
    retry_provider.fail_first_connection = True
    retried = _service(retry_repository, retry_provider, now).execute(keyword="二手车")

    assert all(
        run.status == PlatformRunStatus.FAILED
        for run in retry_repository.list_platform_search_runs(retried.batch_id)
    )
    assert len(retry_provider.search_calls) == 3

    limited_repository = MockRepository(candidates=[], tasks=[])
    limited_provider = FixtureProvider(now)
    limited_provider.error = LicensedProviderError(
        "请求过于频繁",
        kind=ProviderErrorKind.RATE_LIMIT,
        code="429",
        retryable=False,
    )
    limited = _service(limited_repository, limited_provider, now).execute(
        keyword="二手车"
    )
    assert all(
        run.status == PlatformRunStatus.FAILED
        for run in limited_repository.list_platform_search_runs(limited.batch_id)
    )
    assert len(limited_provider.search_calls) == 3


def test_unknown_outcome_blocks_later_retry_until_admin_resolution() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.error = LicensedProviderError(
        "响应中断，扣费状态未知",
        kind=ProviderErrorKind.OUTCOME_UNKNOWN,
        outcome_unknown=True,
    )
    service = _service(repository, provider, now)
    first = service.execute(keyword="二手车", force_refresh=True)
    assert all(
        run.status == PlatformRunStatus.OUTCOME_UNKNOWN
        for run in repository.list_platform_search_runs(first.batch_id)
    )

    provider.error = None
    later_service = _service(repository, provider, now + timedelta(minutes=5))
    second = later_service.execute(keyword="二手车", force_refresh=True)
    assert all(
        run.status == PlatformRunStatus.BLOCKED
        for run in repository.list_platform_search_runs(second.batch_id)
    )
    assert len(provider.search_calls) == 3


def test_sqlite_persists_batches_cache_and_platform_trend_key(tmp_path) -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    database = tmp_path / "commercial.sqlite3"
    repository = SQLiteRepository(database)
    provider = FixtureProvider(now)
    first = _service(repository, provider, now).execute(keyword="二手车")
    repository.close()

    reopened = SQLiteRepository(database)
    stored = reopened.get_search_batch(first.batch_id)
    assert stored is not None
    assert len(reopened.list_platform_search_runs(first.batch_id)) == 3
    for platform in (
        Platform.DOUYIN,
        Platform.XIAOHONGSHU,
        Platform.WECHAT_CHANNELS,
    ):
        assert reopened.list_keyword_trend_results("二手车", platform=platform)


def test_sqlite_migrates_legacy_trend_table_to_platform_primary_key(tmp_path) -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    database = tmp_path / "legacy-trend.sqlite3"
    repository = SQLiteRepository(database)
    provider = FixtureProvider(now)
    _service(repository, provider, now).execute(keyword="二手车")
    repository.close()

    import sqlite3

    connection = sqlite3.connect(database)
    connection.execute("DROP INDEX IF EXISTS idx_keyword_trends_latest")
    connection.execute(
        """
        CREATE TABLE legacy_keyword_trends AS
        SELECT keyword, video_id, computed_at, payload_json
        FROM keyword_trend_results
        WHERE platform = 'xiaohongshu'
        """
    )
    connection.execute("DROP TABLE keyword_trend_results")
    connection.execute(
        """
        CREATE TABLE keyword_trend_results (
            keyword TEXT NOT NULL,
            video_id TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(keyword, video_id, computed_at)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO keyword_trend_results
        SELECT keyword, video_id, computed_at, payload_json
        FROM legacy_keyword_trends
        """
    )
    connection.commit()
    connection.close()

    migrated = SQLiteRepository(database)
    columns = migrated.connection.execute(
        "PRAGMA table_info(keyword_trend_results)"
    ).fetchall()
    primary_key = [
        row["name"] for row in sorted(columns, key=lambda row: row["pk"]) if row["pk"]
    ]
    assert primary_key == ["keyword", "platform", "video_id", "computed_at"]
    assert migrated.list_keyword_trend_results("二手车", platform=Platform.XIAOHONGSHU)


def test_monthly_hard_limit_blocks_network_calls() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)

    class QuotaRepository(MockRepository):
        def monthly_platform_query_count(self, since: datetime) -> int:
            return 100

    repository = QuotaRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    batch = _service(repository, provider, now).execute(keyword="二手车")

    runs = repository.list_platform_search_runs(batch.batch_id)
    assert all(run.status == PlatformRunStatus.BLOCKED for run in runs)
    assert provider.search_calls == []


def test_monthly_cost_limit_blocks_network_calls() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)

    class CostRepository(MockRepository):
        def monthly_platform_query_cost(self, since: datetime) -> float:
            return 10.0

    repository = CostRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.endpoint_prices_cny = {
        Platform.DOUYIN: 0.03,
        Platform.XIAOHONGSHU: 0.12,
        Platform.WECHAT_CHANNELS: 0.15,
    }
    batch = _service(repository, provider, now).execute(keyword="二手车")

    runs = repository.list_platform_search_runs(batch.batch_id)
    assert all(run.status == PlatformRunStatus.BLOCKED for run in runs)
    assert provider.search_calls == []


def test_preview_reports_price_and_uses_six_hour_cache() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.endpoint_prices_cny = {
        Platform.DOUYIN: 0.03,
        Platform.XIAOHONGSHU: 0.12,
        Platform.WECHAT_CHANNELS: 0.15,
    }
    service = _service(repository, provider, now)
    service.execute(keyword="二手车")

    cached_preview = service.preview(keyword="二手车")

    assert all(item.cache_hit for item in cached_preview)
    assert all(item.estimated_api_calls == 0 for item in cached_preview)
    assert all(item.estimated_cost_cny == 0 for item in cached_preview)

    later_preview = _service(
        repository,
        provider,
        now + timedelta(hours=6, minutes=1),
    ).preview(keyword="二手车")

    assert all(not item.cache_hit for item in later_preview)
    assert sum(item.estimated_cost_cny or 0 for item in later_preview) == 0.3


def test_search_batch_model_rejects_unsupported_window() -> None:
    try:
        SearchBatch(
            keyword="二手车",
            published_window_days=30,
            provider="fixture",
            mode=ProviderMode.SANDBOX,
        )
    except ValueError as exc:
        assert "近 1 天或近 7 天" in str(exc)
    else:
        raise AssertionError("unsupported window must be rejected")
