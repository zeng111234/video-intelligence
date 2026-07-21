from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from src.models import (
    AvatarAsset,
    AvatarAssetKind,
    AvatarCapability,
    AvatarJobSnapshot,
    AvatarProviderStatus,
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


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


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


class SandboxAvatarProvider:
    """开发演示供应商：只证明任务闭环，不伪造成片或下载地址。"""

    def __init__(self) -> None:
        self.jobs: dict[str, AvatarJobSnapshot] = {}
        self.idempotency_index: dict[str, str] = {}

    def capabilities(self) -> AvatarCapability:
        return AvatarCapability(
            provider_name="sandbox_avatar",
            display_name="数字人演示供应商",
            mode=ProviderMode.SANDBOX,
            enabled=True,
            permission_status="sandbox_only",
            max_script_chars=240,
            supported_aspect_ratios=["9:16"],
            estimated_cost_cny=0,
            estimated_seconds=45,
            missing_configuration=[],
        )

    def list_assets(self) -> list[AvatarAsset]:
        return [
            AvatarAsset(
                asset_id="sandbox-avatar-public-01",
                kind=AvatarAssetKind.AVATAR,
                name="演示公共数字人",
                authorized=True,
            ),
            AvatarAsset(
                asset_id="sandbox-voice-cn-female-01",
                kind=AvatarAssetKind.VOICE,
                name="演示中文女声",
                authorized=True,
            ),
            AvatarAsset(
                asset_id="sandbox-voice-cn-male-01",
                kind=AvatarAssetKind.VOICE,
                name="演示中文男声",
                authorized=True,
            ),
        ]

    def submit(self, request: AvatarSubmitRequest) -> AvatarJobSnapshot:
        previous = self.find_job(request.idempotency_key)
        if previous is not None:
            return previous
        job_id = f"sandbox-avatar-{uuid.uuid4().hex[:10]}"
        snapshot = AvatarJobSnapshot(
            job_id=job_id,
            idempotency_key=request.idempotency_key,
            status=AvatarProviderStatus.RUNNING,
            progress=35,
            stage="演示任务已创建，未调用真实数字人服务",
            provider_job_id=job_id,
            estimated_cost_cny=0,
            estimated_seconds=45,
        )
        self.jobs[job_id] = snapshot
        self.idempotency_index[request.idempotency_key] = job_id
        return snapshot

    def get_job(self, job_id: str) -> AvatarJobSnapshot:
        snapshot = self.jobs.get(job_id)
        if snapshot is None:
            raise AvatarProviderError("数字人任务不存在。", kind=ProviderErrorKind.VALIDATION)
        return snapshot

    def find_job(self, idempotency_key: str) -> AvatarJobSnapshot | None:
        job_id = self.idempotency_index.get(idempotency_key)
        return self.jobs.get(job_id) if job_id else None

    def download_result(self, job_id: str) -> tuple[bytes, str]:
        raise AvatarProviderError(
            "演示模式不会生成真实视频文件。",
            kind=ProviderErrorKind.VALIDATION,
        )


class BaiduXilingAvatarProvider:
    """百度曦灵基础视频合成适配器。"""

    def __init__(
        self,
        *,
        app_id: str,
        app_key: str,
        figure_id: str,
        voice_id: str,
        base_url: str = "https://open.xiling.baidu.com",
        figure_name: str = "百度曦灵公共数字人",
        voice_name: str = "百度曦灵公共音色",
        callback_url: str = "",
        transparent: bool = False,
        timeout_seconds: float = 20,
        transport: JsonTransport | None = None,
    ) -> None:
        self.app_id = app_id.strip()
        self.app_key = app_key.strip()
        self.figure_id = figure_id.strip()
        self.voice_id = voice_id.strip()
        self.base_url = base_url.rstrip("/")
        self.figure_name = figure_name
        self.voice_name = voice_name
        self.callback_url = callback_url.strip()
        self.transparent = transparent
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.transport = transport or _default_transport
        self.idempotency_index: dict[str, AvatarJobSnapshot] = {}
        self.result_urls: dict[str, str] = {}

    @classmethod
    def from_env(cls) -> BaiduXilingAvatarProvider:
        return cls(
            app_id=os.getenv("BAIDU_XILING_APP_ID", ""),
            app_key=os.getenv("BAIDU_XILING_APP_KEY", ""),
            figure_id=os.getenv("BAIDU_XILING_FIGURE_ID", ""),
            voice_id=os.getenv("BAIDU_XILING_VOICE_ID", ""),
            base_url=os.getenv("BAIDU_XILING_BASE_URL", "https://open.xiling.baidu.com"),
            figure_name=os.getenv("BAIDU_XILING_FIGURE_NAME", "百度曦灵公共数字人"),
            voice_name=os.getenv("BAIDU_XILING_VOICE_NAME", "百度曦灵公共音色"),
            callback_url=os.getenv("BAIDU_XILING_CALLBACK_URL", ""),
            transparent=_bool_env("BAIDU_XILING_TRANSPARENT", False),
            timeout_seconds=_float_env("BAIDU_XILING_TIMEOUT_SECONDS", 20),
        )

    def _missing_configuration(self) -> list[str]:
        missing = []
        if not self.app_id:
            missing.append("BAIDU_XILING_APP_ID")
        if not self.app_key:
            missing.append("BAIDU_XILING_APP_KEY")
        if not self.figure_id:
            missing.append("BAIDU_XILING_FIGURE_ID")
        if not self.voice_id:
            missing.append("BAIDU_XILING_VOICE_ID")
        return missing

    def capabilities(self) -> AvatarCapability:
        missing = self._missing_configuration()
        return AvatarCapability(
            provider_name="baidu_xiling",
            display_name="百度曦灵基础视频合成",
            mode=ProviderMode.PRODUCTION,
            enabled=not missing,
            permission_status="authorized" if not missing else "configuration_missing",
            max_script_chars=_int_env("BAIDU_XILING_MAX_SCRIPT_CHARS", 20000),
            supported_aspect_ratios=["9:16"],
            estimated_cost_cny=float(os.getenv("BAIDU_XILING_ESTIMATED_45S_COST_CNY", "2.25")),
            estimated_seconds=45,
            missing_configuration=missing,
        )

    def list_assets(self) -> list[AvatarAsset]:
        self._ensure_configured()
        return [
            AvatarAsset(
                asset_id=self.figure_id,
                kind=AvatarAssetKind.AVATAR,
                name=self.figure_name,
                authorized=True,
            ),
            AvatarAsset(
                asset_id=self.voice_id,
                kind=AvatarAssetKind.VOICE,
                name=self.voice_name,
                authorized=True,
            ),
        ]

    def submit(self, request: AvatarSubmitRequest) -> AvatarJobSnapshot:
        self._ensure_configured()
        previous = self.find_job(request.idempotency_key)
        if previous is not None:
            return previous

        width, height = _parse_resolution(request.resolution)
        payload: dict[str, Any] = {
            "requestId": request.idempotency_key[:50],
            "figureId": request.avatar_id,
            "text": request.script_text,
            "driveType": "TEXT",
            "ttsParams": {
                "person": request.voice_id,
                "speed": str(_speed_to_baidu(request.speech_rate)),
                "pitch": "5",
                "volume": "5",
            },
            "videoParams": {
                "width": width,
                "height": height,
                "transparent": self.transparent,
            },
            "autoAnimoji": False,
            "subtitleParams": {
                "enabled": True,
                "subtitlePolicy": "SRT",
            },
        }
        if self.callback_url:
            payload["callbackUrl"] = self.callback_url

        data = self._json_request("POST", "/api/digitalhuman/open/v1/video/submit", payload)
        provider_job_id = str(data.get("taskId") or "").strip()
        if not provider_job_id:
            raise AvatarProviderError("百度曦灵未返回任务 ID。")
        snapshot = self._snapshot_from_result(
            data,
            idempotency_key=request.idempotency_key,
            default_status=AvatarProviderStatus.SUBMITTED,
        )
        self.idempotency_index[request.idempotency_key] = snapshot
        return snapshot

    def get_job(self, job_id: str) -> AvatarJobSnapshot:
        self._ensure_configured()
        query = urlencode({"taskId": job_id, "requestId": f"poll-{uuid.uuid4().hex[:12]}"})
        data = self._json_request("GET", f"/api/digitalhuman/open/v1/video/task?{query}")
        return self._snapshot_from_result(data, idempotency_key="")

    def find_job(self, idempotency_key: str) -> AvatarJobSnapshot | None:
        return self.idempotency_index.get(idempotency_key)

    def download_result(self, job_id: str) -> tuple[bytes, str]:
        url = self.result_urls.get(job_id)
        if not url:
            snapshot = self.get_job(job_id)
            if snapshot.status != AvatarProviderStatus.SUCCEEDED:
                raise AvatarProviderError("百度曦灵任务尚未成功，不能下载结果。")
            url = self.result_urls.get(job_id)
        if not url:
            raise AvatarProviderError("百度曦灵未返回视频下载地址。")
        return self.transport("GET", url, {"Accept": "video/mp4,*/*"}, None, 120)

    def _ensure_configured(self) -> None:
        missing = self._missing_configuration()
        if missing:
            raise AvatarProviderError(
                "百度曦灵尚未配置：" + "、".join(missing),
                kind=ProviderErrorKind.AUTHORIZATION,
            )

    def _authorization(self) -> str:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        signature = hmac.new(
            self.app_key.encode("utf-8"),
            f"{self.app_id}{expires_at}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{self.app_id}/{signature}/{expires_at}"

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": self._authorization(),
        }
        if payload is not None:
            headers["Content-Type"] = "application/json;charset=utf-8"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        raw, _ = self.transport(method, f"{self.base_url}{path}", headers, body, self.timeout_seconds)
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AvatarProviderError("百度曦灵返回了无效 JSON。") from exc
        if not bool(envelope.get("success")) or int(envelope.get("code", 1)) != 0:
            message = envelope.get("message")
            if isinstance(message, dict):
                message = message.get("global")
            raise AvatarProviderError(str(message or "百度曦灵请求失败"))
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise AvatarProviderError("百度曦灵响应缺少 result。")
        return result

    def _snapshot_from_result(
        self,
        data: dict[str, Any],
        *,
        idempotency_key: str,
        default_status: AvatarProviderStatus | None = None,
    ) -> AvatarJobSnapshot:
        provider_job_id = str(data.get("taskId") or "")
        status = _baidu_status(str(data.get("status") or ""), default_status)
        video_url = str(data.get("videoUrl") or "")
        if provider_job_id and video_url:
            self.result_urls[provider_job_id] = video_url
        error_message = str(data.get("failedMessage") or "") or None
        return AvatarJobSnapshot(
            job_id=provider_job_id,
            idempotency_key=idempotency_key,
            status=status,
            progress=_status_progress(status),
            stage=_status_stage(status),
            provider_job_id=provider_job_id,
            estimated_cost_cny=None,
            estimated_seconds=round(int(data.get("duration") or 0) / 1000) or None,
            result_mime="video/mp4" if video_url.endswith(".mp4") else None,
            error_kind=ProviderErrorKind.SERVICE if status == AvatarProviderStatus.FAILED else None,
            error_message=error_message,
        )


def build_avatar_provider():
    mode = os.getenv("AVATAR_PROVIDER_MODE", os.getenv("AVATAR_MODE", "sandbox")).lower()
    if mode in {"baidu", "baidu_xiling", "xiling"}:
        return BaiduXilingAvatarProvider.from_env()
    if mode in {"internal", "cloud"}:
        return InternalAvatarProvider.from_env()
    return SandboxAvatarProvider()


def _parse_resolution(resolution: str) -> tuple[int, int]:
    try:
        width, height = resolution.lower().split("x", 1)
        return int(width), int(height)
    except (ValueError, AttributeError):
        return 1080, 1920


def _speed_to_baidu(speech_rate: float) -> int:
    return max(0, min(15, round(5 * speech_rate)))


def _baidu_status(
    value: str,
    default_status: AvatarProviderStatus | None = None,
) -> AvatarProviderStatus:
    status_map = {
        "SUBMIT": AvatarProviderStatus.SUBMITTED,
        "LINE_UP": AvatarProviderStatus.QUEUED,
        "WAIT": AvatarProviderStatus.QUEUED,
        "GENERATING": AvatarProviderStatus.RUNNING,
        "SUCCESS": AvatarProviderStatus.SUCCEEDED,
        "FAILED": AvatarProviderStatus.FAILED,
    }
    return status_map.get(value.upper(), default_status or AvatarProviderStatus.OUTCOME_UNKNOWN)


def _status_progress(status: AvatarProviderStatus) -> int:
    return {
        AvatarProviderStatus.QUEUED: 10,
        AvatarProviderStatus.SUBMITTED: 15,
        AvatarProviderStatus.RUNNING: 60,
        AvatarProviderStatus.SUCCEEDED: 100,
        AvatarProviderStatus.FAILED: 100,
        AvatarProviderStatus.CANCELLED: 100,
        AvatarProviderStatus.OUTCOME_UNKNOWN: 0,
    }[status]


def _status_stage(status: AvatarProviderStatus) -> str:
    return {
        AvatarProviderStatus.QUEUED: "供应商排队中",
        AvatarProviderStatus.SUBMITTED: "已提交供应商",
        AvatarProviderStatus.RUNNING: "供应商合成中",
        AvatarProviderStatus.SUCCEEDED: "供应商合成成功",
        AvatarProviderStatus.FAILED: "供应商合成失败",
        AvatarProviderStatus.CANCELLED: "任务已取消",
        AvatarProviderStatus.OUTCOME_UNKNOWN: "供应商结果待核对",
    }[status]
