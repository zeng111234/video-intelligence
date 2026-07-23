from __future__ import annotations

from datetime import datetime, timedelta

from src.contracts import CandidateRepository
from src.models import HeatLevel, Platform, VideoCandidate


def _strip_tz(dt: datetime) -> datetime:
    """Strip timezone info to produce a naive datetime for safe comparison."""
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


class CandidateService:
    def __init__(self, repository: CandidateRepository) -> None:
        self.repository = repository

    def search(
        self,
        *,
        query: str = "",
        platforms: list[Platform] | None = None,
        category: str | None = None,
        published_within_hours: int = 168,
        min_interactions: int = 0,
        levels: list[HeatLevel] | None = None,
    ) -> list[VideoCandidate]:
        items = self.repository.list_candidates()
        # Use naive datetime to avoid TypeError when comparing with
        # timezone-naive datetimes stored in SQLite.
        cutoff = _strip_tz(datetime.now() - timedelta(hours=published_within_hours))
        normalized_query = query.strip().casefold()

        def matches(candidate: VideoCandidate) -> bool:
            metrics = candidate.metrics
            interactions = sum(
                value or 0
                for value in (
                    metrics.likes,
                    metrics.comments,
                    metrics.shares,
                    metrics.favorites,
                )
            )
            pub_at = _strip_tz(candidate.published_at)
            return (
                (
                    not normalized_query
                    or normalized_query in candidate.title.casefold()
                    or normalized_query in candidate.category.casefold()
                    or any(
                        normalized_query in value.casefold()
                        for value in candidate.matched_by
                    )
                )
                and (not platforms or candidate.platform in platforms)
                and (
                    not category
                    or category == "全部赛道"
                    or candidate.category == category
                )
                and pub_at >= cutoff
                and interactions >= min_interactions
                and (not levels or candidate.heat.level in levels)
            )

        return sorted(
            (item for item in items if matches(item)),
            key=lambda item: item.heat.score,
            reverse=True,
        )

    def get(self, video_id: str | None) -> VideoCandidate | None:
        return self.repository.get_candidate(video_id) if video_id else None
