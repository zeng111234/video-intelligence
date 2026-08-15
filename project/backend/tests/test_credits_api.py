"""积分管理 API 集成测试：管理员登录、查询余额、加/减积分、余额不足被阻止。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 确保项目根目录在 Python 路径中
_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from project.backend.app.main import app  # noqa: E402
from project.backend.app.core import deps as backend_deps  # noqa: E402
from src.repositories import SQLiteRepository  # noqa: E402
from src.services.credits import CreditsService  # noqa: E402

TEST_API_HEADERS = {"X-API-Key": "pytest-api-key"}
TEST_ADMIN_PASSWORD = "pytest-admin-password"


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    # 与根 tests/conftest.py 一致：注入测试 API Key 与管理密码，避免 .env 干扰
    monkeypatch.setenv("API_KEY", "pytest-api-key")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    # 旧断言基于初始余额 0；默认赠送（400）另行验证
    monkeypatch.setenv("DEFAULT_CREDIT_BALANCE", "0")
    repository = SQLiteRepository(tmp_path / "credits-api.db")
    credits = CreditsService(repository)

    app.dependency_overrides[backend_deps.get_repository] = lambda: repository
    app.dependency_overrides[backend_deps.get_credits_service] = lambda: credits
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _login(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/credits/login",
        json={"password": TEST_ADMIN_PASSWORD},
        headers=TEST_API_HEADERS,
    )
    assert resp.status_code == 200
    return resp.json()["token"]


def _admin_headers(token: str) -> dict[str, str]:
    return {**TEST_API_HEADERS, "X-Admin-Token": token}


def test_initial_balance_is_zero(client: TestClient) -> None:
    resp = client.get("/api/v1/credits", headers=TEST_API_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["balance"] == "0"
    assert data["transactions"] == []


def test_login_wrong_password_is_401(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/credits/login",
        json={"password": "wrong-password"},
        headers=TEST_API_HEADERS,
    )
    assert resp.status_code == 401
    assert "管理密码不正确" in resp.json()["message"]


def test_adjust_without_admin_token_is_401(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/credits/adjust",
        json={"amount": "10", "reason": "管理员充值"},
        headers=TEST_API_HEADERS,
    )
    assert resp.status_code == 401
    assert "管理员登录" in resp.json()["message"]


def test_adjust_credit_increases_balance(client: TestClient) -> None:
    token = _login(client)
    resp = client.post(
        "/api/v1/credits/adjust",
        json={"amount": "10", "reason": "管理员充值", "owner": "TEST-CUSTOMER"},
        headers=_admin_headers(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["balance"] == "10"
    assert len(data["transactions"]) == 1
    assert data["transactions"][0]["amount"] == "10"
    assert data["transactions"][0]["reason"] == "管理员充值"


def test_adjust_credit_negative_deducts(client: TestClient) -> None:
    token = _login(client)
    client.post(
        "/api/v1/credits/adjust",
        json={"amount": "10", "reason": "充值", "owner": "TEST-CUSTOMER"},
        headers=_admin_headers(token),
    )
    resp = client.post(
        "/api/v1/credits/adjust",
        json={"amount": "-4", "reason": "转写扣费", "owner": "TEST-CUSTOMER"},
        headers=_admin_headers(token),
    )
    assert resp.status_code == 200
    assert resp.json()["balance"] == "6"


def test_adjust_credit_insufficient_is_400(client: TestClient) -> None:
    token = _login(client)
    resp = client.post(
        "/api/v1/credits/adjust",
        json={"amount": "-5", "reason": "扣费", "owner": "TEST-CUSTOMER"},
        headers=_admin_headers(token),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "积分不足" in body["message"]


def test_adjust_credit_requires_reason(client: TestClient) -> None:
    token = _login(client)
    resp = client.post(
        "/api/v1/credits/adjust",
        json={"amount": "5"},
        headers=_admin_headers(token),
    )
    assert resp.status_code == 422


def test_admin_usage_groups_real_debits_by_project_and_customer(client: TestClient) -> None:
    token = _login(client)
    headers = _admin_headers(token)
    client.post(
        "/api/v1/credits/adjust",
        json={"amount": "10", "reason": "充值", "owner": "CUSTOMER-A"},
        headers=headers,
    )
    client.post(
        "/api/v1/credits/adjust",
        json={
            "amount": "-1.25",
            "reason": "转写扣费",
            "owner": "CUSTOMER-A",
            "ref_type": "transcription",
            "ref_id": "task-1",
        },
        headers=headers,
    )
    client.post(
        "/api/v1/credits/adjust",
        json={
            "amount": "-2.5",
            "reason": "剪辑扣费",
            "owner": "CUSTOMER-A",
            "ref_type": "video_editor",
            "ref_id": "batch-1",
        },
        headers=headers,
    )

    response = client.get("/api/v1/credits/admin/usage", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_consumed"] == "3.75"
    assert {item["name"]: item["consumed"] for item in payload["by_project"]} == {
        "云端剪辑": "2.5",
        "云端转写": "1.25",
    }
    assert payload["by_customer"][0]["key"] == "CUSTOMER-A"
    assert payload["by_customer"][0]["consumed"] == "3.75"
    assert len(payload["recent_transactions"]) == 2


def test_admin_usage_requires_admin_login(client: TestClient) -> None:
    response = client.get("/api/v1/credits/admin/usage", headers=TEST_API_HEADERS)
    assert response.status_code == 401
