"""定价服务测试：默认值、覆盖生效、各收费点按新价扣费。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.services.pricing import (
    get_price,
    list_prices,
    set_price,
)


@pytest.fixture(autouse=True)
def _isolate_price_cache_and_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试独立定价缓存与独立数据库，避免污染真实库与其他测试。"""
    import src.services.pricing as pricing
    from src.repositories import SQLiteRepository

    monkeypatch.setattr(pricing, "_PRICE_CACHE", {})
    monkeypatch.setattr(
        pricing,
        "_pricing_repository",
        lambda: SQLiteRepository(tmp_path / "pricing.db"),
    )
    yield
    monkeypatch.setattr(pricing, "_PRICE_CACHE", {})


def test_default_prices_cover_all_charge_points() -> None:
    assert get_price("transcription_per_second_cny") == Decimal("0.00022")
    assert get_price("transcription_max_per_item_cny") == Decimal("0.20")
    assert get_price("avatar_per_minute_cny") == Decimal("2.5")
    assert get_price("avatar_voice_training_credits") == Decimal("60")
    assert get_price("avatar_face_training_credits") == Decimal("100")


def test_set_price_persists_and_overrides_default(tmp_path, monkeypatch) -> None:
    from src.repositories import SQLiteRepository

    monkeypatch.setattr(
        "src.repositories.SQLiteRepository",
        lambda *a, **k: SQLiteRepository(tmp_path / "p.db"),
    )
    set_price("avatar_voice_training_credits", "80")
    assert get_price("avatar_voice_training_credits") == Decimal("80")
    items = {item["key"]: item for item in list_prices()}
    assert items["avatar_voice_training_credits"]["value"] == "80"
    assert items["avatar_voice_training_credits"]["overridden"] is True


def test_set_price_rejects_unknown_key_and_negative() -> None:
    with pytest.raises(ValueError):
        set_price("unknown_key", "5")
    with pytest.raises(ValueError):
        set_price("avatar_per_minute_cny", "-1")


def test_transcription_estimate_uses_overridden_price(tmp_path, monkeypatch) -> None:
    """转写单价改为 0.001 元/秒后，估算费用按新价计算。"""
    from src.repositories import SQLiteRepository
    from src.services.cloud_transcription import (
        ASRAuthorizationStore,
        AliyunFunASRRuntime,
    )

    monkeypatch.setattr(
        "src.repositories.SQLiteRepository",
        lambda *a, **k: SQLiteRepository(tmp_path / "t.db"),
    )
    set_price("transcription_per_second_cny", "0.001")
    runtime = AliyunFunASRRuntime(
        authorization_store=ASRAuthorizationStore(tmp_path / "asr.json")
    )
    assert runtime.estimate_cost(30) == Decimal("0.03")


def test_avatar_training_uses_overridden_credits(tmp_path, monkeypatch) -> None:
    """训练价格改为 120 积分后，训练扣费按新价。"""
    from src.repositories import SQLiteRepository
    from project.backend.app.api.v1.avatar import _training_credits

    monkeypatch.setattr(
        "src.repositories.SQLiteRepository",
        lambda *a, **k: SQLiteRepository(tmp_path / "a.db"),
    )
    set_price("avatar_face_training_credits", "120")
    assert _training_credits("face") == 120
    set_price("avatar_voice_training_credits", "90")
    assert _training_credits("voice") == 90


def test_crawler_monthly_budget_is_not_a_customer_price() -> None:
    from src.services.commercial_search import _monthly_cost_limit_cny

    assert "crawler_monthly_limit_cny" not in {item["key"] for item in list_prices()}
    with pytest.raises(ValueError):
        set_price("crawler_monthly_limit_cny", "50")
    assert _monthly_cost_limit_cny() == 10.0
