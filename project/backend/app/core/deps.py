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
from src.services.cloud_transcription import (  # noqa: E402
    ASRAuthorizationStore,
    AliyunFunASRRuntime,
)
from src.services.transcription_worker import TranscriptionWorker  # noqa: E402
from src.services.doubao_browser import (  # noqa: E402
    DoubaoBrowserAutomationService,
    DoubaoMobileAutomationService,
)
from src.services.media_resolution import MediaResolutionService  # noqa: E402
from src.services.douyin_link_transcription import DouyinLinkTranscriptionService  # noqa: E402
from src.services.candidate_copy_probe import CandidateCopyProbeService  # noqa: E402
from src.adapters.douyin_parser import LocalDouyinBrowserParserClient  # noqa: E402
from src.adapters.platform_link_parser import LocalPlatformLinkParserClient  # noqa: E402
from src.adapters.douyin_browser_search import (  # noqa: E402
    LocalDouyinBrowserSearchProvider,
    LocalDouyinPublicSearchProvider,
)
from src.adapters.platform_browser_search import (  # noqa: E402
    LocalPlatformBrowserSearchProvider,
)
from src.services.pipeline import PipelineService  # noqa: E402
from src.services.production import ProductionService  # noqa: E402
from src.services.feedback import FeedbackService  # noqa: E402
from src.services.pipeline_worker import PipelineWorker  # noqa: E402
from src.services.copywriting import CopywritingService  # noqa: E402
from src.services.video_editor import VideoEditingService  # noqa: E402
from src.services.publisher import PublishService  # noqa: E402
from src.services.publish_worker import PublishWorker  # noqa: E402
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
    CRAWLER_PROVIDER_MODE,
    CRAWLER_PROVIDER_NAME,
    CRAWLER_ACTIVE_PLATFORMS,
    CrawlerProviderMode,
    ONEAPI_API_KEY,
    COPYWRITING_API_KEY,
    COPYWRITING_BASE_URL,
    COPYWRITING_ESTIMATED_REQUEST_COST_CNY,
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
    XIAOHONGSHU_BROWSER_DISCOVERY_PROFILE_DIR,
    XIAOHONGSHU_BROWSER_DISCOVERY_DEBUG_PORT,
    KUAISHOU_BROWSER_DISCOVERY_ENABLED,
    KUAISHOU_BROWSER_DISCOVERY_PROFILE_DIR,
    KUAISHOU_BROWSER_DISCOVERY_DEBUG_PORT,
    BILIBILI_BROWSER_DISCOVERY_ENABLED,
    BILIBILI_BROWSER_DISCOVERY_PROFILE_DIR,
    BILIBILI_BROWSER_DISCOVERY_DEBUG_PORT,
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
    """付费兜底发现源；热点宝由独立的本机浏览器来源优先处理。"""
    return get_licensed_search_provider()


@lru_cache
def get_hotspot_browser_provider() -> LocalDouyinBrowserSearchProvider:
    """热点宝只使用独立、用户可见的 Chrome 资料目录。"""
    return LocalDouyinBrowserSearchProvider(
        enabled=DOUYIN_BROWSER_DISCOVERY_ENABLED,
        profile_dir=DOUYIN_BROWSER_DISCOVERY_PROFILE_DIR,
        browser_channel=DOUYIN_BROWSER_CHANNEL,
        debug_port=DOUYIN_BROWSER_DISCOVERY_DEBUG_PORT,
        timeout_seconds=DOUYIN_BROWSER_TIMEOUT_SECONDS,
    )


@lru_cache
def get_hotspot_search_service() -> CommercialSearchService:
    return CommercialSearchService(
        repository=get_repository(),
        source_service=get_source_service(),
        trend_service=get_keyword_trend_service(),
        provider=get_hotspot_browser_provider(),
        active_platforms=(Platform.DOUYIN,),
    )


@lru_cache
def get_douyin_public_browser_provider() -> LocalDouyinPublicSearchProvider:
    """抖音官网搜索与热点宝共用同一专用 Chrome 和登录态。"""
    return LocalDouyinPublicSearchProvider(
        enabled=DOUYIN_BROWSER_DISCOVERY_ENABLED,
        profile_dir=DOUYIN_BROWSER_DISCOVERY_PROFILE_DIR,
        browser_channel=DOUYIN_BROWSER_CHANNEL,
        debug_port=DOUYIN_BROWSER_DISCOVERY_DEBUG_PORT,
        timeout_seconds=DOUYIN_BROWSER_TIMEOUT_SECONDS,
    )


@lru_cache
def get_douyin_public_search_service() -> CommercialSearchService:
    return CommercialSearchService(
        repository=get_repository(),
        source_service=get_source_service(),
        trend_service=get_keyword_trend_service(),
        provider=get_douyin_public_browser_provider(),
        active_platforms=(Platform.DOUYIN,),
    )


@lru_cache
def get_xiaohongshu_browser_provider() -> LocalPlatformBrowserSearchProvider:
    return LocalPlatformBrowserSearchProvider(
        platform=Platform.XIAOHONGSHU,
        # 小红书账号已出现第三方自动化预警。即使旧环境变量仍为 true，也不能
        # 重新启用登录态浏览；只允许人工导入已观察到的素材。
        enabled=False,
        profile_dir=XIAOHONGSHU_BROWSER_DISCOVERY_PROFILE_DIR,
        browser_channel=DOUYIN_BROWSER_CHANNEL,
        debug_port=XIAOHONGSHU_BROWSER_DISCOVERY_DEBUG_PORT,
        timeout_seconds=DOUYIN_BROWSER_TIMEOUT_SECONDS,
    )


@lru_cache
def get_xiaohongshu_browser_search_service() -> CommercialSearchService:
    return CommercialSearchService(
        repository=get_repository(),
        source_service=get_source_service(),
        trend_service=get_keyword_trend_service(),
        provider=get_xiaohongshu_browser_provider(),
        active_platforms=(Platform.XIAOHONGSHU,),
    )


@lru_cache
def get_kuaishou_browser_provider() -> LocalPlatformBrowserSearchProvider:
    return LocalPlatformBrowserSearchProvider(
        platform=Platform.KUAISHOU,
        enabled=KUAISHOU_BROWSER_DISCOVERY_ENABLED,
        profile_dir=KUAISHOU_BROWSER_DISCOVERY_PROFILE_DIR,
        browser_channel=DOUYIN_BROWSER_CHANNEL,
        debug_port=KUAISHOU_BROWSER_DISCOVERY_DEBUG_PORT,
        timeout_seconds=DOUYIN_BROWSER_TIMEOUT_SECONDS,
    )


@lru_cache
def get_kuaishou_browser_search_service() -> CommercialSearchService:
    return CommercialSearchService(
        repository=get_repository(),
        source_service=get_source_service(),
        trend_service=get_keyword_trend_service(),
        provider=get_kuaishou_browser_provider(),
        active_platforms=(Platform.KUAISHOU,),
    )


@lru_cache
def get_bilibili_browser_provider() -> LocalPlatformBrowserSearchProvider:
    return LocalPlatformBrowserSearchProvider(
        platform=Platform.BILIBILI,
        enabled=BILIBILI_BROWSER_DISCOVERY_ENABLED,
        profile_dir=BILIBILI_BROWSER_DISCOVERY_PROFILE_DIR,
        browser_channel=DOUYIN_BROWSER_CHANNEL,
        debug_port=BILIBILI_BROWSER_DISCOVERY_DEBUG_PORT,
        timeout_seconds=DOUYIN_BROWSER_TIMEOUT_SECONDS,
    )


@lru_cache
def get_bilibili_browser_search_service() -> CommercialSearchService:
    return CommercialSearchService(
        repository=get_repository(),
        source_service=get_source_service(),
        trend_service=get_keyword_trend_service(),
        provider=get_bilibili_browser_provider(),
        active_platforms=(Platform.BILIBILI,),
    )


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
    """Cloud mode is handled by the persisted Fun-ASR runtime."""

    def unavailable_legacy_loader(_model_name: str):
        raise RuntimeError(
            "云端 ASR 不会加载本地模型，也不会降级到演示数据。"
        )

    return unavailable_legacy_loader


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
    copywriting_engine = get_copywriting_engine()
    capabilities = copywriting_engine.capabilities()
    review_method = (
        getattr(copywriting_engine, "review_transcript_candidates", None)
        if capabilities.get("mode") == "production" and capabilities.get("enabled")
        else None
    )
    cloud_runtime = None
    cloud_storage_directory = None
    if ASR_MODE == ASRMode.CLOUD:
        cloud_runtime = AliyunFunASRRuntime(
            authorization_store=ASRAuthorizationStore(
                PROJECT_ROOT / "data" / "production" / "asr_authorization.json"
            )
        )
        cloud_storage_directory = (
            PROJECT_ROOT / "data" / "production" / "transcription_media"
        )
    return TranscriptionService(
        get_repository(),
        model_loader=model_loader,
        transcript_reviewer=review_method if callable(review_method) else None,
        cloud_runtime=cloud_runtime,
        cloud_storage_directory=cloud_storage_directory,
    )


@lru_cache
def get_transcription_worker() -> TranscriptionWorker:
    return TranscriptionWorker(get_transcription_service())


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
def get_experimental_platform_link_parser() -> LocalPlatformLinkParserClient:
    return LocalPlatformLinkParserClient(
        douyin_parser=get_experimental_douyin_parser(),
        platform_providers={
            Platform.XIAOHONGSHU: get_xiaohongshu_browser_provider(),
            Platform.KUAISHOU: get_kuaishou_browser_provider(),
            Platform.BILIBILI: get_bilibili_browser_provider(),
        },
        timeout_seconds=DOUYIN_BROWSER_TIMEOUT_SECONDS,
    )


@lru_cache
def get_candidate_copy_probe_service() -> CandidateCopyProbeService:
    return CandidateCopyProbeService(get_experimental_platform_link_parser())


@lru_cache
def get_douyin_link_transcription_service() -> DouyinLinkTranscriptionService:
    return DouyinLinkTranscriptionService(
        get_experimental_platform_link_parser(),
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
            estimated_cost_cny=COPYWRITING_ESTIMATED_REQUEST_COST_CNY,
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
            PublishPlatform.BILIBILI,
        )
    }


@lru_cache
def get_publish_service() -> PublishService:
    return PublishService(get_repository(), get_publishers())


@lru_cache
def get_publish_worker() -> PublishWorker:
    return PublishWorker(get_publish_service())


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
        link_transcription_service=get_douyin_link_transcription_service(),
        copywriting_service=get_copywriting_service(),
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
        douyin_link_transcription_service=get_douyin_link_transcription_service(),
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
