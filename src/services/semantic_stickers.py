"""Compatibility helpers for the retired Material Symbols sticker layer.

Formal grammar exports use the project-owned editorial sticker renderer and
subtitle-native emphasis. This module intentionally never creates a sticker;
the empty builder keeps older callers safe while preventing legacy icons from
reappearing in new timelines.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def build_sticker_events(
    segments: Sequence[Mapping], *, duration_seconds: float
) -> list[dict]:
    """Return no legacy icons; callers must use the editorial style engine."""

    return []


def supplement_motion_events(
    events: Sequence[Mapping], stickers: Sequence[Mapping]
) -> list[dict]:
    """Merge only non-retired sticker events without duplicating emphasis."""

    active_stickers = [
        dict(sticker)
        for sticker in stickers
        if str(sticker.get("style_id") or "") != "grammar_sticker"
    ]
    retained = [
        dict(event)
        for event in events
        if str(event.get("style_id") or "") != "grammar_sticker"
        and not (
            event.get("type") in {"semantic_symbol", "text_emphasis"}
            and any(
                float(event.get("start") or 0) < float(sticker.get("end") or 0)
                and float(event.get("end") or 0) > float(sticker.get("start") or 0)
                for sticker in active_stickers
            )
        )
    ]
    return sorted(
        [*retained, *active_stickers],
        key=lambda event: float(event.get("start") or 0),
    )
