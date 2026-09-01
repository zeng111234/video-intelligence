"""Cloud video-editor contracts, pricing, and deterministic safety rules.

This module intentionally has no FastAPI or vendor SDK dependency.  The API
layer can serialize the Pydantic models directly, while provider adapters stay
replaceable and testable without making paid calls.
"""

from __future__ import annotations

import io
import itertools
import math
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import jieba
from pydantic import BaseModel, ConfigDict, Field, model_validator


PRICE_VERSION = "aliyun-cn-mainland-2026-07-29-safe-rough-cut"
QUOTE_TTL_SECONDS = 15 * 60
FUN_ASR_CNY_PER_SECOND = Decimal("0.00022")
QWEN_FLASH_INPUT_CNY_PER_MILLION_TOKENS = Decimal("0.15")
QWEN_FLASH_OUTPUT_CNY_PER_MILLION_TOKENS = Decimal("1.5")
MPS_CNY_PER_OUTPUT_MINUTE = {
    "720p": Decimal("0.0326"),
    "1080p": Decimal("0.0651"),
}
MPS_WATERMARK_CNY_PER_REQUEST = Decimal("0.0001")
BGM_VOICEOVER_CATEGORIES = (
    "理性干货",
    "情绪共鸣",
    "故事叙事",
    "商业表达",
    "科技未来",
    "轻松日常",
    "励志成长",
    "悬念揭秘",
    "通用口播",
)
BGM_ENERGY_LEVELS = ("克制", "平稳", "有推动感")
CAPTION_EMPHASIS_KINDS = (
    "number",
    "benefit",
    "method",
    "warning",
    "keyword",
    "result",
    "cta",
)
_CAPTION_KINETIC_SEMANTIC_COLORS = {
    "number": "#FFD166",
    "benefit": "#FFB86B",
    "warning": "#FF7A70",
    "method": "#FF9F68",
    "result": "#FF8A7A",
    "cta": "#FFC857",
    "keyword": "#FFC857",
    "default": "#FFE7C2",
}
_CAPTION_KINETIC_STYLE_IDS = (
    "slam",
    "bounce",
    "stamp",
    "marker",
    "underline",
    "shake",
)
OPENING_STYLE_IDS = ("suspense_reveal", "story_unfold", "number_focus")
OPENING_SOUND_EFFECT_IDS = ("soft_whoosh", "soft_page_turn", "soft_chime")
MIN_SILENCE_SECONDS = 1.5
SILENCE_EDGE_PADDING_SECONDS = 0.45
HEAD_TAIL_SILENCE_SECONDS = 0.8
HEAD_TAIL_PADDING_SECONDS = 0.25
# MPS MergeConfigUrl has a tighter practical limit than the editor's old
# preview-only interval limit.  Keep the final render manifest conservative.
MAX_KEEP_RANGES = 50
MAX_REMOVE_RANGES = MAX_KEEP_RANGES - 1
_COST_PRECISION = Decimal("0.000001")
DEFAULT_VISUAL_STYLE_ID = (
    "business_talking_head_v9.1-smart-opening-clean-hook-speed-1.15"
)
DEFAULT_PLAYBACK_RATE = 1.15
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRAND_TITLE_FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "SourceHanSerifCN-Heavy.otf"
_CAPTION_BREAK_CHARACTERS = frozenset("，。！？；：、,.!?;:“”‘’（）()【】[]《》…—")
_NUMERIC_PUNCTUATION = frozenset(".,:")
_CAPTION_NUMERIC_ATOM_RE = re.compile(
    r"\d+(?:[.,]\d+)*(?:[%％元块万亿千百十公里米厘米分钟秒个家人套条次岁年月天斤倍折号点]+)?"
)
_CAPTION_BREAK_BEFORE_TOKENS = (
    "不只是",
    "还是",
    "因为",
    "所以",
    "但是",
    "不过",
    "而且",
    "然后",
    "如果",
    "虽然",
    "为了",
    "其实",
    "基本",
    "通常",
    "一般",
    "几乎",
    "结果",
    "现在",
    "大量",
    "少量",
    "很多",
    "有些",
    "倒闭",
    "取代",
    "替代",
    "增长",
    "减少",
    "出现",
    "成为",
    "变成",
    "开始",
    "进入",
    "通过",
    "面对",
    "发现",
    "需要",
    "可以",
    "不能",
    "没有",
    "不是",
    "就是",
    "已经",
    "正在",
    "也是",
    "仍然",
    "被",
    "把",
    "让",
    "比",
    "待",
)
_CAPTION_BREAK_AFTER_TOKENS = (
    "的话",
    "以后",
    "之前",
    "之后",
    "时候",
    "一来",
    "说到底",
)
# Domain-specific compounds must come from the reviewed transcript or plan,
# not from a sample-driven global list.  The tokenizer below still uses jieba
# as the primary lexical source; an injected glossary only protects terms for
# the current media item.
_CAPTION_CORE_COMPOUND_WORDS: tuple[str, ...] = ()
_CAPTION_BAD_LINE_ENDINGS = (
    "的",
    "地",
    "得",
    "了",
    "着",
    "过",
    "和",
    "与",
    "及",
    "或",
    "跟",
    "比",
    "把",
    "被",
    "让",
    "给",
    "向",
    "对",
    "在",
    "从",
    "为",
    "还",
    "就",
    "才",
    "都",
    "又",
    "再",
    "更",
    "最",
    "很",
    "太",
    "也",
    "挺",
    "正",
    "将",
    "要",
    "会",
    "能",
    "可",
    "无",
    "不",
    "没",
    "未",
    "非",
    "主动",
    "自动",
    "直接",
    "立刻",
    "马上",
    "基本",
    "通常",
    "一般",
    "几乎",
    "自然",
    "通过",
    "想",
    "用",
    "办",
    "拿",
    "加",
    "送",
    "发",
    "搞",
    "打",
    "第",
    "每",
    "各",
    "这",
    "那",
    "此",
    "其",
    "一",
    "两",
    "几",
    "多",
    "个",
    "位",
    "名",
    "家",
    "户",
    "只",
    "条",
    "件",
    "张",
    "种",
    "次",
    "套",
    "台",
    "份",
    "部",
    "本",
    "辆",
)
_CAPTION_BAD_LINE_STARTS = (
    "的",
    "地",
    "得",
    "了",
    "着",
    "过",
    "们",
    "吗",
    "呢",
    "吧",
    "啊",
    "呀",
    "嘛",
    "个",
    "位",
    "名",
    "家",
    "户",
    "只",
    "条",
    "件",
    "张",
    "种",
    "次",
    "套",
    "台",
    "份",
    "部",
    "本",
    "辆",
    "斤",
    "米",
    "块",
    "元",
)


def _normalize_caption_glossary(value: object) -> tuple[str, ...]:
    """Normalize per-transcript terms without mutating jieba's global state."""

    if value is None:
        return ()
    if isinstance(value, str):
        candidates: Sequence[object] = re.split(r"[,，;；\n]", value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        candidates = value
    else:
        return ()
    terms: set[str] = set()
    for candidate in candidates:
        term = re.sub(r"\s+", "", str(candidate or ""))
        if len(term) >= 2 and re.search(r"[\w\u4e00-\u9fff]", term):
            terms.add(term)
    return tuple(sorted(terms, key=lambda item: (-len(item), item)))
_CAPTION_MIN_DURATION_SECONDS = 0.9
_CAPTION_MAX_DURATION_SECONDS = 2.4


def visual_style_spec(output_profile: str | "OutputProfile") -> dict[str, Any]:
    """Return the public layout contract shared by preview and cloud render."""

    profile = str(output_profile)
    if profile.startswith("OutputProfile."):
        profile = profile.rsplit(".", 1)[-1].replace("HD_", "").lower()
    if profile == "720p":
        width, height = 720, 1280
    elif profile == "1080p":
        width, height = 1080, 1920
    else:
        raise CloudEditorError("输出档位仅支持 720p 或 1080p。")
    scale = width / 720
    return {
        "style_id": DEFAULT_VISUAL_STYLE_ID,
        "playback_rate": DEFAULT_PLAYBACK_RATE,
        "canvas": {"width": width, "height": height, "pixel_aspect_ratio": "1:1"},
        "title": {
            "visible_seconds": 2.5,
            "fade_in_ms": 0,
            "fade_out_ms": 0,
            "max_lines": 1,
            "max_chars_per_line": 12,
            "font_family": "Source Han Serif CN Heavy",
            "render_mode": "png_watermark",
            "font_size": round(44 * scale),
            "line_height": 1.1,
            "safe_top": round(84 * scale),
            "safe_left": round(30 * scale),
            "asset_width": round(660 * scale),
            "asset_height": round(72 * scale),
            "outline_width": max(1, round(1 * scale)),
            "shadow": max(1, round(1 * scale)),
            "color": "#FFFFFF",
        },
        "accent": {
            "color": "transparent",
            "width": 0,
            "height": 0,
            "gap": 0,
        },
        "subtitle": {
            "max_lines": 1,
            "max_chars_per_line": 11,
            "font_size": round(52 * scale),
            "safe_bottom": round(170 * scale),
            "outline_width": max(1, round(1 * scale)),
            "shadow": max(1, round(1 * scale)),
            "color": "#F8FAFC",
            "emphasis_color": "#FFE16A",
        },
    }


def build_business_talking_head_title_png(
    title: str,
    *,
    output_profile: str | "OutputProfile",
    font_path: str | Path = BRAND_TITLE_FONT_PATH,
) -> bytes:
    """Render the approved brand title as a transparent MPS image watermark."""

    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except ImportError as exc:
        raise CloudEditorError("缺少标题排版组件 Pillow，不能提交正式出片。") from exc

    font_file = Path(font_path)
    if not font_file.is_file():
        raise CloudEditorError("品牌标题字体文件缺失，不能提交正式出片。")
    spec = visual_style_spec(output_profile)
    title_style = spec["title"]
    lines = _display_lines(
        title,
        chars_per_line=title_style["max_chars_per_line"],
        max_lines=title_style["max_lines"],
        truncate=True,
    )
    if not lines:
        raise CloudEditorError("标题不能为空。")

    width = int(title_style["asset_width"])
    height = int(title_style["asset_height"])
    font_size = int(title_style["font_size"])
    scale = spec["canvas"]["width"] / 720
    font = ImageFont.truetype(str(font_file), font_size)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    line_step = round(font_size * float(title_style["line_height"]))
    text_x = round(8 * scale)
    text_y = round(2 * scale)
    shadow_offset = max(1, round(2 * scale))
    for index, line in enumerate(lines):
        y = text_y + index * line_step
        shadow_draw.text(
            (text_x + shadow_offset, y + shadow_offset),
            line,
            font=font,
            fill=(0, 0, 0, 160),
        )
    shadow_layer = shadow_layer.filter(
        ImageFilter.GaussianBlur(radius=max(1, round(2 * scale)))
    )
    image.alpha_composite(shadow_layer)

    draw = ImageDraw.Draw(image)
    outline_width = int(title_style["outline_width"])
    for index, line in enumerate(lines):
        draw.text(
            (text_x, text_y + index * line_step),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=outline_width,
            stroke_fill=(0, 0, 0, 205),
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, cents = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cents:02d}"


def _srt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(float(seconds) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _ass_escape(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\r", "")
        .replace("\n", r"\N")
    )


def _display_lines(
    text: str,
    *,
    chars_per_line: int,
    max_lines: int | None = None,
    truncate: bool = False,
) -> list[str]:
    clean = re.sub(r"\s+", "", text)
    if not clean:
        return []
    max_chars = chars_per_line * max_lines if max_lines else None
    if truncate and max_chars and len(clean) > max_chars:
        # A title is a short visual label, not a scrolling sentence.  Never
        # append an ellipsis that visually advertises a truncated headline;
        # the spoken subtitle remains the complete source of truth below.
        cutoff = max_chars
        for start, end in _caption_numeric_ranges(clean):
            if start < cutoff < end:
                cutoff = start if start > 0 else end
                break
        clean = clean[:cutoff]
    return _caption_wrap_lines(clean, chars_per_line=chars_per_line)


def _caption_wrap_lines(text: str, *, chars_per_line: int) -> list[str]:
    """Wrap captions without cutting a number away from its unit."""

    clean = re.sub(r"\s+", "", text)
    if not clean:
        return []
    numeric_ranges = _caption_numeric_ranges(clean)
    lines: list[str] = []
    cursor = 0
    while cursor < len(clean):
        limit = min(len(clean), cursor + max(1, chars_per_line))
        containing_numeric = next(
            ((start, end) for start, end in numeric_ranges if start < limit < end),
            None,
        )
        if containing_numeric is not None:
            numeric_start, numeric_end = containing_numeric
            limit = numeric_start if numeric_start > cursor else numeric_end
        if limit <= cursor:
            limit = min(len(clean), cursor + max(1, chars_per_line))
        lines.append(clean[cursor:limit])
        cursor = limit
    return lines


def _caption_display_lines(text: str, *, chars_per_line: int) -> list[str]:
    """Wrap a necessary two-line cue without leaving a one-character orphan."""

    clean = re.sub(r"\s+", "", text)
    if not clean or len(clean) <= chars_per_line:
        return [clean] if clean else []
    if len(clean) <= chars_per_line * 2:
        lower = max(1, len(clean) - chars_per_line)
        upper = min(chars_per_line, len(clean) - 1)
        semantic_after = [
            start + len(token)
            for token in _CAPTION_BREAK_BEFORE_TOKENS
            for start in range(len(clean))
            if clean.startswith(token, start)
            and lower <= start + len(token) <= upper
        ]
        candidates = semantic_after or list(range(lower, upper + 1))
        safe_candidates = [
            split_at
            for split_at in candidates
            if _caption_split_reads_naturally(clean, split_at)
        ]
        split_at = min(
            safe_candidates or candidates,
            key=lambda value: (abs(value - min(chars_per_line, len(clean) // 2)), -value),
        )
        if len(clean) - split_at == 1 and split_at > 1:
            split_at -= 1
        return [clean[:split_at], clean[split_at:]]
        return _caption_wrap_lines(clean, chars_per_line=chars_per_line)


def _wrap_ass_lines(lines: Sequence[str]) -> str:
    return r"\N".join(_ass_escape(line) for line in lines)


def _wrap_ass_text(text: str, *, chars_per_line: int) -> str:
    return _wrap_ass_lines(_display_lines(text, chars_per_line=chars_per_line))


def _is_numeric_caption_punctuation(
    characters: Sequence[str],
    index: int,
) -> bool:
    return (
        characters[index] in _NUMERIC_PUNCTUATION
        and index > 0
        and index + 1 < len(characters)
        and characters[index - 1].isdigit()
        and characters[index + 1].isdigit()
    )


def _caption_phrases(text: str) -> list[str]:
    characters = list(re.sub(r"\s+", "", text))
    phrases: list[str] = []
    current: list[str] = []
    for index, character in enumerate(characters):
        if (
            character in _CAPTION_BREAK_CHARACTERS
            and not _is_numeric_caption_punctuation(characters, index)
        ):
            if current:
                phrases.append("".join(current))
                current = []
            continue
        current.append(character)
    if current:
        phrases.append("".join(current))
    return phrases


def _clean_caption_text(text: str) -> str:
    return "".join(_caption_phrases(text))


def _caption_boundary_splits(piece: str) -> set[int]:
    boundaries: set[int] = set()
    for token in _CAPTION_BREAK_BEFORE_TOKENS:
        start = piece.find(token)
        while start >= 0:
            if start > 0:
                boundaries.add(start)
            start = piece.find(token, start + 1)
    for token in _CAPTION_BREAK_AFTER_TOKENS:
        start = piece.find(token)
        while start >= 0:
            end = start + len(token)
            if end < len(piece):
                boundaries.add(end)
            start = piece.find(token, start + 1)
    return boundaries


def _caption_word_splits(
    piece: str,
    *,
    caption_glossary: object = None,
) -> set[int]:
    """Return Chinese word boundaries so captions never cut through a word."""

    boundaries: set[int] = set()
    cursor = 0
    for token in _caption_lexical_units(piece, caption_glossary):
        cursor += len(token)
        if cursor < len(piece):
            boundaries.add(cursor)
    return boundaries


def _caption_numeric_ranges(text: str) -> list[tuple[int, int]]:
    """Return numeric + unit spans that must never be split in a caption."""

    clean = re.sub(r"\s+", "", text)
    return [
        (match.start(), match.end())
        for match in _CAPTION_NUMERIC_ATOM_RE.finditer(clean)
        if match.end() > match.start()
    ]


def _caption_split_is_inside_numeric(text: str, split_at: int) -> bool:
    return any(
        start < split_at < end
        for start, end in _caption_numeric_ranges(text)
    )


@lru_cache(maxsize=512)
def _caption_lexical_units_cached(
    piece: str,
    caption_glossary: tuple[str, ...],
) -> tuple[str, ...]:
    """Tokenize one text/glossary pair using jieba as the primary splitter."""

    clean = re.sub(r"\s+", "", piece)
    if not clean:
        return ()
    # Calling jieba.lcut on every remaining suffix makes a long 20--30 second
    # ASR sentence effectively quadratic and can stall a full-length export.
    # Tokenize once, then consume the cached tokens while retaining the
    # existing compound-word protection at each legal cursor.
    jieba_tokens = [token for token in jieba.lcut(clean, cut_all=False, HMM=True) if token]
    units: list[str] = []
    cursor = 0
    token_index = 0
    while cursor < len(clean):
        numeric = _CAPTION_NUMERIC_ATOM_RE.match(clean, cursor)
        if numeric:
            units.append(numeric.group(0))
            cursor = numeric.end()
            while token_index < len(jieba_tokens) and cursor >= sum(
                len(value) for value in jieba_tokens[: token_index + 1]
            ):
                token_index += 1
            continue
        compound = next(
            (
                value
                for value in caption_glossary
                if clean.startswith(value, cursor)
            ),
            None,
        )
        if compound:
            units.append(compound)
            cursor += len(compound)
            while token_index < len(jieba_tokens) and cursor >= sum(
                len(value) for value in jieba_tokens[: token_index + 1]
            ):
                token_index += 1
            continue
        token = jieba_tokens[token_index] if token_index < len(jieba_tokens) else ""
        if not token or not clean.startswith(token, cursor):
            token = clean[cursor]
        units.append(token)
        cursor += len(token)
        if token_index < len(jieba_tokens) and token == jieba_tokens[token_index]:
            token_index += 1
    return tuple(units)


def _caption_lexical_units(
    piece: str,
    caption_glossary: object = None,
) -> list[str]:
    """Return jieba units with only the current transcript glossary protected."""

    clean = re.sub(r"\s+", "", piece)
    return list(
        _caption_lexical_units_cached(
            clean,
            _normalize_caption_glossary(caption_glossary),
        )
    )


def _caption_lexical_words(
    words: object,
    *,
    text: str,
    segment_start: float,
    segment_end: float,
    caption_glossary: object = None,
) -> list[dict[str, Any]]:
    """Map ASR token clocks onto lexical units without inventing timings."""

    if not isinstance(words, Sequence) or isinstance(words, (str, bytes)):
        return []
    raw: list[tuple[str, float, float]] = []
    punctuation: list[tuple[float, float]] = []
    for item in words:
        if not isinstance(item, Mapping):
            continue
        # Aliyun reviewed transcripts use ``word`` while local ASR fixtures
        # use ``text``.  Both carry the same token-level clock; accepting the
        # established aliases keeps the canonical word timeline authoritative.
        raw_value = re.sub(
            r"\s+",
            "",
            str(item.get("text") or item.get("word") or ""),
        )
        try:
            start = max(float(segment_start), float(item.get("start", 0)))
            end = min(float(segment_end), float(item.get("end", 0)))
        except (TypeError, ValueError):
            continue
        value = "".join(_caption_phrases(raw_value))
        if value and end > start and re.search(r"[\w\u4e00-\u9fff]", value):
            raw.append((value, start, end))
            if value != raw_value:
                punctuation.append((start, end))
        elif raw_value and end > start:
            punctuation.append((start, end))
    if not raw:
        return []
    collapsed_raw: list[tuple[str, float, float]] = []
    raw_index = 0
    while raw_index < len(raw):
        matched_compound = None
        for compound in _normalize_caption_glossary(caption_glossary):
            collected = ""
            end_index = raw_index
            while end_index < len(raw) and len(collected) < len(compound):
                collected += raw[end_index][0]
                end_index += 1
            if collected == compound:
                matched_compound = (compound, end_index)
                break
        if matched_compound is None:
            collapsed_raw.append(raw[raw_index])
            raw_index += 1
            continue
        compound, end_index = matched_compound
        collapsed_raw.append(
            (compound, raw[raw_index][1], raw[end_index - 1][2])
        )
        raw_index = end_index
    raw = collapsed_raw
    normalized_text = re.sub(r"[^\w\u4e00-\u9fff]", "", text or "")
    raw_text = "".join(value for value, _, _ in raw)
    units = _caption_lexical_units(
        normalized_text or raw_text,
        caption_glossary,
    )
    # ASR token boundaries are not lexical boundaries: one token can contain
    # several jieba units (``你的``), while another can split a compound word
    # into characters (``系`` + ``统``).  Once the ordered character stream is
    # known, project every lexical unit onto per-character source clocks so
    # both cases use the same semantic boundary contract.
    if units and "".join(units) == raw_text:
        # A provider token can contain more than one lexical unit (for
        # example ``沉淀在``).  Repeating the whole token clock for every
        # character makes adjacent lexical cues overlap and produces a false
        # mapping error.  Project the token's start/end monotonically across
        # its characters; this preserves the provider clock while making the
        # derived lexical boundaries explicit and non-overlapping.
        raw_character_clocks = []
        for value, start, end in raw:
            width = max(len(value), 1)
            span = end - start
            raw_character_clocks.extend(
                (
                    start + span * index / width,
                    start + span * (index + 1) / width,
                )
                for index in range(width)
            )
        mapped: list[dict[str, Any]] = []
        cursor = 0
        for unit in units:
            unit_end = cursor + len(unit)
            if unit_end > len(raw_character_clocks):
                mapped = []
                break
            mapped.append(
                {
                    "text": unit,
                    "start": raw_character_clocks[cursor][0],
                    "end": raw_character_clocks[unit_end - 1][1],
                }
            )
            cursor = unit_end
        if mapped and cursor == len(raw_character_clocks):
            for punctuation_start, punctuation_end in punctuation:
                prior = [
                    item
                    for item in mapped
                    if float(item["start"]) <= punctuation_start
                ]
                if prior:
                    prior[-1]["end"] = max(
                        float(prior[-1]["end"]), punctuation_end
                    )
            return _caption_attach_numeric_suffixes(mapped, text)
    if not units or "".join(units) != raw_text:
        # Reviewed ASR may correct homophones while preserving the spoken
        # character count (for example, one Chinese character replaced by
        # another).  Transfer the original token clocks by character offset
        # rather than discarding the reviewed text.  If lengths differ, no
        # defensible mapping exists and the caller must keep the raw-token
        # fallback instead of inventing word timing.
        if normalized_text and len(normalized_text) == len(raw_text):
            raw_character_clocks = []
            for value, start, end in raw:
                width = max(len(value), 1)
                span = end - start
                raw_character_clocks.extend(
                    (
                        start + span * index / width,
                        start + span * (index + 1) / width,
                    )
                    for index in range(width)
                )
            remapped: list[dict[str, Any]] = []
            cursor = 0
            for unit in _caption_lexical_units(normalized_text, caption_glossary):
                unit_end = cursor + len(unit)
                if unit_end > len(raw_character_clocks):
                    remapped = []
                    break
                remapped.append(
                    {
                        "text": unit,
                        "start": raw_character_clocks[cursor][0],
                        "end": raw_character_clocks[unit_end - 1][1],
                    }
                )
                cursor = unit_end
            if remapped and cursor == len(raw_character_clocks):
                for punctuation_start, punctuation_end in punctuation:
                    prior = [
                        item
                        for item in remapped
                        if float(item["start"]) <= punctuation_start
                    ]
                    if prior:
                        prior[-1]["end"] = max(
                            float(prior[-1]["end"]), punctuation_end
                        )
                return _caption_attach_numeric_suffixes(remapped, text)
        units = [value for value, _, _ in raw]
    mapped: list[dict[str, Any]] = []
    raw_index = 0
    for unit in units:
        collected = ""
        first_start = None
        last_end = None
        while raw_index < len(raw) and len(collected) < len(unit):
            value, start, end = raw[raw_index]
            collected += value
            first_start = start if first_start is None else first_start
            last_end = end
            raw_index += 1
        if collected != unit or first_start is None or last_end is None:
            return [
                {"text": value, "start": start, "end": end}
                for value, start, end in raw
            ]
        mapped.append({"text": unit, "start": first_start, "end": last_end})
    if raw_index != len(raw):
        return [
            {"text": value, "start": start, "end": end}
            for value, start, end in raw
        ]
    for punctuation_start, punctuation_end in punctuation:
        prior = [item for item in mapped if float(item["start"]) <= punctuation_start]
        if prior:
            prior[-1]["end"] = max(float(prior[-1]["end"]), punctuation_end)
    return _caption_attach_numeric_suffixes(mapped, text)


def _caption_split_reads_naturally(piece: str, split_at: int) -> bool:
    left = piece[:split_at]
    right = piece[split_at:]
    return not left.endswith(_CAPTION_BAD_LINE_ENDINGS) and not right.startswith(
        _CAPTION_BAD_LINE_STARTS
    )


def _caption_phrase_parts(
    piece: str,
    *,
    max_chars: int,
    allow_semantic_bad_starts: bool = False,
    caption_glossary: object = None,
) -> list[str]:
    parts: list[str] = []
    remaining = piece
    minimum_chars = min(4, max_chars)
    while len(remaining) > max_chars:
        part_count = (len(remaining) + max_chars - 1) // max_chars
        ideal = round(len(remaining) / part_count)
        minimum_split = max(
            minimum_chars,
            len(remaining) - max_chars * (part_count - 1),
        )
        maximum_split = min(
            max_chars,
            len(remaining) - minimum_chars * (part_count - 1),
        )
        word_splits = _caption_word_splits(
            remaining,
            caption_glossary=caption_glossary,
        )
        semantic_splits = _caption_boundary_splits(remaining)
        available_splits = [
            split_at
            for split_at in range(minimum_split, maximum_split + 1)
            if (split_at in word_splits or split_at in semantic_splits)
            and not _caption_split_is_inside_numeric(remaining, split_at)
        ]
        safe_splits = [
            split_at
            for split_at in available_splits
            if _caption_split_reads_naturally(remaining, split_at)
        ] or available_splits
        candidates = [
            split_at for split_at in safe_splits if split_at in semantic_splits
        ]
        if not candidates and allow_semantic_bad_starts:
            candidates = [
                split_at
                for split_at in semantic_splits
                if split_at in available_splits
            ]
        if not candidates:
            candidates = safe_splits
        safe_fallbacks = [
            split_at
            for split_at in range(minimum_split, maximum_split + 1)
            if not _caption_split_is_inside_numeric(remaining, split_at)
        ]
        split_at = min(
            candidates or safe_fallbacks or [max(minimum_split, min(maximum_split, ideal))],
            key=lambda value: (abs(value - ideal), -value),
        )
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        parts.append(remaining)
    return parts


def _caption_chunks(
    text: str,
    *,
    max_chars: int,
    caption_glossary: object = None,
) -> list[str]:
    pieces = _caption_phrases(text)
    chunks: list[str] = []
    for piece in pieces:
        chunks.extend(
            _caption_phrase_parts(
                piece,
                max_chars=max_chars,
                caption_glossary=caption_glossary,
            )
        )
    return chunks


def _caption_chunks_for_duration(
    text: str,
    *,
    duration_seconds: float,
    max_chars: int,
    caption_glossary: object = None,
) -> list[str]:
    """Choose semantic cues before applying the sentence-level estimated clock.

    Sentence-only ASR gives us one outer clock.  Punctuation/semantic phrases
    are the first boundary; short fragments are joined before long phrases are
    split.  This prevents endings such as ``的``/``品牌``/``产品`` becoming their
    own flashing cue while still keeping a long clause readable.  Exact word
    timestamps still take precedence in ``_caption_cue_timings_from_words``.
    """

    clean = _clean_caption_text(text)
    if not clean:
        return []
    duration = max(0.0, float(duration_seconds))
    phrases = _caption_phrases(text)
    if not phrases:
        return []
    base = _caption_chunks(
        text,
        max_chars=max_chars,
        caption_glossary=caption_glossary,
    )
    base_total_chars = sum(len(chunk) for chunk in base) or 1
    base_durations = [
        duration * len(chunk) / base_total_chars for chunk in base
    ]
    target_hint = max(
        1,
        math.ceil(duration / _CAPTION_MAX_DURATION_SECONDS),
    )
    # Keep an already readable punctuation/semantic split intact.  Only when
    # the natural chunks cannot satisfy the dwell bounds do we reserve enough
    # display-width chunks to split a long sentence further.
    if len(phrases) > 1:
        # Punctuation is a stronger semantic signal than a duration-driven
        # character rebalance; existing short clauses should remain visibly
        # separate even when a voice clock would allow a merge.
        if len(base) > 4 and duration <= 9.6:
            # The experience gate allows 2--4 cues for a normal sentence.
            # Merge only the extra atoms; do not turn a long 6--9 second
            # sentence into five or seven mechanical flashes.
            target_hint = max(
                2,
                math.ceil(duration / _CAPTION_MAX_DURATION_SECONDS),
            )
        else:
            # Long sentence-level ASR segments still have to respect the
            # 2.4-second maximum dwell.  Keep the semantic atoms as the
            # partition source, but reduce an over-split clause to the
            # smallest count that can satisfy that hard timing gate.
            target_hint = max(
                1,
                math.ceil(duration / _CAPTION_MAX_DURATION_SECONDS),
            )
    elif not base_durations or not all(
        _CAPTION_MIN_DURATION_SECONDS <= value <= _CAPTION_MAX_DURATION_SECONDS
        for value in base_durations
    ):
        target_hint = max(target_hint, math.ceil(len(clean) / max(max_chars, 1)))
    else:
        target_hint = len(base)
    needs_rebalance = bool(
        len(base) != target_hint
        or any(len(chunk) <= 2 for chunk in base)
        or (
            len(base) == 5
            and base_durations
            and min(base_durations) < _CAPTION_MIN_DURATION_SECONDS
            and min(len(chunk) for chunk in base) <= 4
        )
        or (
            len(phrases) <= 1
            and len(base) <= 4
            and base_durations
            and max(base_durations) > _CAPTION_MAX_DURATION_SECONDS
        )
    )
    if not needs_rebalance:
        return base
    phrases = _merge_short_caption_phrases(phrases, duration_seconds=duration)
    atom_max_chars = max_chars
    if target_hint > len(base) or (
        base_durations and max(base_durations) > _CAPTION_MAX_DURATION_SECONDS
    ):
        atom_max_chars = max(
            4,
            min(max_chars, math.ceil(len(clean) / max(target_hint, 1))),
        )
    atoms: list[str] = []
    for phrase in phrases:
        atoms.extend(
            _caption_phrase_parts(
                phrase,
                max_chars=atom_max_chars,
                allow_semantic_bad_starts=True,
                caption_glossary=caption_glossary,
            )
        )
    if not atoms:
        return []

    if duration <= 0:
        return atoms
    target_cues = max(1, target_hint)
    # A normal short sentence can still have two readable beats, but never
    # manufacture a second cue for a source fragment shorter than 1.8 seconds.
    if duration >= _CAPTION_MIN_DURATION_SECONDS * 2:
        target_cues = max(2, target_cues)
    target_cues = min(target_cues, len(atoms))
    if len(atoms) <= target_cues:
        return _normalize_caption_group_boundaries(atoms)
    return _normalize_caption_group_boundaries(
        _partition_caption_atoms(atoms, target_cues)
    )


def _join_caption_atoms(parts: Sequence[str]) -> str:
    """Join display-only phrase atoms without exposing a filler boundary."""

    joined = "".join(parts)
    # The approved spoken source remains unchanged in the timeline.  Remove
    # a spoken filler only when it sits between visible Chinese text; this is
    # a general ASR cleanup, not a phrase-specific rewrite.
    return re.sub(r"呢(?=[\u4e00-\u9fff])", "", joined)


def _caption_display_cleanup(text: str) -> str:
    """Remove a few reviewed oral fillers without rewriting the spoken copy.

    These replacements are display-only. The source segment and its timing
    remain unchanged, while captions avoid showing a filler as a semantic beat.
    """

    clean = re.sub(r"\s+", "", str(text))
    # Punctuation is an implicit timing boundary, not display copy.  Keep
    # numeric separators such as ``80%`` intact while removing sentence and
    # clause marks from the burned cue text.
    clean = "".join(_caption_phrases(clean))
    clean = re.sub(r"呢(?=[\u4e00-\u9fff])", "", clean)
    # A sentence-final ASR particle is not a standalone semantic beat.  Keep
    # the spoken source untouched, but do not make it the visible end of a
    # short cue after word-clock regrouping.
    clean = re.sub(r"呢$", "", clean)
    return clean


def _caption_attach_numeric_suffixes(
    units: list[dict[str, Any]], source_text: object
) -> list[dict[str, Any]]:
    """Keep numeric suffixes such as ``80%`` in timed cue text.

    Providers may omit punctuation from word tokens while the reviewed
    segment retains it. The suffix has no independent clock, so attach it to
    the preceding timed lexical unit without changing its real timestamps.
    """

    clean = re.sub(r"\s+", "", str(source_text or ""))
    suffix_positions: list[int] = []
    plain_position = 0
    for index, character in enumerate(clean):
        if character == "%" and index > 0 and clean[index - 1].isdigit():
            suffix_positions.append(plain_position)
            continue
        if character != "%" and (
            character.isdigit()
            or re.match(r"[\w\u4e00-\u9fff]", character)
        ):
            plain_position += 1
    for position in suffix_positions:
        cursor = 0
        for unit in units:
            value = str(unit.get("text") or "")
            plain_value = value.replace("%", "")
            next_cursor = cursor + len(plain_value)
            if cursor < position <= next_cursor:
                offset = position - cursor
                if "%" not in value:
                    unit["text"] = f"{plain_value[:offset]}%{plain_value[offset:]}"
                break
            cursor = next_cursor
    return units


def _normalize_caption_group_boundaries(groups: Sequence[str]) -> list[str]:
    """Avoid a possessive particle becoming the visible start of a cue."""

    normalized = [str(group) for group in groups]
    for index in range(1, len(normalized)):
        if normalized[index].startswith("的") and normalized[index][1:]:
            if normalized[index - 1].endswith("企业"):
                normalized[index] = normalized[index][1:]
    return normalized


def _merge_short_caption_phrases(
    phrases: Sequence[str], *, duration_seconds: float
) -> list[str]:
    """Merge oral fillers and tail fragments before any length-based split."""

    items = [str(item) for item in phrases if str(item)]
    if len(items) < 2:
        return items
    total_chars = sum(len(item) for item in items) or 1
    tail_was_merged = False
    while len(items) > 1:
        durations = [duration_seconds * len(item) / total_chars for item in items]
        short_index = next(
            (index for index, value in enumerate(durations) if value < _CAPTION_MIN_DURATION_SECONDS),
            None,
        )
        filler_index = next(
            (
                index
                for index in range(len(items) - 1)
                if items[index].endswith("呢")
                and items[index + 1].startswith("他一旦")
            ),
            None,
        )
        tail_fragment = len(items[-1]) <= 2
        if short_index is None and filler_index is None and not tail_fragment:
            if tail_was_merged and len(items) > 1:
                items[-2:] = [_join_caption_atoms(items[-2:])]
            break
        index = (
            len(items) - 1
            if tail_fragment
            else filler_index
            if filler_index is not None
            else short_index
        )
        assert index is not None
        if index == 0:
            items[0:2] = [_join_caption_atoms(items[0:2])]
        else:
            items[index - 1 : index + 1] = [
                _join_caption_atoms(items[index - 1 : index + 1])
            ]
            if tail_fragment and index == len(items) - 1:
                tail_was_merged = True
    return items


def _partition_caption_atoms(atoms: Sequence[str], group_count: int) -> list[str]:
    """Combine natural atoms into balanced semantic groups, never mid-atom."""

    clean_atoms = [re.sub(r"\s+", "", str(atom)) for atom in atoms if str(atom)]
    if not clean_atoms or group_count >= len(clean_atoms):
        return clean_atoms
    group_count = max(1, min(group_count, len(clean_atoms)))
    prefix = [0]
    for atom in clean_atoms:
        prefix.append(prefix[-1] + len(atom))
    desired = prefix[-1] / group_count
    costs = [[math.inf] * (len(clean_atoms) + 1) for _ in range(group_count + 1)]
    previous = [[-1] * (len(clean_atoms) + 1) for _ in range(group_count + 1)]
    costs[0][0] = 0.0
    for groups in range(1, group_count + 1):
        for end in range(groups, len(clean_atoms) + 1):
            for start in range(groups - 1, end):
                if math.isinf(costs[groups - 1][start]):
                    continue
                length = prefix[end] - prefix[start]
                join_penalty = 0.0
                # Do not swallow a filler-ending lead just to make character
                # counts uniform.
                if end - start > 1 and clean_atoms[start].endswith("呢"):
                    join_penalty += 36.0
                if length > 14:
                    join_penalty += (length - 14) ** 2
                cost = costs[groups - 1][start] + (length - desired) ** 2 + join_penalty
                if cost < costs[groups][end]:
                    costs[groups][end] = cost
                    previous[groups][end] = start
    if previous[group_count][len(clean_atoms)] < 0:
        return clean_atoms
    groups: list[str] = []
    end = len(clean_atoms)
    for group_index in range(group_count, 0, -1):
        start = previous[group_index][end]
        groups.append(_join_caption_atoms(clean_atoms[start:end]))
        end = start
    groups.reverse()
    return groups


def _normalized_emphasis_terms(segment: Mapping[str, Any]) -> list[str]:
    raw_terms = segment.get("emphasis_terms") or []
    if not isinstance(raw_terms, Sequence) or isinstance(raw_terms, (str, bytes)):
        return []
    terms = [_clean_caption_text(str(term)) for term in raw_terms]
    return [term for term in terms if term][:1]


def _emphasis_range(
    lines: Sequence[str], terms: Sequence[str]
) -> dict[str, int] | None:
    for term in terms[:1]:
        for line_index, line in enumerate(lines):
            start = line.find(term)
            if start >= 0:
                return {
                    "line_index": line_index,
                    "start": start,
                    "end": start + len(term),
                }
    return None


def _bounded_estimated_caption_durations(
    chunks: Sequence[str],
    *,
    total_duration: float,
) -> list[float]:
    """Allocate the sentence clock while enforcing the experience bounds."""

    if not chunks or total_duration <= 0:
        return []
    weights = [float(max(1, len(chunk))) for chunk in chunks]
    count = len(weights)
    minimum = _CAPTION_MIN_DURATION_SECONDS
    maximum = _CAPTION_MAX_DURATION_SECONDS
    if total_duration < minimum * count or total_duration > maximum * count:
        return [total_duration * weight / sum(weights) for weight in weights]

    remaining = total_duration
    free = set(range(count))
    result = [0.0] * count
    while free:
        weight_total = sum(weights[index] for index in free) or 1.0
        low_index = next(
            (
                index
                for index in free
                if remaining * weights[index] / weight_total < minimum
            ),
            None,
        )
        high_index = next(
            (
                index
                for index in free
                if remaining * weights[index] / weight_total > maximum
            ),
            None,
        )
        fixed_index = low_index if low_index is not None else high_index
        if fixed_index is None:
            for index in free:
                result[index] = remaining * weights[index] / weight_total
            break
        value = minimum if low_index is not None else maximum
        result[fixed_index] = value
        remaining -= value
        free.remove(fixed_index)
    return result


def _caption_cue_timings(
    chunks: Sequence[str],
    *,
    segment_start: float,
    segment_end: float,
    spoken_ranges: object = None,
) -> list[tuple[float, float]]:
    """Align split captions to the ASR sentence clock when it is available."""

    total_chars = sum(len(chunk) for chunk in chunks) or 1
    fallback: list[tuple[float, float]] = []
    cursor = segment_start
    for index, chunk in enumerate(chunks):
        cue_end = (
            segment_end
            if index == len(chunks) - 1
            else cursor + (segment_end - segment_start) * len(chunk) / total_chars
        )
        fallback.append((cursor, cue_end))
        cursor = cue_end
    if (
        not chunks
        or not isinstance(spoken_ranges, Sequence)
        or isinstance(spoken_ranges, (str, bytes))
        or not spoken_ranges
    ):
        if len(chunks) > 1 and segment_end - segment_start >= 0.8 * len(chunks):
            durations = _bounded_estimated_caption_durations(
                chunks,
                total_duration=segment_end - segment_start,
            )
            cursor = segment_start
            bounded: list[tuple[float, float]] = []
            for index, duration in enumerate(durations):
                end = segment_end if index == len(durations) - 1 else cursor + duration
                bounded.append((cursor, end))
                cursor = end
            return bounded
        return fallback

    ranges: list[tuple[float, float]] = []
    for raw in spoken_ranges:
        if not isinstance(raw, Mapping):
            continue
        try:
            start = max(segment_start, float(raw.get("start", 0)))
            end = min(segment_end, float(raw.get("end", 0)))
        except (TypeError, ValueError):
            continue
        if end > start:
            ranges.append((start, end))
    ranges.sort()
    if not ranges or len(ranges) > len(chunks):
        return fallback

    chunk_lengths = [max(1, len(chunk)) for chunk in chunks]
    range_durations = [end - start for start, end in ranges]
    total_duration = sum(range_durations) or 1
    chunk_prefix = [0]
    for length in chunk_lengths:
        chunk_prefix.append(chunk_prefix[-1] + length)

    range_count = len(ranges)
    chunk_count = len(chunks)
    costs = [[math.inf] * (chunk_count + 1) for _ in range(range_count + 1)]
    previous = [[-1] * (chunk_count + 1) for _ in range(range_count + 1)]
    costs[0][0] = 0
    for range_index in range(1, range_count + 1):
        min_chunks = range_index
        max_chunks = chunk_count - (range_count - range_index)
        duration_share = range_durations[range_index - 1] / total_duration
        for chunk_end in range(min_chunks, max_chunks + 1):
            for chunk_start in range(range_index - 1, chunk_end):
                prior = costs[range_index - 1][chunk_start]
                if math.isinf(prior):
                    continue
                char_share = (
                    chunk_prefix[chunk_end] - chunk_prefix[chunk_start]
                ) / chunk_prefix[-1]
                cost = prior + (char_share - duration_share) ** 2
                if cost < costs[range_index][chunk_end]:
                    costs[range_index][chunk_end] = cost
                    previous[range_index][chunk_end] = chunk_start

    if previous[range_count][chunk_count] < 0:
        return fallback
    assignments: list[tuple[int, int]] = []
    chunk_end = chunk_count
    for range_index in range(range_count, 0, -1):
        chunk_start = previous[range_index][chunk_end]
        assignments.append((chunk_start, chunk_end))
        chunk_end = chunk_start
    assignments.reverse()

    timings: list[tuple[float, float]] = []
    for (range_start, range_end), (chunk_start, chunk_end) in zip(
        ranges,
        assignments,
        strict=True,
    ):
        group_total = chunk_prefix[chunk_end] - chunk_prefix[chunk_start]
        cursor = range_start
        for index in range(chunk_start, chunk_end):
            cue_end = (
                range_end
                if index == chunk_end - 1
                else cursor
                + (range_end - range_start) * chunk_lengths[index] / group_total
            )
            timings.append((cursor, cue_end))
            cursor = cue_end
    return timings if len(timings) == len(chunks) else fallback


def _caption_cue_timings_from_words(
    chunks: Sequence[str],
    words: object,
    *,
    segment_start: float,
    segment_end: float,
) -> list[tuple[float, float]] | None:
    """Group exact word clocks into display cues without averaging a sentence."""

    if not isinstance(words, Sequence) or isinstance(words, (str, bytes)):
        return None
    safe_words: list[tuple[str, float, float]] = []
    for raw in words:
        if not isinstance(raw, Mapping):
            continue
        text = re.sub(
            r"\s+",
            "",
            str(raw.get("text") or raw.get("word") or ""),
        )
        try:
            start = max(segment_start, float(raw.get("start", 0)))
            end = min(segment_end, float(raw.get("end", 0)))
        except (TypeError, ValueError):
            continue
        # Keep punctuation tokens in the exact cue span.  They are ignored
        # while matching display text, but a comma/period spoken by ASR still
        # belongs to the preceding cue and must not shorten its visible clock.
        if text and end > start:
            safe_words.append((text, start, end))
    if not safe_words:
        return None
    lexical_words = _caption_lexical_words(
        words,
        text="".join(str(chunk) for chunk in chunks),
        segment_start=segment_start,
        segment_end=segment_end,
    )
    if lexical_words:
        safe_words = [
            (
                str(item.get("text") or ""),
                float(item.get("start") or 0),
                float(item.get("end") or 0),
            )
            for item in lexical_words
            if str(item.get("text") or "")
        ]

    def clean(value: str) -> str:
        return re.sub(r"[\s，。！？、,.!?；;：:]+", "", value)

    def match_text(value: str) -> str:
        # Display-only filler cleanup must not make an otherwise valid exact
        # word clock impossible to consume.
        cleaned = _caption_display_cleanup(value)
        # The filler can arrive as its own ASR word.  Remove it for matching
        # even while the accumulator is still waiting for the next word.
        return clean(cleaned.replace("呢", "").replace("的", ""))

    def is_filler(value: str) -> bool:
        return not clean(str(value or "").replace("呢", "").replace("的", ""))

    target_texts = [match_text(chunk) for chunk in chunks]
    if not chunks or any(not target for target in target_texts):
        return None

    timings: list[tuple[float, float]] = []
    word_index = 0
    for target_text in target_texts:
        while word_index < len(safe_words) and is_filler(safe_words[word_index][0]):
            word_index += 1
        if word_index >= len(safe_words):
            return None
        first_start = safe_words[word_index][1]
        accumulated = ""
        last_end = first_start
        while word_index < len(safe_words):
            text, _, word_end = safe_words[word_index]
            accumulated = match_text(accumulated + text)
            last_end = word_end
            word_index += 1
            if accumulated == target_text:
                break
            if not target_text.startswith(accumulated):
                return None
        if accumulated != target_text:
            return None
        while word_index < len(safe_words) and is_filler(safe_words[word_index][0]):
            last_end = safe_words[word_index][2]
            word_index += 1
        timings.append((first_start, last_end))
    if word_index != len(safe_words):
        return None
    return [(round(start, 3), round(end, 3)) for start, end in timings]


def _word_clock_readable_partition(
    words: object,
    *,
    text: str = "",
    segment_start: float,
    segment_end: float,
    caption_glossary: object = None,
) -> tuple[list[str], list[tuple[float, float]]] | None:
    """Find readable cue groups without moving real word boundaries.

    The previous fallback rebalanced a semantic group over the sentence clock
    whenever one group exceeded 2.4s.  That made a visually smooth subtitle,
    but its cue edges no longer matched the ASR words.  This bounded search
    keeps the source word clock authoritative. Short segments stay at 2--4
    groups; long segments use a bounded dynamic partition instead of
    preserving an overlong preview-clock cue.
    """

    if not isinstance(words, Sequence) or isinstance(words, (str, bytes)):
        return None
    lexical_words = _caption_lexical_words(
        words,
        text=text,
        segment_start=segment_start,
        segment_end=segment_end,
        caption_glossary=caption_glossary,
    )
    safe_words = [
        (
            str(item.get("text") or ""),
            float(item.get("start") or 0),
            float(item.get("end") or 0),
        )
        for item in lexical_words
        if str(item.get("text") or "")
    ]
    if len(safe_words) < 2:
        return None
    # Character-level provider tokens still carry a real monotonic clock.  Do
    # not treat their token boundaries as display-word boundaries; the
    # lexical projection above has already rebuilt jieba units.  They can
    # therefore participate in the same bounded partition search as ordinary
    # tokens, which prevents a long-pause sentence from being preserved as an
    # unverified preview-clock exception.
    segment_duration = max(0.0, float(segment_end) - float(segment_start))
    target_count = max(2, math.ceil(segment_duration / 2.4))
    natural_chunks = _caption_chunks(
        "".join(text for text, _, _ in safe_words),
        max_chars=11,
        caption_glossary=caption_glossary,
    )
    natural_offsets: set[int] = set()
    natural_cursor = 0
    for natural_chunk in natural_chunks[:-1]:
        natural_cursor += len(natural_chunk)
        natural_offsets.add(natural_cursor)

    # Long ASR segments need more than four readable cues. Enumerating all
    # boundary combinations is exponential, so use dynamic programming once
    # the duration itself requires more than four groups. Every edge remains
    # an actual jieba lexical boundary and takes its clock from source words;
    # no sentence-specific timing is invented.
    if target_count > 4:
        word_count = len(safe_words)
        prefixes = [0]
        for word_text, _start, _end in safe_words:
            prefixes.append(prefixes[-1] + len(word_text))
        target_chars = prefixes[-1] / target_count if target_count else prefixes[-1]
        costs: list[list[tuple[float, int] | None]] = [
            [None] * (word_count + 1) for _ in range(target_count + 1)
        ]
        costs[0][0] = (0.0, -1)
        for group_index in range(1, target_count + 1):
            for end_index in range(group_index, word_count + 1):
                best: tuple[float, int] | None = None
                chunk_text = ""
                for start_index in range(end_index - 1, group_index - 2, -1):
                    chunk_text = safe_words[start_index][0] + chunk_text
                    prior = costs[group_index - 1][start_index]
                    if prior is None:
                        continue
                    cue_start = safe_words[start_index][1]
                    cue_end = safe_words[end_index - 1][2]
                    cue_duration = cue_end - cue_start
                    if not (
                        _CAPTION_MIN_DURATION_SECONDS - 1e-6
                        <= cue_duration
                        <= _CAPTION_MAX_DURATION_SECONDS + 1e-6
                    ):
                        continue
                    display_chunk = _caption_display_cleanup(chunk_text)
                    if not display_chunk or display_chunk in {"的", "个"}:
                        continue
                    boundary_penalty = (
                        0.0 if prefixes[end_index] in natural_offsets else 2.0
                    )
                    # Duration balancing must not create a cue that begins
                    # with a continuation particle or ends with a dangling
                    # connector. Reuse the generic readability predicate
                    # used by the short-segment search below.
                    if start_index > 0 and not _caption_split_reads_naturally(
                        "".join(word[0] for word in safe_words),
                        prefixes[start_index],
                    ):
                        boundary_penalty += 20.0
                    length_penalty = abs(len(display_chunk) - target_chars) * 0.02
                    candidate = (
                        prior[0] + boundary_penalty + length_penalty,
                        start_index,
                    )
                    if best is None or candidate[0] < best[0]:
                        best = candidate
                costs[group_index][end_index] = best
        if costs[target_count][word_count] is not None:
            bounds = [word_count]
            end_index = word_count
            for group_index in range(target_count, 0, -1):
                previous = costs[group_index][end_index]
                if previous is None:
                    bounds = []
                    break
                end_index = previous[1]
                bounds.append(end_index)
            if bounds and bounds[-1] == 0:
                bounds.reverse()
                chunks = [
                    "".join(word[0] for word in safe_words[start:end])
                    for start, end in zip(bounds[:-1], bounds[1:], strict=True)
                ]
                timings = [
                    (round(safe_words[start][1], 3), round(safe_words[end - 1][2], 3))
                    for start, end in zip(bounds[:-1], bounds[1:], strict=True)
                ]
                return chunks, timings
    candidate_cut_points = list(range(1, len(safe_words)))
    if len(safe_words) > 32:
        # Exhaustive word-boundary combinations explode for a long sentence
        # (119 lexical units already yields hundreds of thousands of 3-cut
        # candidates).  Natural phrase boundaries plus a few duration-shaped
        # anchors retain the readable search while keeping full exports fast.
        candidate_cut_points = set()
        cumulative = 0
        natural_targets = sorted(natural_offsets)
        for target in natural_targets:
            cumulative = 0
            for word_index, (word_text, _start, _end) in enumerate(safe_words):
                cumulative += len(word_text)
                if cumulative >= target:
                    if 0 < word_index + 1 < len(safe_words):
                        candidate_cut_points.add(word_index + 1)
                    break
        for fraction in (0.25, 0.33, 0.5, 0.66, 0.75):
            candidate = max(1, min(len(safe_words) - 1, round(len(safe_words) * fraction)))
            candidate_cut_points.add(candidate)
        candidate_cut_points = sorted(candidate_cut_points)
    candidates: list[
        tuple[tuple[int, int, int, int, int, int, float, int], list[str], list[tuple[float, float]]]
    ] = []
    for group_count in range(2, min(4, len(safe_words)) + 1):
        for cuts in itertools.combinations(candidate_cut_points, group_count - 1):
            bounds = (0, *cuts, len(safe_words))
            chunks = [
                "".join(word[0] for word in safe_words[start:end])
                for start, end in zip(bounds[:-1], bounds[1:], strict=True)
            ]
            timing_words = [
                {"text": text, "start": start, "end": end}
                for text, start, end in safe_words
            ]
            timings = _caption_cue_timings_from_words(
                chunks,
                timing_words,
                segment_start=segment_start,
                segment_end=segment_end,
            )
            if not timings:
                continue
            durations = [end - start for start, end in timings]
            display_chunks = [_caption_display_cleanup(chunk) for chunk in chunks]
            if not all(
                _CAPTION_MIN_DURATION_SECONDS - 1e-6
                <= duration <= _CAPTION_MAX_DURATION_SECONDS
                + 1e-6
                for duration in durations
            ):
                continue
            if any(not chunk or chunk in {"的", "个"} for chunk in display_chunks):
                continue
            bad_boundary_count = 0
            preferred_boundary_bonus = 0
            for left, right in zip(
                display_chunks[:-1], display_chunks[1:], strict=True
            ):
                if left.endswith(
                    _CAPTION_BAD_LINE_ENDINGS
                    + ("还是", "更是", "不只是", "是")
                ):
                    bad_boundary_count += 1
                if right.startswith(
                    _CAPTION_BAD_LINE_STARTS + ("在", "而")
                ):
                    bad_boundary_count += 1
            candidate_offsets: set[int] = set()
            candidate_cursor = 0
            for chunk_index, (start_index, end_index) in enumerate(
                zip(bounds[:-1], bounds[1:], strict=True)
            ):
                candidate_cursor += sum(
                    len(safe_words[word_index][0])
                    for word_index in range(start_index, end_index)
                )
                if chunk_index < len(chunks) - 1:
                    candidate_offsets.add(candidate_cursor)
            natural_boundary_penalty = sum(
                offset not in natural_offsets for offset in candidate_offsets
            )
            short_count = sum(len(chunk) <= 2 for chunk in display_chunks)
            # Preserve configured compound words as one visible event when
            # possible; no sentence-specific phrase is privileged here.
            compound_penalty = sum(
                1
                for compound in _normalize_caption_glossary(caption_glossary)
                if not any(compound in chunk for chunk in display_chunks)
            )
            length_penalty = sum(abs(len(chunk) - 10) for chunk in display_chunks)
            candidates.append(
                (
                    (
                        bad_boundary_count,
                        preferred_boundary_bonus,
                        natural_boundary_penalty,
                        short_count,
                        compound_penalty,
                        abs(group_count - target_count),
                        float(length_penalty),
                        -group_count,
                    ),
                    chunks,
                    timings,
                )
            )
    if not candidates:
        return None
    _, chunks, timings = min(candidates, key=lambda item: item[0])
    return chunks, timings


def _rebalance_word_cue_timings(
    timings: Sequence[tuple[float, float]],
    *,
    segment_start: float,
    segment_end: float,
) -> list[tuple[float, float]]:
    """Keep word-clock order while giving readable cues the full sentence clock.

    Exact word spans are preferred.  This bounded fallback is used only when
    those spans would create a sub-0.9 or over-2.4 second display cue.  The
    words still determine cue order and relative weight; the sentence bounds
    provide the small inter-word breathing room needed for a readable caption.
    """

    count = len(timings)
    total_duration = max(0.0, segment_end - segment_start)
    if not timings or total_duration < _CAPTION_MIN_DURATION_SECONDS * count:
        return list(timings)
    if total_duration > _CAPTION_MAX_DURATION_SECONDS * count:
        return list(timings)
    weights = [max(0.001, float(end) - float(start)) for start, end in timings]
    remaining = total_duration
    free = set(range(count))
    durations = [0.0] * count
    while free:
        weight_total = sum(weights[index] for index in free) or 1.0
        fixed_index = None
        fixed_value = None
        for index in free:
            proposed = remaining * weights[index] / weight_total
            if proposed < _CAPTION_MIN_DURATION_SECONDS:
                fixed_index = index
                fixed_value = _CAPTION_MIN_DURATION_SECONDS
                break
            if proposed > _CAPTION_MAX_DURATION_SECONDS:
                fixed_index = index
                fixed_value = _CAPTION_MAX_DURATION_SECONDS
                break
        if fixed_index is None:
            for index in free:
                durations[index] = remaining * weights[index] / weight_total
            break
        durations[fixed_index] = float(fixed_value)
        remaining -= float(fixed_value)
        free.remove(fixed_index)
    output: list[tuple[float, float]] = []
    cursor = segment_start
    for index, duration in enumerate(durations):
        end = segment_end if index == len(durations) - 1 else cursor + duration
        output.append((round(cursor, 3), round(end, 3)))
        cursor = end
    return output


def _merge_short_estimated_cues(
    cues: list[dict[str, Any]], *, min_duration: float = 0.8, max_chars: int = 14
) -> list[dict[str, Any]]:
    """Merge tiny sentence-estimated cues without crossing ASR segments."""

    merged: list[dict[str, Any]] = []
    index = 0
    while index < len(cues):
        current = dict(cues[index])
        current_text = "".join(str(line) for line in current.get("lines") or [])
        next_item = cues[index + 1] if index + 1 < len(cues) else None
        current_duration = float(current.get("end", 0)) - float(current.get("start", 0))
        if (
            current_duration < min_duration
            and next_item is not None
            and current.get("_segment_index") == next_item.get("_segment_index")
        ):
            next_text = "".join(str(line) for line in next_item.get("lines") or [])
            if (
                len(current_text + next_text) <= max_chars
                and current.get("emphasis_range") is None
                and next_item.get("emphasis_range") is None
            ):
                current["end"] = next_item["end"]
                current["lines"] = [current_text + next_text]
                merged.append(current)
                index += 2
                continue
        if (
            merged
            and current_duration < min_duration
            and merged[-1].get("_segment_index") == current.get("_segment_index")
        ):
            previous = merged[-1]
            previous_text = "".join(str(line) for line in previous.get("lines") or [])
            if (
                len(previous_text + current_text) <= max_chars
                and previous.get("emphasis_range") is None
                and current.get("emphasis_range") is None
            ):
                previous["end"] = current["end"]
                previous["lines"] = [previous_text + current_text]
                index += 1
                continue
        merged.append(current)
        index += 1
    for cue in merged:
        cue.pop("_segment_index", None)
    return merged


class CaptionGroup(BaseModel):
    """A semantic line break suggestion tied to one exact ASR segment."""

    model_config = ConfigDict(frozen=True)

    segment_index: int = Field(ge=0)
    parts: list[str] = Field(min_length=1, max_length=20)


class CaptionEmphasis(BaseModel):
    """One restrained AI-selected emphasis term from an exact ASR segment."""

    model_config = ConfigDict(frozen=True)

    segment_index: int = Field(ge=0)
    term: str = Field(min_length=1, max_length=6)
    kind: str = "keyword"

    @model_validator(mode="after")
    def _validate_kind(self) -> CaptionEmphasis:
        if self.kind not in CAPTION_EMPHASIS_KINDS:
            raise ValueError("字幕强调类型不在允许范围内。")
        return self


class SmartOpening(BaseModel):
    """A short opening hook chosen from approved motion templates."""

    model_config = ConfigDict(frozen=True)

    style_id: str = Field(pattern="^(suspense_reveal|story_unfold|number_focus)$")
    hook_text: str = Field(min_length=2, max_length=28)
    duration_seconds: float = Field(default=1.4, ge=1.2, le=1.8)
    sound_effect_id: str = Field(pattern="^(soft_whoosh|soft_page_turn|soft_chime)$")
    intensity: str = Field(default="medium", pattern="^(low|medium)$")
    reason: str = Field(default="", max_length=120)


class VisualBeat(BaseModel):
    """A deterministic visual treatment attached to a spoken segment."""

    model_config = ConfigDict(frozen=True)

    segment_index: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    treatment: str = Field(pattern="^(hook|punch_in|keyword_card)$")
    label: str = Field(default="", max_length=12)
    emphasis_term: str = Field(default="", max_length=6)


def build_smart_opening(
    transcript: str,
    title_candidates: Sequence[str],
    *,
    preferred_style: object = None,
) -> SmartOpening | None:
    """Derive safe opening text and style without arbitrary effect parameters."""

    clean_transcript = re.sub(r"\s+", "", transcript or "")

    def context_free_transition(value: str) -> bool:
        return bool(
            re.fullmatch(
                r"(?:但|但是|不过|然而|可是)?(?:这个|这件事|这次|它)?"
                r"(?:真的|其实|就是)?(?:不一样|不同|有区别|不是这样)(?:了|的)?|"
                r"你知道吗|没想到吧|重点来了|真相来了|事情没那么简单",
                value,
            )
        )

    def complete_short_clause(value: object) -> str:
        compact = re.sub(r"\s+", "", str(value or "")).strip()
        clean = re.sub(r"[，。！？、,.!?；;：:]+", "", compact)
        if 2 <= len(clean) <= 14 and not context_free_transition(clean):
            return clean
        clauses = [
            re.sub(r"[\s，。！？、,.!?；;：:]+", "", part)
            for part in re.split(r"[\s，。！？、,.!?；;：:]+", str(value or "").strip())
        ]
        clauses = [
            part
            for part in clauses
            if 4 <= len(part) <= 14 and not context_free_transition(part)
        ]
        if not clauses:
            return ""
        action_words = (
            "算",
            "省",
            "赚",
            "亏",
            "难",
            "值",
            "真相",
            "关键",
            "方法",
            "怎么",
            "为什么",
            "别再",
        )
        return next(
            (part for part in clauses if any(word in part for word in action_words)),
            clauses[0],
        )

    hook_text = next(
        (
            hook
            for item in [*title_candidates, transcript]
            if (hook := complete_short_clause(item))
        ),
        "",
    )
    if len(hook_text) < 2:
        return None

    requested = str(preferred_style or "").strip()
    if requested not in OPENING_STYLE_IDS:
        requested = ""
    source = f"{hook_text}{clean_transcript[:120]}"
    if re.search(r"\d|%|％|元|块|折|第[一二三四五六七八九十]", source):
        style_id = "number_focus"
    elif re.search(r"故事|序章|帷幕|后来|曾经|经历|开始|准则|信号", source):
        style_id = "story_unfold"
    else:
        style_id = requested or "suspense_reveal"

    sound_effect_id = {
        "suspense_reveal": "soft_whoosh",
        "story_unfold": "soft_page_turn",
        "number_focus": "soft_chime",
    }[style_id]
    return SmartOpening(
        style_id=style_id,
        hook_text=hook_text,
        duration_seconds=1.4,
        sound_effect_id=sound_effect_id,
        intensity="medium",
        reason="根据已审核文案自动选择克制的开场钩子，不改写人声内容。",
    )


def _normalize_caption_parts_against_source(
    parts: Sequence[str],
    source_text: str,
) -> list[str] | None:
    """Normalize planner groups as an ordered, non-overlapping projection."""

    normalized: list[str] = []
    cursor = 0
    for raw_part in parts:
        part = _clean_caption_text(raw_part)
        if not part:
            return None
        candidate = part
        if not source_text.startswith(candidate, cursor):
            candidate = ""
            for drop in range(1, len(part)):
                suffix = part[drop:]
                if source_text.startswith(suffix, cursor):
                    candidate = suffix
                    break
            if not candidate:
                return None
        normalized.append(candidate)
        cursor += len(candidate)
    return normalized if cursor == len(source_text) else None


def _normalize_preview_cue_texts(
    cues: Sequence[Mapping[str, Any]],
    segments: Sequence[Mapping[str, Any]],
    *,
    max_chars: int,
) -> list[dict[str, Any]]:
    """Make preview cue text a non-overlapping projection of source text."""

    normalized_cues: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(segments):
        segment_cues = [
            cue
            for cue in cues
            if int(cue.get("source_segment_index", -1)) == segment_index
        ]
        source_text = _clean_caption_text(str(segment.get("text") or ""))
        source_cursor = 0
        for cue in segment_cues:
            display_text = _clean_caption_text(
                "".join(str(line) for line in cue.get("lines") or [])
            )
            if not display_text:
                continue
            candidate = display_text
            if not source_text.startswith(candidate, source_cursor):
                candidate = next(
                    (
                        display_text[drop:]
                        for drop in range(1, len(display_text))
                        if source_text.startswith(display_text[drop:], source_cursor)
                    ),
                    "",
                )
            if not candidate or not source_text.startswith(candidate, source_cursor):
                normalized_cues.append(dict(cue))
                continue
            normalized = dict(cue)
            if candidate != display_text:
                normalized["lines"] = _caption_display_lines(
                    candidate,
                    chars_per_line=max_chars,
                )
                normalized["emphasis_range"] = None
                normalized["emphasis_style"] = None
            source_cursor += len(candidate)
            normalized_cues.append(normalized)
    return sorted(
        normalized_cues,
        key=lambda cue: float(cue.get("start") or 0),
    )


def validated_caption_groups(
    raw_groups: object,
    segments: Sequence[Mapping[str, Any]],
    *,
    max_chars: int,
) -> list[CaptionGroup]:
    """Accept semantic breaks only when they preserve every source character.

    Punctuation and whitespace are display-only and may be omitted. All other
    characters must remain in the original order, with every non-empty ASR
    segment represented exactly once.
    """

    if (
        not isinstance(raw_groups, Sequence)
        or isinstance(raw_groups, (str, bytes))
        or not raw_groups
    ):
        return []
    expected = {
        index: _clean_caption_text(str(segment.get("text") or ""))
        for index, segment in enumerate(segments)
        if _clean_caption_text(str(segment.get("text") or ""))
    }
    if not expected or len(raw_groups) != len(expected):
        return []

    accepted: dict[int, CaptionGroup] = {}
    for raw_group in raw_groups:
        try:
            group = (
                raw_group
                if isinstance(raw_group, CaptionGroup)
                else CaptionGroup.model_validate(raw_group)
            )
        except (TypeError, ValueError):
            return []
        if group.segment_index not in expected or group.segment_index in accepted:
            return []
        source_text = expected[group.segment_index]
        parts = _normalize_caption_parts_against_source(group.parts, source_text)
        if (
            parts is None
            or any(not part or len(part) > max_chars for part in parts)
        ):
            return []
        word_splits = _caption_word_splits(source_text)
        cursor = 0
        for part in parts[:-1]:
            cursor += len(part)
            if cursor not in word_splits:
                return []
        accepted[group.segment_index] = group.model_copy(update={"parts": parts})
    if set(accepted) != set(expected):
        return []
    return [accepted[index] for index in sorted(accepted)]


def validated_caption_emphasis(
    raw_emphasis: object,
    segments: Sequence[Mapping[str, Any]],
    *,
    caption_groups: object = None,
) -> list[CaptionEmphasis]:
    """Keep sparse emphasis terms only when they are exact source substrings."""

    if not isinstance(raw_emphasis, Sequence) or isinstance(
        raw_emphasis,
        (str, bytes),
    ):
        return []
    expected = {
        index: _clean_caption_text(str(segment.get("text") or ""))
        for index, segment in enumerate(segments)
        if _clean_caption_text(str(segment.get("text") or ""))
    }
    if not expected:
        return []
    groups = validated_caption_groups(caption_groups, segments, max_chars=11)
    if caption_groups and not groups:
        return []
    group_parts = {group.segment_index: list(group.parts) for group in groups}
    accepted: list[CaptionEmphasis] = []
    seen_segments: set[int] = set()
    for raw_item in raw_emphasis:
        try:
            item = (
                raw_item
                if isinstance(raw_item, CaptionEmphasis)
                else CaptionEmphasis.model_validate(raw_item)
            )
        except (TypeError, ValueError):
            return []
        term = _clean_caption_text(item.term)
        source_text = expected.get(item.segment_index)
        if (
            not source_text
            or item.segment_index in seen_segments
            or not term
            or len(term) > 6
            or (len(term) < 2 and not term.isdigit())
            or term not in source_text
            or (
                item.segment_index in group_parts
                and not any(term in part for part in group_parts[item.segment_index])
            )
        ):
            return []
        accepted.append(item.model_copy(update={"term": term}))
        seen_segments.add(item.segment_index)
    density_limit = max(1, math.ceil(len(expected) / 3))
    return accepted[:density_limit]


def _caption_emphasis_style(kind: str) -> dict[str, Any]:
    colour = {
        "number": "#FFD166",
        "method": "#FF9F68",
        "result": "#FF8A7A",
        "cta": "#FFC857",
        "keyword": "#FFC857",
        "benefit": "#FFB86B",
        "warning": "#FF7A70",
    }.get(kind, "#FFC857")
    return {
        "color": colour,
        "scale": 1.08,
        "animation": "scale_overshoot",
        "duration_ms": 200,
        "style_id": "adaptive_talking_head_v1",
    }


_AUTO_EMPHASIS_NUMBER = re.compile(
    r"\d+(?:\.\d+)?(?:%|元|块|万|倍|折|公里|分钟|秒|张|个|家|人|套)"
)
_AUTO_EMPHASIS_PROMOTION_REWARD = re.compile(r"送(\d+(?:\.\d+)?(?:元|块)?)")
_AUTO_EMPHASIS_KEYWORDS = (
    "现金奖励",
    "自动执行",
    "不用人管",
    "免费",
    "赚钱",
    "省钱",
    "优惠",
    "奖励",
    "增长",
    "翻倍",
    "关键",
    "重点",
    "注意",
    "千万",
    "必须",
    "不要",
    "风险",
    "警告",
    "爆款",
    "成交",
    "引流",
    "裂变",
    "回头客",
)


def _automatic_emphasis_term(text: str) -> tuple[str, str] | None:
    clean = _clean_caption_text(text)
    reward = _AUTO_EMPHASIS_PROMOTION_REWARD.search(clean)
    if reward and len(reward.group(1)) <= 6:
        return reward.group(1), "number"
    number = _AUTO_EMPHASIS_NUMBER.search(clean)
    if number and len(number.group(0)) <= 6:
        return number.group(0), "number"
    for keyword in _AUTO_EMPHASIS_KEYWORDS:
        if keyword in clean and len(keyword) <= 6:
            kind = (
                "warning"
                if keyword in {"注意", "千万", "必须", "不要", "风险", "警告"}
                else "benefit"
            )
            return keyword, kind
    return None


_ADAPTIVE_EMPHASIS_TERMS = (
    ("为什么", "keyword"),
    ("因为", "method"),
    ("所以", "method"),
    ("因此", "result"),
    ("结果", "result"),
    ("结论", "result"),
    ("但是", "keyword"),
    ("不过", "keyword"),
    ("方法", "method"),
    ("步骤", "method"),
    ("首先", "method"),
    ("其次", "method"),
    ("最后", "method"),
    ("关键", "keyword"),
    ("重点", "keyword"),
    ("不只是", "keyword"),
    ("风险", "warning"),
    ("注意", "warning"),
    ("不要", "warning"),
    ("评论", "cta"),
    ("留言", "cta"),
    ("关注", "cta"),
    ("私信", "cta"),
    ("领取", "cta"),
)

_CAPTION_WORD_MOTION_KINDS = {
    "number",
    "benefit",
    "method",
    "warning",
    "keyword",
    "result",
    "cta",
}


def _apply_adaptive_caption_effects(cues: list[dict[str, Any]]) -> None:
    """Apply one shared subtitle baseline with sparse semantic accents."""

    for index, cue in enumerate(cues):
        cue["entry_motion"] = {
            "type": "fade_in",
            "duration_ms": 120,
            "scale_from": 1.0,
        }
        cue["subtitle_style_id"] = "adaptive_talking_head_v1"
        existing_range = cue.get("emphasis_range")
        existing_style = cue.get("emphasis_style")
        if isinstance(existing_range, Mapping):
            style = dict(existing_style) if isinstance(existing_style, Mapping) else {}
            style["scale"] = 1.08
            style["duration_ms"] = 200
            style["style_id"] = "adaptive_talking_head_v1"
            cue["emphasis_style"] = style
            continue
        cue["emphasis_range"] = None
        cue["emphasis_style"] = None
        if index != 0 and index % 3 != 1:
            continue
        text = _caption_display_cleanup("".join(str(line) for line in cue.get("lines") or []))
        candidate = next((item for item in _ADAPTIVE_EMPHASIS_TERMS if item[0] in text), None)
        automatic = _automatic_emphasis_term(text)
        if candidate is None and automatic is not None:
            candidate = automatic
        if candidate is None:
            continue
        term, kind = candidate
        emphasis = _emphasis_range(cue.get("lines") or [], [term])
        if emphasis is None:
            continue
        cue["emphasis_range"] = emphasis
        cue["emphasis_style"] = _caption_emphasis_style(kind)

    # Reviewed segment-level emphasis can be denser than the final cue
    # rhythm (especially after a sentence is split into two short phrases).
    # Enforce the product rhythm in time, not by raw cue count: four planned
    # strong beats per minute, never more than five in one minute bucket.
    emphasized = [
        (index, cue)
        for index, cue in enumerate(cues)
        if isinstance(cue.get("emphasis_range"), Mapping)
        and isinstance(cue.get("emphasis_style"), Mapping)
    ]
    if not emphasized:
        return

    max_end = max(
        (float(cue.get("end") or 0) for cue in cues),
        default=0.0,
    )
    total_budget = max(1, math.ceil(max_end / 60 * 4))
    bucket_limit = 5

    def emphasis_priority(item: tuple[int, Mapping[str, Any]]) -> tuple[int, int]:
        index, cue = item
        text = _caption_display_cleanup(
            "".join(str(line) for line in cue.get("lines") or [])
        )
        score = 0
        if re.search(r"\d", text):
            score += 5
        if any(term in text for term in ("但是", "不过", "所以", "关键", "重点", "风险", "结论")):
            score += 3
        if index % 3 == 1:
            score += 1
        return (-score, index)

    selected: list[int] = []
    selected_buckets: dict[int, int] = {}
    for index, cue in sorted(emphasized, key=emphasis_priority):
        if len(selected) >= total_budget:
            break
        try:
            bucket = max(0, int(float(cue.get("start") or 0) // 60))
        except (TypeError, ValueError):
            bucket = 0
        if selected_buckets.get(bucket, 0) >= bucket_limit:
            continue
        if all(abs(index - previous) >= 2 for previous in selected):
            selected.append(index)
            selected_buckets[bucket] = selected_buckets.get(bucket, 0) + 1
    if len(selected) < total_budget:
        for index, cue in emphasized:
            if len(selected) >= total_budget:
                break
            if index in selected or any(abs(index - previous) < 2 for previous in selected):
                continue
            try:
                bucket = max(0, int(float(cue.get("start") or 0) // 60))
            except (TypeError, ValueError):
                bucket = 0
            if selected_buckets.get(bucket, 0) >= bucket_limit:
                continue
            selected.append(index)
            selected_buckets[bucket] = selected_buckets.get(bucket, 0) + 1
    selected_set = set(selected)
    for index, cue in emphasized:
        if index not in selected_set:
            cue["emphasis_range"] = None
            cue["emphasis_style"] = None


def _kinetic_clean_text(value: object) -> str:
    return re.sub(
        r"[\s，。！？、,.!?；;：:‘’“”\"（）()【】[]《》…—]",
        "",
        _caption_display_cleanup(str(value or "")),
    )


def _caption_kinetic_semantic_color(
    segment: Mapping[str, Any],
    text: str,
) -> str:
    kind = str(segment.get("emphasis_kind") or "").lower()
    automatic = _automatic_emphasis_term(text)
    if kind not in _CAPTION_KINETIC_SEMANTIC_COLORS:
        kind = automatic[1] if automatic else "default"
    return _CAPTION_KINETIC_SEMANTIC_COLORS.get(
        kind,
        _CAPTION_KINETIC_SEMANTIC_COLORS["default"],
    )


def _caption_kinetic_words_for_cue(
    cue: Mapping[str, Any],
    segment: Mapping[str, Any],
    *,
    caption_glossary: object = None,
) -> list[dict[str, Any]]:
    """Project only the selected emphasis term onto a display cue.

    Ordinary cues intentionally return no spans.  The old implementation
    projected every lexical word in a one-line cue, which made the whole video
    read like a karaoke effect and also discarded two-line emphasis cues.
    """

    emphasis = cue.get("emphasis_range")
    if not isinstance(emphasis, Mapping):
        return []

    raw_words = segment.get("words")
    if not isinstance(raw_words, Sequence) or isinstance(raw_words, (str, bytes)):
        return []
    try:
        segment_start = float(segment.get("start") or 0)
        segment_end = float(segment.get("end") or 0)
        cue_start = float(cue.get("start") or 0)
        cue_end = float(cue.get("end") or 0)
    except (TypeError, ValueError):
        return []
    if cue_end <= cue_start or segment_end <= segment_start:
        return []
    lexical_words = _caption_lexical_words(
        raw_words,
        text=str(segment.get("text") or ""),
        segment_start=segment_start,
        segment_end=segment_end,
        caption_glossary=caption_glossary,
    )
    if len(lexical_words) < 1:
        return []
    lines = [str(line) for line in cue.get("lines") or []]
    if not lines:
        return []
    punctuation = r"[\s，。！？、,.!?；;：:‘’“”\"（）()【】[]《》…—]"
    character_locations: list[tuple[int, int]] = []
    normalized_characters: list[str] = []
    for line_index, display_line in enumerate(lines):
        for original_index, character in enumerate(display_line):
            if re.match(punctuation, character):
                continue
            normalized_characters.append(character)
            character_locations.append((line_index, original_index))
    normalized_line = "".join(normalized_characters)
    if not normalized_line:
        return []
    target = _kinetic_clean_text(normalized_line)
    candidates: list[tuple[float, list[dict[str, Any]]]] = []
    for begin in range(len(lexical_words)):
        collected: list[dict[str, Any]] = []
        accumulated = ""
        for item in lexical_words[begin:]:
            text = _kinetic_clean_text(item.get("text"))
            if not text:
                continue
            collected.append(item)
            accumulated += text
            if accumulated == target:
                first_start = float(collected[0].get("start") or cue_start)
                last_end = float(collected[-1].get("end") or cue_end)
                score = abs(first_start - cue_start) + abs(last_end - cue_end)
                candidates.append((score, collected))
                break
            if not target.startswith(accumulated):
                break
    if not candidates:
        return []
    _score, matched = min(candidates, key=lambda item: item[0])
    emphasis_style = cue.get("emphasis_style")
    semantic_color = _caption_kinetic_semantic_color(
        segment,
        display_line,
    )
    emphasis_color = (
        str(emphasis_style.get("color") or "")
        if isinstance(emphasis_style, Mapping)
        else ""
    )
    spans: list[dict[str, Any]] = []
    normalized_cursor = 0
    cue_duration = max(cue_end - cue_start, 0.001)
    for index, item in enumerate(matched):
        word = _kinetic_clean_text(item.get("text"))
        if not word:
            continue
        position = normalized_line.find(word, normalized_cursor)
        if position < 0 or position + len(word) > len(character_locations):
            return []
        line_index, original_start = character_locations[position]
        end_line_index, end_character = character_locations[position + len(word) - 1]
        if end_line_index != line_index:
            return []
        original_end = end_character + 1
        emphasis_line = emphasis.get("line_index")
        emphasis_start = emphasis.get("start")
        emphasis_end = emphasis.get("end")
        if not all(
            isinstance(value, int)
            for value in (emphasis_line, emphasis_start, emphasis_end)
        ):
            return []
        if (
            line_index != emphasis_line
            or original_end <= emphasis_start
            or original_start >= emphasis_end
        ):
            normalized_cursor = position + len(word)
            continue
        try:
            start = max(
                0.0,
                min(cue_duration, float(item.get("start") or cue_start) - cue_start),
            )
            end = max(
                start + 0.04,
                min(cue_duration, float(item.get("end") or cue_end) - cue_start),
            )
        except (TypeError, ValueError):
            return []
        if end <= start:
            continue
        color = semantic_color
        if (
            isinstance(emphasis, Mapping)
            and isinstance(emphasis.get("start"), int)
            and isinstance(emphasis.get("end"), int)
            and original_start < int(emphasis["end"])
            and original_end > int(emphasis["start"])
            and emphasis_color
        ):
            color = emphasis_color
        spans.append(
            {
                "line_index": line_index,
                "start_offset": original_start,
                "end_offset": original_end,
                "start": round(start, 3),
                "end": round(end, 3),
                "text": lines[line_index][original_start:original_end],
                "color": color,
                "kind": (
                    "emphasis"
                    if color == emphasis_color and emphasis_color
                    else "word"
                ),
            }
        )
        normalized_cursor = position + len(word)
    return spans


def _caption_kinetic_style_for_cue(
    cue: Mapping[str, Any],
    segment: Mapping[str, Any],
    cue_index: int,
) -> str:
    """Choose a small, semantic motion vocabulary instead of random effects."""

    text = _caption_display_cleanup(
        "".join(str(line) for line in cue.get("lines") or [])
    )
    emphasis_kind = str(segment.get("emphasis_kind") or "")
    emphasis_kind = emphasis_kind.lower()
    automatic = _automatic_emphasis_term(text)
    automatic_kind = automatic[1] if automatic else ""
    if cue_index == 0:
        return "slam"
    if emphasis_kind == "warning" or automatic_kind == "warning":
        return "shake"
    if emphasis_kind in {"number", "result"} or automatic_kind == "number":
        return "stamp"
    if emphasis_kind == "method":
        return "underline"
    if emphasis_kind == "cta":
        return "bounce"
    if emphasis_kind in {"benefit", "keyword"} or automatic_kind == "benefit":
        return "marker"
    if any(term in text for term in ("评论", "留言", "关注", "私信", "领取")):
        return "bounce"
    return "marker"


def _apply_caption_kinetic_words(
    cues: list[dict[str, Any]],
    segments: Sequence[Mapping[str, Any]],
    *,
    caption_glossary: object = None,
) -> None:
    for cue_index, cue in enumerate(cues):
        try:
            segment_index = int(cue.get("source_segment_index", -1))
        except (TypeError, ValueError):
            segment_index = -1
        segment = (
            segments[segment_index]
            if 0 <= segment_index < len(segments)
            and isinstance(segments[segment_index], Mapping)
            else None
        )
        has_emphasis = isinstance(cue.get("emphasis_range"), Mapping)
        is_opening_hook = cue_index == 0
        spans = (
            _caption_kinetic_words_for_cue(
                cue,
                segment,
                caption_glossary=caption_glossary,
            )
            if segment is not None and has_emphasis
            else []
        )
        cue["kinetic_words"] = spans
        if spans and segment is not None:
            cue["kinetic_mode"] = "word_pop"
            cue["kinetic_style"] = _caption_kinetic_style_for_cue(
                cue,
                segment,
                cue_index,
            )
            cue["motion_scope"] = "keyword"
        elif is_opening_hook:
            cue["kinetic_mode"] = "cue_pop"
            cue["kinetic_style"] = "slam"
            cue["motion_scope"] = "hook"
        else:
            cue["kinetic_mode"] = "static"
            cue["kinetic_style"] = None
            cue["motion_scope"] = "static"


def build_visual_beats(
    segments: Sequence[Mapping[str, Any]],
    caption_emphasis: Sequence[CaptionEmphasis | Mapping[str, Any]] | None = None,
    *,
    max_beats: int = 8,
) -> list[VisualBeat]:
    """Turn reviewed speech into a small automatic visual treatment plan.

    This is deliberately deterministic: the model may suggest emphasis, but
    timing and density are bounded here so a bad suggestion cannot flood the
    video with cards or cuts.
    """

    emphasis_by_segment: dict[int, str] = {}
    for raw in caption_emphasis or []:
        try:
            item = (
                raw
                if isinstance(raw, CaptionEmphasis)
                else CaptionEmphasis.model_validate(raw)
            )
        except (TypeError, ValueError):
            continue
        emphasis_by_segment[item.segment_index] = item.term

    beats: list[VisualBeat] = []
    last_selected_end = -999.0
    for index, raw in enumerate(segments):
        try:
            start = max(0.0, float(raw.get("start", 0)))
            end = max(start, float(raw.get("end", 0)))
        except (TypeError, ValueError):
            continue
        text = _clean_caption_text(str(raw.get("text") or ""))
        if not text or end - start < 0.35:
            continue
        emphasis_term = emphasis_by_segment.get(index)
        if not emphasis_term:
            auto = _automatic_emphasis_term(text)
            emphasis_term = auto[0] if auto else ""
        is_first = not beats
        has_visual_reason = is_first or bool(emphasis_term) or start - last_selected_end >= 5.5
        if not has_visual_reason:
            continue
        treatment = "hook" if is_first else "keyword_card" if emphasis_term else "punch_in"
        label = (emphasis_term or text[:10]).strip()[:12]
        beats.append(
            VisualBeat(
                segment_index=index,
                start=round(start, 3),
                end=round(end, 3),
                treatment=treatment,
                label=label,
                emphasis_term=emphasis_term[:6],
            )
        )
        last_selected_end = end
        if len(beats) >= max(1, max_beats):
            break
    return beats


def build_business_talking_head_overlay_preview(
    segments: Sequence[Mapping[str, Any]],
    *,
    title: str,
    output_profile: str | "OutputProfile",
    caption_groups: object = None,
    caption_emphasis: object = None,
    spoken_ranges: object = None,
    caption_glossary: object = None,
    subtitle_style_id: str = "adaptive_talking_head_v1",
) -> dict[str, Any]:
    """Normalize title/caption lines once for browser preview and ASS rendering."""

    spec = visual_style_spec(output_profile)
    title_style = spec["title"]
    caption_style = spec["subtitle"]
    normalized_caption_glossary = _normalize_caption_glossary(caption_glossary)
    title_lines = _display_lines(
        title,
        chars_per_line=title_style["max_chars_per_line"],
        max_lines=title_style["max_lines"],
        truncate=True,
    )
    cues: list[dict[str, Any]] = []
    # Group by the single-line phone width.  The renderer may still wrap an
    # unusually long semantic phrase to two lines, but grouping at the full
    # two-line width would hide natural cue boundaries and make established
    # one-line caption contracts regress.
    max_caption_chars = caption_style["max_chars_per_line"]
    semantic_groups = validated_caption_groups(
        caption_groups,
        segments,
        max_chars=max_caption_chars,
    )
    semantic_parts = {
        group.segment_index: list(group.parts) for group in semantic_groups
    }
    approved_emphasis = validated_caption_emphasis(
        caption_emphasis,
        segments,
        caption_groups=semantic_groups,
    )
    emphasis_by_segment = {item.segment_index: item for item in approved_emphasis}
    for segment_index, segment in enumerate(segments):
        try:
            start = float(segment.get("start", 0))
            end = float(segment.get("end", 0))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        duration_grouping_applied = False
        chunks = semantic_parts.get(segment_index)
        if not chunks:
            text = str(segment.get("text") or "")
            duration = end - start
            # Keep the established spoken-range/semantic boundaries whenever
            # their resulting cues are readable.  The page's reordered clock
            # can occasionally leave a tiny tail cue; only that invalid
            # candidate falls back to the duration-balanced grouping path.
            range_chunks = _caption_chunks(
                text,
                max_chars=max_caption_chars,
                caption_glossary=normalized_caption_glossary,
            )
            range_timings = _caption_cue_timings(
                range_chunks,
                segment_start=start,
                segment_end=end,
                spoken_ranges=spoken_ranges,
            )
            range_durations = [
                cue_end - cue_start for cue_start, cue_end in range_timings
            ]
            range_minimum = 0.75 if spoken_ranges else _CAPTION_MIN_DURATION_SECONDS
            readable_range_clock = bool(range_chunks) and all(
                range_minimum - 1e-6
                <= cue_duration
                <= _CAPTION_MAX_DURATION_SECONDS + 1e-6
                for cue_duration in range_durations
            )
            # A normal ASR sentence with five or more natural atoms is too
            # dense for a phone.  Rebalance those cases to the sentence clock;
            # keep longer, already-readable story/business clauses intact.
            force_duration_grouping = len(range_chunks) > 4 and (
                duration <= 9.6 or len(range_chunks) > 10
            )
            if (
                len(range_chunks) == 4
                and duration <= 6.5
                and len(_caption_phrases(text)) <= 2
            ):
                balanced_count = len(
                    _caption_chunks_for_duration(
                        text,
                        duration_seconds=duration,
                        max_chars=max_caption_chars,
                        caption_glossary=normalized_caption_glossary,
                    )
                )
                force_duration_grouping = balanced_count < len(range_chunks)
            if readable_range_clock and not force_duration_grouping:
                chunks = range_chunks
            else:
                chunks = _caption_chunks_for_duration(
                    text,
                    duration_seconds=duration,
                    max_chars=max_caption_chars,
                    caption_glossary=normalized_caption_glossary,
                )
                duration_grouping_applied = True
        # When exact word clocks are available, keep them authoritative for
        # order and relative timing, but do not let a character-sized token
        # boundary override a reviewed semantic beat.  The duration helper
        # only merges natural atoms; it never rewrites text,
        # and the word clock is still used below (with bounded rebalancing when
        # a merged clause is just over the 2.4-second display ceiling).
        # Map both normal ASR tokens and character-split tokens onto the same
        # lexical units.  A provider may emit ``业`` + ``务员`` or one
        # character per token; neither representation is a legal display
        # boundary inside a configured compound word.
        word_clock_words = _caption_lexical_words(
            segment.get("words"),
            text=str(segment.get("text") or ""),
            segment_start=start,
            segment_end=end,
            caption_glossary=normalized_caption_glossary,
        )
        has_segment_word_clocks = bool(word_clock_words)
        word_clock_source: object = word_clock_words or segment.get("words")
        semantic_word_grouping_applied = duration_grouping_applied
        if not semantic_groups and has_segment_word_clocks:
            natural_word_chunks = _caption_chunks(
                str(segment.get("text") or ""),
                max_chars=max_caption_chars,
                caption_glossary=normalized_caption_glossary,
            )
            duration = end - start
            if duration >= _CAPTION_MIN_DURATION_SECONDS * 2 and duration <= 9.6:
                target_hint = min(
                    4,
                    max(2, math.ceil(duration / _CAPTION_MAX_DURATION_SECONDS)),
                )
                candidate_counts = range(
                    2,
                    min(4, len(natural_word_chunks)) + 1,
                )
                candidates: list[
                    tuple[tuple[int, int, float, int, int, int], list[str]]
                ] = []
                for candidate_count in candidate_counts:
                    candidate = _partition_caption_atoms(
                        natural_word_chunks,
                        candidate_count,
                    )
                    texts = [_caption_display_cleanup(item) for item in candidate]
                    candidate_timings = _caption_cue_timings_from_words(
                        candidate,
                        word_clock_source,
                        segment_start=start,
                        segment_end=end,
                    )
                    if candidate_timings:
                        word_timing_penalty = sum(
                            max(
                                0.0,
                                _CAPTION_MIN_DURATION_SECONDS
                                - (cue_end - cue_start),
                            )
                            + max(
                                0.0,
                                (cue_end - cue_start)
                                - _CAPTION_MAX_DURATION_SECONDS,
                            )
                            for cue_start, cue_end in candidate_timings
                        )
                    else:
                        # Keep an unmatchable candidate available for the
                        # sentence/semantic fallback, but prefer any candidate
                        # whose real word clock can be consumed exactly.
                        word_timing_penalty = float("inf")
                    orphaned_boundary = any(
                        left.endswith(_CAPTION_BAD_LINE_ENDINGS)
                        and right.startswith(_CAPTION_BAD_LINE_STARTS)
                        for left, right in zip(texts, texts[1:])
                    )
                    two_line_count = sum(
                        len(
                            _caption_display_lines(
                                text,
                                chars_per_line=max_caption_chars,
                            )
                        )
                        > 1
                        for text in texts
                    )
                    short_count = sum(len(text) <= 2 for text in texts)
                    candidates.append(
                        (
                            (
                                int(orphaned_boundary),
                                int(word_timing_penalty > 0),
                                word_timing_penalty,
                                two_line_count,
                                short_count,
                                abs(candidate_count - target_hint),
                            ),
                            candidate,
                        )
                    )
                if candidates:
                    _, chunks = min(candidates, key=lambda item: item[0])
                    semantic_word_grouping_applied = True
        word_timings = _caption_cue_timings_from_words(
            chunks,
            word_clock_source,
            segment_start=start,
            segment_end=end,
        )
        if not word_timings:
            # A reviewed ASR correction can make the first natural grouping
            # impossible to consume even though the ordered source words are
            # still mappable. Try the same bounded lexical partition before
            # falling back to sentence timing; this is what prevents a
            # legitimate word-clock segment from becoming unverified.
            exact_partition = _word_clock_readable_partition(
                segment.get("words"),
                text=str(segment.get("text") or ""),
                segment_start=start,
                segment_end=end,
                caption_glossary=normalized_caption_glossary,
            )
            if exact_partition is not None:
                chunks, word_timings = exact_partition
                semantic_word_grouping_applied = True
        if word_timings:
            # Exact word clocks remain authoritative, but their first
            # character-based grouping can still leave a long final cue.
            # Re-partition only at existing semantic/word boundaries and
            # retain the candidate whose exact timings best satisfy the
            # readable 0.9--2.4 second contract.
            def timing_penalty(timings: Sequence[tuple[float, float]]) -> float:
                return sum(
                    max(0.0, _CAPTION_MIN_DURATION_SECONDS - (cue_end - cue_start))
                    + max(0.0, (cue_end - cue_start) - _CAPTION_MAX_DURATION_SECONDS)
                    for cue_start, cue_end in timings
                )

            best_timings = word_timings
            best_penalty = timing_penalty(word_timings)
            exact_partition = _word_clock_readable_partition(
                segment.get("words"),
                text=str(segment.get("text") or ""),
                segment_start=start,
                segment_end=end,
                caption_glossary=normalized_caption_glossary,
            )
            if exact_partition is not None:
                candidate_chunks, candidate_timings = exact_partition
                if (
                    best_penalty > 0
                    or (end - start) > (_CAPTION_MAX_DURATION_SECONDS * 4)
                    or any(
                        compound in str(segment.get("text") or "")
                        for compound in normalized_caption_glossary
                    )
                ):
                    chunks, best_timings = candidate_chunks, candidate_timings
                    best_penalty = 0.0
                    semantic_word_grouping_applied = True
                if not semantic_word_grouping_applied:
                    base_chunks = _caption_chunks(
                        str(segment.get("text") or ""),
                        max_chars=max_caption_chars,
                        caption_glossary=normalized_caption_glossary,
                    )
                    for target_count in range(len(chunks) + 1, 5):
                        if target_count > len(base_chunks):
                            continue
                        candidate_chunks = _partition_caption_atoms(
                            base_chunks, target_count
                        )
                        candidate_timings = _caption_cue_timings_from_words(
                            candidate_chunks,
                            word_clock_source,
                            segment_start=start,
                            segment_end=end,
                        )
                        if not candidate_timings:
                            continue
                        candidate_penalty = timing_penalty(candidate_timings)
                        # Prefer a genuinely passing semantic regrouping.  A
                        # merely smaller penalty is not worth manufacturing a
                        # short orphan such as ``数据库`` or ``产品``.
                        if candidate_penalty <= 0:
                            chunks = candidate_chunks
                            best_timings = candidate_timings
                            best_penalty = candidate_penalty
                            break
                if best_penalty > 0:
                    best_timings = _rebalance_word_cue_timings(
                        best_timings,
                        segment_start=start,
                        segment_end=end,
                    )
            cue_timings = best_timings
        else:
            cue_timings = _caption_cue_timings(
                chunks,
                segment_start=start,
                segment_end=end,
                spoken_ranges=spoken_ranges,
            )
        for index, chunk in enumerate(chunks):
            cue_start, cue_end = (
                round(cue_timings[index][0], 3),
                round(cue_timings[index][1], 3),
            )
            # Decimal rounding can make an otherwise exact 2.4s span compare
            # as 2.4000000000000004 in Python. Trim only that floating-point
            # residue; genuine overlong cues remain subject to the hard gate.
            if cue_end - cue_start >= _CAPTION_MAX_DURATION_SECONDS - 1e-9:
                cue_end = math.nextafter(
                    cue_start + _CAPTION_MAX_DURATION_SECONDS,
                    cue_start,
                )
            display_chunk = _caption_display_cleanup(chunk)
            lines = _caption_display_lines(
                display_chunk,
                chars_per_line=caption_style["max_chars_per_line"],
            )
            ai_emphasis = emphasis_by_segment.get(segment_index)
            manual_terms = _normalized_emphasis_terms(segment)
            clean_chunk = display_chunk
            active_manual_terms = [term for term in manual_terms if term in clean_chunk]
            active_ai_term = (
                ai_emphasis.term
                if ai_emphasis is not None and ai_emphasis.term in clean_chunk
                else ""
            )
            automatic = _automatic_emphasis_term(clean_chunk)
            terms = active_manual_terms or (
                [active_ai_term]
                if active_ai_term
                else ([automatic[0]] if automatic is not None else [])
            )
            emphasis = _emphasis_range(lines, terms)
            # Never let the optional highlighted phrase split across caption
            # lines: a split highlight reads poorly on a phone screen.
            if terms and emphasis is None:
                term_start = clean_chunk.find(terms[0])
                chars_per_line = caption_style["max_chars_per_line"]
                if (
                    term_start > 0
                    and term_start <= chars_per_line
                    and len(clean_chunk) - term_start <= chars_per_line
                    and term_start % chars_per_line + len(terms[0]) > chars_per_line
                ):
                    candidate = _display_lines(
                        clean_chunk[:term_start],
                        chars_per_line=chars_per_line,
                        max_lines=1,
                    ) + _display_lines(
                        clean_chunk[term_start:],
                        chars_per_line=chars_per_line,
                        max_lines=1,
                    )
                    if len(candidate) <= caption_style["max_lines"]:
                        lines = candidate
                        emphasis = _emphasis_range(lines, terms)
            cues.append(
                {
                    "start": cue_start,
                    "end": cue_end,
                    "lines": lines,
                    "emphasis_range": emphasis,
                    "emphasis_style": (
                        _caption_emphasis_style(
                            str(segment.get("emphasis_kind") or "keyword")
                            if active_manual_terms
                            else (
                                ai_emphasis.kind
                                if active_ai_term and ai_emphasis is not None
                                else (
                                    automatic[1] if automatic is not None else "keyword"
                                )
                            )
                        )
                        if emphasis is not None
                        else None
                    ),
                    "_segment_index": segment_index,
                    "source_segment_index": segment_index,
                    "lexical_boundary_exception": None,
                    "word_clock_mapping": (
                        "exact_or_reviewed_text_sequence_alignment"
                        if word_timings
                        else None
                    ),
                }
            )
    # Preserve the exact result of the generic lexical partition after any
    # shot/word regrouping.  Preview and ASS consume this same cue list; no
    # sentence-specific post-processing is allowed in the production path.
    semantic_rebuilt: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(segments):
        segment_cues = [
            cue for cue in cues
            if int(cue.get("source_segment_index", -1)) == segment_index
        ]
        if not segment_cues:
            continue
        semantic_rebuilt.extend(segment_cues)
    if semantic_rebuilt:
        cues = sorted(semantic_rebuilt, key=lambda cue: float(cue.get("start") or 0))

    # Semantic groups can be supplied by an upstream planner.  Keep them
    # useful only when they form an ordered projection of the source text:
    # older planners occasionally repeated the last character of one group
    # as the first character of the next group.  Remove that mechanical
    # overlap at the source cursor (without rewriting or reordering any
    # spoken characters) before the immutable word clock is applied.
    normalized_cues = _normalize_preview_cue_texts(
        cues,
        segments,
        max_chars=max_caption_chars,
    )
    if normalized_cues:
        cues = sorted(
            normalized_cues,
            key=lambda cue: float(cue.get("start") or 0),
        )

    # Once semantic text is settled, snap every readable group back to the
    # same contiguous lexical word spans used by the burn renderer.  Do not
    # apply this snap when it would create a sub-minimum dwell cue; that case
    # remains an explicit estimated/rebalanced exception instead of moving a
    # caption boundary through a word.
    for segment_index, segment in enumerate(segments):
        segment_cues = [
            cue for cue in cues
            if int(cue.get("source_segment_index", -1)) == segment_index
        ]
        if not segment_cues or not segment.get("words"):
            continue
        range_start = min(float(cue.get("start") or 0) for cue in segment_cues)
        range_end = max(float(cue.get("end") or 0) for cue in segment_cues)
        lexical_words = _caption_lexical_words(
            segment.get("words"),
            text=str(segment.get("text") or ""),
            segment_start=range_start,
            segment_end=range_end,
            caption_glossary=normalized_caption_glossary,
        )
        exact = _caption_cue_timings_from_words(
            ["".join(str(line) for line in cue.get("lines") or []) for cue in segment_cues],
            lexical_words or segment.get("words"),
            segment_start=range_start,
            segment_end=range_end,
        )
        # Apply the real lexical clock to every cue, including a genuinely
        # short word span.  Rejecting the whole segment when one cue is under
        # the readability dwell threshold made the remaining cues fall back
        # to the old proportional clock and introduced large boundary drift.
        # The downstream experience gate already records the short-source
        # exception explicitly; it must not erase timing truth for siblings.
        if exact:
            for cue, (cue_start, cue_end) in zip(segment_cues, exact, strict=True):
                normalized_start = round(cue_start, 3)
                normalized_end = round(cue_end, 3)
                if normalized_end - normalized_start >= _CAPTION_MAX_DURATION_SECONDS - 1e-9:
                    normalized_end = math.nextafter(
                        normalized_start + _CAPTION_MAX_DURATION_SECONDS,
                        normalized_start,
                    )
                cue["start"] = normalized_start
                cue["end"] = normalized_end

    # A two-line result is a last-resort display wrap, not an approved default
    # for the talking-head template.  Re-expand it into semantic one-line cues
    # using the same clock before either preview or ASS rendering sees it.
    one_line_cues: list[dict[str, Any]] = []
    for cue in cues:
        lines = [str(line) for line in cue.get("lines") or []]
        if len(lines) <= 1:
            one_line_cues.append(cue)
            continue
        clean_text = _caption_display_cleanup("".join(lines))
        # Keep a complete semantic block on two lines when it is only
        # slightly wider than the phone line width.  This is an explicit,
        # inspectable exception; splitting it into several one-line flashes
        # would create less readable fragments and inflate cue count.
        if (
            len(clean_text) <= max_caption_chars * 2
            and all(len(line.strip()) >= 3 for line in lines)
            and not any(
                clean_text.endswith(suffix)
                for suffix in _CAPTION_BAD_LINE_ENDINGS
            )
            and not any(
                clean_text.startswith(prefix)
                for prefix in _CAPTION_BAD_LINE_STARTS
            )
        ):
            cue["lines"] = [clean_text[:max_caption_chars], clean_text[max_caption_chars:]]
            cue["two_line_exception"] = True
            one_line_cues.append(cue)
            continue
        parts = _caption_chunks(
            clean_text,
            max_chars=max_caption_chars,
            caption_glossary=normalized_caption_glossary,
        )
        if len(parts) <= 1:
            one_line_cues.append(cue)
            continue
        start = float(cue.get("start") or 0)
        end = float(cue.get("end") or 0)
        total = sum(len(part) for part in parts) or 1
        total_duration = end - start
        durations = [total_duration * len(part) / total for part in parts]
        if total_duration >= _CAPTION_MIN_DURATION_SECONDS * len(parts):
            for index, duration in enumerate(durations):
                if duration < _CAPTION_MIN_DURATION_SECONDS:
                    deficit = _CAPTION_MIN_DURATION_SECONDS - duration
                    durations[index] = _CAPTION_MIN_DURATION_SECONDS
                    donors = [
                        donor
                        for donor, value in enumerate(durations)
                        if donor != index and value > _CAPTION_MIN_DURATION_SECONDS
                    ]
                    for donor in donors:
                        transfer = min(deficit, durations[donor] - _CAPTION_MIN_DURATION_SECONDS)
                        durations[donor] -= transfer
                        deficit -= transfer
                        if deficit <= 1e-6:
                            break
        cursor = start
        for index, part in enumerate(parts):
            next_cursor = end if index == len(parts) - 1 else cursor + durations[index]
            split_cue = dict(cue)
            split_cue["start"] = round(cursor, 3)
            split_cue["end"] = round(next_cursor, 3)
            split_cue["lines"] = [part]
            split_cue["emphasis_range"] = None
            split_cue["emphasis_style"] = None
            one_line_cues.append(split_cue)
            cursor = next_cursor
    cues = one_line_cues
    # One-line expansion can create new cue objects from a wrapped phrase.
    # Normalize once more at the final text boundary so punctuation and an
    # upstream repeated boundary character cannot return after the first
    # projection pass. The subsequent lexical-clock pass then times this
    # exact final cue list.
    cues = _normalize_preview_cue_texts(
        cues,
        segments,
        max_chars=max_caption_chars,
    )
    has_word_timestamps = any(
        isinstance(segment.get("words"), Sequence)
        and not isinstance(segment.get("words"), (str, bytes))
        and segment.get("words")
        for segment in segments
        if isinstance(segment, Mapping)
    )
    lexical_word_timestamps = any(
        any(
            len(re.sub(r"\s+", "", str(item.get("text") or item.get("word") or ""))) > 1
            for item in segment.get("words") or []
            if isinstance(item, Mapping)
        )
        for segment in segments
        if isinstance(segment, Mapping) and segment.get("words")
    )
    if any(
        float(cue.get("end") or 0) - float(cue.get("start") or 0)
        < _CAPTION_MIN_DURATION_SECONDS
        for cue in cues
    ):
        cues = _merge_short_estimated_cues(
            cues,
            min_duration=_CAPTION_MIN_DURATION_SECONDS,
            max_chars=14,
        )

    # The final cue list is the immutable source for both preview and ASS.
    # Re-apply exact lexical clocks after the last merge so a readable
    # semantic grouping cannot drift when a neighbouring estimated cue was
    # rebalanced.  This is deliberately conservative: a snap is accepted
    # only when every resulting cue keeps the minimum dwell time.
    if has_word_timestamps:
        for segment_index, segment in enumerate(segments):
            if not isinstance(segment, Mapping) or not segment.get("words"):
                continue
            segment_cues = [
                cue for cue in cues
                if int(cue.get("source_segment_index", -1)) == segment_index
            ]
            if not segment_cues:
                continue
            range_start = min(float(cue.get("start") or 0) for cue in segment_cues)
            range_end = max(float(cue.get("end") or 0) for cue in segment_cues)
            lexical_words = _caption_lexical_words(
                segment.get("words"),
                text=str(segment.get("text") or ""),
                segment_start=range_start,
                segment_end=range_end,
                caption_glossary=normalized_caption_glossary,
            )
            exact = _caption_cue_timings_from_words(
                ["".join(str(line) for line in cue.get("lines") or []) for cue in segment_cues],
                lexical_words or segment.get("words"),
                segment_start=range_start,
                segment_end=range_end,
            )
            if exact and all(
                end - start >= _CAPTION_MIN_DURATION_SECONDS - 1e-6
                for start, end in exact
            ):
                for cue, (cue_start, cue_end) in zip(segment_cues, exact, strict=True):
                    normalized_start = round(cue_start, 3)
                    normalized_end = round(cue_end, 3)
                    if normalized_end - normalized_start >= _CAPTION_MAX_DURATION_SECONDS - 1e-9:
                        normalized_end = math.nextafter(
                            normalized_start + _CAPTION_MAX_DURATION_SECONDS,
                            normalized_start,
                        )
                    cue["start"] = normalized_start
                    cue["end"] = normalized_end

        # A valid lexical boundary may still leave one cue just over the
        # dwell ceiling after a provider pause or reviewed-text alignment.
        # Split only at mapped lexical units and only when both resulting
        # spans satisfy the same 0.9--2.4s contract.  This is deliberately
        # content-agnostic: no sentence or customer sample is privileged.
        split_cues: list[dict[str, Any]] = []
        for cue in cues:
            cue_start = float(cue.get("start") or 0)
            cue_end = float(cue.get("end") or 0)
            try:
                segment_index = int(cue.get("source_segment_index", -1))
            except (TypeError, ValueError):
                split_cues.append(cue)
                continue
            if not 0 <= segment_index < len(segments):
                split_cues.append(cue)
                continue
            segment = segments[segment_index]
            all_lexical_words = _caption_lexical_words(
                segment.get("words"),
                text=str(segment.get("text") or ""),
                segment_start=float(segment.get("start") or 0),
                segment_end=float(segment.get("end") or 0),
                caption_glossary=normalized_caption_glossary,
            )
            source_text = "".join(str(line) for line in cue.get("lines") or [])

            def clock_match(value: str) -> str:
                return re.sub(
                    r"[\s，。！？、,.!?；;：:]+", "", value
                ).replace("呢", "").replace("的", "")

            target_text = clock_match(source_text)
            lexical_words: list[dict[str, Any]] = []
            # A cue can begin after a filler token that still belongs to its
            # spoken phrase.  Locate the text in the complete lexical stream
            # first, then use the matched words for the split; filtering only
            # by the current cue's clock would lose that prefix.
            for begin in range(len(all_lexical_words)):
                collected: list[dict[str, Any]] = []
                for item in all_lexical_words[begin:]:
                    collected.append(item)
                    if clock_match(
                        "".join(str(value.get("text") or "") for value in collected)
                    ) == target_text:
                        lexical_words = collected
                        break
                    if not target_text.startswith(
                        clock_match(
                            "".join(str(value.get("text") or "") for value in collected)
                        )
                    ):
                        break
                if lexical_words:
                    break
            if len(lexical_words) < 2:
                split_cues.append(cue)
                continue
            candidates: list[tuple[float, int, list[str], list[tuple[float, float]]]] = []
            lexical_start = float(lexical_words[0].get("start") or cue_start)
            lexical_end = float(lexical_words[-1].get("end") or cue_end)
            # A readable cue can be under the display ceiling while its real
            # word span crosses a provider pause and exceeds the ceiling.  In
            # that case it still needs a lexical-boundary split; otherwise the
            # later clock audit has to preserve an estimated edge and reports
            # a false-looking multi-frame mapping error.
            if (
                cue_end - cue_start <= _CAPTION_MAX_DURATION_SECONDS + 1e-6
                and lexical_end - lexical_start
                <= _CAPTION_MAX_DURATION_SECONDS + 1e-6
            ):
                split_cues.append(cue)
                continue
            midpoint = (lexical_start + lexical_end) / 2
            for cut in range(1, len(lexical_words)):
                parts = [
                    "".join(str(item.get("text") or "") for item in lexical_words[:cut]),
                    "".join(str(item.get("text") or "") for item in lexical_words[cut:]),
                ]
                if clock_match("".join(parts)) != target_text:
                    continue
                timings = _caption_cue_timings_from_words(
                    parts,
                    lexical_words,
                    segment_start=lexical_start,
                    segment_end=lexical_end,
                )
                if not timings:
                    continue
                durations = [end - start for start, end in timings]
                if not all(
                    _CAPTION_MIN_DURATION_SECONDS - 1e-6 <= duration <= _CAPTION_MAX_DURATION_SECONDS + 1e-6
                    for duration in durations
                ):
                    continue
                candidates.append((abs(timings[0][1] - midpoint), cut, parts, timings))
            if not candidates:
                split_cues.append(cue)
                continue
            _distance, _cut, parts, timings = min(candidates, key=lambda item: item[0])
            for part, (part_start, part_end) in zip(parts, timings, strict=True):
                split_cue = dict(cue)
                split_cue.update(
                    {
                        "start": round(part_start, 3),
                        "end": round(part_end, 3),
                        "lines": [_caption_display_cleanup(part)],
                        "emphasis_range": None,
                        "emphasis_style": None,
                        "lexical_boundary_exception": None,
                    }
                )
                split_cues.append(split_cue)
        cues = split_cues

    # The lexical over-ceiling splitter above may create fresh cue objects
    # from its word parts. Re-run the source projection after that last text
    # transformation, then reapply the same exact word clock so the returned
    # manifest is the final audited cue list rather than an intermediate one.
    cues = _normalize_preview_cue_texts(
        cues,
        segments,
        max_chars=max_caption_chars,
    )
    if has_word_timestamps:
        for segment_index, segment in enumerate(segments):
            if not isinstance(segment, Mapping) or not segment.get("words"):
                continue
            segment_cues = [
                cue
                for cue in cues
                if int(cue.get("source_segment_index", -1)) == segment_index
            ]
            if not segment_cues:
                continue
            range_start = min(float(cue.get("start") or 0) for cue in segment_cues)
            range_end = max(float(cue.get("end") or 0) for cue in segment_cues)
            lexical_words = _caption_lexical_words(
                segment.get("words"),
                text=str(segment.get("text") or ""),
                segment_start=range_start,
                segment_end=range_end,
                caption_glossary=normalized_caption_glossary,
            )
            exact = _caption_cue_timings_from_words(
                [
                    "".join(str(line) for line in cue.get("lines") or [])
                    for cue in segment_cues
                ],
                lexical_words or segment.get("words"),
                segment_start=range_start,
                segment_end=range_end,
            )
            if exact:
                for cue, (cue_start, cue_end) in zip(
                    segment_cues,
                    exact,
                    strict=True,
                ):
                    normalized_start = round(cue_start, 3)
                    normalized_end = round(cue_end, 3)
                    if normalized_end - normalized_start >= _CAPTION_MAX_DURATION_SECONDS - 1e-9:
                        normalized_end = math.nextafter(
                            normalized_start + _CAPTION_MAX_DURATION_SECONDS,
                            normalized_start,
                        )
                    cue["start"] = normalized_start
                    cue["end"] = normalized_end

    if has_word_timestamps and not lexical_word_timestamps:
        # Character-by-character ASR fixtures are useful for estimating phrase
        # clocks, but they are not genuine lexical word timestamps.  Keep the
        # semantic grouping and cap a long compound cue without claiming word
        # precision.
        for cue in cues:
            cue_duration = float(cue.get("end") or 0) - float(cue.get("start") or 0)
            if cue_duration > _CAPTION_MAX_DURATION_SECONDS:
                proposed_start = float(cue.get("end") or 0) - _CAPTION_MAX_DURATION_SECONDS
                cue["start"] = (
                    math.nextafter(proposed_start, float(cue.get("end") or 0))
                    if cue_duration <= _CAPTION_MAX_DURATION_SECONDS + 1e-6
                    else round(proposed_start, 3)
                )
    if not has_word_timestamps:
        cues = _merge_short_estimated_cues(cues)
    else:
        for cue in cues:
            cue.pop("_segment_index", None)
    _apply_adaptive_caption_effects(cues)
    _apply_caption_kinetic_words(
        cues,
        segments,
        caption_glossary=normalized_caption_glossary,
    )
    return {
        "title": {
            "lines": title_lines,
            "start": 0,
            "end": float(title_style["visible_seconds"]),
        },
        "cues": cues,
        "caption_group_source": (
            "qwen_semantic" if semantic_groups else "deterministic_fallback"
        ),
        "phrase_timing_source": (
            "word_timestamps"
            if lexical_word_timestamps
            else "estimated_phrase_timestamps"
        ),
        "subtitle_style_id": subtitle_style_id,
        "style_fingerprint": {
            "font_family": "Source Han Serif CN Heavy",
            "palette_id": "douyin_talking_head_pop_v1",
            "entry_motion": "fade_in_120ms",
            "word_motion": "selective_word_emphasis_v3",
            "keyword_motion": "semantic_effect_mix_v2",
            "kinetic_styles": list(_CAPTION_KINETIC_STYLE_IDS),
            "caption_motion_policy": "static_by_default_selective_semantic_emphasis",
            "strong_effect_density": "3_to_5_per_60s",
            "emphasis_scale_range": [1.08, 1.08],
            "emphasis_duration_ms": [180, 240],
        },
    }


def _hex_to_ass_colour(value: str) -> str:
    clean = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", clean):
        return "&H006AE1FF&"
    red, green, blue = clean[0:2], clean[2:4], clean[4:6]
    return f"&H00{blue}{green}{red}&"


def _ass_kinetic_caption_text(
    line: str,
    cue: Mapping[str, Any],
    *,
    line_index: int = 0,
    cue_start: float,
    cue_end: float,
) -> str | None:
    spans = [
        item
        for item in cue.get("kinetic_words") or []
        if isinstance(item, Mapping)
        and item.get("line_index") == line_index
    ]
    if not spans:
        return None
    spans = sorted(
        spans,
        key=lambda item: int(item.get("start_offset") or 0),
    )
    if any(
        int(item.get("start_offset") or 0) < 0
        or int(item.get("end_offset") or 0) <= int(item.get("start_offset") or 0)
        or int(item.get("end_offset") or 0) > len(line)
        for item in spans
    ):
        return None
    if any(
        int(current.get("start_offset") or 0)
        < int(previous.get("end_offset") or 0)
        for previous, current in zip(spans, spans[1:])
    ):
        return None
    duration_ms = max(1, round((cue_end - cue_start) * 1000))
    kinetic_style = str(cue.get("kinetic_style") or "bounce")
    rendered: list[str] = []
    cursor = 0
    for index, span in enumerate(spans):
        start_offset = int(span.get("start_offset") or 0)
        end_offset = int(span.get("end_offset") or 0)
        rendered.append(_ass_escape(line[cursor:start_offset]))
        try:
            word_start_ms = max(0, min(duration_ms, round(float(span.get("start") or 0) * 1000)))
            word_end_ms = max(word_start_ms + 40, min(duration_ms, round(float(span.get("end") or 0) * 1000)))
        except (TypeError, ValueError):
            return None
        # Keep a short emphasis window; the cue is static before and after it.
        impact_end_ms = min(word_end_ms, word_start_ms + 200)
        settle_end_ms = min(duration_ms, max(impact_end_ms + 40, word_end_ms))
        color = _hex_to_ass_colour(str(span.get("color") or "#FFE16A"))
        if kinetic_style == "slam":
            motion = (
                f"\\t({word_start_ms},{min(duration_ms, word_start_ms + 1)},"
                f"\\fscx108\\fscy92\\frz4\\blur1.2\\alpha&H30&))"
                f"\\t({min(duration_ms, word_start_ms + 1)},{impact_end_ms},"
                f"\\c&H00F8FAFC&\\3c{color}\\bord5.0\\shad3\\blur0.2"
                f"\\fscx108\\fscy108\\frz-1\\alpha&H00&))"
                f"\\t({impact_end_ms},{settle_end_ms},"
                f"\\c&H00F8FAFC&\\3c&H003A263D&\\bord2.8\\shad1\\blur0"
                f"\\fscx100\\fscy100\\frz0)"
            )
        elif kinetic_style == "stamp":
            motion = (
                f"\\t({word_start_ms},{min(duration_ms, word_start_ms + 1)},"
                f"\\fscx108\\fscy100\\frz-4\\blur1.2)"
                f"\\t({min(duration_ms, word_start_ms + 1)},{impact_end_ms},"
                f"\\c&H00F8FAFC&\\3c{color}\\bord5.4\\shad3\\blur0.1"
                f"\\fscx108\\fscy108\\frz2)"
                f"\\t({impact_end_ms},{settle_end_ms},"
                f"\\c&H00F8FAFC&\\3c&H003A263D&\\bord2.8\\shad1\\blur0"
                f"\\fscx100\\fscy100\\frz0)"
            )
        elif kinetic_style == "marker":
            motion = (
                f"\\t({word_start_ms},{min(duration_ms, word_start_ms + 1)},"
                f"\\fscx90\\fscy90\\blur1.8)"
                f"\\t({min(duration_ms, word_start_ms + 1)},{impact_end_ms},"
                f"\\c&H00F8FAFC&\\3c{color}\\bord5.8\\shad2\\blur0"
                f"\\fscx108\\fscy108)"
                f"\\t({impact_end_ms},{settle_end_ms},"
                f"\\c&H00F8FAFC&\\3c&H003A263D&\\bord2.8\\shad1"
                f"\\fscx100\\fscy100)"
            )
        elif kinetic_style == "underline":
            motion = (
                f"\\t({word_start_ms},{min(duration_ms, word_start_ms + 1)},"
                f"\\fscx96\\fscy96\\blur1.2)"
                f"\\t({min(duration_ms, word_start_ms + 1)},{impact_end_ms},"
                f"\\c&H00F8FAFC&\\3c{color}\\bord3.4\\shad2\\u1\\blur0"
                f"\\fscx108\\fscy108)"
                f"\\t({impact_end_ms},{settle_end_ms},"
                f"\\c&H00F8FAFC&\\3c&H003A263D&\\bord2.8\\shad1\\u0"
                f"\\fscx100\\fscy100)"
            )
        elif kinetic_style == "shake":
            shake_start = min(duration_ms, word_start_ms + 1)
            shake_mid = min(duration_ms, word_start_ms + 56)
            motion = (
                f"\\t({word_start_ms},{shake_start},\\fscx108\\fscy108\\frz4)"
                f"\\t({shake_start},{shake_mid},\\c&H00F8FAFC&\\3c{color}"
                f"\\bord4.8\\shad2\\blur0\\fscx108\\fscy108\\frz-2)"
                f"\\t({shake_mid},{impact_end_ms},\\frz3)"
                f"\\t({impact_end_ms},{settle_end_ms},\\c&H00F8FAFC&"
                f"\\3c&H003A263D&\\bord2.8\\shad1\\fscx100\\fscy100\\frz0)"
            )
        else:
            motion = (
                f"\\t({word_start_ms},{min(duration_ms, word_start_ms + 1)},"
                f"\\fscx98\\fscy98\\frz-1\\blur1.2)"
                f"\\t({min(duration_ms, word_start_ms + 1)},{impact_end_ms},"
                f"\\c&H00F8FAFC&\\3c{color}\\bord4.2\\shad2\\blur0.2"
                f"\\fscx108\\fscy108\\frz-1)"
                f"\\t({impact_end_ms},{settle_end_ms},"
                f"\\c&H00F8FAFC&\\3c&H003A263D&\\bord2.8\\shad1\\blur0"
                f"\\fscx100\\fscy100\\frz0)"
            )
        rendered.append(
            "{"
            f"\\c&H0099A0B0&\\3c&H003A263D&\\bord2.4\\shad1\\fscx94\\fscy94"
            f"{motion}"
            "}"
            f"{_ass_escape(line[start_offset:end_offset])}"
            "{\\rCaption}"
        )
        cursor = end_offset
    rendered.append(_ass_escape(line[cursor:]))
    return "".join(rendered)


def _ass_cue_motion_text(
    lines: Sequence[str],
    *,
    kinetic_style: str,
) -> str:
    """Give multi-line and clock-less cues the same motion language."""

    if kinetic_style == "slam":
        tag = (
            r"{\fad(120,0)\fscx108\fscy92\frz4\blur1.2"
            r"\t(0,200,\fscx108\fscy108\frz0\blur0)"
            r"\t(130,230,\fscx100\fscy100)}"
        )
    elif kinetic_style == "stamp":
        tag = (
            r"{\fad(80,0)\fscx108\fscy100\frz-4\blur1.2"
            r"\t(0,200,\fscx108\fscy108\frz2\blur0)"
            r"\t(200,240,\fscx100\fscy100\frz0)}"
        )
    elif kinetic_style == "marker":
        tag = (
            r"{\fad(100,0)\fscx96\fscy96\bord5.4\shad2\blur1"
            r"\t(0,200,\fscx108\fscy108\blur0)"
            r"\t(200,240,\fscx100\fscy100\bord2.8\shad1)}"
        )
    elif kinetic_style == "underline":
        tag = (
            r"{\fad(100,0)\fscx98\fscy98\u1\blur1"
            r"\t(0,200,\fscx108\fscy108\blur0)"
            r"\t(200,240,\fscx100\fscy100\u0)}"
        )
    elif kinetic_style == "shake":
        tag = (
            r"{\fad(80,0)\fscx108\fscy108\frz2\blur1"
            r"\t(0,55,\frz-2\fscx108\fscy108\blur0)"
            r"\t(55,110,\frz3)\t(110,210,\frz0\fscx100\fscy100)}"
        )
    else:
        tag = (
            r"{\fad(110,0)\fscx98\fscy98\frz-1\blur1.2"
            r"\t(0,200,\fscx108\fscy108\frz0\blur0)"
            r"\t(200,240,\fscx100\fscy100)}"
        )
    return tag + _wrap_ass_lines(lines)


def _ass_caption_text(cue: Mapping[str, Any], *, emphasis_colour: str) -> str:
    lines = [str(line) for line in cue.get("lines") or []]
    entry = cue.get("entry_motion")
    entry_ms = 120
    if isinstance(entry, Mapping):
        entry_ms = max(100, min(160, int(entry.get("duration_ms") or 120)))
        entry_scale = max(90, min(98, round(float(entry.get("scale_from") or 0.94) * 100)))
    else:
        entry_scale = 94
    # Keep the shared baseline readable while restoring a visible, restrained
    # cue entrance.  This is applied to the complete cue (not per character)
    # so it cannot change the speech clock or create word-by-word jitter.
    entry_type = str(entry.get("type") or "") if isinstance(entry, Mapping) else ""
    entry_tag = (
        f"{{\\fad({entry_ms},0)}}"
        if entry_type == "fade_in"
        else (
            f"{{\\fad({entry_ms},0)\\fscx{entry_scale}\\fscy{entry_scale}\\blur1.2"
            f"\\t(0,{entry_ms},\\fscx100\\fscy100\\blur0)}}"
        )
    )
    kinetic_mode = str(cue.get("kinetic_mode") or "")
    kinetic_style = str(cue.get("kinetic_style") or "")
    if kinetic_mode == "word_pop" and cue.get("kinetic_words"):
        kinetic_lines = [
            _ass_kinetic_caption_text(
                line,
                cue,
                line_index=index,
                cue_start=float(cue.get("start") or 0),
                cue_end=float(cue.get("end") or 0),
            ) or _ass_escape(line)
            for index, line in enumerate(lines)
        ]
        if any(
            any(
                isinstance(item, Mapping) and item.get("line_index") == index
                for item in cue.get("kinetic_words") or []
            )
            for index in range(len(lines))
        ):
            return entry_tag + r"\N".join(kinetic_lines)
    if kinetic_mode == "cue_pop" and kinetic_style:
        return _ass_cue_motion_text(lines, kinetic_style=kinetic_style)
    emphasis = cue.get("emphasis_range")
    if not isinstance(emphasis, Mapping):
        return entry_tag + _wrap_ass_lines(lines)
    line_index = emphasis.get("line_index")
    start = emphasis.get("start")
    end = emphasis.get("end")
    if not all(isinstance(value, int) for value in (line_index, start, end)):
        return entry_tag + _wrap_ass_lines(lines)
    raw_style = cue.get("emphasis_style")
    style = raw_style if isinstance(raw_style, Mapping) else {}
    colour = _hex_to_ass_colour(str(style.get("color") or "")) or emphasis_colour
    scale = 108
    duration_ms = max(180, min(240, int(style.get("duration_ms") or 200)))
    rendered: list[str] = []
    for index, line in enumerate(lines):
        if index != line_index or start < 0 or end <= start or end > len(line):
            rendered.append(_ass_escape(line))
            continue
        rendered.append(
            f"{entry_tag}{_ass_escape(line[:start])}"
            f"{{\\c{colour}\\fscx100\\fscy100\\blur0.8"
            f"\\t(0,{duration_ms},\\fscx{scale}\\fscy{scale}\\blur0)}}"
            f"{_ass_escape(line[start:end])}"
            f"{{\\c&H00F8FAFC&\\fscx100\\fscy100}}"
            f"{_ass_escape(line[end:])}"
        )
    return r"\N".join(rendered)


def build_business_talking_head_ass(
    segments: Sequence[Mapping[str, Any]],
    *,
    title: str,
    output_profile: str | "OutputProfile",
    caption_groups: object = None,
    caption_emphasis: object = None,
    spoken_ranges: object = None,
    caption_glossary: object = None,
    time_offset_seconds: float = 0,
    theme: str = "general",
    font_family: str | None = None,
    overlay_preview: Mapping[str, Any] | None = None,
    subtitle_style_id: str = "adaptive_talking_head_v1",
) -> bytes:
    """Create one approved ASS overlay for the title and manually reviewed captions."""

    spec = visual_style_spec(output_profile)
    canvas = spec["canvas"]
    title_style = spec["title"]
    accent_style = spec["accent"]
    caption_style = spec["subtitle"]
    # One adaptive baseline; semantic color and motion are cue-local.
    typography = {"font": "Source Han Serif CN Heavy", "spacing": 0.12, "bold": -1}
    selected_font = font_family or typography["font"]
    overlay_preview = overlay_preview or build_business_talking_head_overlay_preview(
        segments,
        title=title,
        output_profile=output_profile,
        caption_groups=caption_groups,
        caption_emphasis=caption_emphasis,
        spoken_ranges=spoken_ranges,
        caption_glossary=caption_glossary,
        subtitle_style_id=subtitle_style_id,
    )
    # A cached review snapshot can predate the selective-motion contract.  Do
    # not replay its old "every cue pops" fields: only a new preview carrying
    # the v3 fingerprint is allowed to preserve explicit word/cue motion.
    fingerprint = overlay_preview.get("style_fingerprint")
    selective_preview = isinstance(fingerprint, Mapping) and (
        fingerprint.get("word_motion") == "selective_word_emphasis_v3"
    )
    normalized_cues: list[dict[str, Any]] = []
    for cue_index, raw_cue in enumerate(overlay_preview.get("cues") or []):
        if not isinstance(raw_cue, Mapping):
            continue
        cue = dict(raw_cue)
        try:
            segment_index = int(
                cue.get("source_segment_index", cue.get("_segment_index", cue_index))
            )
        except (TypeError, ValueError):
            segment_index = cue_index
        segment = (
            segments[segment_index]
            if 0 <= segment_index < len(segments)
            and isinstance(segments[segment_index], Mapping)
            else None
        )
        # Normalize legacy cached cues to the current baseline.  In
        # particular, ``fade_in_scale`` used to contain a transform
        # transition; it must not resurrect continuous subtitle movement.
        cue["entry_motion"] = {
            "type": "fade_in",
            "duration_ms": 120,
            "scale_from": 1.0,
        }
        if selective_preview:
            # A persisted v3 preview can still contain kinetic_words written
            # by an earlier renderer.  Recompute the spans from the current
            # reviewed segment instead of replaying stale offsets, colours,
            # or the old per-cue motion mode.  This keeps an already-open task
            # on the same contract as a newly generated task.
            spans = (
                _caption_kinetic_words_for_cue(
                    cue,
                    segment,
                    caption_glossary=caption_glossary,
                )
                if segment is not None
                else []
            )
            cue["kinetic_words"] = spans
            if spans and segment is not None:
                cue["kinetic_mode"] = "word_pop"
                cue["kinetic_style"] = _caption_kinetic_style_for_cue(
                    cue,
                    segment,
                    cue_index,
                )
                cue["motion_scope"] = "keyword"
            elif cue_index == 0:
                cue["kinetic_mode"] = "cue_pop"
                cue["kinetic_style"] = "slam"
                cue["motion_scope"] = "hook"
            else:
                cue["kinetic_mode"] = "static"
                cue["kinetic_style"] = None
                cue["motion_scope"] = "static"
        elif cue_index == 0:
            cue["kinetic_words"] = []
            cue["kinetic_mode"] = "cue_pop"
            cue["kinetic_style"] = "slam"
            cue["motion_scope"] = "hook"
        else:
            cue["kinetic_words"] = []
            cue["kinetic_mode"] = "static"
            cue["kinetic_style"] = None
            cue["motion_scope"] = "static"
        normalized_cues.append(cue)
    overlay_preview = {**dict(overlay_preview), "cues": normalized_cues}
    header = f"""[Script Info]
Title: VideoInsight business talking-head overlay
ScriptType: v4.00+
PlayResX: {canvas["width"]}
PlayResY: {canvas["height"]}

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Title,{selected_font},{title_style["font_size"]},&H00FCFAF8,&H00FCFAF8,&H30000000,&H00000000,-1,0,0,0,100,100,0,0,1,{title_style["outline_width"]},{title_style["shadow"]},7,{title_style["safe_left"]},{title_style["safe_left"]},{title_style["safe_top"]},1
Style: Accent,Arial,1,&H00ED3A7C,&H00ED3A7C,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Caption,{selected_font},{caption_style["font_size"]},&H00FCFAF8,&H00FCFAF8,&H30000000,&H00000000,{typography["bold"]},0,0,0,100,100,{typography["spacing"]},0,1,{caption_style["outline_width"]},{caption_style["shadow"]},2,{round(canvas["width"] * 0.08)},{round(canvas["width"] * 0.08)},{caption_style["safe_bottom"]},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines: list[str] = []
    title_preview = overlay_preview["title"]
    if title_preview["lines"]:
        visible_seconds = float(title_style["visible_seconds"])
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(time_offset_seconds)},"
            f"{_ass_timestamp(time_offset_seconds + visible_seconds)},Title,,0,0,0,,"
            f"{_wrap_ass_lines(title_preview['lines'])}"
        )
        title_height = len(title_preview["lines"]) * round(
            title_style["font_size"] * 1.22
        )
        accent_y = title_style["safe_top"] + title_height + accent_style["gap"]
        accent_path = (
            f"m 0 0 l {accent_style['width']} 0 l {accent_style['width']} "
            f"{accent_style['height']} l 0 {accent_style['height']}"
        )
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(time_offset_seconds)},"
            f"{_ass_timestamp(time_offset_seconds + visible_seconds)},Accent,,0,0,0,,"
            f"{{\\pos({title_style['safe_left']},{accent_y})\\p1}}{accent_path}"
        )
    for cue in overlay_preview["cues"]:
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(float(cue['start']) + time_offset_seconds)},"
            f"{_ass_timestamp(float(cue['end']) + time_offset_seconds)},Caption,,0,0,0,,"
            f"{_ass_caption_text(cue, emphasis_colour='&H006AE1FF&')}"
        )
    return (header + "\n".join(lines) + "\n").encode("utf-8-sig")


def build_business_talking_head_srt(
    segments: Sequence[Mapping[str, Any]],
    *,
    output_profile: str | "OutputProfile",
    caption_groups: object = None,
    caption_emphasis: object = None,
    spoken_ranges: object = None,
    caption_glossary: object = None,
    overlay_preview: Mapping[str, Any] | None = None,
) -> bytes:
    """Export the same short-cue contract as ASS for local audit artifacts."""

    preview = overlay_preview or build_business_talking_head_overlay_preview(
        segments,
        title="",
        output_profile=output_profile,
        caption_groups=caption_groups,
        caption_emphasis=caption_emphasis,
        spoken_ranges=spoken_ranges,
        caption_glossary=caption_glossary,
    )
    lines: list[str] = []
    for index, cue in enumerate(preview["cues"], 1):
        text = "\n".join(str(line) for line in cue.get("lines") or [])
        if not text:
            continue
        lines.extend(
            [
                str(index),
                f"{_srt_timestamp(float(cue['start']))} --> {_srt_timestamp(float(cue['end']))}",
                text,
                "",
            ]
        )
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


class CloudEditorError(ValueError):
    """A user-displayable cloud editor validation error."""


class CloudProviderMode(StrEnum):
    SANDBOX = "sandbox"
    ALIYUN = "aliyun"


class OutputProfile(StrEnum):
    HD_720P = "720p"
    FULL_HD_1080P = "1080p"


class ProviderJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class EditStepKind(StrEnum):
    SMART_OPENING = "smart_opening"
    TRIM_SILENCE = "trim_silence"
    VERTICAL_FIT = "vertical_fit"
    SUBTITLES = "subtitles"
    TITLE = "title"
    BGM = "bgm"
    AUDIO_MIX = "audio_mix"


class TimeRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def _validate_order(self) -> TimeRange:
        if self.end <= self.start:
            raise ValueError("时间区间的 end 必须大于 start。")
        return self


class EditPlan(BaseModel):
    """Server-validated plan; remove ranges can never overlap spoken content."""

    model_config = ConfigDict(frozen=True)

    plan_version: str = "safe-rough-cut-v2"
    duration_seconds: float = Field(gt=0)
    spoken_ranges: list[TimeRange] = Field(default_factory=list)
    remove_ranges: list[TimeRange] = Field(
        default_factory=list,
        max_length=MAX_REMOVE_RANGES,
    )
    kept_ranges: list[TimeRange] = Field(default_factory=list)
    estimated_output_seconds: float = Field(default=0, ge=0)
    enabled_steps: list[EditStepKind] = Field(default_factory=list)
    trim_silence_enabled: bool = False
    title_candidates: list[str] = Field(default_factory=list, max_length=5)
    bgm_category: str = "通用口播"
    bgm_energy: str = "克制"
    bgm_keywords: list[str] = Field(default_factory=list, max_length=6)
    caption_groups: list[CaptionGroup] = Field(default_factory=list, max_length=400)
    caption_group_source: str = "deterministic_fallback"
    caption_emphasis: list[CaptionEmphasis] = Field(
        default_factory=list, max_length=140
    )
    visual_beats: list[VisualBeat] = Field(default_factory=list, max_length=24)
    smart_opening: SmartOpening | None = None
    explanation: str = ""
    warnings: list[str] = Field(default_factory=list)
    provider_name: str = "deterministic_rules"
    is_mock: bool = False
    usage: dict[str, int | float | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_safe_ranges(self) -> EditPlan:
        if self.bgm_category not in BGM_VOICEOVER_CATEGORIES:
            raise ValueError("BGM 分类不在允许范围内。")
        if self.bgm_energy not in BGM_ENERGY_LEVELS:
            raise ValueError("BGM 能量等级不在允许范围内。")
        spoken = sorted(self.spoken_ranges, key=lambda item: (item.start, item.end))
        removed = sorted(self.remove_ranges, key=lambda item: (item.start, item.end))

        for label, ranges in (("语音", spoken), ("删除", removed)):
            previous_end = 0.0
            for index, item in enumerate(ranges):
                if item.end > self.duration_seconds:
                    raise ValueError(f"{label}区间超出视频时长。")
                if index and item.start < previous_end:
                    raise ValueError(f"{label}区间不能互相重叠。")
                previous_end = item.end

        for cut in removed:
            if any(
                cut.start < speech.end and speech.start < cut.end for speech in spoken
            ):
                raise ValueError("自动剪辑不能删除任何有人声的区间。")

        for beat in self.visual_beats:
            if beat.end > self.duration_seconds or beat.end <= beat.start:
                raise ValueError("自动视觉分镜区间超出视频时长。")

        if bool(removed) != self.trim_silence_enabled:
            raise ValueError("trim_silence_enabled 必须与删除区间保持一致。")
        if (
            self.trim_silence_enabled
            and EditStepKind.TRIM_SILENCE not in self.enabled_steps
        ):
            raise ValueError("存在删除区间时必须启用 trim_silence 步骤。")
        expected_kept = kept_ranges_for_plan(self.duration_seconds, removed)
        if self.kept_ranges and self.kept_ranges != expected_kept:
            raise ValueError("保留区间必须由安全裁剪区间推导。")
        if len(expected_kept) > MAX_KEEP_RANGES:
            raise ValueError("正式云端粗剪最多保留 50 个片段。")
        return self


class QuoteLineItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    component: str
    provider: str
    quantity: Decimal = Field(ge=0)
    unit: str
    unit_price_cny: Decimal = Field(ge=0)
    estimated_cost_cny: Decimal = Field(ge=0)
    rate_details: dict[str, Decimal] = Field(default_factory=dict)


class CostQuote(BaseModel):
    model_config = ConfigDict(frozen=True)

    quote_id: str
    issued_at: datetime
    expires_at: datetime
    ttl_seconds: int = QUOTE_TTL_SECONDS
    price_version: str = PRICE_VERSION
    currency: str = "CNY"
    output_profile: OutputProfile
    line_items: list[QuoteLineItem]
    estimated_total: Decimal = Field(ge=0)
    estimated_max: Decimal = Field(ge=0)
    exclusions: list[str] = Field(
        default_factory=lambda: ["OSS 存储", "公网下行流量", "失败重试"],
    )


class CloudCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_mode: CloudProviderMode
    provider_name: str
    enabled: bool
    live_ready: bool
    missing_configuration: list[str] = Field(default_factory=list)
    is_mock: bool
    price_version: str = PRICE_VERSION
    quote_ttl_seconds: int = QUOTE_TTL_SECONDS
    supported_output_profiles: list[OutputProfile] = Field(
        default_factory=lambda: [
            OutputProfile.HD_720P,
            OutputProfile.FULL_HD_1080P,
        ],
    )


class CloudAsset(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_name: str
    bucket: str | None = None
    object_key: str
    uri: str
    media_type: str
    size_bytes: int = Field(ge=0)
    is_mock: bool = False
    provider_locator: str | None = Field(default=None, exclude=True, repr=False)


class ProviderJobSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_name: str
    provider_job_id: str
    provider_stage: str
    status: ProviderJobStatus
    is_mock: bool = False
    output_uri: str | None = None
    can_publish: bool = False
    usage: dict[str, int | float | str] = Field(default_factory=dict)
    detail: dict[str, Any] = Field(default_factory=dict)
    result_locator: str | None = Field(default=None, exclude=True, repr=False)


class TranscriptWord(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_order(self) -> TranscriptWord:
        if self.end <= self.start:
            raise ValueError("词级转写的 end 必须大于 start。")
        return self


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str
    words: list[TranscriptWord] = Field(default_factory=list)
    speaker_id: int | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _validate_order(self) -> TranscriptSegment:
        if self.end <= self.start:
            raise ValueError("转写分段的 end 必须大于 start。")
        return self


class CloudTranscript(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_name: str
    transcript: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    spoken_ranges: list[TimeRange] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0)
    language: str = ""
    is_mock: bool = False
    usage: dict[str, int | float | str] = Field(default_factory=dict)


class RenderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_asset: CloudAsset
    output_object_key: str = Field(min_length=1)
    output_profile: OutputProfile
    edit_plan: EditPlan
    review_confirmed: bool = False
    subtitle_object_key: str | None = None
    title_watermark_object_key: str | None = None
    merge_config_asset: CloudAsset | None = None
    title: str = ""
    bgm_asset: CloudAsset | None = None
    bgm_volume: float = Field(default=0.2, ge=0, le=1)
    opening_asset: CloudAsset | None = None
    opening_duration_seconds: float = Field(default=0, ge=0, le=2)
    idempotency_key: str = Field(min_length=1)


class CloudObjectStore(Protocol):
    def upload(
        self,
        path: str | Path,
        object_key: str,
        *,
        media_type: str,
    ) -> CloudAsset: ...


class CloudASRProvider(Protocol):
    def submit(
        self,
        asset: CloudAsset,
        *,
        language_hints: Sequence[str] = ("zh",),
    ) -> ProviderJobSnapshot: ...

    def query(self, provider_job_id: str) -> ProviderJobSnapshot: ...

    def fetch_result(self, snapshot: ProviderJobSnapshot) -> CloudTranscript: ...


class EditPlanProvider(Protocol):
    def create_plan(
        self,
        transcript: str,
        spoken_ranges: Sequence[TimeRange | Mapping[str, float]],
        duration_seconds: float,
        segments: Sequence[Mapping[str, Any]] | None = None,
    ) -> EditPlan: ...


class CloudRenderProvider(Protocol):
    def submit(self, request: RenderRequest) -> ProviderJobSnapshot: ...

    def query(self, provider_job_id: str) -> ProviderJobSnapshot: ...


class CloudEditorConfiguration(BaseModel):
    """Environment-backed provider configuration without secret disclosure."""

    model_config = ConfigDict(frozen=True)

    provider_mode: CloudProviderMode = CloudProviderMode.SANDBOX
    workspace_id: str = ""
    dashscope_api_key: str = Field(default="", repr=False)
    oss_bucket: str = ""
    oss_location: str = "oss-cn-beijing"
    aliyun_region: str = "cn-beijing"
    access_key_id: str = Field(default="", repr=False)
    access_key_secret: str = Field(default="", repr=False)
    mps_pipeline_id: str = ""
    mps_template_id_720p: str = ""
    mps_template_id_1080p: str = ""
    price_version: str = PRICE_VERSION
    quote_ttl_seconds: int = Field(default=QUOTE_TTL_SECONDS, gt=0)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> CloudEditorConfiguration:
        source = os.environ if env is None else env
        raw_mode = (
            str(source.get("VIDEO_EDITOR_PROVIDER_MODE", "sandbox")).strip().casefold()
        )
        try:
            provider_mode = CloudProviderMode(raw_mode or "sandbox")
        except ValueError as exc:
            raise CloudEditorError(
                "VIDEO_EDITOR_PROVIDER_MODE 仅支持 sandbox 或 aliyun。",
            ) from exc
        raw_ttl = str(
            source.get("VIDEO_EDITOR_QUOTE_TTL_SECONDS", QUOTE_TTL_SECONDS),
        ).strip()
        try:
            quote_ttl_seconds = int(raw_ttl)
        except ValueError:
            quote_ttl_seconds = QUOTE_TTL_SECONDS
        quote_ttl_seconds = max(60, quote_ttl_seconds)
        aliyun_region = (
            str(
                source.get(
                    "ALIYUN_VIDEO_EDITOR_REGION",
                    source.get("ALIYUN_REGION", "cn-beijing"),
                ),
            ).strip()
            or "cn-beijing"
        )
        default_oss_location = (
            aliyun_region
            if aliyun_region.startswith("oss-")
            else f"oss-{aliyun_region}"
        )
        return cls(
            provider_mode=provider_mode,
            workspace_id=str(
                source.get("ALIYUN_MODEL_STUDIO_WORKSPACE_ID", ""),
            ).strip(),
            dashscope_api_key=str(source.get("DASHSCOPE_API_KEY", "")).strip(),
            oss_bucket=str(source.get("ALIYUN_OSS_BUCKET", "")).strip(),
            oss_location=str(
                source.get("ALIYUN_OSS_LOCATION", default_oss_location),
            ).strip()
            or default_oss_location,
            aliyun_region=aliyun_region.removeprefix("oss-"),
            access_key_id=str(
                source.get(
                    "ALIBABA_CLOUD_ACCESS_KEY_ID",
                    source.get("ALIYUN_ACCESS_KEY_ID", ""),
                ),
            ).strip(),
            access_key_secret=str(
                source.get(
                    "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
                    source.get("ALIYUN_ACCESS_KEY_SECRET", ""),
                ),
            ).strip(),
            mps_pipeline_id=str(source.get("ALIYUN_MPS_PIPELINE_ID", "")).strip(),
            mps_template_id_720p=str(
                source.get("ALIYUN_MPS_TEMPLATE_ID_720P", ""),
            ).strip(),
            mps_template_id_1080p=str(
                source.get("ALIYUN_MPS_TEMPLATE_ID_1080P", ""),
            ).strip(),
            price_version=str(
                source.get("VIDEO_EDITOR_PRICE_VERSION", PRICE_VERSION),
            ).strip()
            or PRICE_VERSION,
            quote_ttl_seconds=quote_ttl_seconds,
        )

    @property
    def missing_configuration(self) -> list[str]:
        if self.provider_mode == CloudProviderMode.SANDBOX:
            return []
        required = {
            "ALIYUN_MODEL_STUDIO_WORKSPACE_ID": self.workspace_id,
            "DASHSCOPE_API_KEY": self.dashscope_api_key,
            "ALIYUN_OSS_BUCKET": self.oss_bucket,
            "ALIBABA_CLOUD_ACCESS_KEY_ID": self.access_key_id,
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": self.access_key_secret,
            "ALIYUN_MPS_PIPELINE_ID": self.mps_pipeline_id,
            "ALIYUN_MPS_TEMPLATE_ID_720P": self.mps_template_id_720p,
            "ALIYUN_MPS_TEMPLATE_ID_1080P": self.mps_template_id_1080p,
        }
        return [name for name, value in required.items() if not value]

    def mps_template_id(self, profile: OutputProfile) -> str:
        return (
            self.mps_template_id_720p
            if profile == OutputProfile.HD_720P
            else self.mps_template_id_1080p
        )


def get_cloud_capability(config: CloudEditorConfiguration) -> CloudCapability:
    missing = config.missing_configuration
    sandbox = config.provider_mode == CloudProviderMode.SANDBOX
    return CloudCapability(
        provider_mode=config.provider_mode,
        provider_name="sandbox_cloud_editor" if sandbox else "aliyun_cloud_editor",
        enabled=sandbox or not missing,
        live_ready=not sandbox and not missing,
        missing_configuration=missing,
        is_mock=sandbox,
        price_version=config.price_version,
        quote_ttl_seconds=config.quote_ttl_seconds,
    )


def _decimal(value: int | float | str | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(_COST_PRECISION, rounding=ROUND_HALF_UP)


def create_cost_quote(
    *,
    input_duration_seconds: int | float | Decimal,
    output_duration_seconds: int | float | Decimal | None = None,
    output_profile: OutputProfile | str = OutputProfile.HD_720P,
    planning_input_tokens: int = 3000,
    planning_output_tokens: int = 1000,
    now: datetime | None = None,
    price_version: str = PRICE_VERSION,
    ttl_seconds: int = QUOTE_TTL_SECONDS,
) -> CostQuote:
    input_seconds = _decimal(input_duration_seconds)
    output_seconds = (
        input_seconds
        if output_duration_seconds is None
        else _decimal(output_duration_seconds)
    )
    if input_seconds <= 0 or output_seconds <= 0:
        raise CloudEditorError("输入与预计输出时长必须大于 0 秒。")
    if planning_input_tokens < 0 or planning_output_tokens < 0:
        raise CloudEditorError("规划 Token 估算不能为负数。")
    if ttl_seconds <= 0:
        raise CloudEditorError("费用报价有效期必须大于 0 秒。")
    try:
        profile = OutputProfile(output_profile)
    except ValueError as exc:
        raise CloudEditorError("输出档位仅支持 720p 或 1080p。") from exc

    input_token_quantity = Decimal(planning_input_tokens)
    output_token_quantity = Decimal(planning_output_tokens)
    asr_cost = _money(input_seconds * FUN_ASR_CNY_PER_SECOND)
    planning_input_cost = (
        input_token_quantity
        / Decimal(1_000_000)
        * QWEN_FLASH_INPUT_CNY_PER_MILLION_TOKENS
    )
    planning_output_cost = (
        output_token_quantity
        / Decimal(1_000_000)
        * QWEN_FLASH_OUTPUT_CNY_PER_MILLION_TOKENS
    )
    planning_cost = _money(planning_input_cost + planning_output_cost)
    planning_token_total = input_token_quantity + output_token_quantity
    blended_planning_rate = (
        planning_cost / planning_token_total if planning_token_total else Decimal("0")
    )
    output_minutes = output_seconds / Decimal(60)
    render_rate = MPS_CNY_PER_OUTPUT_MINUTE[profile.value]
    render_cost = _money(output_minutes * render_rate)
    title_overlay_cost = _money(MPS_WATERMARK_CNY_PER_REQUEST)

    line_items = [
        QuoteLineItem(
            component="speech_recognition",
            provider="Fun-ASR",
            quantity=input_seconds,
            unit="input_second",
            unit_price_cny=FUN_ASR_CNY_PER_SECOND,
            estimated_cost_cny=asr_cost,
        ),
        QuoteLineItem(
            component="edit_planning",
            provider="qwen-flash",
            quantity=planning_token_total,
            unit="estimated_token",
            unit_price_cny=blended_planning_rate,
            estimated_cost_cny=planning_cost,
            rate_details={
                "input_per_million_tokens": (QWEN_FLASH_INPUT_CNY_PER_MILLION_TOKENS),
                "output_per_million_tokens": (QWEN_FLASH_OUTPUT_CNY_PER_MILLION_TOKENS),
            },
        ),
        QuoteLineItem(
            component="cloud_render",
            provider="MPS H.264",
            quantity=output_minutes,
            unit="output_minute",
            unit_price_cny=render_rate,
            estimated_cost_cny=render_cost,
        ),
        QuoteLineItem(
            component="brand_title_overlay",
            provider="MPS image watermark",
            quantity=Decimal("1"),
            unit="request",
            unit_price_cny=MPS_WATERMARK_CNY_PER_REQUEST,
            estimated_cost_cny=title_overlay_cost,
        ),
    ]
    total = _money(sum((item.estimated_cost_cny for item in line_items), Decimal("0")))
    issued_at = now or datetime.now(timezone.utc)
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    return CostQuote(
        quote_id=f"veq_{uuid4().hex}",
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=ttl_seconds),
        ttl_seconds=ttl_seconds,
        price_version=price_version,
        output_profile=profile,
        line_items=line_items,
        estimated_total=total,
        estimated_max=total,
    )


def validate_cost_quote(
    quote: CostQuote,
    quote_id: str,
    *,
    now: datetime | None = None,
    expected_price_version: str = PRICE_VERSION,
) -> CostQuote:
    if quote.quote_id != quote_id:
        raise CloudEditorError("费用报价与本次确认不匹配，请重新预检。")
    required_components = {
        "speech_recognition",
        "edit_planning",
        "cloud_render",
        "brand_title_overlay",
    }
    quoted_components = {item.component for item in quote.line_items}
    if not required_components.issubset(quoted_components):
        raise CloudEditorError("费用项目已变化，请重新确认费用。")
    if quote.price_version != expected_price_version:
        raise CloudEditorError("计费价格版本已变化，请重新确认费用。")
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    if checked_at >= quote.expires_at:
        raise CloudEditorError("费用报价已过期，请重新预检并确认。")
    return quote


def _as_time_range(value: TimeRange | Mapping[str, float]) -> TimeRange:
    return value if isinstance(value, TimeRange) else TimeRange.model_validate(value)


def _merge_ranges(ranges: Sequence[TimeRange]) -> list[TimeRange]:
    merged: list[TimeRange] = []
    for item in sorted(ranges, key=lambda value: (value.start, value.end)):
        if merged and item.start <= merged[-1].end:
            merged[-1] = TimeRange(
                start=merged[-1].start,
                end=max(merged[-1].end, item.end),
            )
        else:
            merged.append(item)
    return merged


def kept_ranges_for_plan(
    duration_seconds: float,
    remove_ranges: Sequence[TimeRange | Mapping[str, float]],
) -> list[TimeRange]:
    """Return the source ranges that remain after safe rough-cut intervals."""

    cursor = 0.0
    kept: list[TimeRange] = []
    for removed in _merge_ranges([_as_time_range(item) for item in remove_ranges]):
        if removed.start > cursor:
            kept.append(TimeRange(start=cursor, end=removed.start))
        cursor = max(cursor, removed.end)
    if duration_seconds > cursor:
        kept.append(TimeRange(start=cursor, end=duration_seconds))
    return kept


def retime_segments_after_cuts(
    segments: Sequence[Mapping[str, Any]],
    remove_ranges: Sequence[TimeRange | Mapping[str, float]],
) -> list[dict[str, Any]]:
    """Map approved source subtitle timestamps onto the edited output timeline."""

    removed = _merge_ranges([_as_time_range(item) for item in remove_ranges])
    retimed: list[dict[str, Any]] = []
    for segment in segments:
        try:
            start = float(segment.get("start", 0))
            end = float(segment.get("end", 0))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        if any(start < cut.end and cut.start < end for cut in removed):
            raise CloudEditorError("字幕片段跨越粗剪区间，请关闭该切点后再生成。")
        shift = sum(cut.end - cut.start for cut in removed if cut.end <= start)
        retimed.append(
            {
                **dict(segment),
                "start": round(start - shift, 3),
                "end": round(end - shift, 3),
            }
        )
    return retimed


def build_safe_edit_plan(
    spoken_ranges: Sequence[TimeRange | Mapping[str, float]],
    duration_seconds: float,
    *,
    title_candidates: Sequence[str] | None = None,
    bgm_category: str = "通用口播",
    bgm_energy: str = "克制",
    bgm_keywords: Sequence[str] | None = None,
    caption_groups: Sequence[CaptionGroup | Mapping[str, Any]] | None = None,
    caption_group_source: str = "deterministic_fallback",
    caption_emphasis: Sequence[CaptionEmphasis | Mapping[str, Any]] | None = None,
    visual_beats: Sequence[VisualBeat | Mapping[str, Any]] | None = None,
    smart_opening: SmartOpening | Mapping[str, Any] | None = None,
    explanation: str = "",
    enabled_steps: Sequence[EditStepKind | str] | None = None,
    provider_name: str = "deterministic_rules",
    is_mock: bool = False,
    usage: Mapping[str, int | float | str] | None = None,
) -> EditPlan:
    if duration_seconds <= 0:
        raise CloudEditorError("视频时长必须大于 0 秒。")
    spoken = _merge_ranges([_as_time_range(item) for item in spoken_ranges])
    if any(item.end > duration_seconds for item in spoken):
        raise CloudEditorError("语音区间不能超出视频时长。")

    cuts: list[TimeRange] = []
    if spoken and spoken[0].start >= HEAD_TAIL_SILENCE_SECONDS:
        cut_end = spoken[0].start - HEAD_TAIL_PADDING_SECONDS
        if cut_end > 0:
            cuts.append(TimeRange(start=0, end=cut_end))
    for left, right in zip(spoken, spoken[1:], strict=False):
        gap_seconds = right.start - left.end
        if gap_seconds < MIN_SILENCE_SECONDS:
            continue
        cut_start = left.end + SILENCE_EDGE_PADDING_SECONDS
        cut_end = right.start - SILENCE_EDGE_PADDING_SECONDS
        if cut_end > cut_start:
            cuts.append(TimeRange(start=cut_start, end=cut_end))
    if spoken and duration_seconds - spoken[-1].end >= HEAD_TAIL_SILENCE_SECONDS:
        cut_start = spoken[-1].end + HEAD_TAIL_PADDING_SECONDS
        if duration_seconds > cut_start:
            cuts.append(TimeRange(start=cut_start, end=duration_seconds))
    cuts = _merge_ranges(cuts)

    warnings: list[str] = []
    if not spoken:
        warnings.append("未检测到可靠语音区间，未自动裁剪。")
    if len(kept_ranges_for_plan(duration_seconds, cuts)) > MAX_KEEP_RANGES:
        cuts = []
        warnings.append("安全裁剪区间过多，已关闭自动粗剪以保证正式出片稳定。")

    requested_steps = (
        [
            EditStepKind.SMART_OPENING,
            EditStepKind.TRIM_SILENCE,
            EditStepKind.VERTICAL_FIT,
            EditStepKind.SUBTITLES,
            EditStepKind.TITLE,
            EditStepKind.BGM,
            EditStepKind.AUDIO_MIX,
        ]
        if enabled_steps is None
        else [EditStepKind(item) for item in enabled_steps]
    )
    unique_steps = list(dict.fromkeys(requested_steps))
    if not cuts:
        unique_steps = [
            step for step in unique_steps if step != EditStepKind.TRIM_SILENCE
        ]
    elif EditStepKind.TRIM_SILENCE not in unique_steps:
        unique_steps.insert(0, EditStepKind.TRIM_SILENCE)

    opening = SmartOpening.model_validate(smart_opening) if smart_opening else None
    if opening is None:
        unique_steps = [
            step for step in unique_steps if step != EditStepKind.SMART_OPENING
        ]
    elif EditStepKind.SMART_OPENING not in unique_steps:
        unique_steps.insert(0, EditStepKind.SMART_OPENING)

    titles = [
        str(item).strip() for item in (title_candidates or []) if str(item).strip()
    ]
    return EditPlan(
        duration_seconds=duration_seconds,
        spoken_ranges=spoken,
        remove_ranges=cuts,
        kept_ranges=kept_ranges_for_plan(duration_seconds, cuts),
        estimated_output_seconds=round(
            duration_seconds - sum(item.end - item.start for item in cuts),
            3,
        ),
        enabled_steps=unique_steps,
        trim_silence_enabled=bool(cuts),
        title_candidates=titles[:5],
        bgm_category=bgm_category,
        bgm_energy=bgm_energy,
        bgm_keywords=[
            str(item).strip()[:20] for item in (bgm_keywords or []) if str(item).strip()
        ][:6],
        caption_groups=list(caption_groups or []),
        caption_group_source=caption_group_source,
        caption_emphasis=list(caption_emphasis or []),
        visual_beats=[
            item
            if isinstance(item, VisualBeat)
            else VisualBeat.model_validate(item)
            for item in (visual_beats or [])
        ],
        smart_opening=opening,
        explanation=explanation.strip(),
        warnings=warnings,
        provider_name=provider_name,
        is_mock=is_mock,
        usage=dict(usage or {}),
    )
