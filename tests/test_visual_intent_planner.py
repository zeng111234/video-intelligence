from src.services.style_presets import (
    PRESET_SEMANTIC_ADAPTIVE,
    get_style_preset,
    resolve_style_preset_id,
)
from src.services.visual_intent_planner import build_semantic_visual_request


def test_semantic_visual_request_uses_current_subject_without_industry_dictionary():
    request = build_semantic_visual_request(
        text="先检查电池接口，再确认设备是否正常工作",
        segment_index=0,
        annotations=[
            {
                "source_segment_index": 0,
                "semantic_text": "先检查电池接口",
                "semantic_roles": ["STEP", "PROCESS"],
                "concrete_visual_subject": "电池接口",
                "semantic_director_version": "semantic-director-v1",
            }
        ],
    )

    assert request is not None
    assert request["visual_type"] == "process"
    assert request["preferred_mode"] == "pip"
    assert "电池接口" in request["search_queries"][0]
    assert request["grounded_in_transcript"] is True


def test_semantic_adaptive_preset_is_explicit_and_not_grammar_only():
    preset = get_style_preset(PRESET_SEMANTIC_ADAPTIVE)

    assert resolve_style_preset_id({"style_preset_id": PRESET_SEMANTIC_ADAPTIVE}) == (
        PRESET_SEMANTIC_ADAPTIVE
    )
    assert preset["visual_director"]["enabled"] is True
    assert preset["broll_policy"]["require_semantic_match"] is True
    assert preset["visual_director"]["allow_generated_image_gap_fill"] is True
