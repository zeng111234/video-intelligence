from __future__ import annotations

import pytest

from src.asr_quality import (
    character_error_rate,
    extract_transcript_text,
    hotword_recall,
    number_token_accuracy,
)


def test_exported_transcript_timestamps_and_footer_are_removed() -> None:
    exported = """00:00:00
老百姓买二手车

00:00:03
一车一况一价

由 https://example.com 转录
"""

    assert extract_transcript_text(exported) == "老百姓买二手车\n一车一况一价"


def test_character_error_rate_ignores_spacing_and_punctuation() -> None:
    assert character_error_rate("你好，世界！", "你好 世界") == 0
    assert character_error_rate("速腾", "苏通") == pytest.approx(1.0)


def test_number_and_hotword_metrics_are_explicit() -> None:
    reference = "速腾报价 15.89 万，检测 300 项"
    hypothesis = "速腾报价 15.89 万，检测 30 项"

    assert number_token_accuracy(reference, hypothesis) == pytest.approx(0.5)
    assert hotword_recall(reference, hypothesis, ["速腾", "懂车帝"]) == 1.0
    assert hotword_recall(reference, hypothesis, ["不存在"]) is None
