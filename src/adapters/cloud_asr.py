"""云端 ASR 适配器抽象。

将具体供应商（阿里云、腾讯云、讯飞等）与主服务解耦。
当前提供 sandbox 实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ASRSegment:
    start: float
    end: float
    text: str
    confidence: float


@dataclass
class ASRResult:
    segments: list[ASRSegment]
    language: str
    duration: float
    provider_name: str


class CloudASRProvider(ABC):
    """云端 ASR 供应商接口。"""

    @abstractmethod
    def capabilities(self) -> dict[str, str | bool | int | list[str]]: ...

    @abstractmethod
    def transcribe(
        self,
        audio_path: str,
        *,
        language: str = "zh",
        hotwords: str = "",
    ) -> ASRResult:
        """转写音频文件，返回分段结果。"""
        ...


class SandboxCloudASR(CloudASRProvider):
    """沙箱云端 ASR —— 不发起真实请求，返回演示数据。"""

    def capabilities(self) -> dict[str, str | bool | int | list[str]]:
        return {
            "provider_name": "sandbox_cloud_asr",
            "display_name": "云端语音识别（演示）",
            "mode": "sandbox",
            "enabled": True,
            "supported_languages": ["zh", "en"],
            "max_audio_seconds": 3600,
        }

    def transcribe(
        self,
        audio_path: str,
        *,
        language: str = "zh",
        hotwords: str = "",
    ) -> ASRResult:
        return ASRResult(
            segments=[
                ASRSegment(
                    start=0.0,
                    end=3.5,
                    text="【演示】这是云端语音识别的演示结果。",
                    confidence=0.92,
                ),
                ASRSegment(
                    start=3.5,
                    end=7.0,
                    text="实际生产环境中将调用真实 ASR 供应商。",
                    confidence=0.88,
                ),
            ],
            language=language,
            duration=7.0,
            provider_name="sandbox_cloud_asr",
        )
