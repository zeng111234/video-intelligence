from pathlib import Path

from PIL import Image

from src.services.editorial_stickers import (
    build_editorial_sticker_events,
    render_editorial_sticker,
)


def test_events_are_transcript_grounded_and_sparse_for_unseen_copy(tmp_path: Path):
    segments = [
        {"start": 0, "end": 4, "source_text": "陌生主题产生结果", "semantic_text": "产生结果", "semantic_roles": ["CONCLUSION"], "keyword_candidates": ["产生结果"]},
        {"start": 12, "end": 16, "source_text": "步骤一完成操作", "semantic_text": "步骤一", "semantic_roles": ["STEP"], "keyword_candidates": ["步骤一"]},
        {"start": 24, "end": 28, "source_text": "比例达到目标", "semantic_text": "比例", "semantic_roles": ["PERCENT"], "keyword_candidates": ["比例"]},
        {"start": 36, "end": 40, "source_text": "请点击查看详情", "semantic_text": "查看详情", "semantic_roles": ["CTA"], "keyword_candidates": ["查看详情"]},
        {"start": 48, "end": 52, "source_text": "这个做法存在风险", "semantic_text": "存在风险", "semantic_roles": ["WARNING"], "keyword_candidates": ["存在风险"]},
        {"start": 60, "end": 64, "source_text": "完全陌生的行业内容", "semantic_text": "行业内容", "semantic_roles": ["PRODUCT"], "keyword_candidates": ["行业内容"]},
    ]

    events = build_editorial_sticker_events(segments, duration_seconds=70, max_events=5)

    assert 0 < len(events) <= 5
    assert all(event["semantic_text"] in event["source_text"] for event in events)
    assert all(event["asset_category"] == "custom_semantic_sticker" for event in events)
    assert all(event["source"] == "VideoInsight project-owned editorial vector" for event in events)
    assert all(
        events[index + 1]["start"] - events[index]["start"] >= 8
        for index in range(len(events) - 1)
    )


def test_editorial_sticker_is_a_custom_rgba_vector(tmp_path: Path):
    output = tmp_path / "sticker.png"
    render_editorial_sticker({"style_id": "editorial_coupon"}, output)

    with Image.open(output) as image:
        assert image.mode == "RGBA"
        assert image.size == (300, 220)
        assert image.getbbox() is not None


def test_offer_and_coupon_visuals_are_transcript_grounded():
    segments = [
        {
            "start": 1,
            "end": 4,
            "source_text": "充值100送10元，充值200送30元",
            "semantic_text": "充值100送10元",
            "semantic_roles": ["PRICE", "NUMBER", "KEY_CLAIM"],
        },
        {
            "start": 12,
            "end": 15,
            "source_text": "可以拿到六张无门槛优惠券",
            "semantic_text": "六张",
            "semantic_roles": ["NUMBER", "KEY_CLAIM"],
        },
    ]
    events = build_editorial_sticker_events(segments, duration_seconds=30, max_events=5)
    assert [event["style_id"] for event in events] == [
        "editorial_offer_compare",
        "editorial_coupon",
    ]
    assert all(event["grounded_in_text"] for event in events)
    assert events[0]["offer_values"] == ["100", "10元"]
    assert events[1]["coupon_count"] == 6


def test_generic_claim_does_not_become_offer_compare():
    events = build_editorial_sticker_events(
        [{
            "start": 1,
            "end": 4,
            "source_text": "附近居民都成了回头客",
            "semantic_text": "回头客",
            "semantic_roles": ["KEY_CLAIM", "CONCLUSION"],
        }],
        duration_seconds=10,
        max_events=5,
    )
    assert events[0]["style_id"] == "editorial_positive"


def test_sticker_never_outlives_its_source_segment():
    events = build_editorial_sticker_events(
        [{
            "start": 20.0,
            "end": 21.4,
            "source_text": "充100送10块",
            "semantic_text": "充100送10块",
            "semantic_roles": ["PRICE", "NUMBER", "KEY_CLAIM"],
        }],
        duration_seconds=30,
        max_events=5,
    )
    assert events
    assert events[0]["end"] <= 21.4


def test_offer_values_ignore_years_ordinals_and_thousands_separators():
    cases = [
        ("2026年充值800送120元", ["800", "120元"]),
        ("第2次充值500送80元", ["500", "80元"]),
        ("充值1,000送100元", ["1000", "100元"]),
    ]
    for source_text, expected in cases:
        events = build_editorial_sticker_events(
            [{
                "start": 1,
                "end": 5,
                "source_text": source_text,
                "semantic_text": source_text,
                "semantic_roles": ["PRICE", "NUMBER", "KEY_CLAIM"],
            }],
            duration_seconds=10,
            max_events=5,
        )
        assert events[0]["style_id"] == "editorial_offer_compare"
        assert events[0]["offer_values"] == expected


def test_invalid_or_non_offer_promotions_are_safely_suppressed():
    cases = [
        "充值100不送10元",
        "以前充值300送50，现在取消了",
        "训练2小时奖励自己跑完5公里",
    ]
    for source_text in cases:
        events = build_editorial_sticker_events(
            [{
                "start": 1,
                "end": 5,
                "source_text": source_text,
                "semantic_text": source_text,
                "semantic_roles": ["PRICE", "NUMBER", "KEY_CLAIM"],
            }],
            duration_seconds=10,
            max_events=5,
        )
        assert all(event["style_id"] != "editorial_offer_compare" for event in events)


def test_coupon_requires_explicit_coupon_term_and_valid_integer_count():
    cases = [
        ("证券交易系统支持实时行情", False, None),
        ("本次发放1.5张优惠券", False, None),
        ("本次发放-3张优惠券", False, None),
        ("现在还剩0张优惠券", False, None),
        ("本次发放二十一张优惠券", False, None),
        ("本次发放十二张优惠券", True, 12),
    ]
    for source_text, expected_event, expected_count in cases:
        events = build_editorial_sticker_events(
            [{
                "start": 1,
                "end": 5,
                "source_text": source_text,
                "semantic_text": source_text,
                "semantic_roles": ["NUMBER", "PRODUCT", "KEY_CLAIM"],
            }],
            duration_seconds=10,
            max_events=5,
        )
        coupon_events = [event for event in events if event["style_id"] == "editorial_coupon"]
        assert bool(coupon_events) is expected_event
        if expected_event:
            assert coupon_events[0]["coupon_count"] == expected_count
