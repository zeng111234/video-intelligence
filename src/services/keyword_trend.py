from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta

from src.contracts import CandidateRepository
from src.models import (
    AnomalyStatus,
    CandidateMatch,
    KeywordTrendLevel,
    KeywordTrendResult,
    Platform,
    VideoCandidate,
    VideoMetricSnapshot,
)

MODEL_VERSION = "keyword-trend-v1"
WINDOW_DAYS = 7
COMPONENT_WEIGHTS = {
    "age_adjusted_engagement": 0.35,
    "platform_rank": 0.25,
    "freshness": 0.20,
    "growth_or_persistence": 0.20,
}


def _percentile(value: float | None, values: list[float | None]) -> float | None:
    if value is None:
        return None
    population = sorted(item for item in values if item is not None)
    if not population:
        return None
    below = sum(item < value for item in population)
    equal = sum(item == value for item in population)
    return round(100 * (below + 0.5 * equal) / len(population), 2)


def _visible_likes(
    snapshots: list[VideoMetricSnapshot], since: datetime, now: datetime
) -> list[VideoMetricSnapshot]:
    return sorted(
        (
            item
            for item in snapshots
            if item.likes is not None and since <= item.sampled_at <= now
        ),
        key=lambda item: item.sampled_at,
    )


def _growth(
    snapshots: list[VideoMetricSnapshot], since: datetime, now: datetime
) -> tuple[float | None, float | None]:
    visible = _visible_likes(snapshots, since, now)
    if len(visible) < 2:
        return None, None
    latest = visible[-1]
    previous = next(
        (
            item
            for item in reversed(visible[:-1])
            if latest.sampled_at - item.sampled_at >= timedelta(hours=2)
        ),
        None,
    )
    if previous is None:
        return None, None
    hours = (latest.sampled_at - previous.sampled_at).total_seconds() / 3600
    return ((latest.likes or 0) - (previous.likes or 0)) / hours, hours


def _interval_rates(snapshots: list[VideoMetricSnapshot]) -> list[float]:
    visible = sorted(
        (item for item in snapshots if item.likes is not None),
        key=lambda item: item.sampled_at,
    )
    rates: list[float] = []
    for previous, current in zip(visible, visible[1:], strict=False):
        hours = (current.sampled_at - previous.sampled_at).total_seconds() / 3600
        if hours > 0:
            rates.append(((current.likes or 0) - (previous.likes or 0)) / hours)
    return rates


def _rank_score(matches: list[CandidateMatch]) -> float:
    ordered = sorted(matches, key=lambda item: item.observed_at)
    weights = range(1, len(ordered) + 1)
    weighted = sum(
        ((10 - match.platform_rank) / 9 * 100) * weight
        for match, weight in zip(ordered, weights, strict=False)
    )
    return round(weighted / sum(weights), 2)


class KeywordTrendService:
    """Compute a low-call, keyword-specific rolling trend leaderboard."""

    def __init__(self, repository: CandidateRepository) -> None:
        self.repository = repository

    def recompute(
        self,
        keyword: str,
        *,
        platform: Platform = Platform.DOUYIN,
        provider_name: str | None = None,
        now: datetime | None = None,
    ) -> list[KeywordTrendResult]:
        keyword_key = keyword.strip().casefold()
        if not keyword_key:
            raise ValueError("请输入要计算的关键词。")
        computed_at = now or datetime.now().astimezone()
        since = computed_at - timedelta(days=WINDOW_DAYS)
        matches = self.repository.list_keyword_matches(
            keyword_key, since, platform, provider_name
        )
        if matches:
            latest_scope = max(matches, key=lambda item: item.observed_at)
            matches = [
                match
                for match in matches
                if match.publish_time == latest_scope.publish_time
                and match.sort_type == latest_scope.sort_type
            ]
        grouped: dict[str, list[CandidateMatch]] = defaultdict(list)
        for match in matches:
            grouped[match.video_id].append(match)

        candidates = {
            candidate.video_id: candidate
            for candidate in self.repository.list_candidates()
            if candidate.video_id in grouped and candidate.platform == platform
        }
        if not candidates:
            self.repository.clear_keyword_trend_results(keyword_key, platform)
            return []

        pool_size = len(candidates)
        histories = {
            candidate_id: self.repository.list_snapshots(candidate_id)
            for candidate_id in candidates
        }
        growth_data = {
            candidate_id: _growth(history, since, computed_at)
            for candidate_id, history in histories.items()
        }
        growth_eligible_size = sum(
            value[0] is not None for value in growth_data.values()
        )
        growth_values = [value[0] for value in growth_data.values()]
        positive_growth_median = statistics.median(
            [value for value in growth_values if value is not None and value > 0]
            or [0.0]
        )
        age_like_values = {
            candidate_id: self._likes_per_hour(
                candidate, histories[candidate_id], computed_at
            )
            for candidate_id, candidate in candidates.items()
        }
        age_engagement_values = {
            candidate_id: self._engagement_per_hour(
                candidate, histories[candidate_id], computed_at
            )
            for candidate_id, candidate in candidates.items()
        }
        latest_likes = {
            candidate_id: self._latest_likes(histories[candidate_id])
            for candidate_id in candidates
        }

        results: list[KeywordTrendResult] = []
        for candidate_id, candidate in candidates.items():
            candidate_matches = grouped[candidate_id]
            history = histories[candidate_id]
            growth, growth_hours = growth_data[candidate_id]
            age_likes = age_like_values[candidate_id]
            age_engagement = age_engagement_values[candidate_id]
            rank_score = _rank_score(candidate_matches)
            appearance_count = len(
                {
                    int(item.observed_at.timestamp() // (2 * 3600))
                    for item in candidate_matches
                }
            )
            age_hours = max(
                0.0,
                (computed_at - candidate.published_at).total_seconds() / 3600,
            )
            freshness = round(max(0.0, 100 * (1 - age_hours / 168)), 2)
            persistence = round(min(100.0, appearance_count / 3 * 100), 2)
            growth_percentile = _percentile(growth, growth_values)
            age_like_percentile = _percentile(age_likes, list(age_like_values.values()))
            age_engagement_percentile = _percentile(
                age_engagement, list(age_engagement_values.values())
            )
            likes_percentile = _percentile(
                latest_likes[candidate_id], list(latest_likes.values())
            )
            components = {
                "age_adjusted_engagement": age_engagement_percentile,
                "platform_rank": rank_score,
                "freshness": freshness,
                "growth_or_persistence": (
                    growth_percentile if growth_percentile is not None else persistence
                ),
            }
            available_weight = sum(
                COMPONENT_WEIGHTS[name]
                for name, value in components.items()
                if value is not None
            )
            raw_score = (
                sum(
                    COMPONENT_WEIGHTS[name] * value
                    for name, value in components.items()
                    if value is not None
                )
                / available_weight
                if available_weight
                else 0.0
            )

            anomaly_reasons = self._anomaly_reasons(
                history=history,
                likes_percentile=likes_percentile,
                consecutive_low_rank=self._last_two_ranks_are_low(candidate_matches),
                positive_growth_median=positive_growth_median,
                since=since,
                now=computed_at,
            )
            anomaly_penalty = 0.75 if anomaly_reasons else 1.0
            score = round(raw_score * anomaly_penalty, 1)
            confidence = self._confidence(
                history=history,
                since=since,
                now=computed_at,
                growth_available=growth is not None,
                appearance_count=appearance_count,
                pool_size=pool_size,
                source_confidence=self._source_confidence(history),
                growth_eligible_size=growth_eligible_size,
            )
            level = self._level(
                score=score,
                confidence=confidence,
                pool_size=pool_size,
                growth_percentile=growth_percentile,
                appearance_count=appearance_count,
                age_hours=age_hours,
                anomaly_suspected=bool(anomaly_reasons),
            )
            latest_match = max(candidate_matches, key=lambda item: item.observed_at)
            reasons = [
                f"本次平台搜索排第 {latest_match.platform_rank} 名（共 {pool_size} 条）",
            ]
            if age_engagement_percentile is not None:
                leading_percent = max(1, round(100 - age_engagement_percentile))
                reasons.append(
                    f"按发布时间折算的互动速度位于本平台候选前 {leading_percent}%"
                )
            reasons.append(f"近 7 天有效入榜 {appearance_count} 次")
            if growth is None:
                reasons.append(
                    "首次观测：暂无真实增长证据；增长组件使用入榜持续性替代分"
                )
            else:
                reasons.append(
                    f"{growth_hours:.1f} 小时点赞增长 {growth:.1f}/小时，分位 P{growth_percentile:.0f}"
                )
            if pool_size < 30:
                reasons.append(
                    f"当前仅有 {pool_size} 条同平台样本，暂不输出正式爆火等级"
                )
            checkpoints = [
                checkpoint
                for checkpoint in self.repository.list_sampling_checkpoints(keyword_key)
                if checkpoint.candidate_id == candidate_id
                and checkpoint.platform == platform
                and (provider_name is None or checkpoint.provider_name == provider_name)
            ]
            missed_count = sum(
                checkpoint.status.value == "missed" for checkpoint in checkpoints
            )
            if missed_count:
                reasons.append(
                    f"有 {missed_count} 个计划复采点未再次召回，增长判断受限"
                )
            reasons.extend(anomaly_reasons)
            results.append(
                KeywordTrendResult(
                    keyword=keyword_key,
                    platform=platform,
                    candidate_id=candidate_id,
                    computed_at=computed_at,
                    score=score,
                    level=level,
                    confidence=confidence,
                    platform_rank=latest_match.platform_rank,
                    likes_per_hour=age_likes,
                    engagement_per_hour=age_engagement,
                    like_growth_per_hour=growth,
                    appearance_count=appearance_count,
                    pool_size=pool_size,
                    component_scores={
                        **components,
                        "age_adjusted_likes": age_like_percentile,
                    },
                    percentiles={
                        "growth": growth_percentile,
                        "age_adjusted_likes": age_like_percentile,
                        "age_adjusted_engagement": age_engagement_percentile,
                        "likes": likes_percentile,
                    },
                    anomaly_status=(
                        AnomalyStatus.SUSPECTED
                        if anomaly_reasons
                        else AnomalyStatus.NORMAL
                    ),
                    anomaly_penalty=anomaly_penalty,
                    reasons=reasons,
                    model_version=MODEL_VERSION,
                )
            )

        ordered = sorted(results, key=lambda item: (-item.score, item.platform_rank))
        self.repository.save_keyword_trend_results(ordered)
        return ordered[:10]

    @staticmethod
    def _latest_likes(snapshots: list[VideoMetricSnapshot]) -> float | None:
        visible = sorted(
            (item for item in snapshots if item.likes is not None),
            key=lambda item: item.sampled_at,
        )
        if not visible:
            return None
        latest_likes = visible[-1].likes
        return float(latest_likes) if latest_likes is not None else None

    @classmethod
    def _likes_per_hour(
        cls,
        candidate: VideoCandidate,
        snapshots: list[VideoMetricSnapshot],
        now: datetime,
    ) -> float | None:
        likes = cls._latest_likes(snapshots)
        if likes is None:
            return None
        age_hours = max(1.0, (now - candidate.published_at).total_seconds() / 3600)
        return round(likes / age_hours, 4)

    @staticmethod
    def _engagement_per_hour(
        candidate: VideoCandidate,
        snapshots: list[VideoMetricSnapshot],
        now: datetime,
    ) -> float | None:
        if not snapshots:
            return None
        latest = max(snapshots, key=lambda item: item.sampled_at)
        visible = [
            latest.likes,
            latest.comments,
            latest.shares,
            latest.favorites,
        ]
        if all(value is None for value in visible):
            return None
        engagement = (
            (latest.likes or 0)
            + 3 * (latest.comments or 0)
            + 4 * (latest.shares or 0)
            + 4 * (latest.favorites or 0)
        )
        age_hours = max(1.0, (now - candidate.published_at).total_seconds() / 3600)
        return round(engagement / age_hours, 4)

    @staticmethod
    def _confidence(
        *,
        history: list[VideoMetricSnapshot],
        since: datetime,
        now: datetime,
        growth_available: bool,
        appearance_count: int,
        pool_size: int,
        source_confidence: float,
        growth_eligible_size: int,
    ) -> float:
        visible = _visible_likes(history, since, now)
        confidence = 0.20 + 0.25 * source_confidence
        if growth_available:
            confidence += 0.15
        if len(visible) >= 2 and visible[-1].sampled_at - visible[
            0
        ].sampled_at >= timedelta(hours=6):
            confidence += 0.10
        if appearance_count >= 2:
            confidence += 0.10
        if pool_size >= 30:
            confidence += 0.10
        if pool_size >= 100:
            confidence += 0.05
        confidence += 0.05 * min(1.0, growth_eligible_size / max(pool_size, 1))
        return round(min(1.0, confidence), 2)

    @staticmethod
    def _source_confidence(history: list[VideoMetricSnapshot]) -> float:
        visible = [item.confidence for item in history if item.likes is not None]
        return sum(visible) / len(visible) if visible else 0.0

    @staticmethod
    def _level(
        *,
        score: float,
        confidence: float,
        pool_size: int,
        growth_percentile: float | None,
        appearance_count: int,
        age_hours: float,
        anomaly_suspected: bool = False,
    ) -> KeywordTrendLevel:
        if (
            anomaly_suspected
            or pool_size < 30
            or confidence < 0.60
            or growth_percentile is None
        ):
            return KeywordTrendLevel.OBSERVING
        if (
            pool_size >= 100
            and score >= 85
            and growth_percentile >= 95
            and appearance_count >= 3
            and confidence >= 0.80
        ):
            return KeywordTrendLevel.S
        if (
            pool_size >= 100
            and score >= 75
            and growth_percentile >= 90
            and appearance_count >= 2
            and confidence >= 0.70
        ):
            return KeywordTrendLevel.A
        if (
            score >= 65
            and growth_percentile >= 80
            and age_hours <= 24
            and confidence >= 0.60
        ):
            return KeywordTrendLevel.B
        return KeywordTrendLevel.NORMAL

    @staticmethod
    def _anomaly_reasons(
        *,
        history: list[VideoMetricSnapshot],
        likes_percentile: float | None,
        consecutive_low_rank: bool,
        positive_growth_median: float,
        since: datetime,
        now: datetime,
    ) -> list[str]:
        reasons: list[str] = []
        if (
            likes_percentile is not None
            and likes_percentile >= 90
            and consecutive_low_rank
        ):
            reasons.append("点赞分位很高但平台综合名次持续靠后，疑似结构异常")
        rates = _interval_rates(_visible_likes(history, since, now))
        if any(rate < 0 for rate in rates):
            reasons.append("点赞计数出现倒退，标记增长轨迹异常")
        if (
            len(rates) >= 2
            and positive_growth_median > 0
            and rates[-2] >= positive_growth_median * 5
            and 0 <= rates[-1] <= rates[-2] * 0.2
        ):
            reasons.append("点赞曾异常暴增后快速失速，标记待核验")
        return reasons

    @staticmethod
    def _last_two_ranks_are_low(matches: list[CandidateMatch]) -> bool:
        ordered = sorted(matches, key=lambda item: item.observed_at)
        if len(ordered) < 2:
            return False
        return all(
            ((10 - match.platform_rank) / 9 * 100) <= 30 for match in ordered[-2:]
        )
