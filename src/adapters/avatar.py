from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import httpx

from src.models import (
    AvatarAsset,
    AvatarAssetKind,
    AvatarCapability,
    AvatarJobSnapshot,
    AvatarProfile,
    AvatarProviderStatus,
    AvatarSubmitRequest,
    ProviderErrorKind,
    ProviderMode,
)
from src.retry import ExternalServiceError, RetryPolicy, retry_with_policy

JsonTransport = Callable[
    [str, str, dict[str, str], bytes | None, float], tuple[bytes, str]
]
AudioRenderer = Callable[[str, float, str], bytes]
FileUploadTransport = Callable[[str, str, str, Path, float], tuple[bytes, str]]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_AVATAR_DOWNLOAD_BYTES = 100 * 1024 * 1024


def _default_file_upload_transport(
    url: str, filename: str, mime_type: str, path: Path, timeout: float
) -> tuple[bytes, str]:
    """Use a file handle so training media is not loaded into process memory."""
    try:
        with path.open("rb") as source, httpx.Client(
            timeout=timeout, follow_redirects=False
        ) as client:
            response = client.post(url, files={"file": (filename, source, mime_type)})
            response.raise_for_status()
            return response.content, response.headers.get(
                "Content-Type", "application/octet-stream"
            )
    except httpx.HTTPError as exc:
        raise AvatarProviderError(
            "公司素材上传连接失败，结果未确认，请勿直接重复提交。",
            outcome_unknown=True,
        ) from exc


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


def _split_env_paths(value: str) -> list[str]:
    return [
        item.strip() for item in value.replace("\n", ";").split(";") if item.strip()
    ]


def _missing_required_paths(paths: list[str], *, base_directory: Path) -> list[str]:
    missing = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_absolute():
            path = base_directory / path
        if not path.exists():
            missing.append(raw_path)
    return missing


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


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_no_redirect_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[bytes, str]:
    request = Request(url, data=body, headers=headers, method=method)
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            payload = response.read(MAX_AVATAR_DOWNLOAD_BYTES + 1)
            return payload, response.headers.get_content_type()
    except HTTPError as exc:
        detail = exc.read(300).decode("utf-8", errors="replace")
        kind = (
            ProviderErrorKind.VALIDATION
            if 300 <= exc.code < 400
            else ProviderErrorKind.AUTHORIZATION
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
            "无法下载数字人结果，请检查结果地址和网络状态。",
            kind=ProviderErrorKind.CONNECTION,
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
        self.transport = transport or _default_no_redirect_transport

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
            raise AvatarProviderError(
                "数字人任务不存在。", kind=ProviderErrorKind.VALIDATION
            )
        return snapshot

    def find_job(self, idempotency_key: str) -> AvatarJobSnapshot | None:
        job_id = self.idempotency_index.get(idempotency_key)
        return self.jobs.get(job_id) if job_id else None

    def download_result(self, job_id: str) -> tuple[bytes, str]:
        raise AvatarProviderError(
            "演示模式不会生成真实视频文件。",
            kind=ProviderErrorKind.VALIDATION,
        )


class LocalCommandAvatarProvider:
    """本地数字人模型运行器。

    该适配器只调用用户在环境变量中明确配置的本地命令：先运行 TTS，
    再串行运行 MuseTalk 或 SadTalker。模型、权重、FFmpeg 或授权素材缺失
    时，档位保持禁用，绝不伪造视频结果。
    """

    RECOVERY_GRACE_SECONDS = 1800

    PROFILE_CONFIG = {
        "local_fast": {
            "display_name": "本地极速版",
            "description": "MuseTalk 口型驱动，适合 GTX 1650 的单任务生成。",
            "command_env": "LOCAL_AVATAR_FAST_COMMAND",
            "required_paths_env": "LOCAL_AVATAR_FAST_REQUIRED_PATHS",
            "estimated_seconds": 300,
            "required_vram_gb": 4,
        },
        "local_natural": {
            "display_name": "本地自然版",
            "description": "SadTalker 头部动作驱动，文字会先转成 Windows 系统语音。",
            "command_env": "LOCAL_AVATAR_NATURAL_COMMAND",
            "required_paths_env": "LOCAL_AVATAR_NATURAL_REQUIRED_PATHS",
            "estimated_seconds": 420,
            "required_vram_gb": 4,
        },
        "local_recorded_natural": {
            "display_name": "本人录音驱动版",
            "description": "SadTalker 使用上传的完整口播录音驱动；文案只作任务记录。",
            "command_env": "LOCAL_AVATAR_NATURAL_COMMAND",
            "required_paths_env": "LOCAL_AVATAR_NATURAL_REQUIRED_PATHS",
            "estimated_seconds": 420,
            "required_vram_gb": 4,
            "uses_recorded_audio": True,
        },
    }

    def __init__(
        self,
        *,
        assets_manifest: str = "",
        output_directory: str = "data/avatar_local_jobs",
        working_directory: str = ".",
        tts_command: str = "",
        fast_command: str = "",
        natural_command: str = "",
        tts_required_paths: list[str] | None = None,
        fast_required_paths: list[str] | None = None,
        natural_required_paths: list[str] | None = None,
    ) -> None:
        self.working_directory = Path(working_directory or PROJECT_ROOT).resolve()
        self.assets_manifest = (
            self._working_relative_path(assets_manifest) if assets_manifest else None
        )
        self.output_directory = self._working_relative_path(output_directory)
        self.tts_command = tts_command.strip()
        self.commands = {
            "local_fast": fast_command.strip(),
            "local_natural": natural_command.strip(),
            "local_recorded_natural": natural_command.strip(),
        }
        self.tts_required_paths = tts_required_paths or []
        self.required_paths = {
            "local_fast": fast_required_paths or [],
            "local_natural": natural_required_paths or [],
            "local_recorded_natural": natural_required_paths or [],
        }
        self.jobs: dict[str, AvatarJobSnapshot] = {}
        self.idempotency_index: dict[str, str] = {}
        self.result_paths: dict[str, Path] = {}
        self._lock = threading.Lock()
        self._worker_lock = threading.Lock()

    def _working_relative_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if path.is_absolute():
            return path
        return self.working_directory / path

    @classmethod
    def from_env(cls) -> "LocalCommandAvatarProvider":
        return cls(
            assets_manifest=os.getenv("LOCAL_AVATAR_ASSETS_MANIFEST", ""),
            output_directory=os.getenv(
                "LOCAL_AVATAR_OUTPUT_DIRECTORY", "data/avatar_local_jobs"
            ),
            working_directory=os.getenv("LOCAL_AVATAR_WORKING_DIRECTORY", ""),
            tts_command=os.getenv("LOCAL_AVATAR_TTS_COMMAND", ""),
            fast_command=os.getenv("LOCAL_AVATAR_FAST_COMMAND", ""),
            natural_command=os.getenv("LOCAL_AVATAR_NATURAL_COMMAND", ""),
            tts_required_paths=_split_env_paths(
                os.getenv("LOCAL_AVATAR_TTS_REQUIRED_PATHS", "")
            ),
            fast_required_paths=_split_env_paths(
                os.getenv("LOCAL_AVATAR_FAST_REQUIRED_PATHS", "")
            ),
            natural_required_paths=_split_env_paths(
                os.getenv("LOCAL_AVATAR_NATURAL_REQUIRED_PATHS", "")
            ),
        )

    def capabilities(self) -> AvatarCapability:
        profiles = [self._profile(profile_id) for profile_id in self.PROFILE_CONFIG]
        enabled_profiles = [profile for profile in profiles if profile.enabled]
        missing = [] if enabled_profiles else ["至少一个本地视频命令和授权素材"]
        return AvatarCapability(
            provider_name="local_avatar",
            display_name="本地数字人（GTX 1650 单任务）",
            mode=ProviderMode.PRODUCTION,
            enabled=bool(enabled_profiles),
            permission_status="authorized"
            if enabled_profiles
            else "configuration_missing",
            max_script_chars=240,
            supported_aspect_ratios=["9:16"],
            estimated_cost_cny=0 if enabled_profiles else None,
            estimated_seconds=min(
                (profile.estimated_seconds or 0 for profile in enabled_profiles),
                default=None,
            ),
            missing_configuration=missing,
            profiles=profiles,
        )

    def list_assets(self) -> list[AvatarAsset]:
        return self._load_assets()

    def submit(self, request: AvatarSubmitRequest) -> AvatarJobSnapshot:
        profile = self._profile(request.profile_id)
        if not profile.enabled:
            raise AvatarProviderError(
                f"生成方案“{request.profile_id}”尚未就绪："
                + "、".join(profile.missing_configuration),
                kind=ProviderErrorKind.AUTHORIZATION,
            )
        existing = self.find_job(request.idempotency_key)
        if existing is not None:
            return existing
        assets = {asset.asset_id: asset for asset in self._load_assets()}
        avatar = assets.get(request.avatar_id)
        voice = assets.get(request.voice_id)
        if avatar is None or voice is None:
            raise AvatarProviderError(
                "所选本地授权形象或声音不存在。", kind=ProviderErrorKind.VALIDATION
            )
        job_id = f"local-avatar-{uuid.uuid4().hex[:12]}"
        snapshot = AvatarJobSnapshot(
            job_id=job_id,
            idempotency_key=request.idempotency_key,
            status=AvatarProviderStatus.QUEUED,
            progress=5,
            stage="本地单任务队列等待中",
            provider_job_id=job_id,
            estimated_cost_cny=0,
            estimated_seconds=profile.estimated_seconds,
        )
        with self._lock:
            self.jobs[job_id] = snapshot
            self.idempotency_index[request.idempotency_key] = job_id
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, request, avatar, voice),
            name=f"avatar-{job_id}",
            daemon=True,
        )
        thread.start()
        return snapshot

    def get_job(self, job_id: str) -> AvatarJobSnapshot:
        with self._lock:
            snapshot = self.jobs.get(job_id)
        if (
            snapshot is not None
            and snapshot.status != AvatarProviderStatus.OUTCOME_UNKNOWN
        ):
            return snapshot

        recovered, result_path = self._recover_job(job_id)
        with self._lock:
            current = self.jobs.get(job_id)
            if (
                current is None
                or current.status == AvatarProviderStatus.OUTCOME_UNKNOWN
            ):
                self.jobs[job_id] = recovered
                if result_path is not None:
                    self.result_paths[job_id] = result_path
                return recovered
            return current

    def find_job(self, idempotency_key: str) -> AvatarJobSnapshot | None:
        with self._lock:
            job_id = self.idempotency_index.get(idempotency_key)
            return self.jobs.get(job_id) if job_id else None

    def download_result(self, job_id: str) -> tuple[bytes, str]:
        with self._lock:
            path = self.result_paths.get(job_id)
        if path is None:
            path = self._valid_result_path(job_id)
            if path is not None:
                with self._lock:
                    self.result_paths[job_id] = path
        if path is None:
            raise AvatarProviderError(
                "本地成片尚未生成。", kind=ProviderErrorKind.VALIDATION
            )
        return path.read_bytes(), "video/mp4"

    def _recover_job(self, job_id: str) -> tuple[AvatarJobSnapshot, Path | None]:
        job_directory = self._job_directory(job_id)
        result_path = self._valid_result_path(job_id)
        if result_path is not None:
            return (
                AvatarJobSnapshot(
                    job_id=job_id,
                    idempotency_key=job_id,
                    status=AvatarProviderStatus.SUCCEEDED,
                    progress=100,
                    stage="本地成片已从磁盘恢复",
                    provider_job_id=job_id,
                    estimated_cost_cny=0,
                    result_mime="video/mp4",
                    result_size_bytes=result_path.stat().st_size,
                ),
                result_path,
            )
        if not job_directory.is_dir():
            raise AvatarProviderError(
                "本地数字人任务不存在。",
                kind=ProviderErrorKind.VALIDATION,
            )

        latest_mtime = job_directory.stat().st_mtime
        for path in job_directory.rglob("*"):
            try:
                latest_mtime = max(latest_mtime, path.stat().st_mtime)
            except OSError:
                continue
        age_seconds = max(0, datetime.now(timezone.utc).timestamp() - latest_mtime)
        if age_seconds <= self.RECOVERY_GRACE_SECONDS:
            return (
                AvatarJobSnapshot(
                    job_id=job_id,
                    idempotency_key=job_id,
                    status=AvatarProviderStatus.OUTCOME_UNKNOWN,
                    progress=60,
                    stage="后端重启，正在恢复本地生成结果",
                    provider_job_id=job_id,
                    estimated_cost_cny=0,
                    error_kind=ProviderErrorKind.OUTCOME_UNKNOWN,
                    error_message="本地任务状态因后端重启而丢失，正在等待成片落盘。",
                ),
                None,
            )
        return (
            AvatarJobSnapshot(
                job_id=job_id,
                idempotency_key=job_id,
                status=AvatarProviderStatus.FAILED,
                progress=100,
                stage="本地生成已中断",
                provider_job_id=job_id,
                estimated_cost_cny=0,
                error_kind=ProviderErrorKind.SERVICE,
                error_message="后端重启后未找到完整成片，本次本地生成已中断，请重新提交。",
            ),
            None,
        )

    def _job_directory(self, job_id: str) -> Path:
        suffix = job_id.removeprefix("local-avatar-")
        if not job_id.startswith("local-avatar-") or not suffix or not suffix.isalnum():
            raise AvatarProviderError(
                "本地数字人任务编号无效。",
                kind=ProviderErrorKind.VALIDATION,
            )
        return self.output_directory.resolve() / job_id

    def _valid_result_path(self, job_id: str) -> Path | None:
        result_path = self._job_directory(job_id) / "result.mp4"
        try:
            if result_path.stat().st_size < 12:
                return None
            with result_path.open("rb") as result_file:
                if b"ftyp" not in result_file.read(12)[4:12]:
                    return None
        except OSError:
            return None
        return result_path

    def _profile(self, profile_id: str) -> AvatarProfile:
        config = self.PROFILE_CONFIG.get(profile_id)
        if config is None:
            return AvatarProfile(
                profile_id=profile_id,
                display_name=profile_id,
                enabled=False,
                missing_configuration=["未知的本地生成方案"],
            )
        missing = []
        if not config.get("uses_recorded_audio") and not self.tts_command:
            missing.append("LOCAL_AVATAR_TTS_COMMAND")
        if not self.commands[profile_id]:
            missing.append(config["command_env"])
        if not config.get("uses_recorded_audio"):
            for path in _missing_required_paths(
                self.tts_required_paths,
                base_directory=self.working_directory,
            ):
                missing.append(f"LOCAL_AVATAR_TTS_REQUIRED_PATHS: {path}")
        for path in _missing_required_paths(
            self.required_paths[profile_id],
            base_directory=self.working_directory,
        ):
            missing.append(f"{config['required_paths_env']}: {path}")
        if not self._manifest_is_usable():
            missing.append("LOCAL_AVATAR_ASSETS_MANIFEST（含授权头像和声音）")
        return AvatarProfile(
            profile_id=profile_id,
            display_name=config["display_name"],
            description=config["description"],
            enabled=not missing,
            estimated_cost_cny=0 if not missing else None,
            estimated_seconds=config["estimated_seconds"],
            required_vram_gb=config["required_vram_gb"],
            missing_configuration=missing,
        )

    def _manifest_is_usable(self) -> bool:
        assets = self._load_assets()
        return any(item.kind == AvatarAssetKind.AVATAR for item in assets) and any(
            item.kind == AvatarAssetKind.VOICE for item in assets
        )

    def _load_assets(self) -> list[AvatarAsset]:
        if self.assets_manifest is None or not self.assets_manifest.is_file():
            return []
        try:
            payload = json.loads(self.assets_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = payload.get("assets", payload) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        assets: list[AvatarAsset] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("authorized"):
                continue
            raw_kind = item.get("kind")
            raw_path = item.get("path")
            if raw_kind not in {"avatar", "voice"} or not isinstance(raw_path, str):
                continue
            file_path = self._manifest_relative_path(raw_path)
            if not file_path.is_file():
                continue
            try:
                asset_id = str(item["asset_id"])
                preview_url = (
                    str(item.get("preview_url"))
                    if item.get("preview_url")
                    else (
                        f"/api/v1/avatar/assets/{asset_id}/media"
                        if raw_kind == "voice"
                        else None
                    )
                )
                assets.append(
                    AvatarAsset(
                        asset_id=asset_id,
                        kind=AvatarAssetKind(raw_kind),
                        name=str(item.get("name") or item["asset_id"]),
                        preview_url=preview_url,
                        preview_type=str(
                            item.get("preview_type")
                            or ("audio" if raw_kind == "voice" else "image")
                        ),
                        authorized=True,
                    )
                )
            except (KeyError, ValueError):
                continue
        return assets

    def _asset_path(self, asset_id: str) -> Path:
        if self.assets_manifest is None:
            raise AvatarProviderError(
                "本地资产清单不存在。", kind=ProviderErrorKind.VALIDATION
            )
        payload = json.loads(self.assets_manifest.read_text(encoding="utf-8"))
        items = payload.get("assets", payload) if isinstance(payload, dict) else payload
        for item in items:
            if isinstance(item, dict) and item.get("asset_id") == asset_id:
                path = self._manifest_relative_path(str(item.get("path", "")))
                if path.is_file():
                    return path.resolve()
        raise AvatarProviderError(
            "本地授权素材文件不存在。", kind=ProviderErrorKind.VALIDATION
        )

    def _manifest_relative_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if path.is_absolute() or self.assets_manifest is None:
            return path
        return self.assets_manifest.parent / path

    def _run_job(
        self,
        job_id: str,
        request: AvatarSubmitRequest,
        avatar: AvatarAsset,
        voice: AvatarAsset,
    ) -> None:
        with self._worker_lock:
            try:
                config = self.PROFILE_CONFIG[request.profile_id]
                self.output_directory.mkdir(parents=True, exist_ok=True)
                job_directory = self.output_directory / job_id
                job_directory.mkdir(parents=True, exist_ok=True)
                audio_path = job_directory / "speech.wav"
                output_path = job_directory / "result.mp4"
                avatar_path = self._asset_path(avatar.asset_id)
                voice_path = self._asset_path(voice.asset_id)
                if config.get("uses_recorded_audio"):
                    self._update(
                        job_id, AvatarProviderStatus.RUNNING, 20, "使用本人录音驱动中"
                    )
                    self._prepare_recorded_audio(voice_path, audio_path)
                else:
                    self._update(
                        job_id, AvatarProviderStatus.RUNNING, 20, "本地文字转语音生成中"
                    )
                    self._run_command(
                        self.tts_command,
                        request=request,
                        avatar_path=avatar_path,
                        voice_path=voice_path,
                        audio_path=audio_path,
                        output_path=output_path,
                    )
                if not audio_path.is_file() or audio_path.stat().st_size == 0:
                    raise AvatarProviderError("本地任务未生成有效驱动音频。")
                self._update(
                    job_id, AvatarProviderStatus.RUNNING, 60, "本地数字人视频生成中"
                )
                self._run_command(
                    self.commands[request.profile_id],
                    request=request,
                    avatar_path=avatar_path,
                    voice_path=voice_path,
                    audio_path=audio_path,
                    output_path=output_path,
                )
                if not output_path.is_file() or output_path.stat().st_size < 12:
                    raise AvatarProviderError("本地模型未生成有效 MP4 成片。")
                with self._lock:
                    self.result_paths[job_id] = output_path
                self._update(
                    job_id, AvatarProviderStatus.SUCCEEDED, 100, "本地成片已生成"
                )
            except AvatarProviderError as exc:
                self._fail(job_id, str(exc))
            except Exception as exc:  # noqa: BLE001
                self._fail(job_id, f"本地数字人任务失败：{str(exc)[:300]}")

    def _prepare_recorded_audio(self, voice_path: Path, audio_path: Path) -> None:
        if voice_path.suffix.lower() == ".wav":
            shutil.copyfile(voice_path, audio_path)
            return
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise AvatarProviderError(
                "上传录音不是 WAV，且未找到 FFmpeg，无法转换音频。"
            )
        completed = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(voice_path.resolve()),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(audio_path.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "未知错误").strip()[:300]
            raise AvatarProviderError(f"上传录音转换失败：{detail}")

    def _run_command(
        self,
        template: str,
        *,
        request: AvatarSubmitRequest,
        avatar_path: Path,
        voice_path: Path,
        audio_path: Path,
        output_path: Path,
    ) -> None:
        values = {
            "script_text": request.script_text,
            "speech_rate": str(request.speech_rate),
            "avatar_path": str(avatar_path.resolve()),
            "voice_path": str(voice_path.resolve()),
            "audio_path": str(audio_path.resolve()),
            "audio_output_path": str(audio_path.resolve()),
            "output_path": str(output_path.resolve()),
        }
        try:
            command = [
                part.format(**values)
                for part in shlex.split(template, posix=os.name != "nt")
            ]
        except (KeyError, ValueError) as exc:
            raise AvatarProviderError(f"本地命令模板无效：{exc}") from exc
        if not command:
            raise AvatarProviderError("本地命令不能为空。")
        env = {
            **os.environ,
            "LOCAL_AVATAR_SCRIPT_TEXT": request.script_text,
            "LOCAL_AVATAR_SPEECH_RATE": str(request.speech_rate),
            "LOCAL_AVATAR_AVATAR_PATH": str(avatar_path.resolve()),
            "LOCAL_AVATAR_VOICE_PATH": str(voice_path.resolve()),
            "LOCAL_AVATAR_AUDIO_PATH": str(audio_path.resolve()),
            "LOCAL_AVATAR_AUDIO_OUTPUT_PATH": str(audio_path.resolve()),
            "LOCAL_AVATAR_OUTPUT_PATH": str(output_path.resolve()),
        }
        completed = subprocess.run(
            command,
            cwd=str(self.working_directory.resolve()),
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "未知错误").strip()[:300]
            raise AvatarProviderError(f"本地模型命令执行失败：{detail}")

    def _update(
        self, job_id: str, status: AvatarProviderStatus, progress: int, stage: str
    ) -> None:
        with self._lock:
            snapshot = self.jobs[job_id]
            self.jobs[job_id] = snapshot.model_copy(
                update={"status": status, "progress": progress, "stage": stage}
            )

    def _fail(self, job_id: str, message: str) -> None:
        with self._lock:
            snapshot = self.jobs[job_id]
            self.jobs[job_id] = snapshot.model_copy(
                update={
                    "status": AvatarProviderStatus.FAILED,
                    "progress": 100,
                    "stage": "本地生成失败",
                    "error_kind": ProviderErrorKind.SERVICE,
                    "error_message": message,
                }
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
            base_url=os.getenv(
                "BAIDU_XILING_BASE_URL", "https://open.xiling.baidu.com"
            ),
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
            estimated_cost_cny=_float_env("BAIDU_XILING_ESTIMATED_45S_COST_CNY", 2.25),
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

        data = self._json_request(
            "POST", "/api/digitalhuman/open/v1/video/submit", payload
        )
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
        query = urlencode(
            {"taskId": job_id, "requestId": f"poll-{uuid.uuid4().hex[:12]}"}
        )
        data = self._json_request(
            "GET", f"/api/digitalhuman/open/v1/video/task?{query}"
        )
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
        raw, _ = self.transport(
            method, f"{self.base_url}{path}", headers, body, self.timeout_seconds
        )
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
            error_kind=ProviderErrorKind.SERVICE
            if status == AvatarProviderStatus.FAILED
            else None,
            error_message=error_message,
        )


class ShuyingLegacyAvatarProvider:
    """数影公司旧网关适配器：云 TTS/上传后用 api_code 合成并查询视频。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_code: str,
        avatars_json: str = "[]",
        voices_json: str = "[]",
        result_allowed_hosts: str = "",
        audio_mode: str = "gateway_voice",
        audio_upload_url: str = "",
        audio_allowed_hosts: str = "",
        assets_manifest_path: str = "",
        model_upload_url: str = "",
        model_upload_allowed_hosts: str = "",
        voice_base_url: str = "",
        voice_api_code: str = "",
        edge_tts_voice: str = "zh-CN-XiaoxiaoNeural",
        enabled: bool = False,
        timeout_seconds: float = 120,
        result_timeout_seconds: float = 120,
        max_script_chars: int = 2000,
        estimated_cost_cny: float | None = None,
        estimated_seconds: int | None = None,
        transport: JsonTransport | None = None,
        download_transport: JsonTransport | None = None,
        audio_renderer: AudioRenderer | None = None,
        file_upload_transport: FileUploadTransport | None = None,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_code = api_code.strip()
        self.enabled = enabled
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.result_timeout_seconds = max(1.0, result_timeout_seconds)
        self.max_script_chars = max(1, max_script_chars)
        self.estimated_cost_cny = estimated_cost_cny
        self.estimated_seconds = estimated_seconds
        self.transport = transport or _default_transport
        self.download_transport = download_transport or _default_no_redirect_transport
        self.audio_renderer = audio_renderer or self._render_edge_tts
        self.file_upload_transport = file_upload_transport or _default_file_upload_transport
        self.avatars = self._parse_assets(avatars_json, AvatarAssetKind.AVATAR)
        self.voices = self._parse_assets(voices_json, AvatarAssetKind.VOICE)
        self.audio_mode = audio_mode.strip().casefold() or "gateway_voice"
        self.audio_upload_url = audio_upload_url.strip()
        self.edge_tts_voice = edge_tts_voice.strip()
        self.audio_allowed_hosts = {
            item.strip().casefold()
            for item in audio_allowed_hosts.replace(";", ",").split(",")
            if item.strip()
        }
        raw_manifest_path = assets_manifest_path.strip() or str(
            Path("data") / "avatar_assets" / "shuying_cloud.json"
        )
        manifest_path = Path(raw_manifest_path)
        self.assets_manifest_path = (
            manifest_path if manifest_path.is_absolute() else PROJECT_ROOT / manifest_path
        ).resolve()
        self.model_upload_url = (model_upload_url.strip() or self.audio_upload_url).rstrip("/")
        self.model_upload_allowed_hosts = {
            item.strip().casefold()
            for item in (model_upload_allowed_hosts or audio_allowed_hosts)
            .replace(";", ",")
            .split(",")
            if item.strip()
        }
        self.voice_base_url = voice_base_url.strip().rstrip("/")
        self.voice_api_code = voice_api_code.strip()
        self.result_allowed_hosts = {
            item.strip().casefold()
            for item in result_allowed_hosts.replace(";", ",").split(",")
            if item.strip()
        }
        self.idempotency_index: dict[str, AvatarJobSnapshot] = {}
        self.job_idempotency: dict[str, str] = {}
        self.result_urls: dict[str, str] = {}

    @classmethod
    def from_env(cls) -> ShuyingLegacyAvatarProvider:
        cost = _float_env("SHUYING_AVATAR_ESTIMATED_COST_CNY", 0)
        seconds = _int_env("SHUYING_AVATAR_ESTIMATED_SECONDS", 0)
        return cls(
            base_url=os.getenv("SHUYING_AVATAR_BASE_URL", ""),
            api_code=os.getenv("SHUYING_AVATAR_API_CODE", ""),
            avatars_json=os.getenv("SHUYING_AVATAR_AVATARS_JSON", "[]"),
            voices_json=os.getenv("SHUYING_AVATAR_VOICES_JSON", "[]"),
            result_allowed_hosts=os.getenv("SHUYING_AVATAR_RESULT_ALLOWED_HOSTS", ""),
            audio_mode=os.getenv("SHUYING_AVATAR_AUDIO_MODE", "gateway_voice"),
            audio_upload_url=os.getenv("SHUYING_AVATAR_AUDIO_UPLOAD_URL", ""),
            audio_allowed_hosts=os.getenv("SHUYING_AVATAR_AUDIO_ALLOWED_HOSTS", ""),
            assets_manifest_path=os.getenv("SHUYING_AVATAR_ASSETS_MANIFEST", ""),
            model_upload_url=os.getenv("SHUYING_AVATAR_MODEL_UPLOAD_URL", ""),
            model_upload_allowed_hosts=os.getenv(
                "SHUYING_AVATAR_MODEL_UPLOAD_ALLOWED_HOSTS", ""
            ),
            voice_base_url=os.getenv("SHUYING_VOICE_BASE_URL", ""),
            voice_api_code=os.getenv("SHUYING_VOICE_API_CODE", ""),
            edge_tts_voice=os.getenv(
                "SHUYING_AVATAR_EDGE_TTS_VOICE", "zh-CN-XiaoxiaoNeural"
            ),
            enabled=_bool_env("SHUYING_AVATAR_ENABLED", False),
            timeout_seconds=_float_env("SHUYING_AVATAR_TIMEOUT_SECONDS", 120),
            result_timeout_seconds=_float_env(
                "SHUYING_AVATAR_RESULT_TIMEOUT_SECONDS", 120
            ),
            max_script_chars=_int_env("SHUYING_AVATAR_MAX_SCRIPT_CHARS", 2000),
            estimated_cost_cny=cost if cost > 0 else None,
            estimated_seconds=seconds if seconds > 0 else None,
        )

    @staticmethod
    def _parse_assets(raw: str, kind: AvatarAssetKind) -> list[AvatarAsset]:
        try:
            payload = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        assets: list[AvatarAsset] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            asset_id = str(
                item.get("asset_id")
                or item.get("id")
                or item.get("robotId")
                or item.get("speaker_id")
                or ""
            ).strip()
            name = str(item.get("name") or item.get("title") or asset_id).strip()
            if not asset_id or not name:
                continue
            assets.append(
                AvatarAsset(
                    asset_id=asset_id,
                    kind=kind,
                    name=name,
                    preview_url=str(item.get("preview_url") or "").strip() or None,
                    authorized=bool(item.get("authorized", True)),
                    preview_type=str(
                        item.get("preview_type")
                        or (
                            "video"
                            if str(item.get("preview_url") or "").casefold().split("?", 1)[0].endswith((".mp4", ".mov", ".webm"))
                            else "image"
                        )
                    ),
                    status=str(item.get("status") or "ready"),
                    status_message=str(item.get("status_message") or "").strip() or None,
                    source_type=str(item.get("source_type") or "built_in"),
                )
            )
        return assets

    def _missing_configuration(self) -> list[str]:
        missing = []
        if not self.enabled:
            missing.append("SHUYING_AVATAR_ENABLED")
        if not self.base_url:
            missing.append("SHUYING_AVATAR_BASE_URL")
        elif not self._is_safe_https_url(self.base_url):
            missing.append("SHUYING_AVATAR_BASE_URL(必须为HTTPS)")
        if not self.api_code:
            missing.append("SHUYING_AVATAR_API_CODE")
        if not any(item.authorized for item in self.avatars):
            missing.append("SHUYING_AVATAR_AVATARS_JSON")
        if not any(item.authorized for item in self.voices):
            missing.append("SHUYING_AVATAR_VOICES_JSON")
        if self.audio_mode not in {"gateway_voice", "edge_tts_upload"}:
            missing.append("SHUYING_AVATAR_AUDIO_MODE")
        elif self.audio_mode == "edge_tts_upload":
            if not self.audio_upload_url:
                missing.append("SHUYING_AVATAR_AUDIO_UPLOAD_URL")
            elif not self._is_safe_https_url(self.audio_upload_url):
                missing.append("SHUYING_AVATAR_AUDIO_UPLOAD_URL(必须为HTTPS)")
            if not self.audio_allowed_hosts:
                missing.append("SHUYING_AVATAR_AUDIO_ALLOWED_HOSTS")
            if not self.edge_tts_voice:
                missing.append("SHUYING_AVATAR_EDGE_TTS_VOICE")
        if not self.result_allowed_hosts:
            missing.append("SHUYING_AVATAR_RESULT_ALLOWED_HOSTS")
        return missing

    def capabilities(self) -> AvatarCapability:
        missing = self._missing_configuration()
        return AvatarCapability(
            provider_name="shuying_legacy_cloud",
            display_name="公司数影云数字人",
            mode=ProviderMode.PRODUCTION,
            enabled=not missing,
            permission_status="authorized" if not missing else "configuration_missing",
            max_script_chars=self.max_script_chars,
            supported_aspect_ratios=["9:16"],
            estimated_cost_cny=self.estimated_cost_cny,
            estimated_seconds=self.estimated_seconds,
            missing_configuration=missing,
            supports_cloud_avatar_training=not missing and self._can_train_avatar(),
            supports_voice_cloning=not missing and self._can_clone_voice(),
            supports_voice_sample_upload=not missing,
        )

    def list_assets(self) -> list[AvatarAsset]:
        self._ensure_configured()
        custom_assets = self._refresh_custom_assets(self._load_custom_assets())
        return [*self.avatars, *self.voices, *custom_assets]

    def create_cloud_avatar(
        self, *, name: str, training_video_path: Path, filename: str
    ) -> AvatarAsset:
        self._ensure_configured()
        if not self._can_train_avatar():
            raise AvatarProviderError(
                "公司云形象训练尚未配置素材上传线路。",
                kind=ProviderErrorKind.AUTHORIZATION,
            )
        video_url = self._upload_training_file(
            training_video_path,
            filename=filename,
            mime_type="video/mp4",
            upload_url=self._model_upload_endpoint(),
            allowed_hosts=self.model_upload_allowed_hosts,
        )
        response = self._form_request(
            "/model", {"name": name, "videoUrl": video_url}, submit_operation=True
        )
        data = response.get("data")
        provider_id = (
            str(data.get("id") or "").strip() if isinstance(data, dict) else ""
        )
        if not provider_id:
            raise AvatarProviderError("公司云形象训练未返回模型编号。")
        asset = AvatarAsset(
            asset_id=f"shuying-avatar-{provider_id}",
            kind=AvatarAssetKind.AVATAR,
            name=name,
            preview_url=video_url,
            preview_type="video",
            authorized=True,
            status="training",
            status_message="公司云端正在训练形象。",
            source_type="custom",
        )
        self._upsert_custom_asset(asset, provider_asset_id=provider_id)
        return asset

    def create_voice_clone(
        self, *, name: str, sample_path: Path, filename: str, mime_type: str
    ) -> AvatarAsset:
        self._ensure_configured()
        if not self._can_clone_voice():
            raise AvatarProviderError(
                "声音克隆线路未配置独立凭证。",
                kind=ProviderErrorKind.AUTHORIZATION,
            )
        sample_url = self._upload_training_file(
            sample_path,
            filename=filename,
            mime_type=mime_type,
            upload_url=self.audio_upload_url,
            allowed_hosts=self.audio_allowed_hosts,
        )
        response = self._voice_form_request(
            "/voice_clone",
            {
                "speaker_id": "",
                "url": sample_url,
                "language": "cn",
                "name": name,
                "type": 2,
            },
            submit_operation=True,
            zero_code_success=True,
        )
        data = response.get("data")
        provider_id = (
            str(data.get("task_id") or "").strip() if isinstance(data, dict) else ""
        )
        if not provider_id:
            raise AvatarProviderError("声音克隆未返回训练任务编号。")
        asset_id = f"shuying-voice-{provider_id}"
        preview_path = self._store_voice_sample_preview(
            sample_path=sample_path,
            filename=filename,
            asset_id=asset_id,
        )
        asset = AvatarAsset(
            asset_id=asset_id,
            kind=AvatarAssetKind.VOICE,
            name=name,
            preview_url=f"/api/v1/avatar/assets/{asset_id}/media",
            preview_type="audio",
            authorized=True,
            status="training",
            status_message="公司云端正在训练声音。",
            source_type="custom_clone",
        )
        self._upsert_custom_asset(
            asset,
            provider_asset_id=provider_id,
            extra={"sample_path": str(preview_path)},
        )
        return asset

    def store_pending_voice_sample(
        self, *, name: str, sample_path: Path, filename: str
    ) -> AvatarAsset:
        """Accept authorised samples before the separate clone credential is ready.

        Saving the sample is deliberately not a clone submission: enabling a key later
        must never silently create a billable training task.
        """
        self._ensure_configured()
        asset_id = f"shuying-voice-sample-{uuid.uuid4().hex[:12]}"
        destination = self._store_voice_sample_preview(
            sample_path=sample_path,
            filename=filename,
            asset_id=asset_id,
        )
        asset = AvatarAsset(
            asset_id=asset_id,
            kind=AvatarAssetKind.VOICE,
            name=name,
            preview_url=f"/api/v1/avatar/assets/{asset_id}/media",
            preview_type="audio",
            authorized=True,
            status="pending_configuration",
            status_message="声音样本已保存，等待独立声音线路配置后再发起克隆。",
            source_type="pending_clone",
        )
        self._upsert_custom_asset(
            asset,
            provider_asset_id="",
            extra={"sample_path": str(destination)},
        )
        return asset

    def _store_voice_sample_preview(
        self, *, sample_path: Path, filename: str, asset_id: str
    ) -> Path:
        suffix = Path(filename).suffix.lower() or ".mp3"
        samples_root = (self.assets_manifest_path.parent / "voice_samples").resolve()
        samples_root.mkdir(parents=True, exist_ok=True)
        destination = (samples_root / f"{asset_id}{suffix}").resolve()
        if samples_root not in destination.parents:
            raise AvatarProviderError("声音样本存储路径越界。")
        shutil.copyfile(sample_path, destination)
        return destination

    def _can_train_avatar(self) -> bool:
        return bool(
            self.model_upload_url
            and self.model_upload_allowed_hosts
            and self._is_safe_upload_url(self.model_upload_url)
        )

    def _can_clone_voice(self) -> bool:
        return bool(
            self.voice_base_url
            and self.voice_api_code
            and self.audio_upload_url
            and self.audio_allowed_hosts
            and self._is_safe_https_url(self.voice_base_url)
        )

    def _model_upload_endpoint(self) -> str:
        separator = "&" if "?" in self.model_upload_url else "?"
        return (
            self.model_upload_url
            if "is_video=" in self.model_upload_url
            else f"{self.model_upload_url}{separator}is_video=1"
        )

    def _load_custom_assets(self) -> list[dict[str, Any]]:
        if not self.assets_manifest_path.exists():
            return []
        try:
            payload = json.loads(self.assets_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = payload.get("assets", []) if isinstance(payload, dict) else []
        return [item for item in items if isinstance(item, dict)]

    def _save_custom_assets(self, items: list[dict[str, Any]]) -> None:
        self.assets_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.assets_manifest_path.with_suffix(
            f"{self.assets_manifest_path.suffix}.tmp"
        )
        temporary.write_text(
            json.dumps({"assets": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.assets_manifest_path)

    def _upsert_custom_asset(
        self,
        asset: AvatarAsset,
        *,
        provider_asset_id: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        items = self._load_custom_assets()
        record = asset.model_dump()
        record.update(
            {
                "provider_asset_id": provider_asset_id,
                "updated_at": datetime.now().astimezone().isoformat(),
            }
        )
        if extra:
            record.update(extra)
        self._save_custom_assets(
            [item for item in items if item.get("asset_id") != asset.asset_id]
            + [record]
        )

    def _refresh_custom_assets(
        self, records: list[dict[str, Any]]
    ) -> list[AvatarAsset]:
        changed = False
        assets: list[AvatarAsset] = []
        for record in records:
            try:
                asset = AvatarAsset.model_validate(record)
            except ValueError:
                continue
            raw_sample_path = str(record.get("sample_path") or "").strip()
            if asset.kind == AvatarAssetKind.VOICE and raw_sample_path:
                sample_path = Path(raw_sample_path)
                if not sample_path.is_absolute():
                    sample_path = self.assets_manifest_path.parent / sample_path
                sample_path = sample_path.resolve()
                samples_root = (
                    self.assets_manifest_path.parent / "voice_samples"
                ).resolve()
                if samples_root in sample_path.parents and sample_path.is_file():
                    asset = asset.model_copy(
                        update={
                            "preview_url": (
                                f"/api/v1/avatar/assets/{asset.asset_id}/media"
                            ),
                            "preview_type": "audio",
                        }
                    )
            if asset.status == "training":
                try:
                    refreshed = self._refresh_training_asset(asset, record)
                except AvatarProviderError:
                    refreshed = asset
                if refreshed != asset:
                    record.update(refreshed.model_dump())
                    record["updated_at"] = datetime.now().astimezone().isoformat()
                    changed = True
                    asset = refreshed
            assets.append(asset)
        if changed:
            self._save_custom_assets(records)
        return assets

    def _refresh_training_asset(
        self, asset: AvatarAsset, record: dict[str, Any]
    ) -> AvatarAsset:
        provider_id = str(record.get("provider_asset_id") or "").strip()
        if not provider_id:
            return asset
        if asset.kind == AvatarAssetKind.AVATAR:
            response = self._form_request(
                "/modelDetail", {"id": provider_id}, submit_operation=False
            )
            data = response.get("data")
            if not isinstance(data, dict):
                return asset
            status = self._training_status(data.get("status"))
            preview_url = str(data.get("coverUrl") or data.get("videoUrl") or "").strip()
            return asset.model_copy(
                update={
                    "status": status,
                    "status_message": self._training_status_message(status),
                    "preview_url": preview_url or asset.preview_url,
                    "preview_type": "image" if data.get("coverUrl") else "video",
                }
            )
        if asset.kind == AvatarAssetKind.VOICE and self._can_clone_voice():
            response = self._voice_form_request(
                "/voice_clone_status_2",
                {"speaker_id": provider_id, "type": 2},
                submit_operation=False,
            )
            data = response.get("data")
            raw_status = data.get("status") if isinstance(data, dict) else None
            status = self._training_status(raw_status)
            return asset.model_copy(
                update={
                    "status": status,
                    "status_message": self._training_status_message(status),
                }
            )
        return asset

    @staticmethod
    def _training_status(value: Any) -> str:
        try:
            status = int(value)
        except (TypeError, ValueError):
            return "training"
        if status == 2:
            return "ready"
        if status in {3, 4, -1, 8}:
            return "failed"
        return "training"

    @staticmethod
    def _training_status_message(status: str) -> str:
        return {
            "ready": "训练完成，可用于数字人生成。",
            "failed": "供应商训练失败或审核未通过。",
        }.get(status, "公司云端正在训练，请稍后刷新。")

    def _upload_training_file(
        self,
        path: Path,
        *,
        filename: str,
        mime_type: str,
        upload_url: str,
        allowed_hosts: set[str],
    ) -> str:
        if not self._is_safe_upload_url(upload_url):
            raise AvatarProviderError("公司素材上传地址必须是标准 HTTPS 地址。")
        hostname = urlsplit(upload_url).hostname
        if not hostname or hostname.casefold() not in allowed_hosts:
            raise AvatarProviderError("公司素材上传地址不在允许的供应商域名中。")
        raw, _ = self.file_upload_transport(
            upload_url, filename, mime_type, path, self.timeout_seconds
        )
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AvatarProviderError("公司素材上传接口返回了无效 JSON。") from exc
        try:
            success = isinstance(envelope, dict) and int(envelope.get("code", 0)) == 1
        except (TypeError, ValueError):
            success = False
        if not success:
            raise AvatarProviderError(
                str(envelope.get("msg") or "公司素材上传失败。")
                if isinstance(envelope, dict)
                else "公司素材上传响应格式不正确。"
            )
        result_url = self._upgrade_to_https(str(envelope.get("path") or "").strip())
        result_host = urlsplit(result_url).hostname
        if (
            not self._is_safe_https_url(result_url, allow_path=True)
            or not result_host
            or result_host.casefold() not in allowed_hosts
        ):
            raise AvatarProviderError("公司素材上传结果不在允许的供应商域名中。")
        return result_url

    def submit(self, request: AvatarSubmitRequest) -> AvatarJobSnapshot:
        self._ensure_configured()
        previous = self.find_job(request.idempotency_key)
        if previous is not None:
            return previous

        selected_voice = next(
            (item for item in self.list_assets() if item.asset_id == request.voice_id),
            None,
        )
        if selected_voice is not None and selected_voice.source_type == "custom_clone":
            audio_url = self._synthesize_cloned_voice(request)
        elif self.audio_mode == "edge_tts_upload":
            audio_url = self._synthesize_and_upload_audio(request)
        else:
            voice = self._form_request(
                "/voice",
                {
                    "text": request.script_text,
                    "voice_type": request.voice_id,
                    "member_id": 0,
                    "voice_type_value": "tts",
                    "language": "cn",
                    "speed_ratio": request.speech_rate,
                    "volume_ratio": 1,
                    "pitch_ratio": 1,
                },
                submit_operation=True,
            )
            voice_data = voice.get("data")
            audio_url = (
                str(voice_data.get("ossurl") or "").strip()
                if isinstance(voice_data, dict)
                else ""
            )
            if not audio_url:
                raise AvatarProviderError("公司数影网关未返回合成音频地址。")

        provider_video_name = (
            request.video_name or f"avatar-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )[:50]
        video = self._form_request(
            "/video",
            {
                "videoName": provider_video_name,
                "modeid": request.avatar_id,
                "audioUrl": audio_url,
                "aspect_ratio": request.aspect_ratio,
                "resolution": request.resolution,
                "background": request.background,
            },
            submit_operation=True,
        )
        video_data = video.get("data")
        provider_job_id = (
            str(video_data.get("videoId") or "").strip()
            if isinstance(video_data, dict)
            else ""
        )
        if not provider_job_id:
            raise AvatarProviderError("公司数影网关未返回视频任务编号。")

        snapshot = AvatarJobSnapshot(
            job_id=provider_job_id,
            idempotency_key=request.idempotency_key,
            status=AvatarProviderStatus.QUEUED,
            progress=5,
            stage="公司云端排队中",
            provider_job_id=provider_job_id,
            estimated_cost_cny=self.estimated_cost_cny,
            estimated_seconds=self.estimated_seconds,
        )
        self.idempotency_index[request.idempotency_key] = snapshot
        self.job_idempotency[provider_job_id] = request.idempotency_key
        return snapshot

    def _synthesize_cloned_voice(self, request: AvatarSubmitRequest) -> str:
        if not self._can_clone_voice():
            raise AvatarProviderError("声音克隆线路未配置独立凭证。")
        provider_id = request.voice_id.removeprefix("shuying-voice-")
        response = self._voice_form_request(
            "/voice_2",
            {
                "text": request.script_text,
                "clone_task_id": provider_id,
                "language": "cn",
                "speed_ratio": request.speech_rate,
            },
            submit_operation=True,
        )
        data = response.get("data")
        tts_task_id = str(data or "").strip()
        if not tts_task_id:
            raise AvatarProviderError("克隆声音合成未返回任务编号。")
        for _ in range(10):
            detail = self._voice_form_request(
                "/voice_tts_info",
                {"tts_task_id": tts_task_id},
                submit_operation=False,
                allow_pending=True,
            )
            result = detail.get("data")
            if isinstance(result, dict):
                audio_url = str(
                    result.get("ossurl") or result.get("url") or ""
                ).strip()
                if audio_url:
                    return audio_url
            # Voice TTS is an asynchronous query. Bound it rather than recursing.
            threading.Event().wait(2)
        raise AvatarProviderError("克隆声音合成等待超时，请稍后重试查询。")

    def _synthesize_and_upload_audio(self, request: AvatarSubmitRequest) -> str:
        audio = self.audio_renderer(
            request.script_text, request.speech_rate, self.edge_tts_voice
        )
        if not audio:
            raise AvatarProviderError("云端文字转语音未生成有效音频。")
        boundary = f"----CodexAvatarAudio{uuid.uuid4().hex}"
        body = self._multipart_file_body(
            field_name="file",
            filename="speech.mp3",
            mime_type="audio/mpeg",
            payload=audio,
            boundary=boundary,
        )
        raw, _ = self.transport(
            "POST",
            self.audio_upload_url,
            {
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            body,
            self.timeout_seconds,
        )
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AvatarProviderError("公司音频上传接口返回了无效 JSON。") from exc
        try:
            success = isinstance(envelope, dict) and int(envelope.get("code", 0)) == 1
        except (TypeError, ValueError):
            success = False
        if not success:
            message = (
                str(envelope.get("msg") or "公司音频上传失败。")
                if isinstance(envelope, dict)
                else "公司音频上传响应格式不正确。"
            )
            raise AvatarProviderError(message)
        audio_url = self._upgrade_to_https(str(envelope.get("path") or "").strip())
        if not self._is_safe_https_url(audio_url, allow_path=True):
            raise AvatarProviderError(
                "公司音频上传结果必须是标准 HTTPS 地址。",
                kind=ProviderErrorKind.VALIDATION,
            )
        hostname = urlsplit(audio_url).hostname
        if not hostname or hostname.casefold() not in self.audio_allowed_hosts:
            raise AvatarProviderError(
                "公司音频上传结果不在允许的供应商域名中。",
                kind=ProviderErrorKind.VALIDATION,
            )
        return audio_url

    @staticmethod
    def _render_edge_tts(text: str, speech_rate: float, voice: str) -> bytes:
        rate_percent = round((speech_rate - 1) * 100)
        with tempfile.TemporaryDirectory(prefix="shuying-cloud-tts-") as directory:
            output_path = Path(directory) / "speech.mp3"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "edge_tts",
                    "--voice",
                    voice,
                    f"--rate={rate_percent:+d}%",
                    "--text",
                    text,
                    "--write-media",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "未知错误").strip()[
                    :300
                ]
                raise AvatarProviderError(f"云端文字转语音失败：{detail}")
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise AvatarProviderError("云端文字转语音未生成音频文件。")
            return output_path.read_bytes()

    @staticmethod
    def _upgrade_to_https(value: str) -> str:
        try:
            parts = urlsplit(value)
        except ValueError:
            return value
        if (
            parts.scheme.casefold() == "http"
            and parts.hostname
            and parts.port in {None, 80}
            and not parts.username
            and not parts.password
        ):
            return parts._replace(scheme="https", netloc=parts.hostname).geturl()
        return value

    def get_job(self, job_id: str) -> AvatarJobSnapshot:
        self._ensure_configured()
        response = self._form_request(
            "/videoDetail", {"videoId": job_id}, submit_operation=False
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise AvatarProviderError("公司数影网关状态响应缺少 data。")
        try:
            provider_status = int(data.get("synthesisStatus", 0))
        except (TypeError, ValueError):
            provider_status = 0
        video_url = str(data.get("videoUrl") or "").strip()
        idempotency_key = self.job_idempotency.get(job_id, "")

        if provider_status == 3 and video_url:
            self.result_urls[job_id] = video_url
            return AvatarJobSnapshot(
                job_id=job_id,
                idempotency_key=idempotency_key,
                status=AvatarProviderStatus.SUCCEEDED,
                progress=100,
                stage="公司云端生成成功",
                provider_job_id=job_id,
                estimated_cost_cny=self.estimated_cost_cny,
                estimated_seconds=self.estimated_seconds,
                result_mime="video/mp4",
                result_size_bytes=self._optional_positive_int(data.get("videoSize")),
            )
        if provider_status in {-1, 8}:
            return AvatarJobSnapshot(
                job_id=job_id,
                idempotency_key=idempotency_key,
                status=AvatarProviderStatus.FAILED,
                progress=100,
                stage="公司云端生成失败",
                provider_job_id=job_id,
                estimated_cost_cny=self.estimated_cost_cny,
                estimated_seconds=self.estimated_seconds,
                error_kind=ProviderErrorKind.SERVICE,
                error_message=str(data.get("message") or "供应商生成失败。"),
            )
        return AvatarJobSnapshot(
            job_id=job_id,
            idempotency_key=idempotency_key,
            status=AvatarProviderStatus.RUNNING,
            progress=70 if provider_status == 2 else 30,
            stage="公司云端渲染中" if provider_status == 2 else "公司云端处理中",
            provider_job_id=job_id,
            estimated_cost_cny=self.estimated_cost_cny,
            estimated_seconds=self.estimated_seconds,
        )

    def find_job(self, idempotency_key: str) -> AvatarJobSnapshot | None:
        return self.idempotency_index.get(idempotency_key)

    def download_result(self, job_id: str) -> tuple[bytes, str]:
        url = self.result_urls.get(job_id)
        if not url:
            snapshot = self.get_job(job_id)
            if snapshot.status != AvatarProviderStatus.SUCCEEDED:
                raise AvatarProviderError("公司数影任务尚未成功，不能下载结果。")
            url = self.result_urls.get(job_id)
        if not url:
            raise AvatarProviderError("公司数影网关未返回视频下载地址。")

        if not self._is_safe_https_url(url, allow_path=True):
            raise AvatarProviderError(
                "公司数影结果地址必须是标准 HTTPS 地址。",
                kind=ProviderErrorKind.VALIDATION,
            )
        parts = urlsplit(url)
        if parts.hostname.casefold() not in self.result_allowed_hosts:
            raise AvatarProviderError(
                "公司数影结果地址不在允许的供应商域名中。",
                kind=ProviderErrorKind.VALIDATION,
            )
        payload, mime_type = self.download_transport(
            "GET",
            url,
            {"Accept": "video/mp4,application/octet-stream"},
            None,
            self.result_timeout_seconds,
        )
        if len(payload) > MAX_AVATAR_DOWNLOAD_BYTES:
            raise AvatarProviderError(
                "公司数影结果超过 100 MB 安全限制。",
                kind=ProviderErrorKind.VALIDATION,
            )
        return payload, mime_type

    def _ensure_configured(self) -> None:
        missing = self._missing_configuration()
        if missing:
            raise AvatarProviderError(
                "公司数影云数字人尚未配置：" + "、".join(missing),
                kind=ProviderErrorKind.AUTHORIZATION,
            )

    def _form_request(
        self,
        path: str,
        fields: dict[str, Any],
        *,
        submit_operation: bool,
    ) -> dict[str, Any]:
        boundary = f"----CodexAvatar{uuid.uuid4().hex}"
        body = self._multipart_form_body(
            {**fields, "api_code": self.api_code}, boundary=boundary
        )
        try:
            raw, _ = self.transport(
                "POST",
                f"{self.base_url}{path}",
                {
                    "Accept": "application/json",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                body,
                self.timeout_seconds,
            )
        except AvatarProviderError as exc:
            if exc.outcome_unknown and not submit_operation:
                raise AvatarProviderError(
                    str(exc), kind=exc.kind, outcome_unknown=False
                ) from exc
            raise
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AvatarProviderError("公司数影网关返回了无效 JSON。") from exc
        if not isinstance(envelope, dict):
            raise AvatarProviderError("公司数影网关响应格式不正确。")
        response_data = envelope.get("data")
        try:
            response_code = int(envelope.get("code", 0))
        except (TypeError, ValueError):
            response_code = None
        structural_success = bool(
            isinstance(response_data, dict)
            and (
                (path == "/video" and str(response_data.get("videoId") or "").strip())
                or (path == "/videoDetail" and "synthesisStatus" in response_data)
            )
        )
        success = response_code == 1 or structural_success
        if not success:
            message = next(
                (
                    str(envelope.get(key)).strip()
                    for key in ("msg", "message", "error", "error_message")
                    if isinstance(envelope.get(key), (str, int, float))
                    and str(envelope.get(key)).strip()
                ),
                "",
            )
            if not message:
                code_label = "未知" if response_code is None else str(response_code)
                message = f"公司数影网关拒绝了制作请求，但没有说明原因（返回码 {code_label}）。"
            kind = (
                ProviderErrorKind.AUTHORIZATION
                if any(
                    token in message.casefold()
                    for token in ("key", "code", "鉴权", "授权")
                )
                else ProviderErrorKind.SERVICE
            )
            raise AvatarProviderError(message, kind=kind)
        return envelope

    def _voice_form_request(
        self,
        path: str,
        fields: dict[str, Any],
        *,
        submit_operation: bool,
        zero_code_success: bool = False,
        allow_pending: bool = False,
    ) -> dict[str, Any]:
        """Primary voice route has a legacy success code that differs for cloning."""
        boundary = f"----CodexAvatarVoice{uuid.uuid4().hex}"
        body = self._multipart_form_body(
            {**fields, "api_code": self.voice_api_code}, boundary=boundary
        )
        try:
            raw, _ = self.transport(
                "POST",
                f"{self.voice_base_url}{path}",
                {
                    "Accept": "application/json",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                body,
                self.timeout_seconds,
            )
        except AvatarProviderError as exc:
            if exc.outcome_unknown and not submit_operation:
                raise AvatarProviderError(
                    str(exc), kind=exc.kind, outcome_unknown=False
                ) from exc
            raise
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AvatarProviderError("公司声音线路返回了无效 JSON。") from exc
        if not isinstance(envelope, dict):
            raise AvatarProviderError("公司声音线路响应格式不正确。")
        try:
            code = int(envelope.get("code"))
        except (TypeError, ValueError):
            code = -999
        success = code == 0 if zero_code_success else code == 1
        if allow_pending and code != -1:
            success = True
        if not success:
            raise AvatarProviderError(
                str(envelope.get("msg") or "公司声音线路请求失败。"),
                kind=(
                    ProviderErrorKind.AUTHORIZATION
                    if any(
                        token in str(envelope.get("msg") or "").casefold()
                        for token in ("key", "code", "鉴权", "授权")
                    )
                    else ProviderErrorKind.SERVICE
                ),
            )
        return envelope

    @staticmethod
    def _multipart_form_body(fields: dict[str, Any], *, boundary: str) -> bytes:
        chunks: list[bytes] = []
        for name, value in fields.items():
            safe_name = str(name).replace('"', "")
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("ascii"),
                    (
                        f'Content-Disposition: form-data; name="{safe_name}"\r\n\r\n'
                    ).encode("ascii"),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        chunks.append(f"--{boundary}--\r\n".encode("ascii"))
        return b"".join(chunks)

    @staticmethod
    def _multipart_file_body(
        *,
        field_name: str,
        filename: str,
        mime_type: str,
        payload: bytes,
        boundary: str,
    ) -> bytes:
        safe_field = field_name.replace('"', "")
        safe_filename = filename.replace('"', "")
        return b"".join(
            [
                f"--{boundary}\r\n".encode("ascii"),
                (
                    f'Content-Disposition: form-data; name="{safe_field}"; '
                    f'filename="{safe_filename}"\r\n'
                ).encode("ascii"),
                f"Content-Type: {mime_type}\r\n\r\n".encode("ascii"),
                payload,
                b"\r\n",
                f"--{boundary}--\r\n".encode("ascii"),
            ]
        )

    @staticmethod
    def _optional_positive_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _is_safe_https_url(value: str, *, allow_path: bool = True) -> bool:
        try:
            parts = urlsplit(value)
            port = parts.port
        except ValueError:
            return False
        if (
            parts.scheme.casefold() != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or port not in {None, 443}
            or parts.query
            or parts.fragment
        ):
            return False
        return allow_path or parts.path in {"", "/"}

    @staticmethod
    def _is_safe_upload_url(value: str) -> bool:
        """The recovered video uploader needs only its explicit is_video flag."""
        try:
            parts = urlsplit(value)
            port = parts.port
        except ValueError:
            return False
        return (
            parts.scheme.casefold() == "https"
            and bool(parts.hostname)
            and not parts.username
            and not parts.password
            and port in {None, 443}
            and not parts.fragment
            and parts.query in {"", "is_video=1"}
        )


def build_avatar_provider():
    mode = os.getenv("AVATAR_PROVIDER_MODE", "sandbox").lower()
    if mode in {"local", "local_models", "musetalk_sadtalker"}:
        return LocalCommandAvatarProvider.from_env()
    if mode in {"baidu", "baidu_xiling", "xiling"}:
        return BaiduXilingAvatarProvider.from_env()
    if mode in {"shuying", "shuying_cloud", "shuying_legacy"}:
        return ShuyingLegacyAvatarProvider.from_env()
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
    return status_map.get(
        value.upper(), default_status or AvatarProviderStatus.OUTCOME_UNKNOWN
    )


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
