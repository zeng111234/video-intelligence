"""LLM 适配器独立测试。

覆盖：SandboxCopywritingEngine、OpenAICompatibleCopywritingEngine、LLMAdapterError。
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

import pytest

from src.adapters.llm import (
    DisabledCopywritingEngine,
    LLMAdapterError,
    OpenAICompatibleCopywritingEngine,
    SandboxCopywritingEngine,
)
from src.services.copywriting import CopywritingService


class TestSandboxCopywritingEngineExtended:
    """扩展 SandboxCopywritingEngine 测试。"""

    def setup_method(self):
        self.engine = SandboxCopywritingEngine()

    def test_capabilities_keys(self):
        cap = self.engine.capabilities()
        required_keys = {
            "provider_name",
            "display_name",
            "mode",
            "enabled",
            "max_input_chars",
            "max_output_chars",
            "supports_variants",
            "max_variants",
        }
        assert required_keys.issubset(cap.keys())

    def test_rewrite_with_style_prompt(self):
        results = self.engine.rewrite("测试", style_prompt="专业权威，数据支撑，理性分析")
        assert len(results) >= 1
        assert "可由输入事实支撑的明确判断" in results[0]

    def test_rewrite_style_and_variants_change_the_demo_copy(self):
        results = self.engine.rewrite(
            "企业做内容获客",
            style_prompt="故事叙述，悬念铺垫，引人入胜",
            variant_count=3,
        )

        assert len(results) == 3
        assert "处境—转折—启发" in results[0]
        assert "问题反差" in results[0]
        assert "结果先行" in results[1]
        assert "场景代入" in results[2]

    def test_generate_style_and_variants_change_the_demo_copy(self):
        results = self.engine.generate(
            content_brief="企业做内容获客",
            style_prompt="情感共鸣，触动人心，引发共情",
            variant_count=3,
        )

        assert len(results) == 3
        assert "真实场景或感受" in results[0]
        assert "问题反差" in results[0]
        assert "结果先行" in results[1]
        assert "场景代入" in results[2]

    def test_rewrite_with_tone(self):
        results = self.engine.rewrite("测试", tone="casual")
        assert len(results) >= 1

    def test_rewrite_with_target_length(self):
        results = self.engine.rewrite("测试", target_length=500)
        assert len(results) >= 1

    def test_rewrite_variant_count_zero(self):
        """variant_count=0 应被修正为 1。"""
        results = self.engine.rewrite("测试", variant_count=0)
        assert len(results) >= 1

    def test_rewrite_variant_count_negative(self):
        """负数 variant_count 应被修正为 1。"""
        results = self.engine.rewrite("测试", variant_count=-5)
        assert len(results) >= 1

    def test_rewrite_multiline_source(self):
        results = self.engine.rewrite("第一行\n第二行\n第三行")
        assert len(results) >= 1

    def test_rewrite_result_contains_snippet(self):
        """演示结果应包含源文案片段。"""
        results = self.engine.rewrite("独特的关键词测试")
        assert any("独特的关键词测试" in r for r in results)

    def test_publish_metadata_is_explicitly_demo_output(self):
        result = self.engine.generate_publish_metadata("新品活动，欢迎了解")

        assert result["title"].startswith("【演示】")
        assert result["description"].startswith("【演示结果】")
        assert result["tags"]


class TestLLMAdapterError:
    """测试 LLMAdapterError 异常类。"""

    def test_basic_error(self):
        err = LLMAdapterError("测试错误")
        assert str(err) == "测试错误"
        assert err.retryable is False

    def test_retryable_error(self):
        err = LLMAdapterError("可重试错误", retryable=True)
        assert err.retryable is True

    def test_is_runtime_error(self):
        err = LLMAdapterError("测试")
        assert isinstance(err, RuntimeError)


class TestOpenAICompatibleCopywritingEngine:
    """测试 OpenAICompatibleCopywritingEngine。"""

    def test_init_defaults(self):
        engine = OpenAICompatibleCopywritingEngine()
        assert engine.model == "gpt-4o-mini"
        assert engine.api_key == ""
        assert "openai.com" in engine.base_url

    def test_init_with_params(self):
        engine = OpenAICompatibleCopywritingEngine(
            api_key="sk-test",
            base_url="https://custom.api.com/v1",
            model="gpt-4",
            timeout_seconds=30,
        )
        assert engine.api_key == "sk-test"
        assert engine.model == "gpt-4"
        assert engine.timeout_seconds == 30

    def test_init_timeout_minimum(self):
        """timeout 低于 5 秒应被修正为 5。"""
        engine = OpenAICompatibleCopywritingEngine(timeout_seconds=1)
        assert engine.timeout_seconds == 5.0

    def test_capabilities_without_api_key(self):
        engine = OpenAICompatibleCopywritingEngine(api_key="")
        cap = engine.capabilities()
        assert cap["enabled"] is False
        assert cap["mode"] == "disabled"

    def test_capabilities_with_api_key(self):
        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")
        cap = engine.capabilities()
        assert cap["enabled"] is True
        assert cap["mode"] == "production"

    def test_rewrite_without_api_key_raises(self):
        engine = OpenAICompatibleCopywritingEngine(api_key="")
        with pytest.raises(LLMAdapterError, match="未配置 COPYWRITING_API_KEY"):
            engine.rewrite("测试文案")

    @patch("src.adapters.llm.urlopen")
    def test_generate_publish_metadata_returns_bounded_json_fields(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "新品活动介绍",
                                    "description": "介绍产品活动的核心信息。",
                                    "tags": ["#新品", "品牌活动"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response
        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")

        result = engine.generate_publish_metadata(
            "新品活动，欢迎了解",
            platforms=["douyin", "xiaohongshu"],
        )

        assert result == {
            "title": "新品活动介绍",
            "description": "介绍产品活动的核心信息。",
            "tags": ["新品", "品牌活动"],
        }
        sent = json.loads(mock_urlopen.call_args.args[0].data.decode("utf-8"))
        assert "抖音" not in sent["messages"][1]["content"]
        assert "douyin、xiaohongshu" in sent["messages"][1]["content"]

    @patch("src.adapters.llm.urlopen")
    def test_review_transcript_candidates_returns_voiceover_rewrite(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"corrected_text":"今天的优惠是八十块。","note":"按上下文修订。"}'
                        }
                    }
                ]
            }
        ).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response
        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")

        result = engine.review_transcript_candidates(
            previous_text="今天有活动。",
            next_text="数量有限。",
            candidates=["今天优惠八十元", "今天优惠八十块"],
        )

        assert result == {
            "corrected_text": "今天的优惠是八十块。",
            "note": "按上下文修订。",
        }
        sent = json.loads(mock_urlopen.call_args.args[0].data.decode("utf-8"))
        assert "低置信片段" in sent["messages"][1]["content"]

    def test_from_env_defaults(self):
        """from_env 应使用环境变量。"""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COPYWRITING_API_KEY", None)
            os.environ.pop("COPYWRITING_BASE_URL", None)
            os.environ.pop("COPYWRITING_MODEL", None)
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("OPENAI_BASE_URL", None)
            os.environ.pop("COPYWRITING_LLM_MODEL", None)
            engine = OpenAICompatibleCopywritingEngine.from_env()
            assert engine.api_key == ""
            assert engine.base_url == "https://api.deepseek.com"
            assert engine.model == "deepseek-v4-flash"

    def test_from_env_with_vars(self):
        with patch.dict(
            os.environ,
            {
                "COPYWRITING_API_KEY": "sk-env-test",
                "COPYWRITING_BASE_URL": "https://env.api.com/v1",
                "COPYWRITING_MODEL": "gpt-4-turbo",
            },
        ):
            engine = OpenAICompatibleCopywritingEngine.from_env()
            assert engine.api_key == "sk-env-test"
            assert engine.base_url == "https://env.api.com/v1"
            assert engine.model == "gpt-4-turbo"

    def test_build_system_prompt_basic(self):
        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")
        prompt = engine._build_system_prompt("", 300, "professional")
        assert "短视频口播文案" in prompt
        assert "professional" in prompt
        assert "目标字数" not in prompt
        assert "目标平台" not in prompt
        assert "严格 JSON" in prompt

    def test_build_system_prompt_with_style(self):
        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")
        prompt = engine._build_system_prompt("轻松风格", 500, "casual")
        assert "轻松风格" in prompt
        assert "casual" in prompt

    def test_build_system_prompt_assigns_a_different_structure_per_variant(self):
        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")
        prompt = engine._build_system_prompt(
            "吸引眼球，制造悬念，引发好奇",
            300,
            "energetic",
            variant_count=3,
        )

        assert "所选风格的执行规则" in prompt
        assert "第 1 版必须采用：问题反差" in prompt
        assert "第 2 版必须采用：结果先行" in prompt
        assert "第 3 版必须采用：场景代入" in prompt

    def test_single_copy_prompt_uses_fixed_dedup_and_auto_audience(self):
        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")
        prompt = engine._build_system_prompt("", 300, "natural", variant_count=1)

        assert "每次都必须执行深度语义去重" in prompt
        assert "自动判断最适合的受众" in prompt
        assert "只输出一篇完成度高" in prompt
        assert "其他企业、品牌、机构或人物" in prompt
        assert '"attention_terms"' in prompt
        assert "逐版差异化策略" not in prompt

    def test_parse_attention_terms_deduplicates_and_bounds_values(self):
        content = json.dumps(
            {
                "variants": ["竞品科技发布了这款工具。"],
                "attention_terms": ["竞品科技", "竞品科技", "", "x", "未出现名称"],
            },
            ensure_ascii=False,
        )

        assert OpenAICompatibleCopywritingEngine._parse_attention_terms(content) == [
            "竞品科技",
            "未出现名称",
        ]


class TestCopywritingRiskRules:
    def test_platform_sensitive_marketing_terms_are_flagged(self):
        categories = CopywritingService._risk_categories(["扫码加微信，领取全网最低价优惠。"])

        assert "平台敏感营销用语" in categories

    @patch("src.adapters.llm.urlopen")
    def test_rewrite_includes_voiceover_goal(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"choices": [{"message": {"content": '{"variants":["结果"]}'}}]}
        ).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response
        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")

        engine.rewrite("原始文案", rewrite_goal="压缩为 45 秒并删除重复句")

        sent = json.loads(mock_urlopen.call_args.args[0].data.decode("utf-8"))
        assert "压缩为 45 秒并删除重复句" in sent["messages"][1]["content"]

    @patch("src.adapters.llm.urlopen")
    def test_chat_completion_success(self, mock_urlopen):
        """模拟成功 API 调用。"""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "改写后的文案内容"}}]}
        ).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")
        result = engine._chat_completion("系统提示", "用户提示")
        assert result == "改写后的文案内容"
        sent = json.loads(mock_urlopen.call_args.args[0].data.decode("utf-8"))
        assert sent["response_format"] == {"type": "json_object"}

    @patch("src.adapters.llm.urlopen")
    def test_deepseek_payload_disables_thinking(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "choices": [{"message": {"content": '{"variants":["结果"]}'}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        ).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        engine = OpenAICompatibleCopywritingEngine(
            api_key="sk-test",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
        )
        results = engine.rewrite("原始文案", variant_count=3)
        sent = json.loads(mock_urlopen.call_args.args[0].data.decode("utf-8"))
        assert sent["model"] == "deepseek-v4-flash"
        assert sent["thinking"] == {"type": "disabled"}
        assert results == ["结果"]
        assert engine.last_usage["total_tokens"] == 15

    @patch("src.adapters.llm.urlopen")
    def test_rewrite_exposes_attention_terms_from_model_json(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "variants": ["竞品科技发布了这款工具。"],
                                    "attention_terms": ["竞品科技"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response
        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")

        results = engine.rewrite("原始文案", variant_count=1)

        assert results == ["竞品科技发布了这款工具。"]
        assert engine.last_attention_terms == ["竞品科技"]

    @patch("src.adapters.llm.urlopen")
    def test_chat_completion_http_error(self, mock_urlopen):
        """模拟 HTTP 错误。"""
        mock_urlopen.side_effect = HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=429,
            msg="Rate Limited",
            hdrs=None,
            fp=MagicMock(read=MagicMock(return_value=b"rate limit exceeded")),
        )
        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")
        with pytest.raises(LLMAdapterError, match="429") as exc_info:
            engine._chat_completion("系统提示", "用户提示")
        assert exc_info.value.retryable is True

    @patch("src.adapters.llm.urlopen")
    def test_chat_completion_network_error(self, mock_urlopen):
        """模拟网络错误。"""
        mock_urlopen.side_effect = URLError("Connection refused")
        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")
        with pytest.raises(LLMAdapterError, match="无法连接") as exc_info:
            engine._chat_completion("系统提示", "用户提示")
        assert exc_info.value.retryable is True

    @patch("src.adapters.llm.urlopen")
    def test_chat_completion_invalid_json(self, mock_urlopen):
        """模拟无效 JSON 响应。"""
        mock_response = MagicMock()
        mock_response.read.return_value = b"not valid json"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")
        with pytest.raises(LLMAdapterError, match="无效 JSON"):
            engine._chat_completion("系统提示", "用户提示")

    @patch("src.adapters.llm.urlopen")
    def test_chat_completion_empty_choices(self, mock_urlopen):
        """模拟空 choices 响应。"""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"choices": []}).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")
        result = engine._chat_completion("系统提示", "用户提示")
        assert result == ""

    @patch("src.adapters.llm.urlopen")
    def test_rewrite_success(self, mock_urlopen):
        """模拟完整 rewrite 流程。"""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "改写结果"}}]}
        ).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")
        results = engine.rewrite("原始文案", variant_count=1)
        assert len(results) == 1
        assert results[0] == "改写结果"

    @patch("src.adapters.llm.urlopen")
    def test_rewrite_no_results_raises(self, mock_urlopen):
        """rewrite 无有效结果应抛出异常。"""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"choices": []}).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        engine = OpenAICompatibleCopywritingEngine(api_key="sk-test")
        with pytest.raises(LLMAdapterError, match="未返回有效内容"):
            engine.rewrite("原始文案", variant_count=1)


class TestDisabledCopywritingEngine:
    def test_disabled_capabilities_do_not_enable_generation(self):
        engine = DisabledCopywritingEngine(model="deepseek-v4-flash")
        cap = engine.capabilities()
        assert cap["enabled"] is False
        assert cap["mode"] == "disabled"
        assert cap["missing_configuration"] == ["COPYWRITING_API_KEY"]

    def test_disabled_generate_raises_readable_error(self):
        engine = DisabledCopywritingEngine()
        with pytest.raises(LLMAdapterError, match="COPYWRITING_API_KEY"):
            engine.generate(content_brief="测试")
