from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.adapters.licensed import LicensedProviderError, SandboxLicensedSearchProvider
from src.models import (
    DataSource,
    Platform,
    PlatformRunStatus,
    ProviderCapability,
    ProviderErrorKind,
    ProviderMode,
    ProviderSearchItem,
    ProviderSearchPage,
    ProviderUsage,
    SamplingStatus,
    SearchBatch,
)
from src.repositories import MockRepository, SQLiteRepository
from src.services import HeatService, KeywordTrendService, SourceService
from src.services.commercial_search import (
    CommercialSearchService,
    title_matches_keyword,
)


class FixtureProvider:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.search_calls: list[Platform] = []
        self.error: LicensedProviderError | None = None
        self.fail_first_connection = False
        self.page_override: ProviderSearchPage | None = None
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
        if self.page_override is not None:
            return self.page_override
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
        KeywordTrendService(repository, clock=lambda: now),
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
        KeywordTrendService(repository, clock=lambda: now),
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
    assert preview[0].estimated_cost_cny == 0.09
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


def test_free_local_browser_recovers_from_unknown_outcome_without_billing_lock() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.capabilities = lambda: ProviderCapability(
        provider_name="free_local_browser",
        display_name="免费本机浏览器",
        mode=ProviderMode.LOCAL_BROWSER,
        enabled=True,
        supported_platforms=[
            Platform.DOUYIN,
            Platform.XIAOHONGSHU,
            Platform.WECHAT_CHANNELS,
        ],
        permission_status="local_browser",
    )
    provider.error = LicensedProviderError(
        "浏览器解析异常",
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
    second = _service(
        repository, provider, now + timedelta(minutes=5)
    ).execute(keyword="二手车", force_refresh=True)
    assert all(
        run.status not in {PlatformRunStatus.BLOCKED, PlatformRunStatus.OUTCOME_UNKNOWN}
        for run in repository.list_platform_search_runs(second.batch_id)
    )
    assert len(provider.search_calls) == 6


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


def test_empty_provider_response_is_recorded_as_provider_empty() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.page_override = ProviderSearchPage(
        platform=Platform.DOUYIN,
        provider="fixture_vendor",
        items=[],
        observed_at=now,
        request_id="provider-empty",
        api_call_count=1,
        billable_units=0.03,
        has_more=True,
    )

    batch = _douyin_only_service(repository, provider, now).execute(keyword="冷门词")
    run = repository.list_platform_search_runs(batch.batch_id)[0]

    assert run.status == PlatformRunStatus.PARTIAL
    assert run.result_state == "provider_empty"
    assert run.raw_item_count == 0
    assert run.parsed_item_count == 0
    assert run.returned_count == 0
    assert run.api_call_count == 1
    assert provider.search_calls == [Platform.DOUYIN]


def test_platform_search_keeps_readable_nonliteral_candidates_for_table_filtering() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.page_override = ProviderSearchPage(
        platform=Platform.DOUYIN,
        provider="fixture_vendor",
        items=[
            ProviderSearchItem(
                platform=Platform.DOUYIN,
                platform_item_id="title-match",
                title="2026 带货实战拆解 #带货",
                author_id="author-title",
                author_name="普通作者",
                published_at=now - timedelta(hours=2),
                source_url="https://www.douyin.com/video/title-match",
                provider_rank=1,
                metrics={
                    "item_id": "title-match",
                    "sampled_at": now,
                    "likes": 100,
                    "confidence": 0.9,
                },
            ),
            ProviderSearchItem(
                platform=Platform.DOUYIN,
                platform_item_id="author-only",
                title="今天去钓鱼",
                author_id="author-only",
                author_name="带货达人",
                published_at=now - timedelta(hours=2),
                source_url="https://www.douyin.com/video/author-only",
                provider_rank=2,
                metrics={
                    "item_id": "author-only",
                    "sampled_at": now,
                    "likes": 90,
                    "confidence": 0.9,
                },
            ),
            ProviderSearchItem(
                platform=Platform.DOUYIN,
                platform_item_id="unrelated",
                title="日常 vlog 记录",
                author_id="author-unrelated",
                author_name="普通作者",
                published_at=now - timedelta(hours=2),
                source_url="https://www.douyin.com/video/unrelated",
                provider_rank=3,
                metrics={
                    "item_id": "unrelated",
                    "sampled_at": now,
                    "likes": 80,
                    "confidence": 0.9,
                },
            ),
        ],
        observed_at=now,
        request_id="provider-relevance",
        api_call_count=1,
        billable_units=0.03,
        raw_item_count=3,
        parsed_item_count=3,
    )

    batch = _douyin_only_service(repository, provider, now).execute(keyword="带货")
    run = repository.list_platform_search_runs(batch.batch_id)[0]

    assert run.returned_count == 3
    assert run.irrelevant_count == 0
    assert run.result_state == "no_hot"
    candidates = repository.list_candidates()
    assert [candidate.video_id for candidate in candidates] == [
        "douyin-title-match",
        "douyin-author-only",
        "douyin-unrelated",
    ]
    assert any(
        "标题未直接命中“带货”" in warning
        for warning in candidates[1].data_quality_warnings
    )
    assert len(repository.list_candidate_matches(run.run_id)) == 3
    assert len(repository.list_sampling_checkpoints("带货")) == 3


def test_platform_search_respects_result_limit_without_title_based_skips() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.page_override = ProviderSearchPage(
        platform=Platform.DOUYIN,
        provider="fixture_vendor",
        items=[
            ProviderSearchItem(
                platform=Platform.DOUYIN,
                platform_item_id=f"noise-{index}",
                title=f"无关日常记录 {index}",
                author_id=f"noise-author-{index}",
                author_name="普通作者",
                published_at=now - timedelta(hours=2),
                source_url=f"https://www.douyin.com/video/noise-{index}",
                provider_rank=index + 1,
                metrics={
                    "item_id": f"noise-{index}",
                    "sampled_at": now,
                    "likes": 10,
                    "confidence": 0.9,
                },
            )
            for index in range(30)
        ]
        + [
            ProviderSearchItem(
                platform=Platform.DOUYIN,
                platform_item_id=f"match-{index}",
                title=f"餐饮门店引流方法 {index}",
                author_id=f"match-author-{index}",
                author_name="餐饮作者",
                published_at=now - timedelta(hours=3),
                source_url=f"https://www.douyin.com/video/match-{index}",
                provider_rank=31 + index,
                metrics={
                    "item_id": f"match-{index}",
                    "sampled_at": now,
                    "likes": 100,
                    "confidence": 0.9,
                },
            )
            for index in range(5)
        ],
        observed_at=now,
        request_id="provider-deep-relevance",
        api_call_count=1,
        raw_item_count=35,
        parsed_item_count=35,
    )

    batch = _douyin_only_service(repository, provider, now).execute(
        keyword="餐饮获客",
        count=5,
    )
    run = repository.list_platform_search_runs(batch.batch_id)[0]

    assert run.returned_count == 5
    assert run.irrelevant_count == 0
    assert len(repository.list_candidate_matches(run.run_id)) == 5
    assert [candidate.video_id for candidate in repository.list_candidates()] == [
        f"douyin-noise-{index}" for index in range(5)
    ]


def test_all_nonliteral_platform_results_are_imported_with_a_review_warning() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.page_override = ProviderSearchPage(
        platform=Platform.DOUYIN,
        provider="fixture_vendor",
        items=[
            ProviderSearchItem(
                platform=Platform.DOUYIN,
                platform_item_id="author-only",
                title="今天去钓鱼",
                author_id="author-only",
                author_name="带货达人",
                published_at=now - timedelta(hours=2),
                source_url="https://www.douyin.com/video/author-only",
                provider_rank=1,
                metrics={
                    "item_id": "author-only",
                    "sampled_at": now,
                    "likes": 90,
                    "confidence": 0.9,
                },
            )
        ],
        observed_at=now,
        request_id="provider-all-irrelevant",
        api_call_count=1,
        billable_units=0.03,
        raw_item_count=1,
        parsed_item_count=1,
    )

    batch = _douyin_only_service(repository, provider, now).execute(keyword="带货")
    run = repository.list_platform_search_runs(batch.batch_id)[0]

    assert run.result_state == "no_hot"
    assert run.returned_count == 1
    assert run.irrelevant_count == 0
    candidates = repository.list_candidates()
    assert [candidate.video_id for candidate in candidates] == ["douyin-author-only"]
    assert any(
        "标题未直接命中“带货”" in warning
        for warning in candidates[0].data_quality_warnings
    )
    assert len(repository.list_sampling_checkpoints("带货")) == 1


def test_strict_keyword_relevance_normalizes_spacing_and_punctuation() -> None:
    assert title_matches_keyword(title="AI-获客案例 #AI获客", keyword="ＡＩ 获客")
    assert title_matches_keyword(title="餐饮门店低成本引流方法", keyword="餐饮获客")
    assert title_matches_keyword(
        title="餐饮店在抖音怎么做才能获客",
        keyword="餐饮获客",
    )
    assert not title_matches_keyword(title="实体门店获客方法", keyword="餐饮获客")
    assert not title_matches_keyword(title="日常 vlog", keyword="获客")


def test_kuaishou_public_video_url_matches_platform() -> None:
    assert CommercialSearchService._url_matches_platform(
        "https://www.kuaishou.com/short-video/ks-video-1",
        Platform.KUAISHOU,
    )


def test_bilibili_nonmatching_search_card_is_never_imported() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    page = ProviderSearchPage(
        platform=Platform.BILIBILI,
        provider="fixture_vendor",
        items=[
            ProviderSearchItem(
                platform=Platform.BILIBILI,
                platform_item_id="BVmatch",
                title="贴标机在饮料行业的应用",
                author_id="author-match",
                author_name="匹配作者",
                published_at=now,
                source_url="https://www.bilibili.com/video/BVmatch",
                provider_rank=1,
                metrics={"item_id": "BVmatch", "sampled_at": now, "confidence": 0.9},
            ),
            ProviderSearchItem(
                platform=Platform.BILIBILI,
                platform_item_id="BVnoise",
                title="高级语言弹幕测试",
                author_id="author-noise",
                author_name="无关作者",
                published_at=now,
                source_url="https://www.bilibili.com/video/BVnoise",
                provider_rank=2,
                metrics={"item_id": "BVnoise", "sampled_at": now, "confidence": 0.9},
            ),
        ],
        observed_at=now,
        request_id="bilibili-strict-relevance",
    )

    normalized, errors, counts = CommercialSearchService._normalize_page(
        page,
        platform=Platform.BILIBILI,
        keyword="贴标机",
        provider="fixture_vendor",
        source_type=DataSource.PUBLIC_RESEARCH,
        published_after=None,
        limit=2,
    )

    assert errors == []
    assert [item.platform_item_id for item in normalized] == ["BVmatch"]
    assert counts["irrelevant_count"] == 1


def test_items_outside_requested_window_are_diagnosed_without_extra_pages() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.page_override = ProviderSearchPage(
        platform=Platform.DOUYIN,
        provider="fixture_vendor",
        items=[
            ProviderSearchItem(
                platform=Platform.DOUYIN,
                platform_item_id="old-video",
                title="冷门词旧视频",
                author_id="author-old",
                author_name="旧作者",
                published_at=now - timedelta(days=8),
                source_url="https://www.douyin.com/video/old-video",
                provider_rank=1,
                metrics={
                    "item_id": "old-video",
                    "sampled_at": now,
                    "likes": 1200,
                    "comments": 100,
                    "shares": 50,
                    "favorites": 80,
                    "confidence": 0.9,
                },
            )
        ],
        observed_at=now,
        request_id="provider-old",
        api_call_count=1,
        billable_units=0.03,
        raw_item_count=1,
        parsed_item_count=1,
        has_more=True,
    )

    batch = _douyin_only_service(repository, provider, now).execute(
        keyword="冷门词",
        published_window_days=7,
    )
    run = repository.list_platform_search_runs(batch.batch_id)[0]

    assert run.result_state == "all_out_of_window"
    assert run.raw_item_count == 1
    assert run.parsed_item_count == 1
    assert run.out_of_window_count == 1
    assert run.returned_count == 0
    assert len(repository.list_candidates()) == 0
    assert provider.search_calls == [Platform.DOUYIN]


def test_items_without_reliable_publish_time_are_not_presented_as_one_week_videos() -> None:
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.page_override = ProviderSearchPage(
        platform=Platform.DOUYIN,
        provider="fixture_vendor",
        items=[
            ProviderSearchItem(
                platform=Platform.DOUYIN,
                platform_item_id="unknown-publish-time",
                title="餐饮获客近期方法",
                author_id="author-unknown",
                author_name="待核验作者",
                published_at=now,
                source_url="https://www.douyin.com/video/unknown-publish-time",
                provider_rank=1,
                metrics={
                    "item_id": "unknown-publish-time",
                    "sampled_at": now,
                    "likes": 200,
                    "confidence": 0.6,
                },
                data_quality_warnings=["未取得有效发布时间，页面展示为采样时间。"],
            )
        ],
        observed_at=now,
        request_id="provider-unknown-time",
        raw_item_count=1,
        parsed_item_count=1,
    )

    batch = _douyin_only_service(repository, provider, now).execute(
        keyword="餐饮获客",
        published_window_days=7,
    )
    run = repository.list_platform_search_runs(batch.batch_id)[0]

    assert run.result_state == "all_out_of_window"
    assert run.out_of_window_count == 1
    assert run.returned_count == 0


def test_unlimited_monitoring_keeps_older_related_videos_and_schedules_three_points() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.page_override = ProviderSearchPage(
        platform=Platform.DOUYIN,
        provider="fixture_vendor",
        items=[
            ProviderSearchItem(
                platform=Platform.DOUYIN,
                platform_item_id="older-but-related",
                title="租房避坑完整指南",
                author_id="author-rent",
                author_name="租房作者",
                published_at=now - timedelta(days=90),
                source_url="https://www.douyin.com/video/older-but-related",
                provider_rank=1,
                metrics={
                    "item_id": "older-but-related",
                    "sampled_at": now,
                    "likes": 1200,
                    "comments": 90,
                    "shares": 30,
                    "favorites": 70,
                    "confidence": 0.9,
                },
            )
        ],
        observed_at=now,
        request_id="provider-unlimited",
        api_call_count=1,
        billable_units=0.03,
    )

    batch = _douyin_only_service(repository, provider, now).execute(keyword="租房")
    run = repository.list_platform_search_runs(batch.batch_id)[0]
    checkpoints = repository.list_sampling_checkpoints("租房")

    assert batch.published_window_days == 0
    assert run.out_of_window_count == 0
    assert run.returned_count == 1
    assert [item.offset_hours for item in checkpoints] == [2]


def test_unlimited_monitoring_executes_only_due_zero_window_recrawls() -> None:
    first_seen = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(first_seen)
    service = _douyin_only_service(repository, provider, first_seen)
    service.execute(keyword="租房")

    provider.now = first_seen + timedelta(hours=2)
    due_service = _douyin_only_service(repository, provider, provider.now)
    executed = due_service.execute_due_recrawls(
        max_groups=5,
        published_window_days=0,
    )

    assert len(executed) == 1
    checkpoints = repository.list_sampling_checkpoints("租房")
    assert next(item for item in checkpoints if item.offset_hours == 2).status == SamplingStatus.OBSERVED
    assert next(item for item in checkpoints if item.offset_hours in {4, 12}).status == SamplingStatus.PENDING


def test_explicit_tracking_authorization_is_required_for_due_paid_recrawls() -> None:
    first_seen = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(first_seen)
    service = _douyin_only_service(repository, provider, first_seen)
    batch = service.execute(keyword="租房", schedule_recrawls=False)

    assert repository.list_sampling_checkpoints("租房") == []
    tracked, created, due_at = service.start_batch_tracking(batch.batch_id)
    assert tracked.tracking_authorized is True
    assert created == 1
    assert due_at == first_seen + timedelta(hours=2)
    checkpoint = repository.list_sampling_checkpoints("租房")[0]
    assert checkpoint.billing_authorized is True
    assert checkpoint.tracking_batch_id == batch.batch_id

    provider.now = first_seen + timedelta(hours=2)
    due_service = _douyin_only_service(repository, provider, provider.now)
    executed = due_service.execute_due_recrawls(
        max_groups=5,
        published_window_days=0,
        authorized_only=True,
    )

    assert len(executed) == 1


def test_low_engagement_search_result_is_not_labeled_hot() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)
    provider.page_override = ProviderSearchPage(
        platform=Platform.DOUYIN,
        provider="fixture_vendor",
        items=[
            ProviderSearchItem(
                platform=Platform.DOUYIN,
                platform_item_id="ordinary-video",
                title="二手车低互动视频",
                author_id="ordinary-author",
                author_name="普通作者",
                published_at=now - timedelta(hours=2),
                source_url="https://www.douyin.com/video/ordinary-video",
                provider_rank=1,
                metrics={
                    "item_id": "ordinary-video",
                    "sampled_at": now,
                    "likes": 43,
                    "comments": 23,
                    "shares": 0,
                    "favorites": 0,
                    "confidence": 0.9,
                },
            )
        ],
        observed_at=now,
        request_id="provider-ordinary",
        api_call_count=1,
        billable_units=0.03,
        raw_item_count=1,
        parsed_item_count=1,
        has_more=True,
    )

    batch = _douyin_only_service(repository, provider, now).execute(keyword="二手车")
    run = repository.list_platform_search_runs(batch.batch_id)[0]
    trends = repository.list_keyword_trend_results("二手车", platform=Platform.DOUYIN)

    assert run.result_state == "no_hot"
    assert trends[0].display_tier == "observing"
    assert trends[0].recrawl_count == 0
    assert trends[0].effective_interactions == 112


def test_commercial_search_schedules_window_specific_recrawls_and_marks_misses() -> None:
    first_seen = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(first_seen)
    service = _douyin_only_service(repository, provider, first_seen)

    service.execute(keyword="二手车", published_window_days=1)
    checkpoints = repository.list_sampling_checkpoints("二手车")

    assert [item.offset_hours for item in checkpoints] == [2]
    assert all(item.status == SamplingStatus.PENDING for item in checkpoints)

    two_hours_later = first_seen + timedelta(hours=2)
    provider.now = two_hours_later
    later_service = _douyin_only_service(repository, provider, two_hours_later)
    later_service.execute(keyword="二手车", published_window_days=1, force_refresh=True)

    checkpoints = repository.list_sampling_checkpoints("二手车")
    assert [
        item.status for item in checkpoints if item.offset_hours == 2
    ] == [SamplingStatus.OBSERVED]
    next_checkpoint = next(item for item in checkpoints if item.offset_hours in {4, 12})
    assert next_checkpoint.status == SamplingStatus.PENDING

    thirteen_hours_later = first_seen + timedelta(hours=15)
    provider.now = thirteen_hours_later
    provider.page_override = ProviderSearchPage(
        platform=Platform.DOUYIN,
        provider="fixture_vendor",
        items=[],
        observed_at=thirteen_hours_later,
        request_id="provider-empty-after-recrawl",
        api_call_count=1,
        billable_units=0.03,
        has_more=True,
    )
    missed_service = _douyin_only_service(repository, provider, thirteen_hours_later)
    missed_service.execute(keyword="二手车", published_window_days=1, force_refresh=True)

    checkpoints = repository.list_sampling_checkpoints("二手车")
    assert [
        item.status for item in checkpoints if item.offset_hours == next_checkpoint.offset_hours
    ] == [SamplingStatus.MISSED]


def test_low_cost_fallback_can_skip_commercial_recrawl_schedule() -> None:
    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    provider = FixtureProvider(now)

    _douyin_only_service(repository, provider, now).execute(
        keyword="二手车",
        cache_ttl_minutes=24 * 60,
        schedule_recrawls=False,
    )

    assert repository.list_sampling_checkpoints("二手车") == []


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
    assert sum(item.estimated_cost_cny or 0 for item in later_preview) == 0.9


def test_search_batch_model_rejects_unsupported_window() -> None:
    half_year = SearchBatch(
        keyword="二手车",
        published_window_days=180,
        provider="fixture",
        mode=ProviderMode.SANDBOX,
    )
    assert half_year.published_window_days == 180

    supported = SearchBatch(
        keyword="二手车",
        published_window_days=300,
        provider="fixture",
        mode=ProviderMode.SANDBOX,
    )
    assert supported.published_window_days == 300

    recent = SearchBatch(
        keyword="二手车",
        published_window_days=30,
        provider="fixture",
        mode=ProviderMode.SANDBOX,
    )
    assert recent.published_window_days == 30

    try:
        SearchBatch(
            keyword="二手车",
            published_window_days=60,
            provider="fixture",
            mode=ProviderMode.SANDBOX,
        )
    except ValueError as exc:
        assert "近 30 天" in str(exc)
    else:
        raise AssertionError("unsupported window must be rejected")
