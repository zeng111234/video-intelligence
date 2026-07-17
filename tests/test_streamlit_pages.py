from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).parents[1]


def test_business_ui_hides_streamlit_developer_toolbar() -> None:
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text("utf-8"))

    assert config["client"]["toolbarMode"] == "minimal"


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


def test_candidate_page_only_renders_douyin_rows() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)

    assert not app.exception
    rows = app.dataframe[0].value
    assert not rows.empty
    assert set(rows["平台"]) == {"抖音"}
    assert "selection_mode: SINGLE_ROW" in str(app.dataframe[0].proto)
    assert not any(item.label == "平台" for item in app.text_input)
    assert not any(item.label == "平台" for item in app.multiselect)


def test_keyword_discovery_waits_for_credentials() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)

    assert not app.exception
    assert any(item.label == "平台关键词" for item in app.text_input)
    discover = next(
        button for button in app.button if button.label == "获取综合候选10条（1次调用）"
    )
    assert discover.disabled is True
    local_recompute = next(
        button
        for button in app.button
        if button.label == "仅重新计算本地7日榜（0次调用）"
    )
    assert local_recompute.disabled is False
    assert any(item.label == "召回时间范围" for item in app.selectbox)


def test_local_keyword_recompute_does_not_require_credentials() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)
    keyword = next(item for item in app.text_input if item.label == "平台关键词")
    local_recompute = next(
        button
        for button in app.button
        if button.label == "仅重新计算本地7日榜（0次调用）"
    )

    keyword.set_value("二手车")
    local_recompute.click().run(timeout=15)

    assert not app.exception


def test_candidate_page_can_open_manual_metrics_form() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)
    source_select = next(item for item in app.selectbox if item.label == "备用数据源")

    source_select.set_value("手工链接与指标").run(timeout=15)

    assert not app.exception
    assert any(item.label == "抖音作品 ID" for item in app.text_input)
    assert any(button.label == "保存手工记录" for button in app.button)


def test_candidate_page_focuses_on_hotspot_to_transcript_flow() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py"))
    app.session_state["selected_candidate_id"] = "mock-001"
    app.run(timeout=15)

    assert not app.exception
    assert any(button.label == "将此视频转成文案" for button in app.button)
    assert any(selectbox.label == "备用数据源" for selectbox in app.selectbox)
    assert not any(subheader.value == "人工复核队列" for subheader in app.subheader)


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
