"""Transcript-grounded visual intent planning.

This module deliberately knows nothing about a particular industry or sample
video.  It converts validated semantic annotations into provider-neutral visual
requests.  The renderer and asset matcher still decide whether a request can
be fulfilled and must keep the safe A-roll fallback when it cannot.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


_VISUAL_ROLES = frozenset(
    {
        "PRODUCT",
        "LOCATION",
        "PERSON",
        "SCENE",
        "PROCESS",
        "STEP",
        "EXAMPLE",
        "COMPARISON",
        "NUMBER",
        "PRICE",
        "PERCENT",
    }
)


def _clean(value: object, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _annotation_for_segment(
    annotations: Sequence[Mapping[str, Any]] | None,
    segment_index: int | None,
) -> Mapping[str, Any] | None:
    if segment_index is None:
        return None
    for item in annotations or []:
        if not isinstance(item, Mapping):
            continue
        try:
            if int(item.get("source_segment_index")) == segment_index:
                return item
        except (TypeError, ValueError):
            continue
    return None


def build_semantic_visual_request(
    *,
    text: str,
    segment_index: int | None,
    annotations: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    """Build a grounded request for a concrete semantic beat.

    A request is created only when the director supplied a concrete subject or
    a visualizable semantic role.  Abstract opinions and filler remain on the
    speaker track, which prevents irrelevant stock footage from being added.
    """

    annotation = _annotation_for_segment(annotations, segment_index)
    if annotation is None:
        return None
    roles = [
        str(role).strip().upper()
        for role in annotation.get("semantic_roles") or []
        if str(role).strip().upper() in _VISUAL_ROLES
    ]
    subject = _clean(annotation.get("concrete_visual_subject"), 80)
    source_text = _clean(annotation.get("semantic_text") or text)
    if not source_text:
        return None
    if not subject and not any(role in _VISUAL_ROLES for role in roles):
        return None

    role_set = set(roles)
    if role_set & {"NUMBER", "PRICE", "PERCENT"}:
        visual_type = "data"
        preferred_mode = "pip"
        expected_context = "the stated number, price, or percentage"
        fallback = "numeric_emphasis_or_safe_camera"
    elif role_set & {"COMPARISON"}:
        visual_type = "comparison"
        preferred_mode = "full"
        expected_context = "the two source-grounded sides of the comparison"
        fallback = "comparison_card_or_safe_camera"
    elif role_set & {"PROCESS", "STEP"}:
        visual_type = "process"
        preferred_mode = "pip"
        expected_context = "the described operation, interface, or ordered process"
        fallback = "process_diagram_or_image_parallax"
    elif role_set & {"LOCATION", "SCENE"}:
        visual_type = "scene"
        preferred_mode = "full"
        expected_context = "the named place or scene"
        fallback = "image_parallax_or_safe_camera"
    else:
        visual_type = "semantic_subject"
        preferred_mode = "pip"
        expected_context = "the concrete subject mentioned in the narration"
        fallback = "subject_card_or_safe_camera"

    query_parts = [part for part in (subject, source_text) if part]
    if not query_parts:
        return None
    query = " ".join(dict.fromkeys(query_parts))
    return {
        "visual_type": visual_type,
        "semantic_roles": roles,
        "semantic_subject": subject or None,
        "search_queries": [query],
        "expected_subject": subject or source_text[:48],
        "expected_action": source_text[:48],
        "expected_context": expected_context,
        "preferred_mode": preferred_mode,
        "fallback": fallback,
        "grounded_in_transcript": True,
        "semantic_director_version": annotation.get("semantic_director_version"),
    }

