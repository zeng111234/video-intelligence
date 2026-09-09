from __future__ import annotations

import json

from src.adapters.llm import OpenAICompatibleCopywritingEngine
from src.services.semantic_director import (
    _build_minimax_engine,
    annotate_with_semantic_director,
    validate_semantic_annotations,
    validate_title_candidates,
)


def _segments():
    return [{"start": 1.0, "end": 3.0, "text": "这是一台咖啡机"}]


def test_annotations_are_source_grounded_and_roles_are_allowlisted():
    result = validate_semantic_annotations(
        {
            "annotations": [{
                "source_segment_index": 0,
                "semantic_text": "咖啡机",
                "semantic_roles": ["PRODUCT", "pip", "NUMBER"],
                "importance": 1.2,
                "concrete_visual_subject": "咖啡机",
            }]
        },
        _segments(),
    )
    assert result[0]["semantic_roles"] == ["PRODUCT", "NUMBER"]
    assert result[0]["semantic_text"] in result[0]["source_text"]
    assert result[0]["importance"] == 1.0


def test_invalid_model_text_is_dropped():
    result = validate_semantic_annotations(
        {"annotations": [{
            "source_segment_index": 0,
            "semantic_text": "不存在的产品",
            "semantic_roles": ["PRODUCT"],
        }]},
        _segments(),
    )
    assert result == []


def test_title_candidates_are_short_grounded_summaries():
    assert validate_title_candidates(
        {
            "title_candidates": [
                "咖啡机选购要点",
                "这是一条非常长的标题候选不应保留",
                "完全无关的标题",
            ]
        },
        _segments(),
    ) == ["咖啡机选购要点"]


def test_minimax_director_receives_text_and_keyframes_but_cannot_choose_effects(monkeypatch):
    engine = OpenAICompatibleCopywritingEngine(
        api_key="test", base_url="https://api.minimax.io/v1/text", model="MiniMax-M3"
    )
    captured = {}

    def fake_completion(system, user, *, user_content=None, disable_thinking=False):
        captured["system"] = system
        captured["content"] = user_content
        captured["disable_thinking"] = disable_thinking
        return json.dumps({
            "annotations": [{
                "source_segment_index": 0,
                "semantic_text": "咖啡机",
                "semantic_roles": ["PRODUCT"],
                "importance": 0.8,
                "concrete_visual_subject": "咖啡机",
            }]
        }, ensure_ascii=False)

    monkeypatch.setattr(engine, "_chat_completion", fake_completion)
    annotations, meta = annotate_with_semantic_director(
        _segments(),
        duration_seconds=3.0,
        engine=engine,
        keyframes=[{"timestamp": 2.0, "data_url": "data:image/jpeg;base64,abc"}],
    )
    assert meta["provider"] == "minimax"
    assert annotations[0]["semantic_roles"] == ["PRODUCT"]
    assert captured["content"][0]["type"] == "text"
    assert captured["content"][1]["type"] == "image_url"
    assert captured["content"][1]["image_url"]["detail"] == "low"
    assert "2.0" in captured["content"][0]["text"]
    assert captured["disable_thinking"] is True
    assert "punch_in" in captured["system"]
    assert "broll" in captured["system"]
    assert engine._completion_url(multimodal=True) == "https://api.minimax.io/v1/chat/completions"


def test_partial_minimax_response_is_completed_locally_and_reported_as_partial():
    class PartialEngine:
        model = "MiniMax-M3"

        def annotate_semantic_timeline(self, **kwargs):
            return {
                "annotations": [{
                    "source_segment_index": 0,
                    "semantic_text": "第一句",
                    "semantic_roles": ["KEY_CLAIM"],
                    "importance": 0.9,
                }]
            }

    segments = [
        {"start": 0.0, "end": 2.0, "text": "第一句内容"},
        {"start": 2.0, "end": 4.0, "text": "第二句内容"},
    ]
    annotations, meta = annotate_with_semantic_director(
        segments,
        duration_seconds=4.0,
        engine=PartialEngine(),
        fallback=lambda: [
            {
                "source_segment_index": 1,
                "semantic_text": "第二句",
                "semantic_roles": ["KEY_CLAIM"],
                "importance": 0.7,
            }
        ],
    )

    assert [item["source_segment_index"] for item in annotations] == [0, 1]
    assert annotations[0]["provider"] == "minimax"
    assert annotations[1]["provider"] == "local_rules"
    assert meta["status"] == "used_partial"
    assert meta["provider_annotation_count"] == 1
    assert meta["completed_annotation_count"] == 2
    assert meta["provider_coverage_ratio"] == 0.5
    assert meta["coverage_ratio"] == 1.0
    assert meta["provider_missing_segment_indices"] == [1]
    assert meta["missing_segment_indices"] == []

    _, partial_meta = annotate_with_semantic_director(
        segments,
        duration_seconds=4.0,
        engine=PartialEngine(),
        fallback=list,
    )
    assert partial_meta["status"] == "used_partial"
    assert partial_meta["missing_segment_indices"] == [1]


def test_minimax_director_timeout_is_bounded(monkeypatch):
    for name in (
        "VIDEO_DIRECTOR_API_KEY",
        "MINIMAX_API_KEY",
        "MINIMAX_TEXT_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MINIMAX_TOKEN_PLAN_KEY", "test-key")
    monkeypatch.setenv("VIDEO_DIRECTOR_TIMEOUT_SECONDS", "90")

    engine = _build_minimax_engine()

    assert engine.timeout_seconds == 30.0
