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


def test_candidate_page_is_douyin_only_and_hides_technical_platform_column() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)

    assert not app.exception
    rows = app.dataframe[0].value
    assert not rows.empty
    assert "平台" not in rows.columns
    assert "selection_mode: SINGLE_ROW" in str(app.dataframe[0].proto)
    assert not any(item.label == "平台" for item in app.text_input)
    assert not any(item.label == "平台" for item in app.multiselect)


def test_keyword_discovery_waits_for_credentials() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)

    assert not app.exception
    assert any(item.label == "平台关键词" for item in app.text_input)
    discover = next(
        button for button in app.button if button.label == "获取热门视频（调用1次）"
    )
    assert discover.disabled is True
    assert any(item.label == "召回时间范围" for item in app.selectbox)
    count = next(item for item in app.number_input if item.label == "获取数量")
    assert count.value == 10
    assert count.min == 1
    assert count.max == 10
    assert not any("重新计算本地" in button.label for button in app.button)


def test_paid_discovery_requires_chinese_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("DOUYIN_CLIENT_KEY", "test-key")
    monkeypatch.setenv("DOUYIN_CLIENT_SECRET", "test-secret")
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)
    keyword = next(item for item in app.text_input if item.label == "平台关键词")
    discover = next(
        button for button in app.button if button.label == "获取热门视频（调用1次）"
    )

    keyword.set_value("二手车")
    discover.click().run(timeout=15)

    assert not app.exception
    assert any(button.label == "确认并获取" for button in app.button)
    assert any(button.label == "取消" for button in app.button)
    assert any("将调用 1 次抖音搜索接口" in warning.value for warning in app.warning)


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
        button for button in app.button if button.label == "开始转成文案"
    )
    assert create_button.disabled is True
    assert not any("Mock" in button.label for button in app.button)


def test_task_page_shows_all_demo_statuses() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "tasks.py")).run(timeout=15)

    statuses = set(app.dataframe[0].value["状态"].tolist())
    assert {"处理中", "已完成", "失败"} <= statuses
