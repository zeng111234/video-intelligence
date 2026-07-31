"""三档文案来源测试。

- metadata_original：原创口播脚本，is_original_transcript=False、needs_manual_review=True。
- doubao_mobile_transcript：费用恒 0 的标记档位。
- authorized_asr_transcript：授权后媒体解析 + ASR，暴露预计成本。
"""

from __future__ import annotations

import pytest

from src.adapters.llm import SandboxCopywritingEngine
from src.models import CopySource, TaskStatus, VideoMetricSnapshot
from src.repositories.mock import MockRepository
from src.services.copywriting import CopywritingService


def _service() -> CopywritingService:
    return CopywritingService(MockRepository(), SandboxCopywritingEngine())


def test_copy_source_options_cover_three_tiers() -> None:
    options = _service().copy_source_options()
    by_source = {option.copy_source: option for option in options}

    assert set(by_source) == {
        "metadata_original",
        "doubao_mobile_transcript",
        "authorized_asr_transcript",
    }
    assert by_source["metadata_original"].is_original_transcript is False
    assert by_source["metadata_original"].needs_manual_review is True
    # 手机豆包档位费用恒 0
    assert by_source["doubao_mobile_transcript"].estimated_cost_cny == 0.0
    assert by_source["doubao_mobile_transcript"].is_original_transcript is True
    assert by_source["authorized_asr_transcript"].is_original_transcript is True


def test_generate_metadata_original_marks_not_transcript() -> None:
    service = _service()
    metrics = VideoMetricSnapshot(
        item_id="v1",
        sampled_at=__import__("datetime").datetime.now().astimezone(),
        likes=1200,
        comments=88,
        shares=None,
        favorites=None,
        confidence=1.0,
    )

    task = service.generate_metadata_original(
        title="AI 数字人口播获客实战",
        reference_text="这是操作者整理的可见文案。它只用于概括原意并改写成新的口播稿。",
        hot_words=["AI数字人", "口播获客"],
        metrics=metrics,
    )

    assert task.status == TaskStatus.SUCCEEDED
    assert task.creation_mode == "metadata_original"
    assert task.copy_source == CopySource.METADATA_ORIGINAL.value
    assert task.is_original_transcript is False
    assert task.needs_manual_review is True
    assert task.result_text
    assert "适合数字人口播" in task.style_prompt
    assert "【演示】" not in task.result_text
    assert "\n" in task.result_text
    # 概要包含标题、热点词与可用互动数据，缺失字段不展示为 0
    assert "AI 数字人口播获客实战" in task.content_brief
    assert "操作者提供的可见文案" in task.content_brief
    assert "只用于概括原意" in task.content_brief
    assert "AI数字人" in task.content_brief
    assert "点赞 1200" in task.content_brief
    assert "分享" not in task.content_brief

    stored = service.get_task(task.task_id)
    assert stored is not None
    assert stored.copy_source == "metadata_original"
    assert stored.is_original_transcript is False
    assert stored.needs_manual_review is True


def test_generate_metadata_original_requires_title() -> None:
    with pytest.raises(ValueError, match="标题不能为空"):
        _service().generate_metadata_original(title="  ")


def test_build_copy_result_metadata_original_flags() -> None:
    result = CopywritingService.build_copy_result(
        copy_source="metadata_original",
        text="原创脚本正文",
        candidate_id="douyin-a1",
    )
    assert result.copy_source is CopySource.METADATA_ORIGINAL
    assert result.is_original_transcript is False
    assert result.needs_manual_review is True
    assert any("不是原视频转写" in note for note in result.notes)


def test_build_copy_result_doubao_cost_forced_zero() -> None:
    result = CopywritingService.build_copy_result(
        copy_source=CopySource.DOUBAO_MOBILE_TRANSCRIPT,
        text="豆包转写正文",
        estimated_cost_cny=9.99,  # 即使传入也被强制为 0
    )
    assert result.copy_source is CopySource.DOUBAO_MOBILE_TRANSCRIPT
    assert result.is_original_transcript is True
    assert result.estimated_cost_cny == 0.0


def test_build_copy_result_authorized_asr_exposes_cost() -> None:
    result = CopywritingService.build_copy_result(
        copy_source="authorized_asr_transcript",
        text="ASR 转写正文",
        estimated_cost_cny=0.08,
    )
    assert result.copy_source is CopySource.AUTHORIZED_ASR_TRANSCRIPT
    assert result.is_original_transcript is True
    assert result.needs_manual_review is False
    assert result.estimated_cost_cny == 0.08


def test_build_copy_result_rejects_unknown_source() -> None:
    with pytest.raises(ValueError, match="文案来源只支持"):
        CopywritingService.build_copy_result(
            copy_source="scraper_transcript", text="x"
        )
