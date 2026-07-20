"""深度分析聚合 API —— 基于 SQLite 中的候选、快照和任务记录。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from project.backend.app.core.deps import get_repository

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


class AnalyticsOverview(BaseModel):
    totalViews: int = 0
    totalWatchHours: float = 0.0
    engagementRate: float = 0.0
    shareCount: int = 0


class AnalyticsTrendItem(BaseModel):
    topic: str
    views: str
    growth: float | None = None
    hot: str


class AnalyticsCompetitorItem(BaseModel):
    name: str
    fans: str
    avgViews: str
    engagement: float


class AnalyticsResponse(BaseModel):
    overview: AnalyticsOverview
    trends: list[AnalyticsTrendItem] = Field(default_factory=list)
    competitors: list[AnalyticsCompetitorItem] = Field(default_factory=list)
    contentDistribution: list[dict[str, Any]] = Field(default_factory=list)


def _cutoff(time_range: str) -> datetime:
    now = datetime.now().astimezone()
    if time_range == "24h":
        return now - timedelta(hours=24)
    if time_range == "30d":
        return now - timedelta(days=30)
    if time_range == "90d":
        return now - timedelta(days=90)
    return now - timedelta(days=7)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return value.astimezone()


def _latest_snapshot(repo, candidate):
    snapshots = repo.list_snapshots(candidate.video_id)
    if snapshots:
        return snapshots[-1]
    return candidate.metrics


def _like_growth(repo, candidate) -> float | None:
    snapshots = repo.list_snapshots(candidate.video_id)
    visible = [item for item in snapshots if item.likes is not None]
    if len(visible) < 2:
        return None
    previous = visible[0]
    latest = visible[-1]
    elapsed_hours = (
        _normalize_datetime(latest.sampled_at)
        - _normalize_datetime(previous.sampled_at)
    ).total_seconds() / 3600
    if elapsed_hours <= 0:
        return None
    return round(((latest.likes or 0) - (previous.likes or 0)) / elapsed_hours, 1)


def _hot_label(candidate, growth: float | None) -> str:
    if growth is None:
        return "观察中"
    if growth >= 100:
        return "飙升"
    if growth > 0:
        return "上升"
    if candidate.heat.score > 0:
        return "平稳"
    return "暂无依据"


@router.get("/summary", response_model=AnalyticsResponse)
def get_analytics_summary(
    time_range: str = "7d",
    keyword: str = "",
    repo=Depends(get_repository),
) -> AnalyticsResponse:
    """返回稳定的后端聚合数据；不生成随机增长或固定竞品数据。"""
    cutoff = _cutoff(time_range)
    normalized_keyword = keyword.strip().casefold()
    candidates = []
    for candidate in repo.list_candidates():
        if _normalize_datetime(candidate.published_at) < cutoff:
            continue
        if normalized_keyword and normalized_keyword not in (
            f"{candidate.title} {candidate.category} {' '.join(candidate.matched_by)}"
        ).casefold():
            continue
        candidates.append(candidate)

    latest_by_id = {
        candidate.video_id: _latest_snapshot(repo, candidate)
        for candidate in candidates
    }
    total_views = sum((snapshot.plays or 0) for snapshot in latest_by_id.values())
    total_likes = sum((snapshot.likes or 0) for snapshot in latest_by_id.values())
    total_comments = sum((snapshot.comments or 0) for snapshot in latest_by_id.values())
    total_shares = sum((snapshot.shares or 0) for snapshot in latest_by_id.values())
    total_favorites = sum((snapshot.favorites or 0) for snapshot in latest_by_id.values())
    total_interactions = total_likes + total_comments + total_shares + total_favorites
    engagement_rate = (
        round(total_interactions / total_views * 100, 1) if total_views > 0 else 0.0
    )

    ordered = sorted(
        candidates,
        key=lambda item: (
            latest_by_id[item.video_id].plays or 0,
            item.heat.score,
        ),
        reverse=True,
    )
    trends = [
        AnalyticsTrendItem(
            topic=item.title[:20],
            views=str(latest_by_id[item.video_id].plays or 0),
            growth=(growth := _like_growth(repo, item)),
            hot=_hot_label(item, growth),
        )
        for item in ordered[:5]
    ]

    category_counts: dict[str, int] = {}
    for item in candidates:
        category_counts[item.category or "未分类"] = (
            category_counts.get(item.category or "未分类", 0) + 1
        )
    content_distribution = [
        {
            "label": label,
            "percent": round(count / len(candidates) * 100),
        }
        for label, count in sorted(
            category_counts.items(), key=lambda pair: pair[1], reverse=True
        )
    ] if candidates else []

    return AnalyticsResponse(
        overview=AnalyticsOverview(
            totalViews=total_views,
            totalWatchHours=0.0,
            engagementRate=engagement_rate,
            shareCount=total_shares,
        ),
        trends=trends,
        competitors=[],
        contentDistribution=content_distribution,
    )
