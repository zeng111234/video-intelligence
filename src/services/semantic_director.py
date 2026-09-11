"""Provider-assisted semantic and creative direction for the video editor.

MiniMax may propose a bounded visual treatment, but it never owns the final
clock, asset rights, layout safety, or renderer command.  The local compiler
and renderer remain the source of truth for executable output.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from base64 import b64encode
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from src.adapters.llm import (
    DisabledCopywritingEngine,
    LLMAdapterError,
    OpenAICompatibleCopywritingEngine,
)

SEMANTIC_DIRECTOR_ROLES = frozenset(
    {
        "HOOK", "KEY_CLAIM", "NUMBER", "PRICE", "PERCENT", "NEGATIVE",
        "POSITIVE", "WARNING", "QUESTION", "CONCLUSION", "COMPARISON",
        "STEP", "PROCESS", "EXAMPLE", "CTA", "PRODUCT", "LOCATION",
        "PERSON", "SCENE", "TRANSITION", "LOW_INFORMATION",
    }
)
SEMANTIC_DIRECTOR_VERSION = "semantic-director-v2"
DIRECTOR_PROMPT_VERSION = "creative-director-prompt-v1"
MAX_DIRECTOR_FRAMES = 16
TARGET_DIRECTOR_FRAMES = 12
DIRECTOR_FRAME_WIDTH = 512

CREATIVE_EVENT_TYPES = frozenset(
    {
        "hook_emphasis",
        "keyword_emphasis",
        "number_emphasis",
        "warning_emphasis",
        "conclusion_emphasis",
        "comparison_visual",
        "process_visual",
        "example_visual",
        "product_visual",
        "location_visual",
        "cta_visual",
        "stable_talking_head",
    }
)
CREATIVE_LAYOUTS = frozenset(
    {
        "caption_integrated",
        "full_screen_broll",
        "large_pip_left",
        "large_pip_right",
        "split_comparison",
        "step_stack",
        "number_focus",
        "subject_background_text",
        "stable_subject",
    }
)
CREATIVE_CAPTION_TREATMENTS = frozenset(
    {
        "static",
        "fade_in",
        "scale_overshoot",
        "slam_keyword",
        "stamp_keyword",
        "shake_keyword",
        "underline_keyword",
        "two_level_conclusion",
    }
)
CREATIVE_CAMERA_MOTIONS = frozenset(
    {"none", "slow_push_in", "punch_in_light", "punch_in_medium", "slow_reframe"}
)
CREATIVE_TRANSITIONS = frozenset(
    {"hard_cut", "short_push", "short_whip", "short_blur_dissolve", "none"}
)
CREATIVE_SFX = frozenset(
    {"none", "soft_pop", "soft_tick", "soft_impact", "short_whoosh", "warning_hit", "stamp_hit"}
)
CREATIVE_VISUAL_TYPES = frozenset(
    {"none", "subject_motion", "licensed_broll", "user_asset", "generated_illustration", "programmatic_infographic", "vector_accent"}
)
_NEGATION_PATTERN = re.compile(r"不是|并非|没有|未曾|未|不|无|取消|取消了|不能|不可|别|禁止")
_NUMBER_TOKEN_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:[%％万元亿元块元个张公里分钟天倍]?)")


def build_director_cache_key(**parts: object) -> str:
    """Return a stable cache key for all inputs that affect direction."""

    payload = {
        str(key): value
        for key, value in sorted(parts.items())
        if value is not None
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _source_text(segment: Mapping[str, Any]) -> str:
    return re.sub(r"\s+", "", str(segment.get("text") or "")).strip()


def _clean_json(content: str) -> str:
    cleaned = str(content or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    return cleaned


def validate_semantic_annotations(
    raw: Any,
    segments: Sequence[Mapping[str, Any]],
    *,
    provider: str = "minimax",
) -> list[dict[str, Any]]:
    """Validate and normalize model output against the immutable transcript."""
    if isinstance(raw, Mapping):
        raw = raw.get("annotations")
    if not isinstance(raw, list):
        raise LLMAdapterError("MiniMax 语义导演返回的标注不是数组。")
    by_index = {
        index: _source_text(segment)
        for index, segment in enumerate(segments)
        if isinstance(segment, Mapping) and _source_text(segment)
    }
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        try:
            index = int(item.get("source_segment_index", item.get("segment_index")))
        except (TypeError, ValueError):
            continue
        source = by_index.get(index, "")
        if not source or index in seen:
            continue
        semantic_text = re.sub(r"\s+", "", str(item.get("semantic_text") or item.get("text") or ""))
        if not semantic_text or semantic_text not in source:
            continue
        roles = [
            str(role).strip().upper()
            for role in item.get("semantic_roles") or []
            if str(role).strip().upper() in SEMANTIC_DIRECTOR_ROLES
        ] or ["LOW_INFORMATION"]
        try:
            importance = max(0.0, min(1.0, float(item.get("importance", 0.0))))
        except (TypeError, ValueError):
            importance = 0.0
        start = float(segments[index].get("start") or 0.0)
        end = float(segments[index].get("end") or start)
        normalized.append(
            {
                "start": round(start, 3),
                "end": round(max(start, end), 3),
                "text": source,
                "semantic_text": semantic_text,
                "semantic_roles": list(dict.fromkeys(roles)),
                "importance": round(importance, 3),
                "emotion": str(item.get("emotion") or "neutral").strip()[:32],
                "concrete_visual_subject": str(item.get("concrete_visual_subject") or "").strip()[:80] or None,
                "source_segment_index": index,
                "source_text": source,
                "grounded_in_text": True,
                "semantic_director_version": SEMANTIC_DIRECTOR_VERSION,
                "provider": provider,
            }
        )
        seen.add(index)
    return normalized


def validate_title_candidates(
    raw: Any,
    segments: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Keep only short, readable title summaries grounded in the input."""

    if not isinstance(raw, Mapping):
        return []
    transcript = "".join(
        _source_text(segment)
        for segment in segments
        if isinstance(segment, Mapping)
    )
    candidates: list[str] = []
    for value in raw.get("title_candidates") or []:
        title = re.sub(r"\s+", "", str(value or "")).strip(" ：:|-")
        if not (6 <= len(title) <= 14):
            continue
        if re.search(r"[，。！？、,:;；!?…#]", title):
            continue
        # A concise summary may rephrase the sentence, but must retain at
        # least one two-character source phrase or a source number.
        grounded = any(
            title[index : index + 2] in transcript
            for index in range(max(0, len(title) - 1))
        ) or any(number in title and number in transcript for number in re.findall(r"\d+(?:\.\d+)?%?", title))
        if grounded and title not in candidates:
            candidates.append(title)
        if len(candidates) >= 3:
            break
    return candidates


def validate_global_direction(raw: Any) -> dict[str, Any]:
    """Keep only the small, displayable global style contract from the model."""

    if not isinstance(raw, Mapping):
        return {}
    enum_fields = {
        "content_type": {"knowledge_talking_head", "product_talking_head", "story_talking_head", "general_talking_head"},
        "tone": {"business_warning", "calm_explanation", "positive_result", "neutral"},
        "visual_density": {"low", "medium", "high"},
        "caption_style": {"bold_clean", "clean", "minimal"},
        "color_family": {"warm", "cool", "neutral"},
        "pacing": {"stable_emphasis_stable", "steady", "fast_then_steady"},
    }
    result: dict[str, Any] = {}
    for key, allowed in enum_fields.items():
        value = str(raw.get(key) or "").strip()
        if value in allowed:
            result[key] = value
    if isinstance(raw.get("bgm_recommended"), bool):
        result["bgm_recommended"] = raw["bgm_recommended"]
    return result


def _asset_ids(available_assets: Sequence[Mapping[str, Any]] | None) -> set[str]:
    return {
        str(asset.get("asset_id") or "").strip()
        for asset in available_assets or []
        if isinstance(asset, Mapping) and str(asset.get("asset_id") or "").strip()
    }


def _proposal_rejection_reason(
    item: Mapping[str, Any],
    by_index: Mapping[int, str],
    available_asset_ids: set[str],
) -> str | None:
    try:
        index = int(item.get("source_segment_index"))
    except (TypeError, ValueError):
        return "source_segment_index_invalid"
    source = by_index.get(index, "")
    if not source:
        return "source_segment_index_missing"
    semantic_text = re.sub(r"\s+", "", str(item.get("semantic_text") or ""))
    keyword = re.sub(r"\s+", "", str(item.get("keyword") or ""))
    if not semantic_text or semantic_text not in source:
        return "semantic_text_not_grounded"
    if keyword and keyword not in source:
        return "keyword_not_grounded"
    grounded_text = f"{semantic_text}{keyword}"
    if _NEGATION_PATTERN.search(source) and not _NEGATION_PATTERN.search(grounded_text):
        return "negation_or_cancellation_not_preserved"
    for number in _NUMBER_TOKEN_PATTERN.findall(source):
        token = re.sub(r"\s+", "", number)
        if token and token not in grounded_text:
            return "amount_or_unit_not_preserved"
    enum_fields = (
        ("event_type", CREATIVE_EVENT_TYPES),
        ("layout", CREATIVE_LAYOUTS),
        ("caption_treatment", CREATIVE_CAPTION_TREATMENTS),
        ("camera_motion", CREATIVE_CAMERA_MOTIONS),
        ("transition", CREATIVE_TRANSITIONS),
        ("sfx", CREATIVE_SFX),
        ("visual_type", CREATIVE_VISUAL_TYPES),
    )
    for field, allowed in enum_fields:
        value = str(item.get(field) or "").strip()
        if value not in allowed:
            return f"unknown_{field}"
    asset_id = str(item.get("asset_id") or "").strip()
    asset_query = str(item.get("asset_query") or "").strip()
    layout = str(item.get("layout") or "")
    visual_type = str(item.get("visual_type") or "")
    if asset_id and asset_id not in available_asset_ids:
        return "asset_id_not_available"
    if layout in {"full_screen_broll", "large_pip_left", "large_pip_right"} and visual_type in {
        "licensed_broll", "user_asset", "generated_illustration"
    } and not asset_id and not asset_query:
        return "visual_asset_request_missing"
    try:
        preferred_duration = float(item.get("preferred_duration_seconds", 0.8))
    except (TypeError, ValueError):
        return "preferred_duration_invalid"
    if not math.isfinite(preferred_duration) or not 0.2 <= preferred_duration <= 8.0:
        return "preferred_duration_out_of_range"
    return None


def validate_creative_proposals(
    raw: Any,
    segments: Sequence[Mapping[str, Any]],
    *,
    available_assets: Sequence[Mapping[str, Any]] | None = None,
    return_rejections: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate model creative proposals without granting renderer authority.

    The default return value is the accepted proposal list.  Callers that need
    audit details may request ``(accepted, rejected)`` with
    ``return_rejections=True``.
    """

    raw_items = raw.get("creative_proposals") if isinstance(raw, Mapping) else raw
    if not isinstance(raw_items, list):
        raw_items = []
    by_index = {
        index: _source_text(segment)
        for index, segment in enumerate(segments)
        if isinstance(segment, Mapping) and _source_text(segment)
    }
    known_asset_ids = _asset_ids(available_assets)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_proposal_ids: set[str] = set()
    for proposal_index, raw_item in enumerate(raw_items[:64]):
        if not isinstance(raw_item, Mapping):
            rejected.append({"proposal_index": proposal_index, "status": "rejected", "reason": "proposal_not_object"})
            continue
        reason = _proposal_rejection_reason(raw_item, by_index, known_asset_ids)
        proposal_id = str(raw_item.get("proposal_id") or f"proposal_{proposal_index + 1:03d}").strip()[:80]
        if proposal_id in seen_proposal_ids:
            rejected.append({"proposal_id": proposal_id, "status": "rejected", "reason": "duplicate_proposal_id"})
            continue
        seen_proposal_ids.add(proposal_id)
        if reason:
            rejected.append({"proposal_id": proposal_id, "status": "rejected", "reason": reason})
            continue
        index = int(raw_item["source_segment_index"])
        source = by_index[index]
        semantic_text = re.sub(r"\s+", "", str(raw_item.get("semantic_text") or ""))
        keyword = re.sub(r"\s+", "", str(raw_item.get("keyword") or "")) or None
        accepted.append(
            {
                "proposal_id": proposal_id,
                "source_segment_index": index,
                "semantic_text": semantic_text,
                "source_text": source,
                "importance": round(max(0.0, min(1.0, float(raw_item.get("importance", 0.0)))), 3),
                "event_type": str(raw_item.get("event_type")),
                "layout": str(raw_item.get("layout")),
                "caption_treatment": str(raw_item.get("caption_treatment")),
                "keyword": keyword,
                "visual_type": str(raw_item.get("visual_type")),
                "camera_motion": str(raw_item.get("camera_motion")),
                "transition": str(raw_item.get("transition")),
                "asset_id": str(raw_item.get("asset_id") or "").strip() or None,
                "asset_query": str(raw_item.get("asset_query") or "").strip()[:160] or None,
                "sfx": str(raw_item.get("sfx")),
                "preferred_duration_seconds": round(float(raw_item.get("preferred_duration_seconds", 0.8)), 3),
                "reason": str(raw_item.get("reason") or "").strip()[:240],
                "status": "accepted",
                "grounded_in_text": True,
                "source": "minimax_creative_director",
            }
        )
    if return_rejections:
        return accepted, rejected
    return accepted


def merge_semantic_annotations(
    segments: Sequence[Mapping[str, Any]],
    primary: Sequence[Mapping[str, Any]],
    fallback: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Keep valid primary annotations and fill missing segments locally.

    The renderer must not treat a non-empty, partial provider response as a
    complete director plan. Both inputs are revalidated against the current
    immutable transcript before they are merged.
    """

    primary_checked = validate_semantic_annotations(primary, segments)
    fallback_checked = validate_semantic_annotations(
        fallback or [], segments, provider="local_rules"
    )
    merged: dict[int, dict[str, Any]] = {
        int(item["source_segment_index"]): item for item in fallback_checked
    }
    merged.update(
        {int(item["source_segment_index"]): item for item in primary_checked}
    )
    return [merged[index] for index in sorted(merged)]


def reconcile_semantic_annotations(
    segments: Sequence[Mapping[str, Any]],
    annotations: Sequence[Mapping[str, Any]],
    *,
    fallback: Callable[[], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Remap cached director output to the current transcript clock/text."""

    local = fallback() if fallback else []
    return merge_semantic_annotations(segments, annotations, local)


def sound_effect_candidates() -> list[dict[str, Any]]:
    """Return the curated local sound library as director-selectable options.

    Only the reviewed candidates that were actually integrated into
    ``assets/sounds`` are exposed.  Sending the whole download pool would bloat
    the request and invite choices no renderer path can honour.  Each option
    carries the reviewer's Chinese function label so the model selects by
    meaning (``确认成功`` / ``错误警示``) rather than by an English file stem.
    """

    global _SOUND_EFFECT_CANDIDATE_CACHE
    if _SOUND_EFFECT_CANDIDATE_CACHE is not None:
        return _SOUND_EFFECT_CANDIDATE_CACHE
    library = Path(__file__).resolve().parents[2] / "assets" / "sounds"
    options: list[dict[str, Any]] = []
    try:
        metadata_files = sorted(library.glob("*.json"))
    except OSError:
        metadata_files = []
    for metadata_path in metadata_files:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(metadata.get("kind") or "") != "sound_effect":
            continue
        if metadata.get("authorization_status") != "confirmed":
            continue
        audio_path = metadata_path.with_suffix(".wav")
        if not audio_path.is_file():
            continue
        options.append(
            {
                "sound_id": metadata.get("asset_id"),
                "stored_name": metadata.get("stored_name"),
                "display_name_zh": metadata.get("display_name_zh"),
                "tags_zh": metadata.get("tags_zh") or [],
                "duration_seconds": metadata.get("duration_seconds"),
                "avoid_when_zh": metadata.get("avoid_when_zh"),
            }
        )
    _SOUND_EFFECT_CANDIDATE_CACHE = options
    return options


_SOUND_EFFECT_CANDIDATE_CACHE: list[dict[str, Any]] | None = None


def semantic_director_prompt(
    segments: Sequence[Mapping[str, Any]],
    duration_seconds: float,
    *,
    keyframes: Sequence[Mapping[str, Any]] | None = None,
    available_assets: Sequence[Mapping[str, Any]] | None = None,
    capabilities: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    payload = [
        {
            "source_segment_index": index,
            "start": float(segment.get("start") or 0.0),
            "end": float(segment.get("end") or 0.0),
            "text": _source_text(segment),
        }
        for index, segment in enumerate(segments)
        if isinstance(segment, Mapping) and _source_text(segment)
    ]
    system = (
        "你是受约束的短视频视觉导演。只返回严格 JSON，不直接控制渲染器。"
        'JSON格式：{"title_candidates":[],"annotations":[],"global_direction":{},"creative_proposals":[]}。'
        "annotations 的 semantic_roles 只能使用：" + ",".join(sorted(SEMANTIC_DIRECTOR_ROLES)) + "。"
        "creative_proposals 每条必须包含 proposal_id、source_segment_index、semantic_text、importance、event_type、layout、caption_treatment、"
        "keyword、visual_type、camera_motion、transition、asset_query、sfx、sfx_asset_id、preferred_duration_seconds、reason。"
        "只能使用以下枚举：event_type=" + ",".join(sorted(CREATIVE_EVENT_TYPES)) + "；layout=" + ",".join(sorted(CREATIVE_LAYOUTS))
        + "；caption_treatment=" + ",".join(sorted(CREATIVE_CAPTION_TREATMENTS))
        + "；camera_motion=" + ",".join(sorted(CREATIVE_CAMERA_MOTIONS))
        + "；transition=" + ",".join(sorted(CREATIVE_TRANSITIONS))
        + "；sfx=" + ",".join(sorted(CREATIVE_SFX))
        + "；visual_type=" + ",".join(sorted(CREATIVE_VISUAL_TYPES)) + "。"
        "每个创意必须绑定 source_segment_index 和原文连续片段；不要改写字幕、翻转否定、删掉数字或单位。"
        "普通解释优先 stable_talking_head，不要为了热闹强行添加事件。具体产品、地点、案例优先提出真实 B-roll；数字、步骤和对比优先程序化信息图。"
        "只能从当前可用组件和素材清单中选择，没有可靠视觉主体时不要编造素材。不要生成事实、价格、品牌、人物或用户未提供的信息。"
        "不要让所有字幕持续跳动，不要固定角落标签，不要遮挡人物面部、字幕安全区和平台交互区。"
        "音效优先从 sound_assets 中按中文用途挑选，用 sfx_asset_id 填入对应的 sound_id；"
        "清单之外或拿不准时留空，程序会按 sfx 类别自动选。音效是强调点，不是每句都响。"
    )
    request = {
        "duration_seconds": duration_seconds,
        "segments": payload,
        "sound_assets": sound_effect_candidates(),
        "keyframes": [
            {
                key: frame.get(key)
                for key in ("frame_id", "timestamp", "reason", "segment_index", "visual_analysis")
                if isinstance(frame, Mapping) and frame.get(key) is not None
            }
            for frame in keyframes or []
        ],
        "available_assets": [dict(asset) for asset in available_assets or [] if isinstance(asset, Mapping)],
        "available_components": dict(capabilities or {}),
        "prompt_version": DIRECTOR_PROMPT_VERSION,
    }
    return system, json.dumps(request, ensure_ascii=False)


def _keyframe_prompt(user_prompt: str, keyframes: Sequence[Mapping[str, Any]] | None) -> str:
    """Tell a multimodal model how the supplied frames map to the timeline."""
    if not keyframes:
        return user_prompt
    timestamps = [
        round(float(frame.get("timestamp") or 0.0), 3)
        for frame in keyframes
        if isinstance(frame, Mapping)
    ]
    return user_prompt + "\n关键帧按顺序对应以下视频时间点（秒），只用于识别场景，不改变语义时间轴：" + json.dumps(
        timestamps, ensure_ascii=False
    )


def sample_video_keyframes(
    video_path: str | Path,
    duration_seconds: float,
    *,
    max_frames: int = MAX_DIRECTOR_FRAMES,
    semantic_segments: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Extract a bounded composite frame set for visual direction.

    Scene detection is best-effort.  A failure only removes scene-cut
    candidates; opening, ending, semantic and uniform candidates still work.
    """
    path = Path(video_path)
    duration = max(0.0, float(duration_seconds or 0.0))
    if not path.is_file() or duration <= 0 or max_frames <= 0:
        return []
    limit = max(1, min(int(max_frames), MAX_DIRECTOR_FRAMES))
    candidates: list[dict[str, Any]] = []

    def add_candidate(timestamp: float, reason: str, priority: int, segment_index: int | None = None) -> None:
        if not 0.0 <= timestamp <= duration:
            return
        candidates.append(
            {
                "timestamp": round(timestamp, 3),
                "reason": reason,
                "priority": priority,
                "segment_index": segment_index,
            }
        )

    for timestamp in (0.3, 1.0, 2.0):
        if timestamp <= duration:
            add_candidate(timestamp, "hook", 100)
    for timestamp in (max(0.0, duration - 2.0), max(0.0, duration - 0.6)):
        add_candidate(timestamp, "ending", 90)

    try:
        scene_result = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-v", "info", "-i", str(path),
                "-vf", "select='gt(scene,0.30)',showinfo", "-an", "-f", "null", os.devnull,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        scene_text = f"{scene_result.stdout}\n{scene_result.stderr}"
        for value in re.findall(r"pts_time:(\d+(?:\.\d+)?)", scene_text):
            add_candidate(float(value), "scene_cut", 80)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass

    for index, segment in enumerate(semantic_segments or []):
        if not isinstance(segment, Mapping):
            continue
        text = _source_text(segment)
        roles = {str(role).upper() for role in segment.get("semantic_roles") or []}
        if not roles and not re.search(r"钩子|数字|金额|警告|对比|步骤|结论|评论|关注", text):
            continue
        try:
            start = float(segment.get("start") or 0.0)
            end = float(segment.get("end") or start)
        except (TypeError, ValueError):
            continue
        add_candidate((start + min(end, start + 1.0)) / 2.0, "semantic_peak", 85, index)

    uniform_count = min(6, max(1, math.ceil(duration / 8.0)))
    for index in range(uniform_count):
        add_candidate(duration * (index + 0.5) / uniform_count, "uniform", 30)
    deduped: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (-int(item["priority"]), float(item["timestamp"]))):
        if any(abs(float(candidate["timestamp"]) - float(existing["timestamp"])) < 0.6 for existing in deduped):
            continue
        deduped.append(candidate)
    if len(deduped) < min(TARGET_DIRECTOR_FRAMES, limit):
        for candidate in sorted(candidates, key=lambda item: float(item["timestamp"])):
            if any(abs(float(candidate["timestamp"]) - float(existing["timestamp"])) < 0.6 for existing in deduped):
                continue
            deduped.append(candidate)
            if len(deduped) >= min(TARGET_DIRECTOR_FRAMES, limit):
                break
    selected = sorted(deduped[:limit], key=lambda item: float(item["timestamp"]))
    frames: list[dict[str, Any]] = []
    frame_digests: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="semantic-director-frames-") as temp_dir:
        for index, candidate in enumerate(selected):
            timestamp = float(candidate["timestamp"])
            output = Path(temp_dir) / f"frame-{index:02d}.jpg"
            try:
                result = subprocess.run(
                    [
                        "ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{timestamp:.3f}",
                        "-i", str(path), "-frames:v", "1", "-vf", f"scale={DIRECTOR_FRAME_WIDTH}:-2", "-q:v", "6", str(output),
                    ],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                # Keyframes improve direction but are never allowed to block
                # the transcript-only fallback or the rest of the pipeline.
                continue
            if result.returncode != 0 or not output.is_file():
                continue
            raw_bytes = output.read_bytes()
            digest = hashlib.sha256(raw_bytes).hexdigest()
            if digest in frame_digests:
                continue
            frame_digests.add(digest)
            encoded = b64encode(raw_bytes).decode("ascii")
            frames.append(
                {
                    "frame_id": f"frame_{index + 1:03d}",
                    "timestamp": round(timestamp, 3),
                    "reason": candidate["reason"],
                    "segment_index": candidate.get("segment_index"),
                    "data_url": f"data:image/jpeg;base64,{encoded}",
                    "visual_analysis": {
                        "analysis_mode": "fixed_safe_zone_fallback",
                        "face_boxes": [],
                        "subject_box": None,
                        "subtitle_safe_zone": {"x": 0.08, "y": 0.64, "w": 0.84, "h": 0.18},
                        "platform_avoid_zones": ["bottom_20_percent", "right_action_column"],
                    },
                }
            )
    return frames


def _semantic_director_timeout_seconds() -> float:
    """Keep one provider call bounded so polling can recover promptly.

    The ceiling has to cover a real multi-modal request: a full talking-head
    clip sends up to ``TARGET_DIRECTOR_FRAMES`` keyframes plus every reviewed
    segment, and MiniMax needs well over 30s to return the complete proposal
    JSON for that payload.  Capping at 30s silently turned direction into
    ``status=fallback`` on longer clips, which is what stripped the visual
    plan (and therefore the asset search) out of the export.
    """

    try:
        configured = float(os.getenv("VIDEO_DIRECTOR_TIMEOUT_SECONDS", "120"))
    except (TypeError, ValueError):
        configured = 120.0
    return max(5.0, min(configured, 180.0))


def _build_minimax_engine() -> Any:
    existing_base = os.getenv("COPYWRITING_BASE_URL", "").strip()
    dedicated_base = os.getenv("VIDEO_DIRECTOR_BASE_URL") or os.getenv("MINIMAX_TEXT_BASE_URL")
    base_url = (dedicated_base or (existing_base if "minimax" in existing_base.lower() else "") or "https://api.minimaxi.com/v1").strip()
    api_key = (
        os.getenv("VIDEO_DIRECTOR_API_KEY")
        or os.getenv("MINIMAX_API_KEY")
        or os.getenv("MINIMAX_TEXT_API_KEY")
        or os.getenv("MINIMAX_TOKEN_PLAN_KEY")
        or ""
    ).strip()
    model = os.getenv("VIDEO_DIRECTOR_MODEL") or os.getenv("MINIMAX_TEXT_MODEL") or "MiniMax-M3"
    if not api_key:
        return DisabledCopywritingEngine(base_url=base_url, model=model)
    return OpenAICompatibleCopywritingEngine(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_seconds=_semantic_director_timeout_seconds(),
    )


def minimax_director_configured() -> bool:
    """Return only whether a MiniMax director credential is present."""
    return bool(
        os.getenv("VIDEO_DIRECTOR_API_KEY")
        or os.getenv("MINIMAX_API_KEY")
        or os.getenv("MINIMAX_TEXT_API_KEY")
        or os.getenv("MINIMAX_TOKEN_PLAN_KEY")
    )


def annotate_with_semantic_director(
    segments: Sequence[Mapping[str, Any]],
    *,
    duration_seconds: float,
    engine: Any | None = None,
    fallback: Callable[[], list[dict[str, Any]]] | None = None,
    keyframes: Sequence[Mapping[str, Any]] | None = None,
    available_assets: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Ask MiniMax for direction, with truthful local fallback on failure."""
    selected = engine or _build_minimax_engine()
    method = getattr(selected, "annotate_semantic_timeline", None)
    error = "当前模型引擎不支持语义导演接口。"
    if callable(method):
        try:
            raw_result = method(
                segments=segments,
                duration_seconds=duration_seconds,
                keyframes=list(keyframes or []),
                available_assets=list(available_assets or []),
                capabilities={
                    "caption_treatments": sorted(CREATIVE_CAPTION_TREATMENTS),
                    "layouts": sorted(CREATIVE_LAYOUTS),
                    "camera_motions": sorted(CREATIVE_CAMERA_MOTIONS),
                    "transitions": sorted(CREATIVE_TRANSITIONS),
                    "sfx": sorted(CREATIVE_SFX),
                    "visual_types": sorted(CREATIVE_VISUAL_TYPES),
                },
            )
            checked = validate_semantic_annotations(
                raw_result,
                segments,
            )
            title_candidates = validate_title_candidates(raw_result, segments)
            creative_proposals, proposal_rejections = validate_creative_proposals(
                raw_result,
                segments,
                available_assets=available_assets,
                return_rejections=True,
            )
            global_direction = validate_global_direction(
                raw_result.get("global_direction")
                if isinstance(raw_result, Mapping)
                else None
            )
            if checked:
                local = fallback() if fallback else []
                merged = merge_semantic_annotations(segments, checked, local)
                segment_count = sum(
                    1
                    for segment in segments
                    if isinstance(segment, Mapping) and _source_text(segment)
                )
                provider_indices = {
                    int(item["source_segment_index"]) for item in checked
                }
                provider_missing = [
                    index
                    for index, segment in enumerate(segments)
                    if isinstance(segment, Mapping)
                    and _source_text(segment)
                    and index not in provider_indices
                ]
                missing = [
                    index
                    for index, segment in enumerate(segments)
                    if isinstance(segment, Mapping)
                    and _source_text(segment)
                    and index not in {
                        int(item["source_segment_index"]) for item in merged
                    }
                ]
                return merged, {
                    "provider": "minimax",
                    "model": getattr(selected, "model", None),
                    "status": (
                        "used_partial"
                        if provider_missing or missing
                        else "used"
                    ),
                    "version": SEMANTIC_DIRECTOR_VERSION,
                    "prompt_version": DIRECTOR_PROMPT_VERSION,
                    "provider_annotation_count": len(checked),
                    "completed_annotation_count": len(merged),
                    "input_segment_count": segment_count,
                    "provider_coverage_ratio": round(
                        len(checked) / segment_count, 3
                    )
                    if segment_count
                    else 1.0,
                    "coverage_ratio": round(
                        len(merged) / segment_count, 3
                    )
                    if segment_count
                    else 1.0,
                    "provider_missing_segment_indices": provider_missing,
                    "missing_segment_indices": missing,
                    "title_candidates": title_candidates,
                    "global_direction": global_direction,
                    "creative_proposals": creative_proposals,
                    "creative_proposals_raw": [
                        dict(item)
                        for item in (raw_result.get("creative_proposals") or [])[:64]
                        if isinstance(item, Mapping)
                    ]
                    if isinstance(raw_result, Mapping)
                    else [],
                    "creative_proposal_rejections": proposal_rejections,
                    "proposal_count": len(creative_proposals),
                    "rejected_count": len(proposal_rejections),
                    "keyframe_count": len(keyframes or []),
                    "keyframe_sources": [
                        {
                            "frame_id": frame.get("frame_id"),
                            "timestamp": frame.get("timestamp"),
                            "reason": frame.get("reason"),
                            "analysis_mode": (
                                frame.get("visual_analysis") or {}
                            ).get("analysis_mode")
                            if isinstance(frame, Mapping)
                            else None,
                        }
                        for frame in keyframes or []
                        if isinstance(frame, Mapping)
                    ],
                }
            error = "MiniMax 没有返回可用的语义标注。"
        except (LLMAdapterError, ValueError, TypeError, json.JSONDecodeError) as exc:
            error = str(exc)
    local = fallback() if fallback else []
    return local, {
        "provider": "local_rules",
        "model": None,
        "status": "fallback",
        "version": SEMANTIC_DIRECTOR_VERSION,
        "prompt_version": DIRECTOR_PROMPT_VERSION,
        "fallback_reason": error[:240],
        "title_candidates": [],
        "global_direction": {},
        "creative_proposals": [],
        "creative_proposals_raw": [],
        "creative_proposal_rejections": [],
        "proposal_count": 0,
        "rejected_count": 0,
        "keyframe_count": len(keyframes or []),
    }
