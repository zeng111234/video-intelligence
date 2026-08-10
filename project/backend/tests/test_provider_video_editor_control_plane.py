from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from project.backend.app.control_plane import app
from project.backend.app.core.media_probe import get_media_duration_probe
from project.backend.app.core.repository import get_repository
from project.backend.app.core.security import _auth_tokens
from project.backend.app.core.server_video_editor import (
    get_server_video_editor_runtime,
)
from src.models import CustomerCode
from src.repositories.sqlite import SQLiteRepository
from src.services.credits import cny_to_credits
from src.services.video_editor_cloud import (
    CloudAsset,
    CloudEditorConfiguration,
    CloudProviderMode,
    create_cost_quote,
)


class _FakeObjectStore:
    def __init__(self) -> None:
        self.upload_count = 0

    def upload(self, path, object_key: str, *, media_type: str) -> CloudAsset:
        self.upload_count += 1
        return CloudAsset(
            provider_name="fake_store",
            bucket="private-test-bucket",
            object_key=object_key,
            uri=f"fake://{object_key}",
            media_type=media_type,
            size_bytes=path.stat().st_size,
            is_mock=False,
        )

    def presign_get_url(self, object_key: str, *, expires_seconds: int = 3600) -> str:
        assert expires_seconds == 3600
        return f"https://private-test-bucket.oss-cn-beijing.aliyuncs.com/{object_key}"


def _runtime():
    configuration = CloudEditorConfiguration(
        provider_mode=CloudProviderMode.ALIYUN,
        workspace_id="workspace",
        dashscope_api_key="server-only-test-key",
        oss_bucket="private-test-bucket",
        access_key_id="server-only-access-key",
        access_key_secret="server-only-secret",
        mps_pipeline_id="pipeline",
        mps_template_id_720p="template-720",
        mps_template_id_1080p="template-1080",
    )
    store = _FakeObjectStore()
    return SimpleNamespace(
        configuration=configuration,
        providers=SimpleNamespace(object_store=store),
    )


def test_video_editor_charge_replay_and_duration_reconciliation_are_central(tmp_path):
    repository = SQLiteRepository(tmp_path / "video-control-plane.db")
    now = datetime.now().astimezone()
    customer_code = f"VIDEO-{uuid4().hex[:10].upper()}"
    repository.create_customer_codes(
        [
            CustomerCode(
                code=customer_code,
                name="云剪辑客户",
                initial_credits="10",
                created_at=now,
                updated_at=now,
            )
        ]
    )
    runtime = _runtime()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_server_video_editor_runtime] = lambda: runtime
    app.dependency_overrides[get_media_duration_probe] = lambda: (lambda _path: 40.0)
    _auth_tokens.clear()
    batch_id = f"edit-batch-{uuid4().hex[:12]}"
    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/v1/auth/customer-login", json={"code": customer_code}
            )
            assert login.status_code == 200
            token = login.json()["token"]
            quote = create_cost_quote(
                input_duration_seconds=60,
                output_duration_seconds=61.4,
                output_profile="720p",
            )
            charge_headers = {
                "X-Customer-Token": token,
                "Idempotency-Key": f"video-charge-{batch_id}",
            }
            charge = client.post(
                "/api/v1/provider/video-editor/authorize",
                headers=charge_headers,
                json={
                    "batch_id": batch_id,
                    "quote": quote.model_dump(mode="json"),
                    "max_cost_cny": str(quote.estimated_max),
                },
            )
            assert charge.status_code == 200
            charged = Decimal(charge.json()["charged_credits"])
            assert charged > 0

            replay = client.post(
                "/api/v1/provider/video-editor/authorize",
                headers=charge_headers,
                json={
                    "batch_id": batch_id,
                    "quote": quote.model_dump(mode="json"),
                    "max_cost_cny": str(quote.estimated_max),
                },
            )
            assert replay.status_code == 200
            assert replay.headers["x-idempotent-replay"] == "true"
            assert repository.get_credit_balance(customer_code) == Decimal("10") - charged

            media = b"small authorized video"
            content_hash = hashlib.sha256(media).hexdigest()
            object_key = f"video-editor-input/{batch_id}/input/source.mp4"
            fingerprint = hashlib.sha256(
                f"{batch_id}\0{object_key}\0video/mp4\0{content_hash}".encode()
            ).hexdigest()
            upload_headers = {
                "X-Customer-Token": token,
                "Idempotency-Key": f"video-upload-{uuid4().hex[:32]}",
                "X-Content-SHA256": content_hash,
                "X-Operation-Fingerprint": fingerprint,
            }
            upload = client.post(
                "/api/v1/provider/video-editor/upload",
                headers=upload_headers,
                data={
                    "batch_id": batch_id,
                    "object_key": object_key,
                    "media_type": "video/mp4",
                },
                files={"file": ("source.mp4", media, "video/mp4")},
            )
            assert upload.status_code == 200
            assert upload.json()["object_key"] == object_key
            assert runtime.providers.object_store.upload_count == 1

            replay_upload = client.post(
                "/api/v1/provider/video-editor/upload",
                headers=upload_headers,
                data={
                    "batch_id": batch_id,
                    "object_key": object_key,
                    "media_type": "video/mp4",
                },
                files={"file": ("source.mp4", media, "video/mp4")},
            )
            assert replay_upload.status_code == 200
            assert replay_upload.headers["x-idempotent-replay"] == "true"
            assert runtime.providers.object_store.upload_count == 1

            actual_quote = create_cost_quote(
                input_duration_seconds=40,
                output_duration_seconds=41.4,
                output_profile="720p",
            )
            assert repository.get_credit_balance(customer_code) == (
                Decimal("10") - cny_to_credits(actual_quote.estimated_total)
            )

            output_key = f"video-editor-output/{batch_id}/output/720p.mp4"
            output_url = client.get(
                f"/api/v1/provider/video-editor/outputs/{batch_id}/url",
                headers={"X-Customer-Token": token},
                params={"object_key": output_key},
            )
            assert output_url.status_code == 200
            assert output_url.json()["url"].endswith(output_key)

            invalid_output = client.get(
                f"/api/v1/provider/video-editor/outputs/{batch_id}/url",
                headers={"X-Customer-Token": token},
                params={"object_key": "video-editor-output/another-batch/output/720p.mp4"},
            )
            assert invalid_output.status_code == 400
    finally:
        app.dependency_overrides.clear()
        _auth_tokens.clear()


def test_video_editor_invalid_upload_refunds_once_and_voids_charge(tmp_path):
    repository = SQLiteRepository(tmp_path / "video-invalid-upload.db")
    now = datetime.now().astimezone()
    customer_code = f"VIDEO-{uuid4().hex[:10].upper()}"
    repository.create_customer_codes(
        [
            CustomerCode(
                code=customer_code,
                name="剪辑退款客户",
                initial_credits="10",
                created_at=now,
                updated_at=now,
            )
        ]
    )
    runtime = _runtime()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_server_video_editor_runtime] = lambda: runtime
    app.dependency_overrides[get_media_duration_probe] = lambda: (lambda _path: 40.0)
    _auth_tokens.clear()
    batch_id = f"edit-batch-{uuid4().hex[:12]}"
    try:
        with TestClient(app) as client:
            token = client.post(
                "/api/v1/auth/customer-login", json={"code": customer_code}
            ).json()["token"]
            quote = create_cost_quote(
                input_duration_seconds=60,
                output_duration_seconds=61.4,
                output_profile="720p",
            )
            charge = client.post(
                "/api/v1/provider/video-editor/authorize",
                headers={
                    "X-Customer-Token": token,
                    "Idempotency-Key": f"video-charge-{batch_id}",
                },
                json={
                    "batch_id": batch_id,
                    "quote": quote.model_dump(mode="json"),
                    "max_cost_cny": str(quote.estimated_max),
                },
            )
            assert charge.status_code == 200
            assert repository.get_credit_balance(customer_code) < Decimal("10")

            media = b"invalid video upload"
            object_key = f"video-editor-input/{batch_id}/input/source.mp4"
            upload_data = {
                "batch_id": batch_id,
                "object_key": object_key,
                "media_type": "video/mp4",
            }
            invalid = client.post(
                "/api/v1/provider/video-editor/upload",
                headers={
                    "X-Customer-Token": token,
                    "Idempotency-Key": f"video-upload-invalid-{batch_id}",
                    "X-Content-SHA256": "0" * 64,
                    "X-Operation-Fingerprint": hashlib.sha256(
                        uuid4().bytes
                    ).hexdigest(),
                },
                data=upload_data,
                files={"file": ("source.mp4", media, "video/mp4")},
            )
            assert invalid.status_code == 400
            assert repository.get_credit_balance(customer_code) == Decimal("10")

            blocked = client.post(
                "/api/v1/provider/video-editor/upload",
                headers={
                    "X-Customer-Token": token,
                    "Idempotency-Key": f"video-upload-retry-{batch_id}",
                    "X-Content-SHA256": hashlib.sha256(media).hexdigest(),
                    "X-Operation-Fingerprint": hashlib.sha256(
                        uuid4().bytes
                    ).hexdigest(),
                },
                data=upload_data,
                files={"file": ("source.mp4", media, "video/mp4")},
            )
            assert blocked.status_code == 409, blocked.text
            assert "已退回" in blocked.json()["message"]
            assert repository.get_credit_balance(customer_code) == Decimal("10")
            assert runtime.providers.object_store.upload_count == 0
    finally:
        app.dependency_overrides.clear()
        _auth_tokens.clear()
