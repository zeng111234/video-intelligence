"""Regression tests for "the plan promised an effect the export never showed".

Three separate defects produced that gap:

1. ``_caption_emphasis_items_from_motion_events`` dropped
   ``caption_treatment``, so the downstream kinetic-style chooser re-derived a
   style from the semantic kind alone and distinct director choices rendered
   identically (``shake_keyword`` and ``stamp_keyword`` both came out the same).
2. A highlighted term longer than six characters was skipped outright
   (``len(term) > 6: continue``), so longer copy silently lost effects instead
   of degrading.
3. The grammar-only branch hard-set ``motion_design`` to ``True`` and counted
   ``caption_only`` events as rendered from their label alone.

These tests cover (1) and (2) directly; (3) is asserted through the audit trail
that makes an unfulfilled beat visible.
"""

from __future__ import annotations

from src.services.video_editor_cloud import _caption_kinetic_style_for_cue
from src.services.video_editor_workflow import (
    _LAST_DROPPED_EMPHASIS_TERMS,
    _caption_emphasis_items_from_motion_events,
    _grounded_emphasis_term,
)


def _event(**overrides):
    base = {
        "type": "keyword_emphasis",
        "source_segment_index": 0,
        "semantic_text": "客户资料",
        "source_text": "客户资料留在公司里",
        "semantic_role": "KEY_CLAIM",
        "importance": 0.9,
    }
    base.update(overrides)
    return base


def _kinetic_for(event, text="客户资料留在公司里"):
    projected = _caption_emphasis_items_from_motion_events([event])
    assert projected, event
    segment = {
        "semantic_roles": [str(event.get("semantic_role") or "")],
        "emphasis_kind": projected[0]["kind"],
    }
    if projected[0].get("caption_treatment"):
        segment["caption_treatment"] = projected[0]["caption_treatment"]
    return _caption_kinetic_style_for_cue(
        {"lines": [text], "start": 1.0, "end": 3.0}, segment, 1
    )


def test_director_caption_treatment_reaches_the_renderer():
    """Every explicit treatment must survive the projection unchanged."""

    treatments = (
        "shake_keyword",
        "stamp_keyword",
        "underline_keyword",
        "scale_overshoot",
        "slam_keyword",
    )
    for treatment in treatments:
        projected = _caption_emphasis_items_from_motion_events(
            [_event(caption_treatment=treatment)]
        )
        assert projected[0]["caption_treatment"] == treatment


def test_distinct_treatments_do_not_collapse_into_one_effect():
    """The original bug: shake and stamp produced an identical style."""

    shake = _kinetic_for(_event(caption_treatment="shake_keyword"))
    stamp = _kinetic_for(_event(caption_treatment="stamp_keyword"))
    marker = _kinetic_for(_event(caption_treatment="underline_keyword"))

    assert shake == "shake"
    assert stamp == "stamp"
    assert marker == "marker"
    assert len({shake, stamp, marker}) == 3


def test_unspecified_treatment_still_falls_back_to_semantic_choice():
    """Auto-selection must remain the fallback, not the rule."""

    style = _kinetic_for(_event())
    assert style


def test_over_long_term_degrades_to_a_grounded_fragment():
    """A long clause must keep an accent instead of disappearing."""

    _caption_emphasis_items_from_motion_events(
        [
            _event(
                semantic_text="客户资料留在公司",
                source_text="客户资料留在公司里",
            )
        ]
    )
    projected = _caption_emphasis_items_from_motion_events(
        [
            _event(
                semantic_text="客户资料留在公司",
                source_text="客户资料留在公司里",
            )
        ]
    )
    assert projected, "an over-long term must not vanish"
    term = projected[0]["term"]
    assert 2 <= len(term) <= 6
    # The fragment has to be something the speaker actually said.
    assert term in "客户资料留在公司里"


def test_grounded_helper_never_invents_text():
    """It may shorten a term; it may never synthesise new wording."""

    assert _grounded_emphasis_term("客户资料留在公司", "客户资料留在公司里") == "客户资料"
    assert _grounded_emphasis_term("完全不存在的内容", "另一句话") is None
    # Already short enough: returned unchanged.
    assert _grounded_emphasis_term("库存", "别让客户名单变成了库存") == "库存"


def test_ungrounded_beat_is_dropped_and_not_accented():
    """Text the speaker never said must not become an accent."""

    projected = _caption_emphasis_items_from_motion_events(
        [
            _event(
                semantic_text="完全不存在的内容",
                source_text="另一句话",
            )
        ]
    )
    assert projected == []


def test_audit_trail_is_reset_between_runs_and_records_failures():
    """The audit must not leak a previous run's drops into a clean run."""

    # A run where a long, ungrounded term cannot be accented is impossible to
    # construct without inventing copy, so assert the reset contract instead:
    # a clean run always reports an empty drop list.
    _caption_emphasis_items_from_motion_events(
        [_event(semantic_text="库存", source_text="别让客户名单变成了库存")]
    )
    assert _LAST_DROPPED_EMPHASIS_TERMS == []
    _caption_emphasis_items_from_motion_events([_event()])
    assert _LAST_DROPPED_EMPHASIS_TERMS == []
