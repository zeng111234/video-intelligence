"""LLM 文案生成适配器。"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMAdapterError(RuntimeError):
    """LLM 调用失败。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class DisabledCopywritingEngine:
    """未配置真实模型 Key 时的禁用引擎。"""

    def __init__(
        self,
        *,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-v4-flash",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.last_usage: dict[str, int] = {}

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider_name": "deepseek",
            "display_name": f"AI 文案生成 ({self.model})",
            "mode": "disabled",
            "enabled": False,
            "max_input_chars": 12000,
            "max_output_chars": 4000,
            "supports_variants": True,
            "max_variants": 5,
            "model": self.model,
            "missing_configuration": ["COPYWRITING_API_KEY"],
        }

    def generate(self, **kwargs) -> list[str]:
        raise LLMAdapterError(
            "AI 文案生成未配置 COPYWRITING_API_KEY，无法调用真实模型。"
        )

    def rewrite(self, source_text: str, **kwargs) -> list[str]:
        raise LLMAdapterError(
            "AI 文案生成未配置 COPYWRITING_API_KEY，无法调用真实模型。"
        )


class SandboxCopywritingEngine:
    """离线沙箱文案引擎，不发起真实 LLM 调用。"""

    last_usage: dict[str, int] = {}

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider_name": "sandbox_copywriting",
            "display_name": "文案生成（演示）",
            "mode": "sandbox",
            "enabled": True,
            "max_input_chars": 5000,
            "max_output_chars": 2000,
            "supports_variants": True,
            "max_variants": 3,
            "model": "sandbox-template",
            "missing_configuration": [],
        }

    def generate(
        self,
        *,
        content_brief: str,
        platform: str = "douyin",
        target_audience: str = "",
        selling_points: str = "",
        call_to_action: str = "",
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
        variant_count: int = 1,
    ) -> list[str]:
        snippet = content_brief[:80].replace("\n", " ")
        if "适合数字人口播" in style_prompt:
            title = content_brief.partition("参考视频标题：")[2].split("\n", 1)[0].strip()
            return [
                "做数字人口播，最怕什么？\n"
                "内容讲了很久，用户却划走了。\n"
                "先别急着换形象。\n"
                f"先把「{title[:16]}」讲清楚。\n"
                "开头先说结果。\n"
                "中间只讲一个关键方法。\n"
                "每句话都让用户听得懂。\n"
                "最后再留一个动作。\n"
                "想看具体做法，评论区告诉我。"
            ]
        templates = [
            f"【演示】开场钩子：如果你正在关注「{snippet}」，这条内容值得看完。\n主体：围绕核心卖点，用更清晰的结构讲明价值。\nCTA：{call_to_action or '欢迎私信了解更多。'}",
            f"【演示】开场钩子：同样是做{platform}内容，差距往往在表达顺序。\n主体：「{snippet}」可以先讲痛点，再给方案。\nCTA：{call_to_action or '觉得有用可以收藏。'}",
            f"【演示】开场钩子：别急着堆信息，先让目标用户听懂重点。\n主体：面向{target_audience or '目标客户'}，突出{selling_points or '核心价值'}。\nCTA：{call_to_action or '想要方案可以联系我们。'}",
        ]
        count = max(1, min(variant_count, len(templates)))
        return templates[:count]

    def rewrite(
        self,
        source_text: str,
        *,
        platform: str = "douyin",
        target_audience: str = "",
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
        rewrite_goal: str = "",
        variant_count: int = 1,
    ) -> list[str]:
        snippet = source_text[:80].replace("\n", " ")
        templates = [
            f"【演示】专业口播文案：围绕「{snippet}」展开，以数据驱动的视角为您解读行业趋势。了解更多请联系我们。",
            f"【演示】轻松风格：嘿！今天聊聊「{snippet}」——三个关键点帮你快速上手，记得点赞收藏哦！",
            f"【演示】故事型文案：从一个真实案例说起——「{snippet}」背后的逻辑，让每一步都有据可循。",
        ]
        count = max(1, min(variant_count, len(templates)))
        return templates[:count]


class OpenAICompatibleCopywritingEngine:
    """通过 OpenAI 兼容 API 调用 LLM 进行文案生成。"""

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
        self.last_usage: dict[str, int] = {}

    @classmethod
    def from_env(cls) -> OpenAICompatibleCopywritingEngine:
        return cls(
            api_key=os.getenv("COPYWRITING_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
            base_url=(
                os.getenv("COPYWRITING_BASE_URL")
                or os.getenv("OPENAI_BASE_URL")
                or "https://api.deepseek.com"
            ),
            model=(
                os.getenv("COPYWRITING_MODEL")
                or os.getenv("COPYWRITING_LLM_MODEL")
                or "deepseek-v4-flash"
            ),
        )

    def capabilities(self) -> dict[str, Any]:
        configured = bool(self.api_key)
        return {
            "provider_name": self._provider_name(),
            "display_name": f"AI 文案生成 ({self.model})",
            "mode": "production" if configured else "disabled",
            "enabled": configured,
            "max_input_chars": 12000,
            "max_output_chars": 4000,
            "supports_variants": True,
            "max_variants": 5,
            "model": self.model,
            "missing_configuration": [] if configured else ["COPYWRITING_API_KEY"],
        }

    def generate(
        self,
        *,
        content_brief: str,
        platform: str = "douyin",
        target_audience: str = "",
        selling_points: str = "",
        call_to_action: str = "",
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
        variant_count: int = 1,
    ) -> list[str]:
        if not self.api_key:
            raise LLMAdapterError("未配置 COPYWRITING_API_KEY，无法调用 LLM。")
        system_prompt = self._build_system_prompt(
            style_prompt,
            target_length,
            tone,
            platform,
            target_audience,
            variant_count,
        )
        user_prompt = self._build_generate_prompt(
            content_brief=content_brief,
            platform=platform,
            target_audience=target_audience,
            selling_points=selling_points,
            call_to_action=call_to_action,
        )
        return self._generate_variants(system_prompt, user_prompt, variant_count)

    def rewrite(
        self,
        source_text: str,
        *,
        platform: str = "douyin",
        target_audience: str = "",
        style_prompt: str = "",
        target_length: int = 300,
        tone: str = "professional",
        rewrite_goal: str = "",
        variant_count: int = 1,
    ) -> list[str]:
        if not self.api_key:
            raise LLMAdapterError("未配置 COPYWRITING_API_KEY，无法调用 LLM。")
        system_prompt = self._build_system_prompt(
            style_prompt,
            target_length,
            tone,
            platform,
            target_audience,
            variant_count,
        )
        user_prompt = (
            "任务：优化已有短视频口播文案。\n"
            f"目标受众：{target_audience or '未指定'}\n"
            "要求：保持原文事实和核心信息不变，重组表达为自然、短句、便于停顿的口播稿。"
            "涉及收入、效果或经历时不得改写成可复制的保证。\n"
            f"本次优化目标：{rewrite_goal or '自然口播与风险表达优化'}\n"
            f"原文：\n{source_text}"
        )
        return self._generate_variants(system_prompt, user_prompt, variant_count)

    def _provider_name(self) -> str:
        if "deepseek.com" in self.base_url.lower():
            return "deepseek"
        return "openai_compatible"

    def _build_system_prompt(
        self,
        style_prompt: str,
        target_length: int,
        tone: str,
        platform: str = "douyin",
        target_audience: str = "",
        variant_count: int = 1,
    ) -> str:
        parts = [
            "你是一位专业的短视频口播文案撰写专家。",
            "只能使用用户提供的事实，不得虚构价格、资质、客户案例、数据、效果承诺或平台背书。",
            "输出应口语化、短句、自然停顿，开头直接进入重点；不要输出开场钩子、主体、CTA 等栏目标题。",
            "面对收益、效果、医疗金融或官方背书等风险表达，改为个人经历、条件性或可核实的表述；不得承诺审核通过。",
            "按信息完整度决定篇幅，删除重复句，不为凑字数扩写。",
            "每个变体都要有实质差异，不能只是替换同义词。",
            "返回严格 JSON，不要 Markdown，不要解释。",
            'JSON 格式：{"variants":["文案1","文案2"],"notes":[]}',
        ]
        if style_prompt:
            parts.append(f"风格要求：{style_prompt}")
        if target_audience:
            parts.append(f"目标受众：{target_audience}")
        parts.append(f"语气：{tone}")
        parts.append(f"变体数量：{max(1, min(variant_count, 5))}")
        return "\n".join(parts)

    def _build_generate_prompt(
        self,
        *,
        content_brief: str,
        platform: str,
        target_audience: str,
        selling_points: str,
        call_to_action: str,
    ) -> str:
        return "\n".join(
            [
                "任务：从需求生成短视频文案。",
                f"内容概要：{content_brief}",
                f"目标受众：{target_audience or '未指定'}",
                f"核心卖点：{selling_points or '未指定'}",
                f"行动号召：{call_to_action or '未指定'}",
                "要求：信息不足时保持克制，用可验证表述，不编造缺失事实。",
            ]
        )

    def _generate_variants(
        self,
        system_prompt: str,
        user_prompt: str,
        variant_count: int,
    ) -> list[str]:
        content = self._chat_completion(system_prompt, user_prompt)
        variants = self._parse_variants(content)
        count = max(1, min(variant_count, 5))
        variants = [item.strip() for item in variants if item.strip()]
        if not variants:
            raise LLMAdapterError("LLM 未返回有效内容。")
        return variants[:count]

    def _chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.8,
            "max_tokens": 2400,
            "response_format": {"type": "json_object"},
        }
        if self._provider_name() == "deepseek":
            payload["thinking"] = {"type": "disabled"}

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
            raise LLMAdapterError(
                self._http_error_message(exc),
                retryable=exc.code in {408, 409, 425, 429, 500, 502, 503, 504},
            ) from exc
        except (TimeoutError, URLError) as exc:
            raise LLMAdapterError(
                "无法连接 LLM 服务，请检查网络、Base URL 和供应商状态。",
                retryable=True,
            ) from exc

        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LLMAdapterError("LLM 返回了无效 JSON。") from exc

        self.last_usage = self._extract_usage(envelope.get("usage", {}))
        choices = envelope.get("choices", [])
        if not choices:
            return ""
        return str(choices[0].get("message", {}).get("content", "")).strip()

    @staticmethod
    def _http_error_message(exc: HTTPError) -> str:
        raw = exc.read().decode("utf-8", errors="replace")[:300]
        try:
            payload = json.loads(raw)
            detail = payload.get("error", {}).get("message") or payload.get("message")
        except json.JSONDecodeError:
            detail = ""
        suffix = f"：{detail}" if detail else ""
        return f"LLM API 返回 HTTP {exc.code}{suffix}"

    @staticmethod
    def _extract_usage(usage: Any) -> dict[str, int]:
        if not isinstance(usage, dict):
            return {}
        result: dict[str, int] = {}
        for source_key, target_key in [
            ("prompt_tokens", "prompt_tokens"),
            ("completion_tokens", "completion_tokens"),
            ("total_tokens", "total_tokens"),
        ]:
            value = usage.get(source_key)
            if isinstance(value, int):
                result[target_key] = value
        return result

    @staticmethod
    def _parse_variants(content: str) -> list[str]:
        if not content.strip():
            return []
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            return [cleaned]
        if isinstance(payload, dict):
            variants = payload.get("variants")
            if isinstance(variants, list):
                return [str(item) for item in variants]
            text = payload.get("text") or payload.get("result")
            if text:
                return [str(text)]
        if isinstance(payload, list):
            return [str(item) for item in payload]
        return []


def build_copywriting_engine(secrets: dict[str, Any] | None = None):
    """工厂：根据配置返回文案引擎。

    兼容旧 Streamlit 调用；正式 FastAPI 依赖注入在 project/backend/app/core/deps.py。
    """
    mode = _setting("COPYWRITING_MODE", secrets, "production").strip().lower()
    if mode == "sandbox":
        return SandboxCopywritingEngine()

    api_key = _setting("COPYWRITING_API_KEY", secrets) or _setting(
        "OPENAI_API_KEY", secrets
    )
    base_url = (
        _setting("COPYWRITING_BASE_URL", secrets)
        or _setting("OPENAI_BASE_URL", secrets)
        or "https://api.deepseek.com"
    )
    model = (
        _setting("COPYWRITING_MODEL", secrets)
        or _setting("COPYWRITING_LLM_MODEL", secrets)
        or "deepseek-v4-flash"
    )
    if api_key:
        return OpenAICompatibleCopywritingEngine(
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
    return DisabledCopywritingEngine(base_url=base_url, model=model)


def _setting(key: str, secrets: dict[str, Any] | None = None, default: str = "") -> str:
    value = os.getenv(key, "")
    if not value and secrets:
        value = str(secrets.get(key, ""))
    return (value or default).strip()
