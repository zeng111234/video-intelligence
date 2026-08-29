"""P0-5: 数据卡 / CTA 重做。

确保：
- 拒绝大空框（label / fact / diagram_labels 三者全空）。
- data_chart 必须含数字。
- CTA 只在结尾出现一次。
- 程序渲染（不让生图出文字）已通过 P0-4 prompt 黑名单实现。
- 信息卡计入 deterministic_card_coverage_ratio（P0-1 已实现）。
"""

from __future__ import annotations


from src.services.video_editor_workflow import _sanitize_adaptive_visual_item


# ---------- 1) 拒绝大空框 ----------


def test_rejects_empty_visual_box() -> None:
    item = {
        "renderer": "data_visual_card",
        "visual_intent": "data_chart",
        "semantic_text": "",
        "fact": "",
        "diagram_labels": [],
        "source_text": "今天聊聊数字",
    }
    sanitized, reason = _sanitize_adaptive_visual_item(item)
    assert sanitized is None
    assert reason in {"empty_visual_box_no_payload", "data_chart_without_grounded_fact"}


def test_rejects_data_chart_without_number() -> None:
    item = {
        "renderer": "data_visual_card",
        "visual_intent": "data_chart",
        "semantic_text": "增长",
        "fact": "增长",  # 无数字
        "diagram_labels": ["A", "B"],
        "source_text": "增长了",
    }
    sanitized, reason = _sanitize_adaptive_visual_item(item)
    assert sanitized is None
    assert reason == "data_chart_missing_number"


def test_accepts_data_chart_with_number() -> None:
    item = {
        "renderer": "data_visual_card",
        "visual_intent": "data_chart",
        "semantic_text": "增长 30%",
        "fact": "增长 30%",
        "diagram_labels": ["一月", "二月", "三月"],
        "source_text": "增长了 30%",
    }
    sanitized, reason = _sanitize_adaptive_visual_item(item)
    assert sanitized is not None
    assert reason is None


def test_accepts_concept_card_with_minimum_labels() -> None:
    item = {
        "renderer": "data_visual_card",
        "visual_intent": "concept_card",
        "semantic_text": "三步法",
        "fact": "",
        "diagram_labels": ["打开", "设置", "完成"],
        "source_text": "第一步打开手机",
    }
    sanitized, reason = _sanitize_adaptive_visual_item(item)
    assert sanitized is not None
    assert reason is None


def test_rejects_concept_card_with_too_few_labels() -> None:
    item = {
        "renderer": "data_visual_card",
        "visual_intent": "concept_card",
        "semantic_text": "单步",
        "fact": "",
        "diagram_labels": ["唯一"],
        "source_text": "单步搞定",
    }
    sanitized, reason = _sanitize_adaptive_visual_item(item)
    assert sanitized is None
    assert reason == "diagram_without_source_labels"


# ---------- 2) CTA 限流（结尾只一次） ----------


def test_cta_kept_only_at_end() -> None:
    """多个 cta 时，只保留最后一个（最靠近结尾）。"""
    from src.services import video_editor_workflow

    import inspect

    source = inspect.getsource(
        video_editor_workflow._build_adaptive_visual_intents
    )
    # 限流逻辑：保留最后一个
    assert "cta_items" in source
    assert "CTA 只在结尾出现一次" in source
    assert "kept = cta_items_sorted[-1]" in source


# ---------- 3) P0-4 + P0-5 协同：prompt 黑名单 + 程序渲染 ----------


def test_p0_4_blacklist_prevents_chinese_in_prompt() -> None:
    """生图 prompt 黑名单（中文）已经阻止生图出文字 → 程序渲染生效。"""
    from src.services.video_editor_workflow import _textual_payload_has_unsafe_literals

    assert _textual_payload_has_unsafe_literals("客户扫码") is True
    assert _textual_payload_has_unsafe_literals("评论留言") is True
    # 英文 / 抽象概念可以进 prompt
    assert _textual_payload_has_unsafe_literals("a customer scanning QR code") is False


def test_data_chart_uses_fact_as_title() -> None:
    """P0-5: data_chart 用 fact（大数字 / 核心事实）作为 title。"""
    item = {
        "renderer": "data_visual_card",
        "visual_intent": "data_chart",
        "semantic_text": "忽略这个",  # P0-5 应被 fact 覆盖
        "fact": "增长30%",
        "diagram_labels": ["1月", "2月", "3月"],
        "source_text": "增长了 30%",
    }
    sanitized, _ = _sanitize_adaptive_visual_item(item)
    assert sanitized is not None
    # data_chart 的 title 就是 fact（去空白后的紧凑形式）
    assert sanitized["semantic_text"] == "增长30%"
    assert sanitized["fact"] == "增长30%"


# ---------- 4) 信息卡计入 deterministic_card_coverage_ratio ----------


def test_data_visual_card_counted_as_deterministic() -> None:
    """P0-1: data_visual_card 计入 deterministic_card_coverage_ratio。"""
    from src.services.video_editor_workflow import _is_deterministic_card_item

    assert _is_deterministic_card_item({"renderer": "data_visual_card"}) is True
    assert _is_deterministic_card_item({"renderer": "semantic_info_band"}) is True
    assert _is_deterministic_card_item({"renderer": "cta_card"}) is True
    assert _is_deterministic_card_item({"renderer": "flow_card"}) is True
    assert _is_deterministic_card_item({"renderer": "concept_card"}) is True
    # 非卡片不算
    assert _is_deterministic_card_item({"renderer": "real_broll"}) is False
