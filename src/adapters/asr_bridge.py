"""ASR 桥接层 —— 统一 CloudASRProvider 与 TranscriptionService 的接口差异。

TranscriptionService._transcribe() 期望 model.transcribe(path, **opts) 返回
    (raw_segments, info)
其中 raw_segments 元素需具备 .text / .start / .end / .avg_logprob 属性，
info 需具备 .language 属性。

本模块提供 ASRBridge，将 CloudASRProvider 的 transcribe() 返回值
（ASRResult）适配为上述接口，使 TranscriptionService 可透明使用云端 ASR。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.adapters.cloud_asr import ASRResult, ASRSegment, CloudASRProvider


# ---------------------------------------------------------------------------
# 适配数据结构 —— 模拟 faster_whisper 的返回值接口
# ---------------------------------------------------------------------------

@dataclass
class _AdaptedSegment:
    """模拟 faster_whisper 的 TranscriptionSegment，仅暴露 _transcribe 需要的字段。"""

    start: float
    end: float
    text: str
    avg_logprob: float


@dataclass
class _AdaptedInfo:
    """模拟 faster_whisper 的 TranscriptionInfo。"""

    language: str


class ASRBridge:
    """将 CloudASRProvider 适配为 TranscriptionService 可直接使用的 model 对象。

    用法::

        provider = AliyunASRProvider(access_key_id, access_key_secret)
        bridge = ASRBridge(provider)
        # bridge 可替代 WhisperModel 传入 TranscriptionService(model_loader=lambda _: bridge)

        raw_segments, info = bridge.transcribe("audio.wav", language="zh", hotwords="")
    """

    def __init__(self, provider: CloudASRProvider) -> None:
        if not isinstance(provider, CloudASRProvider):
            raise TypeError(f"provider 必须是 CloudASRProvider 子类，收到 {type(provider).__name__}")
        self._provider = provider
        self._provider_name = provider.capabilities().get("provider_name", "unknown")

    @property
    def provider(self) -> CloudASRProvider:
        return self._provider

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def capabilities(self) -> dict[str, Any]:
        return self._provider.capabilities()

    def transcribe(
        self,
        audio_path: str,
        *,
        language: str = "zh",
        vad_filter: bool = True,
        beam_size: int = 5,
        hotwords: str = "",
        **kwargs: Any,
    ) -> tuple[list[_AdaptedSegment], _AdaptedInfo]:
        """调用底层 CloudASRProvider 并将结果适配为 TranscriptionService 期望的格式。

        Parameters
        ----------
        audio_path : str
            音频文件路径（WAV 格式，16kHz 单声道）。
        language : str
            语言代码。
        vad_filter, beam_size, **kwargs
            保留参数，兼容 WhisperModel.transcribe() 签名，云端实现忽略这些参数。

        Returns
        -------
        tuple[list[_AdaptedSegment], _AdaptedInfo]
        """
        result: ASRResult = self._provider.transcribe(
            audio_path, language=language, hotwords=hotwords,
        )
        segments = [
            _AdaptedSegment(
                start=seg.start,
                end=seg.end,
                text=seg.text,
                avg_logprob=_confidence_to_logprob(seg.confidence),
            )
            for seg in result.segments
        ]
        info = _AdaptedInfo(language=result.language)
        return segments, info


def _confidence_to_logprob(confidence: float) -> float:
    """将置信度（0~1）反向转换为 avg_logprob 近似值。

    TranscriptionService 中使用 math.exp(avg_logprob) 来计算 confidence，
    因此 avg_logprob = ln(confidence)。加上限幅保护。
    """
    import math

    confidence = max(0.001, min(1.0, confidence))
    return math.log(confidence)
