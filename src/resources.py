from __future__ import annotations

import shutil
import importlib.util
import os
from pathlib import Path
from typing import Any

import streamlit as st


ASR_MODEL_REPOSITORIES = {
    "base": "Systran/faster-whisper-base",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}


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


def asr_model_status(
    model_name: str,
    *,
    cache_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return on-disk model readiness without loading it into memory."""
    repository = ASR_MODEL_REPOSITORIES.get(model_name)
    if repository is None:
        raise ValueError("仅支持 base 和 large-v3-turbo 本地模型。")
    root = Path(
        cache_root
        or os.environ.get("HF_HUB_CACHE")
        or Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    )
    model_dir = root / f"models--{repository.replace('/', '--')}"
    snapshots_dir = model_dir / "snapshots"
    snapshots = list(snapshots_dir.glob("*")) if snapshots_dir.is_dir() else []
    files = [path for snapshot in snapshots for path in snapshot.rglob("*") if path.is_file()]
    required_names = {"model.bin", "config.json", "tokenizer.json"}
    installed = bool(files) and required_names.issubset({path.name for path in files})
    return {
        "model_name": model_name,
        "repository": repository,
        "installed": installed,
        "size_bytes": sum(path.stat().st_size for path in model_dir.rglob("*") if path.is_file())
        if model_dir.is_dir()
        else 0,
        "device": "cpu",
        "compute_type": "int8",
        "loaded_lazily": True,
    }


def list_asr_model_statuses() -> list[dict[str, Any]]:
    return [asr_model_status(model_name) for model_name in ASR_MODEL_REPOSITORIES]


def prepare_asr_model(model_name: str) -> dict[str, Any]:
    """Download one supported model without retaining it in 8 GB RAM."""
    status = asr_model_status(model_name)
    if status["installed"]:
        return status
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("缺少 huggingface-hub，无法下载本地字幕模型。") from exc

    repository = ASR_MODEL_REPOSITORIES[model_name]
    last_error: Exception | None = None
    for _ in range(2):
        try:
            snapshot_download(repo_id=repository, max_workers=2)
            return asr_model_status(model_name)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"模型下载失败，已自动重试 1 次：{last_error}") from last_error
