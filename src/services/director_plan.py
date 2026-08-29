"""Auditable director plans for talking-head production.

The director decides *what should happen* on a timeline.  The media renderer
only executes this data and must never infer creative intent from subtitle
fragments.  This module is deterministic by default so an unconfigured model
still produces a truthful, safe plan without a network call.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from src.services.talking_head_templates import build_talking_head_shot_plan


DIRECTOR_PLAN_VERSION = "director-plan-v3.1"
_MAX_GENERATED_ASSETS = 4
_LONG_FORM_THRESHOLD_SECONDS = 60.0
_SHORT_FORM_THRESHOLD_SECONDS = 30.0


def _text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


# === P0-2: 视觉时间窗规划 ===
# 根据字幕时间戳、时长、语义峰值规划真实 B-roll 时间窗，
# 输出 target_real_coverage_seconds / planned_visual_windows /
# coverage_deficit_seconds / unresolved_semantic_windows。
# 通用规则，不依赖具体样片。

_DATA_CHART_PATTERN = re.compile(
    r"\d+(?:\.\d+)?[%％万亿元倍]?|比例|增长|下降|趋势|对比|增长|减少|排名|排行"
)
_CONCEPT_CARD_PATTERN = re.compile(
    r"第[一二三四五六七八九十]|步骤|方法|流程|要点|首先|其次|然后|最后|关键|要点"
)
_CTA_PATTERN = re.compile(
    r"评论|留言|关注|点击|私信|进群|领取|查看|扫码|加我|主页|下方|戳我|扣\d|打\d"
)


def _classify_visual_intent_for_window(text: str) -> str:
    """基于通用规则的视觉窗口分类（不依赖样片答案）。

    返回 "data_chart" / "concept_card" / "cta_card" / "spoken_point"。

    优先级：CTA > data_chart > concept_card。
    CTA 优先是因为结尾转化引导不可被静默吞掉。
    """
    compact = _text(text)
    if not compact:
        return "spoken_point"
    if _CTA_PATTERN.search(compact):
        return "cta_card"
    if _DATA_CHART_PATTERN.search(compact):
        return "data_chart"
    if _CONCEPT_CARD_PATTERN.search(compact):
        return "concept_card"
    return "spoken_point"


def _target_coverage_band(duration_seconds: float) -> tuple[float, float]:
    """返回 (min_ratio, max_ratio) 基于时长。

    长口播 (≥60s): 0.45 - 0.65
    短口播 (<30s): 0.25 - 0.45
    中间: 0.35 - 0.55
    """
    if duration_seconds >= _LONG_FORM_THRESHOLD_SECONDS:
        return (0.45, 0.65)
    if duration_seconds <= _SHORT_FORM_THRESHOLD_SECONDS:
        return (0.25, 0.45)
    return (0.35, 0.55)


def _plan_visual_windows(
    segments: Sequence[Mapping[str, Any]],
    duration_seconds: float,
) -> dict[str, Any]:
    """根据字幕时间戳规划真实 B-roll 时间窗。"""
    min_ratio, max_ratio = _target_coverage_band(duration_seconds)
    is_long = duration_seconds >= _LONG_FORM_THRESHOLD_SECONDS
    is_short = duration_seconds <= _SHORT_FORM_THRESHOLD_SECONDS

    windows: list[dict[str, Any]] = []
    for seg in segments:
        if not isinstance(seg, Mapping):
            continue
        try:
            start = float(seg.get("start", 0))
            end = float(seg.get("end", 0))
        except (TypeError, ValueError):
            continue
        if end <= start or start < 0:
            continue
        span = end - start
        text = _text(seg.get("text") or "")
        intent = _classify_visual_intent_for_window(text)
        # 必须有视觉的窗口（信息卡 / CTA）
        if intent in {"data_chart", "concept_card", "cta_card"}:
            windows.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(span, 3),
                    "kind": intent,
                    "required": True,
                    "min_seconds": min(span, 4.0) if is_long else min(span, 2.5),
                    "preferred_mode": "full" if span >= 3.0 else "pip",
                    "fallback_kind": "deterministic_card",
                }
            )
        elif span >= 4.0 and not is_short:
            # 长 / 中口播：长句自动成为可选真实 B-roll 窗口
            windows.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(span, 3),
                    "kind": "spoken_point",
                    "required": False,
                    "min_seconds": min(span, 6.0) if is_long else min(span, 4.0),
                    "preferred_mode": "pip",
                    "fallback_kind": "a_roll_safe_push",
                }
            )

    target_min_seconds = duration_seconds * min_ratio
    target_max_seconds = duration_seconds * max_ratio
    required_seconds = sum(w["min_seconds"] for w in windows if w["required"])
    optional_seconds = sum(w["min_seconds"] for w in windows if not w["required"])

    # 长口播必须至少 2 PiP + 2 Full；这里规划阶段先在窗口里平衡
    full_windows = [w for w in windows if w.get("preferred_mode") == "full"]
    if is_long:
        # 如果 full 不足 2，强行提升最早两个 spoken_point 窗口为 full
        for w in windows:
            if w["kind"] in {"data_chart", "concept_card", "cta_card"}:
                continue
            if len(full_windows) >= 2:
                break
            if w.get("preferred_mode") == "pip":
                w["preferred_mode"] = "full"
                w["mode_escalation_reason"] = "ensure_min_full_events"
                full_windows.append(w)

    return {
        "is_long_form": is_long,
        "is_short_form": is_short,
        "target_real_coverage_ratio_min": min_ratio,
        "target_real_coverage_ratio_max": max_ratio,
        "target_real_coverage_seconds_min": round(
            max(target_min_seconds, required_seconds), 3
        ),
        "target_real_coverage_seconds_max": round(target_max_seconds, 3),
        "required_window_count": sum(1 for w in windows if w["required"]),
        "optional_window_count": sum(1 for w in windows if not w["required"]),
        "required_window_seconds": round(required_seconds, 3),
        "optional_window_seconds": round(optional_seconds, 3),
        "planned_visual_windows": windows,
    }


def _normalise_segments(
    segments: Sequence[Mapping[str, Any]], duration_seconds: float
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(segments):
        try:
            start = max(0.0, float(raw.get("start", 0)))
            end = min(duration_seconds, float(raw.get("end", 0)))
        except (TypeError, ValueError):
            continue
        text = _text(raw.get("text"))
        if not text or end <= start:
            continue
        item = {
            "segment_index": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
        }
        words = raw.get("words")
        if isinstance(words, Sequence) and not isinstance(words, (str, bytes)):
            safe_words: list[dict[str, Any]] = []
            for word in words:
                if not isinstance(word, Mapping):
                    continue
                try:
                    word_start = max(start, float(word.get("start", word.get("begin_time", 0))))
                    word_end = min(end, float(word.get("end", word.get("end_time", 0))))
                except (TypeError, ValueError):
                    continue
                word_text = _text(word.get("text", word.get("word")))
                if word_text and word_end > word_start:
                    safe_words.append(
                        {
                            "text": word_text,
                            "start": round(word_start, 3),
                            "end": round(word_end, 3),
                        }
                    )
            if safe_words:
                item["words"] = safe_words
        result.append(item)
    result.sort(key=lambda item: (item["start"], item["end"]))
    return result


def _asset_prompt(text: str, *, template_id: str) -> str:
    return (
        "抖音9:16竖屏商业口播配图，画面表达："
        f"{text[:80]}。母题：{template_id}。"
        "写实、清晰、留出字幕安全区，不出现文字、字幕、水印、品牌Logo，"
        "不生成与口播人物相似的真人脸。"
    )


def _pick_asset_scenes(scenes: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    candidates = [
        scene
        for scene in scenes
        if scene.get("role") == "A-roll"
        and float(scene.get("duration_seconds") or 0) >= 1.8
        and scene.get("timeline_start", 0) > 0
    ]
    if not candidates:
        return []
    stride = max(1, len(candidates) // _MAX_GENERATED_ASSETS)
    return candidates[::stride][:_MAX_GENERATED_ASSETS]


def _asset_publish_claim_allowed(asset: Mapping[str, Any]) -> bool:
    """Separate local visual acceptance from a publish-rights claim."""

    if str(asset.get("asset_origin") or "") == "generated_image_asset":
        return False
    if str(asset.get("authorization_status") or "") != "confirmed":
        return False
    return bool(
        asset.get("publish_licensed") is True
        or str(asset.get("source_provider") or "") in {"pexels", "pixabay"}
        or str(asset.get("source") or "") == "user_uploaded_local"
    )


def _visual_intent_for_text(text: str, *, role: str, purpose: str) -> str:
    """Choose a semantic action; this is metadata, not a claim of visual facts."""

    if role == "B-roll":
        return "speaker_pip" if "pip" in purpose else "evidence_broll"
    if purpose == "hook":
        return "speaker"
    compact = re.sub(r"\s+", "", text)
    if re.search(r"\d+(?:\.\d+)?[%％万亿元倍]?|比例|增长|下降|趋势|对比", compact):
        return "data_chart"
    if re.search(r"第[一二三四五六七八九十]|步骤|方法|流程|要点|首先|其次", compact):
        return "concept_card"
    return "speaker"


def build_director_plan(
    segments: Sequence[Mapping[str, Any]],
    *,
    duration_seconds: float,
    title: str = "",
    target_platform: str = "douyin",
    broll_asset: Mapping[str, Any] | None = None,
    broll_assets_by_shot_id: Mapping[str, Mapping[str, Any]] | None = None,
    bgm_asset: Mapping[str, Any] | None = None,
    source_media_identity: Mapping[str, Any] | None = None,
    preserve_source_clock: bool = False,
) -> dict[str, Any]:
    """Build a versioned plan consumed by preview, render and QA."""

    if duration_seconds <= 0:
        raise ValueError("视频时长必须大于 0 秒。")
    if target_platform != "douyin":
        raise ValueError("当前导演计划首版只支持抖音 9:16。")

    normalised = _normalise_segments(segments, duration_seconds)
    shot_plan = build_talking_head_shot_plan(
        normalised,
        duration_seconds=duration_seconds,
        title=title,
        broll_asset=broll_asset,
        broll_assets_by_shot_id=broll_assets_by_shot_id,
        bgm_asset=bgm_asset,
        preserve_source_clock=preserve_source_clock,
    )
    scenes: list[dict[str, Any]] = []
    for index, shot in enumerate(shot_plan.get("shots") or []):
        source_start = float(shot.get("source_start") or 0)
        source_end = float(shot.get("source_end") or 0)
        source_item = next(
            (
                item
                for item in normalised
                if float(item["start"]) < source_end
                and float(item["end"]) > source_start
            ),
            None,
        )
        scene_text = source_item["text"] if source_item else ""
        role = str(shot.get("role") or "A-roll")
        if index == 0 and shot_plan.get("hook_source"):
            purpose = "hook"
            visual_type = "hook_statement"
        elif role == "B-roll":
            purpose = "supporting_visual"
            visual_type = (
                "broll_pip"
                if str(shot.get("overlay_mode") or "") == "pip"
                else "broll_fullscreen"
            )
        else:
            purpose = "spoken_point"
            visual_type = "safe_subject_motion"
        visual_intent = _visual_intent_for_text(
            scene_text,
            role=role,
            purpose=("broll_pip" if visual_type == "broll_pip" else purpose),
        )
        scenes.append(
            {
                "scene_id": f"scene-{index + 1:02d}",
                "purpose": purpose,
                "source_start": shot["source_start"],
                "source_end": shot["source_end"],
                "timeline_start": shot["timeline_start"],
                "timeline_end": shot["timeline_end"],
                "duration_seconds": shot["duration_seconds"],
                "text": scene_text,
                "role": role,
                "visual_type": visual_type,
                "visual_intent": visual_intent,
                "visual_intent_rule": (
                    "transcript_grounded_numeric_or_step_signal"
                    if visual_intent in {"data_chart", "concept_card"}
                    else "semantic_role_and_safe_subject_fallback"
                ),
                "motion": (
                    "underline_and_soft_scale"
                    if purpose == "hook"
                    else "slow_pan_or_scale"
                ),
                "transition": "hard_cut" if index == 0 else "soft_cut",
                "asset_id": shot.get("asset_id"),
            }
        )

    asset_requests: list[dict[str, Any]] = []
    if not broll_asset and not broll_assets_by_shot_id:
        for index, scene in enumerate(_pick_asset_scenes(scenes)):
            asset_requests.append(
                {
                    "request_id": f"generated-broll-{index + 1:02d}",
                    "scene_id": scene["scene_id"],
                    "prompt": _asset_prompt(
                        str(scene.get("text") or ""),
                        template_id=str(shot_plan["template_id"]),
                    ),
                    "negative_prompt": "文字，水印，Logo，畸形手，重复物体，低清晰度",
                    "size": "1024x1792",
                    "source": "openai_compatible_image_relay",
                    "status": "awaiting_budget_and_provider",
                    "fallback": "safe_subject_motion",
                }
            )

    asset_metadata_by_id: dict[str, Mapping[str, Any]] = {}
    for candidate in (
        list((broll_assets_by_shot_id or {}).values())
        + ([broll_asset] if broll_asset else [])
    ):
        if isinstance(candidate, Mapping) and candidate.get("asset_id"):
            asset_metadata_by_id[str(candidate["asset_id"])] = candidate

    visual_events = [
        {
            "event_id": f"visual-{index + 1:02d}",
            "scene_id": scene["scene_id"],
            "start": scene["timeline_start"],
            "end": scene["timeline_end"],
            "type": scene["visual_type"],
            "asset_id": scene.get("asset_id"),
            "mode": scene.get("overlay_mode"),
            "asset_origin": (
                asset_metadata_by_id.get(str(scene.get("asset_id")), {}).get(
                    "asset_origin"
                )
                if scene.get("asset_id")
                else None
            ),
            "authorization_status": (
                asset_metadata_by_id.get(str(scene.get("asset_id")), {}).get(
                    "authorization_status", "unverified"
                )
                if scene.get("asset_id")
                else "not_applicable"
            ),
            "publish_claim_allowed": (
                _asset_publish_claim_allowed(
                    asset_metadata_by_id.get(str(scene.get("asset_id")), {})
                )
                if scene.get("asset_id")
                else False
            ),
            "grounded_in_text": bool(scene.get("text")),
            "visual_intent": scene.get("visual_intent"),
        }
        for index, scene in enumerate(scenes)
        if scene.get("visual_type") != "safe_subject_motion"
    ]
    local_visual_count = sum(
        1
        for event in visual_events
        if event.get("asset_id") and event.get("type") in {"broll_fullscreen", "broll_pip"}
    )
    publishable_visual_count = sum(
        1
        for event in visual_events
        if event.get("asset_id")
        and event.get("type") in {"broll_fullscreen", "broll_pip"}
        and event.get("publish_claim_allowed") is True
    )
    generated_image_count = sum(
        1
        for event in visual_events
        if event.get("asset_origin") == "generated_image_asset"
    )

    identity = None
    if isinstance(source_media_identity, Mapping):
        identity = {
            "source_media_sha256": str(
                source_media_identity.get("source_media_sha256") or ""
            ).lower(),
            "source_duration_seconds": round(
                float(
                    source_media_identity.get("source_duration_seconds")
                    or duration_seconds
                ),
                3,
            ),
            "transcript_sha256": str(
                source_media_identity.get("transcript_sha256") or ""
            ).lower(),
            "transcript_timing_source": str(
                source_media_identity.get("transcript_timing_source")
                or "sentence_timestamps"
            ),
        }

    return {
        "plan_version": DIRECTOR_PLAN_VERSION,
        "target_platform": target_platform,
        "template_id": shot_plan["template_id"],
        "template_version": shot_plan["template_version"],
        "selection": shot_plan["selection"],
        "hook": {
            "source": shot_plan.get("hook_source"),
            "audio_strategy": "extract_original_phrase",
            "visual_treatment": "hook_statement",
            "must_appear_once": True,
        },
        "scenes": scenes,
        "visual_events": visual_events,
        "asset_requests": asset_requests,
        # === P0-2: 视觉时间窗规划 ===
        "visual_window_plan": _plan_visual_windows(
            normalised, duration_seconds=duration_seconds
        ),
        # P0-2 报告字段：当前真实 B-roll 覆盖（由 workflow 写入）
        "current_real_coverage_seconds": 0.0,
        "coverage_deficit_seconds": 0.0,
        "unresolved_semantic_windows": [],
        "subtitle_policy": {
            "source_of_truth": "word_timestamps_then_sentence_timestamps",
            "max_lines": 2,
            "min_visible_seconds": 0.8,
            "max_chars_per_cue": 14,
            "no_duplicate_text_on_visual_cut": True,
            "safe_area": "lower_third_without_face",
        },
        "audio_policy": {
            "voice_priority": True,
            "bgm": shot_plan.get("bgm"),
            "no_unverified_auto_bgm": True,
        },
        "degradation": {
            "mode": (
                "generated_image_pending"
                if asset_requests
                else shot_plan["degradation"]["mode"]
            ),
            "publish_claim_allowed": bool(
                publishable_visual_count > 0
                and all(
                    event.get("publish_claim_allowed") is True
                    for event in visual_events
                    if event.get("asset_id")
                )
            ),
            "local_visual_event_count": local_visual_count,
            "generated_image_event_count": generated_image_count,
            "publishable_visual_event_count": publishable_visual_count,
            "message": (
                "已生成导演分镜和图片需求，等待预算/生图配置。"
                if asset_requests
                else (
                    "已绑定真实视觉素材，等待成片级门禁。"
                    if publishable_visual_count > 0
                    else shot_plan["degradation"]["message"]
                )
            ),
        },
        "quality_targets": {
            "minimum_creative_score": 90,
            "minimum_real_visual_events": 0,
            "minimum_local_visual_events": 0,
            "generated_images_count_as_local_visual_events": True,
            "generated_images_do_not_satisfy_publish_rights_gate": True,
            "minimum_real_visual_coverage_ratio": 0.20,
            "maximum_real_visual_coverage_ratio": 0.35,
            "subtitle_mapping_error_frames": 1,
            "max_audio_video_drift_ms": 67,
            "required_hard_gates": [
                "no_duplicate_subtitle_cues",
                "no_repeated_audio_ranges",
                "no_face_crop_without_safe_region",
                "audio_stream_present",
                "assets_do_not_cover_subtitles",
                "semantic_visual_change_or_safe_subject_motion",
                "real_broll_is_semantically_grounded_when_present",
                "no_fixed_asset_loop",
            ],
        },
        "timeline_duration_seconds": shot_plan["timeline_duration_seconds"],
        "source_duration_seconds": shot_plan["source_duration_seconds"],
        # A director plan may be compiled to FFmpeg or IMS, but it must never
        # lose the identity of the media/transcript it was authored from.
        # Keep this optional for old stored plans; new production plans pass it
        # explicitly and the renderer enforces it as a hard gate.
        "source_media_identity": identity,
    }


def validate_director_plan(plan: Mapping[str, Any]) -> list[str]:
    """Return human-readable violations; an empty list means structurally valid."""

    errors: list[str] = []
    if plan.get("plan_version") != DIRECTOR_PLAN_VERSION:
        errors.append("导演计划版本不受支持。")
    scenes = [scene for scene in plan.get("scenes") or [] if isinstance(scene, Mapping)]
    previous_end = 0.0
    for scene in scenes:
        try:
            start = float(scene.get("timeline_start", 0))
            end = float(scene.get("timeline_end", 0))
        except (TypeError, ValueError):
            errors.append("场景时间轴格式无效。")
            continue
        if end <= start:
            errors.append("场景结束时间必须晚于开始时间。")
        if start < previous_end - 0.02:
            errors.append("场景输出时间轴发生重叠。")
        previous_end = max(previous_end, end)
    if scenes and abs(previous_end - float(plan.get("timeline_duration_seconds") or 0)) > 0.05:
        errors.append("场景时间轴没有覆盖计划输出时长。")
    hook = plan.get("hook") or {}
    if hook.get("source") and hook.get("must_appear_once") is not True:
        errors.append("原话钩子必须声明只出现一次。")
    return errors
