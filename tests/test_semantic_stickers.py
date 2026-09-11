from src.services.semantic_stickers import (
    build_sticker_events,
    supplement_motion_events,
)
from src.services.video_editor_workflow import (
    _clean_grammar_motion_events,
    _resolve_local_sound_effect,
    _select_sparse_sfx_items,
    _visual_sfx_items,
)


def test_clean_grammar_preserves_native_emphasis_not_floating_badges():
    events = [
        {"type": "camera", "start": 3.2, "semantic_text": "关键动作"},
        {"type": "keyword_emphasis", "start": 3.2, "semantic_text": "关键动作"},
        {"type": "semantic_symbol", "style_id": "grammar_green_check", "start": 3.2},
        {"type": "text_emphasis", "start": 3.2, "semantic_text": "关键动作"},
        {"type": "semantic_symbol", "style_id": "grammar_sticker", "start": 3.2},
    ]
    result = _clean_grammar_motion_events(events)
    assert [event["type"] for event in result] == [
        "camera", "keyword_emphasis", "semantic_symbol", "text_emphasis"
    ]
    assert result[2]["style_id"] == "grammar_green_check"
    assert len(events) == 5


def test_retired_material_stickers_never_return_for_unseen_domains():
    segments = [
        {"start": 0, "end": 3, "text": "剪发之前先分区"},
        {"start": 9, "end": 12, "text": "打开地图查看路线"},
        {"start": 20, "end": 23, "text": "准备好检查表"},
        {"start": 32, "end": 35, "text": "其实这很重要"},
    ]
    events = build_sticker_events(segments, duration_seconds=60)
    assert events == []


def test_sticker_word_clock_and_replacement():
    events = build_sticker_events([{
        "start": 10, "end": 15, "text": "先说一下赠品",
        "words": [{"word": "先说一下", "start": 10}, {"word": "赠品", "start": 13}],
    }], duration_seconds=30)
    assert events == []
    combined = supplement_motion_events([
        {"type": "text_emphasis", "start": 12, "end": 14},
        {"type": "camera", "start": 12, "end": 14},
    ], events)
    assert {e["type"] for e in combined} == {"camera", "text_emphasis"}


def test_end_of_sentence_noun_is_not_lost():
    events = build_sticker_events([{
        "start": 0, "end": 3, "text": "还有赠品",
        "words": [{"word": "还有", "start": 0}, {"word": "赠品", "start": 2.8}],
    }], duration_seconds=20)
    assert events == []


def test_sticker_sound_is_bound_to_entry_not_previous_keyword():
    sticker = {"start": 12, "source_segment_index": 0, "importance": 0.88,
               "asset_category": "licensed_sticker", "sfx_profile": "pop_soft"}
    keyword = {"start": 10, "source_segment_index": 0, "importance": 0.99}
    events = _select_sparse_sfx_items([sticker], [keyword], duration_seconds=30, target_count=4)
    assert len(events) == 1
    assert events[0]["start"] == 12


def test_every_rendered_sticker_and_vector_gets_a_bound_sfx():
    items = _visual_sfx_items(
        [
            {
                "event_id": "sticker-1",
                "start": 1.0,
                "end": 2.0,
                "asset_category": "custom_semantic_sticker",
                "semantic_role": "PRICE",
            }
        ],
        [
            {
                "start": 6.0,
                "end": 7.0,
                "asset_id": "editorial-arrow",
                "semantic_role": "PROCESS",
            }
        ],
    )

    assert [item["event_id"] for item in items] == [
        "sticker-1",
        "vector-sticker-01",
    ]
    assert [item["sfx_profile"] for item in items] == [
        "pop_soft",
        "whoosh_soft",
    ]


def test_symbol_candidates_obey_sfx_budget_and_dedup():
    items = [{"start": i * 4, "end": i * 4 + 2, "source_segment_index": i,
              "semantic_role": "PRICE", "importance": 0.9} for i in range(24)]
    chosen = _select_sparse_sfx_items(items + items, items, duration_seconds=97, target_count=12)
    assert len(chosen) <= 12
    assert len({e["source_segment_index"] for e in chosen}) == len(chosen)
    from itertools import pairwise

    assert all(b["start"] - a["start"] >= 2.8 for a, b in pairwise(chosen))
    assert {e["sfx_profile"] for e in chosen} == {"pop_soft", "tick_soft"}


def test_unlicensed_sound_is_not_used(tmp_path, monkeypatch):
    import src.services.video_editor_workflow as workflow
    monkeypatch.setattr(workflow, "_SFX_LIBRARY_DIR", tmp_path)
    (tmp_path / "sfx-pop.wav").write_bytes(b"unknown")
    assert _resolve_local_sound_effect({"sfx_profile": "pop_soft"}, event_index=0) is None


def test_retired_grammar_sticker_is_removed_from_formal_motion_events():
    result = _clean_grammar_motion_events([
        {"type": "semantic_symbol", "style_id": "grammar_sticker", "source": "approved_local_vector_library"},
        {"type": "camera", "style_id": "grammar_camera"},
    ])
    assert [event["type"] for event in result] == ["camera"]
