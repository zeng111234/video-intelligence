"""One-shot low-resolution visual review for the local director timeline."""

from __future__ import annotations

import json
import subprocess
import tempfile
from base64 import b64encode
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ALLOWED_REVIEW_ACTIONS = frozenset(
    {
        "remove_event",
        "reduce_strength",
        "change_layout",
        "change_asset",
        "move_anchor",
        "shorten_duration",
        "change_caption_treatment",
        "remove_sfx",
    }
)
_LAYOUTS = frozenset(
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
_CAPTION_TREATMENTS = frozenset(
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
_CAPTION_STYLE_IDS = {
    "fade_in": "keyword_pop",
    "scale_overshoot": "number_slam",
    "slam_keyword": "number_slam",
    "stamp_keyword": "result_stamp",
    "shake_keyword": "warning_shake",
    "underline_keyword": "logic_arrow",
    "two_level_conclusion": "result_stamp",
}


def sample_preview_frames(
    video_path: str | Path,
    duration_seconds: float,
    *,
    count: int = 10,
) -> list[dict[str, Any]]:
    """Extract at most twelve 360px-wide preview frames without uploading."""

    path = Path(video_path)
    duration = max(0.0, float(duration_seconds or 0.0))
    count = max(1, min(int(count), 12))
    if not path.is_file() or duration <= 0:
        return []
    times = [duration * (index + 0.5) / count for index in range(count)]
    frames: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="director-preview-") as temp_dir:
        for index, timestamp in enumerate(times):
            output = Path(temp_dir) / f"preview-{index:02d}.jpg"
            try:
                result = subprocess.run(
                    [
                        "ffmpeg", "-nostdin", "-y", "-v", "error",
                        "-ss", f"{timestamp:.3f}", "-i", str(path),
                        "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "7", str(output),
                    ],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode != 0 or not output.is_file():
                continue
            frames.append(
                {
                    "frame_id": f"preview_{index + 1:03d}",
                    "timestamp": round(timestamp, 3),
                    "data_url": "data:image/jpeg;base64,"
                    + b64encode(output.read_bytes()).decode("ascii"),
                }
            )
    return frames


def review_director_preview_once(
    engine: Any,
    *,
    preview_frames: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    subtitle_text: Sequence[Mapping[str, Any]],
    already_called: bool = False,
) -> dict[str, Any]:
    """Call a provider review at most once and return a truthful audit result."""

    if already_called:
        return {"status": "skipped", "reason": "review_already_called", "verdict": "pass"}
    method = getattr(engine, "review_director_preview", None)
    if not callable(method):
        return {"status": "skipped", "reason": "engine_review_not_supported", "verdict": "pass"}
    try:
        raw = method(
            preview_frames=list(preview_frames),
            events=[dict(event) for event in events if isinstance(event, Mapping)],
            subtitle_text=[dict(item) for item in subtitle_text if isinstance(item, Mapping)],
        )
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        return {
            "status": "failed",
            "reason": str(exc)[:240],
            "verdict": "pass",
            "revisions": [],
        }
    if not isinstance(raw, Mapping):
        return {"status": "failed", "reason": "review_result_not_object", "verdict": "pass", "revisions": []}
    revisions: list[dict[str, Any]] = []
    review_rejections: list[dict[str, Any]] = []
    for item in raw.get("revisions") or []:
        if not isinstance(item, Mapping):
            review_rejections.append(
                {"event_id": "", "action": "", "reason": "revision_not_object"}
            )
            continue
        revision = dict(item)
        action = str(item.get("action") or "")
        if action not in ALLOWED_REVIEW_ACTIONS:
            review_rejections.append(
                {
                    "event_id": str(item.get("event_id") or ""),
                    "action": action,
                    "reason": "action_not_allowed",
                }
            )
            continue
        revisions.append(revision)
        if len(revisions) >= 12:
            break
    return {
        "status": "completed",
        "verdict": "revise" if str(raw.get("verdict") or "pass") == "revise" else "pass",
        "issues": [dict(item) for item in raw.get("issues") or [] if isinstance(item, Mapping)][:12],
        "revisions": revisions,
        "review_rejections": review_rejections,
        "call_count": 1,
    }


def apply_review_revisions(
    timeline: Mapping[str, Any],
    review: Mapping[str, Any],
    *,
    available_asset_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Apply only safe presentation changes; subtitle facts are immutable."""

    updated = json.loads(json.dumps(timeline, ensure_ascii=False))
    allowed_assets = {str(value) for value in available_asset_ids or []}
    rejected: list[dict[str, Any]] = [
        dict(item)
        for item in review.get("review_rejections") or []
        if isinstance(item, Mapping)
    ]
    event_collections = [
        updated.get("motion_events") or [],
        updated.get("camera_events") or [],
        updated.get("asset_events") or [],
    ]

    def find_event(event_id: str) -> dict[str, Any] | None:
        for collection in event_collections:
            for event in collection:
                if isinstance(event, dict) and str(event.get("event_id") or "") == event_id:
                    return event
        return None

    for revision in review.get("revisions") or []:
        if not isinstance(revision, Mapping):
            continue
        event_id = str(revision.get("event_id") or "")
        action = str(revision.get("action") or "")
        event = find_event(event_id)
        if action not in ALLOWED_REVIEW_ACTIONS or event is None:
            rejected.append({"event_id": event_id, "action": action, "reason": "unknown_event_or_action"})
            continue
        if any(key in revision for key in ("semantic_text", "source_text", "keyword", "subtitle_text")):
            rejected.append({"event_id": event_id, "action": action, "reason": "subtitle_fact_is_immutable"})
            continue
        if action == "remove_event":
            for collection in event_collections:
                collection[:] = [item for item in collection if item is not event]
        elif action == "reduce_strength":
            event["visual_intensity"] = 1
            event["strength_reduced_by_review"] = True
        elif action == "change_layout":
            value = str(revision.get("value") or "")
            if value not in _LAYOUTS:
                rejected.append({"event_id": event_id, "action": action, "reason": "layout_not_allowed"})
            else:
                event["layout"] = value
                event["mode"] = "full" if value == "full_screen_broll" else "pip" if value.startswith("large_pip") else event.get("mode")
        elif action == "change_asset":
            value = str(revision.get("value") or "")
            if value not in allowed_assets:
                rejected.append({"event_id": event_id, "action": action, "reason": "asset_not_available"})
            else:
                event["asset_id"] = value
        elif action == "move_anchor":
            value = str(revision.get("value") or "")
            if value not in {"smart_caption", "left", "right", "center"}:
                rejected.append({"event_id": event_id, "action": action, "reason": "anchor_not_allowed"})
            else:
                event["anchor"] = value
        elif action == "shorten_duration":
            try:
                new_end = float(revision.get("value"))
                start = float(event.get("start") or 0)
            except (TypeError, ValueError):
                rejected.append({"event_id": event_id, "action": action, "reason": "duration_invalid"})
            else:
                if start < new_end <= float(event.get("end") or 0):
                    event["end"] = round(new_end, 3)
                else:
                    rejected.append({"event_id": event_id, "action": action, "reason": "duration_out_of_range"})
        elif action == "change_caption_treatment":
            value = str(revision.get("value") or "")
            if value not in _CAPTION_TREATMENTS:
                rejected.append({"event_id": event_id, "action": action, "reason": "caption_treatment_not_allowed"})
            else:
                event["caption_treatment"] = value
                if value == "static":
                    event["caption_emphasis_disabled"] = True
                else:
                    event["style_id"] = _CAPTION_STYLE_IDS.get(value, "keyword_pop")
        elif action == "remove_sfx":
            event["sfx"] = "none"
            event["sfx_profile"] = None
            event["audio_disabled"] = True
    updated["review_rejections"] = rejected
    updated["review_applied_once"] = True
    return updated
