from pathlib import Path

import pytest

from src.services.publish_accounts import PublishAccountError, PublishAccountManager


def test_account_registry_saves_metadata_without_cookie_fields(tmp_path: Path):
    manager = PublishAccountManager(
        registry_path=tmp_path / "accounts.json",
        profile_root=tmp_path / "profiles",
    )

    account = manager.create(platform="douyin", name="公司主号")
    payload = (tmp_path / "accounts.json").read_text(encoding="utf-8")

    assert account.profile_dir.endswith(account.account_id)
    assert "cookie" not in payload.casefold()
    assert manager.status(account.account_id).status == "needs_login"


def test_account_registry_recovers_from_backup_when_primary_is_missing(tmp_path: Path):
    registry = tmp_path / "accounts.json"
    manager = PublishAccountManager(
        registry_path=registry, profile_root=tmp_path / "profiles"
    )
    account = manager.create(platform="douyin", name="主号")

    registry.unlink()
    recovered = PublishAccountManager(
        registry_path=registry, profile_root=tmp_path / "profiles"
    )

    assert recovered.get(account.account_id).name == "主号"
    assert registry.exists()


def test_account_without_profile_is_not_reported_ready(tmp_path: Path):
    manager = PublishAccountManager(
        registry_path=tmp_path / "accounts.json",
        profile_root=tmp_path / "profiles",
    )
    account = manager.create(platform="douyin", name="主号")
    account.status = "ready"
    manager._accounts[account.account_id] = account
    manager._save()

    assert manager.status(account.account_id).status == "needs_login"


def test_account_supports_core_platforms_and_rejects_unknown_platform(tmp_path: Path):
    manager = PublishAccountManager(
        registry_path=tmp_path / "accounts.json",
        profile_root=tmp_path / "profiles",
    )
    manager.create(platform="douyin", name="主号")

    with pytest.raises(PublishAccountError, match="同名"):
        manager.create(platform="douyin", name="主号")
    assert manager.create(platform="kuaishou", name="快手号").platform == "kuaishou"
    with pytest.raises(PublishAccountError, match="仅支持"):
        manager.create(platform="unknown", name="未知号")


def test_delete_account_only_allows_its_dedicated_profile(tmp_path: Path):
    manager = PublishAccountManager(
        registry_path=tmp_path / "accounts.json",
        profile_root=tmp_path / "profiles",
    )
    account = manager.create(platform="douyin", name="主号")
    profile_dir = Path(account.profile_dir)
    profile_dir.mkdir(parents=True)
    (profile_dir / "marker.txt").write_text("local", encoding="utf-8")

    manager.delete(account.account_id)

    assert not profile_dir.exists()
    assert manager.list() == []


def test_creator_center_page_marks_account_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manager = PublishAccountManager(
        registry_path=tmp_path / "accounts.json",
        profile_root=tmp_path / "profiles",
    )
    account = manager.create(platform="douyin", name="主号")
    account.debug_port = 52210
    manager._accounts[account.account_id] = account
    monkeypatch.setattr(manager, "_port_open", lambda _port: True)
    monkeypatch.setattr(
        manager,
        "_debug_pages",
        lambda _port: [
            {
                "title": "抖音创作者中心",
                "url": "https://creator.douyin.com/creator-micro/home",
            }
        ],
    )

    status = manager.status(account.account_id)

    assert status.status == "ready"
    assert "创作者中心" in status.last_message


def test_closed_profile_requires_live_verification_and_authorization_is_explicit(tmp_path: Path):
    manager = PublishAccountManager(
        registry_path=tmp_path / "accounts.json",
        profile_root=tmp_path / "profiles",
    )
    account = manager.create(platform="bilibili", name="B站主号")
    Path(account.profile_dir).mkdir(parents=True)

    assert manager.status(account.account_id).status == "needs_login"
    assert manager.set_auto_publish_authorized(account.account_id, authorized=True).auto_publish_authorized is True
