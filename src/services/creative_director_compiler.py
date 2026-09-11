"""Compile bounded MiniMax creative proposals into the local edit contract.

This module is intentionally small and renderer-agnostic.  It does not search
the network, render media, or invent a visual from transcript keywords.  It
only validates proposal data, resolves a truthful local clock, and emits the
existing motion/B-roll event shapes consumed by ``VideoEditorWorkflowService``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.services.semantic_director import validate_creative_proposals

_CAMERA_ACTIONS = {
    "none": None,
    "slow_push_in": "slow_push",
    "punch_in_light": "punch_in_soft",
    "punch_in_medium": "punch_in_medium",
    "slow_reframe": "reframe_right",
}
_CAPTION_STYLES = {
    "fade_in": "keyword_pop",
    "scale_overshoot": "number_slam",
    "slam_keyword": "number_slam",
    "stamp_keyword": "result_stamp",
    "shake_keyword": "warning_shake",
    "underline_keyword": "logic_arrow",
    "two_level_conclusion": "result_stamp",
}
_SFX_PROFILES = {
    "none": None,
    "soft_pop": "pop_soft",
    "soft_tick": "tick_soft",
    "soft_impact": "impact_soft",
    "short_whoosh": "whoosh_soft",
    "warning_hit": "warning_tick",
    "stamp_hit": "success_ping",
}
_EVENT_SEMANTIC_KINDS = {
    "hook_emphasis": "hook",
    "keyword_emphasis": "keyword",
    "number_emphasis": "number",
    "warning_emphasis": "warning",
    "conclusion_emphasis": "conclusion",
    "comparison_visual": "comparison",
    "process_visual": "process",
    "example_visual": "example",
    "product_visual": "product",
    "location_visual": "location",
    "cta_visual": "cta",
    "stable_talking_head": "stable",
}
_STRONG_CAPTION_TREATMENTS = {
    "scale_overshoot",
    "slam_keyword",
    "stamp_keyword",
    "shake_keyword",
    "two_level_conclusion",
}
_VISUAL_LAYOUTS = {
    "full_screen_broll",
    "large_pip_left",
    "large_pip_right",
    "split_comparison",
    "step_stack",
    "number_focus",
    "subject_background_text",
}


def _clock_for_phrase(
    segment: Mapping[str, Any],
    phrase: str,
    preferred_duration: float,
) -> tuple[float, float, str]:
    """Resolve a phrase to word time when present, otherwise sentence time."""

    start = max(0.0, float(segment.get("start") or 0.0))
    end = max(start, float(segment.get("end") or start))
    words = segment.get("words")
    if isinstance(words, Sequence) and not isinstance(words, (str, bytes)):
        safe_words: list[tuple[str, float, float]] = []
        for word in words:
            if not isinstance(word, Mapping):
                continue
            word_text = "".join(str(word.get("text") or word.get("word") or "").split())
            try:
                word_start = max(start, float(word.get("start", word.get("begin_time", 0))))
                word_end = min(end, float(word.get("end", word.get("end_time", 0))))
            except (TypeError, ValueError):
                continue
            if word_text and word_end > word_start:
                safe_words.append((word_text, word_start, word_end))
        target = "".join(str(phrase or "").split())
        for left in range(len(safe_words)):
            joined = ""
            for right in range(left, len(safe_words)):
                joined += safe_words[right][0]
                if joined == target:
                    phrase_start = safe_words[left][1]
                    phrase_end = safe_words[right][2]
                    return (
                        round(phrase_start, 3),
                        round(min(end, max(phrase_end, phrase_start + preferred_duration)), 3),
                        "word_timestamps",
                    )
                if len(joined) >= len(target):
                    break
    return round(start, 3), round(end, 3), "sentence_timestamps"


def _asset_map(available_assets: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    return {
        str(asset.get("asset_id")): asset
        for asset in available_assets or []
        if isinstance(asset, Mapping) and str(asset.get("asset_id") or "").strip()
    }


def _role_for_event(event_type: str) -> str:
    return {
        "number_emphasis": "NUMBER",
        "warning_emphasis": "WARNING",
        "conclusion_emphasis": "CONCLUSION",
        "comparison_visual": "COMPARISON",
        "process_visual": "PROCESS",
        "product_visual": "PRODUCT",
        "location_visual": "LOCATION",
        "cta_visual": "CTA",
        "hook_emphasis": "HOOK",
    }.get(event_type, "KEY_CLAIM")


def compile_creative_proposals(
    proposals: Sequence[Mapping[str, Any]],
    segments: Sequence[Mapping[str, Any]],
    *,
    duration_seconds: float,
    available_assets: Sequence[Mapping[str, Any]] | None = None,
    frame_analysis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile accepted provider proposals and return an auditable result.

    ``motion_events`` and ``asset_events`` are directly consumable by the
    existing local renderer.  ``asset_requests`` remain non-executable until a
    separately authorized asset is supplied.
    """

    checked, rejected = validate_creative_proposals(
        list(proposals),
        segments,
        available_assets=available_assets,
        return_rejections=True,
    )
    segment_map = {
        index: segment
        for index, segment in enumerate(segments)
        if isinstance(segment, Mapping)
    }
    assets = _asset_map(available_assets)
    motion_events: list[dict[str, Any]] = []
    camera_events: list[dict[str, Any]] = []
    asset_events: list[dict[str, Any]] = []
    asset_requests: list[dict[str, Any]] = []
    stable_ranges: list[dict[str, Any]] = []
    compiler_rejections = list(rejected)
    strong_starts: list[float] = []
    major_intervals: list[tuple[float, float]] = []
    sfx_starts: list[float] = []
    compiled_proposals: list[dict[str, Any]] = []
    max_strong = 5 if duration_seconds <= 60 else max(5, int(duration_seconds / 60 * 5))

    for proposal in sorted(
        checked,
        key=lambda item: (-float(item.get("importance") or 0), int(item.get("source_segment_index") or 0)),
    ):
        proposal_id = str(proposal.get("proposal_id") or "proposal").strip()
        try:
            segment_index = int(proposal.get("source_segment_index"))
        except (TypeError, ValueError):
            compiler_rejections.append({"proposal_id": proposal_id, "status": "rejected", "reason": "source_segment_index_invalid"})
            continue
        segment = segment_map.get(segment_index)
        if segment is None:
            compiler_rejections.append({"proposal_id": proposal_id, "status": "rejected", "reason": "source_segment_index_missing"})
            continue
        try:
            preferred = float(proposal.get("preferred_duration_seconds") or 0.8)
        except (TypeError, ValueError):
            preferred = 0.8
        phrase = str(proposal.get("keyword") or proposal.get("semantic_text") or "")
        start, end, clock_source = _clock_for_phrase(segment, phrase, preferred)
        start = max(0.0, min(start, duration_seconds))
        end = max(start, min(end, duration_seconds))
        if end <= start:
            compiler_rejections.append({"proposal_id": proposal_id, "status": "rejected", "reason": "empty_compiled_interval"})
            continue

        event_type = str(proposal.get("event_type") or "")
        layout = str(proposal.get("layout") or "")
        caption_treatment = str(proposal.get("caption_treatment") or "")
        camera_motion = str(proposal.get("camera_motion") or "none")
        visual_type = str(proposal.get("visual_type") or "none")
        has_visual = layout in _VISUAL_LAYOUTS or visual_type in {
            "licensed_broll",
            "user_asset",
            "generated_illustration",
            "programmatic_infographic",
            "vector_accent",
        }
        strong = has_visual or caption_treatment in _STRONG_CAPTION_TREATMENTS or camera_motion != "none"
        # ``strong`` used to be one bundled verdict: a caption animation landing
        # within the 6s guard rejected the whole proposal, so the director's
        # planned subtitle treatment, its visual and its asset request all
        # disappeared together.  Eight of fifteen proposals were lost that way.
        # Resolve conflicts per component instead: auto-selected caption styles
        # yield first, then the camera move, and the visible visual is protected
        # (it is what the search and the asset gate depend on).
        effective_caption = caption_treatment
        effective_camera = camera_motion
        demotions: list[str] = []
        if strong:
            if len(strong_starts) >= max_strong:
                # The per-clip strong-effect budget is a product rule, not a
                # spacing preference: once it is spent, further strong beats are
                # rejected outright so a clip cannot escalate without limit.
                compiler_rejections.append(
                    {
                        "proposal_id": proposal_id,
                        "status": "rejected",
                        "reason": "strong_effect_budget_exceeded",
                    }
                )
                continue
            if any(abs(start - previous) < 6.0 for previous in strong_starts):
                # Spacing is different: this is a real conflict, but it is a
                # conflict between *components*.  Degrade the camera move first,
                # then an auto-selected caption animation, and keep the beat --
                # the visual and its asset request must survive, because the
                # search and the asset gate depend on them.
                if camera_motion != "none":
                    effective_camera = "none"
                    demotions.append("camera_motion_dropped_too_close")
                if (
                    not has_visual
                    and caption_treatment in _STRONG_CAPTION_TREATMENTS
                ):
                    effective_caption = "fade_in"
                    demotions.append("caption_treatment_downgraded_too_close")
            still_strong = (
                has_visual
                or effective_caption in _STRONG_CAPTION_TREATMENTS
                or effective_camera != "none"
            )
            if still_strong:
                strong_starts.append(start)
        if has_visual and any(start < other_end and end > other_start for other_start, other_end in major_intervals):
            compiler_rejections.append({"proposal_id": proposal_id, "status": "rejected", "reason": "major_visual_overlap"})
            continue
        face_boxes = (
            frame_analysis.get("face_boxes")
            if isinstance(frame_analysis, Mapping)
            else None
        )
        if layout in {"large_pip_left", "large_pip_right"} and isinstance(face_boxes, Sequence):
            pip_left = layout == "large_pip_left"
            face_overlap = False
            for face in face_boxes:
                if not isinstance(face, Mapping):
                    continue
                try:
                    face_x = float(face.get("x") or 0)
                    face_w = float(face.get("w") or 0)
                except (TypeError, ValueError):
                    continue
                # The runtime PiP is deliberately kept on one side. Reject a
                # proposal only when local face analysis says that side is
                # occupied; fixed-safe-zone fallback has no face boxes.
                if (pip_left and face_x < 0.52) or (not pip_left and face_x + face_w > 0.48):
                    face_overlap = True
                    break
            if face_overlap:
                compiler_rejections.append({"proposal_id": proposal_id, "status": "rejected", "reason": "face_protection_zone_overlap"})
                continue
        if has_visual:
            major_intervals.append((start, end))

        role = _role_for_event(event_type)
        semantic_kind = _EVENT_SEMANTIC_KINDS.get(event_type, "keyword")
        sfx = str(proposal.get("sfx") or "none")
        sfx_profile = _SFX_PROFILES.get(sfx)
        sfx_suppressed = False
        if sfx_profile is not None and any(abs(start - previous) < 1.0 for previous in sfx_starts):
            sfx_profile = None
            sfx = "none"
            sfx_suppressed = True
        elif sfx_profile is not None:
            sfx_starts.append(start)
        base = {
            "source": "minimax_creative_director",
            "proposal_id": proposal_id,
            "start": round(start, 3),
            "end": round(end, 3),
            "semantic_text": phrase,
            "source_text": proposal.get("source_text"),
            "semantic_role": role,
            "semantic_kind": semantic_kind,
            "importance": proposal.get("importance", 0.0),
            "source_segment_index": segment_index,
            "grounded_in_text": True,
            "clock_source": clock_source,
            "layout": layout,
            "transition": proposal.get("transition"),
            "sfx": sfx,
            "sfx_profile": sfx_profile,
            # The director may name one specific sound from the exposed
            # library.  It is carried through untouched; the renderer resolves
            # it against the asset metadata and falls back to the profile
            # rotation when the id is unknown or unlicensed.
            "sfx_asset_id": (
                str(proposal.get("sfx_asset_id") or "").strip() or None
            ),
            "audio_disabled": sfx_profile is None,
            "sfx_suppressed_by_budget": sfx_suppressed,
            "component_demotions": list(demotions),
        }

        if effective_caption != "static":
            motion_events.append(
                {
                    **base,
                    "event_id": f"evt-caption-{proposal_id}",
                    "type": "keyword_emphasis",
                    "renderer": "procedural_overlay_v2_caption_integrated",
                    "style_id": _CAPTION_STYLES.get(effective_caption, "keyword_pop"),
                    "caption_treatment": effective_caption,
                    "semantic_text": str(proposal.get("keyword") or phrase),
                    "render_policy": "caption_only",
                    "anchor": "smart_caption",
                    "avoid_zones": ["face", "subtitle", "subject", "platform_ui"],
                }
            )

        camera_action = _CAMERA_ACTIONS.get(effective_camera)
        if camera_action:
            camera_events.append(
                {
                    **base,
                    "event_id": f"evt-camera-{proposal_id}",
                    "type": "camera",
                    "camera_action": camera_action,
                    "treatment": "punch_in",
                    "label": camera_action,
                }
            )

        asset_id = str(proposal.get("asset_id") or "").strip()
        if layout in _VISUAL_LAYOUTS and visual_type in {
            "licensed_broll",
            "user_asset",
            "generated_illustration",
        }:
            if asset_id and asset_id in assets:
                asset = assets[asset_id]
                mode = "full" if layout == "full_screen_broll" else "pip"
                asset_events.append(
                    {
                        **base,
                        "event_id": f"evt-asset-{proposal_id}",
                        "asset_id": asset_id,
                        "mode": mode,
                        "visual_type": visual_type,
                        "asset_origin": asset.get("asset_origin") or asset.get("source"),
                        "source_provider": asset.get("source_provider") or asset.get("source"),
                        "authorization_status": asset.get("authorization_status") or "unverified",
                        "publish_licensed": bool(asset.get("publish_allowed") or asset.get("publish_licensed")),
                        "publish_claim_allowed": bool(asset.get("publish_allowed") or asset.get("publish_licensed")),
                        "semantic_binding": asset.get("semantic_binding") or proposal.get("semantic_text"),
                        "semantic_query": proposal.get("asset_query"),
                        "selection_reason": "minimax_proposal_asset_id",
                    }
                )
            elif proposal.get("asset_query"):
                asset_requests.append(
                    {
                        "request_id": f"request-{proposal_id}",
                        "proposal_id": proposal_id,
                        "source_segment_index": segment_index,
                        "query": proposal.get("asset_query"),
                        "visual_type": visual_type,
                        "preferred_mode": "full" if layout == "full_screen_broll" else "pip",
                        "status": "awaiting_authorized_asset",
                        "fallback": "safe_subject_motion",
                    }
                )
            else:
                compiler_rejections.append({"proposal_id": proposal_id, "status": "rejected", "reason": "asset_request_unresolved"})
                continue

        if visual_type in {"programmatic_infographic", "vector_accent"} or layout in {"number_focus", "step_stack", "split_comparison"}:
            motion_events.append(
                {
                    **base,
                    "event_id": f"evt-visual-{proposal_id}",
                    "type": "semantic_motion_badge",
                    "renderer": "procedural_overlay_v2_caption_integrated",
                    "style_id": (
                        "data_highlight_card" if event_type == "number_emphasis" else
                        "compare_split_accent" if event_type == "comparison_visual" else
                        "process_marker" if event_type == "process_visual" else
                        "result_stamp" if event_type in {"conclusion_emphasis", "cta_visual"} else
                        "knowledge_pop_card"
                    ),
                    "visual_verb": "compare" if event_type == "comparison_visual" else "reveal",
                    "fact": phrase,
                    "anchor": "smart_caption",
                    "avoid_zones": ["face", "subtitle", "subject", "platform_ui"],
                }
            )

        if event_type == "stable_talking_head" and not strong:
            stable_ranges.append(
                {
                    "proposal_id": proposal_id,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "source_segment_index": segment_index,
                    "reason": proposal.get("reason") or "普通解释保持人物主镜头",
                }
            )
        compiled_proposals.append({**dict(proposal), "status": "compiled"})

    all_events = [*motion_events, *camera_events, *asset_events]
    all_events.sort(key=lambda item: (float(item.get("start") or 0), str(item.get("event_id") or "")))
    return {
        "version": "creative-director-compiler-v1",
        "source": "minimax_creative_director",
        "proposals": [dict(item) for item in checked],
        "compiled_proposals": compiled_proposals,
        "rejected_proposals": compiler_rejections,
        "motion_events": motion_events,
        "camera_events": camera_events,
        "asset_events": asset_events,
        "asset_requests": asset_requests,
        "stable_ranges": stable_ranges,
        "events": all_events,
        "accepted_count": len(checked),
        "compiled_count": len(compiled_proposals),
        "rejected_count": len(compiler_rejections),
        "proposal_count": len(proposals),
        "fallback": "safe_subject_motion",
    }
