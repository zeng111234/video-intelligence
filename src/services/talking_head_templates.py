"""Deterministic release-grade plans for short talking-head videos.

This module deliberately contains no network, model, or media side effects.
It turns an approved transcript into one versioned shot plan consumed by the
preview and local renderer.  Real B-roll is an optional, explicitly supplied
asset; when it is absent the plan says so and uses safe talking-head motion.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


TALKING_HEAD_TEMPLATE_VERSION = "talking-head-release-v1.0"
ADAPTIVE_TALKING_HEAD_TEMPLATE_ID = "adaptive_talking_head_v1"
TALKING_HEAD_TEMPLATE_IDS = (
    "pain_point_solution",
    "knowledge_howto",
    "story_resonance",
)

LEGACY_TEMPLATE_ALIASES = {
    "pain_point_solution": ADAPTIVE_TALKING_HEAD_TEMPLATE_ID,
    "knowledge_howto": ADAPTIVE_TALKING_HEAD_TEMPLATE_ID,
    "story_resonance": ADAPTIVE_TALKING_HEAD_TEMPLATE_ID,
    ADAPTIVE_TALKING_HEAD_TEMPLATE_ID: ADAPTIVE_TALKING_HEAD_TEMPLATE_ID,
}

ADAPTIVE_STYLE_TOKENS = {
    "subtitle_style_id": "adaptive_white_base",
    "hook_style_id": "adaptive_original_quote",
    "transition_policy": "semantic_action_adaptive",
    "insert_policy": "semantic_peak_broll_1_to_3",
    "bgm_profile": "commercial_low_intrusion",
    "entry_motion": "fade_in_120ms",
    "emphasis_style": "semantic_local_color",
    "palette_id": "neutral_tech_business_v1",
    "visual_intents": ["speaker", "evidence_broll", "data_chart", "concept_card", "speaker_pip"],
}

# Kept as a compatibility surface for old plans/imports.  Every legacy ID
# deliberately resolves to the same adaptive visual language.
TALKING_HEAD_STYLE_TOKENS = {
    legacy_id: dict(ADAPTIVE_STYLE_TOKENS)
    for legacy_id in (*TALKING_HEAD_TEMPLATE_IDS, ADAPTIVE_TALKING_HEAD_TEMPLATE_ID)
}


def resolve_talking_head_template_id(template_id: str | None) -> str:
    return LEGACY_TEMPLATE_ALIASES.get(
        str(template_id or ""), ADAPTIVE_TALKING_HEAD_TEMPLATE_ID
    )

_TAKE_MARKER = re.compile(
    # An ordinal at the start of a sentence is not necessarily a new take:
    # phrases such as “第一件事” and “第二句话” are ordinary content.  Only
    # accept an explicit take marker (a bare ordinal, or ordinal + 条/个),
    # while retaining the spoken “好，第七…” marker used by recordings.
    r"^(?:好[，,。.!！]?\s*)?(?:(?:第[一二三四五六七八九十百\d]+)(?:条|个)?(?=$|[，,。.!！；;：:]|\s)|下一条|新的一条|重来|重新来|再来一条)"
)
_INCOMPLETE_END = re.compile(
    r"(?:如果|但是|但|所以|因为|然后|以及|并且|而且|只是|除非|否则|其中|比如|当|让|把|在|对|跟|和)$"
)
_QUESTION = re.compile(r"(?:\?|？|为什么|怎么|如何|到底|还是|是不是|有没有|能不能)")
_PAIN_WORDS = ("离职", "断掉", "丢失", "风险", "问题", "不能", "别让", "后果", "客户", "损失")
_KNOWLEDGE_WORDS = ("第一", "第二", "第三", "首先", "其次", "最后", "三点", "三个", "要点", "步骤", "方法")
_STORY_WORDS = ("有一天", "曾经", "后来", "没想到", "经历", "故事", "终于", "转折", "开始", "离开")


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _normalise_segments(segments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    for index, raw in enumerate(segments):
        try:
            start = max(0.0, float(raw.get("start", 0)))
            end = max(start, float(raw.get("end", 0)))
        except (TypeError, ValueError):
            continue
        text = _clean_text(raw.get("text"))
        if not text or end <= start:
            continue
        normalised.append(
            {
                **dict(raw),
                "index": index,
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
            }
        )
    return normalised


def _score_templates(text: str) -> dict[str, int]:
    compact = _clean_text(text)
    return {
        "pain_point_solution": sum(compact.count(word) for word in _PAIN_WORDS)
        + (2 if _QUESTION.search(compact) else 0),
        "knowledge_howto": sum(compact.count(word) for word in _KNOWLEDGE_WORDS)
        + (2 if re.search(r"\d", compact) else 0),
        "story_resonance": sum(compact.count(word) for word in _STORY_WORDS)
        + (2 if re.search(r"后来|没想到|终于", compact) else 0),
    }


def _range(start: float, end: float, **extra: Any) -> dict[str, Any]:
    return {"start": round(start, 3), "end": round(end, 3), **extra}


def _find_take_boundary(items: Sequence[Mapping[str, Any]]) -> tuple[float | None, str | None]:
    for index, item in enumerate(items):
        if index == 0:
            continue
        text = _clean_text(item.get("text"))
        if _TAKE_MARKER.search(text):
            return float(item["start"]), "识别到报号/新 take 标记"
    return None, None


def _pick_hook(items: Sequence[Mapping[str, Any]], end: float) -> Mapping[str, Any] | None:
    candidates = [item for item in items if float(item["end"]) <= end]
    if not candidates:
        return None
    question_candidates = [item for item in candidates if _QUESTION.search(str(item["text"]))]
    return max(question_candidates or candidates, key=lambda item: float(item["start"]))


def _content_end(items: Sequence[Mapping[str, Any]], duration: float, boundary: float | None) -> tuple[float, list[dict[str, Any]]]:
    end = min(duration, boundary if boundary is not None else duration)
    deleted: list[dict[str, Any]] = []
    if boundary is not None and boundary < duration:
        deleted.append(_range(boundary, duration, reason="take 标记后的下一主题/残句不进入本条成片"))
    eligible = [item for item in items if float(item["start"]) < end]
    if boundary is None and eligible and _INCOMPLETE_END.search(str(eligible[-1]["text"])):
        tail_start = float(eligible[-1]["start"])
        if tail_start < end:
            deleted.append(_range(tail_start, end, reason="结尾是未完成连接词，删除残句段"))
            end = tail_start
    return end, deleted


def _ordered_ranges(
    items: Sequence[Mapping[str, Any]],
    content_end: float,
    hook: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    usable = [item for item in items if float(item["start"]) < content_end and float(item["end"]) <= content_end]
    if not usable:
        return [], []
    if hook is None or float(hook["start"]) <= usable[0]["start"] + 0.2:
        ranges = [_range(float(item["start"]), float(item["end"]), reason="按原时间轴保留") for item in usable]
        return ranges, ranges
    hook_start = float(hook["start"])
    hook_end = min(content_end, float(hook["end"]))
    ordered = [
        _range(hook_start, hook_end, reason="冷开场问题钩子", role="A-roll"),
        _range(float(usable[0]["start"]), hook_start, reason="正文回接", role="A-roll"),
    ]
    ordered.extend(
        _range(float(item["start"]), float(item["end"]), reason="正文保留", role="A-roll")
        for item in usable
        if float(item["start"]) >= hook_end
    )
    # Remove zero/overlapping ranges caused by ASR fragments around the hook.
    ordered = [item for item in ordered if item["end"] - item["start"] >= 0.08]
    return ordered, ordered


def _build_shots(
    ordered_ranges: Sequence[Mapping[str, Any]],
    *,
    template_id: str,
    asset: Mapping[str, Any] | None,
    assets_by_shot_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    shots: list[dict[str, Any]] = []
    timeline = 0.0
    shot_index = 0
    rhythm_targets = {
        "pain_point_solution": (2.6, 4.4, 3.2, 5.0),
        "knowledge_howto": (3.0, 4.8, 2.4, 4.0),
        "story_resonance": (4.8, 3.4, 5.6, 2.8),
    }.get(template_id, (3.0, 4.2, 3.2, 4.6))
    for source_range in ordered_ranges:
        source_start = float(source_range["start"])
        source_end = float(source_range["end"])
        cursor = source_start
        while cursor < source_end - 0.08:
            target_seconds = rhythm_targets[shot_index % len(rhythm_targets)]
            end = min(source_end, cursor + target_seconds)
            duration = end - cursor
            shot_id = f"shot-{shot_index + 1:02d}"
            mapped_asset = (
                assets_by_shot_id.get(shot_id)
                if assets_by_shot_id
                else None
            )
            mapped_asset = mapped_asset or (
                asset if asset and shot_index % 4 == 1 else None
            )
            role = "A-roll"
            fallback = "speaker_safe_push" if shot_index % 2 else "speaker_full_safe"
            if mapped_asset:
                role = "B-roll"
            overlay_mode = (
                str(mapped_asset.get("mode") or "")
                if mapped_asset
                else ""
            )
            if overlay_mode not in {"pip", "full"}:
                overlay_mode = "pip" if shot_index % 2 == 0 else "full"
            shot = {
                "shot_id": shot_id,
                "source_start": round(cursor, 3),
                "source_end": round(end, 3),
                "timeline_start": round(timeline, 3),
                "timeline_end": round(timeline + duration, 3),
                "role": role,
                "asset_id": mapped_asset.get("asset_id") if role == "B-roll" else None,
                "asset_source": mapped_asset.get("source") if role == "B-roll" else "none",
                "asset_origin": mapped_asset.get("asset_origin") if role == "B-roll" else None,
                "authorization_status": (
                    mapped_asset.get("authorization_status", "unverified")
                    if role == "B-roll"
                    else "not_applicable"
                ),
                "framing": "full_subject_safe" if fallback == "speaker_full_safe" else "safe_push_no_face_crop",
                "transition": "hard_cut" if shot_index == 0 else "blur_slide_5_frames",
                "visual_emphasis": "hook_underliner" if shot_index == 0 else (
                    f"asset_{overlay_mode}" if role == "B-roll" else fallback
                ),
                "fallback": "none" if role == "B-roll" else "精剪口播降级",
                "overlay_mode": overlay_mode if role == "B-roll" else None,
                "duration_seconds": round(duration, 3),
                "rhythm_seconds": round(target_seconds, 3),
                "rhythm_variant": shot_index % len(rhythm_targets),
            }
            shots.append(shot)
            timeline += duration
            cursor = end
            shot_index += 1
    return shots


def build_talking_head_shot_plan(
    segments: Sequence[Mapping[str, Any]],
    *,
    duration_seconds: float,
    title: str = "",
    broll_asset: Mapping[str, Any] | None = None,
    broll_assets_by_shot_id: Mapping[str, Mapping[str, Any]] | None = None,
    bgm_asset: Mapping[str, Any] | None = None,
    prefer_first_hook: bool = False,
    preserve_source_clock: bool = False,
) -> dict[str, Any]:
    """Build the single source of truth for talking-head local editing."""

    if duration_seconds <= 0:
        raise ValueError("视频时长必须大于 0 秒。")
    items = _normalise_segments(segments)
    transcript = "".join(str(item["text"]) for item in items)
    scores = _score_templates(transcript)
    legacy_template_id = max(
        TALKING_HEAD_TEMPLATE_IDS,
        key=lambda item: (scores[item], -TALKING_HEAD_TEMPLATE_IDS.index(item)),
    )
    # Content classification remains useful to the director, but it no longer
    # selects a different visual skin.  All content routes compile to one
    # adaptive template so preview, ASS and final output share one contract.
    template_id = ADAPTIVE_TALKING_HEAD_TEMPLATE_ID
    take_boundary, take_reason = _find_take_boundary(items)
    content_end, deleted = _content_end(items, duration_seconds, take_boundary)
    # A short source-range acceptance clip must not move a tail phrase to the
    # front: the audio, captions and B-roll all share the original clock.
    hook = items[0] if prefer_first_hook and items else _pick_hook(items, content_end)
    ordered_ranges, retained = _ordered_ranges(items, content_end, hook)
    if preserve_source_clock:
        # Full-length production keeps pauses and the original A/V clock. The
        # director may still place visual intents on semantic spans, but it
        # must not silently compress every ASR gap into a shorter output.
        ordered_ranges = [_range(0.0, duration_seconds, reason="完整源时钟保留")]
        retained = list(ordered_ranges)
        deleted = []
    if not retained:
        retained = [_range(0, content_end, reason="安全回退保留原片")]
        ordered_ranges = list(retained)
    if content_end < duration_seconds and not any(item["end"] == duration_seconds for item in deleted):
        deleted.append(_range(content_end, duration_seconds, reason="内容边界后的素材不进入本条成片"))
    if items and float(items[0]["start"]) > 0:
        deleted.insert(0, _range(0, float(items[0]["start"]), reason="片头无口播静段"))

    shots = _build_shots(
        ordered_ranges,
        template_id=template_id,
        asset=broll_asset,
        assets_by_shot_id=broll_assets_by_shot_id,
    )
    has_real_broll = any(shot["role"] == "B-roll" for shot in shots)
    has_generated_broll = any(
        shot.get("role") == "B-roll"
        and str(shot.get("asset_origin") or "") == "generated_image_asset"
        for shot in shots
    )
    has_stock_broll = any(
        shot.get("role") == "B-roll"
        and str(shot.get("asset_source") or "") in {"pexels", "pixabay"}
        for shot in shots
    )
    assets: list[dict[str, Any]] = []
    seen_asset_ids: set[str] = set()
    for candidate in (
        list((broll_assets_by_shot_id or {}).values())
        + ([broll_asset] if broll_asset else [])
    ):
        if not isinstance(candidate, Mapping):
            continue
        asset_id = str(candidate.get("asset_id") or "")
        if asset_id and asset_id not in seen_asset_ids:
            assets.append(dict(candidate))
            seen_asset_ids.add(asset_id)
    content_blocks = {
        "pain_point_solution": ["hook", "consequence", "solution", "conclusion"],
        "knowledge_howto": ["conclusion", "point_1", "point_2", "point_3", "summary"],
        "story_resonance": ["person_conflict", "process", "turning_point", "resolution"],
    }[legacy_template_id]
    reason = {
        "pain_point_solution": "问题/风险词与疑问句占比最高，适合先抛痛点再给方案。",
        "knowledge_howto": "出现步骤/序号/要点结构，适合结论先行再拆解。",
        "story_resonance": "出现人物经历、过程或转折词，适合按故事节奏组织。",
    }[legacy_template_id]
    if take_reason:
        reason += f"；{take_reason}。"
    if deleted and any("残句" in str(item.get("reason")) for item in deleted):
        reason += "；结尾残句已安全舍弃。"
    bgm = {
        "track_id": bgm_asset.get("asset_id") if bgm_asset else None,
        "title": bgm_asset.get("title") if bgm_asset else None,
        "source": bgm_asset.get("source") if bgm_asset else "none",
        "authorization_status": bgm_asset.get("authorization_status", "unverified") if bgm_asset else "not_selected",
        "auto_eligible": bool(bgm_asset and bgm_asset.get("authorization_status") == "confirmed"),
        "start_seconds": 0.0,
        "energy_segments": [{"start": 0.0, "end": round(sum(float(item["end"]) - float(item["start"]) for item in ordered_ranges), 3), "energy": "克制"}],
        "ducking": {"enabled": True, "voice_priority": True, "target_db": -24, "attack_ms": 20, "release_ms": 450},
        "fade": {"in_seconds": 0.5, "out_seconds": 0.8},
    }
    return {
        "template_id": template_id,
        "template_version": TALKING_HEAD_TEMPLATE_VERSION,
        "selection": {
            "scores": scores,
            "reason": reason,
            "legacy_template_id": legacy_template_id,
            "compatibility_alias": legacy_template_id,
            "override_available": True,
        },
        "content_blocks": content_blocks,
        "hook_source": (
            {"source_start": float(hook["start"]), "source_end": float(hook["end"]), "text": hook["text"]}
            if hook
            else None
        ),
        "retained_ranges": retained,
        "reordered_ranges": ordered_ranges,
        "deleted_ranges": deleted,
        "shots": shots,
        "assets": assets,
        "subtitle": {
            "style_id": ADAPTIVE_STYLE_TOKENS["subtitle_style_id"],
            "hook_style_id": ADAPTIVE_STYLE_TOKENS["hook_style_id"],
            "style_tokens": dict(ADAPTIVE_STYLE_TOKENS),
            "max_lines": 1,
            "safe_area": "lower_third_without_face",
            "visual_emphasis": "sparse_keyword_only",
        },
        "transition_policy": ADAPTIVE_STYLE_TOKENS["transition_policy"],
        "insert_policy": ADAPTIVE_STYLE_TOKENS["insert_policy"],
        "bgm_profile": ADAPTIVE_STYLE_TOKENS["bgm_profile"],
        "bgm": bgm,
        "degradation": {
            "mode": (
                "generated_image_broll"
                if has_generated_broll
                else "broll"
                if has_real_broll
                else "精剪口播降级"
            ),
            "is_broll": has_real_broll,
            "is_stock_broll": has_stock_broll,
            "message": (
                "已绑定生成图，仅用于本地验收，不代表免费素材库或发布授权。"
                if has_generated_broll
                else "已使用用户确认授权的本地 B-roll。"
                if has_real_broll
                else "未配置可用 B-roll，使用主体安全推拉、构图变化和克制转场；这不是 B-roll。"
            ),
        },
        "source_duration_seconds": round(duration_seconds, 3),
        "timeline_duration_seconds": round(sum(float(item["end"]) - float(item["start"]) for item in ordered_ranges), 3),
        "title": _clean_text(title)[:100],
    }


def retime_segments_for_shot_plan(
    segments: Sequence[Mapping[str, Any]], shot_plan: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Map approved source timestamps onto the reordered output timeline."""

    mappings = [item for item in shot_plan.get("shots", []) if isinstance(item, Mapping)]
    output: list[dict[str, Any]] = []
    for raw_index, raw in enumerate(segments):
        try:
            start = float(raw.get("start", 0))
            end = float(raw.get("end", 0))
        except (TypeError, ValueError):
            continue
        mapped_pieces: list[dict[str, Any]] = []
        for shot in mappings:
            source_start = float(shot.get("source_start", 0))
            source_end = float(shot.get("source_end", 0))
            overlap_start = max(start, source_start)
            overlap_end = min(end, source_end)
            if overlap_end <= overlap_start:
                continue
            timeline_start = float(shot.get("timeline_start", 0))
            mapped = {
                **dict(raw),
                "start": round(timeline_start + overlap_start - source_start, 3),
                "end": round(timeline_start + overlap_end - source_start, 3),
            }
            raw_words = raw.get("words")
            if isinstance(raw_words, Sequence) and not isinstance(raw_words, (str, bytes)):
                mapped_words: list[dict[str, Any]] = []
                for word_index, raw_word in enumerate(raw_words):
                    if not isinstance(raw_word, Mapping):
                        continue
                    try:
                        word_start = float(raw_word.get("start", 0))
                        word_end = float(raw_word.get("end", 0))
                    except (TypeError, ValueError):
                        continue
                    word_overlap_start = max(word_start, overlap_start)
                    word_overlap_end = min(word_end, overlap_end)
                    if word_overlap_end <= word_overlap_start:
                        continue
                    mapped_words.append(
                        {
                            "start": round(
                                timeline_start + word_overlap_start - source_start, 3
                            ),
                            "end": round(
                                timeline_start + word_overlap_end - source_start, 3
                            ),
                            "text": str(raw_word.get("text") or "").strip(),
                            "_source_word_index": word_index,
                        }
                    )
                mapped["words"] = mapped_words
            mapped_pieces.append(mapped)

        # A visual shot boundary must not duplicate the subtitle whose source
        # sentence happens to cross that boundary.  The old implementation
        # emitted one full-text cue for every overlapping shot, which made a
        # long sentence flash repeatedly and looked like a scrolling loop.
        mapped_pieces.sort(key=lambda item: (float(item["start"]), float(item["end"])))
        for piece in mapped_pieces:
            if output:
                previous = output[-1]
                same_source = (
                    previous.get("text") == piece.get("text")
                    and previous.get("_source_segment_index")
                    == raw.get("segment_index", raw.get("index", raw_index))
                )
                contiguous = abs(float(previous["end"]) - float(piece["start"])) <= 0.02
                if same_source and contiguous:
                    previous["end"] = max(float(previous["end"]), float(piece["end"]))
                    previous_words = previous.get("words") or []
                    piece_words = piece.get("words") or []
                    if previous_words or piece_words:
                        existing_by_index = {
                            word.get("_source_word_index"): word
                            for word in previous_words
                        }
                        for word in piece_words:
                            source_word_index = word.get("_source_word_index")
                            existing = existing_by_index.get(source_word_index)
                            if existing is not None:
                                existing["start"] = min(existing["start"], word["start"])
                                existing["end"] = max(existing["end"], word["end"])
                            else:
                                previous_words.append(word)
                        previous_words.sort(key=lambda word: (word["start"], word["end"]))
                        previous["words"] = previous_words
                    continue
            mapped = dict(piece)
            mapped["_source_segment_index"] = raw.get(
                "segment_index", raw.get("index", raw_index)
            )
            output.append(mapped)
    output.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    for item in output:
        item.pop("_source_segment_index", None)
        for word in item.get("words") or []:
            word.pop("_source_word_index", None)
    return output
