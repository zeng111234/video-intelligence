"""Desktop digital-avatar provider backed by the company control plane."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from project.backend.app.services.control_plane_client import (
    active_upstream_admin_session,
    active_upstream_customer_session,
    control_plane_base_url,
)
from src.adapters.avatar import AvatarProviderError
from src.models import (
    AvatarAsset,
    AvatarBillingQuote,
    AvatarCapability,
    AvatarJobSnapshot,
    AvatarSubmitRequest,
    ProviderErrorKind,
    ProviderMode,
)

_HIDDEN_LEGACY_BUILT_IN_AVATAR_IDS = {"10078"}


class RemoteAvatarProvider:
    billing_centrally_managed = True

    def __init__(self) -> None:
        self._resume_lock = threading.Lock()
        self._resume_attempts: dict[str, int] = {}
        self._session_nonce = uuid4().hex[:8]

    @staticmethod
    def _timeout(*, upload: bool = False) -> float:
        default = 600.0 if upload else 60.0
        variable = (
            "CONTROL_PLANE_UPLOAD_TIMEOUT_SECONDS"
            if upload
            else "CONTROL_PLANE_TIMEOUT_SECONDS"
        )
        try:
            value = float(os.getenv(variable, str(default)))
        except ValueError:
            value = default
        return max(10.0, min(value, 1800.0 if upload else 120.0))

    @staticmethod
    def _verify() -> bool | str:
        configured = os.getenv("CONTROL_PLANE_CA_BUNDLE", "").strip()
        if not configured:
            return True
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise AvatarProviderError(
                "公司服务证书配置无效，请联系管理员。",
                kind=ProviderErrorKind.AUTHORIZATION,
            )
        return str(path)

    @staticmethod
    def _headers(idempotency_key: str | None = None) -> dict[str, str]:
        customer_token = active_upstream_customer_session()
        admin_token = active_upstream_admin_session()
        token = customer_token or admin_token
        if not token:
            raise AvatarProviderError(
                "请先登录客户账号或管理员账号，再使用数字人。",
                kind=ProviderErrorKind.AUTHORIZATION,
            )
        token_header = "X-Customer-Token" if customer_token else "X-Admin-Token"
        headers = {token_header: token, "Accept": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _message(response: httpx.Response, fallback: str) -> str:
        try:
            body = response.json()
        except ValueError:
            return fallback
        return str(body.get("message") or body.get("detail") or fallback)

    @staticmethod
    def _kind(status: int) -> ProviderErrorKind:
        if status in {401, 403}:
            return ProviderErrorKind.AUTHORIZATION
        if status == 429:
            return ProviderErrorKind.RATE_LIMIT
        if 400 <= status < 500 and status != 409:
            return ProviderErrorKind.VALIDATION
        return ProviderErrorKind.SERVICE

    def _get(
        self, path: str, *, allow_not_found: bool = False
    ) -> httpx.Response | None:
        base_url = control_plane_base_url()
        if not base_url:
            raise AvatarProviderError(
                "公司服务地址未配置。", kind=ProviderErrorKind.AUTHORIZATION
            )
        response: httpx.Response | None = None
        try:
            with httpx.Client(
                timeout=self._timeout(),
                verify=self._verify(),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                for attempt in range(2):
                    response = client.get(f"{base_url}{path}", headers=self._headers())
                    if response.status_code not in {502, 503} or attempt == 1:
                        break
        except httpx.HTTPError as exc:
            raise AvatarProviderError(
                "暂时无法连接公司数字人服务。",
                kind=ProviderErrorKind.CONNECTION,
            ) from exc
        assert response is not None
        if allow_not_found and response.status_code == 404:
            return None
        if not response.is_success:
            raise AvatarProviderError(
                self._message(response, "公司数字人服务暂不可用。"),
                kind=self._kind(response.status_code),
            )
        return response

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> httpx.Response:
        body = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        response: httpx.Response | None = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with httpx.Client(
                    timeout=self._timeout(),
                    verify=self._verify(),
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    response = client.post(
                        f"{control_plane_base_url()}{path}",
                        headers={
                            **self._headers(idempotency_key),
                            "Content-Type": "application/json",
                        },
                        content=body,
                    )
                if response.status_code not in {502, 503} or attempt == 1:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == 1:
                    break
        if response is None:
            raise AvatarProviderError(
                "数字人操作结果暂时无法确认，系统不会自动重复提交。",
                kind=ProviderErrorKind.OUTCOME_UNKNOWN,
                outcome_unknown=True,
            ) from last_error
        if not response.is_success:
            unknown = response.status_code in {409, 500, 502, 503, 504}
            raise AvatarProviderError(
                self._message(response, "公司数字人操作失败。"),
                kind=(
                    ProviderErrorKind.OUTCOME_UNKNOWN
                    if unknown
                    else self._kind(response.status_code)
                ),
                outcome_unknown=unknown,
            )
        return response

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise AvatarProviderError("公司数字人服务返回了无效结果。") from exc

    def capabilities(self) -> AvatarCapability:
        try:
            response = self._get("/api/v1/provider/avatar/capabilities")
            assert response is not None
            return AvatarCapability.model_validate(self._json(response))
        except AvatarProviderError as exc:
            return AvatarCapability(
                provider_name="company_cloud_avatar",
                display_name="公司云数字人",
                mode=ProviderMode.PRODUCTION,
                enabled=False,
                permission_status=f"unavailable_{exc.kind.value}",
                missing_configuration=[str(exc) or "公司数字人服务暂不可用"],
            )
        except ValueError:
            return AvatarCapability(
                provider_name="company_cloud_avatar",
                display_name="公司云数字人",
                mode=ProviderMode.PRODUCTION,
                enabled=False,
                permission_status="invalid_capability_response",
                missing_configuration=["公司数字人服务返回的能力信息无效。"],
            )

    def list_assets(self) -> list[AvatarAsset]:
        response = self._get("/api/v1/provider/avatar/assets")
        assert response is not None
        try:
            assets = [AvatarAsset.model_validate(item) for item in self._json(response)]
            return [
                asset
                for asset in assets
                if not (
                    asset.kind.value == "avatar"
                    and asset.source_type == "built_in"
                    and asset.asset_id in _HIDDEN_LEGACY_BUILT_IN_AVATAR_IDS
                )
            ]
        except (TypeError, ValueError) as exc:
            raise AvatarProviderError("公司数字人素材列表无效。") from exc

    def quote(self, *, script_text: str, speech_rate: float) -> AvatarBillingQuote:
        characters = sum(1 for character in script_text if not character.isspace())
        response = self._get(
            f"/api/v1/provider/avatar/quote?characters={max(1, characters)}&speech_rate={speech_rate}"
        )
        assert response is not None
        try:
            return AvatarBillingQuote.model_validate(self._json(response))
        except ValueError as exc:
            raise AvatarProviderError("公司数字人费用预估无效。") from exc

    def submit(self, request: AvatarSubmitRequest) -> AvatarJobSnapshot:
        response = self._post(
            "/api/v1/provider/avatar/submit",
            {"request": request.model_dump(mode="json")},
            idempotency_key=f"avatar-submit-{request.idempotency_key}",
        )
        return AvatarJobSnapshot.model_validate(self._json(response))

    def get_job(self, job_id: str) -> AvatarJobSnapshot:
        response = self._get(f"/api/v1/provider/avatar/jobs/{quote(job_id, safe='')}")
        assert response is not None
        return AvatarJobSnapshot.model_validate(self._json(response))

    def find_job(self, idempotency_key: str) -> AvatarJobSnapshot | None:
        response = self._get(
            f"/api/v1/provider/avatar/jobs/find/{quote(idempotency_key, safe='')}",
            allow_not_found=True,
        )
        if response is None:
            return None
        payload = self._json(response)
        return AvatarJobSnapshot.model_validate(payload) if payload else None

    def resume_submit(
        self, request: AvatarSubmitRequest, pending_job_id: str
    ) -> AvatarJobSnapshot:
        with self._resume_lock:
            sequence = self._resume_attempts.get(pending_job_id, 0)
        attempt_id = f"{self._session_nonce}-{sequence}"
        try:
            response = self._post(
                "/api/v1/provider/avatar/resume",
                {
                    "request": request.model_dump(mode="json"),
                    "pending_job_id": pending_job_id,
                    "attempt_id": attempt_id,
                },
                idempotency_key=(
                    f"avatar-resume-{request.idempotency_key}-{attempt_id}"
                ),
            )
            snapshot = AvatarJobSnapshot.model_validate(self._json(response))
        except AvatarProviderError as exc:
            if not exc.outcome_unknown:
                raise
            return AvatarJobSnapshot(
                job_id=pending_job_id,
                idempotency_key=request.idempotency_key,
                status="outcome_unknown",
                progress=0,
                stage="视频提交结果待核对",
                error_kind="outcome_unknown",
                error_message=str(exc),
            )
        if snapshot.job_id == pending_job_id and snapshot.status.value == "running":
            with self._resume_lock:
                self._resume_attempts[pending_job_id] = sequence + 1
        return snapshot

    def download_result(self, job_id: str) -> tuple[bytes, str]:
        response = self._get(
            f"/api/v1/provider/avatar/jobs/{quote(job_id, safe='')}/result"
        )
        assert response is not None
        return response.content, response.headers.get(
            "content-type", "video/mp4"
        ).split(";", 1)[0]

    def _upload_training(
        self,
        *,
        kind: str,
        name: str,
        path: Path,
        filename: str,
        mime_type: str,
    ) -> AvatarAsset:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        content_hash = digest.hexdigest()
        operation_key = f"avatar-train-{kind}-{content_hash[:32]}"
        fingerprint = hashlib.sha256(
            f"{kind}\0{name}\0{content_hash}".encode("utf-8")
        ).hexdigest()
        route = "train-avatar" if kind == "face" else "train-voice"
        response: httpx.Response | None = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with (
                    path.open("rb") as stream,
                    httpx.Client(
                        timeout=self._timeout(upload=True),
                        verify=self._verify(),
                        follow_redirects=False,
                    ) as client,
                ):
                    response = client.post(
                        f"{control_plane_base_url()}/api/v1/provider/avatar/assets/{route}",
                        headers={
                            **self._headers(operation_key),
                            "X-Content-SHA256": content_hash,
                            "X-Operation-Fingerprint": fingerprint,
                        },
                        data={"name": name, "rights_confirmed": "true"},
                        files={"file": (filename, stream, mime_type)},
                    )
                if response.status_code not in {502, 503} or attempt == 1:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == 1:
                    break
        if response is None:
            raise AvatarProviderError(
                "训练提交结果暂时无法确认，系统不会自动重复提交。",
                kind=ProviderErrorKind.OUTCOME_UNKNOWN,
                outcome_unknown=True,
            ) from last_error
        if not response.is_success:
            unknown = response.status_code in {409, 500, 502, 503, 504}
            raise AvatarProviderError(
                self._message(response, "训练提交失败。"),
                kind=(
                    ProviderErrorKind.OUTCOME_UNKNOWN
                    if unknown
                    else self._kind(response.status_code)
                ),
                outcome_unknown=unknown,
            )
        return AvatarAsset.model_validate(self._json(response))

    def create_cloud_avatar(
        self, *, name: str, training_video_path: Path, filename: str
    ) -> AvatarAsset:
        return self._upload_training(
            kind="face",
            name=name,
            path=training_video_path,
            filename=filename,
            mime_type="video/mp4",
        )

    def create_voice_clone(
        self, *, name: str, sample_path: Path, filename: str, mime_type: str
    ) -> AvatarAsset:
        return self._upload_training(
            kind="voice",
            name=name,
            path=sample_path,
            filename=filename,
            mime_type=mime_type,
        )

    def store_pending_voice_sample(
        self, *, name: str, sample_path: Path, filename: str
    ) -> AvatarAsset:
        return self.create_voice_clone(
            name=name,
            sample_path=sample_path,
            filename=filename,
            mime_type=mimetypes.guess_type(filename)[0] or "audio/mpeg",
        )

    def resume_pending_voice_clone(self, asset_id: str) -> AvatarAsset:
        response = self._post(
            f"/api/v1/provider/avatar/assets/{quote(asset_id, safe='')}/resume-voice-clone",
            {},
            idempotency_key=f"avatar-resume-asset-{hashlib.sha256(asset_id.encode()).hexdigest()[:32]}",
        )
        return AvatarAsset.model_validate(self._json(response))

    def render_voice_preview(self, asset_id: str) -> bytes:
        response = self._get(
            f"/api/v1/provider/avatar/assets/{quote(asset_id, safe='')}/voice-preview"
        )
        assert response is not None
        return response.content

    def download_asset_media(self, asset_id: str) -> tuple[bytes, str]:
        response = self._get(
            f"/api/v1/provider/avatar/assets/{quote(asset_id, safe='')}/media"
        )
        assert response is not None
        return response.content, response.headers.get(
            "content-type", "application/octet-stream"
        ).split(";", 1)[0]
