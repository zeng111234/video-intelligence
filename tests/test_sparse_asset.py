from src.services.sparse_asset import (
    SPARSE_ASSET_MODE,
    build_sparse_asset_plan,
    validate_sparse_asset_plan,
)


def _asset(asset_id: str, keywords: list[str]) -> dict:
    return {
        "asset_id": asset_id,
        "asset_origin": "approved_local_asset",
        "source_type": "approved_local_asset",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "keywords": keywords,
    }


def test_sparse_asset_plan_requires_concrete_semantics_and_keeps_other_segments_a_roll():
    plan = build_sparse_asset_plan(
        [
            {"start": 0.0, "end": 2.2, "text": "这家餐厅推出了新菜"},
            {"start": 3.0, "end": 5.0, "text": "这个方法特别重要"},
        ],
        [_asset("local-food", ["restaurant food preparation"])],
        duration_seconds=5.0,
        max_pip=5,
        max_full=0,
    )

    assert len(plan["asset_events"]) == 1
    event = plan["asset_events"][0]
    assert event["semantic_role"] in {
        "PRODUCT",
        "PROCESS",
        "STEP",
        "SCENE",
        "LOCATION",
        "EXAMPLE",
        "SPECIFIC_OBJECT",
        "BEFORE_AFTER",
    }
    assert event["semantic_text"] in event["source_text"]
    assert plan["source_policy"]["provider_search_calls"] == 0
    assert plan["source_policy"]["minimax_image_calls"] == 0


def test_sparse_asset_plan_enforces_pip_and_full_budgets():
    plan = {
        "mode": SPARSE_ASSET_MODE,
        "asset_events": [
            {
                "asset_id": "local-1",
                "start": 0.0,
                "end": 2.0,
                "mode": "pip",
                "layout": "PIP_SIDE_FLOAT",
                "semantic_role": "SCENE",
                "semantic_text": "餐厅",
                "source_text": "这家餐厅",
                "asset_origin": "approved_local_asset",
                "source_type": "approved_local_asset",
                "authorization_status": "confirmed",
                "semantic_match": 0.8,
                "specificity": 0.9,
                "visual_value": 0.8,
                "obstruction_risk": 0.2,
            }
            for _ in range(6)
        ],
    }
    errors = validate_sparse_asset_plan(plan)
    assert "pip_budget_exceeded" in errors


def test_sparse_asset_plan_rejects_unapproved_or_generic_assets():
    plan = build_sparse_asset_plan(
        [{"start": 0.0, "end": 2.0, "text": "这家餐厅推出新菜"}],
        [
            {
                **_asset("generic", ["restaurant"]),
                "manifest_role": "fallback_generic",
            },
            {
                **_asset("unapproved", ["restaurant food preparation"]),
                "authorization_status": "unverified",
            },
        ],
        duration_seconds=2.0,
        max_pip=5,
        max_full=0,
    )
    assert plan["asset_events"] == []
    assert any(
        entry["reason"] == "no_authorized_specific_local_match"
        for entry in plan["rejections"]
    )


def test_sparse_asset_plan_rejects_generic_restaurant_scene_for_private_domain_claim():
    plan = build_sparse_asset_plan(
        [
            {
                "start": 10.0,
                "end": 12.4,
                "text": "百分之八十的顾客还主动加了店里的私域",
            }
        ],
        [_asset("restaurant-scene", ["restaurant food preparation"])],
        duration_seconds=12.4,
        max_pip=5,
        max_full=1,
    )

    assert plan["asset_events"] == []
    assert any(
        entry.get("detail") == "spoken_text_has_only_generic_business_scene"
        for entry in plan["rejections"]
    )


def test_sparse_asset_plan_rejects_multi_industry_sentence_without_one_specific_subject():
    plan = build_sparse_asset_plan(
        [
            {
                "start": 90.0,
                "end": 93.0,
                "text": "除了餐饮水果生鲜美容便利店都能照搬",
            }
        ],
        [_asset("beauty-scene", ["beauty salon hands table"])],
        duration_seconds=93.0,
        max_pip=5,
        max_full=1,
    )

    assert plan["asset_events"] == []
    assert any(
        entry.get("detail") == "multi_industry_sentence_needs_a_specific_single_subject"
        for entry in plan["rejections"]
    )


def test_sparse_asset_plan_tries_the_next_asset_after_a_specific_asset_gate_rejection():
    plan = build_sparse_asset_plan(
        [
            {
                "start": 30.0,
                "end": 32.4,
                "text": "办完马上送一份招牌烧烤",
            }
        ],
        [
            _asset("restaurant-ordering", ["restaurant"]),
            _asset("restaurant-food", ["restaurant food preparation"]),
        ],
        duration_seconds=32.4,
        max_pip=5,
        max_full=1,
    )

    assert plan["asset_events"][0]["asset_id"] == "restaurant-food"


def test_sparse_asset_plan_accepts_concrete_customer_owned_process_with_generic_scene_word():
    plan = build_sparse_asset_plan(
        [
            {
                "start": 0.0,
                "end": 2.8,
                "text": "店里顾客通过门店小程序完成优惠券核销操作",
            }
        ],
        [_asset("owned-app-demo", ["门店小程序 优惠券 核销 操作"])],
        duration_seconds=3.0,
        max_pip=5,
        max_full=1,
    )

    assert len(plan["asset_events"]) == 1
    assert plan["asset_events"][0]["asset_id"] == "owned-app-demo"


def test_sparse_asset_plan_rejects_international_external_stock_even_when_licensed():
    asset = {
        **_asset("pexels-food", ["restaurant food preparation"]),
        "asset_origin": "stock_video_asset",
        "source_type": "provider_cache",
        "source_provider": "pexels",
        "domestic_context": "international",
        "license_name": "Pexels License",
        "publish_licensed": True,
    }
    plan = build_sparse_asset_plan(
        [{"start": 0.0, "end": 2.4, "text": "这家餐厅正在准备招牌烧烤"}],
        [asset],
        duration_seconds=2.4,
        max_pip=5,
        max_full=1,
    )

    assert plan["asset_events"] == []
    assert any(
        entry["reason"] == "no_authorized_specific_local_match"
        for entry in plan["rejections"]
    )


def test_sparse_asset_plan_allows_explicitly_domestic_external_cache_asset():
    asset = {
        **_asset("domestic-food", ["restaurant food preparation"]),
        "asset_origin": "stock_video_asset",
        "source_type": "provider_cache",
        "source_provider": "licensed-local-provider",
        "domestic_context": "domestic",
        "name": "domestic restaurant food preparation",
        "description": "Chinese restaurant kitchen preparing barbecue food",
        "publish_licensed": True,
    }
    plan = build_sparse_asset_plan(
        [{"start": 0.0, "end": 2.4, "text": "这家餐厅正在准备招牌烧烤"}],
        [asset],
        duration_seconds=2.4,
        max_pip=5,
        max_full=1,
    )

    assert len(plan["asset_events"]) == 1
    assert plan["asset_events"][0]["asset_id"] == "domestic-food"


def test_sparse_asset_plan_can_reuse_existing_generated_preview_only_when_enabled():
    asset = {
        "asset_id": "generated-database-preview",
        "asset_origin": "generated_image_asset",
        "source_type": "built_in_image_generation",
        "authorization_status": "generated_for_local_acceptance",
        "rights_status": "generated_for_local_acceptance",
        "semantic_binding": "客户数据库工作台",
        "name": "客户数据库工作台",
        "publish_licensed": False,
    }
    segments = [
        {"start": 0.0, "end": 2.4, "text": "客户资源应该沉淀在公司的数据库"}
    ]

    assert build_sparse_asset_plan(segments, [asset], duration_seconds=2.4)["asset_events"] == []
    plan = build_sparse_asset_plan(
        segments,
        [asset],
        duration_seconds=2.4,
        allow_generated_preview=True,
        max_full=1,
    )
    assert len(plan["asset_events"]) == 1
    event = plan["asset_events"][0]
    assert event["asset_id"] == asset["asset_id"]
    assert event["preview_only"] is True
    assert event["authorization_status"] == "generated_for_local_acceptance"
