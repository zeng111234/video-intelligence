"""Provider-assisted semantic direction for the video editor.

The model is restricted to describing spoken content. It cannot choose
renderer actions, asset types, transitions, or effects. Local grammar and
asset gates remain the only code that creates an executable edit timeline.
"""

from __future__ import annotations

import json
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
SEMANTIC_DIRECTOR_VERSION = "semantic-director-v1"


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


def semantic_director_prompt(segments: Sequence[Mapping[str, Any]], duration_seconds: float) -> tuple[str, str]:
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
        "你是短视频语义导演，只负责理解口播内容。只返回严格 JSON，格式为 "
        '{"title_candidates":["6到14字的短标题候选"],"annotations":[{"source_segment_index":0,"semantic_text":"原文连续片段",'
        '"semantic_roles":["KEY_CLAIM"],"importance":0.8,"emotion":"neutral",'
        '"concrete_visual_subject":null}]}。'
        "semantic_roles 只能使用：" + ",".join(sorted(SEMANTIC_DIRECTOR_ROLES)) + "。"
        "semantic_text 必须逐字来自对应原文；不要改写、补充或杜撰事实。title_candidates 只能总结输入内容中的事实，不能使用省略号、标点或半截词。"
        "只描述语义，不要输出 punch_in、red_x、pip、broll、zoom、image、"
        "camera、sfx、transition、layout、style 或任何渲染动作。"
        "没有具体物体、地点、人物、步骤或过程时，concrete_visual_subject 必须为 null。"
    )
    return system, json.dumps({"duration_seconds": duration_seconds, "segments": payload}, ensure_ascii=False)


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
    max_frames: int = 4,
) -> list[dict[str, str | float]]:
    """Extract small local JPEGs for semantic review; never uploads them itself."""
    path = Path(video_path)
    duration = max(0.0, float(duration_seconds or 0.0))
    if not path.is_file() or duration <= 0 or max_frames <= 0:
        return []
    count = max(1, min(int(max_frames), 4))
    times = [duration * (index + 0.5) / count for index in range(count)]
    frames: list[dict[str, str | float]] = []
    with tempfile.TemporaryDirectory(prefix="semantic-director-frames-") as temp_dir:
        for index, timestamp in enumerate(times):
            output = Path(temp_dir) / f"frame-{index:02d}.jpg"
            try:
                result = subprocess.run(
                    [
                        "ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{timestamp:.3f}",
                        "-i", str(path), "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "6", str(output),
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
            encoded = b64encode(output.read_bytes()).decode("ascii")
            frames.append({"timestamp": round(timestamp, 3), "data_url": f"data:image/jpeg;base64,{encoded}"})
    return frames


def _semantic_director_timeout_seconds() -> float:
    """Keep one provider call bounded so polling can recover promptly."""

    try:
        configured = float(os.getenv("VIDEO_DIRECTOR_TIMEOUT_SECONDS", "30"))
    except (TypeError, ValueError):
        configured = 30.0
    return max(5.0, min(configured, 30.0))


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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Ask MiniMax for annotations, with truthful local fallback on failure."""
    selected = engine or _build_minimax_engine()
    method = getattr(selected, "annotate_semantic_timeline", None)
    error = "当前模型引擎不支持语义导演接口。"
    if callable(method):
        try:
            raw_result = method(
                segments=segments,
                duration_seconds=duration_seconds,
                keyframes=list(keyframes or []),
            )
            checked = validate_semantic_annotations(
                raw_result,
                segments,
            )
            title_candidates = validate_title_candidates(raw_result, segments)
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
                }
            error = "MiniMax 没有返回可用的语义标注。"
        except (LLMAdapterError, ValueError, TypeError, json.JSONDecodeError) as exc:
            error = str(exc)
    local = fallback() if fallback else []
    return local, {"provider": "local_rules", "model": None, "status": "fallback", "version": SEMANTIC_DIRECTOR_VERSION, "fallback_reason": error[:240], "title_candidates": []}
