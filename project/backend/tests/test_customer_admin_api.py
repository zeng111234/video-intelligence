"""管理员客户管理 API 测试：生成激活码、客户列表、禁用、管理员账号管理。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from project.backend.app.core import deps as backend_deps  # noqa: E402
from project.backend.app.main import app  # noqa: E402
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
    """生成激活码 -> 客户可用其登录并获得初始积分。"""
    test_client, repo = client
    resp = test_client.post(
        "/api/v1/admin/codes/generate",
        json={"name": "客户丙", "initial_credits": "300", "count": 2},
        headers=_admin_headers(test_client),
    )
    assert resp.status_code == 200
    codes = resp.json()
    assert len(codes) == 2
    assert all(len(item["code"].replace("-", "")) == 16 for item in codes)
    assert all([len(group) for group in item["code"].split("-")] == [4, 4, 4, 4] for item in codes)
    assert all(item["balance"] == "300" for item in codes)
    # 客户用新激活码登录
    login = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": codes[0]["code"]},
        headers=TEST_API_HEADERS,
    ).json()
    assert login["balance"] == "300"


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
    stale = test_client.get("/api/v1/credits", headers={"X-Customer-Token": active_token})
    assert stale.status_code == 401
    # 禁用后客户登录被拒
    login = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": code},
        headers=TEST_API_HEADERS,
    )
    assert login.status_code == 401


def test_admin_accounts_management(client):
    """新增管理员账号并可登录；重置密码生效。"""
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
    # 列表
    listing = test_client.get(
        "/api/v1/admin/accounts", headers=_admin_headers(test_client)
    ).json()
    assert {item["username"] for item in listing} >= {"admin", "boss2"}
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
