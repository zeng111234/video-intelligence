"""Desktop facade for company-hosted cloud ASR."""

from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from project.backend.app.services.control_plane_client import (
    active_upstream_admin_session,
    active_upstream_customer_session,
    control_plane_base_url,
)
from src.services.video_editor_cloud import (
    CloudAsset,
    CloudTranscript,
    ProviderJobSnapshot,
)


class RemoteASRError(RuntimeError):
    pass


class RemoteAliyunASRRuntime:
    """Keep media local until an explicitly charged server upload."""

    billing_centrally_managed = True

    @staticmethod
    def _timeout(*, upload: bool = False) -> float:
        default = 600.0 if upload else 60.0
        name = (
            "CONTROL_PLANE_UPLOAD_TIMEOUT_SECONDS"
            if upload
            else "CONTROL_PLANE_TIMEOUT_SECONDS"
        )
        try:
            configured = float(os.getenv(name, str(default)))
        except ValueError:
            configured = default
        return max(10.0, min(configured, 1800.0 if upload else 120.0))

    @staticmethod
    def _verify() -> bool | str:
        configured = os.getenv("CONTROL_PLANE_CA_BUNDLE", "").strip()
        if not configured:
            return True
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise RemoteASRError("公司服务证书配置无效，请联系管理员。")
        return str(path)

    @staticmethod
    def _headers(*, idempotency_key: str | None = None) -> dict[str, str]:
        token = active_upstream_customer_session()
        if not token:
            raise RemoteASRError("请先登录客户账号，再使用云端转写。")
        headers = {"X-Customer-Token": token, "Accept": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _message(response: httpx.Response, fallback: str) -> str:
        try:
            payload = response.json()
        except ValueError:
            return fallback
        return str(payload.get("message") or payload.get("detail") or fallback)

    def _get(self, path: str) -> httpx.Response:
        base_url = control_plane_base_url()
        if not base_url:
            raise RemoteASRError("公司服务地址未配置。")
        response: httpx.Response | None = None
        try:
            with httpx.Client(
                timeout=self._timeout(),
                verify=self._verify(),
                follow_redirects=False,
            ) as client:
                for attempt in range(2):
                    response = client.get(
                        f"{base_url}{path}", headers=self._headers()
                    )
                    if response.status_code not in {502, 503} or attempt == 1:
                        break
        except httpx.HTTPError as exc:
            raise RemoteASRError("暂时无法连接公司转写服务。") from exc
        assert response is not None
        if not response.is_success:
            raise RemoteASRError(self._message(response, "公司转写服务暂不可用。"))
        return response

    def capability(self) -> dict[str, Any]:
        try:
            return dict(self._get("/api/v1/provider/asr/capabilities").json())
        except (RemoteASRError, TypeError, ValueError):
            return {
                "provider_mode": "aliyun",
                "provider_name": "company_cloud_asr",
                "enabled": False,
                "live_ready": False,
                "missing_configuration": ["公司转写服务暂不可用"],
                "is_mock": False,
                "billing_authorized": False,
                "price_version": "",
                "supports_local_fallback": False,
            }

    def authorize_provider(
        self, *, confirmed: bool, per_task_cap_cny: Decimal
    ) -> dict[str, Any]:
        token = active_upstream_admin_session()
        if not token:
            raise RemoteASRError("请先登录管理员账号，再确认云端转写费用。")
        payload = json.dumps(
            {
                "confirmed": confirmed,
                "per_task_cap_cny": str(per_task_cap_cny),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        key = f"asr-admin-authorization-{hashlib.sha256(payload).hexdigest()[:24]}"
        try:
            with httpx.Client(
                timeout=self._timeout(),
                verify=self._verify(),
                follow_redirects=False,
            ) as client:
                response = client.post(
                    f"{control_plane_base_url()}/api/v1/provider/asr/admin/authorization",
                    headers={
                        "X-Admin-Token": token,
                        "Idempotency-Key": key,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    content=payload,
                )
        except httpx.HTTPError as exc:
            raise RemoteASRError("暂时无法连接公司转写授权服务。") from exc
        if not response.is_success:
            raise RemoteASRError(self._message(response, "云端转写费用授权失败。"))
        try:
            return dict(response.json())
        except (TypeError, ValueError) as exc:
            raise RemoteASRError("公司转写授权服务返回了无效结果。") from exc

    @property
    def missing_configuration(self) -> list[str]:
        return [
            str(item)
            for item in self.capability().get("missing_configuration", [])
        ]

    @property
    def live_ready(self) -> bool:
        return bool(self.capability().get("live_ready"))

    def estimate_cost(self, duration_seconds: float) -> Decimal:
        capability = self.capability()
        unit = Decimal(str(capability.get("unit_price_cny_per_second") or "0"))
        return Decimal(str(duration_seconds)) * unit

    def ensure_authorized(
        self, duration_seconds: float, *, operation_key: str
    ) -> Decimal:
        key = f"asr-charge-{operation_key}"
        payload = json.dumps(
            {"task_id": operation_key, "duration_seconds": duration_seconds},
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with httpx.Client(
                timeout=self._timeout(),
                verify=self._verify(),
                follow_redirects=False,
            ) as client:
                response = client.post(
                    f"{control_plane_base_url()}/api/v1/provider/asr/authorize",
                    headers={
                        **self._headers(idempotency_key=key),
                        "Content-Type": "application/json",
                    },
                    content=payload,
                )
        except httpx.HTTPError as exc:
            raise RemoteASRError(
                "公司转写服务连接失败，系统不会自动重复扣费。"
            ) from exc
        if not response.is_success:
            raise RemoteASRError(self._message(response, "本次转写费用确认失败。"))
        try:
            return Decimal(str(response.json()["estimated_cost_cny"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RemoteASRError("公司转写服务返回了无效费用结果。") from exc

    def upload(
        self,
        media_path: str | Path,
        *,
        object_key: str,
        media_type: str,
    ) -> CloudAsset:
        source = Path(media_path)
        parts = object_key.split("/")
        if len(parts) < 3:
            raise RemoteASRError("云端素材路径无效。")
        task_id = parts[1]
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        content_hash = digest.hexdigest()
        operation_fingerprint = hashlib.sha256(
            f"{task_id}\0{object_key}\0{media_type}\0{content_hash}".encode()
        ).hexdigest()
        headers = {
            **self._headers(idempotency_key=f"asr-upload-{task_id}"),
            "X-Content-SHA256": content_hash,
            "X-Operation-Fingerprint": operation_fingerprint,
        }
        try:
            with (
                source.open("rb") as stream,
                httpx.Client(
                    timeout=self._timeout(upload=True),
                    verify=self._verify(),
                    follow_redirects=False,
                ) as client,
            ):
                response = client.post(
                    f"{control_plane_base_url()}/api/v1/provider/asr/upload",
                    headers=headers,
                    data={
                        "task_id": task_id,
                        "object_key": object_key,
                        "media_type": media_type,
                    },
                    files={"file": (source.name, stream, media_type)},
                )
        except httpx.HTTPError as exc:
            error = RemoteASRError(
                "公司素材上传连接失败，结果暂时无法确认；系统不会自动重复提交。"
            )
            setattr(error, "outcome_unknown", True)
            raise error from exc
        if not response.is_success:
            error = RemoteASRError(self._message(response, "公司素材上传失败。"))
            if response.status_code in {409, 500, 502, 503, 504}:
                setattr(error, "outcome_unknown", True)
            raise error
        try:
            return CloudAsset.model_validate(response.json())
        except ValueError as exc:
            raise RemoteASRError("公司素材服务返回了无效结果。") from exc

    def submit(self, asset: CloudAsset, *, language: str) -> ProviderJobSnapshot:
        parts = asset.object_key.split("/")
        if len(parts) < 3:
            raise RemoteASRError("云端素材路径无效。")
        task_id = parts[1]
        payload = json.dumps(
            {
                "task_id": task_id,
                "asset": asset.model_dump(mode="json"),
                "language": language,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with httpx.Client(
                timeout=self._timeout(),
                verify=self._verify(),
                follow_redirects=False,
            ) as client:
                response = client.post(
                    f"{control_plane_base_url()}/api/v1/provider/asr/submit",
                    headers={
                        **self._headers(idempotency_key=f"asr-submit-{task_id}"),
                        "Content-Type": "application/json",
                    },
                    content=payload,
                )
        except httpx.HTTPError as exc:
            error = RemoteASRError(
                "公司云端识别提交结果暂时无法确认；系统不会自动重复提交。"
            )
            setattr(error, "outcome_unknown", True)
            raise error from exc
        if not response.is_success:
            error = RemoteASRError(self._message(response, "云端识别提交失败。"))
            if response.status_code in {409, 500, 502, 503, 504}:
                setattr(error, "outcome_unknown", True)
            raise error
        try:
            return ProviderJobSnapshot.model_validate(response.json())
        except ValueError as exc:
            raise RemoteASRError("公司转写服务返回了无效任务结果。") from exc

    def query(self, provider_job_id: str) -> ProviderJobSnapshot:
        encoded_job_id = quote(provider_job_id, safe="")
        response = self._get(f"/api/v1/provider/asr/jobs/{encoded_job_id}")
        try:
            return ProviderJobSnapshot.model_validate(response.json())
        except ValueError as exc:
            raise RemoteASRError("公司转写服务返回了无效状态。") from exc

    def fetch_result(self, snapshot: ProviderJobSnapshot) -> CloudTranscript:
        encoded_job_id = quote(snapshot.provider_job_id, safe="")
        response = self._get(
            f"/api/v1/provider/asr/jobs/{encoded_job_id}/result"
        )
        try:
            return CloudTranscript.model_validate(response.json())
        except ValueError as exc:
            raise RemoteASRError("公司转写服务返回了无效转写结果。") from exc
