"""Transcript-grounded motion design planning.

The planner is deliberately renderer-agnostic.  It turns a reviewed speech
timeline into a small number of semantic motion events which can be rendered
by the local FFmpeg path today and by Lottie/WebGL in a future preview path.
No event is invented from a fixed wall-clock position: every event points back
to one transcript segment and carries a safe fallback.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence


MOTION_DESIGN_PLAN_VERSION = "motion-design-v2"
_MAX_EVENTS = 10
_MIN_EVENT_GAP_SECONDS = 2.8
_MIN_EVENT_SECONDS = 0.9
_MAX_EVENT_SECONDS = 2.8

_NUMBER_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:万|亿|千|百|元|块|个|条|秒|分钟|天|倍|%|％)?"
)
_PROCESS_PATTERN = re.compile(
    r"第[一二三四五六七八九十百\d]+(?:步|点)?|步骤|方法|流程|要点|首先|其次|然后|最后|三招|两招|一招"
)
_WARNING_PATTERN = re.compile(
    r"注意|千万不要|不要|别再|避免|风险|坑|误区|警惕|不能|失败|小心|切记"
)
_CTA_PATTERN = re.compile(
    r"评论|留言|关注|私信|进群|领取|查看|扫码|加我|主页|下方|收藏|转发|点击|扣\s*[0-9一二三四五六七八九十]+"
)
_RESULT_PATTERN = re.compile(
    r"所以|因此|结果|解决|提升|降低|省下|做到|关键|记住|核心|有效|有用|才是"
)


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _segment_clock(segment: Mapping[str, Any]) -> tuple[float, float] | None:
    try:
        start = max(0.0, float(segment.get("start", 0)))
        end = float(segment.get("end", 0))
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    return start, end


def _classify(text: str) -> tuple[str, str, int] | None:
    """Return ``(semantic_kind, short_payload, priority)`` for one segment."""

    number = _NUMBER_PATTERN.search(text)
    if number:
        return "number", number.group(0), 100
    process = _PROCESS_PATTERN.search(text)
    if process:
        return "process", process.group(0), 90
    warning = _WARNING_PATTERN.search(text)
    if warning:
        return "warning", warning.group(0), 88
    cta = _CTA_PATTERN.search(text)
    if cta:
        return "cta", cta.group(0), 86
    result = _RESULT_PATTERN.search(text)
    if result:
        return "result", result.group(0), 76
    return None


_STYLE_BY_KIND = {
    "number": "number_slam",
    "process": "process_marker",
    "warning": "warning_shake",
    "result": "result_stamp",
    "cta": "cta_burst",
}


def build_semantic_motion_events(
    segments: Sequence[Mapping[str, Any]],
    *,
    duration_seconds: float,
) -> list[dict[str, Any]]:
    """Plan bounded, transcript-grounded motion events.

    The renderer receives only the strongest semantic beats.  Ordinary words
    remain the responsibility of the kinetic ASS subtitle layer, preventing a
    sticker from appearing on every cue and making the result noisy.
    """

    if duration_seconds <= 0:
        return []
    candidates: list[dict[str, Any]] = []
    for segment_index, raw in enumerate(segments):
        if not isinstance(raw, Mapping):
            continue
        clock = _segment_clock(raw)
        text = _compact(raw.get("text"))
        if clock is None or not text:
            continue
        start, end = clock
        semantic = _classify(text)
        if semantic is None:
            continue
        kind, payload, priority = semantic
        style_id = _STYLE_BY_KIND[kind]
        event_end = min(end, start + _MAX_EVENT_SECONDS)
        if event_end - start < _MIN_EVENT_SECONDS:
            event_end = min(duration_seconds, start + _MIN_EVENT_SECONDS)
        candidates.append(
            {
                "event_id": f"motion-{len(candidates) + 1:02d}",
                "start": round(start, 3),
                "end": round(event_end, 3),
                "type": "semantic_motion_badge",
                "renderer": "procedural_overlay_v2_caption_integrated",
                "style_id": style_id,
                "semantic_kind": kind,
                "semantic_text": payload[:14],
                "fact": payload[:14] if kind == "number" else "",
                "anchor": "smart_caption",
                "presentation": "caption_integrated_accent",
                "avoid_zones": ["face", "subtitle", "subject"],
                "source_segment_index": segment_index,
                "source_text": text,
                "grounded_in_text": True,
                "fallback": "subtitle_kinetic_emphasis",
                "priority": priority,
            }
        )

    if not candidates:
        return []
    # Strong visual punctuation is intentionally sparse: roughly four events
    # per minute, with the hard upper bound remaining five per minute.
    target_count = min(
        _MAX_EVENTS,
        max(1, math.ceil(duration_seconds / 60 * 4)),
    )
    # Prefer high-value semantic beats, but keep their original clocks.  A
    # second pass enforces a visual breathing space between badges.
    ranked = sorted(
        candidates,
        key=lambda item: (-int(item["priority"]), float(item["start"])),
    )
    selected: list[dict[str, Any]] = []
    for candidate in ranked:
        if any(
            abs(float(candidate["start"]) - float(item["start"]))
            < _MIN_EVENT_GAP_SECONDS
            for item in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= target_count:
            break
    selected.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    for index, event in enumerate(selected, start=1):
        event["event_id"] = f"motion-{index:02d}"
        event.pop("priority", None)
    return selected
