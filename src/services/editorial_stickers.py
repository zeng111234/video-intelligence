"""Small, transcript-grounded editorial sticker events and renderers.

These are project-owned vector drawings, not Material Symbols, stock images,
or generated pictures.  The event builder only emits a sticker when the
spoken cue contains a concrete semantic signal and keeps the event count
small so the A-roll remains dominant.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


_ROLE_RULES: tuple[tuple[str, frozenset[str], str, str], ...] = (
    ("offer_compare", frozenset({"PRICE", "NUMBER", "PERCENT", "KEY_CLAIM"}), "同一句包含可识别的优惠/奖励对照语义", "pop_soft"),
    ("coupon", frozenset({"NUMBER", "PRODUCT", "PROCESS", "STEP", "KEY_CLAIM"}), "明确提到优惠券或券的数量/领取语义", "pop_soft"),
    ("negative", frozenset({"NEGATIVE", "WARNING"}), "明确的否定或风险语义", "warning_tick"),
    ("positive", frozenset({"POSITIVE", "CONCLUSION"}), "明确的正向结果或结论语义", "success_ping"),
    ("number", frozenset({"NUMBER", "PRICE", "PERCENT"}), "数字、价格或比例语义", "pop_soft"),
    ("process", frozenset({"PROCESS", "STEP", "PRODUCT", "SCENE", "LOCATION", "EXAMPLE"}), "具体对象、步骤或场景语义", "pop_soft"),
    ("cta", frozenset({"CTA"}), "明确的行动号召语义", "success_ping"),
)


_MONEY_OR_NUMBER = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?:元|块|%|％|张|个)?")
_OFFER_ACTION = re.compile(r"充|充值|储值|送|赠|返|奖励|优惠|折扣|福利")
# A bare ``券`` is intentionally not enough: it also matches 证券 and other
# unrelated industry nouns.  The visual grammar needs an explicit coupon
# product term before it can draw a coupon shape.
_COUPON_TERM = re.compile(r"优惠券|折扣券|代金券|礼券|兑换券|卡券")
_COUNT_BEFORE_COUPON = re.compile(
    r"(?<![\d.,-])(?P<count>-?(?:\d+(?:\.\d+)?|[零一二三四五六七八九十百千万两]+))\s*(?:张|个)?\s*(?:无门槛)?(?:优惠券|折扣券|代金券|礼券|兑换券|卡券)"
)
_CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _count_value(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if not value or any(char not in _CHINESE_DIGITS and char not in {"十", "百", "千", "万"} for char in value):
        return None
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = 0
    current = 0
    saw_unit = False
    for char in value:
        if char in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[char]
            continue
        saw_unit = True
        unit = units[char]
        total += (current or 1) * unit
        current = 0
    if not saw_unit:
        return _CHINESE_DIGITS.get(value)
    return total + current


_CHARGE_VALUE = re.compile(
    r"(?:充|充值|储值)\s*(?P<charge>(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?))\s*(?P<charge_unit>元|块)?"
)
_REWARD_VALUE = re.compile(
    r"(?:送|赠|返|返还|奖励)\s*(?P<reward>(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?))\s*(?P<reward_unit>元|块)?"
)
_OFFER_NEGATION = re.compile(r"不(?:送|赠|返)|没有|未(?:送|赠|返)|取消|停止|终止|作废|过时")


def _normalize_number(value: str) -> str:
    return value.replace(",", "").strip()


def _offer_visual_values(source_text: str) -> tuple[str, str] | None:
    compact = re.sub(r"\s+", "", source_text)
    if _OFFER_NEGATION.search(compact):
        return None
    charge = _CHARGE_VALUE.search(compact)
    if charge is None:
        return None
    reward = _REWARD_VALUE.search(compact, charge.end())
    if reward is None:
        return None
    charge_value = _normalize_number(charge.group("charge")) + (charge.group("charge_unit") or "")
    reward_value = _normalize_number(reward.group("reward")) + (reward.group("reward_unit") or "")
    return charge_value, reward_value


def _coupon_visual_count(source_text: str) -> int | None:
    match = _COUNT_BEFORE_COUPON.search(re.sub(r"\s+", "", source_text))
    if not match:
        return None
    count = _count_value(match.group("count"))
    return count if count is not None and 1 <= count <= 20 else None


def _coupon_count_is_explicitly_invalid(source_text: str) -> bool:
    compact = re.sub(r"\s+", "", source_text)
    # If a numeric count is present but is not a positive integer, do not draw
    # a factual coupon quantity (or a generic coupon stack) at all.
    if not _COUPON_TERM.search(compact):
        return False
    count_match = re.search(
        r"(?<![\d.,-])(?P<count>-?(?:\d+(?:\.\d+)?|[零一二三四五六七八九十百千万两]+))\s*(?:张|个)?\s*(?:无门槛)?(?:优惠券|折扣券|代金券|礼券|兑换券|卡券)",
        compact,
    )
    if not count_match:
        return False
    count = _count_value(count_match.group("count"))
    return count is None or count < 1 or count > 20 or any(
        char in count_match.group("count") for char in ".-"
    )


def _semantic_kind(source_text: str, roles: set[str], default_kind: str) -> tuple[str, str] | None:
    """Choose a visual grammar from the current sentence, never sample copy."""

    compact = re.sub(r"\s+", "", source_text)
    if _offer_visual_values(compact):
        return "offer_compare", "同一句出现两个以上数字并带有优惠/奖励动作"
    if _COUPON_TERM.search(compact) and roles.intersection(
        {"NUMBER", "PRODUCT", "PROCESS", "STEP", "KEY_CLAIM"}
    ):
        if _coupon_count_is_explicitly_invalid(compact):
            return None
        return "coupon", "当前句明确包含优惠券/券的具体语义"
    # These two rules are conditional visual grammars. A generic KEY_CLAIM
    # or NUMBER must continue to the ordinary role rules instead of becoming
    # an invented promotion/coupon visual.
    if default_kind in {"offer_compare", "coupon"}:
        return None
    if default_kind:
        return default_kind, "当前句与该语义角色匹配"
    return None


def _word_start(segment: Mapping[str, Any], term: str, fallback: float) -> float:
    words = segment.get("words") or []
    joined = ""
    offsets: list[tuple[int, float]] = []
    for word in words:
        token = str(word.get("word") or word.get("text") or "")
        if not token:
            continue
        try:
            timestamp = float(word.get("start", fallback))
        except (TypeError, ValueError):
            timestamp = fallback
        offsets.append((len(joined), timestamp))
        joined += token
    offset = joined.find(term)
    if offset < 0:
        return fallback
    return next((timestamp for position, timestamp in reversed(offsets) if position <= offset), fallback)


def build_editorial_sticker_events(
    segments: Sequence[Mapping[str, Any]],
    *,
    duration_seconds: float,
    max_events: int = 5,
) -> list[dict[str, Any]]:
    """Build at most a few events from the current transcript, never sample copy."""

    if duration_seconds <= 0:
        return []
    events: list[dict[str, Any]] = []
    last_start = -100.0
    used_kinds: set[str] = set()
    for index, segment in enumerate(sorted(segments, key=lambda item: float(item.get("start") or 0))):
        source_text = str(segment.get("source_text") or segment.get("text") or "").strip()
        if not source_text:
            continue
        start = float(segment.get("start") or 0)
        end = min(float(segment.get("end") or start), duration_seconds)
        if end <= start:
            continue
        roles = {
            str(role).strip().upper()
            for role in segment.get("semantic_roles") or []
            if str(role).strip()
        }
        if not roles:
            role = str(segment.get("semantic_role") or "").strip().upper()
            if role:
                roles.add(role)
        for kind, accepted_roles, reason, sfx_profile in _ROLE_RULES:
            role = next((candidate for candidate in accepted_roles if candidate in roles), None)
            if role is None:
                continue
            if kind in used_kinds:
                continue
            candidates = [
                str(candidate).strip()
                for candidate in segment.get("keyword_candidates") or []
                if str(candidate).strip() and str(candidate).strip() in source_text
            ]
            term = str(segment.get("semantic_text") or segment.get("text") or "").strip()
            if term not in source_text:
                term = candidates[0] if candidates else ""
            if not term:
                continue
            selected_kind = _semantic_kind(source_text, roles, kind)
            if selected_kind is None:
                continue
            kind, semantic_reason = selected_kind
            if kind in used_kinds:
                continue
            event_start = _word_start(segment, term, start)
            if event_start < start or event_start - last_start < 8.0:
                continue
            # Keep every sticker inside the transcript segment that gave it
            # meaning.  The renderer retimes source-clock events by the
            # playback rate; extending beyond ``end`` therefore lets a
            # previous sentence's factual graphic bleed into the next
            # sentence in the final output (especially on short offer
            # phrases).  A short event is preferable to carrying stale
            # numbers or labels across a sentence boundary.
            event_end = min(duration_seconds, end, event_start + 2.6)
            if event_end - event_start < 1.2:
                continue
            events.append(
                {
                    "event_id": f"editorial-sticker-{len(events) + 1:02d}",
                    "type": "semantic_sticker",
                    "style_id": f"editorial_{kind}",
                    "asset_category": "custom_semantic_sticker",
                    "start": round(event_start, 3),
                    "end": round(event_end, 3),
                    "semantic_text": term,
                    "source_text": source_text,
                    "semantic_role": role,
                    "source_segment_index": segment.get("source_segment_index", index),
                    "grounded_in_text": True,
                    "importance": 0.86,
                    "visual_intensity": 3,
                    "reason": semantic_reason or reason,
                    "sfx_profile": sfx_profile,
                    "source": "VideoInsight project-owned editorial vector",
                    "license": "project-owned-local-vector",
                    "third_party_cost": 0,
                    "animation": "slide_pop_rotate_fade",
                    "side": "left" if len(events) % 2 == 0 else "right",
                    "safe_area": "lower_caption_safe_band" if kind == "cta" else "side_shelf",
                }
            )
            if kind == "offer_compare":
                values = _offer_visual_values(source_text)
                if values:
                    events[-1]["offer_values"] = list(values)
                    events[-1]["offer_labels"] = [
                        "充值" if re.search(r"充|充值|储值", source_text) else "",
                        "赠送" if re.search(r"送|赠|返|奖励", source_text) else "",
                    ]
            elif kind == "coupon":
                count = _coupon_visual_count(source_text)
                if count is not None:
                    events[-1]["coupon_count"] = count
            last_start = event_start
            used_kinds.add(kind)
            break
        if len(events) >= max_events:
            break
    return events


def render_editorial_sticker(event: Mapping[str, Any], output_path: Path) -> None:
    """Render a transparent, custom vector sticker with no external glyphs."""

    from PIL import Image, ImageDraw, ImageFont

    width, height = 300, 220
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    kind = str(event.get("style_id") or "").removeprefix("editorial_")
    yellow = (255, 211, 92, 255)
    red = (255, 100, 96, 255)
    green = (88, 208, 142, 255)
    blue = (132, 202, 255, 255)
    cx, cy = width // 2, height // 2
    if kind == "offer_compare":
        # The subtitle carries the actual values. This vector only expresses
        # the input -> bonus relationship without repeating sample copy.
        from src.services.video_editor_cloud import BRAND_TITLE_FONT_PATH

        values = [str(value) for value in event.get("offer_values") or []][:2]
        labels = [str(value) for value in event.get("offer_labels") or []][:2]
        font = ImageFont.truetype(str(BRAND_TITLE_FONT_PATH), 30)
        value_font = ImageFont.truetype(str(BRAND_TITLE_FONT_PATH), 36)
        draw.line((54, 92, 126, 92), fill=yellow, width=10)
        draw.line((54, 132, 126, 132), fill=(255, 236, 170, 210), width=7)
        draw.line((174, 92, 246, 92), fill=green, width=10)
        draw.line((174, 132, 246, 132), fill=(255, 236, 170, 210), width=7)
        if values:
            draw.text((90, 56), labels[0] if labels else "", font=font, anchor="mm", fill=yellow)
            draw.text((210, 56), labels[1] if len(labels) > 1 else "", font=font, anchor="mm", fill=green)
            draw.text((90, 118), values[0], font=value_font, anchor="mm", fill=(248, 250, 252, 255))
            draw.text((210, 118), values[1], font=value_font, anchor="mm", fill=(248, 250, 252, 255))
        draw.line((132, 112, 168, 112), fill=blue, width=7)
        draw.line((168, 112, 150, 96), fill=blue, width=7)
        draw.line((168, 112, 150, 128), fill=blue, width=7)
        draw.arc((86, 46, 214, 178), 205, 335, fill=yellow, width=5)
    elif kind == "coupon":
        # A restrained coupon stack: concrete enough to explain the noun,
        # without Material Symbols, a card UI, or invented wording.
        from src.services.video_editor_cloud import BRAND_TITLE_FONT_PATH

        count = max(1, min(6, int(event.get("coupon_count") or 3)))
        coupon_font = ImageFont.truetype(str(BRAND_TITLE_FONT_PATH), 30)
        for index in range(count):
            offset = index * 8
            color = (blue, green, yellow)[index % 3]
            left, top, right, bottom = 70 + offset, 64 + offset // 2, 210 + offset, 132 + offset // 2
            draw.rounded_rectangle((left, top, right, bottom), radius=12, outline=color, width=6)
            draw.line((left + 22, (top + bottom) // 2, right - 22, (top + bottom) // 2), fill=color, width=4)
            draw.ellipse((left - 7, (top + bottom) // 2 - 7, left + 7, (top + bottom) // 2 + 7), outline=color, width=4)
            draw.ellipse((right - 7, (top + bottom) // 2 - 7, right + 7, (top + bottom) // 2 + 7), outline=color, width=4)
        if event.get("coupon_count"):
            draw.text((150, 182), f"×{int(event['coupon_count'])}", font=coupon_font, anchor="mm", fill=yellow)
    elif kind == "positive":
        draw.ellipse((66, 78, 116, 128), outline=blue, width=7)
        draw.ellipse((184, 78, 234, 128), outline=green, width=7)
        draw.arc((90, 43, 210, 163), 200, 340, fill=yellow, width=7)
        draw.line((202, 92, 227, 103, 204, 114), fill=yellow, width=7)
        draw.arc((90, 57, 210, 177), 20, 160, fill=blue, width=5)
    elif kind == "negative":
        draw.line((70, 88, 230, 88), fill=(236, 236, 236, 200), width=8)
        draw.line((70, 116, 180, 116), fill=(236, 236, 236, 160), width=7)
        draw.line((61, 55, 241, 160), fill=red, width=12)
        draw.line((241, 55, 61, 160), fill=(255, 141, 113, 210), width=5)
    elif kind == "number":
        draw.line((54, 156, 246, 156), fill=yellow, width=7)
        # Keep number emphasis editorial and lightweight.  Do not surround
        # the word with a complete circle/ray burst: that reads as a cheap
        # sun/badge icon and competes with the subtitle itself.
        draw.line((76, 116, 52, 96), fill=yellow, width=5)
        draw.line((224, 116, 248, 96), fill=yellow, width=5)
    elif kind == "process":
        draw.line((62, 148, 118, 92, 174, 148, 238, 82), fill=blue, width=8)
        draw.ellipse((48, 134, 76, 162), fill=yellow)
        draw.ellipse((104, 78, 132, 106), fill=green)
        draw.ellipse((160, 134, 188, 162), fill=yellow)
        draw.line((218, 82, 246, 82), fill=green, width=7)
        draw.line((236, 72, 246, 82, 236, 92), fill=green, width=7)
    elif kind == "cta":
        draw.rounded_rectangle((58, 66, 242, 148), radius=24, outline=blue, width=8)
        draw.polygon([(98, 148), (86, 184), (131, 151)], outline=blue, fill=(0, 0, 0, 0))
        draw.line((112, 106, 186, 106), fill=yellow, width=7)
        draw.line((112, 126, 166, 126), fill=(235, 248, 255, 220), width=6)
        draw.line((204, 99, 204, 138), fill=yellow, width=6)
    else:
        draw.arc((70, 60, 230, 180), 190, 350, fill=yellow, width=7)
    canvas.save(output_path)
