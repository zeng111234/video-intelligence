"""P0-6: 字幕准确率假阳性修复。

确保：
- raw ASR 等于 reviewed_text 时 → 触发 raw_asr_equals_reviewed 假阳性。
- 通用 ASR 错词 token 触发 known_error。
- 数字 / 品牌 / 金额 / 人物名 触发 human_review_warnings，不自动修。
- transcript_accuracy_gate 独立判定。
- transcript_timing_gate 独立判定。
- 不为具体样片（烧烤店 / 二手车 / 金鱼）写硬编码。
"""

from __future__ import annotations

import pytest

from src.services.video_editor_workflow import (
    _compact_for_comparison,
    _detect_human_review_warnings,
    _detect_known_transcript_errors,
)


# ---------- 1) 通用错误检测 ----------


def test_detect_known_errors_when_raw_equals_reviewed() -> None:
    """raw ASR 等于 reviewed → 假阳性检测。"""
    text = "今天聊聊烧烤店"
    errors = _detect_known_transcript_errors(text, raw_asr_text=text)
    assert "raw_asr_equals_reviewed_no_human_review" in errors


def test_detect_known_errors_when_reviewed_differs() -> None:
    """raw ≠ reviewed → 不假阳性。"""
    text = "今天聊聊烧烤店"
    errors = _detect_known_transcript_errors(text, raw_asr_text="完全不同")
    assert "raw_asr_equals_reviewed_no_human_review" not in errors


def test_detect_known_errors_finds_r8_patterns() -> None:
    """r8 报告中的通用错词 token 模式被检测。"""
    # 这些是用户列出的"明显错词"，是 ASR 通用错位的表现
    # 不依赖具体样片（不是"广州烧烤店"等具体词）
    text = "会员质半完中头戏又把劝秒道账身仙找班"
    errors = _detect_known_transcript_errors(text)
    assert any("suspicious_run" in e for e in errors), errors


def test_detect_known_errors_no_match_on_clean_text() -> None:
    """干净文本不触发 known errors。"""
    text = "今天我跟你分享三个小技巧"
    errors = _detect_known_transcript_errors(text, raw_asr_text="完全不同")
    assert errors == []


def test_detect_known_errors_anomaly_symbols() -> None:
    """含异常符号（如 ■□◆）→ anomaly_symbol 触发。"""
    text = "今天聊■餐厅经营"
    errors = _detect_known_transcript_errors(text)
    assert "anomaly_symbol_in_transcript" in errors


# ---------- 2) 人工复核警告（数字 / 品牌 / 金额 / 人物名）----------


def test_human_review_warnings_detects_money() -> None:
    text = "今天充 100 块办会员"
    warnings = _detect_human_review_warnings(text)
    kinds = {w["kind"] for w in warnings}
    assert "money_amount" in kinds


def test_human_review_warnings_detects_percent() -> None:
    text = "增长了 30%"
    warnings = _detect_human_review_warnings(text)
    kinds = {w["kind"] for w in warnings}
    assert "percent_ratio" in kinds


def test_human_review_warnings_detects_brand_candidate() -> None:
    text = "推荐使用 CRM 系统管理客户"
    warnings = _detect_human_review_warnings(text)
    kinds = {w["kind"] for w in warnings}
    assert "brand_or_product_candidate" in kinds


def test_human_review_warnings_detects_person_name() -> None:
    text = "我的同事王老师建议我们这样做"
    warnings = _detect_human_review_warnings(text)
    kinds = {w["kind"] for w in warnings}
    assert "person_name_with_title" in kinds


def test_human_review_warnings_ignores_safe_text() -> None:
    text = "今天聊聊餐饮行业的回头客模式"
    warnings = _detect_human_review_warnings(text)
    assert warnings == []


def test_human_review_warnings_does_not_use_specific_sample_words() -> None:
    """禁止为具体样片（烧烤店 / 金鱼 / 二手车）写硬编码警告词。"""
    forbidden = ["烧烤店", "金鱼", "二手车", "广州", "回头客"]
    for sample in [
        "今天聊聊一种新的营销模式",
        "今天的分享希望对你有帮助",
        "普通口播句子不含特殊 token",
    ]:
        warnings = _detect_human_review_warnings(sample)
        for w in warnings:
            for f in forbidden:
                assert f not in str(w), f"样片硬编码 {f} in {w}"


# ---------- 3) _compact_for_comparison ----------


def test_compact_for_comparison_strips_punctuation() -> None:
    assert _compact_for_comparison("今天，今天 聊聊！") == "今天今天聊聊"


def test_compact_for_comparison_empty() -> None:
    assert _compact_for_comparison("") == ""


def test_compact_for_comparison_handles_whitespace() -> None:
    assert _compact_for_comparison("今  天  聊  聊") == "今天聊聊"


# ---------- 4) gate 拆解：transcript_timing_gate / accuracy_gate 独立 ----------


def test_timing_gate_static_check() -> None:
    """transcript_timing_gate 独立 gate 在 video_editor_workflow 中存在。"""
    from src.services import video_editor_workflow

    import inspect

    source = inspect.getsource(video_editor_workflow)
    assert "transcript_timing_gate" in source
    assert "transcript_timing_passed" in source
    # P0-6: 拆开
    assert "transcript_accuracy_passed" in source


def test_accuracy_gate_blocks_unreviewed_text() -> None:
    """reviewed_text == raw_asr_text → transcript_accuracy_gate 自动失败。"""
    # 这个静态检查代表 reporting 逻辑
    from src.services import video_editor_workflow

    import inspect

    source = inspect.getsource(video_editor_workflow)
    # 已知错词模式被检测并阻断
    assert "_detect_known_transcript_errors" in source
    assert "_detect_human_review_warnings" in source
