from src.services.grammar_only import (
    ALLOWED_CAMERA_ACTIONS,
    JY_CLONE_GRAMMAR_ONLY,
    SEMANTIC_ROLES,
    build_grammar_only_timeline,
    build_grammar_style_events,
    annotate_transcript_segments,
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


def test_strong_numeric_semantics_use_minimal_burst_lines_not_a_badge():
    events = build_grammar_style_events(
        [{
            "start": 1.0,
            "end": 3.0,
            "semantic_text": "30%",
            "source_text": "结果提高了30%",
            "semantic_roles": ["PERCENT"],
            "keyword_candidates": ["30%"],
            "importance": .95,
        }]
    )

    symbols = [event for event in events if event.get("type") == "semantic_symbol"]
    assert len(symbols) == 1
    assert symbols[0]["symbol"] == "burst_lines"
    assert symbols[0]["reason"]
    assert "sun" not in symbols[0]["style_id"]


def test_offer_phrase_keeps_charge_and_reward_as_one_grounded_semantic_beat():
    annotations = annotate_transcript_segments(
        [{"start": 1.0, "end": 3.0, "text": "充值300送50块"}],
        duration_seconds=3.0,
    )

    annotation = annotations[0]
    assert annotation["semantic_text"] == "充值300送50块"
    assert annotation["semantic_text"] in annotation["source_text"]
    events = build_grammar_style_events(annotations)
    text_events = [event for event in events if event["type"] == "text_emphasis"]
    assert any(event["text"] == "充值300送50块" for event in text_events)
    assert all(event["semantic_text"] in event["source_text"] for event in events)


def test_positive_degree_idiom_does_not_create_negative_red_x():
    annotations = annotate_transcript_segments(
        [{"start": 12.0, "end": 14.0, "text": "生意好的不行"}],
        duration_seconds=14.0,
    )
    events = build_grammar_style_events(annotations)

    assert annotations[0]["semantic_roles"][:2] == ["HOOK", "POSITIVE"]
    assert annotations[0]["suppress_semantic_symbol"] is True
    assert not any(event.get("symbol") == "red_x" for event in events)


def test_conclusion_marker_does_not_become_the_visual_anchor():
    annotations = annotate_transcript_segments(
        [{"start": 0.0, "end": 2.0, "text": "所以大多数人都办了"}],
        duration_seconds=2.0,
    )

    assert annotations[0]["semantic_text"] != "所以"
    assert annotations[0]["semantic_text"] in annotations[0]["source_text"]


def test_character_inside_a_word_does_not_create_negative_intent():
    annotations = annotate_transcript_segments(
        [{"start": 0.0, "end": 2.0, "text": "特别的餐饮模式"}],
        duration_seconds=2.0,
    )

    assert "NEGATIVE" not in annotations[0]["semantic_roles"]


def test_actual_negative_word_still_allows_red_x():
    annotations = annotate_transcript_segments(
        [{"start": 12.0, "end": 14.0, "text": "这个价格不行"}],
        duration_seconds=14.0,
    )
    events = build_grammar_style_events(annotations)

    assert any(event.get("symbol") == "red_x" for event in events)


def test_explicit_negative_intent_overrides_conclusion_and_number_signals():
    annotations = annotate_transcript_segments(
        [
            {"start": 0.0, "end": 2.0, "text": "所以这个方案失败了"},
            {"start": 3.0, "end": 5.0, "text": "亏损达到30%，这不是成功"},
        ],
        duration_seconds=5.0,
    )

    assert annotations[0]["semantic_roles"][1] == "NEGATIVE"
    assert annotations[0]["emotion"] == "negative"
    assert annotations[1]["semantic_roles"][1] == "NEGATIVE"
    assert annotations[1]["emotion"] == "negative"
    assert all(item["semantic_text"] in item["source_text"] for item in annotations)


def test_cta_intent_overrides_embedded_number():
    annotations = annotate_transcript_segments(
        [{"start": 0.0, "end": 2.0, "text": "评论区填写123领取资料"}],
        duration_seconds=2.0,
    )

    assert annotations[0]["semantic_roles"][1] == "CTA"
    assert annotations[0]["semantic_text"] != "123"
    assert annotations[0]["semantic_text"] in annotations[0]["source_text"]


def test_keyword_selection_prefers_grounded_noun_phrase_over_sentence_prefix():
    annotations = annotate_transcript_segments(
        [{"start": 1.0, "end": 3.0, "text": "老板不用费心想推广方案"}],
        duration_seconds=3.0,
    )

    candidates = annotations[0]["keyword_candidates"]
    assert "老板不用费心" not in candidates
    assert "推广方案" in candidates
    assert all(value in annotations[0]["source_text"] for value in candidates)


def test_semantic_anchors_keep_compound_objects_and_cta_targets():
    annotations = annotate_transcript_segments(
        [
            {"start": 1.0, "end": 3.0, "text": "用户可以直接换上智慧门店系统"},
            {"start": 5.0, "end": 7.0, "text": "评论区填写课程名称即可"},
            {"start": 9.0, "end": 11.0, "text": "会员会变成免费宣传员"},
        ],
        duration_seconds=11.0,
    )

    by_source = {item["source_text"]: item for item in annotations}
    assert "智慧门店系统" in by_source["用户可以直接换上智慧门店系统"]["semantic_text"]
    assert by_source["评论区填写课程名称即可"]["semantic_text"] == "课程名称"
    benefit = by_source["会员会变成免费宣传员"]
    assert "免费宣传员" in benefit["semantic_text"]
    assert all(item["semantic_text"] in item["source_text"] for item in annotations)


def test_chinese_quantity_and_benefit_anchors_are_not_isolated_function_words():
    annotations = annotate_transcript_segments(
        [
            {"start": 1.0, "end": 3.0, "text": "活动吸引了好几百人参加"},
            {"start": 5.0, "end": 7.0, "text": "内容可以自然传播"},
        ],
        duration_seconds=7.0,
    )

    assert any(item["semantic_text"] == "好几百人" for item in annotations)
    assert any("传播" in item["semantic_text"] for item in annotations)
    assert all(item["semantic_text"] not in {"一", "一个", "评论"} for item in annotations)
