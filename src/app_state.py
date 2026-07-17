from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

import streamlit as st

from src.repositories import MockRepository
from src.services import CandidateService, HeatService, TranscriptionService

REPOSITORY_KEY = "_mock_repository"


def initialize_state(state: MutableMapping[str, Any] | None = None) -> None:
    target = st.session_state if state is None else state
    target.setdefault("selected_candidate_id", None)
    target.setdefault("selected_task_id", None)
    target.setdefault("active_transcription_task_id", "transcript-demo-001")
    target.setdefault(REPOSITORY_KEY, MockRepository())


def get_services() -> tuple[CandidateService, HeatService, TranscriptionService]:
    initialize_state()
    repository = st.session_state[REPOSITORY_KEY]
    return CandidateService(repository), HeatService(), TranscriptionService(repository)


def select_candidate(
    video_id: str, state: MutableMapping[str, Any] | None = None
) -> None:
    target = st.session_state if state is None else state
    target["selected_candidate_id"] = video_id


def selected_candidate_id(state: MutableMapping[str, Any] | None = None) -> str | None:
    target = st.session_state if state is None else state
    return target.get("selected_candidate_id")
