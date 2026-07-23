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
from src.adapters.official import (  # noqa: E402
    DouyinHotBillboardAdapter,
    DouyinHotWordsAdapter,
)
from src.services.candidate import CandidateService  # noqa: E402
from src.services.commercial_search import CommercialSearchService  # noqa: E402
from src.services.transcription import TranscriptionService  # noqa: E402
from src.services.doubao_browser import (  # noqa: E402
    DoubaoBrowserAutomationService,
    DoubaoMobileAutomationService,
)
from src.services.media_resolution import MediaResolutionService  # noqa: E402
from src.services.douyin_link_transcription import DouyinLinkTranscriptionService  # noqa: E402
from src.adapters.douyin_parser import LocalDouyinBrowserParserClient  # noqa: E402
from src.adapters.douyin_browser_search import LocalDouyinBrowserSearchProvider  # noqa: E402
from src.services.pipeline import PipelineService  # noqa: E402
from src.services.production import ProductionService  # noqa: E402
from src.services.feedback import FeedbackService  # noqa: E402
from src.services.pipeline_worker import PipelineWorker  # noqa: E402
from src.services.copywriting import CopywritingService  # noqa: E402
from src.services.video_editor import VideoEditingService  # noqa: E402
from src.services.publisher import PublishService  # noqa: E402
from src.services.avatar import AvatarService  # noqa: E402
from src.services.heat import HeatService  # noqa: E402
from src.services.keyword_trend import KeywordTrendService  # noqa: E402
from src.services.hot_pool import OfficialHotPoolService  # noqa: E402
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
    DOUYIN_CLIENT_KEY,
    DOUYIN_CLIENT_SECRET,
    DOUYIN_OFFICIAL_HOT_ENABLED,
    DOUYIN_HOT_WORDS_ENABLED,
    DOUYIN_LOCAL_BROWSER_ENABLED,
    DOUYIN_BROWSER_CHANNEL,
    DOUYIN_BROWSER_TIMEOUT_SECONDS,
    DOUYIN_BROWSER_DISCOVERY_ENABLED,
    DOUYIN_BROWSER_DISCOVERY_PROFILE_DIR,
    DOUYIN_BROWSER_DISCOVERY_DEBUG_PORT,
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
def get_discovery_search_provider():
    """选择候选发现来源；不影响 OneAPI 的受控媒体解析回退。"""
    if DOUYIN_BROWSER_DISCOVERY_ENABLED:
        return LocalDouyinBrowserSearchProvider(
            enabled=True,
            profile_dir=DOUYIN_BROWSER_DISCOVERY_PROFILE_DIR,
            browser_channel=DOUYIN_BROWSER_CHANNEL,
            debug_port=DOUYIN_BROWSER_DISCOVERY_DEBUG_PORT,
            timeout_seconds=DOUYIN_BROWSER_TIMEOUT_SECONDS,
        )
    return get_licensed_search_provider()


@lru_cache
def get_commercial_search_service() -> CommercialSearchService:
    return CommercialSearchService(
        repository=get_repository(),
        source_service=get_source_service(),
        trend_service=get_keyword_trend_service(),
        provider=get_discovery_search_provider(),
        active_platforms=tuple(
            Platform(platform) for platform in CRAWLER_ACTIVE_PLATFORMS
        ),
    )


# ---------------------------------------------------------------------------
# 官方热榜池服务（抖音开放平台热门视频榜 + 实时热点词）
# ---------------------------------------------------------------------------


@lru_cache
def get_official_hot_billboard_adapter() -> DouyinHotBillboardAdapter | None:
    """官方热榜适配器；DOUYIN_OFFICIAL_HOT_ENABLED=false 时返回 None，不调用官方接口。"""
    if not DOUYIN_OFFICIAL_HOT_ENABLED:
        return None
    return DouyinHotBillboardAdapter(DOUYIN_CLIENT_KEY, DOUYIN_CLIENT_SECRET)


@lru_cache
def get_official_hot_words_adapter() -> DouyinHotWordsAdapter | None:
    """官方热点词适配器；DOUYIN_HOT_WORDS_ENABLED=false 时返回 None。"""
    if not DOUYIN_HOT_WORDS_ENABLED:
        return None
    return DouyinHotWordsAdapter(DOUYIN_CLIENT_KEY, DOUYIN_CLIENT_SECRET)


@lru_cache
def get_official_hot_pool_service() -> OfficialHotPoolService:
    return OfficialHotPoolService(
        repository=get_repository(),
        source_service=get_source_service(),
        trend_service=get_keyword_trend_service(),
        billboard_adapter=get_official_hot_billboard_adapter(),
        hot_words_adapter=get_official_hot_words_adapter(),
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
def get_doubao_browser_service() -> DoubaoBrowserAutomationService:
    return DoubaoBrowserAutomationService(get_repository())


@lru_cache
def get_doubao_mobile_service() -> DoubaoMobileAutomationService:
    return DoubaoMobileAutomationService(get_repository())


@lru_cache
def get_media_resolution_service() -> MediaResolutionService:
    return MediaResolutionService(
        get_repository(),
        get_licensed_search_provider(),
    )


@lru_cache
def get_experimental_douyin_parser() -> LocalDouyinBrowserParserClient:
    return LocalDouyinBrowserParserClient(
        enabled=DOUYIN_LOCAL_BROWSER_ENABLED,
        browser_channel=DOUYIN_BROWSER_CHANNEL,
        timeout_seconds=DOUYIN_BROWSER_TIMEOUT_SECONDS,
    )


@lru_cache
def get_douyin_link_transcription_service() -> DouyinLinkTranscriptionService:
    return DouyinLinkTranscriptionService(
        get_experimental_douyin_parser(),
        get_licensed_search_provider(),
        get_transcription_service(),
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


@lru_cache
def get_production_service() -> ProductionService:
    return ProductionService(
        get_repository(),
        storage_directory=PROJECT_ROOT / "data" / "production",
        media_resolution_service=get_media_resolution_service(),
        avatar_service=get_avatar_service(),
        template_service=get_template_service(),
        publish_service=get_publish_service(),
    )


@lru_cache
def get_feedback_service() -> FeedbackService:
    return FeedbackService(get_repository(), PROJECT_ROOT / "data" / "production")


@lru_cache
def get_pipeline_worker() -> PipelineWorker:
    return PipelineWorker(
        repository=get_repository(),
        pipeline_service=get_pipeline_service(),
        commercial_search_service=get_commercial_search_service(),
        avatar_service=get_avatar_service(),
        video_editing_service=get_video_editing_service(),
        publish_service=get_publish_service(),
        template_service=get_template_service(),
        production_service=get_production_service(),
    )


# ---------------------------------------------------------------------------
# 模板管理服务
# ---------------------------------------------------------------------------


@lru_cache
def get_template_service():
    """获取模板管理服务实例。"""
    from src.services.template_service import TemplateService

    return TemplateService(templates_dir=str(PROJECT_ROOT / "data" / "templates"))


# ---------------------------------------------------------------------------
# 字幕生成器
# ---------------------------------------------------------------------------


@lru_cache
def get_subtitle_generator():
    """获取字幕生成器实例。"""
    from src.adapters.subtitle_generator import SubtitleGenerator

    return SubtitleGenerator()
