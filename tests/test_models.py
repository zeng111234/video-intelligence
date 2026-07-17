from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.models import VideoMetricSnapshot


def test_missing_metrics_remain_none() -> None:
    snapshot = VideoMetricSnapshot(
        item_id="item-1",
        sampled_at=datetime.now(timezone.utc),
        likes=12,
        shares=None,
        plays=None,
        confidence=0.7,
    )

    assert snapshot.shares is None
    assert snapshot.plays is None
    assert snapshot.likes == 12


def test_negative_metric_is_rejected() -> None:
    with pytest.raises(ValidationError):
        VideoMetricSnapshot(
            item_id="item-1",
            sampled_at=datetime.now(timezone.utc),
            likes=-1,
            confidence=0.7,
        )
