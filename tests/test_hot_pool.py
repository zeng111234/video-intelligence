"""官方热榜池 + 本地关键词匹配 + 热点词同步 服务层测试。

使用 fake 适配器（按官方热榜/热点词公开契约编程），不依赖 src/adapters/official.py。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from src.models import (
    DataSource,
    EligibilityStatus,
    NormalizedCandidate,
    Platform,
    SamplingStatus,
    SourceCapability,
    SourcePage,
    SourceRequest,
    VideoMetricSnapshot,
)
from src.repositories import MockRepository, SQLiteRepository
from src.services import (
    HeatService,
    KeywordTrendService,
    SourceService,
)
from src.services.hot_pool import (
    HOT_POOL_NO_MATCH_NOTICE,
    HOT_POOL_NO_MATCH_STATE,
    OfficialHotPoolService,
)


@dataclass
class FakeHotWordEntry:
    """与 official.HotWordEntry 契约对齐的 fake 条目。"""

    word: str
    hot_value: int | None
    fetched_at: datetime
    raw: dict = field(default_factory=dict)


class FakeBillboardAdapter:
    """按 DouyinHotBillboardAdapter 公开契约实现的 fake。"""

    def __init__(self, items: list[NormalizedCandidate], enabled: bool = True) -> None:
        self.items = items
        self.enabled = enabled
        self.sync_calls = 0

    def capabilities(self) -> SourceCapability:
        return SourceCapability(
            provider_name="douyin_hot_billboard",
            enabled=self.enabled,
            permission_status=(
                "verified_fixture" if self.enabled else "permission_unavailable"
            ),
            max_page_size=50,
            missing_configuration=(
                [] if self.enabled else ["data.external.billboard_hot_video"]
            ),
        )

    def sync(self, request: SourceRequest) -> SourcePage:
        self.sync_calls += 1
        if not self.enabled:
            raise RuntimeError("官方热榜权限未开通")
        return SourcePage(items=list(self.items))


class FakeHotWordsAdapter:
    def __init__(self, entries: list[FakeHotWordEntry], enabled: bool = True) -> None:
        self.entries = entries
        self.enabled = enabled
        self.fetch_calls = 0

    def capabilities(self) -> SourceCapability:
        return SourceCapability(
            provider_name="douyin_hot_words",
            enabled=self.enabled,
            permission_status=(
                "verified_fixture" if self.enabled else "permission_unavailable"
            ),
            missing_configuration=[] if self.enabled else ["hot_words_permission"],
        )

    def fetch_hot_words(self) -> list[FakeHotWordEntry]:
        self.fetch_calls += 1
        return list(self.entries)


def _billboard_item(
    item_id: str,
    *,
    rank: int,
    title: str,
    hot_words: list[str] | None = None,
    digg_count: int = 100,
    comment_count: int | None = 5,
    sampled_at: datetime,
) -> NormalizedCandidate:
    share_url = f"https://www.iesdouyin.com/share/video/{item_id}/"
    evidence_payload = {
        "rank": rank,
        "hot_words": hot_words or [],
        "hot_value": 1_000_000 - rank,
        "play_count": 50_000,
        "share_url": share_url,
    }
    return NormalizedCandidate(
        platform_item_id=item_id,
        title=title,
        author_id=f"author-{item_id}",
        author_name=f"作者{item_id}",
        platform=Platform.DOUYIN,
        category="官方热榜",
        published_at=sampled_at - timedelta(hours=6),
        source_url=share_url,
        source_type=DataSource.OFFICIAL,
        metrics=VideoMetricSnapshot(
            item_id=item_id,
            sampled_at=sampled_at,
            likes=digg_count,
            comments=comment_count,
            confidence=1.0,
        ),
        matched_by=hot_words or [],
        cohort_key="douyin_hot_billboard:官方热榜",
        eligibility_status=EligibilityStatus.AUTO_MATCHED,
        evidence="official_billboard:" + json.dumps(evidence_payload, ensure_ascii=False),
        official_hot=True,
        official_rank=rank,
        official_hot_value=float(1_000_000 - rank),
    )


def _build_service(repository, billboard, hot_words=None, clock=None):
    source_service = SourceService(repository, HeatService())
    return OfficialHotPoolService(
        repository,
        source_service,
        KeywordTrendService(repository),
        billboard_adapter=billboard,
        hot_words_adapter=hot_words,
        clock=clock,
    )


def test_sync_billboard_persists_pool_snapshots_and_share_url(tmp_path) -> None:
    now = datetime(2026, 7, 23, 10, tzinfo=timezone.utc)
    items = [
        _billboard_item("a1", rank=1, title="AI 数字人口播获客", sampled_at=now),
        _billboard_item(
            "a2", rank=2, title="二手车行情解读", comment_count=None, sampled_at=now
        ),
    ]
    repository = SQLiteRepository(tmp_path / "hot-pool.db")
    service = _build_service(repository, FakeBillboardAdapter(items))

    report = service.sync_billboard()

    assert report.added_candidates == 2
    assert report.added_snapshots == 2
    pool = repository.list_official_hot_pool()
    assert [candidate.video_id for candidate in pool] == [
        "douyin-a1",
        "douyin-a2",
    ]
    first = pool[0]
    # share_url 按官方返回原样保存，不伪装成 v.douyin.com 短链
    assert str(first.source_url) == "https://www.iesdouyin.com/share/video/a1/"
    assert first.evidence.startswith("official_billboard:")
    assert first.share_count is None and first.collect_count is None
    # 评论缺失时保持 null 而不是 0
    second = pool[1]
    assert second.metrics.comments is None
    assert len(repository.list_snapshots("douyin-a1")) == 1


def test_search_matches_by_title_and_hot_words(tmp_path) -> None:
    now = datetime(2026, 7, 23, 10, tzinfo=timezone.utc)
    items = [
        _billboard_item("a1", rank=1, title="今天天气不错", sampled_at=now),
        _billboard_item(
            "a2",
            rank=2,
            title="日常 vlog",
            hot_words=["AI数字人", "口播获客"],
            sampled_at=now,
        ),
    ]
    repository = SQLiteRepository(tmp_path / "match.db")
    service = _build_service(repository, FakeBillboardAdapter(items))
    service.sync_billboard()

    by_title = service.search_hot_pool(keyword="天气")
    assert by_title.result_state == "官方热榜匹配"
    assert [c.video_id for c in by_title.matched] == ["douyin-a1"]

    by_hot_word = service.search_hot_pool(keyword="数字人")
    assert by_hot_word.result_state == "官方热榜匹配"
    assert [c.video_id for c in by_hot_word.matched] == ["douyin-a2"]


def test_search_related_terms_expand_only_explicit_official_pool_matches(tmp_path) -> None:
    now = datetime(2026, 7, 23, 10, tzinfo=timezone.utc)
    items = [
        _billboard_item(
            "a1",
            rank=1,
            title="日常口播获客实战",
            hot_words=["口播获客"],
            sampled_at=now,
        )
    ]
    repository = SQLiteRepository(tmp_path / "related-terms.db")
    service = _build_service(repository, FakeBillboardAdapter(items))
    service.sync_billboard()

    result = service.search_hot_pool(
        keyword="企业获客", related_terms=["口播", "企业获客", "口播"]
    )

    assert [candidate.video_id for candidate in result.matched] == ["douyin-a1"]
    assert result.match_reasons["douyin-a1"] == "相关赛道词命中：口播"


def test_search_zero_match_state_is_pool_empty_not_provider_error(tmp_path) -> None:
    now = datetime(2026, 7, 23, 10, tzinfo=timezone.utc)
    items = [_billboard_item("a1", rank=1, title="完全无关的内容", sampled_at=now)]
    repository = SQLiteRepository(tmp_path / "empty.db")
    service = _build_service(repository, FakeBillboardAdapter(items))
    service.sync_billboard()

    result = service.search_hot_pool(keyword="冷门关键词")

    assert result.result_state == HOT_POOL_NO_MATCH_STATE
    assert result.user_notice == HOT_POOL_NO_MATCH_NOTICE
    assert "不代表抖音搜索无视频" in result.user_notice
    # 明确区别于供应商异常状态
    assert result.result_state not in {
        "provider_empty",
        "provider_payload_invalid",
        "all_invalid",
        "all_out_of_window",
    }
    # 本地匹配零计费
    stored = repository.list_discovery_results()[0]
    assert stored.result_state == HOT_POOL_NO_MATCH_STATE
    assert stored.api_call_count == 0
    assert stored.user_notice == HOT_POOL_NO_MATCH_NOTICE


def test_sync_billboard_disabled_raises_clear_error(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "disabled.db")
    service = _build_service(
        repository, FakeBillboardAdapter([], enabled=False)
    )
    with pytest.raises(ValueError, match="官方热榜不可用"):
        service.sync_billboard()


def test_hot_words_sync_persists_and_suggests(tmp_path) -> None:
    now = datetime(2026, 7, 23, 10, tzinfo=timezone.utc)
    entries = [
        FakeHotWordEntry("AI数字人", 9_900_000, now, {"sentence_id": "1"}),
        FakeHotWordEntry("二手车", None, now, {}),
        FakeHotWordEntry("AI数字人", 9_800_000, now - timedelta(hours=2), {}),
    ]
    repository = SQLiteRepository(tmp_path / "words.db")
    service = _build_service(
        repository,
        FakeBillboardAdapter([]),
        hot_words=FakeHotWordsAdapter(entries),
    )

    result = service.sync_hot_words()

    assert result.error is None
    assert result.saved_count == 2
    suggestions = service.hot_word_suggestions()
    assert [item.word for item in suggestions] == ["AI数字人", "二手车"]
    assert suggestions[0].hot_value == 9_900_000
    assert suggestions[0].fetched_at == now
    assert suggestions[1].hot_value is None


def test_hot_words_sync_disabled_returns_error_without_raising(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "words-disabled.db")
    service = _build_service(
        repository,
        FakeBillboardAdapter([]),
        hot_words=FakeHotWordsAdapter([], enabled=False),
    )
    result = service.sync_hot_words()
    assert result.saved_count == 0
    assert result.error is not None
    assert repository.list_hot_words() == []


def test_monitor_saves_match_schedules_recrawl_and_exposes_next_time(tmp_path) -> None:
    now = datetime(2026, 7, 23, 10, tzinfo=timezone.utc)
    items = [
        _billboard_item(
            "a1",
            rank=1,
            title="AI 数字人口播获客实战",
            hot_words=["AI数字人"],
            sampled_at=now,
        )
    ]
    repository = SQLiteRepository(tmp_path / "monitor.db")
    service = _build_service(
        repository, FakeBillboardAdapter(items), clock=lambda: now.astimezone()
    )

    result = service.monitor(keyword="数字人")

    assert result.result_state == "官方热榜匹配"
    assert result.sync_report is not None
    checkpoints = repository.list_sampling_checkpoints("数字人")
    # 近 1 天窗口复爬间隔 2h/6h/12h
    assert sorted(c.offset_hours for c in checkpoints) == [2, 6, 12]
    assert all(c.status == SamplingStatus.PENDING for c in checkpoints)
    assert result.next_recrawl_at == now + timedelta(hours=2)
    matches = repository.list_candidate_matches(result.request_id)
    assert len(matches) == 1
    assert matches[0].provider_name == "douyin_hot_billboard"
    # 趋势结果暴露增长阶段字段
    assert result.trends
    trend = result.trends[0]
    assert trend.snapshot_count == 1
    assert trend.next_recrawl_at == now + timedelta(hours=2)


def test_monitor_zero_match_keeps_pool_empty_state(tmp_path) -> None:
    now = datetime(2026, 7, 23, 10, tzinfo=timezone.utc)
    items = [_billboard_item("a1", rank=1, title="无关内容", sampled_at=now)]
    repository = SQLiteRepository(tmp_path / "monitor-empty.db")
    service = _build_service(repository, FakeBillboardAdapter(items))

    result = service.monitor(keyword="数字人")

    assert result.result_state == HOT_POOL_NO_MATCH_STATE
    assert result.user_notice == HOT_POOL_NO_MATCH_NOTICE
    assert repository.list_sampling_checkpoints("数字人") == []


def test_execute_due_recrawls_reruns_due_keyword(tmp_path) -> None:
    start = datetime(2026, 7, 23, 0, tzinfo=timezone.utc)

    class Clock:
        def __init__(self, now: datetime) -> None:
            self.now = now

        def __call__(self) -> datetime:
            return self.now

    clock = Clock(start)
    items = [
        _billboard_item("a1", rank=1, title="AI 数字人", sampled_at=start)
    ]
    repository = SQLiteRepository(tmp_path / "recrawl.db")
    billboard = FakeBillboardAdapter(items)
    service = _build_service(repository, billboard, clock=clock)

    first = service.monitor(keyword="数字人")
    assert first.next_recrawl_at == start + timedelta(hours=2)

    # 快进到 2h 复爬到期，热榜内容更新（点赞增长）
    clock.now = start + timedelta(hours=2, minutes=1)
    billboard.items = [
        _billboard_item("a1", rank=1, title="AI 数字人", digg_count=500, sampled_at=clock.now)
    ]
    results = service.execute_due_recrawls()

    assert len(results) == 1
    assert results[0].result_state == "官方热榜匹配"
    snapshots = repository.list_snapshots("douyin-a1")
    assert len(snapshots) == 2
    assert snapshots[-1].likes == 500
    observed = [
        c
        for c in repository.list_sampling_checkpoints("数字人")
        if c.status == SamplingStatus.OBSERVED
    ]
    assert observed
    # 下一个待复爬点是 6h 档
    assert results[0].next_recrawl_at == start + timedelta(hours=6)


def test_monitor_clamps_billboard_rank_into_match_rank(tmp_path) -> None:
    now = datetime(2026, 7, 23, 10, tzinfo=timezone.utc)
    items = [
        _billboard_item("a9", rank=42, title="AI 数字人深度解析", sampled_at=now)
    ]
    repository = SQLiteRepository(tmp_path / "rank-clamp.db")
    service = _build_service(repository, FakeBillboardAdapter(items))

    result = service.monitor(keyword="数字人")

    match = repository.list_candidate_matches(result.request_id)[0]
    assert match.platform_rank == 10  # CandidateMatch.platform_rank 上限为 10


def test_mock_repository_supports_hot_pool_and_words() -> None:
    now = datetime(2026, 7, 23, 10, tzinfo=timezone.utc)
    items = [_billboard_item("a1", rank=1, title="AI 数字人", sampled_at=now)]
    repository = MockRepository(candidates=[], tasks=[])
    service = _build_service(
        repository,
        FakeBillboardAdapter(items),
        hot_words=FakeHotWordsAdapter(
            [FakeHotWordEntry("AI数字人", 100, now, {})]
        ),
    )
    service.sync_billboard()
    result = service.search_hot_pool(keyword="数字人")
    assert result.result_state == "官方热榜匹配"
    service.sync_hot_words()
    assert [w.word for w in service.hot_word_suggestions()] == ["AI数字人"]
