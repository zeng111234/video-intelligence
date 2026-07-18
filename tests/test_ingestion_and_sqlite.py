from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pandas as pd
import pytest

from src.adapters import (
    DouyinHotBillboardAdapter,
    ManualImportAdapter,
    PublicMetadataResearchAdapter,
)
from src.adapters.official import OfficialAdapterDisabledError
from src.models import (
    DataSource,
    HeatLevel,
    HeatResult,
    Platform,
    RelevanceReview,
    ReviewStatus,
    SourceRequest,
    VideoCandidate,
    VideoMetricSnapshot,
)
from src.repositories import SQLiteRepository
from src.services import HeatService, SourceService


def test_csv_import_is_idempotent_and_appends_snapshots(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "test.db")
    service = SourceService(repository, HeatService())
    first_time = datetime(2026, 7, 17, 8, tzinfo=timezone.utc)
    header = (
        "platform_item_id,title,author_name,published_at,source_url,sampled_at,"
        "plays,likes,comments,shares,favorites,followers,confidence\n"
    )
    first = header + (
        "12345678901,AI数字人获客案例,示例企业,2026-07-17T06:00:00+00:00,"
        f"https://www.douyin.com/video/12345678901,{first_time.isoformat()},"
        "1000,100,10,,20,5000,0.7\n"
    )
    request = SourceRequest(source=DataSource.CSV, keywords=["数字人", "获客"])
    page = ManualImportAdapter("input.csv", first.encode()).sync(request)
    report = service.import_page(page)

    assert not page.errors
    assert report.added_candidates == 1
    candidate = repository.list_candidates()[0]
    assert candidate.metrics.shares is None
    assert candidate.matched_by == ["数字人", "获客"]

    second = first.replace(
        first_time.isoformat(), (first_time + timedelta(hours=2)).isoformat()
    ).replace("1000,100,10,,20", "1600,180,18,,35")
    report = service.import_page(
        ManualImportAdapter("input.csv", second.encode()).sync(request)
    )

    assert report.updated_candidates == 1
    assert report.added_snapshots == 1
    assert len(repository.list_candidates()) == 1
    snapshots = repository.list_snapshots(candidate.video_id)
    assert len(snapshots) == 2
    assert snapshots[-1].shares is None


def test_csv_import_reports_specific_missing_columns() -> None:
    page = ManualImportAdapter("bad.csv", b"title\nmissing ids\n").sync(
        SourceRequest(source=DataSource.CSV)
    )

    fields = {error.field for error in page.errors}
    assert {"author_name", "published_at"} <= fields
    assert not page.items


def test_csv_import_accepts_three_platforms_and_preserves_trace_and_nulls() -> None:
    content = (
        "platform,platform_item_id,title,author_name,published_at,source_url,"
        "feed_id,finder_user_name,likes,shares,evidence\n"
        "抖音,dy-1,抖音候选,作者甲,2026-07-17T08:00:00+08:00,"
        "https://www.douyin.com/video/dy-1,,,12,,\n"
        "小红书,xhs-1,小红书候选,作者乙,2026-07-17T08:00:00+08:00,"
        "https://www.xiaohongshu.com/explore/xhs-1,,,8,,截图-1\n"
        "微信视频号,,视频号候选,作者丙,2026-07-17T08:00:00+08:00,,"
        "feed-1,finder-user,,,\n"
    )

    page = ManualImportAdapter("three.csv", content.encode("utf-8")).sync(
        SourceRequest(source=DataSource.CSV)
    )

    assert not page.errors
    assert [item.platform.value for item in page.items] == [
        "douyin",
        "xiaohongshu",
        "wechat_channels",
    ]
    wechat = page.items[2]
    assert wechat.platform_item_id == "feed-1"
    assert wechat.source_url is None
    assert wechat.feed_id == "feed-1"
    assert wechat.finder_user_name == "finder-user"
    assert wechat.metrics.likes is None
    assert wechat.metrics.shares is None
    assert wechat.evidence is None


def test_csv_import_reports_domain_error_by_row_and_field() -> None:
    content = (
        "platform,platform_item_id,title,author_name,published_at,source_url\n"
        "小红书,xhs-1,候选,作者,2026-07-17T08:00:00+08:00,"
        "https://www.douyin.com/video/1\n"
    )

    page = ManualImportAdapter("bad-domain.csv", content.encode("utf-8")).sync(
        SourceRequest(source=DataSource.CSV)
    )

    assert not page.items
    assert page.errors[0].row == 2
    assert page.errors[0].field == "source_url"
    assert "小红书" in page.errors[0].message


def test_csv_naive_datetimes_become_timezone_aware() -> None:
    content = (
        "platform_item_id,title,author_name,published_at,source_url\n"
        "1,数字人口播获客,企业,2026-07-17 08:00:00,"
        "https://www.douyin.com/video/12345678901\n"
    )
    page = ManualImportAdapter("naive.csv", content.encode()).sync(
        SourceRequest(source=DataSource.CSV)
    )

    assert not page.errors
    assert page.items[0].published_at.tzinfo is not None
    assert page.items[0].metrics.sampled_at.tzinfo is not None


@pytest.mark.filterwarnings("ignore:datetime.datetime.utcnow.*:DeprecationWarning")
def test_excel_import_uses_same_normalized_contract() -> None:
    buffer = BytesIO()
    pd.DataFrame(
        [
            {
                "作品ID": "excel-1",
                "标题": "数字人营销获客",
                "作者": "测试企业",
                "平台": "抖音",
                "发布时间": "2026-07-17T08:00:00+08:00",
                "链接": "https://www.douyin.com/video/12345678901",
                "点赞": 88,
            }
        ]
    ).to_excel(buffer, index=False)

    page = ManualImportAdapter("input.xlsx", buffer.getvalue()).sync(
        SourceRequest(source=DataSource.CSV, keywords=["数字人", "获客"])
    )

    assert not page.errors
    assert page.items[0].platform.value == "douyin"
    assert page.items[0].metrics.likes == 88


def test_manual_link_and_metrics_build_normalized_page() -> None:
    now = datetime.now(timezone.utc)
    page = ManualImportAdapter.from_manual(
        platform_item_id="manual-1",
        title="数字人口播帮助企业获客",
        author_name="测试企业",
        published_at=now - timedelta(hours=1),
        source_url="https://www.douyin.com/video/12345678901",
        sampled_at=now,
        likes=200,
        shares=None,
    )

    assert not page.errors
    assert page.items[0].source_type == DataSource.MANUAL
    assert page.items[0].metrics.shares is None
    assert {"数字人口播", "企业服务"} & set(page.items[0].matched_by)


def test_wechat_trace_fields_round_trip_without_fake_url(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "wechat.db")
    service = SourceService(repository, HeatService())
    now = datetime.now(timezone.utc)
    page = ManualImportAdapter.from_manual(
        platform=Platform.WECHAT_CHANNELS,
        platform_item_id="",
        title="视频号数字人口播",
        author_name="测试企业",
        published_at=now,
        source_url=None,
        sampled_at=now,
        feed_id="feed-1",
        finder_user_name="finder-user",
    )

    assert not page.errors
    service.import_page(page)
    candidate = repository.list_candidates()[0]
    assert candidate.platform == Platform.WECHAT_CHANNELS
    assert candidate.source_url is None
    assert candidate.feed_id == "feed-1"
    assert candidate.finder_user_name == "finder-user"


def test_sqlite_review_round_trip(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "review.db")
    candidate = _candidate("review-item", plays=1000, likes=100)
    repository.save_candidate(candidate)
    review = RelevanceReview(
        candidate_id=candidate.video_id,
        is_digital_human=True,
        is_target_vertical=True,
        has_marketing_cta=True,
        status=ReviewStatus.APPROVED,
        reviewer="tester",
        reviewed_at=datetime.now(timezone.utc),
    )
    repository.save_review(review)

    assert repository.get_review(candidate.video_id) == review


def test_official_adapter_is_disabled_without_permission() -> None:
    try:
        DouyinHotBillboardAdapter().sync(SourceRequest(source=DataSource.OFFICIAL))
    except OfficialAdapterDisabledError as exc:
        assert "尚未配置" in str(exc)
    else:
        raise AssertionError("official adapter must stay disabled without credentials")


def test_public_metadata_adapter_rejects_non_douyin_url_without_network() -> None:
    page = PublicMetadataResearchAdapter().sync(
        SourceRequest(
            source=DataSource.PUBLIC_RESEARCH,
            urls=["https://example.com/video/123"],
        )
    )

    assert not page.items
    assert "只接受抖音" in page.errors[0].message


def test_heat_model_can_emit_provisional_s_with_enough_peers_and_growth() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    peers = [
        _candidate(
            f"peer-{index}",
            plays=10_000 + index,
            likes=500 + index,
            sampled_at=now,
            published_at=now - timedelta(hours=4),
        )
        for index in range(99)
    ]
    target = _candidate(
        "target",
        plays=1_000_000,
        likes=100_000,
        sampled_at=now,
        published_at=now - timedelta(hours=4),
    )
    peers.append(target)
    histories = {
        peer.video_id: [
            peer.metrics.model_copy(
                update={
                    "sampled_at": now - timedelta(hours=2),
                    "plays": max(0, (peer.metrics.plays or 0) - 100),
                    "likes": max(0, (peer.metrics.likes or 0) - 5),
                }
            ),
            peer.metrics,
        ]
        for peer in peers
    }
    histories[target.video_id][0] = target.metrics.model_copy(
        update={
            "sampled_at": now - timedelta(hours=2),
            "plays": 100_000,
            "likes": 1_000,
        }
    )

    result = HeatService().analyze(
        target.metrics,
        candidate=target,
        snapshots=histories[target.video_id],
        peers=peers,
        peer_snapshots=histories,
        now=now,
    )

    assert result.level == HeatLevel.S
    assert result.provisional is True
    assert result.snapshot_count == 2
    assert result.percentiles["growth"] is not None


def _candidate(
    video_id: str,
    *,
    plays: int,
    likes: int,
    sampled_at: datetime | None = None,
    published_at: datetime | None = None,
) -> VideoCandidate:
    sampled_at = sampled_at or datetime.now(timezone.utc)
    published_at = published_at or sampled_at - timedelta(hours=1)
    metrics = VideoMetricSnapshot(
        item_id=video_id,
        sampled_at=sampled_at,
        plays=plays,
        likes=likes,
        comments=max(1, likes // 20),
        shares=max(1, likes // 10),
        favorites=max(1, likes // 8),
        followers=50_000,
        confidence=1.0,
    )
    return VideoCandidate(
        video_id=video_id,
        platform_item_id=video_id,
        title=f"数字人获客 {video_id}",
        author_id=f"author-{video_id}",
        author_name="测试作者",
        platform="douyin",
        category="B2B/AI企业服务获客数字人口播",
        published_at=published_at,
        source_url=f"https://www.douyin.com/video/{video_id}",
        source_type=DataSource.CSV,
        metrics=metrics,
        heat=HeatResult(
            score=0,
            level=HeatLevel.INSUFFICIENT,
            confidence=1.0,
        ),
    )
