"""Desktop adapter for the company-hosted copywriting engine."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any
from uuid import uuid4

import httpx

from project.backend.app.services.control_plane_client import (
    active_upstream_customer_session,
    control_plane_base_url,
)
from src.adapters.llm import LLMAdapterError


class RemoteCopywritingEngine:
    """Use paid AI through the control plane without exposing its credential."""

    is_remote = True
    billing_centrally_managed = True

    def __init__(self) -> None:
        self.last_usage: dict[str, int] = {}
        self.last_charged_credits = 0.0
        self.last_attention_terms: list[str] = []
        self._pending_keys: dict[str, str] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _timeout() -> float:
        try:
            configured = float(os.getenv("CONTROL_PLANE_TIMEOUT_SECONDS", "60"))
        except ValueError:
            configured = 60.0
        return max(5.0, min(configured, 120.0))

    @staticmethod
    def _verification() -> bool | str:
        configured = os.getenv("CONTROL_PLANE_CA_BUNDLE", "").strip()
        if not configured:
            return True
        from pathlib import Path

        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise LLMAdapterError("公司服务证书配置无效，请联系管理员。")
        return str(path)

    @staticmethod
    def _session() -> str:
        token = active_upstream_customer_session()
        if not token:
            raise LLMAdapterError("请先登录客户账号，再使用 AI 功能。")
        return token

    def capabilities(self) -> dict[str, Any]:
        token = active_upstream_customer_session()
        base_url = control_plane_base_url()
        if not token or not base_url:
            return {
                "provider_name": "company_control_plane",
                "display_name": "AI 文案生成",
                "mode": "disabled",
                "enabled": False,
                "model": "",
                "max_input_chars": 12000,
                "max_output_chars": 4000,
                "supports_variants": True,
                "max_variants": 5,
                "estimated_cost_cny": None,
                "missing_configuration": ["请先登录客户账号"],
            }
        url = f"{base_url}/api/v1/copywriting/capabilities"
        headers = {"X-Customer-Token": token, "Accept": "application/json"}
        response: httpx.Response | None = None
        try:
            with httpx.Client(
                timeout=self._timeout(),
                verify=self._verification(),
                follow_redirects=False,
            ) as client:
                for attempt in range(2):
                    response = client.get(url, headers=headers)
                    if response.status_code not in {502, 503} or attempt == 1:
                        break
        except (httpx.HTTPError, LLMAdapterError):
            response = None
        if response is None or not response.is_success:
            return {
                "provider_name": "company_control_plane",
                "display_name": "AI 文案生成",
                "mode": "disabled",
                "enabled": False,
                "model": "",
                "max_input_chars": 12000,
                "max_output_chars": 4000,
                "supports_variants": True,
                "max_variants": 5,
                "estimated_cost_cny": None,
                "missing_configuration": ["公司 AI 服务暂不可用"],
            }
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise LLMAdapterError("公司 AI 服务返回了无效配置。") from exc
        payload["model"] = str(payload.pop("model_name", payload.get("model", "")))
        payload["estimated_cost_cny"] = None
        return payload

    def _invoke(self, operation: str, payload: dict[str, Any]):
        base_url = control_plane_base_url()
        if not base_url:
            raise LLMAdapterError("公司服务地址未配置。")
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = hashlib.sha256(
            operation.encode("utf-8") + b"\0" + encoded
        ).hexdigest()
        with self._lock:
            operation_key = self._pending_keys.setdefault(
                fingerprint,
                f"remote-copy-{uuid4().hex}",
            )
        headers = {
            "X-Customer-Token": self._session(),
            "Idempotency-Key": operation_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(
                timeout=self._timeout(),
                verify=self._verification(),
                follow_redirects=False,
            ) as client:
                response = client.post(
                    f"{base_url}/api/v1/provider/copywriting/{operation}",
                    headers=headers,
                    content=encoded,
                )
        except httpx.HTTPError as exc:
            raise LLMAdapterError(
                "公司 AI 服务连接失败，本地内容已保留；系统不会自动重复提交。"
            ) from exc

        keep_key = response.status_code == 409 or response.status_code >= 500
        if not keep_key:
            with self._lock:
                self._pending_keys.pop(fingerprint, None)
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise LLMAdapterError("公司 AI 服务返回了无效结果。") from exc
        if not response.is_success:
            message = response_payload.get("message") or response_payload.get("detail")
            raise LLMAdapterError(str(message or "公司 AI 服务暂不可用。"))
        result = response_payload.get("result")
        if not isinstance(result, (list, dict)):
            raise LLMAdapterError("公司 AI 服务返回了无效结果。")
        raw_usage = response_payload.get("token_usage")
        self.last_usage = (
            {str(key): int(value) for key, value in raw_usage.items()}
            if isinstance(raw_usage, dict)
            else {}
        )
        raw_terms = response_payload.get("attention_terms")
        self.last_attention_terms = (
            [str(item) for item in raw_terms]
            if isinstance(raw_terms, list)
            else []
        )
        try:
            self.last_charged_credits = max(
                0.0, float(response_payload.get("charged_credits") or 0)
            )
        except (TypeError, ValueError):
            self.last_charged_credits = 0.0
        return result

    def generate(self, **kwargs) -> list[str]:
        result = self._invoke("generate", kwargs)
        if not isinstance(result, list):
            raise LLMAdapterError("公司 AI 服务未返回有效文案。")
        return [str(item) for item in result]

    def rewrite(self, source_text: str, **kwargs) -> list[str]:
        result = self._invoke("rewrite", {"source_text": source_text, **kwargs})
        if not isinstance(result, list):
            raise LLMAdapterError("公司 AI 服务未返回有效文案。")
        return [str(item) for item in result]

    def generate_publish_metadata(self, source_text: str, **kwargs) -> dict[str, Any]:
        result = self._invoke(
            "publish-metadata",
            {"source_text": source_text, **kwargs},
        )
        if not isinstance(result, dict):
            raise LLMAdapterError("公司 AI 服务未返回有效发布信息。")
        return result

    def review_transcript_candidates(self, **kwargs) -> dict[str, Any]:
        result = self._invoke("review-transcript-candidates", kwargs)
        if not isinstance(result, dict):
            raise LLMAdapterError("公司 AI 服务未返回有效校对结果。")
        return result

    def review_transcript_batch(self, **kwargs) -> dict[str, Any]:
        result = self._invoke("review-transcript-batch", kwargs)
        if not isinstance(result, dict):
            raise LLMAdapterError("公司 AI 服务未返回有效校对结果。")
        return result

    def select_best_spoken_script(self, **kwargs) -> dict[str, str]:
        result = self._invoke("select-best-spoken-script", kwargs)
        if not isinstance(result, dict):
            raise LLMAdapterError("公司 AI 服务未返回有效选稿结果。")
        return {str(key): str(value) for key, value in result.items()}

    def review_spoken_script(self, **kwargs) -> dict[str, Any]:
        result = self._invoke("review-spoken-script", kwargs)
        if not isinstance(result, dict):
            raise LLMAdapterError("公司 AI 服务未返回有效审核结果。")
        return result
