"""管理员客户管理 API 测试：生成激活码、客户列表、禁用、管理员账号管理。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from project.backend.app.core import deps as backend_deps  # noqa: E402
from project.backend.app.main import app  # noqa: E402
from src.models import CustomerCode  # noqa: E402
from src.repositories.sqlite import SQLiteRepository  # noqa: E402

TEST_API_HEADERS = {"X-API-Key": "pytest-api-key"}
TEST_ADMIN_PASSWORD = "test-admin-password-123"


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_KEY", "pytest-api-key")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("DEFAULT_CREDIT_BALANCE", "0")
    repository = SQLiteRepository(tmp_path / "customer-admin.db")

    app.dependency_overrides[backend_deps.get_repository] = lambda: repository
    try:
        with TestClient(app) as test_client:
            yield test_client, repository
    finally:
        app.dependency_overrides.clear()


def _admin_headers(client: TestClient) -> dict[str, str]:
    token = client.post(
        "/api/v1/auth/admin-login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        headers=TEST_API_HEADERS,
    ).json()["token"]
    return {**TEST_API_HEADERS, "X-Admin-Token": token}


def test_generate_codes_and_customer_login(client):
    """7 天 / 9.9 积分套餐首次登录才起算，且不误扣内容积分。"""
    test_client, repo = client
    resp = test_client.post(
        "/api/v1/admin/codes/generate",
        json={
            "name": "客户丙",
            "initial_credits": "0",
            "valid_days": 7,
            "package_price_credits": "9.9",
            "count": 2,
        },
        headers=_admin_headers(test_client),
    )
    assert resp.status_code == 200
    codes = resp.json()
    assert len(codes) == 2
    assert all(len(item["code"].replace("-", "")) == 16 for item in codes)
    assert all(
        [len(group) for group in item["code"].split("-")] == [4, 4, 4, 4]
        for item in codes
    )
    assert all(item["balance"] == "0" for item in codes)
    assert all(item["valid_days"] == 7 for item in codes)
    assert all(item["package_price_credits"] == "9.9" for item in codes)
    assert all(item["access_status"] == "unused" for item in codes)
    assert all(item["activated_at"] is None for item in codes)
    assert all(item["access_expires_at"] is None for item in codes)
    # 客户用新激活码登录
    login_response = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": codes[0]["code"]},
        headers=TEST_API_HEADERS,
    )
    assert login_response.status_code == 200
    login = login_response.json()
    assert login["balance"] == "0"
    assert login["valid_days"] == 7
    assert login["package_price_credits"] == "9.9"
    assert login["activated_at"] is not None
    assert login["access_expires_at"] is not None

    first_expiry = login["access_expires_at"]
    second_login = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": codes[0]["code"]},
        headers=TEST_API_HEADERS,
    ).json()
    assert second_login["access_expires_at"] == first_expiry
    assert second_login["balance"] == "0"


def test_extend_access_preserves_content_balance_and_revokes_old_session(client):
    """续 7 天 / 9.9 积分只延长使用权，不改变内容余额。"""
    test_client, _ = client
    admin_headers = _admin_headers(test_client)
    created = test_client.post(
        "/api/v1/admin/codes/generate",
        json={
            "name": "客户续期",
            "initial_credits": "25",
            "valid_days": 7,
            "package_price_credits": "9.9",
            "count": 1,
        },
        headers=admin_headers,
    ).json()[0]
    code = created["code"]
    login = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": code},
        headers=TEST_API_HEADERS,
    ).json()
    old_expiry = datetime.fromisoformat(login["access_expires_at"])
    old_token = login["token"]

    renewed = test_client.post(
        f"/api/v1/admin/codes/{code}/extend",
        json={"days": 7, "package_price_credits": "9.9"},
        headers=admin_headers,
    )
    assert renewed.status_code == 200
    payload = renewed.json()
    assert payload["package_price_credits"] == "9.9"
    assert payload["balance"] == "25"
    next_expiry = datetime.fromisoformat(payload["access_expires_at"])
    assert next_expiry - old_expiry == timedelta(days=7)

    stale = test_client.get("/api/v1/credits", headers={"X-Customer-Token": old_token})
    assert stale.status_code == 401
    next_login = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": code},
        headers=TEST_API_HEADERS,
    )
    assert next_login.status_code == 200
    assert next_login.json()["balance"] == "25"


def test_adjust_unused_access_package_does_not_start_countdown(client):
    """未使用激活码只调整套餐；首次登录前不得提前消耗天数。"""
    test_client, _ = client
    admin_headers = _admin_headers(test_client)
    created = test_client.post(
        "/api/v1/admin/codes/generate",
        json={"name": "待激活客户", "count": 1},
        headers=admin_headers,
    ).json()[0]
    code = created["code"]
    assert created["valid_days"] == 7
    assert created["package_price_credits"] == "9.9"
    assert created["initial_credits"] == "9.9"
    assert created["balance"] == "9.9"

    adjusted = test_client.post(
        f"/api/v1/admin/codes/{code}/extend",
        json={"days": 14, "package_price_credits": "19.8"},
        headers=admin_headers,
    )
    assert adjusted.status_code == 200
    payload = adjusted.json()
    assert payload["valid_days"] == 14
    assert payload["package_price_credits"] == "19.8"
    assert payload["access_status"] == "unused"
    assert payload["activated_at"] is None
    assert payload["access_expires_at"] is None
    assert payload["balance"] == "9.9"

    before_login = datetime.now().astimezone()
    login = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": code},
        headers=TEST_API_HEADERS,
    ).json()
    activated_at = datetime.fromisoformat(login["activated_at"])
    expires_at = datetime.fromisoformat(login["access_expires_at"])
    assert activated_at >= before_login
    assert expires_at - activated_at == timedelta(days=14)
    assert login["balance"] == "9.9"


def test_renew_expired_access_starts_from_renewal_time(client):
    """过期码续期从续期时重新起算，同时保留原有内容积分。"""
    test_client, repo = client
    now = datetime.now().astimezone()
    repo.create_customer_codes(
        [
            CustomerCode(
                code="EXPIRED-RENEW-1",
                name="续期客户",
                initial_credits="25",
                valid_days=7,
                package_price_credits="9.9",
                activated_at=now - timedelta(days=8),
                access_expires_at=now - timedelta(days=1),
                created_at=now - timedelta(days=8),
                updated_at=now - timedelta(days=1),
            )
        ]
    )
    repo.ensure_credit_account("EXPIRED-RENEW-1")

    before_renewal = datetime.now().astimezone()
    renewed = test_client.post(
        "/api/v1/admin/codes/EXPIRED-RENEW-1/extend",
        json={"days": 7, "package_price_credits": "9.9"},
        headers=_admin_headers(test_client),
    )
    after_renewal = datetime.now().astimezone()
    assert renewed.status_code == 200
    payload = renewed.json()
    expires_at = datetime.fromisoformat(payload["access_expires_at"])
    assert before_renewal + timedelta(days=7) <= expires_at
    assert expires_at <= after_renewal + timedelta(days=7)
    assert payload["access_status"] == "active"
    assert payload["balance"] == "25"

    login = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "EXPIRED-RENEW-1"},
        headers=TEST_API_HEADERS,
    )
    assert login.status_code == 200
    assert login.json()["balance"] == "25"


def test_list_and_toggle_codes(client):
    """客户列表含余额；禁用后客户无法登录。"""
    test_client, repo = client
    created = test_client.post(
        "/api/v1/admin/codes/generate",
        json={"name": "客户丁", "initial_credits": "400", "count": 1},
        headers=_admin_headers(test_client),
    ).json()[0]
    code = created["code"]
    # 列表
    listing = test_client.get(
        "/api/v1/admin/codes", headers=_admin_headers(test_client)
    ).json()
    assert any(item["code"] == code for item in listing)
    active_token = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": code},
        headers=TEST_API_HEADERS,
    ).json()["token"]
    # 禁用
    resp = test_client.post(
        f"/api/v1/admin/codes/{code}/toggle", headers=_admin_headers(test_client)
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    # 已签发的客户 token 也要立刻失效，不能等 12 小时自然过期。
    stale = test_client.get(
        "/api/v1/credits", headers={"X-Customer-Token": active_token}
    )
    assert stale.status_code == 401
    # 禁用后客户登录被拒
    login = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": code},
        headers=TEST_API_HEADERS,
    )
    assert login.status_code == 401


def test_admin_accounts_management(client):
    """主管理员可管理其他账号；普通管理员仅能修改自己。"""
    test_client, repo = client
    admin_headers = _admin_headers(test_client)
    # 新增管理员
    resp = test_client.post(
        "/api/v1/admin/accounts",
        json={"username": "boss2", "password": "boss-pass-123"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    # 重复创建：400
    resp = test_client.post(
        "/api/v1/admin/accounts",
        json={"username": "boss2", "password": "boss-pass-123"},
        headers=admin_headers,
    )
    assert resp.status_code == 400
    # 新管理员登录
    login = test_client.post(
        "/api/v1/auth/admin-login",
        json={"username": "boss2", "password": "boss-pass-123"},
        headers=TEST_API_HEADERS,
    )
    assert login.status_code == 200
    boss_headers = {**TEST_API_HEADERS, "X-Admin-Token": login.json()["token"]}
    # 列表
    listing = test_client.get(
        "/api/v1/admin/accounts", headers=_admin_headers(test_client)
    ).json()
    assert {item["username"] for item in listing} >= {"admin", "boss2"}
    permissions = {item["username"]: item for item in listing}
    assert permissions["admin"]["is_current"] is True
    assert permissions["admin"]["can_delete"] is False
    assert permissions["boss2"]["can_reset_password"] is True
    assert permissions["boss2"]["can_delete"] is True
    # 普通管理员不能重置其他账号，也不能删除账号
    resp = test_client.post(
        "/api/v1/admin/accounts/admin/password",
        json={"password": "cannot-reset-admin-123"},
        headers=boss_headers,
    )
    assert resp.status_code == 403
    resp = test_client.delete("/api/v1/admin/accounts/admin", headers=boss_headers)
    assert resp.status_code == 403
    # 重置密码
    resp = test_client.post(
        "/api/v1/admin/accounts/boss2/password",
        json={"password": "new-pass-456"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    login = test_client.post(
        "/api/v1/auth/admin-login",
        json={"username": "boss2", "password": "new-pass-456"},
        headers=TEST_API_HEADERS,
    )
    assert login.status_code == 200
    # 主管理员不能删除自己，但可以删除不再使用的测试管理员
    resp = test_client.delete("/api/v1/admin/accounts/admin", headers=admin_headers)
    assert resp.status_code == 400
    resp = test_client.delete("/api/v1/admin/accounts/boss2", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == {"username": "boss2", "deleted": True}
    assert repo.get_admin_account("boss2") is None
    login = test_client.post(
        "/api/v1/auth/admin-login",
        json={"username": "boss2", "password": "new-pass-456"},
        headers=TEST_API_HEADERS,
    )
    assert login.status_code == 401


def test_admin_pricing_list_and_update(client, monkeypatch):
    """管理员可查看全部定价并调整一项价格（改完生效）。"""
    test_client, repo = client
    # 隔离定价仓库：API 内部用真实库单例，测试注入临时库
    import src.services.pricing as pricing

    monkeypatch.setattr(pricing, "_PRICE_CACHE", {})
    monkeypatch.setattr(pricing, "_pricing_repository", lambda: repo)
    admin_login = test_client.post(
        "/api/v1/auth/admin-login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        headers=TEST_API_HEADERS,
    ).json()
    admin_headers = {**TEST_API_HEADERS, "X-Admin-Token": admin_login["token"]}

    resp = test_client.get("/api/v1/admin/pricing", headers=admin_headers)
    assert resp.status_code == 200
    items = {item["key"]: item for item in resp.json()}
    assert "crawler_monthly_limit_cny" not in items
    assert items["avatar_voice_training_credits"]["value"] == "60"

    resp = test_client.put(
        "/api/v1/admin/pricing",
        json={"key": "avatar_voice_training_credits", "value": "80"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == "80"

    resp = test_client.get("/api/v1/admin/pricing", headers=admin_headers)
    items = {item["key"]: item for item in resp.json()}
    assert items["avatar_voice_training_credits"]["value"] == "80"
    assert items["avatar_voice_training_credits"]["overridden"] is True

    # 未知项与负数被拒绝
    resp = test_client.put(
        "/api/v1/admin/pricing",
        json={"key": "unknown", "value": "1"},
        headers=admin_headers,
    )
    assert resp.status_code == 400

    # 未登录（仅 API Key）访问定价被拒
    resp = test_client.get("/api/v1/admin/pricing", headers=TEST_API_HEADERS)
    assert resp.status_code == 401
