"""Local transcript review: catch confident mistakes the confidence score misses.

``_auto_review_segments`` only re-checks a segment when the model reports low
confidence.  ASR is frequently *most* confident exactly where it is wrong, so
obvious errors sailed straight into the burned subtitles::

    记彼18483-2001     (should be GB 18483-2001, a national standard number)
    油烟净化期选行      (should be 油烟净化器选型)
    不是越归越好        (should be 不是越贵越好)

This module adds two local, offline checks that do not depend on the confidence
score or on any paid service:

1. **Structure check** -- a recognised standard/identifier shape that the ASR
   rendered as Chinese homophones is flagged for human review, even at high
   confidence.  These shapes are language-level rules, not sample answers.
2. **Lexicon check** -- an optional project word list (proper nouns, product
   names, industry terms) is matched fuzzily against the transcript; a likely
   miss is flagged together with the term it probably meant.

Nothing here rewrites the transcript on its own authority.  Flagged segments are
marked ``needs_review`` and carry a machine-readable reason, so the review step
shows a human what to confirm instead of silently shipping the miss.
"""

from __future__ import annotations

import re
from typing import Any

# A standard / model / identifier the ASR spelled with Chinese homophones.
# Example: ``记彼18483-2001`` for ``GB 18483-2001``.
_GARBLED_IDENTIFIER = re.compile(
    r"[\u4e00-\u9fff]{1,3}\s?\d{4,5}\s?[-—－]\s?\d{2,4}"
)

# Latin/digit identifiers that survived, for contrast in reports.
_CLEAN_IDENTIFIER = re.compile(r"[A-Za-z]{1,6}\s?\d{3,5}(?:\s?[-—－]\s?\d{2,4})?")

# Homophone pairs commonly produced when a standard prefix is spoken.  Kept
# deliberately small and general: it maps *shapes*, not one transcript.
_STANDARD_PREFIX_HOMOPHONES = {
    "记彼": "GB",
    "记比": "GB",
    "级彼": "GB",
    "国标": "GB",
}

# A very common word replaced by a homophone is a characteristic ASR failure and
# is invisible to a confidence score.  "不是越归越好" shipped in a real cut where
# the speaker said "不是越贵越好"; the lexicon missed it because the wrong form is
# itself a valid word, so nothing looked unusual.  These pairs are common-word
# substitutions, not domain vocabulary, and the check still only *suggests*.
_HOMOPHONE_SUBSTITUTIONS: tuple[tuple[str, str], ...] = (
    ("越归", "越贵"),
    ("越贵", "越贵"),
    ("归好", "贵好"),
    ("犀利", "吸力"),
    ("期选", "器选"),
    ("净化期", "净化器"),
    ("免费", "免维护"),
    ("质保", "质保"),
)

# Context that makes a homophone substitution worth flagging: the word sits in a
# comparative or evaluative clause ("越…越…", "更…", "不是…").
_EVALUATIVE_CONTEXT = re.compile(r"越.{0,2}越|更[加好贵多快省]|不是.*越|比.*[好贵快省]")

# Homophone pairs that are only plausible next to a unit or a number, where the
# wrong character changes the meaning of a figure.
_UNIT_HOMOPHONES: tuple[tuple[str, str], ...] = (
    ("风亮", "风量"),
    ("风凉", "风量"),
    ("摔减", "衰减"),
    ("衰碱", "衰减"),
    ("造价", "造价"),
    ("人工废", "人工费"),
    ("人工费", "人工费"),
)


def _known_terms(extra: Any = None) -> list[str]:
    """Return project terms to watch for, longest first."""

    terms = {str(term).strip() for term in (extra or []) if str(term).strip()}
    return sorted(terms, key=len, reverse=True)


def review_transcript_text(
    text: str,
    *,
    known_terms: Any = None,
) -> list[dict[str, Any]]:
    """Return review findings for one segment's text.

    Each finding is ``{"kind", "reason", "excerpt", "suggestion"}``.  An empty
    list means the text raised no local doubt; it does not prove correctness.
    """

    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return []
    findings: list[dict[str, Any]] = []

    corrupted = _GARBLED_IDENTIFIER.search(compact)
    if corrupted and not _CLEAN_IDENTIFIER.search(compact):
        excerpt = corrupted.group(0)
        prefix = excerpt[: len(excerpt) - len(excerpt.lstrip("".join(_STANDARD_PREFIX_HOMOPHONES)))] or ""
        suggestion = ""
        for homophone, replacement in _STANDARD_PREFIX_HOMOPHONES.items():
            if excerpt.startswith(homophone):
                suggestion = replacement + excerpt[len(homophone) :]
                break
        findings.append(
            {
                "kind": "garbled_identifier",
                "reason": "疑似标准号/编号被听成中文谐音，需要人工确认",
                "excerpt": excerpt,
                "suggestion": suggestion,
                "prefix": prefix,
            }
        )

    # A common word swapped for a homophone.  The wrong form is a real word, so
    # nothing looks malformed -- only the context gives it away.
    for wrong, right in _HOMOPHONE_SUBSTITUTIONS:
        if wrong == right or wrong not in compact:
            continue
        if not _EVALUATIVE_CONTEXT.search(compact):
            continue
        findings.append(
            {
                "kind": "homophone_substitution",
                "reason": f"「{wrong}」在评价句里疑似为「{right}」的同音误识",
                "excerpt": wrong,
                "suggestion": right,
            }
        )
        break

    for wrong, right in _UNIT_HOMOPHONES:
        if wrong == right or wrong not in compact:
            continue
        findings.append(
            {
                "kind": "homophone_substitution",
                "reason": f"「{wrong}」疑似为「{right}」的同音误识",
                "excerpt": wrong,
                "suggestion": right,
            }
        )
        break

    for term in _known_terms(known_terms):
        if term in compact:
            continue
        # Same length, one or two characters differ: the classic near-miss.
        if len(term) < 3 or len(term) > len(compact):
            continue
        window = len(term)
        for start in range(len(compact) - window + 1):
            candidate = compact[start : start + window]
            if sum(1 for a, b in zip(candidate, term) if a != b) <= 1:
                findings.append(
                    {
                        "kind": "near_miss_term",
                        "reason": "与词表中的专有词仅一字之差，需要人工确认",
                        "excerpt": candidate,
                        "suggestion": term,
                    }
                )
                break
    return findings


def apply_review_findings(
    segments: Any,
    *,
    known_terms: Any = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Mark segments that need human eyes and report why.

    Returns ``(flagged_count, findings)``.  Segments keep their original text --
    a suggestion is offered, never applied silently.
    """

    flagged = 0
    reported: list[dict[str, Any]] = []
    for index, segment in enumerate(segments or []):
        text = str(getattr(segment, "text", "") or "")
        findings = review_transcript_text(text, known_terms=known_terms)
        if not findings:
            continue
        flagged += 1
        reported.append({"segment_index": index, "text": text, "findings": findings})
        if hasattr(segment, "model_copy"):
            segment = segment.model_copy(
                update={"needs_review": True, "quality_status": "needs_review"}
            )
            segments[index] = segment
    return flagged, reported
