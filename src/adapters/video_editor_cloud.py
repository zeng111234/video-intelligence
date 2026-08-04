"""Sandbox and Alibaba Cloud adapters for the lightweight video editor.

The Alibaba adapters implement documented HTTP/RPC request construction and
submit/query boundaries.  They do not silently fall back to sandbox behavior.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from src.services.video_editor_cloud import (
    BGM_ENERGY_LEVELS,
    BGM_VOICEOVER_CATEGORIES,
    CAPTION_EMPHASIS_KINDS,
    CloudASRProvider,
    CloudAsset,
    CloudEditorConfiguration,
    CloudObjectStore,
    CloudProviderMode,
    CloudRenderProvider,
    CloudTranscript,
    EditPlan,
    EditPlanProvider,
    EditStepKind,
    OutputProfile,
    ProviderJobSnapshot,
    ProviderJobStatus,
    RenderRequest,
    TimeRange,
    TranscriptSegment,
    build_safe_edit_plan,
    build_smart_opening,
    kept_ranges_for_plan,
    validated_caption_emphasis,
    validated_caption_groups,
    visual_style_spec,
)


JsonTransport = Callable[
    [str, str, dict[str, str], bytes | None, float],
    dict[str, Any],
]
UploadTransport = Callable[
    [str, dict[str, str], Path, float],
    Mapping[str, str] | None,
]

_DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_OSS_PUT_BYTES = 5 * 1024 * 1024 * 1024
_OSS_READ_URL_TTL_SECONDS = 6 * 60 * 60


class CloudProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "service",
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.outcome_unknown = outcome_unknown


def _default_json_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.request(method, url, headers=headers, content=body)
            response.raise_for_status()
    except httpx.TransportError as exc:
        raise CloudProviderError("云服务连接失败。", kind="connection") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        kind = (
            "authorization"
            if status in {401, 403}
            else "rate_limit"
            if status == 429
            else "validation"
            if 400 <= status < 500
            else "service"
        )
        detail = exc.response.text[:300].strip()
        raise CloudProviderError(
            f"云服务 HTTP {status}：{detail or '请求失败'}",
            kind=kind,
        ) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise CloudProviderError("云服务返回了无效 JSON。") from exc
    if not isinstance(payload, dict):
        raise CloudProviderError("云服务响应格式不正确。")
    return payload


def _default_upload_transport(
    url: str,
    headers: dict[str, str],
    path: Path,
    timeout: float,
) -> Mapping[str, str] | None:
    try:
        with (
            path.open("rb") as source,
            httpx.Client(
                timeout=timeout,
                follow_redirects=False,
            ) as client,
        ):
            response = client.put(url, headers=headers, content=source)
            response.raise_for_status()
            return dict(response.headers)
    except httpx.TransportError as exc:
        raise CloudProviderError("OSS 上传连接失败。", kind="connection") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        kind = "authorization" if status in {401, 403} else "service"
        raise CloudProviderError(
            f"OSS 上传 HTTP {status}：{exc.response.text[:300] or '请求失败'}",
            kind=kind,
        ) from exc


def _require_configuration(values: Mapping[str, str]) -> None:
    missing = [name for name, value in values.items() if not value.strip()]
    if missing:
        raise CloudProviderError(
            "云端智能剪辑尚未配置：" + "、".join(missing),
            kind="configuration",
        )


def _submit_once(
    transport: JsonTransport,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> dict[str, Any]:
    try:
        return transport(method, url, headers, body, timeout)
    except CloudProviderError as exc:
        if exc.kind == "connection":
            raise CloudProviderError(
                str(exc),
                kind=exc.kind,
                outcome_unknown=True,
            ) from exc
        raise
    except (ConnectionError, TimeoutError, OSError) as exc:
        raise CloudProviderError(
            "付费任务提交连接失败，结果未确认，请勿直接重复提交。",
            kind="connection",
            outcome_unknown=True,
        ) from exc


def _query_with_single_retry(
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    last_error: BaseException | None = None
    for _ in range(2):
        try:
            return operation()
        except CloudProviderError as exc:
            if exc.kind != "connection":
                raise
            last_error = exc
        except (ConnectionError, TimeoutError, OSError) as exc:
            last_error = exc
    raise CloudProviderError(
        "任务状态查询连接失败，已自动重试一次；当前结果未知。",
        kind="connection",
        outcome_unknown=True,
    ) from last_error


def _status_from_async(value: object) -> ProviderJobStatus:
    normalized = str(value or "").strip().casefold()
    if normalized in {"pending", "submitted", "queued"}:
        return ProviderJobStatus.PENDING
    if normalized in {"running", "transcoding", "processing"}:
        return ProviderJobStatus.RUNNING
    if normalized in {"succeeded", "success", "transcodesuccess"}:
        return ProviderJobStatus.SUCCEEDED
    if normalized in {
        "failed",
        "fail",
        "transcodefail",
        "cancelled",
        "transcodecancelled",
    }:
        return ProviderJobStatus.FAILED
    return ProviderJobStatus.RUNNING


class SandboxCloudObjectStore(CloudObjectStore):
    def upload(
        self,
        path: str | Path,
        object_key: str,
        *,
        media_type: str,
    ) -> CloudAsset:
        source = Path(path)
        size = source.stat().st_size if source.is_file() else 0
        return CloudAsset(
            provider_name="sandbox_object_store",
            object_key=object_key,
            uri=f"sandbox://objects/{quote(object_key, safe='/')}",
            media_type=media_type,
            size_bytes=size,
            is_mock=True,
        )


class SandboxCloudASRProvider(CloudASRProvider):
    def submit(
        self,
        asset: CloudAsset,
        *,
        language_hints: Sequence[str] = ("zh",),
    ) -> ProviderJobSnapshot:
        return ProviderJobSnapshot(
            provider_name="sandbox_fun_asr",
            provider_job_id=f"sandbox-asr-{uuid.uuid4().hex}",
            provider_stage="sandbox_transcription_complete",
            status=ProviderJobStatus.SUCCEEDED,
            is_mock=True,
            detail={
                "message": "演示状态：未调用真实语音识别服务。",
                "language_hints": list(language_hints),
            },
        )

    def query(self, provider_job_id: str) -> ProviderJobSnapshot:
        return ProviderJobSnapshot(
            provider_name="sandbox_fun_asr",
            provider_job_id=provider_job_id,
            provider_stage="sandbox_transcription_complete",
            status=ProviderJobStatus.SUCCEEDED,
            is_mock=True,
            detail={"message": "演示状态：未调用真实语音识别服务。"},
        )

    def fetch_result(self, snapshot: ProviderJobSnapshot) -> CloudTranscript:
        return CloudTranscript(
            provider_name="sandbox_fun_asr",
            transcript="【演示】未调用真实语音识别服务。",
            segments=[
                TranscriptSegment(
                    start=0,
                    end=2,
                    text="【演示】未调用真实语音识别服务。",
                ),
            ],
            spoken_ranges=[TimeRange(start=0, end=2)],
            duration_seconds=2,
            language="zh",
            is_mock=True,
        )


class SandboxEditPlanProvider(EditPlanProvider):
    def create_plan(
        self,
        transcript: str,
        spoken_ranges: Sequence[TimeRange | Mapping[str, float]],
        duration_seconds: float,
        segments: Sequence[Mapping[str, Any]] | None = None,
    ) -> EditPlan:
        normalized = " ".join(transcript.split())
        title = normalized[:28].strip("，。！？、 ") if normalized else ""
        plan = build_safe_edit_plan(
            spoken_ranges,
            duration_seconds,
            title_candidates=[title] if title else [],
            smart_opening=build_smart_opening(
                transcript,
                [title] if title else [],
            ),
            explanation="演示方案仅运行确定性安全规则，未调用 qwen-flash。",
            provider_name="sandbox_edit_plan",
            is_mock=True,
        )
        return plan


class SandboxCloudRenderProvider(CloudRenderProvider):
    def submit(self, request: RenderRequest) -> ProviderJobSnapshot:
        if not request.review_confirmed:
            raise CloudProviderError(
                "字幕和剪辑方案尚未人工确认，不能提交渲染。",
                kind="validation",
            )
        return ProviderJobSnapshot(
            provider_name="sandbox_mps",
            provider_job_id=f"sandbox-render-{uuid.uuid4().hex}",
            provider_stage="sandbox_render_complete",
            status=ProviderJobStatus.SUCCEEDED,
            is_mock=True,
            output_uri=None,
            can_publish=False,
            detail={"message": "演示状态：未生成真实媒体，不能交接发布。"},
        )

    def query(self, provider_job_id: str) -> ProviderJobSnapshot:
        return ProviderJobSnapshot(
            provider_name="sandbox_mps",
            provider_job_id=provider_job_id,
            provider_stage="sandbox_render_complete",
            status=ProviderJobStatus.SUCCEEDED,
            is_mock=True,
            output_uri=None,
            can_publish=False,
            detail={"message": "演示状态：未生成真实媒体，不能交接发布。"},
        )


class AliyunCloudObjectStore(CloudObjectStore):
    """OSS PutObject skeleton using the documented V1 header signature."""

    def __init__(
        self,
        config: CloudEditorConfiguration,
        *,
        transport: UploadTransport | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.config = config
        self.transport = transport or _default_upload_transport
        self.timeout_seconds = timeout_seconds

    def _ensure_configured(self) -> None:
        _require_configuration(
            {
                "ALIYUN_OSS_BUCKET": self.config.oss_bucket,
                "ALIBABA_CLOUD_ACCESS_KEY_ID": self.config.access_key_id,
                "ALIBABA_CLOUD_ACCESS_KEY_SECRET": self.config.access_key_secret,
            },
        )

    @staticmethod
    def _content_md5(path: Path) -> str:
        digest = hashlib.md5()  # noqa: S324 - required by the OSS Content-MD5 contract
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return base64.b64encode(digest.digest()).decode("ascii")

    def build_upload_request(
        self,
        path: str | Path,
        object_key: str,
        *,
        media_type: str,
        now: datetime | None = None,
    ) -> tuple[str, dict[str, str]]:
        self._ensure_configured()
        source = Path(path)
        if not source.is_file():
            raise CloudProviderError("待上传媒体不存在。", kind="validation")
        if not object_key.strip():
            raise CloudProviderError("OSS Object Key 不能为空。", kind="validation")
        if source.stat().st_size > _MAX_OSS_PUT_BYTES:
            raise CloudProviderError(
                "单次 PutObject 不支持超过 5 GB 的文件。",
                kind="validation",
            )

        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        date_header = format_datetime(checked_at.astimezone(timezone.utc), usegmt=True)
        content_type = media_type or "application/octet-stream"
        content_md5 = self._content_md5(source)
        canonical_oss_headers = "x-oss-forbid-overwrite:true\n"
        canonical_resource = f"/{self.config.oss_bucket}/{object_key}"
        string_to_sign = (
            f"PUT\n{content_md5}\n{content_type}\n{date_header}\n"
            f"{canonical_oss_headers}{canonical_resource}"
        )
        signature = base64.b64encode(
            hmac.new(
                self.config.access_key_secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest(),
        ).decode("ascii")
        encoded_key = quote(object_key, safe="/")
        url = (
            f"https://{self.config.oss_bucket}.{self.config.oss_location}"
            f".aliyuncs.com/{encoded_key}"
        )
        headers = {
            "Authorization": f"OSS {self.config.access_key_id}:{signature}",
            "Content-Length": str(source.stat().st_size),
            "Content-MD5": content_md5,
            "Content-Type": content_type,
            "Date": date_header,
            "x-oss-forbid-overwrite": "true",
        }
        return url, headers

    def upload(
        self,
        path: str | Path,
        object_key: str,
        *,
        media_type: str,
    ) -> CloudAsset:
        source = Path(path)
        url, headers = self.build_upload_request(
            source,
            object_key,
            media_type=media_type,
        )
        connection_error: Exception | None = None
        for attempt in range(2):
            try:
                self.transport(url, headers, source, self.timeout_seconds)
                connection_error = None
                break
            except CloudProviderError as exc:
                if exc.kind != "connection":
                    raise
                connection_error = exc
            except (ConnectionError, TimeoutError, OSError) as exc:
                connection_error = exc
            if attempt == 0:
                continue
        if connection_error is not None:
            raise CloudProviderError(
                "OSS 上传连接失败，结果未确认，请勿直接重复提交。",
                kind="connection",
                outcome_unknown=True,
            ) from connection_error
        read_url = self.presign_get_url(object_key)
        return CloudAsset(
            provider_name="aliyun_oss",
            bucket=self.config.oss_bucket,
            object_key=object_key,
            uri=f"oss://{self.config.oss_bucket}/{quote(object_key, safe='/')}",
            media_type=media_type,
            size_bytes=source.stat().st_size,
            is_mock=False,
            provider_locator=read_url,
        )

    def presign_get_url(
        self,
        object_key: str,
        *,
        expires_seconds: int = _OSS_READ_URL_TTL_SECONDS,
        now: datetime | None = None,
    ) -> str:
        """Create a short-lived V1 GET URL for server-to-server ASR access."""
        self._ensure_configured()
        if not object_key.strip():
            raise CloudProviderError("OSS Object Key 不能为空。", kind="validation")
        if not 1 <= expires_seconds <= 32_400:
            raise CloudProviderError(
                "OSS 临时读取地址有效期必须在 1 到 32400 秒之间。",
                kind="validation",
            )
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        expires = int(checked_at.timestamp()) + expires_seconds
        canonical_resource = f"/{self.config.oss_bucket}/{object_key}"
        string_to_sign = f"GET\n\n\n{expires}\n{canonical_resource}"
        signature = base64.b64encode(
            hmac.new(
                self.config.access_key_secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest(),
        ).decode("ascii")
        encoded_key = quote(object_key, safe="/")
        query = urlencode(
            {
                "OSSAccessKeyId": self.config.access_key_id,
                "Expires": str(expires),
                "Signature": signature,
            },
        )
        return (
            f"https://{self.config.oss_bucket}.{self.config.oss_location}"
            f".aliyuncs.com/{encoded_key}?{query}"
        )


class AliyunFunASRProvider(CloudASRProvider):
    """Fun-ASR asynchronous HTTP submit/query adapter."""

    def __init__(
        self,
        config: CloudEditorConfiguration,
        *,
        transport: JsonTransport | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.config = config
        self.transport = transport or _default_json_transport
        self.timeout_seconds = timeout_seconds

    @property
    def _base_url(self) -> str:
        return f"https://{self.config.workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1"

    def _ensure_configured(self) -> None:
        _require_configuration(
            {
                "ALIYUN_MODEL_STUDIO_WORKSPACE_ID": self.config.workspace_id,
                "DASHSCOPE_API_KEY": self.config.dashscope_api_key,
            },
        )

    def build_submit_request(
        self,
        asset: CloudAsset,
        *,
        language_hints: Sequence[str] = ("zh",),
    ) -> tuple[str, dict[str, str], bytes]:
        self._ensure_configured()
        url = f"{self._base_url}/services/audio/asr/transcription"
        headers = {
            "Authorization": f"Bearer {self.config.dashscope_api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        payload = {
            "model": "fun-asr",
            "input": {"file_urls": [asset.provider_locator or asset.uri]},
            "parameters": {
                "channel_id": [0],
                "language_hints": list(language_hints),
            },
        }
        return url, headers, json.dumps(payload, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _snapshot(payload: Mapping[str, Any]) -> ProviderJobSnapshot:
        output = payload.get("output")
        if not isinstance(output, Mapping):
            raise CloudProviderError("Fun-ASR 响应缺少 output。")
        job_id = str(output.get("task_id") or "").strip()
        if not job_id:
            raise CloudProviderError("Fun-ASR 响应缺少 task_id。")
        status = _status_from_async(output.get("task_status"))
        usage = payload.get("usage")
        results = output.get("results")
        result_entries = results if isinstance(results, list) else []
        successful_result = next(
            (
                result
                for result in result_entries
                if isinstance(result, Mapping)
                and str(result.get("subtask_status") or "").casefold() == "succeeded"
            ),
            None,
        )
        result_locator = (
            str(successful_result.get("transcription_url") or "").strip()
            if isinstance(successful_result, Mapping)
            else None
        )
        return ProviderJobSnapshot(
            provider_name="aliyun_fun_asr",
            provider_job_id=job_id,
            provider_stage=(
                "transcription_complete"
                if status == ProviderJobStatus.SUCCEEDED
                else "transcription_failed"
                if status == ProviderJobStatus.FAILED
                else "transcribing"
            ),
            status=status,
            is_mock=False,
            usage=dict(usage) if isinstance(usage, Mapping) else {},
            detail={
                "task_metrics": output.get("task_metrics", {}),
                "subtasks": [
                    {
                        "status": item.get("subtask_status"),
                        "code": item.get("code"),
                        "message": item.get("message"),
                    }
                    for item in result_entries
                    if isinstance(item, Mapping)
                ],
            },
            result_locator=result_locator or None,
        )

    def submit(
        self,
        asset: CloudAsset,
        *,
        language_hints: Sequence[str] = ("zh",),
    ) -> ProviderJobSnapshot:
        url, headers, body = self.build_submit_request(
            asset,
            language_hints=language_hints,
        )
        payload = _submit_once(
            self.transport,
            "POST",
            url,
            headers,
            body,
            self.timeout_seconds,
        )
        return self._snapshot(payload)

    def query(self, provider_job_id: str) -> ProviderJobSnapshot:
        self._ensure_configured()
        if not provider_job_id.strip():
            raise CloudProviderError("Fun-ASR task_id 不能为空。", kind="validation")
        url = f"{self._base_url}/tasks/{quote(provider_job_id, safe='')}"
        headers = {"Authorization": f"Bearer {self.config.dashscope_api_key}"}
        payload = _query_with_single_retry(
            lambda: self.transport(
                "GET",
                url,
                headers,
                None,
                self.timeout_seconds,
            ),
        )
        return self._snapshot(payload)

    @staticmethod
    def _validate_result_url(value: str) -> str:
        parts = urlsplit(value)
        hostname = (parts.hostname or "").casefold()
        if (
            parts.scheme != "https"
            or not hostname
            or not (hostname == "aliyuncs.com" or hostname.endswith(".aliyuncs.com"))
        ):
            raise CloudProviderError(
                "Fun-ASR 结果地址不是受信任的阿里云 HTTPS 地址。",
                kind="validation",
            )
        return value

    @staticmethod
    def _transcript(
        payload: Mapping[str, Any], usage: Mapping[str, Any]
    ) -> CloudTranscript:
        properties = payload.get("properties")
        duration_ms = (
            properties.get("original_duration_in_milliseconds", 0)
            if isinstance(properties, Mapping)
            else 0
        )
        transcripts = payload.get("transcripts")
        entries = transcripts if isinstance(transcripts, list) else []
        text_parts: list[str] = []
        segments: list[TranscriptSegment] = []
        spoken_ranges: list[TimeRange] = []
        for transcript in entries:
            if not isinstance(transcript, Mapping):
                continue
            paragraph = str(transcript.get("text") or "").strip()
            if paragraph:
                text_parts.append(paragraph)
            sentences = transcript.get("sentences")
            if not isinstance(sentences, list):
                continue
            for sentence in sentences:
                if not isinstance(sentence, Mapping):
                    continue
                try:
                    start = float(sentence.get("begin_time", 0)) / 1000
                    end = float(sentence.get("end_time", 0)) / 1000
                except (TypeError, ValueError):
                    continue
                text = str(sentence.get("text") or "").strip()
                if end <= start or not text:
                    continue
                speaker_id = sentence.get("speaker_id")
                words = sentence.get("words")
                word_ranges: list[TimeRange] = []
                word_confidences: list[float] = []
                if isinstance(words, list):
                    for word in words:
                        if not isinstance(word, Mapping):
                            continue
                        try:
                            word_start = float(word.get("begin_time", 0)) / 1000
                            word_end = float(word.get("end_time", 0)) / 1000
                        except (TypeError, ValueError):
                            continue
                        if word_end > word_start:
                            word_ranges.append(
                                TimeRange(start=word_start, end=word_end),
                            )
                        try:
                            word_confidence = float(word.get("confidence"))
                        except (TypeError, ValueError):
                            continue
                        if 0 <= word_confidence <= 1:
                            word_confidences.append(word_confidence)
                try:
                    sentence_confidence = float(sentence.get("confidence"))
                except (TypeError, ValueError):
                    sentence_confidence = None
                if (
                    sentence_confidence is not None
                    and not 0 <= sentence_confidence <= 1
                ):
                    sentence_confidence = None
                if sentence_confidence is None and word_confidences:
                    sentence_confidence = sum(word_confidences) / len(word_confidences)
                segments.append(
                    TranscriptSegment(
                        start=start,
                        end=end,
                        text=text,
                        speaker_id=(
                            int(speaker_id)
                            if isinstance(speaker_id, (int, str))
                            and str(speaker_id).lstrip("-").isdigit()
                            else None
                        ),
                        confidence=sentence_confidence,
                    ),
                )
                spoken_ranges.extend(
                    word_ranges or [TimeRange(start=start, end=end)],
                )
        segments.sort(key=lambda item: (item.start, item.end))
        spoken_ranges.sort(key=lambda item: (item.start, item.end))
        try:
            duration_seconds = max(0.0, float(duration_ms) / 1000)
        except (TypeError, ValueError):
            duration_seconds = 0.0
        if not duration_seconds and segments:
            duration_seconds = max(item.end for item in segments)
        return CloudTranscript(
            provider_name="aliyun_fun_asr",
            transcript="\n".join(text_parts),
            segments=segments,
            spoken_ranges=spoken_ranges,
            duration_seconds=duration_seconds,
            is_mock=False,
            usage=dict(usage),
        )

    def fetch_result(self, snapshot: ProviderJobSnapshot) -> CloudTranscript:
        if snapshot.provider_name != "aliyun_fun_asr":
            raise CloudProviderError(
                "转写结果不属于当前 Fun-ASR 供应商。",
                kind="validation",
            )
        current = snapshot
        if not current.result_locator and current.provider_job_id:
            # result_locator is deliberately excluded from persistence because
            # it is a short-lived signed URL. Re-query by the durable task ID.
            current = self.query(current.provider_job_id)
        if current.status != ProviderJobStatus.SUCCEEDED:
            raise CloudProviderError(
                "Fun-ASR 任务尚未成功，不能读取转写结果。",
                kind="validation",
            )
        if not current.result_locator:
            raise CloudProviderError(
                "Fun-ASR 成功响应缺少 transcription_url。",
            )
        url = self._validate_result_url(current.result_locator)
        payload = _query_with_single_retry(
            lambda: self.transport(
                "GET",
                url,
                {"Accept": "application/json"},
                None,
                self.timeout_seconds,
            ),
        )
        return self._transcript(payload, current.usage)


class AliyunEditPlanProvider(EditPlanProvider):
    """qwen-flash supplies copy hints; deterministic rules own all cut ranges."""

    def __init__(
        self,
        config: CloudEditorConfiguration,
        *,
        transport: JsonTransport | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.config = config
        self.transport = transport or _default_json_transport
        self.timeout_seconds = timeout_seconds

    @property
    def _url(self) -> str:
        return (
            f"https://{self.config.workspace_id}.cn-beijing.maas.aliyuncs.com"
            "/compatible-mode/v1/chat/completions"
        )

    def _ensure_configured(self) -> None:
        _require_configuration(
            {
                "ALIYUN_MODEL_STUDIO_WORKSPACE_ID": self.config.workspace_id,
                "DASHSCOPE_API_KEY": self.config.dashscope_api_key,
            },
        )

    def build_request(
        self,
        transcript: str,
        spoken_ranges: Sequence[TimeRange | Mapping[str, float]],
        duration_seconds: float,
        segments: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[str, dict[str, str], bytes]:
        self._ensure_configured()
        normalized_ranges = [
            item.model_dump() if isinstance(item, TimeRange) else dict(item)
            for item in spoken_ranges
        ]
        system = (
            "你是安全轻剪规划器。只返回 JSON，字段仅允许 "
            "title_candidates、explanation、enabled_steps、bgm_category、"
            "bgm_energy、bgm_keywords、caption_groups、caption_emphasis、"
            "opening_style_id。"
            "enabled_steps 只能取 trim_silence、vertical_fit、subtitles、"
            "title、bgm、audio_mix、smart_opening。不得建议删除、改写或重排"
            "有人声内容。opening_style_id 只能取 suspense_reveal、"
            "story_unfold、number_focus；数字金额优先 number_focus，故事叙事"
            "优先 story_unfold，其余悬念钩子使用 suspense_reveal。"
            f"bgm_category 只能取 {'、'.join(BGM_VOICEOVER_CATEGORIES)}；"
            f"bgm_energy 只能取 {'、'.join(BGM_ENERGY_LEVELS)}；"
            "bgm_keywords 最多 6 个短标签。根据整段文案的主题、情绪和语速选择，"
            "口播配乐应克制、无人声、不抢对白。"
            "caption_groups 必须覆盖 subtitle_segments 中每个非空 segment_index "
            "且每个只出现一次，格式为 "
            '[{"segment_index":0,"parts":["第一段","第二段"]}]。'
            "parts 只决定显示断点：去掉标点和空白后拼接，必须与对应 text "
            "逐字一致，不得增删、改写、调序。每段最多 11 个显示字符；"
            "按完整语义短语分组，不拆数字、英文、专有名词或双字词，"
            "不要让助词、介词、量词或单字悬空。"
            "caption_emphasis 用于克制的口播关键词强调，格式为 "
            '[{"segment_index":0,"term":"49元","kind":"number"}]。'
            "term 必须是对应字幕中的连续原文，最多 6 个字，每个字幕片段最多一个；"
            "每 3 个字幕片段最多选择一个，优先具体数字、金额、比例、核心利益点、"
            "风险警示或结论，不要选择虚词和普通动词。kind 只能取 "
            f"{'、'.join(CAPTION_EMPHASIS_KINDS)}。"
        )
        subtitle_segments = [
            {
                "segment_index": index,
                "text": str(segment.get("text") or ""),
            }
            for index, segment in enumerate(segments or [])
            if str(segment.get("text") or "").strip()
        ]
        user = json.dumps(
            {
                "transcript": transcript,
                "spoken_ranges": normalized_ranges,
                "duration_seconds": duration_seconds,
                "subtitle_segments": subtitle_segments,
            },
            ensure_ascii=False,
        )
        payload = {
            "model": "qwen-flash",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
        }
        headers = {
            "Authorization": f"Bearer {self.config.dashscope_api_key}",
            "Content-Type": "application/json",
        }
        return (
            self._url,
            headers,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    @staticmethod
    def _content(payload: Mapping[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise CloudProviderError("qwen-flash 响应缺少 choices。")
        first = choices[0]
        message = first.get("message") if isinstance(first, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str) or not content.strip():
            raise CloudProviderError("qwen-flash 未返回剪辑方案。")
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```json").removeprefix("```")
            cleaned = cleaned.removesuffix("```").strip()
        return cleaned

    def create_plan(
        self,
        transcript: str,
        spoken_ranges: Sequence[TimeRange | Mapping[str, float]],
        duration_seconds: float,
        segments: Sequence[Mapping[str, Any]] | None = None,
    ) -> EditPlan:
        url, headers, body = self.build_request(
            transcript,
            spoken_ranges,
            duration_seconds,
            segments,
        )
        payload = _submit_once(
            self.transport,
            "POST",
            url,
            headers,
            body,
            self.timeout_seconds,
        )
        try:
            suggestion = json.loads(self._content(payload))
        except json.JSONDecodeError as exc:
            raise CloudProviderError("qwen-flash 返回的方案不是有效 JSON。") from exc
        if not isinstance(suggestion, Mapping):
            raise CloudProviderError("qwen-flash 返回的方案格式不正确。")

        steps: list[EditStepKind] = []
        raw_steps = suggestion.get("enabled_steps", [])
        if isinstance(raw_steps, list):
            for raw_step in raw_steps:
                try:
                    step = EditStepKind(str(raw_step))
                except ValueError:
                    continue
                if step not in steps:
                    steps.append(step)
        raw_titles = suggestion.get("title_candidates", [])
        titles = raw_titles if isinstance(raw_titles, list) else []
        smart_opening = build_smart_opening(
            transcript,
            [str(item) for item in titles],
            preferred_style=suggestion.get("opening_style_id"),
        )
        raw_bgm_category = str(suggestion.get("bgm_category") or "").strip()
        bgm_category = (
            raw_bgm_category
            if raw_bgm_category in BGM_VOICEOVER_CATEGORIES
            else "通用口播"
        )
        raw_bgm_energy = str(suggestion.get("bgm_energy") or "").strip()
        bgm_energy = (
            raw_bgm_energy if raw_bgm_energy in BGM_ENERGY_LEVELS else "克制"
        )
        raw_bgm_keywords = suggestion.get("bgm_keywords", [])
        bgm_keywords = (
            [str(item) for item in raw_bgm_keywords]
            if isinstance(raw_bgm_keywords, list)
            else []
        )
        caption_groups = validated_caption_groups(
            suggestion.get("caption_groups"),
            segments or [],
            max_chars=11,
        )
        caption_group_source = (
            "qwen_semantic" if caption_groups else "deterministic_fallback"
        )
        caption_emphasis = validated_caption_emphasis(
            suggestion.get("caption_emphasis"),
            segments or [],
            caption_groups=caption_groups,
        )
        raw_usage = payload.get("usage")
        usage = (
            {
                str(key): value
                for key, value in raw_usage.items()
                if isinstance(value, (int, float, str))
            }
            if isinstance(raw_usage, Mapping)
            else {}
        )
        plan = build_safe_edit_plan(
            spoken_ranges,
            duration_seconds,
            title_candidates=[str(item) for item in titles],
            bgm_category=bgm_category,
            bgm_energy=bgm_energy,
            bgm_keywords=bgm_keywords,
            caption_groups=caption_groups,
            caption_group_source=caption_group_source,
            caption_emphasis=caption_emphasis,
            smart_opening=smart_opening,
            explanation=str(suggestion.get("explanation") or ""),
            enabled_steps=steps,
            provider_name="aliyun_qwen_flash",
            is_mock=False,
            usage=usage,
        )
        if segments and not caption_groups:
            plan = plan.model_copy(
                update={
                    "warnings": [
                        *plan.warnings,
                        "AI 语义断句未通过逐字校验，已改用安全规则断句。",
                    ]
                }
            )
        return plan


def _rpc_percent_encode(value: object) -> str:
    return quote(str(value), safe="~")


class AliyunMPSRenderProvider(CloudRenderProvider):
    """MPS SubmitJobs/QueryJobList RPC skeleton for approved render jobs."""

    def __init__(
        self,
        config: CloudEditorConfiguration,
        *,
        transport: JsonTransport | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.config = config
        self.transport = transport or _default_json_transport
        self.timeout_seconds = timeout_seconds

    @property
    def _endpoint(self) -> str:
        return f"https://mts.{self.config.aliyun_region}.aliyuncs.com/"

    def _ensure_configured(self, profile: OutputProfile | None = None) -> None:
        required = {
            "ALIBABA_CLOUD_ACCESS_KEY_ID": self.config.access_key_id,
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": self.config.access_key_secret,
            "ALIYUN_OSS_BUCKET": self.config.oss_bucket,
            "ALIYUN_MPS_PIPELINE_ID": self.config.mps_pipeline_id,
        }
        if profile is not None:
            required[
                "ALIYUN_MPS_TEMPLATE_ID_720P"
                if profile == OutputProfile.HD_720P
                else "ALIYUN_MPS_TEMPLATE_ID_1080P"
            ] = self.config.mps_template_id(profile)
        _require_configuration(required)

    def _build_rpc_request(
        self,
        action: str,
        parameters: Mapping[str, str],
        *,
        now: datetime | None = None,
        nonce: str | None = None,
    ) -> tuple[str, dict[str, str], bytes]:
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        params = {
            "AccessKeyId": self.config.access_key_id,
            "Action": action,
            "Format": "JSON",
            "RegionId": self.config.aliyun_region,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": nonce or uuid.uuid4().hex,
            "SignatureVersion": "1.0",
            "Timestamp": checked_at.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ",
            ),
            "Version": "2014-06-18",
            **parameters,
        }
        canonicalized = "&".join(
            f"{_rpc_percent_encode(key)}={_rpc_percent_encode(value)}"
            for key, value in sorted(params.items())
        )
        string_to_sign = f"POST&%2F&{_rpc_percent_encode(canonicalized)}"
        signature = base64.b64encode(
            hmac.new(
                f"{self.config.access_key_secret}&".encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest(),
        ).decode("ascii")
        body = urlencode({**params, "Signature": signature}).encode("utf-8")
        return (
            self._endpoint,
            {"Content-Type": "application/x-www-form-urlencoded"},
            body,
        )

    def build_submit_request(
        self,
        request: RenderRequest,
        *,
        now: datetime | None = None,
        nonce: str | None = None,
    ) -> tuple[str, dict[str, str], bytes]:
        if not request.review_confirmed:
            raise CloudProviderError(
                "字幕和剪辑方案尚未人工确认，不能提交 MPS。",
                kind="validation",
            )
        self._ensure_configured(request.output_profile)
        if request.input_asset.bucket != self.config.oss_bucket:
            raise CloudProviderError(
                "MPS 输入素材必须位于已配置的同地域 OSS Bucket。",
                kind="validation",
            )
        input_payload = {
            "Bucket": self.config.oss_bucket,
            "Location": self.config.oss_location,
            "Object": quote(request.input_asset.object_key, safe=""),
        }
        output_payload = [
            {
                "OutputObject": quote(request.output_object_key, safe=""),
                "TemplateId": self.config.mps_template_id(request.output_profile),
                "UserData": request.idempotency_key,
            },
        ]
        if request.edit_plan.trim_silence_enabled:
            kept_ranges = (
                request.edit_plan.kept_ranges
                or kept_ranges_for_plan(
                    request.edit_plan.duration_seconds,
                    request.edit_plan.remove_ranges,
                )
            )
            if not kept_ranges:
                raise CloudProviderError("粗剪方案没有可保留的视频片段。", kind="validation")
            first = kept_ranges[0]
            output_payload[0]["Clip"] = {
                "TimeSpan": {
                    "Seek": f"{first.start:.3f}",
                    "Duration": f"{first.end - first.start:.3f}",
                },
                "ConfigToClipFirstPart": True,
            }
            remaining = kept_ranges[1:]
            if remaining:
                source_url = request.input_asset.provider_locator or request.input_asset.uri
                merge_items = [
                    {
                        "MergeURL": source_url,
                        "Start": f"{item.start:.3f}",
                        "Duration": f"{item.end - item.start:.3f}",
                    }
                    for item in remaining
                ]
                if len(remaining) <= 4:
                    output_payload[0]["MergeList"] = merge_items
                elif request.merge_config_asset and request.merge_config_asset.provider_locator:
                    output_payload[0]["MergeConfigUrl"] = (
                        request.merge_config_asset.provider_locator
                    )
                else:
                    raise CloudProviderError(
                        "粗剪片段较多，但拼接配置文件尚未准备完成。",
                        kind="validation",
                    )
        if request.subtitle_object_key:
            output_payload[0]["SubtitleConfig"] = {
                "ExtSubtitleList": [
                    {
                        "Input": {
                            "Bucket": self.config.oss_bucket,
                            "Location": self.config.oss_location,
                            "Object": quote(request.subtitle_object_key, safe=""),
                        },
                        "CharEnc": "UTF-8",
                    },
                ],
            }
        if request.opening_asset:
            opening_url = (
                request.opening_asset.provider_locator or request.opening_asset.uri
            )
            if not opening_url:
                raise CloudProviderError(
                    "智能开场未获得可供 MPS 读取的 OSS 地址。",
                    kind="validation",
                )
            output_payload[0]["OpeningList"] = [
                {"openUrl": opening_url, "Start": "0"}
            ]
        if request.title_watermark_object_key:
            title_style = visual_style_spec(request.output_profile)["title"]
            output_payload[0]["WaterMarks"] = [
                {
                    "Type": "Image",
                    "InputFile": {
                        "Bucket": self.config.oss_bucket,
                        "Location": self.config.oss_location,
                        "Object": quote(
                            request.title_watermark_object_key,
                            safe="",
                        ),
                    },
                    "ReferPos": "TopLeft",
                    "Width": str(title_style["asset_width"]),
                    "Dx": str(title_style["safe_left"]),
                    "Dy": str(title_style["safe_top"]),
                    "Timeline": {
                        "Start": (
                            f"{request.opening_duration_seconds:.3f}"
                            if request.opening_duration_seconds
                            else "0"
                        ),
                        "Duration": str(title_style["visible_seconds"]),
                    },
                }
            ]
        if request.bgm_asset:
            bgm_url = request.bgm_asset.provider_locator or request.bgm_asset.uri
            if not bgm_url:
                raise CloudProviderError(
                    "背景音乐未获得可供 MPS 读取的 OSS 地址。",
                    kind="validation",
                )
            # MPS defaults to the longest stream.  That can make a short
            # talking-head clip unexpectedly as long as its BGM, so keep the
            # result bounded by the edited source video.
            output_payload[0]["Amix"] = [
                {
                    "AmixURL": bgm_url,
                    "Map": "0:a:0",
                    "MixDurMode": "first",
                    "Start": "0",
                }
            ]
        return self._build_rpc_request(
            "SubmitJobs",
            {
                "Input": json.dumps(input_payload, separators=(",", ":")),
                "Outputs": json.dumps(output_payload, separators=(",", ":")),
                "OutputBucket": self.config.oss_bucket,
                "OutputLocation": self.config.oss_location,
                "PipelineId": self.config.mps_pipeline_id,
            },
            now=now,
            nonce=nonce,
        )

    def build_query_request(
        self,
        provider_job_id: str,
        *,
        now: datetime | None = None,
        nonce: str | None = None,
    ) -> tuple[str, dict[str, str], bytes]:
        self._ensure_configured()
        if not provider_job_id.strip():
            raise CloudProviderError("MPS JobId 不能为空。", kind="validation")
        return self._build_rpc_request(
            "QueryJobList",
            {"JobIds": provider_job_id},
            now=now,
            nonce=nonce,
        )

    @staticmethod
    def _first_job(payload: Mapping[str, Any], *, submitted: bool) -> Mapping[str, Any]:
        if submitted:
            container = payload.get("JobResultList")
            results = (
                container.get("JobResult") if isinstance(container, Mapping) else None
            )
            if isinstance(results, list) and results:
                first = results[0]
                if isinstance(first, Mapping) and first.get("Success") is False:
                    raise CloudProviderError(
                        str(first.get("Message") or "MPS 创建转码任务失败。"),
                    )
                job = first.get("Job") if isinstance(first, Mapping) else None
                if isinstance(job, Mapping):
                    return job
        container = payload.get("JobList")
        jobs = container.get("Job") if isinstance(container, Mapping) else None
        if isinstance(jobs, list) and jobs and isinstance(jobs[0], Mapping):
            return jobs[0]
        raise CloudProviderError("MPS 响应缺少任务信息。")

    @staticmethod
    def _snapshot(job: Mapping[str, Any]) -> ProviderJobSnapshot:
        job_id = str(job.get("JobId") or "").strip()
        if not job_id:
            raise CloudProviderError("MPS 响应缺少 JobId。")
        status = _status_from_async(job.get("State") or "Submitted")
        output = job.get("Output")
        output_file = output.get("OutputFile") if isinstance(output, Mapping) else None
        output_uri = None
        if isinstance(output_file, Mapping):
            bucket = str(output_file.get("Bucket") or "").strip()
            object_key = str(output_file.get("Object") or "").strip()
            if bucket and object_key:
                output_uri = f"oss://{bucket}/{object_key}"
        return ProviderJobSnapshot(
            provider_name="aliyun_mps",
            provider_job_id=job_id,
            provider_stage=(
                "render_complete"
                if status == ProviderJobStatus.SUCCEEDED
                else "render_failed"
                if status == ProviderJobStatus.FAILED
                else "rendering"
            ),
            status=status,
            is_mock=False,
            output_uri=output_uri,
            can_publish=status == ProviderJobStatus.SUCCEEDED and bool(output_uri),
            detail={
                "creation_time": job.get("CreationTime"),
                "finish_time": job.get("FinishTime"),
                "error_code": job.get("Code"),
                "error_message": job.get("Message"),
            },
        )

    def submit(self, request: RenderRequest) -> ProviderJobSnapshot:
        url, headers, body = self.build_submit_request(request)
        payload = _submit_once(
            self.transport,
            "POST",
            url,
            headers,
            body,
            self.timeout_seconds,
        )
        return self._snapshot(self._first_job(payload, submitted=True))

    def query(self, provider_job_id: str) -> ProviderJobSnapshot:
        url, headers, body = self.build_query_request(provider_job_id)
        payload = _query_with_single_retry(
            lambda: self.transport(
                "POST",
                url,
                headers,
                body,
                self.timeout_seconds,
            ),
        )
        return self._snapshot(self._first_job(payload, submitted=False))


@dataclass(frozen=True)
class CloudProviderBundle:
    object_store: CloudObjectStore
    asr: CloudASRProvider
    edit_plan: EditPlanProvider
    render: CloudRenderProvider


def build_cloud_providers(
    config: CloudEditorConfiguration,
    *,
    json_transport: JsonTransport | None = None,
    upload_transport: UploadTransport | None = None,
) -> CloudProviderBundle:
    if config.provider_mode == CloudProviderMode.SANDBOX:
        return CloudProviderBundle(
            object_store=SandboxCloudObjectStore(),
            asr=SandboxCloudASRProvider(),
            edit_plan=SandboxEditPlanProvider(),
            render=SandboxCloudRenderProvider(),
        )
    return CloudProviderBundle(
        object_store=AliyunCloudObjectStore(
            config,
            transport=upload_transport,
        ),
        asr=AliyunFunASRProvider(config, transport=json_transport),
        edit_plan=AliyunEditPlanProvider(config, transport=json_transport),
        render=AliyunMPSRenderProvider(config, transport=json_transport),
    )
