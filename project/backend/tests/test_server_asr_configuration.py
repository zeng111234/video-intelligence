from __future__ import annotations

from project.backend.app.core.server_asr import server_asr_configuration
from src.services.video_editor_cloud import CloudProviderMode


def test_cloud_asr_does_not_require_enabling_cloud_video_editor():
    configuration = server_asr_configuration(
        {
            "ASR_MODE": "cloud",
            "VIDEO_EDITOR_PROVIDER_MODE": "sandbox",
            "DASHSCOPE_API_KEY": "dashscope-test",
            "ALIYUN_MODEL_STUDIO_WORKSPACE_ID": "workspace-test",
            "ALIYUN_OSS_BUCKET": "bucket-test",
            "ALIBABA_CLOUD_ACCESS_KEY_ID": "access-test",
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "secret-test",
        }
    )
    assert configuration.provider_mode == CloudProviderMode.ALIYUN


def test_sandbox_asr_stays_disabled_when_cloud_video_editor_is_enabled():
    configuration = server_asr_configuration(
        {
            "ASR_MODE": "sandbox",
            "VIDEO_EDITOR_PROVIDER_MODE": "aliyun",
            "DASHSCOPE_API_KEY": "dashscope-test",
        }
    )
    assert configuration.provider_mode == CloudProviderMode.SANDBOX
