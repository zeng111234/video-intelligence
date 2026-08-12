from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.models import CustomerCode
from src.repositories.sqlite import SQLiteRepository
from src.services.avatar_billing import (
    billable_seconds,
    build_avatar_billing_quote,
    credits_for_seconds,
)


def test_supplier_duration_is_rounded_up_to_whole_seconds() -> None:
    assert billable_seconds(1.067) == 2
    assert credits_for_seconds(Decimal("2.5"), 2) == Decimal("0.09")


def test_short_script_uses_spoken_duration_estimate_then_actual_settlement() -> None:
    quote = build_avatar_billing_quote(
        script_text="你好",
        speech_rate=1.0,
        price_per_minute_cny=Decimal("2.5"),
    )

    assert quote.reservation_seconds == 1
    assert Decimal(str(quote.reservation_credits)) == Decimal("0.05")


def test_313_characters_are_not_mistaken_for_313_seconds() -> None:
    quote = build_avatar_billing_quote(
        script_text="字" * 313,
        speech_rate=1.0,
        price_per_minute_cny=Decimal("2.5"),
    )

    assert quote.reservation_seconds == 79
    assert Decimal(str(quote.reservation_credits)) == Decimal("3.30")


def _customer(repository: SQLiteRepository, code: str, credits: str) -> None:
    now = datetime.now().astimezone()
    repository.create_customer_codes(
        [
            CustomerCode(
                code=code,
                name=code,
                initial_credits=credits,
                created_at=now,
                updated_at=now,
            )
        ]
    )


def test_reservation_refuses_insufficient_balance_without_partial_debit(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "insufficient-avatar.db")
    _customer(repository, "AVATAR-LOW", "0.05")

    with pytest.raises(ValueError, match="积分不足"):
        repository.reserve_avatar_billing(
            owner="AVATAR-LOW",
            idempotency_key="avatar-low-balance",
            price_per_minute_cny=Decimal("2.5"),
            billing_unit_seconds=1,
            reserved_seconds=2,
            reserved_credits=Decimal("0.09"),
        )

    assert repository.get_credit_balance("AVATAR-LOW") == Decimal("0.05")
    assert repository.get_avatar_billing(
        owner="AVATAR-LOW", idempotency_key="avatar-low-balance"
    ) is None


def test_known_provider_rejection_releases_reservation_once(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "released-avatar.db")
    _customer(repository, "AVATAR-RELEASE", "1.00")
    repository.reserve_avatar_billing(
        owner="AVATAR-RELEASE",
        idempotency_key="avatar-release-once",
        price_per_minute_cny=Decimal("2.5"),
        billing_unit_seconds=1,
        reserved_seconds=2,
        reserved_credits=Decimal("0.09"),
    )

    repository.release_avatar_billing(
        owner="AVATAR-RELEASE", idempotency_key="avatar-release-once"
    )
    repository.release_avatar_billing(
        owner="AVATAR-RELEASE", idempotency_key="avatar-release-once"
    )

    assert repository.get_credit_balance("AVATAR-RELEASE") == Decimal("1.00")
    transactions = repository.list_credit_transactions(
        owner="AVATAR-RELEASE", limit=10
    )
    assert sum(item["ref_type"] == "avatar_release" for item in transactions) == 1
