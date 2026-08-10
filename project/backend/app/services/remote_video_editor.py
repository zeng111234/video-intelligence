"""Desktop-side cloud-video providers backed by the company control plane."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from project.backend.app.services.control_plane_client import (
    active_upstream_customer_session,
    control_plane_base_url,
)
from src.services.video_editor_cloud import (
    CloudAsset,
    CloudEditorConfiguration,
    CloudProviderMode,
    CloudTranscript,
    EditPlan,
    ProviderJobSnapshot,
    RenderRequest,
)


class RemoteVideoEditorError(RuntimeError):
    def __init__(self, message: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


class _RemoteClient:
    @staticmethod
    def timeout(*, upload: bool = False) -> float:
        default = 600.0 if upload else 60.0
        name = (
            "CONTROL_PLANE_UPLOAD_TIMEOUT_SECONDS"
            if upload
            else "CONTROL_PLANE_TIMEOUT_SECONDS"
        )
        try:
            value = float(os.getenv(name, str(default)))
        except ValueError:
            value = default
        return max(10.0, min(value, 1800.0 if upload else 120.0))

    @staticmethod
    def verify() -> bool | str:
        configured = os.getenv("CONTROL_PLANE_CA_BUNDLE", "").strip()
        if not configured:
            return True
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise RemoteVideoEditorError("公司服务证书配置无效，请联系管理员。")
        return str(path)

    @staticmethod
    def headers(idempotency_key: str | None = None) -> dict[str, str]:
        token = active_upstream_customer_session()
        if not token:
            raise RemoteVideoEditorError("请先登录客户账号，再使用云端剪辑。")
        headers = {"X-Customer-Token": token, "Accept": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def message(response: httpx.Response, fallback: str) -> str:
        try:
            payload = response.json()
        except ValueError:
            return fallback
        return str(payload.get("message") or payload.get("detail") or fallback)

    def get(self, path: str) -> dict[str, Any]:
        base_url = control_plane_base_url()
        if not base_url:
            raise RemoteVideoEditorError("公司服务地址未配置。")
        response: httpx.Response | None = None
        try:
            with httpx.Client(
                timeout=self.timeout(), verify=self.verify(), follow_redirects=False
            ) as client:
                for attempt in range(2):
                    response = client.get(
                        f"{base_url}{path}", headers=self.headers()
                    )
                    if response.status_code not in {502, 503} or attempt == 1:
                        break
        except httpx.HTTPError as exc:
            raise RemoteVideoEditorError("暂时无法连接公司云端剪辑服务。") from exc
        assert response is not None
        if not response.is_success:
            raise RemoteVideoEditorError(self.message(response, "公司云端剪辑服务暂不可用。"))
        try:
            payload = response.json()
        except ValueError as exc:
            raise RemoteVideoEditorError("公司云端剪辑服务返回了无效结果。") from exc
        if not isinstance(payload, dict):
            raise RemoteVideoEditorError("公司云端剪辑服务返回了无效结果。")
        return payload

    def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        response: httpx.Response | None = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with httpx.Client(
                    timeout=self.timeout(),
                    verify=self.verify(),
                    follow_redirects=False,
                ) as client:
                    response = client.post(
                        f"{control_plane_base_url()}{path}",
                        headers={
                            **self.headers(idempotency_key),
                            "Content-Type": "application/json",
                        },
                        content=encoded,
                    )
                if response.status_code not in {502, 503} or attempt == 1:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == 1:
                    break
        if response is None:
            raise RemoteVideoEditorError(
                "公司云端剪辑连接失败，结果暂时无法确认；系统不会自动重复提交。",
                outcome_unknown=True,
            ) from last_error
        if not response.is_success:
            raise RemoteVideoEditorError(
                self.message(response, "公司云端剪辑操作失败。"),
                outcome_unknown=response.status_code in {409, 500, 502, 503, 504},
            )
        try:
            result = response.json()
        except ValueError as exc:
            raise RemoteVideoEditorError("公司云端剪辑服务返回了无效结果。") from exc
        if not isinstance(result, dict):
            raise RemoteVideoEditorError("公司云端剪辑服务返回了无效结果。")
        return result


class RemoteCloudObjectStore:
    # The control plane validates the logged-in customer, charged batch, and
    # exact output key before it signs a URL.  Desktop builds intentionally do
    # not know the private OSS bucket name or its credentials.
    validates_output_ownership_remotely = True

    def __init__(self, client: _RemoteClient) -> None:
        self.client = client

    def upload(
        self,
        path: str | Path,
        object_key: str,
        *,
        media_type: str,
    ) -> CloudAsset:
        source = Path(path)
        parts = object_key.split("/")
        if len(parts) < 3 or parts[0] != "video-editor-input":
            raise RemoteVideoEditorError("云端素材路径无效。")
        batch_id = parts[1]
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        content_hash = digest.hexdigest()
        operation_key = f"video-upload-{hashlib.sha256(object_key.encode()).hexdigest()[:32]}"
        fingerprint = hashlib.sha256(
            f"{batch_id}\0{object_key}\0{media_type}\0{content_hash}".encode()
        ).hexdigest()
        response: httpx.Response | None = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with (
                    source.open("rb") as stream,
                    httpx.Client(
                        timeout=self.client.timeout(upload=True),
                        verify=self.client.verify(),
                        follow_redirects=False,
                    ) as client,
                ):
                    response = client.post(
                        f"{control_plane_base_url()}/api/v1/provider/video-editor/upload",
                        headers={
                            **self.client.headers(operation_key),
                            "X-Content-SHA256": content_hash,
                            "X-Operation-Fingerprint": fingerprint,
                        },
                        data={
                            "batch_id": batch_id,
                            "object_key": object_key,
                            "media_type": media_type,
                        },
                        files={"file": (source.name, stream, media_type)},
                    )
                if response.status_code not in {502, 503} or attempt == 1:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == 1:
                    break
        if response is None:
            raise RemoteVideoEditorError(
                "公司素材上传结果暂时无法确认；系统不会自动重复提交。",
                outcome_unknown=True,
            ) from last_error
        if not response.is_success:
            raise RemoteVideoEditorError(
                self.client.message(response, "公司素材上传失败。"),
                outcome_unknown=response.status_code in {409, 500, 502, 503, 504},
            )
        try:
            return CloudAsset.model_validate(response.json())
        except ValueError as exc:
            raise RemoteVideoEditorError("公司素材服务返回了无效结果。") from exc

    def presign_get_url(
        self,
        object_key: str,
        *,
        expires_seconds: int = 3600,
    ) -> str:
        parts = object_key.split("/")
        if (
            len(parts) != 4
            or parts[0] != "video-editor-output"
            or parts[2] != "output"
        ):
            raise RemoteVideoEditorError("云成片路径无效。")
        batch_id = parts[1]
        query = urlencode({"object_key": object_key})
        result = self.client.get(
            "/api/v1/provider/video-editor/outputs/"
            f"{quote(batch_id, safe='')}/url?{query}"
        )
        url = str(result.get("url") or "").strip()
        if not url.startswith("https://"):
            raise RemoteVideoEditorError("公司云成片服务返回了无效下载地址。")
        del expires_seconds  # The server owns and caps the signed URL lifetime.
        return url


class RemoteCloudASRProvider:
    def __init__(self, client: _RemoteClient, state) -> None:
        self.client = client
        self.state = state
        self.job_batches: dict[str, str] = {}
        self.lock = threading.Lock()

    def submit(self, asset: CloudAsset, *, language_hints=("zh",)) -> ProviderJobSnapshot:
        batch_id = asset.object_key.split("/")[1]
        result = self.client.post(
            "/api/v1/provider/video-editor/asr/submit",
            {
                "batch_id": batch_id,
                "asset": asset.model_dump(mode="json"),
                "language_hints": list(language_hints),
            },
            idempotency_key=f"video-asr-{batch_id}",
        )
        snapshot = ProviderJobSnapshot.model_validate(result)
        with self.lock:
            self.job_batches[snapshot.provider_job_id] = batch_id
        self.state.batch_id = batch_id
        return snapshot

    def _batch(self, job_id: str) -> str:
        with self.lock:
            batch_id = self.job_batches.get(job_id, "")
        if batch_id:
            self.state.batch_id = batch_id
        return batch_id

    def query(self, provider_job_id: str) -> ProviderJobSnapshot:
        self._batch(provider_job_id)
        result = self.client.get(
            f"/api/v1/provider/video-editor/asr/jobs/{quote(provider_job_id, safe='')}"
        )
        return ProviderJobSnapshot.model_validate(result)

    def fetch_result(self, snapshot: ProviderJobSnapshot) -> CloudTranscript:
        self._batch(snapshot.provider_job_id)
        result = self.client.get(
            "/api/v1/provider/video-editor/asr/jobs/"
            f"{quote(snapshot.provider_job_id, safe='')}/result"
        )
        return CloudTranscript.model_validate(result)


class RemoteEditPlanProvider:
    def __init__(self, client: _RemoteClient, state) -> None:
        self.client = client
        self.state = state

    def create_plan(
        self,
        transcript,
        spoken_ranges,
        duration_seconds,
        segments=None,
    ) -> EditPlan:
        batch_id = str(getattr(self.state, "batch_id", ""))
        if not batch_id:
            raise RemoteVideoEditorError("无法确认当前剪辑批次，请刷新后重试。")
        result = self.client.post(
            "/api/v1/provider/video-editor/plan",
            {
                "batch_id": batch_id,
                "transcript": transcript,
                "spoken_ranges": [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
                    for item in spoken_ranges
                ],
                "duration_seconds": duration_seconds,
                "segments": [dict(item) for item in (segments or [])],
            },
            idempotency_key=f"video-plan-{batch_id}",
        )
        return EditPlan.model_validate(result)


class RemoteCloudRenderProvider:
    def __init__(self, client: _RemoteClient) -> None:
        self.client = client

    def submit(self, request: RenderRequest) -> ProviderJobSnapshot:
        parts = request.output_object_key.split("/")
        if len(parts) < 3 or parts[0] != "video-editor-output":
            raise RemoteVideoEditorError("云端成片路径无效。")
        batch_id = parts[1]
        result = self.client.post(
            "/api/v1/provider/video-editor/render/submit",
            {
                "batch_id": batch_id,
                "render_request": request.model_dump(mode="json"),
            },
            idempotency_key=f"video-render-{batch_id}",
        )
        return ProviderJobSnapshot.model_validate(result)

    def query(self, provider_job_id: str) -> ProviderJobSnapshot:
        result = self.client.get(
            f"/api/v1/provider/video-editor/render/jobs/{quote(provider_job_id, safe='')}"
        )
        return ProviderJobSnapshot.model_validate(result)


class RemoteVideoEditorBundle:
    billing_centrally_managed = True

    def __init__(self) -> None:
        self.client = _RemoteClient()
        self.state = threading.local()
        self.object_store = RemoteCloudObjectStore(self.client)
        self.asr = RemoteCloudASRProvider(self.client, self.state)
        self.edit_plan = RemoteEditPlanProvider(self.client, self.state)
        self.render = RemoteCloudRenderProvider(self.client)

    def capability(self) -> dict[str, Any]:
        try:
            return self.client.get("/api/v1/provider/video-editor/capabilities")
        except RemoteVideoEditorError:
            return {
                "provider_mode": "aliyun",
                "provider_name": "company_cloud_video_editor",
                "enabled": False,
                "live_ready": False,
                "missing_configuration": ["公司云端剪辑服务暂不可用"],
                "is_mock": False,
            }

    def authorize_cost(
        self,
        *,
        batch_id: str,
        quote_payload: dict[str, Any],
        max_cost_cny: str,
    ) -> dict[str, Any]:
        self.state.batch_id = batch_id
        return self.client.post(
            "/api/v1/provider/video-editor/authorize",
            {
                "batch_id": batch_id,
                "quote": quote_payload,
                "max_cost_cny": max_cost_cny,
            },
            idempotency_key=f"video-charge-{batch_id}",
        )


@lru_cache
def get_remote_video_editor_runtime():
    configuration = CloudEditorConfiguration(
        provider_mode=CloudProviderMode.ALIYUN,
        workspace_id="company-control-plane",
        dashscope_api_key="remote",
        oss_bucket="company-control-plane",
        access_key_id="remote",
        access_key_secret="remote",
        mps_pipeline_id="remote",
        mps_template_id_720p="remote",
        mps_template_id_1080p="remote",
    )
    return configuration, RemoteVideoEditorBundle()
