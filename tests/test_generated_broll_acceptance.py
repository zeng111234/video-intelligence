from __future__ import annotations

from pathlib import Path

import pytest

from scripts.render_generated_broll_acceptance import _attach_real_word_timestamps


_OPTIONAL_ASR_EVIDENCE = (
    Path(__file__).resolve().parents[1]
    / "work/auto-fine-cut-v1-20260822/asr/assembled-word-timestamps-large-v3-turbo.json"
)


def _words(text: str, start: float) -> list[dict[str, object]]:
    result = []
    cursor = start
    for character in text:
        result.append({"word": character, "start": cursor, "end": cursor + 0.1})
        cursor += 0.1
    return result


@pytest.mark.skipif(
    not _OPTIONAL_ASR_EVIDENCE.is_file(),
    reason="requires optional historical work/ ASR evidence excluded from handoff",
)
def test_local_word_timestamp_mapping_records_asr_text_normalization():
    reviewed = [
        {"start": 0, "end": 1, "text": "你公司的客户资源"},
        {"start": 1, "end": 2, "text": "业务员呢他一旦离职"},
        {"start": 2, "end": 3, "text": "企业真正需要的"},
        {"start": 3, "end": 4, "text": "数影霸屏呢"},
    ]
    raw = (
        _words("以公司的客户资源", 0)
        + _words("业务员呢他一旦离职", 1)
        + _words("企业真正需要的", 2)
        + _words("顺应八平呢", 3)
    )

    attached, mapping = _attach_real_word_timestamps(
        reviewed,
        {"segments": [{"words": raw}], "model": "test-model", "word_count": len(raw)},
    )

    assert mapping["matched"] is True
    assert mapping["matched_segment_count"] == 4
    assert mapping["normalized_substitutions"] == {
        "以->你": 1,
        "顺应八平->数影霸屏": 1,
    }
    assert attached[0]["words"][0]["text"] == "你"
    assert attached[3]["words"][0]["text"] == "数影霸屏"


def test_local_word_timestamp_mapping_fails_closed_on_unmatched_review_text():
    attached, mapping = _attach_real_word_timestamps(
        [{"start": 0, "end": 1, "text": "审核文案"}],
        {"segments": [{"words": _words("不同文案", 0)}]},
    )

    assert mapping["matched"] is False
    assert mapping["matched_segment_count"] == 0
    assert attached[0].get("words") is None
