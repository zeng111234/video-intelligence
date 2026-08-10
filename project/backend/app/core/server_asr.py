"""Server-only Aliyun ASR dependency."""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache

from project.backend.app.core.config import RUNTIME_ROOT
from src.services.cloud_transcription import ASRAuthorizationStore, AliyunFunASRRuntime
from src.services.video_editor_cloud import CloudEditorConfiguration, CloudProviderMode


def server_asr_configuration(
    environment: Mapping[str, str] | None = None,
) -> CloudEditorConfiguration:
    """Build ASR credentials without coupling its switch to cloud editing."""

    source = os.environ if environment is None else environment
    configuration = CloudEditorConfiguration.from_env(source)
    asr_enabled = str(source.get("ASR_MODE", "sandbox")).strip().casefold() == "cloud"
    return configuration.model_copy(
        update={
            "provider_mode": (
                CloudProviderMode.ALIYUN
                if asr_enabled
                else CloudProviderMode.SANDBOX
            )
        }
    )


@lru_cache
def get_server_asr_runtime() -> AliyunFunASRRuntime:
    return AliyunFunASRRuntime(
        authorization_store=ASRAuthorizationStore(
            RUNTIME_ROOT / "data" / "control_plane" / "asr_authorization.json"
        ),
        configuration=server_asr_configuration(),
    )
