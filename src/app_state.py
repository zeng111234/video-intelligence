from __future__ import annotations

from collections.abc import MutableMapping
import os
from pathlib import Path
from typing import Any

import streamlit as st

from src.adapters.avatar import InternalAvatarProvider
from src.adapters.licensed import SandboxLicensedSearchProvider
from src.adapters.publishers.sandbox import SandboxPublisher
from src.adapters.video_editor import SandboxVideoEditor
from src.contracts import LicensedSearchProvider
from src.mock_data import build_mock_candidates, build_mock_tasks
from src.models import PublishPlatform
from src.repositories import MockRepository, SQLiteRepository
from src.services import (
    CandidateService,
    CommercialSearchService,
    CopywritingService,
    HeatService,
    KeywordDiscoveryService,
    KeywordTrendService,
    PipelineService,
    PublishService,
    SourceService,
    TranscriptionService,
    VideoEditingService,
)
from src.services.avatar import AvatarService
from src.adapters.llm import build_copywriting_engine
from src.context_budget import ContextBudget

REPOSITORY_KEY = "_repository"
AVATAR_PROVIDER_KEY = "_avatar_provider"
CONTEXT_BUDGET_KEY = "_context_budget"


def _sqlite_repository(database_path: str) -> SQLiteRepository:
    repository = SQLiteRepository(database_path)
    seed_demo_data = os.getenv("VIDEO_SEED_DEMO_DATA", "").casefold() in {
        "1",
        "true",
        "yes",
    }
    if seed_demo_data:
        repository.seed(build_mock_candidates(), build_mock_tasks())
    SourceService(repository, HeatService()).recompute_all()
    return repository


def _default_repository():
    mode = os.getenv("VIDEO_REPOSITORY_MODE", "sqlite").casefold()
    if mode == "mock":
        return MockRepository()
    database_path = os.getenv(
        "VIDEO_DATABASE_PATH", str(Path("data") / "video_intelligence.db")
    )
    return _sqlite_repository(database_path)


def initialize_state(state: MutableMapping[str, Any] | None = None) -> None:
    target = st.session_state if state is None else state
    target.setdefault("selected_candidate_id", None)
    target.setdefault("selected_task_id", None)
    target.setdefault("active_transcription_task_id", None)
    target.setdefault("avatar_source_task_id", None)
    target.setdefault("avatar_source_revision_id", None)
    target.setdefault("active_avatar_task_id", None)
    target.setdefault("last_discovery_result", None)
    target.setdefault("candidate_local_query", "")
    target.setdefault("active_trend_keyword", "")
    target.setdefault("active_trend_platform", "douyin")
    target.setdefault("active_search_batch_id", None)
    if REPOSITORY_KEY not in target:
        target[REPOSITORY_KEY] = (
            MockRepository() if state is not None else _default_repository()
        )
    if CONTEXT_BUDGET_KEY not in target:
        target[CONTEXT_BUDGET_KEY] = ContextBudget()


def get_services() -> tuple[CandidateService, HeatService, TranscriptionService]:
    initialize_state()
    repository = st.session_state[REPOSITORY_KEY]
    return CandidateService(repository), HeatService(), TranscriptionService(repository)


def get_source_service() -> SourceService:
    initialize_state()
    repository = st.session_state[REPOSITORY_KEY]
    return SourceService(repository, HeatService())


def get_keyword_discovery_service() -> KeywordDiscoveryService:
    source_service = get_source_service()
    return KeywordDiscoveryService(source_service.repository, source_service)


def get_keyword_trend_service() -> KeywordTrendService:
    initialize_state()
    return KeywordTrendService(st.session_state[REPOSITORY_KEY])


def get_commercial_search_service(
    provider: LicensedSearchProvider,
) -> CommercialSearchService:
    source_service = get_source_service()
    repository = source_service.repository
    return CommercialSearchService(
        repository,
        source_service,
        KeywordTrendService(repository),
        provider,
    )


def get_repository():
    initialize_state()
    return st.session_state[REPOSITORY_KEY]


def get_avatar_service() -> AvatarService:
    initialize_state()
    provider = st.session_state.get(AVATAR_PROVIDER_KEY)
    if provider is None:
        provider = InternalAvatarProvider.from_env()
    return AvatarService(st.session_state[REPOSITORY_KEY], provider)


def select_candidate(
    video_id: str, state: MutableMapping[str, Any] | None = None
) -> None:
    target = st.session_state if state is None else state
    target["selected_candidate_id"] = video_id


def selected_candidate_id(state: MutableMapping[str, Any] | None = None) -> str | None:
    target = st.session_state if state is None else state
    return target.get("selected_candidate_id")


def get_context_budget() -> ContextBudget:
    initialize_state()
    return st.session_state[CONTEXT_BUDGET_KEY]


PIPELINE_SERVICE_KEY = "_pipeline_service"


def get_pipeline_service() -> PipelineService:
    """组装完整的流水线服务，注入所有依赖。

    使用 session_state 缓存实例，避免重复创建。
    """
    initialize_state()
    if PIPELINE_SERVICE_KEY in st.session_state:
        return st.session_state[PIPELINE_SERVICE_KEY]

    repository = st.session_state[REPOSITORY_KEY]

    # 搜索服务——使用沙箱商业接口
    provider: LicensedSearchProvider = SandboxLicensedSearchProvider()
    source_service = SourceService(repository, HeatService())
    trend_service = KeywordTrendService(repository)
    search_service = CommercialSearchService(
        repository, source_service, trend_service, provider,
    )

    # 文案服务
    engine = build_copywriting_engine()
    copy_service = CopywritingService(repository, engine)

    # 视频剪辑服务——使用沙箱编辑器
    video_editor = SandboxVideoEditor()
    edit_service = VideoEditingService(repository, video_editor)

    # 发布服务——三平台沙箱发布
    publishers = {
        "douyin": SandboxPublisher(PublishPlatform.DOUYIN),
        "kuaishou": SandboxPublisher(PublishPlatform.KUAISHOU),
        "wechat_channels": SandboxPublisher(PublishPlatform.WECHAT_CHANNELS),
    }
    pub_service = PublishService(repository, publishers)

    pipeline = PipelineService(
        repository=repository,
        commercial_search_service=search_service,
        copywriting_service=copy_service,
        video_editing_service=edit_service,
        publish_service=pub_service,
    )
    st.session_state[PIPELINE_SERVICE_KEY] = pipeline
    return pipeline
