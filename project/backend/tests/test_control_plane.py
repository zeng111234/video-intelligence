from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from project.backend.app.control_plane import app
from project.backend.app.core.copywriting import get_copywriting_service
from project.backend.app.core.deps import get_repository
from project.backend.app.core.security import _auth_tokens, issue_auth_token
from src.adapters.llm import SandboxCopywritingEngine
from src.models import CustomerCode
from src.repositories.sqlite import SQLiteRepository
from src.services.copywriting import CopywritingService


def test_control_plane_customer_login_and_authoritative_balance(tmp_path):
    repository = SQLiteRepository(tmp_path / "control-plane.db")
    now = datetime.now().astimezone()
    repository.create_customer_codes(
        [
            CustomerCode(
                code="CLOUD-01",
                name="云端客户",
                initial_credits="88",
                created_at=now,
                updated_at=now,
            )
        ]
    )
    app.dependency_overrides[get_repository] = lambda: repository
    _auth_tokens.clear()
    try:
        with TestClient(app) as client:
            unauthorized = client.get("/api/v1/credits")
            assert unauthorized.status_code == 401

            login = client.post(
                "/api/v1/auth/customer-login", json={"code": "cloud-01"}
            )
            assert login.status_code == 200
            payload = login.json()
            assert payload["balance"] == "88"

            balance = client.get(
                "/api/v1/credits",
                headers={"X-Customer-Token": payload["token"]},
            )
            assert balance.status_code == 200
            assert balance.json()["balance"] == "88"
            assert balance.headers["x-frame-options"] == "DENY"
            assert balance.headers["cache-control"] == "no-store"
    finally:
        app.dependency_overrides.clear()
        _auth_tokens.clear()


def test_control_plane_copywriting_requires_customer_session(tmp_path):
    repository = SQLiteRepository(tmp_path / "copywriting-control-plane.db")
    now = datetime.now().astimezone()
    repository.create_customer_codes(
        [
            CustomerCode(
                code="COPY-01",
                name="文案客户",
                initial_credits="10",
                created_at=now,
                updated_at=now,
            )
        ]
    )
    service = CopywritingService(repository, SandboxCopywritingEngine())
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_copywriting_service] = lambda: service
    _auth_tokens.clear()
    try:
        with TestClient(app) as client:
            unauthorized = client.post(
                "/api/v1/copywriting/generate",
                json={"content_brief": "介绍一家街角咖啡店"},
            )
            assert unauthorized.status_code == 401

            login = client.post(
                "/api/v1/auth/customer-login", json={"code": "copy-01"}
            )
            token = login.json()["token"]
            missing_request_id = client.post(
                "/api/v1/copywriting/generate",
                headers={"X-Customer-Token": token},
                json={"content_brief": "介绍一家街角咖啡店"},
            )
            assert missing_request_id.status_code == 400

            operation_key = f"copy-test-{uuid4().hex}"
            generated = client.post(
                "/api/v1/copywriting/generate",
                headers={
                    "X-Customer-Token": token,
                    "Idempotency-Key": operation_key,
                },
                json={"content_brief": "介绍一家街角咖啡店"},
            )
            assert generated.status_code == 200
            assert generated.json()["is_mock"] is True

            replay = client.post(
                "/api/v1/copywriting/generate",
                headers={
                    "X-Customer-Token": token,
                    "Idempotency-Key": operation_key,
                },
                json={"content_brief": "介绍一家街角咖啡店"},
            )
            assert replay.status_code == 200
            assert replay.headers["x-idempotent-replay"] == "true"
            assert replay.json()["task_id"] == generated.json()["task_id"]

            history = client.get(
                "/api/v1/copywriting",
                headers={"X-Customer-Token": token},
            )
            assert history.status_code == 200
            assert len(history.json()) == 1

            provider_result = client.post(
                "/api/v1/provider/copywriting/generate",
                headers={
                    "X-Customer-Token": token,
                    "Idempotency-Key": f"provider-test-{uuid4().hex}",
                },
                json={"content_brief": "介绍一家安静的社区书店"},
            )
            assert provider_result.status_code == 200
            assert isinstance(provider_result.json()["result"], list)
            assert provider_result.json()["is_mock"] is True
    finally:
        app.dependency_overrides.clear()
        _auth_tokens.clear()


def test_admin_server_status_never_exposes_secret_values():
    _auth_tokens.clear()
    token = issue_auth_token("admin", "admin")
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/admin/server-status",
                headers={"X-Admin-Token": token},
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["crawler"]["location"] == "customer_desktop"
        assert payload["crawler"]["billable"] is False
        serialized = response.text.casefold()
        assert "api_key" not in serialized
        assert "access_key" not in serialized
        assert "secret" not in serialized
    finally:
        _auth_tokens.clear()
