"""定价服务：管理员可在管理页调整的收费价格。

设计：pricing_settings 表（持久化覆盖值）优先，否则使用代码默认值。
1 元 = 1 积分；金额类价格最终经 cny_to_credits 换算成积分。
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

# 所有可调价格的默认值（元 / 积分）
DEFAULT_PRICING: dict[str, Decimal] = {
    # AI 文案：平台服务价，按实际 Token 用量结算
    "copywriting_input_cny_per_1k_tokens": Decimal("0.0015"),
    "copywriting_output_cny_per_1k_tokens": Decimal("0.003"),
    # 转写：单价（元/秒）与单条费用上限（元）
    "transcription_per_second_cny": Decimal("0.00022"),
    "transcription_max_per_item_cny": Decimal("0.20"),
    # 数字人：生成按分钟计价（元/分钟）；训练一口价（积分）
    "avatar_per_minute_cny": Decimal("2.5"),
    "avatar_voice_training_credits": Decimal("60"),
    "avatar_face_training_credits": Decimal("100"),
}

# 展示给管理页的友好名称与说明
PRICING_LABELS: dict[str, str] = {
    "copywriting_input_cny_per_1k_tokens": "AI 文案输入（平台服务价/千 Token）",
    "copywriting_output_cny_per_1k_tokens": "AI 文案输出（平台服务价/千 Token）",
    "transcription_per_second_cny": "转写单价（元/秒）",
    "transcription_max_per_item_cny": "转写单条费用上限（元）",
    "avatar_per_minute_cny": "数字人生成（元/分钟）",
    "avatar_voice_training_credits": "声音训练（积分/次）",
    "avatar_face_training_credits": "云形象训练（积分/次）",
}

_PRICE_CACHE: dict[str, Decimal] = {}


def _pricing_repository():
    """定价表所在仓库：与主应用使用同一个运行时数据库。"""
    from src.repositories import SQLiteRepository

    project_root = Path(__file__).resolve().parent.parent.parent
    root = Path(os.getenv("VIDEOINSIGHT_RUNTIME_ROOT", str(project_root))).resolve()
    return SQLiteRepository(root / "data" / "video_intelligence.db")


def get_price(key: str) -> Decimal:
    """读取当前生效价格：pricing_settings 表覆盖值 > 代码默认值。

    表不存在或无记录时返回默认值，因此测试环境（临时库）与首次启动均安全。
    """
    if key in _PRICE_CACHE:
        return _PRICE_CACHE[key]
    value: Decimal | None = None
    try:
        stored = _pricing_repository().get_pricing(key)
        if stored is not None:
            value = Decimal(str(stored))
    except Exception:
        value = None
    if value is None:
        value = DEFAULT_PRICING.get(key, Decimal("0"))
    _PRICE_CACHE[key] = value
    return value


def set_price(key: str, value: Decimal | float | str) -> None:
    """设置价格覆盖值并刷新缓存。"""
    if key not in DEFAULT_PRICING:
        raise ValueError(f"未知定价项：{key}")
    parsed = Decimal(str(value))
    if parsed < 0:
        raise ValueError("价格不能为负数")
    _pricing_repository().set_pricing(key, str(parsed))
    _PRICE_CACHE[key] = parsed


def list_prices() -> list[dict]:
    """返回全部可调价格：key、显示名、默认值、当前生效值、是否已覆盖。"""
    rows = _pricing_repository().list_pricing()
    overrides = {row["key"]: row["value"] for row in rows}
    updated = {row["key"]: row["updated_at"] for row in rows}
    return [
        {
            "key": key,
            "label": PRICING_LABELS.get(key, key),
            "default": str(default),
            "value": overrides.get(key, str(default)),
            "overridden": key in overrides,
            "updated_at": updated.get(key),
        }
        for key, default in DEFAULT_PRICING.items()
    ]
