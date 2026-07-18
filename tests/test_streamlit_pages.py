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


def test_candidate_page_shows_three_platform_entry_and_platform_history() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)

    assert not app.exception
    rows = app.dataframe[0].value
    assert not rows.empty
    assert "平台" in rows.columns
    assert "selection_mode: SINGLE_ROW" in str(app.dataframe[0].proto)
    assert any(item.label == "历史平台" for item in app.multiselect)
    visible_text = " ".join(
        [item.value for item in app.markdown]
        + [item.value for item in app.caption]
        + [item.value for item in app.warning]
    )
    assert "抖音" in visible_text
    assert "小红书" in visible_text
    assert "微信视频号" in visible_text
    assert "演示数据" in visible_text


def test_three_platform_search_defaults_to_sandbox_ten_and_seven_days() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)

    assert not app.exception
    assert any(item.label == "关键词" for item in app.text_input)
    discover = next(
        button for button in app.button if button.label == "三平台一键查爆款（演示）"
    )
    assert discover.disabled is False
    window = next(item for item in app.selectbox if item.label == "时间范围")
    assert window.value == "近 7 天"
    count = next(item for item in app.number_input if item.label == "每平台数量")
    assert count.value == 10
    assert count.min == 1
    assert count.max == 10
    assert any(item.label == "强制刷新（可能产生新费用）" for item in app.toggle)


def test_three_platform_search_requires_chinese_confirmation() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)
    keyword = next(item for item in app.text_input if item.label == "关键词")
    discover = next(
        button for button in app.button if button.label == "三平台一键查爆款（演示）"
    )

    keyword.set_value("二手车")
    discover.click().run(timeout=15)

    assert not app.exception
    assert any(button.label == "确认并查询" for button in app.button)
    assert any(button.label == "取消" for button in app.button)
    assert any("不访问三个平台" in info.value for info in app.info)
    preview = next(
        frame.value for frame in app.dataframe if "预计新增接口调用" in frame.value
    )
    assert preview["预计新增接口调用"].sum() == 0


def test_sandbox_search_renders_three_independent_top_ten() -> None:
    from datetime import datetime, timezone

    from src.adapters.licensed import SandboxLicensedSearchProvider
    from src.repositories import MockRepository
    from src.services import HeatService, KeywordTrendService, SourceService
    from src.services.commercial_search import CommercialSearchService

    now = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    repository = MockRepository(candidates=[], tasks=[])
    source = SourceService(repository, HeatService())
    batch = CommercialSearchService(
        repository,
        source,
        KeywordTrendService(repository),
        SandboxLicensedSearchProvider(clock=lambda: now),
        clock=lambda: now,
    ).execute(keyword="二手车")
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py"))
    app.session_state["_repository"] = repository
    app.session_state["active_search_batch_id"] = batch.batch_id
    app.run(timeout=15)

    assert not app.exception
    assert {tab.label for tab in app.tabs} >= {"抖音", "小红书", "微信视频号"}
    ranking_frames = [
        frame.value for frame in app.dataframe if "系统排名" in frame.value.columns
    ]
    assert len(ranking_frames) == 3
    assert all(len(frame) == 10 for frame in ranking_frames)


def test_production_mode_stays_disabled_without_approved_adapter(monkeypatch) -> None:
    monkeypatch.setenv("VIDEO_LICENSED_PROVIDER_MODE", "production")
    monkeypatch.setenv("VIDEO_LICENSED_PROVIDER_NAME", "newrank_pending")
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)

    discover = next(
        button for button in app.button if button.label == "三平台一键查爆款"
    )
    assert discover.disabled is True
    assert any("不会发起平台请求" in item.value for item in app.error)


def test_candidate_page_can_open_manual_metrics_form() -> None:
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)
    source_select = next(item for item in app.selectbox if item.label == "备用数据源")

    source_select.set_value("手工链接与指标").run(timeout=15)

    assert not app.exception
    assert any("作品 ID" in item.label for item in app.text_input)
    assert any(item.label == "平台" for item in app.selectbox)
    assert any(button.label == "保存手工记录" for button in app.button)


def test_page_does_not_call_legacy_douyin_transport(monkeypatch) -> None:
    transport_calls = 0

    def forbidden_transport(*args, **kwargs):
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("manual-only platform must not call a network transport")

    monkeypatch.setattr("src.adapters.official._default_transport", forbidden_transport)
    app = AppTest.from_file(str(ROOT / "app_pages" / "candidates.py")).run(timeout=15)
    assert not app.exception
    assert transport_calls == 0


def test_transcription_page_shows_selected_candidate_platform() -> None:
    from src.models import Platform
    from src.repositories import MockRepository

    candidate = (
        MockRepository()
        .list_candidates()[0]
        .model_copy(
            update={
                "video_id": "xhs-selected",
                "platform_item_id": "xhs-selected",
                "platform": Platform.XIAOHONGSHU,
                "source_url": "https://www.xiaohongshu.com/explore/xhs-selected",
            }
        )
    )
    app = AppTest.from_file(str(ROOT / "app_pages" / "transcription.py"))
    app.session_state["_repository"] = MockRepository(candidates=[candidate], tasks=[])
    app.session_state["selected_candidate_id"] = candidate.video_id
    app.run(timeout=15)

    assert not app.exception
    assert any("小红书" in item.value for item in app.caption)


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
