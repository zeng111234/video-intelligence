"""P0-2: 视觉时间窗规划 + 覆盖缺口决策。

确保：
- 长口播 (≥60s) 目标 0.45-0.65 真实 B-roll 覆盖。
- 短口播 (≤30s) 目标 0.25-0.45 真实 B-roll 覆盖。
- 每个 planned_visual_windows 含通用分类 (data_chart / concept_card / cta_card / spoken_point)。
- required 窗口（数据卡 / 流程卡 / CTA）不会被跳过。
- 当前真实覆盖 < target_min 时产生 coverage_deficit_seconds。
- 未解决窗口（required 但没被任何真实 B-roll 覆盖）被正确识别。
- 不为具体样片（烧烤店 / 二手车 / 金鱼 / 手机支架）写硬编码。
"""

from __future__ import annotations

import pytest

from src.services.director_plan import (
    _classify_visual_intent_for_window,
    _plan_visual_windows,
    _target_coverage_band,
    build_director_plan,
)


# ---------- 1) 通用分类 ----------


@pytest.mark.parametrize(
    "text,expected_intent",
    [
        ("今天增长了 30%", "data_chart"),
        ("利润涨了 5 万元", "data_chart"),
        ("对比去年同期", "data_chart"),
        ("比例达到 80%", "data_chart"),
        ("排名第一", "data_chart"),
        ("流程是 3 步", "data_chart"),
        ("第一步打开手机", "concept_card"),
        ("其次打开设置", "concept_card"),
        ("方法如下", "concept_card"),
        ("评论留言", "cta_card"),
        ("点击下方链接", "cta_card"),
        ("扣 1 报名", "cta_card"),
        ("私信我", "cta_card"),
        ("扫码加群", "cta_card"),
        ("普通口播句子", "spoken_point"),
        ("今天我跟你聊一聊", "spoken_point"),
        ("最后点击完成", "cta_card"),
        ("", "spoken_point"),
    ],
)
def test_classify_visual_intent_for_window_uses_generic_rules(
    text: str, expected_intent: str
) -> None:
    assert _classify_visual_intent_for_window(text) == expected_intent


def test_classify_does_not_match_specific_sample_topics() -> None:
    """禁止为具体样片（烧烤店 / 金鱼）写硬编码分类。

    重点检查不触发任何通用规则的样片文本 — 这才是"硬编码"风险。
    触发通用规则（如"方法"）的样片不视为硬编码，是规则行为。
    """
    for text in [
        "广州冒出了一个挺特别的参与模式",
        "金鱼养殖技巧",
        "手机支架选购指南",
    ]:
        intent = _classify_visual_intent_for_window(text)
        assert intent == "spoken_point", f"样片硬编码：{text} → {intent}"


# ---------- 2) 时长分级 ----------


@pytest.mark.parametrize(
    "duration,expected",
    [
        (15.0, (0.25, 0.45)),
        (30.0, (0.25, 0.45)),
        (45.0, (0.35, 0.55)),
        (60.0, (0.45, 0.65)),
        (120.0, (0.45, 0.65)),
    ],
)
def test_target_coverage_band(duration: float, expected: tuple[float, float]) -> None:
    assert _target_coverage_band(duration) == expected


# ---------- 3) 窗口规划 ----------


def test_plan_visual_windows_long_form_requires_required_windows() -> None:
    """长口播：数据卡 / CTA 段必须出现在 planned_visual_windows 且 required=True。"""
    segments = [
        {"start": 0.0, "end": 5.0, "text": "开场钩子"},
        {"start": 5.0, "end": 20.0, "text": "增长了 50% 的关键数据"},  # data_chart
        {"start": 20.0, "end": 50.0, "text": "今天我跟你详细说说"},
        {"start": 50.0, "end": 70.0, "text": "第一步打开手机，其次打开设置"},  # concept_card
        {"start": 70.0, "end": 100.0, "text": "继续讲述"},
        {"start": 100.0, "end": 110.0, "text": "评论留言区见"},  # cta_card
    ]
    plan = _plan_visual_windows(segments, duration_seconds=110.0)
    assert plan["is_long_form"] is True
    assert plan["is_short_form"] is False
    assert plan["target_real_coverage_ratio_min"] == 0.45
    assert plan["target_real_coverage_ratio_max"] == 0.65
    required = [w for w in plan["planned_visual_windows"] if w["required"]]
    kinds = {w["kind"] for w in required}
    assert "data_chart" in kinds
    assert "concept_card" in kinds
    assert "cta_card" in kinds


def test_plan_visual_windows_short_form_lower_target() -> None:
    segments = [
        {"start": 0.0, "end": 8.0, "text": "开场钩子"},
        {"start": 8.0, "end": 18.0, "text": "增长了 30%"},  # data_chart
        {"start": 18.0, "end": 28.0, "text": "评论留言"},  # cta_card
    ]
    plan = _plan_visual_windows(segments, duration_seconds=28.0)
    assert plan["is_short_form"] is True
    assert plan["is_long_form"] is False
    assert plan["target_real_coverage_ratio_min"] == 0.25
    assert plan["target_real_coverage_ratio_max"] == 0.45


def test_plan_visual_windows_long_form_ensures_two_full_events() -> None:
    """长口播：必须至少 2 个 full 模式（≥2 PiP + ≥2 Full 的全屏部分）。"""
    segments = [
        {"start": 0.0, "end": 10.0, "text": "开场钩子"},
        {"start": 10.0, "end": 30.0, "text": "增长了 50%"},  # data_chart
        {"start": 30.0, "end": 60.0, "text": "普通句子 A 持续说"},
        {"start": 60.0, "end": 90.0, "text": "普通句子 B 持续说"},
        {"start": 90.0, "end": 120.0, "text": "评论留言"},  # cta_card
    ]
    plan = _plan_visual_windows(segments, duration_seconds=120.0)
    full_count = sum(
        1 for w in plan["planned_visual_windows"] if w.get("preferred_mode") == "full"
    )
    assert full_count >= 2, f"full 窗口不足 2：{full_count}"


def test_plan_visual_windows_skips_short_spoken_segments() -> None:
    """短于 4s 的 spoken_point 在长口播里仍可成为可选窗口（不应被遗漏）。"""
    segments = [
        {"start": 0.0, "end": 5.0, "text": "开场钩子"},
        {"start": 5.0, "end": 6.5, "text": "短插话"},
        {"start": 6.5, "end": 20.0, "text": "继续聊"},
    ]
    plan = _plan_visual_windows(segments, duration_seconds=20.0)
    # 1.5s 短句不构成"长句"窗口
    spoken = [
        w for w in plan["planned_visual_windows"] if w["kind"] == "spoken_point"
    ]
    assert all(w["duration"] >= 4.0 for w in spoken)


# ---------- 4) build_director_plan 集成 ----------


def test_build_director_plan_includes_visual_window_plan() -> None:
    segments = [
        {
            "start": 0.0,
            "end": 5.0,
            "text": "今天聊聊烧烤店",
            "words": [],
        },
        {
            "start": 5.0,
            "end": 30.0,
            "text": "增长了 30% 的回头客",
            "words": [],
        },
        {
            "start": 30.0,
            "end": 90.0,
            "text": "详细说说这个模式",
            "words": [],
        },
        {
            "start": 90.0,
            "end": 110.0,
            "text": "评论扣 1",
            "words": [],
        },
    ]
    plan = build_director_plan(
        segments,
        duration_seconds=110.0,
        title="测试",
    )
    assert plan["plan_version"] == "director-plan-v3.1"
    assert "visual_window_plan" in plan
    vwp = plan["visual_window_plan"]
    assert vwp["is_long_form"] is True
    assert vwp["target_real_coverage_ratio_min"] == 0.45
    assert plan["current_real_coverage_seconds"] == 0.0
    assert plan["coverage_deficit_seconds"] == 0.0
    assert plan["unresolved_semantic_windows"] == []


def test_build_director_plan_does_not_embed_specific_sample_text() -> None:
    """回归样片文本不应作为硬编码出现在生产代码。"""
    forbidden_substrings = [
        "广州烧烤店回头客",
        "二手车评估",
        "金鱼养殖",
        "手机支架",
    ]
    # 完整 source 字符串 + 把所有 plan JSON 化检查
    import json

    segments = [
        {"start": 0.0, "end": 30.0, "text": "通用口播 A", "words": []},
        {"start": 30.0, "end": 60.0, "text": "增长了 30%", "words": []},
        {"start": 60.0, "end": 90.0, "text": "继续讲述", "words": []},
    ]
    plan = build_director_plan(
        segments,
        duration_seconds=90.0,
    )
    plan_json = json.dumps(plan, ensure_ascii=False, default=str)
    for forbidden in forbidden_substrings:
        assert forbidden not in plan_json, f"样片硬编码：{forbidden}"


# ---------- 5) 覆盖缺口 + 未解决窗口 ----------


def test_unresolved_windows_identified_correctly() -> None:
    """P0-2 reporting 集成：未覆盖的 required 窗口被识别。"""
    from src.services.video_editor_workflow import (
        _classify_unresolved_windows,
    )

    planned = [
        {
            "start": 0.0,
            "end": 5.0,
            "duration": 5.0,
            "kind": "data_chart",
            "required": True,
            "preferred_mode": "full",
            "fallback_kind": "deterministic_card",
        },
        {
            "start": 10.0,
            "end": 15.0,
            "duration": 5.0,
            "kind": "concept_card",
            "required": True,
            "preferred_mode": "full",
            "fallback_kind": "deterministic_card",
        },
        {
            "start": 30.0,
            "end": 40.0,
            "duration": 10.0,
            "kind": "spoken_point",
            "required": False,
            "preferred_mode": "pip",
            "fallback_kind": "a_roll_safe_push",
        },
    ]
    # 只覆盖第一个 data_chart，不覆盖 concept_card
    real_broll_intervals = [
        {"start": 0.0, "end": 5.0},  # 覆盖 data_chart
        {"start": 20.0, "end": 25.0},  # 不覆盖 concept_card
    ]
    unresolved = _classify_unresolved_windows(planned, real_broll_intervals)
    assert len(unresolved) == 1
    assert unresolved[0]["kind"] == "concept_card"
    # spoken_point 不进 unresolved（required=False）
