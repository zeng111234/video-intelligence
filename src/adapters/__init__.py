from __future__ import annotations

# ruff: noqa: F401

from importlib import import_module
from typing import TYPE_CHECKING, Any

_EXPORT_MODULES = {
    "AliyunASRError": "src.adapters.aliyun_asr",
    "AliyunASRProvider": "src.adapters.aliyun_asr",
    "ASRBridge": "src.adapters.asr_bridge",
    "AvatarProviderError": "src.adapters.avatar",
    "CloudASRProvider": "src.adapters.cloud_asr",
    "DisabledLicensedSearchProvider": "src.adapters.licensed",
    "DouyinHotBillboardAdapter": "src.adapters.official",
    "DouyinKeywordAdapter": "src.adapters.official",
    "FFmpegVideoEditor": "src.adapters.video_editor",
    "InternalAvatarProvider": "src.adapters.avatar",
    "LLMAdapterError": "src.adapters.llm",
    "LicensedProviderError": "src.adapters.licensed",
    "ManualImportAdapter": "src.adapters.importers",
    "OfficialApiError": "src.adapters.official",
    "OpenAICompatibleCopywritingEngine": "src.adapters.llm",
    "PublicMetadataResearchAdapter": "src.adapters.public_metadata",
    "SandboxCloudASR": "src.adapters.cloud_asr",
    "SandboxCopywritingEngine": "src.adapters.llm",
    "SandboxLicensedSearchProvider": "src.adapters.licensed",
    "SandboxVideoEditor": "src.adapters.video_editor",
    "SubtitleGenerator": "src.adapters.subtitle_generator",
    "SubtitleGeneratorError": "src.adapters.subtitle_generator",
    "VideoEditorError": "src.adapters.video_editor",
}

__all__ = sorted(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    try:
        module_name = _EXPORT_MODULES[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})


if TYPE_CHECKING:
    from src.adapters.aliyun_asr import AliyunASRError, AliyunASRProvider
    from src.adapters.asr_bridge import ASRBridge
    from src.adapters.avatar import AvatarProviderError, InternalAvatarProvider
    from src.adapters.cloud_asr import CloudASRProvider, SandboxCloudASR
    from src.adapters.importers import ManualImportAdapter
    from src.adapters.licensed import (
        DisabledLicensedSearchProvider,
        LicensedProviderError,
        SandboxLicensedSearchProvider,
    )
    from src.adapters.llm import (
        LLMAdapterError,
        OpenAICompatibleCopywritingEngine,
        SandboxCopywritingEngine,
    )
    from src.adapters.official import (
        DouyinHotBillboardAdapter,
        DouyinKeywordAdapter,
        OfficialApiError,
    )
    from src.adapters.public_metadata import PublicMetadataResearchAdapter
    from src.adapters.subtitle_generator import SubtitleGenerator, SubtitleGeneratorError
    from src.adapters.video_editor import (
        FFmpegVideoEditor,
        SandboxVideoEditor,
        VideoEditorError,
    )
