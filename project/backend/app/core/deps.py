"""FastAPI 依赖注入 —— 复用 src/services，不依赖 streamlit。"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

# 确保项目根目录在 Python 路径中
_project_root = str(Path(__file__).resolve().parent.parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.repositories.sqlite import SQLiteRepository  # noqa: E402
from src.adapters.licensed import (  # noqa: E402
    DisabledLicensedSearchProvider,
    SandboxLicensedSearchProvider,
)
from src.adapters.oneapi import OneApiLicensedSearchProvider  # noqa: E402
from src.services.candidate import CandidateService  # noqa: E402
from src.services.commercial_search import CommercialSearchService  # noqa: E402
from src.services.transcription import TranscriptionService  # noqa: E402
from src.services.media_resolution import MediaResolutionService  # noqa: E402
from src.services.pipeline import PipelineService  # noqa: E402
from src.services.copywriting import CopywritingService  # noqa: E402
from src.services.video_editor import VideoEditingService  # noqa: E402
from src.services.publisher import PublishService  # noqa: E402
from src.services.avatar import AvatarService  # noqa: E402
from src.services.heat import HeatService  # noqa: E402
from src.services.keyword_trend import KeywordTrendService  # noqa: E402
from src.services.source import SourceService  # noqa: E402
from src.adapters.llm import (  # noqa: E402
    DisabledCopywritingEngine,
    OpenAICompatibleCopywritingEngine,
    SandboxCopywritingEngine,
)
from src.adapters.video_editor import SandboxVideoEditor  # noqa: E402
from src.adapters.avatar import build_avatar_provider  # noqa: E402
from src.adapters.publishers.sandbox import build_publisher  # noqa: E402
from src.models import Platform, PublishPlatform  # noqa: E402
from project.backend.app.core.config import (  # noqa: E402
    DATABASE_PATH,
    PROJECT_ROOT,
    ASRMode,
    ASR_MODE,
    ASR_CLOUD_PROVIDER,
    ALIYUN_ASR_ACCESS_KEY_ID,
    ALIYUN_ASR_ACCESS_KEY_SECRET,
    ALIYUN_ASR_APP_KEY,
    CRAWLER_PROVIDER_MODE,
    CRAWLER_PROVIDER_NAME,
    CRAWLER_ACTIVE_PLATFORMS,
    CrawlerProviderMode,
    ONEAPI_API_KEY,
    COPYWRITING_API_KEY,
    COPYWRITING_BASE_URL,
    COPYWRITING_MODE,
    COPYWRITING_MODEL,
    CopywritingProviderMode,
)


@lru_cache
def get_repository() -> SQLiteRepository:
    return SQLiteRepository(str(DATABASE_PATH))


@lru_cache
def get_candidate_service() -> CandidateService:
    return CandidateService(get_repository())


@lru_cache
def get_heat_service() -> HeatService:
    return HeatService()


@lru_cache
def get_source_service() -> SourceService:
    return SourceService(get_repository(), get_heat_service())


@lru_cache
def get_keyword_trend_service() -> KeywordTrendService:
    return KeywordTrendService(get_repository())


@lru_cache
def get_licensed_search_provider():
    if CRAWLER_PROVIDER_MODE == CrawlerProviderMode.SANDBOX:
        return SandboxLicensedSearchProvider()
    if CRAWLER_PROVIDER_MODE == CrawlerProviderMode.ONEAPI or (
        CRAWLER_PROVIDER_MODE == CrawlerProviderMode.PRODUCTION and ONEAPI_API_KEY
    ):
        return OneApiLicensedSearchProvider(ONEAPI_API_KEY)
    return DisabledLicensedSearchProvider(CRAWLER_PROVIDER_NAME)


@lru_cache
def get_commercial_search_service() -> CommercialSearchService:
    return CommercialSearchService(
        repository=get_repository(),
        source_service=get_source_service(),
        trend_service=get_keyword_trend_service(),
        provider=get_licensed_search_provider(),
        active_platforms=tuple(
            Platform(platform) for platform in CRAWLER_ACTIVE_PLATFORMS
        ),
    )


def _build_asr_model_loader():
    """根据 ASR_MODE 配置构建 model_loader 回调。

    Returns
    -------
    callable
        接收 model_name: str 参数，返回可调用的 model 对象（或 ASRBridge）。
    """
    if ASR_MODE == ASRMode.CLOUD:
        return _build_cloud_asr_loader()
    if ASR_MODE == ASRMode.LOCAL:
        return _local_model_loader
    return _sandbox_model_loader


def _build_cloud_asr_loader():
    """构建云端 ASR model_loader。未配置凭证时自动降级为 sandbox。"""
    from src.adapters.cloud_asr import SandboxCloudASR
    from src.adapters.asr_bridge import ASRBridge

    if ASR_CLOUD_PROVIDER.value == "aliyun":
        if not all(
            [ALIYUN_ASR_ACCESS_KEY_ID, ALIYUN_ASR_ACCESS_KEY_SECRET, ALIYUN_ASR_APP_KEY]
        ):
            # 凭证不完整，降级为 sandbox
            return lambda _: ASRBridge(SandboxCloudASR())
        from src.adapters.aliyun_asr import AliyunASRProvider

        provider = AliyunASRProvider(
            access_key_id=ALIYUN_ASR_ACCESS_KEY_ID,
            access_key_secret=ALIYUN_ASR_ACCESS_KEY_SECRET,
            app_key=ALIYUN_ASR_APP_KEY,
        )
        return lambda _: ASRBridge(provider)

    # 其他供应商暂未实现，降级为 sandbox
    return lambda _: ASRBridge(SandboxCloudASR())


def _local_model_loader(model_name: str):
    """加载本地 faster-whisper 模型。"""
    from src.resources import load_asr_model

    return load_asr_model(model_name)


def _sandbox_model_loader(*args, **kwargs):
    """替代 src.resources.load_asr_model，不依赖 @st.cache_resource。"""
    return None


@lru_cache
def get_transcription_service() -> TranscriptionService:
    model_loader = _build_asr_model_loader()
    return TranscriptionService(get_repository(), model_loader=model_loader)


@lru_cache
def get_media_resolution_service() -> MediaResolutionService:
    return MediaResolutionService(
        get_repository(),
        get_licensed_search_provider(),
    )


# ---------------------------------------------------------------------------
# 文案改写服务
# ---------------------------------------------------------------------------


@lru_cache
def get_copywriting_engine():
    """获取 AI 文案引擎。

    FastAPI 正式页面不自动回退到沙箱：未配置 Key 时返回 disabled 引擎。
    """
    if COPYWRITING_MODE == CopywritingProviderMode.SANDBOX:
        return SandboxCopywritingEngine()
    if COPYWRITING_API_KEY:
        return OpenAICompatibleCopywritingEngine(
            api_key=COPYWRITING_API_KEY,
            base_url=COPYWRITING_BASE_URL,
            model=COPYWRITING_MODEL,
        )
    return DisabledCopywritingEngine(
        base_url=COPYWRITING_BASE_URL,
        model=COPYWRITING_MODEL,
    )


@lru_cache
def get_copywriting_service() -> CopywritingService:
    return CopywritingService(get_repository(), get_copywriting_engine())


# ---------------------------------------------------------------------------
# 视频剪辑服务
# ---------------------------------------------------------------------------


@lru_cache
def get_video_editor():
    """获取视频编辑器实例——优先 FFmpeg，不可用时降级为沙箱。"""
    import shutil

    if shutil.which("ffmpeg"):
        from src.adapters.video_editor import FFmpegVideoEditor

        return FFmpegVideoEditor()
    return SandboxVideoEditor()


@lru_cache
def get_video_editing_service() -> VideoEditingService:
    return VideoEditingService(
        get_repository(),
        get_video_editor(),
        output_directory=PROJECT_ROOT / "data" / "video_edits",
    )


# ---------------------------------------------------------------------------
# 发布服务
# ---------------------------------------------------------------------------


@lru_cache
def get_publishers():
    """构建已注册的发布平台适配器映射。"""
    return {
        platform.value: build_publisher(platform)
        for platform in (
            PublishPlatform.DOUYIN,
            PublishPlatform.KUAISHOU,
            PublishPlatform.WECHAT_CHANNELS,
            PublishPlatform.XIAOHONGSHU,
        )
    }


@lru_cache
def get_publish_service() -> PublishService:
    return PublishService(get_repository(), get_publishers())


@lru_cache
def get_avatar_service() -> AvatarService:
    return AvatarService(
        get_repository(),
        build_avatar_provider(),
        result_directory=PROJECT_ROOT / "data" / "avatar_results",
    )


# ---------------------------------------------------------------------------
# 流水线编排服务
# ---------------------------------------------------------------------------


@lru_cache
def get_pipeline_service() -> PipelineService:
    return PipelineService(
        repository=get_repository(),
        commercial_search_service=get_commercial_search_service(),
        copywriting_service=get_copywriting_service(),
        video_editing_service=get_video_editing_service(),
        publish_service=get_publish_service(),
        media_resolution_service=get_media_resolution_service(),
        transcription_service=get_transcription_service(),
    )
