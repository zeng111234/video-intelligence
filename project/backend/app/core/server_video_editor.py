"""Server-only cloud video-editor providers."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from src.adapters.video_editor_cloud import CloudProviderBundle, build_cloud_providers
from src.services.video_editor_cloud import CloudEditorConfiguration


@dataclass(frozen=True)
class ServerVideoEditorRuntime:
    configuration: CloudEditorConfiguration
    providers: CloudProviderBundle


@lru_cache
def get_server_video_editor_runtime() -> ServerVideoEditorRuntime:
    configuration = CloudEditorConfiguration.from_env()
    return ServerVideoEditorRuntime(
        configuration=configuration,
        providers=build_cloud_providers(configuration),
    )
