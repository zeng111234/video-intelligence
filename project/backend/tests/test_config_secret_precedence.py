from __future__ import annotations

from project.backend.app.core import config


def test_explicit_empty_secret_disables_legacy_fallback(monkeypatch):
    monkeypatch.setenv("VIDEOINSIGHT_TEST_SECRET", "")
    monkeypatch.setattr(
        config,
        "_legacy_streamlit_secret",
        lambda _key: "legacy-secret",
    )

    assert config._secret("VIDEOINSIGHT_TEST_SECRET", "safe-default") == (
        "safe-default"
    )


def test_absent_secret_can_use_legacy_fallback(monkeypatch):
    monkeypatch.delenv("VIDEOINSIGHT_TEST_SECRET", raising=False)
    monkeypatch.setattr(
        config,
        "_legacy_streamlit_secret",
        lambda _key: "legacy-secret",
    )

    assert config._secret("VIDEOINSIGHT_TEST_SECRET") == "legacy-secret"
