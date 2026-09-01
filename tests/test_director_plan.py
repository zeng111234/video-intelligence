from src.services.director_plan import (
    DIRECTOR_PLAN_VERSION,
    build_director_plan,
    validate_director_plan,
)


def test_director_plan_is_auditable_and_requests_images_without_calling_provider():
    plan = build_director_plan(
        [
            {"start": 0.0, "end": 3.0, "text": "业务员离职客户关系不能断"},
            {"start": 3.0, "end": 8.0, "text": "公司要把客户关系沉淀到数据库"},
            {"start": 8.0, "end": 11.0, "text": "到底应该放在谁手里"},
        ],
        duration_seconds=11.0,
        title="客户关系要沉淀在公司",
    )

    assert plan["plan_version"] == DIRECTOR_PLAN_VERSION
    assert plan["hook"]["audio_strategy"] == "extract_original_phrase"
    assert plan["asset_requests"]
    assert plan["degradation"]["publish_claim_allowed"] is False
    assert validate_director_plan(plan) == []


def test_director_plan_keeps_uploaded_broll_as_explicit_asset():
    plan = build_director_plan(
        [{"start": 0.0, "end": 4.0, "text": "这是一个完整观点"}],
        duration_seconds=4.0,
        broll_asset={
            "asset_id": "broll-1234567890",
            "source": "user_uploaded_local",
            "authorization_status": "confirmed",
        },
    )

    assert plan["asset_requests"] == []
    assert plan["degradation"]["mode"] == "broll"


def test_generated_images_are_local_visual_events_but_not_publish_claims():
    generated = {
        "source": "built_in_image_generation",
        "asset_origin": "generated_image_asset",
        "authorization_status": "generated_for_local_acceptance",
        "mode": "full",
    }
    plan = build_director_plan(
        [{"start": 0.0, "end": 20.0, "text": "客户关系沉淀到企业数据库形成持续触达机制"}],
        duration_seconds=20.0,
        title="客户数据库",
        broll_assets_by_shot_id={
            "shot-02": {"asset_id": "generated-01", **generated},
            "shot-04": {"asset_id": "generated-02", **generated},
            "shot-06": {"asset_id": "generated-03", **generated},
        },
    )

    assert plan["degradation"]["local_visual_event_count"] >= 3
    assert plan["degradation"]["generated_image_event_count"] >= 3
    assert plan["degradation"]["publishable_visual_event_count"] == 0
    assert plan["degradation"]["publish_claim_allowed"] is False
    assert plan["quality_targets"]["generated_images_count_as_local_visual_events"] is True


def test_director_plan_rejects_overlapping_output_scenes():
    plan = build_director_plan(
        [{"start": 0.0, "end": 4.0, "text": "完整口播"}],
        duration_seconds=4.0,
    )
    plan["scenes"][1]["timeline_start"] = 0.0
    assert "场景输出时间轴发生重叠。" in validate_director_plan(plan)


def test_director_plan_contains_bounded_transcript_grounded_motion_events():
    plan = build_director_plan(
        [
            {"start": 0.0, "end": 2.4, "text": "这里先说明背景"},
            {"start": 3.2, "end": 5.6, "text": "成本可以降低30%"},
            {"start": 6.4, "end": 8.8, "text": "注意不要重复投放"},
            {"start": 9.6, "end": 12.0, "text": "最后评论区留言领取清单"},
        ],
        duration_seconds=25.0,
    )

    events = plan["motion_events"]
    assert events
    assert len(events) <= 10
    assert all(
        event["renderer"] == "procedural_overlay_v2_caption_integrated"
        for event in events
    )
    assert all(event["anchor"] == "smart_caption" for event in events)
    assert all(event["grounded_in_text"] is True for event in events)
    assert {event["semantic_kind"] for event in events}
    assert all(
        event["end"] > event["start"]
        and event["end"] - event["start"] <= 2.8
        for event in events
    )
