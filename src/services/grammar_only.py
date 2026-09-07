"""Strict transcript-to-edit grammar for the no-external-visuals renderer.

This module deliberately contains no provider, asset-library, or image
generation integration.  A semantic annotation describes *what is being
said*; the local style engine below decides the small set of legal treatments.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import jieba.posseg as jieba_posseg

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
_CHINESE_NUMBER = re.compile(
    r"好?[一二两三四五六七八九十百千万亿几]+"
    r"(?:个|人|家|张|份|套|笔|条|名|次|块|元|折|倍|分钟|秒)?"
)
_PRICE = re.compile(r"\d+(?:\.\d+)?(?:元|块|块钱)")
_PERCENT = re.compile(r"\d+(?:\.\d+)?%")
_OFFER_NUMBER = r"(?:\d+(?:\.\d+)?|好?[一二两三四五六七八九十百千万亿几]+)"
_OFFER_PATTERN = re.compile(
    rf"(?:充值满|充值|充|满)"
    rf"{_OFFER_NUMBER}(?:元|块|块钱|%|折)?"
    rf"[^\d一二两三四五六七八九十百千万亿几]{{0,2}}"
    rf"(?:加送|送|赠|返)"
    rf"{_OFFER_NUMBER}(?:元|块|块钱|%|折)?"
 )
_CTA = ("评论", "留言", "关注", "私信", "领取", "收藏", "转发", "下单", "点击")
_OUTCOME = ("成了", "变成", "升级成", "拿到", "获得", "得到", "裂变出")
_PRODUCT = ("系统", "软件", "工具", "设备", "产品", "小程序", "服务", "方案")
_BENEFIT = (
    "免费", "奖励", "优惠", "收益", "赚钱", "省钱", "回头客", "粉丝",
    "口碑", "传播", "转化", "成交", "增长", "提升",
)
_GENERIC_QUANTITY_TERMS = frozenset(
    {"一个", "一个月", "一种", "一份", "一套", "一张", "一次", "几次", "几个"}
)


def _useful_chinese_number(value: str) -> bool:
    """Reject isolated quantity characters such as the ``一`` in ``一点``."""

    return len(value) >= 2 and value not in _GENERIC_QUANTITY_TERMS


def _anchor_after_marker(source: str, markers: Sequence[str]) -> str | None:
    """Find a grounded content anchor after a generic result/CTA marker."""

    for marker in markers:
        marker_index = source.find(marker)
        if marker_index < 0:
            continue
        suffix = source[marker_index + len(marker) :]
        candidates = _keyword_candidates(suffix, "")
        if candidates:
            return candidates[0]
        # Short conclusion-led sentences such as “所以大多数人都办了” may
        # not yield a jieba noun candidate. Keep a bounded, transcript-grounded
        # suffix instead of highlighting the discourse marker itself.
        suffix = re.sub(r"^[，,、。；;：:\s]+", "", suffix)
        if len(suffix) >= 2:
            return suffix[:8]
    return None


def _cta_anchor(source: str) -> str | None:
    """Prefer the concrete CTA target over the CTA verb itself."""

    for marker in _CTA:
        marker_index = source.find(marker)
        if marker_index < 0:
            continue
        suffix = source[marker_index + len(marker) :]
        candidates = _keyword_candidates(suffix, "")
        # A verb-object compound such as ``填写课程名称`` is readable, but the
        # object is the stronger short-video emphasis.  Keep the longest
        # candidate that does not begin with a lexical verb.
        noun_candidates = [
            value
            for value in candidates
            if str((jieba_posseg.lcut(value, HMM=True) or [None])[0].flag or "")[:1]
            != "v"
            and not re.fullmatch(r"\d+(?:\.\d+)?(?:%|元|块|块钱)?", value)
        ]
        if noun_candidates:
            return max(noun_candidates, key=lambda value: (len(value), -candidates.index(value)))
        non_numeric_candidates = [
            value
            for value in candidates
            if not re.fullmatch(r"\d+(?:\.\d+)?(?:%|元|块|块钱)?", value)
        ]
        if non_numeric_candidates:
            return non_numeric_candidates[0]
        if candidates:
            return candidates[0]
    return None


def _product_anchor(source: str, markers: Sequence[str]) -> str | None:
    """Return the concrete noun phrase adjacent to a generic product label."""

    lexical_tokens = list(jieba_posseg.lcut(source, HMM=True))
    token_texts = [re.sub(r"\s+", "", str(token.word or "")) for token in lexical_tokens]
    for marker in markers:
        marker_index = source.find(marker)
        if marker_index < 0:
            continue
        # The lexical scorer already knows about verb-object and noun-noun
        # phrases.  Prefer a candidate that contains the marker, or one that
        # can be joined to it exactly, before inspecting adjacent tokens.
        candidates = _keyword_candidates(source, marker)
        for candidate in candidates:
            if candidate != marker and marker in candidate:
                return candidate
            joined = f"{candidate}{marker}"
            if candidate != marker and len(joined) <= 8 and joined in source:
                return joined
        for start_index in range(len(token_texts)):
            combined = ""
            end_index = start_index
            while end_index < len(token_texts) and len(combined) <= len(marker):
                combined += token_texts[end_index]
                if combined == marker:
                    break
                end_index += 1
            if combined != marker:
                continue
            prefix_tokens: list[str] = []
            previous_index = start_index - 1
            while previous_index >= 0 and len(prefix_tokens) < 2:
                previous = re.sub(
                    r"\s+", "", str(lexical_tokens[previous_index].word or "")
                )
                previous_flag = str(lexical_tokens[previous_index].flag or "")[:1]
                if previous in {"了", "的", "这", "套", "这套", "一套", "这个", "该"}:
                    previous_index -= 1
                    continue
                if previous and previous_flag in {"n", "a"}:
                    prefix_tokens.insert(0, previous)
                    previous_index -= 1
                    continue
                if prefix_tokens:
                    break
                previous_index -= 1
            if prefix_tokens:
                candidate = f"{''.join(prefix_tokens)}{marker}".strip()
                if 2 <= len(candidate) <= 8:
                    return candidate
        return marker
    return None
_WARNING = ("注意", "千万别", "不要", "风险", "警告", "小心", "避免", "不能")
_NEGATIVE = (
    "不行", "不好", "错误", "失败", "问题", "没有", "不能", "不可",
    "避免", "不是", "禁止", "风险", "亏损", "下降", "减少", "过时",
)
_POSITIVE = ("成功", "有效", "好用", "推荐", "回头客", "增长", "提升", "解决")
_QUESTION = ("为什么", "怎么", "如何", "吗", "呢", "哪个", "是否")
_CONCLUSION = ("所以", "因此", "结论", "最后", "结果", "总结")
_COMPARISON = ("但是", "不过", "相比", "更", "比", "区别", "以前")
_STEP = ("第一", "第二", "第三", "首先", "其次", "然后", "接着", "步骤")
_PROCESS = ("方法", "流程", "过程", "操作", "做法", "执行")
_POSITIVE_DEGREE_PATTERN = re.compile(
    r"(?:好|棒|厉害|牛|强|稳|香|绝|漂亮)(?:的|得)?不行"
)


def _positive_degree_term(text: str) -> str | None:
    """Return a positive-degree idiom, not the negative word inside it."""

    match = _POSITIVE_DEGREE_PATTERN.search(text)
    return match.group(0) if match else None


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
        "办完", "马上", "送一", "半个", "每笔", "一份", "一套",
        "一个", "一个月", "一种", "一张", "一次", "几次", "几个",
        "你的", "也", "还", "再", "才", "挺", "很", "都", "能", "会", "要",
        "把", "给", "从", "向", "在", "与", "和", "等于", "所有", "这样",
        "那样", "一下", "只是", "正在", "通过",
    }
)


def _keyword_candidates(source: str, primary: str) -> list[str]:
    """Return short lexical candidates without inventing content.

    Numeric matches are kept first because they are usually the strongest
    visual anchors.  Jieba only supplies boundaries inside the current
    transcript segment; it never supplies a phrase or a visual treatment.
    """

    values: list[str] = []
    # ``primary`` is often a semantic anchor such as ``49块`` or ``回头客``.
    # It is not always a good display keyword, though: low-information
    # annotations may use the first six characters of a sentence as their
    # anchor (for example, a subject followed by several function words).
    # Let lexical scoring choose the meaningful noun/action in that case.
    primary_text = re.sub(r"\s+", "", str(primary or ""))
    primary_is_numeric = bool(
        _NUMBER.fullmatch(primary_text) or _CHINESE_NUMBER.fullmatch(primary_text)
    )
    primary_is_offer = bool(_OFFER_PATTERN.fullmatch(primary_text))
    if primary_is_numeric or primary_is_offer or (
        2 <= len(primary_text) <= 4
        and primary_text in source
        and primary_text not in _KEYWORD_STOPWORDS
    ):
        values.append(primary_text)
    elif (
        2 <= len(primary_text) <= 8
        and primary_text in source
        and primary_text not in _KEYWORD_STOPWORDS
        and len(
            [
                token
                for token in jieba_posseg.lcut(primary_text, HMM=True)
                if str(token.word or "") not in _KEYWORD_STOPWORDS
                and str(token.flag or "")[:1] in {"n", "a"}
            ]
        ) >= 2
    ):
        # A grounded compound noun such as "门店小程序" is a valid visual
        # anchor even when it is longer than the short 2--4 character
        # fallback.  Function-word-heavy sentence prefixes are still
        # rejected because their lexical run does not meet this condition.
        values.append(primary_text)
    numeric_values = [*_NUMBER.findall(source), *_CHINESE_NUMBER.findall(source)]
    for value in numeric_values:
        value = re.sub(r"\s+", "", str(value or ""))
        if (
            2 <= len(value) <= 8
            and value in source
            and value not in _KEYWORD_STOPWORDS
            and (
                bool(re.search(r"\d", value))
                or _useful_chinese_number(value)
            )
            and value not in values
        ):
            values.append(value)

    lexical_tokens: list[tuple[str, str]] = []
    for token in jieba_posseg.lcut(source, HMM=True):
        token_text = re.sub(
            r"[^\w\u4e00-\u9fff%．.块元折倍]+", "", str(token.word or "")
        )
        if token_text:
            lexical_tokens.append((token_text, str(token.flag or "")))

    candidates: list[tuple[str, float, int]] = []
    source_cursor = 0
    for token_index, (token_text, flag) in enumerate(lexical_tokens):
        token_start = source.find(token_text, source_cursor)
        source_cursor = max(source_cursor, token_start + len(token_text))
        if (
            2 <= len(token_text) <= 8
            and token_text in source
            and token_text not in _KEYWORD_STOPWORDS
            and not token_text.isdigit()
            and flag[:1] in {"n", "v", "a"}
        ):
            score = {"n": 4.0, "v": 3.0, "a": 2.5}.get(flag[:1], 1.0)
            score += min(2.0, max(0, len(token_text) - 2) * 0.35)
            if token_start >= 0 and any(
                marker in source[max(0, token_start - 4) : token_start]
                for marker in ("不", "没", "无", "不用", "不能", "避免")
            ):
                score += 0.8
            candidates.append((token_text, score, token_start if token_start >= 0 else 9999))

        # Keep a compact noun/action phrase when a meaningful expression was
        # split into adjacent lexical pieces. It is still fully grounded in
        # the current segment and never comes from a sample answer.
        if token_index:
            previous_text, previous_flag = lexical_tokens[token_index - 1]
            if (
                previous_flag[:1] in {"n", "v", "a"}
                and flag[:1] in {"n", "v", "a"}
                and previous_text not in _KEYWORD_STOPWORDS
                and token_text not in _KEYWORD_STOPWORDS
            ):
                phrase = previous_text + token_text
                phrase_start = source.find(phrase)
                if 2 <= len(phrase) <= 8 and phrase_start >= 0:
                    phrase_score = 5.0 + min(1.5, (len(phrase) - 2) * 0.3)
                    # A verb followed by its object is usually a more useful
                    # short-video emphasis than two adjacent descriptive
                    # tokens.  This keeps concrete expressions such as a
                    # method/object pair ahead of sentence-prefix fragments
                    # without adding any topic-specific vocabulary.
                    if previous_flag[:1] == "v" and flag[:1] == "n":
                        phrase_score += 1.2
                    elif previous_flag[:1] == "n" and flag[:1] == "n":
                        phrase_score += 0.2
                    candidates.append(
                        (
                            phrase,
                            phrase_score,
                            phrase_start,
                        )
                    )

    # Jieba may split a concrete noun into an adjective/noun run (for
    # example, ``门店`` + ``小`` + ``程序``).  Join a short uninterrupted run
    # of content tokens so the emphasis remains the actual object rather than
    # a partial token.  This is intentionally lexical and topic-agnostic.
    for start_index in range(len(lexical_tokens)):
        phrase_parts: list[str] = []
        phrase_start = -1
        for end_index in range(start_index, min(len(lexical_tokens), start_index + 4)):
            token_text, flag = lexical_tokens[end_index]
            if (
                not token_text
                or token_text in _KEYWORD_STOPWORDS
                or flag[:1] not in {"n", "a"}
            ):
                break
            if phrase_start < 0:
                phrase_start = source.find(token_text)
            phrase_parts.append(token_text)
            phrase = "".join(phrase_parts)
            if 2 <= len(phrase) <= 8 and phrase_start >= 0 and phrase in source:
                candidates.append(
                    (
                        phrase,
                        5.4 + min(2.0, (len(phrase) - 2) * 0.38),
                        phrase_start,
                    )
                )

    for token, _score, _start in sorted(
        dict.fromkeys(candidates),
        key=lambda item: (-item[1], item[2], -len(item[0])),
    ):
        if token not in values:
            values.append(token)
        if len(values) >= 2:
            break
    return values[:2]


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
        "suppress_semantic_symbol": bool(_positive_degree_term(source)),
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
        offer = _OFFER_PATTERN.search(source)
        if offer:
            # Keep the complete, transcript-grounded offer as the semantic
            # anchor.  Splitting it into an isolated number loses the action
            # and benefit relationship that makes the cue important.
            matches.append((offer.group(0), ["PRICE", "NUMBER", "KEY_CLAIM"], 0.97, "positive"))
        price = _PRICE.search(source)
        percent = _PERCENT.search(source)
        number = _NUMBER.search(source)
        if number is None:
            number = next(
                (
                    match
                    for match in _CHINESE_NUMBER.finditer(source)
                    if _useful_chinese_number(match.group(0))
                ),
                None,
            )
        if offer:
            # The complete offer already carries the stronger semantic beat.
            # Numeric matches remain available as keyword candidates, but do
            # not replace the offer-level annotation.
            pass
        elif price:
            matches.append((price.group(0), ["PRICE", "NUMBER"], 0.92, "neutral"))
        elif percent:
            matches.append((percent.group(0), ["PERCENT", "NUMBER"], 0.90, "positive"))
        elif number:
            matches.append((number.group(0), ["NUMBER"], 0.78, "neutral"))
        positive_degree = _positive_degree_term(source)
        if positive_degree:
            matches.append((positive_degree, ["POSITIVE", "KEY_CLAIM"], 0.84, "positive"))
        for patterns, role, emotion, score in (
            (_WARNING, "WARNING", "negative", 0.86),
            (_CTA, "CTA", "positive", 0.82),
            (_QUESTION, "QUESTION", "neutral", 0.75),
            (_CONCLUSION, "CONCLUSION", "positive", 0.86),
            (_OUTCOME, "CONCLUSION", "positive", 0.82),
            (_PRODUCT, "PRODUCT", "neutral", 0.76),
            (_COMPARISON, "COMPARISON", "neutral", 0.72),
            (_STEP, "STEP", "neutral", 0.78),
            (_PROCESS, "PROCESS", "neutral", 0.70),
            (_POSITIVE + _BENEFIT, "POSITIVE", "positive", 0.80),
            (_NEGATIVE, "NEGATIVE", "negative", 0.84),
        ):
            term = _candidate(source, patterns)
            if role == "NEGATIVE" and positive_degree:
                # In phrases such as “效果好的不行”, “不行” is an
                # intensifier.  It must never become a red-X event.
                continue
            if term:
                matches.append((term, [role], score, emotion))
        if not matches:
            # A short phrase from the actual segment is a legal low-information
            # beat; it never invents a subject or an answer.
            anchors = _keyword_candidates(source, "")
            term = anchors[0] if anchors else source[: min(6, len(source))]
            matches.append(
                (
                    term,
                    ["LOW_INFORMATION"],
                    0.56 if anchors else 0.42,
                    "neutral",
                )
            )
        # A segment can contain several signals at once.  Pick the signal that
        # best describes the sentence's intent instead of letting an earlier
        # numeric/conclusion match mask an explicit warning or CTA.  This is a
        # role policy, not a phrase list, so it remains valid for unfamiliar
        # customer topics.
        role_priority = {
            "NEGATIVE": 6,
            "WARNING": 6,
            "CTA": 6,
            "QUESTION": 5,
            "PRICE": 4,
            "PERCENT": 4,
            "NUMBER": 4,
            "CONCLUSION": 3,
            "POSITIVE": 3,
            "KEY_CLAIM": 2,
        }
        def match_priority(match: tuple[str, list[str], float, str]) -> tuple[int, float, int]:
            match_roles = match[1]
            return (
                max((role_priority.get(role, 1) for role in match_roles), default=1),
                float(match[2]),
                len(match[0]),
            )

        term, roles, score, emotion = max(matches, key=match_priority)
        # Result and product patterns identify the semantic function, while
        # the lexical anchor identifies the word that should actually receive
        # visual weight.  Both remain exact substrings of this segment.
        semantic_anchor_role = next(
            (
                role
                for role in ("NEGATIVE", "WARNING", "CTA", "CONCLUSION", "PRODUCT", "POSITIVE")
                if role in roles
            ),
            None,
        )
        if semantic_anchor_role is not None:
            anchor = (
                _anchor_after_marker(source, (*_OUTCOME, *_CONCLUSION))
                if semantic_anchor_role == "CONCLUSION"
                else _cta_anchor(source)
                if semantic_anchor_role == "CTA"
                else _product_anchor(source, _PRODUCT)
                if semantic_anchor_role == "PRODUCT"
                else next(
                    (
                        value
                        for value in _keyword_candidates(source, term)
                        if len(value) > len(term) and term in value
                    ),
                    None,
                )
            )
            if anchor is None:
                anchors = _keyword_candidates(source, term)
                anchor = next(
                    (
                        value
                        for value in anchors
                        if value not in _PRODUCT and value not in _CTA
                    ),
                    anchors[0] if anchors else None,
                )
            if anchor:
                term = anchor
                if semantic_anchor_role == "PRODUCT":
                    score = max(score, min(0.86, 0.66 + len(anchor) * 0.03))
                elif semantic_anchor_role in {"CTA", "POSITIVE"}:
                    # A concrete object/action is a stronger cue than the
                    # generic marker that introduced it, but it remains
                    # bounded by the same semantic score.
                    score = max(score, min(0.86, 0.72 + len(anchor) * 0.02))
        if index == 0:
            roles = ["HOOK", *roles]
            score = max(score, 0.88)
        elif score >= 0.78 and len(source) >= 6:
            roles = ["KEY_CLAIM", *roles]
        candidates.append(_annotation(segment, index, term, roles, score, emotion))

    # Select by semantic importance while retaining the source order.  This
    # is a density guard, not a fixed answer: it works for unfamiliar topics.
    # Keep a real short-video cadence instead of the old five-event ceiling.
    # Keep semantic annotation beats slightly closer than asset/symbol beats;
    # subtitle emphasis is a lighter layer and still receives a temporal
    # spacing guard in the renderer.
    annotation_min_gap = 2.2
    max_events = min(
        len(candidates),
        max(8, round(max(1.0, duration_seconds or 60.0) / 60.0 * 18)),
    )
    selected: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (-float(item["importance"]), item["start"])):
        if any(
            abs(float(candidate["start"]) - float(item["start"])) < annotation_min_gap
            for item in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= max_events:
            break
    return sorted(selected, key=lambda item: (float(item["start"]), int(item["source_segment_index"])))


def _short_text(value: str, *, max_length: int = 8) -> str:
    value = re.sub(r"\s+", "", value)
    return value[:max_length] if len(value) > max_length else value


def build_grammar_style_events(annotations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Map semantic roles to legal local treatments, never to external assets."""

    events: list[dict[str, Any]] = []
    high_count = 0
    for index, annotation in enumerate(annotations):
        roles = [str(role) for role in annotation.get("semantic_roles") or [] if str(role) in SEMANTIC_ROLES]
        if not roles:
            continue
        role = next((item for item in ("PRICE", "PERCENT", "NUMBER", "WARNING", "NEGATIVE", "POSITIVE", "QUESTION", "CONCLUSION", "CTA", "STEP", "PROCESS", "PRODUCT", "KEY_CLAIM", "HOOK") if item in roles), "LOW_INFORMATION")
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
        ]
        # LOW_INFORMATION segments may still contribute one grounded noun or
        # action, but never promote the whole sentence as a fake keyword.
        if not keyword_candidates:
            continue
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
                    else "whoosh_soft" if role in {"HOOK", "KEY_CLAIM"} and importance >= 0.82
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
        positive_degree = bool(annotation.get("suppress_semantic_symbol"))
        if role in {"NEGATIVE", "WARNING"} and not positive_degree and any(term in source_text for term in _NEGATIVE + _WARNING):
            symbol, reason = ("red_x", "明确否定或风险语义") if role == "NEGATIVE" else ("warning", "明确警告语义")
        elif role in {"POSITIVE", "CONCLUSION"} and not positive_degree:
            symbol, reason = "green_check", "明确正向或结论语义"
        elif role == "QUESTION":
            symbol, reason = "question", "明确疑问语义"
        elif role == "CTA":
            symbol, reason = "arrow", "明确行动号召语义"
        # Strong numeric meaning may receive a few asymmetric burst lines.
        # This is punctuation bound to the transcript, not a number badge or
        # a generic decorative sun.
        if (
            role in {"NUMBER", "PRICE", "PERCENT"}
            and importance >= 0.88
            and not positive_degree
        ):
            symbol, reason = "burst_lines", "明确数字/价格/比例语义"
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
        if role in {
            "PRICE", "PERCENT", "NUMBER", "CONCLUSION", "CTA",
        } or (
            role in {"KEY_CLAIM", "STEP", "PROCESS", "PRODUCT", "LOCATION", "EXAMPLE"}
            and importance >= 0.80
        ):
            phrase = _short_text(
                semantic_text,
                max_length=10 if role in {"PRICE", "PERCENT", "NUMBER"} else 8,
            )
            if 2 <= len(phrase) <= (10 if role in {"PRICE", "PERCENT", "NUMBER"} else 8):
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
