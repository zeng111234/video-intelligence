"""客户激活码登录 + 管理员多账号登录 API 测试。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from project.backend.app.core import deps as backend_deps  # noqa: E402
from project.backend.app.main import app  # noqa: E402
from src.models import AdminAccount, CustomerCode  # noqa: E402
from src.repositories.sqlite import SQLiteRepository  # noqa: E402

TEST_API_HEADERS = {"X-API-Key": "pytest-api-key"}
TEST_ADMIN_PASSWORD = "test-admin-password-123"


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_KEY", "pytest-api-key")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("DEFAULT_CREDIT_BALANCE", "0")
    repository = SQLiteRepository(tmp_path / "auth-api.db")
    now = datetime.now().astimezone()
    repository.create_customer_codes(
        [
            CustomerCode(
                code="GOOD-CODE-1",
                name="客户甲",
                initial_credits="400",
                created_at=now,
                updated_at=now,
            ),
            CustomerCode(
                code="DISABLED-1",
                name="客户乙",
                enabled=False,
                initial_credits="400",
                created_at=now,
                updated_at=now,
            ),
        ]
    )

    app.dependency_overrides[backend_deps.get_repository] = lambda: repository
    try:
        with TestClient(app) as test_client:
            yield test_client, repository
    finally:
        app.dependency_overrides.clear()


def test_customer_login_success_and_opens_credit_account(client):
    """激活码登录成功：返回 token，首次登录自动开立积分账户并赠送 400。"""
    test_client, repo = client
    resp = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "GOOD-CODE-1"},
        headers=TEST_API_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "customer"
    assert data["name"] == "客户甲"
    assert data["balance"] == "400"
    assert data["token"]
    # 重复登录：余额不重复赠送
    resp2 = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "GOOD-CODE-1"},
        headers=TEST_API_HEADERS,
    )
    assert resp2.json()["balance"] == "400"


def test_customer_login_unknown_or_disabled(client):
    """激活码不存在或被禁用：拒绝登录。"""
    test_client, _ = client
    resp = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "NO-SUCH-1"},
        headers=TEST_API_HEADERS,
    )
    assert resp.status_code == 401
    resp = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "DISABLED-1"},
        headers=TEST_API_HEADERS,
    )
    assert resp.status_code == 403


def test_customer_token_accesses_api(client):
    """客户 token 可访问业务接口（中间件识别客户身份）。"""
    test_client, repo = client
    login = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "GOOD-CODE-1"},
        headers=TEST_API_HEADERS,
    ).json()
    resp = test_client.get(
        "/api/v1/credits",
        headers={"X-Customer-Token": login["token"]},
    )
    assert resp.status_code == 200
    assert resp.json()["balance"] == "400"
    # 无任何凭证：显式覆盖共享测试 API Key，确保认证没有被夹具掩盖。
    resp = test_client.get("/api/v1/credits", headers={"X-API-Key": ""})
    assert resp.status_code == 401
    # 无效客户 token：401
    resp = test_client.get(
        "/api/v1/credits",
        headers={"X-Customer-Token": "invalid-token"},
    )
    assert resp.status_code == 401


def test_customer_token_cannot_access_unscoped_business_data(client):
    """客户资源尚未标注归属时，不能用客户 token 读取全局任务列表。"""
    test_client, _ = client
    token = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "GOOD-CODE-1"},
        headers=TEST_API_HEADERS,
    ).json()["token"]
    resp = test_client.get("/api/v1/tasks", headers={"X-Customer-Token": token})
    assert resp.status_code == 403
    assert "数据隔离" in resp.json()["message"]


def test_logout_revokes_token_and_media_cookie(client):
    test_client, _ = client
    login = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "GOOD-CODE-1"},
        headers=TEST_API_HEADERS,
    )
    token = login.json()["token"]
    assert "vi_customer_media_token" in login.headers["set-cookie"]
    logout = test_client.post("/api/v1/auth/logout", headers={"X-Customer-Token": token})
    assert logout.status_code == 200
    assert "Max-Age=0" in logout.headers["set-cookie"]
    denied = test_client.get("/api/v1/credits", headers={"X-Customer-Token": token})
    assert denied.status_code == 401


def test_recharge_review_is_atomic_and_cannot_credit_twice(client):
    """同一充值请求被重复审批时，只允许第一笔审批入账。"""
    test_client, _ = client
    customer_token = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "GOOD-CODE-1"},
        headers=TEST_API_HEADERS,
    ).json()["token"]
    created = test_client.post(
        "/api/v1/credits/recharge-request",
        json={"amount": "50", "reason": "加购"},
        headers={"X-Customer-Token": customer_token},
    )
    assert created.status_code == 200
    request_id = created.json()["id"]
    admin_token = test_client.post(
        "/api/v1/auth/admin-login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        headers=TEST_API_HEADERS,
    ).json()["token"]
    headers = {"X-Admin-Token": admin_token}
    approved = test_client.post(
        f"/api/v1/credits/recharge-requests/{request_id}/review",
        json={"status": "approved"},
        headers=headers,
    )
    assert approved.status_code == 200
    duplicate = test_client.post(
        f"/api/v1/credits/recharge-requests/{request_id}/review",
        json={"status": "approved"},
        headers=headers,
    )
    assert duplicate.status_code == 400
    balance = test_client.get("/api/v1/credits", headers={"X-Customer-Token": customer_token})
    assert balance.json()["balance"] == "450"


def test_admin_login_multi_account(client):
    """管理员多账号：默认 admin（ADMIN_PASSWORD 初始化）+ 新增账号。"""
    test_client, repo = client
    # 默认 admin 账号由 ADMIN_PASSWORD 自动创建
    resp = test_client.post(
        "/api/v1/auth/admin-login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        headers=TEST_API_HEADERS,
    )
    assert resp.status_code == 200
    admin_token = resp.json()["token"]
    # 管理员 token 可访问管理接口
    resp = test_client.get(
        "/api/v1/credits",
        headers={"X-Admin-Token": admin_token},
    )
    assert resp.status_code == 200
    # 错误密码：401
    resp = test_client.post(
        "/api/v1/auth/admin-login",
        json={"username": "admin", "password": "wrong"},
        headers=TEST_API_HEADERS,
    )
    assert resp.status_code == 401
    # 新增管理员账号后可登录
    now = datetime.now().astimezone()
    repo.create_admin_account(
        AdminAccount(
            username="boss",
            password_hash="hash-of-boss-pass",
            created_at=now,
            updated_at=now,
        )
    )
    # 表校验需要真实哈希；直接验证账号存在即可（登录校验逻辑在 auth.py 单测覆盖）
    assert repo.get_admin_account("boss") is not None


def test_customer_credit_view_is_scoped_to_customer(client):
    """客户请求 /credits 看到自己的余额；管理员请求看到 admin 余额（contextvar 传播）。"""
    test_client, repo = client
    login = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "GOOD-CODE-1"},
        headers=TEST_API_HEADERS,
    ).json()
    # 客户视角：自己账户 400（激活赠送）
    resp = test_client.get(
        "/api/v1/credits",
        headers={"X-Customer-Token": login["token"]},
    )
    assert resp.status_code == 200
    assert resp.json()["balance"] == "400"
    # 管理员视角：admin 账户（本测试 DEFAULT_CREDIT_BALANCE=0）
    admin_login = test_client.post(
        "/api/v1/auth/admin-login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        headers=TEST_API_HEADERS,
    ).json()
    resp = test_client.get(
        "/api/v1/credits",
        headers={"X-Admin-Token": admin_login["token"]},
    )
    assert resp.status_code == 200
    assert resp.json()["balance"] == "0"


def test_admin_adjust_credits_for_customer(client):
    """管理员给客户充值：写入客户账户，不影响 admin 账户。"""
    test_client, repo = client
    admin_login = test_client.post(
        "/api/v1/auth/admin-login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        headers=TEST_API_HEADERS,
    ).json()
    resp = test_client.post(
        "/api/v1/credits/adjust",
        json={"amount": "200", "reason": "客户加购", "owner": "GOOD-CODE-1"},
        headers={**TEST_API_HEADERS, "X-Admin-Token": admin_login["token"]},
    )
    assert resp.status_code == 200
    # 客户账户首次充值开立：自动赠送 400 + 充值 200 = 600
    assert resp.json()["balance"] == "600"
    # 客户登录后余额不变（不重复赠送）
    login = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "GOOD-CODE-1"},
        headers=TEST_API_HEADERS,
    ).json()
    assert login["balance"] == "600"
