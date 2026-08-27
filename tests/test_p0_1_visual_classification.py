"""P0-1: 4 个独立素材覆盖指标分类。

确保：
- 真实视频素材只算 Pexels / Pixabay / 用户上传且已授权的视频。
- 生成图绝不计入真实 B-roll，`generated_for_local_acceptance` 永久锁定。
- 程序生成信息卡（数据卡 / 流程卡 / CTA / 信息条）单独统计，绝不混入真实 B-roll。
- A-roll 推拉 / 字幕动画不计入真实 B-roll 也不计入信息卡。
- 4 个独立 ratio 互不替代；effective 不冒充真实覆盖。
"""

from __future__ import annotations

import pytest

from src.services.video_editor_workflow import (
    _GENERATED_AUTH_LOCK,
    _is_deterministic_card_item,
    _is_generated_image_broll,
    _is_generated_local_acceptance_asset,
    _is_real_stock_video_broll,
    _seconds_for_intervals,
)


# ---------- 1) 真实视频素材分类 ----------


def test_real_stock_video_accepts_pexels_video() -> None:
    asset = {
        "asset_id": "broll-001",
        "media_kind": "video",
        "source_provider": "pexels",
        "asset_origin": "stock_video_asset",
        "authorization_status": "confirmed",
        "publish_licensed": True,
    }
    assert _is_real_stock_video_broll(asset) is True


def test_real_stock_video_accepts_pixabay_video() -> None:
    asset = {
        "asset_id": "broll-002",
        "media_kind": "video",
        "source_provider": "pixabay",
        "asset_origin": "stock_video_asset",
        "authorization_status": "confirmed",
    }
    assert _is_real_stock_video_broll(asset) is True


def test_real_stock_video_accepts_user_uploaded_video() -> None:
    asset = {
        "asset_id": "upload-001",
        "media_kind": "video",
        "source_provider": "local_upload",
        "asset_origin": "local_uploaded_asset",
        "authorization_status": "confirmed",
    }
    assert _is_real_stock_video_broll(asset) is True


def test_real_stock_video_rejects_image() -> None:
    asset = {
        "asset_id": "img-001",
        "media_kind": "image",
        "source_provider": "pexels",
        "asset_origin": "stock_video_asset",
        "authorization_status": "confirmed",
    }
    assert _is_real_stock_video_broll(asset) is False


def test_real_stock_video_rejects_unconfirmed() -> None:
    asset = {
        "asset_id": "broll-003",
        "media_kind": "video",
        "source_provider": "pexels",
        "asset_origin": "stock_video_asset",
        "authorization_status": "pending",
    }
    assert _is_real_stock_video_broll(asset) is False


def test_real_stock_video_rejects_generated_image() -> None:
    """生成图即使有 pexels 来源也不算真实视频。"""
    asset = {
        "asset_id": "img-002",
        "media_kind": "image",
        "source_provider": "minimax",
        "asset_origin": "generated_image_asset",
        "authorization_status": "generated_for_local_acceptance",
    }
    assert _is_real_stock_video_broll(asset) is False


def test_real_stock_video_rejects_vector() -> None:
    """矢量 / 字幕动画 / 人物推拉不计入真实 B-roll。"""
    for asset_origin in ("vector_track", "semantic_layer", "subtitle_motion"):
        asset = {
            "asset_id": "fake",
            "media_kind": "video",
            "source_provider": "internal",
            "asset_origin": asset_origin,
            "authorization_status": "confirmed",
        }
        assert _is_real_stock_video_broll(asset) is False, asset_origin


# ---------- 2) 生成图分类与锁定 ----------


def test_generated_image_detected_by_origin() -> None:
    asset = {"asset_origin": "generated_image_asset"}
    assert _is_generated_image_broll(asset) is True


def test_generated_image_detected_by_provider() -> None:
    asset = {"source_provider": "built_in_image_generation"}
    assert _is_generated_image_broll(asset) is True


def test_generated_image_detected_by_rights_holder() -> None:
    asset = {"rights_holder": "generated_for_local_acceptance 标记"}
    assert _is_generated_image_broll(asset) is True


def test_generated_lock_constant() -> None:
    assert _GENERATED_AUTH_LOCK == "generated_for_local_acceptance"


def test_real_stock_video_rejects_even_if_user_pretends_confirmed() -> None:
    """防止 generated_for_local_acceptance 被偷偷改成 confirmed 冒充真实。"""
    asset = {
        "asset_id": "img-spoof",
        "media_kind": "video",
        "source_provider": "pexels",
        "asset_origin": "stock_video_asset",
        "authorization_status": "confirmed",
        "rights_status": "generated_for_local_acceptance",
        "rights_holder": "generated_for_local_acceptance",
    }
    # 因为 _is_generated_local_acceptance_asset 返回 True，先于 media_kind 检查
    assert _is_generated_local_acceptance_asset(asset) is True
    assert _is_real_stock_video_broll(asset) is False


# ---------- 3) 程序生成信息卡分类 ----------


@pytest.mark.parametrize(
    "renderer",
    [
        "data_visual_card",
        "semantic_info_band",
        "deterministic_card",
        "cta_card",
        "flow_card",
        "concept_card",
    ],
)
def test_deterministic_card_accepts_known_renderers(renderer: str) -> None:
    item = {"renderer": renderer, "start": 0.0, "end": 4.0}
    assert _is_deterministic_card_item(item) is True


@pytest.mark.parametrize(
    "asset_origin",
    ["vector_track", "semantic_layer", "deterministic_card_asset"],
)
def test_deterministic_card_accepts_known_origins(asset_origin: str) -> None:
    item = {"asset_origin": asset_origin, "start": 0.0, "end": 3.0}
    assert _is_deterministic_card_item(item) is True


def test_deterministic_card_rejects_reframe_event() -> None:
    """人物推拉是 A-roll 运动，不算程序生成卡。"""
    item = {
        "renderer": "safe_push",
        "asset_origin": "reframe_event",
        "start": 0.0,
        "end": 2.0,
    }
    assert _is_deterministic_card_item(item) is False


def test_deterministic_card_rejects_subtitle_motion() -> None:
    """字幕动画是字幕效果，不算程序生成卡。"""
    item = {
        "renderer": "subtitle_emphasis",
        "asset_origin": "subtitle_motion",
        "start": 0.0,
        "end": 1.5,
    }
    assert _is_deterministic_card_item(item) is False


def test_deterministic_card_rejects_real_broll() -> None:
    """真实 B-roll 也不算程序生成卡。"""
    item = {
        "asset_id": "broll-001",
        "media_kind": "video",
        "source_provider": "pexels",
        "asset_origin": "stock_video_asset",
        "authorization_status": "confirmed",
        "renderer": "stock_video",
    }
    assert _is_deterministic_card_item(item) is False


# ---------- 4) 4 个 ratio 互不替代 ----------


def test_seconds_for_intervals_ignores_non_mapping() -> None:
    items = [
        {"start": 0.0, "end": 4.0},
        None,
        {"start": 5.0, "end": 9.0},
    ]
    assert _seconds_for_intervals(items) == 8.0


def test_seconds_for_intervals_handles_negative_span_as_zero() -> None:
    items = [
        {"start": 5.0, "end": 3.0},  # end < start
        {"start": 0.0, "end": 2.0},
    ]
    assert _seconds_for_intervals(items) == 2.0


def test_four_categories_are_disjoint() -> None:
    """同一 asset 不能同时是真实 B-roll + 生成图 + 信息卡。"""
    pexels_video = {
        "asset_id": "a",
        "media_kind": "video",
        "source_provider": "pexels",
        "asset_origin": "stock_video_asset",
        "authorization_status": "confirmed",
    }
    assert _is_real_stock_video_broll(pexels_video) is True
    assert _is_generated_image_broll(pexels_video) is False

    generated_image = {
        "asset_id": "b",
        "media_kind": "image",
        "asset_origin": "generated_image_asset",
        "authorization_status": "generated_for_local_acceptance",
    }
    assert _is_real_stock_video_broll(generated_image) is False
    assert _is_generated_image_broll(generated_image) is True

    card = {"renderer": "data_visual_card", "start": 0.0, "end": 4.0}
    assert _is_real_stock_video_broll(card) is False
    assert _is_generated_image_broll(card) is False
    assert _is_deterministic_card_item(card) is True


def test_reframe_event_is_not_real_broll_and_not_card() -> None:
    """人物推拉是 A-roll 推镜头，不计入真实 B-roll 也不计入信息卡。

    它只应进入 effective_visual_coverage_seconds。
    """
    reframe = {
        "start": 0.0,
        "end": 2.0,
        "renderer": "safe_push",
        "asset_origin": "reframe_event",
    }
    # 没有 media_kind / source_provider / asset_id → 不是真实 B-roll
    assert _is_real_stock_video_broll(reframe) is False
    # 没有 generated_* 标记 → 不是生成图
    assert _is_generated_image_broll(reframe) is False
    # renderer 不在白名单 → 不是程序生成卡
    assert _is_deterministic_card_item(reframe) is False


def test_subtitle_motion_is_not_real_broll_and_not_card() -> None:
    """字幕动效（emphasis / entry motion）不计入真实 B-roll 也不计入信息卡。"""
    subtitle_motion = {
        "start": 0.0,
        "end": 1.5,
        "renderer": "subtitle_emphasis_motion",
        "asset_origin": "subtitle_motion",
    }
    assert _is_real_stock_video_broll(subtitle_motion) is False
    assert _is_generated_image_broll(subtitle_motion) is False
    assert _is_deterministic_card_item(subtitle_motion) is False
