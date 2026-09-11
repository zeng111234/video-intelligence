"""Motion-graphic components: shape + icon + text + entrance + one bound SFX.

Spec: the reference rough cut never shows a bare floating sticker.  Every
sudden visual is a composed motion graphic -- shape, icon, dynamic text, an
entrance animation, an exit, and exactly one short sound effect fired on the
entrance frame.

Four components cover the approved range:

* ``info_pill``      rounded pill + icon + main text + sub text  (whoosh)
* ``number_burst``   burst shape + oversized number             (impact)
* ``success_badge``  disc + check                               (success)
* ``warning_badge``  disc + alert mark                          (warning)

Everything is drawn locally with Pillow plus the reviewed MIT icon set; no new
artwork is downloaded.  Each event carries ``render_class="motion_graphic"``,
``sfx_required=True`` and reuses ``event_id`` for both the visual and its
sound, so the pair is auditable as ``visual_event_id == sfx_event_id``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont

_ICON_LIBRARY_DIR = Path(__file__).resolve().parents[2] / "assets" / "icons"
_FONT_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "fonts"
    / "SourceHanSerifCN-Heavy.otf"
)

MOTION_GRAPHIC_STYLES = frozenset(
    {"info_pill", "number_burst", "success_badge", "warning_badge"}
)

# Brand tokens, matching SUBTITLE_EMPHASIS_COLOR_* in video_editor_workflow.
# Saturated values on purpose: the frame is dark and a muted accent sank into
# the speaker's clothing.
_NUMBER = (255, 92, 76)
_METHOD = (255, 138, 60)
_CONFLICT = (251, 88, 106)
_POSITIVE = (46, 204, 120)

# style -> (icon slug, accent, entrance animation, sound profile)
_STYLE_SPEC: dict[str, tuple[str, tuple[int, int, int], str, str]] = {
    "info_pill": ("route", _METHOD, "slide_up_pop", "whoosh_soft"),
    "number_burst": ("percentage", _NUMBER, "pop_overshoot", "impact_soft"),
    "success_badge": ("check", _POSITIVE, "scale_bounce", "success_ping"),
    "warning_badge": ("alert-circle", _CONFLICT, "scale_bounce", "warning_tick"),
}

# The pill serves several meanings, so its icon follows the role instead of
# always drawing a location pin.  "第一步选科" needs a route, "立即报名" an
# arrow, a product an object mark -- a map pin on all of them is simply wrong.
_PILL_ICON_BY_ROLE: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"STEP", "PROCESS"}), "route"),
    (frozenset({"LOCATION", "SCENE"}), "map-pin"),
    (frozenset({"PRODUCT"}), "package"),
    (frozenset({"CTA"}), "arrow-right"),
    (frozenset({"NUMBER", "PRICE", "PERCENT"}), "coin"),
)

# Sentence-level fallbacks when the director supplied no role at all.
_PILL_ICON_BY_TEXT: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"第一步|第二步|步骤|流程|方法|怎么做"), "route"),
    (re.compile(r"附近|位置|地点|公里|门店地址"), "map-pin"),
    (re.compile(r"产品|商品|设备|型号|套餐"), "package"),
    (re.compile(r"报名|咨询|联系|评论区|留言|领取"), "arrow-right"),
    (re.compile(r"合同|清单|资料|名单|记录"), "file-text"),
)


def _pill_icon(roles: set[str], text: str) -> str:
    """Return the pill icon that matches the beat's meaning."""

    # A standard or model number reaching the pill (``18483-2001``) is an
    # identifier, not money: the role map's ``NUMBER`` entry would put a coin
    # there, which reads as a price.  Check the text before the roles.
    if _STANDARD_NUMBER.search(text or ""):
        return "file-text"
    for accepted, slug in _PILL_ICON_BY_ROLE:
        if roles & accepted:
            return slug
    for pattern, slug in _PILL_ICON_BY_TEXT:
        if pattern.search(text or ""):
            return slug
    return "info-circle"

# A success mark must never assert a sentence that negates or cancels it.
_NEGATION = re.compile(
    r"别让|别把|不要让|不要把|不是|并非|没有|取消|不能|不可|禁止|避免|小心|过头|过短|再长|再短"
)

_ROLE_STYLE: tuple[tuple[str, frozenset[str]], ...] = (
    ("warning_badge", frozenset({"WARNING", "NEGATIVE", "RISK"})),
    ("number_burst", frozenset({"PRICE", "PERCENT", "NUMBER"})),
    ("info_pill", frozenset({"PROCESS", "STEP", "LOCATION", "PRODUCT", "CTA"})),
    ("success_badge", frozenset({"POSITIVE", "CONCLUSION"})),
)

# Evidence in the reviewed sentence itself, used when the director supplied no
# specific role.  A local-rules director can label every segment
# ``LOW_INFORMATION``, and matching on roles alone then produced a clip with no
# visual component at all.
_TEXT_EVIDENCE: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Negation is checked first on purpose.  "不是增长30%" carries a number, but
    # a yellow number burst reads as a positive gain and inverts the meaning;
    # the warning mark must win over the numeric one.
    ("warning_badge", re.compile(
        r"别让|别把|不要让|不要把|不是|并非|不能|不可|禁止|避免|小心|风险|过头|过短|再长|再短|浪费"
        r"|不要|没有|取消|下降|亏损|失败|问题"
        r"|不送|不给|不返|不赠|不含|不参与|不支持|不保证|不推荐|不建议|不值得|不合适|不靠谱"
        r"|别买|别用|别选|降低|减少|下调|缩水|过期|失效|没用|白花钱"
    )),
    ("number_burst", re.compile(
        r"\d+(?:\.\d+)?\s*(?:[%％]|元|块|万|亿|折|倍|公里|千米|米|分钟|秒|天|个|张|家|人|套|分)"
    )),
    ("success_badge", re.compile(
        r"所以|因此|结果|最后|大多数人|回头客|成功|有效|推荐|增长了|提升"
    )),
    ("info_pill", re.compile(
        r"第一步|第二步|步骤|流程|方法|位置|地点|附近|门店|工厂|客户名单|合同|周期"
    )),
)

# A negated or downward clause must never be dressed as a gain.  Kept as a
# vocabulary of expression rather than a sample list: refusals, reductions and
# negative recommendations all invert the meaning of a bright numeric burst.
_NEGATED_NUMBER = re.compile(
    r"不是|并非|没有|不再|不如|比不上|低于|少于|不再有"
    r"|不送|不给|不返|不赠|不含|不包括|不参与|不支持|不保证"
    r"|降低|减少|下调|缩水|下降|亏损|亏了|失败|过期|失效|取消"
    r"|别|禁止|避免|小心|风险"
)

# The subject of a sentence can reject an action outright.
_NEGATIVE_RECOMMENDATION = re.compile(
    r"不推荐|不建议|不值得|不合适|不靠谱|不要|不能用|不能买|别买|别用|别选"
    r"|没用|白花钱|浪费"
)

_NUMBER_TOKEN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:[%％]|[元块万亿折倍]|公里|千米|米|分钟|秒|天|个月|个|张|家|人|套|步)?"
)

# A bare four-digit year is a date, not a business quantity.  "2026年充值800送
# 120元" highlights 2026 and "2026年中考" explodes a year unless the date is
# separated from the amount that actually carries the offer.
_YEAR_AT_START = re.compile(r"(?:19|20)\d{2}\s*年")
# Amounts and offers, which beat a date when both appear.
_AMOUNT_WITH_UNIT = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(万元|公里|千米|分钟|个月|元|块|万|亿|折|倍|%|％|天|米|秒|个|张|家|人|套|分)"
)
_OFFER_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# A standard or model number ("18483-2001") is an identifier, not a business
# quantity.  Bursting it presents a spec code as a headline figure, which reads
# as a wrong fact; such a beat belongs on the caption.
_STANDARD_NUMBER = re.compile(r"\d{3,5}\s*[-—－/]\s*\d{2,4}")
_AMOUNT_EVIDENCE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:元|块|万|亿|折|倍|%|％)"
    r"|充\s*\d+|送\s*\d+|返\s*\d+|奖励\s*\d+"
)


def _icon(slug: str, colour: tuple[int, int, int], size: int) -> Image.Image | None:
    """Load an authorised, checksum-verified label icon in a brand tint."""

    image_path = _ICON_LIBRARY_DIR / f"icon-tabler-{slug}.png"
    try:
        metadata = json.loads(
            image_path.with_suffix(".json").read_text(encoding="utf-8")
        )
        if metadata.get("authorization_status") != "confirmed":
            return None
        digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
        if str(metadata.get("sha256") or "").casefold() != digest.casefold():
            return None
        with Image.open(image_path) as raw:
            icon = raw.convert("RGBA").resize((size, size), Image.LANCZOS)
    except (OSError, ValueError):
        return None
    tinted = Image.new("RGBA", icon.size, (*colour, 0))
    tinted.putalpha(icon.split()[-1])
    return tinted


def _main_and_sub(text: str) -> tuple[str, str]:
    """Split a beat into its number/keyword and its trailing qualifier."""

    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return "", ""
    # Prefer the amount that carries a real unit; the number wins and any
    # leading action word ("送", "返") becomes the qualifier.  Matching the verb
    # phrase first produced "送120" as the headline with a stranded "元".
    unit_amount = _AMOUNT_WITH_UNIT.search(compact)
    if unit_amount:
        value = f"{unit_amount.group(1)}{unit_amount.group(2)}"
        leading = compact[: unit_amount.start()]
        action = ""
        for word in ("充值", "储值", "送", "赠", "返", "奖励"):
            if leading.endswith(word):
                action = word
                break
        tail = compact[unit_amount.end() :][:3]
        sub_text = (action + tail) if action else tail
        return value, sub_text
    offer = _OFFER_NUMBER.search(compact)
    if offer:
        return re.sub(r"\s+", "", offer.group(0)), compact[offer.end() :][:4]
    match = _NUMBER_TOKEN.search(compact)
    if match:
        token = re.sub(r"\s+", "", match.group(0))
        # A leading "2026年" with no amount anywhere is a date: keep it out of
        # the burst and fall through to the keyword path instead.
        if _YEAR_AT_START.match(compact) and match.start() == 0:
            year_end = _YEAR_AT_START.match(compact).end()
            remainder = compact[year_end:]
            if not _AMOUNT_WITH_UNIT.search(remainder):
                return _keyword_and_sub(remainder or compact)
        tail = compact[match.end() :][:4]
        return token, tail
    return _keyword_and_sub(compact)


def _keyword_and_sub(compact: str) -> tuple[str, str]:
    for size in (4, 3, 2):
        if len(compact) >= size:
            return compact[:size], compact[size : size + 4]
    return compact, ""


def _sort_key(item: dict) -> float:
    """Sort by start time, tolerating a malformed field from the model.

    A provider can return ``start="bad"``; sorting on ``float(...)`` directly
    raised before the per-segment guard below ever ran, so one bad field killed
    the whole component layer and therefore the export.
    """

    try:
        return float(item.get("start") or 0)
    except (TypeError, ValueError):
        return float("inf")


def build_motion_graphic_events(
    annotations: Any,
    *,
    duration_seconds: float,
    max_events: int = 4,
    min_gap_seconds: float = 3.0,
    placement: Any = None,
) -> list[dict[str, Any]]:
    """Derive grounded motion-graphic events from semantic annotations.

    ``placement`` is the measured layout (see ``placement_from_measurement``).
    When it is absent the events fall back to the documented default band, so a
    failed measurement degrades the layout instead of breaking the export.
    """

    if duration_seconds <= 0:
        return []
    events: list[dict[str, Any]] = []
    last_start = -1000.0
    style_use_count: dict[str, int] = {}
    # A style may repeat when the copy really has several comparable beats: a
    # lesson with four key numbers must not show a component for only the first.
    # Variety is kept by capping repeats and by the minimum gap, not by banning
    # a style outright.
    max_per_style = max(1, (max_events + 1) // 2)
    for index, segment in enumerate(
        sorted(
            (item for item in annotations or [] if isinstance(item, dict)),
            key=_sort_key,
        )
    ):
        if len(events) >= max_events:
            break
        source_text = str(
            segment.get("source_text")
            or segment.get("text")
            or segment.get("semantic_text")
            or ""
        ).strip()
        if not source_text:
            continue
        roles = {
            str(role).strip().upper()
            for role in segment.get("semantic_roles") or []
            if str(role).strip()
        }
        if not roles:
            single = str(segment.get("semantic_role") or "").strip().upper()
            if single:
                roles.add(single)
        style = next(
            (name for name, accepted in _ROLE_STYLE if roles & accepted), ""
        )
        if not style:
            # Fall back to the sentence itself: a director that only emits
            # ``LOW_INFORMATION`` must not cost the clip every visual beat.
            style = next(
                (
                    name
                    for name, pattern in _TEXT_EVIDENCE
                    if pattern.search(source_text)
                ),
                "",
            )
        if not style:
            continue
        if style == "success_badge" and (
            _NEGATION.search(source_text)
            or _NEGATIVE_RECOMMENDATION.search(source_text)
        ):
            # A success mark must never assert a negated or rejected sentence.
            continue
        if (
            style == "number_burst"
            and _NEGATED_NUMBER.search(source_text)
        ):
            # A number inside a negated clause is not a gain.  The warning rule
            # is listed first, but a phrase can match both patterns and the
            # numeric one may still win, which would print a bright yellow
            # number burst over "不是增长30%" and invert the meaning.  Demote to
            # the warning mark instead of emitting the numeric component.
            style = "warning_badge"
        if style == "number_burst" and _STANDARD_NUMBER.search(source_text):
            # "记彼18483-2001" is a standard code.  A burst would present it as
            # a headline quantity, which is a fact error rather than a styling
            # choice; keep the beat but hand it to the caption-side pill.
            style = "info_pill"
        if style_use_count.get(style, 0) >= max_per_style:
            continue
        try:
            start = max(0.0, float(segment.get("start") or 0))
            end = min(float(segment.get("end") or start), duration_seconds)
        except (TypeError, ValueError):
            continue
        if end <= start or start - last_start < min_gap_seconds:
            continue
        # ``roles`` may legitimately be empty when the beat was matched from the
        # sentence text; indexing it crashed with IndexError on ordinary copy.
        role_label = sorted(roles)[0] if roles else "TEXT_EVIDENCE"
        main_text, sub_text = _main_and_sub(
            str(segment.get("semantic_text") or source_text)
        )
        if not main_text:
            continue
        slug, _accent, animation, sfx_profile = _STYLE_SPEC[style]
        if style == "info_pill":
            slug = _pill_icon(roles, source_text)
        geometry = motion_graphic_geometry(style, placement=placement)
        event_id = f"mg-{index + 1:02d}-{style}"
        events.append(
            {
                "event_id": event_id,
                "type": "semantic_sticker",
                "render_class": "motion_graphic",
                "style_id": style,
                "geometry": geometry,
                "asset_category": "custom_semantic_sticker",
                "start": round(start, 3),
                "end": round(end, 3),
                "semantic_text": main_text,
                "main_text": main_text,
                "sub_text": sub_text,
                "icon_id": slug,
                "animation": animation,
                "sfx_profile": sfx_profile,
                "sfx_required": True,
                "source_text": source_text,
                "semantic_role": role_label,
                "source_segment_index": segment.get("source_segment_index", index),
                "grounded_in_text": True,
                "importance": 0.85,
                "visual_intensity": 3,
                "reason": "语义角色对应的动效组件",
                "source": "VideoInsight project-owned motion graphic",
                "license": "project-owned-local-vector",
                "third_party_cost": 0,
                "side": "right" if len(events) % 2 == 0 else "left",
                "safe_area": "lower_caption_safe_band",
            }
        )
        last_start = start
        style_use_count[style] = style_use_count.get(style, 0) + 1
    return events


def _canvas_size(style: str, geometry: Any = None) -> tuple[int, int]:
    """Canvas size for a component, honouring any explicit placement."""

    if isinstance(geometry, dict) and geometry.get("width") and geometry.get("height"):
        return int(geometry["width"]), int(geometry["height"])
    return {
        "info_pill": (620, 150),
        "number_burst": (620, 300),
        "success_badge": (300, 300),
        "warning_badge": (300, 300),
    }[style]


def placement_from_measurement(
    layout: Any,
    *,
    frame_width: int = 720,
    frame_height: int = 1280,
) -> dict[str, Any]:
    """Derive per-style boxes from a measured subject and safe areas.

    This is the point of the measurement layer: the constants below only apply
    when nothing was measured.  Given a real observation, every component is
    placed inside an area the model reported as free, and sized to fit that
    area, so a different subject position or a wider shot yields a different --
    still correct -- layout.
    """

    result: dict[str, Any] = {
        "measured": False,
        "source": "",
        "boxes": {},
        "reason": "未提供测量结果",
    }
    if not isinstance(layout, Mapping):
        return result
    subject = layout.get("subject")
    zones = [
        zone
        for zone in (layout.get("safe_zones") or [])
        if isinstance(zone, Mapping)
    ]
    if not isinstance(subject, Mapping) or not zones:
        result["reason"] = "测量结果缺少主体框或安全区"
        return result

    def to_pixels(zone: Mapping[str, Any]) -> tuple[int, int, int, int] | None:
        try:
            left = float(zone["left"])
            top = float(zone["top"])
            right = float(zone["right"])
            bottom = float(zone["bottom"])
        except (KeyError, TypeError, ValueError):
            return None
        if right <= left or bottom <= top:
            return None
        return (
            round(left * frame_width),
            round(top * frame_height),
            round(right * frame_width),
            round(bottom * frame_height),
        )

    pixel_zones = [box for box in (to_pixels(zone) for zone in zones) if box]
    if not pixel_zones:
        result["reason"] = "安全区坐标不可用"
        return result

    # The subject box is the hard constraint, not a hint.  The model can report
    # a "safe" top band and a subject that already reaches into it; trusting the
    # zone there would put a component over the speaker's head.  Subtract the
    # subject from every candidate and keep only what genuinely remains.
    try:
        subject_box = (
            round(float(subject["left"]) * frame_width),
            round(float(subject["top"]) * frame_height),
            round(float(subject["right"]) * frame_width),
            round(float(subject["bottom"]) * frame_height),
        )
    except (KeyError, TypeError, ValueError):
        subject_box = None

    def subtract(
        box: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int] | None:
        """Return the largest sub-rectangle of ``box`` clear of the subject."""

        left, top, right, bottom = box
        if subject_box is None:
            return box
        s_left, s_top, s_right, s_bottom = subject_box
        if right <= s_left or left >= s_right or bottom <= s_top or top >= s_bottom:
            return box
        candidates = []
        if s_top > top:
            candidates.append((left, top, right, s_top))
        if s_bottom < bottom:
            candidates.append((left, s_bottom, right, bottom))
        if s_left > left:
            candidates.append((left, top, s_left, bottom))
        if s_right < right:
            candidates.append((s_right, top, right, bottom))
        viable = [
            item
            for item in candidates
            if item[2] - item[0] >= round(frame_width * 0.08)
            and item[3] - item[1] >= round(frame_height * 0.04)
        ]
        if not viable:
            return None
        # Prefer the tallest remaining strip rather than the largest area: a
        # full-height sliver beside the subject is more useful for a component
        # than a shallow band pinned to the frame edge.
        return max(viable, key=lambda item: (item[3] - item[1], item[2] - item[0]))

    clear_zones = [
        box for box in (subtract(zone) for zone in pixel_zones) if box is not None
    ]
    if not clear_zones:
        result["reason"] = "测量到的安全区都被主体占用"
        return result

    # A measurement that leaves only a sliver is worse than the documented
    # default: a 58px-wide component is unreadable, while the constant band is
    # at least a deliberate, reviewed layout.  Require room for a legible mark
    # before trusting the observation.
    usable = [
        box
        for box in clear_zones
        if (box[2] - box[0]) >= round(frame_width * 0.20)
        and (box[3] - box[1]) >= round(frame_height * 0.08)
    ]
    if not usable:
        result["reason"] = (
            "测量到的可用空间不足以放置可读组件，使用默认安全带"
        )
        return result

    def size_of(box: tuple[int, int, int, int]) -> tuple[int, int]:
        return box[2] - box[0], box[3] - box[1]

    # Widest region takes a full-width pill; the tallest narrow region takes the
    # square marks.  Ranking by shape rather than by order keeps this stable
    # even if the model lists its zones differently between calls.
    horizontal = max(usable, key=lambda box: size_of(box)[0])
    vertical = max(usable, key=lambda box: size_of(box)[1])

    def place(style: str, box: tuple[int, int, int, int]) -> dict[str, int]:
        left, top, right, bottom = box
        width, height = size_of(box)
        if style == "info_pill":
            height = min(height, 150)
            width = min(width, round(frame_width * 0.70))
        elif style == "number_burst":
            height = min(height, 300)
            width = min(width, round(frame_width * 0.66))
        else:
            side = min(width, height)
            side = min(side, round(frame_width * 0.34))
            width = height = side
        # Centre the component inside the free area it was given.
        return {
            "x": left + max(0, (size_of(box)[0] - width) // 2),
            "y": top + max(0, (size_of(box)[1] - height) // 2),
            "width": width,
            "height": height,
        }

    result["measured"] = True
    result["source"] = str(layout.get("source") or "measurement")
    result["subject"] = subject
    result["boxes"] = {
        "info_pill": place("info_pill", horizontal),
        "number_burst": place("number_burst", horizontal),
        "success_badge": place("badge", vertical),
        "warning_badge": place("badge", vertical),
    }
    result["reason"] = "已按测量结果放置组件"
    return result


def motion_graphic_geometry(
    style: str,
    *,
    frame_width: int = 720,
    frame_height: int = 1280,
    placement: Any = None,
) -> dict[str, int]:
    """Return an explicit placement for one component.

    When ``placement`` carries a measurement, the box comes from observation.
    Otherwise the documented constants apply -- a conservative band between the
    face and the subtitle of the reference portrait framing, used only when
    nothing could be measured.
    """

    if isinstance(placement, Mapping) and placement.get("measured"):
        box = (placement.get("boxes") or {}).get(style)
        if isinstance(box, Mapping) and box.get("width") and box.get("height"):
            return {
                "x": int(box["x"]),
                "y": int(box["y"]),
                "width": int(box["width"]),
                "height": int(box["height"]),
            }

    # Bounds, from the same measurements the caption renderer uses: the
    # protected face box ends at 0.60H and the burned subtitle band occupies
    # 0.757H-0.918H, so its glyph tops start near 0.757H.  Leaving the band at
    # 0.735H wasted 2% of the frame and made components unnecessarily small.
    face_bottom = round(frame_height * 0.602)
    subtitle_top = round(frame_height * 0.757)
    gap = round(frame_height * 0.008)
    band_top = face_bottom + gap
    band_bottom = subtitle_top - gap
    band_height = max(80, band_bottom - band_top)
    margin = round(frame_width * 0.05)

    if style == "info_pill":
        width = round(frame_width * 0.70)
        height = min(150, band_height)
    elif style == "number_burst":
        # Tie the width to the band height so the shape stays compact around the
        # number.  At 66% width and 14% height the burst stretched into a flat
        # smear with the digits floating on it.  Take the width the band allows
        # rather than a rounded shape: at phone size the band is the binding
        # constraint and a wider mark reads better than a small round one.
        height = band_height
        width = max(round(height * 1.8), round(frame_width * 0.62))
        width = min(width, round(frame_width * 0.68))
    else:
        width = min(round(frame_width * 0.30), band_height)
        height = width
    top = band_top + max(0, (band_height - height) // 2)
    return {
        "x": frame_width - width - margin,
        "y": top,
        "width": width,
        "height": height,
    }


def geometry_is_safe(
    geometry: Any,
    *,
    frame_width: int = 720,
    frame_height: int = 1280,
    subject: Any = None,
) -> bool:
    """Return whether a placement clears the subject and the subtitle band.

    When ``subject`` carries a measured box, that box is the constraint; the
    constants below are only the fallback for an unmeasured clip.  A component
    that cannot find room must be skipped rather than stacked on the speaker or
    on the text.
    """

    if not isinstance(geometry, dict):
        return False
    try:
        left = int(geometry["x"])
        top = int(geometry["y"])
        right = left + int(geometry["width"])
        bottom = top + int(geometry["height"])
    except (KeyError, TypeError, ValueError):
        return False
    if left < 0 or top < 0 or right > frame_width or bottom > frame_height:
        return False
    face_box = None
    if isinstance(subject, Mapping):
        try:
            face_box = (
                round(float(subject["left"]) * frame_width),
                round(float(subject["top"]) * frame_height),
                round(float(subject["right"]) * frame_width),
                round(float(subject["bottom"]) * frame_height),
            )
        except (KeyError, TypeError, ValueError):
            face_box = None
    if face_box is None:
        face_box = (
            round(frame_width * 0.12),
            round(frame_height * 0.10),
            round(frame_width * 0.90),
            round(frame_height * 0.60),
        )
    subtitle = (
        round(frame_width * 0.05),
        round(frame_height * 0.757),
        round(frame_width * 0.95),
        round(frame_height * 0.95),
    )

    def overlaps(box: tuple[int, int, int, int]) -> bool:
        return not (
            right <= box[0] or left >= box[2] or bottom <= box[1] or top >= box[3]
        )

    return not overlaps(face_box) and not overlaps(subtitle)


def render_motion_graphic(event: Any, output_path: Path) -> bool:
    """Draw one motion graphic.  Returns False when the style is not ours."""

    style = str(event.get("style_id") or "")
    if style not in MOTION_GRAPHIC_STYLES:
        return False
    slug = str(event.get("icon_id") or _STYLE_SPEC[style][0])
    _default_slug, accent, _animation, _sfx = _STYLE_SPEC[style]
    width, height = _canvas_size(style, event.get("geometry"))
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    main_text = str(event.get("main_text") or event.get("semantic_text") or "").strip()
    sub_text = str(event.get("sub_text") or "").strip()
    if not main_text:
        return False

    # Contrast strategy: the frame is dark (black shirt, dark shelf) and the
    # subtitle plate is already dark, so a third dark plate disappears.  Every
    # component therefore carries a filled accent plate plus a white keyline,
    # which is what makes the reference cut's marks readable at phone size.
    white = (255, 255, 255, 255)
    keyline = max(4, round(min(width, height) * 0.035))

    if style == "info_pill":
        radius = height // 2
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1), radius=radius,
            fill=(*accent, 250), outline=white,
            width=keyline,
        )
        # Size the wording to the space that actually remains after the icon and
        # both paddings, and shrink the icon with it.  A measured safe area can
        # be narrow (a 0.1W side strip), and a fixed icon size then left almost
        # no room for the text, which got clipped mid-number.
        probe = ImageFont.truetype(str(_FONT_PATH), 100)
        measured = max(1.0, draw.textlength(main_text, font=probe))
        icon_ratio = 0.46
        for _ in range(6):
            icon_size = round(height * icon_ratio)
            text_x = (
                round(height * 0.24) + icon_size + round(height * 0.16)
            )
            available = width - text_x - round(height * 0.20)
            main_size = int(
                max(20, min(round(height * 0.52), 100 * max(40, available) / measured))
            )
            if 100 * max(40, available) / measured >= height * 0.34 or icon_ratio <= 0.26:
                break
            icon_ratio -= 0.04
        icon = _icon(slug, (255, 255, 255), icon_size)
        if icon is not None:
            canvas.alpha_composite(
                icon, (round(height * 0.24), (height - icon.height) // 2)
            )
        main_font = ImageFont.truetype(str(_FONT_PATH), main_size)
        # Never render text the plate cannot hold.  Clipping a numbered
        # identifier mid-string ("18483-2") states something false, so shorten
        # the label to what fits at a readable size instead.
        room = max(40, width - text_x - round(height * 0.20))
        if draw.textlength(main_text, font=main_font) > room:
            trimmed = main_text
            while trimmed and draw.textlength(trimmed, font=main_font) > room:
                trimmed = trimmed[:-1]
            if len(trimmed) < 3:
                return False
            main_text = trimmed
            sub_text = ""
        # Dark ink on the bright plate keeps the wording crisp; white text on a
        # saturated fill loses its edges.
        draw.text((text_x, height // 2), main_text, font=main_font,
                  anchor="lm", fill=(16, 18, 26, 255))
        if sub_text:
            small = ImageFont.truetype(str(_FONT_PATH), round(height * 0.26))
            draw.text(
                (text_x + draw.textlength(main_text, font=main_font) + 12, height // 2 + round(height * 0.06)),
                sub_text, font=small, anchor="lm", fill=(40, 44, 54, 245),
            )
    elif style == "number_burst":
        cx, cy = width // 2, height // 2
        import math

        # Fewer, deeper spikes read as an explosion; twelve shallow points read
        # as a starfish.  Alternating long/short radii with sharp vertices.
        points = 14
        burst = []
        for index in range(points * 2):
            angle = math.pi * index / points
            radius = 0.50 if index % 2 == 0 else 0.29
            burst.append(
                (
                    cx + round(width * 0.5 * radius * math.cos(angle)),
                    cy + round(height * 0.5 * radius * math.sin(angle)),
                )
            )
        draw.polygon(burst, fill=(*accent, 255))
        # White keyline drawn as an outline on the filled shape so no stray
        # shards appear behind the glyphs.
        draw.line([*burst, burst[0]], fill=(255, 255, 255, 255), width=max(5, round(width * 0.014)), joint="curve")
        # Fit the number inside the burst on both axes.  Sizing on width alone
        # let the glyphs grow taller than the plate, so the burst looked like
        # debris behind oversized text instead of a badge with a number on it.
        probe = ImageFont.truetype(str(_FONT_PATH), 100)
        natural = max(1.0, draw.textlength(main_text, font=probe))
        by_width = 100 * (width * 0.54) / natural
        # Keep the digits clearly inside the plate: the reference number card
        # shows the figure occupying roughly a third of its height.
        by_height = (height * 0.40) / 1.18
        font_size = int(max(30, min(by_width, by_height, 190)))
        font = ImageFont.truetype(str(_FONT_PATH), font_size)
        draw.text((cx, cy), main_text, font=font, anchor="mm",
                  fill=(16, 18, 26, 255), stroke_width=5, stroke_fill=white)
        if sub_text:
            small = ImageFont.truetype(
                str(_FONT_PATH), max(18, min(round(height * 0.17), font_size // 3))
            )
            draw.text((cx, cy + round(height * 0.32)), sub_text, font=small, anchor="mm",
                      fill=(16, 18, 26, 255), stroke_width=4, stroke_fill=white)
    else:  # success_badge / warning_badge
        cx, cy = width // 2, height // 2
        # A badge may carry a number ("提升20分" -> 20).  Dropping it leaves the
        # subtitle to say the number while the component that exists to
        # emphasise it shows only a tick, which reads as a missing beat.
        numeric = re.sub(r"\s+", "", str(event.get("main_text") or ""))
        has_number = bool(re.search(r"\d", numeric))
        if not has_number:
            # Badges are marks.  A clipped clause under a check mark reads as a
            # mistake, so a badge without a number carries no text at all.
            numeric = ""
        disc_r = round(min(width, height) * (0.32 if has_number else 0.40))
        cy = cy - (round(height * 0.12) if has_number else 0)
        draw.ellipse((cx - disc_r, cy - disc_r, cx + disc_r, cy + disc_r),
                     fill=(*accent, 245), outline=(250, 250, 252, 250),
                     width=max(4, disc_r // 12))
        icon = _icon(slug, (255, 255, 255), round(disc_r * 1.15))
        if icon is None:
            return False
        canvas.alpha_composite(icon, (cx - icon.width // 2, cy - icon.height // 2))
        if has_number:
            number_font = ImageFont.truetype(
                str(_FONT_PATH), round(height * 0.34)
            )
            draw.text(
                (cx, height - round(height * 0.16)),
                numeric,
                font=number_font,
                anchor="mm",
                fill=(*accent, 255),
                stroke_width=5,
                stroke_fill=(18, 20, 28, 240),
            )
    canvas.save(output_path)
    return True
