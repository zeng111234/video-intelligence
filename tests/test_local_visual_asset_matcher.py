import json
from pathlib import Path

from src.services.local_visual_asset_matcher import (
    append_manifest_asset,
    asset_visual_concepts,
    asset_publish_claim_allowed,
    build_keyword_generation_plan,
    match_local_visual_asset,
    semantic_broll_gate_passed,
    semantic_conflicts,
    visual_search_query,
    visual_asset_priority,
)


def _asset(asset_id: str, binding: str, *, keywords=(), role="fullscreen_broll"):
    return {
        "asset_id": asset_id,
        "semantic_binding": binding,
        "keywords": list(keywords),
        "manifest_role": role,
        "asset_origin": "generated_image_asset",
        "authorization_status": "generated_for_local_acceptance",
        "publish_licensed": False,
    }


def test_precise_semantic_binding_beats_generic_fallback_deterministically():
    assets = [
        _asset("fallback-b", "通用办公与执行", keywords=("办公",), role="fallback_broll"),
        _asset("precise-a", "客户数据库", keywords=("数据", "客户")),
    ]

    result = match_local_visual_asset("客户数据库持续沉淀", assets)

    assert result is not None
    assert result["asset_id"] == "precise-a"
    assert result["match_type"] == "precise"
    assert any("semantic_binding" in reason for reason in result["match_reason"])


def test_keyword_match_uses_manifest_keywords_when_binding_is_not_literal():
    result = match_local_visual_asset(
        "业务员离职后客户关系还要持续触达",
        [_asset("service", "客户服务与持续触达", keywords=("触达", "响应"))],
    )

    assert result is not None
    assert result["asset_id"] == "service"
    assert any(reason.startswith("keywords:") for reason in result["match_reason"])


def test_generic_fallback_is_second_choice_and_not_random_rotation():
    assets = [
        _asset("fallback-z", "通用团队协作", keywords=(), role="fallback_pip_broll"),
        _asset("fallback-a", "通用办公与执行", keywords=(), role="fallback_broll"),
    ]

    result = match_local_visual_asset("没有登记语义的口播", assets)

    assert result is not None
    assert result["match_type"] == "generic_fallback"
    assert result["asset_id"] == "fallback-a"


def test_no_match_returns_safe_degradation_instead_of_inventing_an_asset():
    result = match_local_visual_asset(
        "完全没有对应素材的主题",
        [_asset("precise", "客户数据库", keywords=("客户",))],
        allow_generic_fallback=False,
    )

    assert result is None


def test_multi_industry_copy_does_not_accept_a_single_restaurant_clip():
    """P0-收口 2026-08-31: a multi-industry query must NOT collapse into
    a hardcoded English phrase, and a single restaurant clip must still
    be rejected by the semantic gate (the gate's job, not the search
    string's)."""
    original_query = "除了餐饮水果生鲜美容便利店都能使用"
    query = visual_search_query(original_query)

    # Generic rule: literal text is preserved, no canned English string.
    assert "餐饮" in query
    assert "便利店" in query
    assert query != "multi-industry retail business marketing"
    assert "multi-industry" not in query

    result = match_local_visual_asset(
        query,
        [
            _asset(
                "barbecue",
                "烧烤餐厅顾客",
                keywords=("barbecue", "restaurant", "grill"),
            )
        ],
        allow_generic_fallback=False,
    )

    assert result is None


def test_restaurant_scene_does_not_satisfy_specific_loyalty_request():
    restaurant_only = {
        "asset_id": "restaurant-only",
        "source_type": "provider_cache",
        "source_provider": "pexels",
        "provider_title": "a person preparing a grilled hamburger",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "asset_origin": "stock_video_asset",
    }

    assert match_local_visual_asset(
        "restaurant customer loyalty program smartphone",
        [restaurant_only],
        allow_generic_fallback=False,
    ) is None


def test_generated_asset_never_passes_publish_rights_gate():
    assert asset_publish_claim_allowed(_asset("generated", "客户数据库")) is False
    assert asset_publish_claim_allowed(
        {
            "asset_id": "pexels",
            "asset_origin": "stock_video_asset",
            "source_provider": "pexels",
            "authorization_status": "confirmed",
            "publish_licensed": True,
        }
    ) is True


def test_explicit_domestic_real_asset_beats_precise_international_stock():
    domestic = {
        "asset_id": "domestic-real",
        "semantic_binding": "客户数据库",
        "domestic_context": "domestic",
        "authorization_status": "confirmed",
        "asset_origin": "local_uploaded_asset",
    }
    international = {
        "asset_id": "pexels-precise",
        "semantic_binding": "客户数据库",
        "domestic_context": "international",
        "source_provider": "pexels",
        "authorization_status": "confirmed",
        "publish_licensed": True,
    }
    result = match_local_visual_asset("客户数据库", [international, domestic])
    assert result is not None
    assert result["asset_id"] == "domestic-real"
    assert visual_asset_priority(domestic) < visual_asset_priority(international)


def test_explicit_domestic_generated_is_before_international_but_not_publishable():
    domestic_generated = _asset("domestic-generated", "客户关系", role="fullscreen_broll")
    domestic_generated["domestic_context"] = "domestic"
    international = {
        "asset_id": "pixabay-stock",
        "semantic_binding": "客户关系",
        "domestic_context": "international",
        "source_provider": "pixabay",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "asset_origin": "stock_video_asset",
    }
    result = match_local_visual_asset(
        "客户关系",
        [international, domestic_generated],
    )
    assert result is not None
    assert result["asset_id"] == "domestic-generated"
    assert asset_publish_claim_allowed(result) is False


def test_keyword_generation_requires_explicit_switch_and_does_not_call_provider():
    disabled = build_keyword_generation_plan(
        "客户数据库",
        env={"VIDEO_IMAGE_MANIFEST_PATH": "manifest.json"},
    )
    enabled = build_keyword_generation_plan(
        "客户数据库",
        env={
            "VIDEO_IMAGE_AUTOGENERATE_ON_KEYWORD_MATCH": "true",
            "VIDEO_IMAGE_MANIFEST_PATH": "manifest.json",
        },
    )

    assert disabled["action"] == "safe_degradation"
    assert disabled["provider_calls"] == 0
    assert enabled["action"] == "awaiting_explicit_quote_and_confirmation"
    assert enabled["provider_calls"] == 0


def test_manifest_writeback_is_explicit_and_records_hash(tmp_path: Path):
    image_path = tmp_path / "generated.png"
    image_path.write_bytes(b"local-image-bytes")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"assets": []}), encoding="utf-8")

    entry = append_manifest_asset(
        manifest_path,
        asset_id="generated-keyword-01",
        source_path=image_path,
        role="fullscreen_broll",
        semantic_binding="客户数据库",
        keywords=("客户", "数据库"),
    )

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert entry["asset_id"] == "generated-keyword-01"
    assert len(entry["sha256"]) == 64
    assert saved["assets"][0]["keywords"] == ["客户", "数据库"]


def test_black_swan_provider_cache_cannot_match_customer_database():
    swan = {
        "asset_id": "broll-black-swan",
        "name": "Majestic black swans on tranquil lake",
        "original_name": "black-swan-lake.mp4",
        "source_url": "https://www.pexels.com/video/black-swans-lake/",
        "source_type": "provider_cache",
        "source_provider": "pexels",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "asset_origin": "stock_video_asset",
        "semantic_query": "客户数据库",
        "keywords": ["客户数据库"],
    }

    assert match_local_visual_asset(
        "客户数据库",
        [swan],
        allow_generic_fallback=False,
    ) is None
    assert "swan" in semantic_conflicts("客户数据库", swan)
    assert "lake" in semantic_conflicts("客户数据库", swan)


def test_vehicle_dashboard_is_not_business_database_evidence():
    vehicle_clip = {
        "asset_id": "broll-vehicle-dashboard",
        "source_type": "provider_cache",
        "source_provider": "pexels",
        "provider_title": "person driving with GPS on a phone mounted on the dashboard",
        "source_url": "https://www.pexels.com/video/phone-mounted-on-car-dashboard/",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "asset_origin": "stock_video_asset",
    }

    assert "database" not in asset_visual_concepts(vehicle_clip)
    assert "office_data" not in asset_visual_concepts(vehicle_clip)
    assert match_local_visual_asset(
        "客户 CRM 数据库管理",
        [vehicle_clip],
        allow_generic_fallback=False,
    ) is None


def test_vehicle_phone_use_can_match_product_demo_without_becoming_crm_evidence():
    phone_use = {
        "asset_id": "broll-vehicle-phone-use",
        "source_type": "provider_cache",
        "source_provider": "pexels",
        "provider_title": "person using an application on his phone for guidance while driving",
        "source_url": "https://www.pexels.com/video/person-using-an-application-on-his-phone-for-guidance-while-driving-3010433/",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "asset_origin": "stock_video_asset",
    }

    result = match_local_visual_asset(
        "拧一下就能锁紧的车载手机支架，使用手机导航",
        [phone_use],
        allow_generic_fallback=False,
    )
    assert result is not None
    assert any(
        "vehicle_navigation_product_use" in reason
        for reason in result["match_reason"]
    )
    assert match_local_visual_asset(
        "客户 CRM 数据库管理",
        [phone_use],
        allow_generic_fallback=False,
    ) is None


def test_vehicle_scene_evidence_does_not_become_business_database_evidence():
    used_car = {
        "asset_id": "broll-used-car-scene",
        "source_provider": "pexels",
        "source_type": "provider_cache",
        "provider_title": "used car buyer inspecting a vehicle at a dealership",
        "source_url": "https://www.pexels.com/video/used-car-dealership-inspection/",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "asset_origin": "stock_video_asset",
    }

    assert "vehicle_scene" in asset_visual_concepts(used_car)
    assert match_local_visual_asset(
        "二手车买家检查车况和报价",
        [used_car],
        allow_generic_fallback=False,
    ) is not None
    assert match_local_visual_asset(
        "客户 CRM 数据库管理",
        [used_car],
        allow_generic_fallback=False,
    ) is None


def test_plain_smartphone_footage_is_not_vehicle_navigation_evidence():
    phone_clip = {
        "asset_id": "broll-plain-smartphone",
        "source_provider": "pexels",
        "source_type": "provider_cache",
        "provider_title": "person looking at a food delivery menu on a smartphone",
        "source_url": "https://www.pexels.com/video/food-delivery-menu-smartphone/",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "asset_origin": "stock_video_asset",
    }

    assert "vehicle_navigation" not in asset_visual_concepts(phone_clip)
    assert match_local_visual_asset(
        "二手车买家检查车况",
        [phone_clip],
        allow_generic_fallback=False,
    ) is None


def test_business_dashboard_remains_database_evidence_without_vehicle_context():
    dashboard = {
        "asset_id": "crm-dashboard",
        "source_provider": "pexels",
        "source_type": "provider_cache",
        "name": "CRM analytics dashboard",
        "title": "business data dashboard",
    }

    assert "database" in asset_visual_concepts(dashboard)


def test_zero_score_authorized_asset_cannot_pass_semantic_broll_gate():
    authorized_without_evidence = {
        "asset_id": "broll-authorized-unknown",
        "source_provider": "pexels",
        "source_type": "provider_cache",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "asset_origin": "stock_video_asset",
    }

    assert match_local_visual_asset(
        "客户数据库",
        [authorized_without_evidence],
        allow_generic_fallback=False,
    ) is None
    assert semantic_broll_gate_passed(
        [
            {
                **authorized_without_evidence,
                "semantic_query": "客户数据库",
                "match_score": 0,
                "match_reason": ["no_local_semantic_metadata"],
            }
        ]
    ) is False


def test_business_query_is_normalized_to_concrete_visual_search_terms():
    """P0-收口 2026-08-31: the old assertion was a hardcoded answer
    (``"CRM" / "customer database" / "business analytics"``).  Replace
    it with the general rule: the caller's own text is preserved AND
    the helper does not silently inject the old canned search string.
    """
    search_query = visual_search_query("客户数据库与客户资源沉淀")

    # Generic rule: the literal caller's text is returned.
    assert "客户数据库" in search_query
    # Generic rule: the historical hardcoded CRM/dashboard phrase must
    # NOT be silently injected (otherwise we are back to "answer-driven"
    # code that collapses every Chinese business query into one search).
    assert "CRM dashboard" not in search_query
    assert "customer database business analytics" not in search_query


def test_restaurant_query_is_normalized_to_food_specific_visual_search_terms():
    """P0-收口 2026-08-31: the old assertion was a hardcoded answer
    (``"barbecue restaurant" / "food preparation" / "diners"``).
    Replace it with the general rule: the caller's own text is
    preserved AND the historical hardcoded ``barbecue restaurant
    grill food preparation diners`` must NOT be silently injected
    (that was the single most obvious "answer-driven" search string
    the cross-review called out).
    """
    search_query = visual_search_query("街上有家烧烤店，附近居民都成了回头客")

    # Generic rule: the literal caller's text is returned.
    assert "烧烤店" in search_query
    # Generic rule: no silent hardcoded answer injection.
    assert "barbecue restaurant grill food preparation diners" not in search_query
    assert "barbecue restaurant" not in search_query


def test_specific_english_provider_query_keeps_subject_action_and_context():
    search_query = visual_search_query(
        "restaurant customer using smartphone QR code"
    )

    assert "smartphone" in search_query.casefold()
    assert "qr" in search_query.casefold()
    assert "barbecue restaurant grill" not in search_query.casefold()


def test_specific_relationship_query_is_not_collapsed_to_dashboard_search():
    search_query = visual_search_query(
        "restaurant customer referral marketing"
    )

    assert search_query.casefold() == "restaurant customer referral marketing"


def test_qr_scan_is_a_concrete_relationship_visual_concept():
    asset = {
        "asset_id": "broll-qr",
        "source_type": "provider_cache",
        "source_provider": "pexels",
        "source_url": "https://www.pexels.com/video/woman-taking-photo-of-the-qr-code-on-the-box-7287312/",
        "provider_title": "woman taking photo of the qr code",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "asset_origin": "stock_video_asset",
    }

    result = match_local_visual_asset(
        "customer scanning QR code smartphone",
        [asset],
        allow_generic_fallback=False,
    )

    assert result is not None
    assert result["match_score"] > 0
    assert any("qr_scan" in reason for reason in result["match_reason"])


def test_real_barbecue_food_clip_matches_restaurant_query_from_source_url():
    food_clip = {
        "asset_id": "broll-ribs",
        "source_type": "provider_cache",
        "source_provider": "pexels",
        "source_url": "https://www.pexels.com/video/preparing-keto-back-ribs-meal-18904820/",
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "asset_origin": "stock_video_asset",
        "semantic_query": "barbecue restaurant grill food preparation diners",
        "keywords": ["barbecue restaurant grill food preparation diners"],
    }

    result = match_local_visual_asset(
        "街上有家烧烤店才开一个月",
        [food_clip],
        allow_generic_fallback=False,
    )

    assert result is not None
    assert result["asset_id"] == "broll-ribs"
    assert result["match_score"] > 0
    assert any("concrete_visual_intersection:restaurant" in reason for reason in result["match_reason"])


def test_generic_office_headphones_clip_is_not_customer_database_evidence():
    office = {
        "asset_id": "office-headphones-bike",
        "source_type": "provider_cache",
        "source_provider": "pexels",
        "title": "Man wearing headphones in an office",
        "description": "Office worker with a bicycle in the background",
        "tags": ["office", "computer", "person"],
        "authorization_status": "confirmed",
        "publish_licensed": True,
    }
    assert match_local_visual_asset("客户数据库", [office], allow_generic_fallback=False) is None


def test_factory_clip_is_not_customer_relationship_marketing_evidence():
    factory = {
        "asset_id": "factory-line",
        "source_type": "provider_cache",
        "source_provider": "pexels",
        "title": "Automated industrial production line",
        "description": "Factory manufacturing products",
        "tags": ["factory", "production", "product"],
        "authorization_status": "confirmed",
        "publish_licensed": True,
    }
    assert match_local_visual_asset(
        "客户关系沉淀与持续触达的营销机制",
        [factory],
        allow_generic_fallback=False,
    ) is None


def test_authorized_client_meeting_is_concrete_relationship_evidence():
    meeting = {
        "asset_id": "client-meeting",
        "source_type": "provider_cache",
        "source_provider": "pexels",
        "semantic_binding": "客户会面与关系维护",
        "keywords": ["客户", "商务会议", "关系", "会面", "client", "meeting"],
        "authorization_status": "confirmed",
        "publish_licensed": True,
        "asset_origin": "stock_video_asset",
    }

    result = match_local_visual_asset(
        "企业需要持续触达客户关系的营销机制",
        [meeting],
        allow_generic_fallback=False,
    )

    assert result is not None
    assert result["asset_id"] == "client-meeting"
    assert any(
        reason.startswith("concrete_visual_compatibility:relationship")
        for reason in result["match_reason"]
    )
