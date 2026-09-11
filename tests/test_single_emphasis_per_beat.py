"""Regression tests for the shipped duplicate-text defect.

These target the observed failure, not the implementation.  In the shipped 33s
artifact (``data/video_edits/jy-clone-sparse-asset-v1.mp4``, built 2026-09-07)
the spoken line "更是你整个工厂品牌产品" produced three stacked visual beats on
one span -- ``keyword_emphasis("工厂品牌产品")``,
``keyword_emphasis("工厂品牌")`` and ``text_emphasis("工厂品牌产品")`` -- so the
same words were printed as a caption highlight *and* as an independent text
block.  A separate beat in the same clip stamped a success check mark on the
sentence "别让客户名单变成了库存".

The invariant enforced here is the product rule, not a style preference:
**each spoken beat materialises at most one text layer on screen.**
"""

from __future__ import annotations

from collections import defaultdict

from src.services.grammar_only import (
    annotate_transcript_segments,
    build_grammar_only_timeline,
    build_grammar_style_events,
)

_TEXT_BEARING = {"keyword_emphasis", "text_emphasis"}


def _is_materialised(event) -> bool:
    """Return whether the renderer paints this event as an independent layer."""

    return str(event.get("render_policy") or "").strip().lower() != "caption_only"


def _materialised_text_layers(events) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        if str(event.get("type") or "") not in _TEXT_BEARING:
            continue
        if not _is_materialised(event):
            continue
        grouped[str(event.get("source_segment_index"))].append(event)
    return grouped


def test_shipped_duplicate_text_beat_collapses_to_one_visual():
    """The exact span that printed twice in the shipped V3 artifact."""

    events = build_grammar_style_events(
        [
            {
                "source_segment_index": 7,
                "start": 26.26,
                "end": 29.7,
                "semantic_text": "工厂品牌产品",
                "source_text": "让客户认识的不只是某个业务员更是你整个工厂品牌产品",
                "semantic_roles": ["KEY_CLAIM", "PRODUCT"],
                "keyword_candidates": ["工厂品牌产品", "工厂品牌"],
                "importance": 0.9,
            }
        ]
    )

    # Exactly one text layer may reach the renderer for this beat.
    layers = _materialised_text_layers(events)
    assert len(layers["7"]) == 1, layers["7"]
    survivor = layers["7"][0]
    assert survivor["type"] == "text_emphasis"
    assert survivor["text"] == "工厂品牌产品"
    assert "工厂品牌产品" in survivor["source_text"]

    # The caption highlight is still fed, only demoted to caption-only.
    demoted = [
        event
        for event in events
        if str(event.get("render_policy") or "") == "caption_only"
    ]
    assert demoted
    assert all(
        event.get("semantic_text") in event["source_text"] for event in demoted
    )


def test_overlapping_keyword_candidates_keep_only_the_specific_phrase():
    """``业务员个人`` and ``业务员`` must not both accent the same beat."""

    annotations = annotate_transcript_segments(
        [{"start": 3.0, "end": 5.5, "text": "企业真正需要的不只是业务员个人维护"}],
        duration_seconds=8.0,
    )
    events = build_grammar_style_events(annotations)

    keywords = [
        event
        for event in events
        if event["type"] == "keyword_emphasis" and _is_materialised(event)
    ]
    assert len(keywords) <= 1, [item.get("semantic_text") for item in keywords]
    if keywords:
        assert keywords[0]["semantic_text"] == "业务员个人"


def test_no_more_than_one_text_layer_per_beat_on_unfamiliar_copy():
    """The rule holds on copy the fix was not written against."""

    annotations = annotate_transcript_segments(
        [
            {"start": 0.0, "end": 2.5, "text": "让客户认识的不只是某个业务员更是你整个工厂品牌产品"},
            {"start": 3.0, "end": 5.5, "text": "企业真正需要的不只是业务员个人维护"},
            {"start": 6.0, "end": 8.0, "text": "而是一套可以沉淀用户持续触达的营销机制"},
        ],
        duration_seconds=8.0,
    )
    events = build_grammar_style_events(annotations)

    for index, group in _materialised_text_layers(events).items():
        assert len(group) <= 1, (index, [item.get("semantic_text") for item in group])
        for left in range(len(group)):
            for right in range(left + 1, len(group)):
                first = str(group[left].get("semantic_text") or group[left].get("text") or "")
                second = str(group[right].get("semantic_text") or group[right].get("text") or "")
                assert not (first in second or second in first), (index, first, second)


def test_the_rule_holds_across_many_unfamiliar_sentences():
    """No sentence keeps two text layers, on seven unseen structures."""

    texts = [
        "充值与优惠叠加后每单只省八块钱",
        "这条路走不通因为损耗一直降不下来",
        "第一步先看清合同里的付款周期",
        "会员会变成免费宣传员带来新客",
        "老板不用费心想推广方案",
        "为什么同样的做法别人复购更高",
        "最后的结果是库存周转快了一倍",
    ]
    timeline = build_grammar_only_timeline(
        [
            {"start": index * 4.0, "end": index * 4.0 + 3.0, "text": text}
            for index, text in enumerate(texts)
        ],
        duration_seconds=len(texts) * 4.0,
    )

    layers = _materialised_text_layers(timeline["events"])
    for index, group in layers.items():
        assert len(group) <= 1, (index, [item.get("semantic_text") for item in group])
    # The constraint must not be satisfied by dropping every beat.
    assert len(layers) >= 4


def test_negative_conclusion_is_not_stamped_with_a_success_mark():
    """`别让客户名单变成了库存` is a warning, not an achievement."""

    timeline = build_grammar_only_timeline(
        [{"start": 40.0, "end": 43.0, "text": "第七别让客户名单变成了库存"}],
        duration_seconds=43.0,
    )

    assert not any(
        event.get("symbol") == "green_check" for event in timeline["events"]
    )


def test_a_genuine_positive_conclusion_keeps_its_success_mark():
    """The polarity guard must not suppress real achievements."""

    annotations = annotate_transcript_segments(
        [{"start": 0.0, "end": 2.0, "text": "所以大多数人都办了"}],
        duration_seconds=2.0,
    )
    assert "CONCLUSION" in annotations[0]["semantic_roles"]

    events = build_grammar_style_events(annotations)
    assert any(event.get("symbol") == "green_check" for event in events), events


def test_merged_beats_are_recorded_with_a_reason():
    """A QC pass must be able to explain how a beat was resolved."""

    text = "更是你整个工厂品牌产品"
    timeline = build_grammar_only_timeline(
        [{"source_segment_index": 0, "start": 0.0, "end": 2.0, "text": text}],
        duration_seconds=2.0,
        annotations=[
            {
                "source_segment_index": 0,
                "start": 0.0,
                "end": 2.0,
                "text": text,
                "source_text": text,
                "semantic_text": "工厂品牌产品",
                "semantic_roles": ["KEY_CLAIM", "PRODUCT"],
                "keyword_candidates": ["工厂品牌产品", "工厂品牌"],
                "importance": 0.9,
            }
        ],
    )

    merged = timeline["merged_emphasis_events"]
    assert merged, timeline["events"]
    # Every resolution carries a machine-readable reason: a beat is either
    # demoted to the caption or superseded by a more specific phrase.
    assert all(
        item.get("dropped_reason") or item.get("demoted_reason") for item in merged
    )
    assert all(item.get("source_segment_index") == 0 for item in merged)
    assert len(_materialised_text_layers(timeline["events"]).get("0", [])) == 1
