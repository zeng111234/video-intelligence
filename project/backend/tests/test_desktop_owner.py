from __future__ import annotations

import json

from project.backend.app.core.desktop_owner import (
    desktop_owner_matches,
    ensure_desktop_owner,
)


def test_desktop_runtime_binds_to_only_one_customer(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEOINSIGHT_RUNTIME_ROOT", str(tmp_path))

    assert ensure_desktop_owner("CUSTOMER-ONE") is True
    assert desktop_owner_matches("CUSTOMER-ONE") is True
    assert ensure_desktop_owner("CUSTOMER-TWO") is False
    assert desktop_owner_matches("CUSTOMER-TWO") is False

    binding_path = tmp_path / "data" / "desktop-owner.json"
    raw = binding_path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["version"] == 1
    assert len(payload["owner_sha256"]) == 64
    assert "CUSTOMER-ONE" not in raw


def test_empty_customer_never_creates_a_binding(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEOINSIGHT_RUNTIME_ROOT", str(tmp_path))

    assert ensure_desktop_owner("  ") is False
    assert desktop_owner_matches("") is False
    assert not (tmp_path / "data" / "desktop-owner.json").exists()
