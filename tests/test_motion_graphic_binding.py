"""Motion graphic + bound sound: the pairing rules.

The reference rough cut never shows a bare floating sticker.  Every sudden
visual is a composed component (shape + icon + text + entrance) and every one
of those carries exactly one short sound fired on the entrance frame.

The failure these tests guard against is specific: the audio scheduler used to
drop any event landing within one second of a previously scheduled one, with no
record.  A rendered motion graphic could therefore be visible and silent, and
nothing in the report said so.
"""

from __future__ import annotations

from src.services.motion_graphics import (
    MOTION_GRAPHIC_STYLES,
    build_motion_graphic_events,
    render_motion_graphic,
)
from src.services.video_editor_workflow import _visual_sfx_items


def _annotations() -> list[dict]:
    return [
        {
            "start": 2.4, "end": 3.8, "text": "附近5公里",
            "source_text": "附近5公里", "semantic_roles": ["LOCATION"],
            "source_segment_index": 0,
        },
        {
            "start": 10.8, "end": 12.2, "text": "80%",
            "source_text": "80%的顾客", "semantic_roles": ["PERCENT", "NUMBER"],
            "source_segment_index": 1,
        },
        {
            "start": 16.0, "end": 17.5, "text": "大多数人都办了",
            "source_text": "所以大多数人都办了",
            "semantic_roles": ["POSITIVE", "CONCLUSION"],
            "source_segment_index": 2,
        },
    ]


def test_components_are_motion_graphics_with_one_sound_each():
    events = build_motion_graphic_events(_annotations(), duration_seconds=24.0)

    assert events
    for event in events:
        assert event["render_class"] == "motion_graphic"
        assert event["style_id"] in MOTION_GRAPHIC_STYLES
        assert event["sfx_required"] is True
        assert event["sfx_profile"]
        assert event["animation"]
        assert event["icon_id"]
        assert event["main_text"]


def test_number_keeps_its_unit_and_badges_carry_no_clipped_text():
    events = build_motion_graphic_events(_annotations(), duration_seconds=24.0)
    by_style = {event["style_id"]: event for event in events}

    # "附近5公里" must keep the unit rather than reducing to a bare "5".
    assert by_style["info_pill"]["main_text"] == "5公里"
    assert by_style["number_burst"]["main_text"] == "80%"
    assert by_style["number_burst"]["sub_text"] == "的顾客"


def test_a_negated_sentence_never_gets_a_success_badge():
    negated = [
        {
            "start": 3.0, "end": 4.5, "text": "别让客户名单变成库存",
            "source_text": "别让客户名单变成库存",
            "semantic_roles": ["POSITIVE", "CONCLUSION"],
            "source_segment_index": 0,
        },
        {
            "start": 8.0, "end": 9.5, "text": "不是越归越好",
            "source_text": "不是越归越好",
            "semantic_roles": ["POSITIVE"], "source_segment_index": 1,
        },
    ]
    events = build_motion_graphic_events(negated, duration_seconds=12.0)

    assert not any(event["style_id"] == "success_badge" for event in events)


def test_every_rendered_component_keeps_its_sound_even_when_close_together():
    """Two components half a second apart must both stay audible."""

    items = [
        {
            "event_id": "mg-01-info_pill", "render_class": "motion_graphic",
            "style_id": "info_pill", "start": 2.4, "end": 3.8,
            "sfx_profile": "whoosh_soft", "sfx_required": True,
        },
        {
            "event_id": "mg-02-number_burst", "render_class": "motion_graphic",
            "style_id": "number_burst", "start": 2.9, "end": 4.3,
            "sfx_profile": "impact_soft", "sfx_required": True,
        },
    ]
    bound = _visual_sfx_items(items, [], [])

    ids = {item["event_id"] for item in bound}
    assert "mg-01-info_pill" in ids
    assert "mg-02-number_burst" in ids, "a visible component lost its sound"


def test_the_visual_and_its_sound_share_one_auditable_id():
    items = [
        {
            "event_id": "mg-07-warning_badge", "render_class": "motion_graphic",
            "style_id": "warning_badge", "start": 5.0, "end": 6.4,
            "sfx_profile": "warning_tick", "sfx_required": True,
        }
    ]
    bound = _visual_sfx_items(items, [], [])

    assert len(bound) == 1
    assert bound[0]["sfx_event_id"] == bound[0]["visual_event_id"]
    assert bound[0]["sfx_event_id"] == "mg-07-warning_badge"


def test_plain_caption_emphasis_still_uses_the_sparse_rule():
    """Caption punctuation keeps the one-second spacing guard."""

    captions = [
        {
            "event_id": "cap-01", "type": "keyword_emphasis",
            "start": 5.0, "end": 6.0, "sfx_profile": "pop_soft",
        },
        {
            "event_id": "cap-02", "type": "keyword_emphasis",
            "start": 5.4, "end": 6.4, "sfx_profile": "pop_soft",
        },
    ]
    bound = _visual_sfx_items(captions, [], [])

    assert [item["event_id"] for item in bound] == ["cap-01"]


def test_a_motion_graphic_without_a_library_asset_still_gets_a_profile(tmp_path):
    """No library file must not mean no sound: the procedural path covers it."""

    items = [
        {
            "event_id": "mg-09-success_badge", "render_class": "motion_graphic",
            "style_id": "success_badge", "start": 1.0, "end": 2.0,
            "sfx_required": True,
        }
    ]
    bound = _visual_sfx_items(items, [], [])

    assert len(bound) == 1
    assert bound[0]["sfx_profile"], "missing sound profile and no fallback"
    assert bound[0]["sfx_required"] is True


def test_every_component_style_renders(tmp_path):
    events = build_motion_graphic_events(_annotations(), duration_seconds=24.0)
    rendered = 0
    for event in events:
        path = tmp_path / f"{event['event_id']}.png"
        if render_motion_graphic(event, path):
            assert path.is_file() and path.stat().st_size > 0
            rendered += 1
    assert rendered == len(events)
