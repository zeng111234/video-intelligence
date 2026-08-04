"""Cloud video-editor contracts, pricing, and deterministic safety rules.

This module intentionally has no FastAPI or vendor SDK dependency.  The API
layer can serialize the Pydantic models directly, while provider adapters stay
replaceable and testable without making paid calls.
"""

from __future__ import annotations

import io
import math
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
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
CAPTION_EMPHASIS_KINDS = ("number", "benefit", "warning", "keyword")
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
DEFAULT_VISUAL_STYLE_ID = "business_talking_head_v8"
DEFAULT_PLAYBACK_RATE = 1.15
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRAND_TITLE_FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "SourceHanSerifCN-Heavy.otf"
_CAPTION_BREAK_CHARACTERS = frozenset(
    "，。！？；：、,.!?;:“”‘’（）()【】[]《》…—"
)
_NUMERIC_PUNCTUATION = frozenset(".,:")
_CAPTION_BREAK_BEFORE_TOKENS = (
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
            "max_lines": 2,
            "max_chars_per_line": 9,
            "font_family": "Source Han Serif CN Heavy",
            "render_mode": "png_watermark",
            "font_size": round(52 * scale),
            "line_height": 1.1,
            "safe_top": round(84 * scale),
            "safe_left": round(56 * scale),
            "asset_width": round(520 * scale),
            "asset_height": round(150 * scale),
            "outline_width": max(1, round(1 * scale)),
            "shadow": max(2, round(3 * scale)),
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
            "outline_width": max(1, round(2 * scale)),
            "shadow": max(2, round(3 * scale)),
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
        clean = f"{clean[: max_chars - 1]}…"
    return [
        clean[index : index + chars_per_line]
        for index in range(0, len(clean), chars_per_line)
    ]


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


def _caption_word_splits(piece: str) -> set[int]:
    """Return Chinese word boundaries so captions never cut through a word."""

    boundaries: set[int] = set()
    cursor = 0
    for token in jieba.lcut(piece, cut_all=False, HMM=True):
        cursor += len(token)
        if cursor < len(piece):
            boundaries.add(cursor)
    return boundaries


def _caption_split_reads_naturally(piece: str, split_at: int) -> bool:
    left = piece[:split_at]
    right = piece[split_at:]
    return not left.endswith(_CAPTION_BAD_LINE_ENDINGS) and not right.startswith(
        _CAPTION_BAD_LINE_STARTS
    )


def _caption_phrase_parts(piece: str, *, max_chars: int) -> list[str]:
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
        word_splits = _caption_word_splits(remaining)
        semantic_splits = _caption_boundary_splits(remaining)
        available_splits = [
            split_at
            for split_at in range(minimum_split, maximum_split + 1)
            if split_at in word_splits or split_at in semantic_splits
        ]
        safe_splits = [
            split_at
            for split_at in available_splits
            if _caption_split_reads_naturally(remaining, split_at)
        ] or available_splits
        candidates = [
            split_at for split_at in safe_splits if split_at in semantic_splits
        ]
        if not candidates:
            candidates = safe_splits
        split_at = (
            min(candidates, key=lambda value: (abs(value - ideal), -value))
            if candidates
            else max(minimum_split, min(maximum_split, ideal))
        )
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        parts.append(remaining)
    return parts


def _caption_chunks(text: str, *, max_chars: int) -> list[str]:
    pieces = _caption_phrases(text)
    chunks: list[str] = []
    for piece in pieces:
        chunks.extend(_caption_phrase_parts(piece, max_chars=max_chars))
    return chunks


def _normalized_emphasis_terms(segment: Mapping[str, Any]) -> list[str]:
    raw_terms = segment.get("emphasis_terms") or []
    if not isinstance(raw_terms, Sequence) or isinstance(raw_terms, (str, bytes)):
        return []
    terms = [_clean_caption_text(str(term)) for term in raw_terms]
    return [term for term in terms if term][:1]


def _emphasis_range(lines: Sequence[str], terms: Sequence[str]) -> dict[str, int] | None:
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
            else cursor
            + (segment_end - segment_start) * len(chunk) / total_chars
        )
        fallback.append((cursor, cue_end))
        cursor = cue_end
    if (
        not chunks
        or not isinstance(spoken_ranges, Sequence)
        or isinstance(spoken_ranges, (str, bytes))
    ):
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
    sound_effect_id: str = Field(
        pattern="^(soft_whoosh|soft_page_turn|soft_chime)$"
    )
    intensity: str = Field(default="medium", pattern="^(low|medium)$")
    reason: str = Field(default="", max_length=120)


def build_smart_opening(
    transcript: str,
    title_candidates: Sequence[str],
    *,
    preferred_style: object = None,
) -> SmartOpening | None:
    """Derive safe opening text and style without arbitrary effect parameters."""

    clean_transcript = re.sub(r"\s+", "", transcript or "")
    candidates = [
        re.sub(r"[\s，。！？、,.!?；;：:]+", "", str(item))
        for item in title_candidates
    ]
    hook_text = next((item for item in candidates if len(item) >= 2), "")
    if not hook_text:
        hook_text = clean_transcript[:14].strip("，。！？、,.!?；;：: ")
    if len(hook_text) > 14:
        hook_text = hook_text[:14].rstrip("，。！？、,.!?；;：:")
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
        parts = [_clean_caption_text(part) for part in group.parts]
        if (
            any(not part or len(part) > max_chars for part in parts)
            or "".join(parts) != expected[group.segment_index]
        ):
            return []
        source_text = expected[group.segment_index]
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
    group_parts = {
        group.segment_index: list(group.parts) for group in groups
    }
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
    return {
        "color": "#FFE16A",
        "scale": 1.5,
        "animation": "soft_pop",
        "duration_ms": 120,
    }


_AUTO_EMPHASIS_NUMBER = re.compile(
    r"\d+(?:\.\d+)?(?:%|元|块|万|倍|折|公里|分钟|秒|张|个|家|人|套)"
)
_AUTO_EMPHASIS_PROMOTION_REWARD = re.compile(
    r"送(\d+(?:\.\d+)?(?:元|块)?)"
)
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


def build_business_talking_head_overlay_preview(
    segments: Sequence[Mapping[str, Any]],
    *,
    title: str,
    output_profile: str | "OutputProfile",
    caption_groups: object = None,
    caption_emphasis: object = None,
    spoken_ranges: object = None,
) -> dict[str, Any]:
    """Normalize title/caption lines once for browser preview and ASS rendering."""

    spec = visual_style_spec(output_profile)
    title_style = spec["title"]
    caption_style = spec["subtitle"]
    title_lines = _display_lines(
        title,
        chars_per_line=title_style["max_chars_per_line"],
        max_lines=title_style["max_lines"],
        truncate=True,
    )
    cues: list[dict[str, Any]] = []
    max_caption_chars = (
        caption_style["max_chars_per_line"] * caption_style["max_lines"]
    )
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
    emphasis_by_segment = {
        item.segment_index: item for item in approved_emphasis
    }
    for segment_index, segment in enumerate(segments):
        try:
            start = float(segment.get("start", 0))
            end = float(segment.get("end", 0))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        chunks = semantic_parts.get(segment_index) or _caption_chunks(
            str(segment.get("text") or ""),
            max_chars=max_caption_chars,
        )
        cue_timings = _caption_cue_timings(
            chunks,
            segment_start=start,
            segment_end=end,
            spoken_ranges=spoken_ranges,
        )
        for index, chunk in enumerate(chunks):
            cue_start, cue_end = cue_timings[index]
            lines = _display_lines(
                chunk,
                chars_per_line=caption_style["max_chars_per_line"],
                max_lines=caption_style["max_lines"],
            )
            ai_emphasis = emphasis_by_segment.get(segment_index)
            manual_terms = _normalized_emphasis_terms(segment)
            clean_chunk = re.sub(r"\s+", "", chunk)
            active_manual_terms = [
                term for term in manual_terms if term in clean_chunk
            ]
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
                    "start": round(cue_start, 3),
                    "end": round(cue_end, 3),
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
                                    automatic[1]
                                    if automatic is not None
                                    else "keyword"
                                )
                            )
                        )
                        if emphasis is not None
                        else None
                    ),
                }
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
    }


def _hex_to_ass_colour(value: str) -> str:
    clean = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", clean):
        return "&H006AE1FF&"
    red, green, blue = clean[0:2], clean[2:4], clean[4:6]
    return f"&H00{blue}{green}{red}&"


def _ass_caption_text(cue: Mapping[str, Any], *, emphasis_colour: str) -> str:
    lines = [str(line) for line in cue.get("lines") or []]
    emphasis = cue.get("emphasis_range")
    if not isinstance(emphasis, Mapping):
        return _wrap_ass_lines(lines)
    line_index = emphasis.get("line_index")
    start = emphasis.get("start")
    end = emphasis.get("end")
    if not all(isinstance(value, int) for value in (line_index, start, end)):
        return _wrap_ass_lines(lines)
    raw_style = cue.get("emphasis_style")
    style = raw_style if isinstance(raw_style, Mapping) else {}
    colour = _hex_to_ass_colour(str(style.get("color") or "")) or emphasis_colour
    scale = max(100, min(150, round(float(style.get("scale") or 1.5) * 100)))
    duration_ms = max(0, min(180, int(style.get("duration_ms") or 120)))
    rendered: list[str] = []
    for index, line in enumerate(lines):
        if index != line_index or start < 0 or end <= start or end > len(line):
            rendered.append(_ass_escape(line))
            continue
        rendered.append(
            f"{_ass_escape(line[:start])}"
            f"{{\\c{colour}\\fscx100\\fscy100"
            f"\\t(0,{duration_ms},\\fscx{scale}\\fscy{scale})}}"
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
    time_offset_seconds: float = 0,
) -> bytes:
    """Create one approved ASS overlay for the title and manually reviewed captions."""

    spec = visual_style_spec(output_profile)
    canvas = spec["canvas"]
    title_style = spec["title"]
    accent_style = spec["accent"]
    caption_style = spec["subtitle"]
    overlay_preview = build_business_talking_head_overlay_preview(
        segments,
        title=title,
        output_profile=output_profile,
        caption_groups=caption_groups,
        caption_emphasis=caption_emphasis,
        spoken_ranges=spoken_ranges,
    )
    header = f"""[Script Info]
Title: VideoInsight business talking-head overlay
ScriptType: v4.00+
PlayResX: {canvas["width"]}
PlayResY: {canvas["height"]}

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Title,YaHei,{title_style["font_size"]},&H00FCFAF8,&H00FCFAF8,&H30000000,&H00000000,-1,0,0,0,100,100,0,0,1,{title_style["outline_width"]},{title_style["shadow"]},7,{title_style["safe_left"]},{title_style["safe_left"]},{title_style["safe_top"]},1
Style: Accent,Arial,1,&H00ED3A7C,&H00ED3A7C,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Caption,YaHei,{caption_style["font_size"]},&H00FCFAF8,&H00FCFAF8,&H30000000,&H00000000,-1,0,0,0,100,100,0.18,0,1,{caption_style["outline_width"]},{caption_style["shadow"]},2,{round(canvas["width"] * 0.08)},{round(canvas["width"] * 0.08)},{caption_style["safe_bottom"]},1

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
        title_height = len(title_preview["lines"]) * round(title_style["font_size"] * 1.22)
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
    caption_emphasis: list[CaptionEmphasis] = Field(default_factory=list, max_length=140)
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


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str
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
            str(item).strip()[:20]
            for item in (bgm_keywords or [])
            if str(item).strip()
        ][:6],
        caption_groups=list(caption_groups or []),
        caption_group_source=caption_group_source,
        caption_emphasis=list(caption_emphasis or []),
        smart_opening=opening,
        explanation=explanation.strip(),
        warnings=warnings,
        provider_name=provider_name,
        is_mock=is_mock,
        usage=dict(usage or {}),
    )
