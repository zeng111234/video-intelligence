from __future__ import annotations

import shutil
import importlib.util
from typing import Any

import streamlit as st


@st.cache_resource
def runtime_capabilities() -> dict[str, bool]:
    return {
        "ffmpeg": shutil.which("ffmpeg") is not None
        and shutil.which("ffprobe") is not None,
        "asr": importlib.util.find_spec("faster_whisper") is not None,
    }


@st.cache_resource
def load_asr_model(model_name: str = "base") -> Any:
    """Load the local ASR model lazily so normal page startup stays lightweight."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper 尚未安装，暂时无法执行真实本地转写。"
        ) from exc
    return WhisperModel(model_name, device="cpu", compute_type="int8")
