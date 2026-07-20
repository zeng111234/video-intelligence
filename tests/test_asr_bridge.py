"""ASR 桥接层与云端适配器单元测试。"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.cloud_asr import (
    ASRResult,
    ASRSegment,
    CloudASRProvider,
    SandboxCloudASR,
)
from src.adapters.asr_bridge import ASRBridge, _AdaptedInfo, _AdaptedSegment, _confidence_to_logprob


# ======================================================================
# SandboxCloudASR 基础测试
# ======================================================================

class TestSandboxCloudASR:
    """SandboxCloudASR 应始终返回演示数据，不依赖外部服务。"""

    def test_capabilities_returns_sandbox_mode(self):
        provider = SandboxCloudASR()
        caps = provider.capabilities()
        assert caps["provider_name"] == "sandbox_cloud_asr"
        assert caps["mode"] == "sandbox"
        assert caps["enabled"] is True
        assert "zh" in caps["supported_languages"]

    def test_transcribe_returns_demo_segments(self):
        provider = SandboxCloudASR()
        result = provider.transcribe("dummy.wav")
        assert isinstance(result, ASRResult)
        assert len(result.segments) == 2
        assert result.language == "zh"
        assert result.duration == 7.0
        assert result.provider_name == "sandbox_cloud_asr"
        # 验证片段属性
        for seg in result.segments:
            assert seg.start >= 0
            assert seg.end > seg.start
            assert len(seg.text) > 0
            assert 0 < seg.confidence <= 1

    def test_transcribe_respects_language_param(self):
        provider = SandboxCloudASR()
        result = provider.transcribe("dummy.wav", language="en")
        assert result.language == "en"

    def test_transcribe_respects_hotwords_param(self):
        provider = SandboxCloudASR()
        result = provider.transcribe("dummy.wav", hotwords="自定义词")
        # sandbox 忽略 hotwords，但不应报错
        assert isinstance(result, ASRResult)


# ======================================================================
# ASRBridge 适配测试
# ======================================================================

class TestASRBridge:
    """ASRBridge 应将 CloudASRProvider 的返回值适配为 TranscriptionService 期望的格式。"""

    def test_bridge_accepts_cloud_asr_provider(self):
        provider = SandboxCloudASR()
        bridge = ASRBridge(provider)
        assert bridge.provider is provider
        assert bridge.provider_name == "sandbox_cloud_asr"

    def test_bridge_rejects_non_provider(self):
        with pytest.raises(TypeError, match="必须是 CloudASRProvider"):
            ASRBridge("not a provider")  # type: ignore

    def test_bridge_capabilities_passthrough(self):
        provider = SandboxCloudASR()
        bridge = ASRBridge(provider)
        caps = bridge.capabilities()
        assert caps == provider.capabilities()

    def test_bridge_transcribe_returns_tuple(self):
        provider = SandboxCloudASR()
        bridge = ASRBridge(provider)
        segments, info = bridge.transcribe("dummy.wav")
        assert isinstance(segments, list)
        assert len(segments) == 2
        assert isinstance(info, _AdaptedInfo)

    def test_bridge_segments_have_correct_interface(self):
        provider = SandboxCloudASR()
        bridge = ASRBridge(provider)
        segments, info = bridge.transcribe("dummy.wav")
        for seg in segments:
            assert isinstance(seg, _AdaptedSegment)
            assert hasattr(seg, "start")
            assert hasattr(seg, "end")
            assert hasattr(seg, "text")
            assert hasattr(seg, "avg_logprob")

    def test_bridge_info_has_language(self):
        provider = SandboxCloudASR()
        bridge = ASRBridge(provider)
        _, info = bridge.transcribe("dummy.wav")
        assert info.language == "zh"

    def test_bridge_passes_language_and_hotwords(self):
        """验证 bridge 正确传递 language 和 hotwords 给底层 provider。"""
        mock_provider = MagicMock(spec=CloudASRProvider)
        mock_provider.capabilities.return_value = {"provider_name": "mock"}
        mock_provider.transcribe.return_value = ASRResult(
            segments=[ASRSegment(start=0, end=1, text="test", confidence=0.9)],
            language="en",
            duration=1.0,
            provider_name="mock",
        )
        bridge = ASRBridge(mock_provider)
        bridge.transcribe("test.wav", language="en", hotwords="keyword")

        mock_provider.transcribe.assert_called_once_with(
            "test.wav", language="en", hotwords="keyword",
        )

    def test_bridge_transcribe_passes_extra_kwargs(self):
        """WhisperModel 的额外参数（vad_filter, beam_size）应被 bridge 忽略而不报错。"""
        provider = SandboxCloudASR()
        bridge = ASRBridge(provider)
        segments, info = bridge.transcribe(
            "dummy.wav", language="zh", vad_filter=True, beam_size=5, hotwords="",
        )
        assert len(segments) == 2


# ======================================================================
# _confidence_to_logprob 转换测试
# ======================================================================

class TestConfidenceToLogprob:
    """验证 confidence <-> avg_logprob 的转换一致性。"""

    def test_confidence_05_maps_to_expected_logprob(self):
        logprob = _confidence_to_logprob(0.5)
        assert abs(logprob - math.log(0.5)) < 1e-10

    def test_confidence_09_maps_to_expected_logprob(self):
        logprob = _confidence_to_logprob(0.9)
        assert abs(logprob - math.log(0.9)) < 1e-10

    def test_confidence_zero_clamps_to_minimum(self):
        logprob = _confidence_to_logprob(0.0)
        assert logprob == math.log(0.001)

    def test_confidence_above_one_clamps(self):
        logprob = _confidence_to_logprob(1.5)
        assert abs(logprob - math.log(1.0)) < 1e-10

    def test_roundtrip_conversion(self):
        """exp(logprob) 应还原回原始 confidence。"""
        for conf in [0.1, 0.5, 0.75, 0.9, 0.99]:
            logprob = _confidence_to_logprob(conf)
            recovered = math.exp(logprob)
            assert abs(recovered - conf) < 1e-6, f"conf={conf}, recovered={recovered}"


# ======================================================================
# AliyunASRProvider Mock 测试
# ======================================================================

class TestAliyunASRProviderMock:
    """AliyunASRProvider 的 Mock 行为测试。"""

    def test_import_aliyun_provider(self):
        from src.adapters.aliyun_asr import AliyunASRProvider, AliyunASRError
        assert AliyunASRProvider is not None
        assert AliyunASRError is not None

    def test_provider_requires_valid_credentials(self):
        from src.adapters.aliyun_asr import AliyunASRProvider, AliyunASRError
        with pytest.raises(AliyunASRError, match="access_key_id 不能为空"):
            AliyunASRProvider("", "secret", "appkey")
        with pytest.raises(AliyunASRError, match="access_key_secret 不能为空"):
            AliyunASRProvider("id", "", "appkey")
        with pytest.raises(AliyunASRError, match="app_key 不能为空"):
            AliyunASRProvider("id", "secret", "")

    def test_provider_capabilities(self):
        from src.adapters.aliyun_asr import AliyunASRProvider
        provider = AliyunASRProvider("test-id", "test-secret", "test-key")
        caps = provider.capabilities()
        assert caps["provider_name"] == "aliyun_asr"
        assert caps["mode"] == "cloud"
        assert caps["enabled"] is True
        provider.close()

    def test_transcribe_rejects_missing_file(self):
        from src.adapters.aliyun_asr import AliyunASRProvider, AliyunASRError
        provider = AliyunASRProvider("test-id", "test-secret", "test-key")
        with pytest.raises(AliyunASRError, match="音频文件不存在"):
            provider.transcribe("/nonexistent/file.wav")
        provider.close()

    def test_transcribe_rejects_empty_file(self, tmp_path):
        from src.adapters.aliyun_asr import AliyunASRProvider, AliyunASRError
        empty_file = tmp_path / "empty.wav"
        empty_file.write_bytes(b"")
        provider = AliyunASRProvider("test-id", "test-secret", "test-key")
        with pytest.raises(AliyunASRError, match="音频文件为空"):
            provider.transcribe(str(empty_file))
        provider.close()

    def test_context_manager(self):
        from src.adapters.aliyun_asr import AliyunASRProvider
        with AliyunASRProvider("test-id", "test-secret", "test-key") as provider:
            assert provider.capabilities()["provider_name"] == "aliyun_asr"


# ======================================================================
# ASRBridge + AliyunASRProvider 集成测试（Mock httpx）
# ======================================================================

class TestAliyunASRProviderWithMockHTTP:
    """通过 Mock httpx 验证 AliyunASRProvider 的完整调用链路。"""

    def _make_provider(self):
        from src.adapters.aliyun_asr import AliyunASRProvider
        return AliyunASRProvider("test-ak", "test-sk", "test-appkey")

    def test_full_transcribe_flow(self, tmp_path):
        """模拟完整的 token -> create -> query -> parse 流程。"""
        from src.adapters.aliyun_asr import AliyunASRProvider

        # 创建一个假音频文件
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00" * 1024)

        provider = self._make_provider()

        # Mock 三个 HTTP 调用
        token_resp = MagicMock()
        token_resp.json.return_value = {
            "Code": "200",
            "Data": {"Token": {"Id": "mock-token-123"}},
        }
        token_resp.raise_for_status = MagicMock()

        create_resp = MagicMock()
        create_resp.json.return_value = {
            "StatusCode": 21050000,
            "TaskId": "task-abc",
        }
        create_resp.raise_for_status = MagicMock()

        query_resp = MagicMock()
        query_resp.json.return_value = {
            "StatusCode": 21050000,
            "Result": '[{"text": "你好世界", "begin_time": 1000, "end_time": 3500, "confidence": 0.95}]',
        }
        query_resp.raise_for_status = MagicMock()

        with patch.object(provider._client, "post", side_effect=[token_resp, create_resp]):
            with patch.object(provider._client, "get", return_value=query_resp):
                result = provider.transcribe(str(audio_file))

        assert isinstance(result, ASRResult)
        assert len(result.segments) == 1
        assert result.segments[0].text == "你好世界"
        assert result.segments[0].start == 1.0
        assert result.segments[0].end == 3.5
        assert result.segments[0].confidence == 0.95
        assert result.provider_name == "aliyun_asr"
        provider.close()

    def test_bridge_with_aliyun_mock(self, tmp_path):
        """通过 ASRBridge 使用 AliyunASRProvider，验证适配后的接口兼容性。"""
        from src.adapters.aliyun_asr import AliyunASRProvider

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"\x00" * 1024)

        provider = self._make_provider()

        token_resp = MagicMock()
        token_resp.json.return_value = {
            "Code": "200",
            "Data": {"Token": {"Id": "token"}},
        }
        token_resp.raise_for_status = MagicMock()

        create_resp = MagicMock()
        create_resp.json.return_value = {"StatusCode": 21050000, "TaskId": "t1"}
        create_resp.raise_for_status = MagicMock()

        query_resp = MagicMock()
        query_resp.json.return_value = {
            "StatusCode": 21050000,
            "Result": '[{"text": "测试", "begin_time": 0, "end_time": 2000, "confidence": 0.9}]',
        }
        query_resp.raise_for_status = MagicMock()

        bridge = ASRBridge(provider)

        with patch.object(provider._client, "post", side_effect=[token_resp, create_resp]):
            with patch.object(provider._client, "get", return_value=query_resp):
                segments, info = bridge.transcribe(str(audio_file))

        assert len(segments) == 1
        assert segments[0].text == "测试"
        assert hasattr(segments[0], "avg_logprob")
        assert info.language == "zh"

        # 验证 avg_logprob 与 confidence 一致性
        expected_logprob = math.log(0.9)
        assert abs(segments[0].avg_logprob - expected_logprob) < 1e-6
        provider.close()


# ======================================================================
# 降级测试：未配置凭证时自动使用 SandboxCloudASR
# ======================================================================

class TestFallbackToSandbox:
    """验证未配置凭证时的自动降级行为。"""

    def test_sandbox_bridge_works_as_model(self):
        """SandboxCloudASR 通过 ASRBridge 适配后，接口与 TranscriptionService 兼容。"""
        bridge = ASRBridge(SandboxCloudASR())
        segments, info = bridge.transcribe("dummy.wav", language="zh", hotwords="")

        assert len(segments) > 0
        assert info.language == "zh"

        # 验证 _transcribe 中使用的字段都存在
        for seg in segments:
            assert hasattr(seg, "text")
            assert hasattr(seg, "start")
            assert hasattr(seg, "end")
            assert hasattr(seg, "avg_logprob")
            # 验证 TranscriptionService 的 confidence 计算逻辑不报错
            confidence = math.exp(seg.avg_logprob)
            assert 0 < confidence <= 1


# ======================================================================
# CloudASRProvider 抽象接口测试
# ======================================================================

class TestCloudASRProviderInterface:
    """验证抽象接口约束。"""

    def test_cannot_instantiate_abstract_provider(self):
        with pytest.raises(TypeError):
            CloudASRProvider()  # type: ignore

    def test_custom_provider_must_implement_methods(self):
        class IncompleteProvider(CloudASRProvider):
            pass

        with pytest.raises(TypeError):
            IncompleteProvider()  # type: ignore

    def test_custom_provider_can_be_implemented(self):
        class MyProvider(CloudASRProvider):
            def capabilities(self):
                return {"provider_name": "test"}

            def transcribe(self, audio_path, *, language="zh", hotwords=""):
                return ASRResult(
                    segments=[ASRSegment(start=0, end=1, text="ok", confidence=1.0)],
                    language=language,
                    duration=1.0,
                    provider_name="test",
                )

        provider = MyProvider()
        bridge = ASRBridge(provider)
        segments, info = bridge.transcribe("test.wav")
        assert segments[0].text == "ok"
