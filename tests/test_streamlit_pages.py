from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("path", "expected_title"),
    [
        (ROOT / "app.py", "爆火视频检索"),
        (ROOT / "app_pages" / "candidates.py", "爆火视频检索"),
        (ROOT / "app_pages" / "transcription.py", "音视频转文案"),
        (ROOT / "app_pages" / "tasks.py", "任务记录"),
    ],
)
def test_pages_render_without_exceptions(path: Path, expected_title: str) -> None:
    app = AppTest.from_file(str(path)).run(timeout=15)

    assert not app.exception
    assert any(expected_title in title.value for title in app.title)


def test_candidate_page_renders_at_least_twenty_rows() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)

    assert not app.exception
    assert len(app.dataframe[0].value) >= 20


def test_transcription_creation_is_blocked_without_rights() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "transcription.py")).run(
        timeout=15
    )

    create_button = next(
        button for button in app.button if "生成 Mock 转写" in button.label
    )
    assert create_button.disabled is True


def test_task_page_shows_all_demo_statuses() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "tasks.py")).run(timeout=15)

    statuses = set(app.dataframe[0].value["状态"].tolist())
    assert {"处理中", "已完成", "失败"} <= statuses
