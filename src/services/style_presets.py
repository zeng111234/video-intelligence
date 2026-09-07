"""Style presets driving the talking-head renderer.

Each preset is a flat mapping consumed by ``visual_style_spec``,
``build_business_talking_head_overlay_preview`` and
``build_business_talking_head_ass``.  Built-ins are kept in this module so
the same preset is reachable from any entry point without a heavy dataclass
hierarchy.

The default preset (``talking-head-pure-adaptive-v1``) is deliberately a
zero-regression alias for the historical ``adaptive_talking_head_v1``
subtitle_style_id: callers that do not opt into a preset see the same
captioning contract the customer had before this module existed.

The optional ``talking-head-brand-emphasis-v1`` preset layers the
top-of-frame brand header and the centred large-emphasis card that the
customer reference video uses; Phase 3 of the plan will wire those into
the renderer.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Mapping


PRESET_PURE_ADAPTIVE = "talking-head-pure-adaptive-v1"
PRESET_BRAND_EMPHASIS = "talking-head-brand-emphasis-v1"
PRESET_LOCAL_GRAMMAR_V2 = "talking-head-local-grammar-v2"
PRESET_GRAMMAR_ONLY = "talking-head-grammar-only-v1"
JY_ROUGH_CUT_SUBTITLE_PRESET = "JY_ROUGH_CUT_SUBTITLE_PRESET"
DEFAULT_PRESET_ID = PRESET_PURE_ADAPTIVE
LEGACY_STYLE_ID_ALIAS = "adaptive_talking_head_v1"

_VALID_PRESET_IDS = frozenset(
    {PRESET_PURE_ADAPTIVE, PRESET_BRAND_EMPHASIS, PRESET_LOCAL_GRAMMAR_V2, PRESET_GRAMMAR_ONLY}
)


def _pure_adaptive() -> dict[str, Any]:
    return {
        "preset_id": PRESET_PURE_ADAPTIVE,
        "display_name": "纯自适应基线",
        "source": "adaptive_baseline",
        "top_brand_header": None,
        "big_emphasis_layer": None,
        "themed_decoration_cards": [],
        "evidence_overlay": None,
        "interaction_endcard": None,
        "rhythm": {
            "playback_rate": 1.15,
            "active_ranges_from_audio_analysis": False,
            "silence_threshold_db": -35.0,
            "min_silence_cut_ms": 350,
        },
        "emphasis_palette": {
            "white_on_dark": "#FCFAF8",
            "dark_on_white": "#0A0A0A",
            "warm_yellow": "#FFD166",
            "warm_orange": "#FF9F68",
            "warm_red": "#FB7185",
            "warm_pink": "#ED3A7C",
        },
    }


def _brand_emphasis() -> dict[str, Any]:
    return {
        "preset_id": PRESET_BRAND_EMPHASIS,
        "display_name": "动态口播精剪",
        "source": "user_target_video_reference",
        "top_brand_header": None,
        "big_emphasis_layer": None,
        "themed_decoration_cards": [],
        "evidence_overlay": None,
        "interaction_endcard": None,
        "caption": {
            "max_lines": 1,
            "max_chars_per_line": 11,
            "font_size": 54,
            "safe_bottom": 218,
            "font_style": "normal",
            "outline_width": 5,
            "shadow": 1,
            "color": "#FFFFFF",
            "emphasis_color": "#FFD54A",
        },
        "semantic_stickers": {
            "enabled": True,
            "source_order": [
                "licensed_vector_library",
                "transcript_grounded_generated_visual",
                "procedural_fallback",
            ],
            "target_per_minute": 4,
            "max_per_minute": 5,
            "require_transcript_grounding": True,
        },
        "sound_effects": {
            "enabled": True,
            "source_order": ["licensed_library", "procedural_fallback"],
            "voice_safe_volume": 0.045,
            "max_per_minute": 5,
        },
        "broll_policy": {
            "library_first": True,
            "allow_generated_images_for_gaps": True,
            "require_semantic_match": True,
            "never_count_stickers_as_broll": True,
        },
        "rhythm": {
            "playback_rate": 1.08,
            "active_ranges_from_audio_analysis": True,
            "silence_threshold_db": -38.0,
            "min_silence_cut_ms": 280,
        },
        "emphasis_palette": {
            "white_on_dark": "#FCFAF8",
            "dark_on_white": "#0A0A0A",
            "warm_yellow": "#FFD166",
            "warm_orange": "#FF9F68",
            "warm_red": "#FB7185",
            "warm_pink": "#ED3A7C",
        },
    }


def _local_grammar_v2() -> dict[str, Any]:
    """A-roll-first grammar for the zero-external-asset phase.

    This is intentionally a separate preset.  It does not inherit the rich
    release contract, so a no-B-roll export cannot accidentally enter the old
    B-roll coverage gate or trigger provider/image discovery.
    """

    return {
        "preset_id": PRESET_LOCAL_GRAMMAR_V2,
        "display_name": "动态口播精剪 V2（本地）",
        "source": "local_editing_grammar_v2",
        "phase": "phase_1_local_only",
        "top_brand_header": None,
        "big_emphasis_layer": None,
        "themed_decoration_cards": [],
        "evidence_overlay": None,
        "interaction_endcard": None,
        "caption": {
            "max_lines": 1,
            "max_chars_per_line": 11,
            "font_size": 58,
            "safe_bottom": 220,
            "font_style": "bold",
            "outline_width": 5,
            "shadow": 2,
            "color": "#FFFFFF",
            "emphasis_color": "#FFD54A",
        },
        "camera_grammar": {
            "allowed": [
                "static",
                "punch_in_soft",
                "punch_in_medium",
                "slow_push",
                "reframe_left",
                "reframe_right",
            ],
            "ordinary_zoom": [1.03, 1.06],
            "strong_zoom": [1.06, 1.10],
            "max_changes_per_minute": 10,
        },
        "semantic_symbols": {
            "allowed": [
                "red_x",
                "green_check",
                "warning",
                "question",
                "arrow",
                "underline",
                "circle",
                "highlight_box",
                "burst_lines",
                "number_badge",
                "comparison_vs",
            ],
            "max_per_minute": 6,
            "max_text_emphasis_per_minute": 4,
        },
        "sound_effects": {
            "enabled": True,
            "source_order": ["generated_local", "approved_local_library"],
            "profiles": [
                "pop_soft",
                "click",
                "tick",
                "whoosh_soft",
                "impact_soft",
                "success_ping",
                "error_tick",
            ],
            "max_per_minute": 5,
            "voice_safe_volume": 0.04,
        },
        "external_visuals": {
            "allow_broll_video": False,
            "allow_ai_video": False,
            "allow_generated_images": False,
            "allow_network_search": False,
            "allow_bgm": False,
        },
        "rhythm": {
            "playback_rate": 1.08,
            "active_ranges_from_audio_analysis": False,
            "silence_threshold_db": -38.0,
            "min_silence_cut_ms": 280,
        },
        "emphasis_palette": {
            "white_on_dark": "#FCFAF8",
            "dark_on_white": "#0A0A0A",
            "warm_yellow": "#FFD166",
            "warm_orange": "#FF9F68",
            "warm_red": "#FB7185",
            "warm_pink": "#ED3A7C",
        },
    }


def _grammar_only() -> dict[str, Any]:
    """Strict A-roll grammar: no provider, stock, image, B-roll or PiP."""

    return {
        "preset_id": PRESET_GRAMMAR_ONLY,
        "display_name": "动态口播语法剪辑（无外部素材）",
        "source": "local_style_engine",
        "phase": "grammar_only_v1",
        "grammar_mode": "JY_CLONE_GRAMMAR_ONLY",
        "subtitle_preset_id": JY_ROUGH_CUT_SUBTITLE_PRESET,
        "top_brand_header": None,
        "big_emphasis_layer": None,
        "themed_decoration_cards": [],
        "evidence_overlay": None,
        "interaction_endcard": None,
        "caption": {
            "max_lines": 1,
            "max_chars_per_line": 11,
            "font_size": 72,
            "safe_bottom": 224,
            "font_style": "bold",
            # 720x1280 grammar polish baseline: a strong white caption with
            # a light keyline, never a heavy sticker-like outline.
            "outline_width": 2.5,
            "shadow": 1,
            "color": "#FFFFFF",
            "emphasis_color": "#FFD54A",
        },
        "title": {
            "visible_seconds": 1.8,
            "max_lines": 2,
            "max_chars_per_line": 10,
            "font_size": 36,
            "line_height": 1.05,
            "safe_top": 70,
            "safe_left": 28,
            "asset_width": 520,
            "asset_height": 128,
            "outline_width": 1,
            "shadow": 1,
        },
        "layout_whitelist": [
            "opening_title",
            "subtitle_camera",
            "subtitle_symbol",
        ],
        "camera_grammar": {
            "allowed": ["neutral", "punch_in_soft", "punch_in_medium", "slow_push", "reset"],
            "max_changes_per_minute": 12,
        },
        "text_emphasis": {"min_chars": 2, "max_chars": 8, "max_per_minute": 4},
        "semantic_symbols": {
            "allowed": ["red_x", "green_check", "question", "warning", "arrow", "underline", "burst_lines"],
            "require_semantic_binding": True,
            "max_per_minute": 5,
        },
        "sound_effects": {
            "enabled": True,
            "source_order": ["approved_local_library", "generated_local"],
            "profiles": ["pop_soft", "tick_soft", "whoosh_soft", "impact_soft", "success_ping", "warning_tick"],
            "max_per_minute": 9,
            "voice_safe_volume": 0.04,
        },
        "external_visuals": {
            "allow_broll_video": False,
            "allow_pip": False,
            "allow_stock_video": False,
            "allow_stock_image": False,
            "allow_network_search": False,
            "allow_generated_images": False,
            "allow_minimax": False,
            "allow_ai_video": False,
            "allow_bgm": False,
        },
        "rhythm": {
            "playback_rate": 1.08,
            "active_ranges_from_audio_analysis": False,
            "silence_threshold_db": -38.0,
            "min_silence_cut_ms": 280,
        },
        "emphasis_palette": {
            "white_on_dark": "#FCFAF8",
            "dark_on_white": "#0A0A0A",
            "warm_yellow": "#FFD166",
            "warm_orange": "#FF9F68",
            "warm_red": "#FB7185",
            "warm_green": "#69DBA8",
            "warm_pink": "#ED3A7C",
        },
    }


_BUILTIN_PRESETS: dict[str, dict[str, Any]] = {
    PRESET_PURE_ADAPTIVE: _pure_adaptive(),
    PRESET_BRAND_EMPHASIS: _brand_emphasis(),
    PRESET_LOCAL_GRAMMAR_V2: _local_grammar_v2(),
    PRESET_GRAMMAR_ONLY: _grammar_only(),
}


def list_preset_ids() -> list[str]:
    """Return the public preset ids in deterministic order."""

    return sorted(_BUILTIN_PRESETS.keys())


def get_style_preset(preset_id: str | None) -> dict[str, Any]:
    """Return a defensive copy of the preset, or the default if unknown.

    Callers may mutate the returned mapping (e.g. override
    ``top_brand_header.text`` per task) without bleeding back into the
    built-in catalogue.
    """

    requested = (preset_id or "").strip()
    if not requested or requested == LEGACY_STYLE_ID_ALIAS:
        requested = DEFAULT_PRESET_ID
    if requested not in _BUILTIN_PRESETS:
        requested = DEFAULT_PRESET_ID
    return copy.deepcopy(_BUILTIN_PRESETS[requested])


def resolve_style_preset_id(
    edit_plan: Mapping[str, Any] | None,
    *,
    fallback: str | None = None,
) -> str:
    """Pick the preset id from an edit plan or env override.

    Resolution order:
    1. ``edit_plan["style_preset_id"]`` (exact, non-empty string).
    2. ``VIDEO_EDITOR_STYLE_PRESET`` env var (must be a valid preset id).
    3. ``fallback`` argument (defaults to ``DEFAULT_PRESET_ID``).
    """

    if isinstance(edit_plan, Mapping):
        candidate = edit_plan.get("style_preset_id")
        if isinstance(candidate, str) and candidate.strip():
            cleaned = candidate.strip()
            if cleaned in _VALID_PRESET_IDS or cleaned == LEGACY_STYLE_ID_ALIAS:
                return cleaned if cleaned != LEGACY_STYLE_ID_ALIAS else DEFAULT_PRESET_ID
    env = os.environ.get("VIDEO_EDITOR_STYLE_PRESET", "").strip()
    if env in _VALID_PRESET_IDS:
        return env
    if isinstance(fallback, str) and fallback in _VALID_PRESET_IDS:
        return fallback
    return DEFAULT_PRESET_ID


def merge_top_brand_header_overrides(
    preset: Mapping[str, Any],
    overrides: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a new preset dict with ``top_brand_header`` overridden.

    Empty-string overrides clear the rendered text but keep the surrounding
    style so a per-task override never silently disables the brand header
    feature flag.
    """

    base_header = preset.get("top_brand_header")
    if not isinstance(base_header, dict):
        return dict(preset)
    if not overrides:
        merged_header = dict(base_header)
    else:
        merged_header = {**base_header, **{k: v for k, v in overrides.items() if v is not None}}
    return {**dict(preset), "top_brand_header": merged_header}
