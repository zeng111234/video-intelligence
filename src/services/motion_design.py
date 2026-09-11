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
_DATA_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|倍)\s*(?:的)?\s*(?:顾客|用户|增长|转化|复购|成交|比例|人)"
)
_PRICE_PATTERN = re.compile(r"售价|价格|只要|仅需|花\s*\d+(?:\.\d+)?\s*(?:元|块)?")
_COMPARE_PATTERN = re.compile(r"不是.{0,16}而是|以前.{0,16}现在|传统.{0,16}新|旧.{0,16}新")
_KNOWLEDGE_PATTERN = re.compile(r"知识点|小技巧|剪辑技巧|关键技巧|核心技巧|秘诀|诀窍|重点是|核心是")
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
_LOGIC_PATTERN = re.compile(r"因为|导致|带来影响|影响|从而|结果是")
_ROAD_PATTERN = re.compile(r"高速公路|公路|道路|路上|修建|通道|路线")
_BENEFIT_PATTERN = re.compile(
    r"优惠|福利|特价|折扣|送|省下|省钱|奖励|免费|回头客|成交|赚钱|增长|翻倍|爆款|引流|利润|成本"
)
_KEYWORD_STOPWORDS = {
    "这个", "那个", "一个", "一些", "我们", "你们", "他们", "就是",
    "然后", "但是", "因为", "所以", "可以", "可能", "还是", "已经",
    "自己", "什么", "怎么", "这样", "那样", "今天", "现在", "大家",
}


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _benefit_is_near_number(text: str, number: re.Match[str]) -> bool:
    """Only treat a number as a promotion when the benefit is local to it."""

    context = text[max(0, number.start() - 6) : min(len(text), number.end() + 8)]
    return bool(_BENEFIT_PATTERN.search(context))


def _segment_clock(segment: Mapping[str, Any]) -> tuple[float, float] | None:
    try:
        start = max(0.0, float(segment.get("start", 0)))
        end = float(segment.get("end", 0))
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    return start, end


def _semantic_event_start(
    segment: Mapping[str, Any],
    *,
    segment_start: float,
    segment_end: float,
    payload: str,
    semantic_kind: str = "",
) -> float:
    """Place a visual beat near its spoken keyword, never at a cold lead-in."""

    payload_compact = _compact(payload)
    payload_digits = "".join(re.findall(r"\d+(?:\.\d+)?", payload_compact))
    words = segment.get("words") or []
    for word in words:
        if not isinstance(word, Mapping):
            continue
        word_text = _compact(word.get("word") or word.get("text"))
        if not word_text:
            continue
        word_digits = "".join(re.findall(r"\d+(?:\.\d+)?", word_text))
        matched = (
            bool(payload_compact and payload_compact in word_text)
            or bool(payload_digits and payload_digits in word_digits)
        )
        if not matched:
            continue
        try:
            word_start = float(word.get("start") or segment_start)
        except (TypeError, ValueError):
            word_start = segment_start
        return min(segment_end, max(segment_start, word_start))

    # Some cached transcripts do not retain reliable Chinese word text.  A
    # late-window fallback keeps the accent attached to the end of the spoken
    # sentence instead of showing it over unrelated lead-in words.
    tail_window = {
        "cta": 1.8,
        "result": 2.2,
        "warning": 2.2,
    }.get(semantic_kind, _MAX_EVENT_SECONDS)
    return max(segment_start, segment_end - tail_window)


def _classify(text: str) -> tuple[str, str, int] | None:
    """Return ``(semantic_kind, short_payload, priority)`` for one segment."""

    price = _PRICE_PATTERN.search(text)
    if price:
        price_number = _NUMBER_PATTERN.search(text[price.start() :])
        return "price", (price_number.group(0) if price_number else price.group(0)), 99
    data = _DATA_PATTERN.search(text)
    if data:
        data_number = _NUMBER_PATTERN.search(data.group(0))
        return "data", (data_number.group(0) if data_number else data.group(0)), 97
    number = _NUMBER_PATTERN.search(text)
    if number:
        if _benefit_is_near_number(text, number):
            return "benefit", number.group(0), 98
        return "number", number.group(0), 100
    road = _ROAD_PATTERN.search(text)
    if road:
        return "road", road.group(0), 94
    compare = _COMPARE_PATTERN.search(text)
    if compare:
        return "compare", compare.group(0), 91
    knowledge = _KNOWLEDGE_PATTERN.search(text)
    if knowledge:
        return "knowledge", knowledge.group(0), 89
    logic = _LOGIC_PATTERN.search(text)
    if logic:
        return "logic", logic.group(0), 84
    process = _PROCESS_PATTERN.search(text)
    if process:
        return "process", process.group(0), 90
    benefit = _BENEFIT_PATTERN.search(text)
    if benefit:
        return "benefit", benefit.group(0), 92
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


def _keyword_payload(text: str) -> str:
    """Extract one reusable visual keyword from the current spoken segment.

    This is a fallback for unfamiliar customer domains.  The returned word is
    always present in the transcript; no sample sentence or invented label is
    allowed into the visual plan.
    """

    candidates: list[str] = []
    try:
        import jieba.analyse  # type: ignore[import-untyped]

        candidates.extend(jieba.analyse.extract_tags(text, topK=6, withWeight=False))
    except (ImportError, AttributeError, TypeError, ValueError):
        pass
    candidates.extend(re.findall(r"[\u4e00-\u9fff]{2,6}", text))
    for raw in candidates:
        token = _compact(raw).strip("，。！？；：、,.!?;:")
        if (
            2 <= len(token) <= 6
            and token not in _KEYWORD_STOPWORDS
            and token in text
            and re.search(r"[\u4e00-\u9fff]", token)
        ):
            return token
    return ""


_STYLE_BY_KIND = {
    "number": "number_slam",
    "data": "data_highlight_card",
    "price": "price_zoom_card",
    "benefit": "benefit_burst",
    "road": "road_push",
    "compare": "compare_split_accent",
    "knowledge": "knowledge_pop_card",
    "process": "process_marker",
    "warning": "warning_shake",
    "logic": "logic_arrow",
    "result": "result_stamp",
    "cta": "cta_burst",
    "keyword": "keyword_pop",
}

# Keep the legacy ``style_id`` values in persisted timelines for compatibility,
# but give the renderer an abstraction that is independent of any one icon or
# sticker.  This is the important distinction between semantic direction and
# visual execution: the director chooses a visual verb, while the local style
# engine chooses the actual line treatment.
_VISUAL_VERB_BY_KIND = {
    "number": "impact",
    "price": "impact",
    "data": "accumulate",
    "benefit": "reveal",
    "road": "flow",
    "compare": "compare",
    "knowledge": "reveal",
    "process": "flow",
    "warning": "warning",
    "logic": "flow",
    "result": "resolve",
    "cta": "resolve",
    "keyword": "reveal",
}

_SYMBOL_BY_KIND = {
    "number": "number_badge",
    "data": "highlight_box",
    "price": "burst_lines",
    "benefit": "green_check",
    "road": "arrow",
    "compare": "comparison_vs",
    "knowledge": "question",
    "process": "underline",
    "warning": "warning",
    "logic": "arrow",
    "result": "green_check",
    "cta": "circle",
    "keyword": "highlight_box",
}

_CAMERA_BY_KIND = {
    "number": "punch_in_medium",
    "data": "punch_in_soft",
    "price": "punch_in_medium",
    "benefit": "slow_push",
    "road": "reframe_right",
    "compare": "reframe_left",
    "knowledge": "punch_in_soft",
    "process": "slow_push",
    "warning": "punch_in_medium",
    "logic": "reframe_right",
    "result": "punch_in_soft",
    "cta": "slow_push",
    "keyword": "punch_in_soft",
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
            keyword = _keyword_payload(text)
            if not keyword:
                continue
            semantic = ("keyword", keyword, 60)
        kind, payload, priority = semantic
        style_id = _STYLE_BY_KIND[kind]
        event_start = _semantic_event_start(
            raw,
            segment_start=start,
            segment_end=end,
            payload=payload,
            semantic_kind=kind,
        )
        event_end = min(end, event_start + _MAX_EVENT_SECONDS)
        if event_end - event_start < _MIN_EVENT_SECONDS:
            event_start = max(start, event_end - _MIN_EVENT_SECONDS)
        candidates.append(
            {
                "event_id": f"motion-{len(candidates) + 1:02d}",
                "start": round(event_start, 3),
                "end": round(event_end, 3),
                "type": "semantic_motion_badge",
                "renderer": "procedural_overlay_v2_caption_integrated",
                "style_id": style_id,
                "semantic_kind": kind,
                "visual_verb": _VISUAL_VERB_BY_KIND.get(kind, "reveal"),
                "visual_language": "editorial_line_v1",
                "visual_action": "caption_attached_punctuation",
                "visual_variant": len(candidates) % 2,
                # Generic keywords are already animated by the subtitle
                # layer. Keep them out of the floating procedural icon layer.
                "render_policy": "caption_only" if kind == "keyword" else "semantic_accent",
                "semantic_text": payload[:14],
                "fact": payload[:14] if kind == "number" else "",
                "anchor": "smart_caption",
                "presentation": "caption_integrated_accent",
                "avoid_zones": ["face", "subtitle", "subject"],
                "source_segment_index": segment_index,
                "source_text": text,
                "grounded_in_text": True,
                "fallback": "subtitle_kinetic_emphasis",
                "action": "symbol_overlay",
                "symbol": _SYMBOL_BY_KIND.get(kind, "highlight_box"),
                "camera_action": _CAMERA_BY_KIND.get(kind, "punch_in_soft"),
                "text_emphasis": kind in {"number", "data", "price", "warning", "cta"},
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
    # second pass enforces a visual breathing space between badges.  Do not
    # let one high-frequency semantic class (usually numbers) consume the
    # whole visual budget: when the transcript contains other grounded kinds,
    # reserve a few beats for them so the edit has a deliberate visual
    # vocabulary instead of repeating one pulse.
    ranked = sorted(
        candidates,
        key=lambda item: (-int(item["priority"]), float(item["start"])),
    )
    selected: list[dict[str, Any]] = []

    distinct_styles = {str(item.get("style_id") or "") for item in candidates}
    diversity_target = min(target_count, 4, len(distinct_styles))
    for candidate in ranked:
        style_id = str(candidate.get("style_id") or "")
        if any(str(item.get("style_id") or "") == style_id for item in selected):
            continue
        if any(
            abs(float(candidate["start"]) - float(item["start"]))
            < _MIN_EVENT_GAP_SECONDS
            for item in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= diversity_target:
            break

    for candidate in ranked:
        if candidate in selected:
            continue
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
