from src.services.talking_head_templates import (
    ADAPTIVE_TALKING_HEAD_TEMPLATE_ID,
    LEGACY_TEMPLATE_ALIASES,
    TALKING_HEAD_STYLE_TOKENS,
    TALKING_HEAD_TEMPLATE_VERSION,
    build_talking_head_shot_plan,
    resolve_talking_head_template_id,
    retime_segments_for_shot_plan,
)


def test_legacy_template_ids_share_one_adaptive_style_fingerprint():
    fingerprints = {
        template_id: (
            tokens["subtitle_style_id"],
            tokens["hook_style_id"],
            tokens["transition_policy"],
            tokens["insert_policy"],
            tokens["bgm_profile"],
        )
        for template_id, tokens in TALKING_HEAD_STYLE_TOKENS.items()
    }
    assert len(set(fingerprints.values())) == 1
    assert all(resolve_talking_head_template_id(item) == ADAPTIVE_TALKING_HEAD_TEMPLATE_ID for item in fingerprints)
    assert LEGACY_TEMPLATE_ALIASES["story_resonance"] == ADAPTIVE_TALKING_HEAD_TEMPLATE_ID


def test_pain_point_plan_moves_question_hook_and_drops_next_take():
    segments = [
        {"start": 0.28, "end": 4.0, "text": "业务员离职以后客户关系不能断掉"},
        {"start": 4.0, "end": 29.84, "text": "企业要把客户关系沉淀在自己的数据库里"},
        {"start": 30.32, "end": 36.44, "text": "你的客户资源是在业务员手里还是公司数据库"},
        {"start": 38.64, "end": 39.4, "text": "好，第七"},
        {"start": 40.16, "end": 58.56, "text": "别让客户名单变成库存如果"},
    ]

    plan = build_talking_head_shot_plan(
        segments,
        duration_seconds=58.58,
        title="客户关系要沉淀在公司",
    )

    assert plan["template_id"] == ADAPTIVE_TALKING_HEAD_TEMPLATE_ID
    assert plan["selection"]["compatibility_alias"] == "pain_point_solution"
    assert plan["template_version"] == TALKING_HEAD_TEMPLATE_VERSION
    assert plan["hook_source"]["source_start"] == 30.32
    assert plan["reordered_ranges"][0]["start"] == 30.32
    assert plan["reordered_ranges"][1]["start"] == 0.28
    assert any(item["start"] == 38.64 for item in plan["deleted_ranges"])
    assert all(shot["role"] == "A-roll" for shot in plan["shots"])
    assert plan["degradation"]["mode"] == "精剪口播降级"
    durations = [shot["duration_seconds"] for shot in plan["shots"]]
    assert max(durations) <= 6.0
    assert any(2.0 <= duration <= 6.0 for duration in durations)
    assert len({shot["rhythm_variant"] for shot in plan["shots"]}) >= 2


def test_content_classifier_keeps_legacy_alias_without_changing_default_style():
    knowledge = build_talking_head_shot_plan(
        [
            {"start": 0, "end": 2, "text": "今天给你三个方法"},
            {"start": 2, "end": 4, "text": "第一先做准备"},
            {"start": 4, "end": 6, "text": "第二建立流程"},
            {"start": 6, "end": 8, "text": "第三复盘总结"},
        ],
        duration_seconds=8,
    )
    story = build_talking_head_shot_plan(
        [
            {"start": 0, "end": 2, "text": "曾经有一个客户遇到问题"},
            {"start": 2, "end": 4, "text": "后来我们一起经历了转折"},
            {"start": 4, "end": 6, "text": "终于找到了解决办法"},
        ],
        duration_seconds=6,
    )
    assert knowledge["template_id"] == ADAPTIVE_TALKING_HEAD_TEMPLATE_ID
    assert story["template_id"] == ADAPTIVE_TALKING_HEAD_TEMPLATE_ID
    assert knowledge["selection"]["compatibility_alias"] == "knowledge_howto"
    assert story["selection"]["compatibility_alias"] == "story_resonance"


def test_retime_follows_reordered_shots_without_duplicate_segments():
    segments = [
        {"start": 0.0, "end": 2.0, "text": "正文"},
        {"start": 3.0, "end": 4.0, "text": "钩子"},
    ]
    plan = build_talking_head_shot_plan(segments, duration_seconds=4)
    retimed = retime_segments_for_shot_plan(segments, plan)
    assert [item["text"] for item in retimed] == ["钩子", "正文"]
    assert retimed[0]["start"] == 0.0
    assert retimed[-1]["end"] <= plan["timeline_duration_seconds"]


def test_retime_merges_one_sentence_crossing_visual_shot_boundaries():
    segments = [
        {"start": 0.0, "end": 8.0, "text": "这是一句跨越多个画面镜头的完整口播"},
    ]
    shot_plan = {
        "shots": [
            {"source_start": 0.0, "source_end": 3.6, "timeline_start": 0.0},
            {"source_start": 3.6, "source_end": 7.2, "timeline_start": 3.6},
            {"source_start": 7.2, "source_end": 8.0, "timeline_start": 7.2},
        ],
        "timeline_duration_seconds": 8.0,
    }

    retimed = retime_segments_for_shot_plan(segments, shot_plan)

    assert len(retimed) == 1
    assert retimed[0]["text"] == segments[0]["text"]
    assert retimed[0]["start"] == 0.0
    assert retimed[0]["end"] == 8.0


def test_retime_preserves_word_timestamps_across_visual_shot_boundaries():
    segments = [
        {
            "start": 0.0,
            "end": 4.0,
            "text": "甲乙丙丁",
            "words": [
                {"start": 0.0, "end": 1.0, "text": "甲"},
                {"start": 1.0, "end": 2.0, "text": "乙"},
                {"start": 2.0, "end": 3.0, "text": "丙"},
                {"start": 3.0, "end": 4.0, "text": "丁"},
            ],
        }
    ]
    plan = {
        "shots": [
            {"source_start": 0.0, "source_end": 2.5, "timeline_start": 0.0},
            {"source_start": 2.5, "source_end": 4.0, "timeline_start": 2.5},
        ],
        "timeline_duration_seconds": 4.0,
    }

    retimed = retime_segments_for_shot_plan(segments, plan)

    assert len(retimed) == 1
    assert [word["text"] for word in retimed[0]["words"]] == ["甲", "乙", "丙", "丁"]
    assert retimed[0]["words"][-1]["end"] == 4.0
