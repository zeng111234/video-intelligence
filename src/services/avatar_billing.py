"""数字人成片的保守预留与按整秒结算规则。"""

from __future__ import annotations

import math
from decimal import Decimal

from src.models import AvatarBillingQuote
from src.services.credits import cny_to_credits


AVATAR_BILLING_UNIT_SECONDS = 1
AVATAR_ESTIMATED_CHARACTERS_PER_SECOND = 4.0


def count_billable_characters(script_text: str) -> int:
    """按非空白字符计数，避免客户用空格压低预留金额。"""

    return sum(1 for character in script_text if not character.isspace())


def reservation_seconds(script_text: str, speech_rate: float) -> int:
    """按中文自然口播约每秒 4 字估算时长，完成后按真实时长结算。"""

    characters = max(1, count_billable_characters(script_text))
    safe_rate = max(0.8, min(float(speech_rate), 1.2))
    characters_per_second = AVATAR_ESTIMATED_CHARACTERS_PER_SECOND * safe_rate
    return max(1, math.ceil(characters / characters_per_second))


def billable_seconds(duration_seconds: float) -> int:
    """供应商按整秒计费时，任何小数秒都向上取整。"""

    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("数字人成片时长无效，暂时不能结算。")
    return max(1, math.ceil(duration_seconds / AVATAR_BILLING_UNIT_SECONDS))


def credits_for_seconds(price_per_minute_cny: Decimal, seconds: int) -> Decimal:
    if price_per_minute_cny < 0:
        raise ValueError("数字人分钟价格不能为负数。")
    if seconds <= 0:
        raise ValueError("数字人计费秒数必须大于 0。")
    cost = price_per_minute_cny * Decimal(seconds) / Decimal(60)
    return cny_to_credits(cost)


def build_avatar_billing_quote(
    *,
    script_text: str,
    speech_rate: float,
    price_per_minute_cny: Decimal,
) -> AvatarBillingQuote:
    seconds = reservation_seconds(script_text, speech_rate)
    cost = price_per_minute_cny * Decimal(seconds) / Decimal(60)
    credits = credits_for_seconds(price_per_minute_cny, seconds)
    return AvatarBillingQuote(
        price_per_minute_cny=float(price_per_minute_cny),
        billing_unit_seconds=AVATAR_BILLING_UNIT_SECONDS,
        reservation_seconds=seconds,
        reservation_cost_cny=float(cost),
        reservation_credits=float(credits),
        settlement_note=(
            "先按每秒约 4 字估算并冻结；成片后按供应商实际时长向上取整到整秒结算，"
            "多余积分自动退回，少量差额按真实时长补扣。"
        ),
    )
