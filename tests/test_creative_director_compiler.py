from src.services.creative_director_compiler import compile_creative_proposals


def _proposal(**overrides):
    value = {
        "proposal_id": "p1",
        "source_segment_index": 0,
        "semantic_text": "充值100送10元",
        "importance": 0.95,
        "event_type": "number_emphasis",
        "layout": "number_focus",
        "caption_treatment": "slam_keyword",
        "keyword": "100",
        "visual_type": "programmatic_infographic",
        "camera_motion": "punch_in_medium",
        "transition": "hard_cut",
        "asset_query": None,
        "sfx": "soft_impact",
        "preferred_duration_seconds": 0.7,
        "reason": "数字需要快速强调",
    }
    value.update(overrides)
    return value


def test_compiler_uses_word_clock_and_emits_caption_camera_and_programmatic_visual():
    result = compile_creative_proposals(
        [_proposal()],
        [{
            "start": 0.0,
            "end": 3.0,
            "text": "充值100送10元",
            "words": [
                {"text": "充值", "start": 0.0, "end": 0.4},
                {"text": "100", "start": 0.4, "end": 0.8},
                {"text": "送10元", "start": 0.8, "end": 1.4},
            ],
        }],
        duration_seconds=3.0,
    )
    assert result["rejected_proposals"] == []
    assert result["compiled_count"] == 1
    assert {item["type"] for item in result["motion_events"]} == {
        "keyword_emphasis",
        "semantic_motion_badge",
    }
    assert result["camera_events"][0]["clock_source"] == "word_timestamps"
    assert result["motion_events"][0]["sfx_profile"] == "impact_soft"


def test_compiler_keeps_missing_asset_as_a_request_and_does_not_invent_broll():
    result = compile_creative_proposals(
        [_proposal(
            proposal_id="p-broll",
            event_type="product_visual",
            layout="full_screen_broll",
            visual_type="licensed_broll",
            camera_motion="none",
            caption_treatment="static",
            keyword="充值100送10元",
            asset_query="店内收银台优惠说明",
            sfx="none",
        )],
        [{"start": 0.0, "end": 4.0, "text": "充值100送10元"}],
        duration_seconds=4.0,
    )
    assert result["asset_events"] == []
    assert result["asset_requests"][0]["status"] == "awaiting_authorized_asset"
    assert result["motion_events"] == []


def test_compiler_carries_director_selected_sfx_id_through_every_event():
    result = compile_creative_proposals(
        [_proposal(
            sfx_asset_id="sound-1",
            event_type="product_visual",
            layout="full_screen_broll",
            visual_type="user_asset",
            asset_id="asset-1",
        )],
        [{"start": 0.0, "end": 3.0, "text": "充值100送10元"}],
        duration_seconds=3.0,
        available_assets=[{"asset_id": "asset-1", "publish_allowed": True}],
    )

    assert result["rejected_proposals"] == []
    assert result["proposals"][0]["sfx_asset_id"] == "sound-1"
    assert result["asset_events"][0]["type"] == "broll_fullscreen"
    assert all(
        event["sfx_asset_id"] == "sound-1"
        for event in result["events"]
    )


def test_compiler_bounds_strong_events_and_sfx_spacing():
    proposals = [
        _proposal(
            proposal_id=f"p{i}",
            source_segment_index=i,
            semantic_text=f"第{i + 1}步",
            keyword=f"第{i + 1}步",
            event_type="process_visual",
            layout="number_focus",
            sfx="soft_tick",
        )
        for i in range(7)
    ]
    segments = [
        {"start": float(i * 2), "end": float(i * 2 + 1.2), "text": f"第{i + 1}步"}
        for i in range(7)
    ]
    result = compile_creative_proposals(proposals, segments, duration_seconds=20.0)
    assert len(result["compiled_proposals"]) <= 5
    assert len(result["rejected_proposals"]) >= 2
