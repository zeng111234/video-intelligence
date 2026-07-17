from __future__ import annotations

import shutil
from typing import Any

import streamlit as st


@st.cache_resource
def runtime_capabilities() -> dict[str, bool]:
    return {"ffmpeg": shutil.which("ffmpeg") is not None, "mock_mode": True}


@st.cache_resource
def load_asr_model(model_name: str = "base") -> Any:
    """Reserved ASR resource, imported lazily so the framework starts without it."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper 尚未安装；当前转写模块仍运行在 Mock 模式。"
        ) from exc
    return WhisperModel(model_name, device="cpu", compute_type="int8")
