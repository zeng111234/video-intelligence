from __future__ import annotations

import math
from datetime import datetime, timedelta

from src.models import (
    AnomalyStatus,
    HeatLevel,
    HeatResult,
    MomentumState,
    VideoCandidate,
    VideoMetricSnapshot,
)

MODEL_VERSION = "rule-v1"
METRIC_WEIGHTS = {"likes": 1.0, "comments": 3.0, "shares": 4.0, "favorites": 4.0}
COMPONENT_WEIGHTS = {
    "reach": 0.25,
    "interaction_quality": 0.30,
    "growth": 0.25,
    "account_outperformance": 0.15,
    "freshness": 0.05,
}


def effective_interactions(snapshot: VideoMetricSnapshot) -> float:
    """Return weighted visible interactions without treating unknown values as zero."""
    return sum(
        weight * value
        for field, weight in METRIC_WEIGHTS.items()
        if (value := getattr(snapshot, field)) is not None
    )


def _interaction_quality(snapshot: VideoMetricSnapshot) -> float | None:
    if snapshot.plays is None or snapshot.plays <= 0:
        return None
    visible = [
        (getattr(snapshot, field), weight)
        for field, weight in METRIC_WEIGHTS.items()
        if getattr(snapshot, field) is not None
    ]
    if not visible:
        return None
    return sum(weight * value / snapshot.plays for value, weight in visible) / sum(
        weight for _, weight in visible
    )


def _reach(snapshot: VideoMetricSnapshot) -> float:
    value = snapshot.plays
    return math.log1p(value if value is not None else effective_interactions(snapshot))


def _account_outperformance(snapshot: VideoMetricSnapshot) -> float | None:
    if snapshot.followers is None or snapshot.followers <= 0:
        return None
    numerator: float | None = snapshot.plays
    if numerator is None:
        numerator = effective_interactions(snapshot)
    if numerator is None:
        return None
    return numerator / snapshot.followers


def _growth(snapshots: list[VideoMetricSnapshot]) -> tuple[float | None, float | None]:
    ordered = sorted(snapshots, key=lambda item: item.sampled_at)
    if len(ordered) < 2:
        return None, None
    latest = ordered[-1]
    previous = next(
        (
            item
            for item in reversed(ordered[:-1])
            if latest.sampled_at - item.sampled_at >= timedelta(hours=2)
        ),
        None,
    )
    if previous is None:
        return None, None
    common_fields = [
        field
        for field in METRIC_WEIGHTS
        if getattr(previous, field) is not None and getattr(latest, field) is not None
    ]
    if not common_fields:
        return None, None
    elapsed = (latest.sampled_at - previous.sampled_at).total_seconds() / 3600
    delta = sum(
        METRIC_WEIGHTS[field] * (getattr(latest, field) - getattr(previous, field))
        for field in common_fields
    )
    return delta / elapsed, elapsed


def _percentile(value: float | None, population: list[float | None]) -> float | None:
    if value is None:
        return None
    values = sorted(item for item in population if item is not None)
    if not values:
        return None
    below = sum(item < value for item in values)
    equal = sum(item == value for item in values)
    return round(100 * (below + 0.5 * equal) / len(values), 2)


def follower_band(followers: int | None) -> str:
    if followers is None:
        return "粉丝未知"
    if followers < 10_000:
        return "0-1万"
    if followers < 100_000:
        return "1-10万"
    if followers < 1_000_000:
        return "10-100万"
    return "100万以上"


def video_age_band(published_at: datetime, now: datetime) -> str:
    hours = max(0.0, (now - published_at).total_seconds() / 3600)
    if hours < 6:
        return "0-6h"
    if hours < 24:
        return "6-24h"
    if hours < 72:
        return "1-3d"
    if hours < 168:
        return "3-7d"
    return "7-30d"


class HeatService:
    """Rule-based, explainable heat scoring over a comparable peer bucket."""

    def analyze(
        self,
        metrics: VideoMetricSnapshot,
        *,
        candidate: VideoCandidate | None = None,
        snapshots: list[VideoMetricSnapshot] | None = None,
        peers: list[VideoCandidate] | None = None,
        peer_snapshots: dict[str, list[VideoMetricSnapshot]] | None = None,
        now: datetime | None = None,
    ) -> HeatResult:
        if candidate is None:
            if metrics.confidence < 0.6:
                return HeatResult(
                    score=0,
                    level=HeatLevel.INSUFFICIENT,
                    confidence=metrics.confidence,
                    reasons=["数据置信度低于 0.60，不输出爆火结论"],
                    model_version=MODEL_VERSION,
                )
            return HeatResult(
                score=0,
                level=HeatLevel.INSUFFICIENT,
                confidence=metrics.confidence,
                reasons=["缺少候选上下文与对比桶，无法执行正式热度判定"],
                model_version=MODEL_VERSION,
            )

        current_time = now or datetime.now().astimezone()
        history = sorted(snapshots or [metrics], key=lambda item: item.sampled_at)
        latest = history[-1]
        all_peers = peers or [candidate]
        histories = peer_snapshots or {}
        band = follower_band(latest.followers)
        age_band = video_age_band(candidate.published_at, current_time)
        bucket = [
            peer
            for peer in all_peers
            if peer.platform == candidate.platform
            and peer.category == candidate.category
            and follower_band(peer.metrics.followers) == band
            and video_age_band(peer.published_at, current_time) == age_band
        ]
        if not any(peer.video_id == candidate.video_id for peer in bucket):
            bucket.append(candidate)

        peer_histories = {
            peer.video_id: histories.get(peer.video_id, [peer.metrics])
            for peer in bucket
        }
        reach_p = _percentile(_reach(latest), [_reach(peer.metrics) for peer in bucket])
        quality_p = _percentile(
            _interaction_quality(latest),
            [_interaction_quality(peer.metrics) for peer in bucket],
        )
        growth, growth_hours = _growth(history)
        growth_p = _percentile(
            growth,
            [_growth(peer_histories[peer.video_id])[0] for peer in bucket],
        )
        outperformance_p = _percentile(
            _account_outperformance(latest),
            [_account_outperformance(peer.metrics) for peer in bucket],
        )
        age_hours = max(
            0.0, (current_time - candidate.published_at).total_seconds() / 3600
        )
        freshness = round(max(0.0, 100 * (1 - age_hours / 720)), 2)

        components = {
            "reach": reach_p,
            "interaction_quality": quality_p,
            "growth": growth_p,
            "account_outperformance": outperformance_p,
            "freshness": freshness,
        }
        available_weight = sum(
            COMPONENT_WEIGHTS[name]
            for name, value in components.items()
            if value is not None
        )
        heat_score = (
            sum(
                COMPONENT_WEIGHTS[name] * value
                for name, value in components.items()
                if value is not None
            )
            / available_weight
            if available_weight
            else 0.0
        )
        anomaly_status = AnomalyStatus.NOT_EVALUATED
        anomaly_penalty = 1.0
        score = round(heat_score * anomaly_penalty, 1)
        performance_p = round(
            0.45 * (reach_p or 0) + 0.55 * (quality_p or reach_p or 0), 2
        )

        missing = [
            field
            for field in (
                "plays",
                "likes",
                "comments",
                "shares",
                "favorites",
                "followers",
            )
            if getattr(latest, field) is None
        ]
        confidence = latest.confidence
        if latest.plays is None:
            confidence -= 0.15
        confidence -= 0.05 * sum(
            field in missing for field in ("likes", "comments", "shares", "favorites")
        )
        confidence = round(max(0.0, min(1.0, confidence)), 2)

        sample_size = len(bucket)
        interaction_total = effective_interactions(latest)
        enough_growth = growth_p is not None and growth_hours is not None
        checks: dict[str, bool | None] = {
            "score_gte_85": score >= 85,
            "score_gte_75": score >= 75,
            "score_gte_65": score >= 65,
            "heat_percentile_gte_99": performance_p >= 99,
            "heat_percentile_gte_95": performance_p >= 95,
            "heat_percentile_gte_90": performance_p >= 90,
            "growth_percentile_gte_95": growth_p is not None and growth_p >= 95,
            "growth_percentile_gte_90": growth_p is not None and growth_p >= 90,
            "growth_percentile_gte_80": growth_p is not None and growth_p >= 80,
            "ei_gte_1000": interaction_total >= 1000,
            "ei_gte_500": interaction_total >= 500,
            "two_hour_snapshots": enough_growth,
            "age_lte_24h": age_hours <= 24,
        }

        level = HeatLevel.NORMAL
        momentum = MomentumState.NORMAL
        reasons: list[str] = []
        if confidence < 0.6:
            level = HeatLevel.INSUFFICIENT
            momentum = MomentumState.INSUFFICIENT
            reasons.append("数据置信度低于 0.60，不输出爆火结论")
        elif sample_size < 30:
            reasons.append(f"对比桶仅 {sample_size} 条，当前只提供描述性排序")
        elif not enough_growth and performance_p >= 95:
            level = HeatLevel.STATIC_HIGH
            momentum = MomentumState.STATIC_HIGH
            reasons.append("单次或间隔不足两小时的快照达到 P95，仅标记静态高热")
        elif sample_size >= 100 and all(
            checks[name]
            for name in (
                "score_gte_85",
                "heat_percentile_gte_99",
                "growth_percentile_gte_95",
                "ei_gte_1000",
                "two_hour_snapshots",
            )
        ):
            level = HeatLevel.S
            momentum = MomentumState.VIRAL
        elif sample_size >= 100 and all(
            checks[name]
            for name in (
                "score_gte_75",
                "heat_percentile_gte_95",
                "growth_percentile_gte_90",
                "ei_gte_500",
                "two_hour_snapshots",
            )
        ):
            level = HeatLevel.A
            momentum = MomentumState.VIRAL
        elif all(
            checks[name]
            for name in (
                "score_gte_65",
                "heat_percentile_gte_90",
                "growth_percentile_gte_80",
                "age_lte_24h",
            )
        ):
            level = HeatLevel.B
            momentum = MomentumState.POTENTIAL

        if reach_p is not None:
            reasons.append(f"同桶触达分位 P{reach_p:.0f}")
        if quality_p is not None:
            reasons.append(f"同桶互动质量分位 P{quality_p:.0f}")
        if growth_p is not None:
            reasons.append(f"{growth_hours:.1f} 小时有效互动增长分位 P{growth_p:.0f}")
        else:
            reasons.append("缺少间隔至少 2 小时的可比快照，增长分不可用")
        if missing:
            reasons.append(f"缺失 {', '.join(missing)}，按可见字段重归一化")
        if candidate.official_hot:
            reasons.append("进入抖音官方热门榜；该标签独立于模型等级")

        next_sample_at = self.next_sample_at(history)
        return HeatResult(
            score=score,
            level=level,
            confidence=confidence,
            provisional=sample_size < 500 or confidence < 0.8,
            reasons=reasons,
            model_version=MODEL_VERSION,
            component_scores=components,
            percentiles={
                "heat": performance_p,
                "reach": reach_p,
                "interaction_quality": quality_p,
                "growth": growth_p,
                "account_outperformance": outperformance_p,
            },
            bucket_definition=f"{candidate.platform.value}/{candidate.category}/{band}/{age_band}",
            bucket_sample_size=sample_size,
            snapshot_count=len(history),
            growth_window_hours=growth_hours,
            missing_fields=missing,
            unavailable_components=[
                name for name, value in components.items() if value is None
            ],
            threshold_checks=checks,
            official_hot=candidate.official_hot,
            official_rank=candidate.official_rank,
            official_hot_value=candidate.official_hot_value,
            momentum_state=momentum,
            anomaly_status=anomaly_status,
            next_sample_at=next_sample_at,
        )

    @staticmethod
    def next_sample_at(snapshots: list[VideoMetricSnapshot]) -> datetime | None:
        if not snapshots:
            return None
        first = min(item.sampled_at for item in snapshots)
        latest = max(item.sampled_at for item in snapshots)
        for hour in (2, 6, 24):
            target = first + timedelta(hours=hour)
            if latest < target:
                return target
        return None
