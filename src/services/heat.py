from __future__ import annotations

from src.models import HeatLevel, HeatResult, VideoMetricSnapshot


class HeatService:
    """Mock-compatible heat interface; real scoring is intentionally deferred."""

    def analyze(self, metrics: VideoMetricSnapshot) -> HeatResult:
        if metrics.confidence < 0.6:
            return HeatResult(
                score=0,
                level=HeatLevel.INSUFFICIENT,
                confidence=metrics.confidence,
                reasons=["数据置信度低于 0.60，不输出爆火结论"],
            )
        interactions = (metrics.likes or 0) + 3 * (metrics.comments or 0)
        interactions += 4 * (metrics.shares or 0) + 4 * (metrics.favorites or 0)
        score = min(92.0, 55.0 + interactions / 1_000)
        level = (
            HeatLevel.S
            if score >= 85
            else HeatLevel.A
            if score >= 75
            else HeatLevel.B
            if score >= 65
            else HeatLevel.NORMAL
        )
        return HeatResult(
            score=round(score, 1),
            level=level,
            confidence=metrics.confidence,
            reasons=[
                "Mock 规则：按有效互动生成界面演示结果",
                "正式热度公式将在下一纵向切片实现",
            ],
        )
