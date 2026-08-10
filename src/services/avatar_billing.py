"""数字人成片的保守预留与按整秒结算规则。"""

from __future__ import annotations

import math
from decimal import Decimal

from src.models import AvatarBillingQuote
from src.services.credits import cny_to_credits


AVATAR_BILLING_UNIT_SECONDS = 1


def count_billable_characters(script_text: str) -> int:
    """按非空白字符计数，避免客户用空格压低预留金额。"""

    return sum(1 for character in script_text if not character.isspace())


def reservation_seconds(script_text: str, speech_rate: float) -> int:
    """按每字符最多一秒的保守上限冻结，完成后自动退还差额。"""

    characters = max(1, count_billable_characters(script_text))
    safe_rate = max(0.8, min(float(speech_rate), 1.2))
    return max(1, math.ceil(characters / safe_rate))


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
            "先冻结本次保守上限；成片后按供应商实际时长向上取整到整秒结算，"
            "多余积分自动退回。"
        ),
    )
