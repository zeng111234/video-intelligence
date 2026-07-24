from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import threading
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    return [item.strip() for item in value.replace("\n", ";").split(";") if item.strip()]


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
            permission_status="authorized" if enabled_profiles else "configuration_missing",
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
            raise AvatarProviderError("所选本地授权形象或声音不存在。", kind=ProviderErrorKind.VALIDATION)
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
        if snapshot is not None and snapshot.status != AvatarProviderStatus.OUTCOME_UNKNOWN:
            return snapshot

        recovered, result_path = self._recover_job(job_id)
        with self._lock:
            current = self.jobs.get(job_id)
            if current is None or current.status == AvatarProviderStatus.OUTCOME_UNKNOWN:
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
            raise AvatarProviderError("本地成片尚未生成。", kind=ProviderErrorKind.VALIDATION)
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
                assets.append(
                    AvatarAsset(
                        asset_id=str(item["asset_id"]),
                        kind=AvatarAssetKind(raw_kind),
                        name=str(item.get("name") or item["asset_id"]),
                        preview_url=(
                            str(item.get("preview_url"))
                            if item.get("preview_url")
                            else None
                        ),
                        authorized=True,
                    )
                )
            except (KeyError, ValueError):
                continue
        return assets

    def _asset_path(self, asset_id: str) -> Path:
        if self.assets_manifest is None:
            raise AvatarProviderError("本地资产清单不存在。", kind=ProviderErrorKind.VALIDATION)
        payload = json.loads(self.assets_manifest.read_text(encoding="utf-8"))
        items = payload.get("assets", payload) if isinstance(payload, dict) else payload
        for item in items:
            if isinstance(item, dict) and item.get("asset_id") == asset_id:
                path = self._manifest_relative_path(str(item.get("path", "")))
                if path.is_file():
                    return path.resolve()
        raise AvatarProviderError("本地授权素材文件不存在。", kind=ProviderErrorKind.VALIDATION)

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
                    self._update(job_id, AvatarProviderStatus.RUNNING, 20, "使用本人录音驱动中")
                    self._prepare_recorded_audio(voice_path, audio_path)
                else:
                    self._update(job_id, AvatarProviderStatus.RUNNING, 20, "本地文字转语音生成中")
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
                self._update(job_id, AvatarProviderStatus.RUNNING, 60, "本地数字人视频生成中")
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
                self._update(job_id, AvatarProviderStatus.SUCCEEDED, 100, "本地成片已生成")
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
            raise AvatarProviderError("上传录音不是 WAV，且未找到 FFmpeg，无法转换音频。")
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
            command = [part.format(**values) for part in shlex.split(template, posix=os.name != "nt")]
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

    def _update(self, job_id: str, status: AvatarProviderStatus, progress: int, stage: str) -> None:
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
    mode = os.getenv("AVATAR_PROVIDER_MODE", "sandbox").lower()
    if mode in {"local", "local_models", "musetalk_sadtalker"}:
        return LocalCommandAvatarProvider.from_env()
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
