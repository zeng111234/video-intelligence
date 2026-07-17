from __future__ import annotations

from collections.abc import MutableMapping
import os
from pathlib import Path
from typing import Any

import streamlit as st

from src.mock_data import build_mock_candidates, build_mock_tasks
from src.repositories import MockRepository, SQLiteRepository
from src.services import (
    CandidateService,
    HeatService,
    KeywordDiscoveryService,
    KeywordTrendService,
    SourceService,
    TranscriptionService,
)

REPOSITORY_KEY = "_repository"


def _sqlite_repository(database_path: str) -> SQLiteRepository:
    repository = SQLiteRepository(database_path)
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
    target.setdefault("active_transcription_task_id", "transcript-demo-001")
    target.setdefault("last_discovery_result", None)
    target.setdefault("candidate_local_query", "")
    target.setdefault("active_trend_keyword", "")
    if REPOSITORY_KEY not in target:
        target[REPOSITORY_KEY] = (
            MockRepository() if state is not None else _default_repository()
        )


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


def get_repository():
    initialize_state()
    return st.session_state[REPOSITORY_KEY]


def select_candidate(
    video_id: str, state: MutableMapping[str, Any] | None = None
) -> None:
    target = st.session_state if state is None else state
    target["selected_candidate_id"] = video_id


def selected_candidate_id(state: MutableMapping[str, Any] | None = None) -> str | None:
    target = st.session_state if state is None else state
    return target.get("selected_candidate_id")
