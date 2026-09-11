"""Re-evaluate stored transcripts and gate the export on unresolved suspects.

Two problems this closes:

1. **Stale reviews.** Review results are cached on the transcription task, so a
   transcript produced before a rule existed keeps its old verdict forever.  A
   shipped cut still read ``记彼18483-2001`` and ``油烟净化期选行`` while the task
   claimed ``auto_reviewed=True, uncertain_segment_count=0``.
2. **The confidence score is not per segment.** Every segment in a real task
   carried the identical value ``0.8528750244024405`` -- a single task-level
   number.  The review branch was ``if confidence >= 0.75: accept``, so it could
   never flag anything on this provider.  Detection therefore cannot depend on
   confidence at all.

The functions here re-run the local, offline checks against whatever transcript
is about to be burned, and report what a human should confirm.  They never
rewrite text: a suggestion is offered, the decision stays with the reviewer.
"""

from __future__ import annotations

import os
from typing import Any

from src.services.asr_lexicon import review_transcript_text


def _segment_text(segment: Any) -> str:
    if isinstance(segment, dict):
        return str(
            segment.get("text") or segment.get("semantic_text") or ""
        ).strip()
    return str(getattr(segment, "text", "") or "").strip()


def _segment_start(segment: Any) -> float:
    raw = (
        segment.get("start")
        if isinstance(segment, dict)
        else getattr(segment, "start", None)
    )
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def reevaluate_transcript(
    segments: Any,
    *,
    known_terms: Any = None,
) -> dict[str, Any]:
    """Re-run the local checks and return a review verdict for the timeline.

    ``suspect_segments`` lists every segment a reviewer should confirm, with
    the reason and a suggested reading.  ``checked`` and ``suspect`` let a gate
    decide what to do without re-deriving anything.
    """

    findings: list[dict[str, Any]] = []
    checked = 0
    for index, segment in enumerate(segments or []):
        text = _segment_text(segment)
        if not text:
            continue
        checked += 1
        matches = review_transcript_text(text, known_terms=known_terms)
        if not matches:
            continue
        findings.append(
            {
                "segment_index": index,
                "start": round(_segment_start(segment), 3),
                "text": text,
                "suggestions": matches,
                "reason": matches[0]["reason"],
            }
        )
    return {
        "checked_segment_count": checked,
        "suspect_count": len(findings),
        "suspect_segments": findings,
    }


def apply_confirmed_corrections(
    segments: Any,
    verdict: Any,
    *,
    enabled: bool | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Apply only the *structural* suggestions, and only when opted in.

    Two kinds of finding exist:

    * ``garbled_identifier`` -- a standard/model number the ASR spelled with
      Chinese homophones (``记彼18483-2001`` for ``GB 18483-2001``).  The shape
      is mechanical and the replacement follows from the shape itself, so it is
      safe to apply automatically once the operator opts in.
    * ``near_miss_term`` -- a likely proper-noun miss against the project word
      list.  This still needs a human, because the word list is a hint rather
      than proof.

    Text is never mutated in place; a new list is returned so the reviewer's
    original wording stays available.  Returns ``(segments, applied)``.
    """

    if enabled is None:
        enabled = os.getenv(
            "VIDEO_EDITOR_APPLY_STRUCTURAL_CORRECTIONS", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return segments, []
    applied: list[dict[str, Any]] = []
    updated = list(segments or [])
    for finding in (verdict or {}).get("suspect_segments") or []:
        structural = [
            item
            for item in finding.get("suggestions") or []
            if item.get("kind") == "garbled_identifier" and item.get("suggestion")
        ]
        if not structural:
            continue
        index = int(finding.get("segment_index") or 0)
        if index >= len(updated):
            continue
        excerpt = str(structural[0].get("excerpt") or "")
        replacement = str(structural[0]["suggestion"])
        original = str(finding.get("text") or "")
        if not excerpt or excerpt not in original:
            continue
        new_text = original.replace(excerpt, replacement, 1)
        if new_text == original:
            continue
        segment = updated[index]
        updated[index] = (
            {**segment, "text": new_text} if isinstance(segment, dict) else new_text
        )
        applied.append(
            {
                "segment_index": index,
                "from": original,
                "to": new_text,
                "kind": structural[0]["kind"],
            }
        )
    return updated, applied


def transcript_review_gate(
    verdict: Any,
    *,
    enforcing: bool | None = None,
) -> dict[str, Any]:
    """Decide whether unresolved suspects may still be burned into an export.

    Enforcement is opt-in through ``VIDEO_EDITOR_REQUIRE_TRANSCRIPT_REVIEW`` so
    an existing local pipeline keeps working, but the verdict is always
    reported: a cut that carries unconfirmed errors must not look clean.
    """

    if enforcing is None:
        enforcing = os.getenv(
            "VIDEO_EDITOR_REQUIRE_TRANSCRIPT_REVIEW", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
    suspect_count = int((verdict or {}).get("suspect_count") or 0)
    suspects = list((verdict or {}).get("suspect_segments") or [])
    return {
        "enforcing": bool(enforcing),
        "suspect_count": suspect_count,
        "checked_segment_count": int(
            (verdict or {}).get("checked_segment_count") or 0
        ),
        "passed": suspect_count == 0,
        "blocked": bool(enforcing and suspect_count > 0),
        "reason": (
            "字幕存在未确认的可疑识别，需人工核对后再导出"
            if suspect_count
            else "未发现可疑识别"
        ),
        "suspect_segments": suspects[:40],
    }
