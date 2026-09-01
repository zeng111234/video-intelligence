"""Deterministic local visual-asset matching for talking-head edits.

This module only scores already cached metadata.  It never searches, uploads,
calls a provider, or treats a generated image as publish licensed.  The result
contains the matched terms so a timeline/report can explain why an asset was
chosen.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


_FALLBACK_ROLE_PREFIX = "fallback_"
_GENERIC_PREFIXES = ("通用", "generic", "fallback")
_DOMESTIC_CONTEXTS = {"domestic", "international", "unknown"}
_BUSINESS_QUERY_TERMS = (
    "客户",
    "业务",
    "企业",
    "营销",
    "数据库",
    "客户资源",
    "客户关系",
    "客户名单",
    "crm",
    "customer",
    "client",
    "business",
    "database",
    "analytics",
    "office",
    "management",
)
_RESTAURANT_QUERY_TERMS = (
    "烧烤",
    "餐饮",
    "餐厅",
    "烤肉",
    "顾客",
    "回头客",
    "私域",
    "barbecue",
    "bbq",
    "grill",
    "restaurant",
    "dining",
    "food",
    "ribs",
)
_LOYALTY_QUERY_TERMS = (
    "会员",
    "会员制",
    "会员资格",
    "优惠券",
    "充值",
    "店长",
    "共享店长",
    "推广",
    "宣传员",
    "朋友圈",
    "群里",
    "社交圈",
    "粉丝",
    "转介绍",
    "口碑",
    "传播",
    "活动",
    "loyalty",
    "coupon",
    "referral",
    "social sharing",
)
_DATA_QUERY_TERMS = (
    "小程序",
    "门店",
    "系统",
    "门店系统",
    "智慧门店",
    "报表",
    "数据",
    "自动执行",
    "秒到账",
    "现金奖励",
    "analytics",
    "workflow",
)
_MEETING_QUERY_TERMS = (
    "客户关系",
    "客户沟通",
    "客户会面",
    "商务会议",
    "会面",
    "会议",
    "relationship",
    "meeting",
)
_MULTI_INDUSTRY_TERMS = (
    "餐饮",
    "水果",
    "生鲜",
    "美容",
    "便利店",
    "多业态",
    "多行业",
    "fruit",
    "fresh",
    "beauty",
    "convenience",
    "retail",
)
_BUSINESS_VISUAL_CONCEPTS = (
    "客户",
    "业务",
    "企业",
    "营销",
    "数据库",
    "客户资源",
    "客户关系",
    "客户名单",
    "工厂",
    "品牌",
    "产品",
    "办公",
    "电脑",
    "会议",
    "管理",
    "crm",
    "customer",
    "client",
    "business",
    "database",
    "analytics",
    "dashboard",
    "office",
    "data",
    "management",
    "meeting",
    "factory",
    "manufactur",
    "product",
    "computer",
)
_BUSINESS_CONFLICTS = (
    "动物",
    "野生",
    "天鹅",
    "鸟",
    "湖泊",
    "湖",
    "河流",
    "森林",
    "自然风景",
    "风景",
    "nature",
    "wildlife",
    "animal",
    "swan",
    "bird",
    "lake",
    "river",
    "forest",
    "landscape",
)
_VEHICLE_CONTEXT_TERMS = (
    "汽车",
    "车辆",
    "车载",
    "出风口",
    "导航",
    "手机支架",
    "car",
    "vehicle",
    "gps",
    "navigation",
    "mount",
    "phone mount",
    "driving",
    "traffic",
)

# Business copy is intentionally mapped to concrete visual concepts.  Generic
# words such as ``企业``/``客户``/``办公`` are recall hints only and must never
# be sufficient evidence to put a stock clip into the timeline.
_CONCRETE_CONCEPT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("restaurant", ("烧烤", "餐饮", "餐厅", "烤肉", "顾客", "回头客", "barbecue", "bbq", "grill", "restaurant", "dining", "food", "ribs", "meal")),
    ("database", ("数据库", "客户资源", "客户名单", "crm", "customer database", "dashboard", "analytics", "data management")),
    ("relationship", ("客户关系", "沉淀用户", "持续触达", "营销机制", "customer relationship", "marketing", "outreach", "engagement")),
    ("loyalty", ("会员", "会员制", "会员资格", "优惠券", "充值", "私域", "转介绍", "口碑", "传播", "推广", "宣传", "粉丝", "加入", "loyalty", "membership", "coupon", "referral", "customer signup", "social sharing")),
    ("meeting", ("客户会面", "客户沟通", "会面", "client meeting", "customer meeting", "meeting")),
    ("factory", ("工厂", "流水线", "制造", "factory", "manufactur", "production")),
    ("product", ("品牌", "产品", "brand", "product")),
    ("product_demo", ("支架", "手机支架", "车载", "出风口", "充电", "安装", "拧", "锁紧", "吸住", "product demonstration", "phone mount", "car mount", "device installation")),
    ("qr_scan", ("二维码", "扫码", "qr code", "qr", "scan", "scanning", "signup", "sign up")),
    # A phone alone is not vehicle evidence: app/phone footage is common in
    # unrelated scenes. Require an explicit car, driving, navigation, mount,
    # or air-vent context before assigning this concept.
    ("vehicle_navigation", ("车载", "导航", "出风口", "gps", "navigation", "driving", "vehicle", "car", "air vent", "phone mount")),
    ("vehicle_scene", ("汽车", "车辆", "二手车", "新车", "车型", "车价", "报价", "车况", "公里数", "used car", "new car", "dealership", "vehicle inspection")),
    ("pet_fish", ("鱼", "喂鱼", "鱼缸", "小金鱼", "fish", "aquarium", "goldfish")),
    ("pet_chicken", ("小鸡", "鸡", "雏鸡", "chick", "chicken")),
    ("pet_cat", ("猫", "小猫", "cat", "kitten")),
    ("pet_dog", ("狗", "小狗", "dog", "puppy")),
    ("pet_scene", ("宠物", "pet")),
    ("workflow", ("流程", "步骤", "方法", "操作", "设置", "教程", "workflow", "process", "tutorial", "how to", "demonstration")),
    ("office_data", ("报表", "数据分析", "电脑数据", "report", "analytics", "data analysis", "dashboard")),
    ("multi_industry", ("多业态", "多行业", "零售", "水果", "生鲜", "美容", "便利店", "multi-industry", "multi industry", "retail", "convenience", "beauty")),
)


def _concrete_visual_concepts(value: object) -> set[str]:
    compact = _compact(value)
    concepts: set[str] = set()
    for concept, terms in _CONCRETE_CONCEPT_RULES:
        if any(_compact(term) in compact for term in terms):
            concepts.add(concept)
    # ``product`` is a recall hint, not proof of a product demonstration:
    # factory footage also carries that generic word.  Once a query contains
    # a concrete operation/device concept, the generic concept must not make
    # an unrelated manufacturing clip pass the semantic gate.
    if "product_demo" in concepts:
        concepts.discard("product")
    specific_pets = {
        concept
        for concept in concepts
        if concept in {"pet_fish", "pet_chicken", "pet_cat", "pet_dog"}
    }
    if specific_pets:
        # ``pet_scene`` is a recall hint only.  A fish clip cannot satisfy a
        # chicken request merely because both are animals/pets.
        concepts.discard("pet_scene")
    # Provider titles and URLs often contain ``dashboard`` for a vehicle
    # dashboard.  That word is not evidence of CRM/database footage.  Apply
    # the same disambiguation to both query and asset evidence so a car clip
    # cannot pass a business-data intersection by lexical accident.
    if any(_compact(term) in compact for term in _VEHICLE_CONTEXT_TERMS):
        concepts.discard("database")
        concepts.discard("office_data")
    return concepts


def query_visual_concepts(query: str) -> set[str]:
    """Return concrete concepts required by a business query."""

    return _concrete_visual_concepts(query)


def asset_visual_concepts(asset: Mapping[str, Any]) -> set[str]:
    """Return concepts evidenced by curated asset metadata only."""

    return _concrete_visual_concepts(_visual_evidence_text(asset))


def normalize_domestic_context(value: object, *, default: str = "unknown") -> str:
    """Normalize explicit scene origin; never infer China from locale/provider."""
    normalized = _compact(value)
    aliases = {
        "国内": "domestic",
        "中国": "domestic",
        "国内场景": "domestic",
        "国际": "international",
        "海外": "international",
        "国际化": "international",
        "未知": "unknown",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _DOMESTIC_CONTEXTS else default


def visual_asset_context(asset: Mapping[str, Any]) -> str:
    """Classify reporting context without treating locale as domestic proof."""
    context = normalize_domestic_context(asset.get("domestic_context"))
    if context == "unknown" and _compact(asset.get("source_provider")) in {
        "pexels",
        "pixabay",
    }:
        return "international"
    return context


def visual_asset_priority(asset: Mapping[str, Any]) -> int:
    """Lower is preferred; explicit domestic metadata is required."""
    context = visual_asset_context(asset)
    generated = _compact(asset.get("asset_origin")) == "generated_image_asset"
    confirmed = _compact(asset.get("authorization_status")) in {
        "confirmed",
        "generated_for_local_acceptance",
    }
    if context == "domestic" and not generated and confirmed:
        return 0
    if context == "domestic" and generated and confirmed:
        return 1
    if context == "international" or _compact(asset.get("source_provider")) in {
        "pexels",
        "pixabay",
    }:
        return 2
    return 3


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _evidence_compact(value: object) -> str:
    """Normalize provider slugs without changing enum/ID comparisons."""

    return _compact(str(value or "").replace("-", " "))


def is_business_visual_query(query: str) -> bool:
    compact_query = _compact(query)
    return any(term in compact_query for term in _BUSINESS_QUERY_TERMS)


# P0-收口 2026-08-31: ``is_restaurant_visual_query`` was the most obvious
# "answer-driven" helper in this module: any query mentioning BBQ/餐厅
# was silently routed to a hardcoded search string
# ``"barbecue restaurant grill food preparation diners"``.  That string
# is not a search term; it is a single canned answer that collapses
# every restaurant concept into one Western-grill clip, regardless of
# whether the caller's subject is a juice bar, a noodle shop or a
# hospital cafeteria.  Delete the helper entirely; the generic
# ``visual_search_query`` below now returns the literal query string
# and lets the provider layer do its own classification on real
# candidate footage.


def visual_search_query(query: str) -> str:
    """Turn abstract Chinese business copy into concrete visual search terms.

    P0-收口 2026-08-31: this function used to map every recognised
    category (multi-industry, meeting, loyalty, data, restaurant,
    business) to a hardcoded English search string.  Those hardcoded
    strings are the very "answer-driven" code path the cross-review
    called out: they collapse every director intent into one canned
    phrase and the provider then returns the same handful of clips
    regardless of the caller's actual subject.  Replace the entire
    if-elif chain with a single rule: pass the caller's own text
    through.  When the caller already wrote English with concrete
    concepts (e.g. "restaurant customer using smartphone QR code")
    we still keep those English tokens; otherwise we hand the literal
    Chinese to the provider.  The provider is then responsible for
    matching real B-roll on the actual semantics, not on a frozen
    six-row lookup table.
    """
    query_text = str(query or "").strip()
    if not query_text:
        return ""

    # A director request may already contain a concrete provider query
    # such as "restaurant customer using smartphone QR code".  Keep
    # those English tokens so the provider can return matching footage
    # instead of a collapsed category.
    english_terms = re.findall(r"[A-Za-z][A-Za-z0-9-]*", query_text)
    specific_english_terms = {
        "app", "cashback", "customer", "coupon", "dashboard", "dining",
        "diners", "factory", "loyalty", "menu", "process", "qr",
        "referral", "restaurant", "scan", "smartphone", "marketing",
        "workflow", "shop", "store", "kitchen", "warehouse", "delivery",
        "driver", "clinic", "patient", "student", "teacher", "gym",
    }
    if len(english_terms) >= 3 and any(
        term.casefold() in specific_english_terms for term in english_terms
    ):
        return " ".join(english_terms)

    # Generic path: return the literal query.  The provider / asset
    # matcher is the right place to translate Chinese into English on
    # demand, not here.
    return query_text


def _visual_evidence_text(asset: Mapping[str, Any]) -> str:
    """Return visible/curated metadata, excluding raw provider search intent."""

    values: list[str] = []
    for key in (
        "name",
        "original_name",
        "source_url",
        "title",
        "description",
        "tags",
        "provider_title",
        "provider_description",
        "provider_tags",
        "semantic_binding",
        "visual_keywords",
    ):
        value = asset.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            values.extend(_evidence_compact(item) for item in value if _evidence_compact(item))
        elif _evidence_compact(value):
            values.append(_evidence_compact(value))
    # `semantic_query` and provider-generated `keywords` describe why a search
    # was made, not what the returned footage contains.
    source_type = _compact(asset.get("source_type"))
    if source_type != "provider_cache":
        keywords = asset.get("keywords")
        if isinstance(keywords, Sequence) and not isinstance(keywords, (str, bytes)):
            values.extend(_evidence_compact(item) for item in keywords if _evidence_compact(item))
    return " ".join(dict.fromkeys(values))


def semantic_conflicts(query: str, asset: Mapping[str, Any]) -> list[str]:
    # P0-收口 2026-08-31: the restaurant branch was deleted together with
    # ``is_restaurant_visual_query``.  Business-only conflict detection
    # remains in place so a small-shop video does not get classified as
    # a CRM / loyalty programme clip.
    if not is_business_visual_query(query):
        return []
    evidence = _visual_evidence_text(asset)
    return [term for term in _BUSINESS_CONFLICTS if term in evidence]


def _metadata_terms(asset: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("semantic_binding", "semantic_query", "name"):
        value = _compact(asset.get(key))
        if value:
            values.append(value)
    keywords = asset.get("keywords")
    if isinstance(keywords, Sequence) and not isinstance(keywords, (str, bytes)):
        values.extend(_compact(item) for item in keywords if _compact(item))
    return tuple(dict.fromkeys(values))


def is_generic_fallback(asset: Mapping[str, Any]) -> bool:
    role = _compact(asset.get("manifest_role"))
    semantic = _compact(asset.get("semantic_binding"))
    return role.startswith(_FALLBACK_ROLE_PREFIX) or semantic.startswith(_GENERIC_PREFIXES)


def asset_publish_claim_allowed(asset: Mapping[str, Any]) -> bool:
    """Generated local images are never sufficient for a publish claim."""

    if _compact(asset.get("asset_origin")) == "generated_image_asset":
        return False
    if _compact(asset.get("authorization_status")) != "confirmed":
        return False
    return bool(
        asset.get("publish_licensed") is True
        or _compact(asset.get("source_provider")) in {"pexels", "pixabay"}
        or _compact(asset.get("source")) == "user_uploaded_local"
    )


def _score_asset(query: str, asset: Mapping[str, Any]) -> tuple[int, list[str]]:
    compact_query = _compact(query)
    conflicts = semantic_conflicts(query, asset)
    if conflicts:
        return -1000, ["semantic_conflict:" + ",".join(conflicts)]
    semantic = _compact(asset.get("semantic_binding"))
    semantic_query = _compact(asset.get("semantic_query"))
    keywords = asset.get("keywords")
    keyword_terms = tuple(
        _compact(item)
        for item in keywords
        if _compact(item)
    ) if isinstance(keywords, Sequence) and not isinstance(keywords, (str, bytes)) else ()
    reasons: list[str] = []
    score = 0
    query_concepts = query_visual_concepts(query)
    evidence = _visual_evidence_text(asset)
    asset_concepts = _concrete_visual_concepts(evidence)
    requires_concrete_visual_evidence = bool(query_concepts) or (
        is_business_visual_query(query)
    )
    if requires_concrete_visual_evidence:
        # A concrete operation/device query must be evidenced by that same
        # operation/device concept. Generic ``workflow`` or ``product`` words
        # in an unrelated dashboard/factory asset are not a valid fallback.
        if "product_demo" in query_concepts and "product_demo" not in asset_concepts:
            # A phone used for navigation while driving is a valid, narrower
            # product-use demonstration for a vehicle-mounted device.  It is
            # deliberately not a generic `vehicle` fallback: the asset must
            # also evidence a phone/smartphone and the query must carry the
            # same vehicle/product context.
            vehicle_use_compatible = (
                "vehicle_navigation" in query_concepts
                and "vehicle_navigation" in asset_concepts
                and (
                    any(term in evidence for term in ("phone", "smartphone"))
                    or (
                        "product_demo" in query_concepts
                        and any(term in evidence for term in ("gps", "navigation"))
                    )
                )
            )
            if not vehicle_use_compatible:
                return 0, ["missing_product_demo_evidence"]
            reasons.append("concrete_visual_compatibility:vehicle_navigation_product_use")
        concrete_hits = sorted(query_concepts & asset_concepts)
        compatible_hits: list[str] = []
        # A real client meeting is concrete evidence for relationship
        # maintenance/outreach, even when the provider metadata uses
        # ``meeting`` rather than repeating the abstract business phrase.
        # Keep this compatibility explicit and narrow; it must not make a
        # generic office/person clip qualify as CRM or database evidence.
        if "relationship" in query_concepts and "meeting" in asset_concepts:
            compatible_hits.append("relationship↔meeting")
        # A broad scene word must not satisfy a more specific request.  For
        # example, a restaurant clip is not evidence of a loyalty programme
        # merely because the query also mentions a restaurant.  The specific
        # concept may be satisfied by the narrow relationship/meeting
        # compatibility above, but generic scene overlap alone is rejected.
        specific_query_concepts = query_concepts - {"restaurant"}
        specific_hits = asset_concepts & specific_query_concepts
        compatible_concept_hits = {
            "meeting"
        } if "relationship" in query_concepts and "meeting" in asset_concepts else set()
        if specific_query_concepts and not (specific_hits or compatible_concept_hits):
            return 0, [
                "missing_specific_visual_concept:"
                + ",".join(sorted(specific_query_concepts))
            ]
        if not concrete_hits and not compatible_hits:
            return 0, ["no_concrete_visual_concept_intersection"]
        if concrete_hits:
            reasons.append("concrete_visual_intersection:" + ",".join(concrete_hits))
        if compatible_hits:
            reasons.append("concrete_visual_compatibility:" + ",".join(compatible_hits))
        # A concrete intersection is necessary, but remains metadata evidence;
        # it is not a claim that a vision model inspected every frame.
        score += 28 * len(concrete_hits) + 24 * len(compatible_hits)
    exact_semantic = semantic if semantic and semantic in compact_query else ""
    exact_query = (
        semantic_query
        if semantic_query
        and semantic_query in compact_query
        and _compact(asset.get("source_type")) != "provider_cache"
        else ""
    )
    if exact_semantic or exact_query:
        exact = exact_semantic or exact_query
        score += 100 + min(30, len(exact))
        reasons.append(
            f"semantic_binding:{asset.get('semantic_binding')}"
            if exact_semantic
            else f"semantic_query:{asset.get('semantic_query')}"
        )
    matched_keywords = [
        keyword for keyword in keyword_terms
        if keyword and keyword in compact_query
    ]
    if matched_keywords:
        score += sum(18 + min(8, len(keyword)) for keyword in matched_keywords)
        reasons.append("keywords:" + ",".join(matched_keywords))
    if semantic and not matched_keywords and not exact_semantic:
        semantic_parts = [part for part in re.split(r"[与和、/ ]", semantic) if len(part) >= 2]
        partials = [part for part in semantic_parts if part in compact_query]
        if partials:
            score += sum(8 + min(6, len(part)) for part in partials)
            reasons.append("semantic_parts:" + ",".join(partials))
    if requires_concrete_visual_evidence:
        # Do not add points for arbitrary business vocabulary found only on the
        # asset.  The query/asset intersection above is the sole visual gate.
        score += 28 * len(query_concepts & asset_concepts)
        if "relationship" in query_concepts and "meeting" in asset_concepts:
            score += 24
    if is_generic_fallback(asset):
        # Generic visuals are a deterministic last resort, never a precise hit.
        score -= 80
        reasons.append("generic_fallback")
    return score, reasons


def match_local_visual_asset(
    query: str,
    assets: Sequence[Mapping[str, Any]],
    *,
    used_asset_ids: Sequence[str] = (),
    allow_generic_fallback: bool = True,
    return_counts: bool = False,
) -> dict[str, Any] | None | tuple[dict[str, Any] | None, int, int]:
    """Return the best unused local asset, or ``None`` for safe degradation.

    P0-3：当 ``return_counts=True`` 时返回 ``(matched, semantic_candidate_count,
    accepted_count)`` 元组，便于上层拆 4 个独立本地素材指标。
    """
    used = {str(item) for item in used_asset_ids}
    scored: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
    semantic_candidate_count = 0
    accepted_count = 0
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        asset_id = str(asset.get("asset_id") or "")
        if not asset_id or asset_id in used:
            continue
        generic = is_generic_fallback(asset)
        if generic and not allow_generic_fallback:
            continue
        score, reasons = _score_asset(query, asset)
        # A generic fallback is used only when no precise metadata hit exists.
        precise = not generic and score >= 28
        if not precise and not generic:
            continue
        # 任何通过"precise 或 generic"的资产都计入"语义候选"。
        # "accepted" 仅指本函数最终会返回的那个 top-1。
        semantic_candidate_count += 1
        if generic:
            # Fallbacks remain deterministic and explainable.  A generic asset
            # with a matching keyword beats a blind fallback, but never beats a
            # precise asset.
            fallback_keyword_hit = any(
                reason.startswith("keywords:") for reason in reasons
            )
            score += 10 if fallback_keyword_hit else 0
        rank = (
            visual_asset_priority(asset),
            0 if precise else 1,
            -score,
            -len(reasons),
            asset_id,
        )
        result = dict(asset)
        result["match_score"] = score
        result["match_type"] = "precise" if precise else "generic_fallback"
        result["match_reason"] = reasons or ["generic_fallback_deterministic_order"]
        scored.append((rank, result))
    if not scored:
        if return_counts:
            return None, 0, 0
        return None
    scored.sort(key=lambda item: item[0])
    accepted_count = 1  # 仅返回一个 top-1
    if return_counts:
        return scored[0][1], semantic_candidate_count, accepted_count
    return scored[0][1]


def semantic_broll_gate_passed(assets: Sequence[Mapping[str, Any]]) -> bool:
    """Require every automatic B-roll event to carry an explainable match."""

    for asset in assets:
        if not isinstance(asset, Mapping):
            return False
        if semantic_conflicts(
            str(asset.get("semantic_query") or asset.get("semantic_binding") or ""),
            asset,
        ):
            return False
        score = asset.get("match_score")
        reasons = asset.get("match_reason")
        if not isinstance(score, (int, float)) or score <= 0:
            return False
        if not isinstance(reasons, Sequence) or isinstance(reasons, (str, bytes)) or not reasons:
            return False
        if any("no_local_semantic_metadata" in str(reason) for reason in reasons):
            return False
    return True


def build_keyword_generation_plan(
    query: str,
    *,
    env: Mapping[str, str] | None = None,
    default_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Describe an opt-in keyword-generation action without making a call."""

    source = os.environ if env is None else env
    enabled = str(
        source.get("VIDEO_IMAGE_AUTOGENERATE_ON_KEYWORD_MATCH", "false")
    ).strip().casefold() in {"1", "true", "yes", "on"}
    manifest_path = str(
        source.get("VIDEO_IMAGE_MANIFEST_PATH")
        or default_manifest_path
        or ""
    ).strip()
    if not enabled:
        return {
            "enabled": False,
            "query": _compact(query),
            "manifest_path": manifest_path,
            "provider_calls": 0,
            "action": "safe_degradation",
            "blocking_reason": "explicit_keyword_generation_switch_off",
        }
    if not manifest_path:
        return {
            "enabled": True,
            "query": _compact(query),
            "manifest_path": "",
            "provider_calls": 0,
            "action": "blocked",
            "blocking_reason": "manifest_writeback_path_missing",
        }
    return {
        "enabled": True,
        "query": _compact(query),
        "manifest_path": manifest_path,
        "provider_calls": 0,
        "action": "awaiting_explicit_quote_and_confirmation",
        "blocking_reason": "no_provider_call_until_budget_confirmation",
    }


def append_manifest_asset(
    manifest_path: str | Path,
    *,
    asset_id: str,
    source_path: str | Path,
    role: str,
    semantic_binding: str,
    keywords: Sequence[str],
    domestic_context: str = "unknown",
    domestic_scene: str = "",
) -> dict[str, Any]:
    """Persist a locally generated asset only after its bytes are present.

    The caller must explicitly invoke this function after generation and local
    validation.  It does not create directories, upload bytes, or call APIs.
    """

    manifest_file = Path(manifest_path)
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest must be an object")
    assets = payload.setdefault("assets", [])
    if not isinstance(assets, list):
        raise ValueError("manifest assets must be a list")
    relative_path = os.path.relpath(source, manifest_file.parent)
    digest = hashlib.sha256(source.read_bytes()).hexdigest().upper()
    entry = {
        "asset_id": asset_id,
        "path": relative_path.replace("\\", "/"),
        "sha256": digest,
        "role": role,
        "semantic_binding": semantic_binding,
        "keywords": [str(item).strip() for item in keywords if str(item).strip()],
        "domestic_context": normalize_domestic_context(domestic_context),
        "domestic_scene": str(domestic_scene or "").strip()[:160],
    }
    replaced = False
    for index, current in enumerate(assets):
        if isinstance(current, Mapping) and str(current.get("asset_id") or "") == asset_id:
            assets[index] = entry
            replaced = True
            break
    if not replaced:
        assets.append(entry)
    manifest_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return entry
