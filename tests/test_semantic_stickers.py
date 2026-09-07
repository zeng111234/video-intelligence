from pathlib import Path

from src.services.semantic_stickers import build_sticker_events, supplement_motion_events, render_sticker
from src.services.video_editor_workflow import (
    _select_sparse_sfx_items, _resolve_local_sound_effect, VideoEditorWorkflowService,
    _clean_grammar_motion_events,
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


def test_stickers_grounded_across_unseen_domains():
    segments = [
        {"start": 0, "end": 3, "text": "剪发之前先分区"},
        {"start": 9, "end": 12, "text": "打开地图查看路线"},
        {"start": 20, "end": 23, "text": "准备好检查表"},
        {"start": 32, "end": 35, "text": "其实这很重要"},
    ]
    events = build_sticker_events(segments, duration_seconds=60)
    assert len(events) == 3
    assert {event["symbol"] for event in events} == {"content_cut", "location_on", "checklist"}
    assert all(e["semantic_text"] in e["source_text"] for e in events)
    assert all(e["license"] == "Apache-2.0" for e in events)
    assert all(e["end"] - e["start"] <= 2.5 for e in events)


def test_sticker_word_clock_and_replacement():
    events = build_sticker_events([{
        "start": 10, "end": 15, "text": "先说一下赠品",
        "words": [{"word": "先说一下", "start": 10}, {"word": "赠品", "start": 13}],
    }], duration_seconds=30)
    assert events[0]["start"] == 13
    combined = supplement_motion_events([
        {"type": "text_emphasis", "start": 12, "end": 14},
        {"type": "camera", "start": 12, "end": 14},
    ], events)
    assert {e["type"] for e in combined} == {"camera", "semantic_symbol"}


def test_end_of_sentence_noun_is_not_lost():
    events = build_sticker_events([{
        "start": 0, "end": 3, "text": "还有赠品",
        "words": [{"word": "还有", "start": 0}, {"word": "赠品", "start": 2.8}],
    }], duration_seconds=20)
    assert len(events) == 1
    assert round(events[0]["end"] - events[0]["start"], 3) == 1.8


def test_sticker_sound_is_bound_to_entry_not_previous_keyword():
    sticker = {"start": 12, "source_segment_index": 0, "importance": 0.88,
               "asset_category": "licensed_sticker", "sfx_profile": "pop_soft"}
    keyword = {"start": 10, "source_segment_index": 0, "importance": 0.99}
    events = _select_sparse_sfx_items([sticker], [keyword], duration_seconds=30, target_count=4)
    assert len(events) == 1
    assert events[0]["start"] == 12


def test_sticker_renderer_has_transparent_background(tmp_path):
    from PIL import Image
    event = build_sticker_events([{"text": "领取优惠券", "start": 0, "end": 3}], duration_seconds=30)[0]
    path = tmp_path / "sticker.png"
    render_sticker(event, path)
    image = Image.open(path)
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0
    assert image.getbbox() is not None


def test_symbol_candidates_obey_sfx_budget_and_dedup():
    items = [{"start": i * 4, "end": i * 4 + 2, "source_segment_index": i,
              "semantic_role": "PRICE", "importance": 0.9} for i in range(24)]
    chosen = _select_sparse_sfx_items(items + items, items, duration_seconds=97, target_count=12)
    assert len(chosen) <= 12
    assert len({e["source_segment_index"] for e in chosen}) == len(chosen)
    assert all(b["start"] - a["start"] >= 2.8 for a, b in zip(chosen, chosen[1:]))
    assert {e["sfx_profile"] for e in chosen} == {"pop_soft", "tick_soft"}


def test_unlicensed_sound_is_not_used(tmp_path, monkeypatch):
    import src.services.video_editor_workflow as workflow
    monkeypatch.setattr(workflow, "_SFX_LIBRARY_DIR", tmp_path)
    (tmp_path / "sfx-pop.wav").write_bytes(b"unknown")
    assert _resolve_local_sound_effect({"sfx_profile": "pop_soft"}, event_index=0) is None


def test_late_sticker_animation_is_shifted_after_local_fade():
    result = VideoEditorWorkflowService._local_rhythm_video_filter(
        duration_seconds=20, width=720, height=1280, fps=30,
        playback_rate=1, subtitle_filter="approved.ass",
        motion_items=[{"start": 10, "end": 12, "style_id": "grammar_sticker", "side": "left"}],
    )
    assert "trim=duration=2.000,setpts=PTS-STARTPTS" in result
    assert "setpts=PTS+10.000/TB[motion0]" in result
    assert "fade=t=out:st=1.820" in result
    assert "H*0.67" in result
