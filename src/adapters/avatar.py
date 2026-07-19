from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from src.models import (
    AvatarAsset,
    AvatarCapability,
    AvatarJobSnapshot,
    AvatarSubmitRequest,
    ProviderErrorKind,
    ProviderMode,
)
from src.retry import ExternalServiceError, RetryPolicy, retry_with_policy

JsonTransport = Callable[
    [str, str, dict[str, str], bytes | None, float], tuple[bytes, str]
]


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


class AvatarProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: ProviderErrorKind = ProviderErrorKind.SERVICE,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.outcome_unknown = outcome_unknown


def _default_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[bytes, str]:
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.read(), response.headers.get_content_type()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        kind = (
            ProviderErrorKind.AUTHORIZATION
            if exc.code in {401, 403}
            else ProviderErrorKind.RATE_LIMIT
            if exc.code == 429
            else ProviderErrorKind.VALIDATION
            if 400 <= exc.code < 500
            else ProviderErrorKind.SERVICE
        )
        raise AvatarProviderError(
            f"数字人服务 HTTP {exc.code}：{detail or '请求失败'}", kind=kind
        ) from exc
    except (TimeoutError, URLError) as exc:
        raise AvatarProviderError(
            "无法连接数字人服务，请检查服务地址和运行状态。",
            kind=ProviderErrorKind.CONNECTION,
            outcome_unknown=method == "POST",
        ) from exc


class InternalAvatarProvider:
    def __init__(
        self,
        base_url: str = "",
        token: str = "",
        *,
        enabled: bool = False,
        timeout_seconds: float = 20,
        result_timeout_seconds: float = 120,
        transport: JsonTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.enabled = enabled
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.result_timeout_seconds = max(1.0, result_timeout_seconds)
        self.transport = transport or _default_transport

    @classmethod
    def from_env(cls) -> InternalAvatarProvider:
        return cls(
            os.getenv("AVATAR_SERVICE_BASE_URL", ""),
            os.getenv("AVATAR_SERVICE_TOKEN", ""),
            enabled=os.getenv("AVATAR_SERVICE_ENABLED", "false").casefold()
            in {"1", "true", "yes", "on"},
            timeout_seconds=_float_env("AVATAR_SERVICE_TIMEOUT_SECONDS", 20),
            result_timeout_seconds=_float_env("AVATAR_RESULT_TIMEOUT_SECONDS", 120),
        )

    def _missing_configuration(self) -> list[str]:
        missing = []
        if not self.enabled:
            missing.append("AVATAR_SERVICE_ENABLED")
        if not self.base_url:
            missing.append("AVATAR_SERVICE_BASE_URL")
        elif not self.base_url.startswith(("http://", "https://")):
            missing.append("AVATAR_SERVICE_BASE_URL(http/https)")
        if not self.token:
            missing.append("AVATAR_SERVICE_TOKEN")
        return missing

    def capabilities(self) -> AvatarCapability:
        missing = self._missing_configuration()
        if missing:
            return AvatarCapability(
                provider_name="company_internal_avatar",
                display_name="公司数字人服务",
                mode=ProviderMode.SANDBOX,
                enabled=False,
                permission_status="configuration_missing",
                missing_configuration=missing,
            )
        payload = self._json_request("GET", "/internal/v1/avatar/capabilities")
        return AvatarCapability.model_validate(payload)

    def list_assets(self) -> list[AvatarAsset]:
        self._ensure_configured()
        payload = self._json_request("GET", "/internal/v1/avatar/assets")
        return [AvatarAsset.model_validate(item) for item in payload]

    def submit(self, request: AvatarSubmitRequest) -> AvatarJobSnapshot:
        self._ensure_configured()
        payload = self._json_request(
            "POST",
            "/internal/v1/avatar/jobs",
            request.model_dump(mode="json"),
            retry_safe=False,
        )
        return AvatarJobSnapshot.model_validate(payload)

    def get_job(self, job_id: str) -> AvatarJobSnapshot:
        self._ensure_configured()
        payload = self._json_request(
            "GET", f"/internal/v1/avatar/jobs/{quote(job_id, safe='')}"
        )
        return AvatarJobSnapshot.model_validate(payload)

    def find_job(self, idempotency_key: str) -> AvatarJobSnapshot | None:
        self._ensure_configured()
        try:
            payload = self._json_request(
                "GET",
                "/internal/v1/avatar/jobs/by-idempotency/"
                f"{quote(idempotency_key, safe='')}",
            )
        except AvatarProviderError as exc:
            if exc.kind == ProviderErrorKind.VALIDATION:
                return None
            raise
        return AvatarJobSnapshot.model_validate(payload)

    def download_result(self, job_id: str) -> tuple[bytes, str]:
        self._ensure_configured()
        path = f"/internal/v1/avatar/jobs/{quote(job_id, safe='')}/result"
        return self._request(
            "GET", path, None, retry_safe=True, timeout=self.result_timeout_seconds
        )

    def _ensure_configured(self) -> None:
        missing = self._missing_configuration()
        if missing:
            raise AvatarProviderError(
                "数字人服务尚未配置：" + "、".join(missing),
                kind=ProviderErrorKind.AUTHORIZATION,
            )

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        retry_safe: bool = True,
    ) -> Any:
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        raw, _ = self._request(method, path, body, retry_safe=retry_safe)
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AvatarProviderError("数字人服务返回了无效 JSON。") from exc
        if not isinstance(envelope, dict) or "code" not in envelope:
            raise AvatarProviderError("数字人服务响应格式不正确。")
        if int(envelope.get("code", 0)) != 1:
            message = str(envelope.get("msg") or "数字人服务请求失败")
            error_kind = envelope.get("error_kind", ProviderErrorKind.SERVICE)
            try:
                kind = ProviderErrorKind(error_kind)
            except ValueError:
                kind = ProviderErrorKind.SERVICE
            raise AvatarProviderError(message, kind=kind)
        return envelope.get("data")

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        *,
        retry_safe: bool,
        timeout: float | None = None,
    ) -> tuple[bytes, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"

        def _is_retryable(exc: BaseException) -> bool:
            if isinstance(exc, AvatarProviderError):
                return exc.kind in {
                    ProviderErrorKind.CONNECTION,
                    ProviderErrorKind.RATE_LIMIT,
                    ProviderErrorKind.SERVICE,
                }
            return False

        try:
            return retry_with_policy(
                lambda: self.transport(
                    method,
                    f"{self.base_url}{path}",
                    headers,
                    body,
                    timeout or self.timeout_seconds,
                ),
                policy=RetryPolicy(max_attempts=2 if retry_safe else 1, base_delay=0),
                retry_for=(AvatarProviderError,),
                retryable=_is_retryable,
            )
        except ExternalServiceError as exc:
            cause = exc.__cause__
            if isinstance(cause, AvatarProviderError):
                raise cause
            raise AvatarProviderError(
                str(exc),
                kind=ProviderErrorKind.SERVICE,
            ) from exc
