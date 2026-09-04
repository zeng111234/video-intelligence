"""Strict transcript-to-edit grammar for the no-external-visuals renderer.

This module deliberately contains no provider, asset-library, or image
generation integration.  A semantic annotation describes *what is being
said*; the local style engine below decides the small set of legal treatments.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import jieba

JY_CLONE_GRAMMAR_ONLY = "JY_CLONE_GRAMMAR_ONLY"
GRAMMAR_ONLY_PRESET_ID = "talking-head-grammar-only-v1"
SEMANTIC_ROLES = frozenset(
    {
        "HOOK", "KEY_CLAIM", "NUMBER", "PRICE", "PERCENT", "NEGATIVE",
        "POSITIVE", "WARNING", "QUESTION", "CONCLUSION", "COMPARISON",
        "STEP", "PROCESS", "EXAMPLE", "CTA", "PRODUCT", "LOCATION",
        "PERSON", "SCENE", "TRANSITION", "LOW_INFORMATION",
    }
)
ALLOWED_CAMERA_ACTIONS = frozenset(
    {"neutral", "punch_in_soft", "punch_in_medium", "slow_push", "reset"}
)
ALLOWED_SYMBOLS = frozenset(
    {"red_x", "green_check", "question", "warning", "arrow", "underline", "burst_lines"}
)
FORBIDDEN_VISUAL_KEYS = frozenset(
    {"broll", "brolls", "pip", "external_image", "stock_video", "stock_image",
     "ai_video", "image_generation", "generated_image", "network_asset"}
)


class GrammarOnlyValidationError(ValueError):
    """Raised when an external visual sneaks into a strict timeline."""


_NUMBER = re.compile(r"\d+(?:\.\d+)?(?:%|元|块|万|倍|折|分钟|秒|个|家|人|张|套)?")
_PRICE = re.compile(r"\d+(?:\.\d+)?(?:元|块|块钱)")
_PERCENT = re.compile(r"\d+(?:\.\d+)?%")
_CTA = ("评论", "留言", "关注", "私信", "领取", "收藏", "转发", "下单", "点击")
_WARNING = ("注意", "千万别", "不要", "风险", "警告", "小心", "避免", "不能")
_NEGATIVE = ("不行", "不好", "错误", "失败", "问题", "没有", "不能", "避免")
_POSITIVE = ("成功", "有效", "好用", "推荐", "回头客", "增长", "提升", "解决")
_QUESTION = ("为什么", "怎么", "如何", "吗", "呢", "哪个", "是否")
_CONCLUSION = ("所以", "因此", "结论", "最后", "结果", "总结")
_COMPARISON = ("但是", "不过", "相比", "更", "比", "区别", "以前")
_STEP = ("第一", "第二", "第三", "首先", "其次", "然后", "接着", "步骤")
_PROCESS = ("方法", "流程", "过程", "操作", "做法", "执行")


def _text(segment: Mapping[str, Any]) -> str:
    return re.sub(r"\s+", "", str(segment.get("text") or "")).strip()


def _candidate(text: str, patterns: Sequence[str]) -> str | None:
    for value in patterns:
        if value in text:
            return value
    return None


_KEYWORD_STOPWORDS = frozenset(
    {
        "最近", "今天", "现在", "这个", "那个", "一个", "一些", "我们", "你们",
        "他们", "自己", "其实", "基本", "就是", "然后", "所以", "但是", "不过",
        "因为", "如果", "可以", "能够", "还是", "已经", "还有", "非常", "比较",
        "真的", "大家", "时候", "地方", "东西", "什么", "怎么", "如何", "有人",
        "这种", "那种", "这里", "那里", "以后", "之前", "之后",
    }
)


def _keyword_candidates(source: str, primary: str) -> list[str]:
    """Return short lexical candidates without inventing content.

    Numeric matches are kept first because they are usually the strongest
    visual anchors.  Jieba only supplies boundaries inside the current
    transcript segment; it never supplies a phrase or a visual treatment.
    """

    values: list[str] = []
    for value in [primary, *_NUMBER.findall(source)]:
        value = re.sub(r"\s+", "", str(value or ""))
        if 2 <= len(value) <= 8 and value in source and value not in values:
            values.append(value)
    tokens = []
    for token in jieba.lcut(source, cut_all=False):
        token = re.sub(r"[^\w\u4e00-\u9fff%．.块元折倍]+", "", str(token or ""))
        if (
            2 <= len(token) <= 8
            and token in source
            and token not in _KEYWORD_STOPWORDS
            and not token.isdigit()
        ):
            tokens.append(token)
    for token in sorted(dict.fromkeys(tokens), key=lambda item: (-len(item), source.find(item))):
        if token not in values:
            values.append(token)
        if len(values) >= 2:
            break
    return values[:2] or ([primary] if primary and primary in source else [])


def _annotation(segment: Mapping[str, Any], index: int, term: str, roles: list[str], importance: float, emotion: str = "neutral") -> dict[str, Any]:
    source = _text(segment)
    start = float(segment.get("start") or 0)
    end = float(segment.get("end") or start)
    return {
        "start": round(start, 3), "end": round(end, 3), "text": term,
        "semantic_text": term, "semantic_roles": list(dict.fromkeys(roles)),
        "importance": round(max(0.0, min(1.0, importance)), 3),
        "emotion": emotion, "concrete_visual_subject": None,
        "source_segment_index": index, "source_text": source,
        "grounded_in_text": bool(term and term in source),
        "keyword_candidates": _keyword_candidates(source, term),
    }


def annotate_transcript_segments(segments: Sequence[Mapping[str, Any]], *, duration_seconds: float = 0.0) -> list[dict[str, Any]]:
    """Create sparse, source-grounded semantic annotations from transcript text."""

    candidates: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            continue
        source = _text(segment)
        if not source:
            continue
        matches: list[tuple[str, list[str], float, str]] = []
        price = _PRICE.search(source)
        percent = _PERCENT.search(source)
        number = _NUMBER.search(source)
        if price:
            matches.append((price.group(0), ["PRICE", "NUMBER"], 0.92, "neutral"))
        elif percent:
            matches.append((percent.group(0), ["PERCENT", "NUMBER"], 0.90, "positive"))
        elif number:
            matches.append((number.group(0), ["NUMBER"], 0.78, "neutral"))
        for patterns, role, emotion, score in (
            (_WARNING, "WARNING", "negative", 0.86),
            (_CTA, "CTA", "positive", 0.82),
            (_QUESTION, "QUESTION", "neutral", 0.75),
            (_CONCLUSION, "CONCLUSION", "positive", 0.86),
            (_COMPARISON, "COMPARISON", "neutral", 0.72),
            (_STEP, "STEP", "neutral", 0.78),
            (_PROCESS, "PROCESS", "neutral", 0.70),
            (_POSITIVE, "POSITIVE", "positive", 0.80),
            (_NEGATIVE, "NEGATIVE", "negative", 0.84),
        ):
            term = _candidate(source, patterns)
            if term:
                matches.append((term, [role], score, emotion))
        if not matches:
            # A short phrase from the actual segment is a legal low-information
            # beat; it never invents a subject or an answer.
            term = source[: min(6, len(source))]
            matches.append((term, ["LOW_INFORMATION"], 0.42, "neutral"))
        term, roles, score, emotion = matches[0]
        if index == 0:
            roles = ["HOOK", *roles]
            score = max(score, 0.88)
        elif score >= 0.78 and len(source) >= 6:
            roles = ["KEY_CLAIM", *roles]
        candidates.append(_annotation(segment, index, term, roles, score, emotion))

    # Select by semantic importance while retaining the source order.  This
    # is a density guard, not a fixed answer: it works for unfamiliar topics.
    # Keep a real short-video cadence instead of the old five-event ceiling.
    # The 2.8s spacing guard remains the hard visual-density limiter.
    max_events = min(
        len(candidates),
        max(8, round(max(1.0, duration_seconds or 60.0) / 60.0 * 12)),
    )
    selected: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (-float(item["importance"]), item["start"])):
        if any(abs(float(candidate["start"]) - float(item["start"])) < 2.8 for item in selected):
            continue
        selected.append(candidate)
        if len(selected) >= max_events:
            break
    return sorted(selected, key=lambda item: (float(item["start"]), int(item["source_segment_index"])))


def _short_text(value: str) -> str:
    value = re.sub(r"\s+", "", value)
    return value[:8] if len(value) > 8 else value


def build_grammar_style_events(annotations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Map semantic roles to legal local treatments, never to external assets."""

    events: list[dict[str, Any]] = []
    high_count = 0
    for index, annotation in enumerate(annotations):
        roles = [str(role) for role in annotation.get("semantic_roles") or [] if str(role) in SEMANTIC_ROLES]
        if not roles:
            continue
        role = next((item for item in ("PRICE", "PERCENT", "NUMBER", "WARNING", "NEGATIVE", "POSITIVE", "QUESTION", "CONCLUSION", "CTA", "STEP", "PROCESS", "KEY_CLAIM", "HOOK") if item in roles), "LOW_INFORMATION")
        semantic_text = str(annotation.get("semantic_text") or annotation.get("text") or "")
        source_text = str(annotation.get("source_text") or "")
        if not semantic_text or semantic_text not in source_text:
            continue
        start, end = float(annotation.get("start") or 0), float(annotation.get("end") or 0)
        if end <= start:
            continue
        importance = float(annotation.get("importance") or 0)
        if role in {"PRICE", "PERCENT", "NUMBER"}:
            emphasis = "warm_yellow"
        elif role in {"WARNING", "NEGATIVE"}:
            emphasis = "warm_red"
        elif role in {"POSITIVE", "CONCLUSION", "CTA"}:
            emphasis = "warm_green"
        else:
            emphasis = "warm_yellow"
        intensity = 1
        keyword_candidates = [
            str(value) for value in annotation.get("keyword_candidates") or []
            if str(value) and str(value) in source_text
        ] or [semantic_text]
        keyword_limit = 2 if role in {
            "PRICE", "PERCENT", "NUMBER", "KEY_CLAIM", "WARNING", "NEGATIVE",
            "CONCLUSION", "CTA", "PRODUCT", "PROCESS",
        } else 1
        for keyword_index, keyword in enumerate(keyword_candidates[:keyword_limit]):
            events.append({
                "event_id": f"grammar-keyword-{index + 1:02d}-{keyword_index + 1}",
                "type": "keyword_emphasis",
                "start": round(start, 3), "end": round(end, 3), "semantic_role": role,
                "semantic_text": keyword, "source_text": source_text,
                "emphasis_color": emphasis, "visual_intensity": intensity,
                "importance": importance,
                "source_segment_index": annotation.get("source_segment_index"),
                "grounded_in_text": True,
                "sfx_profile": (
                    "pop_soft" if role in {"PRICE", "PERCENT", "NUMBER"}
                    else "warning_tick" if role in {"WARNING", "NEGATIVE"}
                    else "success_ping" if role in {"POSITIVE", "CONCLUSION", "CTA"}
                    else "tick_soft"
                ),
            })
        camera = None
        if role in {"HOOK", "KEY_CLAIM", "NUMBER", "PRICE", "PERCENT", "WARNING", "CONCLUSION", "CTA"}:
            camera = (
                "punch_in_medium"
                if role in {"PRICE", "NUMBER", "PERCENT", "WARNING"} and importance >= 0.88
                else "punch_in_soft"
            )
        if camera:
            events.append({
                "event_id": f"grammar-camera-{index + 1:02d}", "type": "camera",
                "start": round(start, 3), "end": round(end, 3), "camera_action": camera,
                "semantic_role": role, "semantic_text": semantic_text,
                "source_text": source_text, "visual_intensity": 2,
                "importance": importance,
                "source_segment_index": annotation.get("source_segment_index"),
                "grounded_in_text": True,
            })
        symbol = None
        reason = None
        if role in {"NEGATIVE", "WARNING"} and any(term in source_text for term in _NEGATIVE + _WARNING):
            symbol, reason = ("red_x", "明确否定或风险语义") if role == "NEGATIVE" else ("warning", "明确警告语义")
        elif role in {"POSITIVE", "CONCLUSION"}:
            symbol, reason = "green_check", "明确正向或结论语义"
        elif role == "QUESTION":
            symbol, reason = "question", "明确疑问语义"
        elif role == "CTA":
            symbol, reason = "arrow", "明确行动号召语义"
        # Numbers are emphasized in the subtitle itself.  A separate badge
        # made the old render look like a yellow sun and competed with speech.
        if symbol:
            high_count += 1
            events.append({
                "event_id": f"grammar-symbol-{index + 1:02d}", "type": "semantic_symbol",
                "symbol": symbol, "start": round(start, 3), "end": round(end, 3),
                "style_id": {
                    "red_x": "grammar_red_x", "green_check": "grammar_green_check",
                    "question": "grammar_question", "warning": "grammar_warning",
                    "arrow": "grammar_arrow", "underline": "grammar_underline",
                    "burst_lines": "grammar_burst_lines",
                }[symbol],
                "semantic_role": role, "semantic_text": semantic_text,
                "source_text": source_text, "reason": reason, "visual_intensity": 3,
                "importance": importance,
                "sfx_profile": (
                    "warning_tick" if role in {"WARNING", "NEGATIVE"}
                    else "success_ping" if role in {"POSITIVE", "CONCLUSION", "CTA"}
                    else "tick_soft"
                ),
                "source_segment_index": annotation.get("source_segment_index"),
                "grounded_in_text": True,
            })
        if role in {"PRICE", "PERCENT", "NUMBER", "CONCLUSION", "CTA"}:
            phrase = _short_text(semantic_text)
            if 2 <= len(phrase) <= 8:
                events.append({
                    "event_id": f"grammar-text-{index + 1:02d}", "type": "text_emphasis",
                    "start": round(start, 3), "end": round(end, 3), "text": phrase,
                    "semantic_role": role, "semantic_text": semantic_text,
                    "source_text": source_text, "visual_intensity": 3,
                    "importance": importance,
                    "source_segment_index": annotation.get("source_segment_index"),
                    "grounded_in_text": True,
                })
    # Breathing rule: three consecutive high beats are reduced to keyword-only.
    ordered = sorted(events, key=lambda item: (float(item.get("start") or 0), item["event_id"]))
    streak = 0
    for event in ordered:
        if int(event.get("visual_intensity") or 0) >= 3:
            streak += 1
            if streak >= 3:
                event["visual_intensity"] = 1
                if event.get("type") == "semantic_symbol":
                    event["disabled_by_breathing_rule"] = True
        else:
            streak = 0
    return [event for event in ordered if not event.get("disabled_by_breathing_rule")]


def build_grammar_only_timeline(segments: Sequence[Mapping[str, Any]], *, duration_seconds: float = 0.0) -> dict[str, Any]:
    annotations = annotate_transcript_segments(segments, duration_seconds=duration_seconds)
    events = build_grammar_style_events(annotations)
    return {
        "mode": JY_CLONE_GRAMMAR_ONLY, "style_engine": "local-style-engine-v1",
        "semantic_annotations": annotations, "events": events,
        "brolls": [], "pip": [], "external_image": [], "network_assets": [],
        "visual_intensity_max": max((int(event.get("visual_intensity") or 0) for event in events), default=0),
        "external_visuals": False,
    }


def validate_grammar_only_timeline(timeline: Mapping[str, Any]) -> list[str]:
    """Return all forbidden entries; callers must fail instead of falling back."""

    violations: list[str] = []
    def walk(value: Any, path: str = "timeline") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key).lower()
                if key_text in FORBIDDEN_VISUAL_KEYS and child:
                    violations.append(f"{path}.{key}")
                if (
                    key_text == "type"
                    and str(child) == "semantic_symbol"
                    and isinstance(value.get("symbol"), str)
                    and value.get("symbol") not in ALLOWED_SYMBOLS
                ):
                    violations.append(f"{path}.symbol={value.get('symbol')}")
                if key_text in {"mode", "visual_type", "asset_origin", "source_type"} and str(child).lower() in {"pip", "broll", "external_image", "stock_video", "stock_image", "ai_video"}:
                    violations.append(f"{path}.{key}={child}")
                walk(child, f"{path}.{key}")
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
    walk(timeline)
    # A strict plan must not claim external visuals are enabled, even if the
    # lists happen to be empty.
    if timeline.get("external_visuals") is True:
        violations.append("timeline.external_visuals=true")
    return sorted(set(violations))
