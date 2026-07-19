"""LLM 文案改写适配器。

支持多种后端：
- 本地/自托管 LLM（如 Ollama、vLLM）
- OpenAI 兼容 API
- 离线沙箱（演示模式）
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMAdapterError(RuntimeError):
    """LLM 调用失败。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class SandboxCopywritingEngine:
    """离线沙箱文案改写引擎——不发起真实 LLM 调用。"""

    def capabilities(self) -> dict[str, str | bool | int]:
        return {
            "provider_name": "sandbox_copywriting",
            "display_name": "文案改写（演示）",
            "mode": "sandbox",
            "enabled": True,
            "max_input_chars": 5000,
            "max_output_chars": 2000,
            "supports_variants": True,
            "max_variants": 3,
        }

    def rewrite(
        self,
        source_text: str,
        *,
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
        variant_count: int = 1,
    ) -> list[str]:
        """返回演示文案，不调用真实 LLM。"""
        snippet = source_text[:80].replace("\n", " ")
        templates = [
            f"【演示】专业口播文案：围绕「{snippet}」展开，以数据驱动的视角为您解读行业趋势。了解更多请联系我们。",
            f"【演示】轻松风格：嘿！今天聊聊「{snippet}」——三个关键点帮你快速上手，记得点赞收藏哦！",
            f"【演示】故事型文案：从一个真实案例说起——「{snippet}」背后的逻辑，让每一步都有据可循。",
        ]
        count = max(1, min(variant_count, len(templates)))
        return templates[:count]


class OpenAICompatibleCopywritingEngine:
    """通过 OpenAI 兼容 API 调用 LLM 进行文案改写。

    兼容 OpenAI、Ollama /v1、vLLM 等后端。
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        *,
        timeout_seconds: float = 60,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = max(5.0, timeout_seconds)

    @classmethod
    def from_env(cls) -> OpenAICompatibleCopywritingEngine:
        return cls(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.getenv("COPYWRITING_LLM_MODEL", "gpt-4o-mini"),
        )

    def capabilities(self) -> dict[str, str | bool | int]:
        configured = bool(self.api_key)
        return {
            "provider_name": "openai_compatible",
            "display_name": f"LLM 文案改写 ({self.model})",
            "mode": "production" if configured else "disabled",
            "enabled": configured,
            "max_input_chars": 12000,
            "max_output_chars": 4000,
            "supports_variants": True,
            "max_variants": 5,
            "model": self.model,
        }

    def rewrite(
        self,
        source_text: str,
        *,
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
        variant_count: int = 1,
    ) -> list[str]:
        if not self.api_key:
            raise LLMAdapterError("未配置 OPENAI_API_KEY，无法调用 LLM。")
        system_prompt = self._build_system_prompt(style_prompt, target_length, tone)
        user_prompt = f"请基于以下原文进行改写：\n\n{source_text}"
        results: list[str] = []
        for _ in range(max(1, variant_count)):
            text = self._chat_completion(system_prompt, user_prompt)
            if text:
                results.append(text)
        if not results:
            raise LLMAdapterError("LLM 未返回有效内容。")
        return results

    def _build_system_prompt(
        self, style_prompt: str, target_length: int, tone: str
    ) -> str:
        parts = [
            "你是一位专业的短视频口播文案撰写专家。",
            "请根据用户提供的原文进行改写，保持核心信息不变，但用更吸引人的方式表达。",
        ]
        if style_prompt:
            parts.append(f"风格要求：{style_prompt}")
        parts.append(f"语气：{tone}")
        parts.append(f"目标字数：约{target_length}字")
        parts.append("只输出改写后的文案，不要解释。")
        return "\n".join(parts)

    def _chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.8,
            "max_tokens": 2000,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {self.api_key}",
        }
        url = f"{self.base_url}/chat/completions"
        request = Request(url, data=body, headers=headers, method="POST")  # noqa: S310
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise LLMAdapterError(
                f"LLM API 返回 HTTP {exc.code}：{detail}",
                retryable=exc.code in {429, 500, 502, 503},
            ) from exc
        except (TimeoutError, URLError) as exc:
            raise LLMAdapterError(
                "无法连接 LLM 服务，请检查网络和配置。", retryable=True
            ) from exc
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LLMAdapterError("LLM 返回了无效 JSON。") from exc
        choices = envelope.get("choices", [])
        if not choices:
            return ""
        return str(choices[0].get("message", {}).get("content", "")).strip()


def build_copywriting_engine(secrets: dict[str, Any] | None = None):
    """工厂：根据配置返回合适的文案改写引擎。"""
    import streamlit as st

    mode = os.getenv("COPYWRITING_MODE", "").strip().lower()
    if secrets:
        mode = str(st.secrets.get("COPYWRITING_MODE", mode)).strip().lower()

    if mode == "sandbox":
        return SandboxCopywritingEngine()

    api_key = os.getenv("OPENAI_API_KEY", "")
    if secrets:
        api_key = str(st.secrets.get("OPENAI_API_KEY", api_key)).strip()
    if api_key:
        return OpenAICompatibleCopywritingEngine.from_env()

    # 默认沙箱
    return SandboxCopywritingEngine()
