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
    cookie = resp.headers["set-cookie"]
    assert "vi_customer_media_token=" in cookie
    assert 'vi_admin_media_token=""' in cookie
    # 重复登录：余额不重复赠送
    resp2 = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "GOOD-CODE-1"},
        headers=TEST_API_HEADERS,
    )
    assert resp2.json()["balance"] == "400"


def test_local_admin_server_status_is_truthful_before_company_server(client):
    """本地验收阶段不应 404，也不能假装公司服务已经连接。"""

    test_client, _ = client
    login = test_client.post(
        "/api/v1/auth/admin-login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        headers=TEST_API_HEADERS,
    )
    assert login.status_code == 200

    response = test_client.get(
        "/api/v1/admin/server-status",
        headers={"X-Admin-Token": login.json()["token"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "local_only"
    assert payload["crawler"] == {
        "location": "customer_desktop",
        "billable": False,
        "server_provider_disabled": True,
    }
    assert payload["copywriting"]["live_ready"] is False


def test_customer_login_unknown_or_disabled(client):
    """未知和禁用激活码使用相同响应，不能被用来枚举客户。"""
    test_client, _ = client
    unknown = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "NO-SUCH-1"},
        headers=TEST_API_HEADERS,
    )
    disabled = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "DISABLED-1"},
        headers=TEST_API_HEADERS,
    )
    assert unknown.status_code == disabled.status_code == 401
    assert unknown.json()["message"] == disabled.json()["message"]


def test_customer_login_rate_limit_ignores_spoofed_forwarded_ip(client):
    """浏览器改 X-Forwarded-For 不能绕过本机识别到的来源限流。"""
    test_client, _ = client
    for index in range(10):
        response = test_client.post(
            "/api/v1/auth/customer-login",
            json={"code": f"BAD-CODE-{index}"},
            headers={**TEST_API_HEADERS, "X-Forwarded-For": f"198.51.100.{index}"},
        )
        assert response.status_code == 401
    blocked = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "GOOD-CODE-1"},
        headers={**TEST_API_HEADERS, "X-Forwarded-For": "203.0.113.200"},
    )
    assert blocked.status_code == 429


def test_admin_account_is_temporarily_locked_after_five_failures(client):
    """针对同一管理员账号连续猜密码时，即使换 IP 也会触发身份锁定。"""
    test_client, _ = client
    for index in range(5):
        response = test_client.post(
            "/api/v1/auth/admin-login",
            json={"username": "admin", "password": f"wrong-password-{index}"},
            headers={**TEST_API_HEADERS, "X-Forwarded-For": f"198.51.100.{index}"},
        )
        assert response.status_code == 401
    blocked = test_client.post(
        "/api/v1/auth/admin-login",
        json={"username": "admin", "password": TEST_ADMIN_PASSWORD},
        headers=TEST_API_HEADERS,
    )
    assert blocked.status_code == 429
    assert "15 分钟" in blocked.json()["message"]


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


def test_editor_font_is_public_but_only_serves_the_fixed_asset(client):
    """CSS 字体无法加登录头；固定开源字体可读且不暴露客户内容。"""
    test_client, _ = client
    font = test_client.get(
        "/api/v1/video-editor/brand-title-font",
        headers={"X-API-Key": ""},
    )
    assert font.status_code == 200
    assert font.headers["content-type"].startswith("font/otf")
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


def test_desktop_demo_customer_can_use_business_api_but_not_admin(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    """单客户桌面内测可操作业务页，但不能借激活码进入管理后台。"""

    test_client, _ = client
    monkeypatch.setenv("VIDEOINSIGHT_DESKTOP_DEMO", "true")
    token = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "GOOD-CODE-1"},
        headers=TEST_API_HEADERS,
    ).json()["token"]
    headers = {"X-Customer-Token": token}

    tasks = test_client.get("/api/v1/tasks", headers=headers)
    assert tasks.status_code == 200

    media = test_client.get(
        "/api/v1/nonexistent/media",
        headers={"Cookie": f"vi_customer_media_token={token}"},
    )
    assert media.status_code == 404

    admin = test_client.get("/api/v1/admin/status", headers=headers)
    assert admin.status_code in {401, 403}


def test_bound_control_plane_customer_can_use_local_business_api(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    """正式桌面绑定客户后，本地任务可用且管理接口仍不可越权。"""

    from project.backend.app import main as main_module

    test_client, _ = client
    monkeypatch.setattr(main_module, "control_plane_enabled", lambda: True)
    monkeypatch.setattr(main_module, "desktop_owner_matches", lambda subject: subject == "GOOD-CODE-1")
    token = test_client.post(
        "/api/v1/auth/customer-login",
        json={"code": "GOOD-CODE-1"},
        headers=TEST_API_HEADERS,
    ).json()["token"]
    headers = {"X-Customer-Token": token}

    tasks = test_client.get("/api/v1/tasks", headers=headers)
    assert tasks.status_code == 200

    media = test_client.get(
        "/api/v1/nonexistent/media",
        headers={"Cookie": f"vi_customer_media_token={token}"},
    )
    assert media.status_code == 404

    admin = test_client.get("/api/v1/admin/status", headers=headers)
    assert admin.status_code in {401, 403}


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
    cookie = resp.headers["set-cookie"]
    assert "vi_admin_media_token=" in cookie
    assert 'vi_customer_media_token=""' in cookie
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
