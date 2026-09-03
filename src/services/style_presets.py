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

import os
from typing import Any, Mapping


PRESET_PURE_ADAPTIVE = "talking-head-pure-adaptive-v1"
PRESET_BRAND_EMPHASIS = "talking-head-brand-emphasis-v1"
DEFAULT_PRESET_ID = PRESET_PURE_ADAPTIVE
LEGACY_STYLE_ID_ALIAS = "adaptive_talking_head_v1"

_VALID_PRESET_IDS = frozenset({PRESET_PURE_ADAPTIVE, PRESET_BRAND_EMPHASIS})


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
        "display_name": "品牌头 + 大号强调",
        "source": "user_target_video_reference",
        "top_brand_header": {
            "text": "",
            "position": "left-top",
            "orientation": "vertical",
            "style": "white-on-dark",
            "font_size_pt": 36,
            "present_throughout": True,
        },
        "big_emphasis_layer": {
            "min_duration_seconds": 1.4,
            "max_duration_seconds": 3.6,
            "max_per_minute": 4,
            "styles": {
                "white_on_dark": {
                    "bg": "transparent",
                    "fg": "#FFFFFF",
                    "font_size_pt": 96,
                    "padding_px": 32,
                    "line_gap_px": 16,
                },
                "dark_on_white": {
                    "bg": "#FFFFFF",
                    "fg": "#0A0A0A",
                    "font_size_pt": 96,
                    "padding_px": 32,
                    "line_gap_px": 16,
                },
            },
            "trigger_semantic_kinds": {
                "knowledge",
                "process",
                "result",
                "warning",
                "logic",
            },
            "fall_back_to_subtitle": True,
        },
        "themed_decoration_cards": [],
        "evidence_overlay": None,
        "interaction_endcard": None,
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


_BUILTIN_PRESETS: dict[str, dict[str, Any]] = {
    PRESET_PURE_ADAPTIVE: _pure_adaptive(),
    PRESET_BRAND_EMPHASIS: _brand_emphasis(),
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
    base = _BUILTIN_PRESETS[requested]
    return {
        key: (dict(value) if isinstance(value, dict) else value)
        for key, value in base.items()
    }


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
