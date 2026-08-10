from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from project.backend.app.control_plane import app
from project.backend.app.core.repository import get_repository
from project.backend.app.core.security import _auth_tokens
from project.backend.app.core.server_avatar import get_server_avatar_provider
from src.models import (
    AvatarAsset,
    AvatarAssetKind,
    AvatarCapability,
    AvatarJobSnapshot,
    AvatarProviderStatus,
    CustomerCode,
    ProviderMode,
)
from src.repositories.sqlite import SQLiteRepository
from src.services.credits import cny_to_credits
from src.services.avatar_billing import build_avatar_billing_quote
from src.services.pricing import get_price


class _FakeAvatarProvider:
    def __init__(self) -> None:
        self.submit_count = 0
        self.snapshots: dict[str, AvatarJobSnapshot] = {}
        self.job_prefix = uuid4().hex[:12]

    @staticmethod
    def capabilities() -> AvatarCapability:
        return AvatarCapability(
            provider_name="fake_avatar",
            display_name="测试云数字人",
            mode=ProviderMode.PRODUCTION,
            enabled=True,
            permission_status="authorized",
            max_script_chars=2000,
            estimated_cost_cny=999,
            estimated_seconds=45,
        )

    @staticmethod
    def list_assets() -> list[AvatarAsset]:
        return [
            AvatarAsset(
                asset_id="public-avatar-1",
                kind=AvatarAssetKind.AVATAR,
                name="测试形象",
                authorized=True,
            ),
            AvatarAsset(
                asset_id="public-voice-1",
                kind=AvatarAssetKind.VOICE,
                name="测试声音",
                authorized=True,
            ),
            AvatarAsset(
                asset_id="shared-avatar-1",
                kind=AvatarAssetKind.AVATAR,
                name="公司共享形象",
                authorized=True,
                source_type="custom",
                shared=True,
            ),
            AvatarAsset(
                asset_id="private-avatar-1",
                kind=AvatarAssetKind.AVATAR,
                name="其他客户私有形象",
                authorized=True,
                source_type="custom",
            ),
        ]

    def submit(self, request) -> AvatarJobSnapshot:
        self.submit_count += 1
        job_id = f"fake-job-{self.job_prefix}-{self.submit_count}"
        snapshot = AvatarJobSnapshot(
            job_id=job_id,
            idempotency_key=request.idempotency_key,
            status=AvatarProviderStatus.QUEUED,
            progress=5,
            stage="云端排队中",
            provider_job_id=job_id,
        )
        self.snapshots[snapshot.job_id] = snapshot
        return snapshot

    def get_job(self, job_id: str) -> AvatarJobSnapshot:
        return self.snapshots[job_id]

    def complete(self, job_id: str, *, seconds: int) -> None:
        self.snapshots[job_id] = self.snapshots[job_id].model_copy(
            update={
                "status": AvatarProviderStatus.SUCCEEDED,
                "progress": 100,
                "stage": "云端生成成功",
                "estimated_seconds": seconds,
            }
        )

    def find_job(self, idempotency_key: str) -> AvatarJobSnapshot | None:
        return next(
            (
                snapshot
                for snapshot in self.snapshots.values()
                if snapshot.idempotency_key == idempotency_key
            ),
            None,
        )


def _create_customer(repository, code: str) -> None:
    now = datetime.now().astimezone()
    repository.create_customer_codes(
        [
            CustomerCode(
                code=code,
                name=code,
                initial_credits="20",
                created_at=now,
                updated_at=now,
            )
        ]
    )


def _login(client: TestClient, code: str) -> str:
    response = client.post("/api/v1/auth/customer-login", json={"code": code})
    assert response.status_code == 200
    return response.json()["token"]


def test_company_shared_avatar_is_visible_but_other_customer_asset_is_hidden(tmp_path):
    repository = SQLiteRepository(tmp_path / "avatar-assets-control-plane.db")
    code = f"AVATAR-{uuid4().hex[:8].upper()}"
    _create_customer(repository, code)
    provider = _FakeAvatarProvider()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_server_avatar_provider] = lambda: provider
    _auth_tokens.clear()
    try:
        with TestClient(app) as client:
            token = _login(client, code)
            response = client.get(
                "/api/v1/provider/avatar/assets",
                headers={"X-Customer-Token": token},
            )
            assert response.status_code == 200
            asset_ids = {item["asset_id"] for item in response.json()}
            assert "public-avatar-1" in asset_ids
            assert "shared-avatar-1" in asset_ids
            assert "private-avatar-1" not in asset_ids
    finally:
        app.dependency_overrides.clear()
        _auth_tokens.clear()


def test_avatar_submission_is_centrally_billed_idempotent_and_tenant_isolated(tmp_path):
    repository = SQLiteRepository(tmp_path / "avatar-control-plane.db")
    first_code = f"AVATAR-{uuid4().hex[:8].upper()}"
    second_code = f"AVATAR-{uuid4().hex[:8].upper()}"
    _create_customer(repository, first_code)
    _create_customer(repository, second_code)
    provider = _FakeAvatarProvider()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_server_avatar_provider] = lambda: provider
    _auth_tokens.clear()
    request_key = f"avatar-{uuid4().hex[:12]}"
    try:
        with TestClient(app) as client:
            first_token = _login(client, first_code)
            second_token = _login(client, second_code)
            quoted = client.get(
                "/api/v1/provider/avatar/quote?characters=2&speech_rate=1",
                headers={"X-Customer-Token": first_token},
            )
            assert quoted.status_code == 200
            assert quoted.json()["reservation_seconds"] == 2
            assert Decimal(str(quoted.json()["reservation_credits"])) == Decimal("0.09")
            headers = {
                "X-Customer-Token": first_token,
                "Idempotency-Key": f"avatar-submit-{request_key}",
            }
            payload = {
                "request": {
                    "script_text": "这是一段已授权的测试文案。",
                    "avatar_id": "public-avatar-1",
                    "voice_id": "public-voice-1",
                    "profile_id": "default",
                    "speech_rate": 1,
                    "aspect_ratio": "9:16",
                    "resolution": "1080x1920",
                    "background": "solid",
                    "rights_holder": "测试公司",
                    "script_rights_confirmed": True,
                    "avatar_rights_confirmed": True,
                    "voice_rights_confirmed": True,
                    "idempotency_key": request_key,
                }
            }
            created = client.post(
                "/api/v1/provider/avatar/submit", headers=headers, json=payload
            )
            assert created.status_code == 200
            job_id = created.json()["job_id"]
            quote = build_avatar_billing_quote(
                script_text=payload["request"]["script_text"],
                speech_rate=1,
                price_per_minute_cny=get_price("avatar_per_minute_cny"),
            )
            reserved_credits = Decimal(str(quote.reservation_credits))
            assert created.json()["estimated_seconds"] == quote.reservation_seconds
            assert repository.get_credit_balance(first_code) == Decimal("20") - reserved_credits

            replay = client.post(
                "/api/v1/provider/avatar/submit", headers=headers, json=payload
            )
            assert replay.status_code == 200
            assert replay.headers["x-idempotent-replay"] == "true"
            assert provider.submit_count == 1
            assert repository.get_credit_balance(first_code) == Decimal("20") - reserved_credits

            provider.complete(job_id, seconds=2)
            final_charge = cny_to_credits(
                get_price("avatar_per_minute_cny") * Decimal(2) / Decimal(60)
            )

            owner_query = client.get(
                f"/api/v1/provider/avatar/jobs/{job_id}",
                headers={"X-Customer-Token": first_token},
            )
            assert owner_query.status_code == 200
            assert owner_query.json()["estimated_seconds"] == 2
            assert Decimal(str(owner_query.json()["estimated_cost_cny"])) == final_charge
            assert repository.get_credit_balance(first_code) == Decimal("20") - final_charge

            repeated_query = client.get(
                f"/api/v1/provider/avatar/jobs/{job_id}",
                headers={"X-Customer-Token": first_token},
            )
            assert repeated_query.status_code == 200
            assert repository.get_credit_balance(first_code) == Decimal("20") - final_charge
            transactions = repository.list_credit_transactions(
                owner=first_code, limit=20
            )
            assert sum(item["ref_type"] == "avatar_reserve" for item in transactions) == 1
            assert sum(item["ref_type"] == "avatar_settlement" for item in transactions) == 1
            other_query = client.get(
                f"/api/v1/provider/avatar/jobs/{job_id}",
                headers={"X-Customer-Token": second_token},
            )
            assert other_query.status_code == 404
    finally:
        app.dependency_overrides.clear()
        _auth_tokens.clear()
