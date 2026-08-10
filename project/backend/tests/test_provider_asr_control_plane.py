from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from project.backend.app.control_plane import app
from project.backend.app.core.repository import get_repository
from project.backend.app.core.media_probe import get_media_duration_probe
from project.backend.app.core.security import _auth_tokens
from project.backend.app.core.server_asr import get_server_asr_runtime
from src.models import CustomerCode
from src.repositories.sqlite import SQLiteRepository
from src.services.video_editor_cloud import CloudAsset


class _FakeASRRuntime:
    authorization_store = SimpleNamespace()

    def __init__(self) -> None:
        self.upload_count = 0

    @staticmethod
    def capability():
        return {
            "provider_mode": "aliyun",
            "provider_name": "fake_asr",
            "enabled": True,
            "live_ready": True,
            "missing_configuration": [],
            "is_mock": False,
            "billing_authorized": True,
            "unit_price_cny_per_second": 0.001,
            "per_task_cost_cap_cny": 1,
            "price_version": "test-price",
            "supports_local_fallback": False,
        }

    @staticmethod
    def ensure_authorized(duration_seconds: float) -> Decimal:
        return Decimal(str(duration_seconds)) * Decimal("0.001")

    def upload(self, path, *, object_key: str, media_type: str) -> CloudAsset:
        self.upload_count += 1
        return CloudAsset(
            provider_name="fake_store",
            bucket="fake-bucket",
            object_key=object_key,
            uri=f"fake://{object_key}",
            media_type=media_type,
            size_bytes=path.stat().st_size,
            is_mock=False,
        )


def test_asr_charge_and_upload_are_central_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("ASR_MODE", "cloud")
    repository = SQLiteRepository(tmp_path / "asr-control-plane.db")
    now = datetime.now().astimezone()
    repository.create_customer_codes(
        [
            CustomerCode(
                code="ASR-CLOUD",
                name="转写客户",
                initial_credits="10",
                created_at=now,
                updated_at=now,
            )
        ]
    )
    runtime = _FakeASRRuntime()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_server_asr_runtime] = lambda: runtime
    app.dependency_overrides[get_media_duration_probe] = lambda: (lambda _path: 40.0)
    _auth_tokens.clear()
    task_id = f"transcript-{uuid4().hex[:10]}"
    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/v1/auth/customer-login", json={"code": "ASR-CLOUD"}
            )
            token = login.json()["token"]
            charge_headers = {
                "X-Customer-Token": token,
                "Idempotency-Key": f"asr-charge-{task_id}",
            }
            charge = client.post(
                "/api/v1/provider/asr/authorize",
                headers=charge_headers,
                json={"task_id": task_id, "duration_seconds": 50},
            )
            assert charge.status_code == 200
            assert charge.json()["charged_credits"] == "0.05"

            replay = client.post(
                "/api/v1/provider/asr/authorize",
                headers=charge_headers,
                json={"task_id": task_id, "duration_seconds": 50},
            )
            assert replay.status_code == 200
            assert replay.headers["x-idempotent-replay"] == "true"
            assert repository.get_credit_balance("ASR-CLOUD") == Decimal("9.95")

            media = b"small authorized media"
            content_hash = hashlib.sha256(media).hexdigest()
            upload = client.post(
                "/api/v1/provider/asr/upload",
                headers={
                    "X-Customer-Token": token,
                    "Idempotency-Key": f"asr-upload-{task_id}",
                    "X-Content-SHA256": content_hash,
                    "X-Operation-Fingerprint": hashlib.sha256(
                        f"{task_id}:{content_hash}".encode()
                    ).hexdigest(),
                },
                data={
                    "task_id": task_id,
                    "object_key": f"asr-input/{task_id}/input.mp4",
                    "media_type": "video/mp4",
                },
                files={"file": ("input.mp4", media, "video/mp4")},
            )
            assert upload.status_code == 200
            assert upload.json()["object_key"].startswith(f"asr-input/{task_id}/")
            assert runtime.upload_count == 1
            assert repository.get_credit_balance("ASR-CLOUD") == Decimal("9.96")
    finally:
        app.dependency_overrides.clear()
        _auth_tokens.clear()


def test_asr_invalid_upload_refunds_once_and_voids_charge(tmp_path, monkeypatch):
    monkeypatch.setenv("ASR_MODE", "cloud")
    repository = SQLiteRepository(tmp_path / "asr-invalid-upload.db")
    now = datetime.now().astimezone()
    customer_code = f"ASR-{uuid4().hex[:10].upper()}"
    repository.create_customer_codes(
        [
            CustomerCode(
                code=customer_code,
                name="转写退款客户",
                initial_credits="10",
                created_at=now,
                updated_at=now,
            )
        ]
    )
    runtime = _FakeASRRuntime()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_server_asr_runtime] = lambda: runtime
    app.dependency_overrides[get_media_duration_probe] = lambda: (lambda _path: 40.0)
    _auth_tokens.clear()
    task_id = f"transcript-{uuid4().hex[:10]}"
    try:
        with TestClient(app) as client:
            token = client.post(
                "/api/v1/auth/customer-login", json={"code": customer_code}
            ).json()["token"]
            charge = client.post(
                "/api/v1/provider/asr/authorize",
                headers={
                    "X-Customer-Token": token,
                    "Idempotency-Key": f"asr-charge-{task_id}",
                },
                json={"task_id": task_id, "duration_seconds": 50},
            )
            assert charge.status_code == 200
            assert repository.get_credit_balance(customer_code) == Decimal("9.95")

            media = b"invalid upload"
            bad_hash = "0" * 64
            upload_data = {
                "task_id": task_id,
                "object_key": f"asr-input/{task_id}/input.mp4",
                "media_type": "video/mp4",
            }
            invalid = client.post(
                "/api/v1/provider/asr/upload",
                headers={
                    "X-Customer-Token": token,
                    "Idempotency-Key": f"asr-upload-invalid-{task_id}",
                    "X-Content-SHA256": bad_hash,
                    "X-Operation-Fingerprint": hashlib.sha256(
                        f"{task_id}:{bad_hash}".encode()
                    ).hexdigest(),
                },
                data=upload_data,
                files={"file": ("input.mp4", media, "video/mp4")},
            )
            assert invalid.status_code == 400
            assert repository.get_credit_balance(customer_code) == Decimal("10")

            blocked = client.post(
                "/api/v1/provider/asr/upload",
                headers={
                    "X-Customer-Token": token,
                    "Idempotency-Key": f"asr-upload-retry-{task_id}",
                    "X-Content-SHA256": hashlib.sha256(media).hexdigest(),
                    "X-Operation-Fingerprint": hashlib.sha256(
                        uuid4().bytes
                    ).hexdigest(),
                },
                data=upload_data,
                files={"file": ("input.mp4", media, "video/mp4")},
            )
            assert blocked.status_code == 409, blocked.text
            assert "已退回" in blocked.json()["message"]
            assert repository.get_credit_balance(customer_code) == Decimal("10")
            assert runtime.upload_count == 0
    finally:
        app.dependency_overrides.clear()
        _auth_tokens.clear()


def test_asr_provider_is_fail_closed_while_server_mode_is_sandbox(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ASR_MODE", "sandbox")
    repository = SQLiteRepository(tmp_path / "asr-disabled.db")
    now = datetime.now().astimezone()
    repository.create_customer_codes(
        [
            CustomerCode(
                code="ASR-OFF",
                name="未启用转写客户",
                initial_credits="10",
                created_at=now,
                updated_at=now,
            )
        ]
    )
    runtime = _FakeASRRuntime()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_server_asr_runtime] = lambda: runtime
    _auth_tokens.clear()
    try:
        with TestClient(app) as client:
            token = client.post(
                "/api/v1/auth/customer-login",
                json={"code": "ASR-OFF"},
            ).json()["token"]
            headers = {"X-Customer-Token": token}
            capability = client.get(
                "/api/v1/provider/asr/capabilities",
                headers=headers,
            )
            assert capability.status_code == 200
            assert capability.json()["enabled"] is False
            assert "ASR_MODE=cloud" in capability.json()["missing_configuration"]

            task_id = f"transcript-{uuid4().hex[:10]}"
            blocked = client.post(
                "/api/v1/provider/asr/authorize",
                headers={
                    **headers,
                    "Idempotency-Key": f"asr-charge-{task_id}",
                },
                json={"task_id": task_id, "duration_seconds": 30},
            )
            assert blocked.status_code == 503
            assert repository.get_credit_balance("ASR-OFF") == Decimal("10")
    finally:
        app.dependency_overrides.clear()
        _auth_tokens.clear()
