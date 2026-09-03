from __future__ import annotations

from src.services.motion_design import build_semantic_motion_events


def test_road_phrase_gets_caption_integrated_road_visual():
    events = build_semantic_motion_events(
        [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "修建了一条高速公路",
            }
        ],
        duration_seconds=2.0,
    )

    assert len(events) == 1
    assert events[0]["style_id"] == "road_push"
    assert events[0]["semantic_kind"] == "road"
    assert events[0]["presentation"] == "caption_integrated_accent"


def test_numbered_benefit_phrase_gets_burst_visual_instead_of_plain_number_pulse():
    events = build_semantic_motion_events(
        [
            {
                "start": 1.0,
                "end": 3.2,
                "text": "充值100送10块，回头客更多",
            }
        ],
        duration_seconds=5.0,
    )

    assert len(events) == 1
    assert events[0]["semantic_kind"] == "benefit"
    assert events[0]["style_id"] == "benefit_burst"


def test_event_budget_reserves_visual_vocabulary_for_grounded_semantics():
    events = build_semantic_motion_events(
        [
            {"start": 0.0, "end": 1.4, "text": "附近5公里的居民都成了回头客"},
            {"start": 4.0, "end": 5.4, "text": "修建了一条高速公路"},
            {"start": 8.0, "end": 9.4, "text": "第一步先把流程理顺"},
            {"start": 12.0, "end": 13.4, "text": "最后评论区告诉我"},
        ],
        duration_seconds=20.0,
    )

    styles = {event["style_id"] for event in events}
    assert {"number_slam", "road_push", "process_marker"}.issubset(styles)


def test_semantic_visual_beat_does_not_lead_the_spoken_sentence():
    events = build_semantic_motion_events(
        [
            {
                "start": 0.0,
                "end": 8.0,
                "text": "前面先铺垫，最后说到附近5公里的居民",
            }
        ],
        duration_seconds=8.0,
    )

    assert events[0]["start"] == 5.2


def test_cta_visual_beat_is_pinned_to_the_sentence_tail():
    events = build_semantic_motion_events(
        [{"start": 100.0, "end": 104.0, "text": "如果需要，评论区告诉我"}],
        duration_seconds=104.0,
    )

    assert events[0]["style_id"] == "cta_burst"
    assert events[0]["start"] == 102.2


def test_logic_phrase_gets_minimal_causal_arrow():
    events = build_semantic_motion_events(
        [{"start": 0.0, "end": 2.0, "text": "因为流程自动化，所以效率提升"}],
        duration_seconds=2.0,
    )

    assert events[0]["semantic_kind"] == "logic"
    assert events[0]["style_id"] == "logic_arrow"


def test_price_phrase_gets_compact_zoom_card():
    events = build_semantic_motion_events(
        [{"start": 0.0, "end": 2.0, "text": "现在售价只要99元"}],
        duration_seconds=2.0,
    )

    assert events[0]["semantic_kind"] == "price"
    assert events[0]["style_id"] == "price_zoom_card"


def test_percentage_phrase_gets_data_highlight_card():
    events = build_semantic_motion_events(
        [{"start": 0.0, "end": 2.0, "text": "80%的顾客都主动复购"}],
        duration_seconds=2.0,
    )

    assert events[0]["semantic_kind"] == "data"
    assert events[0]["style_id"] == "data_highlight_card"


def test_compare_phrase_gets_split_accent():
    events = build_semantic_motion_events(
        [{"start": 0.0, "end": 2.0, "text": "不是单纯降价，而是让客户持续复购"}],
        duration_seconds=2.0,
    )

    assert events[0]["semantic_kind"] == "compare"
    assert events[0]["style_id"] == "compare_split_accent"


def test_knowledge_phrase_gets_pop_card():
    events = build_semantic_motion_events(
        [{"start": 0.0, "end": 2.0, "text": "这个核心技巧要记住"}],
        duration_seconds=2.0,
    )

    assert events[0]["semantic_kind"] == "knowledge"
    assert events[0]["style_id"] == "knowledge_pop_card"
