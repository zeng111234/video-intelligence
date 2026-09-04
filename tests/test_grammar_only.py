from src.services.grammar_only import (
    ALLOWED_CAMERA_ACTIONS,
    JY_CLONE_GRAMMAR_ONLY,
    SEMANTIC_ROLES,
    build_grammar_only_timeline,
    build_grammar_style_events,
    validate_grammar_only_timeline,
)
from src.services.style_presets import PRESET_GRAMMAR_ONLY, get_style_preset


def _segments(texts):
    return [
        {"start": index * 3.2, "end": index * 3.2 + 2.2, "text": text}
        for index, text in enumerate(texts)
    ]


def test_grammar_only_preset_is_strict_and_independent():
    preset = get_style_preset(PRESET_GRAMMAR_ONLY)
    assert preset["grammar_mode"] == JY_CLONE_GRAMMAR_ONLY
    assert preset["external_visuals"] == {
        "allow_broll_video": False,
        "allow_pip": False,
        "allow_stock_video": False,
        "allow_stock_image": False,
        "allow_network_search": False,
        "allow_generated_images": False,
        "allow_minimax": False,
        "allow_ai_video": False,
        "allow_bgm": False,
    }
    assert get_style_preset(PRESET_GRAMMAR_ONLY) is not preset


def test_annotations_and_events_are_transcript_grounded_for_unfamiliar_topics():
    timeline = build_grammar_only_timeline(
        _segments(["利润提高了30%", "第二步点击右上角", "真正的问题不是价格", "那天我第一次见到他"]),
        duration_seconds=14,
    )
    assert timeline["mode"] == JY_CLONE_GRAMMAR_ONLY
    assert all(item["semantic_text"] in item["source_text"] for item in timeline["semantic_annotations"])
    assert all(set(item["semantic_roles"]).issubset(SEMANTIC_ROLES) for item in timeline["semantic_annotations"])
    assert all(item.get("type") != "generic_symbol" for item in timeline["events"])
    assert all(item.get("camera_action", "neutral") in ALLOWED_CAMERA_ACTIONS for item in timeline["events"])
    for item in timeline["events"]:
        if item.get("type") == "semantic_symbol":
            assert item.get("semantic_role") and item.get("reason")


def test_forbidden_external_visuals_fail_closed():
    assert validate_grammar_only_timeline({"brolls": [], "pip": [], "external_image": []}) == []
    violations = validate_grammar_only_timeline({"brolls": [{"asset_id": "stock-1"}], "pip": [{"mode": "pip"}]})
    assert violations
    assert any("brolls" in item for item in violations)
    assert validate_grammar_only_timeline({"external_visuals": True})


def test_style_engine_has_breathing_and_no_three_high_beats():
    events = build_grammar_style_events(
        [
            {"start": index * 3.0, "end": index * 3.0 + 2, "semantic_text": str(index), "source_text": str(index), "semantic_roles": ["NUMBER"], "importance": .9}
            for index in range(6)
        ]
    )
    ordered = sorted(events, key=lambda item: float(item.get("start") or 0))
    streak = 0
    for item in ordered:
        streak = streak + 1 if int(item.get("visual_intensity") or 0) >= 3 else 0
        assert streak < 3


def test_polish_increases_grounded_keyword_density_without_number_sun_symbols():
    timeline = build_grammar_only_timeline(
        _segments([
            "先看这个方法能不能省钱",
            "第一步先确认价格",
            "第二步再比较效果",
            "数字30%只是当前结果",
            "如果操作错误就会失败",
            "最后再关注实际变化",
        ] * 4),
        duration_seconds=82,
    )
    annotations = timeline["semantic_annotations"]
    keyword_events = [event for event in timeline["events"] if event["type"] == "keyword_emphasis"]
    assert len(annotations) >= 8
    assert len(keyword_events) >= len(annotations)
    assert all(event["semantic_text"] in event["source_text"] for event in keyword_events)
    assert all(event.get("style_id") != "grammar_number_badge" for event in timeline["events"])
    assert all(event.get("symbol") != "number_badge" for event in timeline["events"])
