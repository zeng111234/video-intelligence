"""Safe OpenAI-compatible image generation for the director pipeline.

The provider is intentionally opt-in.  A missing endpoint, model, key, unit
price or budget produces a truthful unavailable quote and never performs a
paid request.
"""

from __future__ import annotations

import base64
import binascii
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx


class ImageGenerationError(RuntimeError):
    """A safe, user-displayable image generation failure."""


@dataclass(frozen=True)
class ImageGenerationConfiguration:
    mode: str = "disabled"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    unit_price_cny: Decimal | None = None
    budget_cny: Decimal | None = None
    timeout_seconds: float = 60.0
    allowed_hosts: tuple[str, ...] = ()
    autogenerate_on_keyword_match: bool = False
    manifest_path: str = ""

    @property
    def is_minimax_token_plan(self) -> bool:
        return self.mode in {"minimax_token_plan", "minimax"}

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ImageGenerationConfiguration:
        if env is None:
            try:
                from dotenv import load_dotenv

                load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
            except Exception:
                # Environment variables remain the primary configuration path;
                # a missing optional dotenv dependency must not break startup.
                pass
            source = os.environ
        else:
            source = env
        mode = str(source.get("VIDEO_IMAGE_PROVIDER_MODE", "disabled")).strip().casefold() or "disabled"
        is_minimax = mode in {"minimax_token_plan", "minimax"}
        raw_unit = str(source.get("VIDEO_IMAGE_UNIT_PRICE_CNY", "")).strip()
        raw_budget = str(source.get("VIDEO_IMAGE_BUDGET_CNY", "")).strip()

        def decimal_or_none(value: str) -> Decimal | None:
            if not value:
                return None
            try:
                amount = Decimal(value)
            except InvalidOperation:
                return None
            return amount if amount >= 0 else None

        raw_timeout = str(source.get("VIDEO_IMAGE_TIMEOUT_SECONDS", "60")).strip()
        try:
            timeout = max(5.0, min(300.0, float(raw_timeout)))
        except ValueError:
            timeout = 60.0
        # P0-4 v2: MiniMax 默认只允许 https://api.minimax.io。
        # 即便用户显式配了其他 base_url，MiniMax 模式仍强制回到 api.minimax.io。
        base_url = str(source.get("VIDEO_IMAGE_BASE_URL", "")).strip().rstrip("/")
        if is_minimax:
            base_url = "https://api.minimax.io"  # 强制锁定
        model = str(source.get("VIDEO_IMAGE_MODEL", "")).strip()
        if is_minimax and not model:
            model = "image-01"
        api_key = str(
            source.get(
                "MINIMAX_TOKEN_PLAN_KEY" if is_minimax else "VIDEO_IMAGE_API_KEY",
                "",
            )
        ).strip()
        # P0-4 v2: MiniMax 模式默认 allowed_hosts = ("api.minimax.io",)
        configured_hosts_raw = tuple(
            item.strip().casefold()
            for item in str(source.get("VIDEO_IMAGE_ALLOWED_HOSTS", "")).split(",")
            if item.strip()
        )
        if is_minimax:
            # 始终把 api.minimax.io 放进白名单（如果未声明）
            configured_hosts = tuple(
                set(configured_hosts_raw) | {"api.minimax.io"}
            )
        else:
            configured_hosts = configured_hosts_raw
        return cls(
            mode=mode,
            base_url=base_url,
            model=model,
            api_key=api_key,
            unit_price_cny=decimal_or_none(raw_unit),
            budget_cny=decimal_or_none(raw_budget),
            timeout_seconds=timeout,
            allowed_hosts=configured_hosts,
            autogenerate_on_keyword_match=str(
                source.get("VIDEO_IMAGE_AUTOGENERATE_ON_KEYWORD_MATCH", "false")
            ).strip().casefold() in {"1", "true", "yes", "on"},
            manifest_path=str(source.get("VIDEO_IMAGE_MANIFEST_PATH", "")).strip(),
        )

    @property
    def missing_configuration(self) -> list[str]:
        missing: list[str] = []
        if self.mode not in {"openai_compatible", "minimax_token_plan", "minimax"}:
            missing.append("VIDEO_IMAGE_PROVIDER_MODE")
        if not self.base_url:
            missing.append("VIDEO_IMAGE_BASE_URL")
        if not self.model:
            missing.append("VIDEO_IMAGE_MODEL")
        if not self.api_key:
            missing.append(
                "MINIMAX_TOKEN_PLAN_KEY"
                if self.is_minimax_token_plan
                else "VIDEO_IMAGE_API_KEY"
            )
        if not self.is_minimax_token_plan:
            if self.unit_price_cny is None:
                missing.append("VIDEO_IMAGE_UNIT_PRICE_CNY")
            if self.budget_cny is None:
                missing.append("VIDEO_IMAGE_BUDGET_CNY")
        return missing


@dataclass(frozen=True)
class ImageGenerationQuote:
    requested_count: int
    unit_price_cny: Decimal | None
    total_price_cny: Decimal | None
    budget_cny: Decimal | None
    can_generate: bool
    missing_configuration: tuple[str, ...] = ()
    blocking_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_count": self.requested_count,
            "unit_price_cny": (
                str(self.unit_price_cny) if self.unit_price_cny is not None else None
            ),
            "total_price_cny": (
                str(self.total_price_cny) if self.total_price_cny is not None else None
            ),
            "budget_cny": str(self.budget_cny) if self.budget_cny is not None else None,
            "can_generate": self.can_generate,
            "missing_configuration": list(self.missing_configuration),
            "blocking_reason": self.blocking_reason,
        }


@dataclass(frozen=True)
class GeneratedImage:
    image_bytes: bytes
    mime_type: str
    provider: str
    model: str


class OpenAICompatibleImageProvider:
    """Minimal images/generations client for a configured relay."""

    def __init__(
        self,
        configuration: ImageGenerationConfiguration,
        *,
        transport: Any = None,
    ) -> None:
        self.configuration = configuration
        self.transport = transport or self._default_transport

    def quote(self, requested_count: int) -> ImageGenerationQuote:
        return _quote_for_configuration(self.configuration, requested_count)

    def _endpoint(self) -> str:
        parts = urlsplit(self.configuration.base_url)
        # P0-4: 仅允许 HTTPS；拒绝明文 HTTP（防止 API Key 泄露）
        if parts.scheme != "https" or not parts.hostname:
            raise ImageGenerationError("生图 Base URL 必须使用 HTTPS。")
        if parts.username or parts.password:
            raise ImageGenerationError("生图 Base URL 不允许携带账号密码。")
        return f"{self.configuration.base_url}/v1/images/generations"

    def build_request(self, prompt: str, *, negative_prompt: str = "", size: str = "1024x1792") -> tuple[str, dict[str, str], bytes]:
        if not prompt.strip():
            raise ImageGenerationError("生图提示词不能为空。")
        endpoint = self._endpoint()
        payload: dict[str, Any] = {
            "model": self.configuration.model,
            "prompt": prompt.strip(),
            "size": size,
            "n": 1,
            "response_format": "b64_json",
        }
        if negative_prompt.strip():
            payload["negative_prompt"] = negative_prompt.strip()
        return (
            endpoint,
            {
                "Authorization": f"Bearer {self.configuration.api_key}",
                "Content-Type": "application/json",
            },
            json_dumps(payload),
        )

    @staticmethod
    def _default_transport(method: str, url: str, headers: dict[str, str], body: bytes, timeout: float) -> Mapping[str, Any]:
        with httpx.Client(timeout=timeout) as client:
            response = client.request(method, url, headers=headers, content=body)
            response.raise_for_status()
            return response.json()

    def generate(self, prompt: str, *, negative_prompt: str = "", size: str = "1024x1792") -> GeneratedImage:
        quote = self.quote(1)
        if not quote.can_generate:
            raise ImageGenerationError(quote.blocking_reason or "生图当前不可用。")
        endpoint, headers, body = self.build_request(prompt, negative_prompt=negative_prompt, size=size)
        payload = self._call_once(endpoint, headers, bytes(body))
        data = payload.get("data") if isinstance(payload, Mapping) else None
        first = data[0] if isinstance(data, list) and data else None
        if not isinstance(first, Mapping):
            raise ImageGenerationError("中转站返回中缺少 data[0] 图片结果。")
        encoded = first.get("b64_json")
        if isinstance(encoded, str) and encoded:
            try:
                image_bytes = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ImageGenerationError("中转站返回的图片不是有效 Base64。") from exc
            if not image_bytes:
                raise ImageGenerationError("中转站返回了空图片。")
            return GeneratedImage(image_bytes, "image/png", "openai_compatible", self.configuration.model)
        raise ImageGenerationError("当前仅接受 Base64 图片结果；中转站 URL 结果尚未列入安全自动链。")

    def _call_once(self, endpoint: str, headers: dict[str, str], body: bytes) -> Mapping[str, Any]:
        try:
            payload = self.transport(
                "POST", endpoint, headers, body, self.configuration.timeout_seconds
            )
        except (httpx.TimeoutException, httpx.NetworkError, OSError):
            time.sleep(0.05)
            try:
                payload = self.transport(
                    "POST", endpoint, headers, body, self.configuration.timeout_seconds
                )
            except Exception as retry_exc:
                raise ImageGenerationError("生图中转站连接失败，已重试一次。") from retry_exc
        if not isinstance(payload, Mapping):
            raise ImageGenerationError("生图中转站返回格式不是对象。")
        return payload


class MiniMaxTokenPlanImageProvider:
    """MiniMax image-01 client using a Token Plan subscription key."""

    def __init__(
        self,
        configuration: ImageGenerationConfiguration,
        *,
        transport: Any = None,
    ) -> None:
        self.configuration = configuration
        self.transport = transport or OpenAICompatibleImageProvider._default_transport

    def quote(self, requested_count: int) -> ImageGenerationQuote:
        return _quote_for_configuration(self.configuration, requested_count)

    def _endpoint(self) -> str:
        parts = urlsplit(self.configuration.base_url)
        # P0-4: 仅允许 HTTPS；拒绝明文 HTTP（防止 API Key 泄露）
        if parts.scheme != "https" or not parts.hostname:
            raise ImageGenerationError("MiniMax 生图地址必须使用 HTTPS。")
        if parts.username or parts.password:
            raise ImageGenerationError("MiniMax 生图地址不允许携带账号密码。")
        # P0-4: 真校验 host 白名单。若 allowed_hosts 配置了非空列表，
        # 当前 base_url 的 hostname 必须落在白名单内。MiniMax 默认 base_url
        # 是 api.minimax.io，必须显式声明才会被放行。
        if self.configuration.allowed_hosts:
            allowed = {
                str(host).strip().casefold()
                for host in self.configuration.allowed_hosts
                if str(host).strip()
            }
            current_host = str(parts.hostname or "").strip().casefold()
            if current_host not in allowed:
                raise ImageGenerationError(
                    f"MiniMax 生图 host {current_host!r} 不在白名单内。"
                )
        return f"{self.configuration.base_url}/v1/image_generation"

    def build_request(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
        aspect_ratio: str = "9:16",
    ) -> tuple[str, dict[str, str], bytes]:
        if not prompt.strip():
            raise ImageGenerationError("生图提示词不能为空。")
        endpoint = self._endpoint()
        payload: dict[str, Any] = {
            "model": self.configuration.model,
            "prompt": prompt.strip(),
            "aspect_ratio": aspect_ratio,
            "response_format": "base64",
            "n": 1,
            "prompt_optimizer": True,
        }
        # MiniMax currently does not document a separate negative_prompt field;
        # keep it out of the request so Token Plan calls remain protocol-safe.
        _ = negative_prompt
        return (
            endpoint,
            {
                "Authorization": f"Bearer {self.configuration.api_key}",
                "Content-Type": "application/json",
            },
            json_dumps(payload),
        )

    def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
        aspect_ratio: str = "9:16",
    ) -> GeneratedImage:
        quote = self.quote(1)
        if not quote.can_generate:
            raise ImageGenerationError(quote.blocking_reason or "MiniMax 生图当前不可用。")
        endpoint, headers, body = self.build_request(
            prompt,
            negative_prompt=negative_prompt,
            aspect_ratio=aspect_ratio,
        )
        payload = self._call_once(endpoint, headers, bytes(body))
        data = payload.get("data") if isinstance(payload, Mapping) else None
        encoded_items = data.get("image_base64") if isinstance(data, Mapping) else None
        encoded = encoded_items[0] if isinstance(encoded_items, list) and encoded_items else None
        if not isinstance(encoded, str) or not encoded:
            raise ImageGenerationError("MiniMax 返回中缺少 data.image_base64[0] 图片结果。")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ImageGenerationError("MiniMax 返回的图片不是有效 Base64。") from exc
        if not image_bytes:
            raise ImageGenerationError("MiniMax 返回了空图片。")
        mime_type = "image/jpeg" if image_bytes.startswith(b"\xff\xd8") else "image/png"
        return GeneratedImage(image_bytes, mime_type, "minimax_token_plan", self.configuration.model)

    def _call_once(self, endpoint: str, headers: dict[str, str], body: bytes) -> Mapping[str, Any]:
        try:
            payload = self.transport(
                "POST", endpoint, headers, body, self.configuration.timeout_seconds
            )
        except (httpx.TimeoutException, httpx.NetworkError, OSError):
            time.sleep(0.05)
            try:
                payload = self.transport(
                    "POST", endpoint, headers, body, self.configuration.timeout_seconds
                )
            except Exception as retry_exc:
                raise ImageGenerationError("MiniMax 生图连接失败，已重试一次。") from retry_exc
        if not isinstance(payload, Mapping):
            raise ImageGenerationError("MiniMax 返回格式不是对象。")
        return payload


def _quote_for_configuration(
    configuration: ImageGenerationConfiguration,
    requested_count: int,
) -> ImageGenerationQuote:
    count = max(0, int(requested_count))
    missing = tuple(configuration.missing_configuration)
    if count == 0:
        return ImageGenerationQuote(
            count,
            configuration.unit_price_cny,
            Decimal("0"),
            configuration.budget_cny,
            True,
        )
    if missing:
        return ImageGenerationQuote(
            count,
            configuration.unit_price_cny,
            None,
            configuration.budget_cny,
            False,
            missing,
            "生图接口或本条预算尚未配置，未发起调用。",
        )
    if configuration.is_minimax_token_plan:
        return ImageGenerationQuote(
            count,
            Decimal("0"),
            Decimal("0"),
            None,
            True,
        )
    total = (configuration.unit_price_cny or Decimal("0")) * count
    if configuration.budget_cny is None or total > configuration.budget_cny:
        return ImageGenerationQuote(
            count,
            configuration.unit_price_cny,
            total,
            configuration.budget_cny,
            False,
            (),
            "预计生图费用超过本条预算上限。",
        )
    return ImageGenerationQuote(
        count,
        configuration.unit_price_cny,
        total,
        configuration.budget_cny,
        True,
    )


def build_image_provider(
    configuration: ImageGenerationConfiguration,
    *,
    transport: Any = None,
) -> OpenAICompatibleImageProvider | MiniMaxTokenPlanImageProvider:
    if configuration.is_minimax_token_plan:
        return MiniMaxTokenPlanImageProvider(configuration, transport=transport)
    return OpenAICompatibleImageProvider(configuration, transport=transport)


def json_dumps(payload: Mapping[str, Any]) -> bytes:
    import json

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
