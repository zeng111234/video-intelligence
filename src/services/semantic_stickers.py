"""Sparse, grounded sticker projection using the installed Apache-2.0 glyph library.

No provider calls, sample sentences, stock search, or opaque picture cards.
This supplements SPARSE_ASSET_V1 only; strict grammar rendering stays unchanged.
"""
from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
FONT = ROOT / "data/creative_assets/fonts/MaterialSymbolsOutlined.ttf"
LICENSE = ROOT / "data/creative_assets/fonts/LICENSE-MaterialSymbols-Apache-2.0.txt"

# Object/action vocabulary, not transcript answers. Unknown subjects get no sticker.
RULES = (
    (("优惠券", "折扣券", "代金券"), "local_offer", 0xE54E, "PRODUCT", "pop_soft", "#FFD45C"),
    (("会员卡", "充值", "储值卡"), "credit_card", 0xE870, "PRODUCT", "tick_soft", "#67DDD0"),
    (("奖励", "礼物", "赠品", "红包"), "card_giftcard", 0xE8F6, "PRODUCT", "success_ping", "#FFBD73"),
    (("转发", "分享", "朋友圈", "转介绍"), "share", 0xE80D, "PROCESS", "whoosh_soft", "#89C8FF"),
    (("烧烤", "餐厅", "餐饮", "烹饪"), "restaurant", 0xE56C, "SCENE", "pop_soft", "#FFBD73"),
    (("剪发", "理发", "剪刀"), "content_cut", 0xE14E, "PROCESS", "tick_soft", "#67DDD0"),
    (("步骤", "清单", "检查表"), "checklist", 0xE6B1, "STEP", "tick_soft", "#89C8FF"),
    (("地图", "地址", "路线"), "location_on", 0xE0C8, "LOCATION", "pop_soft", "#FFBD73"),
)


def build_sticker_events(segments: Sequence[Mapping], *, duration_seconds: float) -> list[dict]:
    if not FONT.is_file() or not LICENSE.is_file() or duration_seconds <= 0:
        return []
    events: list[dict] = []
    limit = max(1, min(10, round(duration_seconds / 60 * 5)))
    last_by_subject: dict[str, float] = {}
    for index, segment in enumerate(segments):
        text = str(segment.get("text") or "")
        start, end = float(segment.get("start") or 0), float(segment.get("end") or 0)
        for terms, subject, glyph, role, sound, color in RULES:
            term = next((term for term in terms if term in text), None)
            if not term:
                continue
            # Use actual word clocks when available. Otherwise cue start is honest.
            term_start = start
            words = segment.get("words") or []
            joined = ""
            offsets = []
            for word in words:
                token = str(word.get("word") or word.get("text") or "")
                offsets.append((len(joined), float(word.get("start", start))))
                joined += token
            offset = joined.find(term)
            if offset >= 0:
                term_start = next((t for pos, t in reversed(offsets) if pos <= offset), start)
            if term_start < start or term_start >= end:
                break
            if events and term_start - events[-1]["start"] < 6:
                break
            if term_start - last_by_subject.get(subject, -100) < 18:
                break
            events.append({
                "event_id": f"sticker-{index}-{subject}", "type": "semantic_symbol",
                "style_id": "grammar_sticker", "asset_category": "licensed_sticker",
                # A noun is often spoken at the very end of a sentence. Keep
                # its visual echo readable through the following beat instead
                # of silently dropping it or flashing it for 200 ms.
                "start": term_start, "end": min(max(end, term_start + 1.8), term_start + 2.5, duration_seconds),
                "source_segment_index": index, "semantic_text": term, "source_text": text,
                "semantic_role": role, "importance": 0.88, "visual_intensity": 3,
                "symbol": subject, "glyph": glyph, "color": color,
                "side": "left" if len(events) % 2 == 0 else "right",
                "reason": f"Named object/action in source cue: {term}",
                "sfx_profile": sound, "source": "Google Material Symbols",
                "license": "Apache-2.0", "license_file": str(LICENSE),
                "animation": "single_pop_tilt_fade", "third_party_cost": 0,
            })
            last_by_subject[subject] = term_start
            break
        if len(events) >= limit:
            break
    return events


def supplement_motion_events(events: Sequence[Mapping], stickers: Sequence[Mapping]) -> list[dict]:
    # One visual emphasis at a time: don't stack an old text badge on a sticker.
    retained = [dict(event) for event in events if not (
        event.get("type") in {"semantic_symbol", "text_emphasis"}
        and any(float(event.get("start") or 0) < sticker["end"]
                and float(event.get("end") or 0) > sticker["start"] for sticker in stickers)
    )]
    return sorted([*retained, *(dict(s) for s in stickers)], key=lambda e: float(e.get("start") or 0))


def render_sticker(event: Mapping, output_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    canvas = Image.new("RGBA", (320, 320))
    font = ImageFont.truetype(str(FONT), 246)
    # Filled library glyph with a narrow white die-cut edge, no background/card.
    try:
        font.set_variation_by_axes([1, 0, 48, 600])
    except (OSError, ValueError):
        pass
    draw = ImageDraw.Draw(canvas)
    draw.text((160, 156), chr(int(event["glyph"])), font=font, anchor="mm",
              fill=str(event["color"]), stroke_width=4, stroke_fill="#FFFFFF")
    canvas.save(output_path)
