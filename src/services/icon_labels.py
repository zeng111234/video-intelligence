"""Compose small business-icon labels for editorial sticker events.

Spec source: ``assets/downloads/business-icons-20260910/AI接入说明.md``.

The guide is explicit that these outline icons are **label furniture, not
stickers**: the icon occupies roughly 15-25% of the label width, the word or
number is the subject, and the colour comes from the project brand tokens
rather than the icon's own palette.  It also forbids a few defaults that this
module therefore never does:

* no icon is used on a negated or cancelled sentence in the "success" sense;
* no factory/office/computer icon is inserted merely because the copy mentions
  customers, enterprises or marketing;
* nothing is drawn outside the label, so the speaker and the subtitle band stay
  clear.

Icons are the MIT-licensed Tabler outline PNGs; provenance is recorded in the
sidecar metadata written by ``scripts/integrate_business_icons.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

_ICON_LIBRARY_DIR = Path(__file__).resolve().parents[2] / "assets" / "icons"
_FONT_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "fonts"
    / "SourceHanSerifCN-Heavy.otf"
)

# Brand tokens, matching SUBTITLE_EMPHASIS_COLOR_* in video_editor_workflow.
_NUMBER = (255, 209, 102)
_METHOD = (255, 159, 104)
_CONFLICT = (251, 113, 133)
_POSITIVE = (88, 208, 142)
_NEUTRAL = (235, 245, 255)

# Editorial kind -> (icon slug, accent colour).  Mirrors the guide's table:
# numbers get a coin/percentage mark, places a pin, risk a triangle, results a
# check, customers/documents a light annotation.
_KIND_STYLE: dict[str, tuple[str, tuple[int, int, int]]] = {
    "number": ("coin", _NUMBER),
    "offer_compare": ("discount", _NUMBER),
    "process": ("route", _METHOD),
    "positive": ("circle-check", _POSITIVE),
    "negative": ("alert-triangle", _CONFLICT),
    "cta": ("arrow-right", _METHOD),
}

# A conclusion mark must never land on a sentence that cancels or negates it.
_NEGATION = re.compile(
    r"别让|别把|不要让|不要把|不是|并非|没有|取消|不能|不可|禁止|避免|小心|风险"
)


def _short_label(text: str, limit: int = 6) -> str:
    """Return the key fragment for a label -- never the whole subtitle line.

    A label sits above a subtitle that already prints the full sentence, so
    repeating that sentence would print the same words twice on one frame.  The
    label carries only the beat: a number with its unit, or the single
    strongest word.
    """

    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return ""
    # A grounded number (with its unit, percent sign or amount partner) is the
    # beat itself, and stays short enough to read at phone size.
    for match in re.finditer(r"\d+(?:\.\d+)?[%％]?", compact):
        token = match.group(0)
        tail = compact[match.end() : match.end() + 1]
        if tail and tail in "元块万亿折倍天次个月张个人家套步项":
            token += tail
        if len(token) >= 2:
            return token
    if len(compact) <= limit:
        return compact
    try:
        import jieba.posseg as jieba_posseg

        units = [
            re.sub(r"[^\w\u4e00-\u9fff%．.]+", "", str(token.word or ""))
            for token in jieba_posseg.lcut(compact, HMM=True)
        ]
    except (ImportError, LookupError, OSError, TypeError, ValueError):
        units = []
    usable = [unit for unit in units if 2 <= len(unit) <= limit]
    if usable:
        return max(usable, key=len)
    return compact[:limit]


def _icon_for(kind: str, text: str) -> tuple[str, tuple[int, int, int]] | None:
    """Return the icon slug and accent for a beat, or ``None`` to skip it."""

    style = _KIND_STYLE.get(kind)
    if style is None:
        return None
    slug, accent = style
    if accent == _POSITIVE and _NEGATION.search(str(text or "")):
        # The guide forbids a success check on a negative statement.
        return None
    return slug, accent


def _load_icon(slug: str) -> Image.Image | None:
    """Load an authorised label icon, verifying its published checksum."""

    image_path = _ICON_LIBRARY_DIR / f"icon-tabler-{slug}.png"
    metadata_path = image_path.with_suffix(".json")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if metadata.get("authorization_status") != "confirmed":
        return None
    try:
        digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
        if str(metadata.get("sha256") or "").casefold() != digest.casefold():
            return None
        with Image.open(image_path) as icon:
            return icon.convert("RGBA")
    except (OSError, ValueError):
        return None


def _tint(icon: Image.Image, colour: tuple[int, int, int]) -> Image.Image:
    """Recolour a monochrome outline icon to a brand token."""

    tinted = Image.new("RGBA", icon.size, (*colour, 0))
    alpha = icon.split()[-1]
    tinted.putalpha(alpha)
    return tinted


def compose_icon_label(
    *,
    kind: str,
    text: str,
    output_path: Path,
    label_width: int = 560,
    label_height: int = 132,
) -> bool:
    """Draw one label: rounded plate + brand-tinted icon + word/number.

    Returns ``False`` when the beat must not carry an icon (negated conclusions)
    or the icon is unavailable, so the caller can fall back to the plain
    vector/text treatment.
    """

    picked = _icon_for(kind, text)
    if picked is None:
        return False
    slug, accent = picked
    icon = _load_icon(slug)
    if icon is None:
        return False

    label = _short_label(text)
    if not label:
        return False

    canvas = Image.new("RGBA", (label_width, label_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    radius = label_height // 2
    draw.rounded_rectangle(
        (0, 0, label_width - 1, label_height - 1),
        radius=radius,
        fill=(12, 18, 28, 214),
        outline=(*accent, 235),
        width=max(3, label_height // 26),
    )

    # Icon occupies ~15-25% of the label width, per the integration guide.
    icon_side = max(28, round(label_height * 0.46))
    tinted = _tint(icon.resize((icon_side, icon_side), Image.LANCZOS), accent)
    icon_x = round(label_height * 0.34)
    icon_y = (label_height - icon_side) // 2
    canvas.alpha_composite(tinted, (icon_x, icon_y))

    font = ImageFont.truetype(str(_FONT_PATH), max(20, round(label_height * 0.44)))
    text_x = icon_x + icon_side + round(label_height * 0.24)
    available = label_width - text_x - round(label_height * 0.34)
    while label and draw.textlength(label, font=font) > available and len(label) > 2:
        label = label[:-1]
    draw.text(
        (text_x, label_height // 2),
        label,
        font=font,
        anchor="lm",
        fill=(248, 250, 252, 250),
    )
    canvas.save(output_path)
    return True


def available_label_icons() -> list[str]:
    """Return the slugs whose metadata is present and authorised."""

    try:
        metadata_files = sorted(_ICON_LIBRARY_DIR.glob("icon-tabler-*.json"))
    except OSError:
        return []
    slugs: list[str] = []
    for metadata_path in metadata_files:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if metadata.get("authorization_status") == "confirmed":
            slugs.append(str(metadata.get("name") or ""))
    return sorted(slug for slug in slugs if slug)


__all__: list[Any] = ["compose_icon_label", "available_label_icons"]
