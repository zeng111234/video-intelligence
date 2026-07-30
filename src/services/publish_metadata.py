"""发布文案的安全生成与校验。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


DOUYIN_TITLE_LIMIT = 30
DESCRIPTION_LIMIT = 1000
MAX_TAGS = 8
TAG_LIMIT = 30
INFERRED_TAG_LIMIT = 4

_TOPIC_TAG_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("机器人", "自动化设备", "智能设备"), "机器人产业"),
    (("失业", "裁员", "就业", "岗位", "工人"), "就业观察"),
    (("商业", "市场", "订单", "消费"), "商业思维"),
    (("企业", "工厂", "老板", "经营"), "企业经营"),
    (("获客", "客户", "流量", "线索"), "获客增长"),
    (("短视频", "直播", "内容运营"), "短视频运营"),
    (("餐饮", "门店", "到店"), "门店经营"),
    (("教育", "培训", "课程"), "教育观察"),
    (("人工智能", "数字人", "AI"), "科技趋势"),
)


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _remove_source_metadata(value: str) -> str:
    text = re.sub(r"https?://\S+", "", value)
    text = re.sub(r"#[^\s#@]+", "", text)
    text = re.sub(r"@[^\s#@]+", "", text)
    return _compact(text)


def _collapse_repeated_half(value: str) -> str:
    text = _compact(value)
    if len(text) % 2:
        return text
    midpoint = len(text) // 2
    first, second = text[:midpoint].strip(), text[midpoint:].strip()
    return first if first and first == second else text


def normalize_title(value: str, *, remove_source_metadata: bool = False) -> str:
    text = _remove_source_metadata(value) if remove_source_metadata else _compact(value)
    if not remove_source_metadata and (
        "#" in text or "@" in text or re.search(r"https?://", text)
    ):
        raise ValueError("标题不能包含话题、@提及或链接，请在下方标签中单独填写。")
    text = _collapse_repeated_half(text)
    if not text:
        raise ValueError("请填写发布标题。")
    return text[:DOUYIN_TITLE_LIMIT]


def normalize_description(value: str) -> str:
    text = _remove_source_metadata(value)
    return text[:DESCRIPTION_LIMIT]


def normalize_tags(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        tag = str(raw or "").strip().lstrip("#").strip()
        if not tag:
            continue
        if "@" in tag or "#" in tag or re.search(r"https?://", tag):
            raise ValueError("标签不能包含 @、# 或链接。")
        tag = _compact(tag)[:TAG_LIMIT]
        key = tag.casefold()
        if key not in seen:
            result.append(tag)
            seen.add(key)
    if len(result) > MAX_TAGS:
        raise ValueError(f"最多保留 {MAX_TAGS} 个标签。")
    return result


def _infer_topic_tags(value: str) -> list[str]:
    text = _remove_source_metadata(value)
    inferred = [
        tag
        for keywords, tag in _TOPIC_TAG_RULES
        if any(keyword.casefold() in text.casefold() for keyword in keywords)
    ]
    return inferred[:INFERRED_TAG_LIMIT] or ["行业观察"]


def suggested_publish_draft(
    *,
    approved_script: str,
    creative_plan: dict[str, Any] | None,
    profile_tags: list[str] | None,
) -> dict[str, Any]:
    hook = str((creative_plan or {}).get("hook") or "").strip()
    first_sentence = re.split(r"[。！？!?\n]", approved_script.strip(), maxsplit=1)[0]
    title = normalize_title(hook or first_sentence, remove_source_metadata=True)
    tags = normalize_tags(profile_tags or [])
    return {
        "title": title,
        "description": normalize_description(approved_script),
        "tags": tags or _infer_topic_tags(f"{hook} {approved_script}"),
        "approved": False,
        "warnings": ["标题和标签不会继承来源视频的作者、人物或话题。"],
    }


def validated_publish_draft(
    *,
    title: str,
    description: str,
    tags: list[str],
) -> dict[str, Any]:
    return {
        "title": normalize_title(title),
        "description": normalize_description(description),
        "tags": normalize_tags(tags),
    }


def publish_draft_fingerprint(draft: dict[str, Any]) -> str:
    payload = {
        "title": str(draft.get("title") or ""),
        "description": str(draft.get("description") or ""),
        "tags": list(draft.get("tags") or []),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
