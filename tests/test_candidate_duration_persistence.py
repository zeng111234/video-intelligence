from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src.models import (
    DataSource,
    NormalizedCandidate,
    Platform,
    ProviderSearchItem,
    VideoMetricSnapshot,
)
from src.repositories import SQLiteRepository
from src.services import HeatService, SourceService


def _provider_item(*, sampled_at: datetime) -> ProviderSearchItem:
    return ProviderSearchItem(
        platform=Platform.DOUYIN,
        platform_item_id="duration-item",
        title="贴标机使用案例",
        author_id="author-duration",
        author_name="测试作者",
        published_at=sampled_at,
        source_url="https://www.douyin.com/video/duration-item",
        provider_rank=1,
        metrics=VideoMetricSnapshot(
            item_id="duration-item",
            sampled_at=sampled_at,
            likes=12,
            confidence=0.6,
        ),
        evidence=(
            "douyin_public_search:关键词=贴标机;来源=browser_rendered;"
            "发布时间=未返回;时长秒=48"
        ),
        data_quality_warnings=["公开搜索页未显示可核验发布时间，已保留但需要人工确认。"],
    )


def _normalized_item(
    provider_item: ProviderSearchItem,
    *,
    duration_seconds: int | None,
    sampled_at: datetime,
    evidence: str | None = None,
) -> NormalizedCandidate:
    return NormalizedCandidate(
        platform_item_id=provider_item.platform_item_id,
        title=provider_item.title,
        author_id=provider_item.author_id,
        author_name=provider_item.author_name,
        platform=provider_item.platform,
        category="关键词/贴标机",
        published_at=provider_item.published_at,
        duration_seconds=duration_seconds,
        source_url=provider_item.source_url,
        source_type=DataSource.PUBLIC_RESEARCH,
        metrics=provider_item.metrics.model_copy(update={"sampled_at": sampled_at}),
        evidence=provider_item.evidence if evidence is None else evidence,
        data_quality_warnings=provider_item.data_quality_warnings,
    )


def test_duration_flows_to_sqlite_and_null_update_keeps_known_value(tmp_path) -> None:
    sampled_at = datetime(2026, 8, 3, 9, tzinfo=timezone.utc)
    provider_item = _provider_item(sampled_at=sampled_at)

    # Legacy browser adapters still encode the visible-card duration in evidence.
    assert provider_item.duration_seconds == 48
    assert provider_item.published_at_reliable is False

    repository = SQLiteRepository(tmp_path / "duration.db")
    service = SourceService(repository, HeatService())
    normalized = _normalized_item(
        provider_item,
        duration_seconds=provider_item.duration_seconds,
        sampled_at=sampled_at,
    )
    candidate = service._to_candidate(normalized, "douyin-duration-item")

    assert candidate.duration_seconds == 48
    assert candidate.published_at_reliable is False
    assert "published_at_reliable" not in candidate.model_dump()

    repository.save_candidate(candidate)
    stored = repository.get_candidate(candidate.video_id)
    assert stored is not None
    assert stored.duration_seconds == 48

    missing_duration = _normalized_item(
        provider_item,
        duration_seconds=None,
        sampled_at=sampled_at + timedelta(minutes=1),
        evidence="douyin_public_search:关键词=贴标机;来源=browser_rendered",
    )
    repository.save_candidate(
        service._to_candidate(missing_duration, "douyin-duration-item")
    )

    restored = repository.get_candidate(candidate.video_id)
    assert restored is not None
    assert restored.duration_seconds == 48


def test_sqlite_keeps_tokenized_xiaohongshu_url_on_later_bare_upsert(tmp_path) -> None:
    sampled_at = datetime(2026, 8, 3, 9, tzinfo=timezone.utc)
    provider_item = _provider_item(sampled_at=sampled_at)
    normalized = _normalized_item(
        provider_item,
        duration_seconds=48,
        sampled_at=sampled_at,
    ).model_copy(
        update={
            "platform": Platform.XIAOHONGSHU,
            "platform_item_id": "xhs-upsert",
            "source_url": (
                "https://www.xiaohongshu.com/explore/xhs-upsert"
                "?xsec_token=live-token&xsec_source=pc_search"
            ),
        }
    )
    service = SourceService(SQLiteRepository(tmp_path / "xhs-url.db"), HeatService())
    candidate = service._to_candidate(normalized, "xiaohongshu-xhs-upsert")
    service.repository.save_candidate(candidate)
    service.repository.save_candidate(
        candidate.model_copy(
            update={
                "title": "后续扫描更新标题",
                "source_url": "https://www.xiaohongshu.com/explore/xhs-upsert",
            }
        )
    )

    saved = service.repository.get_candidate(candidate.video_id)

    assert saved is not None
    assert saved.title == "后续扫描更新标题"
    assert "xsec_token=live-token" in str(saved.source_url)


def test_reliable_published_at_uses_existing_evidence_and_warnings() -> None:
    now = datetime(2026, 8, 3, 9, tzinfo=timezone.utc)
    provider_item = _provider_item(sampled_at=now)
    normalized = _normalized_item(
        provider_item,
        duration_seconds=provider_item.duration_seconds,
        sampled_at=now,
    )
    candidate = SourceService._to_candidate(normalized, "douyin-duration-item")

    assert provider_item.published_at_reliable is False
    assert normalized.published_at_reliable is False
    assert candidate.published_at_reliable is False


def test_managed_sqlite_database_gets_duration_migration(tmp_path) -> None:
    database_path = tmp_path / "managed-existing.db"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE candidates (video_id TEXT PRIMARY KEY)")
    connection.execute("PRAGMA user_version = 6")
    connection.commit()
    connection.close()

    repository = SQLiteRepository(database_path)
    columns = {
        str(row["name"])
        for row in repository.connection.execute("PRAGMA table_info(candidates)")
    }

    assert "duration_seconds" in columns
